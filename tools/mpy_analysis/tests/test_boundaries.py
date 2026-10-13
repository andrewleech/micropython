import copy
import json
import subprocess
from pathlib import Path

import pytest

from mpy_analysis import boundaries
from mpy_analysis.declarations import selected_digest
from mpy_analysis.metadata import collect_metadata, selected_digest as metadata_digest
from mpy_analysis.scope import FROZEN_GROUP, parse_scope, source_path


_REAL_REPOSITORY_ROOT = boundaries._repository_root


def _write(root, relative, content="value = 1\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _record(path, *, root="source", package_id=None, native=False):
    result = {
        "root": root,
        "path": path,
        "owner": "fixture-owner",
        "provenance": {"kind": "fixture"},
    }
    if package_id is not None:
        result["provenance"]["package_id"] = package_id
    if native:
        result["compilation"] = {"directory": "/", "file": path, "arguments": ["cc", "-c", path]}
    else:
        result["target_path"] = path
    return result


def _scope(root, *paths, packages=()):
    return {
        "schema_version": 1,
        "target": "synthetic",
        "variant": "minimal",
        "port": "unix",
        "roots": {"source": str(root)},
        "groups": {FROZEN_GROUP: [_record(path) for path in paths], "native": []},
        "packages": list(packages),
        "package_requirements": [],
    }


def _package(root, relative, *, name="synthetic-package", metadata=None):
    _write(root, relative, 'metadata(version="1")\nmodule("item.py")\n')
    return {
        "id": f"source/{relative}",
        "root": "source",
        "path": relative,
        "name": name,
        "library": None,
        "metadata": metadata or {"version": "1"},
        "dependencies": [],
    }


def _issues(boundary):
    return {issue["kind"] for issue in boundary["issues"]}


def _discover(data, **kwargs):
    return boundaries.discover_boundaries(parse_scope(data), **kwargs)


@pytest.fixture(autouse=True)
def no_incidental_repository(monkeypatch):
    monkeypatch.setattr(boundaries, "_repository_root", lambda directory: None)


def test_every_selected_row_is_retained_without_copying_or_rewriting(tmp_path):
    _write(tmp_path, "source.py")
    _write(tmp_path, "source.c", "int value;\n")
    data = _scope(tmp_path, "source.py")
    duplicate = copy.deepcopy(data["groups"][FROZEN_GROUP][0])
    duplicate["target_path"] = "alias.py"
    data["groups"][FROZEN_GROUP].append(duplicate)
    data["groups"]["native"] = [_record("source.c", native=True), _record("source.c", native=True)]
    data["groups"]["native"][1]["compilation"]["arguments"].append("-DSECOND")
    data["groups"]["host_and_example_python"] = [_record("source.py")]
    parsed = parse_scope(data)
    original = copy.deepcopy(parsed)
    result = boundaries.discover_boundaries(parsed)
    rows = [row for boundary in result for row in boundary["sources"]]
    expected = [
        (group, record) for group in (FROZEN_GROUP, "native") for record in parsed["groups"][group]
    ]
    assert len(rows) == len(expected) == 4
    assert all(
        actual[0] == wanted[0] and actual[1] is wanted[1] for actual, wanted in zip(rows, expected)
    )
    assert parsed == original
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert "unresolved-identity" in _issues(result[0])


def test_manifest_alone_is_a_candidate_not_a_distribution(tmp_path):
    _write(tmp_path, "board/manifest.py", 'freeze(".")\n')
    _write(tmp_path, "board/item.py")
    result = _discover(_scope(tmp_path, "board/item.py"))
    assert len(result) == 1
    assert result[0]["path"] == "."
    assert result[0]["package_ids"] == []
    assert result[0]["manifest_metadata"] == {}
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert any(item["kind"] == "candidate-manifest" for item in result[0]["candidates"])


def test_required_package_uses_authority_name_and_metadata(tmp_path):
    package = _package(
        tmp_path,
        "distribution/manifest.py",
        metadata={"version": "2", "license": "MIT", "author": "Fixture"},
    )
    _write(tmp_path, "distribution/item.py")
    data = _scope(tmp_path, "distribution/item.py", packages=[package])
    data["groups"][FROZEN_GROUP][0]["provenance"]["package_id"] = package["id"]
    result = _discover(data)
    assert len(result) == 1
    assert result[0]["path"] == "distribution"
    assert result[0]["package_ids"] == [package["id"]]
    assert result[0]["boundary_kind"] == "required-package"
    assert "unresolved-boundary-candidates" not in _issues(result[0])


def test_proximity_does_not_invent_require_ownership(tmp_path):
    package = _package(tmp_path, "distribution/manifest.py")
    _write(tmp_path, "distribution/item.py")
    result = _discover(_scope(tmp_path, "distribution/item.py", packages=[package]))
    assert len(result) == 1
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert result[0]["package_ids"] == []
    assert result[0]["manifest_metadata"] == {}
    assert "required-package-ownership-unconfirmed" in _issues(result[0])


def test_unowned_require_package_has_no_boundary(tmp_path):
    package = _package(tmp_path, "unselected/manifest.py")
    _write(tmp_path, "selected/item.py")
    result = _discover(_scope(tmp_path, "selected/item.py", packages=[package]))
    assert len(result) == 1
    assert result[0]["package_ids"] == []
    assert not any(
        item["directory"] == str(tmp_path / "unselected") for item in result[0]["candidates"]
    )


def test_duplicate_package_names_in_distinct_directories_remain_distinct(tmp_path):
    packages = [
        _package(tmp_path, f"{directory}/manifest.py") for directory in ("first", "second")
    ]
    for directory in ("first", "second"):
        _write(tmp_path, f"{directory}/item.py")
    data = _scope(tmp_path, "first/item.py", "second/item.py", packages=packages)
    for record, package in zip(data["groups"][FROZEN_GROUP], packages):
        record["provenance"]["package_id"] = package["id"]
    result = _discover(data)
    assert [item["id"] for item in result] == ["source/first", "source/second"]
    assert [item["package_ids"] for item in result] == [[package["id"]] for package in packages]


def test_authority_ownership_can_extend_outside_manifest_directory(tmp_path):
    package = _package(tmp_path, "distribution/manifest.py")
    _write(tmp_path, "included/item.py")
    data = _scope(tmp_path, "included/item.py", packages=[package])
    data["groups"][FROZEN_GROUP][0]["provenance"]["package_id"] = package["id"]
    result = _discover(data)
    assert result[0]["directory"] == str(tmp_path / "distribution")
    assert result[0]["package_ids"] == [package["id"]]
    assert result[0]["sources"][0][1]["path"] == "included/item.py"
    assert "source-outside-package-directory" in _issues(result[0])


def test_nearest_nested_package_owns_sources_without_authority_ownership(tmp_path):
    _write(tmp_path, "pkg/pyproject.toml", '[project]\nname="parent-fixture"\nversion="1"\n')
    _write(tmp_path, "pkg/vendor/metadata.txt", "name=child-fixture\nversion=2\n")
    _write(tmp_path, "pkg/item.py")
    _write(tmp_path, "pkg/vendor/item.py")
    result = _discover(_scope(tmp_path, "pkg/item.py", "pkg/vendor/item.py"))
    assert [item["path"] for item in result] == ["pkg", "pkg/vendor"]
    assert [[record["path"] for _, record in item["sources"]] for item in result] == [
        ["pkg/item.py"],
        ["pkg/vendor/item.py"],
    ]
    assert all("ambiguous-nested-boundary" not in _issues(item) for item in result)


def test_authoritative_package_does_not_silently_erase_nested_metadata(tmp_path):
    package = _package(tmp_path, "pkg/manifest.py")
    _write(tmp_path, "pkg/vendor/pyproject.toml", '[project]\nname="nested-fixture"\n')
    _write(tmp_path, "pkg/vendor/item.py")
    data = _scope(tmp_path, "pkg/vendor/item.py", packages=[package])
    data["groups"][FROZEN_GROUP][0]["provenance"]["package_id"] = package["id"]
    result = _discover(data)
    assert len(result) == 1
    assert result[0]["path"] == "pkg"
    assert "ambiguous-nested-boundary" in _issues(result[0])
    assert any(
        item["directory"] == str(tmp_path / "pkg/vendor") and item["kind"] == "package-metadata"
        for item in result[0]["candidates"]
    )


@pytest.mark.parametrize(
    "marker,content,kind",
    [
        ("metadata.txt", "version=1\n", "package-metadata"),
        ("setup.py", 'setup(name="fixture", version="1")\n', "package-metadata"),
        ("setup.cfg", "[metadata]\nname=fixture\n", "package-metadata"),
        ("pyproject.toml", '[project]\nname="fixture"\n', "package-metadata"),
        ("CMakeLists.txt", 'project("fixture" VERSION 1.0)\n', "native-project"),
        ("meson.build", "project('fixture', 'c', version: '1.0')\n", "native-project"),
        (
            "library.json",
            '{"name": "fixture", "version": "1.0", "license": "MIT"}',
            "package-metadata",
        ),
    ],
)
def test_format_markers_establish_selected_ancestry_boundaries(tmp_path, marker, content, kind):
    _write(tmp_path, f"pkg/{marker}", content)
    _write(tmp_path, "pkg/source/item.py")
    result = _discover(_scope(tmp_path, "pkg/source/item.py"))
    assert result[0]["path"] == "pkg"
    assert result[0]["boundary_kind"] == kind
    assert any(item["path"] == str(tmp_path / "pkg" / marker) for item in result[0]["candidates"])


@pytest.mark.parametrize(
    "marker,content",
    [
        ("pyproject.toml", "[tool.fixture]\nsetting=true\n"),
        ("pyproject.toml", "[project\n"),
        ("setup.cfg", "[tool]\nsetting=true\n"),
        ("setup.py", 'raise RuntimeError("must never execute")\n'),
        ("CMakeLists.txt", "project(${PROJECT_NAME})\n"),
        ("meson.build", "project(project_name, 'c')\n"),
        ("CMakeLists.txt", 'if(OPTION)\nproject("conditional-fixture")\nendif()\n'),
        ("CMakeLists.txt", '#[[\nproject("comment-fixture")\n]]\n'),
        ("CMakeLists.txt", 'message("project(fake)")\n'),
        ("meson.build", "# project('comment-fixture')\n"),
        ("meson.build", "message('project(fake)')\n"),
        ("meson.build", "if option\nproject('conditional-fixture')\nendif\n"),
        ("PKG-INFO", "Metadata-Version: 2.4\nName: fixture\n"),
        ("METADATA", "Metadata-Version: 2.4\nName: fixture\n"),
        ("library.json", '{"version": "1.0"}'),
        ("library.json", '{"name": 7}'),
        ("library.json", '{"name": "${LIBRARY_NAME}"}'),
        ("library.json", '{"name": "first", "name": "second"}'),
        ("library.json", '["fixture"]'),
        ("library.json", '{"name": '),
    ],
)
def test_non_package_and_dynamic_markers_stay_candidates(tmp_path, marker, content):
    _write(tmp_path, f"pkg/{marker}", content)
    _write(tmp_path, "pkg/item.py")
    result = _discover(_scope(tmp_path, "pkg/item.py"))
    assert result[0]["path"] == "."
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert "unresolved-boundary-candidates" in _issues(result[0])


def test_malicious_setup_is_parsed_without_execution(tmp_path):
    sentinel = tmp_path / "executed"
    _write(
        tmp_path,
        "pkg/setup.py",
        f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\nsetup(name="fixture")\n',
    )
    _write(tmp_path, "pkg/item.py")
    _discover(_scope(tmp_path, "pkg/item.py"))
    assert not sentinel.exists()


def test_unselected_trees_and_licence_files_do_not_establish_packages(tmp_path, monkeypatch):
    _write(tmp_path, "selected/item.py")
    _write(tmp_path, "selected/LICENSE", "not package metadata\n")
    _write(tmp_path, "unselected/pyproject.toml", '[project]\nname="unselected"\n')
    _write(tmp_path, "unselected/item.py")
    scanned = []
    original_iterdir = Path.iterdir

    def immediate_children(directory):
        scanned.append(directory)
        return original_iterdir(directory)

    def forbidden(*args, **kwargs):
        raise AssertionError("arbitrary tree traversal is forbidden")

    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(Path, "iterdir", immediate_children)
    result = _discover(_scope(tmp_path, "selected/item.py"))
    assert len(result) == 1
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert result[0]["candidates"] == []
    assert set(scanned) == {tmp_path, tmp_path / "selected"}


def test_metadata_above_a_declared_root_is_not_inspected(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="outside"\n')
    _write(tmp_path, "root/pkg/item.py")
    result = _discover(_scope(tmp_path / "root", "pkg/item.py"))
    assert result[0]["path"] == "."
    assert result[0]["candidates"] == []


def test_source_and_root_aliases_coalesce_physical_boundary_but_keep_rows(tmp_path):
    _write(tmp_path, "pkg/metadata.txt", "version=1\n")
    _write(tmp_path, "pkg/item.py")
    (tmp_path / "alias").symlink_to(tmp_path / "pkg", target_is_directory=True)
    data = _scope(tmp_path, "pkg/item.py", "alias/item.py")
    data["roots"]["z_alias"] = str(tmp_path)
    data["groups"][FROZEN_GROUP][1]["root"] = "z_alias"
    result = _discover(data)
    assert len(result) == 1
    assert result[0]["id"] == "source/pkg"
    assert len(result[0]["sources"]) == 2
    assert [row[1]["path"] for row in result[0]["sources"]] == ["pkg/item.py", "alias/item.py"]


def test_physical_manifest_aliases_merge_but_conflicting_assertions_do_not(tmp_path):
    first = _package(tmp_path, "pkg/manifest.py", name="first-fixture", metadata={"version": "1"})
    _write(tmp_path, "pkg/item.py")
    (tmp_path / "alias").symlink_to(tmp_path / "pkg", target_is_directory=True)
    second = {
        **copy.deepcopy(first),
        "id": "source/alias/manifest.py",
        "path": "alias/manifest.py",
        "name": "second-fixture",
        "metadata": {"version": "2"},
    }
    data = _scope(tmp_path, "pkg/item.py", "alias/item.py", packages=[first, second])
    for record, package in zip(data["groups"][FROZEN_GROUP], [first, second]):
        record["provenance"]["package_id"] = package["id"]
    result = _discover(data)
    assert len(result) == 1
    assert result[0]["package_ids"] == sorted([first["id"], second["id"]])
    assert result[0]["manifest_metadata"] == {}
    assert "conflicting-manifest-metadata" in _issues(result[0])


def test_repository_fallback_is_not_a_distribution_name(tmp_path, monkeypatch):
    _write(tmp_path, "checkout/pkg/item.py")
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(boundaries, "_repository_root", lambda directory: checkout)
    result = _discover(_scope(tmp_path, "checkout/pkg/item.py"))
    assert result[0]["path"] == "checkout"
    assert result[0]["boundary_kind"] == "repository"
    assert result[0]["manifest_metadata"] == {}
    assert "unresolved-identity" in _issues(result[0])


def test_actual_nested_git_checkout_blocks_outer_package_metadata(tmp_path, monkeypatch):
    _write(tmp_path, "parent/pyproject.toml", '[project]\nname="outer-fixture"\n')
    _write(tmp_path, "parent/item.py")
    _write(tmp_path, "parent/nested/item.py")
    for directory in (tmp_path / "parent", tmp_path / "parent/nested"):
        subprocess.run(["git", "init", "-q", str(directory)], check=True, capture_output=True)
    monkeypatch.setattr(boundaries, "_repository_root", _REAL_REPOSITORY_ROOT)
    result = _discover(_scope(tmp_path, "parent/item.py", "parent/nested/item.py"))
    assert [item["path"] for item in result] == ["parent", "parent/nested"]
    assert [item["boundary_kind"] for item in result] == ["package-metadata", "repository"]
    assert "unresolved-identity" in _issues(result[1])


def test_git_queries_only_selected_directories_once(tmp_path, monkeypatch):
    _write(tmp_path, "selected/item.py")
    _write(tmp_path, "selected/other.py")
    _write(tmp_path, "unselected/item.py")
    calls = []

    def git_root(directory):
        calls.append(directory)
        return tmp_path

    monkeypatch.setattr(boundaries, "_repository_root", git_root)
    _discover(_scope(tmp_path, "selected/item.py", "selected/other.py"))
    assert calls == [tmp_path / "selected"]


def test_authority_ownership_records_conflicting_nested_checkout(tmp_path, monkeypatch):
    package = _package(tmp_path, "pkg/manifest.py")
    _write(tmp_path, "pkg/nested/item.py")
    data = _scope(tmp_path, "pkg/nested/item.py", packages=[package])
    data["groups"][FROZEN_GROUP][0]["provenance"]["package_id"] = package["id"]
    monkeypatch.setattr(boundaries, "_repository_root", lambda directory: tmp_path / "pkg/nested")
    result = _discover(data)
    assert result[0]["path"] == "pkg"
    assert "ambiguous-nested-boundary" in _issues(result[0])
    assert any(
        item["kind"] == "repository" and item["directory"] == str(tmp_path / "pkg/nested")
        for item in result[0]["candidates"]
    )


def test_required_candidate_evidence_does_not_leak_between_selected_rows(tmp_path):
    package = _package(tmp_path, "first/manifest.py")
    _write(tmp_path, "first/item.py")
    _write(tmp_path, "second/item.py")
    result = _discover(_scope(tmp_path, "first/item.py", "second/item.py", packages=[package]))
    gaps = [
        issue
        for issue in result[0]["issues"]
        if issue["kind"] == "required-package-ownership-unconfirmed"
    ]
    assert [issue["path"] for issue in gaps] == [str(tmp_path / "first/item.py")]


def test_git_root_outside_declared_roots_is_evidence_only(tmp_path, monkeypatch):
    _write(tmp_path, "root/item.py")
    monkeypatch.setattr(boundaries, "_repository_root", lambda directory: tmp_path)
    result = _discover(_scope(tmp_path / "root", "item.py"))
    assert result[0]["directory"] == str(tmp_path / "root")
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert any(
        item["directory"] == str(tmp_path) and item["kind"] == "repository"
        for item in result[0]["candidates"]
    )


def test_manual_selectors_partition_unknown_sources_without_adding_files(tmp_path):
    for path in ("vendor/first/item.py", "vendor/second/item.py", "unselected/item.py"):
        _write(tmp_path, path)
    declarations = [
        {"root": "source", "path": path}
        for path in ("vendor/first", "vendor/second", "unselected")
    ]
    result = _discover(
        _scope(tmp_path, "vendor/first/item.py", "vendor/second/item.py"),
        declared_boundaries=declarations,
    )
    assert [item["path"] for item in result] == ["vendor/first", "vendor/second"]
    assert all(item["boundary_kind"] == "declared" for item in result)
    assert sum(len(item["sources"]) for item in result) == 2


def test_nested_manual_selectors_use_nearest_declared_directory(tmp_path):
    _write(tmp_path, "vendor/item.py")
    _write(tmp_path, "vendor/nested/item.py")
    declarations = [{"root": "source", "path": path} for path in ("vendor", "vendor/nested")]
    result = _discover(
        _scope(tmp_path, "vendor/item.py", "vendor/nested/item.py"),
        declared_boundaries=declarations,
    )
    assert [item["path"] for item in result] == ["vendor", "vendor/nested"]
    assert all("ambiguous-nested-boundary" not in _issues(item) for item in result)


def test_manual_root_binding_uses_dot(tmp_path):
    _write(tmp_path, "item.py")
    result = _discover(
        _scope(tmp_path, "item.py"), declared_boundaries=[{"root": "source", "path": "."}]
    )
    assert result[0]["path"] == "."
    assert result[0]["boundary_kind"] == "declared"


def test_manual_selector_does_not_override_established_package(tmp_path):
    _write(tmp_path, "pkg/pyproject.toml", '[project]\nname="fixture"\n')
    _write(tmp_path, "pkg/vendor/item.py")
    result = _discover(
        _scope(tmp_path, "pkg/vendor/item.py"),
        declared_boundaries=[{"root": "source", "path": "pkg/vendor"}],
    )
    assert result[0]["path"] == "pkg"
    assert result[0]["boundary_kind"] == "package-metadata"
    assert "ambiguous-nested-boundary" in _issues(result[0])


def test_duplicate_physical_manual_claims_are_conflicting(tmp_path):
    _write(tmp_path, "pkg/item.py")
    (tmp_path / "alias").symlink_to(tmp_path / "pkg", target_is_directory=True)
    declarations = [{"root": "source", "path": path} for path in ("pkg", "alias")]
    result = _discover(_scope(tmp_path, "pkg/item.py"), declared_boundaries=declarations)
    assert len(result) == 1
    assert "duplicate-declared-boundary" in _issues(result[0])


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "C:/absolute",
        "C:relative",
        "a\\b",
        "a//b",
        "a/./b",
        "a/../b",
        "",
        "a\x00b",
    ],
)
def test_manual_selector_rejects_unsafe_paths(tmp_path, path):
    _write(tmp_path, "item.py")
    with pytest.raises(ValueError):
        _discover(
            _scope(tmp_path, "item.py"), declared_boundaries=[{"root": "source", "path": path}]
        )


