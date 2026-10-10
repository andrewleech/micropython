"""Pinned reference inputs cannot acquire authority over source-selected APIs."""

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from mpy_api.declarations import render_declarations
from mpy_api.enrichment import load_enrichment, merge_enrichment, verify_enrichment
from mpy_api.frozen import collect_frozen_modules


_REVISION = "a" * 40
_NOTICE = b"Copyright fixture author\nPermission fixture notice\n"
# Exact method body from retained same-source-docstubs/builtins.pyi:121-134,
# generated from captured MicroPython documentation, source revision 9c29c4b.
_CENTER_METHOD = '''    def center(self, width) -> int:
        """
                Return a centered string of length *width*. Padding is done using spaces.
        
                When Unicode support is enabled (``MICROPY_PY_BUILTINS_STR_UNICODE``), this
                method counts Unicode characters rather than bytes, ensuring proper alignment
                for multi-byte UTF-8 characters.
        
                Example::
        
                    >>> 'café'.center(10)  # é is 2 bytes in UTF-8
                    '   café   '
        """
        ...
'''


def _pin(path, *, relative=None):
    data = path.read_bytes()
    return {"path": relative if relative is not None else str(path.resolve()),
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _manifest(tmp_path, text="def truth(value: object, /) -> bool: ...\n", module="probe"):
    tree = tmp_path / "tree"
    tree.mkdir()
    declaration = tree / "input.pyi"
    declaration.write_text(text, encoding="utf-8")
    (tmp_path / "LICENSE.txt").write_bytes(_NOTICE)
    (tmp_path / "evidence.txt").write_bytes(b"captured fixture provenance\n")
    source = {"id": "docs", "role": "documentation", "uri": "https://example.invalid/fixture-docs",
              "revision": _REVISION, "version": "fixture-1", "content_sha256": None,
              "evidence": [_pin(tmp_path / "evidence.txt", relative="evidence.txt")]}
    manifest = {"schema_version": 1, "kind": "mpy-api-pyi-enrichment", "tree": "tree",
                "producer": {"name": "fixture-writer", "version": "1",
                             "source": {"uri": "https://example.invalid/fixture-writer", "revision": _REVISION}},
                "sources": [source], "files": [{**_pin(declaration, relative="input.pyi"), "module": module,
                                                "source_ids": ["docs"], "license_ids": ["fixture-MIT"]}],
                "licenses": [{"id": "fixture-MIT", "expression": "MIT", "copyright": "Copyright fixture author",
                              "notices": [_pin(tmp_path / "LICENSE.txt", relative="LICENSE.txt")], "modified": False}],
                "target": None}
    path = tmp_path / "enrichment.json"
    _save(path, manifest)
    return path, manifest


def _save(path, manifest):
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _center_manifest(tmp_path):
    path, manifest = _manifest(tmp_path, "class str:\n" + _CENTER_METHOD, module="builtins")
    license_path = Path(__file__).resolve().parents[3] / "LICENSE"
    (tmp_path / "MicroPython-LICENSE.txt").write_bytes(license_path.read_bytes())
    (tmp_path / "center-excerpt.pyi").write_text(_CENTER_METHOD, encoding="utf-8")
    excerpt = _pin(tmp_path / "center-excerpt.pyi", relative="center-excerpt.pyi")
    manifest["sources"][0].update(uri="https://github.com/micropython/micropython/blob/9c29c4b/docs/library/builtins.rst",
                                  revision=None, version="captured-9c29c4b", content_sha256=excerpt["sha256"], evidence=[excerpt])
    manifest["licenses"] = [{"id": "MicroPython-MIT", "expression": "MIT",
                             "copyright": "Copyright (c) 2013-2026 Damien P. George",
                             "notices": [_pin(tmp_path / "MicroPython-LICENSE.txt", relative="MicroPython-LICENSE.txt")],
                             "modified": True}]
    manifest["files"][0]["license_ids"] = ["MicroPython-MIT"]
    _save(path, manifest)
    return path


def _parameter(name="value", contract="object", *, kind="positional_only", required=True, default=None):
    result = {"name": name, "kind": kind, "required": required,
              "resolution": {"status": "bounded-resolved", "authority": "fixture selected public ordinal"},
              "type_resolution": "bounded-resolved" if contract is not None else "unresolved",
              "type_contract": {"kind": contract or "unresolved"},
              "source": {"source": "/fixture/selected.c", "line": 10}}
    if default is not None:
        result["default_contract"] = {"kind": type(default).__name__, "value": default}
    return result


def _function(name="truth", returns="bool", *, parameters=None, offset=0, descriptor="function", owner="owner-A"):
    parameters = [_parameter()] if parameters is None else parameters
    minimum = offset + sum(row["required"] for row in parameters if row["kind"] in ("positional_only", "positional_or_keyword"))
    maximum = offset + sum(row["kind"] in ("positional_only", "positional_or_keyword") for row in parameters)
    return {"name": name, "kind": "function",
            "identity": {"container": owner, "container_translation_unit": owner + "-TU", "translation_unit": owner + "-TU",
                         "object_id": owner + ":" + name, "export": name},
            "source": {"source": "/fixture/selected.c", "line": 10},
            "resolution": {"availability": {"status": "resolved"}, "callshape": {"status": "resolved"}},
            "callshape": {"minimum": minimum, "maximum": maximum,
                          "keywords": any(row["kind"] != "positional_only" for row in parameters),
                          "implicit_argument_offset": offset, "descriptor": descriptor,
                          "receiver_kind": "positional_only" if offset else None,
                          "parameters": parameters, "parameters_resolution": "resolved"},
            "semantics": {"function_id": owner + "-TU::same_bare_C_name", "returns": [{"kind": returns or "unresolved"}],
                          "return_resolution": "bounded-resolved" if returns else "unresolved",
                          "return_unresolved": [] if returns else ["successful branch unassessed"]}}


def _class(name, members, *, owner="owner-A"):
    return {"name": name, "kind": "class", "identity": {"container": owner, "export": name},
            "type_identity": owner + ":" + name, "members": members,
            "resolution": {"availability": {"status": "resolved"}},
            "bases": [], "slots": [], "base_resolution": {"status": "resolved"}}


def _module(name, exports, *, owner="owner-A"):
    return {"name": name, "identity": owner, "exports": exports,
            "resolution": {"availability": {"status": "resolved"}}}


def _inventory(*modules):
    return {"schema_version": 1, "modules": list(modules), "types": [],
            "build": {"port": "unix", "target": "fixture", "variant": "standard", "source_revision": "b" * 40,
                      "root": "/fixture", "consumed_inputs": {}},
            "provenance": {"frontend": "fixture selected source", "producer_inputs": {}}}


def _record(report, qualname, field):
    return next(row for row in report["records"] if row["document"]["qualname"] == qualname and row["field"] == field)


def _merge_unchanged(inventory, bundle):
    surface = copy.deepcopy((inventory["modules"], inventory.get("types")))
    declarations = render_declarations(copy.deepcopy(inventory))
    report = merge_enrichment(inventory, bundle)
    assert (inventory["modules"], inventory.get("types")) == surface
    assert render_declarations(copy.deepcopy(inventory)) == declarations
    json.dumps(report)
    json.dumps(bundle["provenance"])
    json.dumps(bundle["inputs"])
    for text in declarations.values():
        ast.parse(text)
    return report, declarations


@pytest.mark.parametrize("annotation,expected", [("object", "confirmed"), ("bool", "conflict"), ("int", "conflict")])
def test_truth_domain_never_narrows(tmp_path, annotation, expected):
    path, _ = _manifest(tmp_path, f"def truth(value: {annotation}, /) -> bool: ...\n")
    inventory = _inventory(_module("probe", [_function()]))
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    row = _record(report, "truth", "parameter:0:value")
    assert row["outcome"] == expected
    assert row["source"]["normalized"] == {"kind": "object"}
    assert row["document"]["lineage"]["sources"][0]["role"] == "documentation"
    assert report["source_lineage"]["source_revision"] == "b" * 40
    assert "value: object" in declarations["modules/probe.pyi"]
    assert _record(report, "truth", "returns")["outcome"] == "confirmed"


def test_parameter_and_return_resolution_are_independent(tmp_path):
    path, _ = _manifest(tmp_path, "def truth(value: int, /) -> str: ...\n")
    function = _function(returns="bytes", parameters=[_parameter(contract=None)])
    inventory = _inventory(_module("probe", [function]))
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    assert _record(report, "truth", "parameter:0:value")["outcome"] == "documentation-unverified"
    assert _record(report, "truth", "returns")["outcome"] == "conflict"
    assert "value: _mpy_Any" in declarations["modules/probe.pyi"]
    assert "-> bytes" in declarations["modules/probe.pyi"]


@pytest.mark.parametrize("known_return", [None, "str"])
def test_actual_center_wrong_return_survives_shape_conflict(tmp_path, known_return):
    # This is the actual retained documentation annotation, not a corrected fixture.
    path = _center_manifest(tmp_path)
    center = _function("center", known_return, parameters=[_parameter("width", "int")], offset=1)
    inventory = _inventory(_module("builtins", [_class("str", [center])]))
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    assert _record(report, "str.center", "callshape")["outcome"] == "conflict"
    row = _record(report, "str.center", "returns")
    assert row["candidate"]["normalized"] == {"kind": "int"}
    assert row["outcome"] == ("conflict" if known_return else "documentation-unverified")
    parsed = ast.parse(declarations["typeshed/stdlib/builtins.pyi"])
    native_str = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == "str")
    method = next(node for node in native_str.body if isinstance(node, ast.FunctionDef) and node.name == "center")
    assert ast.unparse(method.returns) == ("str" if known_return else "_mpy_Any")


