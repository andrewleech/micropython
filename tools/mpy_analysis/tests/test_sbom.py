import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import pytest

from mpy_analysis import scope, sbom

MPY_ROOT = Path(__file__).resolve().parents[3]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def repository(path, files):
    path.mkdir()
    git(path, "init", "--quiet")
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=SBOM Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "Selected sources",
    )
    git(path, "remote", "add", "origin", f"https://example.invalid/{path.name}.git")
    return path


def selection(tmp_path):
    library = repository(
        tmp_path / "external",
        {
            "alpha/manifest.py": 'metadata(version="2")\nrequire("beta")\nmodule("alpha.py")\n',
            "alpha/alpha.py": "value = 2\n",
            "beta/manifest.py": 'metadata(version="3")\nmodule("beta.py")\n',
            "beta/beta.py": "value = 3\n",
            "unused/manifest.py": 'metadata(version="9")\nmodule("unused.py")\n',
            "unused/unused.py": "value = 9\n",
        },
    )
    app = repository(
        tmp_path / "application",
        {
            "manifest.py": f'add_library("fixture", {str(library)!r}, prepend=True)\nrequire("alpha", library="fixture")\nmodule("main.py")\n',
            "main.py": "value = 1\n",
            "selected.c": "int selected;\n",
            "unused.c": "int unused;\n",
        },
    )
    inventory = scope.collect_manifest_scope(
        MPY_ROOT,
        [app / "manifest.py"],
        roots={"app": str(app), "library": str(library)},
        owners={"app": "application", "library": "external"},
        path_vars={"MPY_LIB_DIR": str(library)},
        target="fixture",
        variant="test",
        port="unix",
    )
    return inventory, app, library


def properties(component):
    return {item["name"]: item["value"] for item in component.get("properties", [])}


def test_packages_use_selected_external_revision_and_transitive_dependencies(tmp_path):
    inventory, app, library = selection(tmp_path)
    document, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    components = {item["name"]: item for item in document["components"]}
    edges = {item["ref"]: item["dependsOn"] for item in document["dependencies"]}
    assert components["alpha"]["version"] == "2"
    assert components["beta"]["version"] == "3"
    assert properties(components["alpha"])["mpy:source-revision"] == git(
        library, "rev-parse", "HEAD"
    )
    assert components["beta"]["bom-ref"] in edges[components["alpha"]["bom-ref"]]
    assert components["alpha.py"]["bom-ref"] in edges[components["alpha"]["bom-ref"]]
    assert components["beta.py"]["bom-ref"] not in edges[components["alpha"]["bom-ref"]]
    assert "unused" not in components and "unused.py" not in components
    assert len(receipt["sources"]) == 3
    assert document == sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)[0]


def test_compiler_actions_select_sources_without_repository_sweep(tmp_path):
    inventory, app, library = selection(tmp_path)
    database = app / "compile_commands.json"
    database.write_text(
        json.dumps(
            [
                {
                    "directory": str(app),
                    "file": "selected.c",
                    "arguments": ["cc", "-c", "selected.c", "-o", output],
                }
                for output in ("one.o", "two.o")
            ]
        )
    )
    inventory["groups"]["native"] = scope.collect_compdb_sources(
        database, roots=inventory["roots"], owners={"app": "application", "library": "external"}
    )
    document, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    files = [item for item in document["components"] if item["type"] == "file"]
    assert sum(item["name"] == "selected.c" for item in files) == 1
    assert all(item["name"] != "unused.c" for item in files)
    actions = [row for row in receipt["sources"] if row["group"] == "native"]
    assert [row["compilation"]["arguments"][-1] for row in actions] == ["one.o", "two.o"]
    assert actions[0]["component"] == actions[1]["component"]


def test_dirty_source_hash_and_repository_revision_remain_distinct(tmp_path):
    inventory, app, library = selection(tmp_path)
    revision = git(library, "rev-parse", "HEAD")
    content = b"value = 99\n"
    (library / "alpha/alpha.py").write_bytes(content)
    document, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    alpha = next(item for item in document["components"] if item["name"] == "alpha")
    source = next(item for item in document["components"] if item["name"] == "alpha.py")
    assert properties(alpha)["mpy:source-revision"] == revision
    assert source["hashes"] == [{"alg": "SHA-256", "content": hashlib.sha256(content).hexdigest()}]
    assert any(
        gap["kind"] == "dirty-selected-repository" and gap["path"] == str(library)
        for gap in receipt["gaps"]
    )


