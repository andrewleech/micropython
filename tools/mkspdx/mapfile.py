# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""GNU linker map file parsing."""

import os
import re
from pathlib import Path


def parse_map_file(map_path):
    """
    Parse a GNU linker map file to extract the list of object files
    that were linked into the final binary.

    The map file contains several sections:
    - "Archive member included" - lists archive.a(member.o) entries
    - "Linker script and memory map" - shows memory layout with object files
    - "Discarded input sections" - unused code (excluded from output)

    Args:
        map_path: Path to the linker map file

    Returns:
        List of dicts with 'archive' (optional) and 'object' keys
    """
    object_files = []
    seen_objects = set()

    # Pattern for archive members: libfoo.a(bar.o)
    archive_pattern = re.compile(r"([^\s]+\.a)\(([^\)]+\.o)\)")
    # Pattern for standalone object files
    object_pattern = re.compile(r"^\s*(\S+\.o)\s*$")
    # Pattern for object files in memory map section (with address)
    memmap_object_pattern = re.compile(r"^\s+\S+\s+\S+\s+\S+\s+(\S+\.o)")

    in_archive_section = False
    in_memory_map = False

    with open(map_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            # Track which section we're in
            if "Archive member included" in line:
                in_archive_section = True
                in_memory_map = False
                continue
            if "Linker script and memory map" in line:
                in_memory_map = True
                in_archive_section = False
                continue
            if "Discarded input sections" in line:
                in_memory_map = False
                in_archive_section = False
                continue
            if line.startswith("OUTPUT("):
                in_memory_map = False
                continue

            # Extract archive members
            if in_archive_section:
                match = archive_pattern.search(line)
                if match:
                    archive, member = match.groups()
                    key = f"{archive}:{member}"
                    if key not in seen_objects:
                        seen_objects.add(key)
                        object_files.append({"archive": archive, "object": member})

            # Extract object files from memory map
            if in_memory_map:
                # Try memory map format first
                match = memmap_object_pattern.search(line)
                if not match:
                    match = object_pattern.match(line)
                if match:
                    obj = match.group(1)
                    if obj not in seen_objects and not obj.startswith("0x"):
                        seen_objects.add(obj)
                        object_files.append({"object": obj})

    return object_files


def object_to_source(obj_path, build_dir, source_dirs):
    """
    Map an object file path back to its source file.

    Object files in the build directory follow the source tree structure,
    so build/py/map.o corresponds to py/map.c

    Args:
        obj_path: Path to the object file (may be relative or absolute)
        build_dir: Path to the build directory
        source_dirs: List of directories to search for source files

    Returns:
        Path to source file if found, None otherwise
    """
    build_dir = Path(build_dir)
    obj_path = Path(obj_path)

    # Remove build directory prefix if present
    try:
        if obj_path.is_absolute():
            rel_path = obj_path.relative_to(build_dir)
        elif str(obj_path).startswith(str(build_dir)):
            rel_path = obj_path.relative_to(build_dir)
        else:
            rel_path = obj_path
    except ValueError:
        rel_path = obj_path

    # Try common source extensions
    base = rel_path.with_suffix("")
    extensions = [".c", ".cpp", ".cc", ".S", ".s"]

    for source_dir in source_dirs:
        source_dir = Path(source_dir)
        for ext in extensions:
            candidate = source_dir / (str(base) + ext)
            if candidate.exists():
                return str(candidate)

            # Also try without leading path components (for archive members)
            candidate = source_dir / (base.name + ext)
            if candidate.exists():
                return str(candidate)

    return None


def find_source_in_tree(obj_name, source_root, lib_dirs=None):
    """
    Search for a source file in the source tree by object name.

    This is a fallback for when the simple path mapping doesn't work,
    particularly for third-party library code.

    Args:
        obj_name: Object file name (e.g., "gc.o")
        source_root: Root of source tree to search
        lib_dirs: Additional library directories to search

    Returns:
        Path to source file if found, None otherwise
    """
    source_root = Path(source_root)
    base_name = Path(obj_name).stem
    extensions = [".c", ".cpp", ".cc", ".S", ".s"]

    search_dirs = [source_root]
    if lib_dirs:
        search_dirs.extend(Path(d) for d in lib_dirs)

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for ext in extensions:
            # Use glob to find the file anywhere in the tree
            matches = list(search_dir.glob(f"**/{base_name}{ext}"))
            if len(matches) == 1:
                return str(matches[0])
            elif len(matches) > 1:
                # Multiple matches - try to pick the most likely one
                # Prefer files not in test directories
                for match in matches:
                    if "test" not in str(match).lower():
                        return str(match)
                return str(matches[0])

    return None
