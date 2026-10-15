"""Publication: a changed reference input never becomes a published generation, and the receipt lists exactly what is published."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mpy_api.cli import argument_parser, generate, write_generation
from mpy_api.enrichment import load_enrichment, verify_enrichment


def reference(tmp_path):
    tree = tmp_path / "reference"
    tree.mkdir()
    declaration = tree / "fixture.pyi"
    declaration.write_text("def value() -> int: ...\n")
    notice = tmp_path / "NOTICE.txt"
    notice.write_text("Controlled test fixture; external authorship and redistribution rights are not asserted.\n")

    def pin(path, relative):
        content = path.read_bytes()
        return {"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    source_hash = hashlib.sha256(declaration.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "kind": "mpy-api-pyi-enrichment", "tree": "reference",
        "producer": {"name": "controlled-publication-fixture", "version": "1",
                     "source": {"uri": "urn:mpy-api-test:" + source_hash, "revision": source_hash}},
        "sources": [{"id": "fixture", "role": "documentation", "uri": "urn:mpy-api-test:" + source_hash,
                     "revision": None, "version": "1", "content_sha256": source_hash, "evidence": []}],
        "files": [{**pin(declaration, "fixture.pyi"), "module": "fixture",
                   "source_ids": ["fixture"], "license_ids": ["controlled"]}],
        "licenses": [{"id": "controlled", "expression": "LicenseRef-Controlled-Test-Fixture",
                      "copyright": "Controlled fixture; external authorship is not asserted",
                      "notices": [pin(notice, "NOTICE.txt")], "modified": False}],
        "target": None,
    }))
    return load_enrichment(manifest), declaration, notice


@pytest.mark.parametrize("mutation", ["declaration", "notice", "membership"])
def test_reference_changed_while_staging_is_not_published(tmp_path, monkeypatch, mutation):
    bundle, declaration, notice = reference(tmp_path)
    output = tmp_path / "published"
    write_bytes = Path.write_bytes
    changed = False

    def staging_write(path, data):
        nonlocal changed
        result = write_bytes(path, data)
        staged = [index for index, part in enumerate(path.parts) if part.startswith(".mpy-api-")]
        if staged and not changed:
            changed = True
            assert Path(*path.parts[:staged[0] + 1], "generation", "inventory.json").is_file()
            if mutation == "declaration":
                write_bytes(declaration, b"def value() -> str: ...\n")
            elif mutation == "notice":
                write_bytes(notice, b"Changed controlled notice.\n")
            else:
                write_bytes(declaration.parent / "extra.pyi", b"def extra() -> int: ...\n")
        return result

    monkeypatch.setattr(Path, "write_bytes", staging_write)
    with pytest.raises(ValueError):
        surface = {"build": {"fixture": "controlled"}, "coverage": {}}
        write_generation(output, surface,
                         {"modules/fixture.pyi": "def value() -> int: ...\n"},
                         {"enrichment": bundle["provenance"]},
                         notices=bundle["notice_artifacts"],
                         verify=lambda: verify_enrichment(bundle), inventory=surface)
    assert changed
    assert not output.exists()
    assert not list(tmp_path.glob(".mpy-api-*"))


def test_streamed_inventory_is_identified_by_the_receipt(tmp_path):
    inventory = {"build": {"fixture": "controlled"}, "coverage": {"n": 1}, "modules": [{"name": "m", "text": "\u00e9\n"}],
                 "schema_version": 1}
    declarations = {"typeshed/stdlib/builtins.pyi": "x: int\n", "modules/m.pyi": "y: int\n"}
    output = tmp_path / "out" / "published"
    receipt = write_generation(output, inventory, declarations, {"tool": "t"},
                               notices={"enrichment-notices/a/n.txt": b"notice\n"}, verify=lambda: None,
                               inventory=inventory)
    inventory_bytes = (output / "inventory.json").read_bytes()
    assert inventory_bytes == b'{"build":{"fixture":"controlled"},"coverage":{"n":1},"modules":[{"name":"m","text":"\\u00e9\\n"}],"schema_version":1}\n'
    assert json.loads(inventory_bytes) == inventory
    on_disk = json.loads((output / "generation.json").read_bytes())
    assert on_disk == receipt
    assert [record["path"] for record in receipt["artifacts"]] == [
        "enrichment-notices/a/n.txt", "inventory.json", "modules/m.pyi", "typeshed/stdlib/builtins.pyi"]
    for record in receipt["artifacts"]:
        data = (output / record["path"]).read_bytes()
        assert (record["bytes"], record["sha256"]) == (len(data), hashlib.sha256(data).hexdigest())
    assert {record["path"]: record["role"] for record in receipt["artifacts"]} == {
        "enrichment-notices/a/n.txt": "enrichment-license-notice", "inventory.json": "inventory",
        "modules/m.pyi": "runtime-module", "typeshed/stdlib/builtins.pyi": "runtime-builtins"}
    body = {key: value for key, value in receipt.items() if key != "generation_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
    assert receipt["generation_id"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert (output / "generation.json").read_bytes() == (
        json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()
    assert not list((tmp_path / "out").glob(".mpy-api-*"))


def test_inventory_is_published_only_on_request(tmp_path):
    surface = {"build": {"fixture": "controlled"}, "coverage": {"n": 1}}
    declarations = {"typeshed/stdlib/builtins.pyi": "x: int\n"}
    receipts = {}
    for requested in (False, True):
        output = tmp_path / str(requested)
        receipts[requested] = write_generation(output, surface, declarations, {"tool": "t"}, notices={},
                                               verify=lambda: None, inventory=surface if requested else None)
        assert (output / "inventory.json").is_file() is requested
        assert sorted(path.name for path in output.rglob("*") if path.is_file()) == sorted(
            ["builtins.pyi", "generation.json"] + (["inventory.json"] if requested else []))
    without, with_inventory = receipts[False], receipts[True]
    assert [record["path"] for record in with_inventory["artifacts"] if record["path"] != "inventory.json"] == [
        record["path"] for record in without["artifacts"]]
    assert {key: value for key, value in without.items() if key not in ("artifacts", "generation_id")} == {
        key: value for key, value in with_inventory.items() if key not in ("artifacts", "generation_id")}


def test_encoding_failure_leaves_no_staging_or_output(tmp_path):
    output = tmp_path / "published"
    with pytest.raises(ValueError, match="not JSON compliant"):
        bad = {"build": {}, "coverage": {}, "bad": [float("nan")]}
        write_generation(output, bad, {}, {}, notices={}, verify=lambda: None, inventory=bad)
    assert not output.exists()
    assert not list(tmp_path.glob(".mpy-api-*"))


@pytest.mark.parametrize("field", ["port", "target", "variant"])
def test_command_rejects_known_target_mismatch_before_extraction(tmp_path, field):
    bundle, declaration, _ = reference(tmp_path)
    manifest_path = Path(bundle["provenance"]["manifest"]["path"])
    manifest = json.loads(manifest_path.read_text())
    data = declaration.read_bytes()
    config = {"port": "unix", "target": "fixture", "variant": "standard"}
    manifest["target"] = {**config, "inputs": [{"path": str(declaration.resolve()),
                                              "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}
    manifest_path.write_text(json.dumps(manifest))
    config[field] = "different"
    absent = tmp_path / "absent-input"
    output = tmp_path / "published"
    command = [sys.executable, "-c", "from mpy_api.cli import main; raise SystemExit(main())",
               "--micropython-root", str(tmp_path), "--build-dir", str(tmp_path),
               "--compdb", str(absent), "--elf", str(absent), "--map", str(absent),
               "--link-command", str(absent), "--enrichment", str(manifest_path), "--output", str(output)]
    for key, value in config.items():
        command.extend(["--" + key, value])
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([
        str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run(command, text=True, capture_output=True, env=environment)
    assert result.returncode == 1
    assert "exact-build target" in result.stderr
    assert not result.stdout
    assert not output.exists()


ROOT = Path(__file__).resolve().parents[3]

# The smallest linked build whose generation publishes a builtins module: a module table naming
# builtins, its globals with a qstr, an integer and a wrapped C function.
BUILTINS = r"""#include <stdint.h>
#include <stddef.h>
typedef uintptr_t mp_obj_t;
typedef mp_obj_t mp_rom_obj_t;
#define MP_OBJ_NULL ((mp_obj_t)0)
#define MP_OBJ_SENTINEL ((mp_obj_t)4)
#define MP_OBJ_FROM_PTR(p) ((mp_obj_t)(p))
#define MP_ROM_PTR(p) ((mp_rom_obj_t)(p))
#define MP_ROM_QSTR(q) ((mp_rom_obj_t)((((mp_obj_t)(q)) << 3) | 2))
#define MP_ROM_INT(i) ((mp_rom_obj_t)((((mp_obj_t)(i)) << 1) | 1))
enum { MP_QSTR___name__ = 1, MP_QSTR_builtins, MP_QSTR_answer, MP_QSTR_identity };
typedef struct _mp_obj_type_t { const void *type; uint16_t flags, name; } mp_obj_type_t;
typedef struct { const mp_obj_type_t *type; } mp_obj_base_t;
const mp_obj_type_t mp_type_type = { &mp_type_type, 0, 0 };
const mp_obj_type_t mp_type_module = { &mp_type_type, 0, 0 };
const mp_obj_type_t mp_type_fun_builtin_1 = { &mp_type_type, 0, 0 };
typedef struct { mp_rom_obj_t key; mp_rom_obj_t value; } mp_rom_map_elem_t;
typedef struct { size_t used; const mp_rom_map_elem_t *table; } fixture_map_t;
typedef struct { mp_obj_base_t base; fixture_map_t map; } mp_obj_dict_t;
typedef struct { mp_obj_base_t base; mp_obj_dict_t *globals; } mp_obj_module_t;
typedef struct { mp_obj_base_t base; union { mp_obj_t (*_1)(mp_obj_t); } fun; } mp_obj_fun_builtin_fixed_t;
static mp_obj_t identity(mp_obj_t value) { return value; }
static const mp_obj_fun_builtin_fixed_t identity_obj = { .base = { &mp_type_fun_builtin_1 }, .fun = { ._1 = identity } };
static const mp_rom_map_elem_t builtins_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_builtins) },
    { MP_ROM_QSTR(MP_QSTR_answer), MP_ROM_INT(42) },
    { MP_ROM_QSTR(MP_QSTR_identity), MP_ROM_PTR(&identity_obj) },
};
static const mp_obj_dict_t builtins_globals = { .base = { &mp_type_type }, .map = { .used = 3, .table = builtins_globals_table } };
const mp_obj_module_t mp_module_builtins = { .base = { &mp_type_module }, .globals = (mp_obj_dict_t *)&builtins_globals };
const mp_rom_map_elem_t mp_builtin_module_table[] = {
    { MP_ROM_QSTR(MP_QSTR_builtins), MP_ROM_PTR(&mp_module_builtins) },
};
int main(void) { return mp_builtin_module_table[0].value == 0; }
"""


def linked_build(tmp_path, include=None, root=ROOT):
    """A linked fixture build under ``tmp_path``; ``include`` is a directory the compile arguments name."""
    source, build_dir = tmp_path / "py" / "modbuiltins.c", tmp_path / "build"
    source.parent.mkdir(parents=True)
    build_dir.mkdir()
    source.write_text(BUILTINS)
    obj = build_dir / "modbuiltins.o"
    arguments = ["gcc", "-std=gnu99", "-O0", *([f"-I{include}"] if include else []), "-c", str(source), "-o", str(obj)]
    subprocess.run(arguments, cwd=build_dir, check=True)
    database = tmp_path / "compile_commands.json"
    database.write_text(json.dumps([{"directory": str(build_dir), "arguments": arguments,
                                     "file": str(source), "output": str(obj)}]))
    elf, map_path = build_dir / "fixture.elf", build_dir / "fixture.map"
    argv = ["gcc", str(obj), "-Wl,-Map=" + str(map_path), "-o", str(elf)]
    subprocess.run(argv, cwd=build_dir, check=True)
    command = tmp_path / "link.json"
    command.write_text(json.dumps({"argv": argv, "directory": str(build_dir), "exit": 0}))
    return ["--micropython-root", str(root), "--build-dir", str(build_dir), "--compdb", str(database),
            "--port", "unix", "--target", "fixture", "--variant", "minimal",
            "--elf", str(elf), "--map", str(map_path), "--link-command", str(command)]


def published(output):
    return {path.relative_to(output).as_posix(): path.read_bytes()
            for path in sorted(output.rglob("*")) if path.is_file()}


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs the selected C compiler")
@pytest.mark.parametrize("jobs", [1, 2])
def test_requesting_the_inventory_changes_no_stub_and_only_its_artifact_in_the_receipt(tmp_path, jobs):
    common = linked_build(tmp_path) + ["--source-root", f"fixture={tmp_path}", "--jobs", str(jobs)]
    outputs = {}
    for requested in (False, True):
        output = tmp_path / "out" / ("inventory" if requested else "default")
        generate(argument_parser().parse_args(common + ["--output", str(output)]
                                              + (["--inventory"] if requested else [])))
        outputs[requested] = published(output)
    default, with_inventory = outputs[False], outputs[True]
    assert "typeshed/stdlib/builtins.pyi" in default and "def identity(" in default["typeshed/stdlib/builtins.pyi"].decode()
    assert "inventory.json" not in default and "inventory.json" in with_inventory
    stubs = {name: data for name, data in with_inventory.items() if name not in ("inventory.json", "generation.json")}
    assert stubs == {name: data for name, data in default.items() if name != "generation.json"}
    receipts = {requested: json.loads(files["generation.json"]) for requested, files in outputs.items()}
    inventory_record = [record for record in receipts[True]["artifacts"] if record["path"] == "inventory.json"]
    assert len(inventory_record) == 1 and inventory_record[0]["role"] == "inventory"
    receipts[True]["artifacts"].remove(inventory_record[0])
    for receipt in receipts.values():
        del receipt["generation_id"]
    assert receipts[False] == receipts[True]
    inventory = json.loads(with_inventory["inventory.json"])
    assert [unit["translation_unit"] for unit in inventory["translation_units"]] == receipts[False]["coverage"]["selected_translation_units"]
    assert [module["name"] for module in inventory["modules"]] == ["builtins"]
    # The fact objects lived beside the outputs for the run only.
    assert not list((tmp_path / "out").glob(".mpy-api-*"))


ABSOLUTE_PATH = re.compile(r"(?<![\w.:/+~-])/[\w.+~-]+(?:/[\w.+~-]+)+")


def project_run(tmp_path, label, jobs, *extra, named_include=True, headers="headers"):
    """Generate from a copy of the fixture build that lives under its own parent directory, with an include
    directory beside it that ``--source-root`` names unless ``named_include`` is false."""
    parent = tmp_path / label / "a longer differently named parent"
    base, include = parent / "project", parent / headers / label
    base.mkdir(parents=True)
    include.mkdir(parents=True)
    common = linked_build(base, include) + ["--source-root", f"fixture={base}", "--jobs", str(jobs)]
    if named_include:
        common += ["--source-root", f"headers={include}"]
    output = tmp_path / "out" / label
    generate(argument_parser().parse_args(common + ["--output", str(output), *extra]))
    return published(output)


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs the selected C compiler")
def test_publication_does_not_depend_on_where_the_inputs_live(tmp_path):
    first, second = project_run(tmp_path, "one", 1), project_run(tmp_path, "other", 2)
    assert first == second
    assert set(first) == {"generation.json", "typeshed/stdlib/builtins.pyi"}
    for name, data in first.items():
        text = data.decode()
        assert str(tmp_path) not in text and str(ROOT) not in text, name
        assert not ABSOLUTE_PATH.search(text), (name, ABSOLUTE_PATH.search(text).group())
    receipt = json.loads(first["generation.json"])
    assert receipt["build"]["root"] == "micropython"
    assert receipt["build"]["build_dir"] == "build"
    assert receipt["inputs"]["compdb"]["path"] == "fixture/compile_commands.json"
    # The records that hold host paths are identified by their text as published, never by file digests.
    for role in ("compdb", "link_command", "map"):
        assert set(receipt["inputs"][role]) == {"path", "named_bytes", "named_sha256"}
    assert receipt["inputs"]["elf"]["path"] == "build/fixture.elf"
    assert {tool["path"] for tool in receipt["inputs"]["tools"]} >= {"mpy_api/cli.py", "micropython/tools/manifestfile.py"}


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs the selected C compiler")
def test_inventory_names_the_checkout_and_build_paths(tmp_path):
    inventory = project_run(tmp_path, "inventory", 1, "--inventory")["inventory.json"].decode()
    assert str(tmp_path) not in inventory and str(ROOT) not in inventory
    document = json.loads(inventory)
    assert document["build"]["root"] == "micropython" and document["build"]["build_dir"] == "build"
    assert [unit["translation_unit"].split("::")[0] for unit in document["translation_units"]] == ["fixture/py/modbuiltins.c"]


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs the selected C compiler")
def test_a_host_directory_no_root_names_is_an_error(tmp_path):
    with pytest.raises(ValueError, match=r"no root covers: .*/unnamed/.*--source-root NAME=DIR"):
        project_run(tmp_path, "unnamed", 1, named_include=False)
    assert not (tmp_path / "out" / "unnamed").exists()


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs the selected C compiler")
def test_a_named_directory_with_non_ascii_characters_is_named_and_not_reported(tmp_path):
    first = project_run(tmp_path, "one", 1, headers="h\u00e9aders-\u65e5\u672c")
    second = project_run(tmp_path, "two", 1, headers="h\u00e9aders-\u65e5\u672c")
    assert first == second
    with pytest.raises(ValueError, match="no root covers"):
        project_run(tmp_path, "three", 1, named_include=False, headers="h\u00e9aders-\u65e5\u672c")


def checkout_copy(destination):
    """The parts of the MicroPython checkout the generator reads, without git metadata."""
    shutil.copytree(ROOT / "tools" / "mpy_analysis" / "mpy_analysis", destination / "tools" / "mpy_analysis" / "mpy_analysis",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "tools" / "manifestfile.py", destination / "tools" / "manifestfile.py")
    return destination


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs the selected C compiler")
def test_moving_the_checkout_changes_nothing(tmp_path):
    published_runs = []
    for label in ("here", "somewhere else entirely"):
        checkout = checkout_copy(tmp_path / label / "checkout")
        base = tmp_path / label / "project"
        base.mkdir(parents=True)
        output = tmp_path / "out" / label.split()[0]
        command = [sys.executable, "-c", "from mpy_api.cli import main; raise SystemExit(main())",
                   *linked_build(base, root=checkout), "--source-root", f"fixture={base}", "--output", str(output)]
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join([
            str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH", "")])}
        result = subprocess.run(command, text=True, capture_output=True, env=environment)
        assert result.returncode == 0, result.stderr
        published_runs.append(published(output))
    first, second = published_runs
    assert first == second
    assert str(tmp_path) not in first["generation.json"].decode()
    assert json.loads(first["generation.json"])["build"]["root"] == "micropython"


EXTENSIBLE_BUILTINS = BUILTINS + """
#define MP_REGISTER_EXTENSIBLE_MODULE(name, obj) extern const int mp_extensible_##obj
MP_REGISTER_EXTENSIBLE_MODULE(MP_QSTR_builtins, mp_module_builtins);
"""


def test_open_modules_defaults_to_proven_and_accepts_only_its_choices():
    parser = argument_parser()
    required = ["--compdb", "c", "--micropython-root", "r", "--build-dir", "b", "--port", "p", "--target", "t",
                "--variant", "v", "--elf", "e", "--map", "m", "--link-command", "l", "--output", "o"]
    assert parser.parse_args(required).open_modules == "proven"
    assert parser.parse_args(required + ["--open-modules", "all"]).open_modules == "all"
    with pytest.raises(SystemExit):
        parser.parse_args(required + ["--open-modules", "none"])


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs the selected C compiler")
def test_open_modules_selects_the_catch_all_of_an_unproven_module_and_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "BUILTINS", EXTENSIBLE_BUILTINS)
    runs = {}
    for jobs in (1, 2):
        base = tmp_path / f"jobs{jobs}"
        base.mkdir()
        common = linked_build(base) + ["--source-root", f"fixture={base}", "--jobs", str(jobs)]
        for label, extra in (("default", []), ("proven", ["--open-modules", "proven"]), ("all", ["--open-modules", "all"])):
            output = tmp_path / "out" / f"{label}-{jobs}"
            generate(argument_parser().parse_args(common + ["--output", str(output), *extra]))
            runs[label, jobs] = published(output)
    # Every published file, the receipt and its generation_id included, is the same for any --jobs.
    for label in ("default", "proven", "all"):
        assert runs[label, 1] == runs[label, 2], label
    runs = {label: files for (label, jobs), files in runs.items() if jobs == 1}
    stub = "typeshed/stdlib/builtins.pyi"
    assert runs["default"] == runs["proven"]
    assert "__getattr__" not in runs["proven"][stub].decode()
    opened = runs["all"][stub].decode()
    assert "# unresolved: extensible import contributors unassessed" in opened and "def __getattr__(" in opened
    # The unproven module's declarations are otherwise the same.
    assert [line for line in opened.splitlines() if not line.startswith("# unresolved:") and "__getattr__" not in line] == \
        runs["proven"][stub].decode().splitlines()
    receipts = {label: json.loads(files["generation.json"]) for label, files in runs.items()}
    assert receipts["proven"]["inputs"]["declaration_options"] == {"open_modules": "proven"}
    assert receipts["all"]["inputs"]["declaration_options"] == {"open_modules": "all"}
    assert receipts["all"]["generation_id"] != receipts["proven"]["generation_id"]
