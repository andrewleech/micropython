# SPDX-License-Identifier: MIT
"""Deterministic merge of generated module declarations into a vendored template.

The generated declarations are the facts: which names a build has, which classes and modules are open
(carry ``def __getattr__``), and which signatures are fully proven. The vendored declarations are the
template that supplies the shapes a type checker needs (bases, generics, dunders, overloads).

``merge_template`` keeps the vendored declarations whose name the generated module has, plus the names a
checker needs to bootstrap, and appends what only the generated module declares. It reads only text and
returns text and a report; the result depends on nothing else.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Collection, Iterable

from .stubast import (binding_names, defined_names, has_star_import, index_body, is_dunder, is_private,
                      leading_comments, loaded_names, module_names, parse, reindent, segment)

ANY = "_mpy_Any"
_ANY_IMPORT = f"from typing import Any as {ANY}"
_OBJECT_TYPES = ("object",)
_FUTURE = "__future__"
_BANNER = "# Declarations of the selected MicroPython build merged into the vendored template."


class MergeError(ValueError):
    """The merge could not produce declarations that stand on their own."""


@dataclass(frozen=True)
class Merged:
    text: str
    report: dict


@dataclass(frozen=True)
class ClassInfo:
    """The members of a class (name to abstract) and its base expressions."""
    members: dict[str, bool]
    bases: list[str]


def _lines(text: str) -> list[str]:
    return text.split("\n")


def _head(annotation: str) -> str:
    return annotation.split("[")[0].strip()


def class_table(tree: ast.Module) -> dict[str, ClassInfo]:
    """Class name to members and bases for a module, version-guarded branches merged; the first definition
    of a name gives the bases."""
    table: dict[str, ClassInfo] = {}

    def abstract(node: ast.stmt) -> bool:
        return any((isinstance(item, ast.Name) and item.id == "abstractmethod") or
                   (isinstance(item, ast.Attribute) and item.attr == "abstractmethod")
                   for item in getattr(node, "decorator_list", ()))

    def members(body: list[ast.stmt], found: dict[str, bool]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(node.name, abstract(node))
            elif isinstance(node, ast.If):
                members(node.body, found)
                members(node.orelse, found)
            else:
                for name in defined_names(node):
                    found.setdefault(name, False)

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                found: dict[str, bool] = {}
                members(node.body, found)
                info = table.get(node.name)
                if info is None:
                    table[node.name] = ClassInfo(found, [ast.unparse(base) for base in node.bases])
                else:
                    for name, flag in found.items():
                        info.members.setdefault(name, flag)
            elif isinstance(node, ast.If):
                walk(node.body)
                walk(node.orelse)

    walk(tree.body)
    return table


def _unimplemented(name: str, table: dict[str, ClassInfo]) -> list[str]:
    """The abstract members of ``name`` that no class on its base chain implements."""
    order: list[str] = []

    def visit(base: str) -> None:
        base = _head(base)
        if base in order or base not in table:
            return
        order.append(base)
        for parent in table[base].bases:
            visit(parent)

    visit(name)
    first: dict[str, tuple[bool, str]] = {}
    for cls in order:
        for member, flag in table[cls].members.items():
            first.setdefault(member, (flag, cls))
    return sorted(member for member, (flag, _) in first.items() if flag)


def _signature(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str | None], str | None]:
    """The parameter annotations (receiver excluded) and return annotation of a function as text."""
    args = function.args
    positional = args.posonlyargs + args.args
    params = positional + args.kwonlyargs + ([args.vararg] if args.vararg else []) + ([args.kwarg] if args.kwarg else [])
    result: list[str | None] = []
    for index, param in enumerate(params):
        if index == 0 and param.arg in ("self", "cls") and positional[:1] == [param]:
            continue
        result.append(ast.unparse(param.annotation) if param.annotation else None)
    return result, ast.unparse(function.returns) if function.returns else None


def _fully_proven(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    params, returns = _signature(function)
    return all(item not in (None, ANY, *_OBJECT_TYPES) for item in params) and returns not in (None, ANY)


def _refines(generated: ast.FunctionDef | ast.AsyncFunctionDef, template: list) -> bool:
    """The generated return is proven but only as the bare head of a more specific template return."""
    returns = _signature(generated)[1]
    if returns in (None, ANY):
        return False
    return any(other and _head(other) == _head(returns) and other != returns
               for other in (_signature(item)[1] for item in template))


def _strip_any_receiver(block: list[str]) -> list[str]:
    result = []
    for line in block:
        for receiver in ("self", "cls"):
            line = line.replace(f"{receiver}: {ANY},", f"{receiver},").replace(f"{receiver}: {ANY})", f"{receiver})")
        result.append(line)
    return result


class _Merger:
    def __init__(self, generated: str, template: str, keep: Collection[str], bare: Collection[str],
                 typing: dict[str, ClassInfo], what: str) -> None:
        self.what = what
        self.keep = frozenset(keep)
        self.bare = frozenset(bare)
        self.typing = typing
        self.glines, self.tlines = _lines(generated), _lines(template)
        self.gtree = parse(generated, f"generated {what}")
        self.ttree = parse(template, f"template {what}")
        self.gindex = index_body(self.gtree.body)
        self.dead: frozenset[int] = frozenset()
        self.report: dict[str, list[str]] = {key: [] for key in (
            "dropped", "dropped_members", "kept_dunders", "generated_wins", "template_wins", "return_overrides",
            "return_conflicts", "generated_only", "generated_only_members", "base_provided_members", "open_classes",
            "kept_kind_mismatch", "bare")}

    def generated_block(self, node: ast.stmt, indent: int) -> list[str]:
        """A generated declaration with the comment lines the generator wrote above it."""
        block = leading_comments(self.glines, node) + segment(self.glines, node)
        return _strip_any_receiver(reindent(block, indent))

    # Emission
    def emit(self, unused: frozenset[str]) -> list[str]:
        for items in self.report.values():
            items.clear()
        bound: set[str] = set()
        out = self.body(self.ttree.body, 0, "", bound, unused, top=True, scope_index=self.gindex, open_scope=False)
        for node in self.gtree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.If)):
                continue
            names = defined_names(node)
            if names and all(name not in bound for name in names):
                out += [""] + self.generated_block(node, 0)
                self.report["generated_only"].extend(names)
        for name in sorted(self.bare - bound - set(self.gindex)):
            out += ["", f"class {name}: ..."]
            self.report["bare"].append(name)
        return out

    def body(self, body: list[ast.stmt], indent: int, scope: str, bound: set[str], unused: frozenset[str], *,
             top: bool, scope_index: dict[str, list[ast.stmt]], open_scope: bool) -> list[str]:
        out: list[str] = []
        done: set[str] = set()
        for node in body:
            if isinstance(node, ast.Expr) or id(node) in self.dead:
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                out += self.import_lines(node, indent, unused)
            elif isinstance(node, ast.If):
                inner = self.body(node.body, indent + 4, scope, bound, unused, top=top, scope_index=scope_index,
                                  open_scope=open_scope)
                orelse = self.body(node.orelse, indent + 4, scope, bound, unused, top=top, scope_index=scope_index,
                                   open_scope=open_scope) if node.orelse else []
                if inner or orelse:
                    pad = " " * indent
                    out += [f"{pad}if {ast.unparse(node.test)}:"] + (inner or [" " * (indent + 4) + "pass"])
                    if orelse:
                        out += [f"{pad}else:"] + orelse
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in done:
                    continue
                done.add(node.name)
                group = [item for item in body if id(item) not in self.dead
                         and isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == node.name]
                out += self.function(node.name, group, indent, scope, bound, top, scope_index, open_scope)
            elif isinstance(node, ast.ClassDef):
                out += self.class_(node, indent, scope, bound, unused, top, scope_index, open_scope)
            else:
                names = defined_names(node)
                if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
                    continue
                if not names:
                    out += reindent(segment(self.tlines, node), indent)
                    continue
                if "__all__" in names:
                    continue
                keep = [name for name in names if self.retained(name, scope_index, top, open_scope)]
                if keep:
                    if top:
                        bound.update(names)
                    out += reindent(segment(self.tlines, node), indent)
                else:
                    self.report["dropped"].extend(self.qualify(scope, names))
        return out

    def qualify(self, scope: str, names: Iterable[str]) -> list[str]:
        return [f"{scope}.{name}" if scope else name for name in names]

    def retained(self, name: str, index: dict[str, list[ast.stmt]], top: bool, open_scope: bool) -> bool:
        """Whether the template declaration ``name`` stays: the generated scope has the name, or the name is
        private (a helper of the template), forced by the caller, or a dunder of a class."""
        if name in index:
            return True
        if top:
            return is_private(name) or name in self.keep
        return is_dunder(name) or is_private(name) or open_scope

    def import_lines(self, node: ast.stmt, indent: int, unused: frozenset[str]) -> list[str]:
        if not unused or (isinstance(node, ast.ImportFrom) and node.module == _FUTURE):
            return reindent(segment(self.tlines, node), indent)
        kept = [alias for alias in node.names if (alias.asname or alias.name).split(".")[0] not in unused]
        if not kept:
            return []
        if len(kept) == len(node.names):
            return reindent(segment(self.tlines, node), indent)
        reduced = ast.copy_location(
            ast.ImportFrom(module=node.module, names=kept, level=node.level) if isinstance(node, ast.ImportFrom)
            else ast.Import(names=kept), node)
        return [" " * indent + ast.unparse(ast.fix_missing_locations(reduced))]

    # Functions
    def function(self, name: str, group: list, indent: int, scope: str, bound: set[str], top: bool,
                 index: dict[str, list[ast.stmt]], open_scope: bool) -> list[str]:
        qualified = self.qualify(scope, [name])[0]
        counterpart = next((node for node in index.get(name, ()) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
        if counterpart is None:
            if not self.retained(name, index, top, open_scope):
                self.report["dropped" if top else "dropped_members"].append(qualified)
                return []
            if name in index:
                self.report["kept_kind_mismatch"].append(qualified)
            elif not top and is_dunder(name):
                self.report["kept_dunders"].append(qualified)
            if top:
                bound.add(name)
            return [line for item in group for line in reindent(segment(self.tlines, item), indent)]
        if top:
            bound.add(name)
        if _fully_proven(counterpart) and not _refines(counterpart, group):
            self.report["generated_wins"].append(qualified)
            return self.generated_block(counterpart, indent)
        self.report["template_wins"].append(qualified)
        blocks = [reindent(segment(self.tlines, item), indent) for item in group]
        returns = _signature(counterpart)[1]
        if returns not in (None, ANY):
            template_returns = [_signature(item)[1] for item in group]
            if not any(other and _head(other) == _head(returns) for other in template_returns):
                if len(group) == 1 and template_returns[0] is not None:
                    text = "\n".join(blocks[0])
                    marker = f"-> {template_returns[0]}"
                    if marker in text:
                        blocks[0] = text.replace(marker, f"-> {returns}", 1).split("\n")
                        self.report["return_overrides"].append(qualified)
                else:
                    self.report["return_conflicts"].append(qualified)
        comments = leading_comments(self.glines, counterpart)
        out = [" " * indent + "# template signature; generated declaration unresolved"]
        out += reindent(comments, indent) if comments else []
        for block in blocks:
            out += block
        return out

    # Classes
    def class_(self, node: ast.ClassDef, indent: int, scope: str, bound: set[str], unused: frozenset[str],
               top: bool, index: dict[str, list[ast.stmt]], open_scope: bool) -> list[str]:
        name = node.name
        qualified = self.qualify(scope, [name])[0]
        if top and name in self.bare and name not in index:
            bound.add(name)
            self.report["bare"].append(name)
            return [" " * indent + line for line in self.header(node)] + [" " * (indent + 4) + "..."]
        if not self.retained(name, index, top, open_scope):
            self.report["dropped" if top else "dropped_members"].append(qualified)
            return []
        if top:
            bound.add(name)
        counterpart = next((item for item in index.get(name, ()) if isinstance(item, ast.ClassDef)), None)
        if counterpart is None:
            if name in index:
                self.report["kept_kind_mismatch"].append(qualified)
            return reindent(segment(self.tlines, node), indent)
        members = index_body(counterpart.body)
        open_class = "__getattr__" in members
        if open_class:
            self.report["open_classes"].append(qualified)
        lines = self.header(node)
        pad = " " * indent
        comments = leading_comments(self.glines, counterpart)
        out = reindent(comments, indent) if comments else []
        out += [pad + line for line in lines]
        header_length = len(out)
        out += self.body(node.body, indent + 4, qualified, bound, unused, top=False, scope_index=members,
                         open_scope=open_class)
        template_members = index_body(self.alive(node.body))
        for member, nodes in members.items():
            if member in template_members:
                continue
            if member == "__getattr__" or not self.provided_by_base(node, member):
                for item in nodes:
                    out += self.generated_block(item, indent + 4)
                self.report["generated_only_members"].append(f"{qualified}.{member}")
            else:
                self.report["base_provided_members"].append(f"{qualified}.{member}")
        if not any(line.strip() and not line.strip().startswith("#") for line in out[header_length:]):
            out.append(pad + "    ...")
        return out

    def alive(self, body: list[ast.stmt]) -> list[ast.stmt]:
        """``body`` without the statements dropped for reading a name nothing defines."""
        return [node for node in body if id(node) not in self.dead]

    @staticmethod
    def header(node: ast.ClassDef) -> list[str]:
        """The class statement line(s) of ``node``: decorators, bases, keywords and type parameters."""
        header = ast.ClassDef(name=node.name, bases=node.bases, keywords=node.keywords, body=[ast.Pass()],
                              decorator_list=node.decorator_list, type_params=getattr(node, "type_params", []))
        return ast.unparse(ast.fix_missing_locations(header)).split("\n")[:-1]

    def provided_by_base(self, node: ast.ClassDef, member: str) -> bool:
        """Whether a template base class of ``node`` (one the typing template describes) defines ``member``."""
        seen: set[str] = set()
        todo = [ast.unparse(base) for base in node.bases]
        while todo:
            base = _head(todo.pop())
            if base in seen:
                continue
            seen.add(base)
            info = self.typing.get(base)
            if info is None:
                continue
            if member in info.members:
                return True
            todo += info.bases
        return False


def _unused_imports(tree: ast.Module) -> frozenset[str]:
    """The names bound by imports that nothing in the module reads and that are not explicit re-exports."""
    used = loaded_names(tree)
    unused: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module == _FUTURE:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = (alias.asname or alias.name).split(".")[0]
                reexport = alias.asname is not None and alias.asname == alias.name
                if bound not in used and not reexport:
                    unused.add(bound)
    return frozenset(unused)


def _insert_header(lines: list[str], extra: list[str]) -> list[str]:
    """``lines`` with ``extra`` after the leading ``from __future__`` imports."""
    tree = ast.parse("\n".join(lines))
    position = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == _FUTURE:
            position = node.end_lineno
        else:
            break
    return lines[:position] + extra + lines[position:]


def _demote_abstract(text: str, typing: dict[str, ClassInfo], report: dict) -> str:
    """Rebase a class whose template ABC base leaves abstract members unimplemented (because the build lacks
    them) onto the nearest ancestor ABC whose abstract members are all implemented, same type arguments."""
    mine = class_table(ast.parse(text))
    table = {**typing, **mine}
    for name in sorted(mine):
        if is_private(name) or not _unimplemented(name, table):
            continue
        bases = []
        for base in mine[name].bases:
            head = _head(base)
            arguments = base[len(head):]

            def clean(candidate: str) -> bool:
                return not _unimplemented(name, {**table, name: ClassInfo(mine[name].members, [candidate])})

            if clean(base):
                bases.append(base)
                continue
            candidates: list[str] = []
            todo = list(table[head].bases) if head in table else []
            while todo:
                ancestor = _head(todo.pop(0))
                if ancestor in table and ancestor + arguments not in candidates:
                    candidates.append(ancestor + arguments)
                    todo += table[ancestor].bases
            chosen = next((candidate for candidate in candidates if clean(candidate)), None)
            if chosen is None:
                report.setdefault("abstract_unresolved", []).append(name)
                bases.append(base)
            else:
                report.setdefault("abstract_demoted", []).append(f"{name}: {base} -> {chosen}")
                bases.append(chosen)
        old, new = f"class {name}({', '.join(mine[name].bases)}):", f"class {name}({', '.join(bases)}):"
        if old != new:
            if old not in text:
                raise MergeError(f"cannot rebase class {name}: header not found")
            text = text.replace(old, new)
    return text


def _own_reads(node: ast.stmt) -> set[str]:
    """The names a template statement reads itself: a class reads its bases, keywords and decorators, not its
    members (they are statements of their own)."""
    if isinstance(node, ast.ClassDef):
        parts: list[ast.AST] = [*node.bases, *(keyword.value for keyword in node.keywords), *node.decorator_list]
        return set().union(*(loaded_names(part, strings=False) for part in parts)) if parts else set()
    return loaded_names(node, strings=False)


def _readers(tree: ast.Module, names: set[str]) -> dict[int, str]:
    """The statements of the template, at the top level and in class bodies with version-guarded branches
    included, that read one of ``names``, by id with their qualified name."""
    found: dict[int, str] = {}

    def walk(body: list[ast.stmt], scope: str) -> None:
        for node in body:
            if isinstance(node, ast.If):
                walk(node.body, scope)
                walk(node.orelse, scope)
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if names & _own_reads(node):
                label = ".".join(part for part in (scope, *(defined_names(node) or ["?"])) if part)
                found[id(node)] = label
            if isinstance(node, ast.ClassDef):
                walk(node.body, f"{scope}.{node.name}" if scope else node.name)

    walk(tree.body, "")
    return found


def _dangling(tree: ast.Module, builtin_names: Collection[str]) -> list[str]:
    """Names the module reads that nothing in it defines, imports or binds as a parameter and that the
    ``builtin_names`` of the pack's builtins module do not provide."""
    defined = set(builtin_names)
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            defined.update(binding_names(node))
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif type(node).__name__ in ("TypeVar", "ParamSpec", "TypeVarTuple") and hasattr(node, "name"):
            defined.add(node.name)
    return sorted({node.id for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in defined})


