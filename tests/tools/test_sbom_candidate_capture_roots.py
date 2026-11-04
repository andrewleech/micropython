#!/usr/bin/env python3
"""Tests for capture paths outside conventional build directories."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from sbom import _build_document


class TestCaptureRoots(unittest.TestCase):
    def test_database_parent_does_not_hide_included_source(self):
        make = shutil.which("make")
        cc = shutil.which("cc")
        if not make or not cc:
            self.skipTest("make and a C compiler are required")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()

            def put(name, contents):
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
                return path

            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            put(".gitmodules", "")
            put("LICENSE.spdx.yaml", "packages:\n  - name: root\n    packageFileName: .\n    licenseDeclared: MIT\n")
            put("ports/stm32/boards/main.c", '#include "board.c"\nint main(void) { return board(); }\n')
            put("ports/stm32/boards/board.c", "int board(void) { return 0; }\n")
            put("ports/stm32/Makefile", "build/main.o: boards/main.c boards/board.c\n"
                "\tmkdir -p build\n"
                "\t$(CC) -MMD -MF build/main.d -c boards/main.c -o build/main.o\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                            "user.email=test@example.com", "commit", "-qm", "Add fixture"],
                           check=True)
            port = repo / "ports/stm32"
            subprocess.run([make, "CC=" + cc], cwd=port, check=True, capture_output=True)
            database = put("ports/stm32/compile_commands.json", json.dumps([{
                "directory": str(port), "file": "boards/main.c",
                "arguments": [cc, "-MMD", "-MF", "build/main.d", "-c", "boards/main.c",
                              "-o", "build/main.o"],
            }]))
            args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[], root=[],
                                   image="test-image", offline=True)
            document = _build_document(repo, args)
            evidence = [item for component in document["components"]
                        for item in component["source_evidence"]]
            self.assertIn({"source": "ports/stm32/boards/board.c", "kind": "included",
                           "originating_source": "ports/stm32/boards/main.c",
                           "object": "ports/stm32/build/main.o",
                           "depfile": "ports/stm32/build/main.d"}, evidence)

    def test_nested_frozen_export_parent_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            build = root / "build"
            build.mkdir()
            manifest = root / "manifest.py"
            manifest.write_text("", encoding="utf-8")
            cross = root / "mpy-cross"
            cross.touch()
            output = build / "frozen_content.c"
            export = build / "nested" / "metadata" / "frozen.json"
            env = os.environ.copy()
            env["MICROPY_MPYCROSS"] = str(cross)
            subprocess.run([sys.executable, str(ROOT / "tools/makemanifest.py"),
                            "-o", str(output), "-b", str(build),
                            "-v", "MPY_DIR=" + str(ROOT), "-v", "PORT_DIR=" + str(root),
                            "--frozen-output", str(export), str(manifest)],
                           check=True, env=env, capture_output=True)
            data = json.loads(export.read_text(encoding="utf-8"))
            self.assertEqual(data["frozen_content"]["sha256"],
                             hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(data["files"], [])


if __name__ == "__main__":
    unittest.main()
