#!/usr/bin/env python3
"""Tests for reviewed restrictive licence scopes and serialized evidence."""

import fnmatch
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


class TestCandidateRestrictive(unittest.TestCase):
    def check_source(self, source, expression, restrictions=()):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(root), *args], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ).stdout.strip()

            git("init", "-q")
            selected = root / source
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_text("/* selected source */\n", encoding="utf-8")
            git("add", source)
            tree = git("write-tree")
            reviewed = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))
            packages = []
            for entry in reviewed["packages"]:
                scope = entry["packageFileName"]
                if not (scope == "." or fnmatch.fnmatchcase(source, scope) or source.startswith(scope + "/")):
                    continue
                if scope != "." and (root / scope).is_dir():
                    pin = git("rev-parse", tree + ":" + scope)
                    entry["externalRefs"] = [{"referenceCategory": "PERSISTENT-ID", "referenceType": "gitoid",
                                              "referenceLocator": "gitoid:tree:sha1:" + pin}]
                    entry.pop("downloadLocation", None)
                packages.append(entry)
            (root / "LICENSE.spdx.yaml").write_text(yaml.safe_dump({
                "packages": packages,
                "hasExtractedLicensingInfos": reviewed["hasExtractedLicensingInfos"],
            }), encoding="utf-8")
            git("add", "LICENSE.spdx.yaml")
            git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

            item = {"name": "selected", "path": ".", "owner_root": str(root),
                    "sources": [source], "version": None}
            issues = resolve_licences(root, [item])
            self.assertEqual(issues, [], source)
            self.assertEqual(item["licence_status"], "resolved")
            self.assertEqual(item["licence_declared"], expression)
            self.assertEqual(item["licence"], expression)
            model = {"name": "test-image", "issues": issues, "components": [item]}
            documents = {}
            for format in ("spdx", "cyclonedx"):
                output = root / (format + ".json")
                write_sbom(model, output, format)
                documents[format] = json.loads(output.read_text(encoding="utf-8"))

            package = next(p for p in documents["spdx"]["packages"] if p["name"] == "selected")
            component = next(c for c in documents["cyclonedx"]["components"] if c["name"] == "selected")
            self.assertEqual(package["licenseDeclared"], expression)
            self.assertEqual(component["licenses"], [{"expression": expression, "acknowledgement": "declared"}])
            if expression.startswith("LicenseRef-"):
                extracted = next(info for info in documents["spdx"]["hasExtractedLicensingInfos"]
                                 if info["licenseId"] == expression)
                self.assertEqual(extracted["extractedText"], item["licence_texts"][expression])
                evidence = component["evidence"]["licenses"][0]["license"]
                self.assertEqual(evidence["text"]["content"], extracted["extractedText"])
            if restrictions:
                properties = {p["name"]: json.loads(p["value"]) for p in component["properties"]
                              if p["name"].startswith("micropython:")}
                comments = " ".join(properties["micropython:licence_comments"]).lower()
                for restriction in restrictions:
                    self.assertIn(restriction.lower(), package["licenseComments"].lower())
                    self.assertIn(restriction.lower(), comments)

    def test_stm32_exact_liberty_and_adjacent_mit(self):
        liberty = "LicenseRef-MCD-ST-Liberty-SW-License-Agreement-V2"
        for name in ("usbd_cdc_interface.c", "usbd_cdc_interface.h", "usbd_conf.c", "usbd_conf.h", "usbd_desc.c"):
            with self.subTest(source=name):
                self.check_source("ports/stm32/" + name, liberty,
                                  ("software_license_agreement_liberty_v2", "not in the source tree",
                                   "prior inventory", "ST devices", "not been independently verified"))
        for name in ("usbd_desc.h", "usbd.c", "usbd_msc_interface.c"):
            with self.subTest(source=name):
                self.check_source("ports/stm32/" + name, "MIT")

    def test_cc3200_narrow_bsd_scopes_and_adjacent_mit(self):
        for source in ("ports/cc3200/bootmgr/sl/user.h", "ports/cc3200/fatfs/src/drivers/sd_diskio.c",
                       "ports/cc3200/fatfs/src/drivers/stdcmd.h"):
            with self.subTest(source=source):
                self.check_source(source, "BSD-3-Clause")
        self.check_source("ports/cc3200/hal/cc3200_hal.c", "MIT")

    def test_alif_notice_and_adjacent_mit(self):
        for name in ("alif_dcd_reg.h", "tusb_alif_dcd.c"):
            with self.subTest(source=name):
                self.check_source("ports/alif/tinyusb_port/" + name,
                                  "LicenseRef-Alif-Semiconductor-Software-License-Agreement-Notice",
                                  ("2024 source", "without including it",
                                   "lib/alif_ensemble-cmsis-dfp/License.txt", "Rev. 10/2022",
                                   "Alif devices", "copyleft", "applicability to this file is unconfirmed"))
        self.check_source("ports/alif/tinyusb_port/tusb_config.h", "MIT")

    def test_cypress_notice_and_adjacent_mit(self):
        self.check_source("ports/psoc-edge/secboot/main.c", "LicenseRef-Cypress-Source-Notice",
                          ("Whether an EULA applies is unknown", "If no EULA applies",
                           "personal", "non-exclusive", "non-transferable",
                           "Cypress integrated circuit products", "express written permission"))
        self.check_source("ports/psoc-edge/main.c", "MIT")


if __name__ == "__main__":
    unittest.main()
