#!/usr/bin/env python3
"""
Extract executable line numbers from .mpy files compiled by mpy-cross.

This script uses mpy-tool.py to parse .mpy files and extract line information
from bytecode, enabling line-level coverage analysis.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _setup_mpy_tool(mpy_tools_dir):
    """
    Import mpy_tool module from the specified directory.

    Args:
        mpy_tools_dir: Path to the tools directory containing mpy-tool.py

    Returns:
        The imported mpy_tool module

    Raises:
        RuntimeError: If mpy-tool.py cannot be found or imported
    """
    mpy_tool_path = os.path.join(mpy_tools_dir, "mpy-tool.py")
    if not os.path.exists(mpy_tool_path):
        raise RuntimeError(
            f"mpy-tool.py not found at {mpy_tool_path}. "
            f"Ensure mpy_tools_dir points to the MicroPython tools/ directory."
        )

    # Ensure the py/ directory is in sys.path for makeqstrdata import
    mpy_root = os.path.dirname(mpy_tools_dir)
    py_path = os.path.join(mpy_root, "py")
    if py_path not in sys.path:
        sys.path.insert(0, py_path)

    spec = importlib.util.spec_from_file_location("mpy_tool", mpy_tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to create module spec for {mpy_tool_path}")

    mpy_tool = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mpy_tool)
    except ModuleNotFoundError as e:
        raise RuntimeError(
            f"Failed to import mpy-tool.py: {e}. "
            f"This may indicate the tools directory or py/ directory is incorrect."
        )

    # Initialize global_qstrs which is required for read_mpy() to work
    mpy_tool.global_qstrs = mpy_tool.GlobalQStrList()

    return mpy_tool


def _extract_lines_from_raw_code(rc, lines):
    """
    Recursively extract executable line numbers from a RawCode's line info table.

    Args:
        rc: A RawCode object (typically RawCodeBytecode)
        lines: A set to accumulate line numbers into

    Returns:
        None (modifies lines in-place)
    """
    try:
        line_info = memoryview(rc.fun_data)[rc.offset_line_info : rc.offset_opcodes]
    except (AttributeError, TypeError):
        pass
    else:
        source_line = 1
        while line_info:
            bc_increment, line_increment, line_info = rc.decode_lineinfo(line_info)
            source_line += line_increment
            if bc_increment > 0 and source_line > 0:
                lines.add(source_line)

    # Recurse into children
    if hasattr(rc, "children"):
        for child in rc.children:
            _extract_lines_from_raw_code(child, lines)


def _compile_to_mpy(py_file, mpy_cross, temp_mpy_file):
    """
    Compile a .py file to .mpy using mpy-cross.

    Args:
        py_file: Path to the .py file
        mpy_cross: Path to the mpy-cross binary
        temp_mpy_file: Path where the .mpy output will be written

    Raises:
        RuntimeError: If compilation fails
    """
    try:
        result = subprocess.run(
            [mpy_cross, "-o", temp_mpy_file, py_file],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"mpy-cross binary not found at {mpy_cross}. "
            f"Ensure the binary exists or provide --mpy-cross argument."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Compilation of {py_file} timed out")

    if result.returncode != 0:
        raise RuntimeError(f"mpy-cross compilation failed for {py_file}: {result.stderr}")


def _find_tools_directory():
    """
    Auto-detect the MicroPython tools directory relative to this script.

    Returns:
        Path to tools directory if found, None otherwise

    Searches from the script's location up through parent directories looking
    for a tools/mpy-tool.py file.
    """
    script_dir = Path(__file__).parent
    current = script_dir

    for _ in range(5):  # Search up to 5 levels up
        tools_candidate = current / "tools" / "mpy-tool.py"
        if tools_candidate.exists():
            return str(current / "tools")
        current = current.parent

    return None


def get_executable_lines(py_files, source_root=None, mpy_cross="mpy-cross", mpy_tools_dir=None):
    """
    Compile .py files to .mpy and extract executable line numbers.

    Args:
        py_files: List of .py file paths to analyze
        source_root: Directory to resolve relative paths from (default: current dir)
        mpy_cross: Path to the mpy-cross binary (default: "mpy-cross" in PATH)
        mpy_tools_dir: Path to MicroPython tools/ directory
                      (auto-detected from script location if None)

    Returns:
        Dictionary mapping filename to set of executable line numbers.
        Format: {filename: {line1, line2, ...}}

    Raises:
        RuntimeError: If compilation or parsing fails for any file
    """
    if source_root is None:
        source_root = os.getcwd()

    if mpy_tools_dir is None:
        detected = _find_tools_directory()
        if detected is None:
            raise RuntimeError(
                "Could not auto-detect tools directory. Please provide --mpy-tools-dir argument."
            )
        mpy_tools_dir = detected

    mpy_tool = _setup_mpy_tool(mpy_tools_dir)

    result = {}

    for py_file in py_files:
        # Resolve absolute path
        if not os.path.isabs(py_file):
            py_file = os.path.join(source_root, py_file)

        if not os.path.exists(py_file):
            print(f"Warning: File not found: {py_file}", file=sys.stderr)
            continue

        try:
            # Compile to temporary .mpy file
            with tempfile.NamedTemporaryFile(suffix=".mpy", delete=False) as tmp:
                temp_mpy_path = tmp.name

            try:
                _compile_to_mpy(py_file, mpy_cross, temp_mpy_path)

                # Parse the .mpy file
                compiled_module = mpy_tool.read_mpy(temp_mpy_path)

                # Extract lines from the raw code
                lines = set()
                _extract_lines_from_raw_code(compiled_module.raw_code, lines)

                result[py_file] = lines

            finally:
                # Clean up temporary .mpy file
                if os.path.exists(temp_mpy_path):
                    os.remove(temp_mpy_path)

        except Exception as e:
            print(f"Warning: Failed to analyze {py_file}: {e}", file=sys.stderr)
            continue

    return result


def main():
    """CLI entry point for the script."""
    parser = argparse.ArgumentParser(description="Extract executable line numbers from .mpy files")
    parser.add_argument("files", nargs="+", help="Python .py files to analyze")
    parser.add_argument(
        "--source-root",
        default=None,
        help="Directory to resolve relative file paths from (default: current directory)",
    )
    parser.add_argument(
        "--mpy-cross",
        default="mpy-cross",
        help="Path to mpy-cross binary (default: 'mpy-cross' in PATH)",
    )
    parser.add_argument(
        "--mpy-tools-dir",
        default=None,
        help="Path to MicroPython tools/ directory (auto-detected if not specified)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (default: human-readable format)",
    )

    args = parser.parse_args()

    try:
        result = get_executable_lines(
            args.files,
            source_root=args.source_root,
            mpy_cross=args.mpy_cross,
            mpy_tools_dir=args.mpy_tools_dir,
        )

        if args.json:
            # Convert sets to sorted lists for JSON serialization
            json_result = {filename: sorted(lines) for filename, lines in result.items()}
            print(json.dumps(json_result, indent=2))
        else:
            # Human-readable output
            for filename in sorted(result.keys()):
                lines = sorted(result[filename])
                print(f"{filename}:")
                print(f"  {len(lines)} executable lines: {lines}")

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
