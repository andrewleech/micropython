#!/usr/bin/env python3
"""Tests for licence scope precedence and expression comparisons."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from sbom_licences import resolve_licences


class TestLicenceOrdering(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()

    def write(self, path, content=""):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def declarations(self, packages):
        self.write("LICENSE.spdx.yaml", yaml.safe_dump({"packages": packages}))

    def git(self, *args):
        env = os.environ.copy()
        env.update(
            GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com",
        )
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True,
            capture_output=True, text=True, env=env,
        ).stdout.strip()

    def test_nested_directory_precedes_crossing_glob(self):
        nested = self.write("ports/stm32/usbdev/device.c", "nested\n")
        direct = self.write("ports/stm32/direct.c", "direct\n")
        self.git("init", "-q")
        self.git("add", "ports/stm32")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        tree = self.git("rev-parse", "HEAD:ports/stm32/usbdev")
        self.declarations([
            {
                "name": "crossing-glob",
                "packageFileName": "ports/stm32/*.[chCH]",
                "licenseDeclared": "MIT",
            },
            {
                "name": "usbdev",
                "packageFileName": "ports/stm32/usbdev",
                "licenseDeclared": "Apache-2.0",
                "externalRefs": [{
                    "referenceCategory": "PERSISTENT-ID",
                    "referenceType": "gitoid",
                    "referenceLocator": "gitoid:tree:sha1:" + tree,
                }],
            },
        ])
        components = [
            {"name": "nested", "path": "ports/stm32", "sources": [str(nested)]},
            {"name": "direct", "path": "ports/stm32", "sources": [str(direct)]},
        ]

        self.assertEqual(resolve_licences(self.root, components), [])
        self.assertEqual(components[0]["licence"], "Apache-2.0")
        self.assertEqual(components[1]["licence"], "MIT")
        self.assertEqual([c["licence_status"] for c in components], ["resolved", "resolved"])

    def test_reviewed_file_scope_precedes_package_metadata(self):
        source = self.write("package/module.py")
        self.declarations([{
            "name": "module", "packageFileName": "package/module.py",
            "licenseDeclared": "MIT OR Apache-2.0",
        }])
        self.git("init", "-q")
        for metadata in ("Apache-2.0 OR MIT", "Apache-2.0 AND MIT"):
            with self.subTest(metadata=metadata):
                manifest = self.write("package/manifest.py", f"metadata(license={metadata!r})\n")
                self.git("add", ".")
                self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
                component = {
                    "name": "package", "path": "package", "sources": [str(source)],
                    "manifest": str(manifest), "manifest_license": metadata,
                }
                self.assertEqual(resolve_licences(self.root, [component]), [])
                self.assertEqual(component["licence_declared"], "MIT OR Apache-2.0")
                self.assertEqual(component["licence"], "MIT OR Apache-2.0")
                self.assertEqual(component["licence_status"], "resolved")
                self.assertEqual(
                    [(item["source"], item["expression"], item["provenance"], item["trusted"])
                     for item in component["file_licences"]],
                    [(str(source), "MIT OR Apache-2.0", "reviewed declaration: package/module.py", True)],
                )

    def test_package_metadata_precedes_repository_default(self):
        source = self.write("package/module.py")
        self.declarations([{"name": "root", "packageFileName": ".", "licenseDeclared": "MIT"}])
        manifest = self.write("package/manifest.py", 'metadata(license="Apache-2.0")\n')
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        component = {
            "name": "package", "path": "package", "sources": [str(source)],
            "owner_root": self.root, "manifest": str(manifest), "manifest_license": "Apache-2.0",
        }

        self.assertEqual(resolve_licences(self.root, [component]), [])
        self.assertEqual(component["licence_declared"], "Apache-2.0")
        self.assertEqual(component["licence"], "Apache-2.0")
        self.assertEqual(
            [(item["source"], item["expression"], item["provenance"], item["trusted"])
             for item in component["file_licences"]],
            [(str(source), "Apache-2.0", "package metadata", True)],
        )

    def test_package_metadata_expression_comparison(self):
        source = self.write("package/module.py")
        self.declarations([])
        self.write("package/library.json", json.dumps({"license": "MIT OR Apache-2.0"}))
        for metadata, conflict in (
            ("Apache-2.0 OR MIT", False),
            ("Apache-2.0 AND MIT", True),
        ):
            with self.subTest(metadata=metadata):
                self.write("package/manifest.py", f"metadata(license={metadata!r})\n")
                component = {"name": "package", "path": "package", "sources": [str(source)]}
                issues = resolve_licences(self.root, [component])
                conflicts = [issue for issue in component["licence_issues"]
                             if issue["kind"] == "metadata_conflict"]
                self.assertEqual(bool(conflicts), conflict, issues)
                if conflict:
                    self.assertTrue(issues)
                    self.assertIsNone(component["licence"])
                else:
                    self.assertEqual(issues, [])
                    self.assertEqual(component["licence"], "MIT OR Apache-2.0")


if __name__ == "__main__":
    unittest.main()
