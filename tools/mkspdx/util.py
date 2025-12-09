# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""Utility functions for SBOM generation."""

import hashlib
from pathlib import Path


def compute_file_hash(file_path, algorithm="sha256"):
    """
    Compute hash of a file.

    Args:
        file_path: Path to the file
        algorithm: Hash algorithm name (default: sha256)

    Returns:
        Hex digest of the file hash
    """
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_file_hashes(file_path):
    """
    Compute multiple hashes of a file (SHA256 and SHA1).

    Args:
        file_path: Path to the file

    Returns:
        Dict with 'sha256' and 'sha1' keys
    """
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
            sha1.update(chunk)

    return {"sha256": sha256.hexdigest(), "sha1": sha1.hexdigest()}


def normalize_path(path, base_dir=None):
    """
    Normalize a file path for consistent representation.

    Args:
        path: Path to normalize
        base_dir: Optional base directory to make path relative to

    Returns:
        Normalized path string with forward slashes
    """
    path = Path(path)

    if base_dir:
        try:
            path = path.relative_to(base_dir)
        except ValueError:
            pass

    return str(path).replace("\\", "/")


def is_source_file(path):
    """
    Check if a path is a source file we should include in SBOM.

    Args:
        path: File path to check

    Returns:
        True if this is a source file
    """
    source_extensions = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".S", ".s", ".asm"}
    return Path(path).suffix.lower() in source_extensions
