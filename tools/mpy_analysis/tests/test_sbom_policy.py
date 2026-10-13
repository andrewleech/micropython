"""Behavioural checks for the SBOM completeness policy gate."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from mpy_analysis import sbom, sbom_policy, scope
from mpy_analysis.firmware_receipt import receipt_id
from test_firmware_receipt import make_receipt
from test_sbom import MPY_ROOT, firmware_receipt, selection

PROFILES = ("firmware-complete", "firmware-linked")
KNOWN_FIELD = {"status": "known", "required": True, "evidence": [], "issues": []}


def make_summary(receipt):
    """A summary in which every requirement of every profile holds, bound to `receipt`."""
    image = receipt["images"][0]
    return {
        "schema_version": 1,
        "target": receipt["configuration"]["target"],
        "variant": receipt["configuration"]["variant"],
        "port": receipt["configuration"]["port"],
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
            "schema": receipt["schema"],
            "build_id": receipt["build_id"],
            "receipt_id": receipt["receipt_id"],
            "status": "complete",
            "images": [
                {
                    "id": image["id"],
                    "role": image["role"],
                    "elf_sha256": image["link"]["elf"]["sha256"],
                    "link_closure": "complete",
                    "linked_components": ["urn:mpy:file:1", "urn:mpy:file:2"],
                    "unmapped_source_bindings": [],
                }
            ],
        },
    }


def metadata_gap(field="license"):
    return {
        "kind": "dependency-metadata-unresolved",
        "component": "urn:mpy:dependency:1",
        "field": field,
        "status": "missing",
    }


def leave_metadata_unresolved(summary, field="license"):
    """Record a required metadata field the generator could not resolve."""
    summary["metadata_completeness"] = "incomplete"
    summary["coverage_status"] = "incomplete"
    summary["metadata"][0]["fields"][field] = {**KNOWN_FIELD, "status": "missing"}
    summary["gaps"].append(metadata_gap(field))


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


@pytest.fixture
def evidence(tmp_path):
    receipt, _ = make_receipt(tmp_path / "build-tree")
    summary = make_summary(receipt)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    return receipt, summary, receipt_path


def run_cli(tmp_path, summary, profile, receipt_path=None, capsys=None):
    summary_path = tmp_path / "sbom-summary.json"
    summary_path.write_text(json.dumps(summary))
    argv = ["--summary", str(summary_path), "--profile", profile]
    if receipt_path:
        argv += ["--receipt", str(receipt_path)]
    code = sbom_policy.main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("profile", PROFILES)
def test_complete_summary_satisfies_both_profiles(evidence, tmp_path, capsys, profile):
    receipt, summary, receipt_path = evidence

    code, result = run_cli(tmp_path, summary, profile, receipt_path, capsys)

    assert code == 0
    assert result["result"] == "satisfied" and result["unsatisfied"] == []
    assert result["profile"] == profile
    assert result["receipt"] == {
        "verified": True,
        "receipt_id": receipt["receipt_id"],
        "build_id": receipt["build_id"],
        "status": "complete",
    }
    assert set(result["dimensions"]) == set(sbom_policy.DIMENSIONS)
    assert all(
        row["status"] in ("satisfied", "not-applicable") for row in result["dimensions"].values()
    )
    link = result["dimensions"]["link_closure"]["evidence"]
    assert (link["images_complete"], link["images_total"]) == (1, 1)
    sources = result["dimensions"]["source_accounting"]["evidence"]
    assert (sources["selected_sources_accounted"], sources["selected_sources_total"]) == (2, 2)


def test_linked_profile_tolerates_metadata_gaps_that_fail_the_complete_profile(
    evidence, tmp_path, capsys
):
    _, summary, receipt_path = evidence
    leave_metadata_unresolved(summary, "license")
    leave_metadata_unresolved(summary, "version")

    linked_code, linked = run_cli(tmp_path, summary, "firmware-linked", receipt_path, capsys)
    complete_code, complete = run_cli(tmp_path, summary, "firmware-complete", receipt_path, capsys)

    assert linked_code == 0 and linked["result"] == "satisfied"
    assert linked["dimensions"]["metadata_completeness"]["status"] == "not-applicable"
    assert linked["dimensions"]["metadata_completeness"]["observed"] == "unsatisfied"
    assert linked["dimensions"]["gaps"]["evidence"]["total"] == 2
    assert complete_code == 1 and complete["result"] == "unsatisfied"
    assert complete["unsatisfied"] == ["gaps", "metadata_completeness"]
    gaps = complete["dimensions"]["gaps"]["evidence"]
    assert gaps["by_kind"] == {"dependency-metadata-unresolved": 2}
    assert gaps["by_kind_and_field"] == {
        "dependency-metadata-unresolved": {"license": 1, "version": 1}
    }
    metadata = complete["dimensions"]["metadata_completeness"]["evidence"]
    assert metadata["required_fields_total"] == 3 and metadata["required_fields_known"] == 1
    assert metadata["unresolved_required_fields"] == {"license": 1, "version": 1}


def test_complete_profile_fails_on_any_gap_even_with_resolved_metadata(evidence, tmp_path, capsys):
    _, summary, receipt_path = evidence
    summary["coverage_status"] = "incomplete"
    summary["gaps"].append({"kind": "declaration-applicability", "status": "unmatched"})

    code, result = run_cli(tmp_path, summary, "firmware-complete", receipt_path, capsys)

    assert code == 1
    assert result["unsatisfied"] == ["gaps"]
    assert result["dimensions"]["metadata_completeness"]["status"] == "satisfied"
    assert run_cli(tmp_path, summary, "firmware-linked", receipt_path, capsys)[0] == 0


def test_complete_profile_fails_when_selected_sources_are_unaccounted(evidence, tmp_path, capsys):
    _, summary, receipt_path = evidence
    summary["source_accounting"] = "incomplete"
    summary["coverage_status"] = "incomplete"
    summary["unassessed_groups"] = {"generated": 3}

    code, result = run_cli(tmp_path, summary, "firmware-complete", receipt_path, capsys)

    assert code == 1 and result["unsatisfied"] == ["source_accounting"]
    evidence_row = result["dimensions"]["source_accounting"]["evidence"]
    assert evidence_row["selected_sources_accounted"] == 2
    assert evidence_row["selected_sources_total"] == 5
    assert evidence_row["unassessed_groups"] == {"generated": 3}


@pytest.mark.parametrize("profile", PROFILES)
def test_unlinked_source_fails_every_profile_and_reports_the_image(
    evidence, tmp_path, capsys, profile
):
    _, summary, receipt_path = evidence
    leave_image_unlinked(summary)

    code, result = run_cli(tmp_path, summary, profile, receipt_path, capsys)

    assert code == 1
    assert "firmware_completeness" in result["unsatisfied"]
    assert "link_closure" in result["unsatisfied"]
    link = result["dimensions"]["link_closure"]["evidence"]
    assert link["status"] == "incomplete"
    assert (link["images_complete"], link["images_total"]) == (0, 1)
    assert link["unmapped_source_bindings"] == 1
    assert link["images"][0]["id"] == "application"


@pytest.mark.parametrize("profile", PROFILES)
def test_summary_without_a_receipt_never_satisfies_a_profile(evidence, tmp_path, capsys, profile):
    _, summary, _ = evidence
    summary["firmware_completeness"] = "not-established"
    summary["link_closure"] = {"status": "not-established"}

    code, result = run_cli(tmp_path, summary, profile, capsys=capsys)

    assert code == 1
    assert set(result["unsatisfied"]) >= {"firmware_completeness", "link_closure"}
    assert result["receipt"] is None


def test_posthoc_link_inventory_is_reported_as_unresolved_not_complete(evidence, tmp_path, capsys):
    _, summary, _ = evidence
    summary["firmware_completeness"] = "not-established"
    summary["coverage_status"] = "incomplete"
    summary["gaps"].append({"kind": "posthoc-firmware-link-inventory"})
    summary["link_closure"] = {
        "status": "posthoc-incomplete",
        "images": [
            {
                "id": "application",
                "linked_components": ["urn:mpy:file:1"],
                "unmapped_sources": [{"path": "a.c", "sha256": "0" * 64}],
                "unresolved_inputs": [{"path": "b.o"}, {"path": "c.o"}],
                "unassessed_objects": [{"path": "d.o"}],
            }
        ],
    }

    code, result = run_cli(tmp_path, summary, "firmware-linked", capsys=capsys)

    assert code == 1
    link = result["dimensions"]["link_closure"]["evidence"]
    assert link["unresolved_inputs"] == 2 and link["unmapped_source_bindings"] == 1
    assert link["images"][0]["unassessed_objects"] == 1


def inconsistent_established_with_incomplete_closure(summary):
    summary["link_closure"]["status"] = "incomplete"
    summary["link_closure"]["images"][0]["link_closure"] = "incomplete"


def inconsistent_established_with_blocking_gap(summary):
    summary["coverage_status"] = "incomplete"
    summary["gaps"].append({"kind": "dirty-selected-repository", "repository": "firmware"})


def inconsistent_established_without_receipt(summary):
    summary["link_closure"] = {"status": "not-established"}


def inconsistent_complete_closure_not_established(summary):
    summary["firmware_completeness"] = "not-established"


def inconsistent_complete_image_lists_unmapped(summary):
    summary["link_closure"]["images"][0]["unmapped_source_bindings"] = [
        {"root": "firmware", "path": "x.c"}
    ]


def inconsistent_unmapped_without_gap(summary):
    leave_image_unlinked(summary)
    summary["gaps"].clear()


def inconsistent_closure_status_disagrees_with_images(summary):
    summary["link_closure"]["images"][0]["link_closure"] = "incomplete"


def inconsistent_accounted_with_unassessed_groups(summary):
    summary["unassessed_groups"] = {"generated": 1}
    summary["coverage_status"] = "incomplete"


def inconsistent_coverage_accounted_with_gaps(summary):
    summary["gaps"].append({"kind": "declaration-applicability"})


def inconsistent_metadata_complete_with_gap(summary):
    summary["coverage_status"] = "incomplete"
    summary["gaps"].append(metadata_gap())


def inconsistent_metadata_complete_with_unknown_field(summary):
    summary["metadata"][0]["fields"]["version"]["status"] = "missing"


def inconsistent_boundary_resolved_with_issue(summary):
    summary["metadata"][0]["boundary_issues"] = [{"kind": "unresolved-identity"}]


INCONSISTENCIES = [
    inconsistent_established_with_incomplete_closure,
    inconsistent_established_with_blocking_gap,
    inconsistent_established_without_receipt,
    inconsistent_complete_closure_not_established,
    inconsistent_complete_image_lists_unmapped,
    inconsistent_unmapped_without_gap,
    inconsistent_closure_status_disagrees_with_images,
    inconsistent_accounted_with_unassessed_groups,
    inconsistent_coverage_accounted_with_gaps,
    inconsistent_metadata_complete_with_gap,
    inconsistent_metadata_complete_with_unknown_field,
    inconsistent_boundary_resolved_with_issue,
]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("corrupt", INCONSISTENCIES, ids=lambda fn: fn.__name__)
def test_inconsistent_summary_is_invalid_input_not_a_verdict(
    evidence, tmp_path, capsys, profile, corrupt
):
    _, summary, _ = evidence
    corrupt(summary)

    code, result = run_cli(tmp_path, summary, profile, capsys=capsys)

    assert code == 2
    assert result["result"] == "invalid" and result["errors"]
    assert "dimensions" not in result


@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_unknown_summary_schema_version_is_invalid(evidence, tmp_path, capsys, version):
    _, summary, _ = evidence
    summary["schema_version"] = version

    code, result = run_cli(tmp_path, summary, "firmware-linked", capsys=capsys)

    assert code == 2 and result["result"] == "invalid"
    assert "schema_version" in result["errors"][0]


def test_missing_schema_version_is_invalid(evidence, tmp_path, capsys):
    _, summary, _ = evidence
    del summary["schema_version"]

    assert run_cli(tmp_path, summary, "firmware-linked", capsys=capsys)[0] == 2


def test_unreadable_summary_and_unknown_profile_are_invalid(evidence, tmp_path, capsys):
    _, summary, _ = evidence
    assert sbom_policy.main(["--summary", str(tmp_path / "absent.json"), "--profile", "firmware-linked"]) == 2
    assert json.loads(capsys.readouterr().out)["result"] == "invalid"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert sbom_policy.main(["--summary", str(broken), "--profile", "firmware-linked"]) == 2
    capsys.readouterr()
    code, result = run_cli(tmp_path, summary, "firmware-everything", capsys=capsys)
    assert code == 2 and "unknown profile" in result["errors"][0]
    broken.write_text("[]")
    assert sbom_policy.main(["--summary", str(broken), "--profile", "firmware-linked"]) == 2


def other_receipt(tmp_path, **configuration):
    receipt, _ = make_receipt(tmp_path / "other-tree")
    receipt["configuration"].update(configuration)
    receipt["receipt_id"] = receipt_id(receipt)
    path = tmp_path / "other-receipt.json"
    path.write_text(json.dumps(receipt))
    return receipt, path


def test_receipt_from_another_tree_is_rejected(evidence, tmp_path, capsys):
    """Same build id, different recorded content: the summary names a different receipt_id."""
    _, summary, _ = evidence
    _, path = other_receipt(tmp_path, variables={"LTO": "0", "EXTRA": "1"})

    code, result = run_cli(tmp_path, summary, "firmware-linked", path, capsys)

    assert code == 2
    assert any("receipt_id" in error for error in result["errors"])


def test_receipt_for_another_target_is_rejected_even_with_matching_ids(evidence, tmp_path, capsys):
    receipt, summary, _ = evidence
    other, path = other_receipt(tmp_path, target="other-board")
    summary["link_closure"]["receipt_id"] = other["receipt_id"]

    code, result = run_cli(tmp_path, summary, "firmware-linked", path, capsys)

    assert code == 2
    assert any("target" in error for error in result["errors"])


def test_summary_elf_hash_must_match_the_receipt_image(evidence, tmp_path, capsys):
    _, summary, receipt_path = evidence
    summary["link_closure"]["images"][0]["elf_sha256"] = "9" * 64

    code, result = run_cli(tmp_path, summary, "firmware-linked", receipt_path, capsys)

    assert code == 2
    assert any("images" in error for error in result["errors"])


def test_summary_complete_claim_is_rejected_against_an_incomplete_receipt(
    evidence, tmp_path, capsys
):
    receipt, summary, _ = evidence
    receipt["status"] = "incomplete"
    receipt["receipt_id"] = receipt_id(receipt)
    summary["link_closure"]["receipt_id"] = receipt["receipt_id"]
    path = tmp_path / "incomplete-receipt.json"
    path.write_text(json.dumps(receipt))

    code, result = run_cli(tmp_path, summary, "firmware-linked", path, capsys)

    assert code == 2
    assert any("receipt status" in error for error in result["errors"])


def test_receipt_with_a_stale_artifact_is_rejected(evidence, tmp_path, capsys):
    _, summary, receipt_path = evidence
    (tmp_path / "build-tree" / "build" / "firmware.elf").write_bytes(b"rebuilt elf")

    code, result = run_cli(tmp_path, summary, "firmware-linked", receipt_path, capsys)

    assert code == 2
    assert any("stale" in error for error in result["errors"])


def test_corrupt_receipt_id_and_unreadable_receipt_are_rejected(evidence, tmp_path, capsys):
    receipt, summary, receipt_path = evidence
    tampered = copy.deepcopy(receipt)
    tampered["configuration"]["variant"] = "changed"
    receipt_path.write_text(json.dumps(tampered))
    code, result = run_cli(tmp_path, summary, "firmware-linked", receipt_path, capsys)
    assert code == 2 and any("receipt_id" in error for error in result["errors"])

    receipt_path.write_text("not json")
    assert run_cli(tmp_path, summary, "firmware-linked", receipt_path, capsys)[0] == 2
    assert run_cli(tmp_path, summary, "firmware-linked", tmp_path / "absent.json", capsys)[0] == 2


def test_receipt_cannot_back_a_summary_generated_without_one(evidence, tmp_path, capsys):
    _, summary, receipt_path = evidence
    summary["firmware_completeness"] = "not-established"
    summary["link_closure"] = {"status": "not-established"}

    code, result = run_cli(tmp_path, summary, "firmware-linked", receipt_path, capsys)

    assert code == 2
    assert any("not generated from a build receipt" in error for error in result["errors"])


def test_module_cli_reports_exit_status_and_json(evidence, tmp_path):
    _, summary, receipt_path = evidence
    leave_metadata_unresolved(summary)
    summary_path = tmp_path / "sbom-summary.json"
    summary_path.write_text(json.dumps(summary))
    tool = Path(sbom_policy.__file__).parents[1]

    def run(profile):
        return subprocess.run(
            [
                sys.executable, "-m", "mpy_analysis.sbom_policy", "--summary", str(summary_path),
                "--receipt", str(receipt_path), "--profile", profile,
            ],
            cwd=tool, capture_output=True, text=True,
        )

    linked, complete = run("firmware-linked"), run("firmware-complete")
    assert (linked.returncode, json.loads(linked.stdout)["result"]) == (0, "satisfied")
    assert (complete.returncode, json.loads(complete.stdout)["result"]) == (1, "unsatisfied")


def test_policy_judges_the_summary_the_generator_actually_writes(tmp_path, capsys):
    """Round trip through sbom.generate_sbom: a linked build passes linked, not complete."""
    inventory, app, _ = selection(tmp_path)
    database = app / "compile_commands.json"
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
    inventory["groups"]["native"] = scope.collect_compdb_sources(
        database, roots=inventory["roots"], owners={"app": "application", "library": "external"}
    )
    paths = {}
    for name, source in (("linked", "selected.c"), ("unlinked", "missing.c")):
        evidence_receipt = firmware_receipt(inventory, app, tmp_path / f"build-{name}", source)
        _, summary = sbom.generate_sbom(
            inventory, micropython_root=MPY_ROOT, build_receipt=evidence_receipt
        )
        (tmp_path / f"{name}-summary.json").write_text(json.dumps(summary))
        (tmp_path / f"{name}-receipt.json").write_text(json.dumps(evidence_receipt))
        paths[name] = (tmp_path / f"{name}-summary.json", tmp_path / f"{name}-receipt.json")

    def judge(name, profile):
        code = sbom_policy.main(
            ["--summary", str(paths[name][0]), "--receipt", str(paths[name][1]), "--profile", profile]
        )
        return code, json.loads(capsys.readouterr().out)

    assert judge("linked", "firmware-linked")[0] == 0
    code, result = judge("linked", "firmware-complete")
    assert code == 1 and result["dimensions"]["link_closure"]["status"] == "satisfied"
    for profile in PROFILES:
        code, result = judge("unlinked", profile)
        assert code == 1
        assert result["dimensions"]["link_closure"]["evidence"]["unmapped_source_bindings"] == 1
