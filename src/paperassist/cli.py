"""Command-line entry point."""

import argparse
import sys
import webbrowser
from collections.abc import Sequence

from paperassist import __version__
from paperassist.paths import locate_existing


def _serve(host, port, data_dir, open_browser, source):
    from paperassist.server import LibraryInUse, running_url, serve
    try:
        serve(host, port, data_dir, open_browser, source)
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


def _desktop(data_dir, source, port):
    from paperassist.desktop import run
    return run(data_dir, source=source, port=port)


def desktop_main(argv: Sequence[str] | None = None) -> int:
    return main(['desktop', *(sys.argv[1:] if argv is None else argv)])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='paperassist', description='Local research paper reader and library. '
                                     'Without a command, start the reader and open it in the browser.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    commands = parser.add_subparsers(dest='command')
    server = commands.add_parser('serve', help='Start the local reader without opening a browser')
    server.add_argument('--host', default='127.0.0.1')
    server.add_argument('--port', type=int, default=8765)
    server.add_argument('--data-dir', help='library directory (default: PAPERASSIST_DATA_DIR, the folder chosen in the app, or the per-user data directory)')
    server.add_argument('--open', action='store_true', help='open the reader in the default browser')
    desktop = commands.add_parser('desktop', help='Open the reader in a dedicated desktop window')
    desktop.add_argument('--port', type=int, default=8765)
    desktop.add_argument('--data-dir', help='library directory (same defaults as serve)')
    importer = commands.add_parser('import', help='Import local PDF files and directories')
    importer.add_argument('paths', nargs='+')
    importer.add_argument('--data-dir', help='library directory (default: PAPERASSIST_DATA_DIR, the folder chosen in the app, or the per-user data directory)')
    args = parser.parse_args(argv)
    try:
        data_dir, source = locate_existing(getattr(args, 'data_dir', None))
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    if args.command is None:
        return _serve('127.0.0.1', 8765, data_dir, True, source)
    if args.command == 'serve':
        return _serve(args.host, args.port, data_dir, args.open, source)
    if args.command == 'desktop':
        return _desktop(data_dir, source, args.port)
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
