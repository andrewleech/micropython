#!/usr/bin/env python3
"""Tests for serialized reviewed declarations with unresolved conflicts."""

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


class TestCandidateConflicts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def fixture(self, mismatch=False):
        self.git("init", "-q")
        (self.root / "tracked").write_text("fixture\n", encoding="utf-8")
        self.git("add", "tracked")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        pin = self.git("rev-parse", "HEAD")
        self.git("update-index", "--add", "--cacheinfo", "160000," + pin + ",lib/berkeley-db-1.xx")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "submodule")
        source = self.root / "lib/berkeley-db-1.xx/db.c"
        source.parent.mkdir(parents=True)
        source.write_text("source\n", encoding="utf-8")
        reviewed = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))
        entries = [dict(item) for item in reviewed["packages"]
                   if item["name"] in {"micropython", "berkeley-db-1.xx"}]
        berkeley = next(item for item in entries if item["name"] == "berkeley-db-1.xx")
        berkeley["downloadLocation"] = "git+https://example.com/berkeley@" + ("0" * 40 if mismatch else pin)
        sources = [str(source)]
        if mismatch:
            other = self.root / "other/other.c"
            other.parent.mkdir()
            other.write_text("source\n", encoding="utf-8")
            sources.append(str(other))
            entries.append({"name": "other", "packageFileName": "other/other.c", "licenseDeclared": "MIT"})
        (self.root / "LICENSE.spdx.yaml").write_text(yaml.safe_dump({
            "packages": entries,
            "hasExtractedLicensingInfos": reviewed["hasExtractedLicensingInfos"],
        }), encoding="utf-8")
        item = {"name": "berkeley", "path": ".", "owner_root": str(self.root),
                "sources": sources, "version": None}
        issues = resolve_licences(self.root, [item])
        model = {"name": "test-image", "issues": issues, "components": [item]}
        documents = {}
        for format in ("spdx", "cyclonedx"):
            path = self.root / (format + ".json")
            write_sbom(model, path, format)
            documents[format] = json.loads(path.read_text(encoding="utf-8"))
        return item, documents

    def serialized_component(self, documents):
        spdx = documents["spdx"]
        package = next(item for item in spdx["packages"] if item["name"] == "berkeley")
        (annotation,) = package["annotations"]
        provenance = json.loads(annotation["comment"])
        component = next(item for item in documents["cyclonedx"]["components"]
                         if item["name"] == "berkeley")
        properties = {item["name"]: json.loads(item["value"])
                      for item in component["properties"] if item["name"].startswith("micropython:")}
        return package, provenance, component, properties

    def test_reviewed_declaration_survives_licence_gap(self):
        item, documents = self.fixture()
        self.assertEqual(item["licence_declared"], "BSD-4-Clause")
        self.assertIsNone(item["licence"])
        self.assertEqual(item["licence_status"], "unresolved")
        self.assertEqual({issue["kind"] for issue in item["licence_issues"]}, {"licence_gap"})
        package, provenance, component, properties = self.serialized_component(documents)
        self.assertEqual(package["licenseDeclared"], "BSD-4-Clause")
        self.assertEqual(package["licenseConcluded"], "NOASSERTION")
        self.assertEqual(component["licenses"], [{"expression": "BSD-4-Clause", "acknowledgement": "declared"}])
        for status, issues in ((provenance["licence_status"], provenance["licence_issues"]),
                               (properties["micropython:licence_status"], properties["micropython:licence_issues"])):
            self.assertEqual(status, "unresolved")
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["kind"], "licence_gap")
            self.assertEqual(issues[0]["status"], "unresolved")
            self.assertIn("BSD-3-Clause", issues[0]["message"])
            self.assertIn("conflict", issues[0]["message"])
        self.assertIn("BSD-3-Clause", package["licenseComments"])

    def test_pin_mismatch_does_not_serialize_partial_declaration(self):
        item, documents = self.fixture(mismatch=True)
        self.assertIsNone(item["licence_declared"])
        self.assertIsNone(item["licence"])
        self.assertEqual(item["licence_status"], "unresolved")
        self.assertIn("pin_mismatch", {issue["kind"] for issue in item["licence_issues"]})
        package, provenance, component, properties = self.serialized_component(documents)
        self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        self.assertEqual(package["licenseConcluded"], "NOASSERTION")
        self.assertFalse(component.get("licenses"))
        for status, issues in ((provenance["licence_status"], provenance["licence_issues"]),
                               (properties["micropython:licence_status"], properties["micropython:licence_issues"])):
            self.assertEqual(status, "unresolved")
            mismatch = next(issue for issue in issues if issue["kind"] == "pin_mismatch")
            self.assertEqual(mismatch["status"], "unresolved")
            self.assertEqual(mismatch["declared"], "BSD-4-Clause")
            self.assertNotEqual(mismatch["expected"], mismatch["actual"])


if __name__ == "__main__":
    unittest.main()
