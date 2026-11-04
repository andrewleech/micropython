#!/usr/bin/env python3
"""Tests for candidate source and declaration provenance."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from sbom import _build_document


class TestCandidateProvenance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.put(self.repo, ".gitmodules", "")
        self.declare(self.repo, [{"name": "micropython", "packageFileName": ".",
                                  "licenseDeclared": "MIT"}])
        self.commit(self.repo)

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def put(self, root, path, text):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def declare(self, root, packages):
        return self.put(root, "LICENSE.spdx.yaml", yaml.safe_dump({"packages": packages}))

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def document(self, sources, roots=()):
        entries = []
        for index, source in enumerate(sources):
            obj = f"build/fixture-{index}.o"
            dep = f"build/fixture-{index}.P"
            escaped = str(source).replace("\\", "\\\\").replace(" ", "\\ ").replace("#", "\\#").replace("$", "$$")
            self.put(self.repo, dep, f"{obj}: {escaped}\n")
            entries.append({"directory": str(self.repo), "file": str(source),
                            "arguments": ["cc", "-c", str(source), "-o", obj, "-MF", dep]})
        database = self.put(self.repo, "build/compile_commands.json", json.dumps(entries))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[], root=list(roots),
                               image="test-image", offline=True)
        return _build_document(self.repo, args)

    def external_repo(self, name, declaration=True):
        root = self.base / name
        root.mkdir()
        self.git(root, "init", "-q")
        source = self.put(root, "src/component.c", "int component;\n")
        if declaration:
            self.declare(root, [{"name": name, "packageFileName": ".", "licenseDeclared": "MIT"}])
        self.commit(root)
        return root, source

    def test_dirty_and_untracked_supplemental_declarations_report_issue(self):
        for state in ("dirty", "untracked"):
            with self.subTest(state=state):
                root, source = self.external_repo(state, declaration=(state == "dirty"))
                header_source = self.put(root, "src/header.c", "// SPDX-License-Identifier: MIT\nint header;\n")
                self.commit(root)
                self.declare(root, [{"name": state, "packageFileName": ".",
                                     "licenseDeclared": "Apache-2.0"}])
                document = self.document([source, header_source], [f"{state}={root}"])
                self.assertEqual(len(document["components"]), 1)
                component = document["components"][0]
                self.assertEqual(component["licence_status"], "unresolved")
                self.assertIsNone(component["licence_declared"])
                self.assertIsNone(component["licence"])
                self.assertTrue(any(state in issue and "LICENSE.spdx.yaml" in issue
                                    for issue in document["issues"]), document["issues"])
                files = {item["source"]: item for item in component["file_licences"]}
                self.assertIsNone(files[str(source)]["expression"])
                self.assertFalse(files[str(source)]["trusted"])
                self.assertEqual(files[str(header_source)]["expression"], "MIT")
                self.assertEqual(files[str(header_source)]["provenance"], "source SPDX header")
                self.assertTrue(files[str(header_source)]["trusted"])

    def test_clean_explicit_external_root_has_checkout_revision(self):
        root, source = self.external_repo("external")
        revision = self.git(root, "rev-parse", "HEAD")
        document = self.document([source], [f"external={root}"])
        self.assertEqual(len(document["components"]), 1)
        component = document["components"][0]
        self.assertEqual(component["name"], "external")
        self.assertEqual(component["revision"], revision)
        self.assertEqual(component["sources"], [str(source)])
        self.assertEqual(component["licence"], "MIT")
        self.assertEqual(component["licence_status"], "resolved")
        self.assertEqual(document["issues"], [])

    def test_pinned_declaration_root_is_reviewed(self):
        source = self.put(self.repo, "lib/custom/source.c", "int custom;\n")
        self.git(self.repo, "add", "lib/custom")
        tree = self.git(self.repo, "write-tree")
        pin = self.git(self.repo, "rev-parse", tree + ":lib/custom")
        self.declare(self.repo, [
            {"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"},
            {"name": "custom", "packageFileName": "lib/custom", "licenseDeclared": "Apache-2.0",
             "externalRefs": [{"referenceCategory": "PERSISTENT-ID", "referenceType": "gitoid",
                               "referenceLocator": "gitoid:tree:sha1:" + pin}]},
        ])
        self.commit(self.repo)
        document = self.document([source])
        self.assertEqual(len(document["components"]), 1)
        component = document["components"][0]
        self.assertEqual(component["name"], "custom")
        self.assertEqual(component["sources"], ["lib/custom/source.c"])
        self.assertFalse(component.get("unreviewed", False))
        self.assertEqual(component["licence"], "Apache-2.0")
        self.assertEqual(component["licence_status"], "resolved")
        self.assertEqual(document["issues"], [])

    def test_scoped_submodule_retains_inventory_metadata(self):
        origin, _ = self.external_repo("origin", declaration=False)
        self.put(origin, "pkg/selected.c", "int selected;\n")
        self.commit(origin)
        revision = self.git(origin, "rev-parse", "HEAD")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(origin), "lib/thirdparty")
        self.declare(self.repo, [
            {"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"},
            {"name": "thirdparty", "packageFileName": "lib/thirdparty", "licenseDeclared": "MIT",
             "downloadLocation": "git+https://example.com/thirdparty.git@" + revision},
            {"name": "thirdparty-pkg", "packageFileName": "lib/thirdparty/pkg",
             "licenseDeclared": "Apache-2.0"},
        ])
        self.commit(self.repo)
        source = self.repo / "lib/thirdparty/pkg/selected.c"
        document = self.document([source])
        self.assertEqual(len(document["components"]), 1)
        component = document["components"][0]
        self.assertEqual(component["name"], "thirdparty-pkg")
        self.assertEqual(component["path"], "lib/thirdparty/pkg")
        self.assertEqual(component["revision"], revision)
        self.assertEqual(component["version"], revision[:12])
        self.assertTrue(component["purl"])
        self.assertIn({"name": "submodule:path", "value": "lib/thirdparty"}, component["properties"])
        self.assertIn({"name": "git:commit", "value": revision}, component["properties"])
        self.assertIn({"type": "vcs", "url": str(origin)}, component["externalReferences"])
        self.assertEqual(component["licence"], "Apache-2.0")
        self.assertEqual(component["licence_status"], "resolved")
        self.assertEqual(document["issues"], [])


if __name__ == "__main__":
    unittest.main()