@pytest.mark.parametrize("signature", [
    "def truth(value: object, other: int, /) -> str: ...",
    "def truth(value: object = ..., /) -> str: ...",
    "def truth(value: object) -> str: ...",
    "def truth(*, value: object) -> str: ...",
])
def test_stale_shape_cannot_block_closed_return_comparison(tmp_path, signature):
    path, _ = _manifest(tmp_path, signature + "\n")
    inventory = _inventory(_module("probe", [_function()]))
    report, _ = _merge_unchanged(inventory, load_enrichment(path))
    assert _record(report, "truth", "callshape")["outcome"] == "conflict"
    assert _record(report, "truth", "returns")["outcome"] == "conflict"
    parameters = [row for row in report["records"] if row["field"].startswith("parameter:")]
    assert all(row["outcome"] == "documentation-unverified" for row in parameters)


def test_defaults_keywords_and_class_binding_are_authoritative(tmp_path):
    path, _ = _manifest(tmp_path, "class int:\n    @staticmethod\n    def to_bytes(size: int = 3, *, signed: bool = True) -> str: ...\n", module="builtins")
    parameters = [_parameter("length", "int", kind="positional_or_keyword", required=False, default=1),
                  _parameter("signed", "object", kind="keyword_only", required=False, default=False)]
    function = _function("to_bytes", "bytes", parameters=parameters, offset=1)
    inventory = _inventory(_module("builtins", [_class("int", [function])]))
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    assert _record(report, "int.to_bytes", "callshape")["outcome"] == "conflict"
    assert _record(report, "int.to_bytes", "returns")["outcome"] == "conflict"
    text = declarations["typeshed/stdlib/builtins.pyi"]
    assert "length: int = 1" in text
    assert "signed: object = False" in text
    assert "@staticmethod" not in text


