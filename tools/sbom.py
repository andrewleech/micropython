#!/usr/bin/env python3
"""Generate an SPDX or CycloneDX SBOM from repository or build inputs."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

from packageurl import PackageURL

from sbom_inputs import read_inputs
from sbom_licences import _covers, _load, resolve_licences
from sbom_writers import write_sbom


def run(args, cwd=None):
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def parse_gitmodules(repo):
    """Return list of submodule dicts {name, path, url, branch?}."""
    text = run(
        ["git", "config", "--file", ".gitmodules", "--list"],
        cwd=str(repo),
    )
    sections = {}
    for line in text.splitlines():
        m = re.match(r"^submodule\.(.+)\.([^=]+)=(.*)$", line)
        if not m:
            continue
        name, key, value = m.group(1), m.group(2), m.group(3)
        sections.setdefault(name, {"name": name})[key] = value
    return [s for s in sections.values() if "path" in s and "url" in s]


def submodule_sha(repo, path):
    """Pinned commit SHA for a submodule, read from the parent repo gitlink."""
    text = run(["git", "ls-tree", "HEAD", path], cwd=str(repo))
    parts = text.split()
    if len(parts) < 3 or parts[1] != "commit":
        return None
    return parts[2]


def ls_remote_tags(url, timeout=60):
    """Return {commit_sha: tag_name} for tags published on the remote.

    For annotated tags ``git ls-remote`` returns both ``refs/tags/<t>`` (the
    tag object SHA) and ``refs/tags/<t>^{}`` (the commit it points at). We
    prefer the dereferenced form so the SHA matches the gitlink commit.
    """
    try:
        text = subprocess.run(
            ["git", "ls-remote", "--tags", url],
            check=True,
            text=True,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"warning: ls-remote failed for {url}: {e}", file=sys.stderr)
        return {}
    tags = {}
    deref = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split("\t", 1)
        if not ref.startswith("refs/tags/"):
            continue
        tag = ref[len("refs/tags/") :]
        if tag.endswith("^{}"):
            deref[sha] = tag[:-3]
        else:
            tags.setdefault(sha, tag)
    merged = dict(tags)
    merged.update(deref)  # dereferenced wins for annotated tags
    return merged


def url_to_purl(url, sha, fallback_name=None):
    """Build a canonical Package URL for an upstream repository."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    version = f"@{quote(sha, safe='')}" if sha else ""
    if parsed.netloc.lower() == "github.com" and "/" in path:
        result = "pkg:github/" + "/".join(quote(part, safe="") for part in path.split("/")) + version
    else:
        name = path.rsplit("/", 1)[-1] or fallback_name or parsed.netloc
        result = f"pkg:generic/{quote(name, safe='')}{version}?vcs_url={quote(url, safe='')}"
    return PackageURL.from_string(result).to_string()


# --- in-tree version extractors --------------------------------------------
#
# Each extractor takes the repo root (Path) and returns a version string, or
# raises ExtractorError if its expected source file/field is missing or
# unparseable. A failing extractor signals that the upstream library has
# changed where it stores its version — the registry needs to be updated, and
# the script must NOT silently mask the failure with a tag- or SHA-fallback.
#
# Submodules that aren't checked out are detected separately (see
# resolve_version) and are handled with a warning + fall-through, since that
# is a user-environment issue, not an extractor bug.


class ExtractorError(Exception):
    """An in-tree extractor's expected file or field is missing/unparseable."""


def _read(repo, relpath):
    """Read a file from the repo. Raises ExtractorError if missing/unreadable."""
    try:
        return (repo / relpath).read_text(errors="replace")
    except FileNotFoundError:
        raise ExtractorError(f"file not found: {relpath}")
    except OSError as e:
        raise ExtractorError(f"cannot read {relpath}: {e}")


def _defines(text, names, source):
    """Pull ``#define NAME VALUE`` pairs; raise on any missing."""
    out = {}
    missing = []
    for name in names:
        m = re.search(
            rf"^\s*#\s*define\s+{re.escape(name)}\s+\(?\s*\"?([^\s\")]+)",
            text,
            re.MULTILINE,
        )
        if m:
            out[name] = m.group(1).rstrip("U")
        else:
            missing.append(name)
    if missing:
        raise ExtractorError(f"{source}: missing #define(s): {', '.join(missing)}")
    return out


def _cmake_set(text, names, source):
    """Pull ``set(NAME VALUE)`` pairs from a CMake file; raise on any missing."""
    out = {}
    missing = []
    for name in names:
        m = re.search(rf"set\s*\(\s*{re.escape(name)}\s+\"?([^\s\")]+)", text, re.IGNORECASE)
        if m:
            out[name] = m.group(1)
        else:
            missing.append(name)
    if missing:
        raise ExtractorError(f"{source}: missing CMake set(): {', '.join(missing)}")
    return out


def _make_assigns(text, names, source):
    """Pull ``KEY = VALUE`` pairs from a Make-style fragment; raise on any missing."""
    out = {}
    missing = []
    for name in names:
        m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(\S+)", text, re.MULTILINE)
        if m:
            out[name] = m.group(1)
        else:
            missing.append(name)
    if missing:
        raise ExtractorError(f"{source}: missing key(s): {', '.join(missing)}")
    return out


def _search(pattern, text, source, flags=0):
    """Run a regex; raise ExtractorError if it does not match."""
    m = re.search(pattern, text, flags)
    if not m:
        raise ExtractorError(f"{source}: pattern did not match: {pattern}")
    return m


def _ext_lwip(repo):
    rel = "lib/lwip/src/include/lwip/init.h"
    v = _defines(
        _read(repo, rel),
        ["LWIP_VERSION_MAJOR", "LWIP_VERSION_MINOR", "LWIP_VERSION_REVISION"],
        rel,
    )
    return f"{v['LWIP_VERSION_MAJOR']}.{v['LWIP_VERSION_MINOR']}.{v['LWIP_VERSION_REVISION']}"


def _ext_mbedtls(repo):
    rel = "lib/mbedtls/include/mbedtls/build_info.h"
    return _search(
        r'#\s*define\s+MBEDTLS_VERSION_STRING\s+"([^"]+)"', _read(repo, rel), rel
    ).group(1)


