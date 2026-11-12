# SPDX-License-Identifier: MIT
"""Apply attributed reference fields without changing a build's runtime namespace.

Reference declarations are syntax, not runtime authority. Dependencies are resolved
from captured bytes and either mapped to selected runtime names or given private
annotation-only bindings. Source facts, rather than template precision, decide
which fields can be filled.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from .enrichment import _semantic_descriptor, _source_default, _source_return, _status
from .stubast import index_body, module_names, start_line


_RESOLVED = {"resolved", "bounded-resolved", "source-resolved", "selected-source"}
_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
_ARGS_MAX = 0xFFFF  # MP_OBJ_FUN_ARGS_MAX marks an unbounded native wrapper.
_BUILTINS = set(
    "bool bytes bytearray complex dict float frozenset int list memoryview object range set slice str tuple type BaseException Exception StopIteration OSError ValueError TypeError NotImplementedError Ellipsis NotImplemented".split()
)
_TYPING = set(
    "AbstractSet Annotated Any AnyStr AsyncContextManager AsyncGenerator AsyncIterable AsyncIterator Awaitable BinaryIO Callable ChainMap ClassVar Collection Concatenate Container ContextManager Coroutine Counter DefaultDict Deque Dict Final ForwardRef FrozenSet Generator Generic Hashable IO Iterable Iterator KeysView List Literal LiteralString Mapping MappingView Match MutableMapping MutableSequence MutableSet NamedTuple Never NewType NoReturn NotRequired Optional OrderedDict ParamSpec Pattern Protocol ReadOnly Required Reversible Self Sequence Set Sized SupportsAbs SupportsBytes SupportsComplex SupportsFloat SupportsIndex SupportsInt SupportsRound Text TextIO Tuple Type TypeAlias TypeAliasType TypeGuard TypeIs TypeVar TypeVarTuple TypedDict Union Unpack ValuesView overload final runtime_checkable".split()
)
_TYPE_FACTORIES = {"TypeVar", "TypeVarTuple", "ParamSpec", "NewType", "TypeAliasType"}


class _UnsafeReference(ValueError):
    pass


@dataclass(frozen=True)
class _Ref:
    module: str
    name: str
    nodes: tuple


def _module_path(path):
    parts = list(PurePosixPath(path).with_suffix("").parts)
    for prefix in (("typeshed", "stdlib"), ("stdlib",), ("modules",)):
        if tuple(parts[: len(prefix)]) == prefix:
            parts = parts[len(prefix) :]
            break
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _parameters(node, *, receiver=False):
    args = node.args
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + args.defaults
    result = [
        (arg, "positional_only" if i < len(args.posonlyargs) else "positional_or_keyword", default)
        for i, (arg, default) in enumerate(zip(positional, defaults))
    ]
    if receiver and result:
        result.pop(0)
    if args.vararg:
        result.append((args.vararg, "var_positional", None))
    result.extend(
        (arg, "keyword_only", default) for arg, default in zip(args.kwonlyargs, args.kw_defaults)
    )
    if args.kwarg:
        result.append((args.kwarg, "var_keyword", None))
    return result


def _descriptor(node):
    names = {ast.unparse(d).split(".")[-1] for d in node.decorator_list}
    return next(
        (name for name in ("staticmethod", "classmethod", "property") if name in names), "function"
    )


def _available(symbol):
    return _status(symbol.get("resolution", {}).get("availability")) in _RESOLVED


def _symbol(surface, module, qualname):
    matches = [
        row for row in surface.get("modules", []) if row.get("name") == module and _available(row)
    ]
    if len(matches) != 1:
        return None
    current = matches[0]
    for name in qualname.split("."):
        rows = current.get("exports", []) if current is matches[0] else current.get("members", [])
        rows = [row for row in rows if row.get("name") == name and _available(row)]
        if len(rows) != 1:
            return None
        current = rows[0]
        if current.get("kind") == "class":
            shared = [
                row
                for row in surface.get("types", [])
                if row.get("identity") == current.get("type_identity")
            ]
            if len(shared) == 1:
                current = {**shared[0], **current}
    return current


class _References:
    def __init__(self, bundle):
        texts = bundle.get("declaration_texts")
        if texts is None:
            raise ValueError(
                "enrichment bundle has no captured declaration_texts; load it with load_enrichment"
            )
        self.rows = {row["module"]: row for row in bundle["provenance"]["tree"]["files"]}
        self.trees = {}
        self.indices = {}
        for module, row in self.rows.items():
            text = texts.get(module)
            if (
                not isinstance(text, str)
                or hashlib.sha256(text.encode("utf-8")).hexdigest() != row["sha256"]
            ):
                raise ValueError(f"enrichment captured declaration hash differs: {module}")
            self.trees[module] = ast.parse(text, filename=row["path"])
            self.indices[module] = index_body(self.trees[module].body)
        self.packages = {
            module
            for module, row in self.rows.items()
            if PurePosixPath(row["path"]).name == "__init__.pyi"
        }

    def provenance(self, ref, node=None):
        row = self.rows[ref.module]
        node = node or ref.nodes[0]
        symbol_lineage = any(r["qualname"] == ref.name for r in row.get("symbol_lineage", []))
        return {
            "reference": row["path"],
            "qualname": ref.name,
            "span": {"line": node.lineno, "end_line": node.end_lineno},
            "symbol_lineage": ref.name if symbol_lineage else None,
        }

    def imported_module(self, module, node):
        if not node.level:
            return node.module or ""
        parts = module.split(".") if module in self.packages else module.split(".")[:-1]
        if node.level > len(parts) + 1:
            raise _UnsafeReference("relative import escapes captured package")
        return ".".join(
            parts[: len(parts) - node.level + 1] + ([node.module] if node.module else [])
        )

    def binding(self, module, name):
        found = []
        for node in self.trees[module].body:
            if isinstance(node, ast.ImportFrom):
                source = self.imported_module(module, node)
                for alias in node.names:
                    if alias.name != "*" and (alias.asname or alias.name) == name:
                        found.append((source, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.asname or alias.name.split(".")[0]) == name:
                        found.append(
                            (alias.name if alias.asname else alias.name.split(".")[0], None)
                        )
        found = list(dict.fromkeys(found))
        if len(found) > 1:
            raise _UnsafeReference(f"ambiguous imported binding {module}.{name}")
        return found[0] if found else None

    def resolve(self, module, name, seen=frozenset()):
        key = (module, name)
        if key in seen or module not in self.trees:
            return None
        nodes = self.indices[module].get(name)
        if nodes:
            return _Ref(module, name, tuple(nodes))
        binding = self.binding(module, name)
        if binding and binding[1] is not None:
            return self.resolve(*binding, seen | {key})
        found = []
        for node in self.trees[module].body:
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                resolved = self.resolve(self.imported_module(module, node), name, seen | {key})
                if resolved and resolved not in found:
                    found.append(resolved)
        if len(found) > 1:
            raise _UnsafeReference(f"ambiguous star-imported binding {module}.{name}")
        return found[0] if found else None

    def member(self, ref, name, seen=frozenset()):
        key = (ref.module, ref.name)
        if key in seen or len(ref.nodes) != 1 or not isinstance(ref.nodes[0], ast.ClassDef):
            return None
        owner = ref.nodes[0]
        nodes = index_body(owner.body).get(name)
        if nodes:
            return _Ref(ref.module, ref.name + "." + name, tuple(nodes))
        inherited = []
        for base in owner.bases:
            base = base.value if isinstance(base, ast.Subscript) else base
            if isinstance(base, ast.Name):
                parent = self.resolve(ref.module, base.id)
            elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                binding = self.binding(ref.module, base.value.id)
                parent = (
                    self.resolve(binding[0], base.attr) if binding and binding[1] is None else None
                )
            else:
                parent = None
            member = self.member(parent, name, seen | {key}) if parent else None
            if member and member not in inherited:
                inherited.append(member)
        if len(inherited) > 1:
            raise _UnsafeReference(
                f"ambiguous inherited reference member {ref.module}.{ref.name}.{name}"
            )
        return inherited[0] if inherited else None


def _import_bindings(body):
    bindings = {}
    for node in body:
        if isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    bindings.setdefault(alias.asname or alias.name, set()).add(
                        (node.module, alias.name)
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                bindings.setdefault(name, set()).add((alias.name if alias.asname else name, None))
    return {name: next(iter(values)) for name, values in bindings.items() if len(values) == 1}


def _leading_imports(tree):
    has_documentation = ast.get_docstring(tree, clean=False) is not None
    for index, node in enumerate(tree.body):
        if index == 0 and has_documentation:
            continue
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            break
        yield node


class _Dependencies:
    """A transactional closure of private annotation dependencies for one module."""

    def __init__(self, refs, module, surface, available, reserved, generated, target):
        self.refs, self.module, self.surface, self.available = refs, module, surface, available
        self.reserved = set(reserved)
        self.bindings = {}
        self.statements = {}
        self.provenance = {}
        self.class_bindings = {}
        self.runtime = {}
        self.source_imports = _import_bindings(generated.body)
        self.target_imports = _import_bindings(target.body)
        self.source_index = index_body(generated.body)
        self.target_index = index_body(target.body)
        self.source_aliases = {}
        self.existing_imports = {
            value: name
            for name, value in _import_bindings(_leading_imports(target)).items()
            if name not in self.target_index
        }
        for row in surface.get("modules", []):
            if not _available(row):
                continue
            for symbol in row.get("exports", []):
                if symbol.get("kind") != "class" or not _available(symbol):
                    continue
                try:
                    ref = refs.resolve(row["name"], symbol["name"])
                except _UnsafeReference:
                    continue
                if ref:
                    self.runtime[(ref.module, ref.name)] = (row["name"], symbol["name"])

    def fork(self):
        result = copy.copy(self)
        result.reserved = set(self.reserved)
        result.bindings = dict(self.bindings)
        result.statements = dict(self.statements)
        result.provenance = dict(self.provenance)
        result.class_bindings = dict(self.class_bindings)
        result.source_aliases = dict(self.source_aliases)
        return result

    def unknown(self, annotation):
        if _unknown(annotation):
            return True
        if isinstance(annotation, ast.Name):
            binding = self.target_imports.get(annotation.id)
            if annotation.id not in self.target_index and binding in (
                ("typing", "Any"),
                ("typing_extensions", "Any"),
            ):
                return True
            return any(
                isinstance(node, ast.ImportFrom)
                and node.module in ("typing", "typing_extensions")
                and any(
                    alias.name == "Any" and alias.asname == annotation.id for alias in node.names
                )
                for node in self.statements.values()
            )
        return False

    def source_unknown(self, annotation):
        return _unknown(annotation) or (
            isinstance(annotation, ast.Name)
            and self.source_imports.get(annotation.id)
            in (("typing", "Any"), ("typing_extensions", "Any"))
        )

    def _same_source_binding(self, name, seen=frozenset()):
        if name in seen:
            return True
        if name in self.source_imports:
            return (
                self.source_imports[name] == self.target_imports.get(name)
                and name not in self.target_index
            )
        nodes = self.source_index.get(name, ())
        target = self.target_index.get(name, ())
        if len(nodes) == 1 and isinstance(nodes[0], ast.ClassDef):
            return name in self.reserved
        if len(nodes) == len(target) == 1 and isinstance(nodes[0], (ast.Assign, ast.AnnAssign)):
            value = nodes[0].value
            return (
                value is not None
                and ast.dump(nodes[0]) == ast.dump(target[0])
                and all(
                    self._same_source_binding(node.id, seen | {name})
                    for node in ast.walk(value)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                )
            )
        return name in _BUILTINS and name not in self.reserved

    def source_name(self, name):
        """Resolve generated annotation bindings in the target file's namespace."""
        if name in self.source_aliases:
            return self.source_aliases[name]
        binding = self.source_imports.get(name)
        if binding:
            if binding[1] is None:
                raise ValueError(f"generated module alias {name} needs a qualified type member")
            result = self.existing_imports.get(binding)
            if result is None:
                try:
                    result = self.imported(*binding)
                except _UnsafeReference as error:
                    raise ValueError(f"generated annotation dependency: {error}") from error
            self.source_aliases[name] = result
            return result
        nodes = self.source_index.get(name, ())
        if len(nodes) == 1 and isinstance(nodes[0], ast.ClassDef):
            if name not in self.reserved:
                raise ValueError(
                    f"generated annotation needs missing selected class {self.module}.{name}"
                )
            return name
        if self._same_source_binding(name):
            self.source_aliases[name] = name
            return name
        if len(nodes) == 1 and isinstance(nodes[0], (ast.Assign, ast.AnnAssign)):
            value = nodes[0].value
            if value is None:
                raise ValueError(f"generated annotation helper {name} has no value")
            key = ("source-helper", self.module, name)
            alias = self.alias(key, name)
            self.source_aliases[name] = alias
            if isinstance(value, ast.Call):
                factory = (
                    self.source_imports.get(value.func.id)
                    if isinstance(value.func, ast.Name)
                    else None
                )
                if (
                    not factory
                    or factory[0] not in ("typing", "typing_extensions")
                    or factory[1] not in _TYPE_FACTORIES
                ):
                    raise ValueError(f"generated annotation helper {name} is not a typing factory")
                if (
                    not value.args
                    or not isinstance(value.args[0], ast.Constant)
                    or not isinstance(value.args[0].value, str)
                ):
                    raise ValueError(f"generated typing factory {name} has no literal name")
                replacement = copy.deepcopy(value)
                replacement.func = self.source_expression(value.func)
                replacement.args = [ast.Constant(value=alias)] + [
                    self.source_expression(arg) for arg in value.args[1:]
                ]
                replacement.keywords = [
                    ast.keyword(arg=kw.arg, value=self.source_expression(kw.value))
                    for kw in value.keywords
                ]
            else:
                replacement = self.source_expression(value)
            self.statements[key] = ast.Assign(
                targets=[ast.Name(id=alias, ctx=ast.Store())],
                value=replacement,
            )
            return alias
        if name in _BUILTINS:
            if name not in self.reserved:
                return name
            if "builtins" in self.available and name not in module_names(
                self.available["builtins"]
            ):
                raise ValueError(f"generated annotation needs absent builtin {name}")
            key = ("source-import", "builtins", name)
            alias = self.alias(key, name)
            self.statements[key] = ast.ImportFrom(
                module="builtins",
                names=[ast.alias(name=name, asname=alias)],
                level=0,
            )
            return alias
        raise ValueError(f"unresolved generated annotation binding {self.module}.{name}")

    def source_expression(self, expression):
        if expression is None:
            return None
        dependencies = self

        class Rewrite(ast.NodeTransformer):
            def visit_Name(self, node):
                return ast.copy_location(
                    ast.Name(id=dependencies.source_name(node.id), ctx=node.ctx), node
                )

            def visit_Attribute(self, node):
                binding = (
                    dependencies.source_imports.get(node.value.id)
                    if isinstance(node.value, ast.Name)
                    else None
                )
                if not binding or binding[1] is not None:
                    raise ValueError("generated attribute annotation has no module binding")
                canonical = (binding[0], node.attr)
                alias = dependencies.existing_imports.get(canonical)
                if alias is None:
                    try:
                        alias = dependencies.imported(*canonical)
                    except _UnsafeReference as error:
                        raise ValueError(f"generated annotation dependency: {error}") from error
                return ast.copy_location(ast.Name(id=alias, ctx=ast.Load()), node)

            def visit_Constant(self, node):
                if isinstance(node.value, str):
                    value = self.visit(ast.parse(node.value, mode="eval").body)
                    return ast.copy_location(ast.Constant(value=ast.unparse(value)), node)
                return node

            def visit_Subscript(self, node):
                binding = (
                    dependencies.source_imports.get(node.value.id)
                    if isinstance(node.value, ast.Name)
                    else None
                )
                if binding in (("typing", "Literal"), ("typing_extensions", "Literal")):
                    values = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                    for value in values:
                        try:
                            literal = ast.literal_eval(value)
                        except (ValueError, TypeError, SyntaxError) as error:
                            raise ValueError(
                                "generated Literal contains an unresolved runtime value"
                            ) from error
                        if type(literal) not in (type(None), bool, int, str):
                            raise ValueError("generated Literal contains an unsupported value")
                    return ast.copy_location(
                        ast.Subscript(
                            value=self.visit(node.value),
                            slice=copy.deepcopy(node.slice),
                            ctx=ast.Load(),
                        ),
                        node,
                    )
                return self.generic_visit(node)

            def visit_Call(self, node):
                raise ValueError("generated type expressions cannot execute runtime calls")

        return ast.fix_missing_locations(Rewrite().visit(copy.deepcopy(expression)))

    def bind_class(self, reference, target, tree):
        """Bind documented class variables only to compatible existing pack variables."""

        def factory(node):
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            return value if isinstance(value, ast.Call) else None

        def constraints(call):
            return (
                [ast.unparse(arg) for arg in call.args[1:]],
                sorted((kw.arg, ast.unparse(kw.value)) for kw in call.keywords),
            )

        documented = {}
        for base in reference.nodes[0].bases:
            for name in (node.id for node in ast.walk(base) if isinstance(node, ast.Name)):
                ref = self.refs.resolve(reference.module, name)
                call = factory(ref.nodes[0]) if ref and len(ref.nodes) == 1 else None
                if (
                    call
                    and isinstance(call.func, ast.Name)
                    and self.refs.binding(ref.module, call.func.id)
                    in (("typing", "TypeVar"), ("typing_extensions", "TypeVar"))
                ):
                    documented[(ref.module, ref.name)] = call
        imports = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module in ("typing", "typing_extensions")
            for alias in node.names
            if alias.name == "TypeVar"
        }
        definitions = index_body(tree.body)
        published = {}
        for base in target.bases:
            for name in (node.id for node in ast.walk(base) if isinstance(node, ast.Name)):
                nodes = definitions.get(name, [])
                call = factory(nodes[0]) if len(nodes) == 1 else None
                if call and isinstance(call.func, ast.Name) and call.func.id in imports:
                    published[name] = call
        compatible = len(documented) == len(published) and all(
            constraints(left) == constraints(right)
            for left, right in zip(documented.values(), published.values())
        )
        self.class_bindings.update(
            {key: name for key, name in zip(documented, published)}
            if compatible
            else {key: None for key in documented}
        )

    def alias(self, key, name):
        if key not in self.bindings:
            safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
            candidate = f"_mpy_doc_{safe}_{hashlib.sha256(repr(key).encode()).hexdigest()[:10]}"
            while candidate in self.reserved:
                candidate += "_"
            self.reserved.add(candidate)
            self.bindings[key] = candidate
        return self.bindings[key]

    def imported(self, module, name):
        if module == "collections.abc" and name in _TYPING:
            return self.imported("typing", name)
        key = ("import", module, name)
        alias = self.alias(key, name)
        if module in ("typing", "typing_extensions"):
            if name not in _TYPING:
                raise _UnsafeReference(f"unsupported typing helper {module}.{name}")
            # The assembled support tree, when supplied, is the authority for syntax dependencies.
            actual = (
                "typing"
                if module == "typing_extensions" and "typing_extensions" not in self.available
                else module
            )
            if self.available and (
                actual not in self.available or name not in module_names(self.available[actual])
            ):
                raise _UnsafeReference(f"assembled support lacks {actual}.{name}")
            self.statements[key] = ast.ImportFrom(
                module=actual, names=[ast.alias(name=name, asname=alias)], level=0
            )
        elif module == "_typeshed" or module.startswith("_typeshed."):
            if module not in self.available or name not in module_names(self.available[module]):
                ref = self.refs.resolve(module, name)
                if ref is None:
                    raise _UnsafeReference(f"assembled support lacks {module}.{name}")
                return self.helper(ref)
            self.statements[key] = ast.ImportFrom(
                module=module, names=[ast.alias(name=name, asname=alias)], level=0
            )
        else:
            symbol = _symbol(self.surface, module, name)
            if symbol is None or symbol.get("kind") != "class":
                raise _UnsafeReference(f"reference needs absent runtime type {module}.{name}")
            if module == self.module:
                return name
            if self.available and (
                module not in self.available or name not in module_names(self.available[module])
            ):
                raise _UnsafeReference(f"assembled pack lacks selected type {module}.{name}")
            self.statements[key] = ast.ImportFrom(
                module=module, names=[ast.alias(name=name, asname=alias)], level=0
            )
        return alias

    def name(self, module, name):
        key = (module, name)
        if key in self.class_bindings:
            binding = self.class_bindings[key]
            if binding is None:
                raise _UnsafeReference(
                    f"reference class type parameter {module}.{name} has no compatible published class binding"
                )
            return binding
        binding = self.refs.binding(module, name)
        if binding and binding[0] == "collections.abc" and binding[1] in _TYPING:
            return self.imported("typing", binding[1])
        if binding:
            source, imported = binding
            if imported is None:
                raise _UnsafeReference(f"module {source} needs a qualified annotation member")
            if (
                source in ("typing", "typing_extensions")
                or source == "_typeshed"
                or source.startswith("_typeshed.")
            ):
                return self.imported(source, imported)
            ref = self.refs.resolve(source, imported)
            if ref:
                return self.helper(ref)
            return self.imported(source, imported)
        ref = self.refs.resolve(module, name)
        if ref:
            return self.helper(ref)
        if name in _BUILTINS:
            if (
                self.available
                and "builtins" in self.available
                and name not in module_names(self.available["builtins"])
            ):
                raise _UnsafeReference(f"assembled builtins lacks {name}")
            if name in self.reserved and _symbol(self.surface, self.module, name) is not None:
                return self.imported("builtins", name)
            return name
        if name in ("None", "True", "False"):
            return name
        raise _UnsafeReference(f"unresolved reference annotation name {module}.{name}")

    def expression(self, expression, module):
        if expression is None:
            return None
        dependencies = self

        class Rewrite(ast.NodeTransformer):
            def visit(self, node):
                if not isinstance(
                    node,
                    (
                        ast.Name,
                        ast.Attribute,
                        ast.Constant,
                        ast.Subscript,
                        ast.BinOp,
                        ast.BitOr,
                        ast.Tuple,
                        ast.List,
                        ast.Load,
                    ),
                ):
                    raise _UnsafeReference(f"unsupported annotation syntax {type(node).__name__}")
                return super().visit(node)

            def visit_Name(self, node):
                return ast.copy_location(
                    ast.Name(id=dependencies.name(module, node.id), ctx=node.ctx), node
                )

            def visit_Attribute(self, node):
                parts = []
                root = node
                while isinstance(root, ast.Attribute):
                    parts.insert(0, root.attr)
                    root = root.value
                if isinstance(root, ast.Name):
                    binding = dependencies.refs.binding(module, root.id)
                    if binding and binding[1] is None:
                        source = binding[0]
                        if len(parts) != 1:
                            raise _UnsafeReference(
                                "nested module annotation member is unsupported"
                            )
                        ref = dependencies.refs.resolve(source, parts[0])
                        alias = (
                            dependencies.helper(ref)
                            if ref
                            else dependencies.imported(source, parts[0])
                        )
                        return ast.copy_location(ast.Name(id=alias, ctx=ast.Load()), node)
                raise _UnsafeReference(
                    "attribute annotation has no qualified captured module binding"
                )

            def visit_Constant(self, node):
                if isinstance(node.value, str):
                    try:
                        parsed = ast.parse(node.value, mode="eval").body
                    except SyntaxError as error:
                        raise _UnsafeReference("invalid quoted annotation") from error
                    return ast.copy_location(
                        ast.Constant(value=ast.unparse(self.visit(parsed))), node
                    )
                return node

            def visit_Subscript(self, node):
                raw = ast.unparse(node.value).split(".")[-1]
                # Literal's strings are values, not forward references. Enum or
                # runtime names need independent identity evidence and are skipped.
                if raw == "Literal":
                    values = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                    for value in values:
                        try:
                            literal_value = ast.literal_eval(value)
                        except (ValueError, TypeError, SyntaxError) as error:
                            raise _UnsafeReference(
                                "Literal contains an unqualified runtime value"
                            ) from error
                        if type(literal_value) not in (type(None), bool, int, str):
                            raise _UnsafeReference("unsupported Literal value")
                    return ast.copy_location(
                        ast.Subscript(
                            value=self.visit(node.value),
                            slice=copy.deepcopy(node.slice),
                            ctx=ast.Load(),
                        ),
                        node,
                    )
                return self.generic_visit(node)

        return ast.fix_missing_locations(Rewrite().visit(copy.deepcopy(expression)))

    def helper(self, ref):
        runtime = self.runtime.get((ref.module, ref.name))
        if runtime:
            return self.imported(*runtime)
        key = ("helper", ref.module, ref.name)
        alias = self.alias(key, ref.name)
        if key in self.statements:
            return alias
        if len(ref.nodes) != 1:
            raise _UnsafeReference(f"ambiguous helper {ref.module}.{ref.name}")
        node = ref.nodes[0]
        # Reserve the binding before walking recursively, allowing recursive protocols.
        self.statements[key] = None
        self.provenance[key] = self.refs.provenance(ref)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None:
                raise _UnsafeReference("annotation-only reference is not a type helper")
            if isinstance(value, ast.Call):
                factory = ast.unparse(value.func).split(".")[-1]
                binding = self.refs.binding(ref.module, factory)
                if (
                    factory not in _TYPE_FACTORIES
                    or not binding
                    or binding[0] not in ("typing", "typing_extensions")
                ):
                    raise _UnsafeReference(f"reference value {ref.name} is not a typing factory")
                call = copy.deepcopy(value)
                call.func = self.expression(call.func, ref.module)
                if (
                    not call.args
                    or not isinstance(call.args[0], ast.Constant)
                    or not isinstance(call.args[0].value, str)
                ):
                    raise _UnsafeReference("type helper factory needs a literal name")
                call.args[0] = ast.Constant(value=alias)
                call.args[1:] = [self.expression(arg, ref.module) for arg in call.args[1:]]
                call.keywords = [
                    ast.keyword(arg=kw.arg, value=self.expression(kw.value, ref.module))
                    for kw in call.keywords
                ]
                value = call
            else:
                value = self.expression(value, ref.module)
            replacement = ast.Assign(targets=[ast.Name(id=alias, ctx=ast.Store())], value=value)
        elif isinstance(node, ast.ClassDef):
            bases = {
                ast.unparse(base.value if isinstance(base, ast.Subscript) else base).split(".")[-1]
                for base in node.bases
            }
            if not bases.intersection({"Protocol", "TypedDict"}):
                raise _UnsafeReference(
                    f"reference class {ref.module}.{ref.name} is not a selected runtime type or structural typing helper"
                )
            replacement = copy.deepcopy(node)
            replacement.name = alias
            replacement.bases = [self.expression(base, ref.module) for base in node.bases]
            replacement.decorator_list = []
            if any(
                kw.arg != "total"
                or not isinstance(kw.value, ast.Constant)
                or type(kw.value.value) is not bool
                for kw in node.keywords
            ):
                raise _UnsafeReference("typing helper class keyword is unsupported")
            members = []
            for member in node.body:
                if isinstance(member, _FUNCTION):
                    item = copy.deepcopy(member)
                    for arg, _, default in _parameters(item):
                        arg.annotation = self.expression(arg.annotation, ref.module)
                    item.returns = self.expression(item.returns, ref.module)
                    item.args.defaults = [ast.Constant(value=Ellipsis) for _ in item.args.defaults]
                    item.args.kw_defaults = [
                        ast.Constant(value=Ellipsis) if d is not None else None
                        for d in item.args.kw_defaults
                    ]
                    item.decorator_list = []
                    for decorator in member.decorator_list:
                        raw = ast.unparse(decorator)
                        if raw in ("property", "staticmethod", "classmethod"):
                            item.decorator_list.append(copy.deepcopy(decorator))
                        elif raw.split(".")[-1] == "overload":
                            item.decorator_list.append(self.expression(decorator, ref.module))
                        else:
                            raise _UnsafeReference("unsupported structural helper decorator")
                    item.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
                    members.append(item)
                elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                    item = copy.deepcopy(member)
                    item.annotation = self.expression(member.annotation, ref.module)
                    item.value = ast.Constant(value=Ellipsis) if member.value is not None else None
                    members.append(item)
                elif isinstance(member, (ast.Pass, ast.Expr)):
                    continue
                else:
                    raise _UnsafeReference("unsupported structural helper member")
            replacement.body = members or [ast.Expr(value=ast.Constant(value=Ellipsis))]
        else:
            raise _UnsafeReference("reference binding is not a type helper")
        self.statements.pop(key)
        self.statements[key] = ast.fix_missing_locations(replacement)
        return alias

    def emitted(self):
        imports = [
            node
            for key, node in self.statements.items()
            if key[0] in ("import", "source-import") and node is not None
        ]
        helpers = [
            node
            for key, node in self.statements.items()
            if key[0] in ("helper", "source-helper") and node is not None
        ]
        return imports + helpers


