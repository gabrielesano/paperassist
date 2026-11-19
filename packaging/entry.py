"""PyInstaller entry point: routes through `paperassist.cli:main`."""
import multiprocessing

from paperassist.cli import main
from paperassist.retrieval import configure_ssl_env

if __name__ == "__main__":
    multiprocessing.freeze_support()
    configure_ssl_env()
    raise SystemExit(main())
