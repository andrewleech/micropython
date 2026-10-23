import json
from pathlib import Path

import pytest

from mpy_analysis import fixtures


def inputs(tmp_path, same_file=False):
    source = tmp_path / "src"
    source.mkdir()
    (source / "positive.py").write_text("value = 1\n")
    (source / "negative.py").write_text("value = 2\n")
    records = [
        {
            "root": "fixtures",
            "path": name,
            "target_path": name,
            "owner": "fixture",
            "provenance": "regression",
        }
        for name in ("positive.py", "negative.py")
    ]
    scope = {
        "schema_version": 1,
        "roots": {"fixtures": str(source)},
        "target": "unix",
        "variant": "standard",
        "port": "unix",
        "groups": {"runtime_frozen_python": records},
    }
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(scope))
    expectations = {
        "schema_version": 1,
        "positive": [{"root": "fixtures", "path": "positive.py", "rule": "fixture"}],
        "negative": [{"root": "fixtures", "path": "positive.py" if same_file else "negative.py"}],
    }
    if same_file:
        expectations["positive"][0]["callable"] = "module.positive"
        expectations["negative"][0]["callable"] = "module.negative"
    expected_path = tmp_path / "expected.json"
    expected_path.write_text(json.dumps(expectations))
    policy = tmp_path / "policy"
    policy.mkdir()
    return scope_path, expected_path, policy, tmp_path / "out", source


def fake_run(
    monkeypatch,
    source,
    *,
    positive=True,
    negative=False,
    coverage="assessed",
    blocked=False,
    pysa=False,
):
    def run(scanner, scope, policy, output, executable, pyrefly):
        output.mkdir(exist_ok=True)
        if pysa:
            report = output / "pysa-results"
            report.mkdir()
            errors = []
            if positive:
                errors.append(
                    {
                        "code": "fixture",
                        "path": str(source / "positive.py"),
                        "define": "module.positive",
                    }
                )
            if negative:
                errors.append(
                    {
                        "code": "fixture",
                        "path": str(source / "positive.py"),
                        "define": "module.negative",
                    }
                )
            (report / "errors.json").write_text(json.dumps(errors))
        else:
            report = output / "fixture.sarif"
            findings = []
            for name, enabled in (("positive.py", positive), ("negative.py", negative)):
                if enabled:
                    findings.append(
                        {
                            "ruleId": "fixture",
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": (source / name).as_uri()}
                                    }
                                }
                            ],
                        }
                    )
            report.write_text(json.dumps({"runs": [{"results": findings}]}))
        summary = {
            "coverage_status": coverage,
            "invocations": [{"report_valid": True, "report": str(report)}],
            "prerequisites": {"compatibility_blockers": ["unsupported tool"] if blocked else []},
        }
        if blocked:
            summary["operation_error"] = "unsupported tool"
        (output / "analysis-summary.json").write_text(json.dumps(summary))
        return 1 if blocked else 0

    monkeypatch.setattr(fixtures, "run", run)


def receipt(output):
    return json.loads((output / "fixture-summary.json").read_text())


@pytest.mark.parametrize("scanner", fixtures.SCANNERS)
def test_shared_dispatch_checks_exact_positive_negative_sources(tmp_path, monkeypatch, scanner):
    scope, expected, policy, output, source = inputs(tmp_path)
    fake_run(monkeypatch, source, pysa=scanner == "pysa")
    assert (
        fixtures.check(scanner, scope, expected, policy, output, "selected", "prerequisite") == 0
    )
    assert receipt(output)["status"] == "passed"
    assert receipt(output)["findings_match"] is True


@pytest.mark.parametrize("positive,negative", [(False, False), (True, True)])
def test_missing_positive_and_unexpected_negative_fail(tmp_path, monkeypatch, positive, negative):
    scope, expected, policy, output, source = inputs(tmp_path)
    fake_run(monkeypatch, source, positive=positive, negative=negative)
    assert fixtures.check("ruff-s", scope, expected, policy, output) == 1
    assert receipt(output)["status"] == "failed"
    assert receipt(output)["failures"]


def test_incomplete_coverage_never_passes_fixture_acceptance(tmp_path, monkeypatch):
    scope, expected, policy, output, source = inputs(tmp_path)
    fake_run(monkeypatch, source, coverage="incomplete")
    assert fixtures.check("pyrefly", scope, expected, policy, output) == 1
    assert receipt(output)["status"] == "incomplete"
    assert receipt(output)["findings_match"] is True


def test_blocked_typing_is_not_a_passing_fixture(tmp_path, monkeypatch):
    scope, expected, policy, output, source = inputs(tmp_path)
    fake_run(monkeypatch, source, coverage="incomplete", blocked=True)
    assert fixtures.check("pysa", scope, expected, policy, output) == 1
    assert receipt(output)["status"] == "blocked"
    assert receipt(output)["findings_match"] is False


def test_pysa_callables_distinguish_same_file_positive_negative(tmp_path, monkeypatch):
    scope, expected, policy, output, source = inputs(tmp_path, same_file=True)
    fake_run(monkeypatch, source, pysa=True)
    assert fixtures.check("pysa", scope, expected, policy, output) == 0
    assert receipt(output)["findings"][0]["callable"] == "module.positive"


def test_pysa_negative_callable_failure_is_detected(tmp_path, monkeypatch):
    scope, expected, policy, output, source = inputs(tmp_path, same_file=True)
    fake_run(monkeypatch, source, pysa=True, negative=True)
    assert fixtures.check("pysa", scope, expected, policy, output) == 1
    assert receipt(output)["failures"][0]["callable"] == "module.negative"


def test_expectation_not_in_selected_scope_fails_without_scanning(tmp_path, monkeypatch):
    scope, expected, policy, output, source = inputs(tmp_path)
    payload = json.loads(expected.read_text())
    payload["positive"][0]["path"] = "unselected.py"
    expected.write_text(json.dumps(payload))
    monkeypatch.setattr(
        fixtures, "run", lambda *args: pytest.fail("must not scan invalid expectations")
    )
    assert fixtures.check("ruff-s", scope, expected, policy, output) == 1
    assert "not a selected source" in receipt(output)["operation_error"]


def test_expectations_owned_output_is_not_deleted(tmp_path):
    scope, expected, policy, output, source = inputs(tmp_path)
    output.mkdir()
    conflicting = output / "analysis-summary.json"
    conflicting.write_text(expected.read_text())
    original = conflicting.read_text()
    assert fixtures.check("ruff-s", scope, conflicting, policy, output) == 1
    assert conflicting.read_text() == original


def test_unrebased_report_location_fails():
    with pytest.raises(ValueError, match="not rebased"):
        fixtures._physical_location({"uri": "stage/positive.py"}, {})


def test_packaged_sources_include_imported_companions():
    directory = fixtures.fixture_directory()
    for name in (
        "imported_positive.py",
        "imported_negative.py",
        "imported_companion.py",
        "typing_positive.py",
        "typing_negative.py",
    ):
        assert (directory / name).is_file()
