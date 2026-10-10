"""Inspect pinned reference declarations without granting them source authority."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import keyword
import re
from collections import Counter
from pathlib import Path, PurePosixPath


_RESOLVED = {"resolved", "bounded-resolved", "source-resolved", "selected-source"}
_ROLES = {"documentation", "runtime-observation", "doc-enriched", "released-reference"}
_ATOMS = {"None", "bool", "int", "str", "bytes", "float", "complex", "object",
          "list", "dict", "set", "tuple"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-fA-F]{40,64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def _stable(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _status(value):
    return value.get("status", "unresolved") if isinstance(value, dict) else value


def _fail(message):
    raise ValueError(f"enrichment: {message}")


def _object(value, required, optional=(), *, label):
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        _fail(f"{label} keys missing={sorted(missing)} unsupported={sorted(extra)}")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a nonempty string")
    return value


def _list(value, label, *, nonempty=False):
    if not isinstance(value, list) or nonempty and not value:
        _fail(f"{label} must be {'a nonempty' if nonempty else 'a'} list")
    return value


def _identifier(value, label):
    _text(value, label)
    if not _ID.fullmatch(value):
        _fail(f"unsafe {label}: {value!r}")
    return value


def _qualified(value, label):
    _text(value, label)
    if not all(part.isidentifier() and not keyword.iskeyword(part) for part in value.split(".")):
        _fail(f"invalid {label}: {value!r}")
    return value


def _relative(value, label):
    _text(value, label)
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or "\x00" in value or
            any(part in ("", ".", "..") for part in value.split("/"))):
        _fail(f"unsafe {label}: {value!r}")
    return path


def _physical(path, *, directory=False):
    path = Path(path).absolute()
    for member in (path, *path.parents):
        if member.is_symlink():
            _fail(f"symlink indirection: {member}")
    if not (path.is_dir() if directory else path.is_file()):
        _fail(f"missing or nonregular {'directory' if directory else 'file'}: {path}")
    return path.resolve(strict=True)


def _pin(value, label):
    _object(value, ("path", "bytes", "sha256"), label=label)
    if type(value["bytes"]) is not int or value["bytes"] < 0:
        _fail(f"{label}.bytes must be a nonnegative integer")
    if not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"]):
        _fail(f"{label}.sha256 must be a lowercase SHA256")


def _stamp(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _capture(path, snapshots, pin=None):
    lexical = str(Path(path).absolute())
    physical = _physical(path)
    before = _stamp(physical.stat())
    raw = physical.read_bytes()
    after = _stamp(physical.stat())
    if before != after or _physical(path) != physical:
        _fail(f"input changed during capture: {path}")
    identity = {"path": str(physical), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if pin is not None and any(identity[key] != pin[key] for key in ("bytes", "sha256")):
        _fail(f"size/hash mismatch: {path}")
    if lexical in snapshots and (snapshots[lexical]["identity"] != identity or snapshots[lexical]["stamp"] != after):
        _fail(f"input changed during capture: {path}")
    snapshots[lexical] = {"identity": identity, "data": raw, "stamp": after}
    return identity, raw


def _tree_members(tree):
    members = []
    pending = [tree]
    while pending:
        directory = pending.pop()
        for item in sorted(directory.iterdir()):
            if item.is_symlink():
                _fail(f"symlinked tree member: {item}")
            if item.is_dir():
                pending.append(item)
            elif item.suffix == ".pyi":
                _physical(item)
                members.append(item.relative_to(tree).as_posix())
    return sorted(members)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _references(values, defined, label):
    _list(values, label, nonempty=True)
    if any(not isinstance(value, str) or value not in defined for value in values):
        _fail(f"undeclared reference in {label}")
    if len(set(values)) != len(values):
        _fail(f"duplicate reference in {label}")


def _lineage(row, sources, licenses):
    source_rows = [copy.deepcopy(sources[key]) for key in sorted(row["source_ids"])]
    if any(source["role"] == "doc-enriched" for source in source_rows):
        roles = {source["role"] for source in source_rows}
        if not {"runtime-observation", "documentation"} <= roles:
            _fail("doc-enriched lineage must include runtime-observation and documentation parents")
    return {"sources": source_rows,
            "licenses": [copy.deepcopy(licenses[key]) for key in sorted(row["license_ids"])]}


def _declaration_binding(node, module):
    canonical = (isinstance(node, ast.ClassDef) and module == "builtins" and node.name in _ATOMS
                 and not node.decorator_list and not node.keywords)
    return ("atom", node.name) if canonical else ("declaration", f"{type(node).__name__}@{node.lineno}:{node.col_offset}")


def _definition_expressions(node):
    yield from node.decorator_list
    if isinstance(node, ast.ClassDef):
        yield from node.bases
        yield from (keyword.value for keyword in node.keywords)
    else:
        arguments = node.args
        yield from arguments.defaults
        yield from (default for default in arguments.kw_defaults if default is not None)
        for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                         *([arguments.vararg] if arguments.vararg else []),
                         *([arguments.kwarg] if arguments.kwarg else [])]:
            if argument.annotation is not None:
                yield argument.annotation
        if node.returns is not None:
            yield node.returns


def _binding_effects(node):
    """Find possible bindings in this scope without entering deferred/local bodies."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
        for expression in _definition_expressions(node):
            yield from _binding_effects(expression)
        return
    if isinstance(node, ast.Lambda):
        for expression in [*node.args.defaults, *(default for default in node.args.kw_defaults if default is not None)]:
            yield from _binding_effects(expression)
        return
    if isinstance(node, ast.comprehension):
        # Comprehension iteration targets are local; walrus targets in expressions
        # still bind the surrounding scope.
        yield from _binding_effects(node.iter)
        for condition in node.ifs:
            yield from _binding_effects(condition)
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        yield node.id
    elif isinstance(node, ast.alias):
        yield node.asname or node.name.split(".")[0]
    elif isinstance(node, ast.ExceptHandler) and node.name:
        yield node.name
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        yield node.name
    elif isinstance(node, ast.MatchMapping) and node.rest:
        yield node.rest
    for child in ast.iter_child_nodes(node):
        yield from _binding_effects(child)


