#!/usr/bin/env python3
"""Tests for serialized dependency graphs and native USB scopes."""

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
from sbom_writers import write_sbom


def component(name, **fields):
    item = {
        "name": name,
        "version": None,
        "licence": "MIT",
        "licence_texts": {},
        "licence_comments": [],
    }
    item.update(fields)
    return item


class TestCandidateGraph(unittest.TestCase):
    def test_spdx_and_cyclonedx_have_the_same_dependency_graph(self):
        model = {
            "name": "test-image",
            "issues": [],
            "components": [
                component("parent", components=[
                    component("child", components=[component("grandchild")]),
                ]),
                component("sibling"),
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            documents = {}
            for format in ("spdx", "cyclonedx"):
                path = Path(tmp) / (format + ".json")
                write_sbom(model, path, format)
                documents[format] = json.loads(path.read_text(encoding="utf-8"))

        spdx = documents["spdx"]
        spdx_names = {package["SPDXID"]: package["name"] for package in spdx["packages"]}
        spdx_edges = {
            (spdx_names[edge["spdxElementId"]], spdx_names[edge["relatedSpdxElement"]])
            for edge in spdx["relationships"] if edge["relationshipType"] == "CONTAINS"
        }
        cdx = documents["cyclonedx"]
        cdx_names = {cdx["metadata"]["component"]["bom-ref"]: "MicroPython"}

        def collect(items):
            for item in items:
                cdx_names[item["bom-ref"]] = item["name"]
                collect(item.get("components", []))

        collect(cdx["components"])
        cdx_edges = {
            (cdx_names[edge["ref"]], cdx_names[child])
            for edge in cdx["dependencies"] for child in edge.get("dependsOn", [])
        }
        expected = {
            ("MicroPython", "parent"),
            ("MicroPython", "sibling"),
            ("parent", "child"),
            ("child", "grandchild"),
        }
        self.assertEqual(spdx_edges, expected)
        self.assertEqual(cdx_edges, expected)

    def test_stm32_usb_glue_and_usbdev_have_distinct_native_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(repo), *args], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ).stdout.strip()

            def put(path, content):
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return target

            git("init", "-q")
            put(".gitmodules", "")
            glue = put("ports/stm32/usbd_cdc_interface.c", "int glue;\n")
            core = put("ports/stm32/usbdev/core/src/usbd_core.c", "int core;\n")
            usb_class = put("ports/stm32/usbdev/class/src/usbd_cdc_msc_hid.c", "int usb_class;\n")
            git("add", "ports")
            tree = git("write-tree")
            pin = git("rev-parse", tree + ":ports/stm32/usbdev")
            licence = "LicenseRef-MCD-ST-Liberty-SW-License-Agreement-V2 AND MIT"
            put("LICENSE.spdx.yaml", yaml.safe_dump({"packages": [
                {"name": "micropython", "packageFileName": ".", "licenseDeclared": "MIT"},
                {"name": "stm32-usbd-sources", "packageFileName": "ports/stm32/usbd*.c",
                 "licenseDeclared": licence},
                {"name": "stm32-usbdev", "packageFileName": "ports/stm32/usbdev",
                 "licenseDeclared": licence, "externalRefs": [{
                     "referenceCategory": "PERSISTENT-ID", "referenceType": "gitoid",
                     "referenceLocator": "gitoid:tree:sha1:" + pin,
                 }]},
            ], "hasExtractedLicensingInfos": [{
                "licenseId": "LicenseRef-MCD-ST-Liberty-SW-License-Agreement-V2",
                "extractedText": "ST Liberty agreement notice.",
            }]}))
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            database = put("build/compile_commands.json", json.dumps([
                {"directory": str(repo), "file": str(source), "command": "cc -c " + str(source)}
                for source in (glue, core, usb_class)
            ]))
            args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[], root=[],
                                   image="test-image", offline=True)
            document = _build_document(repo, args)
            components = {item["name"]: item for item in document["components"]}
            self.assertEqual(set(components), {"stm32-usbd-sources", "stm32-usbdev"})
            for name, path, sources in (
                ("stm32-usbd-sources", "ports/stm32/usbd*.c", ["ports/stm32/usbd_cdc_interface.c"]),
                ("stm32-usbdev", "ports/stm32/usbdev", [
                    "ports/stm32/usbdev/core/src/usbd_core.c",
                    "ports/stm32/usbdev/class/src/usbd_cdc_msc_hid.c",
                ]),
            ):
                with self.subTest(name=name):
                    item = components[name]
                    self.assertEqual(item["path"], path)
                    self.assertCountEqual(item["sources"], sources)
                    self.assertEqual(item["selection"], ["native"])
                    self.assertEqual(item["licence_declared"], licence)
                    self.assertEqual(item["revision"], git("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
