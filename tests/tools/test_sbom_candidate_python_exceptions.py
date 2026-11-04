#!/usr/bin/env python3
"""Tests for reviewed CPython-derived micropython-lib source licences."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from sbom_licences import resolve_licences


class TestCandidatePythonExceptions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.origin = self.base / "micropython-lib"
        self.origin.mkdir()
        self.git(self.origin, "init", "-q")
        self.sources = {
            name: f"python-stdlib/{name}/{name}.py"
            for name in ("colorsys", "contextlib", "textwrap")
        }
        self.sources["ucontextlib"] = "micropython/ucontextlib/ucontextlib.py"
        for name, path in self.sources.items():
            self.put(self.origin, path, "value = 1\n")
            self.put(self.origin, str(Path(path).parent / "manifest.py"),
                     f"module('{name}.py')\n")
        self.commit(self.origin)
        self.revision = self.git(self.origin, "rev-parse", "HEAD")

        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(self.origin), "lib/micropython-lib")
        self.checkout = self.repo / "lib/micropython-lib"
        self.git(self.origin, "remote", "add", "origin",
                 "https://github.com/micropython/micropython-lib.git")

        reviewed = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))
        prefix = "lib/micropython-lib"
        packages = [dict(item) for item in reviewed["packages"]
                    if item["packageFileName"] in (".", prefix)
                    or item["packageFileName"].startswith(prefix + "/python-stdlib/")]
        library = next(item for item in packages if item["packageFileName"] == prefix)
        library["downloadLocation"] = library["downloadLocation"].rsplit("@", 1)[0] + "@" + self.revision
        self.declaration = {
            "packages": packages,
            "hasExtractedLicensingInfos": reviewed.get("hasExtractedLicensingInfos", []),
        }
        self.write_declaration()
        self.commit(self.repo)

    def git(self, root, *args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def put(self, root, path, text):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def write_declaration(self):
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(self.declaration, sort_keys=False))

    def component(self, name, external):
        owner = self.origin if external else self.checkout
        source = owner / self.sources[name]
        return {
            "name": name, "path": str(source.parent), "sources": [str(source)],
            "kind": "frozen", "owner_root": str(owner),
            "declaration_root": str(owner if external else self.repo),
            "revision": self.revision,
        }

    def test_reviewed_exceptions_override_default_for_internal_and_external_library(self):
        for external in (False, True):
            with self.subTest(external=external):
                components = [self.component(name, external) for name in self.sources]
                self.assertEqual(resolve_licences(self.repo, components), [])
                for component in components:
                    name = component["name"]
                    expected = "MIT" if name == "ucontextlib" else "Python-2.0"
                    with self.subTest(name=name):
                        self.assertEqual(component["licence_declared"], expected)
                        self.assertEqual(component["licence"], expected)
                        self.assertEqual(component["licence_status"], "resolved")
                        file_licence = component["file_licences"][0]
                        self.assertEqual(file_licence["expression"], expected)
                        self.assertTrue(file_licence["trusted"])
                        if name == "ucontextlib":
                            self.assertEqual(file_licence["provenance"],
                                             "micropython-lib builder default")
                        else:
                            self.assertTrue(file_licence["provenance"].startswith(
                                "reviewed declaration: "))
                            self.assertIn(f"python-stdlib/{name}", file_licence["provenance"])

    def test_mismatched_library_pin_does_not_trust_exceptions_or_default(self):
        library = next(item for item in self.declaration["packages"]
                       if item["packageFileName"] == "lib/micropython-lib")
        library["downloadLocation"] = library["downloadLocation"].rsplit("@", 1)[0] + "@" + "0" * 40
        self.write_declaration()
        self.commit(self.repo)
        for external in (False, True):
            with self.subTest(external=external):
                components = [self.component(name, external) for name in self.sources]
                self.assertTrue(resolve_licences(self.repo, components))
                for component in components:
                    with self.subTest(name=component["name"]):
                        self.assertIsNone(component["licence"])
                        self.assertEqual(component["licence_status"], "unresolved")
                        self.assertIsNone(component["file_licences"][0]["expression"])
                        self.assertFalse(component["file_licences"][0]["trusted"])


if __name__ == "__main__":
    unittest.main()
