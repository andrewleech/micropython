"""Helpers that write compiledb-shaped entries and the dependency files GCC writes with them."""

import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def write(root, rel, text=""):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def fake_elf(label: bytes = b"", sections=()) -> bytes:
    """A minimal valid ELF64 file; trailing label bytes make the hash distinct.

    sections is a sequence of (name, address, size) allocated sections for the section header table.
    """
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    if not sections:
        return ident + struct.pack("<HHIQQQIHHHHHH", 2, 62, 1, 0, 0, 0, 0, 64, 0, 0, 64, 0, 0) + label
    names = b"\0" + b"".join(name.encode() + b"\0" for name, _, _ in sections) + b".shstrtab\0"
    strtab_offset = 64 + len(label)
    table_offset = strtab_offset + len(names)
    headers = bytes(64)
    position = 1
    for name, address, size in sections:
        headers += struct.pack("<IIQQQQIIQQ", position, 1, 2, address, 0, size, 0, 0, 1, 0)
        position += len(name) + 1
    headers += struct.pack("<IIQQQQIIQQ", position, 3, 0, 0, strtab_offset, len(names), 0, 0, 1, 0)
    count = len(sections) + 2
    header = struct.pack(
        "<HHIQQQIHHHHHH", 2, 62, 1, 0, 0, table_offset, 0, 64, 0, 0, 64, count, count - 1
    )
    return ident + header + label + names + headers


def entry(root, source, deps, directory=None, obj=None, write_dep=True):
    """A compiledb-shaped entry for source, with its dependency file written as GCC writes it."""
    directory = Path(directory or root)
    stem = os.path.relpath(Path(directory, source), root)
    stem = os.path.splitext(stem)[0].replace(os.sep, "_")
    obj = obj or f"{root}/build/{stem}.o"
    dep = os.path.splitext(obj)[0] + ".d"
    if write_dep:
        write(
            directory,
            dep,
            f"{obj}: {source} " + " ".join(str(Path(root) / d) for d in deps) + "\n",
        )
    return {
        "directory": str(directory),
        "file": str(source),
        "arguments": ["gcc", "-c", "-MD", "-MF", dep, "-o", obj, str(source)],
    }


def write_db(path, entries):
    Path(path).write_text(json.dumps(entries, indent=1))
    return path


def generate_cyclonedx(*args, **kwargs):
    """Run sbom.generate_sbom and render its model as a CycloneDX document: (document, receipt)."""
    from mpy_analysis import sbom
    from mpy_analysis.sbom_cyclonedx import to_cyclonedx

    model, receipt = sbom.generate_sbom(*args, **kwargs)
    return to_cyclonedx(model), receipt
