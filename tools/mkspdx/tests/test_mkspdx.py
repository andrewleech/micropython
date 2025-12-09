#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""Unit tests for mkspdx SBOM generator."""

import json
import os
import tempfile
import unittest
from pathlib import Path

# Add parent to path for imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mkspdx.mapfile import parse_map_file, object_to_source
from mkspdx.scanner import (
    scan_spdx_headers,
    scan_traditional_copyright,
    validate_spdx_expression,
)
from mkspdx.license import load_license_tree, normalize_license_id, lookup_license_for_path
from mkspdx.spdx import generate_spdx_tv, generate_spdx_json, validate_spdx_document
from mkspdx.util import compute_file_hash, normalize_path


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestMapfileParsing(unittest.TestCase):
    """Tests for GNU linker map file parsing."""

    def test_parse_sample_map(self):
        """Test parsing the sample map file."""
        map_file = FIXTURES_DIR / "sample.map"
        if not map_file.exists():
            self.skipTest("Sample map file not found")

        objects = parse_map_file(map_file)

        # Should find archive members
        archives = [o for o in objects if "archive" in o]
        self.assertGreater(len(archives), 0, "Should find archive members")

        # Check for specific expected entries
        object_names = [o.get("object", "") for o in objects]
        self.assertTrue(
            any("gc.o" in name for name in object_names), "Should find gc.o in map file"
        )

    def test_parse_empty_map(self):
        """Test parsing an empty map file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".map", delete=False) as f:
            f.write("")
            temp_path = f.name

        try:
            objects = parse_map_file(temp_path)
            self.assertEqual(objects, [])
        finally:
            os.unlink(temp_path)

    def test_object_to_source_basic(self):
        """Test basic object to source file mapping."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            build_dir = tmpdir / "build"
            build_dir.mkdir()

            # Create a source file
            source_file = tmpdir / "test.c"
            source_file.write_text("int main() {}")

            result = object_to_source("test.o", build_dir, [tmpdir])
            self.assertIsNotNone(result)
            self.assertTrue(result.endswith("test.c"))


class TestSpdxScanning(unittest.TestCase):
    """Tests for SPDX header scanning."""

    def test_scan_file_with_spdx(self):
        """Test scanning a file with SPDX headers."""
        source_file = FIXTURES_DIR / "source_with_spdx.c"
        if not source_file.exists():
            self.skipTest("Fixture file not found")

        result = scan_spdx_headers(source_file)

        self.assertEqual(result["license"], "MIT")
        self.assertEqual(len(result["copyrights"]), 2)
        self.assertIn("2020-2025 Test Author <test@example.com>", result["copyrights"])

    def test_scan_file_without_spdx(self):
        """Test scanning a file without SPDX headers."""
        source_file = FIXTURES_DIR / "source_no_spdx.c"
        if not source_file.exists():
            self.skipTest("Fixture file not found")

        result = scan_spdx_headers(source_file)

        self.assertIsNone(result["license"])
        self.assertEqual(result["copyrights"], [])

    def test_scan_complex_license(self):
        """Test scanning a file with complex license expression."""
        source_file = FIXTURES_DIR / "source_complex_license.c"
        if not source_file.exists():
            self.skipTest("Fixture file not found")

        result = scan_spdx_headers(source_file)

        self.assertEqual(result["license"], "Apache-2.0 OR MIT")

    def test_scan_traditional_copyright(self):
        """Test fallback traditional copyright scanning."""
        source_file = FIXTURES_DIR / "source_no_spdx.c"
        if not source_file.exists():
            self.skipTest("Fixture file not found")

        result = scan_traditional_copyright(source_file)

        self.assertGreater(len(result), 0)
        self.assertTrue(any("2020" in c for c in result))

    def test_validate_spdx_expression_valid(self):
        """Test SPDX expression validation with valid expressions."""
        valid_expressions = [
            "MIT",
            "Apache-2.0",
            "GPL-3.0-or-later",
            "MIT AND Apache-2.0",
            "MIT OR Apache-2.0",
            "(MIT OR Apache-2.0) AND GPL-3.0-only",
            "Apache-2.0 WITH LLVM-exception",
        ]
        for expr in valid_expressions:
            self.assertTrue(
                validate_spdx_expression(expr), f"Should accept valid expression: {expr}"
            )

    def test_validate_spdx_expression_invalid(self):
        """Test SPDX expression validation with invalid expressions."""
        invalid_expressions = [
            "",
            "AND MIT",
            "MIT AND",
            "MIT OR OR Apache",
            "(MIT",
            "MIT)",
        ]
        for expr in invalid_expressions:
            self.assertFalse(
                validate_spdx_expression(expr), f"Should reject invalid expression: {expr}"
            )


