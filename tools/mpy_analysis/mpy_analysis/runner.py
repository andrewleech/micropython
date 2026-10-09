"""Run report-only scanners over an explicit, externally owned source scope."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from .scope import load_scope, source_identity, source_path
from .typing_environment import load_typing_environment

SCANNERS = (
    "opengrep-stable",
    "opengrep-interfile-alpha",
    "semgrep-ce",
    "ruff-s",
    "pysa",
    "pyrefly",
)
PATTERN_SCANNERS = SCANNERS[:3]
PYTHON_GROUPS = {"runtime_frozen_python", "host_and_example_python"}
PATTERN_GROUPS = PYTHON_GROUPS | {"frontend", "native"}
PYTHON_SUFFIXES = {".py", ".pyi"}
NATIVE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
OWNED_ARTIFACTS = {
    "analysis-summary.json",
    "analysis-prerequisites.json",
    "analysis-scope.json",
    "pyrefly.sarif",
    "pyrefly.log",
    "pysa.log",
    "pysa-results",
    "ruff-s.sarif",
    "ruff-s.log",
} | {
    f"{scanner}-{group}.{suffix}"
    for scanner in PATTERN_SCANNERS
    for group in ("project", "native-c")
    for suffix in ("sarif", "json", "log")
}
PATH_KEYS = {"uri", "path", "filename", "file", "absolute_path", "relative_path"}


class AnalysisToolError(RuntimeError):
    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _clear_reports(output_dir: Path) -> None:
    """Remove this runner's artifacts, without following artifact symlinks."""
    for name in sorted(OWNED_ARTIFACTS):
        path = output_dir / name
        if path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path)


def _target_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"invalid staging path: {value!r}")
    parts = value.split("/")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in parts) or ":" in parts[0]:
        raise ValueError(f"staging path must be relative and contained: {value!r}")
    return PurePosixPath(value)


def _stage_sources(
    scope: dict[str, Any], records: list[tuple[str, dict[str, Any]]], destination: Path
):
    """Stage every import identity; validate the entire tree before copying any file."""
    planned = {}
    targets = []
    for group, record in records:
        target_text = record.get("target_path") if group == "runtime_frozen_python" else None
        target = _target_path(target_text or f"_roots/{record['root']}/{record['path']}")
        physical = source_path(scope, record).resolve()
        if target in planned and planned[target] != physical:
            raise ValueError(f"staging collision at {target}: {planned[target]} and {physical}")
        planned[target] = physical
        targets.append(target)
    for target in planned:
        for parent in target.parents:
            if parent in planned:
                raise ValueError(f"staging file/directory collision: {parent} and {target}")
    base = destination.resolve()
    for target in planned:
        staged = destination.joinpath(*target.parts)
        if not staged.resolve().is_relative_to(base):
            raise ValueError(f"staging path escapes destination: {target}")
        if staged.exists() or staged.is_symlink():
            raise ValueError(f"staging destination already exists: {staged}")
        for parent in staged.parents:
            if parent.is_symlink():
                raise ValueError(f"staging parent must not be a symlink: {parent}")
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"staging file/directory collision: {parent}")
            if parent == destination:
                break
    replacements = {}
    for target, physical in planned.items():
        staged = destination.joinpath(*target.parts)
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(physical, staged)
        replacements[str(staged)] = str(physical)
    return [destination.joinpath(*target.parts) for target in targets], replacements


def _path_aliases(replacements: dict[str, str], cwd: Path) -> dict[str, str]:
    aliases = {}
    for staged, source in replacements.items():
        path = Path(staged)
        relative = os.path.relpath(path, cwd)
        for alias in (staged, path.as_posix(), path.as_uri(), relative, Path(relative).as_posix()):
            if alias in aliases and aliases[alias] != source:
                raise ValueError(f"ambiguous report path: {alias}")
            aliases[alias] = source
    return aliases


