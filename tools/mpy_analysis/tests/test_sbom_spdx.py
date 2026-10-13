import copy
import json
from collections import Counter

import pytest
from mpy_analysis import metadata_bom, sbom_spdx

SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64
SHA1_B = "1" * 40
LICENSE_TEXT = "Synthetic vendor licence terms."

APP = "urn:mpy:build:app"
FIRMWARE = "urn:mpy:firmware-image:fw"
REPO = "urn:mpy:repository:repo"
SYSTEM = "urn:mpy:system-package:libffi8"
SOURCE_FILE = "urn:mpy:file:main"
VENDOR = "urn:mpy:dependency:vendor"


def props(**values):
    return [{"name": name, "value": value} for name, value in values.items()]


def cyclonedx():
    application = {
        "type": "application",
        "bom-ref": APP,
        "name": "x86_64-linux-gnu",
        "properties": props(**{"mpy:port": "unix", "mpy:variant": "standard"}),
    }
    components = [
        {
            "type": "file",
            "bom-ref": SOURCE_FILE,
            "name": "py/main.c",
            "hashes": [{"alg": "SHA-256", "content": SHA256_A}],
            "copyright": "Copyright Example",
            "properties": props(
                **{"mpy:owner": "core", "mpy:path": "py/main.c", "mpy:selection": "native"}
            ),
        },
        {
            "type": "library",
            "bom-ref": REPO,
            "name": "micropython",
            "version": "1.2.3",
            "purl": "pkg:github/micropython/micropython@1.2.3",
            "scope": "required",
            "externalReferences": [{"type": "vcs", "url": "https://github.com/m/m.git"}],
            "licenses": [{"license": {"id": "MIT"}}],
            "properties": props(
                **{
                    "mpy:revision": "abc",
                    "mpy:source:root": ".",
                    "mpy:source:path": "py",
                    "mpy:source:revision": "abc",
                }
            ),
        },
        {
            "type": "library",
            "bom-ref": VENDOR,
            "name": "vendor-lib",
            "description": "Vendor library",
            "supplier": {"name": "Vendor Inc", "contact": [{"email": "a@example.invalid"}]},
            "licenses": [
                {"license": {"name": "LicenseRef-vendor", "text": {"content": LICENSE_TEXT}}}
            ],
            "evidence": {"identity": {"field": "name", "confidence": 1}},
            "properties": props(**{"mpy:boundary": "vendor", "x:other": "kept"}),
        },
        {
            "type": "library",
            "bom-ref": SYSTEM,
            "name": "libffi8",
            "version": "3.4.6",
            "purl": "pkg:deb/ubuntu/libffi8@3.4.6",
            "scope": "optional",
            "components": [
                {
                    "type": "file",
                    "name": "/usr/lib/libffi.so.8.1.4",
                    "hashes": [
                        {"alg": "SHA-256", "content": SHA256_B},
                        {"alg": "SHA-1", "content": SHA1_B},
                    ],
                    "properties": props(**{"mpy:linkage": "dynamic"}),
                },
                {
                    "type": "file",
                    "name": "/usr/lib/libffi.a",
                    "hashes": [{"alg": "SHA-256", "content": SHA256_C}],
                },
            ],
            "properties": props(**{"mpy:package-manager": "dpkg"}),
        },
        {
            "type": "firmware",
            "bom-ref": FIRMWARE,
            "name": "unix",
            "hashes": [
                {"alg": "SHA-256", "content": SHA256_B},
                {"alg": "SHA-1", "content": SHA1_B},
            ],
            "properties": props(
                **{
                    "mpy:image-role": "application",
                    "mpy:link-closure": "complete",
                    "mpy:dynamic-needed": json.dumps(["libffi.so.8", "libc.so.6"]),
                }
            ),
        },
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": application},
        "components": components,
        "dependencies": [
            {"ref": APP, "dependsOn": [FIRMWARE, REPO]},
            {"ref": FIRMWARE, "dependsOn": [SOURCE_FILE, SYSTEM, VENDOR]},
            {"ref": REPO, "dependsOn": [SOURCE_FILE]},
            {"ref": SYSTEM, "dependsOn": []},
            {"ref": VENDOR, "dependsOn": []},
            {"ref": SOURCE_FILE, "dependsOn": []},
        ],
    }