class TestLicenseTree(unittest.TestCase):
    """Tests for license tree parsing."""

    def test_load_license_tree(self):
        """Test loading the MicroPython LICENSE file."""
        license_file = Path(__file__).parent.parent.parent.parent.parent / "LICENSE"
        if not license_file.exists():
            self.skipTest("LICENSE file not found")

        tree = load_license_tree(license_file)

        self.assertGreater(len(tree), 0)
        # Should contain known entries
        self.assertIn("/lib/lwip", tree)
        self.assertIn("/lib/tinyusb", tree)

    def test_normalize_license_id(self):
        """Test license ID normalization."""
        test_cases = [
            ("BSD-3-clause", "BSD-3-Clause"),
            ("bsd-3-clause", "BSD-3-Clause"),
            ("Apache 2.0", "Apache-2.0"),
            ("MIT", "MIT"),
            ("zlib", "Zlib"),
        ]
        for input_id, expected in test_cases:
            result = normalize_license_id(input_id)
            self.assertEqual(result, expected, f"normalize_license_id({input_id!r})")

    def test_lookup_license_for_path(self):
        """Test looking up license for a file path."""
        tree = {
            "/": "MIT",
            "/lib": "MIT",
            "/lib/lwip": "BSD-3-Clause",
            "/lib/tinyusb": "MIT",
        }

        self.assertEqual(lookup_license_for_path("py/gc.c", tree), "MIT")
        self.assertEqual(lookup_license_for_path("lib/lwip/src/core.c", tree), "BSD-3-Clause")
        self.assertEqual(lookup_license_for_path("lib/tinyusb/src/usb.c", tree), "MIT")


class TestSpdxOutput(unittest.TestCase):
    """Tests for SPDX document generation."""

    def setUp(self):
        """Set up test fixtures."""
        self.sample_files = [
            {
                "path": "py/gc.c",
                "sha256": "abc123",
                "sha1": "def456",
                "license": "MIT",
                "copyrights": ["2020 Test Author"],
            },
            {
                "path": "lib/lwip/core.c",
                "sha256": "789xyz",
                "sha1": "abc789",
                "license": "BSD-3-Clause",
                "copyrights": [],
            },
        ]

    def test_generate_spdx_tv(self):
        """Test generating SPDX tag-value format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".spdx", delete=False) as f:
            temp_path = f.name

        try:
            generate_spdx_tv(self.sample_files, "test-project", temp_path)

            with open(temp_path, "r") as f:
                content = f.read()

            self.assertIn("SPDXVersion: SPDX-2.3", content)
            self.assertIn("PackageName: test-project", content)
            self.assertIn("FileName: ./py/gc.c", content)
            self.assertIn("LicenseConcluded: MIT", content)
            self.assertIn("FileCopyrightText: 2020 Test Author", content)
        finally:
            os.unlink(temp_path)

    def test_generate_spdx_json(self):
        """Test generating SPDX JSON format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            generate_spdx_json(self.sample_files, "test-project", temp_path)

            with open(temp_path, "r") as f:
                doc = json.load(f)

            self.assertEqual(doc["spdxVersion"], "SPDX-2.3")
            self.assertEqual(doc["packages"][0]["name"], "test-project")
            self.assertEqual(len(doc["files"]), 2)
            self.assertEqual(doc["files"][0]["licenseConcluded"], "MIT")
        finally:
            os.unlink(temp_path)

    def test_validate_generated_spdx_tv(self):
        """Test that generated SPDX tag-value passes validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".spdx", delete=False) as f:
            temp_path = f.name

        try:
            generate_spdx_tv(self.sample_files, "test-project", temp_path)
            is_valid, errors = validate_spdx_document(temp_path)
            self.assertTrue(is_valid, f"Validation errors: {errors}")
        finally:
            os.unlink(temp_path)

    def test_validate_generated_spdx_json(self):
        """Test that generated SPDX JSON passes validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            generate_spdx_json(self.sample_files, "test-project", temp_path)
            is_valid, errors = validate_spdx_document(temp_path)
            self.assertTrue(is_valid, f"Validation errors: {errors}")
        finally:
            os.unlink(temp_path)


class TestUtilities(unittest.TestCase):
    """Tests for utility functions."""

    def test_compute_file_hash(self):
        """Test file hash computation."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("test content")
            temp_path = f.name

        try:
            sha256 = compute_file_hash(temp_path, "sha256")
            sha1 = compute_file_hash(temp_path, "sha1")

            self.assertEqual(len(sha256), 64)  # SHA256 hex length
            self.assertEqual(len(sha1), 40)  # SHA1 hex length
        finally:
            os.unlink(temp_path)

    def test_normalize_path(self):
        """Test path normalization."""
        self.assertEqual(normalize_path("py/gc.c"), "py/gc.c")
        self.assertEqual(normalize_path("py\\gc.c"), "py/gc.c")
        self.assertEqual(normalize_path(Path("py/gc.c")), "py/gc.c")


if __name__ == "__main__":
    unittest.main()
