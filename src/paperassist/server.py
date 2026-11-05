"""Loopback-only API for the shared local library."""

import ipaddress
import json
import os
import secrets
import shutil
import signal
import sys
import tempfile
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from paperassist.library import Library
from paperassist import inbox, location, paths, picker, references, retrieval, settings


def _write_token(data_dir):
    """Keep one token per library so a restart does not invalidate open browser tabs."""
    path = data_dir / '.token'
    if token := path.read_text(encoding='utf-8').strip() if path.is_file() else '':
        return token
    token = secrets.token_urlsafe(32)
    temporary = path.with_name(f'.token.{os.getpid()}')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as file:
        file.write(token)
    os.replace(temporary, path)
    return token


def create_app(data_dir, source='default', switch=None):
    """`switch(directory)` makes the running server serve another library; `source` says who chose this one."""
    data_dir = Path(data_dir).resolve()
    library = Library(data_dir)
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024
    token = _write_token(data_dir)
    jobs = {}
    cancellations = {}
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='paperassist')
    slots = threading.BoundedSemaphore(8)
    download_lock = threading.Lock()
    picker_lock = threading.Lock()
    web = Path(__file__).parent / 'web'

    @app.before_request
    def protect():
        from urllib.parse import urlsplit
        host = (urlsplit(request.host_url).hostname or '').lower()
        if host not in ('localhost', '127.0.0.1', '::1'):
            abort(403)
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            abort(403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not secrets.compare_digest(request.headers.get('X-PaperAssist-Token', ''), token):
            abort(403)

    @app.errorhandler(Exception)
    def errors(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        if isinstance(error, (KeyError, FileNotFoundError)):
            return jsonify(error=str(error)), 404
        if isinstance(error, ValueError):
            return jsonify(error=str(error)), 409 if 'conflict' in str(error).lower() else 400
        app.logger.exception('Request failed')
        return jsonify(error='Request failed'), 500

    def submit(task):
        if not slots.acquire(blocking=False):
            return jsonify(error='Too many jobs; try again later'), 429
        while len(jobs) >= 50:
            old = next((key for key, value in jobs.items() if value['status'] in ('completed', 'failed', 'cancelled')), None)
            if old is None:
                break
            jobs.pop(old)
            cancellations.pop(old, None)
        job_id = uuid.uuid4().hex
        cancel = cancellations[job_id] = threading.Event()
        job = jobs[job_id] = {'id': job_id, 'status': 'pending'}
        def run():
            job['status'] = 'running'
            try:
                job['result'] = task(job, cancel)
                job['status'] = 'cancelled' if cancel.is_set() else 'completed'
            except Exception as error:
                job['error'] = str(error)
                job['status'] = 'failed'
            finally:
                slots.release()
        executor.submit(run)
        return jsonify(job), 202

    @app.get('/')
    def index():
        return send_from_directory(web, 'index.html')

    @app.get('/<path:name>')
    def assets(name):
        return send_from_directory(web, name)

    @app.get('/api/config')
    def config():
        current = settings.load(data_dir)
        return jsonify(token=token, library_path=str(data_dir), library_source=source, providers=current.providers(),
                       proxy=current.proxy, inbox=current.inbox)

    @app.get('/api/settings')
    def get_settings():
        return jsonify(settings.load(data_dir).view())

    @app.put('/api/settings')
    def put_settings():
        return jsonify(settings.update(data_dir, json_object()).view())

    @app.post('/api/settings/check')
    def check_settings():
        return jsonify(retrieval.check(settings.load(data_dir)))

    @app.post('/api/choose-folder')
    def choose_folder():
        body = request.get_json(silent=True)
        start = body.get('start') if isinstance(body, dict) else None
        if not isinstance(start, (str, type(None))):
            raise ValueError('start must be text')
        # One native window at a time; a second click must not stack dialogs on the desktop.
        if not picker_lock.acquire(blocking=False):
            return jsonify(error='A folder window is already open.'), 409
        try:
            title = body.get('title') if isinstance(body.get('title'), str) else None
            return jsonify(path=picker.choose_folder(start or None, **({'title': title} if title else {})))
        except picker.PickerUnavailable as error:
            return jsonify(error=str(error)), 503
        finally:
            picker_lock.release()

    @app.post('/api/library/inspect')
    def inspect_library():
        return jsonify(location.classify(data_dir, json_object().get('path')))

    @app.post('/api/library/switch')
    def switch_library():
        if switch is None or source in paths.FIXED:
            return jsonify(error=paths.FIXED.get(source, 'This server cannot change its library.')), 409
        values = json_object()
        answer = location.classify(data_dir, values.get('path'))
        if answer['action'] not in ('open', 'move'):
            return jsonify(error=answer['message']), 409
        if values.get('action') != answer['action']:
            return jsonify(error='The folder changed since it was checked. Try again.'), 409
        if any(job['status'] in ('pending', 'running') for job in jobs.values()):
            return jsonify(error='Wait for running tasks to finish.'), 409
        target = Path(values['path'].strip()).expanduser().resolve()
        if answer['action'] == 'open':
            try:
                with library_lock(target):
                    pass
            except LibraryInUse:
                return jsonify(error='That library is open in another PaperAssist. Close it first.'), 409
        else:
            location.move(data_dir, target)
        paths.save_library(target)
        response = jsonify(path=str(target))
        # The server stops only after this response has been sent.
        response.call_on_close(lambda: switch(target))
        return response

    @app.get('/api/documents')
    def documents():
        return jsonify(library.list_documents(request.args.get('q', '')))

    def json_object():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        return value

    @app.post('/api/import')
    def imports():
        if request.is_json:
            paths = json_object().get('paths')
            if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
                raise ValueError('paths must be a list of non-empty strings')
            return submit(lambda job, cancel: library.import_paths(paths, progress=lambda done, total: job.update(progress={'completed': done, 'total': total}), cancel=cancel))
        staging = Path(tempfile.mkdtemp(prefix='paperassist-upload-', dir=data_dir))
        files = []
        try:
            for index, file in enumerate(request.files.getlist('files')):
                path = staging / str(index)
                with path.open('wb') as output:
                    shutil.copyfileobj(file.stream, output, length=1024 * 1024)
                files.append((file.filename, path))
        except Exception:
            shutil.rmtree(staging)
            raise
        def import_files(job, cancel):
            results = []
            try:
                for index, (name, path) in enumerate(files):
                    if cancel.is_set():
                        break
                    try:
                        with path.open('rb') as stream:
                            results.append({'filename': name, 'document': library.import_stream(stream, name)})
                    except Exception as error:
                        results.append({'filename': name, 'error': str(error)})
                    job['progress'] = {'completed': index + 1, 'total': len(files)}
                return {'files': results}
            finally:
                shutil.rmtree(staging)
        response = submit(import_files)
        if response[1] != 202:
            shutil.rmtree(staging)
        return response

    @app.get('/api/jobs/<job_id>')
    def job_status(job_id):
        if job_id not in jobs:
            abort(404)
        return jsonify(jobs[job_id])

    @app.post('/api/jobs/<job_id>/cancel')
    def cancel_job(job_id):
        if job_id not in cancellations:
            abort(404)
        cancellations[job_id].set()
        return jsonify(jobs[job_id])

    @app.get('/api/documents/<document_id>/pdf')
    def pdf(document_id):
        return send_file(library.get_path(document_id, annotated=request.args.get('original') != '1'), mimetype='application/pdf', conditional=True)

    @app.patch('/api/documents/<document_id>')
    def metadata(document_id):
        return jsonify(library.update_metadata(document_id, json_object()))

    @app.put('/api/documents/<document_id>/label')
    def label(document_id):
        return jsonify(library.set_label(document_id, json_object().get('label')))

    @app.put('/api/documents/<document_id>/pdf')
    def save_pdf(document_id):
        revision = request.headers.get('X-PaperAssist-Revision')
        if revision is None:
            raise ValueError('Revision is required')
        return jsonify(library.save_pdf(document_id, request.get_data(), int(revision)))

    @app.route('/api/documents/<document_id>/notes', methods=['GET', 'PUT'])
    def notes(document_id):
        if request.method == 'PUT':
            return jsonify(library.save_notes(document_id, request.get_json()))
        return jsonify(library.get_notes(document_id))

    @app.get('/api/documents/<document_id>/references')
    def get_references(document_id):
        library.get_document(document_id)
        return jsonify(references.get(library, document_id))

    @app.post('/api/documents/<document_id>/references/analyze')
    def analyze_references(document_id):
        library.get_document(document_id)
        return submit(lambda job, cancel: references.analyze(library, document_id))

    @app.patch('/api/documents/<document_id>/references/<reference_id>')
    def link_reference(document_id, reference_id):
        values = json_object()
        if 'local_document_id' not in values:
            raise ValueError('local_document_id is required')
        target = values['local_document_id']
        if target is not None and (not isinstance(target, (str, int)) or isinstance(target, bool) or not str(target).isdigit()):
            raise ValueError('local_document_id must be a document ID or null')
        return jsonify(references.link(library, document_id, reference_id, target))

    @app.post('/api/documents/<document_id>/references/<reference_id>/resolve')
    def resolve_reference(document_id, reference_id):
        ref = next((r for r in references.get(library, document_id)['references'] if r['id'] == reference_id), None)
        if not ref:
            abort(404)
        if not ref.get('doi'):
            raise ValueError('The reference has no DOI to look up')
        return submit(lambda job, cancel: retrieval.crossref_metadata(ref['doi'], settings.load(data_dir)))

    @app.post('/api/documents/<document_id>/references/download')
    def download_references(document_id):
        library.get_document(document_id)
        values = json_object().get('ids')
        if not isinstance(values, list) or not values or any(not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).isdigit() for value in values):
            raise ValueError('ids must be a non-empty list of reference IDs')
        ids = {str(value) for value in values}
        refs = [r for r in references.get(library, document_id)['references'] if r['id'] in ids]
        if ids != {r['id'] for r in refs}:
            raise ValueError('Unknown reference ID')
        def download(job, cancel):
            results = []
            for index, ref in enumerate(refs):
                if cancel.is_set():
                    break
                try:
                    with download_lock:
                        current = references.get(library, document_id)['references']
                        ref = next((r for r in current if r['id'] == ref['id']), ref)
                        job['progress'] = {'completed': index, 'total': len(refs), 'reference_id': ref['id'], 'provider': 'local'}
                        def progress(provider):
                            job['progress'] = {'completed': index, 'total': len(refs), 'reference_id': ref['id'], 'provider': provider}
                        result = retrieval.download(library, ref, progress=progress, settings=settings.load(data_dir))
                        if result.get('document_id'):
                            references.link(library, document_id, ref['id'], result['document_id'])
                except Exception as error:
                    result = {'version': 2, 'status': 'failed', 'summary': f'Search failed: {error}', 'attempts': []}
                references.set_retrieval(library, document_id, ref['id'], result)
                results.append({'id': ref['id'], **result})
                job['progress'] = {'completed': index + 1, 'total': len(refs)}
            return {'references': results}
        return submit(download)

    @app.post('/api/documents/<document_id>/references/<reference_id>/check-folder')
    def check_folder(document_id, reference_id):
        library.get_document(document_id)
        def check(job, cancel):
            with download_lock:
                cfg = settings.load(data_dir)
                return inbox.check_folder(library, document_id, reference_id, cfg.inbox, cfg)
        return submit(check)

    @app.post('/api/documents/<document_id>/references/<reference_id>/link-file')
    def link_file(document_id, reference_id):
        library.get_document(document_id)
        name = json_object().get('name')
        with download_lock:
            return jsonify(inbox.link_file(library, document_id, reference_id, settings.load(data_dir).inbox, name))

    @app.post('/api/documents/<document_id>/references/repair-downloads')
    def repair_downloads(document_id):
        library.get_document(document_id)
        def repair(job, cancel):
            with download_lock:
                return {'documents': references.repair_downloads(library, document_id)}
        return submit(repair)

    return app


class LibraryInUse(RuntimeError):
    pass


def _try_lock(file):
    """Take an exclusive non-blocking lock; raise OSError if another process holds it."""
    if sys.platform == 'win32':
        import msvcrt
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def library_lock(data_dir):
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # The lock is released when the file closes, including when the process dies.
    with (directory / '.server.lock').open('a') as lock:
        try:
            _try_lock(lock)
        except OSError:
            raise LibraryInUse('Library is in use; import through the running app') from None
        yield


def running_url(data_dir):
    """Address of the server holding the library lock, if it recorded one."""
    try:
        return json.loads((Path(data_dir) / '.server.json').read_text(encoding='utf-8'))['url']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def serve(host, port, data_dir, open_browser=False, source='default'):
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
    except ValueError:
        if host != 'localhost':
            raise ValueError('The server must bind to a loopback address')
    previous = None
    while True:
        try:
            following = _serve_library(host, port, data_dir, open_browser, source)
        except Exception as error:
            if previous is None:
                raise
            # The library just chosen cannot start; go back to the one that worked.
            print(f'Could not open {data_dir}: {error}', file=sys.stderr, flush=True)
            paths.save_library(previous)
            data_dir, previous = previous, None
            continue
        if following is None:
            return
        previous, data_dir, source, open_browser = data_dir, following, 'settings', False


def _serve_library(host, port, data_dir, open_browser, source):
    """Serve one library until stopped; return the library to serve next after a switch."""
    chosen = []
    def switch(directory):
        chosen.append(directory)
        # shutdown() waits for the serving loop, so it cannot run on the request thread's behalf inline.
        threading.Thread(target=server.shutdown).start()
    with library_lock(data_dir):
        try:
            server = make_server(host, port, create_app(data_dir, source, switch), threaded=True)
        except (OSError, SystemExit):  # Werkzeug exits when the port is taken
            raise RuntimeError(f'Cannot listen on {host}:{port}; it may be in use. Choose another with --port') from None
        url = f"http://{'[%s]' % host if ':' in host else host}:{server.server_port}"
        marker = Path(data_dir) / '.server.json'
        marker.write_text(json.dumps({'url': url}), encoding='utf-8')
        print(f'Library: {Path(data_dir).resolve()}\nPaperAssist is running at {url} (press Ctrl+C to stop)', flush=True)
        if open_browser:
            # The socket already listens, so the browser's first request waits in the backlog.
            webbrowser.open(url)
        # A service manager stops with SIGTERM; treat it like Ctrl+C so the marker is removed.
        # Only the main thread may install handlers.
        main_thread = threading.current_thread() is threading.main_thread()
        previous = signal.signal(signal.SIGTERM, signal.default_int_handler) if main_thread else None
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if main_thread:
                signal.signal(signal.SIGTERM, previous)
            server.server_close()
            marker.unlink(missing_ok=True)
    return chosen[0] if chosen else None
