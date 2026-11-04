#!/usr/bin/env python3
"""Tests for selected component scopes and their licence ownership."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from sbom import _build_document, _component


class TestCandidateScopes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.put(".gitmodules", "")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def put(self, path, text):
        path = self.repo / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def declarations(self, packages):
        self.put("LICENSE.spdx.yaml", yaml.safe_dump({"packages": packages}))

    def document(self, sources, frozen=()):
        entries = []
        for index, source in enumerate(sources):
            obj = f"build/fixture-{index}.o"
            dep = f"build/fixture-{index}.P"
            escaped = str(source).replace("\\", "\\\\").replace(" ", "\\ ").replace("#", "\\#").replace("$", "$$")
            self.put(dep, f"{obj}: {escaped}\n")
            entries.append({"directory": str(self.repo), "file": str(source),
                            "arguments": ["cc", "-c", str(source), "-o", obj, "-MF", dep]})
        db = self.put("build/compile_commands.json", json.dumps(entries))
        args = SimpleNamespace(compdb=[str(db)], exclude=[], frozen=list(map(str, frozen)),
                               root=[], image="test-image", offline=True)
        return _build_document(self.repo, args)

    def test_native_directory_scopes_are_distinct_and_licensed(self):
        alpha = self.put("src/alpha/a.c", "int a;\n")
        alpha2 = self.put("src/alpha/b.c", "int b;\n")
        beta = self.put("src/beta/c.c", "int c;\n")
        base = self.put("src/base.c", "int base;\n")
        self.git("add", "src")
        tree = self.git("write-tree")
        packages = [{"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"}]
        for name, licence in (("alpha", "Apache-2.0"), ("beta", "BSD-3-Clause")):
            scope = "src/" + name
            pin = self.git("rev-parse", tree + ":" + scope)
            packages.append({"name": name, "packageFileName": scope, "licenseDeclared": licence,
                             "externalRefs": [{"referenceCategory": "PERSISTENT-ID",
                                               "referenceType": "gitoid",
                                               "referenceLocator": "gitoid:tree:sha1:" + pin}]})
        self.declarations(packages)
        self.commit()

        document = self.document([alpha, alpha2, beta, base])
        components = {item["name"]: item for item in document["components"]}
        self.assertEqual(set(components), {"alpha", "beta", "micropython"})
        for name, path, sources, licence in (
            ("alpha", "src/alpha", ["src/alpha/a.c", "src/alpha/b.c"], "Apache-2.0"),
            ("beta", "src/beta", ["src/beta/c.c"], "BSD-3-Clause"),
            ("micropython", ".", ["src/base.c"], "MIT"),
        ):
            component = components[name]
            self.assertEqual(component["path"], path)
            self.assertCountEqual(component["sources"], sources)
            self.assertEqual(component["selection"], ["native"])
            self.assertEqual(component["licence"], licence)
            self.assertEqual(component["licence_status"], "resolved")
        self.assertEqual(document["issues"], [])

    def test_frozen_submodule_never_inherits_root_mit(self):
        origin_temp = tempfile.TemporaryDirectory()
        self.addCleanup(origin_temp.cleanup)
        origin = Path(origin_temp.name).resolve()
        self.git("-C", str(origin), "init", "-q")
        (origin / "pkg").mkdir()
        (origin / "pkg/module.py").write_text("value = 1\n", encoding="utf-8")
        (origin / "pkg/manifest.py").write_text("freeze('.', 'module.py')\n", encoding="utf-8")
        self.git("-C", str(origin), "add", ".")
        self.git("-C", str(origin), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(origin), "lib/thirdparty")
        source = self.repo / "lib/thirdparty/pkg/module.py"
        manifest = self.repo / "lib/thirdparty/pkg/manifest.py"
        generated = self.put("build/frozen_content.c", "/* generated */\n")
        self.declarations([{"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"}])
        self.commit()
        export = self.put("build/frozen.json", json.dumps({
            "frozen_content": {"path": str(generated), "build_dir": str(generated.parent),
                               "sha256": hashlib.sha256(generated.read_bytes()).hexdigest()},
            "files": [{"source": str(source), "manifest": str(manifest), "target": "module.py",
                       "kind": "freeze_as_mpy", "package": "thirdparty-pkg"}],
        }))
        root = self.repo / "lib/thirdparty"
        document = self.document([generated], frozen=[export])
        self.assertEqual(len(document["components"]), 1)
        component = document["components"][0]
        self.assertEqual(component["name"], "thirdparty-pkg")
        self.assertEqual(component["owner_root"], str(root))
        self.assertEqual(component["sources"], ["lib/thirdparty/pkg/module.py"])
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertTrue(any(issue["kind"] == "missing_assertion" for issue in component["licence_issues"]))
        self.assertTrue(any("no asserted licence" in issue for issue in document["issues"]))

    def test_family_children_include_only_selected_sources_and_assess_licence(self):
        selected = self.put("vendor/families/F4/driver.c", "int f4;\n")
        other = self.put("vendor/families/F7/driver.c", "int f7;\n")
        common = self.put("vendor/common.c", "int common;\n")
        self.git("add", "vendor")
        tree = self.git("write-tree")
        pin = self.git("rev-parse", tree + ":vendor")
        self.declarations([
            {"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"},
            {"name": "vendor", "packageFileName": "vendor", "licenseDeclared": "Apache-2.0",
             "externalRefs": [{"referenceCategory": "PERSISTENT-ID", "referenceType": "gitoid",
                               "referenceLocator": "gitoid:tree:sha1:" + pin}]},
        ])
        self.commit()
        root = self.repo / "vendor"
        inventory = {root: [("vendored", {"name": "vendor", "path": "vendor"})]}
        parent = _component("vendor", "vendor")
        parent.update(owner_root=str(root), declaration_root=str(self.repo))
        parent["components"] = []
        for family in ("F4", "F7"):
            child = _component(family, "vendor/families/" + family)
            child.update(owner_root=str(root), declaration_root=str(self.repo))
            parent["components"].append(child)
        with patch("sbom._inventory", return_value=inventory), patch("sbom._build_inventory_component", return_value=parent):
            document = self.document([selected, common])
        component = document["components"][0]
        self.assertEqual(len(component["components"]), 1)
        child = component["components"][0]
        self.assertEqual(child["name"], "F4")
        self.assertEqual(child["sources"], ["vendor/families/F4/driver.c"])
        self.assertEqual(child["selection"], ["native"])
        self.assertEqual(child["licence"], "Apache-2.0")
        self.assertEqual(child["licence_status"], "resolved")
        self.assertNotIn(str(other.relative_to(self.repo)), component["sources"])


if __name__ == "__main__":
    unittest.main()