def _ext_tinyusb(repo):
    rel = "lib/tinyusb/src/tusb_option.h"
    v = _defines(
        _read(repo, rel),
        ["TUSB_VERSION_MAJOR", "TUSB_VERSION_MINOR", "TUSB_VERSION_REVISION"],
        rel,
    )
    return f"{v['TUSB_VERSION_MAJOR']}.{v['TUSB_VERSION_MINOR']}.{v['TUSB_VERSION_REVISION']}"


def _ext_pico_sdk(repo):
    rel = "lib/pico-sdk/pico_sdk_version.cmake"
    text = _read(repo, rel)
    v = _cmake_set(
        text,
        ["PICO_SDK_VERSION_MAJOR", "PICO_SDK_VERSION_MINOR", "PICO_SDK_VERSION_REVISION"],
        rel,
    )
    ver = f"{v['PICO_SDK_VERSION_MAJOR']}.{v['PICO_SDK_VERSION_MINOR']}.{v['PICO_SDK_VERSION_REVISION']}"
    # Pre-release id is optional; only honour an uncommented set().
    live = re.search(
        r"^\s*set\s*\(\s*PICO_SDK_VERSION_PRE_RELEASE_ID\s+\"?([^\s\")]+)", text, re.MULTILINE
    )
    if live:
        ver += f"-{live.group(1)}"
    return ver


def _ext_libffi(repo):
    rel = "lib/libffi/configure.ac"
    return (
        _search(r"AC_INIT\s*\(\s*\[?[^,\]]+\]?\s*,\s*\[?([^,\]]+)\]?", _read(repo, rel), rel)
        .group(1)
        .strip()
    )


def _ext_protobuf_c(repo):
    # AC_INIT spans multiple lines: AC_INIT([protobuf-c],\n        [1.4.1],\n ...
    rel = "lib/protobuf-c/configure.ac"
    return (
        _search(r"AC_INIT\s*\(\s*\[[^\]]+\]\s*,\s*\[([^\]]+)\]", _read(repo, rel), rel, re.DOTALL)
        .group(1)
        .strip()
    )


def _ext_libmetal(repo):
    rel = "lib/libmetal/VERSION"
    v = _make_assigns(_read(repo, rel), ["VERSION_MAJOR", "VERSION_MINOR", "VERSION_PATCH"], rel)
    return f"{v['VERSION_MAJOR']}.{v['VERSION_MINOR']}.{v['VERSION_PATCH']}"


def _ext_open_amp(repo):
    rel = "lib/open-amp/VERSION"
    v = _make_assigns(_read(repo, rel), ["VERSION_MAJOR", "VERSION_MINOR", "VERSION_PATCH"], rel)
    return f"{v['VERSION_MAJOR']}.{v['VERSION_MINOR']}.{v['VERSION_PATCH']}"


def _ext_libhydrogen(repo):
    # library.properties is the project's canonical Arduino metadata file.
    rel = "lib/libhydrogen/library.properties"
    return _search(r"^version\s*=\s*(\S+)", _read(repo, rel), rel, re.MULTILINE).group(1)


def _ext_cyw43(repo):
    rel = "lib/cyw43-driver/src/cyw43.h"
    v = _defines(
        _read(repo, rel),
        ["CYW43_VERSION_MAJOR", "CYW43_VERSION_MINOR", "CYW43_VERSION_MICRO"],
        rel,
    )
    return f"{v['CYW43_VERSION_MAJOR']}.{v['CYW43_VERSION_MINOR']}.{v['CYW43_VERSION_MICRO']}"


def _ext_fsp(repo):
    rel = "lib/fsp/ra/fsp/inc/fsp_version.h"
    return _search(
        r'#\s*define\s+FSP_VERSION_STRING\s+\(?\s*"([^"]+)"', _read(repo, rel), rel
    ).group(1)


def _ext_nxp_driver(repo):
    rel = "lib/nxp_driver/sdk/version.txt"
    text = _read(repo, rel).strip()
    if not text:
        raise ExtractorError(f"{rel}: file is empty")
    return text


def _ext_alif_dfp(repo):
    # First <release version="X" ...> in the PDSC manifest is the latest.
    rel = "lib/alif_ensemble-cmsis-dfp/AlifSemiconductor.Ensemble.pdsc"
    return _search(r'<release\s+version="([^"]+)"', _read(repo, rel), rel).group(1)


def _ext_berkeley_db(repo):
    # The changelog is newest-first, with entries headed "<prev> -> <new>";
    # the first target is the release this tree corresponds to (1.85).
    rel = "lib/berkeley-db-1.xx/changelog"
    return _search(r"^\s*[\d.]+\s*->\s*([\d.]+)", _read(repo, rel), rel, re.MULTILINE).group(1)


def _ext_nimble(repo):
    # Deliberately not version.yml: that file carries "repo.version: 0.0.0" and
    # documents itself as always 0.0.0 on the master branch.  RELEASE_NOTES.md
    # is newest-first, so its top entry is the most recent tagged release the
    # pinned tree contains.  Note the pin may sit on development commits after
    # that release, so treat this as a lower bound rather than an exact pin.
    rel = "lib/mynewt-nimble/RELEASE_NOTES.md"
    return _search(r"Apache NimBLE\s+v([\d.]+)", _read(repo, rel), rel).group(1)


# --- vendored (non-submodule) extractors ------------------------------------
#
# Versions for vendored libraries that embed their version in source. Same
# error semantics as the submodule extractors above (raise ExtractorError on
# missing file/field).


def _vext_littlefs(rel, define):
    """Decode littlefs's 32-bit packed-int version macro."""

    def _inner(repo):
        m = _search(rf"#\s*define\s+{define}\s+0x([0-9a-fA-F]+)", _read(repo, rel), rel)
        n = int(m.group(1), 16)
        return f"{(n >> 16) & 0xFFFF}.{n & 0xFFFF}"

    return _inner


def _vext_oofatfs(repo):
    rel = "lib/oofatfs/ff.h"
    text = _read(repo, rel)
    base = _search(
        r"FatFs\s+-\s+Generic\s+FAT\s+Filesystem\s+module\s+(R[\w.]+)", text, rel
    ).group(1)
    rev = _search(r"#\s*define\s+FF_DEFINED\s+(\d+)", text, rel).group(1)
    return f"{base}-rev{rev}"


