#!/usr/bin/env python3
"""Unified CLI wrapper for MicroPython coverage collection and reporting.

Handles run/report lifecycle with multi-pass support: run each test
separately, accumulate JSON files, merge at report time.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = ".mpy_coverage"
COV_DELIM_START = "---MPY_COV_START---"
COV_DELIM_END = "---MPY_COV_END---"


def _find_micropython():
    """Auto-detect micropython binary.

    Search order:
    1. ports/unix/build-coverage/micropython relative to script dir
    2. micropython in PATH
    """
    in_tree = os.path.join(SCRIPT_DIR, "ports/unix/build-coverage/micropython")
    if os.path.isfile(in_tree) and os.access(in_tree, os.X_OK):
        return in_tree
    return shutil.which("micropython")


def _extract_json_from_output(output):
    """Extract JSON from delimited output."""
    match = re.search(
        rf"{re.escape(COV_DELIM_START)}\s*(.*?)\s*{re.escape(COV_DELIM_END)}",
        output,
        re.DOTALL,
    )
    if not match:
        return None
    return json.loads(match.group(1))


def _generate_wrapper_script(test_script, include, exclude, mode="unix", collect_arcs=False):
    """Generate a temporary wrapper script for coverage collection."""
    abs_test = os.path.abspath(test_script)
    test_basename = os.path.basename(test_script)

    inc_arg = repr(include) if include else "None"
    exc_arg = repr(exclude) if exclude else "None"
    arcs_arg = "True" if collect_arcs else "False"
    test_script_arg = repr(os.path.splitext(test_basename)[0])

    if mode == "unix":
        run_line = f'exec(compile(open("{abs_test}").read(), "{abs_test}", "exec"))'
    else:
        # Hardware: test script is deployed alongside wrapper
        run_line = f'exec(compile(open("{test_basename}").read(), "{test_basename}", "exec"))'

    return (
        "import mpy_coverage\n"
        f"mpy_coverage.start(include={inc_arg}, exclude={exc_arg}, "
        f"collect_arcs={arcs_arg}, test_script={test_script_arg})\n"
        f"{run_line}\n"
        "mpy_coverage.stop()\n"
        "mpy_coverage.export_json()\n"
    )


def _ensure_data_dir(data_dir):
    """Create data directory if it doesn't exist."""
    os.makedirs(data_dir, exist_ok=True)


