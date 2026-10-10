"""Bounded declaration and selected frozen-source consumer contracts."""

import ast
import copy
import hashlib
import json

import pytest

from mpy_api.declarations import render_declarations
from mpy_api.frozen import collect_frozen_modules


def module(name, exports=None, **fields):
    return {"name": name, "identity": "fixture:" + name, "exports": exports or [], **fields}


def inventory(*modules):
    return {"schema_version": 1, "modules": list(modules)}


def function(name, minimum=1, maximum=2, descriptor="function", offset=0):
    return {"name": name, "kind": "function", "identity": "fixture:" + name,
            "resolution": {"availability": {"status": "resolved"}},
            "callshape": {"minimum": minimum, "maximum": maximum, "keywords": False,
                          "descriptor": descriptor, "implicit_argument_offset": offset},
            "semantics": {"return_resolution": "bounded-resolved", "returns": [{"kind": "bool"}]}}


def selected(tmp_path, source, targets=("renamed.py",)):
    physical = tmp_path / "physical.py"
    physical.write_text(source, encoding="utf-8")
    return {"schema_version": 1, "roots": {"fixture": str(tmp_path)},
            "groups": {"runtime_frozen_python": [
                {"root": "fixture", "path": "physical.py", "target_path": target,
                 "owner": "fixture-owner", "provenance": {"kind": "fixture"}}
                for target in targets]}}


def declaration(tree, name):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return node
    raise AssertionError(f"runtime declaration is absent: {name}")


def resolved_name(node, tree):
    assert isinstance(node, ast.Name)
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                if (alias.asname or alias.name) == node.id:
                    return f"{statement.module}.{alias.name}"
    return node.id


def any_annotation(node, tree):
    assert resolved_name(node, tree) == "typing.Any"


def passthrough_relation(function_node, tree, public_index=0):
    parameter = function_node.args.args[public_index]
    assert isinstance(parameter.annotation, ast.Name)
    assert isinstance(function_node.returns, ast.Name)
    assert parameter.annotation.id == function_node.returns.id
    typevar = declaration(tree, parameter.annotation.id)
    assert isinstance(typevar, ast.Assign) and isinstance(typevar.value, ast.Call)
    assert resolved_name(typevar.value.func, tree) == "typing.TypeVar"


def test_runtime_builtin_role_and_safe_package_paths():
    result = render_declarations(inventory(module("builtins"), module("pkg", is_package=True), module("pkg.child")))
    assert list(result) == ["modules/pkg/__init__.pyi", "modules/pkg/child.pyi", "typeshed/stdlib/builtins.pyi"]
    with pytest.raises(ValueError):
        render_declarations(inventory(module("../escape")))
    with pytest.raises(ValueError):
        render_declarations(inventory(module("pkg"), module("pkg", is_package=True)))


def test_wrapper_offsets_and_descriptor_bindings():
    instance = function("instance", 2, 3, offset=1)
    static = function("static", 1, 2, descriptor="staticmethod")
    classmethod = function("class_bound", 2, 2, descriptor="classmethod", offset=1)
    klass = {"name": "C", "kind": "class", "members": [instance, static, classmethod],
             "resolution": {"availability": {"status": "resolved"}}}
    tree = ast.parse(render_declarations(inventory(module("fixture", [klass])))["modules/fixture.pyi"])
    klass = declaration(tree, "C")
    method = declaration(klass, "instance")
    assert len(method.args.posonlyargs) == 3
    assert not method.args.args and not method.args.kwonlyargs
    assert len(method.args.defaults) == 1 and method.args.defaults[0].value is Ellipsis
    for parameter in method.args.posonlyargs[1:]:
        any_annotation(parameter.annotation, tree)
    assert resolved_name(method.returns, tree) == "bool"
    method = declaration(klass, "static")
    assert [resolved_name(decorator, tree) for decorator in method.decorator_list] == ["staticmethod"]
    assert len(method.args.posonlyargs) == 2 and len(method.args.defaults) == 1
    method = declaration(klass, "class_bound")
    assert [resolved_name(decorator, tree) for decorator in method.decorator_list] == ["classmethod"]
    assert len(method.args.posonlyargs) == 2 and not method.args.defaults


