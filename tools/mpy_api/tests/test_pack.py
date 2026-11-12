"""Assembly of the generated declarations and a support layer into a standalone typeshed tree."""
import ast
import hashlib
import json
import re

import pytest

from mpy_api.merge import MergeError
from mpy_api.pack import (LICENSE_DIRECTORY, LICENSE_FILES, MYPY_EXTENSIONS, PROVENANCE, STDLIB, VENDOR_ROOT, PackError,
                          Sources, assemble_pack)
from mpy_api.stubast import decode, import_edges

HEAD = "from typing import Any as _mpy_Any\n"

TEMPLATES = {
    "typing.pyi": "class Any: ...\nclass Sequence: ...\n",
    "abc.pyi": "from typing import Any\ndef abstractmethod(f: Any) -> Any: ...\n",
    "builtins.pyi": ("from typing import Sequence\nclass object: ...\nclass ellipsis: ...\nclass function: ...\n"
                     "class float: ...\nclass slice:\n    def indices(self, n: int) -> tuple[int, int, int]: ...\n"
                     "class int:\n    def __truediv__(self, other: int) -> float: ...\n"
                     "    def __getitem__(self, s: slice) -> int: ...\n    def extra(self) -> int: ...\n"
                     "class tuple: ...\nclass str: ...\nclass bytes: ...\n"),
    "collections/__init__.pyi": ("from typing import Sequence\nclass deque(Sequence):\n    def rotate(self, n: int) -> None: ...\n"
                                 "class OrderedDict: ...\nclass namedtuple: ...\n"),
    "io.pyi": ("class BytesIO:\n    def __init__(self, string: bytes = b\"\", /) -> None: ...\n"
               "    def getvalue(self) -> bytes: ...\n"),
    "_internal.pyi": "x: int\n",
}
FAKE_PRISTINE = "f" * 64

DECLARATIONS = {
    "typeshed/stdlib/builtins.pyi": (HEAD + "class int:\n    def extra(self) -> _mpy_Any: ...\nclass object: ...\nclass str: ...\n"
                                     "class bytes: ...\nclass tuple: ...\n"),
    "modules/time.pyi": HEAD + "def sleep_ms(arg0: _mpy_Any, /) -> None: ...\n",
    "modules/io.pyi": HEAD + ("class BytesIO:\n    # unresolved: constructor count/parser/control-flow contract unassessed\n"
                              "    def __init__(self, *args: _mpy_Any, **kwargs: _mpy_Any) -> None: ...\n    def getvalue(self) -> _mpy_Any: ...\n"),
    "modules/frozen/__init__.pyi": HEAD + "import nonexistent_module\n",
    "modules/frozen/member.pyi": HEAD + "x: int\n",
}


def residue(name, module, form):
    return {"name": name, "module": module, "form": form, "checker": "fixture", "failure": "fixture failure"}


@pytest.fixture
def support(tmp_path):
    vendor, overlay = tmp_path / "vendor", tmp_path / "overlay"
    for relative, text in TEMPLATES.items():
        (vendor / "stdlib" / relative).parent.mkdir(parents=True, exist_ok=True)
        (vendor / "stdlib" / relative).write_text(text)
    (vendor / "stubs/mypy-extensions").mkdir(parents=True)
    (vendor / MYPY_EXTENSIONS).write_text("x: int\n")
    for name in LICENSE_FILES:
        (vendor / name).write_text(f"licence {name}\n")
    (overlay / "stdlib/_mpy_pack").mkdir(parents=True)
    (overlay / "stdlib/_mpy_pack/__init__.pyi").write_text("")
    (overlay / "stdlib/_mpy_pack/_hidden.pyi").write_text("y: int\n")
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({
        "upstream": {"name": "fixture-stubs", "version": "1.0", "wheel_sha256": "0" * 64, "source": "urn:fixture", "licence": "MIT"},
        "files": {"stdlib/typing.pyi": {"pristine_sha256": FAKE_PRISTINE, "trim": "cut for the fixture"}},
        "removed": {"stdlib/asyncio/__init__.pyi": "e" * 64},
        "overlay": {"stdlib/_mpy_pack/__init__.pyi": {"derived_from": "none, written for the pack", "licence": "MIT"},
                    "stdlib/_mpy_pack/_hidden.pyi": {"derived_from": "typeshed stdlib/hidden.pyi", "licence": "Apache-2.0, LICENSE_typeshed"}},
        "support_residue": [residue("typing", "typing", "bootstrap-module"), residue("abc", "abc", "bootstrap-module"),
                            residue("collections", "typing", "kept-import"),residue("ellipsis", "builtins", "forced-name"), residue("function", "builtins", "forced-name"),
                            residue("slice", "builtins", "bare-class"), residue("float", "builtins", "bare-class"),
                            residue("deque", "collections", "forced-name")],
    }))
    return Sources(vendor, provenance, overlay), vendor, provenance


