"""west and ESP-IDF lock/manifest evidence: strict reading, candidate status, workspace correlation."""

import json
import subprocess
from pathlib import Path

import pytest

from mpy_analysis import sbom
from mpy_analysis.locks import inspect_lock
from test_fetch_provenance import MPY_ROOT, commit_file, git, make_repository, scope

IDF_LOCK = """\
dependencies:
  espressif/mdns:
    component_hash: 46ee81d32fbf850462d8af1e83303389602f6a6a9eddd2a55104cb4c063858ed
    source:
      service_url: https://api.components.espressif.com/
      type: service
    version: 1.1.0
  idf:
    component_hash: null
    source:
      type: idf
    version: 5.2.2
  local_part:
    component_hash: null
    source:
      path: ../components/local_part
      type: local
    version: 0.1.0
manifest_hash: dbd89ab1e9f2aab41f4712a1189ef7edbdf8b1ad3e2ba42088e27d990cd832f4
target: esp32s3
version: 1.0.0
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def lock_of(tmp_path, name, text):
    return inspect_lock(write(tmp_path / name, text))


def west_manifest(*, a, b, defaults="", extra_projects="", group_filter=""):
    return f"""\
manifest:
  defaults:
    remote: upstream
{defaults}
  remotes:
    - name: upstream
      url-base: https://example.invalid/org
    - name: vendor
      url-base: ssh://git@example.invalid/vendor/
{group_filter}
  projects:
    - name: alpha
      revision: {a}
      path: modules/a
    - name: beta
      remote: vendor
      repo-path: beta-repo
      revision: {b}
      path: modules/b
{extra_projects}
  self:
    path: zephyr
"""


@pytest.fixture
def west(tmp_path):
    """A west workspace: manifest repository `zephyr` and project checkouts."""
    top = tmp_path / "top"
    (top / ".west").mkdir(parents=True)
    write(top / ".west/config", "[manifest]\npath = zephyr\nfile = west.yml\n")
    a = make_repository(top / "modules/a", {"a.py": "a = 1\n"})
    b = make_repository(top / "modules/b", {"b.py": "b = 1\n"})
    pins = {"a": git(a, "rev-parse", "HEAD"), "b": git(b, "rev-parse", "HEAD")}
    zephyr = make_repository(
        top / "zephyr",
        {
            "app.py": "app = 1\n",
            "west.yml": west_manifest(a=pins["a"], b=pins["b"]),
        },
    )
    return {"top": top, "a": a, "b": b, "zephyr": zephyr, "pins": pins}


def entries(lock):
    return {item["name"]: item for item in lock["entries"]}


def test_west_manifest_is_read_into_candidate_entries(west):
    lock = inspect_lock(west["zephyr"] / "west.yml")
    assert lock["status"] == "candidate"
    assert lock["format"] == "west-manifest"
    assert len(lock["sha256"]) == 64
    alpha, beta = entries(lock)["alpha"], entries(lock)["beta"]
    assert alpha["url"] == "https://example.invalid/org/alpha"
    assert alpha["revision"] == west["pins"]["a"]
    assert alpha["revision_kind"] == "commit"
    assert alpha["revision_source"] == "project"
    assert beta["url"] == "ssh://git@example.invalid/vendor/beta-repo"
    assert lock["workspace"]["state"] == "bound"
    assert alpha["checkout"] == {"state": "matches", "head": west["pins"]["a"]}
    assert beta["checkout"] == {"state": "matches", "head": west["pins"]["b"]}


def test_west_checkout_states_distinguish_stale_missing_and_unverifiable_pins(west):
    moved = commit_file(west["b"], "b.py", "b = 2\n")
    text = west_manifest(
        a=west["pins"]["a"],
        b=west["pins"]["b"],
        extra_projects="""\
    - name: gone
      revision: %s
    - name: notrepo
      revision: %s
      path: notrepo
    - name: floating
      revision: main
      path: modules/a2
