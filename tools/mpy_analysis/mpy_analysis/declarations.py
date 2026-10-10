"""Offline CycloneDX declarations bound to selected physical dependency sources."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
from typing import Any

from .metadata import LICENSING, LICENSEREF, validate_license
from .metadata_bom import parse_json, validate_cyclonedx
from .scope import FROZEN_GROUP, source_path

PREFIX = "mpy:source:"
STRING_FIELDS = ("name", "version", "description", "author", "copyright")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _validate_corrections(corrections: Any) -> None:
    if not isinstance(corrections, dict):
        raise ValueError("manual corrections must be an object keyed by metadata field")
    for field, correction in corrections.items():
        if not isinstance(field, str) or (
            field not in (*STRING_FIELDS, "license", "license_applicability")
            and not (field.startswith("license_text:") and LICENSEREF.fullmatch(field[13:]))
        ):
            raise ValueError(f"unsupported manual correction field: {field}")
        if (
            not isinstance(correction, dict)
            or set(correction) != {"reason", "supersedes"}
            or not isinstance(correction["reason"], str)
            or not correction["reason"].strip()
        ):
            raise ValueError("manual correction requires a nonempty rationale and supersedes")
        supersedes = correction["supersedes"]
        if not isinstance(supersedes, list) or not supersedes:
            raise ValueError("manual correction must identify superseded evidence")
        pins = set()
        for item in supersedes:
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "sha256", "value"}
                or not isinstance(item["path"], str)
                or not item["path"].strip()
                or not isinstance(item["sha256"], str)
                or not SHA256.fullmatch(item["sha256"])
                or not isinstance(item["value"], str)
                or not item["value"].strip()
            ):
                raise ValueError(
                    "manual correction evidence requires exact path, sha256 and value"
                )
            pin = (item["path"], item["sha256"], item["value"])
            if pin in pins:
                raise ValueError("manual correction contains duplicate evidence")
            pins.add(pin)


def _retained_text(license_data: dict[str, Any]) -> tuple[str | None, str, str]:
    text = license_data.get("text")
    if not isinstance(text, dict) or not text.get("content"):
        return None, "missing", "custom licence requires retained nonempty text"
    content = text["content"]
    encoding = text.get("encoding")
    if encoding == "base64":
        try:
            content = base64.b64decode(content, validate=True).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeError):
            return None, "invalid", "custom licence text must be valid base64 containing UTF-8"
    elif encoding is not None:
        return None, "unsupported", f"unsupported custom licence text encoding: {encoding}"
    if not isinstance(content, str) or not content.strip():
        return None, "missing", "custom licence requires retained nonempty text"
    return content.strip(), "known", ""


def validate_document(data: Any) -> None:
    """Validate CycloneDX 1.6 without fetching external schema references."""
    try:
        validate_cyclonedx(data)
    except ValueError as error:
        raise ValueError(f"invalid CycloneDX declaration: {error}") from error


def source_binding(scope: dict[str, Any], root: str, relative: str) -> Path:
    if root not in scope["roots"] or not isinstance(relative, str) or not relative:
        raise ValueError("declaration requires a known source root and relative directory")
    parts = PurePosixPath(relative)
    if (
        parts.is_absolute()
        or ".." in parts.parts
        or "\\" in relative
        or PureWindowsPath(relative).drive
    ):
        raise ValueError("declaration source boundary must stay within its root")
    base = Path(scope["roots"][root]).resolve(strict=True)
    directory = (base / relative).resolve(strict=True)
    if not directory.is_dir() or not directory.is_relative_to(base):
        raise ValueError("declaration boundary must be a contained physical directory")
    return directory


def selected_digest(scope: dict[str, Any], directory: Path) -> tuple[str, int]:
    selected: dict[str, str] = {}
    count = 0
    for group in (FROZEN_GROUP, "native"):
        for record in scope["groups"].get(group, []):
            path = source_path(scope, record)
            if path.is_relative_to(directory):
                selected[path.relative_to(directory).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                count += 1
    payload = json.dumps(sorted(selected.items()), separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest(), count


def revision_matches_selected(scope: dict[str, Any], directory: Path) -> bool:
    """A Git binding is valid only for tracked selected bytes matching HEAD."""
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return False
    root = Path(result.stdout.strip()).resolve()
    paths = sorted(
        {
            source_path(scope, record).relative_to(root).as_posix()
            for group in (FROZEN_GROUP, "native")
            for record in scope["groups"].get(group, [])
            if source_path(scope, record).is_relative_to(directory)
        }
    )
    if not paths:
        return False
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", *paths], capture_output=True
    )
    unchanged = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", *paths], capture_output=True
    )
    return tracked.returncode == 0 and unchanged.returncode == 0


def _properties(component: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in component.get("properties", []):
        name = item["name"]
        if name in values:
            raise ValueError(f"duplicate declaration property: {name}")
        values[name] = item["value"]
    return values


def load_declarations(
    scope: dict[str, Any], paths: list[Path]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    declarations = []
    issues = []
    seen: set[Path] = set()
    for supplied in paths:
        path = Path(supplied).resolve(strict=True)
        raw = path.read_bytes()
        data = parse_json(raw.decode("utf-8-sig"))
        validate_document(data)
        for component in data.get("components", []):
            if component.get("components"):
                raise ValueError("declaration profile requires flat source-bound components")
            values = _properties(component)
            corrections = parse_json(values.get("mpy:metadata:corrections", "{}"))
            _validate_corrections(corrections)
            root, relative = values.get(PREFIX + "root"), values.get(PREFIX + "path")
            directory = source_binding(scope, root, relative)
            if directory in seen:
                raise ValueError(f"duplicate physical dependency declaration: {directory}")
            seen.add(directory)
            revision, expected_hash = (
                values.get(PREFIX + "revision"),
                values.get(PREFIX + "sha256"),
            )
            if bool(revision) == bool(expected_hash):
                raise ValueError(
                    "declaration requires exactly one revision or selected-content hash binding"
                )
            if expected_hash is not None and not SHA256.fullmatch(expected_hash):
                raise ValueError("declaration selected-content hash must be canonical SHA256")
            digest, count = selected_digest(scope, directory)
            result = subprocess.run(
                ["git", "-C", str(directory), "rev-parse", "HEAD"], capture_output=True, text=True
            )
            actual_revision = result.stdout.strip() if result.returncode == 0 else None
            binding_issue = None
            if not count:
                binding_issue = "unmatched"
            elif revision is not None and (
                revision != actual_revision or not revision_matches_selected(scope, directory)
            ):
                binding_issue = "stale"
            elif expected_hash is not None and expected_hash != digest:
                binding_issue = "stale"
            if binding_issue:
                issues.append(
                    {
                        "kind": "declaration-applicability",
                        "status": binding_issue,
                        "path": str(path),
                        "directory": str(directory),
                        "expected_revision": revision,
                        "actual_revision": actual_revision,
                        "expected_sha256": expected_hash,
                        "actual_sha256": digest,
                    }
                )
                continue
            evidence = {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "parser": "cyclonedx-manual-1.6",
                "revision": revision,
                "selected_sha256": digest,
            }
            fields = {}
            field_evidence = []
            component_issues = []
            license_texts = {}

            def assertion(field: str, value: Any, status: str = "known") -> None:
                field_evidence.append(
                    {
                        **evidence,
                        "field": field,
                        "value": value,
                        "status": status,
                        "origin": "manual",
                    }
                )

            def issue(field: str, status: str, detail: str, **extra: Any) -> None:
                row = {
                    "kind": "manual-metadata",
                    "field": field,
                    "status": status,
                    "path": str(path),
                    "directory": str(directory),
                    "detail": detail,
                    **extra,
                }
                component_issues.append(row)
                issues.append(row)

            for field in STRING_FIELDS:
                if field not in component:
                    continue
                value = component[field]
                if not isinstance(value, str) or not value.strip():
                    assertion(field, value, "unsupported")
                    issue(
                        field, "unsupported", "manual assertion must be a nonempty literal string"
                    )
                    continue
                fields[field] = value.strip()
                assertion(field, fields[field])

            expressions = []
            license_complete = True
            for item in component.get("licenses", []):
                if "expression" in item:
                    expression = validate_license(item["expression"])
                    if any(
                        LICENSEREF.fullmatch(symbol.key)
                        for symbol in LICENSING.license_symbols(expression)
                    ):
                        license_complete = False
                        issue(
                            "license",
                            "missing",
                            "Custom licences require a named LicenseRef with retained licence text.",
                        )
                    expressions.append(expression)
                    continue
                license_data = item["license"]
                if "id" in license_data:
                    expressions.append(validate_license(license_data["id"]))
                    continue
                identifier = license_data.get("name", "").strip()
                if not LICENSEREF.fullmatch(identifier):
                    license_complete = False
                    issue(
                        "license",
                        "unsupported",
                        "custom licence name must be a LicenseRef identifier",
                    )
                    continue
                expressions.append(validate_license(identifier))
                field = "license_text:" + identifier
                text, status, detail = _retained_text(license_data)
                assertion(field, text if text is not None else license_data.get("text"), status)
                if text is None:
                    license_complete = False
                    issue(field, status, detail)
                    issue("license", status, detail, cause_fields=[field])
                    continue
                prior = [
                    row["value"]
                    for row in field_evidence[:-1]
                    if row["field"] == field and row["status"] == "known"
                ]
                if any(value != text for value in prior):
                    license_complete = False
                    fields.pop(field, None)
                    license_texts.pop(identifier, None)
                    issue(
                        field,
                        "conflicting",
                        f"different retained texts use the same {identifier}",
                        cause_fields=[field],
                    )
                    issue(
                        "license",
                        "conflicting",
                        f"different retained texts use the same {identifier}",
                        cause_fields=[field],
                    )
                elif not any(
                    row["field"] == field and row["status"] == "conflicting"
                    for row in component_issues
                ):
                    fields[field] = text
                    license_texts[identifier] = {"text": text, "path": str(path)}
            if expressions and license_complete:
                fields["license"] = validate_license(
                    " AND ".join(f"({value})" for value in expressions)
                    if len(expressions) > 1
                    else expressions[0]
                )
                fields["license_applicability"] = "confirmed"
                assertion("license", fields["license"])
                assertion("license_applicability", "confirmed")
            declarations.append(
                {
                    "root": root,
                    "path": relative,
                    "directory": str(directory),
                    "fields": fields,
                    "field_evidence": field_evidence,
                    "license_texts": license_texts,
                    "issues": component_issues,
                    "component": component,
                    "corrections": corrections,
                    "declared_relationships": [
                        item
                        for item in data.get("dependencies", [])
                        if item["ref"] == component.get("bom-ref")
                    ],
                    "evidence": evidence,
                }
            )
    return declarations, issues


def _equivalent(field: str, left: Any, right: Any) -> bool:
    if field == "copyright" and isinstance(left, str) and isinstance(right, str):
        return {line.strip() for line in left.splitlines() if line.strip()} == {
            line.strip() for line in right.splitlines() if line.strip()
        }
    if field.startswith("license_text:") and isinstance(left, str) and isinstance(right, str):
        return left.strip() == right.strip()
    if field == "license" and isinstance(left, str) and isinstance(right, str):
        return LICENSING.is_equivalent(left, right)
    return left == right


def _text_evidence(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose retained upstream terms as identifier-specific facts with their provenance."""
    rows = []
    for item in metadata["evidence"]:
        identifier = item.get("identifier")
        if (
            item.get("field") == "license_text"
            and isinstance(identifier, str)
            and LICENSEREF.fullmatch(identifier)
        ):
            rows.append({**item, "field": "license_text:" + identifier})
        elif item.get("status") == "candidate" and isinstance(item.get("text"), str):
            path = Path(item.get("path", ""))
            if path.parent.name == "LICENSES" and LICENSEREF.fullmatch(path.stem):
                rows.append(
                    {
                        **item,
                        "field": "license_text:" + path.stem,
                        "value": item["text"],
                        "status": "known",
                    }
                )
    for identifier, item in metadata.get("license_texts", {}).items():
        field = "license_text:" + identifier
        if not any(row.get("field") == field for row in [*metadata["evidence"], *rows]):
            rows.append(
                {
                    "field": field,
                    "value": item["text"],
                    "path": item.get("path"),
                    "status": "known",
                }
            )
    return rows


