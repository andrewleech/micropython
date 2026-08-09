# Install the debugpy DAP server package on a device, cross-compiled and
# cached by content hash.
# MIT license; Copyright (c) 2026 Andrew Leech

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess

import platformdirs

from .commands import CommandError

try:
    from mpy_cross import mpy_cross as _PACKAGED_MPY_CROSS
except ImportError:
    _PACKAGED_MPY_CROSS = None

_MARKER_NAME = ".debugpy-install.json"
_PACKAGE_SUBDIR = "debugpy"
_DEFAULT_MPY_CROSS_FLAGS = ("-O2",)
_CACHE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


def _find_mpy_cross():
    # An explicit override always wins, then the mpy_cross PyPI package (as
    # romfs.py does), then PATH.
    env = os.environ.get("MPY_CROSS")
    if env and os.path.isfile(env):
        return env

    if _PACKAGED_MPY_CROSS and os.path.isfile(_PACKAGED_MPY_CROSS):
        return _PACKAGED_MPY_CROSS

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


def _mpy_cross_emitted_version(mpy_cross_version):
    # mpy-cross --version reports "...emitting mpy vN.M"; N is the same value
    # as the low byte of the target's sys.implementation._mpy.
    match = re.search(r"mpy v(\d+)", mpy_cross_version)
    if not match:
        raise CommandError(
            f"could not parse .mpy version from mpy-cross --version output: {mpy_cross_version!r}"
        )
    return int(match.group(1))


def _device_mpy_version(transport):
    transport.exec("import sys")
    return transport.eval("getattr(sys.implementation, '_mpy', 0) & 0xFF")


def _device_lib_dir(transport):
    # The target's own library directory, read from its sys.path. A hardcoded
    # "/lib" assumes the filesystem is mounted at the root, which is not true
    # of boards that mount theirs at /flash: there mkdir("/lib") fails with
    # ENODEV, and anything written under /lib would not be importable anyway.
    # Frozen-in /rom entries are skipped because they are read-only.
    # This is the same resolution `mpremote mip` uses to pick an install target.
    transport.exec("import sys")
    lib_paths = [
        p for p in transport.eval("sys.path") if not p.startswith("/rom") and p.endswith("/lib")
    ]
    if not lib_paths or not lib_paths[0]:
        raise CommandError(
            "debugpy install: no lib directory in the target's sys.path; "
            "pass device_dir and marker_path to choose one"
        )
    return lib_paths[0].rstrip("/")


def _source_files(package_dir):
    # Sorted, relative, '/'-separated paths of every .py file in the package.
    paths = glob.glob(os.path.join(package_dir, "**", "*.py"), recursive=True)
    return sorted(os.path.relpath(p, package_dir).replace(os.sep, "/") for p in paths)


def _cache_key(package_dir, source_files, mpy_cross_version, mpy_cross_flags, device_mpy_version):
    h = hashlib.sha256()
    for rel in source_files:
        with open(os.path.join(package_dir, *rel.split("/")), "rb") as f:
            data = f.read()
        h.update(f"{rel}:{len(data)}\n".encode())
        h.update(data)
    h.update(mpy_cross_version.encode())
    h.update(" ".join(mpy_cross_flags).encode())
    h.update(str(device_mpy_version).encode())
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
        obj = json.loads(data)
    except ValueError:
        return None
    # A marker is only ever an object; anything else (e.g. a bare `3`, hand
    # written or left by an unrelated tool) degrades to "no marker" rather
    # than raising on the caller's .get() below.
    return obj if isinstance(obj, dict) else None


def _verify_installed(transport, files):
    # Hash check so a file corrupted after install (bad write, brownout,
    # filesystem repair) is caught rather than trusted on existence alone.
    # transport.fs_hashfile hashes on the device when it has hashlib, falling
    # back to a full read-back otherwise - on such a target this is a full
    # download of every recorded file on every call, not the zero-transfer
    # fast path it is on a hashlib-capable one.
    for path, expected_hex in files.items():
        try:
            digest = transport.fs_hashfile(path, "sha256")
        except OSError:
            return False
        if digest.hex() != expected_hex:
            return False
    return True


def _unrecorded_files(transport, device_dir, files):
    # Files under device_dir the marker doesn't know about. The marker's own
    # hashes can't see these, so without this the fast path would accept a
    # <stem>.py dropped in later (by `mip install debugpy`, say) that shadows
    # the <stem>.mpy installed here, and the session would run stale source.
    return [path for path in _walk_device_files(transport, device_dir) if path not in files]