def convert(document=None):
    return sbom_spdx.cyclonedx_to_spdx(cyclonedx() if document is None else document)


def by_name(spdx):
    return {item["name"]: item for item in spdx["packages"]}


def annotation_objects(element):
    return [json.loads(item["comment"]) for item in element.get("annotations", [])]


def merged_annotations(element):
    merged = {}
    for item in annotation_objects(element):
        assert not set(merged) & set(item)
        merged.update(item)
    return merged


def test_output_is_schema_valid_spdx_23():
    spdx = convert()
    sbom_spdx.validate_spdx(spdx)
    metadata_bom._validate(spdx, ("spdx-2.3.schema.json",))
    assert spdx["spdxVersion"] == "SPDX-2.3"
    assert spdx["dataLicense"] == "CC0-1.0"
    assert spdx["SPDXID"] == "SPDXRef-DOCUMENT"
    assert spdx["name"] == "x86_64-linux-gnu"
    assert all(
        sbom_spdx._SPDXID.fullmatch(item["SPDXID"]) for item in spdx["packages"] + spdx["files"]
    )


def test_every_component_is_a_package_or_file():
    spdx = convert()
    document = cyclonedx()
    count = 1 + len(document["components"]) + 2
    # Application root and four top-level packages, one top-level file, two nested files,
    # plus two dynamic-link placeholders.
    assert len(spdx["packages"]) + len(spdx["files"]) == count + 2
    assert {item["fileName"] for item in spdx["files"]} == {
        "py/main.c",
        "/usr/lib/libffi.so.8.1.4",
        "/usr/lib/libffi.a",
    }
    purposes = {item["name"]: item["primaryPackagePurpose"] for item in spdx["packages"]}
    assert purposes["unix"] == "FIRMWARE"
    assert purposes["micropython"] == "LIBRARY"
    assert purposes["x86_64-linux-gnu"] == "APPLICATION"


def test_relationship_types_and_counts():
    spdx = convert()
    packages = by_name(spdx)
    files = {item["fileName"]: item for item in spdx["files"]}
    ids = {name: item["SPDXID"] for name, item in packages.items()}
    relationships = {
        (item["spdxElementId"], item["relationshipType"], item["relatedSpdxElement"])
        for item in spdx["relationships"]
    }
    assert Counter(item["relationshipType"] for item in spdx["relationships"]) == {
        "DESCRIBES": 1,
        "DEPENDS_ON": 6,
        "CONTAINS": 3,
        "DYNAMIC_LINK": 2,
    }
    assert len(relationships) == len(spdx["relationships"])
    assert ("SPDXRef-DOCUMENT", "DESCRIBES", ids["x86_64-linux-gnu"]) in relationships
    # Each CycloneDX edge is one DEPENDS_ON.
    for source, target in (
        ("x86_64-linux-gnu", "unix"),
        ("x86_64-linux-gnu", "micropython"),
        ("unix", "libffi8"),
        ("unix", "vendor-lib"),
    ):
        assert (ids[source], "DEPENDS_ON", ids[target]) in relationships
    main = files["py/main.c"]["SPDXID"]
    assert (ids["unix"], "DEPENDS_ON", main) in relationships
    assert (ids["micropython"], "DEPENDS_ON", main) in relationships
    # Ownership: the repository package contains the file, the firmware only depends on it.
    assert (ids["micropython"], "CONTAINS", main) in relationships
    assert (ids["unix"], "CONTAINS", main) not in relationships
    for name in ("/usr/lib/libffi.so.8.1.4", "/usr/lib/libffi.a"):
        assert (ids["libffi8"], "CONTAINS", files[name]["SPDXID"]) in relationships