"""
        % ("1" * 39 + "a", "2" * 39 + "b"),
    )
    (west["top"] / "notrepo").mkdir()
    (west["top"] / "notrepo/file").write_text("x\n")
    make_repository(west["top"] / "modules/a2", {"a.py": "a = 1\n"})
    write(west["zephyr"] / "west.yml", text)
    found = entries(inspect_lock(west["zephyr"] / "west.yml"))
    assert found["alpha"]["checkout"]["state"] == "matches"
    assert found["beta"]["checkout"] == {"state": "differs", "head": moved}
    assert found["gone"]["checkout"] == {"state": "absent", "head": None}
    assert found["notrepo"]["checkout"] == {"state": "not-a-checkout", "head": None}
    assert found["floating"]["revision_kind"] == "symbolic"
    assert found["floating"]["checkout"]["state"] == "unverifiable-symbolic-revision"


def test_west_defaults_and_group_filters(west):
    optional = """\
    - name: optional-one
      groups: [extras]
      revision: %s
      path: modules/c
""" % ("3" * 39 + "c")
    text = west_manifest(
        a=west["pins"]["a"],
        b=west["pins"]["b"],
        defaults="    revision: trunk",
        group_filter="  group-filter: [-extras]",
    ).replace("  self:", optional + "  self:")
    write(west["zephyr"] / "west.yml", text)
    found = entries(inspect_lock(west["zephyr"] / "west.yml"))
    assert found["optional-one"]["active"] is False
    assert found["optional-one"]["checkout"]["state"] == "inactive"
    assert found["alpha"]["active"] is True
    # The workspace configuration filter is applied after the manifest's.
    write(west["top"] / ".west/config", "[manifest]\npath = zephyr\nfile = west.yml\ngroup-filter = +extras\n")
    found = entries(inspect_lock(west["zephyr"] / "west.yml"))
    assert found["optional-one"]["active"] is True
    assert found["optional-one"]["checkout"]["state"] == "absent"


def test_west_default_revision_is_marked_as_a_default(west):
    text = """\
manifest:
  remotes:
    - name: upstream
      url-base: https://example.invalid/org
  projects:
    - name: bare
      remote: upstream
    - name: pinned
      url: https://example.invalid/other/pinned.git
      revision: v1.2
"""
    write(west["zephyr"] / "west.yml", text)
    found = entries(inspect_lock(west["zephyr"] / "west.yml"))
    assert found["bare"]["revision"] == "master"
    assert found["bare"]["revision_source"] == "west-default"
    assert found["pinned"]["url"] == "https://example.invalid/other/pinned.git"


def test_west_manifest_outside_a_workspace_has_no_checkout_correlation(tmp_path):
    lock = lock_of(tmp_path / "plain", "west.yml", west_manifest(a="a" * 40, b="b" * 40))
    assert lock["status"] == "candidate"
    assert lock["workspace"]["state"] == "no-workspace"
    assert {item["checkout"]["state"] for item in lock["entries"]} == {"workspace-unavailable"}
    assert {item["active"] for item in lock["entries"]} == {None}


def test_west_manifest_that_is_not_the_workspace_manifest_is_unbound(west):
    other = write(west["top"] / "other/west.yml", west_manifest(a="a" * 40, b="b" * 40))
    lock = inspect_lock(other)
    assert lock["workspace"]["state"] == "unbound"
    assert {item["checkout"]["state"] for item in lock["entries"]} == {"workspace-unavailable"}


WEST_BASE = """\
manifest:
  remotes:
    - name: upstream
      url-base: https://example.invalid/org
  projects:
    - name: alpha
      remote: upstream
      revision: main
