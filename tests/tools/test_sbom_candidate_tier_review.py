#!/usr/bin/env python3
"""Regression tests for licence evidence trust boundaries."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from sbom import _build_document
from sbom_licences import resolve_licences


class TestLicenceTrustTiers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.put(self.repo, ".gitmodules", "")

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

    def declarations(self, root, entries):
        self.put(root, "LICENSE.spdx.yaml", yaml.safe_dump({"packages": entries}))

    def tree_declaration(self, name, scope, licence):
        tree = self.git(self.repo, "rev-parse", "HEAD:" + scope)
        return {
            "name": name, "packageFileName": scope, "licenseDeclared": licence,
            "externalRefs": [{
                "referenceCategory": "PERSISTENT-ID", "referenceType": "gitoid",
                "referenceLocator": "gitoid:tree:sha1:" + tree,
            }],
        }

    def test_bad_source_header_does_not_fall_back_to_trusted_licence(self):
        for header in (
            "# SPDX-License-Identifier: MIT\n# SPDX-License-Identifier: BSD-3-Clause\n",
            "# SPDX-License-Identifier: Not-A-Valid-SPDX-License\n",
        ):
            with self.subTest(header=header):
                source = self.put(self.repo, "package/mod.py", header + "pass\n")
                self.commit(self.repo)
                self.declarations(self.repo, [
                    {"name": "root", "packageFileName": ".", "licenseDeclared": "MIT"},
                    self.tree_declaration("package", "package", "Apache-2.0"),
                ])
                component = {"name": "package", "path": "package", "kind": "frozen",
                             "owner_root": self.repo, "sources": [str(source)],
                             "manifest_license": "BSD-2-Clause"}
                resolve_licences(self.repo, [component])
                self.assertFalse(component["file_licences"][0]["trusted"])
                self.assertIsNone(component["licence_declared"])
                self.assertIsNone(component["licence"])
                self.assertEqual(component["licence_status"], "unresolved")

    def document_for_external_source(self, root, source):
        depfile = self.put(self.repo, "build/source.P", f"build/source.o: {source}\n")
        database = self.put(self.repo, "build/compile_commands.json", json.dumps([{
            "directory": str(self.repo), "file": str(source),
            "arguments": ["cc", "-c", str(source), "-o", "build/source.o", "-MF", str(depfile)],
        }]))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[],
                               root=[f"external={root}"], image="image", offline=True)
        return _build_document(self.repo, args)

    def test_package_metadata_requires_clean_tracked_identity(self):
        self.declarations(self.repo, [])
        self.commit(self.repo)
        for filename, text in (("library.json", '{"license": "Apache-2.0"}\n'),
                               ("manifest.py", 'metadata(license="Apache-2.0")\n')):
            for state in ("clean", "dirty", "untracked"):
                with self.subTest(filename=filename, state=state):
                    root = self.base / (filename.replace(".", "_") + "_" + state)
                    root.mkdir()
                    self.git(root, "init", "-q")
                    source = self.put(root, "source.c", "int source;\n")
                    if state != "untracked":
                        self.put(root, filename, text if state == "clean" else text.replace("Apache-2.0", "MIT"))
                    self.commit(root)
                    if state != "clean":
                        self.put(root, filename, text)
                    document = self.document_for_external_source(root, source)
                    component = document["components"][0]
                    file_licence = component["file_licences"][0]
                    if state == "clean":
                        self.assertTrue(file_licence["trusted"])
                        self.assertEqual(file_licence["expression"], "Apache-2.0")
                    else:
                        self.assertFalse(file_licence["trusted"])
                        self.assertIsNone(component["licence_declared"])
                        self.assertIsNone(component["licence"])

    def test_repository_inventory_does_not_claim_uniform_nested_licence(self):
        self.put(self.repo, "lib/uzlib/src/inflate.c", "int inflate;\n")
        self.put(self.repo, "lib/uzlib/extra/tool.c", "int tool;\n")
        self.commit(self.repo)
        self.declarations(self.repo, [
            self.tree_declaration("uzlib", "lib/uzlib", "Zlib"),
            {"name": "uzlib-extra", "packageFileName": "lib/uzlib/extra", "licenseDeclared": "GPL-3.0-only"},
        ])
        self.commit(self.repo)
        inventory = {(self.repo / "lib/uzlib").resolve(): [("vendored", {
            "name": "uzlib", "path": "lib/uzlib", "upstream": "https://example.com/uzlib",
        })]}
        args = SimpleNamespace(compdb=[], image=None, offline=True)
        with patch("sbom._inventory", return_value=inventory):
            document = _build_document(self.repo, args)
        component = document["components"][0]
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])


if __name__ == "__main__":
    unittest.main()
