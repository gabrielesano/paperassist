"""Run with `python -m paperassist.native file.pdf [file.pdf ...]`."""
import multiprocessing


def main():
    multiprocessing.freeze_support()  # a frozen app starts its render process through the same executable
    from .app import run  # Qt is loaded here, not in the render process that re-imports this module
    raise SystemExit(run())


if __name__ == "__main__":
    main()