def _rebase_report_paths(
    path: Path,
    replacements: dict[str, str],
    work: Path,
    scan_root: Path,
    *,
    cwd: Path | None = None,
) -> None:
    """Rebase exact source locations, never diagnostics, snippets or arbitrary strings."""
    if path.is_dir():
        for report in sorted(path.rglob("*.json")):
            if not report.is_symlink():
                _rebase_report_paths(report, replacements, work, scan_root, cwd=cwd)
        return
    if not path.is_file() or path.is_symlink():
        return
    text = path.read_text(encoding="utf-8")
    json_lines = False
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        try:
            report = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            return
        json_lines = True
    aliases = _path_aliases(replacements, cwd or scan_root)

    def mapped(value, uri=False, base=None):
        if not isinstance(value, str):
            return None
        candidate = value
        if base and not urlparse(candidate).scheme and not Path(candidate).is_absolute():
            candidate = base.rstrip("/") + "/" + candidate
        source = aliases.get(candidate)
        if source is None:
            if candidate.startswith("file://"):
                parsed = urlparse(candidate)
                if parsed.netloc not in {"", "localhost"}:
                    return None
                candidate = url2pathname(parsed.path)
            else:
                candidate = unquote(candidate) if uri else candidate
            source = aliases.get(candidate)
        return Path(source).as_uri() if source and uri else source

    def visit(value, bases=None):
        if isinstance(value, list):
            for item in value:
                visit(item, bases)
        elif isinstance(value, dict):
            if isinstance(value.get("originalUriBaseIds"), dict):
                bases = {
                    key: item.get("uri")
                    for key, item in value["originalUriBaseIds"].items()
                    if isinstance(item, dict)
                }
            for key, item in list(value.items()):
                if key in PATH_KEYS and isinstance(item, str):
                    base = (bases or {}).get(value.get("uriBaseId")) if key == "uri" else None
                    replacement = mapped(item, key == "uri", base)
                    if replacement is not None:
                        value[key] = replacement
                        if key == "uri":
                            value.pop("uriBaseId", None)
                elif key in {"scanned", "skipped"} and isinstance(item, list):
                    value[key] = [
                        mapped(entry) or entry if isinstance(entry, str) else entry
                        for entry in item
                    ]
                    for entry in value[key]:
                        visit(entry, bases)
                elif key != "originalUriBaseIds":
                    visit(item, bases)
            if isinstance(value.get("originalUriBaseIds"), dict):

                def references(node):
                    if isinstance(node, list):
                        return set().union(*(references(item) for item in node))
                    if isinstance(node, dict):
                        result = (
                            {node["uriBaseId"]}
                            if isinstance(node.get("uriBaseId"), str)
                            else set()
                        )
                        for key, item in node.items():
                            if key != "originalUriBaseIds":
                                result.update(references(item))
                        return result
                    return set()

                used = references(value)
                for name, specification in list(value["originalUriBaseIds"].items()):
                    uri = specification.get("uri") if isinstance(specification, dict) else None
                    if name not in used and isinstance(uri, str):
                        base_path = (
                            url2pathname(urlparse(uri).path) if uri.startswith("file://") else uri
                        )
                        if Path(base_path) in {work, scan_root}:
                            del value["originalUriBaseIds"][name]

    visit(report)
    if json_lines:
        path.write_text("\n".join(json.dumps(item) for item in report) + "\n", encoding="utf-8")
    else:
        _write_json(path, report)


def _rebase_after_invocation(path, replacements, work, scan_root, invocations, *, cwd=None):
    active_error = sys.exc_info()[1]
    try:
        _rebase_report_paths(path, replacements, work, scan_root, cwd=cwd)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if invocations:
            invocations[-1]["report_rebase_error"] = str(exc)
        if active_error is None:
            raise
        print(f"Partial report rebasing failed: {exc}", file=sys.stderr)