def test_manual_selector_rejects_unknown_root_and_non_directory(tmp_path):
    _write(tmp_path, "item.py")
    for declaration in ({"root": "absent", "path": "."}, {"root": "source", "path": "item.py"}):
        with pytest.raises(ValueError):
            _discover(_scope(tmp_path, "item.py"), declared_boundaries=[declaration])


def test_source_and_manual_selector_reject_symlink_escapes(tmp_path):
    _write(tmp_path, "root/item.py")
    _write(tmp_path, "outside/item.py")
    (tmp_path / "root/escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError, match="within root"):
        _discover(
            _scope(tmp_path / "root", "item.py"),
            declared_boundaries=[{"root": "source", "path": "escape"}],
        )
    with pytest.raises(ValueError, match="escapes root"):
        boundaries.discover_boundaries(_scope(tmp_path / "root", "escape/item.py"))


def test_shared_physical_rows_with_distinct_package_owners_report_conflict(tmp_path):
    packages = [
        _package(tmp_path, f"{directory}/manifest.py") for directory in ("first", "second")
    ]
    _write(tmp_path, "shared/item.py")
    data = _scope(tmp_path, "shared/item.py", "shared/item.py", packages=packages)
    for record, package in zip(data["groups"][FROZEN_GROUP], packages):
        record["provenance"]["package_id"] = package["id"]
    result = _discover(data)
    assert len(result) == 2
    assert sum(len(item["sources"]) for item in result) == 2
    assert all("conflicting-source-ownership" in _issues(item) for item in result)


def _generated_binding(data, directory, *, local=False):
    digest, _ = selected_digest(data, directory)
    return {
        "mpy:source:root": "." if local else "source",
        "mpy:source:path": "."
        if local
        else directory.relative_to(Path(data["roots"]["source"])).as_posix(),
        "mpy:source:sha256": digest,
    }


def _core_document(binding):
    headers = "".join(
        f"X-Mpy-Source-{key.removeprefix('mpy:source:').title()}: {value}\n"
        for key, value in binding.items()
    )
    return (
        "Metadata-Version: 2.4\nName: generated-fixture\nVersion: 1.2\n"
        "License-Expression: MIT\n" + headers
    )


def _bom_document(binding, *, spdx=False):
    if spdx:
        return {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "synthetic-source-bom",
            "documentNamespace": "https://example.invalid/spdx/synthetic-source-bom",
            "creationInfo": {
                "creators": ["Tool: synthetic-fixture"],
                "created": "2026-10-10T00:00:00Z",
            },
            "packages": [
                {
                    "SPDXID": "SPDXRef-generated",
                    "name": "generated-fixture",
                    "versionInfo": "1.2",
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                    "licenseDeclared": "MIT",
                    "annotations": [
                        {
                            "annotationType": "OTHER",
                            "annotator": "Tool: synthetic-fixture",
                            "annotationDate": "2026-10-10T00:00:00Z",
                            "comment": json.dumps(binding),
                        }
                    ],
                }
            ],
        }
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "components": [
            {
                "type": "library",
                "bom-ref": "generated-fixture",
                "name": "generated-fixture",
                "version": "1.2",
                "licenses": [{"license": {"id": "MIT"}}],
                "properties": [{"name": key, "value": value} for key, value in binding.items()],
            }
        ],
    }