# --- sub-component builders -------------------------------------------------
#
# A few submodules are not single libraries but collections of independently
# versioned per-MCU-family trees, so one version for the whole submodule is
# never correct. These builders emit CycloneDX nested sub-components — one per
# family — under the parent submodule component, which keeps the parent's
# commit pin intact while still exposing a usable version per family.


def _sub_stm32lib(repo):
    """One sub-component per STM32 family, from its CMSIS device header.

    The version macro prefix is inconsistent across families (F0 uses
    ``__STM32F0_DEVICE_VERSION_*``, H7 ``__STM32H7xx_CMSIS_DEVICE_VERSION_*``,
    most others ``__STM32<fam>_CMSIS_VERSION_*``), so match on the common
    suffix instead of reconstructing each prefix.

    The CMSIS device package version is used rather than the HAL driver
    version because only F4 and F7 still declare the latter, whereas every
    family carries the former.
    """
    base = repo / "lib/stm32lib/CMSIS"
    if not base.is_dir():
        raise ExtractorError("lib/stm32lib/CMSIS: directory missing")
    subs = []
    for d in sorted(p for p in base.iterdir() if p.name.startswith("STM32")):
        rel = f"lib/stm32lib/CMSIS/{d.name}/Include/{d.name.lower()}.h"
        text = _read(repo, rel)
        parts = []
        for field in ("MAIN", "SUB1", "SUB2"):
            m = _search(rf"VERSION_{field}\s+\(\s*0x([0-9A-Fa-f]+)", text, rel)
            parts.append(str(int(m.group(1), 16)))
        subs.append(
            {
                "type": "library",
                "group": "stm32lib",
                "name": d.name,
                "version": ".".join(parts),
                "properties": [
                    {"name": "submodule:path", "value": f"lib/stm32lib/CMSIS/{d.name}"},
                    {"name": "version:source", "value": "in-tree"},
                    {"name": "version:kind", "value": "cmsis-device"},
                ],
            }
        )
    if not subs:
        raise ExtractorError("lib/stm32lib/CMSIS: no STM32* families found")
    return subs


def _sub_asf4(repo):
    """One sub-component per ASF4 MCU tree, from its component-version.h.

    The per-MCU versions genuinely disagree (samd21 1.2, same51 1.1,
    samd51/same54 1.0), which is why the parent submodule carries no version.
    """
    base = repo / "lib/asf4"
    if not base.is_dir():
        raise ExtractorError("lib/asf4: directory missing")
    subs = []
    for d in sorted(p for p in base.iterdir() if (p / "include/component-version.h").is_file()):
        rel = f"lib/asf4/{d.name}/include/component-version.h"
        v = _defines(_read(repo, rel), ["COMPONENT_VERSION_STRING", "BUILD_NUMBER"], rel)
        subs.append(
            {
                "type": "library",
                "group": "asf4",
                "name": d.name,
                # Build number kept as semver build metadata: the version
                # string alone ("1.0") is shared by samd51 and same54.
                "version": f"{v['COMPONENT_VERSION_STRING']}+build.{v['BUILD_NUMBER']}",
                "properties": [
                    {"name": "submodule:path", "value": f"lib/asf4/{d.name}"},
                    {"name": "version:source", "value": "in-tree"},
                ],
            }
        )
    if not subs:
        raise ExtractorError("lib/asf4: no MCU trees with component-version.h found")
    return subs


SUBCOMPONENTS = {
    "lib/stm32lib": _sub_stm32lib,
    "lib/asf4": _sub_asf4,
}


def _vext_libm_dbl(repo):
    # "...copied from the musl library,\nv1.1.16, and, unless otherwise..."
    rel = "lib/libm_dbl/README"
    return _search(r"musl library,\s*v([\d.]+)", _read(repo, rel), rel).group(1)


# Each entry describes a vendored third-party library (not a git submodule).
# ``extractor`` is optional — when absent, the component is emitted with
# version "unknown" and ``version:source=vendored-unknown``, which keeps the
# coverage gap visible to auditors instead of silently dropping the library.
VENDORED = [
    {
        "path": "lib/littlefs",
        "name": "littlefs-lfs1",
        "upstream": "https://github.com/littlefs-project/littlefs",
        "extractor": _vext_littlefs("lib/littlefs/lfs1.h", "LFS1_VERSION"),
    },
    {
        "path": "lib/littlefs",
        "name": "littlefs-lfs2",
        "upstream": "https://github.com/littlefs-project/littlefs",
        "extractor": _vext_littlefs("lib/littlefs/lfs2.h", "LFS2_VERSION"),
    },
    # micropython/oofatfs is the MP fork; upstream is ChaN's FatFs (no VCS).
    {
        "path": "lib/oofatfs",
        "name": "oofatfs",
        "upstream": "https://github.com/micropython/oofatfs",
        "extractor": _vext_oofatfs,
    },
    # re1.5 is Paul Sokolovsky's fork of Russ Cox's re1; version encoded in name.
    {
        "path": "lib/re1.5",
        "name": "re1.5",
        "upstream": "https://github.com/pfalcon/re1.5",
        "extractor": lambda _r: "1.5",
    },
    # The vendored copy is a subset of upstream's files, further modified for
    # MicroPython, and carries no version marker — pinning an upstream release
    # here would misrepresent what is actually compiled.
    {"path": "lib/uzlib", "name": "uzlib", "upstream": "https://github.com/pfalcon/uzlib"},
    # Public domain, upstream publishes no releases or tags to key off.
    {
        "path": "lib/crypto-algorithms",
        "name": "crypto-algorithms",
        "upstream": "https://github.com/B-Con/crypto-algorithms",
    },
    # libm is adapted from newlib-nano-2 (see the header comment in
    # lib/libm/fdlibm.h), itself derived from SunPro's fdlibm — not from musl.
    # No version is declared in-tree, so this stays a documented gap.
    {
        "path": "lib/libm",
        "name": "libm",
        "upstream": "https://github.com/32bitmicro/newlib-nano-2",
    },
    # libm_dbl's README names the exact musl release its files came from.
    {
        "path": "lib/libm_dbl",
        "name": "libm-dbl",
        "upstream": "https://musl.libc.org",
        "extractor": _vext_libm_dbl,
    },
]