def test_resolved_return_with_unresolved_parser_still_compares(tmp_path):
    path, _ = _manifest(tmp_path, "def truth(size: int) -> str: ...\n")
    function = _function(returns="bytes")
    function["callshape"].pop("parameters")
    function["callshape"]["keywords"] = True
    function["callshape"]["parameters_resolution"] = "unresolved"
    function["resolution"]["callshape"] = {"status": "unresolved"}
    report, declarations = _merge_unchanged(_inventory(_module("probe", [function])), load_enrichment(path))
    assert _record(report, "truth", "callshape")["outcome"] == "documentation-unverified"
    assert _record(report, "truth", "returns")["outcome"] == "conflict"
    assert "-> bytes" in declarations["modules/probe.pyi"]


@pytest.mark.parametrize("contract", ["native-integer-conversion", "readable-buffer", "writable-buffer"])
def test_known_unrepresentable_capability_is_not_rendered_any_equivalence(tmp_path, contract):
    path, _ = _manifest(tmp_path, "def truth(value: int, /) -> bool: ...\n")
    parameter = _parameter(contract=contract)
    parameter["representation"] = {"status": "unresolved", "reason": "target capability representation unqualified"}
    inventory = _inventory(_module("probe", [_function(parameters=[parameter])]))
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    row = _record(report, "truth", "parameter:0:value")
    assert row["outcome"] == "conflict"
    assert row["source"]["value"]["kind"] == contract
    assert "value: _mpy_Any" in declarations["modules/probe.pyi"]


@pytest.mark.parametrize("prefix,annotation", [
    ("int = object\n", "int"),
    ("class int: ...\n", "int"),
    ("from alien import int\n", "int"),
    ("from alien import *\n", "int"),
    ("from typing import Any\n", "Any"),
    ("from _typeshed import Incomplete\n", "Incomplete"),
    ("from typing import SupportsIndex\n", "SupportsIndex"),
    ("from typing import TypeVar\nT = TypeVar('T')\n", "T"),
])
def test_shadowed_and_unsupported_atoms_never_gain_precision(tmp_path, prefix, annotation):
    path, _ = _manifest(tmp_path, prefix + f"def truth(value: object, /) -> {annotation}: ...\n")
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function(returns="int")])), load_enrichment(path))
    assert _record(report, "truth", "returns")["outcome"] == "unsupported"
    assert _record(report, "truth", "returns")["candidate"]["normalized"] is None


@pytest.mark.parametrize("annotation,imports", [
    ("int | str", ""),
    ("Union[int, str]", "from typing import Union\n"),
    ("t.Union[int, str]", "import typing as t\n"),
    ("b.int | b.str", "import builtins as b\n"),
])
def test_supported_union_confirmation_uses_bound_names(tmp_path, annotation, imports):
    path, _ = _manifest(tmp_path, imports + f"def truth(value: object, /) -> {annotation}: ...\n")
    function = _function()
    function["semantics"]["returns"] = [{"kind": "int", "source": "left-path"}, {"kind": "str", "source": "right-path"}]
    report, _ = _merge_unchanged(_inventory(_module("probe", [function])), load_enrichment(path))
    assert _record(report, "truth", "returns")["outcome"] == "confirmed"