def test_unresolved_result_does_not_use_incidental_constructor_and_no_static_openness():
    symbol = function("f")
    symbol["semantics"] = {"return_resolution": "unresolved", "returns": [{"kind": "bool"}],
                           "constructor_calls": [{"kind": "int"}]}
    result = render_declarations(inventory(module("closed", [symbol], attribute_policy={"kind": "closed-table-with-coverage-gap", "unresolved": ["framework gap"]}),
                                           module("dynamic", attribute_policy={"kind": "dynamic-provider", "provider": "selected-handler"})))
    closed = ast.parse(result["modules/closed.pyi"])
    any_annotation(declaration(closed, "f").returns, closed)
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for node in closed.body)
    dynamic = ast.parse(result["modules/dynamic.pyi"])
    any_annotation(declaration(dynamic, "__getattr__").returns, dynamic)


def test_representation_gaps_are_tracked_and_repeated_render_is_identical():
    wide = function("wide", 1, 65535)
    class_only = {"name": "__name__", "kind": "unknown", "attribute_scope": "class", "resolution": {"availability": {"status": "resolved"}}}
    klass = {"name": "C", "kind": "class", "members": [class_only], "constructor": {"resolution": "unresolved"},
             "resolution": {"availability": {"status": "resolved"}}}
    data = inventory(module("fixture", [wide, klass]))
    first = render_declarations(data)
    assert first == render_declarations(data)
    tree = ast.parse(first["modules/fixture.pyi"])
    assert wide["resolution"]["declaration"]["status"] == "unresolved"
    assert class_only["resolution"]["declaration"]["status"] == "unresolved"
    assert klass["resolution"]["declaration"]["status"] == "unresolved"
    method = declaration(tree, "wide")
    assert len(method.args.posonlyargs) == 1 and method.args.vararg is not None
    class_node = declaration(tree, "C")
    assert not any(isinstance(node, ast.FunctionDef) and node.name in ("__new__", "__init__") for node in class_node.body)
    assert not any(isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__name__" for node in class_node.body)


def test_frozen_physical_target_and_package_identities(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, "def echo(value):\n    return value\n", ("one.py", "pkg/__init__.py", "pkg/two.py")))
    assert [item["name"] for item in modules] == ["one", "pkg", "pkg.two"]
    assert len({item["identity"] for item in modules}) == 3
    assert len({item["provenance"]["physical_path"] for item in modules}) == 1
    assert [item["provenance"]["target_path"] for item in modules] == ["one.py", "pkg/__init__.py", "pkg/two.py"]
    result = render_declarations(inventory(*modules))
    assert "modules/pkg/__init__.pyi" in result
    tree = ast.parse(result["modules/one.pyi"])
    passthrough_relation(declaration(tree, "echo"), tree)


def test_frozen_keyword_defaults_import_aliases_and_decorators(tmp_path):
    source = "from .helper import thing as alias\nclass C:\n    @staticmethod\n    def f(a, /, b=False, *, c=None, **rest):\n        return 1\n    @classmethod\n    def g(cls, value):\n        return value\n"
    modules = collect_frozen_modules(selected(tmp_path, source, ("pkg/mod.py",)))
    tree = ast.parse(render_declarations(inventory(*modules))["modules/pkg/mod.pyi"])
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "helper"]
    assert len(imports) == 1 and imports[0].level == 1
    assert [(alias.name, alias.asname) for alias in imports[0].names] == [("thing", "alias")]
    klass = declaration(tree, "C")
    method = declaration(klass, "f")
    assert [resolved_name(decorator, tree) for decorator in method.decorator_list] == ["staticmethod"]
    assert [arg.arg for arg in method.args.posonlyargs] == ["a"]
    assert [arg.arg for arg in method.args.args] == ["b"]
    assert method.args.defaults[0].value is False
    assert [arg.arg for arg in method.args.kwonlyargs] == ["c"]
    assert method.args.kw_defaults[0].value is None
    assert method.args.kwarg.arg == "rest"
    assert resolved_name(method.returns, tree) == "int"
    method = declaration(klass, "g")
    assert [resolved_name(decorator, tree) for decorator in method.decorator_list] == ["classmethod"]
    passthrough_relation(method, tree, public_index=1)


