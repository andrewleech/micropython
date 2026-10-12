"""Expand captured tool command lines into the tokens the tool actually receives."""

from __future__ import annotations

import shlex
from pathlib import Path


def resolve_arg(value: str, cwd: Path) -> Path | None:
    if not value or value.startswith("-") or value.startswith("="):
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else cwd / path
    return candidate if candidate.is_file() else None


def flatten(argv: list[str], cwd: Path, depth: int = 0) -> list[str]:
    """Return linker-level tokens: -Wl,/-Xlinker values split out, @response files expanded.

    A response file is kept as an '@path' token so the file itself is still recorded as an
    input, followed by the tokens it contains.
    """
    tokens: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        index += 1
        if arg == "-Xlinker" and index < len(argv):
            tokens.append(argv[index])
            index += 1
        elif arg.startswith("-Wl,"):
            tokens.extend(field for field in arg[4:].split(",") if field)
        elif arg.startswith("@") and depth < 8 and (response := resolve_arg(arg[1:], cwd)) is not None:
            tokens.append(arg)
            try:
                tokens.extend(flatten(shlex.split(response.read_text(encoding="utf-8")), cwd, depth + 1))
            except (OSError, UnicodeError, ValueError):
                pass
        else:
            tokens.append(arg)
    return [token[1:] if token == "--Map" or token.startswith("--Map=") else token for token in tokens]


def enables_lto(argv: list[str], cwd: Path) -> bool:
    return any(token == "-flto" or token.startswith("-flto=") for token in flatten(argv, cwd))
