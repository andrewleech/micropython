import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mpy_analysis import runner


def record(path="main.py", root="repo", target="main.py"):
    result = {"root": root, "path": path, "owner": "firmware", "provenance": "fixture"}
    if target is not None:
        result["target_path"] = target
    return result


def inputs(tmp_path, groups=None):
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "main.py").write_text("import companion\n")
    (repo / "companion.py").write_text("def command(value): return value\n")
    (repo / "unit.c").write_text("int main(void) { return 0; }\n")
    scope = {
        "schema_version": 1,
        "roots": {"repo": str(repo)},
        "target": "unix",
        "variant": "standard",
        "port": "unix",
        "groups": groups
        or {"runtime_frozen_python": [record(), record("companion.py", target="companion.py")]},
    }
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(scope))
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "rules.yml").write_text("rules: []\n")
    (policy / "scanners.json").write_text(
        json.dumps({"project": ["rules.yml"], "native-c": ["rules.yml"]})
    )
    (policy / "ruff.toml").write_text('[lint]\nselect = ["S"]\n')
    return scope_path, policy, tmp_path / "out", scope


def scanner(monkeypatch, *, exit_code=0, findings=0, report=True, observed=None):
    def invoke(command, **kwargs):
        if observed is not None:
            observed.append((command, kwargs))
        if "--sarif-output" in command:
            output = Path(command[command.index("--sarif-output") + 1])
        elif "--output-file" in command:
            output = Path(command[command.index("--output-file") + 1])
        else:
            output = Path(command[command.index("--output") + 1].removeprefix("sarif:"))
        sources = [arg for arg in command if arg.endswith((".py", ".c"))]
        if report:
            locations = [
                {
                    "ruleId": "fixture",
                    "message": {"text": sources[0]},
                    "locations": [{"physicalLocation": {"artifactLocation": {"uri": sources[0]}}}],
                }
            ]
            output.write_text(
                json.dumps({"version": "2.1.0", "runs": [{"results": locations * findings}]})
            )
            if "--json-output" in command:
                Path(command[command.index("--json-output") + 1]).write_text(
                    json.dumps({"paths": {"scanned": sources}, "errors": []})
                )
        return subprocess.CompletedProcess(
            command, exit_code, stdout="fixture output\n", stderr=""
        )

    monkeypatch.setattr(runner.subprocess, "run", invoke)


def summary(output):
    return json.loads((output / "analysis-summary.json").read_text())


@pytest.mark.parametrize(
    "name", ["opengrep-stable", "opengrep-interfile-alpha", "semgrep-ce", "ruff-s"]
)
def test_findings_are_report_only_and_source_groups_survive(tmp_path, monkeypatch, name):
    scope, policy, output, _ = inputs(tmp_path)
    scanner(monkeypatch, exit_code=1, findings=1)
    assert runner.run(name, scope, policy, output, "fixture-scanner") == 0
    result = summary(output)
    assert result["status"] == "findings"
    assert result["scanner_exit_code"] == 1
    assert result["findings_count"] == 1
    assert result["groups"]["runtime_frozen_python"]["selected"] == 2
    assert result["groups"]["runtime_frozen_python"]["submitted"] == 2
    assert result["coverage_status"] == "assessed"


def test_zero_exit_with_sarif_findings_is_not_called_clean(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)
    scanner(monkeypatch, findings=1)
    assert runner.run("semgrep-ce", scope, policy, output) == 0
    assert summary(output)["status"] == "findings"


def test_missing_executable_is_operation_failure(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)

    def missing(*args, **kwargs):
        raise FileNotFoundError("missing scanner")

    monkeypatch.setattr(runner.subprocess, "run", missing)
    assert runner.run("ruff-s", scope, policy, output, "absent") == 1
    result = summary(output)
    assert result["status"] == "operation_failed"
    assert result["scanner_exit_code"] is None
    assert result["invocations"][0]["exit_code"] is None


@pytest.mark.parametrize("code, writes", [(0, False), (2, True)])
def test_missing_report_and_operational_exit_fail(tmp_path, monkeypatch, code, writes):
    scope, policy, output, _ = inputs(tmp_path)
    scanner(monkeypatch, exit_code=code, report=writes)
    assert runner.run("ruff-s", scope, policy, output) == 1
    assert summary(output)["scanner_exit_code"] == code
    assert summary(output)["status"] == "operation_failed"


