#!/usr/bin/env python3
"""Regression tests for frozen manifest evidence and incremental generation."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from sbom import _build_document
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestFrozenManifestTrust(unittest.TestCase):
    def test_sidecar_manifest_licence_requires_clean_tracked_manifest(self):
        for state in ("clean", "dirty", "untracked"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp).resolve()

                def put(name, text):
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                    return path

                def git(*args):
                    subprocess.run(["git", "-C", str(repo), *args], check=True,
                                   capture_output=True)

                git("init", "-q")
                put(".gitmodules", "")
                source = put("packages/alpha/alpha.py", "value = 1\n")
                manifest_text = 'metadata(license="Apache-2.0")\n'
                if state != "untracked":
                    put("packages/alpha/manifest.py", manifest_text if state == "clean"
                        else 'metadata(license="MIT")\n')
                git("add", ".")
                git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
                manifest = put("packages/alpha/manifest.py", manifest_text)
                generated = put("build/frozen_content.c", "/* generated */\n")
                database = put("build/compile_commands.json", json.dumps([
                    {"directory": str(generated.parent), "file": generated.name},
                ]))
                sidecar = put("build/frozen.json", json.dumps({
                    "frozen_content": {
                        "path": str(generated),
                        "build_dir": str(generated.parent),
                        "sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
                    },
                    "files": [{
                        "source": str(source), "target": "alpha.py", "kind": "freeze_as_mpy",
                        "package": "alpha", "manifest": str(manifest), "license": "Apache-2.0",
                    }],
                }))
                args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[str(sidecar)],
                                       root=[], image="test-image", offline=True)
                document = _build_document(repo, args)
                component = next(item for item in document["components"] if item["name"] == "alpha")
                output = repo / "build/out.spdx.json"
                write_sbom(document, output, "spdx")
                packages = json.loads(output.read_text(encoding="utf-8"))["packages"]
                package = next(item for item in packages if item["name"] == "alpha")
                if state == "clean":
                    file_licence = component["file_licences"][0]
                    self.assertTrue(file_licence["trusted"])
                    self.assertEqual(file_licence["expression"], "Apache-2.0")
                    self.assertEqual(component["licence_declared"], "Apache-2.0")
                    self.assertEqual(package["licenseDeclared"], "Apache-2.0")
                else:
                    self.assertFalse(any(item["trusted"] for item in component.get("file_licences", [])))
                    self.assertIsNone(component["licence_declared"])
                    self.assertIsNone(component["licence"])
                    self.assertEqual(package["licenseDeclared"], "NOASSERTION")

                # Exercise the licence resolver even when source identity prevents
                # the document builder from resolving the component.
                record = json.loads(sidecar.read_text(encoding="utf-8"))["files"][0]
                resolved = {
                    "name": record["package"], "kind": "frozen",
                    "path": str(manifest.parent), "owner_root": str(repo),
                    "sources": [record["source"]], "manifest": record["manifest"],
                    "manifest_license": record["license"],
                }
                resolve_licences(repo, [resolved], metadata_status=lambda path: (
                    None if state == "clean" else state
                ))
                self.assertEqual(resolved["file_licences"][0]["trusted"], state == "clean")
                self.assertEqual(resolved["licence_declared"], "Apache-2.0" if state == "clean" else None)

    def test_null_manifest_licence_cannot_fall_back_to_root_default(self):
        for state in ("dirty", "untracked"):
            for manifest_name in ("manifest.py", "included/metadata.py"):
                for root_default in (False, True):
                    with self.subTest(state=state, manifest=manifest_name, root_default=root_default), \
                            tempfile.TemporaryDirectory() as tmp:
                        repo = Path(tmp).resolve()

                        def put(name, text):
                            path = repo / name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(text, encoding="utf-8")
                            return path

                        def git(*args):
                            subprocess.run(["git", "-C", str(repo), *args], check=True,
                                           capture_output=True)

                        git("init", "-q")
                        put(".gitmodules", "")
                        source = put("modules/alpha.py", "value = 1\n")
                        if root_default:
                            put("LICENSE.spdx.yaml",
                                "packages:\n  - name: root\n    packageFileName: .\n"
                                "    licenseDeclared: MIT\n")
                        if state == "dirty":
                            put(manifest_name, 'metadata(license="MIT")\n')
                        git("add", ".")
                        git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
                        put(manifest_name, 'metadata(license="Apache-2.0")\n')
                        generated = put("build/frozen_content.c", "/* generated */\n")
                        database = put("build/compile_commands.json", json.dumps([
                            {"directory": str(generated.parent), "file": generated.name},
                        ]))
                        sidecar = put("build/frozen.json", json.dumps({
                            "frozen_content": {
                                "path": str(generated), "build_dir": str(generated.parent),
                                "sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
                            },
                            "files": [{
                                "source": str(source), "target": "alpha.py", "kind": "freeze_as_mpy",
                                "package": "alpha", "manifest": None, "license": "Apache-2.0",
                            }],
                        }))
                        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[str(sidecar)],
                                               root=[], image="test-image", offline=True)
                        document = _build_document(repo, args)
                        component = next(item for item in document["components"] if item["name"] == "alpha")
                        self.assertFalse(any(item["trusted"] for item in component.get("file_licences", [])))
                        self.assertIsNone(component["licence_declared"])
                        self.assertIsNone(component["licence"])
                        self.assertTrue(any(issue["kind"] == "source_identity"
                                            for issue in component["licence_issues"]))
                        output = repo / "build/out.spdx.json"
                        write_sbom(document, output, "spdx")
                        packages = json.loads(output.read_text(encoding="utf-8"))["packages"]
                        package = next(item for item in packages if item["name"] == "alpha")
                        self.assertEqual(package["licenseDeclared"], "NOASSERTION")


class TestFrozenGenerationTimestamp(unittest.TestCase):
    def test_unchanged_content_is_refreshed_after_newer_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            build = root / "build"
            build.mkdir()
            modules = root / "modules"
            modules.mkdir()
            source = modules / "module.py"
            source.write_text("value = 1\n", encoding="utf-8")
            manifest = root / "manifest.py"
            manifest.write_text(f"freeze_as_str({str(modules)!r})\n", encoding="utf-8")
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
            now = time.time_ns()
            os.utime(output, ns=(now - 120_000_000_000, now - 120_000_000_000))
            os.utime(source, ns=(now - 60_000_000_000, now - 60_000_000_000))
            self.assertLess(output.stat().st_mtime_ns, source.stat().st_mtime_ns)

            subprocess.run(command, check=True, env=env, capture_output=True)
            refreshed = output.stat().st_mtime_ns
            self.assertGreaterEqual(refreshed, source.stat().st_mtime_ns)
            self.assertEqual(output.read_bytes(), content)
            subprocess.run(command, check=True, env=env, capture_output=True)
            self.assertEqual(output.stat().st_mtime_ns, refreshed)
            self.assertEqual(output.read_bytes(), content)
            data = json.loads(export.read_text(encoding="utf-8"))
            self.assertEqual(data["frozen_content"]["sha256"], hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    unittest.main()
