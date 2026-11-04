#!/usr/bin/env python3
"""Tests for reviewed exceptions and conflicting source licence evidence."""

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
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestCandidatePolicyConflicts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")

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

    def frozen_fixture(self, header="", invalid_pin=False):
        origin = self.base / "origin"
        origin.mkdir()
        self.git(origin, "init", "-q")
        self.put(origin, "python-stdlib/uu/uu.py", header + "value = 1\n")
        self.put(origin, "python-stdlib/uu/manifest.py", "freeze('.', 'uu.py')\n")
        self.commit(origin)
        pin = self.git(origin, "rev-parse", "HEAD")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(origin), "lib/micropython-lib")
        library = self.repo / "lib/micropython-lib"
        self.git(library, "remote", "set-url", "origin", "https://github.com/micropython/micropython-lib.git")
        reviewed = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))
        packages = [dict(item) for item in reviewed["packages"]
                    if item["name"] in {"micropython", "micropython-lib", "uu"}]
        library_entry = next(item for item in packages if item["name"] == "micropython-lib")
        library_entry["downloadLocation"] = (
            "git+https://github.com/micropython/micropython-lib.git@"
            + ("0" * 40 if invalid_pin else pin)
        )
        texts = [item for item in reviewed["hasExtractedLicensingInfos"]
                 if item["licenseId"] == "LicenseRef-Ellinghouse-UU-Permission-Notice"]
        declaration = self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump({
            "packages": packages, "hasExtractedLicensingInfos": texts,
        }))
        self.commit(self.repo)
        return library, declaration

    def frozen_document(self, library):
        source = library / "python-stdlib/uu/uu.py"
        manifest = library / "python-stdlib/uu/manifest.py"
        generated = self.put(self.repo, "build/frozen_content.c", "/* generated */\n")
        export = self.put(self.repo, "build/frozen.json", json.dumps({
            "frozen_content": {"path": str(generated), "build_dir": str(generated.parent),
                               "sha256": hashlib.sha256(generated.read_bytes()).hexdigest()},
            "files": [{"source": str(source), "manifest": str(manifest), "target": "uu.py",
                       "kind": "freeze_as_mpy", "package": "uu"}],
        }))
        dep = self.put(self.repo, "build/frozen.P", f"build/frozen.o: {generated}\n")
        database = self.put(self.repo, "build/compile_commands.json", json.dumps([{
            "directory": str(self.repo), "file": str(generated),
            "arguments": ["cc", "-c", str(generated), "-o", "build/frozen.o", "-MF", str(dep)],
        }]))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[str(export)], root=[],
                               image="test-image", offline=True)
        document = _build_document(self.repo, args)
        component = next(item for item in document["components"] if item["name"] == "uu")
        return document, component

    def assert_no_generic_mit(self, document, component):
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["file_licences"][0]["expression"])
        self.assertFalse(component["file_licences"][0]["trusted"])
        output = self.repo / "build/out.spdx.json"
        write_sbom(document, output, "spdx", root=self.repo)
        packages = json.loads(output.read_text(encoding="utf-8"))["packages"]
        package = next(item for item in packages if item["name"] == "uu")
        self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        self.assertEqual(package["licenseConcluded"], "NOASSERTION")

    def test_dirty_declarations_do_not_fall_back_to_generic_mit(self):
        library, declaration = self.frozen_fixture()
        _, component = self.frozen_document(library)
        self.assertEqual(component["licence_declared"],
                         "Python-2.0 AND LicenseRef-Ellinghouse-UU-Permission-Notice")
        declaration.write_text(declaration.read_text(encoding="utf-8") + "\n# unreviewed change\n", encoding="utf-8")
        document, component = self.frozen_document(library)
        self.assertTrue(any("dirty" in issue and "LICENSE.spdx.yaml" in issue for issue in document["issues"]))
        self.assert_no_generic_mit(document, component)

    def test_invalid_pin_does_not_fall_back_to_generic_mit(self):
        library, _ = self.frozen_fixture(invalid_pin=True)
        document, component = self.frozen_document(library)
        self.assertIn("pin_mismatch", {issue["kind"] for issue in component["licence_issues"]})
        self.assert_no_generic_mit(document, component)

    def test_independent_source_spdx_survives_dirty_declarations(self):
        library, declaration = self.frozen_fixture(header="# SPDX-License-Identifier: Apache-2.0\n")
        declaration.write_text(declaration.read_text(encoding="utf-8") + "\n# unreviewed change\n", encoding="utf-8")
        document, component = self.frozen_document(library)
        self.assertTrue(any("dirty" in issue for issue in document["issues"]))
        file_licence = component["file_licences"][0]
        self.assertEqual(file_licence["expression"], "Apache-2.0")
        self.assertEqual(file_licence["provenance"], "source SPDX header")
        self.assertTrue(file_licence["trusted"])
        self.assertEqual(component["licence_declared"], "Apache-2.0")

    def test_source_spdx_conflicts_with_reviewed_explicit_scope(self):
        source = self.put(self.repo, "ports/mimxrt/hal/flexspi_nor_flash.c",
                          "// SPDX-License-Identifier: BSD-3-Clause\nint x;\n")
        reviewed = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))
        packages = [item for item in reviewed["packages"]
                    if item["name"] in {"micropython", "mimxrt-flexspi-nor-flash"}]
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump({"packages": packages}))
        self.commit(self.repo)
        component = {"name": "flexspi", "path": ".", "owner_root": str(self.repo),
                     "sources": [str(source)], "version": None}
        issues = resolve_licences(self.repo, [component])
        self.assertEqual(component["file_licences"][0]["expression"], "BSD-3-Clause")
        self.assertEqual(component["file_licences"][0]["provenance"], "source SPDX header")
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["licence"])
        self.assertTrue(issues)
        conflict = json.dumps(component["licence_issues"])
        self.assertFalse(component["file_licences"][0]["trusted"])
        self.assertIn("conflict", conflict.lower())
        self.assertIn("BSD-3-Clause", conflict)
        self.assertIn("MIT", conflict)


if __name__ == "__main__":
    unittest.main()