@pytest.mark.parametrize(
    "marker",
    ["PKG-INFO", "METADATA", "synthetic.dist-info/METADATA", "synthetic.egg-info/PKG-INFO"],
)
@pytest.mark.parametrize("local", [False, True])
def test_source_bound_core_metadata_establishes_nested_boundary(
    tmp_path, marker, local, monkeypatch
):
    _write(tmp_path, "vendor/item.py")
    data = _scope(tmp_path, "vendor/item.py")
    binding = _generated_binding(data, tmp_path / "vendor", local=local)
    _write(tmp_path, f"vendor/{marker}", _core_document(binding))
    monkeypatch.setattr(boundaries, "_repository_root", lambda directory: tmp_path)
    result = _discover(data)
    assert len(result) == 1
    assert result[0]["path"] == "vendor"
    assert result[0]["boundary_kind"] == "package-metadata"
    metadata = collect_metadata(
        tmp_path / "vendor",
        selected_files=[tmp_path / "vendor/item.py"],
        source_context={"root": "source", "root_directory": str(tmp_path)},
    )
    assert metadata["fields"]["name"] == "generated-fixture"
    assert metadata["fields"]["version"] == "1.2"
    assert metadata["fields"]["license"] == "MIT"


@pytest.mark.parametrize(
    "marker", ["bom.json", "sbom.json", "synthetic.cdx.json", "synthetic.spdx.json"]
)
def test_source_bound_bom_establishes_nested_boundary(tmp_path, marker, monkeypatch):
    _write(tmp_path, "vendor/item.py")
    data = _scope(tmp_path, "vendor/item.py")
    binding = _generated_binding(data, tmp_path / "vendor", local=True)
    document = _bom_document(binding, spdx=marker.endswith(".spdx.json"))
    _write(tmp_path, f"vendor/{marker}", json.dumps(document))
    monkeypatch.setattr(boundaries, "_repository_root", lambda directory: tmp_path)
    result = _discover(data)
    assert result[0]["path"] == "vendor"
    assert result[0]["boundary_kind"] == "package-metadata"
    metadata = collect_metadata(tmp_path / "vendor", selected_files=[tmp_path / "vendor/item.py"])
    assert metadata["fields"]["name"] == "generated-fixture"
    assert metadata["fields"]["license"] == "MIT"


