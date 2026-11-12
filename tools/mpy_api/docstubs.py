# SPDX-License-Identifier: MIT
"""Locate the pinned, offline MicroPython documentation reference bundle.

The bundle contains Jos Verlinde's published documentation stubs and curated
references. Its manifest records their independent versions and licences; it
makes no claim that the reference describes the selected firmware exactly.
"""

from pathlib import Path


def default_enrichment_path() -> Path:
    """Return the packaged schema-v1 manifest without fetching or generating files."""
    return Path(__file__).resolve().parent / "vendor" / "micropython_docstubs" / "enrichment.json"
