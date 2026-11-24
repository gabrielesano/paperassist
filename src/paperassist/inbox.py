"""Link PDFs the user downloaded by hand into a watched folder.

The folder is only ever read: files are verified against the reference, then a copy is
imported into the library under a deterministic name (author, year, journal reference),
never the browser's download name. Nothing in the folder is written, moved or deleted.
"""

import hashlib
import os
import re
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pypdf import PdfReader

from paperassist import references, retrieval
from paperassist.citation import DOI, arxiv_id, has_surname, pdf_name, publication, squash, strip_arxiv_version
from paperassist.http_client import MAX_PDF
from paperassist.settings import Settings

MAX_FILES = 50
MAX_CANDIDATES = 5
# A download made recently is plausibly the one the user wants even when it cannot be verified.
RECENT = timedelta(days=14)
NO_FOLDER = {'status': 'not_configured', 'summary': 'Set a folder for manual downloads in Preferences.'}


def _read(path):
    """First-page text and PDF properties; empty text means the file cannot be checked."""
    try:
        reader = PdfReader(str(path))
        text = reader.pages[0].extract_text() or ''
        properties = ' '.join(str(value) for value in (reader.metadata or {}).values())
    except Exception:
        return {'text': '', 'properties': ''}
    return {'text': text if len(text.strip()) > 100 else '', 'properties': properties}


def _scan(folder):
    entries = []
    with os.scandir(folder) as scan:
        for entry in scan:
            if not entry.name.lower().endswith('.pdf') or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            info = entry.stat(follow_symlinks=False)
            entries.append({'name': entry.name, 'path': Path(entry.path), 'size': info.st_size, 'mtime': info.st_mtime})
    entries.sort(key=lambda item: item['mtime'], reverse=True)
    return [entry for entry in entries[:MAX_FILES] if entry['size'] <= MAX_PDF]


def _sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _verdict(ref, file):
    """'verified', 'rejected' (contradicted), 'unmatched' or 'unreadable' for one file."""
    if not file['text']:
        return 'unreadable'
    identity = ref.get('identity') or {}
    doi = (ref.get('doi') or identity.get('doi') or '').lower()
    surname = ref.get('authors') or identity.get('first_author') or ''
    text, lines = file['text'], file['text'].splitlines()
    own = [value.rstrip('.,;').lower() for value in DOI.findall(file['properties'])]
    found = publication('\n'.join(lines[:6]))
    # Reuse the library's own contradiction rules: a different DOI or a different journal reference.
    if references.has_conflict({**ref, 'doi': doi}, {'doi': doi if doi in own else own[0] if own else '', 'year': None}, {'publication': found}):
        return 'rejected'
    cited = publication(ref.get('raw') or '', ref.get('year', ''))
    printed = ' '.join(f'{text} {file["properties"]}'.lower().split())
    arxiv = strip_arxiv_version(arxiv_id(ref))
    title = squash(identity.get('title', ''))
    checks = (
        bool(doi) and bool(re.search(re.escape(doi) + r'(?![-_;/:a-z0-9]|\.[a-z0-9])', printed)),
        bool(arxiv) and bool(re.search(re.escape(arxiv) + r'(?!\d)', printed)),
        bool(cited) and found == cited and has_surname(text, surname),
        len(title) >= 15 and title in squash(text) and has_surname(text, surname))
    return 'verified' if any(checks) else 'unmatched'


def _library_name(ref, original):
    """The identified paper's name, else one read from the citation, else the download name."""
    identity = ref.get('identity') or {}
    year = str(identity.get('year') or ref.get('year') or '')[:4]
    surname = identity.get('first_author') or ref.get('authors')
    if not surname or pdf_name(surname, '') == '.pdf':
        return original  # a bare year would be a meaningless file name
    fingerprint = identity.get('publication') if identity else publication(ref.get('raw') or '', year)
    return pdf_name(surname, year, fingerprint)


def _import(library, document_id, ref, path, verified, note=''):
    name = _library_name(ref, path.name)
    with path.open('rb') as stream:
        document = library.import_stream(stream, name)
    # A duplicate that is still unreviewed can now be verified; a user correction is never touched.
    if verified and ref.get('identity') and document['metadata_status'] == 'needs_review':
        document = library.apply_verified(document['id'], ref['identity'], name)
    stored = document['filename']
    summary = f'Linked from your folder as {stored}' + (f' (was {path.name}).' if stored != path.name else '.')
    summary += note if verified else ' Chosen by hand, not verified.'
    references.set_retrieval(library, document_id, ref['id'], {'version': 2, 'status': 'local', 'summary': summary,
                                                                'attempts': [], 'document_id': document['id']},
                             target_id=document['id'])
    return document, summary


def _identify(library, document_id, ref, cfg):
    """Identify an unidentified reference like a search does, keeping the result for later actions.

    Returns the reference and a note for the summary when identification did not succeed.
    """
    if ref.get('identity'):
        return ref, ''
    try:
        identity = retrieval.resolve_identity(ref, cfg)
    except Exception:
        identity = None  # network or lookup trouble must not break the folder check
    if not identity:
        return ref, ' The reference could not be identified online.'
    # Keep an earlier search result visible; only the identity is new.
    previous = ref.get('retrieval') or {'version': 2, 'status': 'identified', 'summary': '', 'attempts': []}
    references.set_retrieval(library, document_id, ref['id'], {**previous, 'identity': identity})
    return {**ref, 'identity': identity, 'doi': ref.get('doi') or identity.get('doi', '')}, ''


def _find_reference(library, document_id, reference_id):
    ref = next((r for r in references.get(library, document_id, resolve=False)['references'] if r['id'] == str(reference_id)), None)
    if ref is None:
        raise KeyError('Reference not found')
    return ref


