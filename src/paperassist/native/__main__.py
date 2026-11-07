"""Run with `python -m paperassist.native file.pdf [file.pdf ...]`."""
import multiprocessing
import sys
from importlib import import_module


def main():
    multiprocessing.freeze_support()  # a frozen app starts its render process through the same executable
    try:
        import_module("PySide6.QtWidgets")
        import_module("pypdfium2")
    except (ImportError, OSError) as error:
        print(f"Cannot start the native reader: {error}", file=sys.stderr)
        if getattr(sys, "frozen", False):
            print("Reinstall a complete PaperAssist build for your operating system.", file=sys.stderr)
        else:
            print('Install native support from the PaperAssist source folder:\n'
                  '  python -m pip install -e ".[native]"\n'
                  'Browser mode: paperassist serve --open', file=sys.stderr)
        raise SystemExit(1) from None
    # Application errors must not be mistaken for an incomplete installation.
    from .app import run
    raise SystemExit(run())


if __name__ == "__main__":
    main()
