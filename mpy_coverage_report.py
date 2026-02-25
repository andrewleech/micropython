#!/usr/bin/env python3
# Host-side reporting for MicroPython coverage data.
# Reads JSON from mpy_coverage.py, resolves executable lines,
# generates reports via coverage.py.

import argparse
import json
import os
import re
import sys

import coverage
from coverage.data import CoverageData
from coverage.plugin import FileReporter
from coverage.results import analysis_from_file_reporter


class MpyFileReporter(FileReporter):
    """FileReporter that provides externally-determined executable lines."""

    def __init__(self, filename, executable_lines, source_path=None):
        super().__init__(filename)
        self._executable_lines = executable_lines
        self._source_path = source_path or filename
        self._arcs = None
        self._exit_counts = None

    def lines(self):
        return self._executable_lines

    def source(self):
        with open(self._source_path, encoding="utf-8") as f:
            return f.read()

    def relative_filename(self):
        return self.filename

    def _parse_arcs(self):
        """Lazily compute possible arcs and exit counts from PythonParser."""
        if self._arcs is not None:
            return
        from coverage.parser import PythonParser

        try:
            parser = PythonParser(filename=self._source_path)
            parser.parse_source()
            self._arcs = parser.arcs()
        except Exception as e:
            print(f"Warning: arc analysis failed for {self._source_path}: {e}", file=sys.stderr)
            self._arcs = set()

        # Derive exit counts: number of distinct destinations per source line
        from collections import defaultdict

        dests = defaultdict(set)
        for from_line, to_line in self._arcs:
            dests[from_line].add(to_line)
        self._exit_counts = {line: len(targets) for line, targets in dests.items()}

    def arcs(self):
        self._parse_arcs()
        return self._arcs

    def exit_counts(self):
        self._parse_arcs()
        return self._exit_counts

    def no_branch_lines(self):
        return set()


class MpyCoverage(coverage.Coverage):
    """Coverage subclass that uses MpyFileReporter for known files."""

    def __init__(self, file_reporters, **kwargs):
        super().__init__(**kwargs)
        self._mpy_reporters = file_reporters  # {filename: MpyFileReporter}

    def _get_file_reporter(self, morf):
        if isinstance(morf, str) and morf in self._mpy_reporters:
            return self._mpy_reporters[morf]
        return super()._get_file_reporter(morf)

    def _get_file_reporters(self, morfs=None):
        if morfs is None:
            morfs = self._mpy_reporters.keys()
        result = []
        for morf in morfs:
            fr = self._get_file_reporter(morf)
            result.append((fr, morf))
        return result

    def _analyze(self, morf):
        data = self.get_data()
        fr = self._get_file_reporter(morf)
        filename = fr.filename
        return analysis_from_file_reporter(data, self.config.precision, fr, filename)


def _resolve_executable_lines_co_lines(cov_data):
    """Pathway A: use co_lines data collected on-device."""
    executable = cov_data.get("executable", {})
    return {f: set(lines) for f, lines in executable.items()}


def _resolve_executable_lines_ast(filenames, source_paths):
    """Pathway B1: use coverage.py's PythonParser on host."""
    from coverage.parser import PythonParser

    result = {}
    for filename in filenames:
        source_path = source_paths.get(filename, filename)
        if not os.path.exists(source_path):
            print(f"Warning: source not found for ast analysis: {source_path}", file=sys.stderr)
            continue
        try:
            parser = PythonParser(filename=source_path)
            parser.parse_source()
            result[filename] = parser.statements
        except Exception as e:
            print(f"Warning: ast parse failed for {source_path}: {e}", file=sys.stderr)
    return result


def _resolve_executable_lines_mpy(filenames, source_paths, mpy_cross, mpy_tools_dir=None):
    """Pathway B2: compile to .mpy and extract line info."""
    from mpy_coverage_mpy_analysis import get_executable_lines

    py_files = []
    file_map = {}  # absolute source_path -> original filename
    for filename in filenames:
        source_path = source_paths.get(filename, filename)
        if os.path.exists(source_path):
            abs_path = os.path.abspath(source_path)
            py_files.append(source_path)
            file_map[abs_path] = filename
            file_map[source_path] = filename

    raw = get_executable_lines(py_files, mpy_cross=mpy_cross, mpy_tools_dir=mpy_tools_dir)

    result = {}
    for source_path, lines in raw.items():
        original = file_map.get(
            source_path, file_map.get(os.path.abspath(source_path), source_path)
        )
        result[original] = lines
    return result


