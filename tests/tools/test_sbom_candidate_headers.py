#!/usr/bin/env python3

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from sbom_licences import resolve_licences


class TestFrozenSourceHeaders(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def declarations(self, *entries):
        self.write("LICENSE.spdx.yaml", yaml.safe_dump({"packages": list(entries)}))

    def component(self, *sources, **extra):
        return {
            "name": "package",
            "path": "package",
            "kind": "frozen",
            "owner_root": self.root,
            "sources": [str(source) for source in sources],
            **extra,
        }

    def test_explicit_mit_header_adds_source_evidence(self):
        self.declarations()
        source = self.write("package/mod.py", "# MIT license; Copyright 2024 Example\nprint('hello')\n")
        component = self.component(source, revision="abc123")

        self.assertEqual(resolve_licences(self.root, [component]), [])
        self.assertEqual(component["licence_declared"], "MIT")
        self.assertEqual(component["licence"], "MIT")
        self.assertEqual(component["licence_status"], "resolved")
        self.assertTrue(any(str(source) in note and "source header declares MIT" in note and "abc123" in note
                            for note in component["licence_comments"]))

    def test_absent_header_uses_path_declaration(self):
        self.declarations({"name": "package", "packageFileName": "package/mod.py", "licenseDeclared": "Apache-2.0"})
        source = self.write("package/mod.py", "# A normal comment\nprint('hello')\n")
        component = self.component(source)

        self.assertEqual(resolve_licences(self.root, [component]), [])
        self.assertEqual(component["licence_declared"], "Apache-2.0")
        self.assertEqual(component["licence"], "Apache-2.0")
        self.assertFalse(any("source header declares" in note for note in component["licence_comments"]))

    def test_source_spdx_header_precedes_path_and_package_metadata(self):
        self.declarations({"name": "package", "packageFileName": "package/mod.py", "licenseDeclared": "Apache-2.0"})
        source = self.write("package/mod.py", "# SPDX-License-Identifier: MIT\nprint('hello')\n")
        component = self.component(source, manifest_license="BSD-3-Clause")

        self.assertTrue(resolve_licences(self.root, [component]))
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertTrue(any(
            issue["kind"] == "metadata_conflict" and issue.get("source") == str(source)
            and issue.get("scope") == "package/mod.py"
            and set(issue.get("expressions", [])) == {"MIT", "Apache-2.0"}
            for issue in component["licence_issues"]
        ))
        self.assertEqual(
            [(item["source"], item["expression"], item["provenance"], item["trusted"])
             for item in component["file_licences"]],
            [(str(source), "MIT", "source SPDX header", False)],
        )

    def test_c_spdx_header_precedes_path_and_package_metadata(self):
        self.declarations({"name": "module", "packageFileName": "package/mod.c", "licenseDeclared": "Apache-2.0"})
        source = self.write("package/mod.c", "/* SPDX-License-Identifier: MIT */\nint value;\n")
        component = self.component(source, kind="native", manifest_license="BSD-3-Clause")

        self.assertTrue(resolve_licences(self.root, [component]))
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertTrue(any(
            issue["kind"] == "metadata_conflict" and issue.get("source") == str(source)
            and issue.get("scope") == "package/mod.c"
            and set(issue.get("expressions", [])) == {"MIT", "Apache-2.0"}
            for issue in component["licence_issues"]
        ))
        self.assertEqual(
            [(item["source"], item["expression"], item["provenance"], item["trusted"])
             for item in component["file_licences"]],
            [(str(source), "MIT", "source SPDX header", False)],
        )

    def test_absorbed_identifier_in_multiple_headers_is_a_conflict(self):
        self.declarations()
        source = self.write(
            "package/mod.py",
            "# SPDX-License-Identifier: MIT\n"
            "# SPDX-License-Identifier: MIT OR (MIT AND Zlib)\npass\n",
        )
        component = self.component(source)
        self.assertTrue(resolve_licences(self.root, [component]))
        conflict = next(issue for issue in component["licence_issues"] if issue["kind"] == "metadata_conflict")
        self.assertEqual(set(conflict["expressions"]), {"MIT", "MIT OR (MIT AND Zlib)"})
        self.assertIsNone(component["licence_concluded"])

    def test_absorbed_identifier_in_manifest_metadata_is_a_conflict(self):
        self.declarations()
        source = self.write("package/mod.py", "pass\n")
        self.write(
            "package/manifest.py",
            'metadata(license="MIT")\nmetadata(license="MIT OR (MIT AND Zlib)")\n',
        )
        component = self.component(source)
        self.assertTrue(resolve_licences(self.root, [component]))
        conflict = next(issue for issue in component["licence_issues"] if issue["kind"] == "metadata_conflict")
        self.assertEqual(set(conflict["expressions"]), {"MIT", "MIT OR (MIT AND Zlib)"})
        self.assertIsNone(component["licence_concluded"])

    def test_absorbed_identifier_in_nested_scope_is_not_lost(self):
        self.write("package/nested/mod.py", "pass\n")
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "package"], check=True)
        subprocess.run([
            "git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false", "commit", "-qm", "fixture",
        ], check=True)
        tree = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD:package"], text=True,
        ).strip()
        self.declarations(
            {"name": "package", "packageFileName": "package", "licenseDeclared": "MIT",
             "externalRefs": [{"referenceCategory": "PERSISTENT-ID", "referenceType": "gitoid",
                               "referenceLocator": "gitoid:tree:sha1:" + tree}]},
            {"name": "nested", "packageFileName": "package/nested/mod.py",
             "licenseDeclared": "MIT OR (MIT AND Zlib)"},
        )
        component = self.component(self.root / "package")
        self.assertTrue(resolve_licences(self.root, [component]))
        issue = next(issue for issue in component["licence_issues"] if issue["kind"] == "nested_licence_scopes")
        self.assertIn("Zlib", str(issue["declarations"]))
        self.assertIsNone(component["licence_concluded"])

    def test_header_does_not_license_another_selected_file(self):
        self.declarations()
        licensed = self.write("package/licensed.py", "# SPDX-License-Identifier: MIT\npass\n")
        unrelated = self.write("package/unrelated.py", "pass\n")
        component = self.component(licensed, unrelated)

        issues = resolve_licences(self.root, [component])
        self.assertTrue(issues)
        self.assertIsNone(component["licence_declared"])
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "unresolved")
        files = {item["source"]: item for item in component["file_licences"]}
        self.assertEqual(files[str(licensed)]["expression"], "MIT")
        self.assertEqual(files[str(licensed)]["provenance"], "source SPDX header")
        self.assertTrue(files[str(licensed)]["trusted"])
        self.assertIsNone(files[str(unrelated)]["expression"])
        self.assertTrue(any(issue["kind"] == "missing_assertion" and issue.get("source") == str(unrelated)
                            for issue in component["licence_issues"]))

    def test_header_does_not_license_unreviewed_file(self):
        self.declarations({"name": "root", "packageFileName": ".", "licenseDeclared": "MIT"})
        licensed = self.write("package/licensed.py", "# SPDX-License-Identifier: MIT\npass\n")
        unreviewed = self.write("package/unreviewed.py", "pass\n")
        component = self.component(licensed, unreviewed, unreviewed=True)

        issues = resolve_licences(self.root, [component])
        self.assertTrue(issues)
        self.assertIsNone(component["licence"])
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertTrue(any(issue["kind"] == "missing_assertion" and issue.get("source") == str(unreviewed)
                            for issue in component["licence_issues"]))


if __name__ == "__main__":
    unittest.main()
