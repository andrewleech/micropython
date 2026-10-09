# SPDX-FileCopyrightText: 2026 Matt Trentini
# SPDX-FileCopyrightText: 2026 Andrew Leech
# SPDX-License-Identifier: MIT
"""Identify known libraries from physically selected MicroPython source files.

Version extractors and vendored identities are adapted from Matt Trentini's
MicroPython SBOM generator, licensed under MicroPython's MIT licence:
https://raw.githubusercontent.com/mattytrentini/micropython/efbf88cd35b778ea4d56dedb020bf310fe5c9b88/tools/sbom.py

This module reports source selection, not proof of linked firmware contents.
It reads only selected libraries' metadata, never discovers all library trees,
queries git or the network, or guesses a release from a repository commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


class ExtractorError(ValueError):
    """A selected library's known version metadata is missing or invalid."""


@dataclass(frozen=True)
class _Library:
    path: str
    name: str
    upstream: str | None = None
    version_file: str | None = None
    mode: str = "unknown"
    fields: tuple[str, ...] = ()
    pattern: str = ""


# Paths are explicit MicroPython-relative boundaries, not directory-name matches.
_LIBRARIES = (
    _Library(
        "lib/lwip",
        "lwip",
        None,
        "src/include/lwip/init.h",
        "defines",
        ("LWIP_VERSION_MAJOR", "LWIP_VERSION_MINOR", "LWIP_VERSION_REVISION"),
    ),
    _Library(
        "lib/mbedtls",
        "mbedtls",
        None,
        "include/mbedtls/build_info.h",
        "pattern",
        pattern=r'^\s*#\s*define\s+MBEDTLS_VERSION_STRING\s+"([^"\n]+)"',
    ),
    _Library(
        "lib/tinyusb",
        "tinyusb",
        None,
        "src/tusb_option.h",
        "defines",
        ("TUSB_VERSION_MAJOR", "TUSB_VERSION_MINOR", "TUSB_VERSION_REVISION"),
    ),
    _Library(
        "lib/pico-sdk",
        "pico-sdk",
        None,
        "pico_sdk_version.cmake",
        "cmake",
        ("PICO_SDK_VERSION_MAJOR", "PICO_SDK_VERSION_MINOR", "PICO_SDK_VERSION_REVISION"),
    ),
    _Library(
        "lib/libffi",
        "libffi",
        None,
        "configure.ac",
        "pattern",
        pattern=r"^\s*AC_INIT\s*\(\s*\[?[^,\]]+\]?\s*,\s*\[?([^,\]\n]+)\]?",
    ),
    _Library(
        "lib/protobuf-c",
        "protobuf-c",
        None,
        "configure.ac",
        "pattern",
        pattern=r"^\s*AC_INIT\s*\(\s*\[[^\]]+\]\s*,\s*\[([^\]]+)\]",
    ),
    _Library(
        "lib/libmetal",
        "libmetal",
        None,
        "VERSION",
        "make",
        ("VERSION_MAJOR", "VERSION_MINOR", "VERSION_PATCH"),
    ),
    _Library(
        "lib/open-amp",
        "open-amp",
        None,
        "VERSION",
        "make",
        ("VERSION_MAJOR", "VERSION_MINOR", "VERSION_PATCH"),
    ),
    _Library(
        "lib/libhydrogen",
        "libhydrogen",
        None,
        "library.properties",
        "pattern",
        pattern=r"^\s*version\s*=\s*(\S+)",
    ),
    _Library(
        "lib/cyw43-driver",
        "cyw43-driver",
        None,
        "src/cyw43.h",
        "defines",
        ("CYW43_VERSION_MAJOR", "CYW43_VERSION_MINOR", "CYW43_VERSION_MICRO"),
    ),
    _Library(
        "lib/fsp",
        "fsp",
        None,
        "ra/fsp/inc/fsp_version.h",
        "pattern",
        pattern=r'^\s*#\s*define\s+FSP_VERSION_STRING\s+\(?\s*"([^"\n]+)"',
    ),
    _Library("lib/nxp_driver", "nxp-driver", None, "sdk/version.txt", "text"),
    _Library(
        "lib/alif_ensemble-cmsis-dfp",
        "alif-ensemble-cmsis-dfp",
        None,
        "AlifSemiconductor.Ensemble.pdsc",
        "pattern",
        pattern=r'<release\s+version="([^"]+)"',
    ),
    _Library(
        "lib/berkeley-db-1.xx",
        "berkeley-db-1.xx",
        None,
        "changelog",
        "pattern",
        pattern=r"^\s*[\d.]+\s*->\s*([\d.]+)",
    ),
    _Library(
        "lib/mynewt-nimble",
        "mynewt-nimble",
        None,
        "RELEASE_NOTES.md",
        "lower-bound",
        pattern=r"Apache NimBLE\s+v([\d.]+)",
    ),
    _Library(
        "lib/littlefs",
        "littlefs-lfs1",
        "https://github.com/littlefs-project/littlefs",
        "lfs1.h",
        "littlefs",
        ("LFS1_VERSION",),
    ),
    _Library(
        "lib/littlefs",
        "littlefs-lfs2",
        "https://github.com/littlefs-project/littlefs",
        "lfs2.h",
        "littlefs",
        ("LFS2_VERSION",),
    ),
    _Library("lib/oofatfs", "oofatfs", "https://github.com/micropython/oofatfs", "ff.h", "fatfs"),
    _Library("lib/re1.5", "re1.5", "https://github.com/pfalcon/re1.5"),
    _Library("lib/uzlib", "uzlib", "https://github.com/pfalcon/uzlib"),
    _Library(
        "lib/crypto-algorithms", "crypto-algorithms", "https://github.com/B-Con/crypto-algorithms"
    ),
    _Library("lib/libm", "libm", "https://github.com/32bitmicro/newlib-nano-2"),
    _Library(
        "lib/libm_dbl",
        "libm-dbl",
        "https://musl.libc.org",
        "README",
        "pattern",
        pattern=r"musl library,\s*v([\d.]+)",
    ),
    _Library("lib/axtls", "axtls"),
    _Library("lib/wiznet5k", "wiznet5k"),
    _Library("lib/arduino-lib", "arduino-lib"),
    _Library("lib/alif-security-toolkit", "alif-security-toolkit"),
)
_STM32 = _Library("lib/stm32lib", "stm32lib")
_ASF4 = _Library("lib/asf4", "asf4")


