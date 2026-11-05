"""Loopback-only API for the shared local library."""

import ipaddress
import json
import os
import secrets
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from paperassist.workspace import Workspace, WorkspaceConflict, JobsFull, ReferenceNotFound, LibraryInUse, library_lock
from paperassist import __version__, paths, picker, references, retrieval


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


def create_app(data_dir, source='default', switch=None, choose_folder=None):
    """`switch(directory)` makes the running server serve another library; `source` says who chose this one."""
    data_dir = Path(data_dir).resolve()
    workspace = Workspace(data_dir, source)
    library = workspace.library
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024
    token = _write_token(data_dir)
    picker_lock = threading.Lock()
    web = Path(__file__).parent / 'web'
    app.extensions['paperassist_workspace'] = workspace
    app.extensions['paperassist_shutdown'] = workspace.close

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
        if isinstance(error, JobsFull):
            return jsonify(error=str(error)), 429
        if isinstance(error, WorkspaceConflict):
            return jsonify(error=str(error)), 409
        if isinstance(error, (KeyError, FileNotFoundError)):
            return jsonify(error=str(error)), 404
        if isinstance(error, ValueError):
            return jsonify(error=str(error)), 409 if 'conflict' in str(error).lower() else 400
        app.logger.exception('Request failed')
        return jsonify(error='Request failed'), 500

    @app.get('/')
    def index():
        return send_from_directory(web, 'index.html')

    @app.get('/<path:name>')
    def assets(name):
        return send_from_directory(web, name)

    @app.get('/api/config')
    def config():
        current = workspace.get_settings()
        return jsonify(token=token, version=__version__, library_path=str(data_dir), library_source=source,
                       providers=current.providers(), proxy=current.proxy, inbox=current.inbox)

    @app.get('/api/settings')
    def get_settings():
        return jsonify(workspace.get_settings().view())

    @app.put('/api/settings')
    def put_settings():
        return jsonify(workspace.update_settings(json_object()).view())

    @app.post('/api/settings/check')
    def check_settings():
        return jsonify(workspace.check_settings())

    @app.route('/api/preferences', methods=['GET', 'PUT'])
    def preferences():
        if request.method == 'PUT':
            return jsonify(workspace.save_preferences(json_object(), request.args.get('if_missing') == '1'))
        return jsonify(workspace.get_preferences())

    @app.route('/api/documents/<document_id>/view', methods=['GET', 'PUT'])
    def document_view(document_id):
        if request.method == 'PUT':
            return jsonify(workspace.save_view(document_id, json_object(), request.args.get('if_missing') == '1'))
        return jsonify(workspace.get_view(document_id))

    @app.post('/api/choose-folder')
    def choose_folder_route():
        body = request.get_json(silent=True)
        start = body.get('start') if isinstance(body, dict) else None
        if not isinstance(start, (str, type(None))):
            raise ValueError('start must be text')
        # One native window at a time; a second click must not stack dialogs on the desktop.
        if not picker_lock.acquire(blocking=False):
            return jsonify(error='A folder window is already open.'), 409
        try:
            title = body.get('title') if isinstance(body, dict) and isinstance(body.get('title'), str) else None
            return jsonify(path=(choose_folder or picker.choose_folder)(start or None, **({'title': title} if title else {})))
        except picker.PickerUnavailable as error:
            return jsonify(error=str(error)), 503
        finally:
            picker_lock.release()

    @app.post('/api/library/inspect')
    def inspect_library():
        return jsonify(workspace.inspect_library(json_object().get('path')))

    @app.post('/api/library/switch')
    def switch_library():
        if switch is None or source in paths.FIXED:
            return jsonify(error=paths.FIXED.get(source, 'This server cannot change its library.')), 409
        values = json_object()
        target = workspace.prepare_switch(values.get('path'), values.get('action'))
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
            result = workspace.import_paths(json_object().get('paths'))
        else:
            result = workspace.import_files((file.filename, file.stream) for file in request.files.getlist('files'))
        return jsonify(result), 202

    @app.get('/api/jobs/<job_id>')
    def job_status(job_id):
        try:
            return jsonify(workspace.job_status(job_id))
        except KeyError:
            abort(404)

    @app.post('/api/jobs/<job_id>/cancel')
    def cancel_job(job_id):
        try:
            return jsonify(workspace.cancel_job(job_id))
        except KeyError:
            abort(404)

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
        return jsonify(workspace.analyze_references(document_id)), 202

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
        try:
            return jsonify(workspace.resolve_reference(document_id, reference_id)), 202
        except ReferenceNotFound:
            abort(404)

    @app.post('/api/documents/<document_id>/references/download')
    def download_references(document_id):
        return jsonify(workspace.download_references(document_id, json_object().get('ids'))), 202

    @app.post('/api/documents/<document_id>/references/<reference_id>/check-folder')
    def check_folder(document_id, reference_id):
        return jsonify(workspace.check_folder(document_id, reference_id)), 202

    @app.post('/api/documents/<document_id>/references/<reference_id>/link-file')
    def link_file(document_id, reference_id):
        return jsonify(workspace.link_file(document_id, reference_id, json_object().get('name')))

    @app.post('/api/documents/<document_id>/references/repair-downloads')
    def repair_downloads(document_id):
        return jsonify(workspace.repair_downloads(document_id)), 202

    return app


def running_url(data_dir):
    """Address of the server holding the library lock, if it recorded one."""
    try:
        return json.loads((Path(data_dir) / '.server.json').read_text(encoding='utf-8'))['url']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def serve(host, port, data_dir, open_browser=False, source='default', *, choose_folder=None, on_listen=None):
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
    except ValueError:
        if host != 'localhost':
            raise ValueError('The server must bind to a loopback address')
    previous = None
    while True:
        try:
            following = _serve_library(host, port, data_dir, open_browser, source,
                                       choose_folder=choose_folder, on_listen=on_listen)
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


def _serve_library(host, port, data_dir, open_browser, source, *, choose_folder=None, on_listen=None):
    """Serve one library until stopped; return the library to serve next after a switch."""
    chosen = []
    def switch(directory):
        chosen.append(directory)
        # shutdown() waits for the serving loop, so it cannot run on the request thread's behalf inline.
        threading.Thread(target=server.shutdown).start()
    with library_lock(data_dir):
        app = create_app(data_dir, source, switch, choose_folder)
        try:
            server = make_server(host, port, app, threaded=True)
            # Do not release library ownership while a request can still write to it.
            server.daemon_threads = False
        except (OSError, SystemExit):
            app.extensions['paperassist_shutdown']()  # Werkzeug exits when the port is taken
            raise RuntimeError(f'Cannot listen on {host}:{port}; it may be in use. Choose another with --port') from None
        marker = Path(data_dir) / '.server.json'
        main_thread = threading.current_thread() is threading.main_thread()
        previous = None
        try:
            url = f"http://{'[%s]' % host if ':' in host else host}:{server.server_port}"
            marker.write_text(json.dumps({'url': url}), encoding='utf-8')
            print(f'Library: {Path(data_dir).resolve()}\nPaperAssist is running at {url} (press Ctrl+C to stop)', flush=True)
            if open_browser:
                # The socket listens already; the first browser request waits in its backlog.
                webbrowser.open(url)
            if main_thread:
                previous = signal.signal(signal.SIGTERM, signal.default_int_handler)
            if on_listen:
                on_listen(server)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
            server.server_close()
            app.extensions['paperassist_shutdown']()
            marker.unlink(missing_ok=True)
    return chosen[0] if chosen else None
