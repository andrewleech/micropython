import json
from pathlib import Path

import pytest
from mpy_analysis import common_models
from mpy_analysis.common_models import (
    common_model_paths,
    resolve_model_policy,
)

# Expected provided kinds are nominated independently of packaged provenance.
PROVIDED_KINDS = [
    ("input", "sources", "UserControlled"),
    ("eval_exec", "sinks", "RemoteCodeExecution"),
    ("socket", "sources", "UserControlled"),
    ("execfile", "sinks", "RemoteCodeExecution"),
]


def configuration():
    return {
        "sources": [{"name": "UserControlled"}, {"name": "AppSource"}],
        "sinks": [{"name": "RemoteCodeExecution"}, {"name": "AppSink"}],
        "rules": [
            {
                "name": "Application rule",
                "code": 3101,
                "sources": ["UserControlled", "AppSource"],
                "sinks": ["RemoteCodeExecution", "AppSink"],
                "message_format": "Application flow",
            }
        ],
    }


def write_config(directory, config, name="taint.config"):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path.resolve()


@pytest.mark.parametrize("family,side,kind", PROVIDED_KINDS)
@pytest.mark.parametrize("omission", ["declaration", "ordinary participation"])
def test_every_provided_kind_requires_declaration_and_matching_rule_side(
    tmp_path, family, side, kind, omission
):
    config = configuration()
    if omission == "declaration":
        config[side] = [item for item in config[side] if item["name"] != kind]
    else:
        config["rules"][0][side].remove(kind)
    write_config(tmp_path / "pysa", config)
    with pytest.raises(ValueError, match=kind):
        resolve_model_policy(tmp_path, [family])


@pytest.mark.parametrize("family,side,kind", PROVIDED_KINDS)
def test_matching_side_is_required_even_if_kind_occurs_on_other_side(
    tmp_path, family, side, kind
):
    config = configuration()
    config["rules"][0][side].remove(kind)
    opposite = "sinks" if side == "sources" else "sources"
    config[opposite].append({"name": kind})
    config["rules"][0][opposite].append(kind)
    write_config(tmp_path / "pysa", config)
    with pytest.raises(ValueError, match=rf"{kind}.*rules\.{side}"):
        resolve_model_policy(tmp_path, [family])


@pytest.mark.parametrize(
    "families,message",
    [
        (["builtins"], "builtins"),
        (["unknown"], "unknown"),
        (["input", "input"], "more than once"),
        (["eval_exec", "eval_exec"], "more than once"),
        ("input", "collection"),
        (b"input", "collection"),
        (None, "collection"),
        ([None], "selected from"),
        ([[]], "selected from"),
    ],
)
def test_invalid_family_selection_rejected_by_real_resolver(tmp_path, families, message):
    write_config(tmp_path / "pysa", configuration())
    with pytest.raises(ValueError, match=message):
        resolve_model_policy(tmp_path, families)


def test_shared_only_models_accept_application_config_without_application_models(tmp_path):
    app = tmp_path / "pysa"
    config_path = write_config(app, configuration())
    resolved = resolve_model_policy(tmp_path, iter(["socket", "input", "eval_exec"]))
    assert resolved["model_paths"][0] == app.resolve()
    assert [path.name for path in resolved["model_paths"][1:]] == [
        "socket", "input", "eval_exec"
    ]
    assert resolved["config_paths"] == [config_path]
    assert all(isinstance(path, Path) for path in resolved["model_paths"])
    assert all(isinstance(path, Path) for path in resolved["config_paths"])
    assert [receipt["family"] for receipt in resolved["common_model_provenance"]] == [
        "socket",
        "input",
        "eval_exec",
    ]


def test_execution_only_models_accept_explicit_application_ingress(tmp_path):
    config = {
        "sources": [{"name": "AppSource"}],
        "sinks": [{"name": "RemoteCodeExecution"}],
        "rules": [{"sources": ["AppSource"], "sinks": ["RemoteCodeExecution"]}],
    }
    app = tmp_path / "pysa"
    write_config(app, config)
    (app / "ingress.pysa").write_text("def app.ingress() -> TaintSource[AppSource]: ...\n")
    resolved = resolve_model_policy(tmp_path, ["eval_exec"])
    assert resolved["model_paths"] == [
        app.resolve(), common_model_paths(["eval_exec"])[0].resolve()
    ]
    receipt = resolved["common_model_provenance"][0]
    assert receipt["taint_kinds"] == {"sources": [], "sinks": ["RemoteCodeExecution"]}
    assert receipt["required_capabilities"] == ["MICROPY_PY_BUILTINS_EVAL_EXEC"]


