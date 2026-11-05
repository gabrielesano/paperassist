"""Hand freed heap pages back to the OS: glibc keeps them after large pixmaps are released."""
import ctypes


def trim_heap():
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # not glibc: nothing to do
