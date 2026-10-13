import json

import pytest
from mpy_analysis.sbom_cyclonedx import serialize_cyclonedx, to_cyclonedx
from mpy_analysis.sbom_model import Component, Contact, ExternalReference, Party, property_values
from mpy_analysis.sbom_spdx import to_spdx
from test_sbom_spdx import (
    APP,
    FIRMWARE,
    LIBFFI_A,
    LIBFFI_SO,
    LICENSE_TEXT,
    REPO,
    SHA1_B,
    SHA256_B,
    SYSTEM,
    VENDOR,
    sample_model,
)


def flatten(document):
    result = [document["metadata"]["component"]]

    def visit(component):
        result.append(component)
        for child in component.get("components", []):
            visit(child)

    for component in document["components"]:
        visit(component)
    return result


def properties(component):
    return {item["name"]: item["value"] for item in component.get("properties", [])}


def test_document_shape_and_ordering():
    document = to_cyclonedx(sample_model())
    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == "1.6"
    assert document["version"] == 1
    root = document["metadata"]["component"]
    assert (root["bom-ref"], root["type"], root["name"]) == (APP, "application", "x86_64-linux-gnu")
    refs = [item["bom-ref"] for item in document["components"]]
    assert refs == sorted(refs)
    assert APP not in refs
    assert [item["ref"] for item in document["dependencies"]] == sorted(
        item["ref"] for item in document["dependencies"]
    )
    edges = {item["ref"]: item["dependsOn"] for item in document["dependencies"]}
    assert edges[FIRMWARE] == sorted(edges[FIRMWARE])
    assert edges[APP] == sorted([FIRMWARE, REPO])
    assert edges[SYSTEM] == []


def test_nested_components_have_no_bom_ref_and_sort_by_name():
    document = to_cyclonedx(sample_model())
    system = next(item for item in document["components"] if item["bom-ref"] == SYSTEM)
    assert [item["name"] for item in system["components"]] == [
        "/usr/lib/libffi.a",
        "/usr/lib/libffi.so.8.1.4",
    ]
    assert all("bom-ref" not in item for item in system["components"])
    assert LIBFFI_SO not in {item.get("bom-ref") for item in flatten(document)}
    assert LIBFFI_A not in {item["ref"] for item in document["dependencies"]}


def test_hashes_are_written_sha1_first():
    document = to_cyclonedx(sample_model())
    firmware = next(item for item in document["components"] if item["bom-ref"] == FIRMWARE)
    assert firmware["hashes"] == [
        {"alg": "SHA-1", "content": SHA1_B},
        {"alg": "SHA-256", "content": SHA256_B},
    ]


def test_unknown_hash_algorithm_is_rejected():
    model = sample_model()
    model.components[REPO].hashes = {"crc32": "00"}
    with pytest.raises(ValueError, match="no CycloneDX equivalent"):
        to_cyclonedx(model)


def test_properties_carry_the_mpy_prefix_and_dynamic_links_become_one_property():
    document = to_cyclonedx(sample_model())
    firmware = next(item for item in document["components"] if item["bom-ref"] == FIRMWARE)
    assert [item["name"] for item in firmware["properties"]] == [
        "mpy:dynamic-needed",
        "mpy:image-role",
        "mpy:link-closure",
    ]
    assert json.loads(properties(firmware)["mpy:dynamic-needed"]) == ["libffi.so.8", "libc.so.6"]


def test_dynamic_needed_is_placed_in_sorted_position():
    model = sample_model()
    model.components[FIRMWARE].properties = property_values(
        {"build-id": "b", "image-role": "application", "zeta": "z"}
    )
    firmware = next(
        item for item in to_cyclonedx(model)["components"] if item["bom-ref"] == FIRMWARE
    )
    assert [item["name"] for item in firmware["properties"]] == [
        "mpy:build-id",
        "mpy:dynamic-needed",
        "mpy:image-role",
        "mpy:zeta",
    ]


def test_licences_and_retained_texts():
    document = to_cyclonedx(sample_model())
    components = {item["name"]: item for item in document["components"]}
    assert components["micropython"]["licenses"] == [{"expression": "MIT"}]
    assert "licenses" not in components["unix"]
    assert components["vendor-lib"]["licenses"] == [{"expression": "LicenseRef-vendor"}]
    assert components["vendor-lib"]["evidence"] == {
        "licenses": [
            {
                "license": {
                    "name": "LicenseRef-vendor",
                    "text": {"contentType": "text/plain", "content": LICENSE_TEXT},
                }
            }
        ]
    }


