"""CycloneDX 1.6 JSON written from the neutral SBOM model.

Mapping:

* The root component is ``metadata.component``. Every other component without a parent is a
  top-level ``components`` entry sorted by reference; components with a parent are nested under
  it, sorted by name, and carry no ``bom-ref``.
* Each key of ``depends_on`` becomes a ``dependencies`` entry, sorted by reference.
* Hashes are written in the order of ``_ALGORITHMS``, which puts SHA-1 before SHA-256.
* Properties are written as ``mpy:<name>``. The sonames in ``dynamic_links`` become one
  ``mpy:dynamic-needed`` property holding a JSON list, placed before the first property whose
  name sorts after it.
* A licence expression becomes ``licenses``; retained licence texts become
  ``evidence.licenses``.

The document is validated against the bundled CycloneDX 1.6 schema before it is returned.
"""

from __future__ import annotations

import json
from typing import Any

from .metadata_bom import validate_cyclonedx
from .sbom_model import Component, Contact, ExternalReference, Model, Party

_DYNAMIC_NEEDED = "dynamic-needed"

_ALGORITHMS = {
    "md5": "MD5",
    "sha1": "SHA-1",
    "sha256": "SHA-256",
    "sha384": "SHA-384",
    "sha512": "SHA-512",
    "sha3-256": "SHA3-256",
    "sha3-384": "SHA3-384",
    "sha3-512": "SHA3-512",
    "blake2b-256": "BLAKE2b-256",
    "blake2b-384": "BLAKE2b-384",
    "blake2b-512": "BLAKE2b-512",
    "blake3": "BLAKE3",
}


def _hashes(hashes: dict[str, str]) -> list[dict[str, str]]:
    unknown = set(hashes) - set(_ALGORITHMS)
    if unknown:
        raise ValueError(f"hash algorithm has no CycloneDX equivalent: {sorted(unknown)[0]}")
    return [
        {"alg": _ALGORITHMS[algorithm], "content": hashes[algorithm]}
        for algorithm in _ALGORITHMS
        if algorithm in hashes
    ]


def _contact(contact: Contact) -> dict[str, Any]:
    result = dict(contact.extra)
    for key in ("name", "email", "phone"):
        if getattr(contact, key) is not None:
            result[key] = getattr(contact, key)
    return result


def _party(party: Party) -> dict[str, Any]:
    result = dict(party.extra)
    if party.name is not None:
        result["name"] = party.name
    if party.urls:
        result["url"] = list(party.urls)
    if party.contacts:
        result["contact"] = [_contact(item) for item in party.contacts]
    return result


def _reference(reference: ExternalReference) -> dict[str, Any]:
    result: dict[str, Any] = {"type": reference.type, "url": reference.url}
    if reference.comment is not None:
        result["comment"] = reference.comment
    if reference.hashes:
        result["hashes"] = _hashes(reference.hashes)
    return result


def _properties(component: Component, soname: list[str] | None) -> list[dict[str, str]]:
    items = [{"name": f"mpy:{name}", "value": value} for name, value in component.properties.items()]
    if soname:
        needed = {"name": f"mpy:{_DYNAMIC_NEEDED}", "value": json.dumps(soname, sort_keys=True)}
        position = next(
            (index for index, item in enumerate(items) if item["name"] > needed["name"]), len(items)
        )
        items.insert(position, needed)
    return items


def _component(model: Model, component: Component, nested: dict[str, list[Component]]) -> dict[str, Any]:
    result: dict[str, Any] = {"type": component.kind, "name": component.name}
    if component.parent is None:
        result["bom-ref"] = component.ref
    for key, value in (
        ("version", component.version),
        ("purl", component.purl),
        ("cpe", component.cpe),
        ("group", component.group),
        ("author", component.author),
        ("publisher", component.publisher),
        ("description", component.description),
        ("copyright", component.copyright),
    ):
        if value:
            result[key] = value
    if component.hashes:
        result["hashes"] = _hashes(component.hashes)
    if component.supplier:
        result["supplier"] = _party(component.supplier)
    if component.license:
        result["licenses"] = [{"expression": component.license}]
    if component.license_texts:
        result["evidence"] = {
            "licenses": [
                {
                    "license": {
                        "name": name,
                        "text": {"contentType": "text/plain", "content": text},
                    }
                }
                for name, text in sorted(component.license_texts.items())
            ]
        }
    if component.external_references:
        result["externalReferences"] = [_reference(item) for item in component.external_references]
    properties = _properties(component, model.dynamic_links.get(component.ref))
    if properties:
        result["properties"] = properties
    children = nested.get(component.ref)
    if children:
        result["components"] = [
            _component(model, child, nested) for child in sorted(children, key=lambda item: item.name)
        ]
    return result


def to_cyclonedx(model: Model) -> dict[str, Any]:
    """Return the schema-validated CycloneDX 1.6 document for the model."""
    model.check()
    nested = model.nested()
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": _component(model, model.components[model.root], nested)},
        "components": [
            _component(model, model.components[ref], nested)
            for ref in sorted(model.components)
            if ref != model.root and model.components[ref].parent is None
        ],
        "dependencies": [
            {"ref": ref, "dependsOn": sorted(model.depends_on[ref])} for ref in sorted(model.depends_on)
        ],
    }
    validate_cyclonedx(document)
    return document


def serialize_cyclonedx(document: dict[str, Any]) -> bytes:
    """Serialise as sbom.json is written: sorted keys, indent 2, trailing newline."""
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
