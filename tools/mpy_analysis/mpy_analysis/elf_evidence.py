"""Structural readers for the final ELF: dynamic requirements and the section header table."""

from __future__ import annotations

from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

_SHF_ALLOC = 0x2


def _open(handle, path: Path) -> ELFFile:
    try:
        return ELFFile(handle)
    except ELFError as error:
        raise ValueError(f"not a readable ELF file: {path}: {error}") from error


def dynamic_needed(path: Path) -> list[str]:
    """Sonames from DT_NEEDED in file order; empty for an ELF with no dynamic section."""
    with path.open("rb") as handle:
        needed = []
        for section in _open(handle, path).iter_sections():
            if isinstance(section, DynamicSection):
                needed.extend(tag.needed for tag in section.iter_tags("DT_NEEDED"))
        return needed


def section_table(path: Path) -> dict[str, dict[str, int | bool]]:
    """Section header table by name: start, end and allocated flag."""
    with path.open("rb") as handle:
        table = {}
        for section in _open(handle, path).iter_sections():
            if section.name in table:
                raise ValueError(f"ELF repeats section name {section.name}: {path}")
            table[section.name] = {
                "start": section["sh_addr"],
                "end": section["sh_addr"] + section["sh_size"],
                "allocated": bool(section["sh_flags"] & _SHF_ALLOC),
            }
        return table