def test_supplier_and_references_are_written_back():
    model = sample_model()
    component = model.components[VENDOR]
    component.supplier = Party(
        name="Vendor Inc",
        urls=["https://vendor.invalid"],
        contacts=[Contact(name="Ann", email="a@example.invalid", phone="1", extra={"bom-ref": "c"})],
        extra={"bom-ref": "org"},
    )
    component.external_references = [
        ExternalReference("website", "https://vendor.invalid", comment="home", hashes={"sha256": SHA256_B})
    ]
    written = next(
        item for item in to_cyclonedx(model)["components"] if item["bom-ref"] == VENDOR
    )
    assert written["supplier"] == {
        "bom-ref": "org",
        "name": "Vendor Inc",
        "url": ["https://vendor.invalid"],
        "contact": [{"bom-ref": "c", "name": "Ann", "email": "a@example.invalid", "phone": "1"}],
    }
    assert written["externalReferences"] == [
        {
            "type": "website",
            "url": "https://vendor.invalid",
            "comment": "home",
            "hashes": [{"alg": "SHA-256", "content": SHA256_B}],
        }
    ]
    assert written["publisher"] == "Vendor Publisher"
    assert written["group"] == "org.vendor"
    assert written["cpe"] == "cpe:2.3:a:vendor:lib:1:*:*:*:*:*:*:*"


def test_empty_optional_fields_are_omitted():
    model = sample_model()
    del model.components[LIBFFI_SO], model.components[LIBFFI_A]
    model.components[SYSTEM] = Component(kind="library", ref=SYSTEM, name="bare")
    written = next(item for item in to_cyclonedx(model)["components"] if item["bom-ref"] == SYSTEM)
    assert written == {"type": "library", "name": "bare", "bom-ref": SYSTEM}


def test_serialisation_is_sorted_indented_json():
    document = to_cyclonedx(sample_model())
    data = serialize_cyclonedx(document)
    assert data == (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def test_schema_failures_are_rejected():
    model = sample_model()
    model.components[REPO].external_references = [
        ExternalReference("website", "https://example.invalid/invalid url")
    ]
    with pytest.raises(ValueError):
        to_cyclonedx(model)


def test_both_writers_carry_the_same_components_hashes_and_edges():
    model = sample_model()
    cyclonedx = to_cyclonedx(model)
    spdx = to_spdx(model)
    placeholders = {name for sonames in model.dynamic_links.values() for name in sonames}

    cdx_components = flatten(cyclonedx)
    spdx_elements = [
        *(item for item in spdx["packages"] if item["name"] not in placeholders),
        *spdx["files"],
    ]
    cdx_names = sorted(item["name"] for item in cdx_components)
    spdx_names = sorted(item.get("name", item.get("fileName")) for item in spdx_elements)
    assert cdx_names == spdx_names == sorted(item.name for item in model.components.values())

    cdx_hashes = {
        item["name"]: {
            entry["alg"].lower().replace("sha-", "sha"): entry["content"]
            for entry in item.get("hashes", [])
        }
        for item in cdx_components
    }
    spdx_hashes = {
        item.get("name", item.get("fileName")): {
            entry["algorithm"].lower(): entry["checksumValue"] for entry in item.get("checksums", [])
        }
        for item in spdx_elements
    }
    assert cdx_hashes == spdx_hashes
    assert cdx_hashes == {item.name: item.hashes for item in model.components.values()}

    by_ref = {item.ref: item.name for item in model.components.values()}
    cdx_edges = {
        (by_ref[edge["ref"]], by_ref[target])
        for edge in cyclonedx["dependencies"]
        for target in edge["dependsOn"]
    }
    spdx_names_by_id = {
        item["SPDXID"]: item.get("name", item.get("fileName")) for item in spdx_elements
    }
    spdx_edges = {
        (spdx_names_by_id[item["spdxElementId"]], spdx_names_by_id[item["relatedSpdxElement"]])
        for item in spdx["relationships"]
        if item["relationshipType"] == "DEPENDS_ON"
    }
    assert cdx_edges == spdx_edges
    assert cdx_edges == {
        (by_ref[ref], by_ref[target]) for ref, targets in model.depends_on.items() for target in targets
    }

    cdx_needed = {
        by_ref[item["bom-ref"]]: json.loads(properties(item)["mpy:dynamic-needed"])
        for item in cdx_components
        if "bom-ref" in item and "mpy:dynamic-needed" in properties(item)
    }
    spdx_links = {}
    for item in spdx["relationships"]:
        if item["relationshipType"] == "DYNAMIC_LINK":
            spdx_links.setdefault(spdx_names_by_id[item["spdxElementId"]], set()).add(
                next(
                    package["name"]
                    for package in spdx["packages"]
                    if package["SPDXID"] == item["relatedSpdxElement"]
                )
            )
    assert {name: set(values) for name, values in cdx_needed.items()} == spdx_links
