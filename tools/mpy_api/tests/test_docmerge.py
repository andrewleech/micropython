"""Documentation fields enrich selected declarations, not the runtime namespace."""

import ast
import copy
import hashlib
import json

import pytest

from mpy_api.declarations import render_declarations
from mpy_api.docmerge import enrich_declarations
from mpy_api.docstubs import default_enrichment_path
from mpy_api.enrichment import load_enrichment
from mpy_api.pack import VENDOR_ROOT
from mpy_api.stubast import index_body, module_names


_NOTICE = b"Copyright documentation fixture author\nPermission granted for fixture use.\n"


def _pin(path, relative):
    raw = path.read_bytes()
    return {"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _bundle(tmp_path, modules):
    tree = tmp_path / "tree"
    tree.mkdir()
    rows = []
    for module, (relative, text) in modules.items():
        path = tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        rows.append(
            {
                **_pin(path, relative),
                "module": module,
                "source_ids": ["docs"],
                "license_ids": ["fixture"],
            }
        )
    notice = tmp_path / "NOTICE.txt"
    notice.write_bytes(_NOTICE)
    manifest = {
        "schema_version": 1,
        "kind": "mpy-api-pyi-enrichment",
        "tree": "tree",
        "producer": {
            "name": "fixture",
            "version": "1",
            "source": {"uri": "urn:fixture", "revision": "a" * 40},
        },
        "sources": [
            {
                "id": "docs",
                "role": "documentation",
                "uri": "urn:fixture:docs",
                "revision": "a" * 40,
                "version": "1",
                "content_sha256": None,
                "evidence": [],
            }
        ],
        "licenses": [
            {
                "id": "fixture",
                "expression": "MIT",
                "copyright": "Copyright documentation fixture author",
                "notices": [_pin(notice, "NOTICE.txt")],
                "modified": False,
            }
        ],
        "files": rows,
        "target": None,
    }
    path = tmp_path / "enrichment.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return load_enrichment(path)


def _parameter(name, kind="positional_only", contract=None, required=True, default=None):
    row = {
        "name": name,
        "kind": kind,
        "required": required,
        "type_resolution": "resolved" if contract else "unresolved",
        "type_contract": {"kind": contract or "unresolved"},
    }
    if default is not None:
        row["default_contract"] = {"kind": type(default).__name__, "value": default}
    return row


def _function(
    name,
    *,
    parameters=None,
    minimum=1,
    maximum=1,
    returns=None,
    offset=0,
    descriptor="function",
    keywords=False,
):
    shape = {
        "minimum": minimum,
        "maximum": maximum,
        "keywords": keywords,
        "implicit_argument_offset": offset,
        "descriptor": descriptor,
    }
    if parameters is not None:
        shape.update(parameters=parameters, parameters_resolution="resolved")
    else:
        shape["parameters_resolution"] = "unresolved"
    return {
        "name": name,
        "kind": "function",
        "identity": {"export": name, "object_id": "fixture:" + name},
        "source": {"source": "/fixture/selected.c", "line": 5},
        "resolution": {"availability": "resolved", "callshape": "resolved"},
        "callshape": shape,
        "semantics": {
            "returns": [{"kind": returns or "unresolved"}],
            "return_resolution": "resolved" if returns else "unresolved",
        },
    }


def _class(name, members, *, constructor=None):
    result = {
        "name": name,
        "kind": "class",
        "identity": {"export": name},
        "type_identity": "fixture:" + name,
        "members": members,
        "bases": [],
        "slots": [],
        "base_resolution": "resolved",
        "resolution": {"availability": "resolved"},
    }
    if constructor:
        result["constructor"] = constructor
    return result


def _surface(module, exports):
    return {
        "schema_version": 1,
        "modules": [
            {
                "name": module,
                "identity": {"module": module},
                "exports": exports,
                "resolution": {"availability": "resolved"},
            }
        ],
        "types": [],
        "build": {"source_revision": "b" * 40},
        "provenance": {},
    }


def _apply(surface, bundle, *, declarations=None, available=None, generated=None):
    before = copy.deepcopy(surface)
    captures = dict(bundle["declaration_texts"])
    generated = render_declarations(copy.deepcopy(surface)) if generated is None else generated
    result, report = enrich_declarations(
        declarations or generated,
        bundle,
        surface,
        available_declarations=available,
        generated_declarations=generated,
    )
    assert surface == before
    assert bundle["declaration_texts"] == captures
    json.dumps(report)
    for text in result.values():
        ast.parse(text)
    return result, report


def _function_node(text, name):
    return index_body(ast.parse(text).body)[name][0]


def test_independent_fields_keep_source_types_names_defaults_and_keyword_policy(tmp_path):
    bundle = _bundle(
        tmp_path,
        {"probe": ("probe.pyi", "def read(doc_value: str, limit: int = 9, /) -> str: ...\n")},
    )
    source = _function(
        "read",
        parameters=[
            _parameter("value", contract="object"),
            _parameter("limit", required=False, default=4),
        ],
        minimum=1,
        maximum=2,
        returns="bool",
    )
    result, report = _apply(_surface("probe", [source]), bundle)
    function = _function_node(result["modules/probe.pyi"], "read")
    assert [arg.arg for arg in function.args.posonlyargs] == ["value", "limit"]
    assert ast.unparse(function.args.posonlyargs[0].annotation) == "object"
    assert ast.unparse(function.args.posonlyargs[1].annotation) == "int"
    assert ast.unparse(function.args.defaults[0]) == "4"
    assert ast.unparse(function.returns) == "bool"
    conflicts = {row["field"] for row in report["records"] if row["outcome"] == "conflict"}
    assert {"parameter:0:value", "default:1:limit", "returns"} <= conflicts
    assert all(
        report["references"][row["document"]["reference"]]["source_ids"] == ["docs"]
        for row in report["records"]
    )
    assert report["lineage"]["sources"]["docs"]["role"] == "documentation"


def test_documentation_fills_optional_literal_default_without_changing_optionality(tmp_path):
    bundle = _bundle(
        tmp_path, {"probe": ("probe.pyi", "def read(size: int = 16, /) -> bytes: ...\n")}
    )
    source = _function(
        "read", parameters=[_parameter("size", required=False)], minimum=0, maximum=1
    )
    result, report = _apply(_surface("probe", [source]), bundle)
    function = _function_node(result["modules/probe.pyi"], "read")
    assert ast.unparse(function.args.defaults[0]) == "16"
    assert ast.unparse(function.args.posonlyargs[0].annotation) == "int"
    assert ast.unparse(function.returns) == "bytes"
    assert any(
        row["field"] == "default:0:size" and row["outcome"] == "applied"
        for row in report["records"]
    )


def test_wrapper_shape_conflict_does_not_block_independent_unknown_return(tmp_path):
    bundle = _bundle(
        tmp_path,
        {
            "probe": (
                "probe.pyi",
                "def read(value: int, extra: str, /) -> bytes: ...\ndef absent() -> int: ...\n",
            )
        },
    )
    result, report = _apply(_surface("probe", [_function("read")]), bundle)
    text = result["modules/probe.pyi"]
    function = _function_node(text, "read")
    assert len(function.args.posonlyargs) == 1
    assert function.args.posonlyargs[0].arg == "arg0"
    assert ast.unparse(function.returns) == "bytes"
    assert "absent" not in module_names(ast.parse(text))
    assert any(
        row["field"] == "callshape" and row["outcome"] == "conflict" for row in report["records"]
    )
    assert report["omissions"]["probe"]["count"] == 1
    assert report["omissions"]["probe"]["kinds"] == {"function": 1}


def test_overloads_and_generic_helpers_are_private_and_arity_compatible(tmp_path):
    docs = (
        "from typing import TypeVar, overload\n_T = TypeVar('_T')\n"
        "@overload\ndef choose(value: int, /) -> int: ...\n"
        "@overload\ndef choose(value: str, /) -> str: ...\n"
        "def identity(value: _T, /) -> _T: ...\n"
    )
    bundle = _bundle(tmp_path, {"probe": ("probe.pyi", docs)})
    result, report = _apply(
        _surface("probe", [_function("choose"), _function("identity")]), bundle
    )
    tree = ast.parse(result["modules/probe.pyi"])
    functions = index_body(tree.body)
    assert len(functions["choose"]) == 2
    assert {ast.unparse(node.returns) for node in functions["choose"]} == {"int", "str"}
    identity = functions["identity"][0]
    variable = identity.args.posonlyargs[0].annotation.id
    assert variable.startswith("_mpy_doc_") and identity.returns.id == variable
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign) and node.targets[0].id == variable
    )
    assert helper.value.args[0].value == variable
    assert all(
        name.startswith("_") or name in {"choose", "identity"} for name in module_names(tree)
    )
    applied_lines = {
        row["document"]["span"]["line"] for row in report["records"] if row["outcome"] == "applied"
    }
    assert {4, 6} <= applied_lines