def test_no_absent_overload_constructor_import_helper_or_inherited_surface_is_added(tmp_path):
    text = ("from typing import overload\nfrom alien import imported\n"
            "class Helper: ...\n"
            "class Native(Helper):\n    def __init__(self, /) -> None: ...\n    def inherited(self, /) -> int: ...\n"
            "@overload\ndef truth(value: bool, /) -> int: ...\n"
            "@overload\ndef truth(value: str, /) -> str: ...\n"
            "def standard_only(value: int, /) -> int: ...\n")
    path, _ = _manifest(tmp_path, text)
    native = _class("Native", [])
    native["slots"] = [{"slot": "make_new", "source": "/fixture/selected.c"}]
    inventory = _inventory(_module("probe", [_function(), native]))
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    overloads = [row for row in report["records"] if row["document"]["qualname"] == "truth"]
    assert len(overloads) == 2
    assert all(row["outcome"] == "unsupported" for row in overloads)
    for name in ("Helper", "Native.__init__", "Native.inherited", "standard_only"):
        assert _record(report, name, "declaration")["outcome"] == "unjoined"
    text = declarations["modules/probe.pyi"]
    assert "@overload" not in text
    assert "imported" not in text
    assert "class Helper" not in text
    assert "standard_only" not in text
    assert "def inherited" not in text


def test_document_typevar_cannot_create_or_replace_source_identity(tmp_path):
    path, _ = _manifest(tmp_path, "from typing import TypeVar\nT = TypeVar('T')\ndef identity(value: T, /) -> T: ...\n")
    function = _function("identity")
    function["semantics"]["returns"] = [{"kind": "argument", "exported_argument": "value", "scope": "fixture-C-function", "index": 0}]
    inventory = _inventory(_module("probe", [function]))
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    assert _record(report, "identity", "returns")["outcome"] == "unsupported"
    assert _record(report, "identity", "parameter:0:value")["outcome"] == "unsupported"
    parsed = ast.parse(declarations["modules/probe.pyi"])
    definition = next(node for node in parsed.body if isinstance(node, ast.FunctionDef))
    assert ast.unparse(definition.args.posonlyargs[0].annotation) == ast.unparse(definition.returns)
    assert ast.unparse(definition.returns).startswith("_T_")


@pytest.mark.parametrize("binding", ["qualified", "unresolved", "wrong-target"])
def test_alias_comparison_requires_qualified_exact_target_binding(tmp_path, binding):
    path, _ = _manifest(tmp_path, "def truth_alias(value: object, /) -> bool: ...\n")
    truth = _function()
    alias = _function("truth_alias")
    alias["alias_of"] = "truth"
    alias["alias_target_identity"] = copy.deepcopy(truth["identity"])
    alias["alias_binding"] = {"status": "source-resolved", "target_name": "truth"}
    if binding == "unresolved":
        alias["alias_binding"]["status"] = "unresolved"
    elif binding == "wrong-target":
        alias["alias_target_identity"] = {"container": "wrong-TU", "export": "truth"}
    inventory = _inventory(_module("probe", [truth, alias]))
    report, _ = _merge_unchanged(inventory, load_enrichment(path))
    if binding == "qualified":
        assert _record(report, "truth_alias", "returns")["outcome"] == "confirmed"
    else:
        row = _record(report, "truth_alias", "declaration")
        assert row["outcome"] == "unjoined"
        assert not any(row["outcome"] == "confirmed" for row in report["records"])


@pytest.mark.parametrize("barrier", ["module", "class", "member", "duplicate-module", "duplicate-member", "owner-kind"])
def test_join_requires_every_available_unique_occurrence(tmp_path, barrier):
    path, _ = _manifest(tmp_path, "class Native:\n    def method(self, value: object, /) -> bool: ...\n")
    method = _function("method", offset=1)
    native = _class("Native", [method])
    module = _module("probe", [native])
    inventory = _inventory(module)
    if barrier == "module":
        module["resolution"]["availability"]["status"] = "unresolved"
    elif barrier == "class":
        native["resolution"]["availability"]["status"] = "unresolved"
    elif barrier == "member":
        method["resolution"]["availability"]["status"] = "unresolved"
    elif barrier == "duplicate-module":
        inventory["modules"].append(copy.deepcopy(module))
    elif barrier == "duplicate-member":
        native["members"].append(copy.deepcopy(method))
    else:
        native["kind"] = "constant"
    # Rendering ambiguous surfaces is intentionally unavailable; matching still must reject.
    before = copy.deepcopy(inventory["modules"])
    report = merge_enrichment(inventory, load_enrichment(path))
    assert inventory["modules"] == before
    assert _record(report, "Native.method", "declaration")["outcome"] == "unjoined"


