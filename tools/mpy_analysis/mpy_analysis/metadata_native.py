"""Bounded literal CMake/Meson/PlatformIO project declarations; no build-language evaluation."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from jsonschema import Draft7Validator


def _cmake_tokens(text: str) -> list[tuple[str, bool]]:
    tokens = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
        elif text[index] == "#":
            if text.startswith("#[", index):
                raise ValueError("CMake bracket comments are outside the static grammar")
            end = text.find("\n", index)
            index = len(text) if end == -1 else end + 1
        elif text[index] in "()":
            tokens.append((text[index], False))
            index += 1
        elif text[index] == '"':
            index += 1
            value = []
            while index < len(text) and text[index] != '"':
                if text[index] == "\\":
                    index += 1
                    if index == len(text):
                        raise ValueError("unterminated quoted CMake argument")
                    value.append({"n": "\n", "t": "\t", "r": "\r"}.get(text[index], text[index]))
                else:
                    value.append(text[index])
                index += 1
            if index == len(text):
                raise ValueError("unterminated quoted CMake argument")
            tokens.append(("".join(value), True))
            index += 1
        else:
            end = index
            while end < len(text) and not text[end].isspace() and text[end] not in '()#"':
                end += 1
            value = text[index:end]
            if value.startswith("["):
                raise ValueError("CMake bracket arguments are outside the static grammar")
            tokens.append((value, False))
            index = end
    return tokens


def _cmake(text: str) -> tuple[dict, list[dict]]:
    tokens = _cmake_tokens(text)
    declarations = []
    index = 0
    control_depth = 0
    project_command_unresolved = False
    while index < len(tokens):
        command = tokens[index][0].lower()
        if index + 1 >= len(tokens) or tokens[index + 1][0] != "(":
            raise ValueError("CMake input contains non-command statements")
        index += 2
        depth = 1
        args = []
        while index < len(tokens) and depth:
            value, quoted = tokens[index]
            if not quoted and value == "(":
                depth += 1
            elif not quoted and value == ")":
                depth -= 1
            if depth:
                args.append((value, quoted))
            index += 1
        if depth:
            raise ValueError("unterminated CMake command")
        if command in ("function", "macro") and (
            not args
            or args[0][0].casefold() == "project"
            or any(marker in args[0][0] for marker in ("$", ";", "\\", "(", ")"))
        ):
            project_command_unresolved = True
        if command in ("endif", "endforeach", "endwhile", "endfunction", "endmacro", "endblock"):
            control_depth -= 1
            if control_depth < 0:
                raise ValueError("unmatched CMake control block")
        if command == "project":
            declarations.append((args, control_depth))
        if command in ("if", "foreach", "while", "function", "macro", "block"):
            control_depth += 1
    if control_depth:
        raise ValueError("unterminated CMake control block")
    if project_command_unresolved:
        return {}, [
            {
                "field": field,
                "reason": "builtin CMake project command is overridden or its identity is unresolved",
            }
            for field in ("name", "version", "description", "url")
        ]
    if len(declarations) != 1 or declarations[0][1]:
        return {}, [
            {
                "field": field,
                "reason": "CMake project declaration is absent, ambiguous or conditional",
            }
            for field in ("name", "version", "description")
        ]
    args = declarations[0][0]
    fields = {}
    issues = []
    seen = set()

    def literal(field: str, value: str) -> None:
        if field in seen:
            issues.append(
                {
                    "field": field,
                    "status": "conflicting",
                    "reason": "duplicate CMake project keyword",
                }
            )
            fields.pop(field, None)
            return
        seen.add(field)
        if not value or any(marker in value for marker in ("$", ";", "\\", "(", ")")):
            issues.append(
                {
                    "field": field,
                    "reason": "CMake project field uses expansion or unsupported syntax",
                }
            )
        elif field == "version" and not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", value):
            issues.append(
                {"field": field, "reason": "CMake VERSION must be a literal numeric version"}
            )
        else:
            fields[field] = value

    if args:
        literal("name", args[0][0])
    else:
        issues.append({"field": "name", "reason": "CMake project name is absent"})
    index = 1
    keywords = {"VERSION": "version", "DESCRIPTION": "description", "HOMEPAGE_URL": "url"}
    while index < len(args):
        value = args[index][0]
        if value in keywords:
            if index + 1 >= len(args) or args[index + 1][0] in (*keywords, "LANGUAGES"):
                issues.append(
                    {"field": keywords[value], "reason": "CMake project keyword has no value"}
                )
                index += 1
            else:
                literal(keywords[value], args[index + 1][0])
                index += 2
        elif value == "LANGUAGES":
            index += 1
            while index < len(args) and args[index][0] not in keywords:
                index += 1
        else:
            # Legacy project languages are literals, not metadata fields.
            if any(marker in value for marker in ("$", "(", ")")):
                issues.append(
                    {"field": "metadata", "reason": "unsupported CMake project argument"}
                )
            index += 1
    return fields, issues


_MESON_TOKEN = re.compile(
    r"\s+|#[^\n]*|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[^\s]",
    re.DOTALL,
)


def _meson(text: str) -> tuple[dict, list[dict]]:
    tokens = [
        (match.group(), match.start())
        for match in _MESON_TOKEN.finditer(text)
        if not match.group().isspace() and not match.group().startswith("#")
    ]
    candidates = [
        index
        for index in range(len(tokens) - 1)
        if tokens[index][0] == "project" and tokens[index + 1][0] == "("
    ]
    if len(candidates) != 1 or candidates[0] != 0:
        return {}, [
            {
                "field": field,
                "reason": "Meson project must be one unconditional initial declaration",
            }
            for field in ("name", "version", "license")
        ]
    index = 2
    depth = 1
    call = ["project", "("]
    while index < len(tokens) and depth:
        token = tokens[index][0]
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        # Meson literal keyword syntax has the same AST shape as Python kwargs.
        call.append("=" if token == ":" else token)
        index += 1
    if depth:
        raise ValueError("unterminated Meson project declaration")
    node = ast.parse(" ".join(call), mode="eval").body
    if not isinstance(node, ast.Call) or not node.args:
        raise ValueError("invalid Meson project declaration")
    fields = {}
    issues = []
    try:
        fields["name"] = ast.literal_eval(node.args[0])
    except (ValueError, TypeError):
        issues.append({"field": "name", "reason": "Meson project name is computational"})
    seen = set()
    for keyword in node.keywords:
        field = keyword.arg
        if field not in ("version", "license"):
            continue
        if field in seen:
            fields.pop(field, None)
            issues.append(
                {
                    "field": field,
                    "status": "conflicting",
                    "reason": "duplicate Meson project keyword",
                }
            )
            continue
        seen.add(field)
        try:
            value = ast.literal_eval(keyword.value)
            if field == "license" and isinstance(value, list):
                if not value or any(not isinstance(item, str) for item in value):
                    raise ValueError("licence array must contain strings")
                value = " AND ".join(f"({item})" for item in value)
            if not isinstance(value, str):
                raise ValueError("project metadata must be strings")
            fields[field] = value
        except (ValueError, TypeError):
            issues.append(
                {
                    "field": field,
                    "reason": "Meson project value is computational or not a literal string",
                }
            )
    return fields, issues


def parse_native(text: str, filename: str) -> tuple[dict, list[dict]]:
    """Return literal project fields and explicit unsupported per-field results."""
    try:
        return _cmake(text) if filename == "CMakeLists.txt" else _meson(text)
    except (ValueError, SyntaxError) as error:
        return {}, [
            {"field": field, "reason": str(error)} for field in ("name", "version", "license")
        ]


_COMPUTED = re.compile(r"\$[{(A-Za-z_]")
_AUTHOR = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "url": {"type": "string"},
        "maintainer": {"type": "boolean"},
    },
    "required": ["name"],
}
_PLATFORMIO = {
    "name": {"type": "string"},
    "version": {"type": "string"},
    "description": {"type": "string"},
    "license": {"type": "string"},
    "homepage": {"type": "string"},
    "repository": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "url": {"type": "string"},
            "branch": {"type": "string"},
        },
        "required": ["url"],
    },
    "authors": {"anyOf": [_AUTHOR, {"type": "array", "items": _AUTHOR}]},
}
_PLATFORMIO_FIELDS = {
    "name": "name",
    "version": "version",
    "description": "description",
    "license": "license",
    "homepage": "url",
    "repository": "repository",
    "authors": "author",
}


class _Object(dict):
    """JSON object that remembers keys it repeated."""

    def __init__(self, pairs: list[tuple[str, Any]]):
        super().__init__()
        self.duplicates: set[str] = set()
        for key, value in pairs:
            if key in self:
                self.duplicates.add(key)
            self[key] = value


def _author(entry: dict[str, Any]) -> str:
    name = entry["name"].strip()
    email = entry.get("email", "").strip()
    return f"{name} <{email}>" if email else name


def parse_library_json(text: str) -> list[dict[str, Any]]:
    """Return per-field rows for a PlatformIO ``library.json`` manifest.

    Only literal strings of the documented manifest shape become ``known`` rows.
    A value of the wrong type, one that uses template expansion, or a repeated key is
    ``unsupported`` or ``conflicting`` with its reason. ``repository`` is a ``candidate``
    row: it is retained but never asserted. Keys outside the consumed set are ignored
    and nothing is evaluated, including the ``build`` section. A document that is not a
    JSON object raises ``ValueError``.
    """
    try:
        document = json.loads(text, object_pairs_hook=_Object)
    except ValueError as error:
        raise ValueError(f"library.json is not valid JSON: {error}") from error
    if not isinstance(document, _Object):
        raise ValueError("library.json must be a JSON object")
    rows: list[dict[str, Any]] = []
    for key, schema in _PLATFORMIO.items():
        if key not in document:
            continue
        field = _PLATFORMIO_FIELDS[key]
        value = document[key]
        row: dict[str, Any] = {"field": field, "value": value, "status": "known", "key": key}
        error = next(Draft7Validator(schema).iter_errors(value), None)
        computed = key not in ("description", "authors") and any(
            _COMPUTED.search(item) for item in _strings(value)
        )
        if key in document.duplicates:
            row.update(status="conflicting", reason=f"library.json repeats the {key!r} key")
        elif error is not None:
            row.update(
                status="unsupported",
                reason=f"library.json {key!r} does not match the manifest schema: {error.message}",
            )
        elif computed:
            row.update(
                status="unsupported",
                reason=f"library.json {key!r} uses template expansion, which is not evaluated",
            )
        elif key == "version" and re.search(r"\s", value):
            row.update(status="unsupported", reason="library.json version is not a release string")
        elif key == "repository":
            row.update(value=value["url"], status="candidate")
        elif key == "authors":
            entries = value if isinstance(value, list) else [value]
            names = [_author(entry) for entry in entries]
            if not names or any(not entry["name"].strip() for entry in entries):
                row.update(status="unsupported", reason="library.json author has no name")
            else:
                row["value"] = ", ".join(names)
        rows.append(row)
    return rows


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []
