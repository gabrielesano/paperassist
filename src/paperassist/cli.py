"""Command-line entry point."""

import argparse
import sys
import webbrowser
from collections.abc import Sequence

from paperassist import __version__
from paperassist.paths import resolve_data_dir


def _serve(host, port, data_dir, open_browser):
    from paperassist.server import LibraryInUse, running_url, serve
    try:
        serve(host, port, data_dir, open_browser)
    except LibraryInUse:
        url = running_url(data_dir)
        print(f'This library is already open in a running PaperAssist server: {data_dir}', file=sys.stderr)
        if url:
            print(f'Use it at {url}', file=sys.stderr)
            if open_browser:
                webbrowser.open(url)
                return 0
        return 1
    except (RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='paperassist', description='Local research paper reader and library. '
                                     'Without a command, start the reader and open it in the browser.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    commands = parser.add_subparsers(dest='command')
    server = commands.add_parser('serve', help='Start the local reader without opening a browser')
    server.add_argument('--host', default='127.0.0.1')
    server.add_argument('--port', type=int, default=8765)
    server.add_argument('--data-dir', help='library directory (default: PAPERASSIST_DATA_DIR or the per-user data directory)')
    server.add_argument('--open', action='store_true', help='open the reader in the default browser')
    importer = commands.add_parser('import', help='Import local PDF files and directories')
    importer.add_argument('paths', nargs='+')
    importer.add_argument('--data-dir', help='library directory (default: PAPERASSIST_DATA_DIR or the per-user data directory)')
    args = parser.parse_args(argv)
    if args.command is None:
        return _serve('127.0.0.1', 8765, resolve_data_dir(), True)
    data_dir = resolve_data_dir(args.data_dir)
    if args.command == 'serve':
        return _serve(args.host, args.port, data_dir, args.open)
    import json
    from paperassist.library import Library
    from paperassist.server import library_lock
    try:
        with library_lock(data_dir):
            print(json.dumps(Library(data_dir).import_paths(args.paths), indent=2))
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0