def test_frozen_does_not_execute_and_keeps_unsupported_conditional_names(tmp_path):
    source = "raise RuntimeError('must not execute')\nif feature:\n    def conditional(x):\n        return x\ndef control(x):\n    if x:\n        return 1\n    return False\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    exports = {symbol["name"]: symbol for symbol in modules[0]["exports"]}
    assert exports["conditional"]["resolution"]["availability"]["status"] == "conditional-unassessed"
    assert exports["control"]["semantics"]["return_resolution"] == "unresolved"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == "conditional" or
                   isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "conditional"
                   for node in tree.body)
    assert exports["conditional"]["resolution"]["declaration"]["status"] == "unresolved"
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for node in tree.body)


def test_annotation_support_does_not_shadow_runtime_any(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, "Any = 1\n_mpy_Any = 'selected'\ndef f(x: int):\n    return unknown(x)\n"))
    data = inventory(*modules)
    content = render_declarations(data)["modules/renamed.pyi"]
    tree = ast.parse(content)
    annotation_import = next(alias for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "typing" for alias in node.names if alias.name == "Any")
    assert annotation_import.asname not in {"Any", "_mpy_Any"}
    runtime_any = declaration(tree, "Any")
    assert isinstance(runtime_any, ast.AnnAssign) and resolved_name(runtime_any.annotation, tree) == "int"
    method = declaration(tree, "f")
    any_annotation(method.args.args[0].annotation, tree)
    any_annotation(method.returns, tree)
    assert modules[0]["exports"][-1]["callshape"]["parameters"][0]["annotation_candidate"] == "int"
    assert content == render_declarations(copy.deepcopy(data))["modules/renamed.pyi"]


def test_frozen_native_decorators_do_not_resolve_python_fallthrough(tmp_path):
    source = "import micropython\n@micropython.asm_thumb\ndef asm(r0):\n    mov(r0, 1)\n@micropython.viper\ndef native(x: int) -> int:\n    return x\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    exports = {symbol["name"]: symbol for symbol in modules[0]["exports"]}
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    for name in ("asm", "native"):
        symbol = exports[name]
        assert symbol["kind"] == "unknown"
        assert symbol["resolution"]["callshape"]["status"] == "unresolved"
        assert symbol["semantics"]["return_resolution"] == "unresolved"
        declaration_node = declaration(tree, name)
        assert isinstance(declaration_node, ast.AnnAssign)
        any_annotation(declaration_node.annotation, tree)


def test_frozen_alias_identity_and_variadic_result_are_preserved(tmp_path):
    source = "def f(x):\n    return x\nalias = f\nclass C:\n    def method(self, /):\n        return 1\nAliasC = C\ndef variadic(*args):\n    return args\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    exports = {symbol["name"]: symbol for symbol in modules[0]["exports"]}
    assert exports["alias"]["alias_target_identity"] == exports["f"]["identity"]
    assert exports["AliasC"]["alias_target_identity"] == exports["C"]["identity"]
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    for name, target in (("alias", "f"), ("AliasC", "C")):
        alias = declaration(tree, name)
        assert isinstance(alias, ast.Assign) and isinstance(alias.value, ast.Name) and alias.value.id == target
    method = declaration(declaration(tree, "C"), "method")
    assert len(method.args.posonlyargs) == 1 and not method.args.args
    assert resolved_name(method.returns, tree) == "int"
    variadic = declaration(tree, "variadic")
    assert variadic.args.vararg is not None
    any_annotation(variadic.args.vararg.annotation, tree)
    any_annotation(variadic.returns, tree)


def test_frozen_nonfinite_numeric_values_keep_canonical_json_and_source_evidence(tmp_path):
    source = "positive = 1e400\nnegative = -1e400\nblob = b'abc'\ncomplex_value = 1e400j\ndef f():\n    return -1e400\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    json.dumps(modules, allow_nan=False)
    exports = {symbol["name"]: symbol for symbol in modules[0]["exports"]}
    for name in ("positive", "negative"):
        contract = exports[name]["value_contract"]
        assert contract["kind"] == "float" and "value" not in contract
        assert contract["value_resolution"]["status"] == "unresolved"
        assert isinstance(contract["literal"], str)
        assert exports[name]["source"]["path"].endswith("physical.py")
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    assert resolved_name(declaration(tree, "f").returns, tree) == "float"