def _vendored_purl(upstream, name, version):
    return url_to_purl(upstream, None if version == "unknown" else version, name)


def build_vendored_component(repo, v):
    name = v["name"]
    path = v["path"]
    upstream = v["upstream"]
    extractor = v.get("extractor")
    if extractor:
        # Vendored dirs live inside the parent repo — missing tree is a hard
        # error (the repo is malformed), not a "user hasn't run submodule
        # update" situation.
        if not _is_checked_out(repo / path):
            raise ExtractorError(f"{path}: vendored directory missing")
        version = extractor(repo)
        source = "vendored-in-tree"
    else:
        version = "unknown"
        source = "vendored-unknown"
    return {
        "type": "library",
        "name": name,
        "version": version,
        "purl": _vendored_purl(upstream, name, version),
        "externalReferences": [{"type": "vcs", "url": upstream}],
        "properties": [
            {"name": "vendored:path", "value": path},
            {"name": "version:source", "value": source},
        ],
    }


EXTRACTORS = {
    "lib/lwip": _ext_lwip,
    "lib/mbedtls": _ext_mbedtls,
    "lib/tinyusb": _ext_tinyusb,
    "lib/pico-sdk": _ext_pico_sdk,
    "lib/libffi": _ext_libffi,
    "lib/protobuf-c": _ext_protobuf_c,
    "lib/libmetal": _ext_libmetal,
    "lib/open-amp": _ext_open_amp,
    "lib/libhydrogen": _ext_libhydrogen,
    "lib/cyw43-driver": _ext_cyw43,
    "lib/fsp": _ext_fsp,
    "lib/nxp_driver": _ext_nxp_driver,
    "lib/alif_ensemble-cmsis-dfp": _ext_alif_dfp,
    "lib/berkeley-db-1.xx": _ext_berkeley_db,
    "lib/mynewt-nimble": _ext_nimble,
}

# Submodules that deliberately have no extractor, and why — recorded so the
# gaps don't get re-investigated.  These fall through to the ls-remote/SHA
# layers, which is the honest result rather than a misleading number.
#
# lib/axtls        config/Rules.mak carries MAJOR/MINOR/SUBLEVEL_VERSION, but
#                  that file is BusyBox's build system (it still sets
#                  "PROG := busybox"); the 1.1.0 there is BusyBox's, not
#                  axTLS's.  No axTLS version is declared in-tree.
# lib/stm32lib     A collection of ST CMSIS-device and HAL trees for 14 MCU
#                  families, each independently versioned, so no single value
#                  is meaningful.  Versions are emitted per family as nested
#                  sub-components instead — see SUBCOMPONENTS.
# lib/asf4         Same shape, same treatment: per-MCU component-version.h
#                  files disagree (samd21 1.2, same51 1.1, samd51/same54 1.0).
# lib/wiznet5k     ioLibrary_Driver ships no version marker; the VERSION
#                  define in Internet/FTPServer/ftpd.h is the FTP server's.
# lib/arduino-lib  No version file; README is licence text only.
# lib/alif-security-toolkit
#                  Releases are tagged (v1.104.0, v1.110.0, v1.112.0) but only
#                  on the separate "vendor" branch, and the pinned commit is
#                  not a descendant of any of them, so neither an exact-tag
#                  match nor "git describe" resolves it.
#
# Of these, only alif-security-toolkit could plausibly be resolved in future,
# if upstream ever tags the branch that MicroPython actually pins.


# CPE 2.3 templates for vulnerability scanners that match against NVD/CPE
# (e.g., Grype). These map a submodule path to the most common
# vendor:product string seen in NVD's dictionary for that project; the
# {version} placeholder is filled with the resolved version. CPEs are only
# emitted when the version came from an in-tree extractor or a tag — a raw
# commit SHA cannot be matched against a CPE version string.
#
# Note: CPE strings are inherently fuzzy. NVD frequently lists multiple CPEs
# for the same project (e.g. mbedtls under both `arm:mbed_tls` and
# `trustedfirmware:mbed_tls`), and the matches a scanner produces against
# these should be sanity-checked before being treated as confirmed.
CPE_OVERRIDES = {
    "lib/mbedtls": "cpe:2.3:a:arm:mbed_tls:{version}:*:*:*:*:*:*:*",
    "lib/lwip": "cpe:2.3:a:lwip_project:lwip:{version}:*:*:*:*:*:*:*",
    "lib/libffi": "cpe:2.3:a:libffi_project:libffi:{version}:*:*:*:*:*:*:*",
    "lib/protobuf-c": "cpe:2.3:a:protobuf-c_project:protobuf-c:{version}:*:*:*:*:*:*:*",
    "lib/tinyusb": "cpe:2.3:a:tinyusb_project:tinyusb:{version}:*:*:*:*:*:*:*",
}

# Override the .gitmodules-derived purl when scanners (notably OSV.dev)
# index the project under a different upstream URL — e.g., relocated GitHub
# orgs. The {sha} placeholder is filled with the pinned commit SHA.
PURL_OVERRIDES = {
    # github.com/ARMmbed/mbedtls is archived; the project lives at Mbed-TLS.
    "lib/mbedtls": "pkg:github/Mbed-TLS/mbedtls@{sha}",
}


def _cpe_version(v):
    """Strip a leading ``v`` from a tag-style version (NVD CPEs are bare semver)."""
    return v[1:] if len(v) > 1 and v[0] == "v" and v[1].isdigit() else v


def _is_checked_out(submod_dir):
    """A submodule directory is considered checked-out when it has any contents.

    git creates the directory but leaves it empty until ``submodule update``
    populates it.
    """
    try:
        return submod_dir.is_dir() and any(submod_dir.iterdir())
    except OSError:
        return False


def resolve_version(repo, submod, sha, *, offline):
    """Return (version_string, source_label) using the layered strategy.

    Raises ExtractorError if a registered extractor's expected file/field is
    missing — that is a real bug in the registry, not a fallback case. A
    submodule that simply isn't checked out yet emits a warning to stderr and
    falls through to ls-remote/SHA.
    """
    extractor = EXTRACTORS.get(submod["path"])
    if extractor:
        if _is_checked_out(repo / submod["path"]):
            return extractor(repo), "in-tree"
        print(
            f"warning: {submod['path']} is not checked out; "
            f"in-tree extractor skipped (run `git submodule update --init` "
            f"for a more precise version)",
            file=sys.stderr,
        )
    if not offline:
        tag = ls_remote_tags(submod["url"]).get(sha)
        if tag:
            return tag, "git-tag"
    return sha[:12], "git-sha"


