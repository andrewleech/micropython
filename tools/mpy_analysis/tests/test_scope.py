import copy
import json
import os
from pathlib import Path

import pytest

from conftest import write, write_db
from mpy_analysis import scope


MPY_ROOT = Path(__file__).resolve().parents[3]


def inventory(tmp_path):
    write(tmp_path, "src/main.py", "import helper\n")
    return {
        "schema_version": 1,
        "target": "test-target",
        "variant": "minimal",
        "port": "unix",
        "roots": {"project": str(tmp_path)},
        "groups": {
            "runtime_frozen_python": [
                {
                    "root": "project",
                    "path": "src/main.py",
                    "target_path": "main.py",
                    "owner": "application",
                    "provenance": {"kind": "fixture"},
                }
            ],
        },
    }


def frozen(data):
    return data["groups"]["runtime_frozen_python"]


def collect(tmp_path, manifest_text, *, variables=None, roots=None, owners=None):
    manifest = write(tmp_path, "manifest.py", manifest_text)
    return scope.collect_manifest_scope(
        MPY_ROOT,
        [manifest],
        roots=roots or {"fixture": str(tmp_path)},
        path_vars=variables or {},
        owners=owners or {"fixture": "fixture-owner"},
        target="test-target",
        variant="minimal",
        port="unix",
    )


def test_explicit_scope_keeps_all_groups_metadata_and_physical_locations(tmp_path):
    data = inventory(tmp_path)
    external = tmp_path / "external"
    write(external, "lib/util.py", "value = 1\n")
    data["roots"]["library"] = str(external)
    data["groups"]["host_and_example_python"] = [
        {
            "root": "library",
            "path": "lib/util.py",
            "owner": "library-maintainer",
            "provenance": "tracked consumer input",
        }
    ]
    data["groups"]["frontend"] = []
    data["groups"]["native"] = []
    data["groups"]["consumer_specific"] = [copy.deepcopy(frozen(data)[0])]
    data["consumer_metadata"] = {"selection": "explicit"}
    parsed = scope.parse_scope(data)
    assert parsed == data
    parsed["groups"]["frontend"].append({})
    assert data["groups"]["frontend"] == []
    record = data["groups"]["host_and_example_python"][0]
    assert scope.source_identity(record) == "library/lib/util.py"
    assert scope.source_path(data, record) == external / "lib/util.py"
    assert scope.report_path(data, record) == str(external / "lib/util.py")


@pytest.mark.parametrize("version", [None, 0, 2, True, "1"])
def test_scope_rejects_unsupported_schema_versions(tmp_path, version):
    data = inventory(tmp_path)
    data["schema_version"] = version
    with pytest.raises(ValueError, match="schema_version"):
        scope.parse_scope(data)


@pytest.mark.parametrize(
    "path",
    [
        "../main.py",
        "/main.py",
        "C:/main.py",
        "C:main.py",
        "a\\main.py",
        "./main.py",
        "a//main.py",
        "a/../main.py",
        "",
    ],
)
def test_scope_rejects_unsafe_physical_paths(tmp_path, path):
    data = inventory(tmp_path)
    frozen(data)[0]["path"] = path
    with pytest.raises(ValueError):
        scope.parse_scope(data)


@pytest.mark.parametrize(
    "target", ["../main.py", "/main.py", "C:/main.py", "a\\main.py", "a/./main.py", ""]
)
def test_scope_rejects_unsafe_frozen_targets(tmp_path, target):
    data = inventory(tmp_path)
    frozen(data)[0]["target_path"] = target
    with pytest.raises(ValueError):
        scope.parse_scope(data)


@pytest.mark.parametrize("field", ["root", "path", "owner", "provenance", "target_path"])
def test_scope_requires_source_contract_fields(tmp_path, field):
    data = inventory(tmp_path)
    del frozen(data)[0][field]
    with pytest.raises(ValueError):
        scope.parse_scope(data)


@pytest.mark.parametrize("field", ["target", "variant", "port"])
def test_scope_requires_target_metadata(tmp_path, field):
    data = inventory(tmp_path)
    del data[field]
    with pytest.raises(ValueError, match=field):
        scope.parse_scope(data)


def test_scope_keeps_stub_dependencies_out_of_selected_source_inventory(tmp_path):
    data = inventory(tmp_path)
    write(tmp_path, "builtins.pyi")
    frozen(data)[0]["path"] = "builtins.pyi"
    with pytest.raises(ValueError, match="stub dependencies"):
        scope.parse_scope(data)


def test_scope_rejects_unknown_root(tmp_path):
    data = inventory(tmp_path)
    frozen(data)[0]["root"] = "unmapped"
    with pytest.raises(ValueError, match="unknown source root"):
        scope.parse_scope(data)