def test_dynamic_needed_becomes_dynamic_link_placeholders():
    spdx = convert()
    packages = by_name(spdx)
    links = [item for item in spdx["relationships"] if item["relationshipType"] == "DYNAMIC_LINK"]
    assert {item["spdxElementId"] for item in links} == {packages["unix"]["SPDXID"]}
    targets = {item["relatedSpdxElement"] for item in links}
    assert targets == {packages["libffi.so.8"]["SPDXID"], packages["libc.so.6"]["SPDXID"]}
    for soname in ("libffi.so.8", "libc.so.6"):
        placeholder = packages[soname]
        assert "versionInfo" not in placeholder
        assert placeholder["downloadLocation"] == "NOASSERTION"
        assert placeholder["filesAnalyzed"] is False


def test_shared_soname_has_one_placeholder():
    document = cyclonedx()
    document["components"].append(
        {
            "type": "firmware",
            "bom-ref": "urn:mpy:firmware-image:second",
            "name": "second",
            "properties": props(**{"mpy:dynamic-needed": json.dumps(["libc.so.6"])}),
        }
    )
    spdx = convert(document)
    assert [item["name"] for item in spdx["packages"]].count("libc.so.6") == 1
    links = [item for item in spdx["relationships"] if item["relationshipType"] == "DYNAMIC_LINK"]
    assert len(links) == 3


@pytest.mark.parametrize("value", ["not json", '{"a": 1}', '["ok", ""]', "[1]"])
def test_malformed_dynamic_needed_is_rejected(value):
    document = cyclonedx()
    document["components"][-1]["properties"] = props(**{"mpy:dynamic-needed": value})
    with pytest.raises(ValueError, match="dynamic-needed"):
        convert(document)


def test_checksums_are_preserved():
    spdx = convert()
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["checksums"] == [{"algorithm": "SHA256", "checksumValue": SHA256_A}]
    assert files["/usr/lib/libffi.so.8.1.4"]["checksums"] == [
        {"algorithm": "SHA256", "checksumValue": SHA256_B},
        {"algorithm": "SHA1", "checksumValue": SHA1_B},
    ]
    assert by_name(spdx)["unix"]["checksums"] == [
        {"algorithm": "SHA256", "checksumValue": SHA256_B},
        {"algorithm": "SHA1", "checksumValue": SHA1_B},
    ]


def test_file_without_any_hash_becomes_file_purpose_package():
    document = cyclonedx()
    document["components"].append({"type": "file", "bom-ref": "urn:x", "name": "unhashed.bin"})
    spdx = convert(document)
    package = by_name(spdx)["unhashed.bin"]
    assert package["primaryPackagePurpose"] == "FILE"
    assert merged_annotations(package)["cyclonedx:type"] == "file"
    assert "unhashed.bin" not in {item["fileName"] for item in spdx["files"]}


def test_uppercase_hash_content_is_lowercased():
    document = cyclonedx()
    document["components"][0]["hashes"][0]["content"] = SHA256_A.upper()
    spdx = convert(document)
    assert spdx["files"][0]["checksums"][0]["checksumValue"] == SHA256_A


def test_purl_supplier_version_description_copyright_scope():
    spdx = convert()
    packages = by_name(spdx)
    repo = packages["micropython"]
    assert repo["versionInfo"] == "1.2.3"
    assert repo["externalRefs"][0] == {
        "referenceCategory": "PACKAGE-MANAGER",
        "referenceType": "purl",
        "referenceLocator": "pkg:github/micropython/micropython@1.2.3",
    }
    assert {
        "referenceCategory": "OTHER",
        "referenceType": "vcs",
        "referenceLocator": "https://github.com/m/m.git",
    } in repo["externalRefs"]
    vendor = packages["vendor-lib"]
    assert vendor["supplier"] == "Organization: Vendor Inc (a@example.invalid)"
    assert vendor["description"] == "Vendor library"
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["copyrightText"] == "Copyright Example"
    assert files["/usr/lib/libffi.a"]["copyrightText"] == "NOASSERTION"
    # Scope has no SPDX field: it is annotated and marked on the dependency edge.
    assert merged_annotations(packages["libffi8"])["cyclonedx:scope"] == "optional"
    optional = [
        item
        for item in spdx["relationships"]
        if item["relationshipType"] == "DEPENDS_ON"
        and item["relatedSpdxElement"] == packages["libffi8"]["SPDXID"]
    ]
    assert [item["comment"] for item in optional] == ["CycloneDX scope: optional"]


