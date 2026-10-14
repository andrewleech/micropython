"""Where the libraries carried in MicroPython's tree keep their own version.

``VERSION_SOURCES`` and ``FAMILY_SOURCES`` record locations, never values. A
library that is checked out but whose source cannot be parsed raises, so an
upstream change to a version marker is noticed instead of yielding a stale or
missing version.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path


def _read(root: Path, relative: str) -> tuple[str, Path]:
    path = root / relative
    return path.read_text(encoding="utf-8", errors="replace"), path


def _match(root: Path, relative: str, pattern: str, flags: int = 0) -> tuple[str, Path]:
    text, path = _read(root, relative)
    found = re.search(pattern, text, flags)
    if not found:
        raise ValueError(f"{relative}: version marker not found")
    return found.group(1), path


def _joined(root: Path, relative: str, template: str, names: tuple[str, ...]) -> tuple[str, Path]:
    text, path = _read(root, relative)
    parts = []
    for name in names:
        found = re.search(template.format(name=name), text, re.MULTILINE)
        if not found:
            raise ValueError(f"{relative}: {name} not found")
        parts.append(found.group(1))
    return ".".join(parts), path


def _defines(relative: str, *names: str) -> Callable[[Path], tuple[str, Path]]:
    template = r"^\s*#\s*define\s+{name}\s+\(?\s*(\d+)"
    return lambda root: _joined(root, relative, template, names)


def _assigns(relative: str, *names: str) -> Callable[[Path], tuple[str, Path]]:
    return lambda root: _joined(root, relative, r"^\s*{name}\s*=\s*(\d+)", names)


def _regex(relative: str, pattern: str, flags: int = 0) -> Callable[[Path], tuple[str, Path]]:
    return lambda root: _match(root, relative, pattern, flags)


def _oofatfs(root: Path) -> tuple[str, Path]:
    release, path = _match(
        root, "ff.h", r"FatFs\s+-\s+Generic\s+FAT\s+Filesystem\s+module\s+(R[\w.]+)"
    )
    revision, _ = _match(root, "ff.h", r"#\s*define\s+FF_DEFINED\s+(\d+)")
    return f"{release}-rev{revision}", path


def _pico_sdk(root: Path) -> tuple[str, Path]:
    version, path = _joined(
        root,
        "pico_sdk_version.cmake",
        r"^\s*set\s*\(\s*{name}\s+\"?(\d+)",
        ("PICO_SDK_VERSION_MAJOR", "PICO_SDK_VERSION_MINOR", "PICO_SDK_VERSION_REVISION"),
    )
    text, _ = _read(root, "pico_sdk_version.cmake")
    pre = re.search(r'^\s*set\s*\(\s*PICO_SDK_VERSION_PRE_RELEASE_ID\s+"?([^\s")]+)', text, re.M)
    return (f"{version}-{pre.group(1)}" if pre else version), path


def _family_of(directory: Path, selected: tuple[Path, ...], pattern: str) -> str:
    families = {
        found.group(0)
        for path in selected
        if (found := re.search(pattern, "/".join(path.relative_to(directory).parts[:2])))
    }
    if len(families) != 1:
        raise ValueError(f"selected files do not identify one family: {sorted(families)}")
    return families.pop()


def _stm32lib(directory: Path, selected: tuple[Path, ...]) -> tuple[str, Path]:
    """The CMSIS device package version of the one STM32 family the selected files come from."""
    family = _family_of(directory, selected, r"STM32[A-Z0-9]+xx")
    relative = f"CMSIS/{family}/Include/{family.lower()}.h"
    template = r"^#define\s+\w*VERSION_{name}\s+\(\s*0x([0-9A-Fa-f]+)"
    version, path = _joined(directory, relative, template, ("MAIN", "SUB1", "SUB2"))
    return ".".join(str(int(part, 16)) for part in version.split(".")), path


def _asf4(directory: Path, selected: tuple[Path, ...]) -> tuple[str, Path]:
    """The component version of the one ASF4 MCU tree the selected files come from."""
    mcu = _family_of(directory, selected, r"^[^/]+")
    relative = f"{mcu}/include/component-version.h"
    version, path = _match(
        directory, relative, r'#\s*define\s+COMPONENT_VERSION_STRING\s+"([^"]+)"'
    )
    build, _ = _match(directory, relative, r"#\s*define\s+BUILD_NUMBER\s+(\d+)")
    return f"{version}+build.{build}", path


# Libraries that keep independently versioned generations side by side in one
# directory, as the file-name pattern of each generation.
GENERATIONS: dict[str, dict[str, str]] = {"lib/littlefs": {"lfs1": r"lfs1", "lfs2": r"lfs2"}}


def _littlefs(directory: Path, selected: tuple[Path, ...]) -> tuple[str, Path]:
    """The version of the one littlefs generation the selected files belong to."""
    generation = _family_of(directory, selected, r"lfs[12]")
    macro = f"LFS{generation[-1]}_VERSION"
    packed, path = _match(
        directory, f"{generation}.h", rf"#\s*define\s+{macro}\s+0x([0-9a-fA-F]+)"
    )
    number = int(packed, 16)
    return f"{number >> 16}.{number & 0xFFFF}", path


# Library directory (relative to the MicroPython root) to a function that takes
# the library directory and returns (version, file the version was read from).
# Libraries absent here (and from FAMILY_SOURCES) declare no usable version in
# the tree (axtls, wiznet5k, uzlib, crypto-algorithms, libm, arduino-lib,
# btstack, nrfx, psoc-edge, alif-security-toolkit, re1.5), carry several
# (littlefs generations, see GENERATIONS) or are not libraries of the LICENSE tree. A
# listed library whose checkout sits at an exact git tag takes that tag.
VERSION_SOURCES: dict[str, Callable[[Path], tuple[str, Path]]] = {
    "lib/berkeley-db-1.xx": _regex("changelog", r"^\s*[\d.]+\s*->\s*([\d.]+)", re.MULTILINE),
    "lib/lwip": _defines(
        "src/include/lwip/init.h",
        "LWIP_VERSION_MAJOR",
        "LWIP_VERSION_MINOR",
        "LWIP_VERSION_REVISION",
    ),
    "lib/mbedtls": _regex(
        "include/mbedtls/build_info.h", r'#\s*define\s+MBEDTLS_VERSION_STRING\s+"([^"]+)"'
    ),
    "lib/tinyusb": _defines(
        "src/tusb_option.h", "TUSB_VERSION_MAJOR", "TUSB_VERSION_MINOR", "TUSB_VERSION_REVISION"
    ),
    "lib/pico-sdk": _pico_sdk,
    "lib/libffi": _regex("configure.ac", r"AC_INIT\s*\(\s*\[?[^,\]]+\]?\s*,\s*\[?([^,\]]+)\]?"),
    "lib/protobuf-c": _regex(
        "configure.ac", r"AC_INIT\s*\(\s*\[[^\]]+\]\s*,\s*\[([^\]]+)\]", re.DOTALL
    ),
    "lib/libmetal": _assigns("VERSION", "VERSION_MAJOR", "VERSION_MINOR", "VERSION_PATCH"),
    "lib/open-amp": _assigns("VERSION", "VERSION_MAJOR", "VERSION_MINOR", "VERSION_PATCH"),
    "lib/libhydrogen": _regex("library.properties", r"^version\s*=\s*(\S+)", re.MULTILINE),
    "lib/cyw43-driver": _defines(
        "src/cyw43.h", "CYW43_VERSION_MAJOR", "CYW43_VERSION_MINOR", "CYW43_VERSION_MICRO"
    ),
    "lib/fsp": _regex(
        "ra/fsp/inc/fsp_version.h", r'#\s*define\s+FSP_VERSION_STRING\s+\(?\s*"([^"]+)"'
    ),
    "lib/nxp_driver": _regex("sdk/version.txt", r"^\s*(\S+)"),
    "lib/alif_ensemble-cmsis-dfp": _regex(
        "AlifSemiconductor.Ensemble.pdsc", r'<release\s+version="([^"]+)"'
    ),
    "lib/mynewt-nimble": _regex("RELEASE_NOTES.md", r"Apache NimBLE\s+v([\d.]+)"),
    "lib/oofatfs": _oofatfs,
    "lib/libm_dbl": _regex("README", r"musl library,\s*v([\d.]+)"),
}

# Collections of independently versioned per-family trees; the function also
# receives the selected files and needs them to name exactly one family.
FAMILY_SOURCES: dict[str, Callable[[Path, tuple[Path, ...]], tuple[str, Path]]] = {
    "lib/stm32lib": _stm32lib,
    "lib/asf4": _asf4,
    "lib/littlefs": _littlefs,
}


def generation_of(relative: str, selected: tuple[Path, ...]) -> str | None:
    """The generation of the library at ``relative`` that ``selected`` files belong to, if one."""
    found = {
        name
        for path in selected
        for name, pattern in GENERATIONS.get(relative, {}).items()
        if re.match(pattern, path.name)
    }
    return found.pop() if len(found) == 1 else None