def test_overloads_cannot_leave_gaps_in_resolved_parser_arity(tmp_path):
    docs = (
        "from typing import overload\n@overload\ndef read() -> bytes: ...\n"
        "@overload\ndef read(a: int, b: int, /) -> bytes: ...\n"
    )
    result, report = _apply(
        _surface(
            "probe",
            [
                _function(
                    "read",
                    minimum=0,
                    maximum=2,
                    parameters=[
                        _parameter("a", required=False),
                        _parameter("b", required=False),
                    ],
                )
            ],
        ),
        _bundle(tmp_path, {"probe": ("probe.pyi", docs)}),
    )
    function = _function_node(result["modules/probe.pyi"], "read")
    assert len(function.args.posonlyargs) == 2
    assert all(ast.unparse(value) == "..." for value in function.args.defaults)
    assert any("do not cover" in row["reason"] for row in report["records"])


def test_split_classes_inherit_documented_fields_without_gaining_base_or_members(tmp_path):
    docs = {
        "machine": ("machine/__init__.pyi", "from .Device import Device as Device\n"),
        "machine.Device": (
            "machine/Device.pyi",
            "from typing import overload\nfrom helpers import _Hash, Buffer\n"
            "class Device(_Hash):\n    @overload\n    def __init__(self): ...\n"
            "    @overload\n    def __init__(self, data: Buffer): ...\n    def absent(self) -> int: ...\n",
        ),
        "helpers": (
            "helpers.pyi",
            "from typing import TypeAlias\nBuffer: TypeAlias = bytes | bytearray\n"
            "class _Hash:\n    def digest(self) -> bytes: ...\n"
            "    def update(self, data: Buffer, /) -> None: ...\n    def hexdigest(self) -> str: ...\n",
        ),
    }
    constructor = {"callable": True, "resolution": "unresolved"}
    selected = _class(
        "Device",
        [
            _function("digest", minimum=1, maximum=1, offset=1),
            _function("update", minimum=2, maximum=2, offset=1, returns="None"),
        ],
        constructor=constructor,
    )
    result, report = _apply(_surface("machine", [selected]), _bundle(tmp_path, docs))
    assert set(result) == {"modules/machine.pyi"}
    tree = ast.parse(result["modules/machine.pyi"])
    device = index_body(tree.body)["Device"][0]
    assert not device.bases
    methods = index_body(device.body)
    assert set(methods) == {"__init__", "digest", "update"}
    assert len(methods["__init__"]) == 2
    assert ast.unparse(methods["digest"][0].returns) == "bytes"
    assert methods["update"][0].args.posonlyargs[1].annotation.id.startswith("_mpy_doc_")
    assert "_Hash" not in module_names(tree)
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "helpers" for node in tree.body
    )
    digest = next(
        row
        for row in report["records"]
        if row["field"] == "returns" and row["document"]["qualname"] == "_Hash.digest"
    )
    assert digest["document"]["reference"] == "helpers.pyi" and digest["outcome"] == "applied"


def test_absent_runtime_import_skips_only_dependent_annotation(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from absent import Hidden\ndef read(value: Hidden, /) -> str: ...\n",
        ),
        "absent": ("absent.pyi", "class Hidden:\n    def method(self) -> int: ...\n"),
    }
    result, report = _apply(_surface("probe", [_function("read")]), _bundle(tmp_path, docs))
    text = result["modules/probe.pyi"]
    function = _function_node(text, "read")
    assert ast.unparse(function.args.posonlyargs[0].annotation).startswith("_mpy_Any")
    assert ast.unparse(function.returns) == "str"
    assert "import absent" not in text and "from absent" not in text and "class Hidden" not in text
    assert any(
        row["outcome"] == "skipped" and "not a selected runtime type" in row["reason"]
        for row in report["records"]
    )