@pytest.fixture
def pack(support):
    return assemble_pack(dict(DECLARATIONS), support[0])


def module_texts(pack):
    return {path: decode(data) for path, data in pack.files.items() if path.endswith(".pyi")}


def module_of(path):
    parts = path[len(STDLIB) + 1:-len(".pyi")].split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def stdlib_modules(pack):
    return {module_of(path): text for path, text in module_texts(pack).items() if path.startswith(STDLIB + "/")}


def test_layout_is_one_typeshed_tree_with_licences(pack):
    allowed = re.compile(rf"^({STDLIB}/.+|typeshed/{re.escape(MYPY_EXTENSIONS)}|{LICENSE_DIRECTORY}/[^/]+)$")
    assert all(allowed.match(path) for path in pack.files), sorted(pack.files)
    assert f"{STDLIB}/builtins.pyi" in pack.files and f"{STDLIB}/VERSIONS" in pack.files
    for name in LICENSE_FILES:
        assert pack.files[f"{LICENSE_DIRECTORY}/{name}"] == f"licence {name}\n".encode()


def test_versions_lists_each_top_level_module_or_package(pack):
    top = sorted({module.split(".")[0] for module in stdlib_modules(pack)})
    assert pack.files[f"{STDLIB}/VERSIONS"].decode() == "".join(f"{name}: 3.0-\n" for name in top)
    assert {"builtins", "typing", "abc", "time", "frozen", "collections", "io"} <= set(top)


def test_assembly_is_deterministic_and_independent_of_declaration_order(support, pack):
    reordered = assemble_pack(dict(reversed(list(DECLARATIONS.items()))), support[0])
    assert reordered.files == pack.files and reordered.receipt == pack.receipt and reordered.inputs == pack.inputs


def test_modules_only_the_support_layer_needs_are_in_the_tree(pack):
    modules = set(stdlib_modules(pack))
    assert {"typing", "abc", "collections", "builtins"} <= modules
    assert "_internal" not in modules and "asyncio" not in modules


def test_vendored_module_no_generated_module_replaces_is_copied_as_it_is(pack):
    assert pack.files[f"{STDLIB}/typing.pyi"] == TEMPLATES["typing.pyi"].encode()
    assert pack.records[f"{STDLIB}/typing.pyi"]["treatment"] == "copied"


def test_a_module_the_build_lacks_but_a_residue_name_needs_exists_with_all_its_template(pack):
    collections = stdlib_modules(pack)["collections"]
    assert collections == TEMPLATES["collections/__init__.pyi"]


def test_collided_module_is_the_template_filtered_by_the_generated_names(support):
    declarations = dict(DECLARATIONS)
    declarations["modules/collections.pyi"] = HEAD + "class OrderedDict: ...\nclass other: ...\n"
    collections = stdlib_modules(assemble_pack(declarations, support[0]))["collections"]
    assert "class OrderedDict" in collections and "class other" in collections
    assert "class deque(Sequence):" in collections and "def rotate" in collections
    assert "namedtuple" not in collections


def test_generated_fallback_constructor_gives_way_to_the_template_constructor(pack):
    text = stdlib_modules(pack)["io"]
    assert "def __init__(self, string: bytes" in text and "def getvalue" in text
    assert "*args" not in text


def test_builtins_the_checkers_need_by_name_are_bare_classes_when_the_build_lacks_them(pack):
    builtins = stdlib_modules(pack)["builtins"]
    tree = ast.parse(builtins)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    assert {"ellipsis", "function", "float", "slice"} <= set(classes)
    for name in ("float", "slice"):
        assert [type(item) for item in classes[name].body] == [ast.Expr]
    assert "indices" not in builtins
    assert pack.receipt["merges"]["builtins"]["bare"] == ["float", "slice"]


def test_a_class_the_build_has_is_not_made_bare(support):
    declarations = dict(DECLARATIONS)
    declarations["typeshed/stdlib/builtins.pyi"] += "class slice:\n    def indices(self) -> _mpy_Any: ...\n"
    builtins = stdlib_modules(assemble_pack(declarations, support[0]))["builtins"]
    assert "indices" in builtins and "bare" not in str(assemble_pack(declarations, support[0]).receipt["merges"]["builtins"].get("bare", []))


