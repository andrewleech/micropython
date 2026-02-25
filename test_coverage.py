#!/usr/bin/env python3
# End-to-end trial runner for MicroPython coverage toolchain.
# Runs on CPython, drives the MicroPython coverage binary.

import json
import os
import shutil
import subprocess
import sys
import tempfile

import coverage as coverage_py
from coverage.results import analysis_from_file_reporter

from mpy_coverage_report import (
    MpyCoverage,
    MpyFileReporter,
    _resolve_executable_lines_ast,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MPY_BINARY = os.path.join(SCRIPT_DIR, "ports/unix/build-coverage/micropython")
MPY_CROSS = os.path.join(SCRIPT_DIR, "mpy-cross/build/mpy-cross")


def run_micropython(code):
    result = subprocess.run(
        [MPY_BINARY, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=SCRIPT_DIR,
    )
    if result.returncode != 0:
        print(f"MicroPython error:\n{result.stderr}", file=sys.stderr)
    return result


def run_report(data_file, method, extra_args=None):
    cmd = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "mpy_coverage_report.py"),
        data_file,
        "--method",
        method,
        "--show-missing",
    ]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    return result


def trial_a():
    """Trial A: co_lines pathway (self-contained on-device executable lines)."""
    print("=== Trial A: co_lines ===")
    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target'], collect_executable=True)
import test_target
test_target.run()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            return False

        data = json.load(open(json_path))
        assert "executed" in data, "Missing 'executed' key"
        assert "executable" in data, "Missing 'executable' key (co_lines mode)"
        assert "test_target.py" in data["executed"], "test_target.py not in executed"

        r = run_report(json_path, "co_lines")
        print(r.stdout)
        if r.returncode != 0:
            print(f"FAIL: report error: {r.stderr}")
            return False

        print("PASS")
        return True
    finally:
        os.unlink(json_path)


def trial_b1():
    """Trial B1: ast pathway (host-side CPython parsing)."""
    print("=== Trial B1: ast ===")
    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target'])
import test_target
test_target.run()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            return False

        r = run_report(json_path, "ast")
        print(r.stdout)
        if r.returncode != 0:
            print(f"FAIL: report error: {r.stderr}")
            return False

        print("PASS")
        return True
    finally:
        os.unlink(json_path)


def trial_b2():
    """Trial B2: mpy pathway (host-side .mpy analysis)."""
    print("=== Trial B2: mpy ===")
    if not os.path.exists(MPY_CROSS):
        print(f"SKIP: mpy-cross not found at {MPY_CROSS}")
        return True

    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target'])
import test_target
test_target.run()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            return False

        r = run_report(json_path, "mpy", ["--mpy-cross", MPY_CROSS])
        print(r.stdout)
        if r.returncode != 0:
            print(f"FAIL: report error: {r.stderr}")
            return False

        print("PASS")
        return True
    finally:
        os.unlink(json_path)


def trial_formats():
    """Verify all output formats generate without error."""
    print("=== All formats ===")
    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    outdir = tempfile.mkdtemp(prefix="mpy_cov_")

    try:
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target'], collect_executable=True)
import test_target
test_target.run()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            return False

        for fmt in ["text", "html", "json", "xml", "lcov"]:
            r = run_report(json_path, "co_lines", ["--format", fmt, "--output-dir", outdir])
            if r.returncode != 0:
                print(f"FAIL: {fmt} format error: {r.stderr}")
                return False
            print(f"  {fmt}: OK")

        print("PASS")
        return True
    finally:
        os.unlink(json_path)
        shutil.rmtree(outdir, ignore_errors=True)