def test_structural_helper_dependencies_resolve_from_published_support_names(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from helpers import Readable\ndef read(value: Readable, /) -> bytes: ...\n",
        ),
        "helpers": (
            "helpers.pyi",
            "from typing import Protocol\nclass Readable(Protocol):\n    def read(self) -> bytes: ...\n",
        ),
    }
    available = {"typeshed/stdlib/typing.pyi": "class Any: ...\nclass Protocol: ...\n"}
    result, report = _apply(
        _surface("probe", [_function("read")]), _bundle(tmp_path, docs), available=available
    )
    tree = ast.parse(result["modules/probe.pyi"])
    helper = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert helper.name.startswith("_mpy_doc_")
    function = index_body(tree.body)["read"][0]
    assert function.args.posonlyargs[0].annotation.id == helper.name
    assert all(node.module == "typing" for node in tree.body if isinstance(node, ast.ImportFrom))
    assert any(
        row["field"] == "type-helper" and row["outcome"] == "applied" for row in report["records"]
    )


def test_support_module_existence_does_not_authorize_missing_imported_name(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import SupportsIndex\ndef read(value: SupportsIndex, /) -> bytes: ...\n",
        )
    }
    available = {"typeshed/stdlib/typing.pyi": "class Any: ...\n"}
    result, report = _apply(
        _surface("probe", [_function("read")]), _bundle(tmp_path, docs), available=available
    )
    assert "SupportsIndex" not in result["modules/probe.pyi"]
    assert any("lacks typing.SupportsIndex" in row["reason"] for row in report["records"])


def test_properties_staticmethods_and_classmethods_keep_selected_membership(tmp_path):
    docs = (
        "class Device:\n    @property\n    def size(self) -> int: ...\n"
        "    @staticmethod\n    def encode(value: str, /) -> bytes: ...\n"
        "    @classmethod\n    def create(cls, value: int, /) -> Device: ...\n"
        "    @property\n    def absent(self) -> str: ...\n"
    )
    size = {"name": "size", "kind": "unknown", "resolution": {"availability": "resolved"}}
    device = _class(
        "Device",
        [
            size,
            _function("encode", descriptor="staticmethod"),
            _function("create", minimum=2, maximum=2, offset=1, descriptor="classmethod"),
        ],
    )
    result, _ = _apply(
        _surface("probe", [device]), _bundle(tmp_path, {"probe": ("probe.pyi", docs)})
    )
    klass = _function_node(result["modules/probe.pyi"], "Device")
    methods = index_body(klass.body)
    assert set(methods) == {"size", "encode", "create"}
    assert [ast.unparse(d) for d in methods["size"][0].decorator_list] == ["property"]
    assert [ast.unparse(d) for d in methods["encode"][0].decorator_list] == ["staticmethod"]
    assert [ast.unparse(d) for d in methods["create"][0].decorator_list] == ["classmethod"]
    assert ast.unparse(methods["create"][0].returns) == "Device"


def test_descriptor_conflict_retains_source_binding_and_accepts_independent_result(tmp_path):
    docs = "class Device:\n    @staticmethod\n    def read(value: int, /) -> bytes: ...\n"
    device = _class("Device", [_function("read", minimum=2, maximum=2, offset=1)])
    result, report = _apply(
        _surface("probe", [device]), _bundle(tmp_path, {"probe": ("probe.pyi", docs)})
    )
    klass = _function_node(result["modules/probe.pyi"], "Device")
    method = index_body(klass.body)["read"][0]
    assert not method.decorator_list
    assert method.args.posonlyargs[0].arg == "self"
    assert ast.unparse(method.returns) == "bytes"
    assert any("binding/decorator contradicts" in row["reason"] for row in report["records"])


def test_application_uses_captured_text_and_rejects_tampered_captures(tmp_path):
    bundle = _bundle(tmp_path, {"probe": ("probe.pyi", "def read(value: int, /) -> bytes: ...\n")})
    (tmp_path / "tree/probe.pyi").write_text(
        "def read(value: str, /) -> str: ...\n", encoding="utf-8"
    )
    result, _ = _apply(_surface("probe", [_function("read")]), bundle)
    assert ast.unparse(_function_node(result["modules/probe.pyi"], "read").returns) == "bytes"
    bundle["declaration_texts"]["probe"] += "# changed\n"
    with pytest.raises(ValueError, match="captured declaration hash differs"):
        enrich_declarations(
            render_declarations(_surface("probe", [_function("read")])),
            bundle,
            _surface("probe", [_function("read")]),
            generated_declarations=render_declarations(_surface("probe", [_function("read")])),
        )


@pytest.mark.parametrize("extra", ["", ", extra: int"])
def test_unavailable_doc_fields_never_erase_richer_target_template(tmp_path, extra):
    docs = {
        "probe": (
            "probe.pyi",
            f"from typing import Any\nfrom absent import Hidden\ndef read(value: Hidden{extra}, /) -> Any: ...\n",
        )
    }
    target = (
        "from typing import Any as _mpy_Any\nclass _Support: ...\n"
        "def read(value: _Support, /) -> bytes: ...\n"
    )
    result, report = _apply(
        _surface("probe", [_function("read")]),
        _bundle(tmp_path, docs),
        declarations={"typeshed/stdlib/probe.pyi": target},
    )
    function = _function_node(result["typeshed/stdlib/probe.pyi"], "read")
    assert len(function.args.posonlyargs) == 1
    assert ast.unparse(function.args.posonlyargs[0].annotation) == "_Support"
    assert ast.unparse(function.returns) == "bytes"
    assert not any(row["outcome"] == "applied" for row in report["records"])


def test_shape_conflict_keeps_existing_template_overload_group(tmp_path):
    docs = {
        "probe": ("probe.pyi", "from typing import Any\ndef read(a: int, b: int, /) -> Any: ...\n")
    }
    target = (
        "from typing import Any as _mpy_Any, overload\n"
        "@overload\ndef read(value: int, /) -> bytes: ...\n"
        "@overload\ndef read(value: str, /) -> str: ...\n"
    )
    result, _ = _apply(
        _surface("probe", [_function("read")]),
        _bundle(tmp_path, docs),
        declarations={"typeshed/stdlib/probe.pyi": target},
    )
    functions = index_body(ast.parse(result["typeshed/stdlib/probe.pyi"]).body)["read"]
    assert len(functions) == 2
    assert [ast.unparse(node.returns) for node in functions] == ["bytes", "str"]
    assert [ast.unparse(node.args.posonlyargs[0].annotation) for node in functions] == [
        "int",
        "str",
    ]