"""


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (WEST_BASE + "  self:\n    path: zephyr\n    import: submanifests\n", "self.import"),
        (WEST_BASE.replace("revision: main", "revision: main\n      import: true"), "imports a manifest"),
        (WEST_BASE + "  surprise: 1\n", "unsupported key 'surprise'"),
        ("manifest:\n  projects: []\nextra: 1\n", "unsupported key 'extra'"),
        ("- not\n- a mapping\n", "must be a mapping"),
        ("", "exactly one YAML document"),
        (WEST_BASE.replace("revision: main", "revision: main\n      url: https://x.invalid/y"), "combines url"),
        (WEST_BASE.replace("remote: upstream", "remote: nowhere"), "unknown remote"),
        (WEST_BASE.replace("      remote: upstream\n", ""), "neither url nor remote"),
        (WEST_BASE.replace("name: alpha", "name: alpha\n      name: again"), "duplicate key"),
        (WEST_BASE + "    - name: alpha\n      remote: upstream\n", "defined twice"),
        (WEST_BASE + "    - name: other\n      remote: upstream\n      path: alpha\n", "used twice"),
        (WEST_BASE.replace("name: alpha", "name: alpha\n      path: ../escape"), "relative POSIX path"),
        (WEST_BASE.replace("revision: main", "revision: 1234"), "must be a nonempty string"),
        (WEST_BASE.replace("revision: main", "revision: &pin main"), "anchors"),
        (WEST_BASE.replace("revision: main", "revision: !!str main"), "tags"),
        (WEST_BASE.replace("revision: main", "revision: *pin"), "aliases"),
        (WEST_BASE + "---\n" + WEST_BASE, "exactly one YAML document"),
        (WEST_BASE + "  group-filter: [foo]\n", "+group or -group"),
        (WEST_BASE.replace("- name: alpha", "- name: alpha\n      groups: [a]\n      ok: 1"), "unsupported key 'ok'"),
        ("manifest:\n  projects: [\n", "malformed YAML"),
        ("manifest:\n  1: x\n  projects: []\n", "plain strings"),
    ],
)
def test_west_manifest_outside_the_supported_shape_is_unsupported(tmp_path, text, reason):
    lock = lock_of(tmp_path, "west.yml", text)
    assert lock["status"] == "unsupported"
    assert reason in lock["reason"]
    assert lock["entries"] == []


def test_idf_dependencies_lock_is_read_as_candidates_and_never_verified(tmp_path):
    lock = lock_of(tmp_path, "dependencies.lock", IDF_LOCK)
    assert lock["status"] == "candidate"
    assert lock["format"] == "esp-idf-dependencies-lock"
    assert lock["lock_version"] == "1.0.0"
    assert lock["target"] == "esp32s3"
    assert lock["manifest_hash"].startswith("dbd89ab1")
    found = entries(lock)
    assert found["espressif/mdns"]["version"] == "1.1.0"
    assert found["espressif/mdns"]["component_hash"].startswith("46ee81d3")
    assert found["espressif/mdns"]["source"] == {
        "type": "service",
        "service_url": "https://api.components.espressif.com/",
    }
    assert found["espressif/mdns"]["role"] == "component"
    assert found["idf"]["role"] == "sdk"
    assert found["idf"]["component_hash"] is None
    assert found["local_part"]["source"] == {"type": "local", "path": "../components/local_part"}


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ("version: 1.0.0\n", "version: 2.0.0\n", "lock format version '2.0.0' is not supported"),
        ("version: 1.0.0\n", "", "lock format version None is not supported"),
        ("target: esp32s3\n", "target: esp32s3\nextra: 1\n", "unsupported key 'extra'"),
        ("manifest_hash: dbd89a", "manifest_hash: DBD89A", "SHA-256 hex digest"),
        ("type: service", "type: oci", "unsupported source type 'oci'"),
        ("type: idf", "type: idf\n      path: /x", "unsupported key 'path'"),
        ("service_url: https://api.components.espressif.com/\n      ", "", "lacks required key 'service_url'"),
        ("component_hash: 46ee", "component_hash: 46EE", "SHA-256 hex digest"),
        ("    version: 1.1.0\n", "    version: 1.1\n", "must be a nonempty string"),
        ("    version: 1.1.0\n", "    version: 1.1.0\n    surprise: 1\n", "unsupported key 'surprise'"),
        ("  espressif/mdns:", "  espressif mdns:", "is not a component name"),
        ("  idf:", "  espressif/mdns:", "duplicate key"),
    ],
)
def test_idf_lock_outside_the_supported_shape_is_unsupported(tmp_path, old, new, reason):
    assert old in IDF_LOCK
    lock = lock_of(tmp_path, "dependencies.lock", IDF_LOCK.replace(old, new, 1))
    assert lock["status"] == "unsupported"
    assert reason in lock["reason"]
    assert lock["entries"] == []


def test_idf_service_dependency_requires_a_component_hash(tmp_path):
    text = IDF_LOCK.replace(
        "component_hash: 46ee81d32fbf850462d8af1e83303389602f6a6a9eddd2a55104cb4c063858ed",
        "component_hash: null",
    )
    lock = lock_of(tmp_path, "dependencies.lock", text)
    assert lock["status"] == "unsupported"
    assert "SHA-256 hex digest" in lock["reason"]


IDF_MANIFEST = """\
version: "1.5.1"
description: A component
dependencies:
  idf: ">=4.4.1"
  espressif/cbor:
    version: 0.6.0~1
    rules:
      - if: target != esp32c2
  vendor/pinned:
    git: https://example.invalid/vendor/pinned.git
    version: abcdef
  helper:
    path: ../helper
  plain: "^1.0"
