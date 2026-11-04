#!/usr/bin/env python3
"""Tests for selected build dependency versions and package identities."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from packageurl import PackageURL

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from sbom import ExtractorError, _build_document
from sbom_writers import write_sbom


DEPENDENCIES = {
    "mbedtls": (
        "https://github.com/ARMmbed/mbedtls.git",
        "include/mbedtls/build_info.h",
        '#define MBEDTLS_VERSION_STRING "3.6.2"\n',
        "3.6.2", "pkg:github/Mbed-TLS/mbedtls@{}",
    ),
    "lwip": (
        "https://github.com/lwip-tcpip/lwip.git",
        "src/include/lwip/init.h",
        "#define LWIP_VERSION_MAJOR 2\n#define LWIP_VERSION_MINOR 2\n#define LWIP_VERSION_REVISION 0\n",
        "2.2.0", "pkg:github/lwip-tcpip/lwip@{}",
    ),
    "tinyusb": (
        "https://github.com/micropython/tinyusb.git",
        "src/tusb_option.h",
        "#define TUSB_VERSION_MAJOR 0\n#define TUSB_VERSION_MINOR 17\n#define TUSB_VERSION_REVISION 0\n",
        "0.17.0", "pkg:github/micropython/tinyusb@{}",
    ),
    "pico-sdk": (
        "https://github.com/raspberrypi/pico-sdk.git",
        "pico_sdk_version.cmake",
        'set(PICO_SDK_VERSION_MAJOR 2)\nset(PICO_SDK_VERSION_MINOR 1)\n'
        'set(PICO_SDK_VERSION_REVISION 0)\nset(PICO_SDK_VERSION_PRE_RELEASE_ID "rc1")\n',
        "2.1.0-rc1", "pkg:github/raspberrypi/pico-sdk@{}",
    ),
}


class TestCandidateBuildVersions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.put(self.repo / ".gitignore", "build/\n")
        self.put(self.repo / ".gitmodules", "")
        self.commit(self.repo)

    @staticmethod
    def git(root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()

    @staticmethod
    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def submodule(self, name, url, version_path=None, version_text=None):
        origin = self.base / (name + "-origin")
        origin.mkdir()
        self.git(origin, "init", "-q")
        self.put(origin / "src/selected.c", "// SPDX-License-Identifier: MIT\nint selected;\n")
        if version_path is not None:
            self.put(origin / version_path, version_text)
        self.commit(origin)
        rel = "lib/" + name
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(origin), rel)
        self.git(self.repo, "config", "-f", ".gitmodules", "submodule." + rel + ".url", url)
        self.commit(self.repo)
        root = self.repo / rel
        return root, self.git(root, "rev-parse", "HEAD")

    def document(self, source, roots=(), idf=None):
        build = self.repo / "build"
        obj = "build/selected.o"
        dep = "build/selected.P"
        self.put(self.repo / dep, f"{obj}: {source}\n")
        database = self.put(build / "compile_commands.json", json.dumps([{
            "directory": str(self.repo), "file": str(source),
            "arguments": ["cc", "-c", str(source), "-o", obj, "-MF", dep],
        }]))
        if idf is not None:
            self.put(build / "project_description.json", json.dumps({
                "idf_path": str(idf), "project_path": str(self.repo), "build_dir": str(build),
                "build_component_info": {source.parent.name: {
                    "dir": str(source.parent), "sources": [str(source)],
                    "type": "LIBRARY", "target": "___idf_" + source.parent.name,
                }},
            }))
        args = SimpleNamespace(compdb=[str(database)], exclude=[], frozen=[], root=list(roots),
                               image="test-image", offline=True)
        return _build_document(self.repo, args)

    def output_fields(self, document, name):
        output = {}
        for format in ("spdx", "cyclonedx"):
            path = self.base / (format + ".json")
            write_sbom(document, path, format, root=self.repo)
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data["packages"] if format == "spdx" else data["components"]
            output[format] = next(item for item in entries if item["name"] == name)
        return output["spdx"], output["cyclonedx"]

    def assert_output_identity(self, document, component, version, purl):
        purl = PackageURL.from_string(purl).to_string() if purl else None
        spdx, cdx = self.output_fields(document, component["name"])
        self.assertEqual(spdx.get("versionInfo"), version)
        self.assertEqual(cdx.get("version"), version)
        refs = [ref["referenceLocator"] for ref in spdx.get("externalRefs", [])
                if ref["referenceType"] == "purl"]
        self.assertEqual(refs, [purl] if purl else [])
        self.assertEqual(cdx.get("purl"), purl)

    def assert_output_cpe(self, document, component, expected):
        spdx, cdx = self.output_fields(document, component["name"])
        refs = [ref["referenceLocator"] for ref in spdx.get("externalRefs", [])
                if ref["referenceType"] == "cpe23Type"]
        self.assertEqual(refs, [expected] if expected else [])
        self.assertEqual(cdx.get("cpe"), expected)

    def assert_output_revision(self, document, component, revision):
        spdx, cdx = self.output_fields(document, component["name"])
        provenance = json.loads(spdx["annotations"][0]["comment"])
        properties = {item["name"]: item["value"] for item in cdx.get("properties", [])}
        self.assertEqual(provenance.get("revision"), revision)
        self.assertEqual(json.loads(properties["micropython:revision"]), revision)

    def test_known_selected_submodules_publish_extracted_versions_and_revision_purls(self):
        for name, (url, version_path, contents, version, purl_template) in DEPENDENCIES.items():
            with self.subTest(name=name):
                root, revision = self.submodule(name, url, version_path, contents)
                document = self.document(root / "src/selected.c")
                self.assertEqual(len(document["components"]), 1)
                component = document["components"][0]
                purl = PackageURL.from_string(purl_template.format(revision)).to_string()
                self.assertEqual(component["revision"], revision)
                self.assertEqual(component["version"], version)
                self.assertEqual(component["purl"], purl)
                self.assert_output_identity(document, component, version, purl)
                vendors = {"mbedtls": ("arm", "mbed_tls"), "lwip": ("lwip_project", "lwip"),
                           "tinyusb": ("tinyusb_project", "tinyusb")}
                cpe = (f"cpe:2.3:a:{vendors[name][0]}:{vendors[name][1]}:{version}:*:*:*:*:*:*:*"
                       if name in vendors else None)
                self.assert_output_cpe(document, component, cpe)

    def test_explicit_root_for_inventory_source_keeps_version(self):
        url, path, contents, version, template = DEPENDENCIES["mbedtls"]
        root, revision = self.submodule("mbedtls", url, path, contents)
        document = self.document(root / "src/selected.c", [f"mbedtls={root}"])
        component = document["components"][0]
        self.assertEqual(component["revision"], revision)
        self.assert_output_identity(document, component, version, template.format(revision))

    def test_scoped_inventory_source_keeps_version_and_upstream_identity(self):
        url, path, contents, version, template = DEPENDENCIES["tinyusb"]
        root, revision = self.submodule("tinyusb", url, path, contents)
        self.put(self.repo / "LICENSE.spdx.yaml", (
            "packages:\n"
            "  - name: micropython\n    packageFileName: .\n    licenseDeclared: MIT\n"
            "  - name: tinyusb\n    packageFileName: lib/tinyusb\n    licenseDeclared: MIT\n"
            f"    downloadLocation: git+{url}@{revision}\n"
            "  - name: tinyusb-src\n    packageFileName: lib/tinyusb/src\n    licenseDeclared: MIT\n"
        ))
        self.commit(self.repo)
        document = self.document(root / "src/selected.c")
        component = document["components"][0]
        self.assertEqual(component["name"], "tinyusb-src")
        self.assertEqual(component["revision"], revision)
        self.assert_output_identity(document, component, version, template.format(revision))

    def test_sha_is_revision_not_a_release_version(self):
        root, revision = self.submodule("unknown", "https://github.com/example/unknown.git")
        document = self.document(root / "src/selected.c")
        component = document["components"][0]
        self.assertEqual(component["revision"], revision)
        self.assertIsNone(component["version"])
        self.assert_output_identity(document, component, None, f"pkg:github/example/unknown@{revision}")

    def test_dirty_or_untracked_selected_source_has_no_inventory_identity(self):
        url, path, contents, _, _ = DEPENDENCIES["tinyusb"]
        root, _ = self.submodule("tinyusb", url, path, contents)
        for state in ("dirty", "untracked"):
            with self.subTest(state=state):
                source = root / ("src/selected.c" if state == "dirty" else "src/extra.c")
                self.put(source, "// SPDX-License-Identifier: MIT\nint changed;\n")
                document = self.document(source)
                component = document["components"][0]
                self.assertIsNone(component.get("revision"))
                self.assert_output_identity(document, component, None, None)

    def test_untrusted_or_missing_version_input_never_supplies_a_release(self):
        url, path, contents, _, _ = DEPENDENCIES["lwip"]
        root, revision = self.submodule("lwip", url, path, contents)
        header = root / path
        for state in ("dirty", "untracked"):
            with self.subTest(state=state):
                if state == "untracked":
                    self.git(root, "rm", "--cached", path)
                self.put(header, contents.replace("MAJOR 2", "MAJOR 9"))
                document = self.document(root / "src/selected.c")
                component = document["components"][0]
                self.assertEqual(component["revision"], revision)
                self.assertIsNone(component.get("version"))
                self.assertIsNone(component.get("purl"))
                self.assertNotIn("cpe", component)
                self.assertTrue(any(path in issue for issue in document["issues"]), document["issues"])
                self.assert_output_identity(document, component, None, None)
                self.assert_output_cpe(document, component, None)
        header.unlink()
        with self.assertRaises(ExtractorError):
            self.document(root / "src/selected.c")

    def test_explicit_vendored_root_retains_parent_revision(self):
        root = self.repo / "lib/oofatfs"
        source = self.put(root / "ff.c", "// SPDX-License-Identifier: MIT\nint fatfs;\n")
        self.put(root / "ff.h", "/* FatFs - Generic FAT Filesystem module R0.13c */\n#define FF_DEFINED 86604\n")
        self.commit(self.repo)
        revision = self.git(self.repo, "rev-parse", "HEAD")
        document = self.document(source, [f"oofatfs={root}"])
        component = document["components"][0]
        self.assertEqual(component["revision"], revision)
        self.assert_output_revision(document, component, revision)
        self.assert_output_identity(document, component, "R0.13c-rev86604",
                                    "pkg:github/micropython/oofatfs")
        ordinary = self.document(source)
        self.assertEqual(ordinary["components"][0]["revision"], revision)
        self.assert_output_revision(ordinary, ordinary["components"][0], revision)
        self.put(root / "ff.h", "/* FatFs - Generic FAT Filesystem module R9.99 */\n#define FF_DEFINED 86604\n")
        dirty_version = self.document(source, [f"oofatfs={root}"])
        self.assertIsNone(dirty_version["components"][0].get("revision"))
        self.assert_output_identity(dirty_version, dirty_version["components"][0], None, None)
        self.assert_output_revision(dirty_version, dirty_version["components"][0], None)
        self.put(root / "ff.h", "/* FatFs - Generic FAT Filesystem module R0.13c */\n#define FF_DEFINED 86604\n")
        self.put(source, "// SPDX-License-Identifier: MIT\nint modified;\n")
        dirty_source = self.document(source, [f"oofatfs={root}"])
        self.assertIsNone(dirty_source["components"][0].get("revision"))
        self.assert_output_identity(dirty_source, dirty_source["components"][0], None, None)
        self.assert_output_revision(dirty_source, dirty_source["components"][0], None)

    def test_libm_dbl_website_is_not_a_repository_purl(self):
        root = self.repo / "lib/libm_dbl"
        source = self.put(root / "math.c", "// SPDX-License-Identifier: MIT\nint math_source;\n")
        self.put(root / "README", "copied from the musl library,\nv1.1.16, and, unless otherwise\n")
        self.commit(self.repo)
        revision = self.git(self.repo, "rev-parse", "HEAD")
        document = self.document(source)
        component = document["components"][0]
        self.assertEqual(component["revision"], revision)
        self.assert_output_revision(document, component, revision)
        self.assert_output_identity(document, component, "1.1.16", None)
        self.assertIn({"type": "website", "url": "https://musl.libc.org"},
                      component["externalReferences"])
        _, cdx = self.output_fields(document, component["name"])
        self.assertIn({"type": "website", "url": "https://musl.libc.org"},
                      cdx["externalReferences"])

    def test_unrelated_dirty_stm32_family_keeps_selected_family_version(self):
        root, _ = self.submodule("stm32lib", "https://github.com/micropython/stm32lib.git")
        for family, version in (("STM32F4", "04"), ("STM32H7", "07")):
            header = root / f"CMSIS/{family}/Include/{family.lower()}.h"
            self.put(header, f"#define DEVICE_VERSION_MAIN (0x{version})\n"
                             "#define DEVICE_VERSION_SUB1 (0x01)\n"
                             "#define DEVICE_VERSION_SUB2 (0x02)\n")
        source = self.put(root / "CMSIS/STM32F4/selected.c",
                          "// SPDX-License-Identifier: MIT\nint selected_f4;\n")
        self.commit(root)
        self.commit(self.repo)
        self.put(root / "CMSIS/STM32H7/Include/stm32h7.h",
                 "#define DEVICE_VERSION_MAIN (0x09)\n"
                 "#define DEVICE_VERSION_SUB1 (0x01)\n#define DEVICE_VERSION_SUB2 (0x02)\n")
        document = self.document(source)
        children = document["components"][0]["components"]
        self.assertEqual([child["name"] for child in children], ["STM32F4"])
        self.assertEqual(children[0]["version"], "4.1.2")
        spdx_path = self.base / "family-spdx.json"
        cdx_path = self.base / "family-cdx.json"
        write_sbom(document, spdx_path, "spdx", root=self.repo)
        write_sbom(document, cdx_path, "cyclonedx", root=self.repo)
        spdx = json.loads(spdx_path.read_text(encoding="utf-8"))
        cdx = json.loads(cdx_path.read_text(encoding="utf-8"))
        self.assertEqual(next(p for p in spdx["packages"] if p["name"] == "STM32F4")["versionInfo"], "4.1.2")
        self.assertEqual(cdx["components"][0]["components"][0]["version"], "4.1.2")

    def test_repository_inventory_refuses_mismatched_checkout_metadata(self):
        url, path, contents, version, template = DEPENDENCIES["lwip"]
        root, revision = self.submodule("lwip", url, path, contents)
        args = SimpleNamespace(compdb=[], exclude=[], frozen=[], root=[], image=None, offline=True)
        with patch("sbom.VENDORED", []):
            clean = _build_document(self.repo, args)
            component = clean["components"][0]
            self.assertEqual(component["revision"], revision)
            self.assert_output_identity(clean, component, version, template.format(revision))
            self.assert_output_cpe(clean, component, f"cpe:2.3:a:lwip_project:lwip:{version}:*:*:*:*:*:*:*")
            self.put(root / path, contents.replace("MAJOR 2", "MAJOR 9"))
            dirty = _build_document(self.repo, args)
            dirty_component = dirty["components"][0]
            self.assertEqual(dirty_component["revision"], revision)
            self.assert_output_identity(dirty, dirty_component, None, None)
            self.assert_output_cpe(dirty, dirty_component, None)
            self.assertTrue(dirty["issues"])
            self.commit(root)
            mismatch = _build_document(self.repo, args)
        component = mismatch["components"][0]
        self.assertIsNone(component.get("revision"))
        self.assert_output_identity(mismatch, component, None, None)
        self.assert_output_cpe(mismatch, component, None)
        self.assertTrue(mismatch["issues"])

    def test_untagged_subject_does_not_publish_commit_as_release(self):
        root, _ = self.submodule("unknown", "https://github.com/example/unknown.git")
        document = self.document(root / "src/selected.c")
        self.assertIsNone(document["subject"]["repo_version"])
        revision = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(document["subject"]["repo_revision"], revision)
        spdx_path = self.base / "subject-spdx.json"
        cdx_path = self.base / "subject-cdx.json"
        write_sbom(document, spdx_path, "spdx", root=self.repo)
        write_sbom(document, cdx_path, "cyclonedx", root=self.repo)
        spdx = json.loads(spdx_path.read_text(encoding="utf-8"))
        cdx = json.loads(cdx_path.read_text(encoding="utf-8"))
        subject = next(p for p in spdx["packages"] if p["name"] == "MicroPython")
        self.assertNotIn("versionInfo", subject)
        self.assertNotIn("version", cdx["metadata"]["component"])

    def test_idf_component_name_and_version_header_do_not_prove_upstream_identity(self):
        idf = self.base / "esp-idf"
        idf.mkdir()
        self.git(idf, "init", "-q")
        source = self.put(idf / "components/mbedtls/selected.c", "// SPDX-License-Identifier: MIT\nint selected;\n")
        self.put(idf / "components/mbedtls/include/mbedtls/build_info.h",
                 '#define MBEDTLS_VERSION_STRING "99.1.0"\n')
        self.commit(idf)
        revision = self.git(idf, "rev-parse", "HEAD")
        document = self.document(source, [f"esp-idf={idf}"], idf=idf)
        component = document["components"][0]
        self.assertEqual(component["name"], "mbedtls")
        self.assertEqual(component["revision"], revision)
        self.assertIsNone(component.get("version"))
        self.assertIsNone(component.get("purl"))
        self.assertNotIn("cpe", component)
        self.assert_output_identity(document, component, None, None)


if __name__ == "__main__":
    unittest.main()