def test_builtin_member_reading_a_class_nothing_provides_is_dropped_and_recorded(support):
    sources, vendor, _ = support
    (vendor / "stdlib/builtins.pyi").write_text(TEMPLATES["builtins.pyi"] + "class complex: ...\nclass long:\n"
                                                "    def __complex__(self) -> complex: ...\n")
    declarations = dict(DECLARATIONS)
    declarations["typeshed/stdlib/builtins.pyi"] += "class long: ...\n"
    pack = assemble_pack(declarations, Sources(vendor, support[2], sources.overlay))
    assert "complex" not in stdlib_modules(pack)["builtins"]
    assert pack.receipt["merges"]["builtins"]["dropped_readers"] == ["long.__complex__"]


def test_a_generated_declaration_reading_a_class_nothing_provides_fails_generation(support):
    declarations = dict(DECLARATIONS)
    declarations["modules/time.pyi"] = HEAD + "def sleep(arg0: complex) -> None: ...\n"
    declarations["modules/collections.pyi"] = HEAD + "def f(x: complex) -> int: ...\n"
    with pytest.raises(MergeError, match="complex"):
        assemble_pack(declarations, support[0])


def test_modules_the_build_closes_stay_closed_and_open_ones_stay_open(support):
    declarations = dict(DECLARATIONS)
    declarations["modules/collections.pyi"] = (HEAD + "class OrderedDict:\n    def __getattr__(self, name: _mpy_Any) -> _mpy_Any: ...\n"
                                               "class deque: ...\n")
    texts = stdlib_modules(assemble_pack(declarations, support[0]))
    assert "__getattr__" in texts["collections"] and "__getattr__" not in texts["time"]


def test_every_import_of_the_tree_resolves_inside_it_except_generated_ones_recorded(pack):
    modules = stdlib_modules(pack)
    for module, text in modules.items():
        package = f"{STDLIB}/{module.replace('.', '/')}/__init__.pyi" in pack.files
        for edge in import_edges(ast.parse(text), module, package):
            assert edge.module in modules or module == "frozen", (module, edge.module)
    assert list(pack.receipt["unresolved_generated_imports"]) == ["frozen"]


def test_provenance_records_each_file_with_origin_hash_and_trim(pack):
    support = {path: record for path, record in pack.records.items() if record["role"] == "support"}
    typing = support[f"{STDLIB}/typing.pyi"]
    assert typing["origin"] == "fixture-stubs 1.0" and typing["pristine_sha256"] == FAKE_PRISTINE
    assert typing["trim"] == "cut for the fixture" and typing["source_sha256"] == hashlib.sha256(pack.files[f"{STDLIB}/typing.pyi"]).hexdigest()
    assert "pristine_sha256" not in support[f"{STDLIB}/abc.pyi"]
    builtins = pack.records[f"{STDLIB}/builtins.pyi"]
    assert builtins["role"] == "runtime-builtins" and builtins["treatment"] == "merged" and builtins["template"] == "stdlib/builtins.pyi"
    assert pack.records[f"{STDLIB}/time.pyi"] == {"role": "runtime-module", "treatment": "generated"}
    assert pack.records[f"{STDLIB}/VERSIONS"]["role"] == "typeshed-versions"
    assert set(pack.records) == set(pack.files)
    consumed = {entry["path"]: entry for entry in pack.inputs["files"]}
    assert consumed["stdlib/typing.pyi"]["pristine_sha256"] == FAKE_PRISTINE and "stdlib/abc.pyi" in consumed
    assert pack.inputs["origin"]["version"] == "1.0"


def test_support_residue_is_recorded_in_the_receipt(pack):
    entries = pack.receipt["support_residue"]
    assert {(entry["module"], entry["name"], entry["form"]) for entry in entries} == {
        ("typing", "typing", "bootstrap-module"), ("abc", "abc", "bootstrap-module"), ("typing", "collections", "kept-import"),
        ("builtins", "ellipsis", "forced-name"), ("builtins", "function", "forced-name"), ("builtins", "slice", "bare-class"),
        ("builtins", "float", "bare-class"), ("collections", "deque", "forced-name")}
    assert all(entry["checker"] and entry["failure"] for entry in entries)


def test_the_tree_names_no_host_path(pack):
    absolute = re.compile(r"(?<![\w.:/+~-])/(?:home|tmp|Users|root|var|usr|opt|mnt|srv)/")
    for path, data in pack.files.items():
        assert not absolute.search(decode(data)), path
    assert not absolute.search(json.dumps([pack.records, pack.inputs, pack.receipt]))


@pytest.mark.parametrize("damage", [
    lambda data: data.pop("removed"),
    lambda data: data.pop("overlay"),
    lambda data: data["support_residue"].append({"name": "x"}),
    lambda data: data["support_residue"].append(residue("x", "builtins", "invented-form")),
    lambda data: data.__setitem__("upstream", []),
])
def test_malformed_provenance_is_refused(support, damage):
    sources, vendor, provenance = support
    data = json.loads(provenance.read_text())
    damage(data)
    provenance.write_text(json.dumps(data))
    with pytest.raises(PackError, match="malformed|residue"):
        Sources(vendor, provenance, sources.overlay)


