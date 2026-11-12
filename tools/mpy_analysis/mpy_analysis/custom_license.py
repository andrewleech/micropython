"""Licences the root ``LICENSE`` names that are not SPDX identifiers.

``LICENSE`` lists some carried files under a licence that has no SPDX identifier (for
example the "MCD-ST Liberty SW License Agreement V2" on the STM32 USB sources). Such a
licence is reported as ``LicenseRef-<name>``, with the identifier made from the name in
``LICENSE``. The licence text itself is not in the tree; the text retained for it is the
notice the covered files carry in their headers, which is the part that grants the licence.
"""

from __future__ import annotations

import re
from pathlib import Path

_END = "limitations under the License"


def reference(name: str) -> str:
    """The ``LicenseRef-`` identifier for a licence named in ``LICENSE``."""
    return "LicenseRef-" + re.sub(r"[^A-Za-z0-9.]+", "-", name).strip("-")


def _notice(text: str, name: str) -> str | None:
    """The header comment lines from ``Licensed under <name>`` to the end of the notice."""
    lines = [
        re.sub(r"^[ \t]*(?:/\*+|\*+/?)?[ \t]?", "", line).rstrip() for line in text.splitlines()
    ]
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"Licensed under {name}")), None
    )
    if start is None:
        return None
    for end in range(start, len(lines)):
        if _END in lines[end]:
            return "\n".join(lines[start : end + 1])
    raise ValueError(f"the notice that starts '{lines[start]}' has no end")


def covered_files(root: Path, pattern: str) -> list[Path]:
    """Files a ``LICENSE`` tree entry covers: a glob of files or everything under a directory."""
    matched = sorted(root.glob(pattern))
    return sorted(
        file
        for path in matched
        for file in ([path] if path.is_file() else path.rglob("*"))
        if file.is_file()
    )


def retained_text(root: Path, pattern: str, name: str) -> tuple[str, Path]:
    """The licence notice the files covered by ``pattern`` carry, and one file that has it.

    Every covered file that names the licence must carry the same notice, so the retained
    text is not an arbitrary pick between variants.
    """
    found: dict[str, Path] = {}
    for path in covered_files(root, pattern):
        notice = _notice(path.read_text(errors="replace"), name)
        if notice is not None:
            found.setdefault(notice, path)
    if not found:
        raise ValueError(f"no file covered by {pattern} carries a '{name}' notice")
    if len(found) > 1:
        raise ValueError(
            f"files covered by {pattern} carry {len(found)} different '{name}' notices"
        )
    ((text, path),) = found.items()
    return text, path