def _unknown(annotation):
    return (
        annotation is None
        or isinstance(annotation, ast.Name)
        and (annotation.id == "Any" or annotation.id.startswith("_mpy_Any"))
    )


def _doc_unknown(node, refs, module, seen=frozenset()):
    """Whether the whole reference annotation is Any, including aliases/unions."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _doc_unknown(ast.parse(node.value, mode="eval").body, refs, module, seen)
        except SyntaxError:
            return False
    if isinstance(node, ast.Name):
        key = (module, node.id)
        if key in seen:
            return False
        binding = refs.binding(module, node.id)
        if binding in (("typing", "Any"), ("typing_extensions", "Any")):
            return True
        ref = refs.resolve(module, node.id)
        if ref and len(ref.nodes) == 1 and isinstance(ref.nodes[0], (ast.Assign, ast.AnnAssign)):
            return _doc_unknown(ref.nodes[0].value, refs, ref.module, seen | {key})
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.attr == "Any" and refs.binding(module, node.value.id) in (
            ("typing", None),
            ("typing_extensions", None),
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _doc_unknown(node.left, refs, module, seen) or _doc_unknown(
            node.right, refs, module, seen
        )
    if isinstance(node, ast.Subscript):
        head = ast.unparse(node.value).split(".")[-1]
        if head in ("Union", "Optional"):
            parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return any(_doc_unknown(part, refs, module, seen) for part in parts)
    return False


def _call_key(node):
    return (
        _descriptor(node),
        [(arg.arg, kind, default is None) for arg, kind, default in _parameters(node)],
    )


def _doc_descriptor(node, refs, module):
    """Closed atom/union descriptor; generics retain their separately checked head."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        if node.value is None:
            return {"kind": "None"}
        if isinstance(node.value, str):
            try:
                return _doc_descriptor(ast.parse(node.value, mode="eval").body, refs, module)
            except SyntaxError:
                return None
    if isinstance(node, ast.Name):
        if refs.binding(module, node.id) or refs.resolve(module, node.id):
            return None
        return {"kind": node.id} if node.id in _BUILTINS else None
    if isinstance(node, ast.Subscript):
        head = ast.unparse(node.value).split(".")[-1]
        head = {
            "List": "list",
            "Dict": "dict",
            "Tuple": "tuple",
            "Set": "set",
            "FrozenSet": "frozenset",
            "Type": "type",
        }.get(head, head)
        if head in _BUILTINS:
            return {"kind": head}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        parts = [
            _doc_descriptor(node.left, refs, module),
            _doc_descriptor(node.right, refs, module),
        ]
        if all(parts):
            from .vocabulary import union_descriptor

            return union_descriptor(parts)
    return None


