"""Bounded literal CMake/Meson project declarations; no build-language evaluation."""

from __future__ import annotations

import ast
import re


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
