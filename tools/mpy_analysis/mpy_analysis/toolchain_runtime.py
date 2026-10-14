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
    ("picolibc", "picolibc.h", ("__PICOLIBC__", "__PICOLIBC_MINOR__", "__PICOLIBC_PATCHLEVEL__"), None),
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
        elif name == "picolibc":
            version = ".".join(macros[key] for key in keys)
        else:
            version = macros["_NEWLIB_VERSION"].strip('"')
        c_library = {
            "name": name,
            "version": version,
            "version_source": f"{header} macros from the captured compiler",
            "files": files,
        }
        if name == "picolibc":
            # The headers sit in <root>/include and the archives in <root>/<triple>/lib/...
            for directory in include_directories(compiler, flags) or ():
                if directory.joinpath(header).is_file():
                    c_library["root"] = directory.parent
        break
    return {
        "runtime_dir": runtime_dir,
        "release": (_query(compiler, flags, "-dumpfullversion") or "").strip() or None,
        "library_dir": library_dir,
        "sysroot": Path(sysroot).resolve() if sysroot else None,
        "c_library": c_library,
    }


@lru_cache(maxsize=None)
def include_directories(compiler: str, flags: tuple[str, ...], language: str = "c") -> tuple[Path, ...] | None:
    """The existing directories the compiler searches for headers on its own, resolved.

    The compiler reports them (``-E -v``) for the machine flags and language of a compile; the
    directories named by ``-I``, ``-isystem`` and the like are not among them. None when the
    compiler cannot be queried.
    """
    try:
        result = subprocess.run(
            [compiler, *flags, "-E", "-v", "-x", language, "-"],
            input="",
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    directories: list[Path] = []
    listing = False
    for line in result.stderr.splitlines():
        if line.startswith("#include") and line.endswith("search starts here:"):
            listing = True
        elif line.startswith("End of search list"):
            break
        elif listing and line.startswith(" ") and "(framework directory)" not in line:
            directory = Path(line.strip())
            if directory.is_dir():
                directories.append(directory.resolve())
    return tuple(dict.fromkeys(directories))


def in_compiler_headers(path: str, compiler: str, flags: tuple[str, ...], language: str = "c") -> Path | None:
    """The compiler's own header directory or sysroot that contains the header, if any."""
    resolved = Path(path).resolve()
    for directory in include_directories(compiler, flags, language) or ():
        if resolved.is_relative_to(directory):
            return directory
    sysroot = layout(compiler, flags)["sysroot"]
    if sysroot is not None and resolved.is_relative_to(sysroot):
        return sysroot
    return None


def classify(path: str, compiler: str, flags: tuple[str, ...]) -> tuple[str | None, dict[str, Any] | None, str]:
    """Return (kind, record, "") with kind "compiler-runtime" or "c-library", or (None, None, reason)."""
    found = layout(compiler, flags)
    resolved = Path(path).resolve()
    if found["runtime_dir"] is not None and resolved.parent == found["runtime_dir"]:
        release = found["release"]
        if release is None:
            return None, None, "compiler does not report its release version (-dumpfullversion)"
        return "compiler-runtime", {"release": release}, ""
    if found["library_dir"] is not None and resolved.parent == found["library_dir"] and (found["c_library"] or {}).get("root") is None:
        library = found["c_library"]
        if library is None:
            return None, None, "file lies in the compiler's C library directory but its version macros are unavailable"
        if library["files"] is not None and resolved.name not in library["files"]:
            return None, None, "file shares the C library directory but is not one of the C library's files"
        sysroot = found["sysroot"]
        if sysroot is not None and not resolved.parent.is_relative_to(sysroot):
            return None, None, "file lies outside the compiler's configured sysroot"
        record = {key: value for key, value in library.items() if key not in ("files", "root")}
        return "c-library", record, ""
    library = found["c_library"]
    if library is not None and library.get("root") is not None and resolved.is_relative_to(library["root"]) and resolved.suffix == ".a":
        return "c-library", {key: value for key, value in library.items() if key not in ("files", "root")}, ""
    return None, None, "file is neither compiler runtime nor in the compiler's C library directory"
