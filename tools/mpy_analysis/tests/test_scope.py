import copy
import importlib.util
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


def authority_files(manifest, variables):
    spec = importlib.util.spec_from_file_location(
        "scope_test_manifest_authority", MPY_ROOT / "tools/manifestfile.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    resolver = module.ManifestFile(
        module.MODE_FREEZE,
        {
            "MPY_DIR": str(MPY_ROOT),
            **{name: str(Path(value).resolve()) for name, value in variables.items()},
        },
    )
    before = os.getcwd()
    try:
        resolver.execute(str(manifest))
    finally:
        os.chdir(before)
    return [
        (
            Path(output.full_path).resolve(),
            output.target_path,
            output.kind,
            output.opt,
            {
                field: getattr(output.metadata, field)
                for field in (
                    "version",
                    "description",
                    "license",
                    "author",
                    "stdlib",
                    "pypi",
                    "pypi_publish",
                )
            },
        )
        for output in resolver.files()
    ]


def assert_authority_selection(data, manifest, variables):
    assert [
        (
            scope.source_path(data, record),
            record["target_path"],
            record["provenance"]["freeze_kind"],
            record["provenance"]["opt"],
            record["provenance"]["package_metadata"],
        )
        for record in frozen(data)
    ] == authority_files(manifest, variables)


def test_manifest_retains_authority_graph_repeated_requires_and_metadata_ownership(tmp_path):
    library = tmp_path / "library"
    write(
        library,
        "python-ecosys/parent/manifest.py",
        'metadata(version="2", description="parent", license="MIT", author="Maintainer", '
        'pypi="parent-pypi", pypi_publish="mpy-parent")\n'
        'module("parent.py", opt=1)\n'
        'require("child", version=">=9", enabled=True)\n'
        'include("extra.py")\n'
        'require("child")\n',
    )
    write(library, "python-ecosys/parent/parent.py")
    write(
        library,
        "python-ecosys/parent/extra.py",
        'options.defaults(enabled=True)\nif options.enabled:\n    module("parent_extra.py")\n'
        'require("child")\n',
    )
    write(library, "python-ecosys/parent/parent_extra.py")
    write(
        library,
        "python-ecosys/child/manifest.py",
        'metadata(version="3", description="child")\n'
        'options.defaults(enabled=False)\nif options.enabled:\n    module("child.py")\n',
    )
    write(library, "python-ecosys/child/child.py")
    write(tmp_path, "local.py")
    write(tmp_path, "local_after.py")
    write(tmp_path, "included.py", 'require("parent")\n')
    variables = {"MPY_LIB_DIR": str(library)}
    data = collect(
        tmp_path,
        'module("local.py")\ninclude("included.py")\ninclude("included.py")\n'
        'require("parent")\nrequire("child")\nmodule("local_after.py")\n',
        variables=variables,
        roots={"consumer": str(tmp_path), "library": str(library)},
        owners={"consumer": "consumer", "library": "library"},
    )
    assert_authority_selection(data, tmp_path / "manifest.py", variables)
    parent_id = "library/python-ecosys/parent/manifest.py"
    child_id = "library/python-ecosys/child/manifest.py"
    packages = {package["id"]: package for package in data["packages"]}
    assert set(packages) == {parent_id, child_id}
    assert packages[parent_id]["dependencies"] == [child_id]
    assert packages[child_id]["dependencies"] == []
    assert packages[parent_id]["name"] == "parent"
    assert packages[parent_id]["root"] == "library"
    assert packages[parent_id]["path"] == "python-ecosys/parent/manifest.py"
    assert packages[parent_id]["library"] is None
    assert packages[parent_id]["metadata"] == {
        "version": "2",
        "description": "parent",
        "license": "MIT",
        "author": "Maintainer",
        "stdlib": False,
        "pypi": "parent-pypi",
        "pypi_publish": "mpy-parent",
    }
    assert packages[child_id]["metadata"]["version"] == "3"
    assert data["package_requirements"] == [
        {"parent": None, "package": parent_id},
        {"parent": parent_id, "package": child_id},
        {"parent": parent_id, "package": child_id},
        {"parent": parent_id, "package": child_id},
        {"parent": None, "package": parent_id},
        {"parent": None, "package": child_id},
    ]
    ownership = {
        record["target_path"]: record["provenance"].get("package_id") for record in frozen(data)
    }
    assert ownership == {
        "local.py": None,
        "parent.py": parent_id,
        "child.py": child_id,
        "parent_extra.py": parent_id,
        "local_after.py": None,
    }
    assert scope.parse_scope(data) == data