def test_scope_rejects_relative_roots(tmp_path):
    data = inventory(tmp_path)
    data["roots"]["project"] = "relative"
    with pytest.raises(ValueError, match="absolute"):
        scope.parse_scope(data)


def test_scope_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = write(tmp_path, "outside.py")
    data = inventory(root)
    (root / "src/main.py").unlink()
    (root / "src/main.py").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes root"):
        scope.parse_scope(data)


def test_scope_rejects_missing_and_nonfile_sources(tmp_path):
    data = inventory(tmp_path)
    frozen(data)[0]["path"] = "missing.py"
    with pytest.raises(FileNotFoundError):
        scope.parse_scope(data)
    frozen(data)[0]["path"] = "src"
    with pytest.raises(ValueError, match="not a file"):
        scope.parse_scope(data)


def test_scope_rejects_empty_selection(tmp_path):
    data = inventory(tmp_path)
    data["groups"] = {"native": [], "runtime_frozen_python": []}
    with pytest.raises(ValueError, match="selection is empty"):
        scope.parse_scope(data)


def test_scope_preserves_multiple_frozen_identities_for_one_source(tmp_path):
    data = inventory(tmp_path)
    second = copy.deepcopy(frozen(data)[0])
    second["target_path"] = "package/main.py"
    frozen(data).append(second)
    parsed = scope.parse_scope(data)
    assert len(frozen(parsed)) == 2
    assert len({scope.source_path(parsed, record) for record in frozen(parsed)}) == 1


def test_scope_rejects_frozen_target_overwrite(tmp_path):
    data = inventory(tmp_path)
    write(tmp_path, "src/other.py")
    other = copy.deepcopy(frozen(data)[0])
    other["path"] = "src/other.py"
    frozen(data).append(other)
    with pytest.raises(ValueError, match="target collision"):
        scope.parse_scope(data)


def test_scope_rejects_frozen_file_directory_collision(tmp_path):
    data = inventory(tmp_path)
    frozen(data)[0]["target_path"] = "package"
    other = copy.deepcopy(frozen(data)[0])
    other["target_path"] = "package/main.py"
    frozen(data).append(other)
    with pytest.raises(ValueError, match="file/directory collision"):
        scope.parse_scope(data)


def test_manifest_collects_actual_freeze_include_options_and_import_identities(tmp_path):
    write(tmp_path, "sources/pkg/main.py", "import helper\n")
    write(tmp_path, "sources/helper.py", "value = 1\n")
    write(
        tmp_path,
        "extra.py",
        'options.defaults(enabled=False)\nif options.enabled:\n    freeze("sources", "helper.py")\n',
    )
    data = collect(
        tmp_path,
        'freeze("sources", "pkg/main.py")\nfreeze_as_str("sources/pkg")\ninclude("extra.py", enabled=True)\n',
    )
    selected = {(record["path"], record["target_path"]) for record in frozen(data)}
    assert selected == {
        ("sources/pkg/main.py", "pkg/main.py"),
        ("sources/pkg/main.py", "main.py"),
        ("sources/helper.py", "helper.py"),
    }
    assert all(record["owner"] == "fixture-owner" for record in frozen(data))
    provenance = frozen(data)[0]["provenance"]
    assert provenance["authority"] == str(MPY_ROOT / "tools/manifestfile.py")
    assert provenance["manifests"] == [str(tmp_path / "manifest.py")]
    assert provenance["path_vars"]["MPY_DIR"] == str(MPY_ROOT)
    assert "freeze_kind" in provenance


def test_manifest_retains_provenance_for_repeated_frozen_selections(tmp_path):
    write(tmp_path, "sources/source.py")
    data = collect(tmp_path, 'freeze("sources", "source.py", opt=0)\nfreeze_as_str("sources")\n')
    assert len(frozen(data)) == 2
    assert {record["target_path"] for record in frozen(data)} == {"source.py"}
    assert [record["provenance"]["opt"] for record in frozen(data)] == [0, None]
    assert len({record["provenance"]["freeze_kind"] for record in frozen(data)}) == 2


def test_manifest_uses_explicit_library_variables_and_root_owners(tmp_path):
    library = tmp_path / "external-library"
    write(library, "python-ecosys/demo/manifest.py", 'metadata(version="1")\npackage("demo")\n')
    write(library, "python-ecosys/demo/demo/__init__.py", "value = 1\n")
    data = collect(
        tmp_path,
        'require("demo")\n',
        variables={"MPY_LIB_DIR": str(library)},
        roots={"consumer": str(tmp_path), "library": str(library)},
        owners={"consumer": "consumer", "library": "library-maintainer"},
    )
    record = frozen(data)[0]
    assert record["root"] == "library"
    assert record["path"] == "python-ecosys/demo/demo/__init__.py"
    assert record["target_path"] == "demo/__init__.py"
    assert record["owner"] == "library-maintainer"
    assert record["provenance"]["package_metadata"]["version"] == "1"


