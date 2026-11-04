#!/usr/bin/env python3
"""Tests for native source selection from compiler dependency files."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from sbom import _build_document
from sbom_inputs import read_inputs


class TestCandidateDepfiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.put(".gitmodules", "")
        self.put("LICENSE.spdx.yaml", "packages:\n  - name: micropython\n    packageFileName: .\n    licenseDeclared: MIT\n")
        self.put("src/main.c", "int main(void) { return 0; }\n")
        self.put("src/with space#$.c", "int included;\n")
        self.put("src/header.h", "")
        self.put("src/phony.c", "")
        subprocess.run(["git", "-C", str(self.repo), "add", ".gitmodules",
                        "LICENSE.spdx.yaml", "src"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Test",
                        "-c", "user.email=test@example.com", "commit", "-qm",
                        "Add fixture sources"], check=True)

    def put(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def database(self, entries):
        return self.put("build/compile_commands.json", json.dumps(entries))

    def entry(self, source="src/main.c", obj="build/main.o", dep="build/main.P"):
        return {"directory": str(self.repo), "file": source,
                "arguments": ["cc", "-c", source, "-o", obj, "-MF", dep]}

    def test_included_sources_escaped_paths_and_first_rule(self):
        self.put("src/main.c", "int main(void) { return 0; }\n")
        included = self.put("src/with space#$.c", "int included;\n")
        self.put("src/header.h", "")
        self.put("build/generated.c", "")
        self.put("build/genhdr/generated.c", "")
        outside_build = self.put("genhdr/source.c", "")
        self.put("src/phony.c", "")
        self.put("build/main.P", "build/main.o: src/main.c src/with\\ space\\#$$.c \\\n"
                 " src/header.h build/generated.c build/genhdr/generated.c genhdr/source.c src/with\\ space\\#$$.c\n"
                 "src/header.h: src/phony.c\n")
        db = self.database([self.entry(), self.entry()])
        evidence = []
        records = read_inputs(self.repo, [db], [self.repo / "build"], [], evidence)
        self.assertEqual([record["source"] for record in records].count(included), 1)
        self.assertEqual({record["source"] for record in records},
                         {self.repo / "src/main.c", included, outside_build})
        record = next(record for record in records if record["source"] == included)
        self.assertEqual(record["input_kind"], "depfile")
        self.assertEqual(record["originating_source"], self.repo / "src/main.c")
        self.assertEqual(record["object"], self.repo / "build/main.o")
        self.assertEqual(record["depfile"], self.repo / "build/main.P")
        self.assertTrue(all(item["status"] == "read" for item in evidence))
        args = SimpleNamespace(compdb=[str(db)], exclude=[str(self.repo / "build")], frozen=[], root=[],
                               image="test-image", offline=True)
        document = _build_document(self.repo, args)
        source_evidence = [item for component in document["components"]
                           for item in component["source_evidence"]]
        self.assertIn({"source": "src/with space#$.c", "kind": "included",
                       "originating_source": "src/main.c", "object": "build/main.o",
                       "depfile": "build/main.P"}, source_evidence)

    def test_missing_dependency_outside_excluded_build_is_reported(self):
        self.put("build/main.P", "build/main.o: src/main.c build/missing.c genhdr/missing.c\n")
        db = self.database([self.entry()])
        evidence = []
        read_inputs(self.repo, [db], [self.repo / "build"], [], evidence)
        self.assertEqual(evidence[0]["status"], "incomplete")
        self.assertIn(str(self.repo / "genhdr/missing.c"), evidence[0]["error"])
        self.assertNotIn(str(self.repo / "build/missing.c"), evidence[0]["error"])

    def test_missing_depfile_is_reported_in_document(self):
        self.put("src/main.c", "int main(void) { return 0; }\n")
        db = self.database([self.entry()])
        args = SimpleNamespace(compdb=[str(db)], exclude=[], frozen=[], root=[],
                               image="test-image", offline=True)
        document = _build_document(self.repo, args)
        self.assertTrue(any("depfile missing for src/main.c: build/main.P" in issue
                            for issue in document["issues"]))


if __name__ == "__main__":
    unittest.main()
