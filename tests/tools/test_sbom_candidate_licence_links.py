#!/usr/bin/env python3
"""Tests for links in reviewed custom licence declarations."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestLicenceLinks(unittest.TestCase):
    REMOTE = "https://example.com/vendor/dependency.git"
    REF = "LicenseRef-Reviewed"
    TEXT = "Copyright Example\nPermission to use this software is granted.\n"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "project"
        self.checkout = self.base / "dependency"
        self.repo.mkdir()
        self.checkout.mkdir()
        self.git(self.checkout, "init", "-q")
        self.git(self.checkout, "remote", "add", "origin", self.REMOTE)
        self.source = self.checkout / "source.c"
        self.source.write_text("int example;\n", encoding="utf-8")
        (self.checkout / "other.c").write_text("int other;\n", encoding="utf-8")
        self.git(self.checkout, "add", "source.c", "other.c")
        self.git(self.checkout, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        self.revision = self.git(self.checkout, "rev-parse", "HEAD")
        self.LINKS = [
            f"https://example.com/vendor/dependency/blob/{self.revision}/{name}"
            for name in ("source.c", "other.c")
        ]
        declaration = {
            "packages": [],
            "external_repositories": [{
                "url": self.REMOTE,
                "commit": self.revision,
                "licenses": [{"path": "source.c", "license": self.REF}],
            }],
            "hasExtractedLicensingInfos": [{
                "licenseId": self.REF,
                "name": "Reviewed terms",
                "extractedText": self.TEXT,
                "seeAlso": self.LINKS,
            }],
        }
        (self.repo / "LICENSE.spdx.yaml").write_text(
            yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8"
        )

    def git(self, root, *args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def resolve(self, **overrides):
        component = {
            "name": "dependency",
            "version": None,
            "path": str(self.checkout),
            "owner_root": str(self.checkout),
            "declaration_root": str(self.checkout),
            "revision": self.revision,
            "sources": [str(self.source)],
        }
        component.update(overrides)
        issues = resolve_licences(self.repo, [component])
        return component, issues

    def serialize(self, component, issues, format):
        path = self.base / (format + ".json")
        model = {"name": "test-image", "issues": issues, "components": [component]}
        write_sbom(model, path, format, root=self.repo)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_trusted_pinned_links_reach_both_formats(self):
        component, issues = self.resolve()
        self.assertEqual(issues, [])
        self.assertTrue(component["file_licences"][0]["trusted"])
        self.assertEqual(component["licence"], self.REF)
        self.assertEqual(component["licence_details"][self.REF]["seeAlso"], self.LINKS)

        spdx = self.serialize(component, issues, "spdx")
        extracted = {item["licenseId"]: item for item in spdx["hasExtractedLicensingInfos"]}
        self.assertEqual(extracted[self.REF]["extractedText"], self.TEXT)
        self.assertEqual(set(extracted[self.REF]["seeAlsos"]), set(self.LINKS))

        cdx = self.serialize(component, issues, "cyclonedx")
        item = cdx["components"][0]
        licence = item["evidence"]["licenses"][0]["license"]
        self.assertEqual(licence["name"], "Reviewed terms")
        self.assertEqual(licence["text"]["content"], self.TEXT)
        self.assertEqual(licence["url"], self.LINKS[0])
        links = {ref["url"] for ref in item["externalReferences"] if ref["type"] == "license"}
        self.assertEqual(links, set(self.LINKS))

    def test_untrusted_checkout_does_not_export_reviewed_links(self):
        for reason in ("revision", "dirty"):
            with self.subTest(reason=reason):
                if reason == "dirty":
                    self.source.write_text("int modified;\n", encoding="utf-8")
                component, issues = self.resolve(**({"revision": "0" * 40} if reason == "revision" else {}))
                self.assertTrue(issues)
                self.assertFalse(component["file_licences"][0]["trusted"])
                self.assertIsNone(component["licence"])
                self.assertNotIn(self.REF, component["licence_details"])

                spdx = self.serialize(component, issues, "spdx")
                extracted = {item["licenseId"] for item in spdx.get("hasExtractedLicensingInfos", [])}
                self.assertNotIn(self.REF, extracted)
                cdx = self.serialize(component, issues, "cyclonedx")
                item = cdx["components"][0]
                self.assertEqual(item.get("evidence", {}).get("licenses", []), [])
                self.assertFalse(any(ref["url"] in self.LINKS for ref in item.get("externalReferences", [])))


if __name__ == "__main__":
    unittest.main()