def test_partial_report_is_rebased_on_tool_failure(tmp_path, monkeypatch):
    scope_path, policy, output, scope = inputs(tmp_path)
    scanner(monkeypatch, exit_code=2, findings=1)
    assert runner.run("opengrep-interfile-alpha", scope_path, policy, output) == 1
    report = json.loads((output / "opengrep-interfile-alpha-project.sarif").read_text())
    finding = report["runs"][0]["results"][0]
    uri = finding["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == (Path(scope["roots"]["repo"]) / Path(finding["message"]["text"]).name).as_uri()
    assert "mpy-analysis-" in finding["message"]["text"]
    assert summary(output)["coverage_status"] == "not_assessed"


def test_stale_owned_reports_removed_unrelated_reports_preserved(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)
    output.mkdir()
    (output / "pyrefly.sarif").write_text("stale")
    (output / "keep.sarif").write_text(
        json.dumps({"runs": [{"results": [{"ruleId": "unrelated"}]}]})
    )
    (output / "pysa-results").mkdir()
    (output / "pysa-results" / "stale.json").write_text("{}")
    scanner(monkeypatch)
    assert runner.run("ruff-s", scope, policy, output) == 0
    assert not (output / "pyrefly.sarif").exists()
    assert not (output / "pysa-results").exists()
    assert (output / "keep.sarif").exists()
    assert summary(output)["findings_count"] == 0


def test_cleanup_does_not_follow_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    preserved = outside / "preserve.txt"
    preserved.write_text("user file")
    output = tmp_path / "out"
    output.mkdir()
    (output / "pysa-results").symlink_to(outside, target_is_directory=True)
    runner._clear_reports(output)
    assert preserved.read_text() == "user file"
    assert not (output / "pysa-results").exists()


def test_failed_scope_clears_stale_reports_and_emits_summary(tmp_path):
    scope, policy, output, _ = inputs(tmp_path)
    scope.write_text("invalid json")
    output.mkdir()
    (output / "ruff-s.sarif").write_text("stale")
    assert runner.run("ruff-s", scope, policy, output) == 1
    assert not (output / "ruff-s.sarif").exists()
    assert summary(output)["status"] == "operation_failed"


def test_empty_selection_fails(tmp_path):
    scope_path, policy, output, scope = inputs(tmp_path)
    scope["groups"] = {"runtime_frozen_python": []}
    scope_path.write_text(json.dumps(scope))
    assert runner.run("ruff-s", scope_path, policy, output) == 1
    assert "empty" in summary(output)["operation_error"]


def test_unknown_groups_are_unassessed_not_silently_removed(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(
        tmp_path,
        {
            "runtime_frozen_python": [record()],
            "custom-language": [record("unit.c", target=None)],
        },
    )
    scanner(monkeypatch)
    assert runner.run("ruff-s", scope, policy, output) == 0
    result = summary(output)
    assert result["coverage_status"] == "not_assessed"
    assert result["groups"]["custom-language"]["unassessed"] == ["repo/unit.c"]
    assert result["source_counts"]["custom-language"] == 1


def test_external_roots_and_multiple_import_identities_stage_without_loss(tmp_path, monkeypatch):
    scope_path, policy, output, scope = inputs(tmp_path)
    external = tmp_path / "library"
    external.mkdir()
    (external / "module.py").write_text("value = 1\n")
    scope["roots"]["library"] = str(external)
    scope["groups"]["runtime_frozen_python"] += [
        record("module.py", "library", "one.py"),
        record("module.py", "library", "two.py"),
    ]
    scope_path.write_text(json.dumps(scope))
    observed = []
    scanner(monkeypatch, observed=observed)
    assert runner.run("opengrep-interfile-alpha", scope_path, policy, output) == 0
    command = observed[0][0]
    assert any(arg.endswith("/one.py") for arg in command)
    assert any(arg.endswith("/two.py") for arg in command)
    assert summary(output)["source_counts"]["runtime_frozen_python"] == 4


@pytest.mark.parametrize(
    "target", ["../escape.py", "/escape.py", "C:/escape.py", "a\\b.py", "a/./b.py", "a//b.py"]
)
def test_staging_rejects_escaping_or_noncanonical_paths(tmp_path, target):
    _, _, _, scope = inputs(tmp_path)
    with pytest.raises(ValueError):
        runner._stage_sources(
            scope, [("runtime_frozen_python", record(target=target))], tmp_path / "stage"
        )
    assert not (tmp_path / "stage").exists()


@pytest.mark.parametrize("targets", [("same.py", "same.py"), ("pkg", "pkg/module.py")])
def test_staging_rejects_collisions_before_copy(tmp_path, targets):
    _, _, _, scope = inputs(tmp_path)
    records = [
        ("runtime_frozen_python", record(target=targets[0])),
        ("runtime_frozen_python", record("companion.py", target=targets[1])),
    ]
    with pytest.raises(ValueError, match="collision"):
        runner._stage_sources(scope, records, tmp_path / "stage")
    assert not (tmp_path / "stage").exists()


def test_rebasing_changes_only_exact_path_fields_in_json_lines(tmp_path):
    work = tmp_path / "work"
    root = work / "src"
    root.mkdir(parents=True)
    staged = root / "module.py"
    source = tmp_path / "source.py"
    report = tmp_path / "result.json"
    report.write_text(
        json.dumps(
            {"filename": "src/module.py", "message": str(staged), "path": "prefix" + str(staged)}
        )
        + "\n"
        + json.dumps({"filename": staged.as_uri()})
        + "\n"
    )
    runner._rebase_report_paths(report, {str(staged): str(source)}, work, root, cwd=work)
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    assert rows[0]["filename"] == str(source)
    assert rows[0]["message"] == str(staged)
    assert rows[0]["path"] == "prefix" + str(staged)
    assert rows[1]["filename"] == str(source)


def test_sarif_uri_base_and_percent_encoded_external_paths(tmp_path):
    work = tmp_path / "work"
    root = work / "src"
    root.mkdir(parents=True)
    staged = root / "a b.py"
    source = tmp_path / "external source.py"
    report = tmp_path / "report.sarif"
    report.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "originalUriBaseIds": {"ROOT": {"uri": root.as_uri() + "/"}},
                        "artifacts": [{"location": {"uri": "a%20b.py", "uriBaseId": "ROOT"}}],
                    }
                ]
            }
        )
    )
    runner._rebase_report_paths(report, {str(staged): str(source)}, work, root)
    location = json.loads(report.read_text())["runs"][0]["artifacts"][0]["location"]
    assert location == {"uri": source.as_uri()}