@pytest.mark.parametrize(
    "marker",
    [
        "synthetic.dist-info/METADATA",
        "synthetic.egg-info/PKG-INFO",
        "bom.json",
        "synthetic.cdx.json",
        "synthetic.spdx.json",
    ],
)
@pytest.mark.parametrize("failure", ["unbound", "stale", "wrong-root", "wrong-path", "malformed"])
def test_generated_markers_do_not_guess_ownership(tmp_path, marker, failure):
    _write(tmp_path, "vendor/item.py")
    data = _scope(tmp_path, "vendor/item.py")
    binding = _generated_binding(data, tmp_path / "vendor")
    if failure == "unbound":
        binding = {}
    elif failure == "stale":
        binding["mpy:source:sha256"] = "0" * 64
    elif failure == "wrong-root":
        binding["mpy:source:root"] = "absent"
    elif failure == "wrong-path":
        binding["mpy:source:path"] = "."
    if marker.endswith(("METADATA", "PKG-INFO")):
        content = _core_document(binding)
    else:
        content = json.dumps(_bom_document(binding, spdx=marker.endswith(".spdx.json")))
    if failure == "malformed":
        content = "{ malformed"
    _write(tmp_path, f"vendor/{marker}", content)
    result = _discover(data)
    assert result[0]["path"] == "."
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert any(item["kind"] == "candidate-metadata" for item in result[0]["candidates"])
    assert sum(len(item["sources"]) for item in result) == 1