def test_unqualified_presence_does_not_become_available_any_export():
    candidates = []
    for status in ("unresolved", "conflict", "conditional-unassessed", "dynamic-unassessed", "unassessed"):
        candidate = function("candidate_" + status.replace("-", "_"))
        candidate["resolution"]["availability"] = {"status": status}
        candidates.append(candidate)
    first_conflict = function("conflicted")
    first_conflict["resolution"]["availability"] = {"status": "unresolved"}
    second_conflict = copy.deepcopy(first_conflict)
    second_conflict["identity"] = "different:selected:owner"
    second_conflict["resolution"]["availability"] = {"status": "conflict"}
    known_unknown = {"name": "known_unknown", "kind": "unknown",
                     "resolution": {"availability": {"status": "resolved"}},
                     "value_contract": {"kind": "unresolved"}}
    data = inventory(module("closed", candidates + [first_conflict, second_conflict, known_unknown],
                            attribute_policy={"kind": "closed-table"}))
    tree = ast.parse(render_declarations(data)["modules/closed.pyi"])
    for candidate in candidates + [first_conflict, second_conflict]:
        assert candidate["resolution"]["declaration"]["status"] == "unresolved"
        assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == candidate["name"] or
                       isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == candidate["name"]
                       for node in tree.body)
    known = declaration(tree, "known_unknown")
    assert isinstance(known, ast.AnnAssign)
    any_annotation(known.annotation, tree)
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for node in tree.body)


def test_known_key_presence_with_conflicting_value_keeps_any_declaration():
    first = function("known")
    second = copy.deepcopy(first)
    second["identity"] = "different:selected:owner"
    second["semantics"] = {"return_resolution": "unresolved"}
    tree = ast.parse(render_declarations(inventory(module("fixture", [first, second])))["modules/fixture.pyi"])
    known = declaration(tree, "known")
    assert isinstance(known, ast.AnnAssign)
    any_annotation(known.annotation, tree)


def test_augmented_assignment_does_not_alias_its_rhs(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, "items = []\nextra = (1,)\nitems += extra\n"))
    items = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "items")
    assert "alias_of" not in items and "alias_binding" not in items
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    declaration_node = declaration(tree, "items")
    assert isinstance(declaration_node, ast.AnnAssign)
    any_annotation(declaration_node.annotation, tree)


@pytest.mark.parametrize("statement,removed", [
    ("if True:\n    del x\n", True),
    ("if feature:\n    del x\n", False),
    ("try:\n    del x\nfinally:\n    pass\n", True),
    ("try:\n    del x\nexcept Exception:\n    pass\n", False),
    ("for item in iterable:\n    del x\n", False),
    ("while feature:\n    del x\n", False),
    ("with context:\n    del x\n", False),
])
def test_nested_deletion_does_not_leave_qualified_available_name(tmp_path, statement, removed):
    modules = collect_frozen_modules(selected(tmp_path, "x = 1\n" + statement))
    exports = {symbol["name"]: symbol for symbol in modules[0]["exports"]}
    if removed:
        assert "x" not in exports
    else:
        assert exports["x"]["resolution"]["availability"]["status"] == "conditional-unassessed"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    with pytest.raises(AssertionError):
        declaration(tree, "x")
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for node in tree.body)


@pytest.mark.parametrize("source", [
    "def f():\n    return 1\nif feature:\n    f = other\n",
    "def f():\n    return 1\ntry:\n    from _fast import f\nexcept ImportError:\n    pass\n",
    "try:\n    from _fast import f\nexcept ImportError:\n    from _slow import f\n",
    "def f():\n    return 1\nfor item in iterable:\n    f = other\n",
])
def test_branch_rebinding_preserves_definite_key_with_unknown_value(tmp_path, source):
    modules = collect_frozen_modules(selected(tmp_path, source))
    symbol = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "f")
    assert symbol["resolution"]["availability"]["status"] == "selected-source"
    assert symbol["resolution"]["value"]["status"] == "unresolved"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    declaration_node = declaration(tree, "f")
    assert isinstance(declaration_node, ast.AnnAssign)
    any_annotation(declaration_node.annotation, tree)