def _scope_bindings(body, module):
    """Resolve annotation names conservatively over the complete declaration scope."""
    bindings = {}

    def bind(name, value):
        bindings.setdefault(name, []).append(value)

    def star_import():
        # Unknown exports can rebind every name already present in this scope.
        # The wildcard marker also blocks fallback to outer or builtin names.
        for name in list(bindings):
            if name != "*":
                bind(name, ("shadowed", "star import"))
        bind("*", ("shadowed", "star import"))

    def visit(statement):
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(statement.name, _declaration_binding(statement, module))
            for expression in _definition_expressions(statement):
                for name in _binding_effects(expression):
                    bind(name, ("shadowed", name))
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                bind(alias.asname or alias.name.split(".")[0], ("module", alias.name))
        elif isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                if alias.name == "*":
                    star_import()
                else:
                    bind(alias.asname or alias.name, ("import", f"{statement.module}.{alias.name}") if not statement.level else ("shadowed", alias.name))
        else:
            # Conditional or expression bindings have no unconditional witness.
            for name in _binding_effects(statement):
                if name == "*":
                    star_import()
                else:
                    bind(name, ("shadowed", name))

    for statement in body:
        visit(statement)
    return {name: values[0] if len(values) == 1 else ("shadowed", name) for name, values in bindings.items()}


def _binding(node, scopes):
    if isinstance(node, ast.Name):
        for scope in reversed(scopes):
            if node.id in scope:
                return scope[node.id]
            if "*" in scope:
                return "shadowed", f"{node.id} (star import)"
        if node.id in _ATOMS:
            return "atom", node.id
        if node.id in ("staticmethod", "classmethod"):
            return "decorator", node.id
        return "unknown", node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        base = _binding(node.value, scopes)
        if base[0] == "module" and base[1] in ("typing", "typing_extensions", "builtins"):
            if base[1] == "builtins" and node.attr in _ATOMS:
                return "atom", node.attr
            return "import", f"{base[1]}.{node.attr}"
    return "unsupported", ast.unparse(node)


def _annotation(node, scopes):
    raw = ast.unparse(node) if node is not None else None
    descriptor, reason = _annotation_value(node, scopes)
    return {"raw": raw, "normalized": descriptor, "reason": reason,
            "span": {"line": getattr(node, "lineno", None), "column": getattr(node, "col_offset", None),
                     "end_line": getattr(node, "end_lineno", None), "end_column": getattr(node, "end_col_offset", None)}}


def _union(descriptors):
    atoms = {}
    for descriptor in descriptors:
        children = descriptor["alternatives"] if descriptor.get("kind") == "union" else [descriptor]
        for child in children:
            atoms[_stable(child)] = child
    values = [atoms[key] for key in sorted(atoms)]
    return values[0] if len(values) == 1 else {"kind": "union", "alternatives": values}


def _annotation_value(node, scopes):
    if node is None:
        return None, "annotation omitted"
    if isinstance(node, ast.Constant) and node.value is None:
        return {"kind": "None"}, None
    if isinstance(node, (ast.Name, ast.Attribute)):
        binding = _binding(node, scopes)
        if binding[0] == "atom":
            return {"kind": binding[1]}, None
        return None, f"annotation binding unassessed: {binding[0]} {binding[1]}"
    parts = None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        parts = [node.left, node.right]
    elif isinstance(node, ast.Subscript):
        binding = _binding(node.value, scopes)
        if binding in (("import", "typing.Union"), ("import", "typing_extensions.Union")):
            parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        elif binding in (("import", "typing.Optional"), ("import", "typing_extensions.Optional")):
            parts = [node.slice, ast.Constant(value=None)]
    if parts is not None:
        descriptors = []
        for part in parts:
            descriptor, reason = _annotation_value(part, scopes)
            if descriptor is None:
                return None, reason
            descriptors.append(descriptor)
        return _union(descriptors), None
    return None, "annotation expression/checker representation unassessed"


