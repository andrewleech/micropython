import hashlib
import json
from pathlib import Path

import pytest

from mpy_analysis import metadata
from mpy_analysis.metadata_native import parse_native


def write_files(directory, files):
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return directory


def collect(directory, files=None, **kwargs):
    return metadata.collect_metadata(directory, selected_files=files, **kwargs)


def statuses(result, field):
    return {issue["status"] for issue in result["issues"] if issue["field"] == field}


def binding(directory, selected):
    return {
        "mpy:source:root": ".",
        "mpy:source:path": ".",
        "mpy:source:sha256": metadata.selected_digest(directory, selected),
    }


def cdx_document(properties=None, name="synthetic-upstream", version="1.0"):
    component = {
        "type": "library",
        "bom-ref": "synthetic-main",
        "name": name,
        "version": version,
        "licenses": [{"license": {"id": "MIT"}}],
    }
    if properties is not None:
        component["properties"] = [
            {"name": key, "value": value} for key, value in properties.items()
        ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "components": [component],
    }


def spdx_document(properties=None):
    package = {
        "SPDXID": "SPDXRef-Synthetic",
        "name": "synthetic-spdx",
        "versionInfo": "3.4",
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseDeclared": "MIT",
        "licenseConcluded": "MIT",
        "copyrightText": "NOASSERTION",
    }
    if properties is not None:
        package["annotations"] = [
            {
                "annotationDate": "2026-01-01T00:00:00Z",
                "annotationType": "OTHER",
                "annotator": "Tool: synthetic-fixture",
                "comment": json.dumps(properties),
            }
        ]
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "synthetic-document",
        "documentNamespace": "https://example.invalid/synthetic/unique",
        "creationInfo": {
            "creators": ["Tool: synthetic-fixture"],
            "created": "2026-01-01T00:00:00Z",
        },
        "packages": [package],
    }


def test_manifest_metadata_promotes_standard_fields(tmp_path):
    values = {
        "name": "synthetic-manifest",
        "version": "2.2",
        "author": "Fixture Author",
        "description": "Declared metadata",
        "license": "MIT",
        "url": "https://example.invalid/project",
    }
    result = collect(tmp_path, manifest_metadata=values)
    assert result["fields"] == {**values, "license_applicability": "confirmed"}
    assert all(row["parser"] == "micropython-manifest-authority-1" for row in result["evidence"])
    assert not statuses(result, "license_applicability")


def test_missing_name_is_not_derived_from_directory(tmp_path):
    result = collect(tmp_path, manifest_metadata={"version": "2"})
    assert result["fields"] == {"version": "2"}
    assert "missing" in statuses(result, "name")


