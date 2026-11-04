#!/usr/bin/env python3
#
# Unit tests for tools/sbom_writers.py.

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))

from cyclonedx.exception import MissingOptionalDependencyException
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonValidator
from spdx_tools.common.spdx_licensing import spdx_licensing
from spdx_tools.spdx.parser.jsonlikedict.json_like_dict_parser import JsonLikeDictParser
from spdx_tools.spdx.validation.document_validator import validate_full_spdx_document

from sbom_writers import write_sbom


def component(name, expression, version="1.2.3", **fields):
    item = {
        "name": name,
        "version": version,
        "licence": expression,
        "licence_texts": {},
        "licence_comments": [],
    }
    item.update(fields)
    return item


class TestSbomWriters(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.model = {"name": "test-image", "issues": [], "components": []}

    def write(self, format):
        path = Path(self.tmpdir.name) / (format + ".json")
        write_sbom(self.model, path, format)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_license_expressions_preserve_and_or(self):
        self.model["components"] = [
            component("all", "MIT AND Apache-2.0"),
            component("choice", "MIT OR Apache-2.0"),
        ]
        spdx = {p["name"]: p for p in self.write("spdx")["packages"]}
        cdx = {p["name"]: p for p in self.write("cyclonedx")["components"]}
        for name, expression in (("all", "MIT AND Apache-2.0"), ("choice", "MIT OR Apache-2.0")):
            with self.subTest(name=name):
                self.assertEqual(
                    spdx_licensing.parse(spdx[name]["licenseDeclared"]),
                    spdx_licensing.parse(expression),
                )
                licenses = cdx[name]["licenses"]
                self.assertEqual(len(licenses), 1)
                self.assertEqual(licenses[0]["expression"], expression)
                self.assertEqual(licenses[0]["acknowledgement"], "declared")

    def test_custom_license_text_and_name(self):
        self.model["components"] = [component(
            "custom", "LicenseRef-custom",
            licence_texts={"LicenseRef-custom": "Custom permission text.\n"},
            licence_details={"LicenseRef-custom": {"name": "Custom terms", "comment": "License notice"}},
        )]
        spdx = self.write("spdx")
        extracted = spdx["hasExtractedLicensingInfos"][0]
        self.assertEqual(extracted["licenseId"], "LicenseRef-custom")
        self.assertEqual(extracted["name"], "Custom terms")
        self.assertEqual(extracted["extractedText"], "Custom permission text.\n")
        self.assertEqual(extracted["comment"], "License notice")
        package = next(p for p in spdx["packages"] if p["name"] == "custom")
        self.assertEqual(package["licenseDeclared"], "LicenseRef-custom")

        cdx = self.write("cyclonedx")["components"][0]
        self.assertEqual(cdx["licenses"][0]["expression"], "LicenseRef-custom")
        license = cdx["evidence"]["licenses"][0]["license"]
        self.assertEqual(license["name"], "Custom terms")
        self.assertEqual(license["text"]["content"], "Custom permission text.\n")
        properties = {p["name"]: p["value"] for p in license["properties"]}
        self.assertEqual(properties["micropython:licence-id"], "LicenseRef-custom")
        self.assertEqual(properties["micropython:licence-comment"], "License notice")

    def test_declared_license_is_not_concluded_license(self):
        self.model["components"] = [component("conflict", "MIT", licence_declared="Apache-2.0")]
        spdx = next(p for p in self.write("spdx")["packages"] if p["name"] == "conflict")
        self.assertEqual(spdx["licenseDeclared"], "Apache-2.0")
        self.assertEqual(spdx["licenseConcluded"], "NOASSERTION")
        cdx = self.write("cyclonedx")["components"][0]
        self.assertEqual(cdx["licenses"], [{"acknowledgement": "declared", "expression": "Apache-2.0"}])

    def test_conclusion_is_distinct_from_declaration_in_both_formats(self):
        first = Path(self.tmpdir.name) / "first.c"
        second = Path(self.tmpdir.name) / "second.c"
        first.write_text("first\n", encoding="utf-8")
        second.write_text("second\n", encoding="utf-8")
        conclusion = "BSD-3-Clause AND (MIT OR Apache-2.0)"
        self.model["components"] = [component(
            "selected", "MIT", licence_declared="MIT", licence_concluded=conclusion,
            file_licences=[
                {"source": str(first), "expression": "BSD-3-Clause",
                 "provenance": "source SPDX header", "trusted": True},
                {"source": str(second), "expression": "MIT OR Apache-2.0",
                 "provenance": "source SPDX header", "trusted": True},
            ],
        )]
        spdx = next(p for p in self.write("spdx")["packages"] if p["name"] == "selected")
        self.assertEqual(spdx["licenseDeclared"], "MIT")
        self.assertEqual(spdx_licensing.parse(spdx["licenseConcluded"]), spdx_licensing.parse(conclusion))
        self.assertIn("selected", spdx["licenseComments"].lower())
        cdx = self.write("cyclonedx")["components"][0]
        self.assertEqual(cdx["licenses"], [{"acknowledgement": "declared", "expression": "MIT"}])

    def test_conclusion_without_declaration_has_concluded_acknowledgement(self):
        source = Path(self.tmpdir.name) / "choice.c"
        source.write_text("choice\n", encoding="utf-8")
        choice = "MIT OR Apache-2.0"
        self.model["components"] = [component(
            "choice", None, licence_declared=None, licence_concluded=choice,
            file_licences=[{"source": str(source), "expression": choice,
                            "provenance": "source SPDX header", "trusted": True}],
        )]
        spdx = next(p for p in self.write("spdx")["packages"] if p["name"] == "choice")
        self.assertEqual(spdx["licenseDeclared"], "NOASSERTION")
        self.assertEqual(spdx_licensing.parse(spdx["licenseConcluded"]), spdx_licensing.parse(choice))
        cdx = self.write("cyclonedx")["components"][0]
        self.assertEqual(cdx["licenses"], [{"acknowledgement": "concluded", "expression": choice}])

    def test_stale_conclusion_is_not_exported_for_incomplete_evidence(self):
        source = Path(self.tmpdir.name) / "source.c"
        source.write_text("source\n", encoding="utf-8")
        for expression, trusted, issues in (
            (None, False, []),
            ("MIT", False, []),
            ("MIT", True, [{"kind": "metadata_conflict", "status": "unresolved", "message": "conflict"}]),
        ):
            with self.subTest(expression=expression, trusted=trusted, issues=issues):
                self.model["components"] = [component(
                    "incomplete", None, licence_declared=None, licence_concluded="MIT",
                    licence_issues=issues,
                    file_licences=[{"source": str(source), "expression": expression,
                                    "provenance": "source SPDX header", "trusted": trusted}],
                )]
                spdx = next(p for p in self.write("spdx")["packages"] if p["name"] == "incomplete")
                self.assertEqual(spdx["licenseConcluded"], "NOASSERTION")
                cdx = self.write("cyclonedx")["components"][0]
                self.assertFalse(cdx.get("licenses"))

    def test_absorbed_identifier_does_not_validate_stale_conclusion(self):
        source = Path(self.tmpdir.name) / "source.c"
        source.write_text("source\n", encoding="utf-8")
        self.model["components"] = [component(
            "dependency", None, licence_declared=None, licence_concluded="MIT",
            file_licences=[{"source": str(source), "expression": "MIT OR (MIT AND Zlib)",
                            "provenance": "source SPDX header", "trusted": True}],
        )]
        spdx = next(p for p in self.write("spdx")["packages"] if p["name"] == "dependency")
        self.assertEqual(spdx["licenseConcluded"], "NOASSERTION")
        self.assertFalse(self.write("cyclonedx")["components"][0].get("licenses"))

    def test_shared_source_with_absorbed_identifier_blocks_both_conclusions(self):
        source = Path(self.tmpdir.name) / "shared.c"
        source.write_text("source\n", encoding="utf-8")
        self.model["components"] = [
            component(name, None, licence_declared=None, licence_concluded=expression,
                      file_licences=[{"source": str(source), "expression": expression,
                                      "provenance": "source SPDX header", "trusted": True}])
            for name, expression in (("first", "MIT"), ("second", "MIT OR (MIT AND Zlib)"))
        ]
        packages = {p["name"]: p for p in self.write("spdx")["packages"]}
        components = {p["name"]: p for p in self.write("cyclonedx")["components"]}
        for name in ("first", "second"):
            with self.subTest(name=name):
                self.assertEqual(packages[name]["licenseConcluded"], "NOASSERTION")
                self.assertFalse(components[name].get("licenses"))

    def test_purls_and_versions_match_across_formats(self):
        self.model["components"] = [
            component("parent", "MIT", "2.0", purl="pkg:generic/example@2.0", components=[
                component("child", "Apache-2.0", "3.4.5", purl="pkg:pypi/example-child@3.4.5"),
            ]),
            component("unversioned", "MIT", None),
        ]
        spdx = {p["name"]: p for p in self.write("spdx")["packages"]}
        cdx_roots = {p["name"]: p for p in self.write("cyclonedx")["components"]}
        cdx = dict(cdx_roots)
        cdx.update({p["name"]: p for p in cdx_roots["parent"]["components"]})
        for name, version, purl in (
            ("parent", "2.0", "pkg:generic/example@2.0"),
            ("child", "3.4.5", "pkg:pypi/example-child@3.4.5"),
        ):
            with self.subTest(name=name):
                self.assertEqual(spdx[name]["versionInfo"], version)
                self.assertEqual(cdx[name]["version"], version)
                refs = [r["referenceLocator"] for r in spdx[name]["externalRefs"] if r["referenceType"] == "purl"]
                self.assertEqual(refs, [purl])
                self.assertEqual(cdx[name]["purl"], purl)
        self.assertNotIn("versionInfo", spdx["unversioned"])
        self.assertNotIn("version", cdx["unversioned"])
        self.assertNotIn("purl", cdx["unversioned"])

    def test_stdout_dash_writes_json_for_both_formats(self):
        self.model["components"] = [component("example", "MIT")]
        for format in ("spdx", "cyclonedx"):
            with self.subTest(format=format):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    write_sbom(self.model, "-", format)
                document = json.loads(output.getvalue())
                self.assertEqual(document["spdxVersion"] if format == "spdx" else document["specVersion"],
                                 "SPDX-2.3" if format == "spdx" else "1.6")
                self.assertTrue(output.getvalue().endswith("\n"))

    def test_spdx_output_passes_document_validator(self):
        self.model["components"] = [component("example", "MIT AND Apache-2.0")]
        document = JsonLikeDictParser().parse(self.write("spdx"))
        self.assertEqual(validate_full_spdx_document(document), [])

    def test_cyclonedx_output_passes_schema_validator(self):
        self.model["components"] = [component("example", "MIT OR Apache-2.0")]
        document = self.write("cyclonedx")
        try:
            error = JsonValidator(SchemaVersion.V1_6).validate_str(json.dumps(document))
        except MissingOptionalDependencyException as exc:
            self.skipTest(str(exc))
        self.assertIsNone(error, str(error))


if __name__ == "__main__":
    unittest.main()