def test_selected_frozen_literal_precision_exists_without_enrichment(tmp_path):
    physical = tmp_path / "selected.py"
    physical.write_text("def answer(value):\n    return 42\n", encoding="utf-8")
    scope = {"schema_version": 1, "roots": {"fixture": str(tmp_path)}, "groups": {"runtime_frozen_python": [
        {"root": "fixture", "path": "selected.py", "target_path": "probe.py", "owner": "fixture-owner", "provenance": {"kind": "fixture"}}]}}
    modules = collect_frozen_modules(scope)
    inventory = _inventory(*modules)
    source = inventory["modules"][0]["exports"][0]
    assert source["semantics"]["return_resolution"] == "source-resolved"
    assert source["semantics"]["returns"][0]["kind"] == "int"
    path, _ = _manifest(tmp_path, "def answer(value: bool) -> str: ...\n")
    report, declarations = _merge_unchanged(inventory, load_enrichment(path))
    assert _record(report, "answer", "parameter:0:value")["outcome"] == "documentation-unverified"
    assert _record(report, "answer", "returns")["outcome"] == "conflict"
    assert "-> int" in declarations["modules/probe.pyi"]


@pytest.mark.parametrize("change", ["size", "hash", "syntax", "utf8", "extra-file", "missing-file", "file-symlink", "tree-symlink"])
def test_load_rejects_unpinned_or_unsafe_tree(tmp_path, change):
    path, manifest = _manifest(tmp_path)
    declaration = tmp_path / "tree" / "input.pyi"
    if change == "size":
        manifest["files"][0]["bytes"] += 1
    elif change == "hash":
        manifest["files"][0]["sha256"] = "0" * 64
    elif change in ("syntax", "utf8"):
        declaration.write_bytes(b"def broken(: ...\n" if change == "syntax" else b"\xff")
        manifest["files"][0].update(_pin(declaration, relative="input.pyi"))
    elif change == "extra-file":
        (declaration.parent / "unlisted.pyi").write_text("def surprise(): ...\n", encoding="utf-8")
    elif change == "missing-file":
        declaration.unlink()
    elif change == "file-symlink":
        actual = tmp_path / "outside.pyi"
        declaration.rename(actual)
        declaration.symlink_to(actual)
    else:
        actual = tmp_path / "real-tree"
        declaration.parent.rename(actual)
        (tmp_path / "tree").symlink_to(actual, target_is_directory=True)
    _save(path, manifest)
    with pytest.raises(ValueError):
        load_enrichment(path)


@pytest.mark.parametrize("name", ["LICENSE.txt", "evidence.txt"])
@pytest.mark.parametrize("change", ["missing", "hash", "symlink"])
def test_notice_and_source_evidence_pins_are_loaded_not_just_reported(tmp_path, name, change):
    path, _ = _manifest(tmp_path)
    member = tmp_path / name
    if change == "missing":
        member.unlink()
    elif change == "hash":
        member.write_bytes(b"X" * member.stat().st_size)
    else:
        real = tmp_path / ("real-" + name)
        member.rename(real)
        member.symlink_to(real)
    with pytest.raises(ValueError):
        load_enrichment(path)


def test_target_consumed_bytes_are_freshness_checked(tmp_path):
    path, manifest = _manifest(tmp_path)
    selected = tmp_path / "selected.c"
    selected.write_bytes(b"int selected;\n")
    manifest["target"] = {"port": "unix", "target": "fixture", "variant": "standard", "inputs": [_pin(selected)]}
    _save(path, manifest)
    bundle = load_enrichment(path)
    selected.write_bytes(b"int modified;\n")
    with pytest.raises(ValueError):
        verify_enrichment(bundle)


@pytest.mark.parametrize("change", ["declaration", "added", "deleted", "manifest", "evidence", "notice", "symlink", "same-byte-replacement"])
def test_verify_covers_every_consumed_byte_and_tree_membership(tmp_path, change):
    path, _ = _manifest(tmp_path)
    bundle = load_enrichment(path)
    declaration = tmp_path / "tree" / "input.pyi"
    captured_notices = copy.deepcopy(bundle["notice_artifacts"])
    if change == "declaration":
        data = declaration.read_bytes()
        declaration.write_bytes(data.replace(b"bool", b"str "))
        assert len(data) == declaration.stat().st_size
    elif change == "added":
        (declaration.parent / "added.pyi").write_text("def added(): ...\n", encoding="utf-8")
    elif change == "deleted":
        declaration.unlink()
    elif change == "manifest":
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    elif change == "evidence":
        (tmp_path / "evidence.txt").unlink()
    elif change == "notice":
        (tmp_path / "LICENSE.txt").write_bytes(b"X" * len(_NOTICE))
    elif change == "symlink":
        outside = tmp_path / "moved.pyi"
        declaration.rename(outside)
        declaration.symlink_to(outside)
    else:
        raw = declaration.read_bytes()
        replacement = tmp_path / "replacement.pyi"
        replacement.write_bytes(raw)
        replacement.replace(declaration)
    with pytest.raises(ValueError):
        verify_enrichment(bundle)
    assert bundle["notice_artifacts"] == captured_notices
    assert list(bundle["notice_artifacts"].values()) == [_NOTICE]


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(schema_version=2),
    lambda m: m.update(qualifications={"truth": True}),
    lambda m: m["producer"]["source"].update(revision="main"),
    lambda m: m["sources"][0].update(revision=None, content_sha256=None),
    lambda m: m["sources"][0].update(role="trusted"),
    lambda m: m["sources"].append(copy.deepcopy(m["sources"][0])),
    lambda m: m["files"].append(copy.deepcopy(m["files"][0])),
    lambda m: m["files"][0].update(path="../outside.pyi"),
    lambda m: m["files"][0].update(source_ids=["absent"]),
    lambda m: m["files"][0].update(license_ids=["absent"]),
    lambda m: m["licenses"][0].update(id="../unsafe"),
    lambda m: m["licenses"][0].update(modified="false"),
    lambda m: m["licenses"][0].update(notices=[]),
    lambda m: m["licenses"][0]["notices"][0].update(path="/absolute/notice"),
    lambda m: m["files"][0].update(symbol_lineage=[{"qualname": "absent", "source_ids": ["docs"], "license_ids": ["fixture-MIT"]}]),
])
def test_manifest_schema_pins_and_lineage_are_mandatory(tmp_path, mutate):
    path, manifest = _manifest(tmp_path)
    mutate(manifest)
    _save(path, manifest)
    with pytest.raises(ValueError):
        load_enrichment(path)