def test_optional_passthrough_default_does_not_introduce_unsolved_typevar(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, "def f(x=None):\n    return x\n"))
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    function_node = declaration(tree, "f")
    assert function_node.args.defaults[0].value is None
    any_annotation(function_node.args.args[0].annotation, tree)
    any_annotation(function_node.returns, tree)
    assert modules[0]["exports"][0]["resolution"]["declaration"]["status"] == "unresolved"
    table_function = function("table")
    table_function["callshape"].update({
        "parameters_resolution": "resolved",
        "parameters": [{"name": "x", "kind": "positional_or_keyword", "required": False,
                        "default_contract": {"kind": "None"}}]})
    table_function["semantics"]["returns"] = [{"kind": "argument-table", "storage_field": "u_obj", "exported_argument": "x"}]
    table_tree = ast.parse(render_declarations(inventory(module("fixture", [table_function])))["modules/fixture.pyi"])
    table_node = declaration(table_tree, "table")
    any_annotation(table_node.args.args[0].annotation, table_tree)
    any_annotation(table_node.returns, table_tree)


def test_container_annotations_use_qualified_non_generic_nominal_types(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, "sequence = []\nmapping = {}\nunique = {1}\nempty = ()\npair = (1, True)\ndef produce():\n    return []\n"))
    builtin_types = [{"name": name, "kind": "class", "members": [], "resolution": {"availability": {"status": "resolved"}}}
                     for name in ("list", "dict", "set", "tuple")]
    tree = ast.parse(render_declarations(inventory(module("builtins", builtin_types), *modules))["modules/renamed.pyi"])
    for name, nominal in (("sequence", "list"), ("mapping", "dict"), ("unique", "set"), ("empty", "tuple"), ("pair", "tuple")):
        assert resolved_name(declaration(tree, name).annotation, tree) == nominal
    assert resolved_name(declaration(tree, "produce").returns, tree) == "list"
    pair = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "pair")
    assert [element["kind"] for element in pair["value_contract"]["elements"]] == ["int", "bool"]


@pytest.mark.parametrize("payload", [
    b"# coding: latin-1\nlabel = 'caf\xe9'\n",
    b"\xef\xbb\xbflabel = 'caf\xc3\xa9'\n",
    "\u212a = 1\n".encode("utf-8"),
    "def \u212a(value):\n    return value\n".encode("utf-8"),
    "label = f'{driver.\u212a}'\n".encode("utf-8"),
])
def test_frozen_frontend_rejects_lexer_identity_changes(tmp_path, payload):
    scope = selected(tmp_path, "", ("one.py", "pkg/__init__.py"))
    (tmp_path / "physical.py").write_bytes(payload)
    with pytest.raises(ValueError):
        collect_frozen_modules(scope)


def test_frozen_raw_utf8_snapshot_preserves_bytes_and_distinct_targets(tmp_path):
    payload = "# coding: utf-8\nlabel = 'caf\u00e9'\n".encode("utf-8")
    scope = selected(tmp_path, "", ("one.py", "pkg/__init__.py"))
    (tmp_path / "physical.py").write_bytes(payload)
    modules = collect_frozen_modules(scope)
    for module_record in modules:
        label = next(symbol for symbol in module_record["exports"] if symbol["name"] == "label")
        assert label["value_contract"]["value"] == "caf\u00e9"
        assert module_record["provenance"]["sha256"] == hashlib.sha256(payload).hexdigest()
        assert module_record["provenance"]["bytes"] == len(payload)
    assert modules[0]["identity"] != modules[1]["identity"]


def test_frozen_source_change_after_real_ast_collection_fails(tmp_path, monkeypatch):
    import mpy_api.frozen as frozen

    scope = selected(tmp_path, "value = 1\n", ("one.py", "two.py"))
    physical = tmp_path / "physical.py"
    original = frozen._Collector.statements
    changed = False

    def change_after_collection(collector, *args, **kwargs):
        nonlocal changed
        result = original(collector, *args, **kwargs)
        if not changed:
            changed = True
            physical.write_bytes(b"value = 2\n")
        return result

    monkeypatch.setattr(frozen._Collector, "statements", change_after_collection)
    with pytest.raises(ValueError):
        collect_frozen_modules(scope)


