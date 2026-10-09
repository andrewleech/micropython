import json

import pytest

from mpy_analysis.common_models import common_model_paths, common_model_provenance
from mpy_analysis.typing_environment import load_typing_environment


def policy(
    tmp_path,
    *,
    legacy=True,
    firmware="1.29.0",
    target_package="micropython-unix-stubs",
    port="unix",
    target="unix",
):
    stubs = tmp_path / "stubs"
    (stubs / "stdlib").mkdir(parents=True)
    (stubs / "micropython.pyi").write_text("def const(value): ...\n")
    (stubs / "stdlib/builtins.pyi").write_text("class object: ...\nclass type: ...\n")
    (stubs / "stdlib/typing.pyi").write_text("Any = object()\n" if legacy else "class Any: ...\n")
    packages = {target_package: "1.29.0.post1", "micropython-stdlib-stubs": "1.29.0.post2"}
    for name, version in packages.items():
        metadata = stubs / f"{name.replace('-', '_')}-{version}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        )
    entry = {
        "port": port,
        "directory": "stubs",
        "typeshed": "stubs",
        "target": target,
        "variant": "standard",
        "python_version": "3.9",
        "python_platform": "linux",
        "pyrefly_version": "1.3.2",
        "packages": packages,
        "target_package": target_package,
        "provenance": {"firmware_version": firmware, "source_revision": "fixture-source"},
        "common_models": ["builtins", "socket"],
    }
    (tmp_path / "typing.json").write_text(json.dumps({"environments": [entry]}))
    return stubs, entry


def load(tmp_path):
    return load_typing_environment(tmp_path, "unix", target="unix", variant="standard")


def test_prerequisite_receipt_retains_bootstrap_and_fallback_blockers(tmp_path):
    policy(tmp_path)
    environment = load(tmp_path)
    receipt = environment.prerequisite_info
    assert receipt["typing_coverage_complete"] is False
    assert any("CPython" in gap for gap in receipt["compatibility_blockers"])
    assert any("Any = object()" in gap for gap in receipt["compatibility_blockers"])
    assert receipt["stub_files"]
    assert all(len(item["sha256"]) == 64 for item in receipt["stub_files"])
    assert [item["family"] for item in receipt["common_model_provenance"]] == [
        "builtins",
        "socket",
    ]
    config = environment.pyrefly_config(tmp_path / "sources")
    assert "typeshed-path = " + json.dumps(str(tmp_path / "stubs")) in config
    assert "skip-interpreter-query = true" in config
    assert "site-package-path = []" in config
    assert 'python-version = "3.9"' in config


def test_declaration_only_probe_does_not_approve_typing(tmp_path):
    policy(tmp_path, legacy=False)
    receipt = load(tmp_path).prerequisite_info
    assert len(receipt["compatibility_blockers"]) == 1
    assert receipt["typing_coverage_complete"] is False


def test_target_version_mismatch_is_retained_as_blocker(tmp_path):
    policy(tmp_path, firmware="1.30.0")
    blockers = load(tmp_path).prerequisite_info["compatibility_blockers"]
    assert any("not declared firmware 1.30.0" in blocker for blocker in blockers)


def test_target_variant_must_match(tmp_path):
    policy(tmp_path)
    with pytest.raises(ValueError, match="variant"):
        load_typing_environment(tmp_path, "unix", target="unix", variant="minimal")


def test_missing_builtin_dependency_fails_without_host_fallback(tmp_path):
    stubs, _ = policy(tmp_path)
    (stubs / "stdlib/builtins.pyi").unlink()
    with pytest.raises(FileNotFoundError, match="builtins.pyi"):
        load(tmp_path)


def test_package_pin_is_enforced(tmp_path):
    _, entry = policy(tmp_path)
    entry["packages"]["micropython-unix-stubs"] = "1.28.0"
    (tmp_path / "typing.json").write_text(json.dumps({"environments": [entry]}))
    with pytest.raises(ValueError, match="expected '1.28.0'"):
        load(tmp_path)


