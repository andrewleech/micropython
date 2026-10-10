import json

from mpy_analysis import sbom
from mpy_analysis.declarations import selected_digest
from test_generic_sbom import direct_dependency
from test_sbom import MPY_ROOT


def _declaration(tmp_path, inventory, external, *, name="pkg", version="1", licenses=None):
    digest, count = selected_digest(inventory, external)
    assert count
    component = {
        "type": "library",
        "name": name,
        "version": version,
        "licenses": licenses or [{"license": {"id": "MIT"}}],
        "properties": [
            {"name": "mpy:source:root", "value": "separate"},
            {"name": "mpy:source:path", "value": "."},
            {"name": "mpy:source:sha256", "value": digest},
        ],
    }
    path = tmp_path / "dependency-bom.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [component],
            }
        )
    )
    return path


def test_manual_license_text_conflict_remains_unresolved(tmp_path):
    inventory, external = direct_dependency(
        tmp_path,
        {
            "source.py": (
                "# SPDX-License-Identifier: LicenseRef-Local\n"
                "# SPDX-FileCopyrightText: 2026 Copyright A\nvalue = 1\n"
            ),
            "pyproject.toml": (
                "[project]\nname = 'pkg'\nversion = '1'\nlicense = { text = 'LicenseRef-Local' }\n"
            ),
            "LICENSES/LicenseRef-Local.txt": "Original terms.\n",
        },
    )
    declaration = _declaration(
        tmp_path,
        inventory,
        external,
        licenses=[
            {
                "license": {
                    "name": "LicenseRef-Local",
                    "text": {"content": "Different terms."},
                }
            }
        ],
    )
    document, receipt = sbom.generate_sbom(
        inventory,
        micropython_root=MPY_ROOT,
        declaration_files=[declaration],
    )
    dependency_ref = next(
        row["dependency_component"] for row in receipt["sources"] if row["root"] == "separate"
    )
    metadata = next(row for row in receipt["metadata"] if row["component"] == dependency_ref)
    dependency = next(item for item in document["components"] if item["bom-ref"] == dependency_ref)
    assert metadata["fields"]["license"]["status"] == "conflicting"
    assert "Different terms" not in json.dumps(dependency.get("licenses", []))


def test_manual_copyright_does_not_overwrite_selected_source_assertion(tmp_path):
    inventory, external = direct_dependency(
        tmp_path,
        {
            "source.py": (
                "# SPDX-License-Identifier: MIT\n"
                "# SPDX-FileCopyrightText: 2026 Copyright A\nvalue = 1\n"
            ),
            "pyproject.toml": (
                "[project]\nname = 'pkg'\nversion = '1'\nlicense = { text = 'MIT' }\n"
            ),
        },
    )
    declaration = _declaration(tmp_path, inventory, external)
    data = json.loads(declaration.read_text())
    data["components"][0]["copyright"] = "2026 Copyright B"
    declaration.write_text(json.dumps(data))
    document, receipt = sbom.generate_sbom(
        inventory,
        micropython_root=MPY_ROOT,
        declaration_files=[declaration],
    )
    dependency_ref = next(
        row["dependency_component"] for row in receipt["sources"] if row["root"] == "separate"
    )
    metadata = next(row for row in receipt["metadata"] if row["component"] == dependency_ref)
    dependency = next(item for item in document["components"] if item["bom-ref"] == dependency_ref)
    assert metadata["fields"]["copyright"]["status"] == "conflicting"
    assert dependency.get("copyright") != "2026 Copyright B"
