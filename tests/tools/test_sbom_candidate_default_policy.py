#!/usr/bin/env python3
"""Tests for licence defaults and reviewed exceptions."""

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


class TestCandidateDefaultPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.reviewed = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))

    def git(self, root, *args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def put(self, root, path, text):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def declarations(self, packages, texts=()):
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump({
            "packages": packages, "hasExtractedLicensingInfos": list(texts),
        }, sort_keys=False))

    def external_library(self, package, pin="matching", metadata=None):
        library = self.base / "external"
        library.mkdir()
        self.git(library, "init", "-q")
        source = self.put(library, f"python-stdlib/{package}/{package}.py", "value = 1\n")
        self.put(library, f"python-stdlib/{package}/manifest.py", f"freeze('.', '{package}.py')\n")
        if metadata is not None:
            self.put(library, f"python-stdlib/{package}/library.json", json.dumps({"license": metadata}))
        self.commit(library)
        revision = self.git(library, "rev-parse", "HEAD")
        self.git(library, "remote", "add", "origin", "https://github.com/micropython/micropython-lib.git")
        names = {"micropython", "micropython-lib", "uu"}
        packages = [dict(item) for item in self.reviewed["packages"] if item["name"] in names]
        library_entry = next(item for item in packages if item["name"] == "micropython-lib")
        if pin == "mismatched":
            library_entry["downloadLocation"] = library_entry["downloadLocation"].rsplit("@", 1)[0] + "@" + "0" * 40
        elif pin == "matching":
            library_entry["downloadLocation"] = library_entry["downloadLocation"].rsplit("@", 1)[0] + "@" + revision
        elif pin == "unreviewed":
            packages.remove(library_entry)
            packages = [item for item in packages if item["name"] != "uu"]
        texts = [item for item in self.reviewed["hasExtractedLicensingInfos"]
                 if item["licenseId"] == "LicenseRef-Ellinghouse-UU-Permission-Notice"]
        self.declarations(packages, texts)
        self.git(self.repo, "add", "LICENSE.spdx.yaml")
        self.git(self.repo, "update-index", "--add", "--cacheinfo",
                 "160000," + revision + ",lib/micropython-lib")
        self.git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        component = {
            "name": package, "path": str(source.parent), "sources": [str(source)],
            "kind": "frozen", "owner_root": str(library), "declaration_root": str(library),
            "revision": revision,
        }
        return component

    def assert_unresolved(self, component):
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["file_licences"][0]["expression"])
        self.assertFalse(component["file_licences"][0]["trusted"])

    def test_external_reviewed_uu_uses_exception_not_default(self):
        component = self.external_library("uu")
        issues = resolve_licences(self.repo, [component])
        expression = "Python-2.0 AND LicenseRef-Ellinghouse-UU-Permission-Notice"
        self.assertEqual(issues, [])
        self.assertEqual(component["licence_declared"], expression)
        self.assertEqual(component["licence"], expression)
        self.assertEqual(component["licence_status"], "resolved")
        self.assertNotEqual(component["file_licences"][0]["provenance"], "micropython-lib builder default")
        self.assertTrue(component["file_licences"][0]["trusted"])
        self.assertTrue(component["licence_texts"]["LicenseRef-Ellinghouse-UU-Permission-Notice"])

    def test_external_uu_unverified_revision_never_inherits_mit(self):
        for pin in ("mismatched", "unreviewed"):
            with self.subTest(pin=pin):
                with tempfile.TemporaryDirectory() as temp:
                    old_base, old_repo = self.base, self.repo
                    self.base = Path(temp).resolve()
                    self.repo = self.base / "repo"
                    self.repo.mkdir()
                    self.git(self.repo, "init", "-q")
                    try:
                        component = self.external_library("uu", pin=pin)
                        issues = resolve_licences(self.repo, [component])
                        self.assertTrue(issues)
                        self.assert_unresolved(component)
                    finally:
                        self.base, self.repo = old_base, old_repo

    def test_external_verified_library_uses_mit_default_for_simple_package(self):
        component = self.external_library("example")
        issues = resolve_licences(self.repo, [component])
        self.assertEqual(issues, [])
        self.assertEqual(component["licence_declared"], "MIT")
        self.assertEqual(component["licence"], "MIT")
        self.assertEqual(component["licence_status"], "resolved")
        self.assertEqual(component["file_licences"][0]["provenance"], "micropython-lib builder default")
        self.assertTrue(component["file_licences"][0]["trusted"])

    def test_explicit_noassertion_blocks_repository_default(self):
        for location in ("manifest", "library_json", "component", "scope"):
            with self.subTest(location=location):
                source = self.put(self.repo, f"pkg-{location}/module.py", "value = 1\n")
                directory = source.parent
                packages = [{"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"}]
                component = {"name": location, "path": str(directory), "sources": [str(source)],
                             "owner_root": str(self.repo)}
                if location == "manifest":
                    self.put(directory, "manifest.py", "metadata(license='NOASSERTION')\n")
                elif location == "library_json":
                    self.put(directory, "library.json", json.dumps({"license": "NOASSERTION"}))
                elif location == "component":
                    component["manifest_license"] = "NOASSERTION"
                else:
                    packages.append({"name": location, "packageFileName": str(source.relative_to(self.repo)),
                                     "licenseDeclared": "NOASSERTION"})
                self.declarations(packages)
                self.commit(self.repo)
                issues = resolve_licences(self.repo, [component])
                self.assertTrue(issues)
                self.assert_unresolved(component)

    def test_noassertion_blocks_verified_library_default(self):
        component = self.external_library("example", metadata="NOASSERTION")
        issues = resolve_licences(self.repo, [component])
        self.assertTrue(issues)
        self.assert_unresolved(component)

    def test_legacy_mit_header_conflicts_with_reviewed_scope(self):
        source = self.put(self.repo, "ports/mimxrt/hal/flexspi_nor_flash.py", "# MIT license\nvalue = 1\n")
        packages = [dict(item) for item in self.reviewed["packages"] if item["name"] == "micropython"]
        packages.append({"name": "reviewed", "packageFileName": str(source.relative_to(self.repo)),
                         "licenseDeclared": "BSD-3-Clause"})
        self.declarations(packages)
        self.commit(self.repo)
        component = {"name": "reviewed", "path": str(source.parent), "sources": [str(source)],
                     "owner_root": str(self.repo)}
        issues = resolve_licences(self.repo, [component])
        self.assertTrue(issues)
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["licence"])
        self.assertEqual(component["file_licences"][0]["expression"], "MIT")
        self.assertEqual(component["file_licences"][0]["provenance"], "source MIT header")
        self.assertFalse(component["file_licences"][0]["trusted"])
        conflicts = [issue for issue in component["licence_issues"] if issue["kind"] == "metadata_conflict"]
        self.assertTrue(conflicts)
        self.assertIn("MIT", json.dumps(conflicts))
        self.assertIn("BSD-3-Clause", json.dumps(conflicts))


if __name__ == "__main__":
    unittest.main()