def test_duplicate_json_keys_and_opaque_legacy_input_fail(tmp_path):
    path, _ = _manifest(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_enrichment(path)
    path.write_text("opaque enrichment", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid manifest"):
        load_enrichment(path)


@pytest.mark.parametrize("selected", ["compiler", "producer", "missing", "hash-conflict", "size-conflict", "target-conflict", "enrichment-self-claim"])
def test_exact_target_claim_uses_real_selected_inputs_only(tmp_path, selected):
    path, manifest = _manifest(tmp_path)
    input_path = tmp_path / "selected.c"
    input_path.write_bytes(b"int selected;\n")
    pin = _pin(input_path)
    manifest["target"] = {"port": "unix", "target": "fixture", "variant": "standard", "inputs": [pin]}
    _save(path, manifest)
    bundle = load_enrichment(path)
    inventory = _inventory(_module("probe", [_function()]))
    if selected in ("compiler", "hash-conflict"):
        identity = {"path": pin["path"], "resolved": pin["path"], "sha256": pin["sha256"]}
        if selected == "hash-conflict":
            identity["sha256"] = "0" * 64
        inventory["build"]["consumed_inputs"] = {pin["path"]: identity}
    elif selected in ("producer", "size-conflict"):
        inventory["provenance"]["producer_inputs"]["source"] = copy.deepcopy(pin)
        if selected == "size-conflict":
            inventory["provenance"]["producer_inputs"]["source"]["bytes"] += 1
    elif selected == "target-conflict":
        inventory["build"]["variant"] = "minimal"
    elif selected == "enrichment-self-claim":
        inventory["provenance"]["producer_inputs"]["enrichment"] = {"inputs": bundle["inputs"], "provenance": bundle["provenance"]}
    if selected in ("compiler", "producer"):
        report, _ = _merge_unchanged(inventory, bundle)
        assert report["target_match"]["status"] == "exact-build-inputs-matched"
    else:
        with pytest.raises(ValueError, match="exact-build"):
            merge_enrichment(inventory, bundle)
        assert "enrichment" not in inventory


def test_unmatched_reference_records_source_version_difference(tmp_path):
    path, _ = _manifest(tmp_path)
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function()])), load_enrichment(path))
    target = report["target_match"]
    assert target["status"] == "unmatched-reference"
    assert target["source_versions"][0]["revision_match"] is False
    assert target["source_versions"][0]["version"] == "fixture-1"


def test_symbol_specific_licence_and_both_conflicting_lineages_are_retained(tmp_path):
    path, manifest = _manifest(tmp_path, "def truth(value: bool, /) -> str: ...\n")
    apache_notice = b"Copyright Apache fixture author\nApache-2.0 fixture notice\n"
    (tmp_path / "APACHE-NOTICE.txt").write_bytes(apache_notice)
    manifest["licenses"].append({"id": "fixture-Apache", "expression": "Apache-2.0", "copyright": "Copyright Apache fixture author",
                                 "notices": [_pin(tmp_path / "APACHE-NOTICE.txt", relative="APACHE-NOTICE.txt")], "modified": True})
    manifest["files"][0]["symbol_lineage"] = [{"qualname": "truth", "source_ids": ["docs"], "license_ids": ["fixture-Apache"]}]
    _save(path, manifest)
    bundle = load_enrichment(path)
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function()])), bundle)
    row = _record(report, "truth", "returns")
    assert row["outcome"] == "conflict"
    license_row = row["document"]["lineage"]["licenses"][0]
    assert license_row["expression"] == "Apache-2.0"
    assert license_row["modified"] is True
    assert license_row["copyright"] == "Copyright Apache fixture author"
    assert row["source"]["value"] == [{"kind": "bool"}]
    assert report["source_lineage"]["source_revision"] == "b" * 40
    assert set(bundle["notice_artifacts"].values()) == {_NOTICE, apache_notice}
    for name in bundle["notice_artifacts"]:
        assert name.startswith("enrichment-notices/")
        assert not Path(name).is_absolute()
        assert ".." not in Path(name).parts