def test_literal_enum_values_cannot_create_unresolved_runtime_dependencies(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import Literal\nfrom absent import Modes\ndef read(value: Literal[Modes.ACTIVE], /) -> bytes: ...\n",
        )
    }
    result, report = _apply(_surface("probe", [_function("read")]), _bundle(tmp_path, docs))
    text = result["modules/probe.pyi"]
    assert "Modes" not in text and "Literal" not in text
    assert ast.unparse(_function_node(text, "read").returns) == "bytes"
    assert any(
        "Literal contains an unqualified runtime value" in row["reason"]
        for row in report["records"]
    )


def test_literal_string_values_are_not_interpreted_as_forward_type_names(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import Literal\ndef read(value: Literal['active', 'idle'], /) -> bytes: ...\n",
        )
    }
    result, _ = _apply(_surface("probe", [_function("read")]), _bundle(tmp_path, docs))
    annotation = _function_node(result["modules/probe.pyi"], "read").args.posonlyargs[0].annotation
    assert isinstance(annotation, ast.Subscript)
    assert [element.value for element in annotation.slice.elts] == ["active", "idle"]


@pytest.mark.parametrize("has_published_binding", [False, True])
def test_class_typevars_bind_to_existing_pack_generic_or_are_skipped(
    tmp_path, has_published_binding
):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import Generic, TypeVar\n_T = TypeVar('_T')\nclass Box(Generic[_T]):\n    def get(self) -> _T: ...\n",
        )
    }
    surface = _surface(
        "probe", [_class("Box", [_function("get", minimum=1, maximum=1, offset=1)])]
    )
    target = None
    if has_published_binding:
        target = {
            "typeshed/stdlib/probe.pyi": "from typing import Any as _mpy_Any, Generic, TypeVar\n_P = TypeVar('_P')\n"
            "class Box(Generic[_P]):\n    def get(self) -> _mpy_Any: ...\n"
        }
    result, report = _apply(surface, _bundle(tmp_path, docs), declarations=target)
    text = next(iter(result.values()))
    box = index_body(ast.parse(text).body)["Box"][0]
    result_type = index_body(box.body)["get"][0].returns
    if has_published_binding:
        assert ast.unparse(result_type) == "_P"
        assert ast.unparse(box.bases[0]) == "Generic[_P]"
        assert "_mpy_doc__T_" not in text
    else:
        assert ast.unparse(result_type).startswith("_mpy_Any")
        assert not box.bases
        assert any(
            "no compatible published class binding" in row["reason"] for row in report["records"]
        )


def test_source_capability_rendered_as_any_does_not_become_doc_string(tmp_path):
    parameter = _parameter("value", contract="readable-buffer-capability")
    docs = {"probe": ("probe.pyi", "def read(value: str, /) -> bytes: ...\n")}
    source = _function("read", parameters=[parameter])
    result, report = _apply(_surface("probe", [source]), _bundle(tmp_path, docs))
    function = _function_node(result["modules/probe.pyi"], "read")
    assert ast.unparse(function.args.posonlyargs[0].annotation).startswith("_mpy_Any")
    assert ast.unparse(function.returns) == "bytes"
    conflict = next(row for row in report["records"] if row["field"] == "parameter:0:value")
    assert conflict["outcome"] == "conflict"
    assert conflict["source_field"]["normalized"] == {"kind": "readable-buffer-capability"}


def test_overloads_cover_optional_selected_parser_parameter(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import overload\n"
            "@overload\ndef read(value: int, /) -> bytes: ...\n"
            "@overload\ndef read(value: int, size: int, /) -> bytes: ...\n",
        )
    }
    parameters = [_parameter("value"), _parameter("size", required=False)]
    result, _ = _apply(
        _surface("probe", [_function("read", parameters=parameters, minimum=1, maximum=2)]),
        _bundle(tmp_path, docs),
    )
    functions = index_body(ast.parse(result["modules/probe.pyi"]).body)["read"]
    assert len(functions) == 2 and [len(node.args.posonlyargs) for node in functions] == [1, 2]


def test_incompatible_overload_refinements_leave_no_applied_report_rows(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import overload\n"
            "@overload\ndef read(value: int, /) -> int: ...\n"
            "@overload\ndef read(value: str, /) -> str: ...\n",
        )
    }
    result, report = _apply(
        _surface(
            "probe", [_function("read", parameters=[_parameter("value", contract="object")])]
        ),
        _bundle(tmp_path, docs),
    )
    functions = index_body(ast.parse(result["modules/probe.pyi"]).body)["read"]
    assert (
        len(functions) == 1
        and ast.unparse(functions[0].args.posonlyargs[0].annotation) == "object"
    )
    assert not any(row["outcome"] == "applied" for row in report["records"])


def test_any_alias_is_skipped_instead_of_claimed_as_type_precision(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from helpers import Buffer\ndef read(data: Buffer, /) -> bytes: ...\n",
        ),
        "helpers": (
            "helpers.pyi",
            "from typing import Any, TypeAlias\nBuffer: TypeAlias = bytes | Any\n",
        ),
    }
    result, report = _apply(_surface("probe", [_function("read")]), _bundle(tmp_path, docs))
    function = _function_node(result["modules/probe.pyi"], "read")
    assert ast.unparse(function.args.posonlyargs[0].annotation).startswith("_mpy_Any")
    assert ast.unparse(function.returns) == "bytes"
    assert not any(row["field"] == "type-helper" for row in report["records"])
    assert any("alias/union containing Any" in row["reason"] for row in report["records"])


@pytest.mark.parametrize("documented_default", ["...", "UNKNOWN_CONSTANT"])
def test_unknown_documented_default_preserves_template_value(tmp_path, documented_default):
    docs = {
        "probe": ("probe.pyi", f"def read(size: int = {documented_default}, /) -> bytes: ...\n")
    }
    target = {
        "typeshed/stdlib/probe.pyi": "from typing import Any as _mpy_Any\ndef read(size: int = 7, /) -> bytes: ...\n"
    }
    source = _function(
        "read", parameters=[_parameter("size", required=False)], minimum=0, maximum=1
    )
    result, report = _apply(
        _surface("probe", [source]), _bundle(tmp_path, docs), declarations=target
    )
    function = _function_node(result["typeshed/stdlib/probe.pyi"], "read")
    assert ast.unparse(function.args.defaults[0]) == "7"
    assert any(
        row["field"] == "default:0:size" and row["outcome"] == "skipped"
        for row in report["records"]
    )


