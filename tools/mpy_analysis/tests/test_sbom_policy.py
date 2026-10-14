"""Behavioural checks for the SBOM completeness policy gate."""

import json

import pytest
from link_fixture import link_evidence
from mpy_analysis import sbom, sbom_policy
from mpy_analysis.deployment import SCHEMA as DEPLOYMENT_SCHEMA
from test_sbom import MPY_ROOT, compiled_selection

PROFILES = ("firmware-complete", "firmware-linked")
KNOWN_FIELD = {"status": "known", "required": True, "evidence": [], "issues": []}


def make_summary(evidence_path):
    """A summary in which every firmware profile requirement holds, bound to the evidence file."""
    evidence = json.loads(evidence_path.read_text())
    image = evidence["images"][0]
    return {
        "schema_version": 1,
        "target": "board",
        "status": "generated",
        "coverage_status": "selected-sources-accounted",
        "source_accounting": "accounted",
        "firmware_completeness": "established",
        "metadata_completeness": "complete",
        "boundary_resolution": "resolved",
        "gaps": [],
        "unassessed_groups": {},
        "sources": [{"component": "urn:mpy:file:1"}, {"component": "urn:mpy:file:2"}],
        "metadata": [
            {
                "component": "urn:mpy:dependency:1",
                "boundary_issues": [],
                "fields": {name: dict(KNOWN_FIELD) for name in ("name", "version", "license")},
            }
        ],
        "link_closure": {
            "schema": evidence["schema"],
            "evidence_id": evidence["evidence_id"],
            "status": "complete",
            "images": [
                {
                    "id": image["id"],
                    "elf_sha256": image["elf"]["sha256"],
                    "link_closure": "complete",
                    "linked_components": ["urn:mpy:file:1", "urn:mpy:file:2"],
                    "unmapped_source_bindings": [],
                }
            ],
        },
    }


def leave_metadata_unresolved(summary, field="license"):
    """Record a required metadata field the generator could not resolve."""
    summary["metadata_completeness"] = "incomplete"
    summary["coverage_status"] = "incomplete"
    summary["metadata"][0]["fields"][field] = {**KNOWN_FIELD, "status": "missing"}
    summary["gaps"].append(
        {
            "kind": "dependency-metadata-unresolved",
            "component": "urn:mpy:dependency:1",
            "field": field,
            "status": "missing",
        }
    )


def leave_image_unlinked(summary):
    """One linked source binding that no selected component owns."""
    summary["firmware_completeness"] = "not-established"
    summary["coverage_status"] = "incomplete"
    closure = summary["link_closure"]
    closure["status"] = "incomplete"
    closure["images"][0]["link_closure"] = "incomplete"
    closure["images"][0]["unmapped_source_bindings"] = [{"root": "firmware", "path": "src/other.c"}]
    summary["gaps"].append(
        {
            "kind": "linked-source-not-in-selected-scope",
            "root": "firmware",
            "path": "src/other.c",
            "image": closure["images"][0]["id"],
        }
    )


def deploy(summary, status="established", **block):
    """Attach a deployment block and the claim that goes with it."""
    summary["deployment_completeness"] = status
    summary["deployment"] = {
        "schema": DEPLOYMENT_SCHEMA,
        "status": status,
        "members": [{"digest": "match"}],
        "reasons": [] if status == "established" else [{"code": "image-hash-mismatch"}],
        "runtime_fetch": {"mode": "none"},
        "link_evidence": {"evidence_id": summary["link_closure"]["evidence_id"]},
    } | block


@pytest.fixture
def bound(tmp_path):
    evidence_path = link_evidence(tmp_path / "build")
    return make_summary(evidence_path), evidence_path


def run_cli(tmp_path, summary, profile, evidence_path=None, capsys=None):
    summary_path = tmp_path / "sbom-summary.json"
    summary_path.write_text(json.dumps(summary))
    argv = ["--summary", str(summary_path), "--profile", profile]
    if evidence_path:
        argv += ["--link-evidence", str(evidence_path)]
    code = sbom_policy.main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("profile", PROFILES)
def test_complete_summary_with_matching_evidence_satisfies_the_profile(bound, tmp_path, capsys, profile):
    summary, evidence_path = bound

    code, result = run_cli(tmp_path, summary, profile, evidence_path, capsys)

    assert code == 0
    assert result["result"] == "satisfied" and result["unsatisfied"] == []
    assert result["link_evidence"] == {
        "verified": True,
        "evidence_id": summary["link_closure"]["evidence_id"],
    }
    link = result["dimensions"]["link_closure"]["evidence"]
    assert (link["images_complete"], link["images_total"]) == (1, 1)
    sources = result["dimensions"]["source_accounting"]["evidence"]
    assert (sources["selected_sources_accounted"], sources["selected_sources_total"]) == (2, 2)


