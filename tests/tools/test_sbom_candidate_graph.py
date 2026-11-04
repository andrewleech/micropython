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
    def test_build_and_repository_graphs(self):
        for image in ("test-image", None):
            with self.subTest(image=image), tempfile.TemporaryDirectory() as tmp:
                model = {
                    "name": image or "micropython-repository",
                    "subject": {"image": image},
                    "issues": [],
                    "components": [
                        component("parent", components=[
                            component("child", components=[component("grandchild")]),
                        ]),
                        component("sibling"),
                    ],
                }
                documents = {}
                for format in ("spdx", "cyclonedx"):
                    path = Path(tmp) / (format + ".json")
                    write_sbom(model, path, format)
                    documents[format] = json.loads(path.read_text(encoding="utf-8"))

                spdx = documents["spdx"]
                spdx_names = {package["SPDXID"]: package["name"] for package in spdx["packages"]}
                root = next(package for package in spdx["packages"] if package["name"] == "MicroPython")
                subject = json.loads(root["annotations"][0]["comment"])
                contains = {
                    (spdx_names[edge["spdxElementId"]], spdx_names[edge["relatedSpdxElement"]])
                    for edge in spdx["relationships"] if edge["relationshipType"] == "CONTAINS"
                }
                build_dependencies = {
                    (spdx_names[edge["spdxElementId"]], spdx_names[edge["relatedSpdxElement"]])
                    for edge in spdx["relationships"] if edge["relationshipType"] == "BUILD_DEPENDENCY_OF"
                }
                nested_edges = {("parent", "child"), ("child", "grandchild")}
                if image:
                    self.assertEqual(contains, nested_edges)
                    self.assertEqual(build_dependencies, {
                        ("parent", "MicroPython"), ("sibling", "MicroPython"),
                    })
                    self.assertEqual(subject["scope"], "build-input")
                    self.assertTrue(subject["scope_description"])
                else:
                    self.assertEqual(contains, nested_edges | {
                        ("MicroPython", "parent"), ("MicroPython", "sibling"),
                    })
                    self.assertEqual(build_dependencies, set())
                    self.assertNotIn("scope", subject)

                cdx = documents["cyclonedx"]
                metadata = cdx["metadata"]
                root = metadata["component"]
                cdx_names = {root["bom-ref"]: "MicroPython"}

                def collect(items):
                    for item in items:
                        cdx_names[item["bom-ref"]] = item["name"]
                        if image:
                            self.assertNotIn("scope", item)
                        collect(item.get("components", []))

                collect(cdx["components"])
                parent = next(item for item in cdx["components"] if item["name"] == "parent")
                self.assertEqual([item["name"] for item in parent["components"]], ["child"])
                self.assertEqual(
                    [item["name"] for item in parent["components"][0]["components"]], ["grandchild"],
                )
                self.assertEqual(set(cdx_names.values()), {
                    "MicroPython", "parent", "child", "grandchild", "sibling",
                })
                cdx_edges = {
                    (cdx_names[edge["ref"]], cdx_names[child])
                    for edge in cdx.get("dependencies", []) for child in edge.get("dependsOn", [])
                }
                properties = {prop["name"]: prop["value"] for prop in root.get("properties", [])}
                if image:
                    self.assertEqual(cdx_edges, nested_edges)
                    self.assertEqual(metadata["lifecycles"], [{"phase": "build"}])
                    self.assertEqual(properties["micropython:scope"], "build-input")
                else:
                    self.assertEqual(cdx_edges, nested_edges | {
                        ("MicroPython", "parent"), ("MicroPython", "sibling"),
                    })
                    self.assertNotIn("lifecycles", metadata)
                    self.assertNotIn("micropython:scope", properties)

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