def test_doc_enriched_requires_runtime_and_documentation_parents(tmp_path):
    path, manifest = _manifest(tmp_path)
    merged_source = copy.deepcopy(manifest["sources"][0])
    merged_source.update(id="merged", role="doc-enriched")
    manifest["sources"].append(merged_source)
    manifest["files"][0]["source_ids"] = ["merged", "docs"]
    _save(path, manifest)
    with pytest.raises(ValueError, match="parents"):
        load_enrichment(path)
    runtime_source = copy.deepcopy(manifest["sources"][0])
    runtime_source.update(id="runtime", role="runtime-observation")
    manifest["sources"].append(runtime_source)
    manifest["files"][0]["source_ids"].append("runtime")
    _save(path, manifest)
    bundle = load_enrichment(path)
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function()])), bundle)
    assert {source["role"] for source in _record(report, "truth", "returns")["document"]["lineage"]["sources"]} == {
        "documentation", "runtime-observation", "doc-enriched"}


def test_tree_identity_and_consumed_identity_change_with_licence_lineage(tmp_path):
    path, manifest = _manifest(tmp_path)
    first = load_enrichment(path)
    manifest["licenses"][0]["modified"] = True
    _save(path, manifest)
    second = load_enrichment(path)
    assert first["tree_identity"]["sha256"] != second["tree_identity"]["sha256"]


@pytest.mark.parametrize("imports,annotation", [
    ("from typing import Union as U\nimport builtins as b\nfrom alien import *\n", "U[b.int, b.str]"),
    ("import builtins as b\nfrom alien import *\n", "b.int"),
    ("from typing import Union as U\nif FLAG:\n    from alien import *\n", "U[int, str]"),
])
def test_star_import_invalidates_all_annotation_aliases(tmp_path, imports, annotation):
    path, _ = _manifest(tmp_path, imports + f"def truth(value: object, /) -> {annotation}: ...\n")
    function = _function(returns="int")
    if "U[" in annotation:
        function["semantics"]["returns"] = [{"kind": "int"}, {"kind": "str"}]
    report, _ = _merge_unchanged(_inventory(_module("probe", [function])), load_enrichment(path))
    row = _record(report, "truth", "returns")
    assert row["outcome"] == "unsupported"
    assert row["candidate"]["normalized"] is None


@pytest.mark.parametrize("decorator,imports", [
    ("ov", "from typing import overload as ov\n"),
    ("t.overload", "import typing as t\n"),
    ("staticmethod", ""),
    ("classmethod", ""),
])
def test_star_import_cannot_leave_decorator_bindings_trusted(tmp_path, decorator, imports):
    text = imports + f"from alien import *\n@{decorator}\ndef truth(value: object, /) -> bool: ...\n"
    path, _ = _manifest(tmp_path, text)
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function()])), load_enrichment(path))
    rows = [row for row in report["records"] if row["document"]["qualname"] == "truth"]
    assert rows
    assert all(row["outcome"] == "unsupported" for row in rows)
    assert all(row["field"] == "declaration" for row in rows)


@pytest.mark.parametrize("rebind", [
    "from alien import truth\n",
    "truth = other\n",
    "if FLAG:\n    def truth(value: object, /) -> int: ...\n",
    "if FLAG:\n    truth = other\n",
    "if FLAG:\n    from alien import truth\n",
    "from alien import *\n",
    "del truth\n",
])
def test_rebound_document_callable_cannot_confirm_known_fields(tmp_path, rebind):
    path, _ = _manifest(tmp_path, "def truth(value: object, /) -> bool: ...\n" + rebind)
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function()])), load_enrichment(path))
    callable_rows = [row for row in report["records"] if row["document"]["qualname"] == "truth"
                     and row["document"]["kind"] == "function"]
    assert callable_rows
    assert all(row["field"] == "declaration" and row["outcome"] == "unsupported" for row in callable_rows)
    assert all("binding" in row["reason"] for row in callable_rows)


@pytest.mark.parametrize("document", [
    "class Native:\n    def method(self, value: object, /) -> bool: ...\n"
    "class Native:\n    def method(self, value: object, /) -> int: ...\n",
    "@alter\nclass Native:\n    def method(self, value: object, /) -> bool: ...\n",
    "class Native(metaclass=alter):\n    def method(self, value: object, /) -> bool: ...\n",
    "class Native:\n    def method(self, value: object, /) -> bool: ...\nfrom alien import Native\n",
    "class Native:\n    def method(self, value: object, /) -> bool: ...\n"
    "if FLAG:\n    class Native:\n        def method(self, value: object, /) -> int: ...\n",
])
def test_unassessed_document_owner_blocks_member_comparisons(tmp_path, document):
    path, _ = _manifest(tmp_path, document)
    inventory = _inventory(_module("probe", [_class("Native", [_function("method", offset=1)])]))
    report, _ = _merge_unchanged(inventory, load_enrichment(path))
    member_rows = [row for row in report["records"] if row["document"]["qualname"] == "Native.method"]
    assert member_rows
    assert all(row["field"] == "declaration" and row["outcome"] == "unsupported" for row in member_rows)


