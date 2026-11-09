"""Native reader and command-line library import."""

import argparse
import json
import sys
from collections.abc import Sequence

from paperassist.paths import locate_existing


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == 'import':
        return import_main(arguments[1:])
    if arguments and arguments[0] == 'serve':
        print('The web reader has been retired. Run paperassist to open the native reader.', file=sys.stderr)
        return 1
    if arguments and arguments[0] == 'desktop':
        arguments.pop(0)  # Keep existing desktop launch commands working.
    from paperassist.native.__main__ import main as native_main
    return native_main(arguments)


def import_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog='paperassist import', description='Import local PDF files and directories.')
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--data-dir', help='library directory (default: PAPERASSIST_DATA_DIR, '
                        'the folder chosen in the app, or the per-user data directory)')
    args = parser.parse_args(argv)
    from paperassist.library import Library
    from paperassist.workspace import library_lock
    try:
        data_dir, _ = locate_existing(args.data_dir)
        with library_lock(data_dir):
            print(json.dumps(Library(data_dir).import_paths(args.paths), indent=2))
    except (RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0
