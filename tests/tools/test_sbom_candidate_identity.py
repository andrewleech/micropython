#!/usr/bin/env python3
"""Tests for build source identity in real Git checkouts."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from sbom import _build_document


class TestCandidateIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.origin = self.base / "dependency"
        self.repo = self.base / "parent"
        self.origin.mkdir()
        self.repo.mkdir()
        self.git(self.origin, "init", "-q")
        self.put(self.origin / "tracked.c", "int tracked;\n")
        self.put(self.origin / "module.py", "value = 1\n")
        self.put(self.origin / "manifest.py", "freeze('.', 'module.py')\n")
        self.commit(self.origin)
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(self.origin), "lib/dependency")
        self.commit(self.repo)
        self.dependency = self.repo / "lib/dependency"
        self.pin = self.git(self.dependency, "rev-parse", "HEAD")

    @staticmethod
    def git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    @staticmethod
    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, repo):
        self.git(repo, "add", ".")
        self.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def document(self, source, frozen=False):
        frozen_inputs = []
        if frozen:
            generated = self.put(self.repo / "build/frozen_content.c", "/* generated */\n")
            export = self.put(self.repo / "build/frozen.json", json.dumps({
                "frozen_content": {
                    "path": str(generated), "build_dir": str(generated.parent),
                    "sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
                },
                "files": [{"source": str(source), "manifest": str(self.dependency / "manifest.py"),
                           "target": "module.py", "kind": "freeze_as_mpy", "package": "dependency"}],
            }))
            frozen_inputs.append(str(export))
            source = generated
        sources = source if isinstance(source, list) else [source]
        database = self.put(self.repo / "build/compile_commands.json", json.dumps([{
            "directory": str(self.repo), "file": str(item), "command": "cc -c " + str(item),
        } for item in sources]))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=frozen_inputs,
                               root=[], image="test-image", offline=True)
        return _build_document(self.repo, args)

    def test_clean_selected_sources_use_checked_out_gitlink(self):
        for filename, frozen in (("tracked.c", False), ("module.py", True)):
            with self.subTest(frozen=frozen):
                document = self.document(self.dependency / filename, frozen=frozen)
                self.assertEqual(len(document["components"]), 1)
                component = document["components"][0]
                self.assertEqual(component["revision"], self.pin)
                self.assertFalse(component.get("unreviewed", False))
                self.assertEqual(component["sources"], ["lib/dependency/" + filename])

    def test_selected_source_rejects_checkout_different_from_gitlink(self):
        self.put(self.dependency / "tracked.c", "int changed;\n")
        self.commit(self.dependency)
        self.assertNotEqual(self.git(self.dependency, "rev-parse", "HEAD"), self.pin)
        for filename, frozen in (("tracked.c", False), ("module.py", True)):
            with self.subTest(frozen=frozen):
                with self.assertRaisesRegex(ValueError, "lib/dependency"):
                    self.document(self.dependency / filename, frozen=frozen)

    def test_untracked_selected_sources_are_not_pinned(self):
        for filename, frozen in (("extra.c", False), ("extra.py", True)):
            with self.subTest(frozen=frozen):
                source = self.put(self.dependency / filename, "# untracked\n" if frozen else "int extra;\n")
                document = self.document(source, frozen=frozen)
                self.assertEqual(len(document["components"]), 1)
                component = document["components"][0]
                self.assertTrue(component.get("unreviewed", False))
                self.assertIsNone(component["revision"])
                self.assertTrue(any(filename in issue for issue in document["issues"]))

    def test_clean_and_untracked_sources_have_separate_identity(self):
        untracked = self.put(self.dependency / "extra.c", "int extra;\n")
        document = self.document([self.dependency / "tracked.c", untracked])
        components = {tuple(component["sources"]): component for component in document["components"]}
        clean = components[("lib/dependency/tracked.c",)]
        dirty = components[("lib/dependency/extra.c",)]
        self.assertEqual(clean["revision"], self.pin)
        self.assertFalse(clean.get("unreviewed", False))
        self.assertIsNone(dirty["revision"])
        self.assertTrue(dirty.get("unreviewed", False))
        self.assertTrue(any("extra.c" in issue for issue in document["issues"]))


if __name__ == "__main__":
    unittest.main()