def _make_data_filename(test_script):
    """Generate a timestamped filename for coverage data."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    basename = os.path.splitext(os.path.basename(test_script))[0]
    return f"{timestamp}_{basename}.json"


def cmd_run(args):
    """Execute 'run' subcommand — collect coverage for a single test."""
    test_script = args.test_script
    if not os.path.isfile(test_script):
        print(f"Error: test script not found: {test_script}", file=sys.stderr)
        return 1

    data_dir = args.data_dir
    _ensure_data_dir(data_dir)

    include = args.include or None
    exclude = args.exclude or None
    collect_arcs = getattr(args, "branch", False)

    if args.device:
        return _run_device(
            args, test_script, include, exclude, data_dir, collect_arcs=collect_arcs
        )
    else:
        return _run_unix(args, test_script, include, exclude, data_dir, collect_arcs=collect_arcs)


def _run_unix(args, test_script, include, exclude, data_dir, collect_arcs=False):
    """Run coverage collection using unix micropython binary."""
    mp = args.micropython or _find_micropython()
    if mp is None:
        print(
            "Error: micropython binary not found. Use --micropython or build "
            "ports/unix with VARIANT=coverage.",
            file=sys.stderr,
        )
        return 1

    wrapper_code = _generate_wrapper_script(
        test_script, include, exclude, mode="unix", collect_arcs=collect_arcs
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=SCRIPT_DIR, delete=False) as f:
        f.write(wrapper_code)
        wrapper_path = f.name

    try:
        result = subprocess.run(
            [mp, wrapper_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=SCRIPT_DIR,
        )

        if result.returncode != 0:
            print(f"Error: micropython exited with code {result.returncode}", file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return 1

        cov_data = _extract_json_from_output(result.stdout)
        if cov_data is None:
            print("Error: no coverage data found in output", file=sys.stderr)
            if result.stdout:
                print("stdout:", result.stdout[:500], file=sys.stderr)
            return 1

        out_name = _make_data_filename(test_script)
        out_path = os.path.join(data_dir, out_name)
        with open(out_path, "w") as f:
            json.dump(cov_data, f)
            f.write("\n")

        n_files = len(cov_data.get("executed", {}))
        n_lines = sum(len(v) for v in cov_data.get("executed", {}).values())
        print(f"Saved {out_path} ({n_files} files, {n_lines} lines)")
        return 0
    finally:
        os.unlink(wrapper_path)


def _run_device(args, test_script, include, exclude, data_dir, collect_arcs=False):
    """Run coverage collection on a hardware target via mpremote."""
    device = args.device

    # Deploy mpy_coverage.py unless --no-deploy
    if not args.no_deploy:
        cov_src = os.path.join(SCRIPT_DIR, "mpy_coverage.py")
        if not os.path.isfile(cov_src):
            print(f"Error: mpy_coverage.py not found at {cov_src}", file=sys.stderr)
            return 1
        deploy_cmd = [
            "mpremote",
            "connect",
            device,
            "resume",
            "fs",
            "cp",
            cov_src,
            ":mpy_coverage.py",
        ]
        r = subprocess.run(deploy_cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"Error deploying mpy_coverage.py: {r.stderr}", file=sys.stderr)
            return 1

    # Deploy test script
    deploy_test_cmd = [
        "mpremote",
        "connect",
        device,
        "resume",
        "fs",
        "cp",
        test_script,
        f":{os.path.basename(test_script)}",
    ]
    r = subprocess.run(deploy_test_cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"Error deploying test script: {r.stderr}", file=sys.stderr)
        return 1

    # Generate and deploy wrapper
    wrapper_code = _generate_wrapper_script(
        test_script, include, exclude, mode="device", collect_arcs=collect_arcs
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=SCRIPT_DIR, delete=False) as f:
        f.write(wrapper_code)
        wrapper_path = f.name

    try:
        # Deploy wrapper
        deploy_wrapper_cmd = [
            "mpremote",
            "connect",
            device,
            "resume",
            "fs",
            "cp",
            wrapper_path,
            ":_cov_runner.py",
        ]
        r = subprocess.run(deploy_wrapper_cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"Error deploying wrapper: {r.stderr}", file=sys.stderr)
            return 1

        # Run wrapper
        run_cmd = [
            "mpremote",
            "connect",
            device,
            "resume",
            "run",
            ":_cov_runner.py",
        ]
        r = subprocess.run(run_cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"Error running wrapper: {r.stderr}", file=sys.stderr)
            return 1

        cov_data = _extract_json_from_output(r.stdout)
        if cov_data is None:
            print("Error: no coverage data found in device output", file=sys.stderr)
            if r.stdout:
                print("stdout:", r.stdout[:500], file=sys.stderr)
            return 1

        out_name = _make_data_filename(test_script)
        out_path = os.path.join(data_dir, out_name)
        with open(out_path, "w") as f:
            json.dump(cov_data, f)
            f.write("\n")

        n_files = len(cov_data.get("executed", {}))
        n_lines = sum(len(v) for v in cov_data.get("executed", {}).values())
        print(f"Saved {out_path} ({n_files} files, {n_lines} lines)")
        return 0
    finally:
        os.unlink(wrapper_path)
        # Clean up wrapper on device
        cleanup_cmd = [
            "mpremote",
            "connect",
            device,
            "resume",
            "fs",
            "rm",
            ":_cov_runner.py",
        ]
        subprocess.run(cleanup_cmd, capture_output=True, text=True, timeout=10)


def cmd_report(args):
    """Execute 'report' subcommand — merge and generate reports."""
    from mpy_coverage_report import merge_coverage_data, run_report

    data_dir = args.data_dir
    json_files = sorted(glob.glob(os.path.join(data_dir, "*.json")))

    if not json_files:
        print(f"No coverage data files found in {data_dir}", file=sys.stderr)
        return 1

    print(f"Merging {len(json_files)} data file(s) from {data_dir}", file=sys.stderr)
    merged = merge_coverage_data(json_files)

    n_files = len(merged.get("executed", {}))
    n_lines = sum(len(v) for v in merged.get("executed", {}).values())
    print(f"Merged: {n_files} files, {n_lines} executed lines", file=sys.stderr)

    formats = args.formats or ["text"]

    run_report(
        merged,
        method=args.method,
        source_root=args.source_root,
        path_maps=args.path_map or [],
        mpy_cross=args.mpy_cross,
        mpy_tools_dir=args.mpy_tools_dir,
        formats=formats,
        output_dir=args.output_dir,
        show_missing=args.show_missing,
        branch=getattr(args, "branch", False),
    )
    return 0


def cmd_list(args):
    """Execute 'list' subcommand — show collected data files."""
    data_dir = args.data_dir
    json_files = sorted(glob.glob(os.path.join(data_dir, "*.json")))

    if not json_files:
        print(f"No coverage data files in {data_dir}")
        return 0

    print(f"Coverage data in {data_dir}:")
    for path in json_files:
        basename = os.path.basename(path)
        try:
            with open(path) as f:
                data = json.load(f)
            executed = data.get("executed", {})
            n_files = len(executed)
            n_lines = sum(len(v) for v in executed.values())
            print(f"  {basename}  ({n_files} files, {n_lines} lines)")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  {basename}  (error: {e})")
    return 0


def cmd_clean(args):
    """Execute 'clean' subcommand — remove collected data."""
    data_dir = args.data_dir
    json_files = glob.glob(os.path.join(data_dir, "*.json"))

    if not json_files:
        print(f"No coverage data files in {data_dir}")
        return 0

    if not args.yes:
        answer = input(f"Remove {len(json_files)} file(s) from {data_dir}? [y/N] ")
        if answer.lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

    for path in json_files:
        os.unlink(path)
    print(f"Removed {len(json_files)} file(s).")

    # Remove data dir if empty
    try:
        os.rmdir(data_dir)
    except OSError:
        pass
    return 0


def _extract_test_name(json_path, data):
    """Extract test name from JSON metadata, falling back to filename parsing.

    Args:
        json_path: Path to the JSON file.
        data: Already-loaded JSON data dict.
    """
    meta = data.get("_metadata", {})
    if "test_script" in meta:
        return meta["test_script"]
    # Fallback: strip "YYYYMMDD_HHMMSS_" prefix (16 chars) and ".json" suffix
    basename = os.path.basename(json_path)
    if len(basename) > 21 and basename[8] == "_" and basename[15] == "_":
        return basename[16:].removesuffix(".json")
    return basename.removesuffix(".json")


def _write_aligned_csv(rows, headers, file=None):
    """Write CSV with space-padding for column alignment."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    def _format_row(values):
        parts = []
        for i, val in enumerate(values):
            parts.append(str(val).ljust(widths[i]))
        return ", ".join(parts)

    print(_format_row(headers), file=file)
    for row in rows:
        print(_format_row(row), file=file)