def _selected_license_aggregate(metadata: dict[str, Any]) -> str | None:
    """Resolve complete selected-file coverage without treating each row as a package claim."""
    for row in metadata["evidence"]:
        if (
            row.get("field") == "license_applicability"
            and row.get("status") == "known"
            and row.get("detail") == "selected-file-annotations"
            and row.get("expressions")
        ):
            return validate_license(
                " AND ".join(f"({expression})" for expression in row["expressions"])
            )
    return None


def _license_candidates(metadata: dict[str, Any]) -> list[str]:
    """Retain expression candidates even when their custom text is unresolved."""
    values = [
        row["value"]
        for row in metadata["evidence"]
        if row.get("field") == "license"
        and row.get("status", "known") == "known"
        and row.get("applicability") != "selected-file"
        and isinstance(row.get("value"), str)
    ]
    if metadata["fields"].get("license"):
        values.append(metadata["fields"]["license"])
    aggregate = _selected_license_aggregate(metadata)
    if aggregate:
        values.append(aggregate)
    for issue in metadata["issues"]:
        if issue.get("field") == "license":
            values.extend(issue.get("values", []))
    return list(dict.fromkeys(values))


def merge_declaration(metadata: dict[str, Any], declaration: dict[str, Any]) -> dict[str, Any]:
    """Merge manual facts without silently replacing conflicting upstream assertions."""
    _validate_corrections(declaration["corrections"])
    license_candidates = _license_candidates(metadata)
    selected_license = _selected_license_aggregate(metadata)
    corrected_text = False
    result = {
        **metadata,
        "fields": dict(metadata["fields"]),
        "evidence": [*metadata["evidence"], *_text_evidence(metadata)],
        "issues": [*metadata["issues"], *declaration.get("issues", [])],
        "license_texts": dict(metadata.get("license_texts", {})),
    }
    for field in {
        row["field"]
        for row in result["evidence"]
        if row.get("field", "").startswith("license_text:")
    }:
        texts = [
            row
            for row in result["evidence"]
            if row.get("field") == field
            and row.get("status") == "known"
            and isinstance(row.get("value"), str)
            and row["value"].strip()
        ]
        if texts and all(_equivalent(field, texts[0]["value"], row["value"]) for row in texts):
            result["fields"][field] = texts[0]["value"]
        elif texts:
            result["fields"].pop(field, None)
            result["issues"].append(
                {
                    "field": field,
                    "status": "conflicting",
                    "reason": "retained upstream licence texts disagree",
                    "cause_fields": [field],
                }
            )
    result["evidence"].extend(
        row
        for row in declaration.get("field_evidence", [])
        if row.get("field") not in declaration["fields"] or row.get("status") != "known"
    )
    for field, value in declaration["fields"].items():
        prior = [
            item
            for item in result["evidence"]
            if item.get("field") == field
            and item.get("value") is not None
            and item.get("status", "known") == "known"
        ]
        upstream_value = result["fields"].get(field)
        if field == "copyright":
            aggregate = upstream_value
            if aggregate is None and prior:
                aggregate = "\n".join(item["value"] for item in prior)
            different = (
                prior if aggregate is not None and not _equivalent(field, aggregate, value) else []
            )
        elif field == "license":
            selected = [item for item in prior if item.get("applicability") == "selected-file"]
            different = [
                item
                for item in prior
                if item.get("applicability") != "selected-file"
                and not _equivalent(field, item["value"], value)
            ]
            aggregate = selected_license or (upstream_value if selected else None)
            if aggregate is not None and not _equivalent(field, aggregate, value):
                different.extend(selected)
        else:
            different = [item for item in prior if not _equivalent(field, item["value"], value)]
        field_differs = upstream_value is not None and not _equivalent(
            field, upstream_value, value
        )
        conflicts = [
            item
            for item in result["issues"]
            if item.get("field") == field and item.get("status") == "conflicting"
        ]
        conflict_causes = (
            [field]
            if different or field_differs
            else sorted(
                {cause for item in conflicts for cause in item.get("cause_fields", [field])}
            )
        )
        correction = declaration["corrections"].get(field)
        corrected = False
        evidence = {
            **declaration["evidence"],
            "field": field,
            "value": value,
            "status": "known",
            "origin": "manual",
        }
        if correction is not None:
            expected = {
                (item.get("path"), item.get("sha256"), item["value"]) for item in different
            }
            actual = {
                (item["path"], item["sha256"], item["value"]) for item in correction["supersedes"]
            }
            evidence["correction"] = correction
            if actual != expected or not expected:
                evidence["status"] = "stale"
                result["evidence"].append(evidence)
                result["issues"].append(
                    {
                        "kind": "manual-correction",
                        "field": field,
                        "status": "stale",
                        "reason": "correction evidence changed or no longer conflicts; refresh or retire correction",
                        "path": declaration["evidence"]["path"],
                        "expected_evidence": correction["supersedes"],
                        "current_evidence": [
                            {key: item.get(key) for key in ("path", "sha256", "value")}
                            for item in different
                        ],
                    }
                )
                if different or field_differs or conflicts:
                    result["fields"].pop(field, None)
                    result["issues"].append(
                        {
                            "field": field,
                            "status": "conflicting",
                            "reason": "manual and upstream assertions differ",
                            "path": declaration["evidence"]["path"],
                            "cause_fields": conflict_causes,
                        }
                    )
                continue
            corrected = True
        if corrected:
            issues = []
            for item in result["issues"]:
                if item.get("field") == field and item.get("status") == "conflicting":
                    continue
                causes = item.get("cause_fields", [])
                if (
                    field.startswith("license_text:")
                    and item.get("field") in {"license", "license_applicability"}
                    and field in causes
                ):
                    remaining = [cause for cause in causes if cause != field]
                    if not remaining:
                        continue
                    item = {**item, "cause_fields": remaining}
                issues.append(item)
            result["issues"] = issues
            if field.startswith("license_text:"):
                corrected_text = True
            result["fields"][field] = value
        elif different or field_differs or conflicts:
            result["fields"].pop(field, None)
            result["issues"].append(
                {
                    "field": field,
                    "status": "conflicting",
                    "reason": "manual and upstream assertions differ",
                    "path": declaration["evidence"]["path"],
                    "cause_fields": conflict_causes,
                }
            )
        else:
            result["fields"][field] = value
        result["evidence"].append(evidence)
    for field in declaration["corrections"].keys() - declaration["fields"].keys():
        result["issues"].append(
            {
                "kind": "manual-correction",
                "field": field,
                "status": "stale",
                "reason": "correction has no usable manual assertion; refresh or retire correction",
                "path": declaration["evidence"]["path"],
            }
        )
    text_fields = {
        row["field"]
        for row in result["evidence"]
        if row.get("field", "").startswith("license_text:")
    }
    for field in text_fields:
        identifier = field[13:]
        text = result["fields"].get(field)
        if isinstance(text, str) and text.strip():
            origin = next(
                (
                    row.get("path")
                    for row in reversed(result["evidence"])
                    if row.get("field") == field
                    and row.get("status") == "known"
                    and _equivalent(field, row["value"], text)
                ),
                None,
            )
            result["license_texts"][identifier] = {"text": text, "path": origin}
        else:
            result["license_texts"].pop(identifier, None)
        if any(
            issue.get("field") == field and issue.get("status") == "conflicting"
            for issue in result["issues"]
        ):
            result["fields"].pop(field, None)
            result["license_texts"].pop(identifier, None)
            result["fields"].pop("license", None)
            result["fields"].pop("license_applicability", None)
            result["issues"].append(
                {
                    "field": "license",
                    "status": "conflicting",
                    "reason": f"retained texts for {identifier} disagree",
                    "cause_fields": [field],
                }
            )
    if corrected_text:
        blocked = any(
            issue.get("field") == "license"
            and issue.get("status")
            in {"conflicting", "unsupported", "missing", "invalid", "unbound"}
            for issue in result["issues"]
        )
        if blocked:
            result["fields"].pop("license", None)
            result["fields"].pop("license_applicability", None)
        elif "license" not in result["fields"] and license_candidates:
            expression = license_candidates[0]
            if all(
                _equivalent("license", expression, value) for value in license_candidates
            ) and all(
                not LICENSEREF.fullmatch(symbol.key)
                or result["license_texts"].get(symbol.key, {}).get("text", "").strip()
                for symbol in LICENSING.license_symbols(expression)
            ):
                result["fields"]["license"] = expression
        if "license" in result["fields"] and not any(
            issue.get("field") == "license_applicability"
            and issue.get("status")
            in {"conflicting", "unsupported", "missing", "invalid", "unbound"}
            for issue in result["issues"]
        ):
            if metadata["fields"].get("license_applicability") == "confirmed" or any(
                row.get("field") == "license_applicability"
                and row.get("status") == "known"
                and row.get("value") == "confirmed"
                for row in result["evidence"]
            ):
                result["fields"]["license_applicability"] = "confirmed"
    if any(
        issue.get("field") == "license" and issue.get("status") == "conflicting"
        for issue in result["issues"]
    ):
        result["fields"].pop("license", None)
        result["fields"].pop("license_applicability", None)
    return result
