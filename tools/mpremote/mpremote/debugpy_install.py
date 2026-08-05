# Install the debugpy DAP server package on a device, cross-compiled and
# cached by content hash.
# MIT license; Copyright (c) 2026 Andrew Leech

import glob
import hashlib
import json
import os
import shutil
import subprocess

import platformdirs

from .commands import CommandError
from .mip import _ensure_path_exists

try:
    from mpy_cross import mpy_cross as _PACKAGED_MPY_CROSS
except ImportError:
    _PACKAGED_MPY_CROSS = None

_MARKER_PATH = "/lib/.debugpy-install.json"
_DEFAULT_DEVICE_DIR = "/lib/debugpy"
_DEFAULT_MPY_CROSS_FLAGS = ("-O2",)


def _find_mpy_cross():
    # Prefer the mpy_cross PyPI package, as romfs.py does, then an explicit
    # override, then PATH.
    if _PACKAGED_MPY_CROSS and os.path.isfile(_PACKAGED_MPY_CROSS):
        return _PACKAGED_MPY_CROSS

    env = os.environ.get("MPY_CROSS")
    if env and os.path.isfile(env):
        return env

    found = shutil.which("mpy-cross")
    if found:
        return found

    raise CommandError(
        "mpy-cross not found; install the mpy-cross package, set MPY_CROSS, "
        "or put mpy-cross on PATH"
    )


def _mpy_cross_version(mpy_cross):
    try:
        proc = subprocess.run([mpy_cross, "--version"], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise CommandError(f"mpy-cross not executable: {mpy_cross}")
    except subprocess.CalledProcessError as e:
        raise CommandError(f"mpy-cross --version failed for {mpy_cross}: {e.stderr}")
    return (proc.stdout + proc.stderr).strip()


def _source_files(package_dir):
    # Sorted, relative, '/'-separated paths of every .py file in the package.
    paths = glob.glob(os.path.join(package_dir, "**", "*.py"), recursive=True)
    return sorted(os.path.relpath(p, package_dir).replace(os.sep, "/") for p in paths)


def _cache_key(package_dir, source_files, mpy_cross_version, mpy_cross_flags):
    h = hashlib.sha256()
    for rel in source_files:
        with open(os.path.join(package_dir, *rel.split("/")), "rb") as f:
            h.update(f.read())
        h.update(rel.encode())
    h.update(mpy_cross_version.encode())
    h.update(" ".join(mpy_cross_flags).encode())
    return h.hexdigest()


def _compile_one(mpy_cross, mpy_cross_flags, src, dest):
    # Compile to a temp file first so a partial cache dir never looks complete.
    tmp = dest + ".tmp"
    try:
        subprocess.run(
            [mpy_cross, *mpy_cross_flags, "-o", tmp, src], check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        raise CommandError(f"mpy-cross failed on {src}: {e.stderr.decode(errors='replace')}")
    os.replace(tmp, dest)


def _compile_sources(package_dir, source_files, mpy_cross, mpy_cross_flags, cache_dir):
    # Cross-compile each source to .mpy in cache_dir (already keyed by the
    # caller), skipping files a previous run already produced there.
    manifest = {}
    for rel in source_files:
        dest_rel = rel[:-3] + ".mpy"
        dest = os.path.join(cache_dir, *dest_rel.split("/"))
        if not os.path.isfile(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            _compile_one(
                mpy_cross, mpy_cross_flags, os.path.join(package_dir, *rel.split("/")), dest
            )
        with open(dest, "rb") as f:
            data = f.read()
        manifest[dest_rel] = data
    return manifest


def _read_marker(transport, marker_path):
    try:
        data = transport.fs_readfile(marker_path)
    except OSError:
        return None
    try:
        return json.loads(data)
    except ValueError:
        return None


def ensure_debugpy_installed(
    transport,
    package_dir,
    *,
    mpy_cross=None,
    mpy_cross_flags=_DEFAULT_MPY_CROSS_FLAGS,
    cache_dir=None,
    device_dir=_DEFAULT_DEVICE_DIR,
    marker_path=_MARKER_PATH,
):
    """Ensure the debugpy package at `package_dir` is installed under `device_dir`.

    Cross-compiles every .py under `package_dir` to .mpy, keyed by a hash of the
    sources plus the mpy-cross version, flags and `device_dir`, and caches the
    result on the host under `cache_dir`. Compares the key against the on-device
    marker at `marker_path`; on a match nothing is transferred.

    On a mismatch the marker is invalidated first (so any interruption between
    here and the final marker write leaves no marker a future call can mistake
    for success), then every `.mpy` is written to the device with hash
    verification (`__init__.mpy` files last, so the package never looks
    importable mid-install), device files from a previous install that the
    current sources no longer produce are removed, and finally the new marker
    is written. Returns True if anything was installed, False if the device
    was already up to date.
    """
    if mpy_cross is None:
        mpy_cross = _find_mpy_cross()
    mpy_cross_version = _mpy_cross_version(mpy_cross)

    source_files = _source_files(package_dir)
    if not source_files:
        raise CommandError(f"debugpy install: no .py files found under {package_dir}")

    key = _cache_key(package_dir, source_files, mpy_cross_version, mpy_cross_flags)

    if cache_dir is None:
        cache_dir = platformdirs.user_cache_dir(appname="mpremote", appauthor=False)
        cache_dir = os.path.join(cache_dir, "debugpy-mpy")
    key_cache_dir = os.path.join(cache_dir, key)

    installed = _read_marker(transport, marker_path)
    if installed and installed.get("key") == key and installed.get("device_dir") == device_dir:
        return False

    # Invalidate before mutating: this is the first device write of an
    # install, so a kill or a source rollback before the final marker write
    # below always lands on "no valid marker" rather than a stale-but-valid
    # one that a later call could mistake for success.
    transport.fs_writefile(marker_path, b"")

    manifest = _compile_sources(
        package_dir, source_files, mpy_cross, mpy_cross_flags, key_cache_dir
    )

    # __init__.mpy entries last (both the package root and subpackages), so a
    # half-written install is never importable.
    ordered = sorted(manifest.items(), key=lambda kv: os.path.basename(kv[0]) == "__init__.mpy")

    device_files = {}
    for dest_rel, data in ordered:
        dest = device_dir.rstrip("/") + "/" + dest_rel
        _ensure_path_exists(transport, dest)
        transport.fs_writefile(dest, data, verify_hash=True)
        device_files[dest] = hashlib.sha256(data).hexdigest()

    if installed:
        for stale in set(installed.get("files", {})) - set(device_files):
            try:
                transport.fs_rmfile(stale)
            except OSError:
                pass

    marker = json.dumps({"key": key, "device_dir": device_dir, "files": device_files}).encode()
    transport.fs_writefile(marker_path, marker, verify_hash=True)
    return True
