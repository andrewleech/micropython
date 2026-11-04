#!/usr/bin/env python3
"""Tests for excluding compilation commands with frozen content."""

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


class TestFrozenExclusion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.put(".gitmodules", "")
        self.put("packages/alpha/manifest.py", 'metadata(version="1.2", license="MIT")\n')
        source = self.put("packages/alpha/alpha.py", "value = 1\n")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-qm", "fixture")

        generated = self.put("build/frozen_content.c", "/* generated */\n")
        self.database = self.put("build/compile_commands.json", json.dumps([
            {"directory": str(generated.parent), "file": generated.name},
        ]))
        self.sidecar = self.put("build/frozen.json", json.dumps({
            "frozen_content": {
                "path": str(generated),
                "build_dir": str(generated.parent),
                "sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
            },
            "files": [{
                "source": str(source),
                "target": "alpha.py",
                "kind": "freeze_as_mpy",
                "package": "alpha",
                "manifest": str(self.repo / "packages/alpha/manifest.py"),
                "version": "1.2",
                "license": "MIT",
            }],
        }))

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def put(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def document(self, *, excludes=(), frozen=()):
        args = SimpleNamespace(compdb=[str(self.database)], exclude=list(excludes),
                               frozen=list(map(str, frozen)), root=[], image="test-image",
                               offline=True)
        return _build_document(self.repo, args)

    def test_matching_sidecar_includes_frozen_package_without_exclusion(self):
        document = self.document(frozen=[self.sidecar])
        alpha = next(component for component in document["components"]
                     if component["name"] == "alpha")
        self.assertEqual(alpha["sources"], ["packages/alpha/alpha.py"])
        self.assertEqual(alpha["version"], "1.2")

    def test_excluded_frozen_command_is_rejected_with_or_without_sidecar(self):
        for exclude in ("build", str(self.repo / "build"), "build/frozen_content.c"):
            for frozen in ((), (self.sidecar,)):
                with self.subTest(exclude=exclude, sidecar=bool(frozen)):
                    with self.assertRaisesRegex(ValueError, r"excluded frozen_content\.c"):
                        self.document(excludes=[exclude], frozen=frozen)

    def test_unexcluded_frozen_command_requires_sidecar(self):
        with self.assertRaisesRegex(ValueError, r"frozen_content\.c.*--frozen"):
            self.document()


if __name__ == "__main__":
    unittest.main()