def _candidate(entry):
    return {'name': entry['name'], 'size': entry['size'],
            'modified': datetime.fromtimestamp(entry['mtime'], timezone.utc).isoformat()}


def _scan_and_read(folder):
    return [{**entry, **_read(entry['path'])} for entry in _scan(Path(folder))]


def _verified_entries(ref, scanned, digests=None):
    verdicts = [(entry, _verdict(ref, entry)) for entry in scanned]
    verified = [entry for entry, verdict in verdicts if verdict == 'verified']
    if len(verified) <= 1:
        return verdicts, verified
    # The same paper downloaded twice is one candidate, not an ambiguity.
    seen = digests if digests is not None else {}
    unique = {}
    for entry in verified:
        digest = seen.get(entry['path'])
        if digest is None:
            digest = seen[entry['path']] = _sha256(entry['path'])
        unique.setdefault(digest, entry)
    return verdicts, list(unique.values())


def check_folder(library, document_id, reference_id, folder, cfg=None):
    if not folder:
        return NO_FOLDER
    ref, note = _identify(library, document_id, _find_reference(library, document_id, reference_id), cfg or Settings())
    scanned = _scan_and_read(folder)
    verdicts, unique = _verified_entries(ref, scanned)
    if len(unique) > 1:
        return {'status': 'ambiguous', 'candidates': [_candidate(e) for e in unique[:MAX_CANDIDATES]],
                'summary': 'Several PDFs in your folder match this reference; choose one.' + note}
    if unique:
        document, summary = _import(library, document_id, ref, unique[0]['path'], True, note)
        return {'status': 'linked', 'document_id': document['id'], 'filename': document['filename'], 'summary': summary}
    recent = datetime.now(timezone.utc).timestamp() - RECENT.total_seconds()
    open_ones = [e for e, verdict in verdicts if verdict in ('unmatched', 'unreadable') and e['mtime'] >= recent]
    if open_ones:
        return {'status': 'unverified', 'candidates': [_candidate(e) for e in open_ones[:MAX_CANDIDATES]],
                'summary': 'No PDF could be verified automatically; choose one if it is the right paper.' + note}
    return {'status': 'none', 'summary': f'No PDF in {folder} matches this reference.' + note}


def check_folder_batch(library, document_id, folder, *, reference_ids=None, progress=None, cancel=None, lock=None):
    """Scan `folder` once and import unambiguous verified PDFs for eligible missing references."""
    if not folder:
        return NO_FOLDER
    guard = lock if lock is not None else nullcontext()
    with guard:
        current = references.get(library, document_id)['references']
    wanted = {str(v) for v in reference_ids} if reference_ids is not None else None
    targets = [r for r in current if not r.get('local_document_id') and not r.get('legacy')
               and (wanted is None or str(r['id']) in wanted)]
    if not targets:
        return {'status': 'none', 'linked': [], 'ambiguous': [],
                'summary': 'All eligible references are already in the library.'}
    scanned = _scan_and_read(folder)
    if not scanned:
        return {'status': 'none', 'linked': [], 'ambiguous': [],
                'summary': f'No verified PDF match found in {folder}.'}
    linked, ambiguous, digests = [], [], {}
    for index, ref in enumerate(targets):
        if cancel is not None and cancel.is_set():
            break
        if progress is not None:
            progress(index, len(targets), ref['id'])
        _, unique = _verified_entries(ref, scanned, digests)
        if len(unique) > 1:
            ambiguous.append(ref['id'])
        elif len(unique) == 1:
            with guard:
                latest = next((r for r in references.get(library, document_id, resolve=False)['references']
                               if r['id'] == ref['id']), None)
                if latest is not None and not latest.get('local_document_id'):
                    document, _ = _import(library, document_id, latest, unique[0]['path'], True)
                    linked.append({'reference_id': latest['id'], 'document_id': document['id'],
                                   'filename': document['filename']})
        if progress is not None:
            progress(index + 1, len(targets), ref['id'])
    n, m = len(linked), len(ambiguous)
    status = 'linked' if n else 'ambiguous' if m else 'none'
    if n and m:
        summary = (f"Linked {n} PDF{'s' if n != 1 else ''} from your folder "
                   f"({m} reference{'s have' if m != 1 else ' has'} multiple matches — use Check folder on the card).")
    elif n:
        summary = f"Linked {n} PDF{'s' if n != 1 else ''} from your folder."
    elif m:
        summary = f"{m} reference{'s have' if m != 1 else ' has'} multiple matching PDFs in your folder; use Check folder on the card to choose."
    else:
        summary = f'No verified PDF match found in {folder}.'
    return {'status': status, 'linked': linked, 'ambiguous': ambiguous, 'summary': summary}


def link_file(library, document_id, reference_id, folder, name):
    """Link a file the user chose among the unverified candidates; the name is never a path."""
    if not folder:
        raise ValueError(NO_FOLDER['summary'])
    root = Path(folder).resolve()
    if not isinstance(name, str) or name in ('', '.', '..') or Path(name).name != name or '\\' in name or Path(name).suffix.lower() != '.pdf':
        raise ValueError('name must be the file name of a PDF in the folder')
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError('name must be the file name of a PDF in the folder')
    if not path.is_file():
        raise FileNotFoundError('That PDF is no longer in the folder')
    if path.stat().st_size > MAX_PDF:
        raise ValueError('That PDF is too large')
    ref = _find_reference(library, document_id, reference_id)
    _import(library, document_id, ref, path, False)
    return _find_reference(library, document_id, reference_id)