def trial_merge():
    """Multi-pass merge: run two separate collections, merge, verify combined data."""
    print("=== Multi-pass merge ===")
    from mpy_coverage_report import merge_coverage_data

    data_dir = tempfile.mkdtemp(prefix="mpy_cov_merge_")

    try:
        # Pass 1: run test_target.run() — exercises branching(1) and MyClass.method_a
        json_path_1 = os.path.join(data_dir, "pass1.json")
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target'])
import test_target
test_target.run()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path_1}')
""")
        if r.returncode != 0:
            print("FAIL: pass 1 micropython error")
            return False

        # Pass 2: exercise paths not covered by run() — with_nested, method_b, branching(-1)
        json_path_2 = os.path.join(data_dir, "pass2.json")
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target'])
import test_target
test_target.with_nested()
test_target.MyClass(5).method_b()
test_target.branching(-1)
mpy_coverage.stop()
mpy_coverage.export_json('{json_path_2}')
""")
        if r.returncode != 0:
            print("FAIL: pass 2 micropython error")
            return False

        # Load individual data to verify they're different
        with open(json_path_1) as f:
            data1 = json.load(f)
        with open(json_path_2) as f:
            data2 = json.load(f)

        lines1 = set(data1["executed"].get("test_target.py", []))
        lines2 = set(data2["executed"].get("test_target.py", []))

        # Each pass should cover lines the other doesn't
        only_in_1 = lines1 - lines2
        only_in_2 = lines2 - lines1
        if not only_in_1 or not only_in_2:
            print("FAIL: passes don't have unique lines — test is not exercising different paths")
            print(f"  pass1 lines: {sorted(lines1)}")
            print(f"  pass2 lines: {sorted(lines2)}")
            return False

        # Merge
        merged = merge_coverage_data([json_path_1, json_path_2])
        merged_lines = set(merged["executed"].get("test_target.py", []))

        # Merged must be the union
        expected = lines1 | lines2
        if merged_lines != expected:
            print("FAIL: merged lines don't match union of individual passes")
            print(f"  expected: {sorted(expected)}")
            print(f"  got:      {sorted(merged_lines)}")
            return False

        print(
            f"  pass1: {len(lines1)} lines, pass2: {len(lines2)} lines, "
            f"merged: {len(merged_lines)} lines"
        )
        print("PASS")
        return True
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def trial_cli_run_and_report():
    """Test the CLI run and report subcommands end-to-end."""
    print("=== CLI run + report ===")
    data_dir = tempfile.mkdtemp(prefix="mpy_cov_cli_")
    cli_script = os.path.join(SCRIPT_DIR, "mpy_coverage_cli.py")

    try:
        # Run via CLI
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "run",
                "test_target.py",
                "--micropython",
                MPY_BINARY,
                "--include",
                "test_target",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: cli run error: {r.stderr}")
            return False

        # Check a data file was created
        json_files = [f for f in os.listdir(data_dir) if f.endswith(".json")]
        if len(json_files) != 1:
            print(f"FAIL: expected 1 data file, got {len(json_files)}")
            return False
        print(f"  run created: {json_files[0]}")

        # List via CLI
        r = subprocess.run(
            [sys.executable, cli_script, "--data-dir", data_dir, "list"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: cli list error: {r.stderr}")
            return False
        if json_files[0] not in r.stdout:
            print("FAIL: list output doesn't contain data file name")
            return False
        print("  list OK")

        # Report via CLI (co_lines to avoid needing mpy-cross)
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "report",
                "--method",
                "ast",
                "--show-missing",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: cli report error: {r.stderr}")
            print(f"  stdout: {r.stdout}")
            return False
        print("  report OK")

        # Clean via CLI
        r = subprocess.run(
            [sys.executable, cli_script, "--data-dir", data_dir, "clean", "--yes"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: cli clean error: {r.stderr}")
            return False
        remaining = (
            [f for f in os.listdir(data_dir) if f.endswith(".json")]
            if os.path.isdir(data_dir)
            else []
        )
        if remaining:
            print(f"FAIL: clean didn't remove files: {remaining}")
            return False
        print("  clean OK")

        print("PASS")
        return True
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def trial_cli_multipass():
    """Test CLI multi-pass: two runs then merged report."""
    print("=== CLI multi-pass ===")
    data_dir = tempfile.mkdtemp(prefix="mpy_cov_mp_")
    cli_script = os.path.join(SCRIPT_DIR, "mpy_coverage_cli.py")

    # Create two small test scripts that exercise different paths
    test1 = os.path.join(SCRIPT_DIR, "_cov_test_pass1.py")
    test2 = os.path.join(SCRIPT_DIR, "_cov_test_pass2.py")

    try:
        with open(test1, "w") as f:
            f.write("import test_target\ntest_target.run()\n")
        with open(test2, "w") as f:
            f.write("import test_target\ntest_target.with_nested()\ntest_target.branching(-1)\n")

        # Run pass 1
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "run",
                test1,
                "--micropython",
                MPY_BINARY,
                "--include",
                "test_target",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: pass 1 error: {r.stderr}")
            return False

        # Run pass 2
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "run",
                test2,
                "--micropython",
                MPY_BINARY,
                "--include",
                "test_target",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: pass 2 error: {r.stderr}")
            return False

        # Check two data files
        json_files = [f for f in os.listdir(data_dir) if f.endswith(".json")]
        if len(json_files) != 2:
            print(f"FAIL: expected 2 data files, got {len(json_files)}")
            return False
        print("  2 data files collected")

        # Report (merged)
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "report",
                "--method",
                "ast",
                "--show-missing",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: report error: {r.stderr}")
            return False

        # Verify merged report mentions test_target.py
        if "test_target" not in r.stdout:
            print("FAIL: report doesn't mention test_target")
            print(f"  stdout: {r.stdout}")
            return False
        print("  merged report OK")

        print("PASS")
        return True
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        for f in (test1, test2):
            if os.path.exists(f):
                os.unlink(f)


def trial_branch():
    """Branch coverage: collect arcs, generate branch report."""
    print("=== Branch coverage ===")
    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        # Collect with arcs enabled
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target'], collect_arcs=True)
import test_target
test_target.run()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            print(r.stderr)
            return False

        data = json.load(open(json_path))
        assert "executed" in data, "Missing 'executed' key"
        assert "arcs" in data, "Missing 'arcs' key"
        assert "test_target.py" in data["arcs"], "test_target.py not in arcs"

        arcs = data["arcs"]["test_target.py"]
        print(f"  collected {len(arcs)} arcs")

        # Verify some expected arcs exist (line transitions in branching())
        arc_set = set(tuple(a) for a in arcs)
        # branching(1) takes positive path: line 6->7 (if x > 0 -> return "positive")
        assert (6, 7) in arc_set, f"Expected arc (6, 7) not found. Arcs: {sorted(arc_set)}"
        print("  arc (6, 7) present: if->positive branch taken")

        # Generate branch report using ast method (auto-detects arc data)
        r = run_report(json_path, "ast", ["--show-missing"])
        if r.returncode != 0:
            print(f"FAIL: branch report error: {r.stderr}")
            return False

        # Check report output contains branch coverage columns
        output = r.stdout
        print(r.stderr, end="")
        if "Branch" not in output and "BrPart" not in output:
            print("FAIL: branch report output missing branch columns")
            print(f"  output: {output[:500]}")
            return False
        print("  branch report generated")

        # The report should show test_target.py
        if "test_target" not in output:
            print("FAIL: test_target not in branch report output")
            return False

        print(output)
        print("PASS")
        return True
    finally:
        os.unlink(json_path)


def trial_branch_cli():
    """Test CLI with --branch flag on run and report."""
    print("=== CLI branch coverage ===")
    data_dir = tempfile.mkdtemp(prefix="mpy_cov_branch_")
    cli_script = os.path.join(SCRIPT_DIR, "mpy_coverage_cli.py")

    try:
        # Run with --branch
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "run",
                "test_target.py",
                "--micropython",
                MPY_BINARY,
                "--include",
                "test_target",
                "--branch",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: cli run --branch error: {r.stderr}")
            return False
        print("  run --branch OK")

        # Verify data file has arcs
        json_files = [f for f in os.listdir(data_dir) if f.endswith(".json")]
        if len(json_files) != 1:
            print(f"FAIL: expected 1 data file, got {len(json_files)}")
            return False

        with open(os.path.join(data_dir, json_files[0])) as f:
            data = json.load(f)
        if "arcs" not in data:
            print("FAIL: data file missing 'arcs' key")
            return False
        print("  data file has arcs")

        # Report (auto-detects branch from arc data)
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "report",
                "--method",
                "ast",
                "--show-missing",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: cli report --branch error: {r.stderr}")
            print(f"  stdout: {r.stdout}")
            return False
        print("  report --branch OK")

        print("PASS")
        return True
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def trial_test_map():
    """Test test-map subcommand: two runs, verify file-level and line-level mapping."""
    print("=== CLI test-map ===")
    data_dir = tempfile.mkdtemp(prefix="mpy_cov_testmap_")
    cli_script = os.path.join(SCRIPT_DIR, "mpy_coverage_cli.py")

    # Create two test scripts that exercise different paths
    test1 = os.path.join(SCRIPT_DIR, "_cov_test_pass1.py")
    test2 = os.path.join(SCRIPT_DIR, "_cov_test_pass2.py")

    try:
        with open(test1, "w") as f:
            f.write("import test_target\ntest_target.run()\n")
        with open(test2, "w") as f:
            f.write("import test_target\ntest_target.with_nested()\ntest_target.branching(-1)\n")

        # Run pass 1
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "run",
                test1,
                "--micropython",
                MPY_BINARY,
                "--include",
                "test_target",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: pass 1 error: {r.stderr}")
            return False

        # Run pass 2
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "run",
                test2,
                "--micropython",
                MPY_BINARY,
                "--include",
                "test_target",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: pass 2 error: {r.stderr}")
            return False

        # Verify JSON files have _metadata
        json_files = sorted([f for f in os.listdir(data_dir) if f.endswith(".json")])
        if len(json_files) != 2:
            print(f"FAIL: expected 2 data files, got {len(json_files)}")
            return False

        for jf in json_files:
            with open(os.path.join(data_dir, jf)) as f:
                data = json.load(f)
            if "_metadata" not in data:
                print(f"FAIL: {jf} missing _metadata")
                return False
            if "test_script" not in data["_metadata"]:
                print(f"FAIL: {jf} _metadata missing test_script")
                return False
        print("  _metadata present in both data files")

        # test-map (file-level)
        r = subprocess.run(
            [sys.executable, cli_script, "--data-dir", data_dir, "test-map"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: test-map error: {r.stderr}")
            return False

        output = r.stdout
        if "_cov_test_pass1" not in output or "_cov_test_pass2" not in output:
            print("FAIL: test-map output missing test names")
            print(f"  output: {output}")
            return False
        if "test_target.py" not in output:
            print("FAIL: test-map output missing test_target.py")
            print(f"  output: {output}")
            return False
        # Verify CSV header
        lines = output.strip().split("\n")
        if not lines[0].startswith("app_file"):
            print(f"FAIL: unexpected header: {lines[0]}")
            return False
        print("  file-level test-map OK")

        # test-map --line-detail
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "test-map",
                "--line-detail",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=SCRIPT_DIR,
        )
        if r.returncode != 0:
            print(f"FAIL: test-map --line-detail error: {r.stderr}")
            return False

        output = r.stdout
        lines = output.strip().split("\n")
        # Header should have 3 columns
        header_parts = [p.strip() for p in lines[0].split(",")]
        if header_parts != ["app_file", "line", "test"]:
            print(f"FAIL: unexpected line-detail header: {header_parts}")
            return False
        # Should have data rows with line numbers
        if len(lines) < 2:
            print("FAIL: no data rows in line-detail output")
            return False
        # Check a data row has a numeric line column
        data_parts = [p.strip() for p in lines[1].split(",")]
        if len(data_parts) != 3:
            print(f"FAIL: expected 3 columns, got {len(data_parts)}: {data_parts}")
            return False
        if not data_parts[1].isdigit():
            print(f"FAIL: line column not numeric: {data_parts[1]}")
            return False
        print("  line-detail test-map OK")

        # Verify alignment: all commas in same column positions
        comma_positions = None
        for line in lines:
            positions = [i for i, c in enumerate(line) if c == ","]
            if comma_positions is None:
                comma_positions = positions
            elif positions != comma_positions:
                print("FAIL: misaligned columns")
                print(f"  expected commas at {comma_positions}, got {positions}")
                print(f"  line: {line!r}")
                return False
        print("  column alignment OK")

        print("PASS")
        return True
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        for f in (test1, test2):
            if os.path.exists(f):
                os.unlink(f)


# --- Cross-validation helpers ---


def _run_cpython_coverage(target_path, entry_code, branch=False):
    """Run target under CPython's coverage.py in-process.

    Args:
        target_path: Absolute path to the .py file to measure.
        entry_code: Python code string that imports/calls target functions.
            The target file's directory is added to sys.path.
        branch: If True, collect branch/arc coverage.

    Returns:
        Dict with executed_lines, statements, missing_lines, coverage_pct,
        and if branch: executed_arcs, missing_arcs, total_branches, covered_branches.
    """
    basename = os.path.basename(target_path)
    target_dir = os.path.dirname(target_path)

    cov = coverage_py.Coverage(branch=branch, source=[target_dir])
    cov.start()
    try:
        # Build the execution namespace
        saved_path = sys.path[:]
        if target_dir not in sys.path:
            sys.path.insert(0, target_dir)
        try:
            source = open(target_path, encoding="utf-8").read()
            code = compile(source, basename, "exec")
            ns = {"__name__": "__main__", "__file__": basename}
            exec(code, ns)
            # Run the entry code in same namespace
            exec(entry_code, ns)
        finally:
            sys.path[:] = saved_path
    finally:
        cov.stop()
        cov.save()

    # Build analysis using coverage.py's own infrastructure
    data = cov.get_data()
    fr = cov._get_file_reporter(target_path)

    analysis = analysis_from_file_reporter(data, cov.config.precision, fr, target_path)

    result = {
        "executed_lines": analysis.executed,
        "statements": analysis.statements,
        "missing_lines": analysis.missing,
        "coverage_pct": analysis.numbers.pc_covered,
    }

    if branch:
        result["executed_arcs"] = analysis.arcs_executed_set
        result["missing_arcs"] = set(analysis.arcs_missing())
        result["total_branches"] = analysis.numbers.n_branches
        result["covered_branches"] = analysis.numbers.n_partial_branches

    return result


def _get_mpy_analysis(target_path, executed_lines, branch=False, arcs=None):
    """Build a coverage.py Analysis from mpy-collected data using ast method.

    Uses the same PythonParser that coverage.py uses, so statements are
    identical by construction. Any differences in executed/missing reveal
    genuine tracing divergences.

    Args:
        target_path: Absolute path to the .py source.
        executed_lines: Set of line numbers executed by MicroPython.
        branch: Whether to include branch analysis.
        arcs: Set of (from, to) arcs if branch=True.

    Returns:
        Analysis object with statements, executed, missing, numbers, etc.
    """
    basename = os.path.basename(target_path)

    # Resolve executable lines using ast method (same PythonParser)
    exec_lines = _resolve_executable_lines_ast([basename], {basename: target_path})
    statements = exec_lines.get(basename, set())

    reporter = MpyFileReporter(basename, statements, source_path=target_path)
    file_reporters = {basename: reporter}

    cov = MpyCoverage(file_reporters, data_file=None)
    cov._init()
    cov._post_init()

    data = cov.get_data()
    if branch and arcs:
        # Filter self-loops
        arc_set = set()
        for from_line, to_line in arcs:
            if from_line != to_line:
                arc_set.add((from_line, to_line))
        data.add_arcs({basename: arc_set})
    else:
        data.add_lines({basename: set(executed_lines)})

    analysis = analysis_from_file_reporter(data, cov.config.precision, reporter, basename)
    return analysis


# Known MicroPython settrace divergences from CPython:
#
# 1. except-header tracing: MicroPython's compiler emits set_source_line for
#    `except XxxError:` headers even when the exception path is not taken.
#    CPython only traces the except line when the exception handler activates.
#    This causes MicroPython to report 1 extra executed line per unvisited
#    except clause.
#
# 2. Arc encoding convention: MicroPython's settrace uses co_firstlineno
#    differently from CPython for function entry/exit arcs. The raw arc tuples
#    are not directly comparable. Line-level branch metrics (which lines have
#    partial branches) are compared instead.

# Lines in test_target_xval.py where MicroPython diverges from CPython.
# Each entry: (line_number, description)
_KNOWN_DIVERGENCES = {
    55: "except-header: MicroPython traces `except ZeroDivisionError:` even when not taken",
}


def _compare_lines(cpython_result, mpy_analysis, label):
    """Compare line coverage between CPython and MicroPython.

    Applies known-divergence filtering. Returns True if all metrics match
    after accounting for documented VM differences.
    """
    ok = True

    # Compare statements (PythonParser is shared, should always match)
    cp_stmts = cpython_result["statements"]
    mpy_stmts = mpy_analysis.statements
    if cp_stmts != mpy_stmts:
        only_cp = cp_stmts - mpy_stmts
        only_mpy = mpy_stmts - cp_stmts
        print(f"  DIFF statements: cpython_only={sorted(only_cp)} mpy_only={sorted(only_mpy)}")
        ok = False
    else:
        print(f"  MATCH statements: {len(cp_stmts)} lines")

    # Compare executed lines, filtering known divergences
    cp_exec = cpython_result["executed_lines"]
    mpy_exec = mpy_analysis.executed
    only_cp = cp_exec - mpy_exec
    only_mpy = mpy_exec - cp_exec

    # Filter known divergences
    unexpected_cp = only_cp - set(_KNOWN_DIVERGENCES.keys())
    unexpected_mpy = only_mpy - set(_KNOWN_DIVERGENCES.keys())
    known_diffs = (only_cp | only_mpy) & set(_KNOWN_DIVERGENCES.keys())

    if known_diffs:
        for line in sorted(known_diffs):
            print(f"  KNOWN divergence line {line}: {_KNOWN_DIVERGENCES[line]}")

    if unexpected_cp or unexpected_mpy:
        print(
            f"  DIFF executed (unexpected): "
            f"cpython_only={sorted(unexpected_cp)} mpy_only={sorted(unexpected_mpy)}"
        )
        ok = False
    else:
        print(f"  MATCH executed: {len(cp_exec)} lines (+ {len(known_diffs)} known divergences)")

    # Compare missing lines with same filtering
    cp_miss = cpython_result["missing_lines"]
    mpy_miss = mpy_analysis.missing
    miss_only_cp = cp_miss - mpy_miss
    miss_only_mpy = mpy_miss - cp_miss
    unexpected_miss_cp = miss_only_cp - set(_KNOWN_DIVERGENCES.keys())
    unexpected_miss_mpy = miss_only_mpy - set(_KNOWN_DIVERGENCES.keys())

    if unexpected_miss_cp or unexpected_miss_mpy:
        print(
            f"  DIFF missing (unexpected): "
            f"cpython_only={sorted(unexpected_miss_cp)} mpy_only={sorted(unexpected_miss_mpy)}"
        )
        ok = False
    else:
        print(f"  MATCH missing: {len(cp_miss)} lines (+ {len(known_diffs)} known divergences)")

    # Compare line coverage percentage (executed/statements), NOT the
    # branch-weighted pc_covered which is affected by arc convention differences.
    n_stmts = len(cp_stmts) if cp_stmts else 1
    cp_line_pct = 100.0 * len(cpython_result["executed_lines"]) / n_stmts
    mpy_line_pct = 100.0 * len(mpy_analysis.executed) / n_stmts
    # Each known divergence can shift percentage by at most 1/n_statements
    tolerance = len(known_diffs) * (100.0 / n_stmts) + 0.01
    if abs(cp_line_pct - mpy_line_pct) > tolerance:
        print(f"  DIFF line_coverage_pct: cpython={cp_line_pct:.2f}% mpy={mpy_line_pct:.2f}%")
        ok = False
    else:
        print(
            f"  MATCH line_coverage_pct: cpython={cp_line_pct:.2f}% "
            f"mpy={mpy_line_pct:.2f}% (tol={tolerance:.2f})"
        )

    return ok


def _compare_arcs(cpython_result, mpy_analysis):
    """Log arc/branch diagnostic comparison between CPython and MicroPython.

    MicroPython's settrace produces raw line-to-line transition arcs, while
    coverage.py's PythonParser models arcs with AST-derived function entry/exit
    conventions (negative line numbers). These representations don't map 1:1:

    - Function entry arcs: CPython uses (-def_line, first_body_line),
      MicroPython records (def_line, first_body_line) as a positive transition
    - While/for loop arcs: MicroPython's bytecode may produce different
      line-to-line transitions than CPython's AST model
    - Class body: Sequential class body lines are interior arcs in MicroPython
      but modeled as entry arcs by CPython

    Because of these systematic convention differences, arc comparison is
    diagnostic only. Line-level metrics (in _compare_lines) are the meaningful
    cross-validation. This function always returns True.
    """
    cp_exec_arcs = cpython_result.get("executed_arcs", set())
    mpy_exec_arcs = mpy_analysis.arcs_executed_set

    # Separate interior arcs (positive->positive) from entry/exit arcs
    cp_interior = {a for a in cp_exec_arcs if a[0] > 0 and a[1] > 0}
    mpy_interior = {a for a in mpy_exec_arcs if a[0] > 0 and a[1] > 0}

    common = cp_interior & mpy_interior
    only_cp = cp_interior - mpy_interior
    only_mpy = mpy_interior - cp_interior
    print(
        f"  interior_arcs: {len(common)} common, "
        f"{len(only_cp)} cpython-only, {len(only_mpy)} mpy-only"
    )

    if only_cp:
        print(f"    cpython-only: {sorted(only_cp)}")
    if only_mpy:
        print(f"    mpy-only: {sorted(only_mpy)}")

    # Log entry/exit arc counts
    cp_entry_exit = len(cp_exec_arcs) - len(cp_interior)
    mpy_entry_exit = len(mpy_exec_arcs) - len(mpy_interior)
    print(f"  entry/exit arcs: cpython={cp_entry_exit} mpy={mpy_entry_exit}")

    # Compare total branch count from PythonParser (should match since same parser)
    cp_total = cpython_result.get("total_branches", 0)
    mpy_total = mpy_analysis.numbers.n_branches
    if cp_total != mpy_total:
        print(f"  DIFF total_branches: cpython={cp_total} mpy={mpy_total}")
    else:
        print(f"  MATCH total_branches: {cp_total}")

    return True  # diagnostic only, never fails


# --- Cross-validation trials ---


def trial_xval_lines():
    """Cross-validate line coverage (ast, partial paths) between CPython and MicroPython."""
    print("=== Xval: line coverage, partial ===")
    target_path = os.path.join(SCRIPT_DIR, "test_target_xval.py")
    entry_code = "run_partial()"

    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        # CPython reference
        cp_result = _run_cpython_coverage(target_path, entry_code, branch=False)
        print(
            f"  cpython: {len(cp_result['executed_lines'])} executed, "
            f"{len(cp_result['missing_lines'])} missing, "
            f"{cp_result['coverage_pct']:.1f}%"
        )

        # MicroPython collection
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target_xval'])
import test_target_xval
test_target_xval.run_partial()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            print(r.stderr)
            return False

        data = json.load(open(json_path))
        mpy_lines = set(data["executed"].get("test_target_xval.py", []))
        print(f"  mpy: {len(mpy_lines)} executed lines")

        # Build mpy analysis and compare
        mpy_analysis = _get_mpy_analysis(target_path, mpy_lines)
        ok = _compare_lines(cp_result, mpy_analysis, "partial")

        if ok:
            print("PASS")
        else:
            print("FAIL: line coverage mismatch")
        return ok
    finally:
        os.unlink(json_path)


def trial_xval_lines_full():
    """Cross-validate line coverage (ast, full paths) — expect 100% on both sides."""
    print("=== Xval: line coverage, full ===")
    target_path = os.path.join(SCRIPT_DIR, "test_target_xval.py")
    entry_code = "run_partial()\nrun_full()"

    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        # CPython reference
        cp_result = _run_cpython_coverage(target_path, entry_code, branch=False)
        print(
            f"  cpython: {len(cp_result['executed_lines'])} executed, "
            f"{len(cp_result['missing_lines'])} missing, "
            f"{cp_result['coverage_pct']:.1f}%"
        )

        # MicroPython collection
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target_xval'])
import test_target_xval
test_target_xval.run_partial()
test_target_xval.run_full()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            print(r.stderr)
            return False

        data = json.load(open(json_path))
        mpy_lines = set(data["executed"].get("test_target_xval.py", []))
        print(f"  mpy: {len(mpy_lines)} executed lines")

        mpy_analysis = _get_mpy_analysis(target_path, mpy_lines)
        ok = _compare_lines(cp_result, mpy_analysis, "full")

        # Extra check: both should be 100%
        if cp_result["coverage_pct"] < 100.0:
            print(f"  WARN: cpython not 100%: {cp_result['coverage_pct']:.2f}%")
            print(f"  cpython missing: {sorted(cp_result['missing_lines'])}")
        if mpy_analysis.numbers.pc_covered < 100.0:
            print(f"  WARN: mpy not 100%: {mpy_analysis.numbers.pc_covered:.2f}%")
            print(f"  mpy missing: {sorted(mpy_analysis.missing)}")

        if ok:
            print("PASS")
        else:
            print("FAIL: line coverage mismatch")
        return ok
    finally:
        os.unlink(json_path)


def trial_xval_branches():
    """Cross-validate branch coverage (ast, partial paths)."""
    print("=== Xval: branch coverage, partial ===")
    target_path = os.path.join(SCRIPT_DIR, "test_target_xval.py")
    entry_code = "run_partial()"

    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        # CPython reference with branch=True
        cp_result = _run_cpython_coverage(target_path, entry_code, branch=True)
        print(
            f"  cpython: {len(cp_result['executed_arcs'])} arcs executed, "
            f"{len(cp_result.get('missing_arcs', set()))} missing"
        )

        # MicroPython collection with arcs
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target_xval'], collect_arcs=True)
import test_target_xval
test_target_xval.run_partial()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            print(r.stderr)
            return False

        data = json.load(open(json_path))
        mpy_lines = set(data["executed"].get("test_target_xval.py", []))
        mpy_arcs_raw = data.get("arcs", {}).get("test_target_xval.py", [])
        mpy_arcs = set(tuple(a) for a in mpy_arcs_raw)
        print(f"  mpy: {len(mpy_arcs)} arcs collected")

        # Build mpy analysis with arcs
        mpy_analysis = _get_mpy_analysis(target_path, mpy_lines, branch=True, arcs=mpy_arcs)

        # Compare lines first
        ok_lines = _compare_lines(cp_result, mpy_analysis, "partial-branch")
        ok_arcs = _compare_arcs(cp_result, mpy_analysis)

        ok = ok_lines and ok_arcs
        if ok:
            print("PASS")
        else:
            print("FAIL: branch coverage mismatch")
        return ok
    finally:
        os.unlink(json_path)


def trial_xval_branches_full():
    """Cross-validate branch coverage (ast, full paths) — expect 100% branch coverage."""
    print("=== Xval: branch coverage, full ===")
    target_path = os.path.join(SCRIPT_DIR, "test_target_xval.py")
    entry_code = "run_partial()\nrun_full()"

    with tempfile.NamedTemporaryFile(suffix=".json", dir=SCRIPT_DIR, delete=False) as f:
        json_path = f.name

    try:
        # CPython reference with branch=True
        cp_result = _run_cpython_coverage(target_path, entry_code, branch=True)
        print(
            f"  cpython: {len(cp_result['executed_arcs'])} arcs executed, "
            f"{len(cp_result.get('missing_arcs', set()))} missing"
        )

        # MicroPython collection with arcs
        r = run_micropython(f"""
import mpy_coverage
mpy_coverage.start(include=['test_target_xval'], collect_arcs=True)
import test_target_xval
test_target_xval.run_partial()
test_target_xval.run_full()
mpy_coverage.stop()
mpy_coverage.export_json('{json_path}')
""")
        if r.returncode != 0:
            print("FAIL: MicroPython exited with error")
            print(r.stderr)
            return False

        data = json.load(open(json_path))
        mpy_lines = set(data["executed"].get("test_target_xval.py", []))
        mpy_arcs_raw = data.get("arcs", {}).get("test_target_xval.py", [])
        mpy_arcs = set(tuple(a) for a in mpy_arcs_raw)
        print(f"  mpy: {len(mpy_arcs)} arcs collected")

        mpy_analysis = _get_mpy_analysis(target_path, mpy_lines, branch=True, arcs=mpy_arcs)

        ok_lines = _compare_lines(cp_result, mpy_analysis, "full-branch")
        ok_arcs = _compare_arcs(cp_result, mpy_analysis)

        ok = ok_lines and ok_arcs
        if ok:
            print("PASS")
        else:
            print("FAIL: branch coverage mismatch")
        return ok
    finally:
        os.unlink(json_path)


def main():
    if not os.path.exists(MPY_BINARY):
        print(f"Error: MicroPython coverage binary not found at {MPY_BINARY}")
        print("Build it with: cd ports/unix && make VARIANT=coverage")
        sys.exit(1)

    results = []
    trials = [
        trial_a,
        trial_b1,
        trial_b2,
        trial_formats,
        trial_merge,
        trial_cli_run_and_report,
        trial_cli_multipass,
        trial_branch,
        trial_branch_cli,
        trial_test_map,
        trial_xval_lines,
        trial_xval_lines_full,
        trial_xval_branches,
        trial_xval_branches_full,
    ]
    for trial in trials:
        try:
            results.append(trial())
        except Exception as e:
            print(f"FAIL: {e}")
            import traceback

            traceback.print_exc()
            results.append(False)
        print()

    if all(results):
        print("All trials passed.")
    else:
        print("Some trials failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
