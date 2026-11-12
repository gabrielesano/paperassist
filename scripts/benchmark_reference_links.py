"""Read-only coverage and timing comparison; this is not an accuracy benchmark."""
import argparse
import hashlib
import json
import logging
import resource
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

from pypdf import PdfReader

from paperassist import references


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--baseline-ref', help='Git revision containing the previous references.py')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    baseline = references
    if args.baseline_ref:
        source = subprocess.run(['git', 'show', f'{args.baseline_ref}:src/paperassist/references.py'],
                                cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True).stdout
        baseline = types.ModuleType('reference_baseline')
        exec(compile(source, '<git baseline references.py>', 'exec'), baseline.__dict__)
    db = sqlite3.connect((args.data_dir / 'library.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT d.id, d.filename, d.stored_name FROM documents d '
                          'JOIN reference_analyses a ON a.source_id = d.id ORDER BY d.id').fetchall()
    finally:
        db.close()
    results = []
    for document_id, filename, stored_name in rows:
        path = args.data_dir / 'originals' / (stored_name or filename)
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).digest()
        started = time.perf_counter()
        reader = PdfReader(path)
        pages = [page.extract_text() or '' for page in reader.pages]
        old = baseline.extract(pages)
        baseline.map_destinations(reader, pages, old)
        old_seconds = time.perf_counter() - started
        started = time.perf_counter()
        refs = references.extract(pages)
        references.map_destinations(reader, pages, refs)
        text_counts = {ref['id']: ref['citation_count'] for ref in refs}
        mapping_seconds = time.perf_counter() - started
        started = time.perf_counter()
        references.crosscheck_links(path, pages, refs)
        old_owners = {name: ref['raw'] for ref in old for name in ref.get('destinations', [])}
        new_owners = {name: ref['raw'] for ref in refs for name in ref.get('destinations', [])}
        result = {'document': str(document_id), 'pages': len(pages), 'references': len(refs),
                  'baseline_mapped': sum(bool(ref.get('destinations')) for ref in old),
                  'mapped': sum(bool(ref.get('destinations')) for ref in refs),
                  'changed_destination_owners': sum(old_owners[name] != new_owners[name]
                                                    for name in old_owners.keys() & new_owners.keys()),
                  'lost_baseline_destinations': len(old_owners.keys() - new_owners.keys()),
                  'text_zeros': sum(count == 0 for count in text_counts.values()),
                  'recovered_zeros': sum(text_counts[ref['id']] == 0 and bool(ref['citation_count']) for ref in refs),
                  'remaining_zeros': sum(ref['citation_count'] == 0 for ref in refs),
                  'text_events': sum(count or 0 for count in text_counts.values()),
                  'reconciled_events': sum(ref['citation_count'] or 0 for ref in refs),
                  'baseline_extract_and_map_seconds': round(old_seconds, 3),
                  'extract_and_map_seconds': round(mapping_seconds, 3),
                  'crosscheck_seconds': round(time.perf_counter() - started, 3),
                  'original_unchanged': hashlib.sha256(path.read_bytes()).digest() == digest}
        results.append(result)
        print(json.dumps(result), flush=True)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    output = {'baseline_revision': args.baseline_ref, 'accuracy_measured': False,
              'peak_rss_mib': round(peak / (1024 ** 2 if sys.platform == 'darwin' else 1024), 1),
              'documents': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + '\n')


if __name__ == '__main__':
    main()
