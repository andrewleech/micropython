#!/usr/bin/env python3
"""Tests for portable SBOM identities and licence provenance."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from sbom_writers import write_sbom


def make_model(root):
    source_dir = root / "lib" / "mixed"
    source_dir.mkdir(parents=True)
    (source_dir / "first.c").write_text("/* SPDX-License-Identifier: MIT */\n", encoding="utf-8")
    (source_dir / "second.c").write_text("/* SPDX-License-Identifier: Apache-2.0 */\n", encoding="utf-8")
    files = [
        {"source": "lib/mixed/first.c", "expression": "MIT",
         "provenance": "source header", "trusted": True},
        {"source": "lib/mixed/second.c", "expression": "Apache-2.0",
         "provenance": "reviewed declaration: lib/mixed/second.c", "trusted": True},
    ]
    mixed = {
        "name": "mixed", "version": "1.0", "path": "lib/mixed",
        "owner_root": str(source_dir), "declaration_root": str(root),
        "sources": [entry["source"] for entry in files],
        "licence": None, "licence_declared": None, "licence_mixed": True,
        "licence_status": "resolved", "licence_issues": [],
        "licence_comments": [], "licence_texts": {}, "file_licences": files,
        "components": [{
            "name": "child", "version": "1.0", "path": "lib/mixed/child",
            "owner_root": str(source_dir), "declaration_root": str(root),
            "licence": "MIT", "licence_declared": "MIT", "licence_texts": {},
            "licence_comments": [],
        }],
    }
    unresolved = {
        "name": "unresolved", "version": "1.0", "path": "lib/unresolved",
        "owner_root": str(root / "lib" / "unresolved"), "declaration_root": str(root),
        "licence": None, "licence_declared": "MIT", "licence_texts": {},
        "licence_comments": [], "licence_status": "unresolved",
        "licence_issues": [{"kind": "pin_mismatch", "message": "review required"}],
    }
    return {"name": "test-image", "issues": [], "components": [mixed, unresolved]}


def component_by_name(document, format, name):
    if format == "spdx":
        return next(package for package in document["packages"] if package["name"] == name)
    return next(component for component in document["components"] if component["name"] == name)


class TestSbomCandidatePortability(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.roots = [Path(self.temporary.name) / "checkout-one", Path(self.temporary.name) / "checkout-two"]
        self.models = [make_model(root) for root in self.roots]

    def documents(self, format):
        result = []
        for root, model in zip(self.roots, self.models):
            output = root / (format + ".json")
            write_sbom(model, output, format, root=root)
            text = output.read_text(encoding="utf-8")
            result.append((text, json.loads(text)))
        return result

    def test_checkout_paths_do_not_leak_or_change_identities(self):
        for format in ("spdx", "cyclonedx"):
            with self.subTest(format=format):
                outputs = self.documents(format)
                for text, _ in outputs:
                    for root in self.roots:
                        self.assertNotIn(str(root), text)
                first, second = (entry[1] for entry in outputs)
                if format == "spdx":
                    self.assertEqual(first["documentNamespace"], second["documentNamespace"])
                    identities = lambda doc: {p["name"]: p["SPDXID"] for p in doc["packages"]}
                else:
                    self.assertEqual(first["serialNumber"], second["serialNumber"])
                    def identities(doc):
                        result = {}
                        def collect(components):
                            for item in components:
                                result[item["name"]] = item["bom-ref"]
                                collect(item.get("components", []))
                        collect(doc["components"])
                        return result
                self.assertEqual(identities(first), identities(second))
                self.assertIn("child", identities(first))

    def test_mixed_file_licence_provenance_is_readable(self):
        for format in ("spdx", "cyclonedx"):
            with self.subTest(format=format):
                document = self.documents(format)[0][1]
                component = component_by_name(document, format, "mixed")
                if format == "spdx":
                    self.assertEqual(component["licenseDeclared"], "NOASSERTION")
                    evidence = component["licenseComments"]
                    for entry in self.models[0]["components"][0]["file_licences"]:
                        for key in ("source", "expression", "provenance"):
                            self.assertIn(entry[key], evidence)
                else:
                    self.assertFalse(component.get("licenses"))
                    properties = {p["name"]: p["value"] for p in component["properties"]}
                    files = json.loads(properties["micropython:file_licences"])
                    self.assertEqual(files, self.models[0]["components"][0]["file_licences"])

    def test_unresolved_claim_is_not_a_declared_licence(self):
        for format in ("spdx", "cyclonedx"):
            with self.subTest(format=format):
                document = self.documents(format)[0][1]
                component = component_by_name(document, format, "unresolved")
                if format == "spdx":
                    self.assertEqual(component["licenseDeclared"], "NOASSERTION")
                    annotations = component.get("annotations", [])
                    provenance = [json.loads(a["comment"]) for a in annotations
                                  if a.get("annotationType") == "OTHER" and a.get("comment", "").startswith("{")]
                    for entry in provenance:
                        if "licence_declared" in entry:
                            self.assertIsNone(entry["licence_declared"])
                else:
                    self.assertFalse(component.get("licenses"))
                    properties = {p["name"]: p["value"] for p in component["properties"]}
                    if "micropython:licence_declared" in properties:
                        self.assertIsNone(json.loads(properties["micropython:licence_declared"]))


if __name__ == "__main__":
    unittest.main()
