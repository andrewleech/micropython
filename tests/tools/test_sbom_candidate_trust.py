#!/usr/bin/env python3
"""Tests for independent licence evidence and declaration trust."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from sbom import _build_document
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestCandidateTrust(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.put(".gitmodules", "")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def put(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def declarations(self, packages, texts=()):
        self.put("LICENSE.spdx.yaml", yaml.safe_dump({
            "packages": packages, "hasExtractedLicensingInfos": list(texts),
        }))

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def document(self, sources):
        entries = []
        for index, source in enumerate(sources):
            obj = f"build/source-{index}.o"
            dep = f"build/source-{index}.P"
            self.put(dep, f"{obj}: {source}\n")
            entries.append({"directory": str(self.repo), "file": str(source),
                            "arguments": ["cc", "-c", str(source), "-o", obj, "-MF", dep]})
        database = self.put("build/compile_commands.json", json.dumps(entries))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[], root=[],
                               image="test-image", offline=True)
        return _build_document(self.repo, args)

    def serialized_package(self, document):
        output = self.repo / "out.spdx.json"
        write_sbom(document, output, "spdx")
        packages = json.loads(output.read_text(encoding="utf-8"))["packages"]
        name = document["components"][0]["name"]
        return next(package for package in packages if package["name"] == name)

    def test_dirty_declaration_does_not_suppress_source_spdx(self):
        source = self.put("src/main.c", "// SPDX-License-Identifier: MIT\nint main(void);\n")
        self.declarations([{"name": "root", "packageFileName": ".", "licenseDeclared": "MIT"}])
        self.commit()
        self.declarations([{"name": "root", "packageFileName": ".", "licenseDeclared": "Apache-2.0"}])

        document = self.document([source])
        component = document["components"][0]
        self.assertTrue(any("dirty" in issue and "LICENSE.spdx.yaml" in issue for issue in document["issues"]))
        self.assertEqual(component["file_licences"][0]["expression"], "MIT")
        self.assertEqual(component["file_licences"][0]["provenance"], "source SPDX header")
        self.assertTrue(component["file_licences"][0]["trusted"])
        self.assertEqual(component["licence_declared"], "MIT")
        self.assertEqual(self.serialized_package(document)["licenseDeclared"], "MIT")

    def pinned_component(self, sources):
        self.declarations([
            {"name": "root", "packageFileName": ".", "licenseDeclared": "MIT"},
            {"name": "vendor", "packageFileName": "vendor", "licenseDeclared": "Apache-2.0",
             "externalRefs": [{"referenceCategory": "PERSISTENT-ID", "referenceType": "gitoid",
                               "referenceLocator": "gitoid:tree:sha1:" + "0" * 40}]},
        ])
        self.commit()
        return {"name": "vendor", "path": "vendor", "owner_root": str(self.repo),
                "sources": [str(source) for source in sources], "version": None}

    def test_invalid_pin_preserves_independent_source_evidence(self):
        source = self.put("vendor/with_header.c", "// SPDX-License-Identifier: MIT\nint x;\n")
        component = self.pinned_component([source])

        issues = resolve_licences(self.repo, [component])
        self.assertTrue(any("licence pin" in issue for issue in issues), issues)
        self.assertEqual(component["file_licences"][0]["expression"], "MIT")
        self.assertTrue(component["file_licences"][0]["trusted"])
        self.assertEqual(component["licence_declared"], "MIT")

    def test_mixed_source_and_pin_dependent_file_has_no_component_declaration(self):
        source = self.put("vendor/with_header.c", "// SPDX-License-Identifier: MIT\nint x;\n")
        unresolved = self.put("vendor/without_header.c", "int y;\n")
        component = self.pinned_component([source, unresolved])

        issues = resolve_licences(self.repo, [component])
        files = {item["source"]: item for item in component["file_licences"]}
        self.assertEqual(files[str(source)]["expression"], "MIT")
        self.assertTrue(files[str(source)]["trusted"])
        self.assertIsNone(files[str(unresolved)]["expression"])
        self.assertFalse(files[str(unresolved)]["trusted"])
        self.assertTrue(any("licence pin" in issue for issue in issues), issues)
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        document = {"name": "test-image", "issues": issues, "components": [component]}
        self.assertEqual(self.serialized_package(document)["licenseDeclared"], "NOASSERTION")

    def test_package_metadata_survives_dirty_declaration(self):
        source = self.put("src/main.c", "int main(void);\n")
        self.put("library.json", json.dumps({"license": "BSD-3-Clause"}))
        self.declarations([{"name": "root", "packageFileName": ".", "licenseDeclared": "MIT"}])
        self.commit()
        self.declarations([{"name": "root", "packageFileName": ".", "licenseDeclared": "Apache-2.0"}])

        document = self.document([source])
        component = document["components"][0]
        self.assertTrue(any("dirty" in issue for issue in document["issues"]))
        self.assertEqual(component["file_licences"][0]["expression"], "BSD-3-Clause")
        self.assertTrue(component["file_licences"][0]["trusted"])
        self.assertEqual(component["licence_declared"], "BSD-3-Clause")
        self.assertEqual(self.serialized_package(document)["licenseDeclared"], "BSD-3-Clause")

    def test_source_licenseref_requires_trusted_extracted_text(self):
        source = self.put("src/main.c", "// SPDX-License-Identifier: LicenseRef-Custom\nint main(void);\n")
        packages = [{"name": "root", "packageFileName": ".", "licenseDeclared": "MIT"}]
        texts = [{"licenseId": "LicenseRef-Custom", "extractedText": "Custom licence terms."}]
        self.declarations(packages, texts)
        self.commit()

        clean = self.document([source])["components"][0]
        self.assertEqual(clean["licence_declared"], "LicenseRef-Custom")
        self.assertEqual(clean["licence_texts"]["LicenseRef-Custom"], "Custom licence terms.")
        self.declarations(packages, [{"licenseId": "LicenseRef-Custom", "extractedText": "Changed terms."}])
        dirty_document = self.document([source])
        dirty = dirty_document["components"][0]
        self.assertTrue(any("dirty" in issue for issue in dirty_document["issues"]))
        self.assertIsNone(dirty["file_licences"][0]["expression"])
        self.assertFalse(dirty["file_licences"][0]["trusted"])
        self.assertIsNone(dirty["licence_declared"])
        self.assertNotIn("LicenseRef-Custom", dirty["licence_texts"])
        self.assertEqual(self.serialized_package(dirty_document)["licenseDeclared"], "NOASSERTION")


if __name__ == "__main__":
    unittest.main()