def test_wrapper_unknown_parser_default_application_has_its_own_attribution(tmp_path):
    docs = {"probe": ("probe.pyi", "def read(size: int = 16, /) -> bytes: ...\n")}
    result, report = _apply(
        _surface("probe", [_function("read", minimum=0, maximum=1)]), _bundle(tmp_path, docs)
    )
    function = _function_node(result["modules/probe.pyi"], "read")
    assert ast.unparse(function.args.defaults[0]) == "16"
    row = next(row for row in report["records"] if row["field"] == "default:0:size")
    assert row["outcome"] == "applied" and row["before"] == "..." and row["after"] == "16"


def test_reference_attribution_is_shared_and_omissions_are_aggregated(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "def read(value: int, /) -> bytes: ...\n"
            "def absent_one() -> str: ...\ndef absent_two() -> str: ...\n",
        )
    }
    bundle = _bundle(tmp_path, docs)
    _, report = _apply(_surface("probe", [_function("read")]), bundle)
    assert len(report["references"]) == 1 and len(report["lineage"]["sources"]) == 1
    assert report["omissions"]["probe"]["count"] == 2
    assert report["summary"]["omitted"] == 2
    assert all(
        "lineage" not in row["document"] and "sha256" not in row["document"]
        for row in report["records"]
    )
    assert all(row["document"]["reference"] in report["references"] for row in report["records"])
    assert report["references"]["probe.pyi"]["sha256"] == bundle["manifest"]["files"][0]["sha256"]


def test_source_references_preserve_distinct_selected_occurrences(tmp_path):
    left = _function("read", minimum=2, maximum=2, offset=1)
    right = _function("read", minimum=2, maximum=2, offset=1)
    right["source"] = {"source": "/fixture/other.c", "line": 25}
    surface = _surface("probe", [_class("Left", [left]), _class("Right", [right])])
    docs = {
        "probe": (
            "probe.pyi",
            "class Left:\n    def read(self, value: int, /) -> bytes: ...\n"
            "class Right:\n    def read(self, value: str, /) -> int: ...\n",
        )
    }
    _, report = _apply(surface, _bundle(tmp_path, docs))
    references = {}
    for qualified, selected in (("Left.read", left), ("Right.read", right)):
        fields = [row for row in report["records"] if row["document"]["qualname"] == qualified]
        keys = {row["source"] for row in fields}
        assert len(keys) == 1
        key = keys.pop()
        references[qualified] = key
        source = report["symbols"][key]
        assert source["module"] == "probe" and source["qualname"] == qualified
        assert source["identity"] == selected["identity"]
        assert source["source"] == selected["source"]
        assert source["resolution"] == selected["resolution"]
        assert {row["field"] for row in fields} >= {"callshape", "parameter:0:value", "returns"}
    assert references["Left.read"] != references["Right.read"]
    assert all(
        row["source"] is None or row["source"] in report["symbols"] for row in report["records"]
    )


def test_original_rendered_source_fields_keep_precedence_over_template_and_docs(tmp_path):
    source = _function("read", parameters=[_parameter("value", contract="object")], returns="bool")
    docs = {"probe": ("probe.pyi", "def read(value: str, /) -> bytes: ...\n")}
    target = {"typeshed/stdlib/probe.pyi": "def read(value: str, /) -> bytes: ...\n"}
    result, report = _apply(
        _surface("probe", [source]), _bundle(tmp_path, docs), declarations=target
    )
    function = _function_node(result["typeshed/stdlib/probe.pyi"], "read")
    assert ast.unparse(function.args.posonlyargs[0].annotation) == "object"
    assert ast.unparse(function.returns) == "bool"
    assert {row["field"] for row in report["records"] if row["outcome"] == "conflict"} >= {
        "parameter:0:value",
        "returns",
    }


def test_hover_documentation_includes_module_class_constructor_property_and_inherited_method(
    tmp_path,
):
    docs = {
        "probe": (
            "probe.pyi",
            '"""Selected module documentation."""\nfrom helpers import _Hash\n'
            'class Device(_Hash):\n    """Selected device documentation."""\n'
            '    def __init__(self, data: bytes = b"") -> None:\n'
            '        """Construct a selected device."""\n        ...\n'
            "    @property\n    def size(self) -> int:\n"
            '        """Number of buffered bytes."""\n        ...\n'
            '    def absent(self) -> str:\n        """Must not become a selected member."""\n        ...\n'
            'class Absent:\n    """Must not become a selected class."""\n',
        ),
        "helpers": (
            "helpers.pyi",
            "class _Hash:\n    def digest(self) -> bytes:\n"
            '        """Return the hash as bytes."""\n        ...\n'
            '    def hexdigest(self) -> str:\n        """Not selected by this build."""\n        ...\n',
        ),
    }
    size = {"name": "size", "kind": "unknown", "resolution": {"availability": "resolved"}}
    device = _class(
        "Device",
        [_function("digest", minimum=1, maximum=1, offset=1), size],
        constructor={"callable": True, "resolution": "unresolved"},
    )
    result, report = _apply(_surface("probe", [device]), _bundle(tmp_path, docs))
    tree = ast.parse(result["modules/probe.pyi"])
    assert ast.get_docstring(tree) == "Selected module documentation."
    klass = index_body(tree.body)["Device"][0]
    assert ast.get_docstring(klass) == "Selected device documentation."
    members = index_body(klass.body)
    assert set(members) == {"__init__", "digest", "size"} and not klass.bases
    assert ast.get_docstring(members["__init__"][0]) == "Construct a selected device."
    assert ast.get_docstring(members["digest"][0]) == "Return the hash as bytes."
    assert ast.get_docstring(members["size"][0]) == "Number of buffered bytes."
    assert "Absent" not in module_names(tree) and "_Hash" not in module_names(tree)
    records = [row for row in report["records"] if row["field"] == "documentation"]
    assert len(records) == 5 and all(row["outcome"] == "applied" for row in records)
    inherited = next(row for row in records if row["document"]["qualname"] == "_Hash.digest")
    assert inherited["document"]["reference"] == "helpers.pyi"
    text = "Return the hash as bytes.".encode("utf-8")
    assert inherited["after"] == {"bytes": len(text), "sha256": hashlib.sha256(text).hexdigest()}
    assert report["symbols"][inherited["source"]]["qualname"] == "Device.digest"