def build_component(repo, submod, sha, version, source):
    name = submod["path"].split("/")[-1]
    properties = [
        {"name": "submodule:path", "value": submod["path"]},
        {"name": "git:commit", "value": sha},
        {"name": "version:source", "value": source},
    ]
    if submod.get("branch"):
        properties.append({"name": "git:branch", "value": submod["branch"]})

    purl_template = PURL_OVERRIDES.get(submod["path"])
    purl = (PackageURL.from_string(purl_template.format(sha=sha)).to_string()
            if purl_template else url_to_purl(submod["url"], sha))

    component = {
        "type": "library",
        "name": name,
        "version": version,
        "purl": purl,
        "externalReferences": [{"type": "vcs", "url": submod["url"]}],
        "properties": properties,
    }

    cpe_template = CPE_OVERRIDES.get(submod["path"])
    if cpe_template and source in ("in-tree", "git-tag"):
        component["cpe"] = cpe_template.format(version=_cpe_version(version))

    sub_builder = SUBCOMPONENTS.get(submod["path"])
    if sub_builder:
        component["components"] = sub_builder(repo)

    return component


def repo_version(repo):
    try:
        return run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(repo),
        ).strip()
    except subprocess.CalledProcessError:
        return "unknown"


def _component(name, path, version=None, revision=None, purl=None):
    return {"name": name, "path": path, "version": version, "revision": revision,
            "purl": purl, "licence": None, "licence_texts": {}, "licence_comments": [],
            "selection": [], "sources": [], "source_evidence": []}


def _display_path(repo, path):
    try:
        return path.relative_to(repo).as_posix() or "."
    except ValueError:
        return str(path)


def _inventory(repo):
    roots = {}
    for submod in parse_gitmodules(repo):
        roots.setdefault((repo / submod["path"]).resolve(), []).append(("submodule", submod))
    for vendor in VENDORED:
        roots.setdefault((repo / vendor["path"]).resolve(), []).append(("vendored", vendor))
    return roots


def _build_inventory_component(repo, kind, item, offline):
    sha = None
    if kind == "submodule":
        sha = submodule_sha(repo, item["path"])
        if not sha:
            raise ValueError(f"no gitlink for {item['path']}")
        version, source = resolve_version(repo, item, sha, offline=offline)
        raw = build_component(repo, item, sha, version, source)
    else:
        raw = build_vendored_component(repo, item)
    component = _component(raw["name"], item["path"],
                           None if raw["version"] == "unknown" else raw["version"],
                           sha, raw["purl"])
    component["owner_root"] = str((repo / item["path"]).resolve())
    component["declaration_root"] = str(repo)
    for field in ("cpe", "properties", "externalReferences"):
        if field in raw:
            component[field] = raw[field]
    if "components" in raw:
        component["components"] = []
        for child in raw["components"]:
            path = next(p["value"] for p in child["properties"] if p["name"] == "submodule:path")
            normalized = _component(child["name"], path, child["version"])
            normalized["owner_root"] = component["owner_root"]
            normalized["declaration_root"] = str(repo)
            normalized["properties"] = child["properties"]
            normalized["group"] = child["group"]
            component["components"].append(normalized)
    return component


def _root_arg(value, repo):
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise ValueError(f"invalid root {value!r}; expected NAME=PATH")
    resolved = Path(path)
    resolved = (resolved if resolved.is_absolute() else repo / resolved).absolute()
    resolved = resolved.resolve()
    if not resolved.is_dir():
        raise ValueError(f"root is not a directory: {resolved}")
    return name, resolved


def _checkout_root(path):
    try:
        root = run(["git", "-C", str(path), "rev-parse", "--show-toplevel"]).strip()
        return Path(root).resolve()
    except (OSError, subprocess.CalledProcessError):
        return None


def _revision(repo):
    return run(["git", "rev-parse", "HEAD"], cwd=str(repo)).strip()


def _has_symlink(path, stop=None):
    for part in (path, *path.parents):
        if part == stop:
            break
        if part.is_symlink():
            return True
    return False