def test_linked_profile_tolerates_metadata_gaps_that_fail_the_complete_profile(bound, tmp_path, capsys):
    summary, evidence_path = bound
    leave_metadata_unresolved(summary, "license")
    leave_metadata_unresolved(summary, "version")

    linked_code, linked = run_cli(tmp_path, summary, "firmware-linked", evidence_path, capsys)
    complete_code, complete = run_cli(tmp_path, summary, "firmware-complete", evidence_path, capsys)

    assert linked_code == 0
    assert linked["dimensions"]["metadata_completeness"]["status"] == "not-applicable"
    assert linked["dimensions"]["metadata_completeness"]["observed"] == "unsatisfied"
    assert complete_code == 1
    assert complete["unsatisfied"] == ["gaps", "metadata_completeness"]
    metadata = complete["dimensions"]["metadata_completeness"]["evidence"]
    assert metadata["required_fields_total"] == 3 and metadata["required_fields_known"] == 1
    assert metadata["unresolved_required_fields"] == {"license": 1, "version": 1}


def test_complete_profile_fails_when_selected_sources_are_unaccounted(bound, tmp_path, capsys):
    summary, evidence_path = bound
    summary["source_accounting"] = "incomplete"
    summary["coverage_status"] = "incomplete"
    summary["unassessed_groups"] = {"generated": 3}

    code, result = run_cli(tmp_path, summary, "firmware-complete", evidence_path, capsys)

    assert code == 1 and result["unsatisfied"] == ["source_accounting"]
    row = result["dimensions"]["source_accounting"]["evidence"]
    assert (row["selected_sources_accounted"], row["selected_sources_total"]) == (2, 5)
    assert row["unassessed_groups"] == {"generated": 3}


@pytest.mark.parametrize("profile", PROFILES)
def test_unlinked_source_fails_every_profile_and_reports_the_image(bound, tmp_path, capsys, profile):
    summary, evidence_path = bound
    leave_image_unlinked(summary)

    code, result = run_cli(tmp_path, summary, profile, evidence_path, capsys)

    assert code == 1
    assert {"firmware_completeness", "link_closure"} <= set(result["unsatisfied"])
    link = result["dimensions"]["link_closure"]["evidence"]
    assert (link["images_complete"], link["images_total"]) == (0, 1)
    assert link["unmapped_source_bindings"] == 1
    assert link["images"][0]["id"] == "application"


@pytest.mark.parametrize("profile", PROFILES)
def test_summary_without_evidence_never_satisfies_a_profile(bound, tmp_path, capsys, profile):
    summary, _ = bound
    summary["firmware_completeness"] = "not-established"
    summary["link_closure"] = {"status": "not-established"}

    code, result = run_cli(tmp_path, summary, profile, capsys=capsys)

    assert code == 1
    assert set(result["unsatisfied"]) >= {"firmware_completeness", "link_closure"}
    assert result["link_evidence"] is None


INCONSISTENT = {
    "established-with-incomplete-closure": lambda s: (
        s["link_closure"].update(status="incomplete"),
        s["link_closure"]["images"][0].update(link_closure="incomplete"),
    ),
    "established-with-blocking-gap": lambda s: (
        s.update(coverage_status="incomplete"),
        s["gaps"].append({"kind": "dirty-selected-repository", "repository": "firmware"}),
    ),
    "established-without-evidence": lambda s: s.update(link_closure={"status": "not-established"}),
    "complete-image-with-unmapped-binding": lambda s: s["link_closure"]["images"][0].update(
        unmapped_source_bindings=[{"root": "firmware", "path": "x.c"}]
    ),
    "accounted-with-unassessed-groups": lambda s: s.update(
        unassessed_groups={"generated": 1}, coverage_status="incomplete"
    ),
    "metadata-complete-with-unknown-field": lambda s: s["metadata"][0]["fields"]["version"].update(
        status="missing"
    ),
}


@pytest.mark.parametrize("corrupt", INCONSISTENT.values(), ids=INCONSISTENT)
def test_inconsistent_summary_is_invalid_input_not_a_verdict(bound, tmp_path, capsys, corrupt):
    summary, _ = bound
    corrupt(summary)

    code, result = run_cli(tmp_path, summary, "firmware-linked", capsys=capsys)

    assert code == 2
    assert result["result"] == "invalid" and result["errors"]
    assert "dimensions" not in result


@pytest.mark.parametrize("version", [2, "1", None])
def test_unknown_summary_schema_version_is_invalid(bound, tmp_path, capsys, version):
    summary, _ = bound
    summary["schema_version"] = version

    code, result = run_cli(tmp_path, summary, "firmware-linked", capsys=capsys)

    assert code == 2 and "schema_version" in result["errors"][0]