def test_manifest_rejects_sources_outside_explicit_roots(tmp_path):
    manifest_root = tmp_path / "manifests"
    source_root = tmp_path / "external"
    write(source_root, "source.py")
    with pytest.raises(ValueError, match="no explicit root"):
        collect(
            manifest_root,
            'freeze("$(EXTERNAL)", "source.py")\n',
            variables={"EXTERNAL": str(source_root)},
        )


def test_manifest_rejects_unmapped_owner(tmp_path):
    write(tmp_path, "source.py")
    with pytest.raises(ValueError, match="owner for root"):
        collect(tmp_path, 'module("source.py")\n', owners={"other": "unused"})


def test_manifest_rejects_ambiguous_root_aliases(tmp_path):
    write(tmp_path, "source.py")
    with pytest.raises(ValueError, match="ambiguous roots"):
        collect(
            tmp_path,
            'module("source.py")\n',
            roots={"one": str(tmp_path), "two": str(tmp_path)},
            owners={"one": "one", "two": "two"},
        )


def test_manifest_resolves_relative_path_variables_against_invocation_directory(
    tmp_path, monkeypatch
):
    source = "checkout/ports/unix/modules/selected.py"
    write(tmp_path, source, "value = 'selected'\n")
    write(tmp_path, f"manifests/{source}", "value = 'decoy'\n")
    monkeypatch.chdir(tmp_path)
    data = collect(
        tmp_path / "manifests",
        'module("selected.py", base_path="$(PORT_DIR)/modules")\n',
        variables={"PORT_DIR": "checkout/ports/unix"},
        roots={"fixture": str(tmp_path)},
        owners={"fixture": "fixture-owner"},
    )
    assert frozen(data)[0]["path"] == source
    assert frozen(data)[0]["provenance"]["path_vars"]["PORT_DIR"] == str(
        tmp_path / "checkout/ports/unix"
    )


def test_manifest_rejects_conflicting_authority_variable(tmp_path):
    with pytest.raises(ValueError, match="MPY_DIR"):
        collect(tmp_path, "", variables={"MPY_DIR": str(tmp_path)})


def test_manifest_rejects_precompiled_frozen_inputs_without_silent_drop(tmp_path):
    write(tmp_path, "source.mpy", "bytecode")
    with pytest.raises(ValueError, match="no assessable Python source"):
        collect(tmp_path, 'freeze_mpy(".", "source.mpy")\n')


def test_manifest_rejects_empty_python_selection(tmp_path):
    with pytest.raises(ValueError, match="no Python sources"):
        collect(tmp_path, "")


def test_manifest_restores_working_directory_after_authority_failure(tmp_path):
    before = os.getcwd()
    with pytest.raises(ValueError, match="manifest resolution failed"):
        collect(tmp_path, 'module("missing.py")\n')
    assert os.getcwd() == before


def test_manifest_target_collision_is_not_silently_overwritten(tmp_path):
    write(tmp_path, "one/source.py")
    write(tmp_path, "two/source.py")
    with pytest.raises(ValueError, match="target collision"):
        collect(tmp_path, 'freeze("one", "source.py")\nfreeze("two", "source.py")\n')


def test_compdb_uses_each_entry_directory_preserves_external_sources_and_commands(tmp_path):
    project = tmp_path / "project"
    library = tmp_path / "library"
    write(project, "port/main.c")
    write(project, "py/map.c")
    write(library, "source.c")
    entries = [
        {
            "directory": str(project / "port"),
            "file": "main.c",
            "arguments": ["cc", "-c", "main.c"],
            "owner": "port-owner",
            "provenance": {"kind": "build-recorder"},
        },
        {
            "directory": str(project / "port"),
            "file": "../py/map.c",
            "arguments": ["cc", "-DFLAG", "-c", "../py/map.c"],
        },
        {
            "directory": str(library),
            "file": "source.c",
            "command": "cc -c source.c",
            "output": "source.o",
        },
        {
            "directory": str(project / "port"),
            "file": "main.c",
            "arguments": ["cc", "-DOTHER", "-c", "main.c"],
        },
    ]
    database = write_db(tmp_path / "compile_commands.json", entries)
    records = scope.collect_compdb_sources(
        database,
        roots={"project": str(project), "library": str(library)},
        owners={"project": "micropython", "library": "third-party"},
    )
    assert len(records) == 4
    assert [record["path"] for record in records] == [
        "port/main.c",
        "py/map.c",
        "source.c",
        "port/main.c",
    ]
    assert [record["compilation"] for record in records] == entries
    assert [record["owner"] for record in records] == [
        "port-owner",
        "micropython",
        "third-party",
        "micropython",
    ]
    assert records[0]["provenance"]["compiler_provenance"] == entries[0]["provenance"]
    assert [record["provenance"]["entry_index"] for record in records] == list(range(4))