def _apply_path_map(filenames, path_maps):
    """Remap device paths to host paths.

    Returns dict {original_filename: resolved_host_path}.
    """
    source_paths = {}
    for filename in filenames:
        resolved = filename
        for mapping in path_maps:
            if "=" not in mapping:
                continue
            device_prefix, host_prefix = mapping.split("=", 1)
            if filename.startswith(device_prefix):
                resolved = host_prefix + filename[len(device_prefix) :]
                break
        source_paths[filename] = resolved
    return source_paths


def _load_json(path):
    """Load coverage JSON data, handling serial-delimited format."""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    # Strip serial delimiters if present
    match = re.search(r"---MPY_COV_START---\s*(.*?)\s*---MPY_COV_END---", text, re.DOTALL)
    if match:
        text = match.group(1)

    return json.loads(text)


def merge_coverage_data(json_files):
    """Merge multiple coverage JSON files into a single data dict.

    Merges executed line data and arc data (union per file).
    Executable line data is not merged as it is computed at report time.

    Args:
        json_files: List of paths to JSON coverage data files.

    Returns:
        Dict with merged "executed" and optionally "arcs" data.
    """
    merged = {"executed": {}}
    for path in json_files:
        data = _load_json(path)
        for filename, lines in data.get("executed", {}).items():
            if filename not in merged["executed"]:
                merged["executed"][filename] = set()
            merged["executed"][filename].update(lines)
        if "arcs" in data:
            if "arcs" not in merged:
                merged["arcs"] = {}
            for filename, arcs in data["arcs"].items():
                if filename not in merged["arcs"]:
                    merged["arcs"][filename] = set()
                for arc in arcs:
                    merged["arcs"][filename].add(tuple(arc))
    # Convert sets to sorted lists
    for filename in merged["executed"]:
        merged["executed"][filename] = sorted(merged["executed"][filename])
    if "arcs" in merged:
        for filename in merged["arcs"]:
            merged["arcs"][filename] = sorted([list(a) for a in merged["arcs"][filename]])
    return merged


