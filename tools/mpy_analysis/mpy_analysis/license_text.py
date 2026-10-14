"""Identify a licence file by comparing its text with the canonical SPDX text.

Only an exact match after normalisation counts: case, whitespace, punctuation,
text before the licence body and (for Apache-2.0) the appendix are ignored,
anything else is not identified. Licences whose text carries variable parts
(BSD clauses naming a holder) and modified or combined texts therefore stay
unidentified instead of being guessed.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "licenses"
_START = {"MIT": "permission is hereby granted"}
_END = "end of terms and conditions"


def _normal(identifier: str, text: str) -> str:
    text = text.lower()
    start = _START.get(identifier)
    if start and start in text:
        text = text[text.index(start) :]
    if _END in text:
        text = text[: text.index(_END) + len(_END)]
    return re.sub(r"[^a-z0-9]", "", text)


@cache
def _corpus() -> dict[str, str]:
    return {
        path.stem: _normal(path.stem, path.read_text(encoding="utf-8"))
        for path in sorted(_DATA.glob("*.txt"))
    }


def identify(text: str) -> str | None:
    """The SPDX identifier whose canonical text ``text`` reproduces exactly, if any."""
    for identifier, canonical in _corpus().items():
        if _normal(identifier, text) == canonical:
            return identifier
    return None
