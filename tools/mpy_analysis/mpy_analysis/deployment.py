"""Deployment manifest (`mpy-deployment/v1`) and its verifier.

Link evidence says which sources a built firmware linked into its images. It says nothing
about what the device carries once deployed: which images are flashed, which files sit on the
filesystem, which packages or native modules are installed, whether anything is fetched at run
time. The consuming application owns that statement; this module defines its schema and checks
it against bytes on disk and, when given, the link evidence.

The verifier never infers deployment. A file is deployed only when a manifest member names it,
and a dynamic import or a directory listing never adds one. Each member is hashed from the file
named by its path (relative paths resolve against the manifest's directory). The deployment is
`established` only when

* every member file exists and hashes to its declared SHA-256,
* the manifest lists at least one `firmware-image` member, every `firmware-image` member is an
  image of the link evidence (named by `image`, or found by an exact artifact hash
  match) and its declared hash is that image's ELF hash or one of its deployment artifact hashes,
* every image of the link evidence is either bound by a member or excluded with a stated reason,
* `runtime_fetch` is declared and is `none` or `restricted`.

A `restricted` fetch carries the consumer's `immutable_by` statement (hash-pinning policy name and
the permitted sources). That statement is the application owner's declaration; the verifier does
not observe the device and reports it as declared. A manifest that cannot be parsed, names another
target, repeats an identifier or path, or excludes a firmware image is rejected with
`DeploymentError`; evidence that is merely missing or stale makes the result `not-established`
with the exact reasons.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from .metadata_bom import parse_json

SCHEMA = "mpy-deployment/v1"
ESTABLISHED = "established"
NOT_ESTABLISHED = "not-established"
NOT_DECLARED = "not-declared"
IMAGE_KINDS = ("firmware-image", "bootloader-image")
_SCHEMA_FILE = Path(__file__).parent / "data" / "deployment-v1.schema.json"
_BOOTLOADER_ROLE = "bootloader"


class DeploymentError(ValueError):
    """A deployment manifest that is invalid input rather than incomplete evidence."""


@dataclass(frozen=True)
class Deployment:
    """A schema-valid manifest with the directory its relative paths resolve against."""

    data: dict[str, Any]
    directory: Path
    sha256: str

    def resolve(self, path: str) -> Path:
        location = Path(path)
        return location if location.is_absolute() else self.directory / location


def load_deployment(path: Path) -> Deployment:
    """Read and validate a manifest file; raise DeploymentError when it is not valid input."""
    location = Path(path)
    try:
        raw = location.read_bytes()
        data = parse_json(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise DeploymentError(f"cannot read deployment manifest {location}: {exc}") from exc
    deployment = Deployment(
        data=_validate(data),
        directory=location.resolve().parent,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    _check_references(deployment)
    return deployment


def _validate(data: Any) -> dict[str, Any]:
    schema = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
    errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda error: list(map(str, error.path)))
    if errors:
        error = errors[0]
        where = "/".join(str(item) for item in error.path) or "<root>"
        raise DeploymentError(f"deployment manifest is not {SCHEMA} at {where}: {error.message}")
    return data


def _check_references(deployment: Deployment) -> None:
    data = deployment.data
    ids: set[str] = set()
    paths: dict[Path, str] = {}
    for member in data["members"]:
        if member["id"] in ids:
            raise DeploymentError(f"duplicate member id: {member['id']}")
        ids.add(member["id"])
        resolved = deployment.resolve(member["path"]).resolve(strict=False)
        if resolved in paths:
            raise DeploymentError(f"members {paths[resolved]} and {member['id']} name the same file: {resolved}")
        paths[resolved] = member["id"]
    bound = {member.get("image") for member in data["members"]} - {None}
    for exclusion in data.get("exclusions", []):
        if "path" in exclusion:
            resolved = deployment.resolve(exclusion["path"]).resolve(strict=False)
            if resolved in paths:
                kind = next(m["kind"] for m in data["members"] if m["id"] == paths[resolved])
                what = "firmware image member" if kind in IMAGE_KINDS else "member"
                raise DeploymentError(
                    f"exclusion names the {what} {paths[resolved]}; a deployed member cannot be excluded"
                )
        elif exclusion["image"] in bound:
            raise DeploymentError(
                f"exclusion names image {exclusion['image']!r}, which a member binds"
            )


def _hash_file(path: Path) -> tuple[str, str]:
    """SHA-256 and SHA-1 of a regular file; ValueError when the path is not a regular file."""
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not a regular file")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(block)
            sha1.update(block)
    return sha256.hexdigest(), sha1.hexdigest()


def _image_artifacts(link_evidence: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Per image id, the artifacts a deployed image file can legitimately be: ELF and deployment files."""
    result: dict[str, list[dict[str, str]]] = {}
    for image in link_evidence["images"]:
        seen = {(artifact["role"], artifact["path"], artifact["sha256"]) for artifact in image["artifacts"]}
        elf = image["elf"]
        seen.add(("elf", elf["path"], elf["sha256"]))
        result[image["id"]] = [
            {"role": role, "path": path, "sha256": digest}
            for role, path, digest in sorted(seen)
            if role in ("elf", "deployment")
        ]
    return result