def test_reference_without_prose_keeps_existing_hover_documentation(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "class Device:\n    @staticmethod\n    def read(value: int, /) -> bytes: ...\n",
        )
    }
    surface = _surface("probe", [_class("Device", [_function("read", descriptor="staticmethod")])])
    target = {
        "typeshed/stdlib/probe.pyi": '"""Existing module hover."""\nfrom typing import Any as _mpy_Any\n'
        'class Device:\n    """Existing class hover."""\n    @staticmethod\n'
        '    def read(value: int, /) -> bytes:\n        """Existing function hover."""\n        ...\n'
    }
    result, report = _apply(surface, _bundle(tmp_path, docs), declarations=target)
    tree = ast.parse(result["typeshed/stdlib/probe.pyi"])
    klass = index_body(tree.body)["Device"][0]
    assert ast.get_docstring(tree) == "Existing module hover."
    assert ast.get_docstring(klass) == "Existing class hover."
    assert ast.get_docstring(index_body(klass.body)["read"][0]) == "Existing function hover."
    assert not any(row["field"] == "documentation" for row in report["records"])


def test_class_hover_is_inserted_before_decorated_first_method_and_helpers_follow_future(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            '"""Module hover."""\nfrom typing import TypeVar\n_T = TypeVar("_T")\n'
            'class Device:\n    """Class hover."""\n    @staticmethod\n'
            "    def read(value: _T, /) -> _T: ...\n",
        )
    }
    surface = _surface("probe", [_class("Device", [_function("read", descriptor="staticmethod")])])
    target = {
        "typeshed/stdlib/probe.pyi": "from __future__ import annotations\nfrom typing import Any as _mpy_Any\n"
        "class Device:\n    @staticmethod\n    def read(value: _mpy_Any, /) -> _mpy_Any: ...\n"
    }
    result, _ = _apply(surface, _bundle(tmp_path, docs), declarations=target)
    tree = ast.parse(result["typeshed/stdlib/probe.pyi"])
    assert ast.get_docstring(tree) == "Module hover."
    assert isinstance(tree.body[1], ast.ImportFrom) and tree.body[1].module == "__future__"
    klass = index_body(tree.body)["Device"][0]
    assert ast.get_docstring(klass) == "Class hover."
    assert [ast.unparse(node) for node in index_body(klass.body)["read"][0].decorator_list] == [
        "staticmethod"
    ]


@pytest.mark.parametrize(
    "minimum,maximum,keywords,signature",
    [
        (0, 0xFFFF, True, "value: int, *, strict: bool = False"),
        (1, 0xFFFF, False, "fmt: str, /, *values: int"),
        (0, 3, False, "value: int, /"),
    ],
)
def test_unresolved_native_wrapper_bounds_are_outer_limits(
    tmp_path, minimum, maximum, keywords, signature
):
    docs = {"probe": ("probe.pyi", f"def read({signature}) -> bytes: ...\n")}
    source = _function("read", minimum=minimum, maximum=maximum, keywords=keywords)
    result, report = _apply(_surface("probe", [source]), _bundle(tmp_path, docs))
    function = _function_node(result["modules/probe.pyi"], "read")
    assert ast.unparse(function.returns) == "bytes"
    assert not any(
        row["field"] == "callshape" and row["outcome"] == "conflict" for row in report["records"]
    )
    if not keywords and maximum == 0xFFFF:
        assert function.args.vararg.arg == "values"
        assert ast.unparse(function.args.vararg.annotation) == "int"


def test_native_keyword_overloads_do_not_need_to_cover_wrapper_sentinel(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import overload\n"
            "@overload\ndef read(value: int, /) -> bytes: ...\n"
            "@overload\ndef read(value: str, /, *, mode: int = 0) -> str: ...\n",
        )
    }
    source = _function("read", minimum=0, maximum=0xFFFF, keywords=True)
    result, report = _apply(_surface("probe", [source]), _bundle(tmp_path, docs))
    functions = index_body(ast.parse(result["modules/probe.pyi"]).body)["read"]
    assert len(functions) == 2
    assert {ast.unparse(node.returns) for node in functions} == {"bytes", "str"}
    assert not any(row["outcome"] == "conflict" for row in report["records"])


def test_native_keyword_reference_cannot_remove_required_positional_minimum(tmp_path):
    docs = {"probe": ("probe.pyi", "def read(value: int) -> bytes: ...\n")}
    source = _function("read", minimum=1, maximum=0xFFFF, keywords=True)
    _, report = _apply(_surface("probe", [source]), _bundle(tmp_path, docs))
    assert any(
        row["field"] == "callshape" and row["outcome"] == "conflict" for row in report["records"]
    )


def test_identical_absolute_and_relative_class_imports_resolve_one_selected_type(tmp_path):
    docs = {
        "machine": ("machine/__init__.pyi", "from .Pin import Pin\nfrom .Signal import Signal\n"),
        "machine.Pin": ("machine/Pin.pyi", "class Pin: ...\n"),
        "machine.Signal": (
            "machine/Signal.pyi",
            "from machine.Pin import Pin\nfrom .Pin import Pin\n"
            "class Signal:\n    def read(self, value: Pin, /) -> Pin: ...\n",
        ),
    }
    surface = _surface(
        "machine",
        [
            _class("Pin", []),
            _class(
                "Signal",
                [
                    _function("read", minimum=2, maximum=2, offset=1),
                ],
            ),
        ],
    )
    result, report = _apply(surface, _bundle(tmp_path, docs))
    tree = ast.parse(result["modules/machine.pyi"])
    method = index_body(index_body(tree.body)["Signal"][0].body)["read"][0]
    assert ast.unparse(method.args.posonlyargs[1].annotation) == "Pin"
    assert ast.unparse(method.returns) == "Pin"
    assert not any("ambiguous imported binding" in row["reason"] for row in report["records"])


def test_duplicate_typevar_imports_keep_generic_reference_binding(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import TypeVar\nfrom typing import TypeVar\n"
            "_T = TypeVar('_T')\ndef read(value: _T, /) -> _T: ...\n",
        )
    }
    result, report = _apply(_surface("probe", [_function("read")]), _bundle(tmp_path, docs))
    function = _function_node(result["modules/probe.pyi"], "read")
    assert ast.unparse(function.returns) == ast.unparse(function.args.posonlyargs[0].annotation)
    assert ast.unparse(function.returns).startswith("_mpy_doc_")
    assert not any("ambiguous imported binding" in row["reason"] for row in report["records"])


def test_conflicting_typing_imports_remain_ambiguous(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import Any as Choice\nfrom typing import SupportsIndex as Choice\n"
            "def read(value: Choice, /) -> bytes: ...\n",
        )
    }
    result, report = _apply(_surface("probe", [_function("read")]), _bundle(tmp_path, docs))
    function = _function_node(result["modules/probe.pyi"], "read")
    assert ast.unparse(function.args.posonlyargs[0].annotation).startswith("_mpy_Any")
    assert any("ambiguous imported binding" in row["reason"] for row in report["records"])


