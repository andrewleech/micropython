"""Identify linked system files through the installed dpkg database.

Only the on-disk dpkg format is parsed (``status``, ``info/*.list``, ``info/*.md5sums``,
``/etc/os-release``); there is no package-name table. A file is identified only when the
database lists it, records a checksum for it, and the checksum matches the file on disk.
Other package managers are reported as unsupported rather than guessed.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

DPKG_ADMIN_DIR = Path("/var/lib/dpkg")
OS_RELEASE = Path("/etc/os-release")


@lru_cache(maxsize=None)
def _resolved_directory(directory: str) -> str:
    return str(Path(directory).resolve(strict=False))


def _normal(path: str | Path) -> str:
    candidate = Path(path)
    return str(Path(_resolved_directory(str(candidate.parent))) / candidate.name)


def _stanzas(text: str) -> list[dict[str, str]]:
    result = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        key = None
        for line in block.splitlines():
            if line.startswith((" ", "\t")):
                continue
            name, separator, value = line.partition(":")
            if separator:
                key = name
                fields[key] = value.strip()
        if "Package" in fields:
            result.append(fields)
    return result


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _database(admin_dir: str) -> dict[str, Any]:
    admin = Path(admin_dir)
    status_file = admin / "status"
    info = admin / "info"
    if not status_file.is_file() or not info.is_dir():
        return {"supported": False, "files": {}, "packages": {}}
    packages = {}
    for stanza in _stanzas(status_file.read_text(encoding="utf-8", errors="replace")):
        if stanza.get("Status", "").split()[-1:] == ["installed"]:
            packages[(stanza["Package"], stanza.get("Architecture", ""))] = stanza
    files: dict[str, tuple[str, str, str]] = {}
    for list_file in sorted(info.glob("*.list")):
        stem = list_file.name[: -len(".list")]
        name, _, architecture = stem.partition(":")
        key = (name, architecture)
        if key not in packages:
            candidates = [item for item in packages if item[0] == name]
            if len(candidates) != 1:
                continue
            key = candidates[0]
        for line in list_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and line != "/.":
                files.setdefault(_normal(line), (key[0], key[1], stem))
                files.setdefault(line, (key[0], key[1], stem))
    return {"supported": True, "files": files, "packages": packages, "info": str(info)}


def _distro() -> dict[str, str]:
    values: dict[str, str] = {}
    if OS_RELEASE.is_file():
        for line in OS_RELEASE.read_text(encoding="utf-8", errors="replace").splitlines():
            name, separator, value = line.partition("=")
            if separator and name in ("ID", "VERSION_ID"):
                values[name] = value.strip().strip("\"'")
    return {"id": values.get("ID", ""), "version_id": values.get("VERSION_ID", "")}


def identify_system_file(
    path: str | Path, *, admin_dir: Path | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (package record, None) or (None, reason) for one linked system file."""
    admin_dir = DPKG_ADMIN_DIR if admin_dir is None else admin_dir
    database = _database(str(admin_dir))
    if not database["supported"]:
        return None, f"no supported package database at {admin_dir}"
    raw = Path(path)
    resolved = raw.resolve(strict=False)
    owner = None
    for candidate in (str(resolved), _normal(resolved), str(raw), _normal(raw)):
        owner = database["files"].get(candidate)
        if owner:
            listed = candidate
            break
    if owner is None:
        return None, "installed package database lists no owner for this file"
    name, architecture, stem = owner
    checksums = Path(database["info"]) / f"{stem}.md5sums"
    expected = None
    if checksums.is_file():
        for line in checksums.read_text(encoding="utf-8", errors="replace").splitlines():
            digest, _, relative = line.partition("  ")
            if relative and _normal("/" + relative) in (_normal(resolved), listed):
                expected = digest
                break
    if expected is None:
        return None, f"package {name} records no checksum for this file"
    if not resolved.is_file():
        return None, "linked system file is not a regular file"
    if _md5(resolved) != expected:
        return None, f"file does not match the checksum recorded by package {name}"
    stanza = database["packages"][(name, architecture)]
    return {
        "manager": "dpkg",
        "name": name,
        "version": stanza["Version"],
        "architecture": stanza.get("Architecture", ""),
        "maintainer": stanza.get("Maintainer", ""),
        "md5sum": expected,
        "distro": _distro(),
    }, None
