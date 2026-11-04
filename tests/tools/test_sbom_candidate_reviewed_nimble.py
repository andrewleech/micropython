#!/usr/bin/env python3
#
# SPDX-License-Identifier: MIT

"""Tests for reviewed, revision-specific NimBLE file licences."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from spdx_tools.common.spdx_licensing import spdx_licensing


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import sbom_licences
from sbom_licences import resolve_licences
from sbom_writers import write_sbom


class TestReviewedNimble(unittest.TestCase):
    URL = "https://github.com/micropython/mynewt-nimble.git"
    REVISION = "42849560ba7906f023f61e5f7ff3709ba2c1dfca"
    EXPECTED = {
        "nimble/host/src/ble_att.c": "Apache-2.0",
        "ext/tinycrypt/src/aes_encrypt.c": "BSD-3-Clause",
        "ext/tinycrypt/src/ecc.c": "BSD-2-Clause AND BSD-3-Clause",
        "ext/tinycrypt/src/ecc_dh.c": "BSD-2-Clause AND BSD-3-Clause",
        "porting/nimble/src/os_mbuf.c": "Apache-2.0 AND BSD-3-Clause",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.checkout = Path(self.temp.name).resolve() / "mynewt-nimble"
        self.checkout.mkdir()
        self.git("init", "-q")
        self.git("remote", "add", "origin", self.URL)
        for name in self.EXPECTED:
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            notice = """/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *  http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
""" if name.endswith("ble_att.c") else ""
            path.write_text(notice + "int fixture;\n", encoding="utf-8")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.checkout), *args], check=True, capture_output=True, text=True,
        ).stdout.strip()

    def component(self, names, **overrides):
        component = {
            "name": "mynewt-nimble", "version": "1.4.0", "path": str(self.checkout),
            "owner_root": str(self.checkout), "declaration_root": str(self.checkout),
            "revision": self.REVISION, "sources": [str(self.checkout / name) for name in names],
        }
        component.update(overrides)
        return component

    def resolve_with_simulated_pin(self, component):
        # The fixture cannot reproduce the upstream commit hash; all other Git checks are real.
        real_git = sbom_licences._git

        def git(root, *args):
            if Path(root) == self.checkout and args == ("rev-parse", "HEAD"):
                return self.REVISION
            return real_git(root, *args)

        with patch.object(sbom_licences, "_git", side_effect=git):
            return resolve_licences(REPO, [component])

    def test_reviewed_scopes_and_source_notice(self):
        component = self.component(self.EXPECTED)
        self.assertEqual(self.resolve_with_simulated_pin(component), [])
        records = {Path(item["source"]).relative_to(self.checkout).as_posix(): item
                   for item in component["file_licences"]}
        self.assertEqual(set(records), set(self.EXPECTED))
        for name, expected in self.EXPECTED.items():
            with self.subTest(source=name):
                self.assertTrue(records[name]["trusted"])
                self.assertEqual(spdx_licensing.parse(records[name]["expression"]),
                                 spdx_licensing.parse(expected))
        self.assertEqual(component["licence_status"], "mixed")
        self.assertIsNone(component["licence"])

    def test_fake_checkout_and_untrusted_identity_are_not_reviewed(self):
        name = "ext/tinycrypt/src/aes_encrypt.c"
        component = self.component([name])
        self.assertTrue(resolve_licences(REPO, [component]))
        self.assertIsNone(component["licence"])
        self.assertFalse(component["file_licences"][0]["trusted"])
        self.assertIsNone(component["file_licences"][0]["expression"])
        for overrides in ({"revision": "0" * 40}, {"unreviewed": True},
                          {"identity_problems": ["unverified"]}):
            with self.subTest(overrides=overrides):
                component = self.component([name], **overrides)
                self.assertTrue(self.resolve_with_simulated_pin(component))
                self.assertIsNone(component["licence"])
                self.assertFalse(component["file_licences"][0]["trusted"])
                self.assertIsNone(component["file_licences"][0]["expression"])
        self.git("remote", "set-url", "origin", "https://example.com/mynewt-nimble.git")
        component = self.component([name])
        self.assertTrue(self.resolve_with_simulated_pin(component))
        self.assertIsNone(component["licence"])
        self.assertFalse(component["file_licences"][0]["trusted"])
        self.assertIsNone(component["file_licences"][0]["expression"])

    def test_mixed_file_spdx_attribution_is_retained(self):
        component = self.component(self.EXPECTED)
        self.assertEqual(self.resolve_with_simulated_pin(component), [])
        model = {"name": "test-image", "subject": {"image": "firmware.elf"},
                 "issues": [], "components": [component]}
        output = Path(self.temp.name) / "sbom.json"
        write_sbom(model, output, "spdx", root=self.checkout)
        document = json.loads(output.read_text(encoding="utf-8"))
        package = next(item for item in document["packages"] if item["name"] == "mynewt-nimble")
        self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        self.assertEqual(package["licenseConcluded"], "NOASSERTION")
        records = {item["fileName"].removeprefix("./"): item for item in document["files"]}
        self.assertEqual(set(records), set(self.EXPECTED))
        evidence = {rel["spdxElementId"] for rel in document["relationships"]
                    if rel["relatedSpdxElement"] == package["SPDXID"] and rel["relationshipType"] == "OTHER"}
        for name, expected in self.EXPECTED.items():
            with self.subTest(source=name):
                record = records[name]
                self.assertIn(record["SPDXID"], evidence)
                self.assertEqual(spdx_licensing.parse(record["licenseConcluded"]),
                                 spdx_licensing.parse(expected))
                self.assertTrue(record["checksums"])


if __name__ == "__main__":
    unittest.main()
