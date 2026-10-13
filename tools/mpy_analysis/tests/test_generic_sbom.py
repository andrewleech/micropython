import json
from copy import deepcopy

import pytest
from conftest import generate_cyclonedx
from mpy_analysis import sbom
from mpy_analysis.declarations import validate_document
from mpy_analysis.scope import FROZEN_GROUP
from test_sbom import MPY_ROOT, git, repository, selection


def direct_dependency(tmp_path, files):
    inventory, app, library = selection(tmp_path)
    external = repository(tmp_path / "separate", files)
    inventory["roots"]["separate"] = str(external)
    inventory["groups"][FROZEN_GROUP].append(
        {
            "root": "separate",
            "path": "source.py",
            "target_path": "separate/source.py",
            "owner": "external",
            "provenance": {"kind": "fixture"},
        }
    )
    return inventory, external


def test_direct_frozen_dependency_uses_generic_declared_distribution_metadata(tmp_path):
    inventory, external = direct_dependency(
        tmp_path,
        {
            "source.py": "value = 1\n",
            "metadata.txt": "version = 2.2\nauthor = Fixture Author\ndepends = copy, types\n",
            "setup.py": "from setuptools import setup\nsetup(name='distribution-fixture', version='2.2', license='MIT', url='https://example.invalid/project')\n",
        },
    )
    document, receipt = generate_cyclonedx(inventory, micropython_root=MPY_ROOT)
    component = next(
        item for item in document["components"] if item["name"] == "distribution-fixture"
    )
    assert component["version"] == "2.2"
    assert component["licenses"] == [{"expression": "MIT"}]
    metadata = next(
        item for item in receipt["metadata"] if item["component"] == component["bom-ref"]
    )
    assert metadata["fields"]["license"]["status"] == "known"
    assert metadata["declared_dependencies"] == ["copy", "types"]
    assert any(item.get("path", "").endswith("setup.py") for item in metadata["evidence"])
    assert (
        next(row for row in receipt["sources"] if row["root"] == "separate")[
            "dependency_component"
        ]
        == component["bom-ref"]
    )
    validate_document(document)


def test_complete_source_accounting_does_not_hide_missing_dependency_metadata(tmp_path):
    inventory, external = direct_dependency(tmp_path, {"source.py": "value = 1\n"})
    document, receipt = generate_cyclonedx(inventory, micropython_root=MPY_ROOT)
    assert receipt["source_accounting"] == "accounted"
    assert receipt["metadata_completeness"] == "incomplete"
    assert receipt["coverage_status"] == "incomplete"
    row = next(row for row in receipt["sources"] if row["root"] == "separate")
    matrix = next(
        item for item in receipt["metadata"] if item["component"] == row["dependency_component"]
    )
    for field in ("name", "version", "license"):
        assert matrix["fields"][field]["status"] != "known"
        assert any(
            gap.get("component") == row["dependency_component"] and gap.get("field") == field
            for gap in receipt["gaps"]
        )
    component = next(
        item for item in document["components"] if item["bom-ref"] == row["dependency_component"]
    )
    assert "version" not in component
    assert "licenses" not in component


def test_pinned_application_declaration_fills_missing_package_license(tmp_path):
    inventory, app, library = selection(tmp_path)
    path = tmp_path / "dependencies.cdx.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [
                    {
                        "type": "library",
                        "name": "alpha",
                        "version": "2",
                        "licenses": [{"expression": "MIT"}],
                        "properties": [
                            {"name": "mpy:source:root", "value": "library"},
                            {"name": "mpy:source:path", "value": "alpha"},
                            {
                                "name": "mpy:source:revision",
                                "value": git(library, "rev-parse", "HEAD"),
                            },
                        ],
                    }
                ],
            }
        )
    )
    document, receipt = generate_cyclonedx(
        inventory, micropython_root=MPY_ROOT, declaration_files=[path]
    )
    alpha = next(item for item in document["components"] if item["name"] == "alpha")
    assert alpha["licenses"] == [{"expression": "MIT"}]
    matrix = next(item for item in receipt["metadata"] if item["component"] == alpha["bom-ref"])
    assert matrix["fields"]["license"]["status"] == "known"
    assert any(item.get("origin") == "manual" for item in matrix["fields"]["license"]["evidence"])
    (library / "alpha/alpha.py").write_text("value = 99\n")
    document, receipt = generate_cyclonedx(
        inventory, micropython_root=MPY_ROOT, declaration_files=[path]
    )
    alpha = next(item for item in document["components"] if item["name"] == "alpha")
    assert "licenses" not in alpha
    assert any(
        item.get("kind") == "declaration-applicability" and item["status"] == "stale"
        for item in receipt["gaps"]
    )