def merge_template(generated: str, template: str, *, what: str, keep: Collection[str] = (),
                   bare: Collection[str] = (), typing: dict[str, ClassInfo] | None = None,
                   builtin_names: Collection[str] | None = None, strict: bool = True) -> Merged:
    """The template's declarations restricted to the names ``generated`` has, merged with ``generated``.

    Rules, applied to the template in order and recursively through version guards:
    - private names (helpers, type variables, protocols), the names in ``keep`` and, in a class, dunders
      stay as they are; every other name stays only when the generated scope defines it;
    - a template statement (a function, an overload, a member, a class) that reads a name nothing defines is
      dropped, with whatever reads it in turn, before anything else fails;
    - a class named in ``bare`` that the generated module lacks stays as its header (bases and type
      parameters, no members), or is a plain empty class when the template has none, for names a checker
      looks up in the module without being told;
    - a class the generated module leaves open (``def __getattr__``) keeps every template member and gains the
      generated ``__getattr__``; a class it closes keeps only the members the generated class names;
    - a function the generated module declares with fully proven parameters and return type replaces the
      template's overloads unless its return is the bare head of a more specific template return; otherwise
      the template's declaration stays and a proven generated return replaces a template return that does not
      share its head (a single declaration only);
    - names and members only the generated module declares are appended, except members a template base class
      from ``typing`` already defines (they would shadow the base with weaker signatures);
    - ``__all__`` is dropped, a class whose abstract base members are no longer implemented is rebased
      (see ``_demote_abstract``) and imports that nothing reads are pruned.

    ``typing`` is the class table of the template ``typing`` module, used to find base-class members and
    abstract members. ``builtin_names`` are the names of the pack's merged builtins; ``None`` means this
    module is the builtins module and the names it defines itself are used. A name that is still read but
    defined nowhere (a generated declaration reads it, or no template statement can be dropped) raises
    ``MergeError``, or with ``strict`` false is listed as ``dangling`` in the report."""
    typing = typing or {}
    merger = _Merger(generated, template, keep, bare, typing, what)
    gen_imports = {name: node for node in merger.gtree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                   for name in binding_names(node)}
    # A template statement that reads a name the build does not have goes too (a member reading a class the build
    # lacks, an overload with a parameter of that class), and so does whatever reads that statement.
    dropped: dict[int, str] = {}
    while True:
        lines = merger.emit(frozenset())
        parsed = ast.parse("\n".join(lines))
        known = module_names(parsed) if builtin_names is None else builtin_names
        dangling = set(_dangling(parsed, known)) - {ANY} - gen_imports.keys()
        readers = _readers(merger.ttree, dangling)
        if not set(readers) - set(dropped):
            break
        dropped.update(readers)
        merger.dead = frozenset(dropped)
    unused = _unused_imports(ast.parse("\n".join(lines)))
    lines = merger.emit(unused)
    tree = ast.parse("\n".join(lines))
    bound = module_names(tree)
    header: list[str] = []
    rescued: list[str] = []
    known = bound if builtin_names is None else builtin_names
    needed = sorted(loaded_names(tree, strings=False) - bound - set(known))
    for name in needed:
        if name in gen_imports:
            line = "\n".join(segment(merger.glines, gen_imports[name]))
            if line not in header:
                header.append(line)
            rescued.append(name)
        elif name == ANY:
            header.append(_ANY_IMPORT)
    lines = _insert_header(lines, header)
    text = _BANNER + "\n" + "\n".join(lines) + "\n"
    report = {key: sorted(items) for key, items in merger.report.items()}
    report["rescued_imports"] = sorted(rescued)
    report["dropped_readers"] = sorted(set(dropped.values()))
    report["pruned_imports"] = sorted(unused)
    text = _demote_abstract(text, typing, report)
    result = ast.parse(text)
    unbound = sorted({_head(ast.unparse(base)) for node in ast.walk(result) if isinstance(node, ast.ClassDef)
                      for base in node.bases} - module_names(result) - set(known))
    imports = [f"from typing import {name}" for name in unbound if name in typing]
    if imports:
        text = "\n".join(_insert_header(text.split("\n"), imports))
        result = ast.parse(text)
    dangling = [] if has_star_import(result) else _dangling(result, module_names(result) if builtin_names is None else builtin_names)
    if dangling and strict:
        raise MergeError(f"merged {what} reads names nothing defines: {', '.join(dangling)}")
    report["dangling"] = dangling
    return Merged(text, report)