def _shape(node, symbol, in_class, *, constructor=False, overload=False):
    """Check documented calls against independently selected argument constraints."""
    shape = symbol.get("callshape") or {}
    descriptor = "function" if constructor else shape.get("descriptor", "function")
    documented_descriptor = _descriptor(node)
    if documented_descriptor != descriptor:
        return False, "reference binding/decorator contradicts selected callable"
    receiver = in_class and descriptor != "staticmethod"
    rows = _parameters(node, receiver=receiver)
    if receiver and not node.args.posonlyargs and not node.args.args:
        return False, "reference method has no receiver"
    parameters = shape.get("parameters")
    if isinstance(parameters, list) and _status(shape.get("parameters_resolution")) in _RESOLVED:
        if len(parameters) != len(rows):
            omitted_optional = (
                overload
                and len(rows) < len(parameters)
                and all(
                    not row.get("required", True)
                    and row.get("kind")
                    in ("positional_only", "positional_or_keyword", "keyword_only")
                    for row in parameters[len(rows) :]
                )
            )
            if not omitted_optional:
                return False, "reference parameter count contradicts selected parser"
        for parameter, (arg, kind, default) in zip(parameters, rows):
            selected_kind = parameter.get("kind")
            if selected_kind == "positional_only" and kind == "positional_or_keyword":
                kind = "positional_only"
            if selected_kind != kind:
                return False, "reference parameter kind contradicts selected parser"
            if selected_kind != "positional_only" and parameter.get("name") != arg.arg:
                return False, "reference keyword name contradicts selected parser"
            if kind not in ("var_positional", "var_keyword"):
                required = default is None
                if required != parameter.get("required", True) and not overload:
                    return False, "reference required/default presence contradicts selected parser"
                if parameter.get("required", True) and not required:
                    return False, "reference overload makes a selected required parameter optional"
        return True, "reference corresponds to selected parser fields"
    offset = shape.get("implicit_argument_offset", 0 if constructor else (1 if in_class else 0))
    minimum, maximum = shape.get("minimum"), shape.get("maximum")
    if maximum == _ARGS_MAX:
        maximum = None
    positional = [row for row in rows if row[1] in ("positional_only", "positional_or_keyword")]
    doc_min = sum(
        default is None and (kind == "positional_only" or shape.get("keywords") is not True)
        for _, kind, default in positional
    )
    doc_max = None if any(kind == "var_positional" for _, kind, _ in rows) else len(positional)
    if type(offset) is not int:
        return False, "source binding offset is unresolved"
    if type(minimum) is int and doc_min < minimum - offset:
        return False, "reference minimum arity contradicts selected wrapper"
    if type(maximum) is int and (doc_max is None or doc_max > maximum - offset):
        return False, "reference maximum arity contradicts selected wrapper"
    if shape.get("keywords") is False and any(
        kind in ("keyword_only", "var_keyword") for _, kind, _ in rows
    ):
        return False, "reference requires keywords excluded by selected wrapper"
    return True, "reference fills unresolved argument correspondence within selected bounds"


