#!/usr/bin/env python3
#
# SPDX-License-Identifier: MIT

"""Tests for the reviewed libm_dbl source licences."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from spdx_tools.common.spdx_licensing import spdx_licensing


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import sbom_licences
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestReviewedLibm(unittest.TestCase):
    ROOT = REPO / "lib/libm_dbl"
    REF = "LicenseRef-Sun-Microsystems-Freely-Granted"
    SOURCES = {
        "acosh.c": "MIT",
        "acos.c": "SunPro",
        "atan.c": "SunPro",
        "__tan.c": REF,
        "exp.c": REF,
        "pow.c": REF,
    }
    CONCLUDED = "MIT AND SunPro AND " + REF

    def component(self):
        return {
            "name": "libm_dbl", "version": None, "path": str(self.ROOT),
            "owner_root": str(REPO), "declaration_root": str(REPO),
            "sources": [str(self.ROOT / name) for name in self.SOURCES],
        }

    def assert_expression(self, actual, expected):
        self.assertEqual(spdx_licensing.parse(actual), spdx_licensing.parse(expected))

    def test_reviewed_notices_and_spdx_file_records(self):
        component = self.component()
        self.assertEqual(resolve_licences(REPO, [component]), [])
        files = {Path(item["source"]).name: item for item in component["file_licences"]}
        self.assertEqual(set(files), set(self.SOURCES))
        for name, expression in self.SOURCES.items():
            with self.subTest(source=name):
                self.assertTrue(files[name]["trusted"])
                self.assert_expression(files[name]["expression"], expression)
        self.assertIsNone(component["licence_declared"])
        self.assert_expression(component["licence_concluded"], self.CONCLUDED)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sbom.json"
            model = {"name": "test-image", "subject": {"image": "firmware.elf"},
                     "issues": [], "components": [component]}
            write_sbom(model, output, "spdx", root=REPO)
            document = json.loads(output.read_text(encoding="utf-8"))
        package = next(item for item in document["packages"] if item["name"] == "libm_dbl")
        self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        self.assert_expression(package["licenseConcluded"], self.CONCLUDED)
        records = {Path(item["fileName"]).name: item for item in document["files"]}
        self.assertEqual(set(records), set(self.SOURCES))
        evidence = {rel["spdxElementId"] for rel in document["relationships"]
                    if rel["relatedSpdxElement"] == package["SPDXID"]
                    and rel["relationshipType"] == "OTHER"}
        for name, expression in self.SOURCES.items():
            with self.subTest(source=name):
                record = records[name]
                self.assert_expression(record["licenseConcluded"], expression)
                self.assertTrue(record["checksums"])
                self.assertIn(record["SPDXID"], evidence)
        texts = {item["licenseId"]: item["extractedText"]
                 for item in document["hasExtractedLicensingInfos"]}
        self.assertIn("2004", texts[self.REF])
        self.assertIn("freely granted", texts[self.REF])
        detail = next(item for item in document["hasExtractedLicensingInfos"]
                      if item["licenseId"] == self.REF)
        self.assertEqual(set(detail["seeAlsos"]), {
            "https://github.com/micropython/micropython/blob/"
            "0fd6c573ea815774668bbb16b8e197c8822368b2/lib/libm_dbl/" + name
            for name in ("__tan.c", "exp.c", "pow.c")
        })

    def test_readme_excluded_files_have_no_asserted_licence(self):
        for name in ("lgamma.c", "log10.c", "tanh.c"):
            with self.subTest(source=name):
                component = self.component()
                component["sources"] = [str(self.ROOT / name)]
                self.assertTrue(resolve_licences(REPO, [component]))
                self.assertIsNone(component["licence_declared"])
                self.assertIsNone(component["licence_concluded"])
                self.assertIn("missing_assertion", {
                    issue["kind"] for issue in component["licence_issues"]
                })
                self.assertFalse(component["file_licences"][0]["trusted"])
                self.assertIsNone(component["file_licences"][0]["expression"])

    def test_wrong_tree_identity_does_not_trust_reviewed_evidence(self):
        real_git = sbom_licences._git

        def wrong_tree(root, *args):
            if Path(root) == REPO and args == ("ls-tree", "HEAD", "--", "lib/libm_dbl"):
                return "040000 tree " + "0" * 40 + "\tlib/libm_dbl"
            return real_git(root, *args)

        component = self.component()
        with patch.object(sbom_licences, "_git", side_effect=wrong_tree):
            issues = resolve_licences(REPO, [component])
        self.assertTrue(issues)
        self.assertIn("pin_mismatch", {
            issue["kind"] for issue in component["licence_issues"]
        })
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence_concluded"])
        for item in component["file_licences"]:
            with self.subTest(source=item["source"]):
                self.assertFalse(item["trusted"])
                self.assertIsNone(item["expression"])


if __name__ == "__main__":
    unittest.main()