def test_file_types_follow_names():
    spdx = convert()
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["fileTypes"] == ["SOURCE"]
    assert files["/usr/lib/libffi.so.8.1.4"]["fileTypes"] == ["BINARY"]
    assert files["/usr/lib/libffi.a"]["fileTypes"] == ["BINARY"]
    document = cyclonedx()
    document["components"][0]["name"] = "data.unknownext"
    assert "fileTypes" not in convert(document)["files"][0]


def test_licences_map_without_invention():
    spdx = convert()
    packages = by_name(spdx)
    assert packages["micropython"]["licenseDeclared"] == "MIT"
    assert packages["unix"]["licenseDeclared"] == "NOASSERTION"
    assert packages["unix"]["licenseConcluded"] == "NOASSERTION"
    assert packages["vendor-lib"]["licenseDeclared"] == "LicenseRef-vendor"
    assert spdx["hasExtractedLicensingInfos"] == [
        {"licenseId": "LicenseRef-vendor", "extractedText": LICENSE_TEXT}
    ]
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["licenseInfoInFiles"] == ["NOASSERTION"]
    assert files["py/main.c"]["licenseConcluded"] == "NOASSERTION"


def test_licence_expression_and_concluded_acknowledgement():
    document = cyclonedx()
    document["components"][1]["licenses"] = [{"expression": "Apache-2.0 OR GPL-2.0-or-later"}]
    package = by_name(convert(document))["micropython"]
    assert package["licenseDeclared"] == "Apache-2.0 OR GPL-2.0-or-later"
    assert package["licenseConcluded"] == "NOASSERTION"
    document["components"][1]["licenses"] = [
        {"license": {"id": "Apache-2.0"}},
        {"license": {"id": "GPL-2.0-only"}},
        {"license": {"id": "MIT", "acknowledgement": "concluded"}},
    ]
    package = by_name(convert(document))["micropython"]
    assert package["licenseDeclared"] == "Apache-2.0 AND GPL-2.0-only"
    assert package["licenseConcluded"] == "MIT"
    assert "cyclonedx:licenses" not in merged_annotations(package)


def test_named_licence_without_text_is_not_invented():
    document = cyclonedx()
    document["components"][1]["licenses"] = [{"license": {"name": "Proprietary EULA"}}]
    spdx = convert(document)
    package = by_name(spdx)["micropython"]
    assert package["licenseDeclared"] == "NOASSERTION"
    assert merged_annotations(package)["cyclonedx:licenses"] == [
        {"license": {"name": "Proprietary EULA"}}
    ]
    assert [item["licenseId"] for item in spdx["hasExtractedLicensingInfos"]] == [
        "LicenseRef-vendor"
    ]


def test_every_property_is_recoverable_from_annotations():
    document = cyclonedx()
    spdx = convert(document)
    elements = {
        **{item["name"]: item for item in spdx["packages"]},
        **{item["fileName"]: item for item in spdx["files"]},
    }

    def check(component):
        recovered = merged_annotations(elements[component["name"]])
        for item in component.get("properties", []):
            assert recovered[item["name"]] == item["value"]
        for child in component.get("components", []):
            check(child)

    check(document["metadata"]["component"])
    for component in document["components"]:
        check(component)
    assert merged_annotations(elements["vendor-lib"])["cyclonedx:evidence"] == {
        "identity": {"field": "name", "confidence": 1}
    }
    assert merged_annotations(elements["vendor-lib"])["x:other"] == "kept"
    assert merged_annotations(elements["unix"])["mpy:dynamic-needed"] == json.dumps(
        ["libffi.so.8", "libc.so.6"]
    )