def typing_environment(tmp_path, *, blockers=()):
    return SimpleNamespace(
        stub_directory=tmp_path / "stubs",
        typeshed_directory=tmp_path / "typeshed",
        prerequisite_info={
            "expected_pyrefly_version": "1.3.2",
            "compatibility_blockers": list(blockers),
            "typing_coverage_complete": False,
            "coverage_gaps": ["unverified builtins"],
        },
        pyrefly_config=lambda root: 'project-includes = ["src/**/*.py"]\n',
    )


@pytest.mark.parametrize("name", ["pyrefly", "pysa"])
def test_typing_blockers_persist_receipt_and_refuse_analysis(tmp_path, monkeypatch, name):
    scope, policy, output, _ = inputs(tmp_path)
    environment = typing_environment(tmp_path, blockers=["unsafe CPython fallback"])
    monkeypatch.setattr(runner, "load_typing_environment", lambda *args, **kwargs: environment)
    commands = []

    def version(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "pyrefly 1.3.2\n", "")

    monkeypatch.setattr(runner.subprocess, "run", version)
    assert runner.run(name, scope, policy, output) == 1
    result = summary(output)
    assert result["coverage_status"] == "incomplete"
    assert result["prerequisites"]["version_check"]["actual_version"] == "1.3.2"
    assert result["prerequisites"]["compatibility_blockers"] == ["unsafe CPython fallback"]
    assert len(commands) == 1 and commands[0][-1] == "--version"


def test_typing_version_mismatch_is_failure_with_receipt(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)
    monkeypatch.setattr(
        runner, "load_typing_environment", lambda *args, **kwargs: typing_environment(tmp_path)
    )
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "pyrefly 9.0.0", ""),
    )
    assert runner.run("pyrefly", scope, policy, output) == 1
    result = summary(output)
    assert "version mismatch" in result["operation_error"]
    assert result["prerequisites"]["version_check"]["actual_version"] == "9.0.0"