def test_resolved_parser_overload_coverage_uses_parser_not_native_wrapper_sentinel(tmp_path):
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import overload\n"
            "@overload\ndef read(value: int) -> bytes: ...\n"
            "@overload\ndef read(value: int, size: int) -> bytes: ...\n",
        )
    }
    parameters = [
        _parameter("value", kind="positional_or_keyword"),
        _parameter("size", kind="positional_or_keyword", required=False),
    ]
    source = _function("read", minimum=0, maximum=0xFFFF, keywords=True, parameters=parameters)
    result, report = _apply(_surface("probe", [source]), _bundle(tmp_path, docs))
    assert len(index_body(ast.parse(result["modules/probe.pyi"]).body)["read"]) == 2
    assert not any(row["outcome"] == "conflict" for row in report["records"])


def _assert_local_annotation_bindings(text):
    tree = ast.parse(text)
    names = module_names(tree) | {
        "bool",
        "bytes",
        "bytearray",
        "complex",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "memoryview",
        "object",
        "range",
        "set",
        "slice",
        "str",
        "tuple",
        "type",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            expressions = [
                arg.annotation
                for arg in (
                    node.args.posonlyargs
                    + node.args.args
                    + node.args.kwonlyargs
                    + ([node.args.vararg] if node.args.vararg else [])
                    + ([node.args.kwarg] if node.args.kwarg else [])
                )
            ] + [node.returns]
            for expression in expressions:
                if expression is not None:
                    assert {
                        loaded.id
                        for loaded in ast.walk(expression)
                        if isinstance(loaded, ast.Name) and isinstance(loaded.ctx, ast.Load)
                    } <= names


@pytest.mark.parametrize("keyword_default", [False, True])
def test_overloads_install_source_default_values_instead_of_only_reporting_conflicts(
    tmp_path, keyword_default
):
    optional = _parameter(
        "size",
        kind="keyword_only" if keyword_default else "positional_only",
        required=False,
        default=9,
    )
    separator = ", /, *, size: int = 1" if keyword_default else ", size: int = 1, /"
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import overload\n"
            f"@overload\ndef read(value: int{separator}) -> int: ...\n"
            f"@overload\ndef read(value: str{separator}) -> str: ...\n",
        )
    }
    source = _function(
        "read",
        minimum=1,
        maximum=2,
        keywords=keyword_default,
        parameters=[_parameter("value"), optional],
    )
    result, report = _apply(_surface("probe", [source]), _bundle(tmp_path, docs))
    functions = index_body(ast.parse(result["modules/probe.pyi"]).body)["read"]
    assert len(functions) == 2
    for function in functions:
        values = function.args.kw_defaults if keyword_default else function.args.defaults
        assert [ast.unparse(value) for value in values] == ["9"]
    conflicts = [row for row in report["records"] if row["field"] == "default:1:size"]
    assert len(conflicts) == 2
    assert all(
        row["outcome"] == "conflict" and row["before"] == row["after"] == "9" for row in conflicts
    )


@pytest.mark.parametrize(
    "header", ["def read(value, /):", "def read(value: object, /) -> object:"]
)
def test_later_single_reference_preserves_earlier_overload_contracts(tmp_path, header):
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _bundle(
        first_root,
        {
            "probe": (
                "probe.pyi",
                "from typing import overload\n"
                "@overload\ndef read(value: int, /) -> int: ...\n"
                "@overload\ndef read(value: str, /) -> str: ...\n",
            )
        },
    )
    second = _bundle(
        second_root,
        {"probe": ("probe.pyi", header + '\n    """Later hover documentation."""\n    ...\n')},
    )
    surface = _surface("probe", [_function("read")])
    generated = render_declarations(copy.deepcopy(surface))
    before, _ = enrich_declarations(generated, first, surface, generated_declarations=generated)
    result, report = enrich_declarations(before, second, surface, generated_declarations=generated)
    text = result["modules/probe.pyi"]
    functions = index_body(ast.parse(text).body)["read"]
    assert len(functions) == 2
    assert [ast.unparse(node.args.posonlyargs[0].annotation) for node in functions] == [
        "int",
        "str",
    ]
    assert [ast.unparse(node.returns) for node in functions] == ["int", "str"]
    assert all(ast.get_docstring(node) == "Later hover documentation." for node in functions)
    assert any(
        row["field"] == "callshape" and row["outcome"] == "preserved" for row in report["records"]
    )
    _assert_local_annotation_bindings(text)


def test_overloads_restore_positional_only_prefix_of_mixed_native_parser(tmp_path):
    parameters = [
        _parameter("arg0", kind="positional_only"),
        _parameter("mode", kind="positional_or_keyword"),
        _parameter("extra", kind="positional_or_keyword", required=False),
    ]
    source = _function("read", minimum=2, maximum=3, keywords=True, parameters=parameters)
    docs = {
        "probe": (
            "probe.pyi",
            "from typing import overload\n"
            "@overload\ndef read(id: int, mode: int) -> int: ...\n"
            "@overload\ndef read(id: str, mode: int, extra: int) -> str: ...\n",
        )
    }
    result, _ = _apply(_surface("probe", [source]), _bundle(tmp_path, docs))
    functions = index_body(ast.parse(result["modules/probe.pyi"]).body)["read"]
    assert len(functions) == 2
    assert all([arg.arg for arg in node.args.posonlyargs] == ["arg0"] for node in functions)
    assert [[arg.arg for arg in node.args.args] for node in functions] == [
        ["mode"],
        ["mode", "extra"],
    ]


@pytest.mark.parametrize("source_any", ["_mpy_Any", "_mpy_Any_", "_GeneratedUnknown"])
def test_source_any_aliases_are_explicitly_bound_when_template_uses_another_alias(
    tmp_path, source_any
):
    method = _function(
        "put",
        minimum=2,
        maximum=2,
        offset=1,
        returns="None",
        parameters=[_parameter("arg0", contract="readable-buffer-capability")],
    )
    method["receiver_contract"] = {}
    surface = _surface("probe", [_class("Box", [method])])
    generated = {
        path: text.replace("_mpy_Any", source_any)
        for path, text in render_declarations(copy.deepcopy(surface)).items()
    }
    target = {
        "typeshed/stdlib/probe.pyi": "from typing import Any as TargetAny\n"
        "class Box:\n    def put(self, value: TargetAny, /) -> None: ...\n"
    }
    docs = {"probe": ("probe.pyi", "class Box:\n    def put(self, value: str, /) -> None: ...\n")}
    result, report = _apply(
        surface, _bundle(tmp_path, docs), declarations=target, generated=generated
    )
    text = result["typeshed/stdlib/probe.pyi"]
    put = index_body(index_body(ast.parse(text).body)["Box"][0].body)["put"][0]
    assert put.args.posonlyargs[0].annotation is None
    assert ast.unparse(put.args.posonlyargs[1].annotation) == "TargetAny"
    assert any(
        row["field"] == "parameter:0:arg0" and row["outcome"] == "conflict"
        for row in report["records"]
    )
    _assert_local_annotation_bindings(text)