targets:
  - esp32s3
"""


def test_idf_component_manifest_declares_candidates(tmp_path):
    lock = lock_of(tmp_path, "idf_component.yml", IDF_MANIFEST)
    assert lock["status"] == "candidate"
    found = entries(lock)
    assert found["idf"]["role"] == "sdk-constraint"
    assert found["idf"]["spec"] == ">=4.4.1"
    assert found["espressif/cbor"]["spec"] == "0.6.0~1"
    assert found["espressif/cbor"]["conditional"] is True
    assert found["espressif/cbor"]["source"] == "registry"
    assert found["vendor/pinned"]["source"] == "git"
    assert found["vendor/pinned"]["git"] == "https://example.invalid/vendor/pinned.git"
    assert found["helper"]["source"] == "local"
    assert found["plain"]["spec"] == "^1.0"
    assert found["plain"]["conditional"] is False


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("dependencies:\n  a/b:\n    surprise: 1\n", "unsupported key 'surprise'"),
        ("dependencies:\n  a/b/c: '1'\n", "is not a component name"),
        ("dependencies: [a]\n", "must be a mapping"),
        ("dependencies:\n  a/b:\n    git: https://x.invalid/b\n    path: ../b\n", "combines git"),
        ("dependencies:\n  a/b: 5\n", "must be a nonempty string"),
        ("dependencies:\n  a/b: &x '1'\n  c: *x\n", "anchors"),
        ("[1, 2]\n", "must be a mapping"),
    ],
)
def test_idf_component_manifest_outside_the_supported_shape_is_unsupported(tmp_path, text, reason):
    lock = lock_of(tmp_path, "idf_component.yml", text)
    assert lock["status"] == "unsupported"
    assert reason in lock["reason"]


def test_idf_component_manifest_without_dependencies_has_no_entries(tmp_path):
    lock = lock_of(tmp_path, "idf_component.yml", "version: '1.0'\n")
    assert (lock["status"], lock["entries"]) == ("candidate", [])


def test_non_utf8_and_oversized_files_are_unsupported(tmp_path):
    path = tmp_path / "dependencies.lock"
    path.write_bytes(b"\xff\xfe\x00bad")
    assert "not UTF-8" in inspect_lock(path)["reason"]
    path.write_text("a: " + "x" * (1 << 20) + "\n")
    assert "exceeds" in inspect_lock(path)["reason"]


def generate(west, rows):
    roots = {"zephyr": west["zephyr"], "a": west["a"]}
    return sbom.generate_sbom(scope(roots, rows), micropython_root=MPY_ROOT)


def lock_summary(receipt):
    [item] = receipt["fetch_provenance"]["locks"]
    return {entry["name"]: entry for entry in item["entries"]}


def test_selected_project_and_unused_candidate_are_told_apart_without_components(west):
    model, receipt = generate(west, [("zephyr", "app.py"), ("a", "a.py")])
    found = lock_summary(receipt)
    assert found["alpha"]["selection"] == "selected"
    assert found["alpha"]["selected_boundaries"] == ["a/."]
    assert found["beta"]["selection"] == "candidate"
    assert found["beta"]["selected_boundaries"] == []
    assert not any(gap["kind"] == "checkout-gitlink-mismatch" for gap in receipt["gaps"])
    names = {component.name for component in model.components.values()}
    assert not {"alpha", "beta", "beta-repo"} & names
    directories = json.dumps([c.properties for c in model.components.values()])
    assert str(west["b"]) not in directories


def test_stale_manifest_pin_of_a_selected_project_is_a_mismatch_gap(west):
    moved = commit_file(west["a"], "a.py", "a = 2\n")
    _, receipt = generate(west, [("zephyr", "app.py"), ("a", "a.py")])
    [gap] = [g for g in receipt["gaps"] if g["kind"] == "checkout-gitlink-mismatch"]
    assert gap["pin_source"] == "west-manifest"
    assert gap["project"] == "alpha"
    assert gap["path"] == str(west["a"])
    assert gap["gitlink"] == west["pins"]["a"]
    assert gap["checkout"] == moved
    assert gap["manifest"] == str(west["zephyr"] / "west.yml")


def test_stale_pin_of_an_unselected_project_is_evidence_only(west):
    commit_file(west["b"], "b.py", "b = 2\n")
    _, receipt = generate(west, [("zephyr", "app.py"), ("a", "a.py")])
    assert lock_summary(receipt)["beta"]["checkout"]["state"] == "differs"
    assert not any(gap["kind"] == "checkout-gitlink-mismatch" for gap in receipt["gaps"])


def test_unsupported_manifest_raises_a_boundary_gap_and_generation_still_succeeds(west, tmp_path):
    write(west["zephyr"] / "west.yml", WEST_BASE + "  self:\n    import: submanifests\n")
    git(west["zephyr"], "add", "west.yml")
    git(west["zephyr"], "commit", "--quiet", "-m", "Import")
    inventory = scope({"zephyr": west["zephyr"], "a": west["a"]}, [("zephyr", "app.py"), ("a", "a.py")])
    scope_file = write(tmp_path / "scope.json", json.dumps(inventory))
    output = tmp_path / "out"
    assert (
        sbom.main(
            [
                "--scope",
                str(scope_file),
                "--micropython-root",
                str(MPY_ROOT),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    receipt = json.loads((output / "sbom-summary.json").read_text())
    [gap] = [g for g in receipt["gaps"] if g["kind"] == "unsupported-lock-format"]
    assert gap["format"] == "west-manifest"
    assert gap["status"] == "unsupported"
    assert "self.import" in gap["reason"]
    assert gap["path"] == str(west["zephyr"] / "west.yml")
    assert receipt["boundary_resolution"] == "incomplete"
    assert receipt["fetch_provenance"]["locks"][0]["status"] == "unsupported"
    assert receipt["fetch_provenance"]["locks"][0]["entries"] == []


def test_idf_lock_beside_selected_sources_is_candidate_evidence_not_selection(tmp_path):
    project = make_repository(
        tmp_path / "project",
        {"main.py": "x = 1\n", "dependencies.lock": IDF_LOCK},
    )
    model, receipt = sbom.generate_sbom(
        scope({"project": project}, [("project", "main.py")]), micropython_root=MPY_ROOT
    )
    [lock] = receipt["fetch_provenance"]["locks"]
    assert lock["status"] == "candidate"
    assert {entry["selection"] for entry in lock["entries"]} == {"not-determined"}
    assert not any(gap["kind"] == "unsupported-lock-format" for gap in receipt["gaps"])
    names = {component.name for component in model.components.values()}
    assert not {"espressif/mdns", "idf", "local_part", "mdns"} & names
    assert not any("espressif" in json.dumps(c.properties) for c in model.components.values())


def test_lock_files_outside_a_selected_boundary_chain_are_not_read(tmp_path):
    project = make_repository(
        tmp_path / "project",
        {"src/main.py": "x = 1\n", "other/dependencies.lock": IDF_LOCK, "west.yml": WEST_BASE},
    )
    _, receipt = sbom.generate_sbom(
        scope({"project": project / "src"}, [("project", "main.py")]), micropython_root=MPY_ROOT
    )
    assert receipt["fetch_provenance"]["locks"] == []


def test_lock_reached_through_a_symlink_with_another_target_name_is_still_read(tmp_path):
    project = make_repository(
        tmp_path / "project", {"main.py": "x = 1\n", "locks/resolved.txt": IDF_LOCK}
    )
    (project / "dependencies.lock").symlink_to("locks/resolved.txt")
    _, receipt = sbom.generate_sbom(
        scope({"project": project}, [("project", "main.py")]), micropython_root=MPY_ROOT
    )
    [lock] = receipt["fetch_provenance"]["locks"]
    assert lock["format"] == "esp-idf-dependencies-lock"
    assert lock["status"] == "candidate"
    assert lock["path"] == str(project / "locks/resolved.txt")


def test_lock_symlink_escaping_the_root_is_not_read(tmp_path):
    outside = write(tmp_path / "outside/dependencies.lock", IDF_LOCK)
    project = make_repository(tmp_path / "project", {"main.py": "x = 1\n"})
    (project / "dependencies.lock").symlink_to(outside)
    _, receipt = sbom.generate_sbom(
        scope({"project": project}, [("project", "main.py")]), micropython_root=MPY_ROOT
    )
    assert receipt["fetch_provenance"]["locks"] == []