def _source_state(root):
    """Return tracked and changed paths for a Git checkout."""
    tracked = set(run(["git", "ls-files", "-z"], cwd=str(root)).split("\0"))
    status = run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                 cwd=str(root)).split("\0")
    changed = set()
    index = 0
    while index < len(status):
        entry = status[index]
        index += 1
        if not entry:
            continue
        changed.add(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            if index < len(status):
                changed.add(status[index])
                index += 1
    return tracked, changed

def _idf_component_roots(compdbs):
    """Read component boundaries from ESP-IDF build descriptions."""
    roots = {}
    for compdb in compdbs:
        description = Path(compdb).resolve().parent / "project_description.json"
        if not description.is_file():
            continue
        try:
            data = json.loads(description.read_text(encoding="utf-8"))
            idf = Path(data["idf_path"]).resolve()
            info = data["build_component_info"]
            if not isinstance(info, dict):
                raise ValueError("build_component_info is not an object")
            for name, component in info.items():
                if not isinstance(name, str) or not isinstance(component, dict):
                    raise ValueError("invalid component entry")
                directory = component.get("dir")
                if not isinstance(directory, str) or not Path(directory).is_absolute():
                    raise ValueError(f"invalid directory for component {name}")
                directory = Path(directory).resolve()
                managed = directory.parent.name == "managed_components"
                if directory.is_relative_to(idf / "components") or managed:
                    identity = (name, idf if directory.is_relative_to(idf / "components") else None)
                    if directory in roots and roots[directory] != identity:
                        raise ValueError(f"conflicting metadata for component {directory}")
                    roots[directory] = identity
        except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{description}: invalid ESP-IDF build description: {exc}") from exc
    return roots


def _build_document(repo, args):
    inventory = _inventory(repo)
    issues = []
    components = []
    repo_revision = _revision(repo)
    declaration_states = {}
    declaration_checkouts = {}

    def metadata_status(path):
        git_root = _checkout_root(path.parent)
        if git_root is None or _has_symlink(path, git_root):
            return "untracked"
        if git_root not in declaration_checkouts:
            try:
                declaration_checkouts[git_root] = _source_state(git_root)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ValueError(f"{git_root}: cannot determine source identity: {exc}") from exc
        tracked, changed = declaration_checkouts[git_root]
        relative = path.relative_to(git_root).as_posix()
        if relative not in tracked:
            return "untracked"
        if relative in changed:
            return "dirty"
        return None

    def declaration_state(root):
        if root not in declaration_states:
            declaration = root / "LICENSE.spdx.yaml"
            if not declaration.is_file():
                declaration_states[root] = (None, None)
            else:
                git_root = _checkout_root(root)
                if git_root is None:
                    status = "untracked"
                else:
                    if git_root not in declaration_checkouts:
                        declaration_checkouts[git_root] = _source_state(git_root)
                    tracked, changed = declaration_checkouts[git_root]
                    relative = declaration.relative_to(git_root).as_posix()
                    status = "untracked" if relative not in tracked else (
                        "dirty" if relative in changed else None
                    )
                digest = hashlib.sha256(declaration.read_bytes()).hexdigest()
                declaration_states[root] = (status, digest)
        return declaration_states[root]
    declaration_state(repo)

    if not args.compdb:
        for entries in inventory.values():
            for kind, item in entries:
                component = _build_inventory_component(repo, kind, item, args.offline)
                component["selection"].append("repository inventory")
                components.append(component)
        name = "micropython-repository"
    else:
        if not args.image:
            raise ValueError("--image is required with --compdb")
        explicit = {}
        for value in args.root:
            root_name, root = _root_arg(value, repo)
            if root in explicit:
                raise ValueError(f"duplicate explicit root: {root}")
            explicit[root] = root_name
        idf_metadata = _idf_component_roots(args.compdb)
        idf_components = {root: item[0] for root, item in idf_metadata.items()}
        roots = set(inventory) | set(explicit) | set(idf_components) | {repo}
        depfile_evidence = []
        records = read_inputs(repo, args.compdb, args.exclude, args.frozen, depfile_evidence)
        for evidence in depfile_evidence:
            if evidence["status"] == "read":
                continue
            source = _display_path(repo, evidence["source"])
            depfile = evidence.get("depfile")
            location = _display_path(repo, depfile) if depfile else "not specified"
            message = f"depfile {evidence['status']} for {source}: {location}"
            if evidence.get("error"):
                message += f" ({evidence['error']})"
            if message not in issues:
                issues.append(message)
        unsafe_roots = {
            candidate: item["path"]
            for candidate, entries in inventory.items()
            for _, item in entries
            if _has_symlink(repo / item["path"], repo)
        }
        checkout_roots = {repo: repo}
        source_states = {}
        submodule_revisions = {}
        idf_checkout_problems = {}

        def checkout_root(path):
            if path not in checkout_roots:
                checkout_roots[path] = _checkout_root(path)
            return checkout_roots[path]

        def idf_checkout_problem(checkout, owner):
            """Check each nested checkout against its parent's Git link."""
            while checkout != owner:
                parent = checkout_root(checkout.parent)
                if parent is None or not checkout.is_relative_to(parent):
                    return f"{checkout}: parent checkout unavailable"
                relative = checkout.relative_to(parent).as_posix()
                try:
                    expected = submodule_sha(parent, relative)
                    actual = _revision(checkout)
                except (OSError, subprocess.CalledProcessError):
                    return f"{checkout}: submodule identity unavailable"
                if not expected or actual != expected:
                    return f"{checkout}: submodule HEAD {actual} does not match gitlink {expected or 'unavailable'}"
                checkout = parent
            return None

        def validate_submodule(root, item):
            if root not in submodule_revisions:
                declared = repo / item["path"]
                if _has_symlink(declared, repo):
                    raise ValueError(f"{item['path']}: submodule root contains a symlink")
                expected = submodule_sha(repo, item["path"])
                if not expected:
                    raise ValueError(f"{item['path']}: submodule gitlink unavailable")
                if checkout_root(root) != root:
                    raise ValueError(f"{item['path']}: submodule checkout unavailable")
                try:
                    actual = _revision(root)
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise ValueError(f"{item['path']}: submodule checkout HEAD unavailable") from exc
                if actual != expected:
                    raise ValueError(
                        f"{item['path']}: submodule checkout HEAD {actual} does not match gitlink {expected}"
                    )
                submodule_revisions[root] = expected
            return submodule_revisions[root]

        def source_problem(git_root, path):
            if git_root not in source_states:
                try:
                    source_states[git_root] = _source_state(git_root)
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise ValueError(f"{git_root}: cannot determine source identity: {exc}") from exc
            tracked, changed = source_states[git_root]
            relative = path.relative_to(git_root).as_posix()
            if relative not in tracked:
                return "untracked"
            if relative in changed:
                return "dirty"
            return None
        nested_idf_components = {}
        declarations = {}
        groups = {}
        for record in records:
            source = record["source"]
            manifest = record.get("manifest")
            owner_path = manifest if manifest is not None else source
            matches = [root for root in roots if owner_path == root or root in owner_path.parents]
            root = max(matches, key=lambda p: len(p.parts)) if matches else None
            if root is None:
                root = checkout_root(owner_path.parent) or owner_path.parent
            identity_problems = []
            if root in idf_components and record["selection"] == "native":
                idf_path = idf_metadata[root][1]
                owner_checkout = checkout_root(idf_path) if idf_path else checkout_root(root)
                source_checkout = checkout_root(source.parent)
                if idf_path and not owner_checkout:
                    problem = f"{idf_path}: IDF checkout unavailable"
                    identity_problems.append(problem)
                    if problem not in issues:
                        issues.append(problem)
                elif owner_checkout and source_checkout and source_checkout != owner_checkout:
                    key = (source_checkout, owner_checkout)
                    if key not in idf_checkout_problems:
                        idf_checkout_problems[key] = idf_checkout_problem(*key)
                    problem = idf_checkout_problems[key]
                    if problem:
                        identity_problems.append(problem)
                        if problem not in issues:
                            issues.append(problem)
                    if source_checkout.is_relative_to(root):
                        root = source_checkout
                        nested_idf_components[root] = root.name
            for selected_path in (source, manifest):
                if selected_path is None:
                    continue
                for candidate, path in unsafe_roots.items():
                    if selected_path.is_relative_to(candidate):
                        raise ValueError(f"{path}: source root contains a symlink")
                submodule_roots = [
                    candidate for candidate, entries in inventory.items()
                    if selected_path.is_relative_to(candidate)
                    and any(kind == "submodule" for kind, _ in entries)
                ]
                for candidate in submodule_roots:
                    item = next(item for kind, item in inventory[candidate] if kind == "submodule")
                    validate_submodule(candidate, item)
                if submodule_roots:
                    git_root = max(submodule_roots, key=lambda p: len(p.parts))
                elif root != repo and selected_path.is_relative_to(root):
                    git_root = checkout_root(root)
                elif selected_path.is_relative_to(repo):
                    git_root = repo
                else:
                    git_root = checkout_root(root)
                problem = None
                if git_root and selected_path.is_relative_to(git_root):
                    problem = source_problem(git_root, selected_path)
                elif root in idf_components:
                    problem = "unverified"
                if problem:
                    message = f"{problem} selected source: {_display_path(repo, selected_path)}"
                    if message not in issues:
                        issues.append(message)
                    identity_problems.append(message)
            unreviewed = bool(identity_problems)
            if root == repo and record["selection"] == "native":
                relative = source.relative_to(repo)
                parts = relative.parts
                if len(parts) > 2 and parts[0] == "lib":
                    root = repo / "lib" / parts[1]
                    if repo not in declarations:
                        declarations[repo] = _load(repo)[0] if not declaration_state(repo)[0] else []
                    root_scope = _display_path(repo, root)
                    unreviewed = unreviewed or not any(
                        entry["scope"] == root_scope and "pin" in entry
                        for entry in declarations[repo]
                    )
                elif "managed_components" in parts:
                    pos = parts.index("managed_components")
                    root = repo.joinpath(*parts[:pos + 2])
                    unreviewed = True
                elif source_problem(repo, source) == "untracked":
                    root = source.parent
                    unreviewed = True
                if unreviewed:
                    issue = f"unreviewed source root: {_display_path(repo, root)}"
                    if issue not in issues:
                        issues.append(issue)
            if record["selection"] == "frozen" and not matches:
                issue = f"unmatched frozen package root: {owner_path}"
                if issue not in issues:
                    issues.append(issue)
            if record["selection"] == "frozen" and source != root and root not in source.parents:
                unreviewed = True
                issue = f"frozen source outside owning root: {source}"
                if issue not in issues:
                    issues.append(issue)
            entries = inventory.get(root, [])
            if len(entries) > 1:
                filename = source.name
                selected = [entry for entry in entries if
                            (entry[1]["name"].endswith("lfs1") and filename.startswith("lfs1")) or
                            (entry[1]["name"].endswith("lfs2") and filename.startswith("lfs2"))]
                entries = selected
                if not entries:
                    unreviewed = True
                    issue = f"unmatched inventory component: {_display_path(repo, source)}"
                    if issue not in issues:
                        issues.append(issue)
            scoped = None
            if record["selection"] == "native":
                declaration_root = repo if root == repo or repo in root.parents else (checkout_root(root) or root)
                if source.is_relative_to(declaration_root):
                    if declaration_root not in declarations:
                        declarations[declaration_root] = (
                            _load(declaration_root)[0] if not declaration_state(declaration_root)[0] else []
                        )
                    relative = source.relative_to(declaration_root).as_posix()
                    matches_scope = [
                        entry for entry in declarations[declaration_root]
                        if entry["scope"] != "." and _covers(entry["scope"], relative)
                    ]
                    def scope_rank(entry):
                        scope = entry["scope"]
                        prefix = re.split(r"[*?\[]", scope, maxsplit=1)[0]
                        if prefix != scope:
                            prefix = prefix.rpartition("/")[0]
                        depth = len(prefix.split("/")) if prefix else 0
                        return depth, len(scope.split("/")), len(scope)

                    matches_scope.sort(
                        key=scope_rank,
                        reverse=True,
                    )
                    if matches_scope:
                        candidate = matches_scope[0]
                        scope_path = declaration_root / candidate["scope"]
                        if scope_path != root and scope_path.is_relative_to(root):
                            scoped = candidate
            grouped_scope = None
            if (scoped and entries and entries[0][0] == "vendored"
                    and entries[0][1]["name"] == "oofatfs" and scoped.get("expression")):
                grouped_scope = (scoped["expression"], scoped.get("licenseComments"),
                                 scoped.get("licenseGap"))
            package = record.get("package")
            target = record.get("target")
            if record["selection"] == "frozen":
                key = (str(root), "frozen", package, str(manifest) if manifest else None,
                       unreviewed, bool(identity_problems))
            else:
                key = (str(root), "native", entries[0][1].get("name") if entries else None,
                       grouped_scope if grouped_scope else scoped["scope"] if scoped else None,
                       unreviewed, bool(identity_problems))
            if key not in groups:
                if record["selection"] == "frozen":
                    package_path = manifest.parent if manifest else root
                    revision = repo_revision if root == repo else None
                    submodules = [item for kind, item in inventory.get(root, []) if kind == "submodule"]
                    if submodules:
                        revision = validate_submodule(root, submodules[0])
                    elif root != repo and checkout_root(root):
                        revision = _revision(checkout_root(root))
                    name = ("micropython-frozen" if root == repo and package in (None, "micropython")
                            else package or root.name)
                    component = _component(name, _display_path(repo, package_path),
                                           record.get("version"), revision)
                    component["kind"] = "frozen"
                    component["manifest"] = _display_path(repo, manifest) if manifest else None
                    component["manifest_license"] = record.get("license")
                elif scoped:
                    revision = repo_revision if root == repo else None
                    submodules = [item for kind, item in inventory.get(root, []) if kind == "submodule"]
                    if submodules:
                        revision = validate_submodule(root, submodules[0])
                    elif root != repo and checkout_root(root):
                        revision = _revision(checkout_root(root))
                    if entries and not identity_problems:
                        component = _build_inventory_component(repo, *entries[0], args.offline)
                        if grouped_scope:
                            component["name"] += "-" + grouped_scope[0]
                        else:
                            component["name"] = scoped.get("name") or scoped["scope"]
                            component["path"] = _display_path(repo, declaration_root / scoped["scope"])
                    elif grouped_scope:
                        component = _component(
                            entries[0][1]["name"] + "-" + grouped_scope[0],
                            _display_path(repo, root),
                            revision=revision,
                        )
                    else:
                        component = _component(
                            scoped.get("name") or scoped["scope"],
                            _display_path(repo, declaration_root / scoped["scope"]),
                            revision=revision,
                        )
                    if not grouped_scope:
                        component["scope"] = scoped["scope"]
                elif root in explicit or root in idf_components or root in nested_idf_components:
                    git_root = checkout_root(root)
                    component = _component(
                        explicit.get(root) or idf_components.get(root) or nested_idf_components[root],
                        _display_path(repo, root),
                        revision=_revision(git_root) if git_root else None,
                    )
                elif entries and not identity_problems:
                    component = _build_inventory_component(repo, *entries[0], args.offline)
                else:
                    component = _component(entries[0][1]["name"] if entries else
                                           ("micropython" if root == repo else root.name),
                                           _display_path(repo, root),
                                           revision=repo_revision if root == repo else None)
                    if not matches:
                        issues.append(f"unmatched source: {source}")
                declaration_root = repo if root == repo or repo in root.parents else (checkout_root(root) or root)
                component["owner_root"] = str(root)
                component["declaration_root"] = str(declaration_root)
                if unreviewed:
                    component["unreviewed"] = True
                if identity_problems:
                    component["revision"] = None
                    component["identity_problems"] = list(identity_problems)
                groups[key] = component
                components.append(component)
            component = groups[key]
            if identity_problems:
                for problem in identity_problems:
                    if problem not in component["identity_problems"]:
                        component["identity_problems"].append(problem)
            if record["selection"] == "frozen" and (
                component["version"] != record.get("version") or
                component["manifest_license"] != record.get("license")
            ):
                raise ValueError(f"conflicting frozen package metadata: {package or root}")
            if record["selection"] == "frozen":
                selection = f"frozen:{record['kind']}:{target}"
                if package is not None:
                    selection += f":require:{package}"
            else:
                selection = "native"
            if selection not in component["selection"]:
                component["selection"].append(selection)
            path = _display_path(repo, source)
            if path not in component["sources"]:
                component["sources"].append(path)
            kind = ("frozen" if record["selection"] == "frozen" else
                    "included" if record.get("input_kind") == "depfile" else "compiled")
            evidence = {"source": path, "kind": kind}
            for field in ("originating_source", "object", "depfile"):
                if record.get(field) is not None:
                    evidence[field] = _display_path(repo, record[field])
            if evidence not in component["source_evidence"]:
                component["source_evidence"].append(evidence)
        name = args.image
    external_cache = {}
    for component in components:
        children = component.get("components", [])
        if args.compdb:
            selected = []
            for child in children:
                family = child["name"].lower()
                sources = [
                    source for source in component["sources"]
                    if any(part.lower() == family or part.lower().startswith(family + "_")
                           for part in Path(source).parts)
                ]
                if sources:
                    child["sources"] = sources
                    child["source_evidence"] = [
                        evidence for evidence in component["source_evidence"]
                        if evidence["source"] in sources
                    ]
                    child["selection"] = list(component["selection"])
                    selected.append(child)
            component["components"] = selected
        items = [component] + component.get("components", [])
        for item in items:
            declaration_state(Path(item.get("declaration_root", repo)))
        issues.extend(resolve_licences(repo, items, declaration_states=declaration_states,
                                       metadata_status=metadata_status, external_cache=external_cache))
        if component.get("identity_problems"):
            component["licence_status"] = "unresolved"
            component["licence_declared"] = None
            component["licence"] = None
            component["licence_texts"] = {}
            component["licence_details"] = {}
            component["licence_mixed"] = False
            for evidence in component["file_licences"]:
                evidence["trusted"] = False
                if evidence["provenance"] in ("source SPDX header", "source MIT header"):
                    evidence["provenance"] += " (unverified source)"
                else:
                    evidence["expression"] = None
                    evidence["provenance"] = None
            component["licence_issues"].extend(
                {"kind": "source_identity", "status": "unresolved", "message": problem}
                for problem in component["identity_problems"]
            )
            component["licence_comments"].extend(component["identity_problems"])
        for item in items:
            declaration_root = Path(item.get("declaration_root", repo))
            declaration = declaration_root / "LICENSE.spdx.yaml"
            status, digest = declaration_states[declaration_root]
            if status:
                message = (
                    f"{status} licence declaration: {_display_path(repo, declaration)} "
                    f"(sha256: {digest})"
                )
                if message not in issues:
                    issues.append(message)
    return {
        "name": name,
        "subject": {
            "repo_version": repo_version(repo),
            "repo_revision": repo_revision,
            "image": args.image,
        },
        "components": components,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate a repository or build-input SBOM.")
    parser.add_argument("--repo", default=".", help="MicroPython repository root")
    parser.add_argument("--offline", action="store_true", help="Do not query remote tags")
    parser.add_argument("--compdb", action="append", default=[], metavar="PATH",
                        help="Compilation database for one image; repeat for multiple databases")
    parser.add_argument("--root", action="append", default=[], metavar="NAME=PATH",
                        help="Explicit dependency root, relative to --repo or absolute")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATH",
                        help="Exclude commands under this source or working directory")
    parser.add_argument("--image", help="Identity of the single image described by the inputs")
    parser.add_argument("--frozen", action="append", default=[], metavar="PATH",
                        help="Resolved frozen-input JSON from makemanifest.py")
    parser.add_argument("--format", choices=("spdx", "cyclonedx"), default="spdx")
    parser.add_argument("-o", "--output", default="-", help="Output file, or - for stdout")
    args = parser.parse_args()
    if not args.compdb and (args.root or args.exclude or args.image or args.frozen):
        parser.error("--root, --exclude, --image and --frozen require --compdb")
    try:
        repo = Path(args.repo).resolve()
        document = _build_document(repo, args)
        write_sbom(document, args.output, args.format, root=repo)
    except (ValueError, OSError, ExtractorError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
