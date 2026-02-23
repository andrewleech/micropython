#!/usr/bin/env python3
# End-to-end trial runner for MicroPython coverage toolchain.
# Runs on CPython, drives the MicroPython coverage binary.

import json
import os
import shutil
import subprocess
import sys
import tempfile

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

        # Generate branch report using ast method
        r = run_report(json_path, "ast", ["--branch", "--show-missing"])
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

        # Report with --branch
        r = subprocess.run(
            [
                sys.executable,
                cli_script,
                "--data-dir",
                data_dir,
                "report",
                "--method",
                "ast",
                "--branch",
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
