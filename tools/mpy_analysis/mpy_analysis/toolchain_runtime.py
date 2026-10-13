"""Identify statically linked files supplied by the compiler or the C library.

The captured compiler answers every question, so no package manager is consulted:

* the compiler runtime (libgcc, crt objects) lives in the directory named by
  ``-print-libgcc-file-name`` and carries the compiler release version;
* the C library lives in the directory named by ``-print-file-name=libc.a`` (or
  ``libc.so``) and its version comes from the macros the compiler sees in the library's own
  header (``features.h`` for glibc, ``newlib.h`` for newlib).

A file outside both directories is not identified here and stays an explicit gap.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

# Name, version header, version macros, and the file names that belong to the library when its
# directory is shared with unrelated libraries (None: the whole directory belongs to it).
_C_LIBRARIES = (
    (
        "glibc",
        "features.h",
        ("__GLIBC__", "__GLIBC_MINOR__"),
        frozenset(
            {
                "crt1.o", "Scrt1.o", "gcrt1.o", "crti.o", "crtn.o", "libc.a", "libc_nonshared.a",
                "libm.a", "libdl.a", "libpthread.a", "librt.a", "libutil.a", "libmvec.a",
            }
        ),
    ),
    ("newlib", "newlib.h", ("_NEWLIB_VERSION",), None),
)


def machine_flags(argv: list[str]) -> tuple[str, ...]:
    """Flags that select the multilib variant, taken from an already flattened link command."""
    return tuple(arg for arg in argv if arg.startswith(("-m", "--sysroot", "-specs=")))


def _query(compiler: str, flags: tuple[str, ...], *arguments: str, stdin: str | None = None) -> str | None:
    try:
        result = subprocess.run(
            [compiler, *flags, *arguments],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _macros(compiler: str, flags: tuple[str, ...], header: str) -> dict[str, str]:
    output = _query(compiler, flags, "-E", "-dM", "-x", "c", "-", stdin=f"#include <{header}>\n")
    macros = {}
    for line in (output or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0] == "#define":
            macros[parts[1]] = parts[2]
    return macros


@lru_cache(maxsize=None)
def layout(compiler: str, flags: tuple[str, ...]) -> dict[str, Any]:
    """Directories and C library identity the compiler resolves for these flags."""
    runtime = _query(compiler, flags, "-print-libgcc-file-name")
    runtime_dir = Path(runtime.strip()).resolve().parent if runtime and "/" in runtime else None
    sysroot = (_query(compiler, flags, "-print-sysroot") or "").strip()
    library_dir = None
    for name in ("libc.a", "libc.so"):
        found = (_query(compiler, flags, f"-print-file-name={name}") or "").strip()
        if "/" in found:
            library_dir = Path(found).resolve().parent
            break
    c_library = None
    for name, header, keys, files in _C_LIBRARIES:
        macros = _macros(compiler, flags, header)
        if not all(key in macros for key in keys):
            continue
        if name == "glibc":
            version = f"{macros['__GLIBC__']}.{macros['__GLIBC_MINOR__']}"
        else:
            version = macros["_NEWLIB_VERSION"].strip('"')
        c_library = {
            "name": name,
            "version": version,
            "version_source": f"{header} macros from the captured compiler",
            "files": files,
        }
        break
    return {
        "runtime_dir": runtime_dir,
        "library_dir": library_dir,
        "sysroot": Path(sysroot).resolve() if sysroot else None,
        "c_library": c_library,
    }


def classify(path: str, compiler: str, flags: tuple[str, ...]) -> tuple[str | None, dict[str, Any] | None, str]:
    """Return ("compiler-runtime" | "c-library", c-library record or None, "") or (None, None, reason)."""
    found = layout(compiler, flags)
    resolved = Path(path).resolve()
    if found["runtime_dir"] is not None and resolved.parent == found["runtime_dir"]:
        return "compiler-runtime", None, ""
    if found["library_dir"] is not None and resolved.parent == found["library_dir"]:
        library = found["c_library"]
        if library is None:
            return None, None, "file lies in the compiler's C library directory but its version macros are unavailable"
        if library["files"] is not None and resolved.name not in library["files"]:
            return None, None, "file shares the C library directory but is not one of the C library's files"
        sysroot = found["sysroot"]
        if sysroot is not None and not resolved.parent.is_relative_to(sysroot):
            return None, None, "file lies outside the compiler's configured sysroot"
        record = {key: value for key, value in library.items() if key != "files"}
        return "c-library", record, ""
    return None, None, "file is neither compiler runtime nor in the compiler's C library directory"