def test_input_only_selection_requires_no_execution_kind_or_capability(tmp_path):
    write_config(
        tmp_path / "pysa",
        {
            "sources": [{"name": "UserControlled"}],
            "sinks": [{"name": "AppSink"}],
            "rules": [{"sources": ["UserControlled"], "sinks": ["AppSink"]}],
        },
    )
    resolved = resolve_model_policy(tmp_path, ["input"])
    receipt = resolved["common_model_provenance"][0]
    assert receipt["taint_kinds"] == {"sources": ["UserControlled"], "sinks": []}
    assert receipt["required_capabilities"] == ["MICROPY_PY_BUILTINS_INPUT"]


def test_application_kinds_extensions_and_unknown_engine_settings_are_allowed(tmp_path):
    config = configuration()
    config["sources"].append({"name": "AppParametricSource", "kind": "parametric"})
    config["rules"][0]["filters"] = {"maximum_source_distance": 4}
    config["engine_owned_setting"] = {"opaque": [True, None, {"value": 3}]}
    app = tmp_path / "pysa"
    path = write_config(app, config)
    (app / "extensions.pysa").write_text("def app.source() -> TaintSource[AppSource]: ...\n")
    resolved = resolve_model_policy(tmp_path, ["input", "eval_exec"])
    assert resolved["config_paths"] == [path]
    assert resolved["model_paths"][0] == app.resolve()


def test_no_selected_families_accepts_application_owned_models_and_config(tmp_path):
    app = tmp_path / "pysa"
    path = write_config(app, {})
    (app / "models.pysa").write_text("def app.source() -> TaintSource[AppSource]: ...\n")
    resolved = resolve_model_policy(tmp_path, [])
    assert resolved == {
        "model_paths": [app.resolve()],
        "config_paths": [path],
        "common_model_provenance": [],
    }


def test_missing_policy_directory_is_a_resource_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="policy directory"):
        resolve_model_policy(tmp_path / "missing", ["input"])


@pytest.mark.parametrize("families", [[], ["input"]])
def test_total_config_absence_is_a_resource_error(tmp_path, families):
    app = tmp_path / "pysa"
    app.mkdir()
    (app / "models.pysa").write_text("# application models\n")
    with pytest.raises(FileNotFoundError, match="configuration"):
        resolve_model_policy(tmp_path, families)


def test_total_model_absence_is_a_resource_error(tmp_path):
    write_config(tmp_path / "pysa", configuration())
    with pytest.raises(FileNotFoundError, match="taint models"):
        resolve_model_policy(tmp_path, [])


def test_application_model_path_must_be_a_directory(tmp_path):
    (tmp_path / "pysa").write_text("not a directory")
    with pytest.raises(ValueError, match="must be a directory"):
        resolve_model_policy(tmp_path, ["input"])


def test_selected_packaged_model_resource_must_exist(tmp_path, monkeypatch):
    model_root = tmp_path / "packaged"
    (model_root / "input").mkdir(parents=True)
    monkeypatch.setattr(common_models, "_MODEL_ROOT", model_root)
    with pytest.raises(FileNotFoundError, match="input"):
        resolve_model_policy(tmp_path, ["input"])


def test_selected_packaged_provenance_resource_must_exist(tmp_path, monkeypatch):
    model_root = tmp_path / "packaged"
    directory = model_root / "input"
    directory.mkdir(parents=True)
    (directory / "models.pysa").write_text("# fixture model\n")
    monkeypatch.setattr(common_models, "_MODEL_ROOT", model_root)
    with pytest.raises(FileNotFoundError, match="provenance.json"):
        resolve_model_policy(tmp_path, ["input"])


def test_multiple_nested_config_inputs_combine_declarations_and_rules(tmp_path):
    app = tmp_path / "pysa"
    config = configuration()
    rules = config.pop("rules")
    declarations = write_config(app, config, "z-declarations.config")
    rule_path = write_config(app, {"rules": rules}, "nested/a-rules.config")
    extra_path = write_config(app, {"features": [{"name": "AppFeature"}]}, "a.config")
    (app / "nested/models.pysa").write_text("# application models\n")
    resolved = resolve_model_policy(tmp_path, ["input", "eval_exec"])
    assert resolved["config_paths"] == [extra_path, rule_path, declarations]