def test_board_specific_target_package_is_explicit(tmp_path):
    package = "micropython-rp2-rpi_pico_w-stubs"
    policy(tmp_path, target_package=package, port="rp2", target="pico-w")
    environment = load_typing_environment(tmp_path, "rp2", target="pico-w", variant="standard")
    assert environment.prerequisite_info["target_package"] == "micropython-rp2-rpi-pico-w-stubs"
    assert package in environment.prerequisite_info["packages"]


def test_target_package_field_is_required(tmp_path):
    _, entry = policy(tmp_path)
    del entry["target_package"]
    (tmp_path / "typing.json").write_text(json.dumps({"environments": [entry]}))
    with pytest.raises(ValueError, match="target_package"):
        load(tmp_path)


def test_selected_target_package_must_have_explicit_pin(tmp_path):
    _, entry = policy(tmp_path)
    del entry["packages"][entry["target_package"]]
    (tmp_path / "typing.json").write_text(json.dumps({"environments": [entry]}))
    with pytest.raises(ValueError, match="explicitly pin both target and stdlib"):
        load(tmp_path)


def test_unknown_release_requires_evidence_not_a_policy_boolean(tmp_path):
    _, entry = policy(tmp_path, legacy=False)
    entry["pyrefly_version"] = "99.0.0"
    entry["typing_coverage_complete"] = True
    (tmp_path / "typing.json").write_text(json.dumps({"environments": [entry]}))
    receipt = load(tmp_path).prerequisite_info
    assert receipt["typing_coverage_complete"] is False
    assert any("99.0.0" in blocker for blocker in receipt["compatibility_blockers"])


def test_non_object_environment_is_rejected(tmp_path):
    (tmp_path / "typing.json").write_text(json.dumps({"environments": ["stubs"]}))
    with pytest.raises(ValueError, match="explicit environment objects"):
        load(tmp_path)


def test_common_models_are_opt_in_and_have_source_provenance():
    assert common_model_paths([]) == []
    assert common_model_paths(["execfile"])[0].name == "execfile"
    assert common_model_provenance(["socket"])[0]["sanitizers"] == []
    with pytest.raises(ValueError, match="selected from"):
        common_model_paths(["unjustified-sanitizer"])
    with pytest.raises(ValueError, match="more than once"):
        common_model_paths(["builtins", "builtins"])


def test_same_port_selects_exact_target_and_variant(tmp_path):
    _, first = policy(tmp_path)
    cli = dict(first, target="linux-x64", variant="cli", python_platform="linux")
    gui = dict(first, target="linux-x64", variant="gui", python_platform="darwin")
    alternate = dict(first, target="linux-arm64", variant="cli", python_platform="linux")
    (tmp_path / "typing.json").write_text(json.dumps({"environments": [cli, gui, alternate]}))
    selected = load_typing_environment(tmp_path, "unix", target="linux-x64", variant="gui")
    assert selected.prerequisite_info["port"] == "unix"
    assert selected.prerequisite_info["target"] == "linux-x64"
    assert selected.prerequisite_info["variant"] == "gui"
    assert selected.python_platform == "darwin"
    assert selected.prerequisite_info["typing_coverage_complete"] is False


def test_other_port_is_not_a_fallback_for_target(tmp_path):
    _, entry = policy(tmp_path)
    (tmp_path / "typing.json").write_text(
        json.dumps({"environments": [dict(entry, port="windows")]})
    )
    with pytest.raises(ValueError, match="no typing environment"):
        load(tmp_path)


def test_duplicate_typing_selection_is_ambiguous(tmp_path):
    _, entry = policy(tmp_path)
    (tmp_path / "typing.json").write_text(json.dumps({"environments": [entry, entry]}))
    with pytest.raises(ValueError, match="duplicate typing environment"):
        load(tmp_path)
