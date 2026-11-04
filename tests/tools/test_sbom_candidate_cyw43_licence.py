#!/usr/bin/env python3
"""Tests for reviewed CYW43 driver licence declarations."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestCyw43Licence(unittest.TestCase):
    URL = "https://github.com/georgerobotics/cyw43-driver.git"
    REVISION = "055d64274b014dd7b1c2fc94d26e8a18face7124"
    REF = "LicenseRef-CYW43-NonCommercial"
    SOURCES = {
        "src/cyw43_bthci_uart.c", "src/cyw43_ctrl.c", "src/cyw43_lwip.c",
        "src/cyw43_ll.c", "src/cyw43_sdio.c", "src/cyw43_spi.c", "src/cyw43_stats.c",
    }
    FIRMWARE = "firmware/w43439A0_7_95_49_00_combined.h"
    LINKS = {
        f"https://github.com/georgerobotics/cyw43-driver/blob/{REVISION}/LICENSE",
        f"https://github.com/georgerobotics/cyw43-driver/blob/{REVISION}/LICENSE.RP",
    }

    @classmethod
    def setUpClass(cls):
        cls.metadata = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))

    def test_selected_sources_and_terms(self):
        entries = [item for item in self.metadata["external_repositories"] if item["url"] == self.URL]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["commit"], self.REVISION)
        declarations = entry["licenses"]
        self.assertEqual(len(declarations), len(self.SOURCES))
        self.assertEqual({item["path"] for item in declarations}, self.SOURCES)
        self.assertEqual({item["license"] for item in declarations}, {self.REF})
        details = [item for item in self.metadata["hasExtractedLicensingInfos"]
                   if item["licenseId"] == self.REF]
        self.assertEqual(len(details), 1)
        text = details[0]["extractedText"]
        self.assertIn("solely for personal benefit", text)
        self.assertIn("not for any commercial purpose", text)
        self.assertIn("Raspberry Pi Ltd semiconductor", text)
        self.assertIn("different terms", text)
        self.assertIn("LICENSE.RP", text)
        self.assertEqual(set(details[0]["seeAlso"]), self.LINKS)

    def test_selected_driver_and_blob_in_both_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            project = base / "project"
            checkout = base / "driver"
            project.mkdir()
            checkout.mkdir()

            def git(*args):
                return subprocess.run(["git", "-C", str(checkout), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()

            git("init", "-q")
            git("remote", "add", "origin", self.URL)
            for name in self.SOURCES | {self.FIRMWARE}:
                source = checkout / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("/* fixture */\n", encoding="utf-8")
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            revision = git("rev-parse", "HEAD")
            # Retain the real declarations but bind them to the local fixture commit.
            metadata = {
                "packages": [],
                "external_repositories": [
                    {**item, "commit": revision} for item in self.metadata["external_repositories"]
                    if item["url"] == self.URL
                ],
                "hasExtractedLicensingInfos": [
                    item for item in self.metadata["hasExtractedLicensingInfos"]
                    if item["licenseId"] == self.REF
                ],
            }
            (project / "LICENSE.spdx.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")

            def component(name, sources):
                return {"name": name, "version": None, "path": str(checkout),
                        "owner_root": str(checkout), "declaration_root": str(checkout),
                        "revision": revision, "sources": [str(checkout / source) for source in sources]}

            driver = component("cyw43-driver", sorted(self.SOURCES))
            firmware = component("cyw43-firmware", [self.FIRMWARE])
            issues = resolve_licences(project, [driver, firmware])
            self.assertEqual(driver["licence"], self.REF)
            self.assertEqual(len(driver["file_licences"]), len(self.SOURCES))
            for item in driver["file_licences"]:
                self.assertEqual(item["expression"], self.REF)
                self.assertTrue(item["trusted"])
            self.assertIsNone(firmware["licence"])
            self.assertIsNone(firmware["file_licences"][0]["expression"])
            self.assertNotIn(self.REF, firmware.get("licence_details", {}))

            model = {"name": "test-image", "issues": issues, "components": [driver, firmware]}
            for format in ("spdx", "cyclonedx"):
                with self.subTest(format=format):
                    output = base / (format + ".json")
                    write_sbom(model, output, format, root=project)
                    data = json.loads(output.read_text(encoding="utf-8"))
                    if format == "spdx":
                        packages = {item["name"]: item for item in data["packages"]}
                        self.assertEqual(packages["cyw43-driver"]["licenseDeclared"], self.REF)
                        self.assertEqual(packages["cyw43-firmware"]["licenseConcluded"], "NOASSERTION")
                        self.assertEqual(packages["cyw43-firmware"]["licenseDeclared"], "NOASSERTION")
                        infos = {item["licenseId"]: item for item in data["hasExtractedLicensingInfos"]}
                        self.assertEqual(set(infos[self.REF]["seeAlsos"]), self.LINKS)
                        self.assertIn("solely for personal benefit", infos[self.REF]["extractedText"])
                    else:
                        components = {item["name"]: item for item in data["components"]}
                        driver_output = components["cyw43-driver"]
                        license_evidence = driver_output["evidence"]["licenses"][0]["license"]
                        self.assertIn("solely for personal benefit", license_evidence["text"]["content"])
                        links = {item["url"] for item in driver_output["externalReferences"]
                                 if item["type"] == "license"}
                        self.assertEqual(links, self.LINKS)
                        self.assertFalse(components["cyw43-firmware"].get("licenses"))


if __name__ == "__main__":
    unittest.main()