def _default(node):
    if node is None:
        return None
    if isinstance(node, ast.Constant) and node.value is Ellipsis:
        return {"raw": "...", "normalized": None}
    value = None
    known = False
    if isinstance(node, ast.Constant) and type(node.value) in (type(None), bool, int, str, bytes):
        value, known = node.value, True
    elif (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) and
          isinstance(node.operand, ast.Constant) and type(node.operand.value) is int):
        value, known = (-node.operand.value if isinstance(node.op, ast.USub) else node.operand.value), True
    descriptor = None
    if known:
        descriptor = {"kind": "None" if value is None else type(value).__name__}
        if isinstance(value, bytes):
            descriptor["hex"] = value.hex()
        elif value is not None:
            descriptor["value"] = value
    return {"raw": ast.unparse(node), "normalized": descriptor}


def _candidate_parameters(node, scopes):
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    rows = []

    def add(argument, default, kind):
        rows.append({"name": argument.arg, "kind": kind,
                     "required": default is None and kind not in ("var_positional", "var_keyword"),
                     "default": _default(default), "annotation": _annotation(argument.annotation, scopes)})

    for index, (argument, default) in enumerate(zip(positional, defaults)):
        add(argument, default, "positional_only" if index < len(args.posonlyargs) else "positional_or_keyword")
    if args.vararg:
        add(args.vararg, None, "var_positional")
    for argument, default in zip(args.kwonlyargs, args.kw_defaults):
        add(argument, default, "keyword_only")
    if args.kwarg:
        add(args.kwarg, None, "var_keyword")
    return rows


