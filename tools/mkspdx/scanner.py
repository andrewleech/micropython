# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""SPDX header scanning from source files."""

import re
from pathlib import Path

# SPDX license identifier pattern - matches various comment styles
SPDX_LICENSE_PATTERN = re.compile(
    r"SPDX-License-Identifier:\s*([^\s*/]+(?:\s+(?:AND|OR|WITH)\s+[^\s*/]+)*)",
    re.IGNORECASE,
)

# SPDX copyright pattern
SPDX_COPYRIGHT_PATTERN = re.compile(
    r"SPDX-FileCopyrightText:\s*(.+?)(?:\s*\*/|\s*-->|\s*$)", re.IGNORECASE
)

# Traditional copyright patterns (fallback)
TRADITIONAL_COPYRIGHT_PATTERNS = [
    re.compile(
        r"Copyright\s*(?:\(c\)|©)?\s*(\d{4}(?:\s*-\s*\d{4})?)\s+(.+?)(?:\s*\*/|\s*$)",
        re.IGNORECASE,
    ),
    re.compile(r"Copyright\s+(.+?)(?:\s*\*/|\s*$)", re.IGNORECASE),
]

# Default number of lines to scan for SPDX headers
DEFAULT_SCAN_LINES = 20


def scan_spdx_headers(file_path, max_lines=DEFAULT_SCAN_LINES):
    """
    Scan a source file for SPDX license identifier and copyright headers.

    Looks for:
    - SPDX-License-Identifier: <expression>
    - SPDX-FileCopyrightText: <text>

    Args:
        file_path: Path to the source file
        max_lines: Maximum number of lines to scan (0 = unlimited)

    Returns:
        Dict with 'license' (str or None) and 'copyrights' (list of str)
    """
    result = {"license": None, "copyrights": []}

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines_to_scan = max_lines if max_lines > 0 else None
            for i, line in enumerate(f):
                if lines_to_scan and i >= lines_to_scan:
                    break

                # Look for license identifier
                match = SPDX_LICENSE_PATTERN.search(line)
                if match and not result["license"]:
                    result["license"] = match.group(1).strip()

                # Look for SPDX copyright
                match = SPDX_COPYRIGHT_PATTERN.search(line)
                if match:
                    copyright_text = match.group(1).strip()
                    if copyright_text not in result["copyrights"]:
                        result["copyrights"].append(copyright_text)

    except (IOError, OSError):
        pass

    return result


def scan_traditional_copyright(file_path, max_lines=DEFAULT_SCAN_LINES):
    """
    Scan a source file for traditional copyright notices.

    This is a fallback for files that don't have SPDX headers.

    Args:
        file_path: Path to the source file
        max_lines: Maximum number of lines to scan (0 = unlimited)

    Returns:
        List of copyright strings found
    """
    copyrights = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines_to_scan = max_lines if max_lines > 0 else None
            for i, line in enumerate(f):
                if lines_to_scan and i >= lines_to_scan:
                    break

                for pattern in TRADITIONAL_COPYRIGHT_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        copyright_text = match.group(0).strip()
                        # Clean up comment artifacts
                        copyright_text = re.sub(r"\s*\*/\s*$", "", copyright_text)
                        copyright_text = re.sub(r"^\s*\*\s*", "", copyright_text)
                        if copyright_text and copyright_text not in copyrights:
                            copyrights.append(copyright_text)

    except (IOError, OSError):
        pass

    return copyrights


def try_reuse_copyright(file_path, project_root):
    """
    Try to extract copyright information using the reuse library.

    Falls back gracefully if reuse is not available.

    Args:
        file_path: Path to the source file
        project_root: Root of the project for reuse context

    Returns:
        List of copyright strings, empty list if reuse unavailable or fails
    """
    try:
        from reuse.project import Project

        project = Project(Path(project_root))
        info = project.reuse_info_of(Path(file_path))
        if info and info.copyright_lines:
            return list(info.copyright_lines)
    except ImportError:
        pass
    except Exception:
        # reuse can raise various exceptions for edge cases
        pass
    return []


def validate_spdx_expression(expression):
    """
    Validate that an SPDX license expression is well-formed.

    This is a basic validation - it checks for known license IDs
    and valid operators but doesn't do full parsing.

    Args:
        expression: SPDX license expression string

    Returns:
        True if the expression appears valid, False otherwise
    """
    if not expression:
        return False

    # Tokenize - split on whitespace but also treat parens as separate tokens
    # First add spaces around parentheses
    expr = expression.replace("(", " ( ").replace(")", " ) ")
    tokens = [t for t in expr.split() if t]

    operators = {"AND", "OR", "WITH"}

    expect_license = True
    paren_depth = 0

    for token in tokens:
        if token == "(":
            if not expect_license:
                return False
            paren_depth += 1
        elif token == ")":
            if expect_license:
                return False
            paren_depth -= 1
            if paren_depth < 0:
                return False
        elif token in operators:
            if expect_license:
                return False
            expect_license = True
        else:
            # Should be a license ID
            if not expect_license:
                return False
            # Basic license ID format check
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", token):
                return False
            expect_license = False

    return paren_depth == 0 and not expect_license
