# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""
MicroPython build-time SBOM generator.

This package provides tools for generating Software Bill of Materials (SBOM)
documents from MicroPython build artifacts.
"""

from .mapfile import parse_map_file, object_to_source
from .scanner import scan_spdx_headers, try_reuse_copyright
from .license import load_license_tree
from .spdx import generate_spdx_tv, generate_spdx_json
from .util import compute_file_hash

__all__ = [
    "parse_map_file",
    "object_to_source",
    "scan_spdx_headers",
    "try_reuse_copyright",
    "load_license_tree",
    "generate_spdx_tv",
    "generate_spdx_json",
    "compute_file_hash",
]
