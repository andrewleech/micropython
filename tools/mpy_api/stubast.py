# SPDX-License-Identifier: MIT
"""Parsing helpers for stub text shared by the declaration merge and the pack assembly: source segments,
the names a statement defines, and the import edges of a module."""

from __future__ import annotations

import ast
from dataclasses import dataclass


def decode(data: bytes) -> str:
    """Stub text with LF line ends; vendored stubs mix CRLF and LF."""
    return data.decode("utf-8").replace("\r\n", "\n")


def parse(text: str, what: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        raise ValueError(f"{what} is not valid stub syntax: {exc}") from exc


def is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) > 4


def is_private(name: str) -> bool:
    return name.startswith("_") and not is_dunder(name)


def start_line(node: ast.stmt) -> int:
    decorators = getattr(node, "decorator_list", None)
    return min([node.lineno] + [decorator.lineno for decorator in decorators]) if decorators else node.lineno


def segment(lines: list[str], node: ast.stmt) -> list[str]:
    """The source lines of ``node``, decorators included."""
    return lines[start_line(node) - 1:node.end_lineno]


def leading_comments(lines: list[str], node: ast.stmt) -> list[str]:
    """The comment lines directly above ``node`` (decorators included), without a blank line between."""
    found: list[str] = []
    index = start_line(node) - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        found.append(lines[index])
        index -= 1
    return found[::-1]


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def reindent(block: list[str], indent: int) -> list[str]:
    """``block`` with its first line at ``indent`` columns and every deeper line shifted equally."""
    old = indent_of(block[0])
    return ["" if not line.strip() else " " * indent + line[old:] if indent_of(line) >= old else line for line in block]


def defined_names(node: ast.stmt) -> list[str]:
    """The names a class, function or assignment statement binds; imports are not definitions here."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    return []


def binding_names(node: ast.stmt) -> list[str]:
    """The names an import statement binds."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(alias.asname or alias.name).split(".")[0] for alias in node.names]
    return []


def index_body(body: list[ast.stmt]) -> dict[str, list[ast.stmt]]:
    """Name to defining statements of a module or class body, version-guarded branches included, in text order."""
    index: dict[str, list[ast.stmt]] = {}
    for node in body:
        for name in defined_names(node):
            index.setdefault(name, []).append(node)
        if isinstance(node, ast.If):
            for name, nodes in index_body(node.body + node.orelse).items():
                index.setdefault(name, []).extend(nodes)
    return index


def module_names(tree: ast.Module) -> set[str]:
    """Every name the module binds at its top level, version-guarded branches and imports included."""
    names: set[str] = set()

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            names.update(defined_names(node))
            names.update(binding_names(node))
            if isinstance(node, ast.If):
                walk(node.body)
                walk(node.orelse)

    walk(tree.body)
    return names


def module_definitions(tree: ast.Module) -> set[str]:
    """The names the module defines with class, function and assignment statements at its top level,
    version-guarded branches included; the names it imports are not definitions."""
    names: set[str] = set()

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            names.update(defined_names(node))
            if isinstance(node, ast.If):
                walk(node.body)
                walk(node.orelse)

    walk(tree.body)
    return names


def has_star_import(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
               for node in ast.walk(tree))


def loaded_names(tree: ast.AST, strings: bool = True) -> set[str]:
    """The names an AST reads; with ``strings`` also the identifiers inside string constants, which stand for
    forward references in annotations."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif strings and isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                expression = ast.parse(node.value, mode="eval")
            except SyntaxError:
                continue
            names.update(inner.id for inner in ast.walk(expression) if isinstance(inner, ast.Name))
    return names


@dataclass(frozen=True)
class Edge:
    """One module's dependency on another: ``names`` are the names read from ``module`` (``*`` for a star
    import), ``bare`` is true when the statement needs the module itself (``import module``)."""
    module: str
    names: tuple[str, ...]
    bare: bool


def import_edges(tree: ast.Module, module: str, is_package: bool) -> list[Edge]:
    """The import edges of ``tree``, the module named ``module``, with relative imports made absolute and the
    attributes read through ``import M`` or ``import M as x`` counted as names of ``M``."""
    parts = module.split(".")
    package = parts if is_package else parts[:-1]
    edges: list[Edge] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(Edge(alias.name, (), True))
                if alias.asname or "." not in alias.name:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                if node.level - 1 > len(package):
                    raise ValueError(f"relative import beyond the top-level package in {module}")
                prefix = package[:len(package) - (node.level - 1)]
                base = ".".join(prefix + ([node.module] if node.module else []))
            edges.append(Edge(base, tuple(alias.name for alias in node.names), False))
    attributes: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            attributes.setdefault(aliases[node.value.id], set()).add(node.attr)
    edges.extend(Edge(target, tuple(sorted(names)), False) for target, names in sorted(attributes.items()))
    return edges
