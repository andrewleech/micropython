"""Readers for the final ELF: DT_NEEDED and the names of the sections loaded into memory."""

from __future__ import annotations

from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile


def dynamic_needed(path: Path) -> list[str]:
    """Sonames from DT_NEEDED in file order; empty for an ELF with no dynamic section."""
    with path.open("rb") as handle:
        try:
            elf = ELFFile(handle)
        except ELFError as error:
            raise ValueError(f"not a readable ELF file: {path}: {error}") from error
        needed = []
        for section in elf.iter_sections():
            if isinstance(section, DynamicSection):
                needed.extend(tag.needed for tag in section.iter_tags("DT_NEEDED"))
        return needed


def loaded_sections(path: Path) -> set[str]:
    """Names of the output sections the ELF allocates (SHF_ALLOC), which are the bytes of the image."""
    with path.open("rb") as handle:
        try:
            elf = ELFFile(handle)
        except ELFError as error:
            raise ValueError(f"not a readable ELF file: {path}: {error}") from error
        return {section.name for section in elf.iter_sections() if section["sh_flags"] & 0x2}
