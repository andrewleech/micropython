"""Check external scanner policy against explicit positive and negative sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from .runner import OWNED_ARTIFACTS, SCANNERS, run
from .scope import load_scope, source_identity, source_path


FIXTURE_SUMMARY = "fixture-summary.json"


def fixture_directory() -> Path:
    """Return packaged source fixtures; consumers select their own scope and rules."""
    return Path(__file__).resolve().parent / "data" / "fixtures"


def _physical_location(location, bases):
    uri = location.get("uri")
    if not isinstance(uri, str):
        raise ValueError("fixture report source location must have a URI")
    base_id = location.get("uriBaseId")
    if base_id:
        base = bases.get(base_id, {}).get("uri")
        if not isinstance(base, str):
            raise ValueError(f"fixture report has an unresolved URI base: {base_id}")
        if not urlparse(uri).scheme and not uri.startswith("/"):
            uri = base.rstrip("/") + "/" + uri
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError(f"fixture report has a nonlocal file URI: {uri}")
        physical = Path(url2pathname(parsed.path))
    elif not parsed.scheme:
        physical = Path(unquote(uri))
    else:
        raise ValueError(f"fixture report has an unsupported source URI: {uri}")
    if not physical.is_absolute():
        raise ValueError(f"fixture report source is not rebased to an absolute location: {uri}")
    return str(physical.resolve())


def _findings(summary, scanner):
    findings = []
    for invocation in summary["invocations"]:
        if not invocation["report_valid"]:
            continue
        report = Path(invocation["report"])
        if scanner == "pysa" and not report.is_dir():
            continue
        if report.is_dir():
            payload = json.loads((report / "errors.json").read_text(encoding="utf-8"))
            for result in payload:
                path = result.get("path") or result.get("filename")
                if not isinstance(path, str) or not Path(path).is_absolute():
                    raise ValueError("Pysa fixture source is missing or not rebased")
                findings.append(
                    (
                        str(result.get("code", result.get("rule", ""))),
                        str(Path(path).resolve()),
                        result.get("define"),
                    )
                )
        else:
            payload = json.loads(report.read_text(encoding="utf-8"))
            for sarif_run in payload["runs"]:
                bases = sarif_run.get("originalUriBaseIds", {})
                for result in sarif_run.get("results", []):
                    rule = result.get("ruleId")
                    if not isinstance(rule, str):
                        raise ValueError("fixture SARIF finding is missing ruleId")
                    locations = result.get("locations", [])
                    if not locations:
                        raise ValueError("fixture SARIF finding is missing a source location")
                    for location in locations:
                        artifact = location.get("physicalLocation", {}).get("artifactLocation", {})
                        findings.append((rule, _physical_location(artifact, bases), None))
    return findings


def _expectations(path, scope):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
    ):
        raise ValueError("fixture expectations require schema_version 1")
    selected = {
        source_identity(record): source_path(scope, record)
        for records in scope["groups"].values()
        for record in records
    }
    expectations = {}
    for kind in ("positive", "negative"):
        entries = payload.get(kind)
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"fixture expectations require nonempty {kind} sources")
        normalized = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("root"), str)
                or not isinstance(entry.get("path"), str)
            ):
                raise ValueError(f"fixture {kind} source requires root and path")
            identity = source_identity(entry)
            if identity not in selected:
                raise ValueError(f"fixture expectation is not a selected source: {identity}")
            rule = entry.get("rule")
            if rule is not None and (not isinstance(rule, str) or not rule):
                raise ValueError("fixture rule must be a nonempty string when supplied")
            callable_name = entry.get("callable")
            if callable_name is not None and (
                not isinstance(callable_name, str) or not callable_name
            ):
                raise ValueError("fixture callable must be a nonempty string when supplied")
            normalized.append(
                {
                    "identity": identity,
                    "physical_path": str(selected[identity]),
                    "rule": rule,
                    "callable": callable_name,
                }
            )
        expectations[kind] = normalized
    for positive in expectations["positive"]:
        for negative in expectations["negative"]:
            same_path = positive["physical_path"] == negative["physical_path"]
            rules_overlap = (
                positive["rule"] is None
                or negative["rule"] is None
                or positive["rule"] == negative["rule"]
            )
            callables_overlap = (
                positive["callable"] is None
                or negative["callable"] is None
                or positive["callable"] == negative["callable"]
            )
            if same_path and rules_overlap and callables_overlap:
                raise ValueError("positive and negative expectation selectors overlap")
    return expectations


def check(
    scanner,
    scope_path,
    expectations_path,
    policy_dir,
    output_dir,
    executable=None,
    pyrefly_executable=None,
):
    output_dir = Path(output_dir).resolve()
    result_path = output_dir / FIXTURE_SUMMARY
    scope_path, expectations_path, policy_dir = (
        Path(scope_path).resolve(),
        Path(expectations_path).resolve(),
        Path(policy_dir).resolve(),
    )
    owned = OWNED_ARTIFACTS | {FIXTURE_SUMMARY}

    def conflicts(path):
        return any(
            path == output_dir / name or output_dir / name in path.parents for name in owned
        )

    if (
        conflicts(scope_path)
        or conflicts(expectations_path)
        or policy_dir == output_dir
        or output_dir in policy_dir.parents
    ):
        print("fixture inputs conflict with output artifacts", file=sys.stderr)
        return 1
    receipt = {
        "schema_version": 1,
        "scanner": scanner,
        "status": "operation_failed",
        "findings_match": False,
    }
    try:
        scope = load_scope(scope_path)
        if any(
            conflicts(source_path(scope, record))
            for records in scope["groups"].values()
            for record in records
        ):
            print("fixture source conflicts with owned output artifacts", file=sys.stderr)
            return 1
        expectations = _expectations(expectations_path, scope)
        output_dir.mkdir(parents=True, exist_ok=True)
        if result_path.is_dir() and not result_path.is_symlink():
            raise ValueError("fixture-summary.json must not be a directory")
        result_path.unlink(missing_ok=True)
        rc = run(scanner, scope_path, policy_dir, output_dir, executable, pyrefly_executable)
        summary = json.loads((output_dir / "analysis-summary.json").read_text(encoding="utf-8"))
        receipt["analysis_summary"] = str(output_dir / "analysis-summary.json")
        receipt["coverage_status"] = summary["coverage_status"]
        if rc:
            receipt["status"] = (
                "blocked"
                if summary.get("prerequisites", {}).get("compatibility_blockers")
                else "operation_failed"
            )
            receipt["operation_error"] = summary.get("operation_error", "scanner operation failed")
        else:
            findings = _findings(summary, scanner)
            failures = []
            for kind, entries in expectations.items():
                for entry in entries:
                    present = any(
                        path == entry["physical_path"]
                        and (entry["rule"] is None or rule == entry["rule"])
                        and (entry["callable"] is None or callable_name == entry["callable"])
                        for rule, path, callable_name in findings
                    )
                    if present != (kind == "positive"):
                        failures.append(
                            {
                                "kind": kind,
                                **entry,
                                "reason": "missing finding"
                                if kind == "positive"
                                else "unexpected finding",
                            }
                        )
            receipt.update(
                expectations=expectations,
                findings=[
                    {"rule": rule, "physical_path": path, "callable": callable_name}
                    for rule, path, callable_name in findings
                ],
                failures=failures,
                findings_match=not failures,
            )
            receipt["status"] = (
                "failed"
                if failures
                else "passed"
                if summary["coverage_status"] == "assessed"
                else "incomplete"
            )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        receipt["operation_error"] = str(exc)
        print(str(exc), file=sys.stderr)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if result_path.is_symlink():
            result_path.unlink()
        result_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0 if receipt["status"] == "passed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanner", choices=SCANNERS, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--expectations", type=Path, required=True)
    parser.add_argument("--policy-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--executable")
    parser.add_argument("--pyrefly-executable")
    args = parser.parse_args(argv)
    return check(
        args.scanner,
        args.scope,
        args.expectations,
        args.policy_dir,
        args.output_dir,
        args.executable,
        args.pyrefly_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