def test_generated_metadata_does_not_scan_unselected_nested_directories(tmp_path, monkeypatch):
    _write(tmp_path, "selected/item.py")
    _write(tmp_path, "unselected/synthetic.dist-info/METADATA", _core_document({}))
    _write(tmp_path, "selected/deeper/synthetic.egg-info/PKG-INFO", _core_document({}))
    visited = []
    original = Path.iterdir

    def inspect_immediate(directory):
        visited.append(directory)
        return original(directory)

    monkeypatch.setattr(Path, "iterdir", inspect_immediate)
    result = _discover(_scope(tmp_path, "selected/item.py"))
    assert set(visited) == {tmp_path, tmp_path / "selected"}
    assert result[0]["candidates"] == []


def test_generated_metadata_external_symlink_is_not_authority(tmp_path):
    _write(tmp_path, "vendor/item.py")
    data = _scope(tmp_path, "vendor/item.py")
    binding = _generated_binding(data, tmp_path / "vendor", local=True)
    _write(tmp_path, "outside/METADATA", _core_document(binding))
    (tmp_path / "vendor/synthetic.dist-info").symlink_to(
        tmp_path / "outside", target_is_directory=True
    )
    result = _discover(data)
    assert result[0]["path"] == "."
    assert result[0]["boundary_kind"] == "unresolved-root"