def _bind_member(
    member: dict[str, Any],
    link_evidence: dict[str, Any] | None,
    artifacts: dict[str, list[dict[str, str]]],
    roles: dict[str, str],
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
    """The link evidence binding of an image member, its status, and the reasons it is not accepted."""
    reasons: list[dict[str, Any]] = []

    def reject(code: str, detail: str):
        reasons.append({"code": code, "member": member["id"], "detail": detail})
        return None, "rejected", reasons

    named = member.get("image")
    if link_evidence is None:
        if named is not None or member["kind"] == "firmware-image":
            return reject(
                "link-evidence-required",
                f"{member['kind']} member {member['id']} cannot be accounted without a link evidence",
            )
        return None, "unbound", reasons
    if named is not None:
        if named not in artifacts:
            return reject(
                "image-unknown",
                f"member {member['id']} names image {named!r}, which the link evidence does not contain",
            )
        candidates = [named]
    else:
        candidates = [
            image_id
            for image_id, items in artifacts.items()
            if any(item["sha256"] == member["sha256"] for item in items)
        ]
        if not candidates:
            if member["kind"] == "firmware-image":
                return reject(
                    "firmware-image-not-in-evidence",
                    f"firmware-image member {member['id']} matches no image or deployment artifact of the link evidence",
                )
            return None, "unbound", reasons
        if len(candidates) > 1:
            return reject(
                "image-ambiguous",
                f"member {member['id']} hash matches several images: {sorted(candidates)}",
            )
    image_id = candidates[0]
    is_bootloader = roles[image_id] == _BOOTLOADER_ROLE
    if is_bootloader != (member["kind"] == "bootloader-image"):
        return reject(
            "image-role-mismatch",
            f"{member['kind']} member {member['id']} is bound to image {image_id!r} "
            f"with role {roles[image_id]!r}",
        )
    matches = [item for item in artifacts[image_id] if item["sha256"] == member["sha256"]]
    if not matches:
        return reject(
            "image-hash-mismatch",
            f"member {member['id']} declares {member['sha256']}, which is neither the ELF hash nor a "
            f"deployment artifact hash of image {image_id!r}",
        )
    return (
        {
            "image": image_id,
            "artifact_role": matches[0]["role"],
            "artifact_path": matches[0]["path"],
            "bound_by": "image" if named is not None else "hash",
        },
        "bound",
        reasons,
    )


def _verify_member(deployment: Deployment, member: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resolved = deployment.resolve(member["path"])
    row: dict[str, Any] = {
        "id": member["id"],
        "kind": member["kind"],
        "path": member["path"],
        "resolved_path": str(resolved.resolve(strict=False)),
        "declared_sha256": member["sha256"],
        "actual_sha256": None,
        "actual_sha1": None,
    }
    reasons: list[dict[str, Any]] = []

    def fail(verdict: str, detail: str) -> None:
        row["digest"] = verdict
        reasons.append({"code": f"member-{verdict}", "member": member["id"], "detail": detail})

    try:
        row["actual_sha256"], row["actual_sha1"] = _hash_file(resolved)
    except FileNotFoundError:
        fail("missing", f"member {member['id']} file does not exist: {resolved}")
    except (IsADirectoryError, ValueError):
        fail("not-a-file", f"member {member['id']} path is not a regular file: {resolved}")
    except OSError as exc:
        fail("unreadable", f"member {member['id']} file cannot be read: {resolved}: {exc.strerror or exc}")
    else:
        if row["actual_sha256"] == member["sha256"]:
            row["digest"] = "match"
        else:
            fail(
                "hash-mismatch",
                f"member {member['id']} file hashes to {row['actual_sha256']}, "
                f"the manifest declares {member['sha256']}",
            )
    return row, reasons


def verify_deployment(
    deployment: Deployment, *, target: str, link_evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return the evidence block for a manifest, checked against the files and the link evidence.

    `link_evidence` is a loaded `mpy-link-evidence/v1` evidence, or None when none was supplied.
    Raises DeploymentError for a manifest for another target or one that excludes a firmware image.
    """
    data = deployment.data
    if data["target"] != target:
        raise DeploymentError(
            f"deployment manifest target {data['target']!r} does not match the SBOM target {target!r}"
        )
    artifacts = _image_artifacts(link_evidence) if link_evidence else {}
    roles = {image["id"]: image["role"] for image in link_evidence["images"]} if link_evidence else {}
    exclusions = data.get("exclusions", [])
    for exclusion in exclusions:
        image_id = exclusion.get("image")
        if image_id in roles and roles[image_id] != _BOOTLOADER_ROLE:
            raise DeploymentError(
                f"exclusion of image {image_id!r} (role {roles[image_id]!r}) is rejected: "
                "firmware image bytes cannot be left out of a deployment"
            )

    members: list[dict[str, Any]] = []
    reasons: list[dict[str, Any]] = []
    for member in data["members"]:
        row, member_reasons = _verify_member(deployment, member)
        reasons.extend(member_reasons)
        if member["kind"] in IMAGE_KINDS:
            binding, status, binding_reasons = _bind_member(member, link_evidence, artifacts, roles)
            row["image"] = member.get("image")
            row["image_binding"] = binding
            row["binding"] = status
            reasons.extend(binding_reasons)
        else:
            row["binding"] = "not-applicable"
        members.append(row)

    if not any(member["kind"] == "firmware-image" for member in data["members"]):
        reasons.append(
            {
                "code": "no-firmware-image-member",
                "member": None,
                "detail": "the manifest lists no firmware-image member, so no firmware bytes are accounted",
            }
        )

    accounted: dict[str, list[str]] = {image_id: [] for image_id in artifacts}
    for row in members:
        binding = row.get("image_binding")
        if binding:
            accounted[binding["image"]].append(row["id"])
    for exclusion in exclusions:
        if "image" in exclusion and accounted.get(exclusion["image"]):
            raise DeploymentError(
                f"exclusion names image {exclusion['image']!r}, "
                f"which member {accounted[exclusion['image']][0]} binds"
            )
        if "path" in exclusion:
            resolved = deployment.resolve(exclusion["path"]).resolve(strict=False)
            owners = [
                image_id
                for image_id, items in artifacts.items()
                if not accounted[image_id] and any(Path(item["path"]) == resolved for item in items)
            ]
            if owners:
                raise DeploymentError(
                    f"exclusion path {exclusion['path']!r} is an artifact of image {owners[0]!r}, "
                    "which no member binds; exclude the image by image instead"
                )
    excluded = {item["image"]: item["reason"] for item in exclusions if "image" in item}
    images = []
    for image in link_evidence["images"] if link_evidence else []:
        image_id = image["id"]
        row = {
            "id": image_id,
            "role": image["role"],
            "members": accounted[image_id],
            "excluded": image_id in excluded and not accounted[image_id],
            "exclusion_reason": excluded.get(image_id),
        }
        images.append(row)
        if not accounted[image_id] and image_id not in excluded:
            reasons.append(
                {
                    "code": "image-not-in-deployment",
                    "member": None,
                    "detail": f"image {image_id!r} is neither a deployment member nor excluded with a reason",
                }
            )
    for image_id in sorted(set(excluded) - set(roles)) if link_evidence else []:
        reasons.append(
            {
                "code": "exclusion-image-unknown",
                "member": None,
                "detail": f"exclusion names image {image_id!r}, which the link evidence does not contain",
            }
        )

    fetch = data.get("runtime_fetch")
    if fetch is None:
        mode = "undeclared"
        reasons.append(
            {
                "code": "runtime-fetch-undeclared",
                "member": None,
                "detail": "the manifest does not state whether the device fetches code or data at run time",
            }
        )
    else:
        mode = fetch["mode"]
        if mode == "unrestricted":
            reasons.append(
                {
                    "code": "runtime-fetch-unrestricted",
                    "member": None,
                    "detail": "the device may fetch unbounded content at run time",
                }
            )
    runtime_fetch = {"mode": mode, "basis": "declared by the manifest owner, not observed"}
    if fetch and "immutable_by" in fetch:
        runtime_fetch["immutable_by"] = fetch["immutable_by"]

    return {
        "schema": SCHEMA,
        "name": data["name"],
        "target": data["target"],
        "manifest_sha256": deployment.sha256,
        "status": NOT_ESTABLISHED if reasons else ESTABLISHED,
        "reasons": reasons,
        "members": members,
        "runtime_fetch": runtime_fetch,
        "exclusions": [dict(item) for item in exclusions],
        "link_evidence": None
        if link_evidence is None
        else {"evidence_id": link_evidence["evidence_id"]},
        "images": images,
    }
