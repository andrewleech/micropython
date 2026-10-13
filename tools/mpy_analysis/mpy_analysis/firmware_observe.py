"""Observe, inside a running ``tools/makemanifest.py``, the files it reads and the processes it starts.

The firmware capture wrapper starts ``makemanifest.py`` with this module's ``install`` called from
a ``sitecustomize`` module. ``install`` does nothing unless the process is ``makemanifest.py``
and the capture named a log file in ``MPY_CAPTURE_OBSERVE``. In that process it

* appends one JSON line per regular file the process opens for reading, with the SHA-256 of the
  file taken at that moment, in the order the opens happen;
* appends one JSON line per process the interpreter is about to start;
* replaces ``sys.executable`` with the capture's Python wrapper, so that the ``mpy-tool.py`` step
  the generator starts through ``sys.executable`` is itself a captured action.

Files that belong to the interpreter installation and to the capture itself are not logged. The
audit hook only observes; it never changes what the generator reads or writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import site
import sys
import sysconfig
import threading
from pathlib import Path

LOG_VARIABLE = "MPY_CAPTURE_OBSERVE"
WRAPPER_DIR_VARIABLE = "MPY_CAPTURE_WRAPPER_DIR"
GENERATOR = "makemanifest.py"

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_guard = threading.local()


def is_generator(argv: list[str]) -> bool:
    """Whether the command line runs the frozen-content generator."""
    return any(Path(arg).name == GENERATOR for arg in argv[:1])


def _interpreter_prefixes() -> tuple[str, ...]:
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    paths.update(value for value in sysconfig.get_paths().values())
    try:
        paths.update(site.getsitepackages())
        paths.add(site.getusersitepackages())
    except AttributeError:
        pass
    return tuple(sorted(os.path.realpath(path) for path in paths if path))


def _append(log: str, event: dict) -> None:
    payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def _digest(path: str) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
            size += len(block)
    return hasher.hexdigest(), size


def install() -> None:
    log = os.environ.get(LOG_VARIABLE)
    if not log or not is_generator(sys.argv):
        return
    ignored = _interpreter_prefixes()
    capture_dirs = tuple(
        os.path.realpath(path)
        for path in (os.environ.get(WRAPPER_DIR_VARIABLE), str(Path(__file__).resolve().parent), log)
        if path
    )
    wrapper_dir = os.environ.get(WRAPPER_DIR_VARIABLE)
    if wrapper_dir:
        sys.executable = str(Path(wrapper_dir) / "python")

    def skip(real: str) -> bool:
        if real.endswith((".pyc", ".pyo")) or real.startswith(("/proc/", "/sys/", "/dev/")):
            return True
        return any(real == prefix or real.startswith(prefix + os.sep) for prefix in ignored + capture_dirs)

    def hook(event: str, arguments: tuple) -> None:
        if getattr(_guard, "active", False):
            return
        if event == "open":
            path, _, flags = arguments
            if not isinstance(path, (str, bytes, os.PathLike)) or (isinstance(flags, int) and flags & _WRITE_FLAGS):
                return
            _guard.active = True
            try:
                real = os.path.realpath(os.fsdecode(path))
                if not os.path.isfile(real) or skip(real):
                    return
                sha256, size = _digest(real)
                _append(log, {"event": "read", "path": real, "sha256": sha256, "size": size})
            except OSError:
                return
            finally:
                _guard.active = False
        elif event == "subprocess.Popen":
            _guard.active = True
            try:
                _append(log, {"event": "spawn", "argv": [os.fsdecode(item) for item in arguments[1]]})
            finally:
                _guard.active = False

    _append(log, {"event": "start", "pid": os.getpid(), "argv": list(sys.argv)})
    sys.addaudithook(hook)