def test_missing_typing_environment_does_not_run_host_checker(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)

    def missing(*args, **kwargs):
        raise FileNotFoundError("custom typeshed missing")

    monkeypatch.setattr(runner, "load_typing_environment", missing)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not invoke checker")
    )
    assert runner.run("pyrefly", scope, policy, output) == 1
    assert summary(output)["coverage_status"] == "incomplete"
    assert not summary(output)["invocations"]


def test_scope_input_owned_artifact_is_never_overwritten(tmp_path):
    scope_path, policy, output, scope = inputs(tmp_path)
    output.mkdir()
    conflicting = output / "analysis-summary.json"
    conflicting.write_text(json.dumps(scope))
    original = conflicting.read_text()
    assert runner.run("ruff-s", conflicting, policy, output) == 1
    assert conflicting.read_text() == original


def test_cli_dispatches_explicit_paths(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(runner, "run", lambda *args: observed.append(args) or 0)
    assert (
        runner.main(
            [
                "--scanner",
                "pysa",
                "--scope",
                "inventory.json",
                "--policy-dir",
                "policy",
                "--output-dir",
                "reports",
                "--executable",
                "pyre-bin",
                "--pyrefly-executable",
                "pyrefly-bin",
            ]
        )
        == 0
    )
    assert observed == [
        (
            "pysa",
            Path("inventory.json"),
            Path("policy"),
            Path("reports"),
            "pyre-bin",
            "pyrefly-bin",
        )
    ]


@pytest.mark.parametrize("name", ["pyrefly", "pysa"])
def test_typing_reports_diagnostics_without_claiming_builtin_coverage(tmp_path, monkeypatch, name):
    scope_path, policy, output, scope = inputs(tmp_path)
    models = policy / "pysa"
    models.mkdir()
    (models / "taint.config").write_text("{}")
    (models / "models.pysa").write_text("# fixture\n")
    monkeypatch.setattr(
        runner, "load_typing_environment", lambda *args, **kwargs: typing_environment(tmp_path)
    )
    observed = []

    def invoke(command, **kwargs):
        observed.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "pyrefly 1.3.2\n", "")
        if "--save-results-to" in command:
            directory = Path(command[command.index("--save-results-to") + 1])
            directory.mkdir()
            staged = observed[1][-1]
            (directory / "errors.json").write_text(json.dumps([{"path": staged, "code": 5001}]))
        else:
            report = Path(command[command.index("--output") + 1].removeprefix("sarif:"))
            report.write_text(json.dumps({"version": "2.1.0", "runs": [{"results": []}]}))
            Path(command[command.index("--report-pysa") + 1]).write_text("{}")
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run(name, scope_path, policy, output, "selected", "prerequisite") == 0
    result = summary(output)
    assert result["status"] == "diagnostics"
    assert result["scanner_exit_code"] == 1
    assert result["coverage_status"] == "incomplete"
    assert result["prerequisites"]["type_diagnostics_present"] is True
    assert "--typeshed-path" in observed[1]
    if name == "pysa":
        assert observed[0][0] == "prerequisite"
        assert observed[2][0] == "selected"
        errors = json.loads((output / "pysa-results" / "errors.json").read_text())
        assert Path(errors[0]["path"]).parent == Path(scope["roots"]["repo"])


def test_first_group_report_survives_second_group_failure(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(
        tmp_path,
        {
            "runtime_frozen_python": [record()],
            "native": [record("unit.c", target=None)],
        },
    )
    observed = []

    def invoke(command, **kwargs):
        observed.append(command)
        report = Path(command[command.index("--sarif-output") + 1])
        report.write_text(json.dumps({"version": "2.1.0", "runs": [{"results": []}]}))
        Path(command[command.index("--json-output") + 1]).write_text(
            json.dumps(
                {
                    "paths": {"scanned": [arg for arg in command if arg.endswith((".py", ".c"))]},
                    "errors": [],
                }
            )
        )
        return subprocess.CompletedProcess(command, 0 if len(observed) == 1 else 2, "", "")

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run("opengrep-stable", scope, policy, output) == 1
    result = summary(output)
    assert result["scanner_exit_code"] == 2
    assert [invocation["exit_code"] for invocation in result["invocations"]] == [0, 2]
    assert result["groups"]["runtime_frozen_python"]["status"] == "assessed"
    assert result["groups"]["native"]["status"] == "not_assessed"
    assert result["groups"]["native"]["submitted"] == 1
    assert (output / "opengrep-stable-project.sarif").is_file()


def test_staging_rejects_symlink_parent_escape_before_any_copy(tmp_path):
    _, _, _, scope = inputs(tmp_path)
    destination = tmp_path / "stage"
    destination.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (destination / "escape").symlink_to(outside, target_is_directory=True)
    records = [
        ("runtime_frozen_python", record()),
        ("runtime_frozen_python", record("companion.py", target="escape/companion.py")),
    ]
    with pytest.raises(ValueError, match="escapes"):
        runner._stage_sources(scope, records, destination)
    assert not (destination / "main.py").exists()
    assert not (outside / "companion.py").exists()


def test_missing_external_policy_is_failure(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)
    (policy / "rules.yml").unlink()
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("must not run without policy"),
    )
    assert runner.run("opengrep-stable", scope, policy, output) == 1
    assert "policy missing" in summary(output)["operation_error"]