class _Application:
    def __init__(self, declarations, bundle, surface, available, generated_declarations):
        self.declarations, self.bundle, self.surface = declarations, bundle, surface
        self.refs = _References(bundle)
        self.available = {
            _module_path(path): ast.parse(text) for path, text in (available or {}).items()
        }
        self.records = []
        self.symbols = {}
        self.source_occurrence = None
        self.used = set()
        self.generated = {
            _module_path(path): ast.parse(text) for path, text in generated_declarations.items()
        }

    def record(self, ref, field, outcome, reason, symbol=None, before=None, after=None, node=None):
        document = self.refs.provenance(ref, node)
        self.used.add((document["reference"], document["qualname"], document["span"]["line"]))
        source_ref = None
        if symbol is not None:
            module, qualname = self.source_occurrence
            source_ref = f"{module}:{qualname}"
            if source_ref not in self.symbols:
                self.symbols[source_ref] = {
                    "module": module,
                    "qualname": qualname,
                    "identity": copy.deepcopy(symbol.get("identity")),
                    "source": copy.deepcopy(symbol.get("source")),
                    "resolution": copy.deepcopy(symbol.get("resolution")),
                }
        self.records.append(
            {
                "document": document,
                "field": field,
                "outcome": outcome,
                "reason": reason,
                "source": source_ref,
                "before": before,
                "after": after,
            }
        )

    def documentation(self, target, ref, symbol):
        reference = ref.nodes[0]
        text = ast.get_docstring(reference, clean=False)
        if text is None:
            return None
        before = (
            ast.get_docstring(target, clean=False)
            if isinstance(target, (ast.Module, ast.ClassDef, *_FUNCTION))
            else None
        )

        def identity(value):
            if value is None:
                return None
            raw = value.encode("utf-8")
            return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

        self.record(
            ref,
            "documentation",
            "confirmed" if text == before else "applied",
            "documentation-derived hover text",
            symbol,
            identity(before),
            identity(text),
            node=reference.body[0],
        )
        return ast.Expr(value=ast.Constant(value=text))

    def documentation_edit(self, target, ref, symbol, edits):
        statement = self.documentation(target, ref, symbol)
        if statement is None:
            return
        first = target.body[0]
        has_docstring = ast.get_docstring(target, clean=False) is not None
        start = start_line(first)
        end = first.end_lineno if has_docstring else start - 1
        indent = 0 if isinstance(target, ast.Module) else target.col_offset + 4
        block = " " * indent + ast.unparse(ast.fix_missing_locations(statement))
        edits.append((start, end, block))

    def annotation(
        self,
        original,
        documented,
        resolution,
        contract,
        ref,
        field,
        symbol,
        dependencies,
        *,
        source_annotation=False,
    ):
        def retained():
            return (
                dependencies.source_expression(original)
                if source_annotation
                else copy.deepcopy(original)
            )

        def evidence():
            self.records[-1]["source_field"] = {
                "resolution": copy.deepcopy(resolution),
                "normalized": _semantic_descriptor(contract),
            }

        before = ast.unparse(original) if original is not None else None
        after = ast.unparse(documented) if documented is not None else None
        if documented is None:
            self.record(
                ref, field, "skipped", "reference annotation omitted", symbol, before, before
            )
            evidence()
            return retained()
        descriptor = _semantic_descriptor(contract)
        try:
            unknown = _doc_unknown(documented, self.refs, ref.module)
            doc_descriptor = (
                _doc_descriptor(documented, self.refs, ref.module)
                if _status(resolution) in _RESOLVED and descriptor is not None
                else None
            )
        except _UnsafeReference as error:
            self.record(ref, field, "skipped", str(error), symbol, before, before)
            evidence()
            return retained()
        if unknown:
            self.record(
                ref,
                field,
                "skipped",
                "reference annotation is Any or an alias/union containing Any",
                symbol,
                before,
                before,
            )
            evidence()
            return retained()
        if _status(resolution) in _RESOLVED and descriptor is not None:
            if descriptor != doc_descriptor:
                self.record(
                    ref,
                    field,
                    "conflict",
                    "selected source field retains precedence over documentation",
                    symbol,
                    before,
                    before,
                )
                evidence()
                return retained()
            if not isinstance(documented, ast.Subscript) or not isinstance(original, ast.Name):
                self.record(
                    ref,
                    field,
                    "confirmed",
                    "reference agrees with selected source type",
                    symbol,
                    before,
                    before,
                )
                evidence()
                return retained()
        trial = dependencies.fork()
        try:
            result = trial.expression(documented, ref.module)
        except _UnsafeReference as error:
            self.record(ref, field, "skipped", str(error), symbol, before, before)
            evidence()
            return retained()
        dependencies.__dict__.update(trial.__dict__)
        if before == ast.unparse(result):
            self.record(
                ref,
                field,
                "confirmed",
                "declaration already matches reference",
                symbol,
                before,
                before,
            )
        else:
            self.record(
                ref,
                field,
                "applied",
                "documentation-derived field; not compiler proof",
                symbol,
                before,
                after,
            )
        evidence()
        return result

    def functions(self, target, generated, ref, symbol, dependencies, in_class, constructor=False):
        targets = target
        target = targets[0]
        nodes = ref.nodes
        if not all(isinstance(node, ast.FunctionDef) for node in nodes):
            self.record(
                ref,
                "declaration",
                "conflict",
                "reference declaration kind contradicts selected callable",
                symbol,
            )
            return None
        overloaded = len(nodes) > 1
        for node in nodes:
            for decorator in node.decorator_list:
                raw = ast.unparse(decorator)
                if raw in ("staticmethod", "classmethod", "property"):
                    if self.refs.binding(ref.module, raw) or self.refs.resolve(ref.module, raw):
                        self.record(
                            ref,
                            "declaration",
                            "skipped",
                            "reference decorator shadows a builtin descriptor",
                            symbol,
                            node=node,
                        )
                        return None
                elif isinstance(decorator, ast.Name):
                    binding = self.refs.binding(ref.module, decorator.id)
                    if binding not in (("typing", "overload"), ("typing_extensions", "overload")):
                        self.record(
                            ref,
                            "declaration",
                            "skipped",
                            "unqualified reference overload decorator",
                            symbol,
                            node=node,
                        )
                        return None
                elif isinstance(decorator, ast.Attribute) and isinstance(
                    decorator.value, ast.Name
                ):
                    binding = self.refs.binding(ref.module, decorator.value.id)
                    if decorator.attr != "overload" or binding not in (
                        ("typing", None),
                        ("typing_extensions", None),
                    ):
                        self.record(
                            ref,
                            "declaration",
                            "skipped",
                            "unqualified reference decorator",
                            symbol,
                            node=node,
                        )
                        return None
                else:
                    self.record(
                        ref,
                        "declaration",
                        "skipped",
                        "unsupported reference decorator expression",
                        symbol,
                        node=node,
                    )
                    return None
            decorators = {ast.unparse(d).split(".")[-1] for d in node.decorator_list}
            if decorators - {"overload", "staticmethod", "classmethod", "property"}:
                self.record(
                    ref,
                    "declaration",
                    "skipped",
                    "unsupported reference decorator",
                    symbol,
                    node=node,
                )
                return None
            if overloaded and "overload" not in decorators:
                self.record(
                    ref,
                    "declaration",
                    "skipped",
                    "ambiguous non-overload reference declarations",
                    symbol,
                    node=node,
                )
                return None
        if len(targets) > 1 and not overloaded:
            self.record(
                ref,
                "callshape",
                "preserved",
                "a single reference cannot replace existing overload call contracts",
                symbol,
            )
            results = []
            for current in targets:
                result = copy.deepcopy(current)
                documentation = self.documentation(current, ref, symbol)
                if documentation is not None:
                    result.body = [documentation, ast.Expr(value=ast.Constant(value=Ellipsis))]
                results.append(ast.fix_missing_locations(result))
            return results
        compatible = [
            _shape(node, symbol, in_class, constructor=constructor, overload=overloaded)
            for node in nodes
        ]
        shape_ok = all(ok for ok, _ in compatible)
        shape = symbol.get("callshape") or {}
        parser_proven = (
            isinstance(shape.get("parameters"), list)
            and _status(shape.get("parameters_resolution")) in _RESOLVED
        )
        if overloaded and shape_ok and parser_proven:
            intervals = []
            for node in nodes:
                rows = _parameters(node, receiver=in_class and _descriptor(node) != "staticmethod")
                positional = [
                    row for row in rows if row[1] in ("positional_only", "positional_or_keyword")
                ]
                intervals.append(
                    (
                        sum(default is None for _, _, default in positional),
                        float("inf")
                        if any(kind == "var_positional" for _, kind, _ in rows)
                        else len(positional),
                    )
                )
            selected = shape["parameters"]
            positional = [
                row
                for row in selected
                if row.get("kind") in ("positional_only", "positional_or_keyword")
            ]
            low = sum(row.get("required", True) for row in positional)
            high = (
                None
                if any(row.get("kind") == "var_positional" for row in selected)
                else len(positional)
            )
            covered = low - 1
            for start, end in sorted(intervals):
                if start > covered + 1:
                    break
                covered = max(covered, end)
            if (high is not None and covered < high) or (high is None and covered != float("inf")):
                shape_ok = False
                compatible = [
                    (False, "reference overloads do not cover selected parser arity bounds")
                ]
        outcome = "applied" if shape_ok else "conflict"
        self.record(
            ref,
            "callshape",
            outcome,
            "; ".join(dict.fromkeys(reason for _, reason in compatible)),
            symbol,
        )
        shape_record = self.records[-1]
        if overloaded and not shape_ok:
            return None
        results = []
        pairs = (
            [(target, node) for node in nodes]
            if shape_ok
            else [(item, nodes[0]) for item in targets]
        )
        for target_node, node in pairs:
            node_ref = _Ref(ref.module, ref.name, (node,))
            if shape_ok:
                result = copy.deepcopy(node)
                result.name = target.name
                existing_doc = ast.get_docstring(target_node, clean=False)
                result.body = (
                    [ast.Expr(value=ast.Constant(value=existing_doc))]
                    if existing_doc is not None
                    else []
                ) + [ast.Expr(value=ast.Constant(value=Ellipsis))]
                result.decorator_list = copy.deepcopy(generated.decorator_list)
                if overloaded:
                    result.decorator_list.insert(
                        0, ast.Name(id=dependencies.imported("typing", "overload"), ctx=ast.Load())
                    )
            else:
                result = copy.deepcopy(target_node)
            shape = symbol.get("callshape") or {}
            receiver = in_class and _descriptor(generated) != "staticmethod"
            doc_rows = _parameters(node, receiver=receiver)
            result_rows = _parameters(result, receiver=receiver)
            source_rows = _parameters(generated, receiver=receiver)
            target_rows = _parameters(target_node, receiver=receiver)
            parameters = shape.get("parameters")
            proven_parameters = (
                isinstance(parameters, list)
                and _status(shape.get("parameters_resolution")) in _RESOLVED
            )
            if shape_ok and shape.get("keywords") is False:
                result.args.posonlyargs += result.args.args
                result.args.args = []
            if shape_ok:
                for index, (argument, _, default) in enumerate(result_rows):
                    selected = (
                        parameters[index] if proven_parameters and index < len(parameters) else {}
                    )
                    authoritative = (
                        source_rows[index][0]
                        if proven_parameters and index < len(source_rows)
                        else None
                    )
                    if authoritative is not None:
                        argument.arg = authoritative.arg
                    original = authoritative.annotation if authoritative is not None else None
                    source_annotation = authoritative is not None
                    type_proven = (
                        _status(selected.get("type_resolution")) in _RESOLVED
                        and _semantic_descriptor(selected.get("type_contract")) is not None
                    )
                    if not type_proven and len(target_rows) == len(result_rows):
                        original = target_rows[index][0].annotation
                        source_annotation = False
                    if (
                        overloaded
                        and type_proven
                        and _semantic_descriptor(selected.get("type_contract"))
                        != _doc_descriptor(doc_rows[index][0].annotation, self.refs, ref.module)
                    ):
                        raise _UnsafeReference(
                            "overload parameter refinement contradicts a selected source field"
                        )
                    argument.annotation = self.annotation(
                        original,
                        doc_rows[index][0].annotation,
                        selected.get("type_resolution"),
                        selected.get("type_contract"),
                        node_ref,
                        f"parameter:{index}:{argument.arg}",
                        symbol,
                        dependencies,
                        source_annotation=source_annotation,
                    )
                    if (
                        overloaded
                        and doc_rows[index][0].annotation is not None
                        and not _doc_unknown(doc_rows[index][0].annotation, self.refs, ref.module)
                        and dependencies.unknown(argument.annotation)
                    ):
                        raise _UnsafeReference(
                            "overload parameter dependency could not be represented"
                        )
                    if argument.annotation is None:
                        argument.annotation = (
                            dependencies.source_expression(source_rows[index][0].annotation)
                            if index < len(source_rows)
                            else None
                        )
                if receiver:
                    first = (result.args.posonlyargs + result.args.args)[0]
                    source_first = (generated.args.posonlyargs + generated.args.args)[0]
                    target_positional = target_node.args.posonlyargs + target_node.args.args
                    target_annotation = (
                        target_positional[0].annotation if target_positional else None
                    )
                    first.arg = source_first.arg
                    first.annotation = (
                        copy.deepcopy(target_annotation)
                        if dependencies.source_unknown(source_first.annotation)
                        else dependencies.source_expression(source_first.annotation)
                    )
                # Source-known default values and parameter kinds are independent of type precision.
                if proven_parameters and not overloaded:
                    source_args = copy.deepcopy(generated.args)
                    annotations = [arg.annotation for arg, _, _ in _parameters(result)]
                    for (arg, _, _), annotation in zip(
                        _parameters(
                            ast.FunctionDef(name="_", args=source_args, body=[], decorator_list=[])
                        ),
                        annotations,
                    ):
                        arg.annotation = annotation
                    result.args = source_args
                elif proven_parameters:
                    positional = result.args.posonlyargs + result.args.args
                    count = int(receiver and bool(generated.args.posonlyargs)) + sum(
                        kind == "positional_only" for _, kind, _ in source_rows[: len(result_rows)]
                    )
                    result.args.posonlyargs = positional[:count]
                    result.args.args = positional[count:]
                self.defaults(
                    result,
                    node,
                    parameters if proven_parameters else [],
                    node_ref,
                    symbol,
                    receiver,
                    target_node,
                    source_rows,
                )
            semantic = _source_return(symbol)
            original_return = (
                generated.returns if semantic["normalized"] is not None else target_node.returns
            )
            result.returns = self.annotation(
                original_return,
                node.returns,
                semantic["resolution"],
                {"kind": "union", "alternatives": semantic["value"]},
                node_ref,
                "returns",
                symbol,
                dependencies,
                source_annotation=semantic["normalized"] is not None,
            )
            if constructor:
                result.returns = ast.Constant(value=None)
            documentation = self.documentation(target_node, node_ref, symbol)
            if documentation is not None:
                result.body = [documentation, ast.Expr(value=ast.Constant(value=Ellipsis))]
            results.append(ast.fix_missing_locations(result))
        if shape_ok and [_call_key(node) for node in results] == [
            _call_key(node) for node in targets
        ]:
            shape_record["outcome"] = "confirmed"
        return results

    def defaults(self, result, documented, parameters, ref, symbol, receiver, target, source_rows):
        rows = _parameters(result, receiver=receiver)
        docs = _parameters(documented, receiver=receiver)
        target_rows = _parameters(target, receiver=receiver)
        replacements = {}
        for index, ((arg, kind, default), (_, _, candidate)) in enumerate(zip(rows, docs)):
            if default is None or candidate is None:
                continue
            parameter = parameters[index] if index < len(parameters) else {}
            source_default = _source_default(parameter.get("default_contract"))
            if source_default is not None:
                from .enrichment import _default

                documented_default = _default(candidate)
                outcome = (
                    "skipped"
                    if documented_default["normalized"] is None
                    else "confirmed"
                    if documented_default["normalized"] == source_default
                    else "conflict"
                )
                replacement = source_rows[index][2] if index < len(source_rows) else None
                if replacement is None:
                    raise ValueError("source-qualified default has no generated declaration value")
                replacements[id(default)] = copy.deepcopy(replacement)
                self.record(
                    ref,
                    f"default:{index}:{arg.arg}",
                    outcome,
                    "selected source default retains precedence",
                    symbol,
                    ast.unparse(replacement),
                    ast.unparse(replacement),
                )
                continue
            # Unknown documented values cannot erase existing template defaults.
            if isinstance(candidate, ast.Constant) and type(candidate.value) in (
                type(None),
                bool,
                int,
                float,
                str,
                bytes,
            ):
                replacement = copy.deepcopy(candidate)
            elif (
                isinstance(candidate, ast.UnaryOp)
                and isinstance(candidate.op, (ast.USub, ast.UAdd))
                and isinstance(candidate.operand, ast.Constant)
                and type(candidate.operand.value) in (int, float)
            ):
                replacement = copy.deepcopy(candidate)
            else:
                fallback = target_rows[index][2] if len(target_rows) == len(rows) else None
                replacement = (
                    copy.deepcopy(fallback)
                    if fallback is not None
                    else ast.Constant(value=Ellipsis)
                )
                replacements[id(default)] = replacement
                self.record(
                    ref,
                    f"default:{index}:{arg.arg}",
                    "skipped",
                    "reference default value is not independently representable",
                    symbol,
                    ast.unparse(replacement),
                    ast.unparse(replacement),
                )
                continue
            previous = target_rows[index][2] if len(target_rows) == len(rows) else None
            before = ast.unparse(previous) if previous is not None else None
            replacements[id(default)] = replacement
            self.record(
                ref,
                f"default:{index}:{arg.arg}",
                "confirmed" if before == ast.unparse(replacement) else "applied",
                "documentation-derived optional default",
                symbol,
                before,
                ast.unparse(replacement),
            )
        result.args.defaults = [
            replacements.get(id(value), value) for value in result.args.defaults
        ]
        result.args.kw_defaults = [
            replacements.get(id(value), value) for value in result.args.kw_defaults
        ]

    def module(self, path, text):
        module = _module_path(path)
        if module not in self.generated or module not in self.refs.trees:
            return text
        tree = ast.parse(text)
        dependencies = _Dependencies(
            self.refs,
            module,
            self.surface,
            self.available,
            module_names(tree),
            self.generated[module],
            tree,
        )
        edits = []
        selected_modules = [
            row
            for row in self.surface.get("modules", [])
            if row.get("name") == module and _available(row)
        ]
        if (
            len(selected_modules) == 1
            and ast.get_docstring(self.refs.trees[module], clean=False) is not None
        ):
            self.source_occurrence = (module, "<module>")
            self.documentation_edit(
                tree,
                _Ref(module, "<module>", (self.refs.trees[module],)),
                selected_modules[0],
                edits,
            )

        def visit(body, generated_body, prefix="", owner=None):
            source_index = index_body(generated_body)
            for name, targets in index_body(body).items():
                qualname = prefix + name
                self.source_occurrence = (module, qualname)
                symbol = _symbol(self.surface, module, qualname)
                constructor = False
                if (
                    symbol is None
                    and owner
                    and name in ("__init__", "__new__")
                    and owner.get("constructor", {}).get("callable") is True
                ):
                    constructor = True
                    symbol = copy.deepcopy(owner["constructor"])
                    symbol["resolution"] = {"constructor": symbol.get("resolution", "unresolved")}
                    symbol["identity"] = {"constructor_of": copy.deepcopy(owner.get("identity"))}
                    symbol["callshape"] = {**symbol.get("callshape", {}), "descriptor": "function"}
                    symbol["semantics"] = {
                        "return_resolution": "resolved",
                        "returns": [{"kind": "None"}],
                    }
                if symbol is None or name == "__getattr__":
                    continue
                ref = None
                try:
                    parts = qualname.split(".")
                    ref = self.refs.resolve(module, parts[0])
                    for part in parts[1:]:
                        ref = self.refs.member(ref, part) if ref else None
                except _UnsafeReference as error:
                    if ref is not None:
                        self.record(ref, "declaration", "skipped", str(error), symbol)
                    continue
                if ref is None:
                    continue
                generated_nodes = source_index.get(name, [])
                if not generated_nodes:
                    continue
                target, generated = targets[0], generated_nodes[0]
                if isinstance(target, ast.ClassDef) and isinstance(generated, ast.ClassDef):
                    if len(ref.nodes) != 1 or not isinstance(ref.nodes[0], ast.ClassDef):
                        self.record(
                            ref,
                            "declaration",
                            "conflict",
                            "reference class kind contradicts selected declaration",
                            symbol,
                        )
                        continue
                    self.documentation_edit(target, ref, symbol, edits)
                    self.record(
                        ref,
                        "bases",
                        "preserved",
                        "reference cannot change selected inheritance or members",
                        symbol,
                        [ast.unparse(b) for b in generated.bases],
                        [ast.unparse(b) for b in generated.bases],
                    )
                    enclosing_bindings = dict(dependencies.class_bindings)
                    try:
                        dependencies.bind_class(ref, target, tree)
                    except _UnsafeReference as error:
                        self.record(ref, "generic-bindings", "skipped", str(error), symbol)
                        dependencies.class_bindings = enclosing_bindings
                        continue
                    visit(target.body, generated.body, qualname + ".", symbol)
                    dependencies.class_bindings = enclosing_bindings
                elif isinstance(target, _FUNCTION) and isinstance(generated, _FUNCTION):
                    trial = dependencies.fork()
                    record_start = len(self.records)
                    try:
                        replacement = self.functions(
                            targets, generated, ref, symbol, trial, bool(prefix), constructor
                        )
                    except _UnsafeReference as error:
                        for row in self.records[record_start:]:
                            if row["outcome"] == "applied":
                                row.update(
                                    outcome="skipped",
                                    reason=f"declaration not installed: {error}",
                                    after=row["before"],
                                )
                        self.record(ref, "declaration", "skipped", str(error), symbol)
                        continue
                    if replacement:
                        dependencies.__dict__.update(trial.__dict__)
                        indent = " " * target.col_offset
                        block = "\n\n".join(ast.unparse(node) for node in replacement)
                        block = "\n".join(
                            indent + line if line else line for line in block.splitlines()
                        )
                        edits.append(
                            (
                                min(start_line(node) for node in targets),
                                max(node.end_lineno for node in targets),
                                block,
                            )
                        )
                elif isinstance(target, ast.AnnAssign) and isinstance(generated, ast.AnnAssign):
                    if len(ref.nodes) == 1 and isinstance(ref.nodes[0], ast.AnnAssign):
                        resolution = symbol.get("resolution", {}).get("types")
                        contract = symbol.get("value_contract")
                        original = (
                            generated.annotation
                            if _status(resolution) in _RESOLVED
                            and _semantic_descriptor(contract) is not None
                            else target.annotation
                        )
                        annotation = self.annotation(
                            original,
                            ref.nodes[0].annotation,
                            symbol.get("resolution", {}).get("types"),
                            symbol.get("value_contract"),
                            ref,
                            "annotation",
                            symbol,
                            dependencies,
                            source_annotation=original is generated.annotation,
                        )
                        replacement = copy.deepcopy(target)
                        replacement.annotation = annotation
                        edits.append(
                            (
                                target.lineno,
                                target.end_lineno,
                                " " * target.col_offset + ast.unparse(replacement),
                            )
                        )
                    elif (
                        bool(prefix)
                        and len(ref.nodes) == 1
                        and isinstance(ref.nodes[0], ast.FunctionDef)
                        and _descriptor(ref.nodes[0]) == "property"
                        and _unknown(generated.annotation)
                    ):
                        documented = ref.nodes[0]
                        if len(_parameters(documented)) != 1:
                            self.record(
                                ref,
                                "declaration",
                                "conflict",
                                "property getter must only accept its receiver",
                                symbol,
                            )
                            continue
                        annotation = self.annotation(
                            generated.annotation,
                            documented.returns,
                            symbol.get("resolution", {}).get("types"),
                            symbol.get("value_contract"),
                            ref,
                            "property",
                            symbol,
                            dependencies,
                            source_annotation=True,
                        )
                        documentation = self.documentation(target, ref, symbol)
                        if not dependencies.unknown(annotation) or documentation is not None:
                            replacement = copy.deepcopy(documented)
                            replacement.args = ast.arguments(
                                posonlyargs=[],
                                args=[ast.arg(arg="self")],
                                vararg=None,
                                kwonlyargs=[],
                                kw_defaults=[],
                                kwarg=None,
                                defaults=[],
                            )
                            replacement.returns = annotation
                            replacement.decorator_list = [ast.Name(id="property", ctx=ast.Load())]
                            replacement.body = (
                                [documentation] if documentation is not None else []
                            ) + [ast.Expr(value=ast.Constant(value=Ellipsis))]
                            block = ast.unparse(ast.fix_missing_locations(replacement))
                            edits.append(
                                (
                                    target.lineno,
                                    target.end_lineno,
                                    "\n".join(
                                        " " * target.col_offset + line
                                        for line in block.splitlines()
                                    ),
                                )
                            )

        visit(tree.body, self.generated[module].body)
        lines = text.splitlines()
        for start, end, block in sorted(edits, reverse=True):
            lines[start - 1 : end] = block.splitlines()
        helper_nodes = dependencies.emitted()
        if helper_nodes:
            rewritten = ast.parse("\n".join(lines))
            offset = (
                rewritten.body[0].end_lineno
                if ast.get_docstring(rewritten, clean=False) is not None
                else 0
            )
            for node in _leading_imports(rewritten):
                offset = max(offset, node.end_lineno)
            lines[offset:offset] = (
                ["# Private annotation dependencies for enriched declarations."]
                + [ast.unparse(ast.fix_missing_locations(node)) for node in helper_nodes]
                + [""]
            )
            for key, document in dependencies.provenance.items():
                self.used.add(
                    (document["reference"], document["qualname"], document["span"]["line"])
                )
                self.records.append(
                    {
                        "document": document,
                        "field": "type-helper",
                        "outcome": "applied",
                        "reason": "private annotation dependency, not a runtime export",
                        "source": None,
                        "before": None,
                        "after": dependencies.bindings[key],
                    }
                )
        return "\n".join(lines) + "\n"

    def run(self):
        result = {path: self.module(path, text) for path, text in self.declarations.items()}
        omitted = {}
        for candidate in self.bundle["candidates"]:
            key = (candidate["file"], candidate["qualname"], candidate["span"]["line"])
            if key not in self.used:
                row = omitted.setdefault(
                    candidate["module"],
                    {
                        "reference": candidate["file"],
                        "count": 0,
                        "kinds": Counter(),
                        "reason": "not applied to a compatible selected declaration or required helper",
                    },
                )
                row["count"] += 1
                row["kinds"][candidate["kind"]] += 1
        self.records.sort(
            key=lambda row: (
                row["document"]["reference"],
                row["document"]["qualname"],
                row["document"]["span"]["line"],
                row["field"],
            )
        )
        summary = Counter(row["outcome"] for row in self.records)
        summary["omitted"] = sum(row["count"] for row in omitted.values())
        references = {}
        for row in self.refs.rows.values():
            references[row["path"]] = {
                key: copy.deepcopy(row[key])
                for key in ("module", "bytes", "sha256", "source_ids", "license_ids")
            }
            if row.get("symbol_lineage"):
                references[row["path"]]["symbol_lineage"] = {
                    symbol["qualname"]: {
                        key: list(symbol[key]) for key in ("source_ids", "license_ids")
                    }
                    for symbol in row["symbol_lineage"]
                }
        return result, {
            "schema_version": 1,
            "authority": "documentation-derived fields; source facts unchanged",
            "lineage": {
                key: {row["id"]: copy.deepcopy(row) for row in self.bundle["provenance"][key]}
                for key in ("sources", "licenses")
            },
            "references": dict(sorted(references.items())),
            "symbols": dict(sorted(self.symbols.items())),
            "omissions": dict(sorted(omitted.items())),
            "summary": dict(sorted(summary.items())),
            "records": self.records,
        }


def enrich_declarations(
    declarations: dict[str, str],
    bundle: dict,
    surface: dict,
    *,
    generated_declarations: dict[str, str],
    available_declarations: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict]:
    """Enrich selected declarations with captured, attributed documentation fields.

    ``available_declarations`` is the assembled pack's text mapping, used to check
    support imports against actual published names. Apply after template assembly
    so a template cannot overwrite a documentation-derived field.
    ``generated_declarations`` supplies the original renderer output, before pack
    assembly, so application neither rerenders nor copies the source surface.
    Only input keys are returned; neither source facts nor the bundle is modified.
    """
    return _Application(
        declarations, bundle, surface, available_declarations, generated_declarations
    ).run()
