"""Submodule provenance of selected checkouts, through real Git repositories and the SBOM CLI path."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from mpy_analysis import sbom
from mpy_analysis.sbom import FIRMWARE_BLOCKING_GAPS
from mpy_analysis.scope import FROZEN_GROUP
from mpy_analysis.submodules import SubmoduleInspector, url_relation

MPY_ROOT = Path(__file__).resolve().parents[3]
IDENTITY = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
FILE_TRANSPORT = ["-c", "protocol.file.allow=always"]


def git(repo, *args):
    return subprocess.run(
        ["git", *IDENTITY, *FILE_TRANSPORT, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_repository(path, files):
    path.mkdir(parents=True)
    git(path, "init", "--quiet")
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "Initial")
    return path


def commit_file(repo, name, content):
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "--quiet", "-m", f"Change {name}")
    return git(repo, "rev-parse", "HEAD")


def add_submodule(superproject, upstream, path):
    git(superproject, "submodule", "add", "--quiet", str(upstream), path)
    git(superproject, "commit", "--quiet", "-m", f"Add {path}")


def scope(roots, rows):
    """A selected-source scope; rows are (root name, relative path) frozen sources."""
    return {
        "schema_version": 1,
        "target": "fixture",
        "variant": "test",
        "port": "unix",
        "roots": {name: str(path) for name, path in roots.items()},
        "groups": {
            FROZEN_GROUP: [
                {
                    "root": root,
                    "path": path,
                    "target_path": f"{root}_{path.replace('/', '_')}",
                    "owner": "external",
                    "provenance": {"kind": "fixture"},
                }
                for root, path in rows
            ],
            "native": [],
        },
        "packages": [],
        "package_requirements": [],
    }


@pytest.fixture
def workspace(tmp_path):
    """An application repository with an initialised SDK submodule and an unused one."""
    sdk = make_repository(tmp_path / "upstream/sdk", {"sdk.py": "value = 1\n", "other.py": "x = 1\n"})
    unused = make_repository(tmp_path / "upstream/unused", {"unused.py": "value = 2\n"})
    app = make_repository(tmp_path / "app", {"main.py": "value = 0\n"})
    add_submodule(app, sdk, "libs/sdk")
    add_submodule(app, unused, "libs/unused")
    return {
        "app": app,
        "sdk": app / "libs/sdk",
        "unused": app / "libs/unused",
        "upstream": sdk,
        "root": tmp_path,
    }


def generate(workspace, rows=(("app", "main.py"), ("sdk", "sdk.py"))):
    roots = {"app": workspace["app"], "sdk": workspace["sdk"]}
    return sbom.generate_sbom(scope(roots, rows), micropython_root=MPY_ROOT)


def gaps(receipt, kind):
    return [gap for gap in receipt["gaps"] if gap["kind"] == kind]


def submodule_records(receipt):
    return {item["path"]: item for item in receipt["fetch_provenance"]["submodules"]}


def properties(component):
    return component.properties


def test_selected_submodule_reports_url_pin_and_checkout_state(workspace):
    model, receipt = generate(workspace)
    pin = git(workspace["app"], "rev-parse", "HEAD:libs/sdk")
    record = submodule_records(receipt)["libs/sdk"]
    assert record["url"] == str(workspace["upstream"])
    assert record["pinned_commit"] == pin
    assert record["head"] == pin
    assert record["head_matches_pin"] is True
    assert record["checkout"] == "present"
    assert record["url_relation"] == "identical"
    assert record["findings"] == []
    assert record["selected"] is True
    sdk_component = next(
        item["component"] for item in receipt["metadata"] if item["root"] == "sdk"
    )
    chain = json.loads(properties(model.components[sdk_component])["fetch-provenance"])
    assert [(item["path"], item["owns_root"], item["pinned_commit"]) for item in chain] == [
        ("libs/sdk", True, pin)
    ]
    for kind in (
        "checkout-gitlink-mismatch",
        "submodule-local-override",
        "dirty-selected-repository",
    ):
        assert not gaps(receipt, kind)
    # The gitlink is revision evidence only: the dependency has no release version of its own.
    assert model.components[sdk_component].version is None
    entry = next(item for item in receipt["metadata"] if item["root"] == "sdk")
    assert entry["fields"]["version"]["status"] != "known"
    assert entry["fields"]["license"]["status"] != "known"
    # Without package metadata the checkout is an unmarked boundary: the pin is not an identity.
    assert entry["boundary_kind"] == "repository"
    assert any(
        gap["kind"] == "unresolved-identity" and gap["boundary"] == entry["boundary"]
        for gap in receipt["gaps"]
    )
    assert any(
        gap["kind"] == "dependency-metadata-unresolved"
        and gap["field"] == "license"
        and gap["component"] == sdk_component
        for gap in receipt["gaps"]
    )


def test_unused_initialised_submodule_is_a_candidate_not_a_component(workspace):
    model, receipt = generate(workspace)
    unused = submodule_records(receipt)["libs/unused"]
    assert unused["selected"] is False
    assert unused["selected_boundaries"] == []
    assert unused["checkout"] == "present"
    assert unused["head_matches_pin"] is True
    directory = str(workspace["unused"])
    for component in model.components.values():
        assert directory not in json.dumps(component.properties)
        assert component.name != "unused.py"
    assert all(directory not in json.dumps(gap) for gap in receipt["gaps"])


def test_uninitialised_submodule_is_reported_without_a_component_or_gap(workspace, tmp_path):
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(workspace["app"]), str(clone)], check=True)
    model, receipt = sbom.generate_sbom(
        scope({"app": clone}, [("app", "main.py")]), micropython_root=MPY_ROOT
    )
    records = submodule_records(receipt)
    assert set(records) == {"libs/sdk", "libs/unused"}
    for record in records.values():
        assert record["checkout"] == "not-initialized"
        assert record["findings"] == ["not-initialized"]
        assert record["selected"] is False
        assert record["pinned_commit"]
        assert "head" not in record
    assert not any("libs/" in json.dumps(gap) for gap in receipt["gaps"])
    assert not any("libs/" in json.dumps(c.properties) for c in model.components.values())


def test_changed_pin_is_a_mismatch_gap_with_the_pinned_commit(workspace):
    pin = git(workspace["app"], "rev-parse", "HEAD:libs/sdk")
    git(workspace["sdk"], "checkout", "--quiet", "--detach", "HEAD")
    moved = commit_file(workspace["sdk"], "sdk.py", "value = 99\n")
    _, receipt = generate(workspace)
    [gap] = gaps(receipt, "checkout-gitlink-mismatch")
    assert gap["path"] == str(workspace["sdk"])
    assert gap["gitlink"] == pin
    assert gap["checkout"] == moved
    assert gap["pin_source"] == "git-submodule"
    assert "index_gitlink" not in gap
    record = submodule_records(receipt)["libs/sdk"]
    assert record["findings"] == ["head-differs-from-pin"]
    assert record["head_matches_pin"] is False
    assert record["diff_from_pin"]["files"] == [{"status": "M", "path": "sdk.py"}]
    assert "checkout-gitlink-mismatch" in FIRMWARE_BLOCKING_GAPS


def test_staged_pin_change_is_reported_with_both_pins(workspace):
    pin = git(workspace["app"], "rev-parse", "HEAD:libs/sdk")
    moved = commit_file(workspace["sdk"], "sdk.py", "value = 7\n")
    git(workspace["app"], "add", "libs/sdk")
    _, receipt = generate(workspace)
    [gap] = gaps(receipt, "checkout-gitlink-mismatch")
    assert (gap["gitlink"], gap["index_gitlink"], gap["checkout"]) == (pin, moved, moved)
    record = submodule_records(receipt)["libs/sdk"]
    assert "index-pin-differs-from-head-pin" in record["findings"]


def test_pin_that_was_never_fetched_is_reported(workspace):
    git(workspace["app"], "update-index", "--cacheinfo", f"160000,{'1' * 40},libs/sdk")
    git(workspace["app"], "commit", "--quiet", "-m", "Pin an unfetched commit")
    _, receipt = generate(workspace)
    record = submodule_records(receipt)["libs/sdk"]
    assert {"head-differs-from-pin", "pin-object-unavailable"} <= set(record["findings"])
    assert "diff_from_pin" not in record
    assert gaps(receipt, "checkout-gitlink-mismatch")


def test_local_override_is_reported_against_the_declared_url(workspace):
    git(workspace["sdk"], "remote", "set-url", "origin", "https://example.invalid/fork/sdk.git")
    _, receipt = generate(workspace)
    [gap] = gaps(receipt, "submodule-local-override")
    assert gap["declared_url"] == str(workspace["upstream"])
    assert gap["remote_url"] == "https://example.invalid/fork/sdk.git"
    assert submodule_records(receipt)["libs/sdk"]["findings"] == ["local-override"]
    assert "submodule-local-override" in FIRMWARE_BLOCKING_GAPS
    # The pin still matches, so only the override is raised.
    assert not gaps(receipt, "checkout-gitlink-mismatch")


def test_same_location_over_another_transport_is_not_an_override(workspace):
    git(workspace["app"], "config", "-f", ".gitmodules", "submodule.libs/sdk.url", "https://example.invalid/org/sdk.git")
    git(workspace["sdk"], "remote", "set-url", "origin", "git@example.invalid:org/sdk")
    _, receipt = generate(workspace)
    record = submodule_records(receipt)["libs/sdk"]
    assert record["url_relation"] == "equivalent"
    assert record["findings"] == []
    assert not gaps(receipt, "submodule-local-override")


def test_url_rewrite_configuration_applies_to_both_sides(workspace, tmp_path, monkeypatch):
    rewrite = tmp_path / "gitconfig"
    rewrite.write_text('[url "https://mirror.invalid/"]\n\tinsteadOf = https://example.invalid/\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(rewrite))
    git(workspace["app"], "config", "-f", ".gitmodules", "submodule.libs/sdk.url", "https://example.invalid/org/sdk.git")
    git(workspace["sdk"], "remote", "set-url", "origin", "https://example.invalid/org/sdk.git")
    _, receipt = generate(workspace)
    record = submodule_records(receipt)["libs/sdk"]
    assert record["remote_url"] == "https://mirror.invalid/org/sdk.git"
    assert record["url"] == "https://example.invalid/org/sdk.git"
    assert record["url_relation"] == "identical"
    assert not gaps(receipt, "submodule-local-override")


@pytest.mark.parametrize(
    ("declared", "remote", "base", "relation"),
    [
        ("https://h/a/b.git", "https://h/a/b.git", None, "identical"),
        ("https://h/a/b.git", "git@h:a/b", None, "equivalent"),
        ("https://h/a/b", "ssh://git@h/a/b.git/", None, "equivalent"),
        ("https://h/a/b.git", "https://h/c/b.git", None, "different"),
        ("https://h/a/b.git", "https://other/a/b.git", None, "different"),
        ("https://h/a/b.git", "/srv/mirror/b", None, "different"),
        ("/srv/mirror/b", "/srv/mirror/b/", None, "equivalent"),
        ("../sdk.git", "https://h/org/sdk.git", "https://h/org/app.git", "identical"),
        ("../sdk.git", "https://h/org/sdk.git", "git@h:org/app.git", "equivalent"),
        ("../sdk.git", "https://h/elsewhere/sdk.git", "https://h/org/app.git", "different"),
        ("../sdk.git", "https://h/org/sdk.git", None, "unavailable"),
        ("../../../../sdk.git", "https://h/org/sdk.git", "https://h/org/app.git", "unavailable"),
        (None, "https://h/a/b.git", None, "unavailable"),
        ("https://h/a/b.git", None, None, "unavailable"),
    ],
)
def test_url_relation(declared, remote, base, relation):
    assert url_relation(declared, remote, base) == relation


def test_dirty_tracked_file_is_reported_as_a_patch_against_the_pin(workspace):
    (workspace["sdk"] / "sdk.py").write_text("value = 'patched'\n")
    pin = git(workspace["app"], "rev-parse", "HEAD:libs/sdk")
    _, receipt = generate(workspace)
    [gap] = gaps(receipt, "dirty-selected-repository")
    assert gap["path"] == str(workspace["sdk"])
    changes = gap["tracked_changes"]
    assert changes["base"] == pin
    assert changes["count"] == 1
    assert changes["files"] == [{"status": "M", "path": "sdk.py"}]
    expected = subprocess.run(
        ["git", "-C", str(workspace["sdk"]), "diff", "--binary", "--no-renames", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout
    assert changes["patch_sha256"] == hashlib.sha256(expected).hexdigest()
    record = submodule_records(receipt)["libs/sdk"]
    assert record["findings"] == ["dirty-tracked-files"]
    # A different patch has a different identity.
    (workspace["sdk"] / "sdk.py").write_text("value = 'patched again'\n")
    _, other = generate(workspace)
    assert gaps(other, "dirty-selected-repository")[0]["tracked_changes"]["patch_sha256"] != changes["patch_sha256"]


def test_untracked_file_in_a_submodule_is_not_a_tracked_patch(workspace):
    (workspace["sdk"] / "scratch.txt").write_text("notes\n")
    _, receipt = generate(workspace)
    assert not gaps(receipt, "dirty-selected-repository")
    assert "diff_from_pin" not in submodule_records(receipt)["libs/sdk"]


def test_deleted_and_staged_tracked_files_are_listed(workspace):
    (workspace["sdk"] / "other.py").unlink()
    (workspace["sdk"] / "sdk.py").write_text("value = 3\n")
    git(workspace["sdk"], "add", "sdk.py")
    _, receipt = generate(workspace)
    [gap] = gaps(receipt, "dirty-selected-repository")
    assert gap["tracked_changes"]["files"] == [
        {"status": "D", "path": "other.py"},
        {"status": "M", "path": "sdk.py"},
    ]


def test_gitlink_without_a_gitmodules_url_has_no_declared_source(workspace):
    git(workspace["app"], "config", "-f", ".gitmodules", "--remove-section", "submodule.libs/sdk")
    git(workspace["app"], "add", ".gitmodules")
    git(workspace["app"], "commit", "--quiet", "-m", "Drop the entry")
    _, receipt = generate(workspace)
    record = submodule_records(receipt)["libs/sdk"]
    assert record["url"] is None
    assert "not-registered-in-gitmodules" in record["findings"]
    [gap] = gaps(receipt, "repository-provenance-unavailable")
    assert gap["path"] == str(workspace["sdk"])


@pytest.fixture
def nested(workspace):
    """The SDK pins a nested submodule, both checked out recursively."""
    inner = make_repository(workspace["root"] / "upstream/inner", {"inner.py": "value = 5\n"})
    add_submodule(workspace["upstream"], inner, "vendor/inner")
    git(workspace["sdk"], "pull", "--quiet", "origin", "HEAD")
    git(workspace["app"], "add", "libs/sdk")
    git(workspace["app"], "commit", "--quiet", "-m", "Move the SDK pin")
    git(workspace["app"], "submodule", "update", "--init", "--recursive", "--quiet")
    return {**workspace, "inner": workspace["sdk"] / "vendor/inner", "innerroot": inner}


def generate_nested(nested):
    roots = {"app": nested["app"], "sdk": nested["sdk"], "inner": nested["inner"]}
    rows = [("app", "main.py"), ("sdk", "sdk.py"), ("inner", "inner.py")]
    return sbom.generate_sbom(scope(roots, rows), micropython_root=MPY_ROOT)


def test_nested_submodule_chain_covers_every_level(nested):
    model, receipt = generate_nested(nested)
    inner_pin = git(nested["sdk"], "rev-parse", "HEAD:vendor/inner")
    entry = next(item for item in receipt["metadata"] if item["root"] == "inner")
    assert [(item["path"], item["depth"], item["owns_root"]) for item in entry["fetch_provenance"]] == [
        ("libs/sdk", 0, False),
        ("vendor/inner", 1, True),
    ]
    inner = entry["fetch_provenance"][1]
    assert inner["superproject"] == str(nested["sdk"])
    assert inner["pinned_commit"] == inner_pin
    assert inner["url"] == str(nested["innerroot"])
    records = submodule_records(receipt)
    assert records["vendor/inner"]["selected"] is True
    assert records["vendor/inner"]["depth"] == 1
    sdk_entry = next(item for item in receipt["metadata"] if item["root"] == "sdk")
    assert [item["path"] for item in sdk_entry["fetch_provenance"]] == ["libs/sdk"]
    # The SDK checkout's own submodule list includes the nested one as part of its subtree.
    assert not gaps(receipt, "checkout-gitlink-mismatch")


def test_nested_submodule_state_is_checked_independently_of_the_outer_one(nested):
    moved = commit_file(nested["inner"], "inner.py", "value = 6\n")
    _, receipt = generate_nested(nested)
    [gap] = gaps(receipt, "checkout-gitlink-mismatch")
    assert gap["path"] == str(nested["inner"])
    assert gap["checkout"] == moved
    # A nested checkout moving is a change of the nested gitlink, not of the outer pin.
    records = submodule_records(receipt)
    assert records["libs/sdk"]["findings"] == []
    assert records["vendor/inner"]["findings"] == ["head-differs-from-pin"]


def test_nested_dirty_patch_is_attributed_to_the_nested_checkout(nested):
    (nested["inner"] / "inner.py").write_text("value = 'patched'\n")
    _, receipt = generate_nested(nested)
    [gap] = gaps(receipt, "dirty-selected-repository")
    assert gap["path"] == str(nested["inner"])
    assert gap["tracked_changes"]["files"] == [{"status": "M", "path": "inner.py"}]


def test_uninitialised_nested_submodule_is_a_candidate_below_a_selected_checkout(nested, tmp_path):
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--quiet", str(nested["app"]), str(clone)], check=True)
    git(clone, "submodule", "update", "--init", "--quiet", "libs/sdk")
    model, receipt = sbom.generate_sbom(
        scope({"app": clone, "sdk": clone / "libs/sdk"}, [("app", "main.py"), ("sdk", "sdk.py")]),
        micropython_root=MPY_ROOT,
    )
    records = submodule_records(receipt)
    assert records["vendor/inner"]["checkout"] == "not-initialized"
    assert records["vendor/inner"]["depth"] == 1
    assert records["vendor/inner"]["selected"] is False
    assert not any("vendor/inner" in json.dumps(c.properties) for c in model.components.values())


def test_deinitialised_directory_with_copied_files_is_an_unusable_checkout(workspace):
    sdk = workspace["sdk"]
    git(workspace["app"], "submodule", "deinit", "--quiet", "--force", "libs/sdk")
    sdk.mkdir(exist_ok=True)
    (sdk / "sdk.py").write_text("value = 1\n")
    _, receipt = generate(workspace)
    record = submodule_records(receipt)["libs/sdk"]
    assert record["checkout"] == "unusable"
    assert record["findings"] == ["unusable-checkout"]
    [gap] = gaps(receipt, "selected-source-not-tracked")
    assert gap["path"] == str(sdk / "sdk.py")
    assert gap["submodule"] == {
        "path": "libs/sdk",
        "url": str(workspace["upstream"]),
        "pinned_commit": record["pinned_commit"],
        "checkout": "unusable",
        "checkout_reason": "the directory has content but is not a Git checkout",
    }


def test_unreadable_checkout_metadata_is_an_unusable_checkout(workspace):
    sdk = workspace["sdk"]
    (sdk / ".git").unlink()
    (sdk / ".git").write_text("gitdir: ../../does-not-exist\n")
    inspector = SubmoduleInspector()
    [record] = inspector.chain(sdk)
    assert record["checkout"] == "unusable"
    assert record["checkout_reason"] == "the checkout metadata is unavailable"


def test_directory_outside_any_repository_has_no_chain(tmp_path):
    (tmp_path / "plain").mkdir()
    assert SubmoduleInspector().chain(tmp_path / "plain") == []


def test_plain_nested_repository_is_not_a_submodule(tmp_path):
    make_repository(tmp_path / "outer", {"a.py": "a = 1\n"})
    make_repository(tmp_path / "outer/inner", {"b.py": "b = 1\n"})
    assert SubmoduleInspector().chain(tmp_path / "outer/inner") == []


def firmware_summary(workspace, tmp_path):
    """SBOM of a linked image whose receipt names the application and the SDK checkout."""
    from mpy_analysis import scope as scope_module
    from test_sbom import firmware_receipt

    app = workspace["app"]
    (app / "selected.c").write_text("int selected;\n")
    git(app, "add", "selected.c")
    git(app, "commit", "--quiet", "-m", "Add a native source")
    database = tmp_path / "compile_commands.json"
    database.write_text(
        json.dumps(
            [
                {
                    "directory": str(app),
                    "file": "selected.c",
                    "arguments": ["cc", "-c", "selected.c", "-o", "selected.o"],
                }
            ]
        )
    )
    inventory = scope({"app": app, "library": workspace["sdk"]}, [("library", "sdk.py")])
    inventory["groups"]["native"] = scope_module.collect_compdb_sources(
        database,
        roots=inventory["roots"],
        owners={"app": "application", "library": "external"},
    )
    evidence = firmware_receipt(inventory, app, tmp_path / "firmware-build")
    _, receipt = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT, build_receipt=evidence)
    return receipt


def test_clean_pinned_submodule_leaves_firmware_completeness_established(workspace, tmp_path):
    receipt = firmware_summary(workspace, tmp_path)
    assert receipt["link_closure"]["status"] == "complete"
    assert receipt["firmware_completeness"] == "established"


def test_changed_pin_prevents_firmware_completeness(workspace, tmp_path):
    commit_file(workspace["sdk"], "sdk.py", "value = 42\n")
    receipt = firmware_summary(workspace, tmp_path)
    assert gaps(receipt, "checkout-gitlink-mismatch")
    assert receipt["firmware_completeness"] == "not-established"


def test_local_override_prevents_firmware_completeness(workspace, tmp_path):
    git(workspace["sdk"], "remote", "set-url", "origin", "https://example.invalid/fork/sdk.git")
    receipt = firmware_summary(workspace, tmp_path)
    assert gaps(receipt, "submodule-local-override")
    assert receipt["firmware_completeness"] == "not-established"
