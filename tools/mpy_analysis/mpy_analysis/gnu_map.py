"""Parse GNU ld link-map evidence without inferring identities from basenames."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

_ADDRESS = r"(?:0x)?[0-9a-fA-F]+"
_ARCHIVE_ID = re.compile(r"^(?P<archive>.+)\((?P<member>[^()]*)\)$")
_SECTION_ROW = re.compile(
    rf"^\s+(?P<section>\S+)\s+(?P<address>{_ADDRESS})\s+(?P<size>{_ADDRESS})\s+(?P<identity>.+?)\s*$"
)
# An output section header starts in column one: name, then optionally address, size and load address.
_OUTPUT_SECTION = re.compile(
    rf"^(?P<name>[^\s=]+)(?:\s+{_ADDRESS}\s+{_ADDRESS})?(?:\s+load address\s+{_ADDRESS})?\s*$"
)
_EXTRACTED_HEADER = "Archive member included to satisfy reference by file (symbol)"


def _path_fields(raw: str, base_dir: Path) -> dict[str, str]:
    """Keep the map spelling and add a normalized absolute path for correlation."""
    path = Path(raw)
    return {"raw": raw, "resolved": str((path if path.is_absolute() else base_dir / path).resolve(strict=False))}


def _single_operand(value: str, line_number: int) -> str:
    try:
        parts = shlex.split(value, posix=True)
    except ValueError as error:
        raise ValueError(f"malformed GNU ld map LOAD operand on line {line_number}: {error}") from error
    if len(parts) != 1 or not parts[0]:
        raise ValueError(f"ambiguous GNU ld map LOAD operand on line {line_number}")
    return parts[0]


def _identity(raw: str, line_number: int) -> dict[str, str]:
    """Decode archive(member) only when both components are unambiguously present."""
    if "\"" in raw or "'" in raw:
        try:
            words = shlex.split(raw, posix=True)
        except ValueError as error:
            raise ValueError(f"malformed input identity on line {line_number}: {error}") from error
        if len(words) != 1:
            raise ValueError(f"ambiguous input identity on line {line_number}: {raw!r}")
        raw = words[0]
    match = _ARCHIVE_ID.fullmatch(raw)
    if match:
        archive, member = match.group("archive"), match.group("member")
        if not archive or not member:
            raise ValueError(f"malformed archive member identity on line {line_number}: {raw!r}")
        return {"kind": "archive_member", "path": archive, "member": member, "raw": raw}
    if "(" in raw or ")" in raw:
        raise ValueError(f"ambiguous input identity on line {line_number}: {raw!r}")
    return {"kind": "object", "path": raw, "raw": raw}


def parse_gnu_map(text: str, *, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Return exact LOAD, archive-selection, discarded, and allocated map evidence.

    Only GNU ld map sections with recognized markers and rows are accepted. Paths are
    retained as written; ``resolved`` fields are convenience absolute paths and do not
    replace raw identity keys.
    """
    if not isinstance(text, str):
        raise TypeError("GNU ld map text must be a string")
    root = Path(base_dir if base_dir is not None else os.getcwd()).resolve(strict=False)
    lines = text.splitlines()
    loads: list[dict[str, str]] = []
    extracted: dict[tuple[str, str], dict[str, Any]] = {}
    discarded: list[dict[str, Any]] = []
    allocated: dict[tuple[str, ...], dict[str, Any]] = {}
    ambiguities: list[dict[str, Any]] = []
    saw_extracted = False
    saw_discarded = False
    saw_allocated = False
    mode: str | None = None
    recognized = False
    pending_archive: tuple[str, str] | None = None
    pending_section: str | None = None
    output_section: str | None = None

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^(?:LLD\b|LLVM linker\b)", stripped, re.IGNORECASE):
            raise ValueError("unsupported linker map dialect: LLD")
        if stripped == "Memory Configuration":
            mode = None
            pending_section = None
            continue
        if not stripped:
            continue
        if stripped == _EXTRACTED_HEADER:
            saw_extracted = recognized = True
            mode = "extracted"
            continue
        if stripped == "Discarded input sections":
            saw_discarded = recognized = True
            mode = "discarded"
            pending_section = None
            continue
        if stripped == "Merging program properties":
            mode = None
            pending_archive = None
            pending_section = None
            recognized = True
            continue
        if stripped == "As-needed library included to satisfy reference by file (symbol)":
            # Shared libraries are recorded from the final ELF's DT_NEEDED list, not the map.
            mode = "as-needed"
            recognized = True
            continue
        if mode == "as-needed" and not stripped.startswith(("LOAD ", "Linker script and memory map")):
            continue
        if stripped.startswith("Linker script and memory map"):
            mode = "allocated"
            recognized = True
            continue
        if stripped.startswith("LOAD "):
            operand_text = stripped[5:]
            if operand_text == "linker stubs":
                loads.append({"raw": operand_text, "kind": "linker-stubs"})
            else:
                value = _single_operand(operand_text, number)
                loads.append({"kind": "path", **_path_fields(value, root)})
            recognized = True
            continue
        if mode == "extracted":
            combined = re.match(r"^\s*(.+\([^()]+\))\s{2,}(.+?)\s+\((.*)\)\s*$", line)
            if combined:
                identity = _identity(combined.group(1).strip(), number)
                if identity["kind"] != "archive_member":
                    raise ValueError(f"expected archive member on line {number}: {line!r}")
                key = (identity["path"], identity["member"])
                if key in extracted:
                    ambiguities.append(
                        {
                            "kind": "duplicate_extraction",
                            "archive_path": key[0],
                            "member": key[1],
                            "line": number,
                        }
                    )
                record = extracted.setdefault(
                    key,
                    {
                        "archive_path": key[0],
                        "archive": _path_fields(key[0], root),
                        "member": key[1],
                        "references": [],
                    },
                )
                record["references"].append(
                    {"file": combined.group(2).strip(), "symbol": combined.group(3)}
                )
                pending_archive = None
                continue
            if line[:1].isspace():
                if pending_archive is None:
                    raise ValueError(f"orphan archive extraction reference on line {number}")
                reference = re.match(r"^\s{2,}(.+?)\s+\((.*)\)\s*$", line)
                if not reference:
                    raise ValueError(f"malformed archive extraction reference on line {number}: {line!r}")
                extracted[pending_archive]["references"].append(
                    {"file": reference.group(1).strip(), "symbol": reference.group(2)}
                )
                continue
            identity = _identity(stripped, number)
            if identity["kind"] != "archive_member":
                raise ValueError(f"expected archive member on line {number}: {line!r}")
            key = (identity["path"], identity["member"])
            if key in extracted:
                ambiguities.append(
                    {
                        "kind": "duplicate_extraction",
                        "archive_path": key[0],
                        "member": key[1],
                        "line": number,
                    }
                )
            extracted.setdefault(
                key,
                {
                    "archive_path": key[0],
                    "archive": _path_fields(key[0], root),
                    "member": key[1],
                    "references": [],
                },
            )
            pending_archive = key
            continue
        if mode == "allocated" and line[:1] not in (" ", "\t"):
            header = _OUTPUT_SECTION.match(line)
            if header:
                output_section = header.group("name")
                pending_section = None
                continue
        if mode == "discarded" or mode == "allocated":
            row = _SECTION_ROW.match(line)
            section = row.group("section") if row else None
            if row:
                address, size, identity_text = (
                    row.group("address"),
                    row.group("size"),
                    row.group("identity"),
                )
            else:
                wrapped = re.match(
                    rf"^\s+(?P<address>{_ADDRESS})\s+(?P<size>{_ADDRESS})\s+(?P<identity>.+?)\s*$",
                    line,
                )
                section_only = re.match(r"^\s+(?P<section>\S+)\s*$", line)
                if wrapped and pending_section is not None:
                    section = pending_section
                    address, size, identity_text = (
                        wrapped.group("address"),
                        wrapped.group("size"),
                        wrapped.group("identity"),
                    )
                    pending_section = None
                elif section_only and not section_only.group("section").startswith("*"):
                    pending_section = section_only.group("section")
                    continue
                else:
                    if mode == "discarded":
                        raise ValueError(
                            f"malformed discarded-section record on line {number}: {line!r}"
                        )
                    if line[:1].isspace() and re.match(
                        rf"^\s+\S+\s+{_ADDRESS}\s+{_ADDRESS}\s+\S", line
                    ):
                        raise ValueError(
                            f"malformed allocated-section record on line {number}: {line!r}"
                        )
                    continue
            identity = _identity(identity_text.strip(), number)
            record = {
                "section": section,
                "output_section": output_section if mode == "allocated" else None,
                "address": int(address, 16),
                "size": int(size, 16),
                "identity": identity,
                "line": number,
            }
            if mode == "discarded":
                discarded.append(record)
            else:
                saw_allocated = True
                key = (identity["kind"], identity["path"], identity.get("member", ""))
                group = allocated.setdefault(key, {"identity": identity, "sections": []})
                group["sections"].append(record)
            recognized = True
            continue
        if stripped.startswith("GNU ld") or stripped.startswith("OUTPUT(") or stripped.startswith("START GROUP") or stripped.startswith("END GROUP"):
            recognized = True
            continue
    if pending_section is not None:
        raise ValueError(f"section {pending_section!r} has no address, size and origin")

    if not recognized or not (saw_extracted or saw_discarded or saw_allocated):
        raise ValueError("unrecognized or unsupported linker map format (expected GNU ld map sections)")
    return {
        "format": "gnu-ld-map",
        "loads": loads,
        "extracted_archive_members": list(extracted.values()),
        "discarded_sections": discarded,
        "allocated_sections": list(allocated.values()),
        "ambiguities": ambiguities,
    }