def test_annotation_shape():
    spdx = convert()
    for element in [*spdx["packages"], *spdx["files"]]:
        for item in element.get("annotations", []):
            assert item["annotator"] == "Tool: mpy_analysis"
            assert item["annotationType"] == "OTHER"
            assert item["annotationDate"] == spdx["creationInfo"]["created"]
            assert isinstance(json.loads(item["comment"]), dict)


def test_repeated_property_names_are_not_collapsed():
    document = cyclonedx()
    document["components"][1]["properties"] = props(**{"mpy:a": "1"}) + props(**{"mpy:a": "2"})
    package = by_name(convert(document))["micropython"]
    assert sorted(item["mpy:a"] for item in annotation_objects(package)) == ["1", "2"]


def test_deterministic_bytes_and_input_derived_namespace():
    first = sbom_spdx.serialize_spdx(convert())
    second = sbom_spdx.serialize_spdx(convert(copy.deepcopy(cyclonedx())))
    assert first == second
    changed = cyclonedx()
    changed["components"][1]["version"] = "9.9.9"
    other = convert(changed)
    assert other["documentNamespace"] != convert()["documentNamespace"]
    assert convert()["documentNamespace"].startswith("https://spdx.org/spdxdocs/mpy-analysis-")


def test_serialisation_matches_sbom_json_style():
    spdx = convert()
    data = sbom_spdx.serialize_spdx(spdx)
    assert data == (json.dumps(spdx, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert json.loads(data) == spdx


def test_creation_info_is_never_wall_clock():
    spdx = convert()
    assert spdx["creationInfo"]["created"] == sbom_spdx.EPOCH
    assert spdx["creationInfo"]["creators"] == ["Tool: mpy_analysis"]
    document = cyclonedx()
    document["metadata"]["timestamp"] = "2026-03-04T05:06:07.890+02:00"
    document["metadata"]["tools"] = {
        "components": [{"type": "application", "name": "gen", "version": "2"}]
    }
    document["metadata"]["authors"] = [{"name": "Ann", "email": "ann@example.invalid"}]
    info = convert(document)["creationInfo"]
    assert info["created"] == "2026-03-04T03:06:07Z"
    assert info["creators"] == [
        "Tool: mpy_analysis",
        "Tool: gen-2",
        "Person: Ann (ann@example.invalid)",
    ]
    document["metadata"]["tools"] = [{"vendor": "v", "name": "legacy", "version": "1"}]
    assert "Tool: legacy-1" in convert(document)["creationInfo"]["creators"]


def test_invalid_timestamp_is_rejected():
    document = cyclonedx()
    document["metadata"]["timestamp"] = "yesterday"
    with pytest.raises(ValueError, match="timestamp"):
        convert(document)


def test_document_level_extras_are_annotated():
    document = cyclonedx()
    document["serialNumber"] = "urn:uuid:3e671687-395b-41f5-a30f-a58921a69b79"
    document["metadata"]["properties"] = props(**{"mpy:doc": "x"})
    spdx = convert(document)
    extras = annotation_objects(spdx)[0]
    assert extras["cyclonedx:serialNumber"] == document["serialNumber"]
    assert extras["cyclonedx:metadata.properties"] == document["metadata"]["properties"]
    assert extras["cyclonedx:version"] == 1


def test_orphan_top_level_file_is_contained_by_the_document():
    document = cyclonedx()
    document["components"].append(
        {
            "type": "file",
            "bom-ref": "urn:orphan",
            "name": "orphan.c",
            "hashes": [{"alg": "SHA-256", "content": SHA256_C}],
        }
    )
    spdx = convert(document)
    orphan = next(item for item in spdx["files"] if item["fileName"] == "orphan.c")
    contains = [
        item
        for item in spdx["relationships"]
        if item["relationshipType"] == "CONTAINS"
        and item["relatedSpdxElement"] == orphan["SPDXID"]
    ]
    assert [item["spdxElementId"] for item in contains] == ["SPDXRef-DOCUMENT"]


def test_document_without_root_describes_unreferenced_packages():
    document = cyclonedx()
    del document["metadata"]
    document["dependencies"] = [e for e in document["dependencies"] if e["ref"] != APP]
    spdx = convert(document)
    assert spdx["name"] == "mpy-analysis-sbom"
    described = {
        item["relatedSpdxElement"]
        for item in spdx["relationships"]
        if item["relationshipType"] == "DESCRIBES"
    }
    packages = by_name(spdx)
    assert packages["micropython"]["SPDXID"] in described
    assert packages["libffi8"]["SPDXID"] not in described


def test_unknown_dependency_references_are_rejected():
    document = cyclonedx()
    document["dependencies"].append({"ref": FIRMWARE, "dependsOn": ["urn:missing"]})
    with pytest.raises(ValueError, match="unknown bom-ref"):
        convert(document)
    document = cyclonedx()
    document["dependencies"].append({"ref": "urn:missing", "dependsOn": []})
    with pytest.raises(ValueError, match="unknown bom-ref"):
        convert(document)


def test_duplicate_bom_ref_is_rejected():
    document = cyclonedx()
    document["components"][3]["components"][0]["bom-ref"] = REPO
    with pytest.raises(ValueError, match="duplicate"):
        convert(document)


def test_non_cyclonedx_input_is_rejected():
    with pytest.raises(ValueError):
        sbom_spdx.cyclonedx_to_spdx({"bomFormat": "CycloneDX", "specVersion": "1.5"})


def test_validate_spdx_rejects_bad_documents():
    good = convert()
    sbom_spdx.validate_spdx(good)
    bad = copy.deepcopy(good)
    bad["packages"][0]["SPDXID"] = "SPDXRef-has_underscore"
    with pytest.raises(ValueError, match="identifier"):
        sbom_spdx.validate_spdx(bad)
    bad = copy.deepcopy(good)
    bad["relationships"].append(
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-missing",
        }
    )
    with pytest.raises(ValueError, match="unknown element"):
        sbom_spdx.validate_spdx(bad)
    bad = copy.deepcopy(good)
    bad["spdxVersion"] = 3
    with pytest.raises(ValueError, match="schema validation"):
        sbom_spdx.validate_spdx(bad)
    bad = copy.deepcopy(good)
    bad["packages"].append(copy.deepcopy(bad["packages"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        sbom_spdx.validate_spdx(bad)


def test_round_trip_through_metadata_bom_preserves_source_binding():
    cdx = cyclonedx()
    spdx = convert(cdx)
    components, schemas = metadata_bom.read_bom(json.loads(sbom_spdx.serialize_spdx(spdx)))
    assert schemas[0].name == "spdx-2.3.schema.json"
    packages = by_name(spdx)
    reread = {item["id"]: item for item in components}
    repo = reread[packages["micropython"]["SPDXID"]]
    assert repo["binding"] == {
        "mpy:source:root": ".",
        "mpy:source:path": "py",
        "mpy:source:revision": "abc",
    }
    original = {item["id"]: item for item in metadata_bom.read_bom(cdx)[0]}[REPO]
    assert repo["binding"] == {
        key: value for key, value in original["binding"].items() if key.startswith("mpy:source:")
    }
    assert repo["fields"]["name"] == "micropython"
    assert repo["fields"]["version"] == "1.2.3"
    assert {item["id"] for item in components} == {item["SPDXID"] for item in spdx["packages"]}
    assert reread[packages["unix"]["SPDXID"]]["binding"] == {}
    assert reread[packages["vendor-lib"]["SPDXID"]]["license_texts"] == {
        "LicenseRef-vendor": LICENSE_TEXT
    }
    root_dependencies = reread[packages["x86_64-linux-gnu"]["SPDXID"]]["dependencies"]
    assert sorted(root_dependencies) == ["micropython", "unix"]


def test_repeated_source_binding_property_is_rejected_by_the_reader_as_in_cyclonedx():
    document = cyclonedx()
    document["components"][1]["properties"] = props(**{"mpy:source:root": "."}) + props(
        **{"mpy:source:root": "other"}
    )
    with pytest.raises(ValueError, match="duplicate"):
        metadata_bom.read_bom(document)
    with pytest.raises(ValueError, match="duplicate"):
        metadata_bom.read_bom(convert(document))
