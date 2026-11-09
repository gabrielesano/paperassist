"""Run with `python -m paperassist.native file.pdf [file.pdf ...]`."""
import multiprocessing
import sys
from importlib import import_module


def startup_error(message):
    if sys.platform == "win32" and getattr(sys, "frozen", False) and sys.stderr is None:
        import ctypes
        ctypes.WinDLL("user32").MessageBoxW(None, message, "PaperAssist", 0x10)
    else:
        print(message, file=sys.stderr)


def main(argv=None):
    multiprocessing.freeze_support()  # a frozen app starts its render process through the same executable
    try:
        import_module("PySide6.QtWidgets")
        import_module("pypdfium2")
    except (ImportError, OSError) as error:
        message = f"Cannot start the native reader: {error}\n"
        if getattr(sys, "frozen", False):
            message += "Reinstall a complete PaperAssist build for your operating system."
        else:
            message += ('Install native support from the PaperAssist source folder:\n'
                        '  python -m pip install -e .')
        startup_error(message)
        raise SystemExit(1) from None
    # Application errors must not be mistaken for an incomplete installation.
    from .app import run
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
