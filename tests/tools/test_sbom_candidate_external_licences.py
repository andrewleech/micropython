#!/usr/bin/env python3
"""Tests for reviewed licences of pinned external source repositories."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import sbom_licences
from sbom import _build_document
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestExternalLicences(unittest.TestCase):
    URL = "https://example.com/vendor/dependency.git"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.checkout = self.base / "external" / "dependency"
        self.checkout.mkdir(parents=True)
        self.git(self.checkout, "init", "-q")
        self.git(self.checkout, "remote", "add", "origin", self.URL)
        self.put(self.checkout, "library.json", '{"license": "Apache-2.0"}\n')
        self.put(self.checkout, "src/default.c", "int default_source;\n")
        self.put(self.checkout, "src/header.c", "// SPDX-License-Identifier: BSD-3-Clause\nint header;\n")
        self.put(self.checkout, "src/special/exception.c", "int exception;\n")
        self.put(self.checkout, "src/special/conflict.c", "// SPDX-License-Identifier: Apache-2.0\nint conflict;\n")
        self.put(self.checkout, "metadata/library.json", '{"license": "Apache-2.0"}\n')
        self.put(self.checkout, "metadata/package.c", "int package;\n")
        self.put(self.checkout, "metadata/exception.c", "int exception;\n")
        self.put(self.checkout, "metadata/header.c", "// SPDX-License-Identifier: BSD-3-Clause\nint header;\n")
        self.put(self.checkout, "other/uncovered.c", "int uncovered;\n")
        self.commit(self.checkout)
        self.revision = self.git(self.checkout, "rev-parse", "HEAD")
        self.declarations()
        self.commit(self.repo)

    def git(self, root, *args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
        ).stdout.strip()

    def put(self, root, path, content):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def declarations(self, url=None, revision=None):
        document = {
            "packages": [],
            "external_repositories": [{
                "url": url or self.URL,
                "commit": revision or self.revision,
                "licenses": [
                    {"path": "src", "license": "MIT"},
                    {"path": "src/special", "license": "GPL-2.0-only"},
                    {"path": "metadata", "license": "MIT"},
                    {"path": "metadata/exception.c", "license": "BSD-3-Clause"},
                ],
            }],
        }
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(document, sort_keys=False))

    def resolve(self, source_path="src/default.c", checkout=None, **identity):
        checkout = checkout or self.checkout
        source = checkout / source_path
        component = {
            "name": "dependency", "path": str(source.parent), "owner_root": str(checkout),
            "declaration_root": str(checkout), "revision": self.revision, "sources": [str(source)],
        }
        component.update(identity)
        issues = resolve_licences(self.repo, [component])
        return component, component["file_licences"][0], issues

    def assert_unresolved(self, component, file_licence, issues):
        self.assertIsNone(file_licence["expression"])
        self.assertFalse(file_licence["trusted"])
        self.assertIsNone(component["licence"])
        self.assertTrue(issues)

    def test_matching_checkout_and_selected_path(self):
        component, file_licence, issues = self.resolve()
        self.assertEqual(issues, [])
        self.assertEqual(component["licence"], "MIT")
        self.assertEqual(file_licence["expression"], "MIT")
        self.assertTrue(file_licence["trusted"])
        component, file_licence, issues = self.resolve("other/uncovered.c")
        self.assert_unresolved(component, file_licence, issues)

    def test_source_and_package_metadata_take_precedence_over_default(self):
        component, file_licence, issues = self.resolve("metadata/package.c")
        self.assertEqual(issues, [])
        self.assertEqual(component["licence"], "Apache-2.0")
        self.assertEqual(file_licence["provenance"], "package metadata")
        component, file_licence, issues = self.resolve("metadata/header.c")
        self.assertEqual(issues, [])
        self.assertEqual(component["licence"], "BSD-3-Clause")
        self.assertEqual(file_licence["provenance"], "source SPDX header")

    def test_file_exception_is_not_overridden_by_package_metadata(self):
        component, file_licence, issues = self.resolve("metadata/exception.c")
        if issues:
            self.assertIsNone(component["licence"])
            self.assertTrue(any("conflict" in issue.lower() for issue in issues), issues)
        else:
            self.assertEqual(component["licence"], "BSD-3-Clause")
            self.assertEqual(file_licence["expression"], "BSD-3-Clause")
            self.assertTrue(file_licence["trusted"])

    def test_narrow_directory_conflicts_with_root_package_metadata(self):
        declaration_path = self.repo / "LICENSE.spdx.yaml"
        declaration = yaml.safe_load(declaration_path.read_text(encoding="utf-8"))
        declaration["external_repositories"][0]["licenses"] = [
            {"path": "src/special", "license": "GPL-2.0-only"},
        ]
        declaration_path.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
        component, file_licence, issues = self.resolve(
            "src/special/exception.c", path=str(self.checkout),
        )
        self.assertTrue(any("conflict" in issue.lower() for issue in issues), issues)
        self.assertIsNone(component["licence"])
        self.assertFalse(file_licence["trusted"])

    def test_mismatched_url_or_revision_is_not_reviewed(self):
        for kind in ("url", "commit", "component_revision"):
            with self.subTest(kind=kind):
                if kind == "url":
                    self.declarations(url="https://example.com/other/dependency.git")
                elif kind == "commit":
                    self.declarations(revision="0" * 40)
                else:
                    self.declarations()
                overrides = {"revision": "0" * 40} if kind == "component_revision" else {}
                self.assert_unresolved(*self.resolve(**overrides))

    def test_checkout_at_another_commit_is_not_reviewed(self):
        self.put(self.checkout, "new-file", "new revision\n")
        self.commit(self.checkout)
        self.assert_unresolved(*self.resolve())

    def test_unverified_or_dirty_sources_are_not_reviewed(self):
        for overrides in ({"unreviewed": True}, {"identity_problems": ["unverified"]}):
            with self.subTest(overrides=overrides):
                self.assert_unresolved(*self.resolve(**overrides))
        source = self.checkout / "src/default.c"
        source.write_text("int modified;\n", encoding="utf-8")
        self.assert_unresolved(*self.resolve())
        self.git(self.checkout, "checkout", "--", "src/default.c")
        self.put(self.checkout, "src/untracked.c", "int untracked;\n")
        self.assert_unresolved(*self.resolve("src/untracked.c"))
        self.assert_unresolved(*self.resolve("src/missing.c"))

    def test_checkout_checks_are_shared_but_each_source_is_verified(self):
        paths = ["src/default.c", "src/special/exception.c"]

        def components():
            return [{
                "name": path, "path": str((self.checkout / path).parent),
                "owner_root": str(self.checkout), "declaration_root": str(self.checkout),
                "revision": self.revision, "sources": [str(self.checkout / path)],
            } for path in paths]

        selected = components()
        with patch.object(sbom_licences, "_verified_external_checkout",
                          wraps=sbom_licences._verified_external_checkout) as verify:
            self.assertEqual(resolve_licences(self.repo, selected), [])
        self.assertEqual(verify.call_count, 1)
        self.assertEqual([item["licence"] for item in selected], ["MIT", "GPL-2.0-only"])

        self.put(self.checkout, "src/untracked.c", "int untracked;\n")
        paths.append("src/untracked.c")
        selected = components()
        issues = resolve_licences(self.repo, selected)
        self.assertTrue(issues)
        for component in selected:
            self.assertIsNone(component["licence"])
            self.assertFalse(component["file_licences"][0]["trusted"])

        (self.checkout / "src/untracked.c").unlink()
        paths.pop()
        self.put(self.checkout, "src/special/exception.c", "int changed;\n")
        selected = components()
        self.assertTrue(resolve_licences(self.repo, selected))
        for component in selected:
            self.assertIsNone(component["licence"])
            self.assertFalse(component["file_licences"][0]["trusted"])

    def test_nested_exception_and_conflict_are_preserved(self):
        component, file_licence, issues = self.resolve("src/special/exception.c")
        self.assertEqual(issues, [])
        self.assertEqual(component["licence"], "GPL-2.0-only")
        self.assertEqual(file_licence["expression"], "GPL-2.0-only")
        component, file_licence, issues = self.resolve("src/special/conflict.c")
        self.assertEqual(file_licence["expression"], "Apache-2.0")
        self.assertFalse(file_licence["trusted"])
        self.assertIsNone(component["licence"])
        self.assertTrue(any("conflict" in issue.lower() for issue in issues), issues)

    def test_checkout_relocation_does_not_change_reviewed_scope(self):
        relocated = self.base / "another" / "location" / "dependency"
        relocated.parent.mkdir(parents=True)
        shutil.move(str(self.checkout), str(relocated))
        component, file_licence, issues = self.resolve(checkout=relocated)
        self.assertEqual(issues, [])
        self.assertEqual(component["licence"], "MIT")
        self.assertTrue(file_licence["trusted"])

    def test_nested_checkout_requires_matching_parent_gitlink(self):
        parent = self.base / "sdk"
        parent.mkdir()
        self.git(parent, "init", "-q")
        self.git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(self.checkout), "components/dependency")
        nested = parent / "components/dependency"
        self.git(nested, "remote", "set-url", "origin", self.URL)
        self.commit(parent)
        component, file_licence, issues = self.resolve(checkout=nested)
        self.assertEqual(issues, [])
        self.assertEqual(component["licence"], "MIT")
        self.assertTrue(file_licence["trusted"])
        self.git(parent, "update-index", "--add", "--cacheinfo",
                 "160000," + self.git(parent, "rev-parse", "HEAD") + ",components/dependency")
        self.git(parent, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "different gitlink")
        self.assert_unresolved(*self.resolve(checkout=nested))

    def test_dirty_source_does_not_export_declaration_licence_text(self):
        ref = "LicenseRef-Test-Reviewed"
        self.put(self.repo, ".gitmodules", "")
        source = self.put(self.repo, "src/reviewed.c", "int clean;\n")
        declaration = yaml.safe_load((self.repo / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))
        declaration["packages"].append({
            "name": "reviewed", "packageFileName": "src/reviewed.c", "licenseDeclared": ref,
        })
        declaration["hasExtractedLicensingInfos"] = [{
            "licenseId": ref, "extractedText": "Reviewed licence text", "name": "Reviewed licence",
        }]
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(declaration, sort_keys=False))
        self.commit(self.repo)
        dep = self.put(self.repo, "build/source.P", f"build/source.o: {source}\n")
        database = self.put(self.repo, "build/compile_commands.json", json.dumps([{
            "directory": str(self.repo), "file": str(source),
            "arguments": ["cc", "-c", str(source), "-o", "build/source.o", "-MF", str(dep)],
        }]))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[], root=[],
                               image="test-image", offline=True)
        for dirty in (False, True):
            with self.subTest(dirty=dirty):
                if dirty:
                    source.write_text("int modified;\n", encoding="utf-8")
                document = _build_document(self.repo, args)
                component = next(item for item in document["components"]
                                 if "src/reviewed.c" in item["sources"])
                spdx = self.repo / "build/output.spdx.json"
                cyclonedx = self.repo / "build/output.cdx.json"
                write_sbom(document, spdx, "spdx", root=self.repo)
                write_sbom(document, cyclonedx, "cyclonedx", root=self.repo)
                spdx_data = json.loads(spdx.read_text(encoding="utf-8"))
                cdx_data = json.loads(cyclonedx.read_text(encoding="utf-8"))
                extracted = {item["licenseId"] for item in spdx_data.get("hasExtractedLicensingInfos", [])}
                cdx_component = next(item for item in cdx_data["components"]
                                     if item["name"] == component["name"])
                evidence = cdx_component.get("evidence", {}).get("licenses", [])
                if dirty:
                    self.assertTrue(component["identity_problems"])
                    self.assertIsNone(component["licence"])
                    self.assertNotIn(ref, extracted)
                    self.assertEqual(evidence, [])
                else:
                    self.assertEqual(component["licence"], ref)
                    self.assertIn(ref, extracted)
                    self.assertTrue(evidence)


if __name__ == "__main__":
    unittest.main()