def test_cli_preserves_declared_metadata_input_collision(tmp_path):
    inventory, app, library = selection(tmp_path)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(inventory))
    output = tmp_path / "output"
    output.mkdir()
    declaration = output / "sbom.json"
    content = json.dumps(
        {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1, "components": []}
    )
    declaration.write_text(content)
    result = sbom.main(
        [
            "--scope",
            str(scope_path),
            "--micropython-root",
            str(MPY_ROOT),
            "--output-dir",
            str(output),
            "--dependency-metadata",
            str(declaration),
        ]
    )
    assert result == 2
    assert declaration.read_text() == content


def test_direct_and_transitive_requirements_both_retain_build_edges(tmp_path):
    inventory, app, library = selection(tmp_path)
    beta = next(item for item in inventory["packages"] if item["name"] == "beta")
    inventory["package_requirements"].append({"parent": None, "package": beta["id"]})
    document, receipt = generate_cyclonedx(inventory, micropython_root=MPY_ROOT)
    components = {item["name"]: item for item in document["components"]}
    edges = {item["ref"]: item["dependsOn"] for item in document["dependencies"]}
    beta_ref = components["beta"]["bom-ref"]
    assert beta_ref in edges[document["metadata"]["component"]["bom-ref"]]
    assert beta_ref in edges[components["alpha"]["bom-ref"]]


def test_distribution_name_is_not_replaced_by_logical_require_name(tmp_path):
    inventory, app, library = selection(tmp_path)
    (library / "alpha/setup.py").write_text(
        "from setuptools import setup\n"
        "setup(name='distribution-alpha', version='2', license='MIT')\n"
    )
    document, receipt = generate_cyclonedx(inventory, micropython_root=MPY_ROOT)
    component = next(
        item for item in document["components"] if item["name"] == "distribution-alpha"
    )
    assert component["version"] == "2"
    matrix = next(
        item for item in receipt["metadata"] if item["component"] == component["bom-ref"]
    )
    assert matrix["fields"]["name"]["status"] == "known"
    assert not matrix["fields"]["name"]["issues"]


def test_physical_package_aliases_do_not_choose_first_conflicting_identity(tmp_path):
    inventory, app, library = selection(tmp_path)
    original = next(item for item in inventory["packages"] if item["name"] == "alpha")
    alias = deepcopy(original)
    alias["id"] = "alias/alpha/manifest.py"
    alias["root"] = "alias"
    alias["name"] = "other-alpha"
    alias["metadata"]["version"] = "9"
    inventory["roots"]["alias"] = str(library)
    inventory["packages"].append(alias)
    inventory["package_requirements"].append({"parent": None, "package": alias["id"]})
    source = deepcopy(
        next(
            row
            for row in inventory["groups"][FROZEN_GROUP]
            if row["provenance"].get("package_id") == original["id"]
        )
    )
    source["root"] = "alias"
    source["provenance"]["package_id"] = alias["id"]
    inventory["groups"][FROZEN_GROUP].append(source)
    document, receipt = generate_cyclonedx(inventory, micropython_root=MPY_ROOT)
    rows = [
        row
        for row in receipt["sources"]
        if row["root"] in {"library", "alias"} and row["path"] == source["path"]
    ]
    assert len(rows) == 2
    assert rows[0]["dependency_component"] == rows[1]["dependency_component"]
    matrix = next(
        item
        for item in receipt["metadata"]
        if item["component"] == rows[0]["dependency_component"]
    )
    assert matrix["fields"]["name"]["status"] == "conflicting"
    assert matrix["fields"]["version"]["status"] == "conflicting"
    assert {item["value"] for item in matrix["fields"]["version"]["evidence"]} == {"2", "9"}
    component = next(
        item for item in document["components"] if item["bom-ref"] == matrix["component"]
    )
    assert component["name"].startswith("unresolved:")
    assert "version" not in component
    assert receipt["metadata_completeness"] == "incomplete"


