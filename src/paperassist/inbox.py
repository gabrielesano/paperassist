"""Link PDFs the user downloaded by hand into a watched folder.

The folder is only ever read: files are verified against the reference, then a copy is
imported into the library. Nothing in the folder is written, moved or deleted.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pypdf import PdfReader

from paperassist import references
from paperassist.citation import fold, pdf_name, publication
from paperassist.retrieval import DOI_PATTERN, MAX_PDF, _arxiv_id

MAX_FILES = 50
MAX_CANDIDATES = 5
# A download made recently is plausibly the one the user wants even when it cannot be verified.
RECENT = timedelta(days=14)
NO_FOLDER = {'status': 'not_configured', 'summary': 'Set a folder for manual downloads in Sources.'}


def _squash(text):
    return re.sub(r'[^a-z0-9]', '', fold(text))


def _read(path):
    """First-page text and PDF properties; empty text means the file cannot be checked."""
    try:
        reader = PdfReader(str(path))
        text = reader.pages[0].extract_text() or ''
        properties = ' '.join(str(value) for value in (reader.metadata or {}).values())
    except Exception:
        return {'text': '', 'properties': ''}
    return {'text': text if len(text.strip()) > 100 else '', 'properties': properties}


def _entry(path):
    info = path.stat()
    return {'name': path.name, 'path': path, 'size': info.st_size, 'mtime': info.st_mtime}


def _scan(folder):
    files = [p for p in folder.iterdir() if p.suffix.lower() == '.pdf' and not p.is_symlink() and p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [entry for entry in map(_entry, files[:MAX_FILES]) if entry['size'] <= MAX_PDF]


def _has_surname(text, surname):
    return bool(surname) and bool(re.search(r'(?<!\w)' + re.escape(fold(surname)) + r'(?!\w)', fold(text)))


def _verdict(ref, file):
    """'verified', 'rejected' (contradicted), 'unmatched' or 'unreadable' for one file."""
    if not file['text']:
        return 'unreadable'
    identity = ref.get('identity') or {}
    doi = (ref.get('doi') or identity.get('doi') or '').lower()
    surname = ref.get('authors') or identity.get('first_author') or ''
    text, lines = file['text'], file['text'].splitlines()
    own = [value.rstrip('.,;').lower() for value in DOI_PATTERN.findall(file['properties'])]
    found = publication('\n'.join(lines[:6]))
    # Reuse the library's own contradiction rules: a different DOI or a different journal reference.
    if references._conflict({**ref, 'doi': doi}, {'doi': doi if doi in own else own[0] if own else '', 'year': None}, {'publication': found}):
        return 'rejected'
    cited = publication(ref.get('raw') or '', ref.get('year', ''))
    printed = ' '.join(f'{text} {file["properties"]}'.lower().split())
    arxiv = re.sub(r'v\d+$', '', _arxiv_id(ref).lower())
    title = _squash(identity.get('title', ''))
    checks = (
        bool(doi) and bool(re.search(re.escape(doi) + r'(?![-_;/:a-z0-9]|\.[a-z0-9])', printed)),
        bool(arxiv) and bool(re.search(re.escape(arxiv) + r'(?!\d)', printed)),
        bool(cited) and found == cited and _has_surname(text, surname),
        len(title) >= 15 and title in _squash(text) and _has_surname(text, surname))
    return 'verified' if any(checks) else 'unmatched'


def _library_name(ref, original):
    identity = ref.get('identity') or {}
    surname = ref.get('authors') or identity.get('first_author')
    return pdf_name(surname, identity.get('year') or str(ref.get('year') or '')[:4], identity.get('publication')) \
        if identity and surname else original


def _import(library, document_id, ref, path, verified):
    name = _library_name(ref, path.name)
    with path.open('rb') as stream:
        document = library.import_stream(stream, name)
    if verified and ref.get('identity'):
        library.apply_verified(document['id'], ref['identity'], name)
    references.link(library, document_id, ref['id'], document['id'])
    summary = f'Linked from your folder: {path.name}.'
    references.set_retrieval(library, document_id, ref['id'], {'version': 2, 'status': 'local', 'summary': summary,
                                                                'attempts': [], 'document_id': document['id']})
    return document, summary


def _find_reference(library, document_id, reference_id):
    ref = next((r for r in references.get(library, document_id)['references'] if r['id'] == str(reference_id)), None)
    if ref is None:
        raise KeyError('Reference not found')
    return ref


def _candidate(entry):
    return {'name': entry['name'], 'size': entry['size'],
            'modified': datetime.fromtimestamp(entry['mtime'], timezone.utc).isoformat()}


def check_folder(library, document_id, reference_id, folder):
    if not folder:
        return NO_FOLDER
    ref = _find_reference(library, document_id, reference_id)
    verdicts = [(entry, _verdict(ref, {**entry, **_read(entry['path'])})) for entry in _scan(Path(folder))]
    verified = [entry for entry, verdict in verdicts if verdict == 'verified']
    # The same paper downloaded twice is one candidate, not an ambiguity.
    unique = list({hashlib.sha256(entry['path'].read_bytes()).hexdigest(): entry for entry in verified}.values()) if len(verified) > 1 else verified
    if len(unique) > 1:
        return {'status': 'ambiguous', 'candidates': [_candidate(e) for e in unique[:MAX_CANDIDATES]],
                'summary': 'Several PDFs in your folder match this reference; choose one.'}
    if unique:
        document, summary = _import(library, document_id, ref, unique[0]['path'], True)
        return {'status': 'linked', 'document_id': document['id'], 'filename': document['filename'], 'summary': summary}
    recent = datetime.now(timezone.utc).timestamp() - RECENT.total_seconds()
    open_ones = [e for e, verdict in verdicts if verdict in ('unmatched', 'unreadable') and e['mtime'] >= recent]
    if open_ones:
        return {'status': 'unverified', 'candidates': [_candidate(e) for e in open_ones[:MAX_CANDIDATES]],
                'summary': 'No PDF could be verified automatically; choose one if it is the right paper.'}
    return {'status': 'none', 'summary': f'No PDF in {folder} matches this reference.'}


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