def test_declarations_without_builtins_or_with_other_paths_are_refused(support):
    with pytest.raises(PackError, match="builtins"):
        assemble_pack({"modules/time.pyi": HEAD}, support[0])
    with pytest.raises(PackError, match="unexpected declaration path"):
        assemble_pack({"typeshed/stdlib/builtins.pyi": HEAD, "elsewhere/time.pyi": HEAD}, support[0])


def test_the_vendored_tree_and_its_provenance_agree():
    provenance = json.loads(PROVENANCE.read_text())
    present = {path.relative_to(VENDOR_ROOT).as_posix() for path in VENDOR_ROOT.rglob("*") if path.is_file()}
    assert set(provenance["files"]) <= present
    assert not set(provenance["removed"]) & present
    for entry in (*provenance["files"].values(), *({"pristine_sha256": digest} for digest in provenance["removed"].values())):
        assert re.fullmatch(r"[0-9a-f]{64}", entry["pristine_sha256"])
    for entry in provenance["files"].values():
        assert entry["trim"] and "\n" not in entry["trim"] and "\u2014" not in entry["trim"]
    assert (VENDOR_ROOT / ".gitattributes").read_text().strip() == "* -text"


def test_the_real_support_layer_assembles_a_tree_that_stands_on_its_own():
    declarations = {"typeshed/stdlib/builtins.pyi": HEAD + "class int: ...\nclass object: ...\nclass str: ...\n",
                    "modules/time.pyi": HEAD + "def sleep(arg0: _mpy_Any, /) -> None: ...\n"}
    pack = assemble_pack(declarations)
    modules = stdlib_modules(pack)
    assert {"builtins", "typing", "time"} <= set(modules)
    for module, text in modules.items():
        package = f"{STDLIB}/{module.replace('.', '/')}/__init__.pyi" in pack.files
        for edge in import_edges(ast.parse(text), module, package):
            assert edge.module in modules, (module, edge.module)
    assert pack.receipt["unresolved_generated_imports"] == {}


def test_an_overlay_file_the_tree_uses_is_recorded_with_its_attribution(support):
    sources, vendor, provenance = support
    declarations = dict(DECLARATIONS)
    declarations["modules/uses_overlay.pyi"] = HEAD + "import _mpy_pack._hidden\n"
    pack = assemble_pack(declarations, sources)
    record = pack.records[f"{STDLIB}/_mpy_pack/_hidden.pyi"]
    assert record["origin"] == "mpy-api overlay" and record["derived_from"] == "typeshed stdlib/hidden.pyi"
    assert record["licence"] == "Apache-2.0, LICENSE_typeshed"


def test_an_overlay_file_without_attribution_is_refused(support):
    sources, vendor, provenance = support
    data = json.loads(provenance.read_text())
    data["overlay"] = {}
    provenance.write_text(json.dumps(data))
    declarations = dict(DECLARATIONS)
    declarations["modules/uses_overlay.pyi"] = HEAD + "import _mpy_pack._hidden\n"
    with pytest.raises(PackError, match="no attribution"):
        assemble_pack(declarations, Sources(vendor, provenance, sources.overlay))


def ancestors(text, name):
    """The classes ``name`` derives from in a stub, through the bases declared in it."""
    bases = {node.name: [ast.unparse(base) for base in node.bases] for node in ast.parse(text).body if isinstance(node, ast.ClassDef)}
    found, todo = set(), list(bases.get(name, []))
    while todo:
        base = todo.pop()
        if base not in found:
            found.add(base)
            todo += bases.get(base, [])
    return found


def test_exception_classes_the_build_declares_with_a_parent_slot_derive_from_base_exception(support):
    """A class the template lacks is generated; the generated hierarchy must still let ``except X`` accept it."""
    declarations = dict(DECLARATIONS)
    declarations["typeshed/stdlib/builtins.pyi"] += (
        "class BaseException: ...\nclass Exception(BaseException): ...\nclass LookupError(Exception): ...\n"
        "class IndexError(LookupError): ...\nclass OverflowError(ArithmeticError): ...\nclass ArithmeticError(Exception): ...\n")
    builtins = stdlib_modules(assemble_pack(declarations, support[0]))["builtins"]
    assert ancestors(builtins, "IndexError") == {"LookupError", "Exception", "BaseException"}
    assert ancestors(builtins, "OverflowError") == {"ArithmeticError", "Exception", "BaseException"}