def test_native_without_compiler_evidence_fails_and_removes_stale_outputs(tmp_path):
    inventory, app, library = selection(tmp_path)
    inventory["groups"]["native"] = [
        {
            "root": "app",
            "path": "selected.c",
            "owner": "application",
            "provenance": {"kind": "manual"},
        }
    ]
    with pytest.raises(ValueError, match="compiler evidence"):
        sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(inventory))
    output = tmp_path / "output"
    output.mkdir()
    (output / "sbom.json").write_text("stale-success")
    (output / "sbom-summary.json").write_text("stale-success")
    assert (
        sbom.main(
            [
                "--scope",
                str(path),
                "--micropython-root",
                str(MPY_ROOT),
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert not (output / "sbom.json").exists()
    assert not (output / "sbom-summary.json").exists()


def test_generated_source_does_not_inherit_committed_content_provenance(tmp_path):
    inventory, app, library = selection(tmp_path)
    generated = app / "generated.c"
    generated.write_text("int generated;\n")
    database = app / "compile_commands.json"
    database.write_text(
        json.dumps(
            [
                {
                    "directory": str(app),
                    "file": "generated.c",
                    "arguments": ["cc", "-c", "generated.c"],
                }
            ]
        )
    )
    inventory["groups"]["native"] = scope.collect_compdb_sources(
        database, roots=inventory["roots"], owners={"app": "application", "library": "external"}
    )
    document, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    source = next(row for row in receipt["sources"] if row["path"] == "generated.c")
    assert source["git_tracked"] is False
    assert source["repository"] is None
    assert all(
        source["component"] not in edge["dependsOn"]
        for edge in document["dependencies"]
        if edge["ref"] in {repo["ref"] for repo in receipt["repositories"]}
    )
    assert any(
        gap["kind"] == "selected-source-not-tracked" and gap["path"] == str(generated)
        for gap in receipt["gaps"]
    )
    component = next(
        item for item in document["components"] if item["bom-ref"] == source["component"]
    )
    assert component["hashes"] == [
        {"alg": "SHA-256", "content": hashlib.sha256(generated.read_bytes()).hexdigest()}
    ]


def test_nested_native_project_retains_containing_repository_origin(tmp_path):
    mpy = repository(
        tmp_path / "micropython", {"tools/manifestfile.py": "# fixture authority identity\n"}
    )
    (mpy / "vendor").mkdir()
    dependency = repository(
        mpy / "vendor/component",
        {
            "family/src/main.c": "int selected;\n",
            "family/CMakeLists.txt": "project(portable_native VERSION 1.2 LANGUAGES C)\n",
        },
    )
    file = "vendor/component/family/src/main.c"
    database = tmp_path / "compile_commands.json"
    database.write_text(
        json.dumps([{"directory": str(mpy), "file": file, "arguments": ["cc", "-c", file]}])
    )
    inventory = {
        "schema_version": 1,
        "roots": {"micropython": str(mpy)},
        "target": "fixture",
        "variant": "test",
        "port": "samd",
        "packages": [],
        "package_requirements": [],
        "groups": {
            "native": scope.collect_compdb_sources(
                database, roots={"micropython": str(mpy)}, owners={"micropython": "micropython"}
            )
        },
    }
    document, receipt = sbom.generate_sbom(inventory, micropython_root=mpy)
    component = next(item for item in document["components"] if item["name"] == "portable_native")
    assert component["version"] == "1.2"
    assert component["externalReferences"] == [
        {"type": "vcs", "url": git(dependency, "remote", "get-url", "origin")}
    ]


def test_package_manifest_alias_uses_physical_repository_revision(tmp_path):
    inventory, app, library = selection(tmp_path)
    alias = app / "manifest-alias.py"
    alias.symlink_to(library / "alpha/manifest.py")
    inventory["roots"]["all"] = str(tmp_path)
    package = next(item for item in inventory["packages"] if item["name"] == "alpha")
    package["root"], package["path"] = "all", "application/manifest-alias.py"
    document, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    alpha = next(item for item in document["components"] if item["name"] == "alpha")
    assert properties(alpha)["mpy:source-revision"] == git(library, "rev-parse", "HEAD")
    assert properties(alpha)["mpy:manifest"] == str((library / "alpha/manifest.py").resolve())


def test_cli_preserves_package_input_that_collides_with_output(tmp_path):
    inventory, app, library = selection(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    manifest = output / "sbom.json"
    content = 'metadata(version="2")\n'
    manifest.write_text(content)
    inventory["roots"]["all"] = str(tmp_path)
    package = next(item for item in inventory["packages"] if item["name"] == "alpha")
    package["root"], package["path"] = "all", "output/sbom.json"
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(inventory))
    assert (
        sbom.main(
            [
                "--scope",
                str(path),
                "--micropython-root",
                str(MPY_ROOT),
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert manifest.read_text() == content


@pytest.mark.parametrize("arguments", ["cc", [42], [], ["cc\x00"], {"cc": "source"}])
def test_direct_native_scope_rejects_malformed_compiler_arguments(tmp_path, arguments):
    inventory, app, library = selection(tmp_path)
    inventory["groups"]["native"] = [
        {
            "root": "app",
            "path": "selected.c",
            "owner": "application",
            "provenance": {"kind": "compilation-database"},
            "compilation": {"directory": str(app), "file": "selected.c", "arguments": arguments},
        }
    ]
    with pytest.raises(ValueError):
        sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)


@pytest.mark.parametrize("failure", ["json", "schema", "missing-source"])
def test_cli_invalidates_stale_success_on_early_scope_failure(tmp_path, failure):
    inventory, app, library = selection(tmp_path)
    path = tmp_path / "scope.json"
    if failure == "json":
        path.write_text("{invalid")
    else:
        if failure == "schema":
            inventory["schema_version"] = 999
        else:
            (app / "main.py").unlink()
        path.write_text(json.dumps(inventory))
    output = tmp_path / "output"
    output.mkdir()
    (output / "sbom.json").write_text("stale-success")
    (output / "sbom-summary.json").write_text('{"status":"generated"}')
    assert (
        sbom.main(
            [
                "--scope",
                str(path),
                "--micropython-root",
                str(MPY_ROOT),
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert not (output / "sbom.json").exists()
    assert not (output / "sbom-summary.json").exists()


@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
@pytest.mark.parametrize("invalid_scope", [False, True])
def test_cli_preserves_referenced_license_output_and_hardlink(
    tmp_path, alias, malformed, invalid_scope
):
    inventory, app, library = selection(tmp_path)
    if invalid_scope:
        inventory["schema_version"] = 999
    legal = app / "legal"
    legal.mkdir()
    license_input = legal / ("license.txt" if alias else "sbom.json")
    content = b"\xff invalid UTF-8" if malformed else b"Retain this licence text.\n"
    license_input.write_bytes(content)
    if alias:
        (legal / "sbom.json").hardlink_to(license_input)
    (app / "pyproject.toml").write_text(
        '[project]\nname="application"\nversion="1"\nlicense="MIT"\n'
        f'license-files=["legal/{license_input.name}"]\n'
    )
    summary = legal / "sbom-summary.json"
    summary.write_text("existing summary")
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(inventory))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(Path(sbom.__file__).parents[1]), environment.get("PYTHONPATH")])
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mpy_analysis.sbom",
            "--scope",
            str(scope_path),
            "--micropython-root",
            str(MPY_ROOT),
            "--output-dir",
            str(legal),
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "must not overwrite" in result.stderr
    assert license_input.read_bytes() == content
    assert (legal / "sbom.json").read_bytes() == content
    assert summary.read_text() == "existing summary"


def test_cli_supports_output_inside_project_build_directory(tmp_path):
    inventory, app, library = selection(tmp_path)
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(inventory))
    output = app / "build"
    assert (
        sbom.main(
            [
                "--scope",
                str(path),
                "--micropython-root",
                str(MPY_ROOT),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    sbom.validate_cyclonedx(json.loads((output / "sbom.json").read_text()))
    assert json.loads((output / "sbom-summary.json").read_text())["status"] == "generated"


@pytest.mark.parametrize("failure", ["scope", "profile"])
def test_cli_invalidates_safe_project_build_output_on_malformed_input(tmp_path, failure):
    inventory, app, library = selection(tmp_path)
    if failure == "scope":
        inventory["schema_version"] = 999
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(inventory))
    output = app / "build"
    output.mkdir()
    for name in ("sbom.json", "sbom-summary.json"):
        (output / name).write_text("stale success")
    arguments = [
        "--scope",
        str(path),
        "--micropython-root",
        str(MPY_ROOT),
        "--output-dir",
        str(output),
    ]
    if failure == "profile":
        declaration = tmp_path / "declaration.json"
        declaration.write_text(
            '{"bomFormat": "CycloneDX", "specVersion": "1.6", "version": "bad"}'
        )
        arguments += ["--dependency-metadata", str(declaration)]
    assert sbom.main(arguments) == 2
    assert not (output / "sbom.json").exists()
    assert not (output / "sbom-summary.json").exists()


@pytest.mark.parametrize(
    "origin,canonical",
    [
        ("git@example.invalid:team/component.git", "ssh://git@example.invalid/team/component.git"),
        ("example.invalid:team/component.git", "ssh://example.invalid/team/component.git"),
        ("git@example.invalid:team/a b.git", "ssh://git@example.invalid/team/a%20b.git"),
        ("git@[2001:db8::1]:team/component.git", "ssh://git@[2001:db8::1]/team/component.git"),
        (
            "https://example.invalid/team/component.git",
            "https://example.invalid/team/component.git",
        ),
        (
            "ssh://git@example.invalid:2222/team/component.git",
            "ssh://git@example.invalid:2222/team/component.git",
        ),
    ],
)
def test_repository_origins_are_canonical_uris_with_raw_evidence(tmp_path, origin, canonical):
    inventory, app, library = selection(tmp_path)
    git(library, "remote", "set-url", "origin", origin)
    document, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    record = next(item for item in receipt["repositories"] if item["directory"] == str(library))
    assert record["origin"] == origin
    assert record["vcs_url"] == canonical
    repository_component = next(
        item for item in document["components"] if item["bom-ref"] == record["ref"]
    )
    assert properties(repository_component)["mpy:origin"] == origin
    assert repository_component["externalReferences"] == [{"type": "vcs", "url": canonical}]
    assert parse_qs(urlparse(repository_component["purl"]).query)["vcs_url"] == [canonical]
    alpha = next(item for item in document["components"] if item["name"] == "alpha")
    assert {"type": "vcs", "url": canonical} in alpha["externalReferences"]
    sbom.validate_cyclonedx(document)


def test_invalid_origin_remains_explicit_without_invalid_vcs_reference(tmp_path):
    inventory, app, library = selection(tmp_path)
    origin = "https://example.invalid/a b.git"
    git(library, "remote", "set-url", "origin", origin)
    document, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    record = next(item for item in receipt["repositories"] if item["directory"] == str(library))
    assert record["origin"] == origin
    assert record["vcs_url"] is None
    assert any(
        gap["kind"] == "repository-origin-unsupported" and gap["origin"] == origin
        for gap in receipt["gaps"]
    )
    repository_component = next(
        item for item in document["components"] if item["bom-ref"] == record["ref"]
    )
    assert "externalReferences" not in repository_component
    assert "purl" not in repository_component
    sbom.validate_cyclonedx(document)


def test_cli_rejects_final_bom_schema_failure_without_success_output(tmp_path, monkeypatch):
    inventory, app, library = selection(tmp_path)
    original_assemble = sbom.assemble

    def invalid_component(builder, *args):
        result = original_assemble(builder, *args)
        next(iter(builder.components.values()))["externalReferences"] = [
            {"type": "website", "url": "https://example.invalid/invalid url"}
        ]
        return result

    monkeypatch.setattr(sbom, "assemble", invalid_component)
    with pytest.raises(ValueError):
        sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(inventory))
    output = app / "build"
    output.mkdir()
    for name in ("sbom.json", "sbom-summary.json"):
        (output / name).write_text("stale success")
    assert (
        sbom.main(
            [
                "--scope",
                str(path),
                "--micropython-root",
                str(MPY_ROOT),
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert not (output / "sbom.json").exists()
    assert not (output / "sbom-summary.json").exists()


@pytest.mark.parametrize("invalid_scope", [False, True])
@pytest.mark.parametrize("invalid_bom", [None, "json", "schema"])
def test_cli_preserves_selected_license_sidecar_output_alias(tmp_path, invalid_scope, invalid_bom):
    inventory, app, library = selection(tmp_path)
    (app / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    output = app / "legal"
    output.mkdir()
    terms = output / "sbom.json"
    content = "# SPDX-License-Identifier: MIT\n# SPDX-FileCopyrightText: 2026 Fixture\n"
    terms.write_text(content)
    (app / "main.py.license").symlink_to("legal/sbom.json")
    if invalid_bom:
        (app / "bom.json").write_text(
            "{"
            if invalid_bom == "json"
            else json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "version": 1,
                    "components": [{"type": "library"}],
                }
            )
        )
    if invalid_scope:
        inventory["schema_version"] = 999
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(json.dumps(inventory))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mpy_analysis.sbom",
            "--scope",
            str(scope_file),
            "--micropython-root",
            str(MPY_ROOT),
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert terms.read_text() == content
    assert (app / "main.py.license").is_symlink()
    assert not (output / "sbom-summary.json").exists()
