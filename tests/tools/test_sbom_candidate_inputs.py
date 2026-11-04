#!/usr/bin/env python3
"""Tests for build-input selection and frozen package ownership."""

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

from manifestfile import KIND_FREEZE_AS_MPY, ManifestFile, MODE_FREEZE
from sbom import _build_document
from sbom_inputs import read_inputs


class TestSBOMInputs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.put(".gitmodules", "")
        self.put("src/tracked.c", "int tracked;\n")
        self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout

    def put(self, path, text):
        path = self.repo / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-qm", "fixture")

    def compdb(self, entries, name="build/compile_commands.json"):
        return self.put(name, json.dumps(entries))

    def frozen_fixture(self, files):
        generated = self.put("build/frozen_content.c", "/* generated */\n")
        db = self.compdb([{"directory": str(generated.parent), "file": generated.name}])
        export = self.put("build/frozen.json", json.dumps({
            "frozen_content": {
                "path": str(generated),
                "build_dir": str(generated.parent),
                "sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
            },
            "files": files,
        }))
        return generated, db, export

    def document(self, compdb, *, excludes=(), frozen=()):
        args = SimpleNamespace(compdb=[str(compdb)], exclude=list(excludes),
                               frozen=list(map(str, frozen)), root=[], image="test-image",
                               offline=True)
        return _build_document(self.repo, args)

    def test_relative_sources_duplicates_and_host_exclusion(self):
        self.put("host/tool.c", "int tool;\n")
        self.put("hosted/keep.c", "int keep;\n")
        self.commit()
        db = self.compdb([
            {"directory": "..", "file": "src/tracked.c"},
            {"directory": str(self.repo / "src"), "file": "./tracked.c"},
            {"directory": str(self.repo), "file": "host/tool.c"},
            {"directory": str(self.repo / "host"), "file": "../src/tracked.c"},
            {"directory": str(self.repo), "file": "hosted/keep.c"},
        ])
        records = read_inputs(self.repo, [db, db], ["host"], [])
        self.assertEqual({record["source"] for record in records}, {
            self.repo / "src/tracked.c", self.repo / "hosted/keep.c",
        })
        self.assertEqual(len(records), 2)
        document = self.document(db, excludes=["host"])
        sources = [source for component in document["components"] for source in component["sources"]]
        self.assertCountEqual(sources, ["src/tracked.c", "hosted/keep.c"])

    def test_external_and_untracked_sources_are_preserved(self):
        untracked = self.put("custom/untracked.c", "int custom;\n")
        external = self.base / "external" / "external.c"
        external.parent.mkdir()
        external.write_text("int external;\n", encoding="utf-8")
        db = self.compdb([
            {"directory": str(self.repo), "file": "src/tracked.c"},
            {"directory": str(self.repo), "file": str(untracked)},
            {"directory": str(external.parent), "file": external.name},
        ])
        document = self.document(db)
        components = document["components"]
        sources = {source for component in components for source in component["sources"]}
        self.assertEqual(sources, {"src/tracked.c", "custom/untracked.c", str(external)})
        custom = next(c for c in components if "custom/untracked.c" in c["sources"])
        self.assertTrue(custom.get("unreviewed"))
        self.assertIsNone(custom["licence"])
        self.assertTrue(any("unreviewed source root" in issue for issue in document["issues"]))
        self.assertTrue(any("unmatched source" in issue and str(external) in issue
                            for issue in document["issues"]))

    def test_generated_frozen_source_requires_export(self):
        generated, db, export = self.frozen_fixture([])
        with self.assertRaisesRegex(ValueError, r"frozen_content\.c.*--frozen"):
            self.document(db)
        missing = self.repo / "build/missing-frozen.json"
        with self.assertRaisesRegex(ValueError, "cannot read input"):
            self.document(db, frozen=[missing])
        self.assertTrue(generated.is_file())
        self.assertEqual(self.document(db, frozen=[export])["components"], [])

    def test_frozen_source_rejects_mismatched_digest(self):
        generated, db, export = self.frozen_fixture([])
        generated.write_text("/* changed after export */\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"frozen_content\.c digest does not match"):
            self.document(db, frozen=[export])

    def test_frozen_source_rejects_ambiguous_compilation_commands(self):
        generated, db, export = self.frozen_fixture([])
        self.compdb([
            {"directory": str(generated.parent), "file": generated.name, "output": "a.o"},
            {"directory": str(generated.parent), "file": generated.name, "output": "b.o"},
        ])
        with self.assertRaisesRegex(ValueError, r"multiple frozen_content\.c targets or commands"):
            self.document(db, frozen=[export])

    def test_required_packages_group_files_and_keep_declaring_manifest(self):
        top = self.put("manifest.py", 'add_library("local", "packages")\nrequire("alpha", library="local")\n')
        alpha_manifest = self.put(
            "packages/alpha/manifest.py",
            'metadata(version="1.2", license="MIT")\n'
            'require("beta", library="local")\n'
            'module("first.py")\ninclude("extra.py")\n',
        )
        self.put("packages/alpha/extra.py", 'module("second.py")\n')
        self.put("packages/alpha/first.py", "x = 1\n")
        self.put("packages/alpha/second.py", "x = 2\n")
        beta_manifest = self.put(
            "packages/beta/manifest.py", 'metadata(version="2.0", license="MIT")\nmodule("beta.py")\n',
        )
        self.put("packages/beta/beta.py", "x = 3\n")
        self.commit()
        manifest = ManifestFile(MODE_FREEZE, {"MPY_DIR": str(self.repo)})
        manifest.execute(str(top))
        files = manifest.files()
        self.assertEqual(len(files), 3)
        by_name = {Path(item.full_path).name: item for item in files}
        for name in ("first.py", "second.py"):
            self.assertEqual(by_name[name].metadata.require_name, "alpha")
            self.assertEqual(Path(by_name[name].metadata.manifest_path), alpha_manifest)
        self.assertEqual(by_name["beta.py"].metadata.require_name, "beta")
        self.assertEqual(Path(by_name["beta.py"].metadata.manifest_path), beta_manifest)
        self.assertTrue(all(item.kind == KIND_FREEZE_AS_MPY for item in files))
        _, db, export = self.frozen_fixture([
            {"source": str(Path(item.full_path).resolve()), "target": item.target_path,
             "kind": "freeze_as_mpy", "package": item.metadata.require_name,
             "manifest": item.metadata.manifest_path, "version": item.metadata.version,
             "license": item.metadata.license}
            for item in files
        ])
        document = self.document(db, frozen=[export])
        components = document["components"]
        self.assertEqual(len(components), 2)
        alpha = next(c for c in components if c["name"] == "alpha")
        beta = next(c for c in components if c["name"] == "beta")
        self.assertEqual(alpha["manifest"], "packages/alpha/manifest.py")
        self.assertEqual(alpha["version"], "1.2")
        self.assertCountEqual(alpha["sources"], ["packages/alpha/first.py", "packages/alpha/second.py"])
        self.assertEqual(len(alpha["selection"]), 2)
        self.assertTrue(all(selection.endswith(":require:alpha") for selection in alpha["selection"]))
        self.assertEqual(beta["manifest"], "packages/beta/manifest.py")
        self.assertEqual(beta["sources"], ["packages/beta/beta.py"])
        self.assertTrue(beta["selection"][0].endswith(":require:beta"))


if __name__ == "__main__":
    unittest.main()