def _prune_cache(cache_dir, keep_key):
    # The host cache is disposable, so keep only the key in use and drop
    # every sibling directory that looks like a key this module wrote (a bare
    # 64-char hex string) - cache_dir need not be exclusively owned by this
    # installer, though a tool using 64-hex names of its own would collide.
    # Alternating between two targets or flag sets thrashes: each run deletes
    # the other's entry and recompiles. Note the key hashes mpy-cross's
    # --version string, not the binary, so two builds reporting the same
    # version share one entry.
    try:
        entries = os.listdir(cache_dir)
    except OSError:
        return
    for name in entries:
        if name != keep_key and _CACHE_KEY_RE.match(name):
            shutil.rmtree(os.path.join(cache_dir, name), ignore_errors=True)


def _walk_device_files(transport, device_dir):
    # Every file under device_dir, recursively. A directory that can't be
    # listed contributes nothing: the caller treats "can't see it" as empty.
    found = []

    def walk(path):
        try:
            entries = transport.fs_listdir(path)
        except OSError:
            return
        for entry in entries:
            full = path.rstrip("/") + "/" + entry.name
            if entry.st_mode & 0x4000:  # directory
                walk(full)
            else:
                found.append(full)

    walk(device_dir)
    return found


def _sweep_device_dir(transport, device_dir, keep_paths):
    # Recursively remove anything under device_dir that isn't one of the
    # files this run just wrote (keep_paths). This is the only check that
    # doesn't depend on the marker: it catches a stale <stem>.py left by an
    # earlier, differently-installed copy of the package that would
    # otherwise shadow the <stem>.mpy this run wrote (MicroPython's importer
    # prefers .py over .mpy), as well as any orphan a killed run or a lost
    # marker leaves with no file list to prune from.
    failed = []
    for full in _walk_device_files(transport, device_dir):
        if full not in keep_paths:
            try:
                transport.fs_rmfile(full)
            except OSError:
                failed.append(full)
    if failed:
        # Recording a clean install here would make every later run take the
        # fast path over a file that may be shadowing what we just wrote.
        raise CommandError(
            "debugpy install: could not remove stale file(s) under {}: {}".format(
                device_dir, ", ".join(sorted(failed))
            )
        )