def test_manifest_duplicate_names_use_authority_library_selection_and_physical_roots(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory, package_name, version in [
        (first, "first_pkg", "1"),
        (second, "second_pkg", "2"),
    ]:
        write(
            directory,
            "nested/demo/manifest.py",
            f'metadata(version="{version}")\npackage("{package_name}")\n',
        )
        write(directory, f"nested/demo/{package_name}/__init__.py")
    # An alias must not create a second identity for the same physical manifest.
    alias = tmp_path / "first-alias"
    alias.symlink_to(first, target_is_directory=True)
    variables = {"FIRST": str(first), "SECOND": str(second), "ALIAS": str(alias)}
    data = collect(
        tmp_path,
        'add_library("first", "$(FIRST)")\n'
        'add_library("second", "$(SECOND)", prepend=True)\n'
        'add_library("alias", "$(ALIAS)")\n'
        'require("demo")\nrequire("demo", library="first")\n'
        'require("demo", library="second")\nrequire("demo", library="alias")\n',
        variables=variables,
        roots={"consumer": str(tmp_path), "first": str(first), "second": str(second)},
        owners={"consumer": "consumer", "first": "first", "second": "second"},
    )
    assert_authority_selection(data, tmp_path / "manifest.py", variables)
    first_id = "first/nested/demo/manifest.py"
    second_id = "second/nested/demo/manifest.py"
    assert [
        (package["id"], package["name"], package["library"]) for package in data["packages"]
    ] == [
        (second_id, "demo", None),
        (first_id, "demo", "first"),
    ]
    assert data["package_requirements"] == [
        {"parent": None, "package": second_id},
        {"parent": None, "package": first_id},
        {"parent": None, "package": second_id},
        {"parent": None, "package": first_id},
    ]
    assert {record["provenance"]["package_id"] for record in frozen(data)} == {first_id, second_id}


def test_manifest_require_after_unrequired_include_does_not_fabricate_file_ownership(tmp_path):
    write(tmp_path, "library/demo/manifest.py", 'metadata(version="1")\nmodule("demo.py")\n')
    write(tmp_path, "library/demo/demo.py")
    data = collect(
        tmp_path,
        'add_library("local", "library")\ninclude("library/demo")\nrequire("demo")\n',
    )
    assert "package_id" not in frozen(data)[0]["provenance"]
    assert data["packages"] == [
        {
            "id": "fixture/library/demo/manifest.py",
            "name": "demo",
            "root": "fixture",
            "path": "library/demo/manifest.py",
            "library": None,
            "metadata": {},
            "dependencies": [],
        }
    ]
    assert data["package_requirements"] == [
        {"parent": None, "package": "fixture/library/demo/manifest.py"}
    ]


def package_inventory(tmp_path):
    data = inventory(tmp_path)
    write(tmp_path, "packages/parent/manifest.py")
    write(tmp_path, "packages/child/manifest.py")
    parent_id = "project/packages/parent/manifest.py"
    child_id = "project/packages/child/manifest.py"
    data["packages"] = [
        {
            "id": parent_id,
            "name": "parent",
            "root": "project",
            "path": "packages/parent/manifest.py",
            "library": None,
            "metadata": {"version": None, "stdlib": False},
            "dependencies": [child_id],
        },
        {
            "id": child_id,
            "name": "child",
            "root": "project",
            "path": "packages/child/manifest.py",
            "library": "ecosys",
            "metadata": {"version": "1"},
            "dependencies": [],
        },
    ]
    data["package_requirements"] = [
        {"parent": None, "package": parent_id},
        {"parent": parent_id, "package": child_id},
    ]
    frozen(data)[0]["provenance"]["package_id"] = parent_id
    return data


def test_scope_retains_optional_package_inventory_and_edges(tmp_path):
    data = package_inventory(tmp_path)
    assert scope.parse_scope(data) == data
    del data["package_requirements"]
    assert scope.parse_scope(data) == data


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("id", ""),
        ("id", 1),
        ("name", None),
        ("name", ""),
        ("root", "missing"),
        ("path", "../manifest.py"),
        ("library", ""),
        ("library", []),
        ("metadata", None),
        ("metadata", []),
        ("metadata", {"version": 1}),
        ("metadata", {"stdlib": "true"}),
        ("metadata", {"author": []}),
        ("metadata", {"license": False}),
        ("metadata", {"pypi": {}}),
        ("metadata", {"pypi_publish": 1}),
        ("dependencies", None),
        ("dependencies", "child"),
        ("dependencies", [None]),
        ("dependencies", ["missing"]),
    ],
)
def test_scope_rejects_malformed_package_attributes(tmp_path, field, value):
    data = package_inventory(tmp_path)
    data["packages"][0][field] = value
    with pytest.raises(ValueError):
        scope.parse_scope(data)