def _read(root: Path, relative: str) -> str:
    try:
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ExtractorError(f"{relative}: version metadata escapes MicroPython root")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExtractorError(f"cannot read {relative}: {exc}") from exc


def _search(pattern: str, text: str, source: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match or not match.group(1).strip():
        raise ExtractorError(f"{source}: version field missing or invalid ({pattern})")
    return match.group(1).strip()


def _define(text: str, field: str, source: str) -> str:
    return _search(
        rf'^\s*#\s*define\s+{re.escape(field)}\s+\(?\s*"?([^\s")]+)',
        text,
        source,
    )


def _numeric(value: str, source: str) -> str:
    match = re.fullmatch(r"(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*", value)
    if not match:
        raise ExtractorError(f"{source}: invalid numeric version field {value!r}")
    number = match.group(1)
    return str(int(number, 16 if number.lower().startswith("0x") else 10))


def _version(root: Path, library: _Library) -> dict[str, Any]:
    if library.version_file is None:
        return {
            "version": None,
            "version_source": "unknown",
            "gaps": ["No exact library version is declared in known in-tree metadata."],
        }
    source = f"{library.path}/{library.version_file}"
    text = _read(root, source)
    mode = library.mode
    if mode in {"defines", "cmake", "make"}:
        values = []
        for field in library.fields:
            if mode == "defines":
                value = _define(text, field, source)
            elif mode == "cmake":
                value = _search(rf'^\s*set\s*\(\s*{field}\s+"?([^\s")]+)', text, source)
            else:
                value = _search(rf"^\s*{field}\s*=\s*(\S+)", text, source)
            values.append(_numeric(value, source))
        version = ".".join(values)
        if mode == "cmake":
            prerelease = re.search(
                r'^\s*set\s*\(\s*PICO_SDK_VERSION_PRE_RELEASE_ID\s+"?([^\s")]+)',
                text,
                re.MULTILINE,
            )
            if prerelease:
                version += f"-{prerelease.group(1)}"
    elif mode == "littlefs":
        value = _define(text, library.fields[0], source)
        if not re.fullmatch(r"0[xX][0-9a-fA-F]{1,8}[uUlL]*", value):
            raise ExtractorError(f"{source}: invalid packed littlefs version {value!r}")
        number = int(_numeric(value, source))
        version = f"{number >> 16}.{number & 0xFFFF}"
    elif mode == "fatfs":
        base = _search(
            r"FatFs\s+-\s+Generic\s+FAT\s+Filesystem\s+module\s+(R[\w.]+)", text, source
        )
        revision = _numeric(_define(text, "FF_DEFINED", source), source)
        version = f"{base}-rev{revision}"
    elif mode == "text":
        version = text.strip()
        if not version or "\n" in version:
            raise ExtractorError(f"{source}: expected one nonempty version line")
    else:
        version = _search(library.pattern, text, source)
    result = {"version": version, "version_source": "in-tree", "version_evidence": source}
    if mode == "lower-bound":
        result.update(
            version=None,
            version_source="declared-lower-bound",
            version_lower_bound=version,
            gaps=["Release notes establish a lower bound, not the selected tree's exact release."],
        )
    return result


def _nested_repository(source: Path, directory: Path) -> bool:
    """Keep independently checked-out nested libraries for repository resolution."""
    for parent in source.parents:
        if parent == directory:
            return False
        if (parent / ".git").exists():
            return True
    return False


def _record(root: Path, library: _Library, sources: list[Path]) -> dict[str, Any]:
    return {
        "name": library.name,
        "directory": str(root / library.path),
        "sources": sorted({str(source) for source in sources}),
        **({"upstream": library.upstream} if library.upstream is not None else {}),
        **_version(root, library),
    }


def _stm32_family(relative: Path) -> str | None:
    parts = relative.parts
    candidate = None
    if len(parts) >= 2 and parts[0] == "CMSIS":
        candidate = parts[1]
    elif parts:
        candidate = parts[0].removesuffix("_HAL_Driver")
    if candidate and re.fullmatch(r"STM32[A-Z][0-9A-Z]xx", candidate):
        return candidate
    return None


def selected_libraries(micropython_root: Path, sources: Sequence[Path]) -> list[dict]:
    """Return known libraries with selected sources physically under this root.

    Records contain name, absolute directory and deduplicated absolute sources,
    optional upstream, version (string or None), and version_source. Unknown
    paths and nested repositories are left to the caller's repository fallback.
    Relative input paths are relative to micropython_root. Directory boundaries
    and symlink targets, not basename/prefix similarity, determine ownership.

    Upstream identities are provided only for the adapted vendored registry.
    Submodule repository identities are the caller's responsibility.

    version_evidence is a root-relative metadata path. NimBLE additionally has
    version_lower_bound, with version=None and version_source=declared-lower-bound.
    STM32 family records use version_kind=cmsis-device: this is the CMSIS device
    package version, not a claim about the HAL's release. Their directory is the
    stm32lib root because a family's HAL and CMSIS sources occupy sibling trees.
    Unversioned records carry gaps. Known selected extractors fail hard.
    """
    root = Path(micropython_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"MicroPython root is not a directory: {root}")
    groups: dict[_Library, list[Path]] = {}
    stm32: dict[str | None, list[Path]] = {}
    asf4: dict[str | None, list[Path]] = {}
    for selected in sorted(set(Path(path) for path in sources)):
        source = (root / selected).resolve()
        if not source.is_relative_to(root):
            continue
        if not source.is_file():
            raise ValueError(f"Selected source is not a file: {source}")
        relative = source.relative_to(root)
        for library in (*_LIBRARIES, _STM32, _ASF4):
            boundary = Path(library.path)
            if not relative.is_relative_to(boundary):
                continue
            if _nested_repository(source, root / boundary):
                break
            inside = relative.relative_to(boundary)
            if library.mode == "littlefs" and inside.as_posix() not in {
                f"{library.version_file[:-2]}.c",
                library.version_file,
                f"{library.version_file[:-2]}_util.c",
                f"{library.version_file[:-2]}_util.h",
            }:
                continue
            if library == _STM32:
                stm32.setdefault(_stm32_family(inside), []).append(source)
            elif library == _ASF4:
                family = (
                    inside.parts[0]
                    if len(inside.parts) > 1 and re.fullmatch(r"sam[de][0-9]{2}", inside.parts[0])
                    else None
                )
                asf4.setdefault(family, []).append(source)
            else:
                groups.setdefault(library, []).append(source)
            break
    records = [_record(root, library, paths) for library, paths in groups.items()]
    for family, paths in stm32.items():
        record = _record(root, _STM32, paths)
        if family is not None:
            metadata = f"lib/stm32lib/CMSIS/{family}/Include/{family.lower()}.h"
            text = _read(root, metadata)
            values = [
                _numeric(
                    _search(
                        rf"^\s*#\s*define\s+\w*VERSION_{field}\s+\(\s*(0x[0-9a-fA-F]+)",
                        text,
                        metadata,
                    ),
                    metadata,
                )
                for field in ("MAIN", "SUB1", "SUB2")
            ]
            record.update(
                name=f"stm32lib-{family}",
                version=".".join(values),
                version_source="in-tree",
                version_kind="cmsis-device",
                version_evidence=metadata,
            )
            del record["gaps"]
        records.append(record)
    for family, paths in asf4.items():
        if family is None:
            records.append(_record(root, _ASF4, paths))
            continue
        metadata = f"lib/asf4/{family}/include/component-version.h"
        text = _read(root, metadata)
        version = _define(text, "COMPONENT_VERSION_STRING", metadata)
        build = _numeric(_define(text, "BUILD_NUMBER", metadata), metadata)
        library = _Library(f"lib/asf4/{family}", f"asf4-{family}", _ASF4.upstream)
        record = _record(root, library, paths)
        record.update(
            version=f"{version}+build.{build}", version_source="in-tree", version_evidence=metadata
        )
        del record["gaps"]
        records.append(record)
    return sorted(records, key=lambda record: (record["directory"], record["name"]))
