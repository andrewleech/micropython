#!/usr/bin/env python3
"""Tests for the pinned lwIP source licence review."""

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


class TestReviewedLwip(unittest.TestCase):
    URL = "https://github.com/lwip-tcpip/lwip.git"
    REF = "LicenseRef-PPP-Komulainen"
    SOURCES = {
        "src/core/tcp.c": "BSD-3-Clause",
        "src/netif/ppp/eui64.c": REF,
        "src/netif/ppp/pppos.c": "BSD-3-Clause",
    }

    @classmethod
    def setUpClass(cls):
        cls.metadata = yaml.safe_load((REPO / "LICENSE.spdx.yaml").read_text(encoding="utf-8"))

    def entry(self):
        entries = [item for item in self.metadata["external_repositories"] if item["url"] == self.URL]
        self.assertEqual(len(entries), 1)
        return entries[0]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        self.checkout = self.project / "lwip"
        self.checkout.mkdir()
        self.git("init", "-q")
        self.git("remote", "add", "origin", self.URL)
        ppp_notice = """Copyright (c) 1999 Tommi Komulainen.  All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in
   the documentation and/or other materials provided with the
   distribution.
3. The name(s) of the authors of this software must not be used to
   endorse or promote products derived from this software without
   prior written permission.
4. Redistributions of any form whatsoever must retain the following
   acknowledgment:
   "This product includes software developed by Tommi Komulainen
    <Tommi.Komulainen@iki.fi>".

THE AUTHORS OF THIS SOFTWARE DISCLAIM ALL WARRANTIES WITH REGARD TO
THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS, IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY
SPECIAL, INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING
OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""
        bsd_notice = """Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. The name of the author may not be used to endorse or promote products
   derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR IMPLIED
WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT
SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY
OF SUCH DAMAGE.
"""
        for name in self.SOURCES:
            source = self.checkout / name
            source.parent.mkdir(parents=True, exist_ok=True)
            notice = ppp_notice if name.endswith("/eui64.c") else bsd_notice
            source.write_text("/*\n" + notice + "*/\nint fixture;\n", encoding="utf-8")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        self.fixture_revision = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.checkout), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def resolve(self, declaration_revision, component_revision=None):
        # The fixture has a different commit from the reviewed checkout.
        metadata = {
            "packages": [],
            "external_repositories": [{**self.entry(), "commit": declaration_revision}],
            "hasExtractedLicensingInfos": self.metadata["hasExtractedLicensingInfos"],
        }
        (self.project / "LICENSE.spdx.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
        component = {
            "name": "lwip", "version": None, "path": str(self.checkout),
            "owner_root": str(self.checkout), "declaration_root": str(self.checkout),
            "revision": component_revision or self.fixture_revision,
            "sources": [str(self.checkout / path) for path in self.SOURCES],
        }
        issues = resolve_licences(self.project, [component])
        return component, issues

    def test_mixed_files_have_individual_spdx_attribution(self):
        component, issues = self.resolve(self.fixture_revision)
        self.assertEqual(issues, [])
        self.assertTrue(component["licence_mixed"])
        self.assertIsNone(component["licence"])
        self.assertIsNone(component["licence_declared"])
        files = {Path(item["source"]).relative_to(self.checkout).as_posix(): item
                 for item in component["file_licences"]}
        self.assertEqual(set(files), set(self.SOURCES))
        for path, expression in self.SOURCES.items():
            with self.subTest(path=path):
                self.assertTrue(files[path]["trusted"])
                self.assertEqual(files[path]["expression"], expression)
                self.assertIn("reviewed declaration", files[path]["provenance"])

        model = {"name": "test-image", "subject": {"image": "firmware.elf"},
                 "issues": issues, "components": [component]}
        output = self.base / "sbom.json"
        write_sbom(model, output, "spdx", root=self.project)
        document = json.loads(output.read_text(encoding="utf-8"))
        package = next(item for item in document["packages"] if item["name"] == "lwip")
        self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        self.assertEqual(package["licenseConcluded"], "NOASSERTION")
        self.assertNotIn("licenseInfoFromFiles", package)
        records = {item["fileName"]: item for item in document["files"]}
        for path, expression in self.SOURCES.items():
            with self.subTest(path=path):
                record = next(item for name, item in records.items() if name.endswith(path))
                self.assertEqual(record["licenseConcluded"], expression)
                self.assertTrue(record["checksums"])
                self.assertEqual(record["licenseInfoInFiles"], ["NOASSERTION"])
                self.assertTrue(any(
                    relationship["spdxElementId"] == record["SPDXID"]
                    and relationship["relatedSpdxElement"] == package["SPDXID"]
                    and relationship["relationshipType"] == "OTHER"
                    for relationship in document["relationships"]
                ))
        details = {item["licenseId"]: item for item in document["hasExtractedLicensingInfos"]}
        self.assertIn("Tommi Komulainen", details[self.REF]["extractedText"])
        self.assertTrue(details[self.REF].get("seeAlsos"))
        self.assertNotIn("BSD-3-Clause AND " + self.REF, json.dumps(package))

    def test_exact_pin_mismatch_blocks_reviewed_assertions(self):
        for declaration, revision in ((self.entry()["commit"], self.fixture_revision),
                                      (self.fixture_revision, "0" * 40)):
            with self.subTest(declaration=declaration, revision=revision):
                component, issues = self.resolve(declaration, revision)
                self.assertTrue(issues)
                self.assertIsNone(component["licence"])
                self.assertIsNone(component["licence_declared"])
                self.assertEqual(len(component["file_licences"]), len(self.SOURCES))
                for item in component["file_licences"]:
                    self.assertIsNone(item["expression"])
                    self.assertFalse(item["trusted"])


if __name__ == "__main__":
    unittest.main()
