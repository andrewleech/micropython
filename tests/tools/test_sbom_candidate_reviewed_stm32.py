#!/usr/bin/env python3
"""Tests for the reviewed STM32F7 selected source licences."""

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


class TestReviewedStm32(unittest.TestCase):
    URL = "https://github.com/micropython/stm32lib"
    SYSTEM = "CMSIS/STM32F7xx/Source/Templates/system_stm32f7xx.c"
    SOURCES = {SYSTEM} | {
        "STM32F7xx_HAL_Driver/Src/stm32f7xx_" + suffix + ".c" for suffix in (
            "hal", "hal_adc", "hal_adc_ex", "hal_cortex", "hal_dma", "hal_gpio",
            "hal_i2c", "hal_pwr", "hal_pwr_ex", "hal_rcc", "hal_rcc_ex", "hal_rtc",
            "hal_rtc_ex", "hal_spi", "hal_tim", "hal_tim_ex", "hal_uart", "ll_rcc",
            "ll_utils", "hal_flash", "hal_flash_ex", "hal_pcd", "hal_pcd_ex", "ll_usb",
            "hal_sd", "ll_sdmmc", "ll_fmc", "hal_mmc", "hal_sdram", "hal_dcmi",
            "hal_dma_ex", "hal_can", "hal_i2s",
        )
    }
    OTHER_FAMILY = "STM32F4xx_HAL_Driver/Src/stm32f4xx_hal.c"

    @classmethod
    def setUpClass(cls):
        metadata = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))
        entries = [entry for entry in metadata["external_repositories"]
                   if entry["url"].removesuffix(".git") == cls.URL]
        if len(entries) != 1:
            raise AssertionError(f"expected one stm32lib declaration, got {len(entries)}")
        cls.entry = entries[0]

    def git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()
        self.git(self.root, "init", "-q")
        self.checkout = self.root / "lib/stm32lib"
        self.checkout.mkdir(parents=True)
        self.git(self.checkout, "init", "-q")
        self.git(self.checkout, "remote", "add", "origin", self.entry["url"])
        for name in self.SOURCES | {self.OTHER_FAMILY}:
            source = self.checkout / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("/* fixture without SPDX header */\n", encoding="utf-8")
        self.commit(self.checkout)
        self.revision = self.git(self.checkout, "rev-parse", "HEAD")
        metadata = {"packages": [], "external_repositories": [{**self.entry, "commit": self.revision}]}
        (self.root / "LICENSE.spdx.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
        self.git(self.root, "add", "LICENSE.spdx.yaml")
        self.git(self.root, "update-index", "--add", "--cacheinfo",
                 "160000," + self.revision + ",lib/stm32lib")
        self.git(self.root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def component(self, sources, revision=None):
        return {"name": "stm32lib", "version": None, "path": str(self.checkout),
                "owner_root": str(self.checkout), "declaration_root": str(self.root),
                "revision": revision or self.revision,
                "sources": [str(self.checkout / source) for source in sorted(sources)]}

    def test_selected_sources_resolve_and_export_spdx_evidence(self):
        component = self.component(self.SOURCES)
        issues = resolve_licences(self.root, [component])
        self.assertEqual(issues, [])
        self.assertEqual(component["licence"], "BSD-3-Clause")
        self.assertEqual(len(component["file_licences"]), len(self.SOURCES))
        for item in component["file_licences"]:
            self.assertEqual(item["expression"], "BSD-3-Clause")
            self.assertTrue(item["trusted"])
        output = self.root / "sbom.json"
        write_sbom({"name": "test-image", "subject": {"image": "firmware.elf"},
                    "issues": issues, "components": [component]}, output, "spdx", root=self.root)
        document = json.loads(output.read_text(encoding="utf-8"))
        package = next(item for item in document["packages"] if item["name"] == "stm32lib")
        records = {item["fileName"].removeprefix("./").removeprefix("lib/stm32lib/"): item
                   for item in document["files"]}
        self.assertEqual(set(records), self.SOURCES)
        evidence = {rel["spdxElementId"] for rel in document["relationships"]
                    if rel["relatedSpdxElement"] == package["SPDXID"]
                    and rel["relationshipType"] == "OTHER"}
        for record in records.values():
            self.assertEqual(record["licenseConcluded"], "BSD-3-Clause")
            self.assertTrue(record["checksums"])
            self.assertIn(record["SPDXID"], evidence)

    def test_other_family_and_nonmatching_revision_are_not_inferred(self):
        for component in (self.component({self.OTHER_FAMILY}),
                          self.component({self.SYSTEM}, revision="0" * 40)):
            with self.subTest(source=component["sources"], revision=component["revision"]):
                resolve_licences(self.root, [component])
                self.assertIsNone(component["licence"])
                self.assertIsNone(component["file_licences"][0]["expression"])
                self.assertFalse(component["file_licences"][0]["trusted"])

    def test_production_pin_rejects_fixture_checkout(self):
        metadata = {"packages": [], "external_repositories": [self.entry]}
        (self.root / "LICENSE.spdx.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
        self.commit(self.root)
        component = self.component({self.SYSTEM})
        self.assertTrue(resolve_licences(self.root, [component]))
        self.assertIsNone(component["licence"])
        self.assertIsNone(component["file_licences"][0]["expression"])
        self.assertFalse(component["file_licences"][0]["trusted"])


if __name__ == "__main__":
    unittest.main()