def _candidates(raw, row, sources, licenses):
    try:
        text = raw.decode("utf-8")
        tree = ast.parse(text, filename=row["path"], type_comments=True)
    except (UnicodeDecodeError, SyntaxError, ValueError) as error:
        _fail(f"invalid declaration {row['path']}: {error}")
    result = []
    overrides = {item["qualname"]: item for item in row.get("symbol_lineage", [])}
    seen = set()

    def append(node, qualname, kind, **fields):
        seen.add(qualname)
        lineage = _lineage(overrides.get(qualname, row), sources, licenses)
        result.append({"file": row["path"], "sha256": row["sha256"], "module": row["module"],
                       "qualname": qualname, "kind": kind,
                       "span": {"line": node.lineno, "column": node.col_offset,
                                "end_line": node.end_lineno, "end_column": node.end_col_offset},
                       "lineage": lineage, **fields})

    def visit(body, prefix, scopes, owner_barrier=None):
        module = row["module"] if not prefix else ""
        local = _scope_bindings(body, module)
        scopes = [*scopes, local]
        counts = Counter(node.name for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        for node in body:
            if isinstance(node, ast.ClassDef):
                qualname = prefix + node.name
                reason = (owner_barrier or
                          ("ambiguous document declaration" if counts[node.name] > 1 else
                           "document declaration binding overwritten or conditional" if local.get(node.name) != _declaration_binding(node, module) else
                           "document class creation/decorators unassessed" if node.decorator_list or node.keywords else None))
                append(node, qualname, "class", bases=[ast.unparse(base) for base in node.bases], reason=reason)
                visit(node.body, qualname + ".", scopes, reason)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [_binding(decorator, scopes) for decorator in node.decorator_list]
                overload = any(binding in (("import", "typing.overload"), ("import", "typing_extensions.overload")) for binding in decorators)
                known_decorators = {("decorator", "staticmethod"), ("decorator", "classmethod"),
                                    ("import", "typing.overload"), ("import", "typing_extensions.overload")}
                descriptor = "staticmethod" if ("decorator", "staticmethod") in decorators else "classmethod" if ("decorator", "classmethod") in decorators else "function"
                stub_body = all(isinstance(statement, ast.Pass) or
                                isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                                and (statement.value.value is Ellipsis or isinstance(statement.value.value, str))
                                for statement in node.body)
                reason = (owner_barrier or
                          ("document overload has no qualified public overload identity" if overload else
                           "ambiguous document declaration" if counts[node.name] > 1 else
                           "document declaration binding overwritten or conditional" if local.get(node.name) != _declaration_binding(node, module) else
                           "unsupported declaration decorator" if any(binding not in known_decorators for binding in decorators) else
                           "asynchronous declaration semantics unassessed" if isinstance(node, ast.AsyncFunctionDef) else
                           "nonstub declaration body unassessed" if not stub_body else None))
                append(node, prefix + node.name, "function", parameters=_candidate_parameters(node, scopes),
                       returns=_annotation(node.returns, scopes), descriptor=descriptor,
                       decorators=[ast.unparse(decorator) for decorator in node.decorator_list], reason=reason)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    continue
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    append(node, prefix + name, "import", raw=ast.unparse(node), reason="imports/reexports cannot add runtime declarations")
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    name = target.id if isinstance(target, ast.Name) else ast.unparse(target)
                    append(node, prefix + name, "assignment", raw=ast.unparse(node), reason="document value/helper declarations are not runtime authority")
            elif not (isinstance(node, ast.Pass) or isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
                append(node, prefix + f"<statement@{node.lineno}>", "unsupported", raw=ast.unparse(node), reason="declaration control flow/construct unassessed")

    visit(tree.body, "", [])
    if set(overrides) - seen:
        _fail(f"symbol lineage names absent from {row['path']}: {sorted(set(overrides) - seen)}")
    return result


def load_enrichment(manifest_path):
    """Capture a schema-v1 manifest, its complete declaration tree and attributed bytes.

    Public bundle fields are JSON-safe except ``notice_artifacts`` (path -> bytes).
    ``inputs`` identities and ``provenance`` are suitable for generation receipts;
    private fields retain immutable captures for ``verify_enrichment``.
    """
    snapshots = {}
    manifest_identity, raw = _capture(manifest_path, snapshots)
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_pairs,
                              parse_constant=lambda value: _fail(f"invalid JSON constant: {value}"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"invalid manifest: {error}")
    _object(manifest, ("schema_version", "kind", "tree", "producer", "sources", "files", "licenses", "target"), label="manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["kind"] != "mpy-api-pyi-enrichment":
        _fail("unsupported manifest schema/kind")
    base = Path(manifest_identity["path"]).parent
    tree_path = Path(_text(manifest["tree"], "tree"))
    tree = _physical(tree_path if tree_path.is_absolute() else base / _relative(manifest["tree"], "tree"), directory=True)
    producer = manifest["producer"]
    _object(producer, ("name", "version", "source"), label="producer")
    _text(producer["name"], "producer.name")
    _text(producer["version"], "producer.version")
    _object(producer["source"], ("uri", "revision"), label="producer.source")
    _text(producer["source"]["uri"], "producer.source.uri")
    if not _REVISION.fullmatch(_text(producer["source"]["revision"], "producer.source.revision")):
        _fail("producer.source.revision must be an immutable hexadecimal revision")
    sources, licenses = {}, {}
    for source in _list(manifest["sources"], "sources", nonempty=True):
        _object(source, ("id", "role", "uri", "revision", "version", "content_sha256", "evidence"), label="source")
        key = _identifier(source["id"], "source.id")
        if key in sources:
            _fail(f"duplicate source id: {key}")
        if not isinstance(source["role"], str) or source["role"] not in _ROLES:
            _fail(f"unsupported source role: {source['role']!r}")
        _text(source["uri"], "source.uri")
        if source["revision"] is not None and not _REVISION.fullmatch(_text(source["revision"], "source.revision")):
            _fail("source.revision must be an immutable hexadecimal revision")
        if source["content_sha256"] is not None and (not isinstance(source["content_sha256"], str) or not _SHA256.fullmatch(source["content_sha256"])):
            _fail("source.content_sha256 must be a lowercase SHA256")
        if source["revision"] is None and source["content_sha256"] is None:
            _fail("source must have an immutable revision or content_sha256")
        if source["version"] is not None:
            _text(source["version"], "source.version")
        for pin in _list(source["evidence"], "source.evidence"):
            _pin(pin, "source.evidence")
            _capture(base / _relative(pin["path"], "evidence path"), snapshots, pin)
        sources[key] = source
    notice_artifacts = {}
    for license_row in _list(manifest["licenses"], "licenses", nonempty=True):
        _object(license_row, ("id", "expression", "copyright", "notices", "modified"), label="license")
        key = _identifier(license_row["id"], "license.id")
        if key in licenses:
            _fail(f"duplicate license id: {key}")
        _text(license_row["expression"], "license.expression")
        _text(license_row["copyright"], "license.copyright")
        if type(license_row["modified"]) is not bool:
            _fail("license.modified must be a boolean")
        for pin in _list(license_row["notices"], "license.notices", nonempty=True):
            _pin(pin, "license.notice")
            relative = _relative(pin["path"], "notice path")
            identity, data = _capture(base / relative, snapshots, pin)
            # Only generated path components are published, not arbitrary manifest names.
            basename = re.sub(r"[^A-Za-z0-9._-]", "_", relative.name)
            destination = f"enrichment-notices/{key}/{identity['sha256']}-{basename}"
            notice_artifacts[destination] = data
        licenses[key] = license_row
    files = _list(manifest["files"], "files", nonempty=True)
    paths, modules = set(), set()
    candidates = []
    for row in files:
        _object(row, ("path", "module", "bytes", "sha256", "source_ids", "license_ids"), ("symbol_lineage",), label="file")
        relative = _relative(row["path"], "declaration path")
        if relative.suffix != ".pyi":
            _fail("declaration path must have .pyi suffix")
        _pin({key: row[key] for key in ("path", "bytes", "sha256")}, "file")
        _qualified(row["module"], "module")
        if row["path"] in paths or row["module"] in modules:
            _fail("duplicate declaration file/module mapping")
        paths.add(row["path"])
        modules.add(row["module"])
        _references(row["source_ids"], sources, "file.source_ids")
        _references(row["license_ids"], licenses, "file.license_ids")
        _lineage(row, sources, licenses)
        symbols = set()
        for item in _list(row.get("symbol_lineage", []), "symbol_lineage"):
            _object(item, ("qualname", "source_ids", "license_ids"), label="symbol_lineage")
            _qualified(item["qualname"], "symbol qualname")
            if item["qualname"] in symbols:
                _fail("duplicate symbol lineage")
            symbols.add(item["qualname"])
            _references(item["source_ids"], sources, "symbol.source_ids")
            _references(item["license_ids"], licenses, "symbol.license_ids")
        _, data = _capture(tree / relative, snapshots, {key: row[key] for key in ("path", "bytes", "sha256")})
        candidates.extend(_candidates(data, row, sources, licenses))
    members = _tree_members(tree)
    if members != sorted(paths):
        _fail(f"tree membership differs from pinned declarations: pinned={sorted(paths)} actual={members}")
    target = manifest["target"]
    if target is not None:
        _object(target, ("port", "target", "variant", "inputs"), label="target")
        for key in ("port", "target", "variant"):
            if not isinstance(target[key], str):
                _fail(f"target.{key} must be a string")
        target_paths = set()
        for pin in _list(target["inputs"], "target.inputs", nonempty=True):
            _pin(pin, "target.input")
            path = Path(_text(pin["path"], "target.input.path"))
            if not path.is_absolute() or str(path) != str(path.resolve()) or pin["path"] in target_paths:
                _fail("target input must have unique absolute physical path")
            target_paths.add(pin["path"])
            _capture(path, snapshots, pin)
    tree_records = sorted(copy.deepcopy(files), key=lambda row: row["path"])
    for row in tree_records:
        row["lineage"] = _lineage(row, sources, licenses)
        for symbol in row.get("symbol_lineage", []):
            symbol["lineage"] = _lineage(symbol, sources, licenses)
    tree_identity = {"path": str(tree), "sha256": hashlib.sha256(_stable(tree_records).encode()).hexdigest(), "files": tree_records}
    inputs = sorted((copy.deepcopy(snapshot["identity"]) for snapshot in snapshots.values()), key=lambda row: row["path"])
    provenance = {"schema_version": 1, "manifest": manifest_identity, "tree": tree_identity,
                  "producer": copy.deepcopy(producer), "sources": copy.deepcopy(manifest["sources"]),
                  "licenses": copy.deepcopy(manifest["licenses"]), "target": copy.deepcopy(target)}
    bundle = {"provenance": provenance, "inputs": inputs, "candidates": candidates,
              "manifest": copy.deepcopy(manifest), "tree_identity": tree_identity,
              "notice_artifacts": notice_artifacts, "_snapshots": snapshots,
              "_tree": str(tree), "_members": members}
    verify_enrichment(bundle)
    return bundle


def verify_enrichment(bundle):
    """Reject any changed consumed byte stream, path indirection or tree membership."""
    tree = _physical(bundle["_tree"], directory=True)
    if _tree_members(tree) != bundle["_members"]:
        _fail("declaration tree membership changed after capture")
    for lexical, snapshot in bundle["_snapshots"].items():
        path = _physical(lexical)
        identity = snapshot["identity"]
        before = _stamp(path.stat())
        raw = path.read_bytes()
        after = _stamp(path.stat())
        if (before != after or after != snapshot["stamp"] or _physical(lexical) != path or
                str(path) != identity["path"] or len(raw) != identity["bytes"] or
                hashlib.sha256(raw).hexdigest() != identity["sha256"]):
            _fail(f"consumed input changed after capture: {lexical}")


def _input_identities(value):
    if isinstance(value, dict):
        if all(key in value for key in ("path", "sha256")):
            identity = {"path": value.get("resolved", value["path"]), "sha256": value["sha256"]}
            if "bytes" in value:
                identity["bytes"] = value["bytes"]
            yield identity
        else:
            for child in value.values():
                yield from _input_identities(child)
    elif isinstance(value, list):
        for child in value:
            yield from _input_identities(child)


def _target_match(inventory, bundle):
    target = bundle["manifest"]["target"]
    build = inventory.get("build", {})
    versions = [{"source_id": source["id"], "revision": source["revision"], "version": source["version"],
                 "selected_source_revision": build.get("source_revision"),
                 "revision_match": source["revision"] == build.get("source_revision") if source["revision"] else None}
                for source in bundle["manifest"]["sources"]]
    if target is None:
        return {"status": "unmatched-reference", "reason": "reference has no exact-build claim", "source_versions": versions}
    if any(build.get(key) != target[key] for key in ("port", "target", "variant")):
        _fail("exact-build target port/target/variant differs from selected build")
    identities = list(_input_identities(build.get("consumed_inputs", {})))
    producer_inputs = inventory.get("provenance", {}).get("producer_inputs", {})
    identities.extend(_input_identities({key: value for key, value in producer_inputs.items() if key != "enrichment"}))
    by_path = {}
    for identity in identities:
        by_path.setdefault(identity["path"], []).append(identity)
    for pin in target["inputs"]:
        matches = by_path.get(pin["path"], [])
        if not matches or any(identity["sha256"] != pin["sha256"] or
                              "bytes" in identity and identity["bytes"] != pin["bytes"] for identity in matches):
            _fail(f"false exact-build consumed input claim: {pin['path']}")
    return {"status": "exact-build-inputs-matched", "authority": "selected consumed inputs, not annotation authority", "source_versions": versions}


def _available(symbol):
    return _status(symbol.get("resolution", {}).get("availability")) in _RESOLVED


def _join(inventory, candidate):
    modules = [module for module in inventory.get("modules", []) if module.get("name") == candidate["module"]]
    if len(modules) != 1:
        return None, None, "absent or ambiguous import occurrence"
    module = modules[0]
    if not _available(module):
        return None, None, "module occurrence availability unresolved"
    current = module
    owners = []
    parts = candidate["qualname"].split(".")
    for index, part in enumerate(parts):
        symbols = current.get("exports", []) if current is module else current.get("members", [])
        matches = [symbol for symbol in symbols if symbol.get("name") == part]
        if len(matches) != 1:
            return None, None, "absent or ambiguous source-selected declaration occurrence"
        current = matches[0]
        if not _available(current):
            return None, None, "declaration occurrence availability unresolved"
        if index < len(parts) - 1:
            if current.get("kind") != "class":
                return None, None, "document owner kind differs from selected class occurrence"
            owners.append({"identity": copy.deepcopy(current.get("identity")), "type_identity": current.get("type_identity"),
                           "source": copy.deepcopy(current.get("source")), "provenance": copy.deepcopy(current.get("provenance")),
                           "availability": copy.deepcopy(current.get("resolution", {}).get("availability"))})
    if current.get("kind") != candidate["kind"]:
        return None, None, "document declaration kind differs from selected source"
    alias = current.get("alias_binding")
    if current.get("alias_of"):
        symbols = module.get("exports", []) if not owners else _owner_symbols(module, parts[:-1])
        targets = [symbol for symbol in symbols if symbol.get("name") == current["alias_of"]]
        if len(targets) != 1 or not _available(targets[0]):
            return None, None, "alias target binding unresolved"
        target = targets[0]
        native_id = current.get("identity", {})
        target_id = target.get("identity", {})
        same_object = (isinstance(native_id, dict) and isinstance(target_id, dict) and
                       native_id.get("object_id") is not None and
                       native_id.get("object_id") == target_id.get("object_id"))
        if not same_object and (not alias or _status(alias) not in _RESOLVED or
                                current.get("alias_target_identity") != target.get("identity")):
            return None, None, "alias target binding unresolved"
    identity = {"module": candidate["module"], "module_identity": copy.deepcopy(module.get("identity")),
                "owners": owners, "export_identity": copy.deepcopy(current.get("identity")),
                "module_provenance": copy.deepcopy(module.get("provenance")),
                "export_provenance": copy.deepcopy(current.get("provenance")),
                "availability": copy.deepcopy(current.get("resolution", {}).get("availability")),
                "type_identity": current.get("type_identity"),
                "binding": copy.deepcopy(current.get("callshape", {}).get("descriptor")),
                "receiver_offset": current.get("callshape", {}).get("implicit_argument_offset"),
                "alias_of": current.get("alias_of"), "alias_binding": copy.deepcopy(alias),
                "alias_target_identity": copy.deepcopy(current.get("alias_target_identity")),
                "inherited_from": copy.deepcopy(current.get("inherited_from")),
                "function_identity": copy.deepcopy((current.get("semantics") or {}).get("function_id")),
                "function_source": copy.deepcopy(current.get("source"))}
    return current, identity, None


def _owner_symbols(module, owners):
    current = module
    for name in owners:
        symbols = current.get("exports", []) if current is module else current.get("members", [])
        current = next(symbol for symbol in symbols if symbol.get("name") == name)
    return current.get("members", [])


def _semantic_descriptor(contract):
    """Strip source evidence/literal values without approximating unknown capabilities."""
    if not isinstance(contract, dict) or "resolution" in contract and _status(contract["resolution"]) not in _RESOLVED:
        return None
    kind = contract.get("kind")
    if kind == "none":
        kind = "None"
    if kind in _ATOMS:
        if kind == "tuple" and "elements" in contract:
            elements = [_semantic_descriptor(element) for element in contract["elements"]]
            return {"kind": "tuple", "elements": elements} if all(element is not None for element in elements) else None
        return {"kind": kind}
    if kind == "union":
        alternatives = [_semantic_descriptor(child) for child in contract.get("alternatives", [])]
        return _union(alternatives) if alternatives and all(child is not None for child in alternatives) else None
    if kind in ("argument", "argument-table"):
        return {"kind": kind, "exported_argument": contract.get("exported_argument"),
                "storage_field": contract.get("storage_field")}
    if kind not in (None, "unresolved", "void-C-return"):
        # A qualified capability is known even when it deliberately renders as Any.
        return {"kind": kind}
    return None


def _source_return(symbol):
    semantic = symbol.get("semantics") or {}
    contracts = semantic.get("returns", [])
    resolved = _status(semantic.get("return_resolution")) in _RESOLVED
    values = [_semantic_descriptor(contract) for contract in contracts]
    descriptor = _union(values) if resolved and values and all(value is not None for value in values) else None
    return {"resolution": semantic.get("return_resolution", "unresolved"), "value": copy.deepcopy(contracts),
            "normalized": descriptor, "evidence": {"source": copy.deepcopy(symbol.get("source")),
            "return_unresolved": copy.deepcopy(semantic.get("return_unresolved", [])),
            "resolution": copy.deepcopy(symbol.get("resolution", {}).get("types", {}))}}


def _compare(source, annotation):
    if _status(source["resolution"]) not in _RESOLVED or source.get("normalized") is None:
        reason = "source field has no complete independently qualified descriptor"
        if annotation.get("reason"):
            reason += "; " + annotation["reason"]
        return "documentation-unverified", reason
    if annotation.get("normalized") is None:
        return "unsupported", annotation.get("reason") or "candidate annotation unassessed"
    if source["normalized"] == annotation["normalized"]:
        return "confirmed", "candidate agrees with independently qualified source field"
    return "conflict", "candidate differs from independently qualified source field"


def _shape_comparison(symbol, candidate, in_class):
    shape = symbol.get("callshape") or {}
    source_resolution = symbol.get("resolution", {}).get("callshape", "unresolved")
    doc = copy.deepcopy(candidate["parameters"])
    reasons = []
    descriptor = shape.get("descriptor")
    offset = shape.get("implicit_argument_offset")
    if descriptor != candidate["descriptor"]:
        reasons.append("binding/decorator differs")
    bound = in_class and descriptor != "staticmethod" and offset != 0
    if bound:
        if not doc or doc[0]["kind"] not in ("positional_only", "positional_or_keyword") or not doc[0]["required"]:
            reasons.append("document receiver correspondence differs")
        else:
            receiver = doc.pop(0)
            if shape.get("receiver_kind") and receiver["kind"] != shape["receiver_kind"]:
                reasons.append("receiver parameter kind differs")
    if _status(source_resolution) not in _RESOLVED or type(offset) is not int:
        return "documentation-unverified", ["source binding/callshape unresolved"], doc, False
    parameters = shape.get("parameters")
    correspondence = False
    if isinstance(parameters, list) and _status(shape.get("parameters_resolution")) in _RESOLVED:
        if len(parameters) != len(doc):
            reasons.append("parameter arity differs")
        else:
            for index, (source, documented) in enumerate(zip(parameters, doc)):
                if source.get("kind") != documented["kind"]:
                    reasons.append(f"parameter {index} kind differs")
                if source.get("required", True) != documented["required"] and source.get("kind") not in ("var_positional", "var_keyword"):
                    reasons.append(f"parameter {index} default presence differs")
                if source.get("kind") != "positional_only" and source.get("name") != documented["name"]:
                    reasons.append(f"parameter {index} keyword name differs")
                default = documented.get("default")
                if default and default.get("normalized") is not None:
                    source_default = _source_default(source.get("default_contract"))
                    if source_default is not None and source_default != default["normalized"]:
                        reasons.append(f"parameter {index} default value differs")
            correspondence = not reasons
    else:
        positional = [row for row in doc if row["kind"] in ("positional_only", "positional_or_keyword")]
        minimum = sum(row["required"] for row in positional)
        maximum = None if any(row["kind"] == "var_positional" for row in doc) else len(positional)
        for key, value in (("minimum", minimum), ("maximum", maximum)):
            source_value = shape.get(key)
            if type(source_value) is int:
                source_value -= offset
            if source_value != value:
                reasons.append(f"wrapper {key} differs")
        if shape.get("keywords") is False and any(row["kind"] != "positional_only" for row in doc):
            reasons.append("positional-only/keyword acceptance differs")
        if not reasons:
            return "documentation-unverified", ["source parameter correspondence unresolved"], doc, False
    return ("conflict" if reasons else "confirmed"), reasons or ["candidate shape agrees with qualified source correspondence"], doc, correspondence


def _source_default(contract):
    if not isinstance(contract, dict) or _status(contract.get("resolution", "resolved")) not in _RESOLVED:
        return None
    kind = contract.get("kind")
    kind = {"none": "None", "bool-storage": "bool", "integer-storage-literal": "int"}.get(kind, kind)
    if kind == "None":
        return {"kind": "None"}
    value = contract.get("value", contract.get("literal_value"))
    if kind == "bytes" and isinstance(value, bytes):
        return {"kind": "bytes", "hex": value.hex()}
    if kind in ("bool", "int", "str") and type(value).__name__ == kind:
        return {"kind": kind, "value": value}
    return None


def merge_enrichment(inventory, bundle):
    """Attach attributed comparison evidence, never mutate authoritative symbol facts."""
    target_match = _target_match(inventory, bundle)
    records = []
    source_lineage = {"source_revision": inventory.get("build", {}).get("source_revision"),
                      "root": inventory.get("build", {}).get("root"),
                      "authority": inventory.get("provenance", {}).get("frontend"),
                      "producer_inputs": copy.deepcopy(inventory.get("provenance", {}).get("producer_inputs", {}))}
    # Enrichment lineage is held separately to avoid embedding the report in itself.
    source_lineage["producer_inputs"].pop("enrichment", None)

    def record(candidate, field, source, value, outcome, reason, identity=None):
        records.append({"document": {key: copy.deepcopy(candidate[key]) for key in ("file", "sha256", "module", "qualname", "kind", "span", "lineage")},
                        "target_identity": identity, "field": field, "source": source,
                        "source_lineage_ref": "source_lineage", "candidate": copy.deepcopy(value),
                        "outcome": outcome, "reason": reason})

    for candidate in bundle["candidates"]:
        if candidate["kind"] not in ("class", "function"):
            record(candidate, "declaration", None, candidate.get("raw"), "unsupported", candidate["reason"])
            continue
        symbol, identity, reason = _join(inventory, candidate)
        if symbol is None:
            record(candidate, "declaration", None, {"kind": candidate["kind"]}, "unjoined", reason)
            continue
        if candidate.get("reason"):
            record(candidate, "declaration", {"resolution": copy.deepcopy(symbol.get("resolution"))},
                   {"kind": candidate["kind"], "decorators": candidate.get("decorators")}, "unsupported", candidate["reason"], identity)
            continue
        if candidate["kind"] == "class":
            record(candidate, "declaration", {"resolution": copy.deepcopy(symbol.get("resolution"))}, {"kind": "class"},
                   "confirmed", "class occurrence exists in source-selected available surface", identity)
            if candidate["bases"]:
                record(candidate, "bases", {"value": copy.deepcopy(symbol.get("bases", []))}, candidate["bases"],
                       "unsupported", "document bases cannot change source-selected inheritance", identity)
            continue
        outcome, shape_reasons, doc_parameters, correspondence = _shape_comparison(symbol, candidate, bool(identity["owners"]))
        record(candidate, "callshape", {"resolution": copy.deepcopy(symbol.get("resolution", {}).get("callshape")),
                                      "value": copy.deepcopy(symbol.get("callshape"))},
               {"descriptor": candidate["descriptor"], "parameters": candidate["parameters"]}, outcome, "; ".join(shape_reasons), identity)
        parameters = symbol.get("callshape", {}).get("parameters") or []
        for index, documented in enumerate(doc_parameters):
            annotation = documented["annotation"]
            if correspondence and index < len(parameters):
                parameter = parameters[index]
                source = {"resolution": parameter.get("type_resolution", "unresolved"),
                          "value": copy.deepcopy(parameter.get("type_contract")),
                          "normalized": _semantic_descriptor(parameter.get("type_contract")),
                          "evidence": {key: copy.deepcopy(parameter[key]) for key in ("source", "resolution", "accepted_domain", "constraints", "result_storage") if key in parameter}}
                field_outcome, field_reason = _compare(source, annotation)
            else:
                source = {"resolution": "unresolved", "normalized": None,
                          "reason": "public argument correspondence not qualified"}
                field_outcome, field_reason = "documentation-unverified", "parameter candidate has no compatible qualified public correspondence"
            record(candidate, f"parameter:{index}:{documented['name']}", source, annotation, field_outcome, field_reason, identity)
        # Closed result comparison does not depend on argument shape. Identity/generic
        # candidates stay unsupported unless a complete source relation is representable.
        source = _source_return(symbol)
        return_outcome, return_reason = _compare(source, candidate["returns"])
        record(candidate, "returns", source, candidate["returns"], return_outcome, return_reason, identity)
    records.sort(key=lambda row: (row["document"]["file"], row["document"]["module"], row["document"]["qualname"],
                                  row["document"]["span"]["line"], row["document"]["span"]["column"], row["field"]))
    report = {"schema_version": 1, "inputs": copy.deepcopy(bundle["inputs"]), "target_match": target_match,
              "source_lineage": source_lineage,
              "summary": dict(sorted(Counter(row["outcome"] for row in records).items())), "records": records}
    inventory["enrichment"] = report
    inventory.setdefault("provenance", {})["enrichment"] = copy.deepcopy(bundle["provenance"])
    return report
