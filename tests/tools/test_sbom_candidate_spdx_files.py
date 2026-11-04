#!/usr/bin/env python3
#
# SPDX-License-Identifier: MIT

import json
import sys
import tempfile
import unittest
from pathlib import Path

from spdx_tools.common.spdx_licensing import spdx_licensing
from spdx_tools.spdx.parser.jsonlikedict.json_like_dict_parser import JsonLikeDictParser
from spdx_tools.spdx.validation.document_validator import validate_full_spdx_document


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from sbom_writers import write_sbom


class TestSpdxFileLicences(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "checkout"
        self.root.mkdir()

    def source(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def document(self, files):
        component = {
            "name": "mixed-dependency",
            "version": None,
            "licence": None,
            "licence_declared": None,
            "licence_status": "mixed",
            "licence_texts": {"LicenseRef-custom": "Custom permission text.\n"},
            "licence_comments": [],
            "file_licences": files,
        }
        model = {
            "name": "test-image",
            "subject": {"image": "firmware.elf"},
            "issues": [],
            "components": [component],
        }
        output = self.root / "sbom.json"
        write_sbom(model, output, "spdx", root=self.root)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_mixed_trusted_file_licences_have_standard_attribution(self):
        first = self.source("vendor/first.c", "first source\n")
        second = self.source("vendor/second.c", "// SPDX-License-Identifier: LicenseRef-custom\n")
        document = self.document([
            {"source": str(first), "expression": "MIT OR Apache-2.0",
             "provenance": "reviewed declaration", "trusted": True},
            {"source": str(second), "expression": "LicenseRef-custom",
             "provenance": "source SPDX header", "trusted": True},
        ])

        package = next(item for item in document["packages"] if item["name"] == "mixed-dependency")
        self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        self.assertEqual(package["licenseConcluded"], "NOASSERTION")
        self.assertNotIn("licenseInfoFromFiles", package)
        self.assertNotIn("MIT AND LicenseRef-custom", json.dumps(document))
        self.assertEqual(
            {item["licenseId"]: item["extractedText"] for item in document["hasExtractedLicensingInfos"]}
            ["LicenseRef-custom"],
            "Custom permission text.\n",
        )

        records = {item["fileName"].removeprefix("./"): item for item in document.get("files", [])}
        expected = {"vendor/first.c": "MIT OR Apache-2.0", "vendor/second.c": "LicenseRef-custom"}
        self.assertEqual(set(records), set(expected))
        evidence = {
            rel["spdxElementId"] for rel in document["relationships"]
            if rel["relatedSpdxElement"] == package["SPDXID"] and rel["relationshipType"] == "OTHER"
        }
        for name, expression in expected.items():
            with self.subTest(name=name):
                record = records[name]
                self.assertIn(record["SPDXID"], evidence)
                actual = record["licenseConcluded"] if name == "vendor/first.c" else record["licenseInfoInFiles"][0]
                self.assertEqual(spdx_licensing.parse(actual), spdx_licensing.parse(expression))
                self.assertTrue(record["checksums"])
        self.assertNotIn(str(self.root), json.dumps(document))
        self.assertEqual(validate_full_spdx_document(JsonLikeDictParser().parse(document)), [])

    def test_untrusted_file_does_not_assert_its_expression(self):
        trusted = self.source("vendor/trusted.c", "// SPDX-License-Identifier: MIT\n")
        untrusted = self.source("vendor/untrusted.c", "untrusted\n")
        document = self.document([
            {"source": str(trusted), "expression": "MIT", "provenance": "source SPDX header", "trusted": True},
            {"source": str(untrusted), "expression": "GPL-3.0-only", "provenance": "unverified", "trusted": False},
        ])
        package = next(item for item in document["packages"] if item["name"] == "mixed-dependency")
        self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        self.assertNotIn("GPL-3.0-only", package.get("licenseInfoFromFiles", []))
        records = {item["fileName"].removeprefix("./"): item for item in document.get("files", [])}
        self.assertEqual(records["vendor/trusted.c"]["licenseInfoInFiles"], ["MIT"])
        if "vendor/untrusted.c" in records:
            record = records["vendor/untrusted.c"]
            self.assertNotIn("GPL-3.0-only", record.get("licenseInfoInFiles", []))
            self.assertIn(record.get("licenseConcluded", "NOASSERTION"), ("NOASSERTION", "NONE"))
        self.assertEqual(validate_full_spdx_document(JsonLikeDictParser().parse(document)), [])

    def test_file_attribution_does_not_assert_runtime_containment(self):
        source = self.source("vendor/module.c", "// SPDX-License-Identifier: MIT\n")
        document = self.document([
            {"source": str(source), "expression": "MIT", "provenance": "source SPDX header", "trusted": True},
        ])
        package = next(item for item in document["packages"] if item["name"] == "mixed-dependency")
        record = next(item for item in document["files"] if item["fileName"].endswith("vendor/module.c"))
        relationships = {
            (rel["spdxElementId"], rel["relationshipType"], rel["relatedSpdxElement"])
            for rel in document["relationships"]
        }
        self.assertIn((package["SPDXID"], "BUILD_DEPENDENCY_OF", "SPDXRef-MicroPython"), relationships)
        self.assertIn((record["SPDXID"], "OTHER", package["SPDXID"]), relationships)
        self.assertNotIn(("SPDXRef-MicroPython", "CONTAINS", package["SPDXID"]), relationships)
        self.assertNotIn(("SPDXRef-MicroPython", "CONTAINS", record["SPDXID"]), relationships)

    def nested_document(self, source, parent_expression, child_expression, child_trusted=True):
        def component(name, expression, children=(), trusted=True):
            return {
                "name": name,
                "version": None,
                "licence": None,
                "licence_texts": {},
                "licence_comments": [],
                "file_licences": [{
                    "source": str(source), "expression": expression,
                    "provenance": "reviewed declaration", "trusted": trusted,
                }],
                "components": list(children),
            }

        child = component("child", child_expression, trusted=child_trusted)
        model = {
            "name": "test-image",
            "subject": {"image": "firmware.elf"},
            "issues": [],
            "components": [component("parent", parent_expression, [child])],
        }
        output = self.root / "nested.json"
        write_sbom(model, output, "spdx", root=self.root)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_shared_file_is_attributed_once_to_deepest_component(self):
        source = self.source("vendor/child/shared.c", "shared source\n")
        document = self.nested_document(source, "MIT", "MIT")
        self.assertEqual(len(document["files"]), 1)
        record = document["files"][0]
        packages = {package["name"]: package["SPDXID"] for package in document["packages"]}
        evidence = [
            rel["relatedSpdxElement"] for rel in document["relationships"]
            if rel["spdxElementId"] == record["SPDXID"] and rel["relationshipType"] == "OTHER"
        ]
        self.assertEqual(evidence, [packages["child"]])
        self.assertEqual(record["licenseConcluded"], "MIT")
        self.assertEqual(validate_full_spdx_document(JsonLikeDictParser().parse(document)), [])

    def test_conflicting_trusted_file_conclusions_are_not_asserted(self):
        source = self.source("vendor/child/shared.c", "shared source\n")
        document = self.nested_document(source, "MIT", "Apache-2.0")
        self.assertEqual(len(document["files"]), 1)
        record = document["files"][0]
        self.assertEqual(record["licenseConcluded"], "NOASSERTION")
        self.assertEqual(record["licenseInfoInFiles"], ["NOASSERTION"])
        self.assertEqual(validate_full_spdx_document(JsonLikeDictParser().parse(document)), [])

    def test_untrusted_child_does_not_inherit_parent_file_conclusion(self):
        source = self.source("vendor/child/shared.c", "shared source\n")
        document = self.nested_document(source, "MIT", None, child_trusted=False)
        self.assertEqual(len(document["files"]), 1)
        record = document["files"][0]
        child = next(package for package in document["packages"] if package["name"] == "child")
        evidence = [
            rel["relatedSpdxElement"] for rel in document["relationships"]
            if rel["spdxElementId"] == record["SPDXID"] and rel["relationshipType"] == "OTHER"
        ]
        self.assertEqual(evidence, [child["SPDXID"]])
        self.assertEqual(record["licenseConcluded"], "NOASSERTION")
        self.assertEqual(record["licenseInfoInFiles"], ["NOASSERTION"])
        self.assertEqual(validate_full_spdx_document(JsonLikeDictParser().parse(document)), [])

    def test_relative_file_without_explicit_root_is_rejected(self):
        self.source("vendor/relative.c", "fixture source\n")
        model = {
            "name": "test-image",
            "subject": {"image": "firmware.elf"},
            "issues": [],
            "components": [{
                "name": "dependency", "version": None, "licence": None,
                "licence_texts": {}, "licence_comments": [],
                "file_licences": [{
                    "source": "vendor/relative.c", "expression": "MIT",
                    "provenance": "reviewed declaration", "trusted": True,
                }],
            }],
        }
        output = self.root / "relative.json"
        with self.assertRaises(ValueError):
            write_sbom(model, output, "spdx")
        write_sbom(model, output, "spdx", root=self.root)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(document["files"]), 1)
        self.assertEqual(document["files"][0]["licenseConcluded"], "MIT")

    def test_absolute_file_does_not_require_explicit_root(self):
        source = self.source("vendor/absolute.c", "fixture source\n")
        model = {
            "name": "test-image",
            "subject": {"image": "firmware.elf"},
            "issues": [],
            "components": [{
                "name": "dependency", "version": None, "licence": None,
                "licence_texts": {}, "licence_comments": [],
                "file_licences": [{
                    "source": str(source), "expression": "MIT",
                    "provenance": "reviewed declaration", "trusted": True,
                }],
            }],
        }
        output = self.root / "absolute.json"
        write_sbom(model, output, "spdx")
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(document["files"]), 1)
        self.assertEqual(document["files"][0]["licenseConcluded"], "MIT")
        self.assertNotIn(str(self.root), json.dumps(document))


if __name__ == "__main__":
    unittest.main()