def cmd_test_map(args):
    """Execute 'test-map' subcommand — show which tests cover each file/line."""
    data_dir = args.data_dir
    json_files = sorted(glob.glob(os.path.join(data_dir, "*.json")))

    if not json_files:
        print(f"No coverage data files found in {data_dir}", file=sys.stderr)
        return 1

    line_detail = args.line_detail

    # file_map: {app_file: set(test_names)}
    # line_map: {app_file: {line: set(test_names)}}
    file_map = {}
    line_map = {}

    for json_path in json_files:
        try:
            with open(json_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: skipping {json_path}: {e}", file=sys.stderr)
            continue

        test_name = _extract_test_name(json_path, data)

        for filename, lines in data.get("executed", {}).items():
            if filename not in file_map:
                file_map[filename] = set()
            file_map[filename].add(test_name)

            if line_detail:
                if filename not in line_map:
                    line_map[filename] = {}
                for line in lines:
                    if line not in line_map[filename]:
                        line_map[filename][line] = set()
                    line_map[filename][line].add(test_name)

    if not file_map:
        print("No coverage entries found in data files", file=sys.stderr)
        return 1

    if line_detail:
        rows = []
        for app_file in sorted(line_map):
            for line in sorted(line_map[app_file]):
                for test in sorted(line_map[app_file][line]):
                    rows.append((app_file, str(line), test))
        _write_aligned_csv(rows, ["app_file", "line", "test"])
    else:
        rows = []
        for app_file in sorted(file_map):
            for test in sorted(file_map[app_file]):
                rows.append((app_file, test))
        _write_aligned_csv(rows, ["app_file", "test"])

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="mpy-coverage",
        description="MicroPython coverage collection and reporting",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory for coverage data files (default: {DEFAULT_DATA_DIR})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    p_run = subparsers.add_parser("run", help="Collect coverage for a test script")
    p_run.add_argument("test_script", help="Python test script to run")
    p_run.add_argument(
        "--device", default=None, help="Hardware target device path (triggers mpremote flow)"
    )
    p_run.add_argument(
        "--micropython", default=None, help="Path to micropython binary (unix port)"
    )
    p_run.add_argument(
        "--include",
        action="append",
        default=[],
        help="Filename substring filter to include (repeatable)",
    )
    p_run.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Filename substring filter to exclude (repeatable)",
    )
    p_run.add_argument(
        "--no-deploy", action="store_true", help="Skip auto-deploy of mpy_coverage.py to device"
    )
    p_run.add_argument(
        "--branch", action="store_true", help="Collect arc data for branch coverage"
    )
    p_run.set_defaults(func=cmd_run)

    # --- report ---
    p_report = subparsers.add_parser("report", help="Generate merged coverage report")
    p_report.add_argument(
        "--method",
        choices=["auto", "co_lines", "ast", "mpy"],
        default="auto",
        help="Executable line detection method (default: auto)",
    )
    p_report.add_argument("--source-root", default=None, help="Root directory for source files")
    p_report.add_argument("--mpy-cross", default="mpy-cross", help="Path to mpy-cross binary")
    p_report.add_argument(
        "--mpy-tools-dir", default=None, help="Path to MicroPython tools/ directory"
    )
    p_report.add_argument(
        "--path-map",
        action="append",
        default=[],
        help="Path mapping device_prefix=host_prefix (repeatable)",
    )
    p_report.add_argument(
        "--format",
        dest="formats",
        action="append",
        default=[],
        choices=["text", "html", "json", "xml", "lcov"],
        help="Output format (repeatable, default: text)",
    )
    p_report.add_argument("--output-dir", default=None, help="Output directory for report files")
    p_report.add_argument(
        "--show-missing", action="store_true", help="Show missing line numbers in text report"
    )
    p_report.add_argument("--branch", action="store_true", help="Enable branch coverage reporting")
    p_report.set_defaults(func=cmd_report)

    # --- list ---
    p_list = subparsers.add_parser("list", help="List collected coverage data files")
    p_list.set_defaults(func=cmd_list)

    # --- test-map ---
    p_testmap = subparsers.add_parser(
        "test-map", help="Show which tests cover each application file"
    )
    p_testmap.add_argument(
        "--line-detail",
        action="store_true",
        help="Show per-line test associations instead of file-level",
    )
    p_testmap.set_defaults(func=cmd_test_map)

    # --- clean ---
    p_clean = subparsers.add_parser("clean", help="Remove collected coverage data")
    p_clean.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_clean.set_defaults(func=cmd_clean)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
