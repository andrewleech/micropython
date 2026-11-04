#!/usr/bin/env python3

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from sbom_licences import resolve_licences


class TestLicenceResolution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.reviewed = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text())

    def write(self, root, path, content=""):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def declarations(self, root, names, extra=()):
        entries = [dict(item) for item in self.reviewed["packages"] if item["name"] in names]
        document = {
            "packages": entries + list(extra),
            "hasExtractedLicensingInfos": self.reviewed["hasExtractedLicensingInfos"],
        }
        self.write(root, "LICENSE.spdx.yaml", yaml.safe_dump(document, sort_keys=False))
        return document

    def save_declarations(self, document):
        self.write(self.root, "LICENSE.spdx.yaml", yaml.safe_dump(document, sort_keys=False))

    def git(self, *args):
        env = os.environ.copy()
        env.update(
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.com",
        )
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    def init_git(self):
        self.git("init", "-q")

    def commit(self):
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def component(self, name, path, sources, **extra):
        return {"name": name, "path": str(path), "sources": [str(s) for s in sources], **extra}

    def test_selected_in_tree_asyncio_inherits_root_mit(self):
        self.declarations(self.root, {"micropython"})
        source = self.write(self.root, "extmod/asyncio/core.py")
        component = self.component("asyncio", "extmod/asyncio", [source], kind="frozen", owner_root=self.root)

        self.assertEqual(resolve_licences(self.root, [component]), [])
        self.assertEqual(component["licence_declared"], "MIT")
        self.assertEqual(component["licence"], "MIT")

    def test_external_package_uses_its_declaration_root(self):
        self.declarations(self.root, {"micropython"})
        external = Path(self.temp.name) / "external"
        source = self.write(external, "package/mod.py")
        self.declarations(
            external,
            set(),
            [{"name": "external", "packageFileName": ".", "licenseDeclared": "Apache-2.0"}],
        )
        component = self.component(
            "package", external / "package", [source], kind="frozen", declaration_root=external
        )

        self.assertEqual(resolve_licences(self.root, [component]), [])
        self.assertEqual(component["licence_declared"], "Apache-2.0")
        self.assertEqual(component["licence"], "Apache-2.0")

    def test_external_package_without_declaration_does_not_inherit_mit(self):
        self.declarations(self.root, {"micropython"})
        external = Path(self.temp.name) / "external"
        source = self.write(external, "package/mod.py")
        component = self.component(
            "package", external / "package", [source], kind="frozen", declaration_root=external
        )

        issues = resolve_licences(self.root, [component])
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertTrue(any("no asserted licence" in issue for issue in issues), issues)

    def test_stm32_overlapping_scopes_keep_terms_and_restriction(self):
        self.init_git()
        source = self.write(self.root, "ports/stm32/usbdev/device.c", "source\n")
        self.git("add", "ports/stm32/usbdev/device.c")
        self.commit()
        tree = self.git("rev-parse", "HEAD:ports/stm32/usbdev")
        ref = "LicenseRef-MCD-ST-Liberty-SW-License-Agreement-V2"
        document = self.declarations(
            self.root,
            {"micropython", "stm32-usbdev"},
            [{"name": "device", "packageFileName": "ports/stm32/usbdev/device.c", "licenseDeclared": ref + " AND MIT"}],
        )
        parent = next(item for item in document["packages"] if item["name"] == "stm32-usbdev")
        parent["externalRefs"][0]["referenceLocator"] = "gitoid:tree:sha1:" + tree
        self.save_declarations(document)
        component = self.component("usbdev", "ports/stm32/usbdev", [source])

        self.assertEqual(resolve_licences(self.root, [component]), [])
        self.assertIn(ref, component["licence_declared"])
        self.assertIn("MIT", component["licence_declared"])
        self.assertIn(" AND ", component["licence_declared"])
        self.assertEqual(component["licence"], component["licence_declared"])
        self.assertIn(ref, component["licence_texts"])
        self.assertIn("http://www.st.com/software_license_agreement_liberty_v2", component["licence_texts"][ref])
        self.assertTrue(any("limits use to ST devices" in note for note in component["licence_comments"]))

    def berkeley_fixture(self, mismatch=False):
        self.init_git()
        self.write(self.root, "tracked", "fixture\n")
        self.git("add", "tracked")
        self.commit()
        pin = self.git("rev-parse", "HEAD")
        self.git("update-index", "--add", "--cacheinfo", "160000," + pin + ",lib/berkeley-db-1.xx")
        self.commit()
        source = self.write(self.root, "lib/berkeley-db-1.xx/db.c", "source\n")
        document = self.declarations(self.root, {"micropython", "berkeley-db-1.xx"})
        entry = next(item for item in document["packages"] if item["name"] == "berkeley-db-1.xx")
        entry["downloadLocation"] = "git+https://example.com/berkeley@" + ("0" * 40 if mismatch else pin)
        self.save_declarations(document)
        return self.component("berkeley", "lib/berkeley-db-1.xx", [source])

    def test_pinned_berkeley_retains_reviewed_declaration_with_unresolved_conflict(self):
        component = self.berkeley_fixture()

        issues = resolve_licences(self.root, [component])
        self.assertEqual(component["licence_declared"], "BSD-4-Clause")
        self.assertIsNone(component["licence"])
        self.assertTrue(any("unresolved licence conflict" in issue and "BSD-3-Clause" in issue for issue in issues), issues)
        self.assertTrue(any("BSD-3-Clause" in note for note in component["licence_comments"]))

    def test_mismatched_pin_never_asserts_a_licence(self):
        component = self.berkeley_fixture(mismatch=True)

        issues = resolve_licences(self.root, [component])
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertTrue(any("licence pin" in issue and "does not match" in issue for issue in issues), issues)

    def test_and_and_or_are_preserved(self):
        self.declarations(self.root, set())
        self.init_git()
        for expression in ("MIT AND Apache-2.0", "MIT OR Apache-2.0"):
            with self.subTest(expression=expression):
                source = self.write(self.root, "package/mod.py")
                manifest = self.write(self.root, "package/manifest.py", f"metadata(license={expression!r})\n")
                self.git("add", ".")
                self.commit()
                component = self.component("package", "package", [source], manifest_license=expression,
                                           manifest=str(manifest))
                self.assertEqual(resolve_licences(self.root, [component]), [])
                self.assertEqual(component["licence_declared"], expression)
                self.assertEqual(component["licence"], expression)

    def test_mixed_sources_keep_each_expression_and_provenance(self):
        first = self.write(self.root, "package/first.py")
        second = self.write(self.root, "package/second.py", "# SPDX-License-Identifier: BSD-3-Clause\n")
        self.declarations(
            self.root,
            set(),
            [{"name": "first", "packageFileName": "package/first.py", "licenseDeclared": "MIT OR Apache-2.0"}],
        )
        component = self.component("package", "package", [first, second])

        self.assertEqual(resolve_licences(self.root, [component]), [])
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "mixed")
        self.assertEqual(
            [(item["source"], item["expression"], item["provenance"], item["trusted"])
             for item in component["file_licences"]],
            [
                (str(first), "MIT OR Apache-2.0", "reviewed declaration: package/first.py", True),
                (str(second), "BSD-3-Clause", "source SPDX header", True),
            ],
        )

    def test_micropython_lib_default_is_limited_to_owned_package_sources(self):
        library = Path(self.temp.name) / "micropython-lib"
        package = library / "python-stdlib" / "example"
        owned = self.write(library, "python-stdlib/example/example.py", "pass\n")
        self.write(library, "python-stdlib/example/manifest.py", "freeze('.', 'example.py')\n")
        outside_package = self.write(library, "other/vendor.py", "pass\n")
        external = Path(self.temp.name) / "external"
        external_source = self.write(external, "python-stdlib/example/example.py", "pass\n")
        self.write(external, "python-stdlib/example/manifest.py", "freeze('.', 'example.py')\n")
        subprocess.run(["git", "init", "-q", str(library)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(library), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(library), "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
            check=True, capture_output=True,
        )
        revision = subprocess.run(
            ["git", "-C", str(library), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.init_git()
        self.git("update-index", "--add", "--cacheinfo", "160000," + revision + ",lib/micropython-lib")
        self.commit()
        subprocess.run(
            ["git", "-C", str(library), "remote", "add", "origin",
             "https://github.com/micropython/micropython-lib.git"],
            check=True, capture_output=True,
        )
        document = self.declarations(self.root, {"micropython", "micropython-lib"})
        entry = next(item for item in document["packages"] if item["name"] == "micropython-lib")
        entry["downloadLocation"] = "git+https://github.com/micropython/micropython-lib.git@" + revision
        self.save_declarations(document)
        components = [
            self.component("owned", package, [owned], kind="frozen", owner_root=library,
                           declaration_root=library, revision=revision),
            self.component("outside-package", package, [outside_package], kind="frozen",
                           owner_root=library, declaration_root=library, revision=revision),
            self.component("external", external / "python-stdlib/example", [external_source], kind="frozen",
                           owner_root=external, declaration_root=external),
        ]

        issues = resolve_licences(self.root, components)
        self.assertEqual(components[0]["licence"], "MIT")
        files = {item["source"]: item for item in components[0]["file_licences"]}
        self.assertEqual(files[str(owned)]["expression"], "MIT")
        self.assertEqual(files[str(owned)]["provenance"], "micropython-lib builder default")
        self.assertTrue(files[str(owned)]["trusted"])
        for component, source in zip(components[1:], (outside_package, external_source)):
            with self.subTest(component=component["name"]):
                self.assertIsNone(component["licence"])
                self.assertEqual(component["licence_status"], "unresolved")
                self.assertTrue(any(issue["kind"] == "missing_assertion" and issue.get("source") == str(source)
                                    for issue in component["licence_issues"]))
        self.assertEqual(len(issues), 2)


if __name__ == "__main__":
    unittest.main()
