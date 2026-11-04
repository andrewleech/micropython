#!/usr/bin/env python3
"""Tests for selected ESP-IDF components and managed-source provenance."""

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


class TestCandidateEspIdf(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.repo = base / "firmware"
        self.repo.mkdir()
        self.idf = base / "esp-idf"
        self.idf.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.idf, "init", "-q")
        self.put(self.repo / ".gitmodules", "")
        self.put(self.repo / ".gitignore", "ports/esp32/managed_components/\nbuild/\n")
        self.put(self.repo / "LICENSE.spdx.yaml", yaml.safe_dump({
            "packages": [{"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"}],
        }))
        self.commit(self.repo)
        self.system = self.put(self.idf / "components/esp_system/system.c",
                               "// SPDX-License-Identifier: Apache-2.0\nint system_source;\n")
        self.rtos = self.put(self.idf / "components/freertos/tasks.c",
                             "// SPDX-License-Identifier: MIT\nint tasks;\n")
        self.unselected = self.put(self.idf / "components/freertos/unused.c",
                                   "// SPDX-License-Identifier: GPL-3.0-only\nint unused;\n")
        self.commit(self.idf)
        self.managed = self.repo / "ports/esp32/managed_components/espressif__mdns"
        self.mdns = self.put(self.managed / "mdns.c",
                             "// SPDX-License-Identifier: Apache-2.0\nint mdns;\n")
        self.mdns_other = self.put(self.managed / "other.c",
                                   "// SPDX-License-Identifier: MIT\nint other;\n")
        self.mdns_unused = self.put(self.managed / "unused.c",
                                    "// SPDX-License-Identifier: GPL-3.0-only\nint unused;\n")
        self.put(self.managed / "idf_component.yml", "version: 1.3.2\nurl: https://example.org/mdns\n")
        self.put(self.managed / "LICENSE", "Apache License\nVersion 2.0\n")
        self.checksums()
        self.put(self.managed / ".component_hash", "a" * 64 + "\n")

    def put(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def checksums(self):
        files = []
        for path in sorted(self.managed.iterdir()):
            if path.is_file() and path.name not in (".component_hash", "CHECKSUMS.json"):
                data = path.read_bytes()
                files.append({"path": path.name, "size": len(data),
                              "hash": hashlib.sha256(data).hexdigest()})
        self.put(self.managed / "CHECKSUMS.json", json.dumps({
            "version": "1.0", "algorithm": "sha256", "files": files,
        }))

    def document(self, sources, metadata=True, roots=None):
        build = self.repo / "build"
        entries = []
        for index, source in enumerate(sources):
            obj = f"build/source-{index}.o"
            dep = f"build/source-{index}.P"
            self.put(self.repo / dep, f"{obj}: {source}\n")
            entries.append({"directory": str(self.repo), "file": str(source),
                            "arguments": ["cc", "-c", str(source), "-o", obj, "-MF", dep]})
        database = self.put(build / "compile_commands.json", json.dumps(entries))
        if metadata:
            info = {}
            for name, directory, members in (
                ("esp_system", getattr(self, "system_component", self.idf / "components/esp_system"),
                 [self.system]),
                ("freertos", self.rtos.parent, [self.rtos, self.unselected]),
                ("espressif__mdns", self.managed, [self.mdns, self.mdns_other, self.mdns_unused]),
            ):
                info[name] = {"dir": str(directory), "sources": list(map(str, members)),
                              "type": "LIBRARY", "target": "___idf_" + name}
            self.put(build / "project_description.json", json.dumps({
                "idf_path": str(self.idf), "project_path": str(self.repo / "ports/esp32"),
                "build_dir": str(build), "build_component_info": info,
            }))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[],
                               root=roots if roots is not None else [f"esp-idf={self.idf}"],
                               image="test-image", offline=True)
        return _build_document(self.repo, args)

    def by_source(self, document):
        return {source: item for item in document["components"] for source in item["sources"]}

    def test_build_metadata_separates_selected_idf_components(self):
        document = self.document([self.system, self.rtos])
        sources = self.by_source(document)
        self.assertEqual(set(sources), {str(self.system), str(self.rtos)})
        system = sources[str(self.system)]
        rtos = sources[str(self.rtos)]
        self.assertIsNot(system, rtos)
        self.assertEqual(system["name"], "esp_system")
        self.assertEqual(rtos["name"], "freertos")
        revision = self.git(self.idf, "rev-parse", "HEAD")
        self.assertEqual(system["revision"], revision)
        self.assertEqual(rtos["revision"], revision)
        for component, licence in ((system, "Apache-2.0"), (rtos, "MIT")):
            self.assertEqual(component["licence"], licence)
            self.assertEqual(component["licence_status"], "resolved")
        self.assertNotIn(str(self.unselected), sources)

    def test_explicit_component_root_name_takes_precedence(self):
        document = self.document([self.system], roots=[
            f"esp-idf={self.idf}", f"custom-system={self.system.parent}",
        ])
        component = self.by_source(document)[str(self.system)]
        self.assertEqual(component["name"], "custom-system")
        self.assertEqual(component["revision"], self.git(self.idf, "rev-parse", "HEAD"))

    def test_without_component_metadata_idf_root_is_not_guessed(self):
        document = self.document([self.system, self.rtos], metadata=False)
        sources = self.by_source(document)
        self.assertIs(sources[str(self.system)], sources[str(self.rtos)])
        self.assertNotIn(str(self.unselected), sources)

    def test_dirty_idf_source_does_not_inherit_clean_revision(self):
        self.put(self.system, "// SPDX-License-Identifier: Apache-2.0\nint changed;\n")
        document = self.document([self.system])
        component = self.by_source(document)[str(self.system)]
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["licence"])
        self.assertIsNone(component["revision"])
        self.assertTrue(component.get("identity_problems"))

    def nested_submodule(self):
        origin = Path(self.temp.name) / "submodule-origin"
        origin.mkdir()
        self.git(origin, "init", "-q")
        self.put(origin / "nested.c", "// SPDX-License-Identifier: BSD-3-Clause\nint nested;\n")
        self.commit(origin)
        self.git(self.idf, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(origin), "components/esp_system/vendor")
        self.commit(self.idf)
        checkout = self.idf / "components/esp_system/vendor"
        return checkout, checkout / "nested.c"

    def test_nested_idf_submodule_uses_its_own_revision(self):
        checkout, selected = self.nested_submodule()
        self.system = selected
        document = self.document([selected])
        component = self.by_source(document)[str(selected)]
        self.assertEqual(component["revision"], self.git(checkout, "rev-parse", "HEAD"))
        self.assertEqual(component["licence"], "BSD-3-Clause")
        self.assertEqual(component["licence_status"], "resolved")
        self.assertFalse(component.get("identity_problems"))
        self.assertFalse(any("untracked selected source" in issue for issue in document["issues"]))

    def test_nested_idf_submodule_with_mismatched_gitlink_is_unverified(self):
        checkout, selected = self.nested_submodule()
        self.system = selected
        self.put(selected, "// SPDX-License-Identifier: BSD-3-Clause\nint new_revision;\n")
        self.commit(checkout)
        document = self.document([selected])
        component = self.by_source(document)[str(selected)]
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["licence"])
        self.assertIsNone(component["revision"])
        self.assertTrue(component.get("identity_problems"))

    def test_idf_component_inside_submodule_checks_parent_gitlink(self):
        origin = Path(self.temp.name) / "component-origin"
        origin.mkdir()
        self.git(origin, "init", "-q")
        self.put(origin / "esp_system/system.c",
                 "// SPDX-License-Identifier: BSD-3-Clause\nint system_source;\n")
        self.commit(origin)
        self.git(self.idf, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(origin), "components/vendor_bundle")
        self.commit(self.idf)
        checkout = self.idf / "components/vendor_bundle"
        self.system_component = checkout / "esp_system"
        self.system = self.system_component / "system.c"

        document = self.document([self.system])
        component = self.by_source(document)[str(self.system)]
        self.assertEqual(component["revision"], self.git(checkout, "rev-parse", "HEAD"))
        self.assertEqual(component["licence"], "BSD-3-Clause")
        self.assertEqual(component["licence_status"], "resolved")
        self.assertFalse(component.get("identity_problems"))

        self.put(self.system, "// SPDX-License-Identifier: BSD-3-Clause\nint changed;\n")
        document = self.document([self.system])
        component = self.by_source(document)[str(self.system)]
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["licence"])
        self.assertIsNone(component["revision"])
        self.assertTrue(component.get("identity_problems"))

        self.commit(checkout)
        document = self.document([self.system])
        component = self.by_source(document)[str(self.system)]
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["licence"])
        self.assertIsNone(component["revision"])
        self.assertTrue(component.get("identity_problems"))

    def test_idf_component_without_checkout_is_unverified(self):
        self.idf = Path(self.temp.name) / "idf-without-git"
        self.system = self.put(self.idf / "components/esp_system/system.c",
                               "// SPDX-License-Identifier: Apache-2.0\nint system_source;\n")
        self.rtos = self.put(self.idf / "components/freertos/tasks.c", "int tasks;\n")
        self.unselected = self.put(self.idf / "components/freertos/unused.c", "int unused;\n")
        document = self.document([self.system])
        component = self.by_source(document)[str(self.system)]
        self.assertEqual(component["licence_status"], "unresolved")
        self.assertIsNone(component["licence"])
        self.assertIsNone(component["revision"])
        self.assertTrue(component.get("identity_problems"))

    def assert_unverified_mdns(self, document, expected):
        sources = self.by_source(document)
        self.assertEqual(set(sources), set(expected))
        for source, expression in expected.items():
            component = sources[source]
            self.assertEqual(component["licence_status"], "unresolved")
            self.assertIsNone(component["licence"])
            file_licence = next(item for item in component["file_licences"] if item["source"] == source)
            self.assertEqual(file_licence["expression"], expression)
            self.assertEqual(file_licence["provenance"], "source SPDX header (unverified source)")
            self.assertFalse(file_licence["trusted"])
            self.assertTrue(component.get("identity_problems") or component.get("unreviewed"))
        self.assertNotIn(str(self.mdns_unused.relative_to(self.repo)), sources)

    def test_local_registry_marker_is_not_a_trust_anchor(self):
        expected = {str(self.mdns.relative_to(self.repo)): "Apache-2.0",
                    str(self.mdns_other.relative_to(self.repo)): "MIT"}
        self.assert_unverified_mdns(self.document([self.mdns, self.mdns_other]), expected)

    def test_missing_marker_or_modified_managed_source_fails_closed(self):
        expected = {str(self.mdns.relative_to(self.repo)): "Apache-2.0"}
        marker = self.managed / ".component_hash"
        original_marker = marker.read_text(encoding="utf-8")
        for case in ("missing marker", "modified source"):
            with self.subTest(case=case):
                self.put(marker, original_marker)
                self.put(self.mdns, "// SPDX-License-Identifier: Apache-2.0\nint mdns;\n")
                if case == "missing marker":
                    marker.unlink()
                else:
                    self.put(self.mdns, "// SPDX-License-Identifier: Apache-2.0\nint altered;\n")
                self.assert_unverified_mdns(self.document([self.mdns]), expected)


if __name__ == "__main__":
    unittest.main()
