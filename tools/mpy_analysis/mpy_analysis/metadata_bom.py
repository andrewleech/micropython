"""Offline, schema-validated upstream JSON BOM field assertions.

Source binding is evaluated by the metadata collector. SPDX package annotations
carry a JSON object with the same mpy:source:* keys as CycloneDX properties.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource


class UnsupportedBomError(ValueError):
    """A valid JSON object uses a BOM format/version outside supported parsers."""


def parse_json(text: str) -> Any:
    """Reject duplicate JSON keys instead of silently replacing assertions."""

    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON metadata key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique)


def schema_paths(document: Any) -> list[Path]:
    """Return offline schema inputs for a supported document/version."""
    if not isinstance(document, dict):
        return []
    if document.get("bomFormat") == "CycloneDX" and document.get("specVersion") == "1.6":
        names = ("bom-1.6.schema.json", "spdx.schema.json", "jsf-0.82.schema.json")
    elif document.get("spdxVersion") == "SPDX-2.3":
        names = ("spdx-2.3.schema.json",)
    else:
        return []
    directory = Path(__file__).parent / "data"
    return [directory / name for name in names]


def _schemas(names: tuple[str, ...]) -> tuple[list[dict], list[Path]]:
    directory = Path(__file__).parent / "data"
    paths = [directory / name for name in names]
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths], paths


def _validate(document: dict, names: tuple[str, ...]) -> list[Path]:
    schemas, paths = _schemas(names)
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    errors = sorted(
        Draft7Validator(schemas[0], registry=registry, format_checker=FormatChecker()).iter_errors(
            document
        ),
        key=lambda error: str(list(error.path)),
    )
    if errors:
        error = errors[0]
        raise ValueError(f"BOM schema validation failed at {list(error.path)}: {error.message}")
    return paths


def validate_cyclonedx(document: Any) -> list[Path]:
    """Validate the supported CycloneDX document against bundled offline schemas."""
    if not isinstance(document, dict) or not isinstance(document.get("specVersion"), str):
        raise ValueError("CycloneDX document requires a string specVersion")
    if document["specVersion"] != "1.6":
        raise UnsupportedBomError("supported CycloneDX version is 1.6")
    return _validate(document, ("bom-1.6.schema.json", "spdx.schema.json", "jsf-0.82.schema.json"))


def _properties(items: list[dict]) -> dict:
    values = {}
    for item in items:
        key = item["name"]
        if key in values:
            raise ValueError(f"duplicate upstream BOM property: {key}")
        values[key] = item["value"]
    return values


def _cyclonedx(document: dict) -> tuple[list[dict], list[Path]]:
    schemas = validate_cyclonedx(document)
    components = []
    by_ref = {}

    def visit(component: dict) -> None:
        reference = component.get("bom-ref")
        if reference and reference in by_ref:
            raise ValueError("duplicate CycloneDX bom-ref")
        fields = {
            field: component[field]
            for field in ("name", "version", "description")
            if field in component
        }
        issues = []
        assertions = []
        if component.get("author"):
            assertions.append(("author", component["author"]))
        if component.get("authors"):
            names = [item["name"] for item in component["authors"] if item.get("name")]
            if names:
                assertions.append(("author", ", ".join(names)))
        expressions = []
        texts = {}
        for value in component.get("licenses", []):
            if "expression" in value:
                expressions.append(value["expression"])
            else:
                license_data = value["license"]
                if "id" in license_data:
                    expressions.append(license_data["id"])
                text = license_data.get("text", {})
                identifier = license_data.get("name", "")
                if (
                    identifier.startswith("LicenseRef-")
                    and text.get("content")
                    and text.get("encoding") is None
                ):
                    texts[identifier] = text["content"]
                    expressions.append(identifier)
                elif identifier:
                    issues.append(
                        (
                            "license",
                            "named licence is not a validated SPDX identifier with retained text",
                        )
                    )
        if expressions:
            fields["license"] = " AND ".join(f"({value})" for value in expressions)
        for reference_data in component.get("externalReferences", []):
            if reference_data["type"] == "website":
                assertions.append(("url", reference_data["url"]))
        result = {
            "id": reference or component["name"],
            "fields": fields,
            "binding": _properties(component.get("properties", [])),
            "parser": "cyclonedx-upstream-1.6",
            "issues": issues,
            "assertions": assertions,
            "license_texts": texts,
            "dependencies": [],
        }
        components.append(result)
        if reference:
            by_ref[reference] = result
        for child in component.get("components", []):
            visit(child)

    main = document.get("metadata", {}).get("component")
    if main:
        visit(main)
    for component in document.get("components", []):
        visit(component)
    for edge in document.get("dependencies", []):
        owner = by_ref.get(edge["ref"])
        if owner:
            owner["dependencies"] = [
                by_ref[ref]["fields"].get("name", ref) if ref in by_ref else ref
                for ref in edge.get("dependsOn", [])
            ]
    return components, schemas


def _spdx(document: dict) -> tuple[list[dict], list[Path]]:
    if not isinstance(document.get("spdxVersion"), str):
        raise ValueError("SPDX document requires a string spdxVersion")
    if document["spdxVersion"] != "SPDX-2.3":
        raise UnsupportedBomError("supported upstream SPDX JSON version is 2.3")
    schemas = _validate(document, ("spdx-2.3.schema.json",))
    texts = {
        item["licenseId"]: item["extractedText"]
        for item in document.get("hasExtractedLicensingInfos", [])
    }
    if len(texts) != len(document.get("hasExtractedLicensingInfos", [])):
        raise ValueError("duplicate SPDX extracted licence identifier")
    components = []
    by_ref = {}
    identifiers = (
        [document["SPDXID"]]
        + [item["SPDXID"] for item in document.get("packages", [])]
        + [item["SPDXID"] for item in document.get("files", [])]
    )
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate SPDX element identifier")
    for package in document.get("packages", []):
        fields = {"name": package["name"]}
        for key, field in (
            ("versionInfo", "version"),
            ("summary", "description"),
            ("homepage", "url"),
            ("originator", "author"),
        ):
            value = package.get(key)
            if value and value not in ("NOASSERTION", "NONE"):
                fields[field] = value
        if "description" not in fields and package.get("description"):
            fields["description"] = package["description"]
        assertions = []
        for key in ("licenseDeclared", "licenseConcluded"):
            value = package.get(key)
            if value and value not in ("NOASSERTION", "NONE"):
                assertions.append(("license", value))
        binding = {}
        for annotation in package.get("annotations", []):
            comment = annotation.get("comment", "")
            if comment.lstrip().startswith("{"):
                try:
                    data = parse_json(comment)
                except ValueError as error:
                    if "mpy:source:" in comment:
                        raise ValueError(
                            f"invalid SPDX source-binding annotation: {error}"
                        ) from error
                    continue
                if isinstance(data, dict):
                    for key, value in data.items():
                        if key.startswith("mpy:source:"):
                            if key in binding:
                                raise ValueError("duplicate SPDX source binding annotation")
                            binding[key] = value
        result = {
            "id": package["SPDXID"],
            "fields": fields,
            "assertions": assertions,
            "binding": binding,
            "parser": "spdx-upstream-2.3",
            "issues": [],
            "license_texts": texts,
            "dependencies": [],
        }
        components.append(result)
        by_ref[package["SPDXID"]] = result
    for relationship in document.get("relationships", []):
        left, right = relationship["spdxElementId"], relationship["relatedSpdxElement"]
        if relationship["relationshipType"] == "DEPENDENCY_OF":
            left, right = right, left
        elif relationship["relationshipType"] != "DEPENDS_ON":
            continue
        if left in by_ref:
            by_ref[left]["dependencies"].append(
                by_ref[right]["fields"]["name"] if right in by_ref else right
            )
    return components, schemas


def read_bom(document: Any) -> tuple[list[dict], list[Path]]:
    """Return component candidates plus every offline schema file consumed."""
    if not isinstance(document, dict):
        raise ValueError("upstream BOM must be a JSON object")
    if document.get("bomFormat") == "CycloneDX":
        return _cyclonedx(document)
    if "spdxVersion" in document:
        return _spdx(document)
    raise UnsupportedBomError("unsupported upstream BOM format")
