"""Shared library operations and bounded background jobs.

A host owns the process-level library_lock for the workspace lifetime. Call close()
before releasing it. Jobs return the same polling records to every kind of UI.
"""

import copy
import shutil
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from paperassist.library import Library
from paperassist import inbox, location, metadata_refresh, paths, references, retrieval, settings


class WorkspaceConflict(ValueError):
    pass


class JobsFull(RuntimeError):
    pass


class ReferenceNotFound(KeyError):
    pass


class _Job(dict):
    """Publish replacement values under the polling lock; workers leave nested values untouched."""
    def __init__(self, lock, **values):
        self.lock = lock
        super().__init__(values)

    def __setitem__(self, key, value):
        with self.lock:
            super().__setitem__(key, value)

    def update(self, *args, **kwargs):
        with self.lock:
            super().update(*args, **kwargs)


class Workspace:
    def __init__(self, data_dir, source='default'):
        self.data_dir = Path(data_dir).resolve()
        self.source = source
        self.library = Library(self.data_dir)
        self.jobs = {}
        self.cancellations = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='paperassist')
        self.slots = threading.BoundedSemaphore(8)
        self.download_lock = threading.Lock()
        self.job_lock = threading.RLock()

    def close(self):
        for cancel in list(self.cancellations.values()):
            cancel.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def get_settings(self):
        return settings.load(self.data_dir)

    def update_settings(self, changes):
        return settings.update(self.data_dir, changes)

    def check_settings(self):
        return retrieval.check(self.get_settings())

    def get_preferences(self):
        return settings.preferences()

    def save_preferences(self, changes, only_if_missing=False):
        return settings.update_preferences(changes, only_if_missing)

    def get_view(self, document_id):
        return self.library.get_view(document_id)

    def save_view(self, document_id, view, only_if_missing=False):
        return self.library.save_view(document_id, view, only_if_missing)

    def submit(self, task, cleanup=None):
        with self.job_lock:
            return self._submit(task, cleanup)

    def _submit(self, task, cleanup):
        if not self.slots.acquire(blocking=False):
            raise JobsFull('Too many jobs; try again later')
        while len(self.jobs) >= 50:
            old = next((key for key, value in self.jobs.items() if value['status'] in ('completed', 'failed', 'cancelled')), None)
            if old is None:
                break
            self.jobs.pop(old)
            self.cancellations.pop(old, None)
        job_id = uuid.uuid4().hex
        cancel = self.cancellations[job_id] = threading.Event()
        job = self.jobs[job_id] = _Job(self.job_lock, id=job_id, status='pending')
        def run():
            job['status'] = 'running'
            try:
                job['result'] = task(job, cancel)
                job['status'] = 'cancelled' if cancel.is_set() else 'completed'
            except Exception as error:
                job['error'] = str(error)
                job['status'] = 'failed'
            finally:
                self.slots.release()
        try:
            future = self.executor.submit(run)
        except Exception:
            self.slots.release()
            self.jobs.pop(job_id)
            self.cancellations.pop(job_id)
            raise
        def finished(future):
            if future.cancelled():
                job['status'] = 'cancelled'
                try:
                    # An upload never started its task's finally block.
                    if cleanup:
                        cleanup()
                finally:
                    self.slots.release()
        future.add_done_callback(finished)
        return copy.deepcopy(dict(job))

    def job_status(self, job_id):
        with self.job_lock:
            return copy.deepcopy(dict(self.jobs[job_id]))

    def cancel_job(self, job_id):
        self.cancellations[job_id].set()
        return self.job_status(job_id)

    def inspect_library(self, path):
        return location.classify(self.data_dir, path)

    def prepare_switch(self, path, action):
        """Validate and persist the choice; the host closes this workspace, then opens it."""
        with self.job_lock:
            if self.source in paths.FIXED:
                raise WorkspaceConflict(paths.FIXED[self.source])
            answer = self.inspect_library(path)
            if answer['action'] not in ('open', 'move'):
                raise WorkspaceConflict(answer['message'])
            if action != answer['action']:
                raise WorkspaceConflict('The folder changed since it was checked. Try again.')
            if any(job['status'] in ('pending', 'running') for job in self.jobs.values()):
                raise WorkspaceConflict('Wait for running tasks to finish.')
            target = Path(path.strip()).expanduser().resolve()
            if action == 'open':
                try:
                    with library_lock(target):
                        pass
                except LibraryInUse:
                    raise WorkspaceConflict('That library is open in another PaperAssist. Close it first.') from None
            else:
                location.move(self.data_dir, target)
            paths.save_library(target)
            return target

    def import_paths(self, paths):
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise ValueError('paths must be a list of non-empty strings')
        return self.submit(lambda job, cancel: self.library.import_paths(
            paths, progress=lambda done, total: job.update(progress={'completed': done, 'total': total}), cancel=cancel))

    def open_files(self, paths):
        """Import/reuse external opening requests without blocking the reader thread."""
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path for path in paths):
            raise ValueError('Choose local PDF files to open.')
        def open_files(job, cancel):
            result = {'documents': [], 'errors': []}
            for index, path in enumerate(paths):
                if cancel.is_set():
                    break
                try:
                    result['documents'].append(self.library.open_file(path))
                except (OSError, ValueError, KeyError) as error:
                    result['errors'].append({'path': path, 'error': str(error)})
                job['progress'] = {'completed': index + 1, 'total': len(paths)}
            return result
        return self.submit(open_files)

    def refresh_metadata(self, document_ids=None, *, online=True, ocr='auto'):
        """Refresh a snapshot of the whole library, or the explicitly selected PDFs.

        ocr: 'off', 'auto' (only scans native extraction cannot read) or 'force'.
        """
        if ocr not in metadata_refresh.OCR_MODES:
            raise ValueError("ocr must be one of: " + ", ".join(metadata_refresh.OCR_MODES))
        if document_ids is None:
            document_ids = [document['id'] for document in self.library.list_documents()]
        elif (not isinstance(document_ids, list) or not document_ids
              or any(type(value) is not int or value <= 0 for value in document_ids)):
            raise ValueError('document_ids must be a non-empty list of positive integers')
        document_ids = list(dict.fromkeys(document_ids))
        cfg = self.get_settings()
        return self.submit(lambda job, cancel: metadata_refresh.refresh(
            self.library, document_ids, cfg, online, job, cancel, ocr))

    def import_files(self, streams):
        """Stage (name, binary stream) uploads before returning; the job owns their cleanup."""
        staging = Path(tempfile.mkdtemp(prefix='paperassist-upload-', dir=self.data_dir))
        files = []
        try:
            for index, (name, stream) in enumerate(streams):
                path = staging / str(index)
                with path.open('wb') as output:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
                files.append((name, path))
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
                            results.append({'filename': name, 'document': self.library.import_stream(stream, name)})
                    except Exception as error:
                        results.append({'filename': name, 'error': str(error)})
                    job['progress'] = {'completed': index + 1, 'total': len(files)}
                return {'files': results}
            finally:
                shutil.rmtree(staging)
        try:
            return self.submit(import_files, cleanup=lambda: shutil.rmtree(staging))
        except Exception:
            shutil.rmtree(staging)
            raise

    def analyze_references(self, document_id):
        self.library.get_document(document_id)
        return self.submit(lambda job, cancel: references.analyze(self.library, document_id))

    def resolve_reference(self, document_id, reference_id):
        ref = next((r for r in references.get(self.library, document_id)['references'] if r['id'] == reference_id), None)
        if not ref:
            raise ReferenceNotFound(reference_id)
        if not ref.get('doi'):
            raise ValueError('The reference has no DOI to look up')
        return self.submit(lambda job, cancel: retrieval.crossref_metadata(ref['doi'], self.get_settings()))

    def download_references(self, document_id, values):
        self.library.get_document(document_id)
        if not isinstance(values, list) or not values or any(
                not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value) for value in values):
            raise ValueError('ids must be a non-empty list of reference IDs')
        ids = {str(value) for value in values}
        refs = [r for r in references.get(self.library, document_id)['references'] if r['id'] in ids]
        if ids != {r['id'] for r in refs}:
            raise ValueError('Unknown reference ID')
        def download(job, cancel):
            results = []
            for index, ref in enumerate(refs):
                if cancel.is_set():
                    break
                try:
                    with self.download_lock:
                        current = references.get(self.library, document_id)['references']
                        ref = next((r for r in current if r['id'] == ref['id']), None)
                        if ref is None:
                            raise ReferenceNotFound('Reference changed during analysis')
                        job['progress'] = {'completed': index, 'total': len(refs), 'reference_id': ref['id'], 'provider': 'local'}
                        def progress(provider):
                            job['progress'] = {'completed': index, 'total': len(refs), 'reference_id': ref['id'], 'provider': provider}
                        result = retrieval.download(self.library, ref, progress=progress, settings=self.get_settings())
                        if result.get('document_id') and str(result['document_id']) != str(document_id):
                            references.link(self.library, document_id, ref['id'], result['document_id'])
                        references.set_retrieval(self.library, document_id, ref['id'], result)
                except Exception as error:
                    result = {'version': 2, 'status': 'failed', 'summary': f'Search failed: {error}', 'attempts': []}
                    if ref is not None:
                        try:
                            with self.download_lock:
                                references.set_retrieval(self.library, document_id, ref['id'], result)
                        except KeyError:
                            pass  # Re-analysis can remove a reference while a download is in flight.
                results.append({'id': refs[index]['id'], **result})
                job['progress'] = {'completed': index + 1, 'total': len(refs)}
            return {'references': results}
        return self.submit(download)

    def check_folder(self, document_id, reference_id):
        self.library.get_document(document_id)
        def check(job, cancel):
            with self.download_lock:
                cfg = self.get_settings()
                return inbox.check_folder(self.library, document_id, reference_id, cfg.inbox, cfg)
        return self.submit(check)

    def link_file(self, document_id, reference_id, name):
        self.library.get_document(document_id)
        with self.download_lock:
            return inbox.link_file(self.library, document_id, reference_id, self.get_settings().inbox, name)

    def repair_downloads(self, document_id):
        self.library.get_document(document_id)
        def repair(job, cancel):
            with self.download_lock:
                return {'documents': references.repair_downloads(self.library, document_id)}
        return self.submit(repair)


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
    # Keep the legacy filename so older readers and CLI imports share this lock.
    # The lock is released when the file closes, including when the process dies.
    with (directory / '.server.lock').open('a') as lock:
        try:
            _try_lock(lock)
        except OSError:
            raise LibraryInUse('Library is in use; import through the running app') from None
        yield

