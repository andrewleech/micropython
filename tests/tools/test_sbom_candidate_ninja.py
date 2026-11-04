#!/usr/bin/env python3
"""Tests for Ninja dependency evidence and incremental frozen exports."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from sbom import _build_document
from sbom_inputs import read_inputs


class TestNinjaDependencies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.build = self.repo / "build"
        self.build.mkdir(parents=True)
        self.source = self.repo / "main.c"
        self.included = self.base / "build" / "sdk" / "included.c"
        self.included.parent.mkdir(parents=True)
        self.included.write_text("int included(void) { return 1; }\n", encoding="utf-8")
        self.source.write_text(
            '#include "{}"\nint main(void) {{ return included(); }}\n'.format(self.included),
            encoding="utf-8",
        )
        self.database = self.build / "compile_commands.json"
        self.database.write_text(json.dumps([{
            "directory": str(self.build),
            "file": str(self.source),
            "arguments": ["cc", "-MMD", "-MF", "main.o.d", "-c", str(self.source), "-o", "main.o"],
        }]), encoding="utf-8")

    def test_ninja_log_includes_external_source_under_build_directory(self):
        ninja = shutil.which("ninja")
        cc = shutil.which("cc")
        if not ninja or not cc:
            self.skipTest("ninja and a C compiler are required")
        (self.build / "build.ninja").write_text(
            "rule cc\n"
            "  command = {} -MMD -MF $out.d -c $in -o $out\n"
            "  depfile = $out.d\n"
            "  deps = gcc\n"
            "build main.o: cc {}\n".format(shlex.quote(cc), self.source),
            encoding="utf-8",
        )
        subprocess.run([ninja], cwd=self.build, check=True, capture_output=True)
        self.assertTrue((self.build / ".ninja_deps").is_file())
        self.assertFalse((self.build / "main.o.d").exists())
        evidence = []
        records = read_inputs(self.repo, [self.database], [], [], evidence)
        included = [record for record in records if record["source"] == self.included]
        self.assertEqual(len(included), 1)
        self.assertEqual(included[0]["originating_source"], self.source)
        self.assertEqual(included[0]["object"], self.build / "main.o")
        self.assertEqual(evidence[0]["status"], "read")
        self.assertEqual(evidence[0]["depfile"], self.build / ".ninja_deps")

    def test_missing_ninja_dependency_evidence_is_reported(self):
        (self.build / "build.ninja").write_text("", encoding="utf-8")
        evidence = []
        records = read_inputs(self.repo, [self.database], [], [], evidence)
        self.assertEqual({record["source"] for record in records}, {self.source})
        self.assertEqual(evidence[0]["status"], "missing")
        self.assertEqual(evidence[0]["depfile"], self.build / "main.o.d")

        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitmodules").write_text("", encoding="utf-8")
        (self.repo / "LICENSE.spdx.yaml").write_text(
            "packages:\n  - name: root\n    packageFileName: .\n    licenseDeclared: MIT\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "add", ".gitmodules", "LICENSE.spdx.yaml", "main.c"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=Test", "-c",
             "user.email=test@example.com", "commit", "-qm", "Add fixture sources"],
            check=True,
        )
        args = SimpleNamespace(compdb=[str(self.database)], exclude=[], frozen=[], root=[],
                               image="test-image", offline=True)
        document = _build_document(self.repo, args)
        self.assertTrue(any("depfile missing for main.c: build/main.o.d" in issue
                            for issue in document["issues"]))


class TestIncrementalFrozenExport(unittest.TestCase):
    def test_repeated_export_keeps_frozen_c_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            build = root / "build"
            build.mkdir()
            manifest = root / "manifest.py"
            manifest.write_text("", encoding="utf-8")
            cross = root / "mpy-cross"
            cross.touch()
            output = build / "frozen_content.c"
            export = build / "frozen.json"
            env = os.environ.copy()
            env["MICROPY_MPYCROSS"] = str(cross)
            command = [sys.executable, str(ROOT / "tools/makemanifest.py"),
                       "-o", str(output), "-b", str(build),
                       "-v", "MPY_DIR=" + str(ROOT), "-v", "PORT_DIR=" + str(root),
                       "--frozen-output", str(export), str(manifest)]
            subprocess.run(command, check=True, env=env, capture_output=True)
            content = output.read_bytes()
            # A fixed future mtime makes a rewrite detectable without sleeping.
            timestamp = 2_000_000_000_000_000_000
            os.utime(output, ns=(timestamp, timestamp))
            subprocess.run(command, check=True, env=env, capture_output=True)
            self.assertEqual(output.stat().st_mtime_ns, timestamp)
            self.assertEqual(output.read_bytes(), content)
            data = json.loads(export.read_text(encoding="utf-8"))
            self.assertEqual(data["frozen_content"]["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(data["files"], [])
            export.unlink()
            subprocess.run(command, check=True, env=env, capture_output=True)
            data = json.loads(export.read_text(encoding="utf-8"))
            self.assertEqual(data["frozen_content"]["sha256"],
                             hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
