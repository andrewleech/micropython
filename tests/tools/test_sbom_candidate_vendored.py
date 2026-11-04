#!/usr/bin/env python3
"""Tests for scoped vendored identities and binary frozen sources."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from sbom import _build_document


class TestCandidateVendored(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.git("init", "-q")
        self.put(".gitmodules", "")
        for path, text in {
            "lib/littlefs/lfs1.c": "int lfs1;\n",
            "lib/littlefs/lfs1.h": "#define LFS1_VERSION 0x00010007\n",
            "lib/littlefs/lfs2.c": "int lfs2;\n",
            "lib/littlefs/lfs2.h": "#define LFS2_VERSION 0x0002000b\n",
            "lib/oofatfs/ff.c": "int ff;\n",
            "lib/oofatfs/ff.h": "/* FatFs - Generic FAT Filesystem module R0.13c */\n#define FF_DEFINED 86604\n",
            "lib/oofatfs/ffunicode.c": "int ffunicode;\n",
            "lib/oofatfs/diskio.h": "/* diskio */\n",
            "lib/oofatfs/ffconf.h": "/* ffconf */\n",
        }.items():
            self.put(path, text)
        self.put("frozen/binary.mpy", b"M\x06\x00\x1f\xff\xfe\x80\x00")
        self.git("add", ".")
        tree = self.git("write-tree")

        with (REPO / "LICENSE.spdx.yaml").open(encoding="utf-8") as file:
            declarations = yaml.safe_load(file)
        scopes = {"lib/littlefs", "lib/oofatfs"}
        packages = [item for item in declarations["packages"]
                    if item["packageFileName"].startswith(("lib/littlefs", "lib/oofatfs"))]
        for item in packages:
            if item["packageFileName"] in scopes:
                pin = self.git("rev-parse", tree + ":" + item["packageFileName"])
                item["externalRefs"][0]["referenceLocator"] = "gitoid:tree:sha1:" + pin
        packages.extend([
            {"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"},
            {"name": "binary", "packageFileName": "frozen/binary.mpy", "licenseDeclared": "MIT"},
        ])
        self.put("LICENSE.spdx.yaml", yaml.safe_dump({"packages": packages}))
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def put(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def document(self, sources, frozen=None):
        entries = []
        for index, source in enumerate(sources):
            obj = f"build/fixture-{index}.o"
            dep = f"build/fixture-{index}.P"
            escaped = str(source).replace("\\", "\\\\").replace(" ", "\\ ").replace("#", "\\#").replace("$", "$$")
            self.put(dep, f"{obj}: {escaped}\n")
            entries.append({"directory": str(self.repo), "file": str(source),
                            "arguments": ["cc", "-c", str(source), "-o", obj, "-MF", dep]})
        database = self.put("build/compile_commands.json", json.dumps(entries))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=frozen or [], root=[],
                               image="test-image", offline=True)
        return _build_document(self.repo, args)

    def test_scoped_vendored_sources_keep_identity_and_licence(self):
        expected = {
            "lib/littlefs/lfs1.c": ("1.7", "BSD-3-Clause"),
            "lib/littlefs/lfs2.c": ("2.11", "BSD-3-Clause"),
            "lib/oofatfs/ff.c": ("R0.13c-rev86604", "BSD-1-Clause"),
            "lib/oofatfs/ff.h": ("R0.13c-rev86604", "BSD-1-Clause"),
            "lib/oofatfs/ffunicode.c": ("R0.13c-rev86604", "BSD-1-Clause"),
            "lib/oofatfs/diskio.h": ("R0.13c-rev86604", "BSD-1-Clause"),
            "lib/oofatfs/ffconf.h": ("R0.13c-rev86604", "MIT"),
        }
        document = self.document([self.repo / path for path in expected])
        by_source = {source: item for item in document["components"] for source in item["sources"]}
        self.assertEqual(set(by_source), set(expected))
        for source, (version, licence) in expected.items():
            with self.subTest(source=source):
                item = by_source[source]
                self.assertEqual(item["version"], version)
                self.assertIn({"name": "version:source", "value": "vendored-in-tree"},
                              item["properties"])
                upstream = ("https://github.com/littlefs-project/littlefs"
                            if source.startswith("lib/littlefs/") else
                            "https://github.com/micropython/oofatfs")
                self.assertIn({"type": "vcs", "url": upstream}, item["externalReferences"])
                self.assertEqual(item["licence_declared"], licence)
                self.assertEqual(item["licence"], licence)
                self.assertEqual(item["licence_status"], "resolved")
                self.assertIn("native", item["selection"])
        self.assertEqual(document["issues"], [])

    def test_frozen_mpy_binary_keeps_covered_declaration(self):
        source = self.repo / "frozen/binary.mpy"
        frozen_content = self.put("build/frozen_content.c", "/* generated */\n")
        capture = self.put("build/frozen.json", json.dumps({
            "frozen_content": {
                "path": str(frozen_content),
                "build_dir": str(frozen_content.parent),
                "sha256": hashlib.sha256(frozen_content.read_bytes()).hexdigest(),
            },
            "files": [{"source": str(source), "target": "binary.mpy", "kind": "freeze_mpy"}],
        }))
        document = self.document([frozen_content], [str(capture)])
        self.assertEqual(len(document["components"]), 1)
        item = document["components"][0]
        self.assertEqual(item["sources"], ["frozen/binary.mpy"])
        self.assertEqual(item["licence_declared"], "MIT")
        self.assertEqual(item["licence"], "MIT")
        self.assertEqual(item["licence_status"], "resolved")
        self.assertEqual(document["issues"], [])


if __name__ == "__main__":
    unittest.main()