def test_pysa_directory_without_errors_report_is_failure(tmp_path):
    results = tmp_path / "pysa-results"
    results.mkdir()
    (results / "metadata.json").write_text("{}")
    with pytest.raises(runner.AnalysisToolError, match="errors.json"):
        runner._validate_report(results, "pysa")


def test_interfile_stages_native_companions_but_does_not_claim_header_coverage(
    tmp_path, monkeypatch
):
    scope_path, policy, output, scope = inputs(
        tmp_path,
        {
            "runtime_frozen_python": [record()],
            "native": [record("unit.c", target=None), record("unit.h", target=None)],
        },
    )
    (Path(scope["roots"]["repo"]) / "unit.h").write_text("#define VALUE 1\n")
    seen = []

    def invoke(command, **kwargs):
        root = Path(kwargs["cwd"])
        assert (root / "_roots" / "repo" / "unit.h").is_file()
        seen.append(command)
        Path(command[command.index("--sarif-output") + 1]).write_text(
            json.dumps({"version": "2.1.0", "runs": [{"results": []}]})
        )
        Path(command[command.index("--json-output") + 1]).write_text(
            json.dumps(
                {
                    "paths": {"scanned": [arg for arg in command if arg.endswith((".py", ".c"))]},
                    "errors": [],
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run("opengrep-interfile-alpha", scope_path, policy, output) == 0
    assert len(seen) == 2
    assert summary(output)["groups"]["native"]["unassessed"] == ["repo/unit.h"]
    assert summary(output)["groups"]["native"]["submitted"] == 1


def test_cross_group_staging_collision_is_not_overwritten(tmp_path):
    _, _, _, scope = inputs(tmp_path)
    records = [
        ("runtime_frozen_python", record(target="_roots/repo/companion.py")),
        ("host_and_example_python", record("companion.py", target=None)),
    ]
    with pytest.raises(ValueError, match="collision"):
        runner._stage_sources(scope, records, tmp_path / "stage")
    assert not (tmp_path / "stage").exists()


def test_selected_source_owned_output_is_not_deleted(tmp_path):
    scope_path, policy, output, scope = inputs(tmp_path)
    output.mkdir()
    source = output / "ruff-s.sarif"
    source.write_text("user source")
    scope["roots"]["reports"] = str(output)
    scope["groups"] = {"custom": [record("ruff-s.sarif", "reports", target=None)]}
    scope_path.write_text(json.dumps(scope))
    assert runner.run("ruff-s", scope_path, policy, output) == 1
    assert source.read_text() == "user source"


def test_valid_nested_src_targets_have_unambiguous_cwd_aliases(tmp_path):
    work = tmp_path / "work"
    root = work / "src"
    root.mkdir(parents=True)
    replacements = {
        str(root / "x.py"): str(tmp_path / "first.py"),
        str(root / "src" / "x.py"): str(tmp_path / "second.py"),
    }
    for cwd, paths in ((root, ["x.py", "src/x.py"]), (work, ["src/x.py", "src/src/x.py"])):
        report = tmp_path / "report.json"
        report.write_text(json.dumps([{"path": path} for path in paths]))
        runner._rebase_report_paths(report, replacements, work, root, cwd=cwd)
        assert [row["path"] for row in json.loads(report.read_text())] == [
            str(tmp_path / "first.py"),
            str(tmp_path / "second.py"),
        ]


def test_file_uri_conversion_preserves_platform_source_location(tmp_path):
    work = tmp_path / "work"
    root = work / "src"
    root.mkdir(parents=True)
    staged = root / "space name.py"
    source = tmp_path / "actual source.py"
    report = tmp_path / "report.json"
    uri = staged.as_uri().replace("file:///", "file://localhost/", 1)
    report.write_text(json.dumps({"uri": uri}))
    runner._rebase_report_paths(report, {str(staged): str(source)}, work, root)
    assert json.loads(report.read_text())["uri"] == source.as_uri()


def test_rebase_failure_does_not_override_original_scanner_exit(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)
    scanner(monkeypatch, exit_code=2, findings=1)

    def broken(*args, **kwargs):
        raise ValueError("rebase failure")

    monkeypatch.setattr(runner, "_rebase_report_paths", broken)
    assert runner.run("opengrep-interfile-alpha", scope, policy, output) == 1
    result = summary(output)
    assert result["scanner_exit_code"] == 2
    assert "exit 2" in result["operation_error"]
    assert result["invocations"][0]["report_rebase_error"] == "rebase failure"


@pytest.mark.parametrize(
    "notification",
    [
        {"level": "warning", "message": {"text": "unable to parse selected input"}},
        {
            "level": "note",
            "descriptor": {"id": "skipped-input"},
            "message": {"text": "large file"},
        },
    ],
)
def test_reported_execution_gap_preserves_findings_but_not_coverage(
    tmp_path, monkeypatch, notification
):
    scope, policy, output, _ = inputs(tmp_path)

    def invoke(command, **kwargs):
        report = Path(command[command.index("--output-file") + 1])
        report.write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "results": [],
                            "invocations": [
                                {
                                    "executionSuccessful": True,
                                    "toolExecutionNotifications": [notification],
                                }
                            ],
                        }
                    ]
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run("ruff-s", scope, policy, output) == 0
    result = summary(output)
    assert result["coverage_status"] == "not_assessed"
    assert result["invocations"][0]["coverage_gaps"]
    assert result["groups"]["runtime_frozen_python"]["submitted"] == 2


def test_pattern_json_skipped_file_cannot_be_called_assessed(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)

    def invoke(command, **kwargs):
        Path(command[command.index("--sarif-output") + 1]).write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "results": [],
                            "invocations": [
                                {"executionSuccessful": True, "toolExecutionNotifications": []}
                            ],
                        }
                    ],
                }
            )
        )
        Path(command[command.index("--json-output") + 1]).write_text(
            json.dumps(
                {
                    "paths": {
                        "scanned": [],
                        "skipped": [{"path": command[-1], "reason": "too large"}],
                    },
                    "errors": [],
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run("semgrep-ce", scope, policy, output) == 0
    result = summary(output)
    assert result["coverage_status"] == "not_assessed"
    evidence = result["invocations"][0]["coverage_evidence"]
    assert len(evidence["missing_selected"]) == 2
    assert evidence["skipped"]


def test_nonobject_pattern_policy_is_reported_failure(tmp_path):
    scope, policy, output, _ = inputs(tmp_path)
    (policy / "scanners.json").write_text("[]")
    assert runner.run("semgrep-ce", scope, policy, output) == 1
    assert "must be an object" in summary(output)["operation_error"]


def test_missing_pattern_coverage_sidecar_is_operational_failure(tmp_path, monkeypatch):
    scope, policy, output, _ = inputs(tmp_path)

    def invoke(command, **kwargs):
        Path(command[command.index("--sarif-output") + 1]).write_text(
            json.dumps({"runs": [{"results": []}]})
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run("semgrep-ce", scope, policy, output) == 1
    result = summary(output)
    assert result["scanner_exit_code"] == 0
    assert result["coverage_status"] == "not_assessed"
    assert "did not produce its report" in result["operation_error"]


def test_external_uri_base_is_not_mistaken_for_staged_relative_path(tmp_path):
    work = tmp_path / "work"
    root = work / "src"
    root.mkdir(parents=True)
    staged = root / "main.py"
    source = tmp_path / "source.py"
    report = tmp_path / "report.json"
    location = {"uri": "main.py", "uriBaseId": "OTHER"}
    report.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "originalUriBaseIds": {
                            "OTHER": {"uri": (tmp_path / "unrelated").as_uri() + "/"}
                        },
                        "artifacts": [{"location": location}],
                    }
                ]
            }
        )
    )
    runner._rebase_report_paths(report, {str(staged): str(source)}, work, root)
    assert json.loads(report.read_text())["runs"][0]["artifacts"][0]["location"] == location