def ensure_debugpy_installed(
    transport,
    package_dir,
    *,
    mpy_cross=None,
    mpy_cross_flags=_DEFAULT_MPY_CROSS_FLAGS,
    cache_dir=None,
    device_dir=None,
    marker_path=None,
):
    # Cross-compile package_dir to .mpy (host-cached, keyed by a hash of the
    # sources, mpy-cross's version/flags and the target's .mpy version) and
    # install under device_dir. Skips the transfer when the on-device marker
    # at marker_path already matches: same key, same device_dir, and every
    # recorded file still hashes the same on the device. Any mismatch there
    # forces a full reinstall rather than raising - only a transfer failure
    # (hash mismatch during write) is an error.
    #
    # device_dir and marker_path default to the target's own lib directory
    # (see _device_lib_dir), which differs per board, so they are resolved
    # from the connected device rather than fixed at import time.
    #
    # Raises if the target has no .mpy support or its .mpy version doesn't
    # match what mpy_cross emits. Returns True if anything was installed,
    # False if the device was already up to date.
    if device_dir is None or marker_path is None:
        lib_dir = _device_lib_dir(transport)
        if device_dir is None:
            device_dir = lib_dir + "/" + _PACKAGE_SUBDIR
        if marker_path is None:
            marker_path = lib_dir + "/" + _MARKER_NAME

    # device_dir is swept of everything this run didn't write, so refuse a
    # path whose sweep would reach beyond the package - the filesystem root,
    # or a parent of the marker.
    device_dir = device_dir.rstrip("/")
    if not device_dir or marker_path.startswith(device_dir + "/"):
        raise CommandError(
            f"debugpy install: device_dir {device_dir or '/'!r} is too broad to sweep "
            f"(it would remove files outside the package, including {marker_path})"
        )

    if mpy_cross is None:
        mpy_cross = _find_mpy_cross()
    mpy_cross_version = _mpy_cross_version(mpy_cross)
    emitted_mpy_version = _mpy_cross_emitted_version(mpy_cross_version)

    device_mpy_version = _device_mpy_version(transport)
    if not device_mpy_version:
        raise CommandError(
            "target has no .mpy support (sys.implementation._mpy is 0); "
            "cannot install a cross-compiled package"
        )
    if device_mpy_version != emitted_mpy_version:
        raise CommandError(
            f"mpy-cross ({mpy_cross}) emits .mpy v{emitted_mpy_version} but the "
            f"target reports v{device_mpy_version}; use a matching mpy-cross build"
        )

    source_files = _source_files(package_dir)
    if not source_files:
        raise CommandError(f"debugpy install: no .py files found under {package_dir}")

    key = _cache_key(
        package_dir, source_files, mpy_cross_version, mpy_cross_flags, device_mpy_version
    )

    if cache_dir is None:
        from .main import _PROG

        cache_dir = platformdirs.user_cache_dir(appname=_PROG, appauthor=False)
        cache_dir = os.path.join(cache_dir, "debugpy-mpy")
    key_cache_dir = os.path.join(cache_dir, key)

    installed = _read_marker(transport, marker_path)
    if (
        installed
        and installed.get("key") == key
        and installed.get("device_dir") == device_dir
        and _verify_installed(transport, installed.get("files", {}))
        and not _unrecorded_files(transport, device_dir, installed.get("files", {}))
    ):
        return False

    # Compile before touching the device: an mpy-cross failure or a Ctrl-C
    # here must leave a good device install alone rather than invalidate it
    # for nothing.
    manifest = _compile_sources(
        package_dir, source_files, mpy_cross, mpy_cross_flags, key_cache_dir
    )

    # The key just used to compile is provably no longer needed once it has
    # succeeded, so prune the old one now rather than before a compile that
    # might fail.
    _prune_cache(cache_dir, key)

    # Invalidate before mutating: a kill or a source rollback between here
    # and the final marker write below always leaves a marker with no key,
    # so the next call reinstalls rather than trusting a half-written
    # device. Write order gives no import-time guarantee here - MicroPython
    # treats a directory missing __init__ as a namespace package, so a
    # partial write can already be importable - it's this missing key, and
    # _verify_installed's per-file hash check on the read side, that catch
    # it. Orphan cleanup for an interrupted run doesn't depend on this
    # marker at all: _sweep_device_dir below walks device_dir directly.
    transport.fs_ensure_path_exists(marker_path)
    transport.fs_writefile(marker_path, json.dumps({}).encode())

    device_files = {}
    for dest_rel, data in manifest.items():
        dest = device_dir.rstrip("/") + "/" + dest_rel
        transport.fs_ensure_path_exists(dest)
        transport.fs_writefile(dest, data, verify_hash=True)
        device_files[dest] = hashlib.sha256(data).hexdigest()

    # Sweep device_dir rather than just diffing against the previous marker's
    # file list: that also catches a stray file the marker never knew about
    # (see _sweep_device_dir).
    _sweep_device_dir(transport, device_dir, set(device_files))

    # The marker's directory already exists from the invalidation write above.
    marker = json.dumps({"key": key, "device_dir": device_dir, "files": device_files}).encode()
    transport.fs_writefile(marker_path, marker, verify_hash=True)
    return True


def do_debugpy_install(state, args):
    # The command layer over ensure_debugpy_installed: mpremote has no copy of
    # the debugpy package and no way to guess where one is, so the host
    # directory is named on the command line.
    package_dir = os.path.realpath(args.package_dir[0])
    if not os.path.isdir(package_dir):
        raise CommandError(f"debugpy install: {package_dir} is not a directory")
    if not os.path.isfile(os.path.join(package_dir, "__init__.py")):
        # The likely mistake is naming micropython-lib's package *folder*
        # rather than the package itself, so say which one was meant.
        inner = os.path.join(package_dir, _PACKAGE_SUBDIR)
        hint = (
            f"; did you mean {inner}?"
            if os.path.isfile(os.path.join(inner, "__init__.py"))
            else ""
        )
        raise CommandError(
            f"debugpy install: {package_dir} has no __init__.py, so it is not "
            f"the debugpy package directory{hint}"
        )

    state.ensure_raw_repl()
    state.did_action()

    installed = ensure_debugpy_installed(state.transport, package_dir, mpy_cross=args.mpy_cross)
    if installed:
        print(f"debugpy installed from {package_dir}")
        # Nothing here resets the device, matching `mip install`. It matters
        # more for this package than for most: a debug session imports
        # debugpy, and an import that happened before this write keeps the
        # old module. Chaining `+ soft-reset` covers a single invocation; a
        # separate one soft-resets on its first command anyway.
        print("soft-reset the device before debugging if it has already imported debugpy")
    else:
        print(f"debugpy already up to date from {package_dir}")
