#!/usr/bin/env python3
"""Tests for selected CYW43 firmware build inputs."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from sbom import _build_document
from sbom_inputs import read_inputs
from sbom_writers import write_sbom


class TestRadioFirmware(unittest.TestCase):
    WIFI = "lib/cyw43-driver/firmware/w43439A0_7_95_49_00_combined.h"
    BT = "lib/cyw43-driver/firmware/cyw43_btfw_43439.h"
    UNUSED = "lib/cyw43-driver/firmware/w4343WA1_7_45_98_102_combined.h"
    NVRAM = "lib/cyw43-driver/firmware/wifi_nvram_43439.h"
    DRIVER = "lib/cyw43-driver/src/cyw43.c"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "project"
        self.vendor = self.base / "vendor"
        self.repo.mkdir()
        self.vendor.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.vendor, "init", "-q")
        for name in (self.WIFI, self.BT, self.UNUSED, self.NVRAM):
            self.put(self.vendor, name.removeprefix("lib/cyw43-driver/"), "const unsigned char blob[] = {1, 2};\n")
        self.put(self.vendor, "src/cyw43.c", "int driver;\n")
        self.put(self.vendor, "src/cyw43.h", "#define CYW43_VERSION_MAJOR 1\n#define CYW43_VERSION_MINOR 0\n#define CYW43_VERSION_MICRO 0\n")
        self.commit(self.vendor)
        self.revision = self.git(self.vendor, "rev-parse", "HEAD")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(self.vendor), "lib/cyw43-driver")
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump({"packages": [
            {"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"},
            {"name": "cyw43-driver", "packageFileName": "lib/cyw43-driver", "licenseDeclared": "Apache-2.0",
             "downloadLocation": "git+https://example.com/cyw43-driver.git@" + self.revision},
        ]}))
        self.put(self.repo, "src/main.c", "int main(void) { return 0; }\n")
        self.put(self.repo, "src/host.c", "int host;\n")
        self.put(self.repo, "src/ordinary.h", "#define ORDINARY 1\n")
        self.put(self.repo, "other/lib/cyw43-driver/firmware/w43439A0_7_95_49_00_combined.h", "decoy\n")
        self.commit(self.repo)

    @staticmethod
    def git(root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    @staticmethod
    def put(root, name, text):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def database(self, prerequisites, *, host_prerequisites=()):
        entries = []
        for source, obj, deps in (("src/main.c", "main", prerequisites),
                                  ("src/host.c", "host", host_prerequisites)):
            dep = f"build/{obj}.P"
            self.put(self.repo, dep, f"build/{obj}.o: {source} {' '.join(deps)}\n")
            entries.append({"directory": str(self.repo), "file": source,
                            "arguments": ["cc", "-c", source, "-o", f"build/{obj}.o", "-MF", dep]})
        return self.put(self.repo, "build/compile_commands.json", json.dumps(entries))

    def document(self, db, excludes=()):
        args = SimpleNamespace(compdb=[str(db)], exclude=list(excludes), frozen=[], root=[],
                               image="test-image", offline=True)
        return _build_document(self.repo, args)

    def test_only_selected_owned_firmware_headers_are_inputs(self):
        decoy = "other/lib/cyw43-driver/firmware/w43439A0_7_95_49_00_combined.h"
        db = self.database([self.WIFI, self.BT, self.NVRAM, "src/ordinary.h", decoy, self.WIFI])
        records = read_inputs(self.repo, [db], [], [])
        firmware = [record for record in records if record.get("input_kind") == "firmware"]
        self.assertEqual({record["source"] for record in firmware}, {self.repo / self.WIFI, self.repo / self.BT})
        self.assertEqual(len(firmware), 2)
        for record in firmware:
            self.assertEqual(record["selection"], "native")
            self.assertEqual(record["originating_source"], self.repo / "src/main.c")
            self.assertEqual(record["object"], self.repo / "build/main.o")
            self.assertEqual(record["depfile"], self.repo / "build/main.P")
        self.assertNotIn(self.repo / self.UNUSED, {record["source"] for record in records})
        self.assertNotIn(self.repo / decoy, {record["source"] for record in records})
        self.assertNotIn(self.repo / self.NVRAM, {record["source"] for record in records})
        self.assertNotIn(self.repo / "src/ordinary.h", {record["source"] for record in records})

    def test_excluded_path_and_excluded_command_do_not_select_firmware(self):
        db = self.database([self.WIFI, self.BT], host_prerequisites=[self.UNUSED])
        records = read_inputs(self.repo, [db], [self.BT, "src/host.c"], [])
        selected = {record["source"] for record in records}
        self.assertIn(self.repo / self.WIFI, selected)
        self.assertNotIn(self.repo / self.BT, selected)
        self.assertNotIn(self.repo / self.UNUSED, selected)
        document = self.document(db, [self.BT, "src/host.c"])
        firmware = next(item for item in document["components"] if item["name"] == "cyw43-firmware")
        self.assertEqual(firmware["sources"], [self.WIFI])

    def test_selected_missing_firmware_reports_incomplete_depfile(self):
        missing = "lib/cyw43-driver/firmware/wmissing_combined.h"
        db = self.database([missing])
        evidence = []
        read_inputs(self.repo, [db], [], [], evidence)
        main = next(item for item in evidence if item["source"] == self.repo / "src/main.c")
        self.assertEqual(main["status"], "incomplete")
        self.assertIn(missing, main["error"])

    def test_local_blob_specific_declaration_applies(self):
        declaration_path = self.repo / "LICENSE.spdx.yaml"
        declaration = yaml.safe_load(declaration_path.read_text(encoding="utf-8"))
        for scope in ("lib/cyw43-driver/firmware", self.WIFI):
            with self.subTest(scope=scope):
                declaration["packages"] = declaration["packages"][:2] + [{
                    "name": "radio-blob-terms", "packageFileName": scope,
                    "licenseDeclared": "BSD-2-Clause",
                }]
                self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(declaration))
                self.commit(self.repo)
                db = self.database([self.WIFI, self.DRIVER])
                document = self.document(db)
                firmware = next(item for item in document["components"] if self.WIFI in item["sources"])
                driver = next(item for item in document["components"] if self.DRIVER in item["sources"])
                self.assertIsNot(firmware, driver)
                self.assertEqual(firmware["licence"], "BSD-2-Clause")
                self.assertEqual(driver["licence"], "Apache-2.0")

    def test_verified_external_blob_specific_declaration_applies(self):
        url = "https://example.com/cyw43-driver.git"
        self.git(self.repo / "lib/cyw43-driver", "remote", "set-url", "origin", url)
        declaration_path = self.repo / "LICENSE.spdx.yaml"
        declaration = yaml.safe_load(declaration_path.read_text(encoding="utf-8"))
        for scope in ("firmware", self.WIFI.removeprefix("lib/cyw43-driver/")):
            with self.subTest(scope=scope):
                declaration["external_repositories"] = [{
                    "url": url, "commit": self.revision,
                    "licenses": [{"path": ".", "license": "Apache-2.0"},
                                 {"path": scope, "license": "BSD-2-Clause"}],
                }]
                self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(declaration))
                self.commit(self.repo)
                db = self.database([self.WIFI, self.DRIVER])
                document = self.document(db)
                firmware = next(item for item in document["components"] if self.WIFI in item["sources"])
                driver = next(item for item in document["components"] if self.DRIVER in item["sources"])
                self.assertIsNot(firmware, driver)
                self.assertEqual(firmware["licence"], "BSD-2-Clause")
                self.assertEqual(driver["licence"], "Apache-2.0")

    def test_blob_glob_matches_only_wifi_firmware(self):
        url = "https://example.com/cyw43-driver.git"
        self.git(self.repo / "lib/cyw43-driver", "remote", "set-url", "origin", url)
        declaration_path = self.repo / "LICENSE.spdx.yaml"
        original = yaml.safe_load(declaration_path.read_text(encoding="utf-8"))
        for kind in ("local", "external"):
            with self.subTest(kind=kind):
                declaration = dict(original)
                if kind == "local":
                    declaration["packages"] = original["packages"] + [{
                        "name": "wifi-blob-terms",
                        "packageFileName": "lib/cyw43-driver/*/w*_combined.h",
                        "licenseDeclared": "BSD-2-Clause",
                    }]
                else:
                    declaration["external_repositories"] = [{
                        "url": url, "commit": self.revision,
                        "licenses": [{"path": "*/w*_combined.h", "license": "BSD-2-Clause"}],
                    }]
                self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(declaration))
                self.commit(self.repo)
                for source, expected in ((self.WIFI, "BSD-2-Clause"), (self.BT, None)):
                    with self.subTest(source=source):
                        db = self.database([source])
                        document = self.document(db)
                        firmware = next(item for item in document["components"] if source in item["sources"])
                        self.assertEqual(firmware["licence"], expected)

    def test_broad_glob_does_not_license_firmware(self):
        url = "https://example.com/cyw43-driver.git"
        self.git(self.repo / "lib/cyw43-driver", "remote", "set-url", "origin", url)
        declaration_path = self.repo / "LICENSE.spdx.yaml"
        original = yaml.safe_load(declaration_path.read_text(encoding="utf-8"))
        for kind in ("local", "external"):
            with self.subTest(kind=kind):
                declaration = dict(original)
                if kind == "local":
                    declaration["packages"] = original["packages"] + [{
                        "name": "generic-terms", "packageFileName": "*",
                        "licenseDeclared": "Apache-2.0",
                    }]
                else:
                    declaration["external_repositories"] = [{
                        "url": url, "commit": self.revision,
                        "licenses": [{"path": "*", "license": "Apache-2.0"}],
                    }]
                self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(declaration))
                self.commit(self.repo)
                db = self.database([self.WIFI])
                document = self.document(db)
                firmware = next(item for item in document["components"] if self.WIFI in item["sources"])
                self.assertIsNone(firmware["licence"])
                self.assertIsNone(firmware.get("licence_declared"))

    def test_untrusted_external_blob_declaration_is_not_applied(self):
        url = "https://example.com/cyw43-driver.git"
        self.git(self.repo / "lib/cyw43-driver", "remote", "set-url", "origin", url)
        declaration_path = self.repo / "LICENSE.spdx.yaml"
        declaration = yaml.safe_load(declaration_path.read_text(encoding="utf-8"))
        declaration["external_repositories"] = [{
            "url": url, "commit": self.revision,
            "licenses": [{"path": "firmware", "license": "BSD-2-Clause"}],
        }]
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(declaration))
        self.commit(self.repo)
        db = self.database([self.WIFI])
        self.put(self.repo, self.WIFI, "modified firmware\n")
        document = self.document(db)
        firmware = next(item for item in document["components"] if self.WIFI in item["sources"])
        self.assertIsNone(firmware["licence"])
        self.assertIsNone(firmware.get("licence_declared"))

    def test_dirty_external_declaration_is_not_applied(self):
        url = "https://example.com/cyw43-driver.git"
        self.git(self.repo / "lib/cyw43-driver", "remote", "set-url", "origin", url)
        declaration_path = self.repo / "LICENSE.spdx.yaml"
        declaration = yaml.safe_load(declaration_path.read_text(encoding="utf-8"))
        declaration["external_repositories"] = [{
            "url": url, "commit": self.revision,
            "licenses": [{"path": "firmware", "license": "BSD-2-Clause"}],
        }]
        self.put(self.repo, "LICENSE.spdx.yaml", yaml.safe_dump(declaration))
        db = self.database([self.WIFI])
        document = self.document(db)
        firmware = next(item for item in document["components"] if self.WIFI in item["sources"])
        self.assertIsNone(firmware["licence"])
        self.assertIsNone(firmware.get("licence_declared"))

    def test_separate_unlicensed_component_and_format_evidence(self):
        db = self.database([self.WIFI, self.BT, self.DRIVER])
        document = self.document(db)
        firmware = next(item for item in document["components"] if item["name"] == "cyw43-firmware")
        driver = next(item for item in document["components"] if self.DRIVER in item["sources"])
        self.assertIsNot(firmware, driver)
        self.assertEqual(firmware["path"], "lib/cyw43-driver/firmware")
        self.assertEqual(firmware["revision"], self.revision)
        self.assertEqual(Path(firmware["owner_root"]), self.repo / "lib/cyw43-driver")
        self.assertEqual(set(firmware["sources"]), {self.WIFI, self.BT})
        self.assertEqual(driver["licence"], "Apache-2.0")
        self.assertIsNone(firmware["licence"])
        self.assertIsNone(firmware.get("licence_declared"))
        expected = [{"source": name, "kind": "included", "originating_source": "src/main.c",
                     "object": "build/main.o", "depfile": "build/main.P"} for name in (self.WIFI, self.BT)]
        self.assertCountEqual(firmware["source_evidence"], expected)
        for format in ("spdx", "cyclonedx"):
            with self.subTest(format=format):
                output = self.base / (format + ".json")
                write_sbom(document, output, format, root=self.repo)
                result = json.loads(output.read_text(encoding="utf-8"))
                if format == "spdx":
                    package = next(item for item in result["packages"] if item["name"] == "cyw43-firmware")
                    self.assertEqual(package["licenseDeclared"], "NOASSERTION")
                    self.assertEqual(package["licenseConcluded"], "NOASSERTION")
                    provenance = json.loads(package["annotations"][0]["comment"])
                    relationships = {(item["spdxElementId"], item["relationshipType"], item["relatedSpdxElement"])
                                     for item in result["relationships"]}
                    self.assertIn((package["SPDXID"], "BUILD_DEPENDENCY_OF", "SPDXRef-MicroPython"), relationships)
                    self.assertNotIn(("SPDXRef-MicroPython", "CONTAINS", package["SPDXID"]), relationships)
                else:
                    package = next(item for item in result["components"] if item["name"] == "cyw43-firmware")
                    self.assertFalse(package.get("licenses"))
                    properties = {item["name"]: json.loads(item["value"]) for item in package["properties"]
                                  if item["name"].startswith("micropython:")}
                    provenance = {"source_evidence": properties["micropython:source_evidence"]}
                    self.assertNotEqual(package.get("scope"), "required")
                self.assertCountEqual(provenance["source_evidence"], expected)
                self.assertNotIn(self.UNUSED, json.dumps(provenance))


if __name__ == "__main__":
    unittest.main()