def test_duplicate_bound_bom_components_remain_conflicting_candidates(tmp_path):
    _write(tmp_path, "vendor/item.py")
    data = _scope(tmp_path, "vendor/item.py")
    document = _bom_document(_generated_binding(data, tmp_path / "vendor"))
    second = copy.deepcopy(document["components"][0])
    second["bom-ref"] = "second-fixture"
    document["components"].append(second)
    _write(tmp_path, "vendor/bom.json", json.dumps(document))
    result = _discover(data)
    assert result[0]["path"] == "."
    assert any("Multiple components" in item.get("detail", "") for item in result[0]["candidates"])


@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("dirty_source", ["owned", "nested"])
def test_revision_bound_core_metadata_requires_current_selected_git_bytes(
    tmp_path, dirty, dirty_source
):
    directory = tmp_path / "vendor"
    source = _write(tmp_path, "vendor/item.py")
    nested = _write(tmp_path, "vendor/nested/item.py")
    _write(tmp_path, "vendor/nested/pyproject.toml", '[project]\nname="nested-fixture"\n')
    subprocess.run(["git", "init", "-q", str(directory)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(directory), "add", "item.py", "nested/item.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-qm",
            "Selected fixture",
        ],
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    binding = {
        "mpy:source:root": ".",
        "mpy:source:path": ".",
        "mpy:source:revision": revision,
    }
    _write(tmp_path, "vendor/synthetic.dist-info/METADATA", _core_document(binding))
    if dirty:
        selected = source if dirty_source == "owned" else nested
        selected.write_text("value = 2\n", encoding="utf-8")
    data = _scope(tmp_path, "vendor/item.py", "vendor/nested/item.py")
    result = _discover(data)
    assert result[0]["path"] == ("." if dirty else "vendor")
    assert result[0]["boundary_kind"] == ("unresolved-root" if dirty else "package-metadata")
    assert result[1]["path"] == "vendor/nested"
    assert len(result[0]["sources"]) == len(result[1]["sources"]) == 1
    current = boundaries._binding_matches(data, directory, binding)
    assert current is not dirty
    metadata = collect_metadata(
        directory,
        selected_files=[source],
        binding_files=[source, nested],
        source_context={"revision": revision if current else None},
    )
    assert ("name" in metadata["fields"]) is not dirty
    if dirty:
        assert any(
            issue["field"] == "applicability" and issue["status"] == "unbound"
            for issue in metadata["issues"]
        )


def test_bound_bom_retains_all_native_compiler_rows(tmp_path):
    _write(tmp_path, "vendor/item.c", "int value;\n")
    data = _scope(tmp_path)
    data["groups"]["native"] = [
        _record("vendor/item.c", native=True),
        _record("vendor/item.c", native=True),
    ]
    data["groups"]["native"][1]["compilation"]["arguments"].append("-DSECOND")
    document = _bom_document(_generated_binding(data, tmp_path / "vendor"))
    _write(tmp_path, "vendor/bom.json", json.dumps(document))
    result = _discover(data)
    assert len(result) == 1
    assert result[0]["path"] == "vendor"
    assert len(result[0]["sources"]) == 2
    assert all(group == "native" for group, _ in result[0]["sources"])


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("marker", ["PKG-INFO", "bom.json", "synthetic.spdx.json"])
def test_nested_owned_selection_shares_descendant_binding_not_licensing(tmp_path, local, marker):
    directory = tmp_path / "pkg"
    parent_python = _write(tmp_path, "pkg/item.py", "# SPDX-License-Identifier: MIT\n")
    parent_native = _write(tmp_path, "pkg/item.c", "// SPDX-License-Identifier: MIT\n")
    child_python = _write(
        tmp_path, "pkg/vendor/item.py", "# SPDX-License-Identifier: Apache-2.0\n"
    )
    child_native = _write(
        tmp_path, "pkg/vendor/item.c", "// SPDX-License-Identifier: Apache-2.0\n"
    )
    _write(
        tmp_path,
        "pkg/vendor/pyproject.toml",
        '[project]\nname="nested-fixture"\nversion="2"\nlicense="Apache-2.0"\n',
    )
    _write(
        tmp_path,
        "pkg/vendor/REUSE.toml",
        'version=1\n[[annotations]]\npath="**"\nprecedence="override"\n'
        'SPDX-License-Identifier="Apache-2.0"\nSPDX-FileCopyrightText="Nested Fixture"\n',
    )
    (directory / "alias.py").symlink_to(parent_python)
    data = _scope(tmp_path, "pkg/item.py", "pkg/alias.py", "pkg/vendor/item.py")
    data["groups"]["native"] = [
        _record("pkg/item.c", native=True),
        _record("pkg/item.c", native=True),
        _record("pkg/vendor/item.c", native=True),
        _record("pkg/vendor/item.c", native=True),
    ]
    data["groups"]["native"][1]["compilation"]["arguments"].append("-DPARENT_SECOND")
    data["groups"]["native"][3]["compilation"]["arguments"].append("-DCHILD_SECOND")
    binding = _generated_binding(data, directory, local=local)
    content = (
        _core_document(binding)
        if marker == "PKG-INFO"
        else json.dumps(_bom_document(binding, spdx=marker.endswith(".spdx.json")))
    )
    _write(tmp_path, f"pkg/{marker}", content)
    scope = parse_scope(data)
    discovered = boundaries.discover_boundaries(scope)
    parent, child = discovered
    assert [item["path"] for item in discovered] == ["pkg", "pkg/vendor"]
    assert [len(item["sources"]) for item in discovered] == [4, 3]
    assert parent["boundary_kind"] == child["boundary_kind"] == "package-metadata"
    all_selected = [
        source_path(scope, record)
        for group in (FROZEN_GROUP, "native")
        for record in scope["groups"][group]
    ]
    assert metadata_digest(directory, all_selected) == binding["mpy:source:sha256"]
    assert (
        metadata_digest(directory, [parent_python, parent_native]) != binding["mpy:source:sha256"]
    )
    context = {"root": "source", "root_directory": tmp_path}
    parent_metadata = collect_metadata(
        directory,
        selected_files=[source_path(scope, record) for _, record in parent["sources"]],
        binding_files=all_selected,
        source_context=context,
    )
    child_metadata = collect_metadata(
        directory / "vendor",
        selected_files=[source_path(scope, record) for _, record in child["sources"]],
        binding_files=[child_python, child_native],
        source_context=context,
    )
    assert parent_metadata["fields"]["name"] == "generated-fixture"
    assert parent_metadata["fields"]["license"] == "MIT"
    assert child_metadata["fields"]["name"] == "nested-fixture"
    assert child_metadata["fields"]["license"] == "Apache-2.0"
    assert all(
        result["fields"]["license_applicability"] == "confirmed"
        for result in (parent_metadata, child_metadata)
    )
    assert str(directory / "vendor/REUSE.toml") not in parent_metadata["markers"]
    assert {
        row["selected_path"]
        for row in parent_metadata["evidence"]
        if row.get("applicability") == "selected-file"
    } == {str(parent_python), str(parent_native)}
    assert any(
        row.get("detail") == "selected-file-annotations" and row["total"] == 2
        for row in parent_metadata["evidence"]
    )
    child_python.write_text("# SPDX-License-Identifier: BSD-2-Clause\n", encoding="utf-8")
    assert not boundaries._binding_matches(scope, directory, binding)
    stale = collect_metadata(
        directory,
        selected_files=[parent_python, parent_native],
        binding_files=all_selected,
        source_context=context,
    )
    assert "name" not in stale["fields"]
    assert any(
        issue["field"] == "applicability" and issue["status"] == "stale"
        for issue in stale["issues"]
    )
    assert stale["fields"]["license"] == "MIT"


def test_library_json_owns_only_selected_directories_and_the_nearest_wins(tmp_path):
    _write(tmp_path, "outer/library.json", '{"name": "outer", "version": "1"}')
    _write(tmp_path, "outer/src/item.py")
    _write(tmp_path, "outer/inner/library.json", '{"name": "inner", "version": "2"}')
    _write(tmp_path, "outer/inner/src/item.py")
    _write(tmp_path, "unselected/library.json", '{"name": "unselected", "version": "3"}')
    _write(tmp_path, "unselected/src/item.py")
    result = _discover(_scope(tmp_path, "outer/src/item.py", "outer/inner/src/item.py"))
    assert {item["path"]: item["boundary_kind"] for item in result} == {
        "outer": "package-metadata",
        "outer/inner": "package-metadata",
    }
    assert all("unselected" not in str(item["candidates"]) for item in result)


def test_library_json_symlink_outside_the_root_is_not_a_candidate(tmp_path):
    outside = tmp_path / "outside"
    _write(outside, "library.json", '{"name": "outside"}')
    _write(tmp_path / "root", "pkg/item.py")
    (tmp_path / "root/pkg/library.json").symlink_to(outside / "library.json")
    result = _discover(_scope(tmp_path / "root", "pkg/item.py"))
    assert result[0]["boundary_kind"] == "unresolved-root"
    assert result[0]["candidates"] == []
