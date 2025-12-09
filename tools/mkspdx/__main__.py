#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""
Build-time SBOM generator for MicroPython.

This tool generates Software Bill of Materials (SBOM) documents by analyzing
linker map files to determine which source files were actually compiled into
a firmware build, then extracting SPDX license information from those files.

Usage:
    python -m mkspdx --build-dir ports/stm32/build-PYBV10 --output firmware.spdx

Requirements:
    pip install reuse  (optional, for enhanced copyright extraction)
"""

import argparse
import sys
from pathlib import Path

from .mapfile import parse_map_file, object_to_source, find_source_in_tree
from .scanner import scan_spdx_headers, scan_traditional_copyright, try_reuse_copyright
from .license import load_license_tree, lookup_license_for_path
from .spdx import generate_spdx_tv, generate_spdx_json, validate_spdx_document
from .util import compute_file_hashes, normalize_path


def collect_source_files(map_file, build_dir, project_root, lib_dir=None):
    """
    Collect source files from a linker map file.

    Returns list of resolved source file paths.
    """
    object_files = parse_map_file(map_file)
    source_dirs = [project_root, build_dir]
    if lib_dir:
        source_dirs.append(lib_dir)

    source_files = []
    seen = set()

    for obj_entry in object_files:
        obj_path = obj_entry.get("object", "")
        if not obj_path:
            continue

        # Try direct mapping first
        source_path = object_to_source(obj_path, build_dir, source_dirs)

        # Fall back to tree search for library code
        if not source_path:
            source_path = find_source_in_tree(
                obj_path, project_root, [lib_dir] if lib_dir else None
            )

        if source_path and source_path not in seen:
            seen.add(source_path)
            source_files.append(source_path)

    return source_files


def process_source_file(source_path, project_root, license_tree):
    """
    Process a single source file to extract SBOM information.

    Returns dict with file info or None if file doesn't exist.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        return None

    # Compute path relative to project root
    try:
        rel_path = source_path.relative_to(project_root)
    except ValueError:
        rel_path = source_path

    # Compute hashes
    hashes = compute_file_hashes(source_path)

    # Scan for SPDX headers
    spdx_info = scan_spdx_headers(source_path)

    # Try reuse library for copyright if not found via scanning
    if not spdx_info["copyrights"]:
        spdx_info["copyrights"] = try_reuse_copyright(source_path, project_root)

    # Fall back to traditional copyright scanning
    if not spdx_info["copyrights"]:
        spdx_info["copyrights"] = scan_traditional_copyright(source_path)

    # Fall back to license tree for files without SPDX headers
    if not spdx_info["license"]:
        spdx_info["license"] = lookup_license_for_path(rel_path, license_tree, project_root)

    return {
        "path": normalize_path(rel_path),
        "sha256": hashes["sha256"],
        "sha1": hashes["sha1"],
        "license": spdx_info["license"],
        "copyrights": spdx_info["copyrights"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate SBOM from MicroPython build artifacts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate SBOM for STM32 build
    python -m mkspdx --build-dir ports/stm32/build-PYBV10 -o firmware.spdx

    # Generate JSON format SBOM
    python -m mkspdx --build-dir ports/rp2/build-RPI_PICO -o firmware.json --format spdx-json

    # Validate existing SBOM
    python -m mkspdx --validate firmware.spdx
""",
    )
    parser.add_argument(
        "--build-dir",
        help="Path to build directory containing map file",
    )
    parser.add_argument(
        "--map-file",
        help="Path to linker map file (default: auto-detect in build-dir)",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Path to MicroPython source root (default: current directory)",
    )
    parser.add_argument(
        "--lib-dir",
        help="Path to lib/ directory with submodules (default: <project-root>/lib)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output SBOM file path",
    )
    parser.add_argument(
        "--format",
        choices=["spdx-tv", "spdx-json"],
        default="spdx-tv",
        help="Output format (default: spdx-tv)",
    )
    parser.add_argument(
        "--project-name",
        help="Project name for SBOM (default: derived from build-dir)",
    )
    parser.add_argument(
        "--validate",
        metavar="SPDX_FILE",
        help="Validate an existing SPDX document",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Validation mode
    if args.validate:
        is_valid, errors = validate_spdx_document(args.validate)
        if is_valid:
            print(f"Valid SPDX document: {args.validate}")
            sys.exit(0)
        else:
            print(f"Invalid SPDX document: {args.validate}")
            for error in errors:
                print(f"  - {error}")
            sys.exit(1)

    # Generation mode - require build-dir and output
    if not args.build_dir:
        parser.error("--build-dir is required for SBOM generation")
    if not args.output:
        parser.error("--output is required for SBOM generation")

    build_dir = Path(args.build_dir).resolve()
    project_root = Path(args.project_root).resolve()
    lib_dir = Path(args.lib_dir) if args.lib_dir else project_root / "lib"

    if not build_dir.exists():
        print(f"Error: Build directory not found: {build_dir}", file=sys.stderr)
        sys.exit(1)

    # Find map file
    if args.map_file:
        map_file = Path(args.map_file)
    else:
        map_files = list(build_dir.glob("*.map"))
        if not map_files:
            # Try subdirectories
            map_files = list(build_dir.glob("**/*.map"))
        if not map_files:
            print(f"Error: No .map file found in {build_dir}", file=sys.stderr)
            sys.exit(1)
        map_file = map_files[0]

    if not map_file.exists():
        print(f"Error: Map file not found: {map_file}", file=sys.stderr)
        sys.exit(1)

    # Derive project name
    project_name = args.project_name or f"micropython-{build_dir.name}"

    if args.verbose:
        print(f"Build directory: {build_dir}")
        print(f"Map file: {map_file}")
        print(f"Project root: {project_root}")
        print(f"Project name: {project_name}")

    # Load license tree
    license_file = project_root / "LICENSE"
    license_tree = load_license_tree(license_file)
    if args.verbose:
        print(f"Loaded {len(license_tree)} license mappings from {license_file}")

    # Parse map file and collect source files
    print(f"Parsing map file: {map_file}")
    source_files = collect_source_files(map_file, build_dir, project_root, lib_dir)
    print(f"Found {len(source_files)} source files")

    # Process each source file
    files_info = []
    missing_license = 0
    for source_path in source_files:
        file_info = process_source_file(source_path, project_root, license_tree)
        if file_info:
            files_info.append(file_info)
            if not file_info["license"]:
                missing_license += 1
                if args.verbose:
                    print(f"  No license: {file_info['path']}")

    print(f"Processed {len(files_info)} source files")
    if missing_license > 0:
        print(f"Warning: {missing_license} files have no license information")

    # Generate output
    output_path = Path(args.output)
    if args.format == "spdx-tv":
        generate_spdx_tv(files_info, project_name, output_path)
    elif args.format == "spdx-json":
        generate_spdx_json(files_info, project_name, output_path)

    print(f"Generated SBOM: {output_path}")

    # Validate output
    is_valid, errors = validate_spdx_document(output_path)
    if not is_valid:
        print("Warning: Generated document has validation issues:")
        for error in errors:
            print(f"  - {error}")


if __name__ == "__main__":
    main()