def test_legacy_metadata_key_values_and_literal_setup_agree(tmp_path):
    write_files(
        tmp_path,
        {
            "metadata.txt": "type = module\nversion = 2.2\nauthor = Fixture Author\ndepends = synthetic-one, synthetic-two\n",
            "setup.py": "from setuptools import setup\nsetup(name='synthetic-legacy', version='2.2', author='Fixture Author', license='MIT', url='https://example.invalid/legacy')\n",
            "LICENSE": "MIT License\nArbitrary retained licence text.\n",
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-legacy"
    assert result["fields"]["version"] == "2.2"
    assert result["fields"]["license"] == "MIT"
    assert result["declared_dependencies"] == ["synthetic-one", "synthetic-two"]
    assert any(row["field"] == "type" and row["value"] == "module" for row in result["evidence"])
    assert str(tmp_path / "LICENSE") in result["markers"]


def test_legacy_colon_metadata_is_source_declaration(tmp_path):
    write_files(
        tmp_path,
        {
            "metadata.txt": "Name: synthetic-colon\nVersion: 2.4\nLicense: MIT\nRequires: synthetic-one\n"
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-colon"
    assert result["fields"]["license"] == "MIT"
    assert result["declared_dependencies"] == ["synthetic-one"]


def test_static_project_spdx_and_authors(tmp_path):
    write_files(
        tmp_path,
        {
            "pyproject.toml": """[project]
name = "synthetic-project"
version = "1.2.3"
description = "Static fixture"
authors = [{name = "Fixture Author", email = "author@example.invalid"}]
license = "MIT OR Apache-2.0"
dependencies = ["synthetic-one>=2", "synthetic-two; python_version<'3.11'"]
[project.urls]
Homepage = "https://example.invalid/project"
Repository = "https://example.invalid/repository"
[build-system]
requires = ["synthetic-build-tool"]
"""
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["author"] == "Fixture Author <author@example.invalid>"
    assert result["fields"]["license"] == "MIT OR Apache-2.0"
    assert result["fields"]["url"] == "https://example.invalid/project"
    assert "synthetic-build-tool" not in result["declared_dependencies"]
    assert len(result["declared_dependencies"]) == 2


def test_dynamic_project_can_use_independent_static_assertion(tmp_path):
    write_files(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="synthetic-dynamic"\ndynamic=["version", "license"]\n',
            "setup.cfg": "[metadata]\nversion=3\nlicense=MIT\n",
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["version"] == "3"
    assert result["fields"]["license"] == "MIT"
    assert "unsupported" in statuses(result, "version")
    assert any(
        row["status"] == "dynamic" and row["field"] == "version" for row in result["evidence"]
    )


def test_static_dynamic_same_project_is_conflicting(tmp_path):
    write_files(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="synthetic-dynamic"\nversion="4"\ndynamic=["version"]\n'
        },
    )
    result = collect(tmp_path)
    assert "version" not in result["fields"]
    assert "conflicting" in statuses(result, "version")


@pytest.mark.parametrize("value", ['{text="MIT"}', '{file="LICENSE"}'])
def test_legacy_project_license_is_text_not_spdx(tmp_path, value):
    write_files(
        tmp_path,
        {
            "pyproject.toml": f'[project]\nname="synthetic-text"\nlicense={value}\n',
            "LICENSE": "MIT\n",
        },
    )
    result = collect(tmp_path)
    assert "license" not in result["fields"]
    assert "unsupported" in statuses(result, "license")


def test_project_license_file_glob_is_retained(tmp_path):
    write_files(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="synthetic-license-files"\nlicense="MIT"\nlicense-files=["licenses/*.txt"]\n',
            "licenses/legal.txt": "Retained legal terms\n",
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["license"] == "MIT"
    assert str(tmp_path / "licenses/legal.txt") in result["markers"]
    assert any(row.get("text") == "Retained legal terms\n" for row in result["evidence"])


def test_setup_cfg_references_are_not_evaluated(tmp_path):
    write_files(
        tmp_path,
        {
            "setup.cfg": "[metadata]\nname=synthetic-config\nversion=attr: synthetic.__version__\ndescription=file: DESCRIPTION.txt\nlicense=MIT\n[options]\ninstall_requires=\n synthetic-one>=1\n synthetic-two\n",
            "DESCRIPTION.txt": "Literal referenced description\n",
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-config"
    assert "version" not in result["fields"]
    assert "description" not in result["fields"]
    assert "unsupported" in statuses(result, "version")
    assert str(tmp_path / "DESCRIPTION.txt") in result["markers"]
    assert result["declared_dependencies"] == ["synthetic-one>=1", "synthetic-two"]


def test_setup_py_never_executes_sentinel(tmp_path):
    sentinel = tmp_path / "executed"
    write_files(
        tmp_path,
        {
            "setup.py": f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\nfrom setuptools import setup\nsetup(name='synthetic-safe', version='9', license='MIT')\n"
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-safe"
    assert not sentinel.exists()


def test_setup_computed_field_preserves_literal_fields(tmp_path):
    write_files(
        tmp_path,
        {
            "setup.py": "from setuptools import setup\nsetup(name='synthetic-partial', version=get_version(), install_requires=['synthetic-one'])\n"
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-partial"
    assert "version" not in result["fields"]
    assert "unsupported" in statuses(result, "version")
    assert result["declared_dependencies"] == ["synthetic-one"]


@pytest.mark.parametrize(
    "body",
    [
        "if True:\n setup(name='synthetic-conditional', version='1')\n",
        "setup(name='synthetic-one')\nsetup(name='synthetic-two')\n",
        "setup(**{'name': 'synthetic-expanded'})\n",
        "setup = other_callable\nsetup(name='synthetic-rebound')\n",
    ],
)
def test_ambiguous_setup_cannot_assert_identity(tmp_path, body):
    write_files(tmp_path, {"setup.py": "from setuptools import setup\n" + body})
    result = collect(tmp_path)
    assert "name" not in result["fields"]
    assert "unsupported" in statuses(result, "name")


def test_conflict_not_first_wins(tmp_path):
    write_files(
        tmp_path,
        {
            "setup.cfg": "[metadata]\nname=synthetic-config\nversion=1\nlicense=MIT\n",
            "pyproject.toml": '[project]\nname="synthetic-project"\nversion="2"\nlicense="Apache-2.0"\n',
        },
    )
    result = collect(tmp_path)
    for field in ("name", "version", "license"):
        assert field not in result["fields"]
        assert "conflicting" in statuses(result, field)
        assert (
            len(
                [
                    row
                    for row in result["evidence"]
                    if row["field"] == field and row["status"] == "known"
                ]
            )
            == 2
        )


@pytest.mark.parametrize(
    "expression",
    [
        "MadeUp-License",
        "MIT WITH MadeUp-exception",
        "LicenseRef-",
        "MIT OR",
        "",
        "NONE",
        "NOASSERTION",
    ],
)
def test_invalid_spdx_expressions_rejected(expression):
    with pytest.raises(ValueError):
        metadata.validate_license(expression)


@pytest.mark.parametrize(
    "expression",
    ["MIT", "MIT OR Apache-2.0", "GPL-2.0-only WITH Classpath-exception-2.0", "LicenseRef-Custom"],
)
def test_valid_spdx_expression_syntax(expression):
    assert metadata.validate_license(expression)


def test_custom_licence_requires_retained_text(tmp_path):
    result = collect(tmp_path, manifest_metadata={"license": "LicenseRef-Custom"})
    assert "license" not in result["fields"]
    assert "unsupported" in statuses(result, "license")
    write_files(tmp_path, {"LICENSES/LicenseRef-Custom.txt": "Custom terms\n"})
    result = collect(tmp_path, manifest_metadata={"license": "LicenseRef-Custom"})
    assert result["fields"]["license"] == "LicenseRef-Custom"
    assert str(tmp_path / "LICENSES/LicenseRef-Custom.txt") in result["markers"]
    assert any(row.get("text") == "Custom terms\n" for row in result["evidence"])


def test_root_licence_text_does_not_cover_unannotated_selection(tmp_path):
    write_files(tmp_path, {"LICENSE": "MIT License\n", "selected.py": "value = 1\n"})
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert "license" not in result["fields"]
    assert "missing" in statuses(result, "license_applicability")
    assert any(
        row["field"] == "license_text" and row["applicability"] == "unresolved"
        for row in result["evidence"]
    )


def test_inline_spdx_applies_only_to_selected_files(tmp_path):
    write_files(
        tmp_path,
        {
            "selected.py": "# SPDX-License-Identifier: MIT\n# SPDX-FileCopyrightText: 2026 Fixture Author\nx=1\n",
            "unselected.py": "# SPDX-License-Identifier: Apache-2.0\nx=2\n",
            "LICENSES/MIT.txt": "MIT terms\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert result["fields"]["license"] == "MIT"
    assert str(tmp_path / "unselected.py") not in result["markers"]
    assert str(tmp_path / "LICENSES/MIT.txt") in result["markers"]
    assert any(row["field"] == "copyright" for row in result["evidence"])


def test_sidecar_replaces_inline_header(tmp_path):
    write_files(
        tmp_path,
        {
            "selected.py": "# SPDX-License-Identifier: Apache-2.0\n",
            "selected.py.license": "SPDX-License-Identifier: MIT\nSPDX-FileCopyrightText: Fixture Author\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert result["fields"]["license"] == "MIT"
    assert str(tmp_path / "selected.py.license") in result["markers"]


def test_mixed_selected_licences_preserve_per_file_scope(tmp_path):
    write_files(
        tmp_path,
        {
            "one.py": "# SPDX-License-Identifier: MIT\n",
            "two.py": "# SPDX-License-Identifier: Apache-2.0\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "one.py", tmp_path / "two.py"])
    assert metadata.LICENSING.is_equivalent(result["fields"]["license"], "MIT AND Apache-2.0")
    rows = [
        row
        for row in result["evidence"]
        if row.get("applicability") == "selected-file" and row["field"] == "license"
    ]
    assert {row["selected_path"] for row in rows} == {
        str(tmp_path / "one.py"),
        str(tmp_path / "two.py"),
    }


def test_partial_file_spdx_does_not_promote_component_licence(tmp_path):
    write_files(tmp_path, {"one.py": "# SPDX-License-Identifier: MIT\n", "two.py": "value=2\n"})
    result = collect(tmp_path, [tmp_path / "one.py", tmp_path / "two.py"])
    assert "license" not in result["fields"]
    assert "unsupported" in statuses(result, "license_applicability")


def test_reuse_override_and_nested_scope_without_tree_sweep(tmp_path):
    write_files(
        tmp_path,
        {
            "REUSE.toml": 'version=1\n[[annotations]]\npath="**"\nprecedence="closest"\nSPDX-License-Identifier="MIT"\nSPDX-FileCopyrightText="Fixture Author"\n',
            "nested/REUSE.toml": 'version=1\n[[annotations]]\npath="selected.py"\nprecedence="override"\nSPDX-License-Identifier="Apache-2.0"\nSPDX-FileCopyrightText="Nested Fixture Author"\n',
            "nested/selected.py": "# SPDX-License-Identifier: BSD-2-Clause\n",
            "unused/REUSE.toml": "not valid toml",
            "unused/file.py": "# SPDX-License-Identifier: GPL-3.0-only\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "nested/selected.py"])
    assert result["fields"]["license"] == "Apache-2.0"
    assert str(tmp_path / "nested/REUSE.toml") in result["markers"]
    assert str(tmp_path / "unused/REUSE.toml") not in result["markers"]
    assert str(tmp_path / "unused/file.py") not in result["markers"]


def test_reuse_aggregate_preserves_multiple_assertions(tmp_path):
    write_files(
        tmp_path,
        {
            "REUSE.toml": 'version=1\n[[annotations]]\npath="selected.py"\nprecedence="aggregate"\nSPDX-License-Identifier="MIT"\nSPDX-FileCopyrightText="Fixture Author"\n',
            "selected.py": "# SPDX-License-Identifier: Apache-2.0\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert metadata.LICENSING.is_equivalent(result["fields"]["license"], "MIT AND Apache-2.0")


def test_invalid_reuse_has_explicit_issue(tmp_path):
    write_files(tmp_path, {"REUSE.toml": "not valid toml", "selected.py": "x=1\n"})
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert "unsupported" in statuses(result, "license_applicability")
    assert str(tmp_path / "REUSE.toml") in result["markers"]


def test_source_selection_escape_rejected(tmp_path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("x=1\n")
    with pytest.raises(ValueError, match="contained"):
        collect(tmp_path, [outside])


def test_reference_escape_and_symlink_are_issues(tmp_path):
    outside = tmp_path.parent / "outside-license.txt"
    outside.write_text("Outside text\n")
    (tmp_path / "LICENSE").symlink_to(outside)
    write_files(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="synthetic-escape"\nlicense={file="../outside-license.txt"}\n'
        },
    )
    result = collect(tmp_path)
    assert "unsupported" in statuses(result, "license")
    assert str(outside) not in result["markers"]


@pytest.mark.parametrize(
    "filename", ["PKG-INFO", "synthetic.dist-info/METADATA", "synthetic.egg-info/PKG-INFO"]
)
def test_generated_core_metadata_unbound(filename, tmp_path):
    write_files(
        tmp_path,
        {
            filename: "Metadata-Version: 2.4\nName: synthetic-generated\nVersion: 8\nLicense-Expression: MIT\nRequires-Dist: synthetic-one\n"
        },
    )
    result = collect(tmp_path)
    assert not result["fields"]
    assert "unbound" in statuses(result, "name")
    assert result["declared_dependencies"] == []


def test_core_metadata_selected_hash_binding(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    digest = metadata.selected_digest(tmp_path, selected)
    write_files(
        tmp_path,
        {
            "PKG-INFO": f"Metadata-Version: 2.4\nName: synthetic-bound\nVersion: 8\nLicense-Expression: MIT\nRequires-Dist: synthetic-one>=2\nX-Mpy-Source-Root: .\nX-Mpy-Source-Path: .\nX-Mpy-Source-Sha256: {digest}\n"
        },
    )
    result = collect(tmp_path, selected)
    assert result["fields"]["name"] == "synthetic-bound"
    assert result["fields"]["license"] == "MIT"
    assert result["declared_dependencies"] == ["synthetic-one>=2"]


def test_cmake_literal_project_metadata_and_no_license_macro_guessing(tmp_path):
    write_files(
        tmp_path,
        {
            "CMakeLists.txt": 'cmake_minimum_required(VERSION 3.16)\nproject(synthetic_native VERSION 2.3.4 DESCRIPTION "Native fixture" HOMEPAGE_URL "https://example.invalid/native" LANGUAGES C)\nset(SYNTHETIC_LICENSE "MIT")\n'
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic_native"
    assert result["fields"]["version"] == "2.3.4"
    assert result["fields"]["description"] == "Native fixture"
    assert "license" not in result["fields"]


def test_cmake_variable_version_unresolved(tmp_path):
    write_files(
        tmp_path, {"CMakeLists.txt": "project(synthetic_native VERSION ${SYNTHETIC_VERSION})\n"}
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic_native"
    assert "version" not in result["fields"]
    assert "unsupported" in statuses(result, "version")


@pytest.mark.parametrize(
    "text",
    [
        '# project(fake VERSION 9)\nset(TEXT "project(fake VERSION 9)")\n',
        "if(ENABLE)\nproject(synthetic VERSION 1)\nendif()\n",
        "project(one VERSION 1)\nproject(two VERSION 2)\n",
        "function(build)\nproject(synthetic VERSION 1)\nendfunction()\n",
    ],
)
def test_cmake_comments_strings_control_flow_and_ambiguity(text):
    fields, issues = parse_native(text, "CMakeLists.txt")
    assert not fields
    assert issues


def test_meson_literal_array_license(tmp_path):
    write_files(
        tmp_path,
        {
            "meson.build": "# comment mentioning project('fake')\nproject('synthetic-meson', 'c', version: '1.2', license: ['MIT', 'Apache-2.0'])\n"
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-meson"
    assert result["fields"]["version"] == "1.2"
    assert metadata.LICENSING.is_equivalent(result["fields"]["license"], "MIT AND Apache-2.0")


def test_meson_computed_version(tmp_path):
    write_files(
        tmp_path,
        {"meson.build": "project('synthetic-meson', version: get_version(), license: 'MIT')\n"},
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-meson"
    assert "version" not in result["fields"]
    assert "unsupported" in statuses(result, "version")


@pytest.mark.parametrize(
    "factory,filename", [(cdx_document, "bom.json"), (spdx_document, "bom.spdx.json")]
)
def test_upstream_bom_unbound_never_imported(tmp_path, factory, filename):
    write_files(tmp_path, {filename: json.dumps(factory()), "selected.py": "x=1\n"})
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert not result["fields"]
    assert "unbound" in statuses(result, "name")
    assert any(row["status"] == "unbound" for row in result["evidence"])


@pytest.mark.parametrize(
    "factory,filename", [(cdx_document, "bom.cdx.json"), (spdx_document, "bom.spdx.json")]
)
def test_upstream_bom_exact_hash_binding(tmp_path, factory, filename):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    write_files(tmp_path, {filename: json.dumps(factory(binding(tmp_path, selected)))})
    result = collect(tmp_path, selected)
    assert result["fields"]["version"] in ("1.0", "3.4")
    assert result["fields"]["license"] == "MIT"
    assert any(path.endswith(".schema.json") for path in result["markers"])


def test_stale_hash_bound_bom_not_promoted(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    document = cdx_document(binding(tmp_path, selected))
    write_files(tmp_path, {"bom.json": json.dumps(document), "selected.py": "x=2\n"})
    result = collect(tmp_path, selected)
    assert not result["fields"]
    assert "stale" in statuses(result, "applicability")


def test_revision_binding_requires_source_authority(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    properties = {
        "mpy:source:root": "fixture",
        "mpy:source:path": ".",
        "mpy:source:revision": "a" * 40,
    }
    write_files(tmp_path, {"bom.json": json.dumps(cdx_document(properties))})
    selected = [tmp_path / "selected.py"]
    context = {"root": "fixture", "root_directory": tmp_path, "revision": "a" * 40}
    assert (
        collect(tmp_path, selected, source_context=context)["fields"]["name"]
        == "synthetic-upstream"
    )
    context["revision"] = None
    assert not collect(tmp_path, selected, source_context=context)["fields"]


def test_revision_binding_without_selected_files_cannot_match(tmp_path):
    properties = {"mpy:source:root": ".", "mpy:source:path": ".", "mpy:source:revision": "a" * 40}
    write_files(tmp_path, {"bom.json": json.dumps(cdx_document(properties))})
    result = collect(tmp_path, [], source_context={"revision": "a" * 40})
    assert not result["fields"]
    assert "unbound" in statuses(result, "applicability")


def test_bom_binding_escape_rejected(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    properties = binding(tmp_path, selected)
    properties["mpy:source:path"] = "../escape"
    write_files(tmp_path, {"bom.json": json.dumps(cdx_document(properties))})
    result = collect(tmp_path, selected)
    assert not result["fields"]
    assert "unsupported" in statuses(result, "applicability")


def test_bom_unselected_components_and_dependencies_are_not_selection(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    document = cdx_document(binding(tmp_path, selected))
    document["components"].append(
        {"type": "library", "bom-ref": "unused", "name": "synthetic-unused", "version": "99"}
    )
    document["dependencies"] = [{"ref": "synthetic-main", "dependsOn": ["unused"]}]
    write_files(tmp_path, {"bom.json": json.dumps(document)})
    result = collect(tmp_path, selected)
    assert result["fields"]["name"] == "synthetic-upstream"
    assert result["declared_dependencies"] == ["synthetic-unused"]
    assert any(
        row["value"] == "synthetic-unused" and row["status"] == "unbound"
        for row in result["evidence"]
    )


def test_multiple_components_bound_to_one_boundary_conflict(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    document = cdx_document(binding(tmp_path, selected))
    duplicate = dict(document["components"][0], **{"bom-ref": "duplicate"})
    document["components"].append(duplicate)
    write_files(tmp_path, {"bom.json": json.dumps(document)})
    result = collect(tmp_path, selected)
    assert not result["fields"]
    assert "conflicting" in statuses(result, "applicability")


def test_bom_schema_failure_is_operational(tmp_path):
    document = cdx_document()
    del document["components"][0]["name"]
    write_files(tmp_path, {"bom.json": json.dumps(document)})
    with pytest.raises(ValueError, match="BOM schema validation failed"):
        collect(tmp_path)


def test_spdx_declared_and_concluded_disagreement(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    document = spdx_document(binding(tmp_path, selected))
    document["packages"][0]["licenseConcluded"] = "Apache-2.0"
    write_files(tmp_path, {"bom.spdx.json": json.dumps(document)})
    result = collect(tmp_path, selected)
    assert "license" not in result["fields"]
    assert "conflicting" in statuses(result, "license")
    assert all(
        issue["cause_fields"] == ["license"]
        for issue in result["issues"]
        if issue["field"] in ("license", "license_applicability")
        and issue["status"] in ("conflicting", "unsupported")
    )


def test_spdx_custom_license_text_is_retained(tmp_path):
    write_files(tmp_path, {"selected.py": "x=1\n"})
    selected = [tmp_path / "selected.py"]
    document = spdx_document(binding(tmp_path, selected))
    document["packages"][0]["licenseDeclared"] = "LicenseRef-Fixture"
    document["packages"][0]["licenseConcluded"] = "NOASSERTION"
    document["hasExtractedLicensingInfos"] = [
        {"licenseId": "LicenseRef-Fixture", "extractedText": "Fixture custom terms"}
    ]
    write_files(tmp_path, {"bom.spdx.json": json.dumps(document)})
    result = collect(tmp_path, selected)
    assert result["fields"]["license"] == "LicenseRef-Fixture"
    assert any(
        row["field"] == "license_text" and row["value"] == "Fixture custom terms"
        for row in result["evidence"]
    )


def test_evidence_hash_and_determinism(tmp_path):
    text = "[metadata]\nname=synthetic-stable\nversion=1\nlicense=MIT\n"
    write_files(tmp_path, {"setup.cfg": text})
    result = collect(tmp_path)
    assert result == collect(tmp_path)
    assert {
        row["sha256"]
        for row in result["evidence"]
        if row.get("path") == str(tmp_path / "setup.cfg")
    } == {hashlib.sha256(text.encode()).hexdigest()}
    assert result["markers"] == sorted(set(result["markers"]))


def test_license_applicability_is_normalized_and_method_is_evidence(tmp_path):
    result = collect(tmp_path, manifest_metadata={"license": "MIT"})
    assert result["fields"]["license_applicability"] == "confirmed"
    proof = [row for row in result["evidence"] if row["field"] == "license_applicability"]
    assert proof[0]["value"] == "confirmed"
    assert proof[0]["detail"] == "package-declaration"
    write_files(tmp_path, {"selected.py": "# SPDX-License-Identifier: MIT\n"})
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert result["fields"]["license_applicability"] == "confirmed"
    assert any(row.get("detail") == "selected-file-annotations" for row in result["evidence"])


def test_enclosing_reuse_annotations_apply_to_nested_package(tmp_path):
    write_files(
        tmp_path,
        {
            "REUSE.toml": 'version=1\n[[annotations]]\npath="packages/synthetic/selected.py"\nprecedence="aggregate"\nSPDX-License-Identifier="MIT"\nSPDX-FileCopyrightText="Fixture Author"\n',
            "LICENSES/MIT.txt": "Retained MIT text\n",
            "packages/synthetic/selected.py": "value=1\n",
            "unused/REUSE.toml": "not valid toml",
        },
    )
    directory = tmp_path / "packages/synthetic"
    result = collect(
        directory,
        [directory / "selected.py"],
        source_context={"root": "fixture", "root_directory": tmp_path},
    )
    assert result["fields"]["license"] == "MIT"
    assert result["fields"]["license_applicability"] == "confirmed"
    assert str(tmp_path / "REUSE.toml") in result["markers"]
    assert str(tmp_path / "LICENSES/MIT.txt") in result["markers"]
    assert str(tmp_path / "unused/REUSE.toml") not in result["markers"]


def test_nested_custom_license_text_preserves_scope(tmp_path):
    write_files(
        tmp_path,
        {
            "nested/selected.py": "# SPDX-License-Identifier: LicenseRef-Nested\n",
            "nested/LICENSES/LicenseRef-Nested.txt": "Nested custom terms\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "nested/selected.py"])
    assert result["fields"]["license"] == "LicenseRef-Nested"
    assert result["fields"]["license_applicability"] == "confirmed"
    assert str(tmp_path / "nested/LICENSES/LicenseRef-Nested.txt") in result["markers"]


def test_same_licenseref_with_different_scoped_text_is_conflicting(tmp_path):
    write_files(
        tmp_path,
        {
            "one/selected.py": "# SPDX-License-Identifier: LicenseRef-Local\n",
            "one/LICENSES/LicenseRef-Local.txt": "One set of terms\n",
            "two/selected.py": "# SPDX-License-Identifier: LicenseRef-Local\n",
            "two/LICENSES/LicenseRef-Local.txt": "Different terms\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "one/selected.py", tmp_path / "two/selected.py"])
    assert "license" not in result["fields"]
    assert "license_applicability" not in result["fields"]
    assert "conflicting" in statuses(result, "license")
    text_cause = ["license_text:LicenseRef-Local"]
    assert all(
        issue["cause_fields"] == text_cause
        for issue in result["issues"]
        if issue["field"] in ("license", "license_applicability")
        and issue["status"] in ("conflicting", "unsupported")
    )
    assert any(
        issue.get("values") == ["LicenseRef-Local"]
        for issue in result["issues"]
        if issue["field"] == "license" and issue["status"] == "conflicting"
    )


def test_source_local_dep5_under_enclosing_root_applies_relative_paths(tmp_path):
    write_files(
        tmp_path,
        {
            "packages/synthetic/.reuse/dep5": "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n\nFiles: selected.py\nCopyright: 2026 Fixture Author\nLicense: MIT\n",
            "packages/synthetic/selected.py": "value=1\n",
        },
    )
    directory = tmp_path / "packages/synthetic"
    result = collect(
        directory, [directory / "selected.py"], source_context={"root_directory": tmp_path}
    )
    assert result["fields"]["license"] == "MIT"
    assert str(directory / ".reuse/dep5") in result["markers"]


def test_bom_duplicate_json_keys_rejected(tmp_path):
    write_files(
        tmp_path,
        {
            "bom.json": '{"bomFormat":"CycloneDX","specVersion":"1.6","components":[],"components":[]}'
        },
    )
    with pytest.raises(ValueError, match="duplicate JSON"):
        collect(tmp_path)


def test_core_dynamic_license_not_asserted(tmp_path):
    write_files(tmp_path, {"selected.py": "value=1\n"})
    selected = [tmp_path / "selected.py"]
    digest = metadata.selected_digest(tmp_path, selected)
    write_files(
        tmp_path,
        {
            "PKG-INFO": f"Metadata-Version: 2.4\nName: synthetic-dynamic-core\nVersion: 1\nLicense-Expression: MIT\nDynamic: License-Expression\nX-Mpy-Source-Root: .\nX-Mpy-Source-Path: .\nX-Mpy-Source-Sha256: {digest}\n"
        },
    )
    result = collect(tmp_path, selected)
    assert result["fields"]["name"] == "synthetic-dynamic-core"
    assert "license" not in result["fields"]
    assert "license_applicability" not in result["fields"]
    assert "unsupported" in statuses(result, "license")


@pytest.mark.parametrize("filename", ["bom.cdx.xml", "bom.spdx", "bom.spdx.yaml"])
def test_unsupported_bom_encodings_have_field_issues(tmp_path, filename):
    write_files(tmp_path, {filename: "Unsupported encoded BOM fixture\n"})
    result = collect(tmp_path)
    assert "unsupported" in statuses(result, "name")
    assert str(tmp_path / filename) in result["markers"]


@pytest.mark.parametrize(
    "filename,text",
    [
        ("CMakeLists.txt", "project(synthetic VERSION 1 VERSION 2)\n"),
        ("meson.build", "project('synthetic', version: '1', version: '2')\n"),
    ],
)
def test_native_duplicate_values_are_conflicting(tmp_path, filename, text):
    write_files(tmp_path, {filename: text})
    result = collect(tmp_path)
    assert "version" not in result["fields"]
    assert "conflicting" in statuses(result, "version")


def test_invalid_reuse_cannot_confirm_even_valid_file_header(tmp_path):
    write_files(
        tmp_path,
        {"REUSE.toml": "not valid toml", "selected.py": "# SPDX-License-Identifier: MIT\n"},
    )
    result = collect(tmp_path, [tmp_path / "selected.py"])
    assert "license_applicability" not in result["fields"]
    assert "unsupported" in statuses(result, "license_applicability")


def test_stale_bom_status_is_preserved_per_field(tmp_path):
    write_files(tmp_path, {"selected.py": "value=1\n"})
    selected = [tmp_path / "selected.py"]
    properties = binding(tmp_path, selected)
    properties["mpy:source:sha256"] = "0" * 64
    write_files(tmp_path, {"bom.json": json.dumps(cdx_document(properties))})
    result = collect(tmp_path, selected)
    assert "stale" in statuses(result, "name")
    assert any(row["field"] == "name" and row["status"] == "stale" for row in result["evidence"])


@pytest.mark.parametrize(
    "root_precedence,nested_precedence,always,fallback,header_allowed",
    [
        ("closest", "closest", (), "nested", True),
        ("closest", "aggregate", ("nested",), "root", True),
        ("closest", "override", ("nested",), None, False),
        ("aggregate", "closest", ("root",), "nested", True),
        ("aggregate", "aggregate", ("root", "nested"), None, True),
        ("aggregate", "override", ("root", "nested"), None, False),
        ("override", "closest", ("root",), None, False),
        ("override", "aggregate", ("root",), None, False),
        ("override", "override", ("root",), None, False),
    ],
)
@pytest.mark.parametrize("header_fields", ["neither", "license", "copyright", "both"])
def test_nested_reuse_precedence_per_field(
    tmp_path,
    root_precedence,
    nested_precedence,
    always,
    fallback,
    header_allowed,
    header_fields,
):
    header_license = header_fields in ("license", "both")
    header_copyright = header_fields in ("copyright", "both")
    header = ""
    if header_license:
        header += "# SPDX-License-Identifier: BSD-2-Clause\n"
    if header_copyright:
        header += "# SPDX-FileCopyrightText: Header Fixture Author\n"
    write_files(
        tmp_path,
        {
            "REUSE.toml": (
                'version=1\n[[annotations]]\npath="**"\n'
                f'precedence="{root_precedence}"\n'
                'SPDX-License-Identifier="MIT"\n'
                'SPDX-FileCopyrightText="Root Fixture Author"\n'
            ),
            "nested/REUSE.toml": (
                'version=1\n[[annotations]]\npath="selected.py"\n'
                f'precedence="{nested_precedence}"\n'
                'SPDX-License-Identifier="Apache-2.0"\n'
                'SPDX-FileCopyrightText="Nested Fixture Author"\n'
            ),
            "nested/selected.py": header + "value=1\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "nested/selected.py"])
    licenses = {"root": "MIT", "nested": "Apache-2.0", "header": "BSD-2-Clause"}
    notices = {
        "root": "Root Fixture Author",
        "nested": "Nested Fixture Author",
        "header": "Header Fixture Author",
    }
    license_scopes = set(always)
    copyright_scopes = set(always)
    if header_allowed and header_license:
        license_scopes.add("header")
    elif fallback:
        license_scopes.add(fallback)
    if header_allowed and header_copyright:
        copyright_scopes.add("header")
    elif fallback:
        copyright_scopes.add(fallback)
    expected_license = " AND ".join(licenses[scope] for scope in sorted(license_scopes))
    assert metadata.LICENSING.is_equivalent(result["fields"]["license"], expected_license)
    assert result["fields"]["license_applicability"] == "confirmed"
    for scope, notice in notices.items():
        assert (notice in result["fields"]["copyright"]) == (scope in copyright_scopes)
    paths = {
        "root": str(tmp_path / "REUSE.toml"),
        "nested": str(tmp_path / "nested/REUSE.toml"),
        "header": str(tmp_path / "nested/selected.py"),
    }
    license_rows = [
        row
        for row in result["evidence"]
        if row["field"] == "license" and row.get("applicability") == "selected-file"
    ]
    assert {row["path"] for row in license_rows} == {paths[scope] for scope in license_scopes}


@pytest.mark.parametrize("header_field", ["license", "copyright"])
def test_closest_complementary_field_keeps_its_actual_scope(tmp_path, header_field):
    if header_field == "license":
        root_field = 'SPDX-License-Identifier="MIT"\n'
        nested_field = 'SPDX-FileCopyrightText="Nested Fixture Author"\n'
        header = "# SPDX-License-Identifier: BSD-2-Clause\n"
        expected_license = "BSD-2-Clause"
        expected_copyright = "Nested Fixture Author"
        expected_license_path = tmp_path / "nested/selected.py"
        expected_copyright_path = tmp_path / "nested/REUSE.toml"
    else:
        root_field = 'SPDX-FileCopyrightText="Root Fixture Author"\n'
        nested_field = 'SPDX-License-Identifier="Apache-2.0"\n'
        header = "# SPDX-FileCopyrightText: Header Fixture Author\n"
        expected_license = "Apache-2.0"
        expected_copyright = "Header Fixture Author"
        expected_license_path = tmp_path / "nested/REUSE.toml"
        expected_copyright_path = tmp_path / "nested/selected.py"
    write_files(
        tmp_path,
        {
            "REUSE.toml": (
                'version=1\n[[annotations]]\npath="**"\nprecedence="closest"\n' + root_field
            ),
            "nested/REUSE.toml": (
                'version=1\n[[annotations]]\npath="selected.py"\nprecedence="closest"\n'
                + nested_field
            ),
            "nested/selected.py": header + "value=1\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "nested/selected.py"])
    assert result["fields"]["license"] == expected_license
    assert expected_copyright in result["fields"]["copyright"]
    license_rows = [
        row
        for row in result["evidence"]
        if row["field"] == "license" and row.get("applicability") == "selected-file"
    ]
    copyright_rows = [row for row in result["evidence"] if row["field"] == "copyright"]
    assert {row["path"] for row in license_rows} == {str(expected_license_path)}
    assert {row["path"] for row in copyright_rows} == {str(expected_copyright_path)}


@pytest.mark.parametrize("format_name", ["cyclonedx", "spdx", "core-metadata"])
@pytest.mark.parametrize("binding_kind", ["sha256", "revision"])
def test_boundary_local_upstream_binding_is_portable_with_source_context(
    tmp_path,
    format_name,
    binding_kind,
):
    directory = tmp_path / "packages/synthetic"
    write_files(directory, {"selected.py": "value=1\n"})
    selected = [directory / "selected.py"]
    properties = {"mpy:source:root": ".", "mpy:source:path": "."}
    properties["mpy:source:" + binding_kind] = (
        metadata.selected_digest(directory, selected) if binding_kind == "sha256" else "a" * 40
    )
    if format_name == "cyclonedx":
        write_files(directory, {"bom.cdx.json": json.dumps(cdx_document(properties))})
        expected_name = "synthetic-upstream"
    elif format_name == "spdx":
        write_files(directory, {"bom.spdx.json": json.dumps(spdx_document(properties))})
        expected_name = "synthetic-spdx"
    else:
        headers = "".join(
            f"X-Mpy-Source-{key.removeprefix('mpy:source:').title()}: {value}\n"
            for key, value in properties.items()
        )
        write_files(
            directory,
            {
                "PKG-INFO": (
                    "Metadata-Version: 2.4\nName: synthetic-core\nVersion: 1\n"
                    "License-Expression: MIT\n" + headers
                )
            },
        )
        expected_name = "synthetic-core"
    context = {"root": "consumer-external", "root_directory": tmp_path, "revision": "a" * 40}
    result = collect(directory, selected, source_context=context)
    assert result["fields"]["name"] == expected_name
    assert result["fields"]["license"] == "MIT"
    assert result["fields"]["license_applicability"] == "confirmed"
    assert not statuses(result, "applicability")
    if binding_kind == "revision":
        context["revision"] = None
    else:
        write_files(directory, {"selected.py": "value=2\n"})
    invalid = collect(directory, selected, source_context=context)
    assert "name" not in invalid["fields"]
    assert statuses(invalid, "applicability") & {"unbound", "stale"}


@pytest.mark.parametrize(
    "selector_root,selector_path,accepted",
    [
        ("consumer-external", "packages/synthetic", True),
        ("other-consumer", "packages/synthetic", False),
        ("consumer-external", ".", False),
        (".", "packages/synthetic", False),
    ],
)
def test_nonlocal_upstream_selectors_enforce_analysis_root(
    tmp_path,
    selector_root,
    selector_path,
    accepted,
):
    directory = tmp_path / "packages/synthetic"
    write_files(directory, {"selected.py": "value=1\n"})
    selected = [directory / "selected.py"]
    properties = {
        "mpy:source:root": selector_root,
        "mpy:source:path": selector_path,
        "mpy:source:sha256": metadata.selected_digest(directory, selected),
    }
    write_files(directory, {"bom.cdx.json": json.dumps(cdx_document(properties))})
    result = collect(
        directory,
        selected,
        source_context={"root": "consumer-external", "root_directory": tmp_path},
    )
    assert ("name" in result["fields"]) == accepted
    if not accepted:
        assert "unbound" in statuses(result, "applicability")


@pytest.mark.parametrize(
    "imports,shadow,call",
    [
        ("from setuptools import setup\n", "def setup(**kwargs):\n pass\n", "setup"),
        ("from setuptools import setup\n", "async def setup(**kwargs):\n pass\n", "setup"),
        ("from setuptools import setup\n", "class setup:\n pass\n", "setup"),
        ("from setuptools import setup\n", "from synthetic_other import setup\n", "setup"),
        ("from setuptools import setup\n", "import synthetic_other as setup\n", "setup"),
        ("from setuptools import setup\n", "from synthetic_other import *\n", "setup"),
        (
            "from setuptools import setup as configure\n",
            "def configure(**kwargs):\n pass\n",
            "configure",
        ),
        (
            "from setuptools import setup\n",
            "from contextlib import suppress as setup\n",
            "setup",
        ),
        (
            "import setuptools\n",
            "import synthetic_other as setuptools\n",
            "setuptools.setup",
        ),
        ("import setuptools\n", "class setuptools:\n pass\n", "setuptools.setup"),
        (
            "from setuptools import setup\n",
            "try:\n pass\nexcept Exception as setup:\n pass\n",
            "setup",
        ),
        (
            "from setuptools import setup\n",
            "match None:\n case setup:\n  pass\n",
            "setup",
        ),
        (
            "from setuptools import setup\n",
            "import setuptools as setup\n",
            "setup",
        ),
        ("from .setuptools import setup\n", "", "setup"),
    ],
)
def test_setup_callable_identity_cannot_be_shadowed(tmp_path, imports, shadow, call):
    write_files(
        tmp_path,
        {
            "setup.py": imports
            + shadow
            + (f"{call}(name='synthetic-forged', version='999', license='MIT')\n")
        },
    )
    result = collect(tmp_path)
    for field in ("name", "version", "license"):
        assert field not in result["fields"]
        assert "unsupported" in statuses(result, field)
    assert "license_applicability" not in result["fields"]


def test_unrelated_setup_bindings_do_not_shadow_the_supported_import(tmp_path):
    write_files(
        tmp_path,
        {
            "setup.py": (
                "from setuptools import setup as configure\n"
                "from contextlib import suppress\n"
                "def unrelated():\n pass\n"
                "class Unrelated:\n pass\n"
                "configure(name='synthetic-authentic', version='1', license='MIT')\n"
            )
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic-authentic"
    assert result["fields"]["license_applicability"] == "confirmed"


@pytest.mark.parametrize(
    "definition",
    [
        "function(project)\nendfunction()\n",
        "macro(project)\nendmacro()\n",
        "function(PROJECT)\nendfunction()\n",
        'macro("project")\nendmacro()\n',
        "function(${SYNTHETIC_COMMAND})\nendfunction()\n",
    ],
)
def test_cmake_project_command_identity_cannot_be_overridden(tmp_path, definition):
    write_files(
        tmp_path,
        {"CMakeLists.txt": definition + "project(synthetic_forged VERSION 999)\n"},
    )
    result = collect(tmp_path)
    for field in ("name", "version"):
        assert field not in result["fields"]
        assert "unsupported" in statuses(result, field)
    assert any("identity" in issue["reason"] for issue in result["issues"])


def test_unrelated_cmake_function_keeps_literal_builtin_project_supported(tmp_path):
    write_files(
        tmp_path,
        {
            "CMakeLists.txt": (
                "function(synthetic_helper)\nendfunction()\n"
                "project(synthetic_authentic VERSION 1.2)\n"
            )
        },
    )
    result = collect(tmp_path)
    assert result["fields"]["name"] == "synthetic_authentic"
    assert result["fields"]["version"] == "1.2"


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        "null",
        '{"bomFormat":"CycloneDX","specVersion":"1.6","components":[{"type":"library"}]}',
        '{"bomFormat":"CycloneDX","specVersion":16,"components":[]}',
        '{"bomFormat":"CycloneDX","components":[]}',
        '{"spdxVersion":23}',
        '{"bomFormat":"CycloneDX","specVersion":"1.6","components":[],"components":[]}',
    ],
)
def test_invalid_upstream_bom_cannot_be_masked_by_complete_static_metadata(tmp_path, content):
    write_files(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname="synthetic-complete"\nversion="1"\nlicense="MIT"\n'
            ),
            "bom.json": content,
        },
    )
    with pytest.raises(ValueError):
        collect(tmp_path)


@pytest.mark.parametrize(
    "document",
    [
        {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1, "components": []},
        {"spdxVersion": "SPDX-2.2"},
        {"bomFormat": "SyntheticUnsupportedFormat", "version": 1},
    ],
)
def test_unsupported_bom_formats_remain_explicit_partial_metadata(tmp_path, document):
    write_files(tmp_path, {"bom.json": json.dumps(document)})
    result = collect(tmp_path)
    assert not result["fields"]
    assert "unsupported" in statuses(result, "name")
    assert any(
        issue["field"] == "metadata" and "unsupported upstream BOM" in issue["reason"]
        for issue in result["issues"]
    )


def test_binding_files_are_distinct_from_owned_licensing_files(tmp_path):
    write_files(
        tmp_path,
        {
            "owned.py": "# SPDX-License-Identifier: MIT\n",
            "vendor/selected.py": "# SPDX-License-Identifier: Apache-2.0\n",
            "vendor/REUSE.toml": "invalid TOML must not be consulted for parent licensing",
        },
    )
    owned = tmp_path / "owned.py"
    nested = tmp_path / "vendor/selected.py"
    alias = tmp_path / "alias.py"
    alias.symlink_to(owned)
    descendants = [owned, nested, alias, nested]
    properties = binding(tmp_path, descendants)
    assert properties["mpy:source:sha256"] == metadata.selected_digest(tmp_path, [owned, nested])
    write_files(tmp_path, {"bom.json": json.dumps(cdx_document(properties))})
    inputs = set()
    result = collect(
        tmp_path, [owned, alias, owned], binding_files=descendants, input_paths=inputs
    )
    assert result["fields"]["name"] == "synthetic-upstream"
    assert result["fields"]["license"] == "MIT"
    assert result["fields"]["license_applicability"] == "confirmed"
    assert nested in inputs
    assert nested in {Path(path) for path in result["markers"]}
    assert tmp_path / "vendor/REUSE.toml" not in inputs
    assert {
        row["selected_path"]
        for row in result["evidence"]
        if row.get("applicability") == "selected-file"
    } == {str(owned)}
    assert any(
        row.get("detail") == "selected-file-annotations" and row["total"] == 1
        for row in result["evidence"]
    )
    default_binding = collect(tmp_path, [owned])
    assert "name" not in default_binding["fields"]
    assert "stale" in statuses(default_binding, "applicability")


def test_empty_binding_files_do_not_fall_back_to_owned_selection(tmp_path):
    write_files(tmp_path, {"selected.py": "# SPDX-License-Identifier: MIT\n"})
    selected = [tmp_path / "selected.py"]
    write_files(tmp_path, {"bom.json": json.dumps(cdx_document(binding(tmp_path, selected)))})
    result = collect(tmp_path, selected, binding_files=[])
    assert "name" not in result["fields"]
    assert "unbound" in statuses(result, "applicability")
    assert result["fields"]["license"] == "MIT"
    assert result["fields"]["license_applicability"] == "confirmed"


@pytest.mark.parametrize("selection", ["outside", "escape", "directory"])
def test_binding_files_require_contained_physical_files(tmp_path, selection):
    directory = write_files(
        tmp_path / "package", {"selected.py": "# SPDX-License-Identifier: MIT\n"}
    )
    write_files(tmp_path, {"outside.py": "value=1\n"})
    (directory / "escape.py").symlink_to(tmp_path / "outside.py")
    invalid = {
        "outside": tmp_path / "outside.py",
        "escape": directory / "escape.py",
        "directory": directory,
    }[selection]
    with pytest.raises(ValueError, match="binding file is not contained"):
        collect(directory, [directory / "selected.py"], binding_files=[invalid])


@pytest.mark.parametrize("expression_conflict", [False, True])
@pytest.mark.parametrize("text_source", ["source-text", "second-bom"])
def test_bound_bom_custom_text_conflicts_have_structural_derived_causes(
    tmp_path, expression_conflict, text_source
):
    write_files(
        tmp_path,
        {
            "selected.py": "# SPDX-License-Identifier: LicenseRef-Fixture\n",
        },
    )
    if text_source == "source-text":
        write_files(tmp_path, {"LICENSES/LicenseRef-Fixture.txt": "Local custom terms\n"})
    selected = [tmp_path / "selected.py"]
    document = spdx_document(binding(tmp_path, selected))
    document["packages"][0]["licenseDeclared"] = "LicenseRef-Fixture"
    document["packages"][0]["licenseConcluded"] = "NOASSERTION"
    document["hasExtractedLicensingInfos"] = [
        {"licenseId": "LicenseRef-Fixture", "extractedText": "Upstream custom terms"}
    ]
    write_files(tmp_path, {"bom.spdx.json": json.dumps(document)})
    if text_source == "second-bom":
        document["hasExtractedLicensingInfos"][0]["extractedText"] = "Other upstream terms"
        write_files(tmp_path, {"second.spdx.json": json.dumps(document)})
    result = collect(
        tmp_path,
        selected,
        manifest_metadata={"license": "MIT"} if expression_conflict else None,
    )
    assert "license" not in result["fields"]
    assert "license_applicability" not in result["fields"]
    conflicts = [
        issue
        for issue in result["issues"]
        if issue["field"] == "license" and issue["status"] == "conflicting"
    ]
    assert len(conflicts) == 2
    direct = next(issue for issue in conflicts if "values" not in issue)
    derived = next(issue for issue in conflicts if "values" in issue)
    assert direct["cause_fields"] == ["license_text:LicenseRef-Fixture"]
    causes = ["license_text:LicenseRef-Fixture"]
    if expression_conflict:
        causes.insert(0, "license")
        assert set(derived["values"]) == {"MIT", "LicenseRef-Fixture"}
    else:
        assert derived["values"] == ["LicenseRef-Fixture"]
    assert derived["cause_fields"] == causes
    applicability = [
        issue for issue in result["issues"] if issue["field"] == "license_applicability"
    ]
    assert len(applicability) == 1
    assert applicability[0]["cause_fields"] == causes


def test_missing_custom_text_has_structural_text_causes(tmp_path):
    result = collect(tmp_path, manifest_metadata={"license": "LicenseRef-Missing"})
    assert "license" not in result["fields"]
    assert all(
        issue["cause_fields"] == ["license_text:LicenseRef-Missing"]
        for issue in result["issues"]
        if issue["field"] in ("license", "license_applicability")
    )


LIBRARY = {
    "name": "synthetic-library",
    "version": "4.5.6",
    "description": "Synthetic manifest",
    "license": "MIT",
    "homepage": "https://example.invalid/library",
    "repository": {"type": "git", "url": "https://example.invalid/library.git"},
    "authors": [{"name": "Fixture Author", "email": "author@example.invalid", "maintainer": True}],
    "frameworks": "*",
}


def library_json(directory, **overrides):
    values = {**LIBRARY, **overrides}
    write_files(directory, {"library.json": json.dumps(values), "src/item.c": "int item;\n"})
    return [directory / "src/item.c"]


def test_library_json_literals_are_source_bound_and_scoped_to_the_package(tmp_path):
    files = library_json(tmp_path)
    result = collect(tmp_path, files)
    assert result["fields"] == {
        "name": "synthetic-library",
        "version": "4.5.6",
        "description": "Synthetic manifest",
        "license": "MIT",
        "url": "https://example.invalid/library",
        "author": "Fixture Author <author@example.invalid>",
        "license_applicability": "confirmed",
    }
    digest = hashlib.sha256((tmp_path / "library.json").read_bytes()).hexdigest()
    rows = [row for row in result["evidence"] if row["parser"] == "platformio-library-json-1"]
    assert {row["field"] for row in rows} >= {"name", "version", "license", "url", "repository"}
    assert all(row["path"] == str(tmp_path / "library.json") for row in rows)
    assert all(row["sha256"] == digest for row in rows)
    repository = next(row for row in rows if row["field"] == "repository")
    assert repository["status"] == "candidate"
    assert repository["value"] == "https://example.invalid/library.git"
    assert "repository" not in result["fields"]


def test_library_json_evidence_follows_the_file_bytes(tmp_path):
    files = library_json(tmp_path)
    before = collect(tmp_path, files)
    library_json(tmp_path, version="4.5.7")
    after = collect(tmp_path, files)
    assert before["fields"]["version"] == "4.5.6"
    assert after["fields"]["version"] == "4.5.7"
    digests = {
        row["sha256"]
        for result in (before, after)
        for row in result["evidence"]
        if row["field"] == "version"
    }
    assert len(digests) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", 7),
        ("name", ["synthetic"]),
        ("version", 4.5),
        ("version", None),
        ("version", "${SYNTHETIC_VERSION}"),
        ("version", "$(shell describe)"),
        ("version", "4.5 beta"),
        ("license", ["MIT"]),
        ("license", "Synthetic custom terms"),
        ("license", "${SYNTHETIC_LICENSE}"),
        ("homepage", "not a uri"),
        ("homepage", {"url": "https://example.invalid"}),
        ("authors", [{"email": "author@example.invalid"}]),
        ("authors", ["Fixture Author"]),
    ],
)
def test_library_json_malformed_or_computed_values_are_unsupported_evidence(
    tmp_path, field, value
):
    files = library_json(tmp_path, **{field: value})
    result = collect(tmp_path, files)
    target = {"homepage": "url", "authors": "author"}.get(field, field)
    assert target not in result["fields"]
    assert statuses(result, target) == {"unsupported"}
    rows = [row for row in result["evidence"] if row["field"] == target]
    assert rows and all(row["status"] == "unsupported" for row in rows)
    assert any(row.get("value") == value for row in rows)
    # A rejected field does not disturb independent literal fields.
    for other in ("name", "version"):
        if other != target:
            assert result["fields"][other] == LIBRARY[other]


def test_library_json_repeated_key_conflicts(tmp_path):
    write_files(
        tmp_path,
        {
            "library.json": '{"name": "first", "name": "second", "version": "1"}',
            "src/item.c": "int item;\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "src/item.c"])
    assert "name" not in result["fields"]
    assert "conflicting" in statuses(result, "name")
    assert result["fields"]["version"] == "1"


@pytest.mark.parametrize("content", ["{", '["synthetic"]', '"synthetic"', ""])
def test_library_json_that_is_not_an_object_asserts_nothing(tmp_path, content):
    write_files(tmp_path, {"library.json": content, "src/item.c": "int item;\n"})
    result = collect(tmp_path, [tmp_path / "src/item.c"])
    for field in ("name", "version", "license"):
        assert field not in result["fields"]
        assert "unsupported" in statuses(result, field)


def test_library_json_build_section_is_not_evaluated(tmp_path):
    sentinel = tmp_path / "executed"
    write_files(
        tmp_path,
        {
            "extra.py": f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n",
            "library.json": json.dumps(
                {**LIBRARY, "build": {"extraScript": "extra.py", "flags": "-DV=${X}"}}
            ),
            "src/item.c": "int item;\n",
        },
    )
    result = collect(tmp_path, [tmp_path / "src/item.c"])
    assert result["fields"]["name"] == "synthetic-library"
    assert not sentinel.exists()
    assert str(tmp_path / "extra.py") not in result["markers"]


def test_library_json_licence_agrees_with_matching_selected_headers(tmp_path):
    files = library_json(tmp_path)
    files[0].write_text("// SPDX-License-Identifier: MIT\nint item;\n")
    result = collect(tmp_path, files)
    assert result["fields"]["license"] == "MIT"
    assert result["fields"]["license_applicability"] == "confirmed"


def test_library_json_licence_conflicts_with_mixed_nested_licensing(tmp_path):
    files = library_json(tmp_path)
    write_files(
        tmp_path,
        {"vendor/inner.c": "// SPDX-License-Identifier: GPL-2.0-only\nint inner;\n"},
    )
    result = collect(tmp_path, [*files, tmp_path / "vendor/inner.c"])
    assert "license" not in result["fields"]
    assert "license_applicability" not in result["fields"]
    assert "conflicting" in statuses(result, "license")
    conflict = next(
        issue
        for issue in result["issues"]
        if issue["field"] == "license" and issue["status"] == "conflicting"
    )
    assert set(conflict["values"]) == {"MIT", "GPL-2.0-only"}