@pytest.mark.parametrize("target", ["(a, b, c)", "[a, b, c]", "(a, [b, c])"])
def test_grouped_name_deletion_removes_all_nested_bindings(tmp_path, target):
    modules = collect_frozen_modules(selected(tmp_path, "a = 1\nb = 2\nc = 3\ndel " + target + "\n"))
    exports = {symbol["name"] for symbol in modules[0]["exports"]}
    assert not exports.intersection({"a", "b", "c"})
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    for name in ("a", "b", "c"):
        with pytest.raises(AssertionError):
            declaration(tree, name)


@pytest.mark.parametrize("effect", [
    'del globals()["x"]',
    '_ = globals().pop("x")',
    'if globals().pop("x"):\n    pass',
    'def result(value=globals().pop("x")):\n    return value',
    '@globals().pop("x")\ndef result(value):\n    return value',
    'class Result(globals().pop("x")):\n    pass',
    'class Result:\n    _ = globals().pop("x")',
])
def test_eager_namespace_expressions_do_not_retain_stale_exports(tmp_path, effect):
    modules = collect_frozen_modules(selected(tmp_path, "x = 1\n" + effect + "\n"))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["resolution"]["availability"]["status"] == "conditional-unassessed"
    assert exported["resolution"]["value"]["status"] == "unresolved"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    with pytest.raises(AssertionError):
        declaration(tree, "x")
    assert isinstance(declaration(tree, "__getattr__"), ast.FunctionDef)


@pytest.mark.parametrize("effect", [
    "result = (x := 2)",
    "if (x := callback()):\n    pass",
    "def result(value=(x := 2)):\n    return value",
])
def test_eager_walrus_preserves_presence_without_stale_function_shape(tmp_path, effect):
    modules = collect_frozen_modules(selected(tmp_path, "def x(value):\n    return value\n" + effect + "\n"))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["kind"] == "unknown"
    assert exported["resolution"]["availability"]["status"] == "selected-source"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    rebound = declaration(tree, "x")
    assert isinstance(rebound, ast.AnnAssign)
    any_annotation(rebound.annotation, tree)


@pytest.mark.parametrize("body", ["x = Driver()", "del x"])
@pytest.mark.parametrize("invocation", [
    "modify()",
    "_ = modify()",
    "if modify():\n    pass",
    "def result(value=modify()):\n    return value",
    "@modify\ndef result(value):\n    return value",
    "class Result(modify()):\n    pass",
    "class Result:\n    modify()",
    "class Result:\n    @modify\n    def result(value):\n        return value",
])
def test_import_time_calls_expose_declared_global_effect_gaps(tmp_path, body, invocation):
    source = "x = None\ndef modify(*args):\n    global x\n    " + body + "\n" + invocation + "\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["kind"] == "unknown"
    assert exported["resolution"]["availability"]["status"] == "conditional-unassessed"
    assert exported["binding_alternatives"]
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    with pytest.raises(AssertionError):
        declaration(tree, "x")
    with pytest.raises(AssertionError):
        declaration(tree, "__getattr__")


def test_deferred_global_mutation_body_does_not_execute_during_collection(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, "x = 1\ndef modify():\n    global x\n    del x\n"))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["value_contract"] == {"kind": "int", "value": 1}
    assert exported["resolution"]["availability"]["status"] == "selected-source"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    assert isinstance(declaration(tree, "x").annotation, ast.Name)
    assert declaration(tree, "x").annotation.id == "int"


def test_unbound_alias_candidate_does_not_capture_later_same_named_function(tmp_path):
    source = "print_ = print\ndef print(value):\n    return value\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "print_")
    assert exported["alias_candidate"] == "print"
    assert "alias_target_identity" not in exported and "alias_of" not in exported
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    alias = declaration(tree, "print_")
    assert isinstance(alias, ast.AnnAssign)
    any_annotation(alias.annotation, tree)
    assert isinstance(declaration(tree, "print"), ast.FunctionDef)