def test_unknown_documented_parameter_fallback_imports_its_generated_any_dependency(tmp_path):
    surface = _surface("probe", [_function("read", minimum=0, maximum=0xFFFF)])
    target = {"typeshed/stdlib/probe.pyi": "def read(*args): ...\n"}
    docs = {"probe": ("probe.pyi", "def read(value, /): ...\n")}
    result, _ = _apply(surface, _bundle(tmp_path, docs), declarations=target)
    text = result["typeshed/stdlib/probe.pyi"]
    function = _function_node(text, "read")
    annotation = function.args.posonlyargs[0].annotation
    assert isinstance(annotation, ast.Name)
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "typing"
        and any(alias.name == "Any" and alias.asname == annotation.id for alias in node.names)
        for node in ast.parse(text).body
    )
    _assert_local_annotation_bindings(text)


def test_source_qualified_any_return_uses_explicit_target_binding(tmp_path):
    source = _function("read", minimum=0, maximum=0, returns="readable-buffer-capability")
    target = {
        "typeshed/stdlib/probe.pyi": "from typing import Any as TargetAny\ndef read() -> str: ...\n"
    }
    docs = {"probe": ("probe.pyi", "def read() -> str: ...\n")}
    result, report = _apply(
        _surface("probe", [source]), _bundle(tmp_path, docs), declarations=target
    )
    text = result["typeshed/stdlib/probe.pyi"]
    assert ast.unparse(_function_node(text, "read").returns) == "TargetAny"
    assert any(
        row["field"] == "returns" and row["outcome"] == "conflict" for row in report["records"]
    )
    _assert_local_annotation_bindings(text)


def test_generated_identity_typevar_dependency_survives_cross_alias_template_namespace(tmp_path):
    source = _function("identity", parameters=[_parameter("value", contract="object")])
    source["semantics"] = {
        "returns": [
            {
                "kind": "argument",
                "exported_argument": "value",
                "scope": "fixture-C-function",
                "index": 0,
            }
        ],
        "return_resolution": "bounded-resolved",
    }
    surface = _surface("probe", [source])
    generated = render_declarations(copy.deepcopy(surface))
    original = _function_node(generated["modules/probe.pyi"], "identity")
    variable = original.returns.id
    target = {
        "typeshed/stdlib/probe.pyi": f"from typing import TypeVar as Factory\n{variable} = Factory({variable!r}, str)\n"
        "def identity(value: object, /) -> str: ...\n"
    }
    docs = {"probe": ("probe.pyi", "def identity(value: object, /) -> str: ...\n")}
    result, report = _apply(
        surface, _bundle(tmp_path, docs), declarations=target, generated=generated
    )
    text = result["typeshed/stdlib/probe.pyi"]
    tree = ast.parse(text)
    identity = index_body(tree.body)["identity"][0]
    assert identity.returns.id == identity.args.posonlyargs[0].annotation.id
    assert identity.returns.id != variable
    helper = index_body(tree.body)[identity.returns.id][0]
    assert isinstance(helper, ast.Assign) and isinstance(helper.value, ast.Call)
    assert helper.value.func.id == "Factory"
    assert [arg.value for arg in helper.value.args] == [identity.returns.id]
    assert ast.unparse(index_body(tree.body)[variable][0].value.args[1]) == "str"
    assert any(
        row["field"] == "returns" and row["outcome"] == "conflict" for row in report["records"]
    )
    _assert_local_annotation_bindings(text)


def test_default_array_reference_and_vendored_members_keep_annotations_locally_bound():
    append = _function(
        "append", minimum=2, maximum=2, offset=1, returns="None", parameters=[_parameter("arg0")]
    )
    extend = _function(
        "extend", minimum=2, maximum=2, offset=1, returns="None", parameters=[_parameter("arg0")]
    )
    length = _function("__len__", minimum=1, maximum=1, offset=1, returns="int", parameters=[])
    for member in (append, extend, length):
        member["receiver_contract"] = {}
    surface = _surface(
        "array",
        [
            _class(
                "array",
                [append, extend, length],
                constructor={"callable": True, "resolution": "unresolved"},
            )
        ],
    )
    template = (VENDOR_ROOT / "stdlib" / "array.pyi").read_text(encoding="utf-8")
    result, _ = _apply(
        surface,
        load_enrichment(default_enrichment_path()),
        declarations={"typeshed/stdlib/array.pyi": template},
    )
    text = result["typeshed/stdlib/array.pyi"]
    tree = ast.parse(text)
    methods = index_body(index_body(tree.body)["array"][0].body)
    for name in ("append", "extend", "__len__"):
        receiver = (methods[name][0].args.posonlyargs + methods[name][0].args.args)[0]
        assert receiver.annotation is None
    assert ast.get_docstring(methods["append"][0]) is not None
    assert not any(
        node.id.startswith("_mpy_Any") for node in ast.walk(tree) if isinstance(node, ast.Name)
    )
    _assert_local_annotation_bindings(text)


def test_generated_unknown_receiver_needs_no_alias_when_template_has_only_variadic_args(tmp_path):
    source = _function("put", minimum=2, maximum=2, offset=1, returns="None")
    source["receiver_contract"] = {}
    surface = _surface("probe", [_class("Box", [source])])
    target = {
        "typeshed/stdlib/probe.pyi": "from typing import Any\nclass Box:\n    def put(*args: Any) -> None: ...\n"
    }
    docs = {"probe": ("probe.pyi", "class Box:\n    def put(self, value: int, /) -> None: ...\n")}
    result, _ = _apply(surface, _bundle(tmp_path, docs), declarations=target)
    text = result["typeshed/stdlib/probe.pyi"]
    put = index_body(index_body(ast.parse(text).body)["Box"][0].body)["put"][0]
    assert put.args.posonlyargs[0].arg == "self" and put.args.posonlyargs[0].annotation is None
    assert ast.unparse(put.args.posonlyargs[1].annotation) == "int"
    _assert_local_annotation_bindings(text)