@pytest.mark.parametrize(
    "field", ["id", "name", "root", "path", "library", "metadata", "dependencies"]
)
def test_scope_requires_package_attributes(tmp_path, field):
    data = package_inventory(tmp_path)
    del data["packages"][0][field]
    with pytest.raises(ValueError):
        scope.parse_scope(data)


@pytest.mark.parametrize(
    "requirements",
    [
        None,
        {},
        [None],
        [{}],
        [{"parent": None}],
        [{"package": "missing"}],
        [{"parent": "missing", "package": "missing"}],
        [{"parent": 1, "package": "missing"}],
        [{"parent": None, "package": "missing"}],
    ],
)
def test_scope_rejects_malformed_and_dangling_package_requirements(tmp_path, requirements):
    data = package_inventory(tmp_path)
    data["package_requirements"] = requirements
    with pytest.raises(ValueError):
        scope.parse_scope(data)


@pytest.mark.parametrize("packages", [None, {}, [None]])
def test_scope_rejects_malformed_package_lists(tmp_path, packages):
    data = package_inventory(tmp_path)
    data["packages"] = packages
    with pytest.raises(ValueError):
        scope.parse_scope(data)


def test_scope_rejects_duplicate_package_ids(tmp_path):
    data = package_inventory(tmp_path)
    data["packages"].append(copy.deepcopy(data["packages"][0]))
    with pytest.raises(ValueError, match="duplicate package id"):
        scope.parse_scope(data)


def test_scope_rejects_requirements_missing_from_parent_dependencies(tmp_path):
    data = package_inventory(tmp_path)
    data["packages"][0]["dependencies"] = []
    with pytest.raises(ValueError, match="parent dependencies"):
        scope.parse_scope(data)


@pytest.mark.parametrize("package_id", [None, "", 1, "missing"])
def test_scope_rejects_malformed_and_dangling_source_package_ids(tmp_path, package_id):
    data = package_inventory(tmp_path)
    frozen(data)[0]["provenance"]["package_id"] = package_id
    with pytest.raises(ValueError, match="package_id"):
        scope.parse_scope(data)


def test_scope_rejects_package_references_without_package_inventory(tmp_path):
    data = package_inventory(tmp_path)
    del data["packages"]
    with pytest.raises(ValueError, match="unknown package"):
        scope.parse_scope(data)


def test_manifest_retains_required_packages_without_selected_python_files(tmp_path):
    write(tmp_path, "library/header/manifest.py", 'metadata(description="header only")\n')
    write(tmp_path, "local.py")
    data = collect(
        tmp_path,
        'add_library("local", "library")\nrequire("header", version=">=7")\nmodule("local.py")\n',
    )
    assert [record["path"] for record in frozen(data)] == ["local.py"]
    assert "package_id" not in frozen(data)[0]["provenance"]
    package = data["packages"][0]
    assert package["id"] == "fixture/library/header/manifest.py"
    assert package["metadata"]["version"] is None
    assert package["metadata"]["description"] == "header only"
    assert package["dependencies"] == []
    assert data["package_requirements"] == [{"parent": None, "package": package["id"]}]


def test_manifest_requires_explicit_roots_for_selected_package_manifests(tmp_path):
    library = tmp_path / "library"
    sources = tmp_path / "sources"
    write(library, "demo/manifest.py", 'module("demo.py", base_path="$(SOURCES)")\n')
    write(sources, "demo.py")
    manifest = write(
        tmp_path, "manifest.py", 'add_library("local", "$(LIBRARY)")\nrequire("demo")\n'
    )
    with pytest.raises(ValueError, match="no explicit root"):
        scope.collect_manifest_scope(
            MPY_ROOT,
            [manifest],
            roots={"sources": str(sources)},
            path_vars={"SOURCES": str(sources), "LIBRARY": str(library)},
            owners={"sources": "source-owner"},
            target="test-target",
            variant="minimal",
            port="unix",
        )


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


def test_scope_cli_takes_the_whole_selection_from_the_database_without_a_manifest(tmp_path):
    write(tmp_path, "port/main.c")
    database = write_db(
        tmp_path / "compile_commands.json",
        [{"directory": str(tmp_path / "port"), "file": "main.c", "arguments": ["cc", "-c", "main.c"]}],
    )
    output = tmp_path / "scope.json"
    argv = ["--micropython-root", str(MPY_ROOT), "--root", f"project={tmp_path}", "--owner", "project=project-owner"]
    argv += ["--target", "t", "--variant", "v", "--port", "p", "--compile-database", str(database), "--output", str(output)]
    assert scope.main(argv) == 0
    assert scope.load_scope(output)["groups"]["native"][0]["path"] == "port/main.c"
    assert scope.main(argv[:-4] + ["--output", str(output)]) == 2


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