def test_class_alias_candidate_does_not_capture_later_class_member(tmp_path):
    source = "def helper(value):\n    return value\nclass Result:\n    alias = helper\n    def helper(value):\n        return value\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    cls = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "Result")
    exported = next(symbol for symbol in cls["members"] if symbol["name"] == "alias")
    assert exported["alias_candidate"] == "helper"
    assert "alias_target_identity" not in exported and "alias_of" not in exported
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    body = ast.Module(body=declaration(tree, "Result").body, type_ignores=[])
    alias = declaration(body, "alias")
    assert isinstance(alias, ast.AnnAssign)
    any_annotation(alias.annotation, tree)
    assert isinstance(declaration(body, "helper"), ast.FunctionDef)


def test_unrepresentable_native_identifier_does_not_become_normalized_api():
    exported = function("\u212a", 0, 0)
    records = inventory(module("fixture", [exported]))
    tree = ast.parse(render_declarations(records)["modules/fixture.pyi"])
    for name in ("K", "\u212a"):
        with pytest.raises(AssertionError):
            declaration(tree, name)
    assert exported["resolution"]["declaration"]["status"] == "unresolved"
    with pytest.raises(ValueError):
        render_declarations(inventory(module("\u212a")))


def test_ascii_fstring_value_keeps_export_with_unresolved_type(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, 'label = f"{driver.value}"\n'))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "label")
    assert exported["resolution"]["availability"]["status"] == "selected-source"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    label = declaration(tree, "label")
    assert isinstance(label, ast.AnnAssign)
    any_annotation(label.annotation, tree)


@pytest.mark.parametrize("helper_body", [
    'globals()["x"] = "changed"',
    'del globals()["x"]',
    'locals()["x"] = "changed"',
    'del locals()["x"]',
    'exec("x = \'changed\'")',
])
@pytest.mark.parametrize("invocation", ["reset()", "class Result:\n    reset()"])
def test_called_deferred_namespace_helpers_invalidate_current_bindings(tmp_path, helper_body, invocation):
    source = "def reset():\n    " + helper_body + "\nx = 1\n" + invocation + "\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["resolution"]["availability"]["status"] == "conditional-unassessed"
    assert exported["resolution"]["value"]["status"] == "unresolved"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    with pytest.raises(AssertionError):
        declaration(tree, "x")


@pytest.mark.parametrize("escape, mutation", [
    ("_g = globals()", 'del _g["x"]'),
    ("_g = locals()", '_g["x"] = "changed"'),
    ("_pop = globals().pop", '_pop("x")'),
    ("_get_globals = globals", '_get_globals().pop("x")'),
])
def test_escaped_namespace_access_affects_names_bound_after_escape(tmp_path, escape, mutation):
    modules = collect_frozen_modules(selected(tmp_path, escape + "\nx = 1\n" + mutation + "\n"))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["resolution"]["availability"]["status"] == "conditional-unassessed"
    assert exported["resolution"]["value"]["status"] == "unresolved"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    with pytest.raises(AssertionError):
        declaration(tree, "x")


@pytest.mark.parametrize("assertion", ['assert reset()', 'assert predicate, reset()'])
def test_assertion_calls_do_not_retain_stale_global_contracts(tmp_path, assertion):
    source = "x = 1\ndef reset():\n    global x\n    del x\n" + assertion + "\n"
    modules = collect_frozen_modules(selected(tmp_path, source))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["resolution"]["availability"]["status"] == "conditional-unassessed"
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    with pytest.raises(AssertionError):
        declaration(tree, "x")


def test_assertion_namespace_mutation_is_a_tracked_dynamic_region(tmp_path):
    modules = collect_frozen_modules(selected(tmp_path, 'x = 1\nassert globals().pop("x")\n'))
    exported = next(symbol for symbol in modules[0]["exports"] if symbol["name"] == "x")
    assert exported["resolution"]["availability"]["status"] == "conditional-unassessed"
    assert modules[0]["attribute_policy"]["dynamic_providers"]
    tree = ast.parse(render_declarations(inventory(*modules))["modules/renamed.pyi"])
    with pytest.raises(AssertionError):
        declaration(tree, "x")
    assert isinstance(declaration(tree, "__getattr__"), ast.FunctionDef)
