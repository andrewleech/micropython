import json
import shutil
import subprocess
from pathlib import Path

import pytest
from mpy_analysis import link_evidence

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None or shutil.which("ar") is None, reason="needs gcc and ar")


def run(*argv, cwd):
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path):
    """A linked program: two compiled objects, one object the linker discards, one archive member."""
    app, vendor, build = tmp_path / "app", tmp_path / "vendor", tmp_path / "build"
    for directory in (app, vendor, build):
        directory.mkdir()
    (app / "cfg.h").write_text("#define CFG 1\n")
    (app / "main.c").write_text(
        '#include <stdint.h>\n#include "cfg.h"\n#include "vendor.h"\n'
        "int helper(void);\nint main(void) { return helper() + vendored() + CFG; }\n"
    )
    (app / "helper.c").write_text("int helper(void) { return 1; }\n")
    (app / "gc.c").write_text("int gc_unused(void) { return 2; }\n")
    (vendor / "vendor.h").write_text("int vendored(void);\n")
    (vendor / "vendor.c").write_text("int vendored(void) { return 3; }\n")

    entries = []
    for source in (app / "main.c", app / "helper.c", app / "gc.c", vendor / "vendor.c"):
        obj = build / f"{source.stem}.o"
        argv = ["gcc", "-c", "-ffunction-sections", f"-I{app}", f"-I{vendor}", "-MD", "-MF", str(obj.with_suffix(".d")), "-o", str(obj), str(source)]
        run(*argv, cwd=build)
        entries.append({"directory": str(build), "arguments": argv, "file": str(source)})
    run("ar", "rcs", "libvendor.a", "vendor.o", cwd=build)
    run("gcc", "-Wl,-Map=app.map", "-Wl,--gc-sections", "-o", "app", "main.o", "helper.o", "gc.o", "libvendor.a", cwd=build)
    database = tmp_path / "compile_commands.json"
    database.write_text(json.dumps(entries))
    return {"database": database, "build": build, "roots": {"app": str(app), "vendor": str(vendor)}, "entries": entries}


def evidence_of(project):
    image = {"id": "app", "role": "application", "elf": str(project["build"] / "app"), "map": str(project["build"] / "app.map")}
    return link_evidence.build_evidence([str(project["database"])], [image], project["roots"])


def test_sources_are_the_objects_that_contribute_bytes(project):
    evidence, selected = evidence_of(project)
    [image] = evidence["images"]

    assert {(item["root"], item["path"]) for item in image["sources"]} == {
        ("app", "main.c"),
        ("app", "helper.c"),
        ("vendor", "vendor.c"),
    }
    assert image["unresolved"] == [] and image["unbound_sources"] == []
    assert {Path(entry["file"]).name for entry in selected} == {"main.c", "helper.c", "vendor.c"}


def test_archive_member_is_tied_to_its_source_by_its_bytes(project):
    evidence, _ = evidence_of(project)
    [image] = evidence["images"]
    assert ("vendor", "vendor.c") in {(item["root"], item["path"]) for item in image["sources"]}


def test_headers_are_owned_by_a_root_or_the_compiler(project):
    evidence, _ = evidence_of(project)
    headers = {Path(item["path"]).name: item for item in evidence["images"][0]["header_accounting"]}

    assert headers["cfg.h"]["source_bindings"] == [{"root": "app", "path": "cfg.h"}]
    assert headers["vendor.h"]["source_bindings"] == [{"root": "vendor", "path": "vendor.h"}]
    assert headers["stdint.h"]["disposition"] == "toolchain"


def test_header_outside_every_root_is_unowned(project):
    roots = {"app": project["roots"]["app"]}
    image = {"id": "app", "role": "application", "elf": str(project["build"] / "app"), "map": str(project["build"] / "app.map")}
    evidence, _ = link_evidence.build_evidence([str(project["database"])], [image], roots)
    [image] = evidence["images"]

    assert "vendor.h" in {Path(item["path"]).name for item in image["header_accounting"] if item["disposition"] == "unowned"}
    assert {Path(path).name for path in image["unbound_sources"]} == {"vendor.c"}


def test_contributing_object_without_a_compile_command_is_unresolved(project):
    entries = [entry for entry in project["entries"] if not entry["file"].endswith("helper.c")]
    project["database"].write_text(json.dumps(entries))

    evidence, _ = evidence_of(project)
    [row] = evidence["images"][0]["unresolved"]

    assert row["kind"] == "object" and row["path"].endswith("helper.o")


def test_missing_dependency_file_is_unresolved_not_dropped(project):
    (project["build"] / "main.d").unlink()

    evidence, _ = evidence_of(project)

    assert [row["kind"] for row in evidence["images"][0]["unresolved"]] == ["dependency-file"]