def test_config_paths_are_deduplicated_by_real_path(tmp_path):
    app = tmp_path / "pysa"
    path = write_config(app, configuration(), "a.config")
    (app / "b.config").symlink_to(path)
    resolved = resolve_model_policy(tmp_path, ["input"])
    assert resolved["config_paths"] == [path]


@pytest.mark.parametrize(
    "config,message",
    [
        ([], "must be an object"),
        ({"sources": {}}, "sources declarations must be a list"),
        ({"sinks": None}, "sinks declarations must be a list"),
        ({"sources": ["UserControlled"]}, "sources declarations must be objects"),
        ({"sinks": [{}]}, "nonempty name"),
        ({"sources": [{"name": 1}]}, "nonempty name"),
        ({"sources": [{"name": " "}]}, "nonempty name"),
        ({"rules": {}}, "rules must be a list"),
        ({"rules": [False]}, "rules must be objects"),
        ({"rules": [{}]}, "rules.sources"),
        ({"rules": [{"sources": "UserControlled", "sinks": []}]}, "rules.sources"),
        ({"rules": [{"sources": [None], "sinks": []}]}, "rules.sources"),
        ({"rules": [{"sources": [], "sinks": [""]}]}, "rules.sinks"),
    ],
)
def test_invalid_structures_used_for_kind_validation_are_rejected(tmp_path, config, message):
    write_config(tmp_path / "pysa", config)
    with pytest.raises(ValueError, match=message):
        resolve_model_policy(tmp_path, ["input"])


def test_model_fingerprint_tracks_byte_changes_not_provenance_or_newline_normalisation(
    tmp_path, monkeypatch
):
    model_root = tmp_path / "packaged"
    family = model_root / "input"
    family.mkdir(parents=True)
    (family / "provenance.json").write_text(
        json.dumps(
            {"family": "input", "taint_kinds": {"sources": ["UserControlled"], "sinks": []}}
        )
    )
    model = family / "models.pysa"
    payload = (
        b"# fixture\r\n"
        b"def builtins.input(prompt=..., /) -> TaintSource[UserControlled]: ...\r\n"
    )
    model.write_bytes(payload)
    monkeypatch.setattr(common_models, "_MODEL_ROOT", model_root)
    policy = tmp_path / "policy"
    write_config(policy / "pysa", configuration())
    original = resolve_model_policy(policy, ["input"])["common_model_provenance"][0]
    assert original["model_files"][0]["sha256"] == (
        "0fe9e691c73cae22c2354d0bd54c1357a3c84048b7d42231780393aaad87c503"
    )
    model.write_bytes(payload.replace(b"\r\n", b"\n"))
    changed = resolve_model_policy(policy, ["input"])["common_model_provenance"][0]
    assert changed["model_files"][0]["sha256"] == (
        "ad16a69ea5826479d2f6758a0d7642ac6bf9f6ad7549be6986d6fd1bed27ba15"
    )


def test_invalid_json_is_a_configuration_error(tmp_path):
    app = tmp_path / "pysa"
    app.mkdir()
    (app / "taint.config").write_text("{")
    with pytest.raises(ValueError):
        resolve_model_policy(tmp_path, ["input"])


@pytest.mark.parametrize("form", ["string_combine_rules", "combined_source_rules"])
def test_needed_nonordinary_rule_participation_names_the_unsupported_form(tmp_path, form):
    config = configuration()
    config["rules"][0]["sources"].remove("UserControlled")
    if form == "string_combine_rules":
        config[form] = [
            {
                "name": "Source combination",
                "code": 5051,
                "rule": [
                    {"sources": ["UserControlled"], "partial_sink": "Combination[input]"},
                    {"sources": ["AppSource"], "partial_sink": "Combination[app]"},
                ],
            }
        ]
    else:
        config[form] = [
            {
                "name": "Source combination",
                "code": 5051,
                "sources": {"input": ["UserControlled"], "app": ["AppSource"]},
                "partial_sink": "Combination",
            }
        ]
    write_config(tmp_path / "pysa", config)
    with pytest.raises(ValueError, match=rf"UserControlled.*{form}.*unsupported"):
        resolve_model_policy(tmp_path, ["input"])


@pytest.mark.parametrize("form", ["string_combine_rules", "combined_source_rules"])
def test_ordinary_participation_allows_engine_owned_additional_rule_forms(tmp_path, form):
    config = configuration()
    config[form] = [{"engine_owned": {"opaque": True}}]
    path = write_config(tmp_path / "pysa", config)
    resolved = resolve_model_policy(tmp_path, ["input", "eval_exec"])
    assert resolved["config_paths"] == [path]
