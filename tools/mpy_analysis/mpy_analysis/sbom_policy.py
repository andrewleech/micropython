"""Evaluate a named completeness profile against an SBOM generation summary.

`mpy_analysis.sbom` reports what it could and could not account for in `sbom-summary.json` and
exits 0 for any valid partial result. This module is the separate gate: it reads only that summary
(and, optionally, the link evidence file the summary names), never the SBOM document, and
exits non-zero unless every requirement of the chosen profile holds.

Exit status: 0 profile satisfied, 1 a requirement is not satisfied, 2 unreadable or invalid input
(including an unknown summary schema version, an internally inconsistent summary, an unknown
profile, link evidence that is invalid or does not belong to the summary, or a deployment manifest
that is invalid or no longer verifies as the summary says). A JSON result is always printed on
standard output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Sequence

from .deployment import NOT_DECLARED, DeploymentError, load_deployment, verify_deployment
from .deployment import SCHEMA as DEPLOYMENT_SCHEMA
from .link_evidence import SCHEMA as EVIDENCE_SCHEMA
from .link_evidence import load_link_evidence
from .sbom import FIRMWARE_BLOCKING_GAPS

RESULT_SCHEMA = "mpy-sbom-policy/v1"
SUMMARY_SCHEMA_VERSIONS = frozenset({1})
SATISFIED, UNSATISFIED, NOT_APPLICABLE, INVALID = (
    "satisfied",
    "unsatisfied",
    "not-applicable",
    "invalid",
)
EXIT_CODES = {SATISFIED: 0, UNSATISFIED: 1, INVALID: 2}

_DIGEST = re.compile(r"[0-9a-f]{64}")
_LINK_STATUSES = {"complete", "incomplete", "not-established", "posthoc-incomplete"}
_IMAGE_STATUSES = {"complete", "incomplete"}
_DEPLOYMENT_STATUSES = {"established", "not-established", NOT_DECLARED}
_METADATA_GAP_KINDS = {"dependency-metadata-unresolved"}


class PolicyInputError(ValueError):
    """Input that cannot be judged; carries every problem found."""

    def __init__(self, errors: Sequence[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


# Each dimension reads the validated summary and returns (satisfied, evidence). The evidence holds
# the numbers behind the judgement so a failing result shows how far from complete the build is.
Dimension = Callable[[dict[str, Any]], tuple[bool, dict[str, Any]]]


def _count(items) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))


def _firmware_completeness(summary):
    blocking = _count(gap["kind"] for gap in summary["gaps"] if gap["kind"] in FIRMWARE_BLOCKING_GAPS)
    value = summary["firmware_completeness"]
    return value == "established", {"value": value, "blocking_gaps_by_kind": blocking}


def _link_closure(summary):
    closure = summary["link_closure"]
    images = closure.get("images", [])
    rows = [
        {
            "id": image["id"],
            "status": image.get("link_closure", closure["status"]),
            "linked_components": len(image["linked_components"]),
            "unmapped_source_bindings": len(image["unmapped_source_bindings"])
            if "unmapped_source_bindings" in image
            else len(image.get("unmapped_sources", [])),
            "unresolved_inputs": len(image.get("unresolved_inputs", [])),
            "unassessed_objects": len(image.get("unassessed_objects", [])),
        }
        for image in images
    ]
    complete = sum(1 for row in rows if row["status"] == "complete")
    evidence = {
        "status": closure["status"],
        "images_complete": complete,
        "images_total": len(rows),
        "unmapped_source_bindings": sum(row["unmapped_source_bindings"] for row in rows),
        "unresolved_inputs": sum(row["unresolved_inputs"] for row in rows),
        "images": rows,
    }
    return closure["status"] == "complete", evidence


def _source_accounting(summary):
    unassessed = summary["unassessed_groups"]
    accounted = len(summary["sources"])
    value = summary["source_accounting"]
    return value == "accounted", {
        "value": value,
        "selected_sources_accounted": accounted,
        "selected_sources_total": accounted + sum(unassessed.values()),
        "unassessed_groups": dict(sorted(unassessed.items())),
    }


def _gaps(summary):
    gaps = summary["gaps"]
    by_field: dict[str, dict[str, int]] = {}
    for kind in sorted({gap["kind"] for gap in gaps if gap.get("field")}):
        by_field[kind] = _count(gap["field"] for gap in gaps if gap["kind"] == kind and gap.get("field"))
    return not gaps, {
        "total": len(gaps),
        "by_kind": _count(gap["kind"] for gap in gaps),
        "by_kind_and_field": by_field,
    }


def _required_fields(summary):
    for row in summary["metadata"]:
        for name, field in row["fields"].items():
            if field.get("required"):
                yield row, name, field


def _metadata_completeness(summary):
    required = list(_required_fields(summary))
    unresolved = [(row, name, field) for row, name, field in required if field.get("status") != "known"]
    value = summary["metadata_completeness"]
    return value == "complete", {
        "value": value,
        "boundary_resolution": summary["boundary_resolution"],
        "components_total": len(summary["metadata"]),
        "components_with_unresolved_required_fields": len({row["component"] for row, _, _ in unresolved}),
        "required_fields_total": len(required),
        "required_fields_known": len(required) - len(unresolved),
        "unresolved_required_fields": _count(name for _, name, _ in unresolved),
        "unresolved_required_field_status": _count(field.get("status") for _, _, field in unresolved),
        "boundary_issues": sum(len(row.get("boundary_issues", [])) for row in summary["metadata"]),
    }


def _deployment(summary):
    value = summary.get("deployment_completeness", "not-declared")
    evidence: dict[str, Any] = {"value": value}
    block = summary.get("deployment")
    if block:
        evidence.update(
            {
                "members_total": len(block["members"]),
                "members_verified": sum(1 for row in block["members"] if row["digest"] == "match"),
                "reasons_by_code": _count(reason["code"] for reason in block["reasons"]),
                "runtime_fetch": block["runtime_fetch"]["mode"],
            }
        )
    return value == "established", evidence


# Adding a dimension is one entry here plus its name in the profiles.
DIMENSIONS: dict[str, Dimension] = {
    "firmware_completeness": _firmware_completeness,
    "link_closure": _link_closure,
    "source_accounting": _source_accounting,
    "gaps": _gaps,
    "metadata_completeness": _metadata_completeness,
    "deployment": _deployment,
}

PROFILES: dict[str, dict[str, Any]] = {
    "firmware-complete": {
        "description": "link closure established, every selected source accounted, no gap of any "
        "kind, required component metadata resolved",
        "require": (
            "firmware_completeness",
            "link_closure",
            "source_accounting",
            "gaps",
            "metadata_completeness",
        ),
    },
    "firmware-linked": {
        "description": "link closure established; metadata and other gaps are reported but do not fail",
        "require": ("firmware_completeness", "link_closure"),
    },
    "deployable": {
        "description": "firmware closure established and every deployment member verified, with "
        "runtime fetch declared none or restricted",
        "require": ("firmware_completeness", "deployment"),
    },
}


def _check_deployment(summary: dict[str, Any], errors: list[str]) -> None:
    value = summary.get("deployment_completeness", "not-declared")
    block = summary.get("deployment")
    if value not in _DEPLOYMENT_STATUSES:
        errors.append(
            f"deployment_completeness must be one of {sorted(_DEPLOYMENT_STATUSES)}, not {value!r}"
        )
        return
    if value == "not-declared":
        if block is not None:
            errors.append("deployment_completeness is not-declared but a deployment block exists")
        return
    if (
        not isinstance(block, dict)
        or block.get("schema") != DEPLOYMENT_SCHEMA
        or block.get("status") != value
        or not isinstance(block.get("members"), list)
        or not all(isinstance(row, dict) and "digest" in row for row in block["members"])
        or not isinstance(block.get("reasons"), list)
        or not all(isinstance(row, dict) and isinstance(row.get("code"), str) for row in block["reasons"])
        or not isinstance(block.get("runtime_fetch"), dict)
        or not isinstance(block["runtime_fetch"].get("mode"), str)
    ):
        errors.append(f"deployment block is missing or does not carry status {value!r}")
        return
    if value == "not-established":
        if not block["reasons"]:
            errors.append("deployment_completeness is not-established but the deployment block has no reason")
        return
    if block["reasons"]:
        errors.append("deployment_completeness is established but the deployment block lists reasons")
    if any(row["digest"] != "match" for row in block["members"]):
        errors.append("deployment_completeness is established but a member digest does not match")
    if block["runtime_fetch"]["mode"] not in ("none", "restricted"):
        errors.append("deployment_completeness is established but runtime fetch is not none or restricted")
    closure = summary.get("link_closure")
    evidence = block.get("link_evidence")
    if (
        not isinstance(evidence, dict)
        or not isinstance(closure, dict)
        or evidence.get("evidence_id") != closure.get("evidence_id")
    ):
        errors.append("deployment_completeness is established but is not bound to the summary's link evidence")


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_link_closure(closure: Any, gaps: list[dict[str, Any]], errors: list[str]) -> None:
    if not isinstance(closure, dict) or closure.get("status") not in _LINK_STATUSES:
        errors.append("link_closure.status must be one of " + ", ".join(sorted(_LINK_STATUSES)))
        return
    status = closure["status"]
    if status == "not-established":
        return
    images = closure.get("images")
    if not isinstance(images, list) or not images:
        errors.append(f"link_closure.status {status!r} requires a non-empty images list")
        return
    ids = [image.get("id") if isinstance(image, dict) else None for image in images]
    if not all(isinstance(item, str) and item for item in ids) or len(set(ids)) != len(ids):
        errors.append("link_closure.images need unique non-empty ids")
        return
    if status == "posthoc-incomplete":
        return
    if not isinstance(closure.get("evidence_id"), str) or not _DIGEST.fullmatch(closure["evidence_id"]):
        errors.append("link_closure.evidence_id must be a SHA-256 hex digest")
    if closure.get("schema") != EVIDENCE_SCHEMA:
        errors.append(f"link_closure.schema must be {EVIDENCE_SCHEMA!r}")
    unmapped_by_image = Counter(
        gap.get("image") for gap in gaps if gap["kind"] == "linked-source-not-in-selected-scope"
    )
    statuses = []
    for image in images:
        label = f"link_closure.images[{image['id']}]"
        if image.get("link_closure") not in _IMAGE_STATUSES:
            errors.append(f"{label}.link_closure must be complete or incomplete")
            continue
        statuses.append(image["link_closure"])
        if not isinstance(image.get("elf_sha256"), str) or not _DIGEST.fullmatch(image["elf_sha256"]):
            errors.append(f"{label}.elf_sha256 must be a SHA-256 hex digest")
        if not isinstance(image.get("linked_components"), list):
            errors.append(f"{label}.linked_components must be a list")
        unmapped = image.get("unmapped_source_bindings")
        if not isinstance(unmapped, list):
            errors.append(f"{label}.unmapped_source_bindings must be a list")
            continue
        if image["link_closure"] == "complete" and unmapped:
            errors.append(f"{label} is complete but lists unmapped source bindings")
        if unmapped_by_image.get(image["id"], 0) != len(unmapped):
            errors.append(
                f"{label} lists {len(unmapped)} unmapped source bindings but the gaps record "
                f"{unmapped_by_image.get(image['id'], 0)}"
            )
    if len(statuses) == len(images) and (status == "complete") != all(s == "complete" for s in statuses):
        errors.append(f"link_closure.status {status!r} disagrees with its images")


def validate_summary(summary: Any) -> dict[str, Any]:
    """Return the summary when it is a known schema and internally consistent, else raise."""
    if not isinstance(summary, dict):
        raise PolicyInputError(["summary must be a JSON object"])
    version = summary.get("schema_version")
    if isinstance(version, bool) or version not in SUMMARY_SCHEMA_VERSIONS:
        raise PolicyInputError(
            [
                f"unsupported summary schema_version {version!r}; "
                f"known: {sorted(SUMMARY_SCHEMA_VERSIONS)}"
            ]
        )
    errors: list[str] = []
    choices = {
        "status": {"generated"},
        "firmware_completeness": {"established", "not-established"},
        "coverage_status": {"incomplete", "selected-sources-accounted"},
        "source_accounting": {"accounted", "incomplete"},
        "metadata_completeness": {"complete", "incomplete"},
        "boundary_resolution": {"resolved", "incomplete"},
    }
    for key, allowed in choices.items():
        if summary.get(key) not in allowed:
            errors.append(f"{key} must be one of {sorted(allowed)}, not {summary.get(key)!r}")
    gaps = summary.get("gaps")
    if not isinstance(gaps, list) or not all(
        isinstance(gap, dict) and isinstance(gap.get("kind"), str) and gap["kind"] for gap in gaps
    ):
        errors.append("gaps must be a list of objects with a kind")
        gaps = []
    unassessed = summary.get("unassessed_groups")
    if not isinstance(unassessed, dict) or not all(_is_count(count) for count in unassessed.values()):
        errors.append("unassessed_groups must map group names to counts")
        unassessed = {}
    if not isinstance(summary.get("sources"), list):
        errors.append("sources must be a list")
    metadata = summary.get("metadata")
    if not isinstance(metadata, list) or not all(
        isinstance(row, dict) and isinstance(row.get("fields"), dict) for row in metadata
    ):
        errors.append("metadata must be a list of objects with a fields object")
        metadata = []
    _check_link_closure(summary.get("link_closure"), gaps, errors)
    _check_deployment(summary, errors)
    if errors:
        raise PolicyInputError(errors)

    closure = summary["link_closure"]
    blocking = [gap["kind"] for gap in gaps if gap["kind"] in FIRMWARE_BLOCKING_GAPS]
    established = summary["firmware_completeness"] == "established"
    if established and closure["status"] != "complete":
        errors.append(
            f"firmware_completeness is established but link_closure.status is {closure['status']!r}"
        )
    if established and blocking:
        errors.append(
            "firmware_completeness is established but blocking gaps exist: "
            + ", ".join(sorted(set(blocking)))
        )
    if not established and closure["status"] == "complete" and not blocking:
        errors.append("link_closure is complete with no blocking gaps but firmware_completeness is not established")
    if (summary["source_accounting"] == "accounted") != (not unassessed):
        errors.append("source_accounting disagrees with unassessed_groups")
    if (summary["coverage_status"] == "selected-sources-accounted") != (not gaps and not unassessed):
        errors.append("coverage_status disagrees with gaps and unassessed_groups")
    if summary["metadata_completeness"] == "complete":
        if any(gap["kind"] in _METADATA_GAP_KINDS for gap in gaps):
            errors.append("metadata_completeness is complete but metadata gaps exist")
        if any(field.get("status") != "known" for _, _, field in _required_fields(summary)):
            errors.append("metadata_completeness is complete but a required field is not known")
    if summary["boundary_resolution"] == "resolved" and any(row.get("boundary_issues") for row in metadata):
        errors.append("boundary_resolution is resolved but boundary issues exist")
    if errors:
        raise PolicyInputError(errors)
    return summary


def verify_deployment_file(
    summary: dict[str, Any], deployment_path: Path, link_evidence: dict[str, Any] | None
) -> None:
    """Check that the manifest still verifies the way the summary recorded it."""
    block = summary.get("deployment")
    if block is None:
        raise PolicyInputError(["summary was generated without a deployment manifest"])
    try:
        current = verify_deployment(
            load_deployment(deployment_path), target=summary["target"], link_evidence=link_evidence
        )
    except DeploymentError as exc:
        raise PolicyInputError([f"deployment manifest is not valid: {exc}"]) from exc
    errors = []
    for key in ("manifest_sha256", "status"):
        if current[key] != block[key]:
            errors.append(f"summary deployment.{key} {block[key]!r} does not match the manifest now: {current[key]!r}")
    if not errors and current["members"] != block["members"]:
        errors.append("summary deployment members differ from the files on disk now")
    if errors:
        raise PolicyInputError(errors)


def verify_evidence(summary: dict[str, Any], evidence_path: Path) -> dict[str, Any]:
    """Read the link evidence file and check that it is the one the summary was generated from."""
    try:
        evidence = load_link_evidence(evidence_path)
    except ValueError as exc:
        raise PolicyInputError([str(exc)]) from exc
    closure = summary["link_closure"]
    if closure["status"] not in ("complete", "incomplete"):
        raise PolicyInputError(
            [f"summary link_closure.status {closure['status']!r} was not generated from link evidence"]
        )
    errors = []
    for key in ("schema", "evidence_id"):
        if closure[key] != evidence[key]:
            errors.append(f"summary link_closure.{key} {closure[key]!r} does not match the link evidence {evidence[key]!r}")
    images = {image["id"]: image["elf"]["sha256"] for image in evidence["images"]}
    summary_images = {image["id"]: image["elf_sha256"] for image in closure["images"]}
    if images != summary_images:
        errors.append(
            "summary images do not match the link evidence images: "
            f"summary {sorted(summary_images.items())}, evidence {sorted(images.items())}"
        )
    if errors:
        raise PolicyInputError(errors)
    return evidence


def evaluate(
    summary: dict[str, Any], profile: str, evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Judge a validated summary against a profile and return the machine-readable result."""
    required = set(PROFILES[profile]["require"])
    dimensions = {}
    for name, judge in DIMENSIONS.items():
        satisfied, detail = judge(summary)
        observed = SATISFIED if satisfied else UNSATISFIED
        dimensions[name] = {
            "required": name in required,
            "status": observed if name in required else NOT_APPLICABLE,
            "observed": observed,
            "evidence": detail,
        }
    failed = [name for name in DIMENSIONS if dimensions[name]["status"] == UNSATISFIED]
    closure = summary["link_closure"]
    return {
        "schema": RESULT_SCHEMA,
        "profile": profile,
        "result": UNSATISFIED if failed else SATISFIED,
        "unsatisfied": failed,
        "subject": {
            key: summary.get(key) for key in ("target", "variant", "port", "scope_sha256")
        }
        | {"evidence_id": closure.get("evidence_id")},
        "link_evidence": None
        if evidence is None
        else {"verified": True, "evidence_id": evidence["evidence_id"]},
        "dimensions": dimensions,
    }