def _validate_report(path: Path, kind: str) -> None:
    if path.is_symlink():
        raise AnalysisToolError(f"analysis report must not be a symlink: {path}")
    if kind == "pysa" and (not path.is_dir() or not (path / "errors.json").is_file()):
        raise AnalysisToolError(f"Pysa did not produce errors.json: {path}")
    files = list(path.rglob("*.json")) if kind == "pysa" and path.is_dir() else [path]
    if not files or any(not file.is_file() or file.is_symlink() for file in files):
        raise AnalysisToolError(f"analysis tool did not produce its report: {path}")
    for file in files:
        text = file.read_text(encoding="utf-8")
        if not text.strip():
            raise AnalysisToolError(f"analysis tool produced an empty report: {file}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            if kind != "pysa":
                raise AnalysisToolError(f"analysis tool produced invalid JSON: {file}")
            try:
                payload = [json.loads(line) for line in text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise AnalysisToolError(f"analysis tool produced invalid JSON: {file}") from exc
        if kind == "sarif" and (
            not isinstance(payload, dict) or not isinstance(payload.get("runs"), list)
        ):
            raise AnalysisToolError(f"analysis tool produced invalid SARIF: {file}")
        if kind == "sarif" and any(
            not isinstance(run, dict) or not isinstance(run.get("results", []), list)
            for run in payload["runs"]
        ):
            raise AnalysisToolError(f"analysis tool produced invalid SARIF runs: {file}")
        if kind == "pysa" and file.name == "errors.json" and not isinstance(payload, list):
            raise AnalysisToolError(f"Pysa produced invalid errors.json: {file}")


def _sarif_coverage_gaps(report):
    payload = json.loads(report.read_text(encoding="utf-8"))
    gaps = []
    for run_index, sarif_run in enumerate(payload["runs"]):
        invocations = sarif_run.get("invocations", [])
        if not isinstance(invocations, list):
            raise ValueError("SARIF invocations must be a list")
        for invocation_index, invocation in enumerate(invocations):
            if not isinstance(invocation, dict):
                raise ValueError("SARIF invocation must be an object")
            if invocation.get("executionSuccessful") is False:
                gaps.append(
                    {
                        "run": run_index,
                        "invocation": invocation_index,
                        "reason": "scanner reported unsuccessful execution",
                    }
                )
            notifications = invocation.get("toolExecutionNotifications", [])
            if not isinstance(notifications, list):
                raise ValueError("SARIF toolExecutionNotifications must be a list")
            for notification in notifications:
                if not isinstance(notification, dict):
                    raise ValueError("SARIF tool execution notification must be an object")
                message = notification.get("message", {})
                text = (
                    message.get("text", message.get("markdown", ""))
                    if isinstance(message, dict)
                    else str(message)
                )
                descriptor = notification.get("descriptor", {})
                descriptor_id = descriptor.get("id", "") if isinstance(descriptor, dict) else ""
                if notification.get("level") in {"warning", "error"} or re.search(
                    r"skip|timeout|timed.out|pars(?:e|ing).*(?:fail|error)|syntax.?error|unsupported|incomplete",
                    f"{descriptor_id} {text}",
                    re.IGNORECASE,
                ):
                    gaps.append(
                        {
                            "run": run_index,
                            "invocation": invocation_index,
                            "reason": "scanner reported an execution coverage gap",
                            "notification": notification,
                        }
                    )
    return gaps


def _pattern_coverage(report, expected_paths, cwd):
    payload = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        raise ValueError("pattern scanner JSON report must contain paths.scanned")
    scanned = payload["paths"].get("scanned")
    skipped = payload["paths"].get("skipped", [])
    errors = payload.get("errors", [])
    if not isinstance(scanned, list) or any(not isinstance(path, str) for path in scanned):
        raise ValueError("pattern scanner paths.scanned must be a list of source paths")
    if not isinstance(skipped, list) or not isinstance(errors, list):
        raise ValueError("pattern scanner skipped paths and errors must be lists")

    def canonical(value):
        if value.startswith("file://"):
            parsed = urlparse(value)
            if parsed.netloc not in {"", "localhost"}:
                raise ValueError("pattern scanner source URI must be local")
            value = url2pathname(parsed.path)
        path = Path(value)
        return str((path if path.is_absolute() else cwd / path).resolve())

    actual = {canonical(path) for path in scanned}
    missing = sorted({canonical(path) for path in expected_paths} - actual)
    gaps = []
    if missing:
        gaps.append(
            {"reason": "selected files absent from scanner paths.scanned", "paths": missing}
        )
    if skipped:
        gaps.append({"reason": "scanner reported skipped inputs", "skipped": skipped})
    if errors:
        gaps.append({"reason": "scanner reported analysis errors", "errors": errors})
    return {
        "scanned": sorted(actual),
        "missing_selected": missing,
        "skipped": skipped,
        "errors": errors,
    }, gaps


def _invoke(
    command,
    cwd,
    report,
    log_path,
    invocations,
    allowed=(0, 1),
    kind="sarif",
    records=(),
    coverage_report=None,
    expected_paths=(),
):
    invocation = {
        "command": command,
        "cwd": str(cwd),
        "report": str(report),
        "exit_code": None,
        "started": False,
        "report_valid": False,
        "coverage_gaps": [],
        "sources": [
            {
                "group": group,
                "identity": source_identity(record),
                "target_path": record.get("target_path"),
            }
            for group, record in records
        ],
    }
    invocations.append(invocation)
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    invocation["started"] = True
    invocation["exit_code"] = result.returncode
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode not in allowed:
        raise AnalysisToolError(
            f"analysis tool failed (exit {result.returncode}): {command[0]}", result.returncode
        )
    try:
        _validate_report(report, kind)
    except AnalysisToolError as exc:
        exc.returncode = result.returncode
        raise
    if kind == "sarif":
        invocation["coverage_gaps"] = _sarif_coverage_gaps(report)
    if coverage_report is not None:
        invocation["coverage_report"] = str(coverage_report)
        _validate_report(coverage_report, "json")
        evidence, gaps = _pattern_coverage(coverage_report, expected_paths, cwd)
        invocation["coverage_evidence"] = evidence
        invocation["coverage_gaps"].extend(gaps)
    invocation["report_valid"] = True
    return result.returncode


def _eligible(scanner, group, record):
    suffix = Path(record["path"]).suffix.lower()
    if scanner in {"pyrefly", "pysa"}:
        return group == "runtime_frozen_python" and suffix in PYTHON_SUFFIXES
    if scanner == "ruff-s":
        return group in PYTHON_GROUPS and suffix in PYTHON_SUFFIXES
    return group in PATTERN_GROUPS and (group != "native" or suffix in NATIVE_SUFFIXES)


def _records(scope, scanner):
    return [
        (group, record)
        for group, records in scope["groups"].items()
        for record in records
        if _eligible(scanner, group, record)
    ]


def _executable(value, default):
    value = value or default
    return str(Path(value).expanduser().resolve()) if "/" in value or "\\" in value else value


def _run_ruff(scope, policy_dir, output_dir, executable, invocations):
    policy = policy_dir / "ruff.toml"
    if not policy.is_file():
        raise FileNotFoundError(f"Ruff policy missing: {policy}")
    records = _records(scope, "ruff-s")
    files = sorted({str(source_path(scope, record)) for _, record in records})
    report = output_dir / "ruff-s.sarif"
    try:
        return _invoke(
            [
                _executable(executable, "ruff"),
                "check",
                "--config",
                str(policy),
                "--output-format",
                "sarif",
                "--output-file",
                str(report),
                *files,
            ],
            policy_dir,
            report,
            output_dir / "ruff-s.log",
            invocations,
            records=records,
        )
    finally:
        _rebase_after_invocation(
            report, {file: file for file in files}, policy_dir, policy_dir, invocations
        )


def _run_pattern(scanner, scope, policy_dir, output_dir, executable, invocations):
    policy = json.loads((policy_dir / "scanners.json").read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError(
            "scanners.json must be an object with project/native-c configuration lists"
        )
    records = _records(scope, scanner)
    interfile = scanner == "opengrep-interfile-alpha"
    rc = 0
    with tempfile.TemporaryDirectory(prefix="mpy-analysis-") as directory:
        work = Path(directory)
        root = work / "src"
        root.mkdir()
        if interfile:
            staged_records = [
                (group, record)
                for group, group_records in scope["groups"].items()
                if group in PATTERN_GROUPS
                for record in group_records
            ]
            staged_files, replacements = _stage_sources(scope, staged_records, root)
            files = [
                file
                for (group, record), file in zip(staged_records, staged_files)
                if _eligible(scanner, group, record)
            ]
        else:
            files = [source_path(scope, record) for _, record in records]
            replacements = {str(file): str(file) for file in files}
        for name in ("project", "native-c"):
            selected = sorted(
                {
                    str(file)
                    for (group, _), file in zip(records, files)
                    if (group == "native") == (name == "native-c")
                }
            )
            selected_records = [
                (group, record)
                for group, record in records
                if (group == "native") == (name == "native-c")
            ]
            if not selected:
                continue
            configs = policy.get(name)
            if (
                not isinstance(configs, list)
                or not configs
                or any(not isinstance(item, str) or not item for item in configs)
            ):
                raise ValueError(f"scanner policy must supply nonempty {name} configs")
            command = [
                _executable(executable, "semgrep" if scanner == "semgrep-ce" else "opengrep"),
                "scan",
            ]
            for config in configs:
                candidate = policy_dir / config
                if candidate.exists():
                    command.extend(["--config", str(candidate.resolve())])
                elif config.startswith(("p/", "r/", "https://", "http://")):
                    command.extend(["--config", config])
                else:
                    raise FileNotFoundError(f"scanner policy missing: {candidate}")
            report = output_dir / f"{scanner}-{name}.sarif"
            coverage_report = output_dir / f"{scanner}-{name}.json"
            command.extend(
                [
                    "--no-git-ignore",
                    "--x-ignore-semgrepignore-files",
                    "--sarif-output",
                    str(report),
                    "--json-output",
                    str(coverage_report),
                ]
            )
            command.append("--no-error" if scanner == "semgrep-ce" else "--jobs=4")
            if interfile:
                command.append("--taint-interfile")
            command.extend(selected)
            try:
                rc = max(
                    rc,
                    _invoke(
                        command,
                        root if interfile else policy_dir,
                        report,
                        output_dir / f"{scanner}-{name}.log",
                        invocations,
                        records=selected_records,
                        coverage_report=coverage_report,
                        expected_paths=selected,
                    ),
                )
            finally:
                _rebase_after_invocation(
                    report,
                    replacements,
                    work if interfile else policy_dir,
                    root if interfile else policy_dir,
                    invocations,
                )
                _rebase_after_invocation(
                    coverage_report,
                    replacements,
                    work if interfile else policy_dir,
                    root if interfile else policy_dir,
                    invocations,
                )
    return rc


def _run_types(
    scanner, scope, policy_dir, output_dir, executable, pyrefly_executable, invocations
):
    environment = load_typing_environment(
        policy_dir, scope["port"], target=scope["target"], variant=scope["variant"]
    )
    prerequisites = dict(environment.prerequisite_info)
    prerequisites["coverage_status"] = "incomplete"
    _write_json(output_dir / "analysis-prerequisites.json", prerequisites)
    binary = _executable(executable if scanner == "pyrefly" else pyrefly_executable, "pyrefly")
    version_command = [binary, "--version"]
    try:
        version_result = subprocess.run(
            version_command, cwd=policy_dir, check=False, capture_output=True, text=True
        )
    except OSError as exc:
        prerequisites["version_check"] = {"command": version_command, "operation_error": str(exc)}
        _write_json(output_dir / "analysis-prerequisites.json", prerequisites)
        raise
    version_text = version_result.stdout + version_result.stderr
    version_match = re.search(r"\b(\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*))\b", version_text)
    actual_version = version_match.group(1) if version_match else None
    prerequisites["version_check"] = {
        "command": version_command,
        "exit_code": version_result.returncode,
        "output": version_text,
        "actual_version": actual_version,
    }
    _write_json(output_dir / "analysis-prerequisites.json", prerequisites)
    if version_result.returncode != 0:
        raise AnalysisToolError(
            "Pyrefly prerequisite version check failed", version_result.returncode
        )
    if actual_version != prerequisites.get("expected_pyrefly_version"):
        raise ValueError(
            f"Pyrefly prerequisite version mismatch: expected {prerequisites.get('expected_pyrefly_version')}, found {actual_version}"
        )
    if prerequisites.get("compatibility_blockers"):
        raise ValueError(
            "unsupported typing prerequisite: "
            + "; ".join(prerequisites["compatibility_blockers"])
        )
    with tempfile.TemporaryDirectory(prefix="mpy-analysis-") as directory:
        work = Path(directory)
        root = work / "src"
        root.mkdir()
        records = _records(scope, scanner)
        files, replacements = _stage_sources(scope, records, root)
        config = work / "pyrefly.toml"
        config.write_text(environment.pyrefly_config(root), encoding="utf-8")
        report = output_dir / "pyrefly.sarif"
        pysa_input = work / "pyrefly-pysa.json"
        command = [
            binary,
            "check",
            "--config",
            str(config),
            "--search-path",
            str(root),
            "--search-path",
            str(environment.stub_directory),
            "--typeshed-path",
            str(environment.typeshed_directory),
            "--disable-search-path-heuristics",
            "true",
            "--report-pysa",
            str(pysa_input),
            "--report-pysa-format",
            "json",
            "--output",
            f"sarif:{report}",
            *map(str, files),
        ]
        try:
            rc = _invoke(
                command, work, report, output_dir / "pyrefly.log", invocations, records=records
            )
        finally:
            _rebase_after_invocation(report, replacements, work, root, invocations, cwd=work)
        prerequisites.update(pyrefly_exit_code=rc, type_diagnostics_present=rc == 1)
        _write_json(output_dir / "analysis-prerequisites.json", prerequisites)
        if scanner == "pyrefly":
            return rc
        _validate_report(pysa_input, "json")
        models = policy_dir / "pysa"
        if (
            not models.is_dir()
            or not (models / "taint.config").is_file()
            or not any(models.rglob("*.pysa"))
        ):
            raise FileNotFoundError(f"Pysa models/config missing: {models}")
        _write_json(
            work / ".pyre_configuration",
            {
                "source_directories": [str(root)],
                "search_path": [str(root), str(environment.stub_directory)],
                "workers": 1,
            },
        )
        results = output_dir / "pysa-results"
        command = [
            _executable(executable, "pyre"),
            "--noninteractive",
            "--dot-pyre-directory",
            str(work / ".pyre"),
            "analyze",
            "--taint-models-path",
            str(models),
            "--save-results-to",
            str(results),
            "--output-format",
            "json",
            "--pyrefly-results",
            str(pysa_input),
        ]
        families = prerequisites.get("common_models", [])
        if families:
            from .common_models import common_model_paths

            for model_path in common_model_paths(families):
                command.extend(["--taint-models-path", str(model_path)])
        try:
            return _invoke(
                command,
                work,
                results,
                output_dir / "pysa.log",
                invocations,
                kind="pysa",
                records=records,
            )
        finally:
            _rebase_after_invocation(results, replacements, work, root, invocations, cwd=work)


def _findings(output_dir):
    count = 0
    for report in (output_dir / name for name in OWNED_ARTIFACTS if name.endswith(".sarif")):
        if not report.is_file() or report.is_symlink():
            continue
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                count += sum(
                    len(run.get("results", []))
                    for run in payload.get("runs", [])
                    if isinstance(run, dict) and isinstance(run.get("results", []), list)
                )
        except (OSError, ValueError, TypeError):
            continue
    try:
        errors_path = output_dir / "pysa-results" / "errors.json"
        if errors_path.is_symlink() or errors_path.parent.is_symlink():
            return count
        errors = json.loads(errors_path.read_text(encoding="utf-8"))
        if isinstance(errors, list):
            count += len(errors)
    except (OSError, ValueError):
        pass
    return count


def _coverage(scope, scanner, invocations):
    submitted = set()
    assessed = set()
    for invocation in invocations:
        if not invocation["started"]:
            continue
        for source in invocation.get("sources", []):
            key = (source["group"], source["identity"], source["target_path"])
            submitted.add(key)
            if (
                invocation["report_valid"]
                and not invocation.get("coverage_gaps")
                and not invocation.get("report_rebase_error")
            ):
                assessed.add(key)
    groups = {}
    for group, records in scope.get("groups", {}).items():
        keys = [(group, source_identity(record), record.get("target_path")) for record in records]
        groups[group] = {
            "selected": len(records),
            "submitted": sum(key in submitted for key in keys),
            "unassessed": [
                source_identity(record)
                for record, key in zip(records, keys)
                if key not in assessed
            ],
            "status": "assessed" if all(key in assessed for key in keys) else "not_assessed",
        }
        if scanner in {"pyrefly", "pysa"} and any(
            _eligible(scanner, group, record) for record in records
        ):
            groups[group]["status"] = "incomplete"
    return groups


def run(
    scanner: str,
    scope_path: Path,
    policy_dir: Path,
    output_dir: Path,
    executable: str | None = None,
    pyrefly_executable: str | None = None,
) -> int:
    scope_path, policy_dir, output_dir = (
        scope_path.resolve(),
        policy_dir.resolve(),
        output_dir.resolve(),
    )

    def overlaps_owned(path):
        return any(
            path == output_dir / name or output_dir / name in path.parents
            for name in OWNED_ARTIFACTS
        )

    if overlaps_owned(scope_path) or policy_dir == output_dir or output_dir in policy_dir.parents:
        print("scope/policy input conflicts with the output directory", file=sys.stderr)
        return 1
    scope = {"groups": {}}
    invocations = []
    rc = None
    error = None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            scope = load_scope(scope_path)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            _clear_reports(output_dir)
            raise
        if any(
            overlaps_owned(source_path(scope, record))
            for records in scope["groups"].values()
            for record in records
        ):
            print("selected source conflicts with an owned output artifact", file=sys.stderr)
            return 1
        _clear_reports(output_dir)
        if scanner not in SCANNERS:
            raise ValueError(f"unknown scanner: {scanner}")
        _write_json(output_dir / "analysis-scope.json", scope)
        if not policy_dir.is_dir():
            raise FileNotFoundError(f"policy directory missing: {policy_dir}")
        if not _records(scope, scanner):
            raise ValueError("source selection is empty for the selected scanner")
        if scanner in {"pyrefly", "pysa"}:
            _write_json(
                output_dir / "analysis-prerequisites.json",
                {
                    "port": scope["port"],
                    "coverage_status": "incomplete",
                    "typing_coverage_complete": False,
                    "coverage_gaps": ["typing environment not yet validated"],
                },
            )
            rc = _run_types(
                scanner, scope, policy_dir, output_dir, executable, pyrefly_executable, invocations
            )
        elif scanner == "ruff-s":
            rc = _run_ruff(scope, policy_dir, output_dir, executable, invocations)
        else:
            rc = _run_pattern(scanner, scope, policy_dir, output_dir, executable, invocations)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
        rc = getattr(exc, "returncode", None)
        if rc is None and invocations:
            rc = invocations[-1]["exit_code"]
        print(error, file=sys.stderr)
    findings = _findings(output_dir) if output_dir.is_dir() else 0
    groups = _coverage(scope, scanner, invocations)
    summary = {
        "schema_version": 1,
        "scanner": scanner,
        "report_only": True,
        "target": scope.get("target"),
        "variant": scope.get("variant"),
        "port": scope.get("port"),
        "status": "operation_failed"
        if error
        else "diagnostics"
        if scanner in {"pyrefly", "pysa"} and rc == 1
        else "findings"
        if rc == 1 or findings
        else "clean",
        "scanner_exit_code": rc,
        "invocations": invocations,
        "findings_count": findings,
        "source_counts": {group: len(records) for group, records in scope["groups"].items()},
        "source_owners": sorted(
            {record["owner"] for records in scope["groups"].values() for record in records}
        ),
        "groups": groups,
        "coverage_status": "incomplete"
        if scanner in {"pyrefly", "pysa"}
        else "not_assessed"
        if error or any(group["status"] != "assessed" for group in groups.values())
        else "assessed",
    }
    if error:
        summary["operation_error"] = error
    prerequisites = output_dir / "analysis-prerequisites.json"
    if prerequisites.is_file():
        summary["prerequisites"] = json.loads(prerequisites.read_text(encoding="utf-8"))
    try:
        _write_json(output_dir / "analysis-summary.json", summary)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1 if error else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanner", choices=SCANNERS, required=True)
    parser.add_argument(
        "--scope", type=Path, required=True, help="Validated schema-v1 source inventory JSON"
    )
    parser.add_argument("--policy-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("sast-results"))
    parser.add_argument("--executable", help="Selected analyser executable (Pysa: pyre)")
    parser.add_argument(
        "--pyrefly-executable", help="Pysa's explicit Pyrefly prerequisite executable"
    )
    args = parser.parse_args(argv)
    return run(
        args.scanner,
        args.scope,
        args.policy_dir,
        args.output_dir,
        args.executable,
        args.pyrefly_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