def test_unassessed_outer_owner_barrier_reaches_nested_classes(tmp_path):
    text = ("@alter\nclass Native:\n    class Inner:\n"
            "        def method(self, value: object, /) -> bool: ...\n")
    path, _ = _manifest(tmp_path, text)
    inner = _class("Inner", [_function("method", offset=1)])
    inventory = _inventory(_module("probe", [_class("Native", [inner])]))
    report, _ = _merge_unchanged(inventory, load_enrichment(path))
    assert _record(report, "Native.Inner", "declaration")["outcome"] == "unsupported"
    assert _record(report, "Native.Inner.method", "declaration")["outcome"] == "unsupported"
    assert not any(row["outcome"] == "confirmed" for row in report["records"])


@pytest.mark.parametrize("statement", [
    "x = (int := str)\n",
    "x: object = (int := str)\n",
    "def helper(value=(int := str)): ...\n",
    "def helper(*, value=(int := str)): ...\n",
    "@decorate(int := str)\ndef helper(): ...\n",
    "def helper(value: (int := str)): ...\n",
    "def helper() -> (int := str): ...\n",
    "class Helper((int := object)): ...\n",
    "class Helper(metaclass=(int := object)): ...\n",
    "try:\n    ...\nexcept Error as int:\n    ...\n",
    "match value:\n    case int:\n        ...\n",
    "match value:\n    case [*int]:\n        ...\n",
    "match value:\n    case {**int}:\n        ...\n",
])
def test_all_immediate_binding_forms_shadow_annotation_atoms(tmp_path, statement):
    path, _ = _manifest(tmp_path, statement + "def truth(value: object, /) -> int: ...\n")
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function(returns="int")])), load_enrichment(path))
    row = _record(report, "truth", "returns")
    assert row["outcome"] == "unsupported"
    assert row["candidate"]["normalized"] is None


@pytest.mark.parametrize("statement", [
    "def helper():\n    int = str\n",
    "class Helper:\n    int = str\n",
    "if FLAG:\n    def helper():\n        int = str\n",
    "if FLAG:\n    class Helper:\n        int = str\n",
    "helper = lambda: (int := str)\n",
    "values = [int for int in values]\n",
])
def test_nested_local_bindings_do_not_invent_outer_shadowing(tmp_path, statement):
    path, _ = _manifest(tmp_path, statement + "def truth(value: object, /) -> int: ...\n")
    report, _ = _merge_unchanged(_inventory(_module("probe", [_function(returns="int")])), load_enrichment(path))
    assert _record(report, "truth", "returns")["outcome"] == "confirmed"


@pytest.mark.parametrize("default,documented", [(1, "2"), (False, "True"), ("little", "'big'")])
def test_native_shaped_default_value_conflict_is_single_axis(tmp_path, default, documented):
    parameter = _parameter(required=False, default=default)
    path, _ = _manifest(tmp_path, f"def truth(value: object = {documented}, /) -> bool: ...\n")
    report, declarations = _merge_unchanged(_inventory(_module("probe", [_function(parameters=[parameter])])), load_enrichment(path))
    assert _record(report, "truth", "callshape")["outcome"] == "conflict"
    assert "default value differs" in _record(report, "truth", "callshape")["reason"]
    assert _record(report, "truth", "returns")["outcome"] == "confirmed"
    parsed = ast.parse(declarations["modules/probe.pyi"])
    definition = next(node for node in parsed.body if isinstance(node, ast.FunctionDef))
    assert ast.literal_eval(definition.args.defaults[0]) == default


@pytest.mark.parametrize("annotation,imports", [("Any", "from typing import Any\n"), ("Incomplete", "from _typeshed import Incomplete\n"), (None, "")])
def test_unknown_source_takes_precedence_over_unsupported_document_annotation(tmp_path, annotation, imports):
    suffix = f" -> {annotation}" if annotation else ""
    path, _ = _manifest(tmp_path, imports + f"def truth(value, /){suffix}: ...\n")
    function = _function(returns=None, parameters=[_parameter(contract=None)])
    report, declarations = _merge_unchanged(_inventory(_module("probe", [function])), load_enrichment(path))
    for field in ("parameter:0:value", "returns"):
        row = _record(report, "truth", field)
        assert row["outcome"] == "documentation-unverified"
        assert row["candidate"]["reason"] in row["reason"]
    assert "-> _mpy_Any" in declarations["modules/probe.pyi"]
