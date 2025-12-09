# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""License tree parsing from LICENSE file."""

import re
from pathlib import Path


def load_license_tree(license_file):
    """
    Parse the LICENSE file to extract the license tree for third-party code.

    The MicroPython LICENSE file contains a tree structure like:
        / (MIT)
            /lib
                /asf4 (Apache-2.0)
                /lwip (BSD-3-clause)

    Args:
        license_file: Path to the LICENSE file

    Returns:
        Dict mapping path prefixes to SPDX license identifiers
    """
    license_map = {}

    # Pattern matches indented paths with license in parentheses
    # e.g., "        /lib/asf4 (Apache-2.0)"
    path_license_pattern = re.compile(r"^\s+(/\S+)\s+\(([^)]+)\)")

    # Also match root license
    root_pattern = re.compile(r"^/\s+\(([^)]+)\)")

    try:
        with open(license_file, "r", encoding="utf-8") as f:
            for line in f:
                # Check for root license
                match = root_pattern.match(line)
                if match:
                    license_map["/"] = normalize_license_id(match.group(1))
                    continue

                # Check for path-specific license
                match = path_license_pattern.search(line)
                if match:
                    path, license_id = match.groups()
                    license_map[path] = normalize_license_id(license_id)

    except (IOError, OSError):
        pass

    return license_map


def normalize_license_id(license_id):
    """
    Normalize a license identifier to SPDX format.

    Handles common variations:
    - "BSD-3-clause" -> "BSD-3-Clause"
    - "Apache 2.0" -> "Apache-2.0"
    - "MIT" -> "MIT"

    Args:
        license_id: Raw license identifier string

    Returns:
        Normalized SPDX license identifier
    """
    # Common mappings
    mappings = {
        "bsd-3-clause": "BSD-3-Clause",
        "bsd-2-clause": "BSD-2-Clause",
        "bsd-1-clause": "BSD-1-Clause",
        "bsd-4-clause": "BSD-4-Clause",
        "apache-2.0": "Apache-2.0",
        "apache 2.0": "Apache-2.0",
        "apache2": "Apache-2.0",
        "gpl-2.0": "GPL-2.0-only",
        "gpl-2.0-only": "GPL-2.0-only",
        "gpl-2.0-or-later": "GPL-2.0-or-later",
        "gpl-3.0": "GPL-3.0-only",
        "lgpl-3.0": "LGPL-3.0-only",
        "lgpl-3.0-only": "LGPL-3.0-only",
        "zlib": "Zlib",
        "isc": "ISC",
        "ofl-1.1": "OFL-1.1",
    }

    normalized = mappings.get(license_id.lower(), license_id)

    # If not in mappings, try to fix common case issues
    if normalized == license_id:
        # Handle "BSD-3-clause" -> "BSD-3-Clause"
        if license_id.lower().startswith("bsd-"):
            parts = license_id.split("-")
            if len(parts) >= 3:
                normalized = f"{parts[0].upper()}-{parts[1]}-{parts[2].capitalize()}"

    return normalized


def lookup_license_for_path(file_path, license_tree, project_root=None):
    """
    Look up the license for a file path using the license tree.

    Finds the most specific matching path prefix.

    Args:
        file_path: Path to the file (relative or absolute)
        license_tree: Dict from load_license_tree()
        project_root: Optional project root for making paths relative

    Returns:
        SPDX license identifier or None if not found
    """
    if project_root:
        try:
            file_path = Path(file_path).relative_to(project_root)
        except ValueError:
            pass

    file_path_str = "/" + str(file_path).replace("\\", "/")

    # Find the most specific (longest) matching prefix
    best_match = None
    best_length = 0

    for path_prefix, license_id in license_tree.items():
        if file_path_str.startswith(path_prefix):
            if len(path_prefix) > best_length:
                best_match = license_id
                best_length = len(path_prefix)

    return best_match
