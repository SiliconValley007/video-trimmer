"""Silence native (C/DLL) writes to stderr while keeping Python's own stderr usable."""

import os
import sys


def silence_native_stderr() -> bool:
    """Point OS file descriptor 2 at the null device.

    ``sys.stderr`` is first re-bound to a duplicate of the original descriptor, so Python
    tracebacks are still printed. Returns ``False`` when there is nothing to silence
    (for example a windowed executable without a console).
    """
    try:
        original = os.dup(2)
    except OSError:
        return False
    try:
        sys.stderr = os.fdopen(
            original,
            "w",
            buffering=1,
            encoding="utf-8",
            errors="replace",
            closefd=False,
        )
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, 2)
        finally:
            os.close(devnull)
    except OSError:
        return False
    return True