def test_invalid_optional_website_is_retained_but_not_emitted_as_url(tmp_path):
    inventory, external = direct_dependency(
        tmp_path,
        {
            "source.py": "value = 1\n",
            "pyproject.toml": (
                "[project]\nname='fixture'\nversion='1'\nlicense='MIT'\n"
                "[project.urls]\nHomepage='https://bad host.invalid/'\n"
            ),
        },
    )
    document, receipt = generate_cyclonedx(inventory, micropython_root=MPY_ROOT)
    component = next(item for item in document["components"] if item["name"] == "fixture")
    assert all(item["type"] != "website" for item in component.get("externalReferences", []))
    matrix = next(
        item for item in receipt["metadata"] if item["component"] == component["bom-ref"]
    )
    assert matrix["fields"]["url"]["status"] == "unsupported"
    assert any(
        item["value"] == "https://bad host.invalid/"
        for item in matrix["fields"]["url"]["evidence"]
    )


def test_invalid_nested_bom_cannot_be_hidden_by_complete_parent_metadata(tmp_path):
    inventory, external = direct_dependency(
        tmp_path,
        {
            "source.py": "not_selected = True\n",
            "vendor/source.py": "value = 1\n",
            "pyproject.toml": "[project]\nname='fixture'\nversion='1'\nlicense='MIT'\n",
            "vendor/bom.json": json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "version": 1,
                    "components": [{"type": "library"}],
                }
            ),
        },
    )
    inventory["groups"][FROZEN_GROUP][-1]["path"] = "vendor/source.py"
    with pytest.raises(ValueError, match="invalid selected upstream BOM"):
        sbom.generate_sbom(inventory, micropython_root=MPY_ROOT)


def test_bound_parent_bom_and_nested_package_share_digest_not_licence_scope(tmp_path):
    from mpy_analysis.declarations import selected_digest

    inventory, external = direct_dependency(
        tmp_path,
        {
            "source.py": "# SPDX-License-Identifier: MIT\nvalue = 1\n",
            "vendor/source.py": "# SPDX-License-Identifier: Apache-2.0\nvalue = 2\n",
            "vendor/metadata.txt": "name=child-fixture\nversion=2\n",
        },
    )
    nested = deepcopy(inventory["groups"][FROZEN_GROUP][-1])
    nested["path"] = "vendor/source.py"
    nested["target_path"] = "separate/vendor/source.py"
    inventory["groups"][FROZEN_GROUP].append(nested)
    digest, _ = selected_digest(inventory, external)
    (external / "bom.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [
                    {
                        "type": "library",
                        "name": "parent-fixture",
                        "version": "1",
                        "licenses": [{"expression": "MIT"}],
                        "properties": [
                            {"name": "mpy:source:root", "value": "."},
                            {"name": "mpy:source:path", "value": "."},
                            {"name": "mpy:source:sha256", "value": digest},
                        ],
                    }
                ],
            }
        )
    )
    document, receipt = generate_cyclonedx(inventory, micropython_root=MPY_ROOT)
    components = {item["name"]: item for item in document["components"]}
    parent = components["parent-fixture"]
    child = components["child-fixture"]
    assert parent["version"] == "1"
    assert parent["licenses"] == [{"expression": "MIT"}]
    assert child["licenses"] == [{"expression": "Apache-2.0"}]
    rows = {row["path"]: row for row in receipt["sources"] if row["root"] == "separate"}
    assert rows["source.py"]["dependency_component"] == parent["bom-ref"]
    assert rows["vendor/source.py"]["dependency_component"] == child["bom-ref"]
    matrix = next(item for item in receipt["metadata"] if item["component"] == parent["bom-ref"])
    assert all(
        matrix["fields"][field]["status"] == "known"
        for field in ("name", "version", "license", "license_applicability")
    )