def _invalid(profile: str | None, errors: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "profile": profile,
        "result": INVALID,
        "errors": list(errors),
    }


def run(
    summary_path: Path,
    profile: str,
    evidence_path: Path | None = None,
    deployment_path: Path | None = None,
) -> dict[str, Any]:
    """Return the policy result for a summary file; `result` is satisfied, unsatisfied or invalid."""
    if profile not in PROFILES:
        return _invalid(profile, [f"unknown profile {profile!r}; known: {sorted(PROFILES)}"])
    try:
        try:
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PolicyInputError([f"cannot read summary {summary_path}: {exc}"]) from exc
        validate_summary(summary)
        evidence = verify_evidence(summary, evidence_path) if evidence_path else None
        if deployment_path:
            verify_deployment_file(summary, deployment_path, evidence)
    except PolicyInputError as exc:
        return _invalid(profile, exc.errors)
    return evaluate(summary, profile, evidence)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--summary", type=Path, required=True, help="sbom-summary.json from mpy_analysis.sbom")
    parser.add_argument(
        "--link-evidence",
        type=Path,
        help="mpy-link-evidence/v1 file; matched to the summary when given",
    )
    parser.add_argument(
        "--deployment",
        type=Path,
        help="mpy-deployment/v1 manifest; re-verified against the files on disk and the summary when given",
    )
    parser.add_argument("--profile", required=True, help="profile name: " + ", ".join(sorted(PROFILES)))
    args = parser.parse_args(argv)
    result = run(args.summary, args.profile, args.link_evidence, args.deployment)
    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_CODES[result["result"]]


if __name__ == "__main__":
    sys.exit(main())