def run_report(
    cov_data,
    method,
    source_root=None,
    path_maps=None,
    mpy_cross="mpy-cross",
    mpy_tools_dir=None,
    formats=None,
    output_dir=None,
    show_missing=False,
    branch=None,
):
    """Generate coverage reports from collected data.

    Branch coverage is auto-detected from arc data presence in cov_data,
    matching coverage.py's behaviour where ``coverage report`` auto-detects
    branch data collected by ``coverage run --branch``.

    Args:
        branch: Override auto-detection. True forces branch mode (warns if
            no arc data), False forces line-only mode, None (default)
            auto-detects from arc data presence.

    Returns the total coverage percentage.
    """
    if formats is None:
        formats = ["text"]
    if path_maps is None:
        path_maps = []

    # Auto-detect branch mode from arc data presence (matches coverage.py behaviour)
    arc_data = cov_data.get("arcs", {})
    if branch is None:
        branch = bool(arc_data)
    elif branch and not arc_data:
        print(
            "Warning: branch mode requested but no arc data in coverage JSON. "
            "Falling back to line-only mode.",
            file=sys.stderr,
        )
        branch = False

    executed = cov_data.get("executed", {})
    filenames = list(executed.keys())

    # Resolve device paths to host source paths
    source_paths = _apply_path_map(filenames, path_maps)

    # If source_root provided, resolve relative paths
    if source_root:
        for filename in filenames:
            current = source_paths[filename]
            if not os.path.isabs(current):
                source_paths[filename] = os.path.join(source_root, current)

    # Resolve executable lines based on method
    if method == "co_lines":
        executable = _resolve_executable_lines_co_lines(cov_data)
    elif method == "ast":
        executable = _resolve_executable_lines_ast(filenames, source_paths)
    elif method == "mpy":
        executable = _resolve_executable_lines_mpy(
            filenames, source_paths, mpy_cross, mpy_tools_dir
        )
    elif method == "auto":
        # Prefer mpy (most accurate for MicroPython), fall back to co_lines
        # if on-device data is available, then ast as last resort.
        executable = _resolve_executable_lines_mpy(
            filenames, source_paths, mpy_cross, mpy_tools_dir
        )
    else:
        raise ValueError(f"Unknown method: {method}")

    # Build MpyFileReporters
    file_reporters = {}
    for filename in filenames:
        source_path = source_paths.get(filename, filename)
        exec_lines = executable.get(filename, set())
        if not exec_lines:
            continue
        if not os.path.exists(source_path):
            print(f"Warning: source not found: {source_path}", file=sys.stderr)
            continue
        file_reporters[filename] = MpyFileReporter(filename, exec_lines, source_path)

    if not file_reporters:
        print("No files to report on.", file=sys.stderr)
        return 0.0

    # Create CoverageData and inject coverage data
    cov_obj = MpyCoverage(file_reporters, data_file=None)
    cov_obj._init()
    cov_obj._post_init()

    data = cov_obj.get_data()
    if branch:
        # Inject arc data — add_arcs and add_lines are mutually exclusive
        injected_arcs = {}
        for filename, arcs in arc_data.items():
            if filename in file_reporters:
                # Filter self-loop arcs (l1 == l2)
                arc_set = set()
                for arc in arcs:
                    from_line, to_line = arc[0], arc[1]
                    if from_line != to_line:
                        arc_set.add((from_line, to_line))
                injected_arcs[filename] = arc_set
        data.add_arcs(injected_arcs)
    else:
        line_data = {}
        for filename, lines in executed.items():
            if filename in file_reporters:
                line_data[filename] = set(lines)
        data.add_lines(line_data)

    total = 0.0

    for fmt in formats:
        if fmt == "text":
            total = cov_obj.report(show_missing=show_missing)
        elif fmt == "html":
            outdir = output_dir or "htmlcov"
            total = cov_obj.html_report(directory=outdir)
            print(f"HTML report written to {outdir}/", file=sys.stderr)
        elif fmt == "json":
            outfile = os.path.join(output_dir or ".", "coverage.json")
            total = cov_obj.json_report(outfile=outfile)
            print(f"JSON report written to {outfile}", file=sys.stderr)
        elif fmt == "xml":
            outfile = os.path.join(output_dir or ".", "coverage.xml")
            total = cov_obj.xml_report(outfile=outfile)
            print(f"XML report written to {outfile}", file=sys.stderr)
        elif fmt == "lcov":
            outfile = os.path.join(output_dir or ".", "coverage.lcov")
            total = cov_obj.lcov_report(outfile=outfile)
            print(f"LCOV report written to {outfile}", file=sys.stderr)
        else:
            print(f"Unknown format: {fmt}", file=sys.stderr)

    return total


def main():
    parser = argparse.ArgumentParser(
        description="Generate coverage reports from MicroPython coverage data"
    )
    parser.add_argument("data_file", help="JSON coverage data file from mpy_coverage.py")
    parser.add_argument(
        "--method",
        choices=["auto", "co_lines", "ast", "mpy"],
        default="auto",
        help="Method for determining executable lines (default: auto)",
    )
    parser.add_argument("--source-root", default=None, help="Root directory for source files")
    parser.add_argument("--mpy-cross", default="mpy-cross", help="Path to mpy-cross binary")
    parser.add_argument("--mpy-tools-dir", default=None, help="Path to MicroPython tools/ dir")
    parser.add_argument(
        "--path-map",
        action="append",
        default=[],
        help="Path mapping as device_prefix=host_prefix (repeatable)",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        action="append",
        default=[],
        choices=["text", "html", "json", "xml", "lcov"],
        help="Output format (repeatable, default: text)",
    )
    parser.add_argument("--output-dir", default=None, help="Output directory for report files")
    parser.add_argument("--show-missing", action="store_true", help="Show missing line numbers")
    parser.add_argument(
        "--no-branch",
        action="store_true",
        help="Disable branch coverage even if arc data is present",
    )

    args = parser.parse_args()

    if not args.formats:
        args.formats = ["text"]

    cov_data = _load_json(args.data_file)

    # Auto-detect branch from arc data; --no-branch overrides to line-only
    branch = False if args.no_branch else None

    run_report(
        cov_data,
        method=args.method,
        source_root=args.source_root,
        path_maps=args.path_map,
        mpy_cross=args.mpy_cross,
        mpy_tools_dir=args.mpy_tools_dir,
        formats=args.formats,
        output_dir=args.output_dir,
        show_missing=args.show_missing,
        branch=branch,
    )


if __name__ == "__main__":
    main()