def test_unreadable_summary_and_unknown_profile_are_invalid(bound, tmp_path, capsys):
    summary, _ = bound
    broken = tmp_path / "broken.json"
    for content in (None, "{not json", "[]"):
        if content is not None:
            broken.write_text(content)
        path = broken if content is not None else tmp_path / "absent.json"
        assert sbom_policy.main(["--summary", str(path), "--profile", "firmware-linked"]) == 2
        assert json.loads(capsys.readouterr().out)["result"] == "invalid"
    code, result = run_cli(tmp_path, summary, "firmware-everything", capsys=capsys)
    assert code == 2 and "unknown profile" in result["errors"][0]


def test_evidence_from_a_different_build_is_rejected(bound, tmp_path, capsys):
    summary, _ = bound
    other = link_evidence(tmp_path / "other-build", source_path="other.c")

    code, result = run_cli(tmp_path, summary, "firmware-linked", other, capsys)

    assert code == 2
    assert any("evidence_id" in error for error in result["errors"])


def test_summary_images_must_match_the_evidence_images(bound, tmp_path, capsys):
    summary, evidence_path = bound
    summary["link_closure"]["images"][0]["elf_sha256"] = "9" * 64

    code, result = run_cli(tmp_path, summary, "firmware-linked", evidence_path, capsys)

    assert code == 2
    assert any("images" in error for error in result["errors"])


def test_unreadable_or_foreign_evidence_is_rejected(bound, tmp_path, capsys):
    summary, evidence_path = bound
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"schema": "something-else/v1"}))
    garbage = tmp_path / "garbage.json"
    garbage.write_text("not json")

    for path in (foreign, garbage, tmp_path / "absent.json"):
        code, result = run_cli(tmp_path, summary, "firmware-linked", path, capsys)
        assert code == 2 and result["errors"]


def test_evidence_cannot_back_a_summary_generated_without_it(bound, tmp_path, capsys):
    summary, evidence_path = bound
    summary["firmware_completeness"] = "not-established"
    summary["link_closure"] = {"status": "not-established"}

    code, result = run_cli(tmp_path, summary, "firmware-linked", evidence_path, capsys)

    assert code == 2
    assert any("was not generated from link evidence" in error for error in result["errors"])


def test_deployable_profile_needs_an_established_deployment(bound, tmp_path, capsys):
    summary, evidence_path = bound
    declined, result = run_cli(tmp_path, summary, "deployable", evidence_path, capsys)
    assert declined == 1 and result["unsatisfied"] == ["deployment"]

    deploy(summary)
    code, result = run_cli(tmp_path, summary, "deployable", evidence_path, capsys)
    assert code == 0
    deployment = result["dimensions"]["deployment"]["evidence"]
    assert (deployment["members_verified"], deployment["members_total"]) == (1, 1)

    deploy(summary, "not-established")
    code, result = run_cli(tmp_path, summary, "deployable", evidence_path, capsys)
    assert code == 1
    assert result["dimensions"]["deployment"]["evidence"]["reasons_by_code"] == {"image-hash-mismatch": 1}


@pytest.mark.parametrize(
    "overclaim",
    [
        {"members": [{"digest": "mismatch"}]},
        {"reasons": [{"code": "image-hash-mismatch"}]},
        {"runtime_fetch": {"mode": "unrestricted"}},
        {"link_evidence": {"evidence_id": "0" * 64}},
    ],
    ids=["digest", "reasons", "runtime-fetch", "unbound-evidence"],
)
def test_summary_overclaiming_deployment_is_invalid(bound, tmp_path, capsys, overclaim):
    summary, evidence_path = bound
    deploy(summary, **overclaim)

    code, result = run_cli(tmp_path, summary, "deployable", evidence_path, capsys)

    assert code == 2 and result["errors"]


def test_policy_judges_the_summary_the_generator_actually_writes(tmp_path, capsys):
    """Round trip through sbom.generate_sbom: a linked build passes linked, an unlinked one fails both."""
    inventory, _ = compiled_selection(tmp_path)
    paths = {}
    for name, source in (("linked", "selected.c"), ("unlinked", "missing.c")):
        evidence_path = link_evidence(tmp_path / f"build-{name}", source)
        _, summary = sbom.generate_sbom(inventory, micropython_root=MPY_ROOT, link_evidence=evidence_path)
        summary_path = tmp_path / f"{name}-summary.json"
        summary_path.write_text(json.dumps(summary))
        paths[name] = (summary_path, evidence_path)

    def judge(name, profile):
        summary_path, evidence_path = paths[name]
        code = sbom_policy.main(
            ["--summary", str(summary_path), "--link-evidence", str(evidence_path), "--profile", profile]
        )
        return code, json.loads(capsys.readouterr().out)

    assert judge("linked", "firmware-linked")[0] == 0
    code, result = judge("linked", "firmware-complete")
    assert result["dimensions"]["link_closure"]["status"] == "satisfied"
    for profile in PROFILES:
        code, result = judge("unlinked", profile)
        assert code == 1
        assert result["dimensions"]["link_closure"]["evidence"]["unmapped_source_bindings"] == 1