def test_compdb_missing_and_empty_database_fail_explicitly(tmp_path):
    kwargs = {"roots": {"project": str(tmp_path)}, "owners": {"project": "project"}}
    with pytest.raises(FileNotFoundError):
        scope.collect_compdb_sources(tmp_path / "missing.json", **kwargs)
    empty = write_db(tmp_path / "compile_commands.json", [])
    with pytest.raises(ValueError, match="selected entries"):
        scope.collect_compdb_sources(empty, **kwargs)


def test_compdb_does_not_filter_non_c_compiled_sources(tmp_path):
    write(tmp_path, "startup.S")
    database = write_db(
        tmp_path / "compile_commands.json",
        [
            {
                "directory": str(tmp_path),
                "file": "startup.S",
                "arguments": ["cc", "-c", "startup.S"],
            }
        ],
    )
    records = scope.collect_compdb_sources(
        database, roots={"project": str(tmp_path)}, owners={"project": "project"}
    )
    assert records[0]["path"] == "startup.S"


@pytest.mark.parametrize(
    "entry",
    [
        {"directory": "relative", "file": "source.c", "command": "cc -c source.c"},
        {"file": "source.c", "command": "cc -c source.c"},
        {"directory": "REPLACE", "file": "source.c"},
        {"directory": "REPLACE", "file": "source.c", "arguments": []},
        {"directory": "REPLACE", "file": "source.c", "arguments": [42]},
    ],
)
def test_compdb_rejects_invalid_compilation_prerequisites(tmp_path, entry):
    write(tmp_path, "source.c")
    entry = copy.deepcopy(entry)
    if entry.get("directory") == "REPLACE":
        entry["directory"] = str(tmp_path)
    database = write_db(tmp_path / "compile_commands.json", [entry])
    with pytest.raises(ValueError):
        scope.collect_compdb_sources(
            database, roots={"project": str(tmp_path)}, owners={"project": "project"}
        )


def test_scope_cli_validates_supplied_inventory_and_emits_json(tmp_path, capsys):
    data = inventory(tmp_path)
    path = write(tmp_path, "scope.json", json.dumps(data))
    assert scope.main(["--scope", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == data


def test_scope_cli_collects_plain_manifest_and_native_database(tmp_path):
    write(tmp_path, "modules/main.py")
    write(tmp_path, "port/main.c")
    manifest = write(tmp_path, "manifest.py", 'freeze("modules", "main.py")\n')
    database = write_db(
        tmp_path / "compile_commands.json",
        [
            {
                "directory": str(tmp_path / "port"),
                "file": "main.c",
                "arguments": ["cc", "-c", "main.c"],
            }
        ],
    )
    output = tmp_path / "output/scope.json"
    assert (
        scope.main(
            [
                "--manifest",
                str(manifest),
                "--micropython-root",
                str(MPY_ROOT),
                "--root",
                f"project={tmp_path}",
                "--owner",
                "project=project-owner",
                "--target",
                "unix",
                "--variant",
                "fixture",
                "--port",
                "unix",
                "--compile-database",
                str(database),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    data = scope.load_scope(output)
    assert frozen(data)[0]["target_path"] == "main.py"
    assert data["groups"]["native"][0]["path"] == "port/main.c"


def test_scope_cli_rejects_failed_scope_without_output(tmp_path, capsys):
    data = inventory(tmp_path)
    frozen(data)[0]["path"] = "missing.py"
    source = write(tmp_path, "scope.json", json.dumps(data))
    output = tmp_path / "result.json"
    assert scope.main(["--scope", str(source), "--output", str(output)]) == 2
    assert "scope:" in capsys.readouterr().err
    assert not output.exists()


def test_scope_cli_rejects_ambiguous_collection_options(tmp_path, capsys):
    source = write(tmp_path, "scope.json", json.dumps(inventory(tmp_path)))
    assert scope.main(["--scope", str(source), "--root", f"project={tmp_path}"]) == 2
    assert "cannot be combined" in capsys.readouterr().err
