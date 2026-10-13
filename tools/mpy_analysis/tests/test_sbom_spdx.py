import copy
import json
from collections import Counter

import pytest
from mpy_analysis import metadata_bom, sbom_spdx
from mpy_analysis.sbom_cyclonedx import to_cyclonedx
from mpy_analysis.sbom_model import Component, Contact, ExternalReference, Model, Party
from mpy_analysis.sbom_spdx import to_spdx

SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64
SHA1_A = "2" * 40
SHA1_B = "1" * 40
SHA1_C = "3" * 40
LICENSE_TEXT = "Synthetic vendor licence terms."

APP = "urn:mpy:build:app"
FIRMWARE = "urn:mpy:firmware-image:fw"
REPO = "urn:mpy:repository:repo"
SYSTEM = "urn:mpy:system-package:libffi8"
SOURCE_FILE = "urn:mpy:file:main"
VENDOR = "urn:mpy:dependency:vendor"
LIBFFI_SO = "urn:mpy:file:libffi-so"
LIBFFI_A = "urn:mpy:file:libffi-a"


def sample_model() -> Model:
    components = [
        Component(
            kind="application",
            ref=APP,
            name="x86_64-linux-gnu",
            properties={"port": "unix", "variant": "standard"},
        ),
        Component(
            kind="file",
            ref=SOURCE_FILE,
            name="py/main.c",
            hashes={"sha256": SHA256_A, "sha1": SHA1_A},
            copyright="Copyright Example",
            properties={"owner": "core", "path": "py/main.c", "selection": "native"},
        ),
        Component(
            kind="library",
            ref=REPO,
            name="micropython",
            version="1.2.3",
            purl="pkg:github/micropython/micropython@1.2.3",
            external_references=[ExternalReference("vcs", "https://github.com/m/m.git")],
            license="MIT",
            properties={
                "revision": "abc",
                "source:root": ".",
                "source:path": "py",
                "source:revision": "abc",
            },
        ),
        Component(
            kind="library",
            ref=VENDOR,
            name="vendor-lib",
            description="Vendor library",
            supplier=Party(name="Vendor Inc", contacts=[Contact(email="a@example.invalid")]),
            publisher="Vendor Publisher",
            group="org.vendor",
            cpe="cpe:2.3:a:vendor:lib:1:*:*:*:*:*:*:*",
            license="LicenseRef-vendor",
            license_texts={"LicenseRef-vendor": LICENSE_TEXT},
            properties={"boundary": "vendor"},
        ),
        Component(
            kind="library",
            ref=SYSTEM,
            name="libffi8",
            version="3.4.6",
            purl="pkg:deb/ubuntu/libffi8@3.4.6",
            properties={"package-manager": "dpkg"},
        ),
        Component(
            kind="file",
            ref=LIBFFI_SO,
            name="/usr/lib/libffi.so.8.1.4",
            hashes={"sha256": SHA256_B, "sha1": SHA1_B},
            properties={"linkage": "dynamic"},
            parent=SYSTEM,
        ),
        Component(
            kind="file",
            ref=LIBFFI_A,
            name="/usr/lib/libffi.a",
            hashes={"sha256": SHA256_C, "sha1": SHA1_C},
            parent=SYSTEM,
        ),
        Component(
            kind="firmware",
            ref=FIRMWARE,
            name="unix",
            hashes={"sha256": SHA256_B, "sha1": SHA1_B},
            properties={"image-role": "application", "link-closure": "complete"},
        ),
    ]
    return Model(
        root=APP,
        name="x86_64-linux-gnu",
        components={item.ref: item for item in components},
        depends_on={
            APP: {FIRMWARE, REPO},
            FIRMWARE: {SOURCE_FILE, SYSTEM, VENDOR},
            REPO: {SOURCE_FILE},
            SYSTEM: set(),
            VENDOR: set(),
            SOURCE_FILE: set(),
        },
        dynamic_links={FIRMWARE: ["libffi.so.8", "libc.so.6"]},
    )


def convert(model=None):
    return to_spdx(sample_model() if model is None else model)


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


def add(model, **fields):
    component = Component(**fields)
    model.components[component.ref] = component
    return component


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
    # The application root and four top-level packages, one top-level file, two nested files,
    # plus two dynamic-link placeholders.
    assert len(spdx["packages"]) + len(spdx["files"]) == 10
    assert {item["fileName"] for item in spdx["files"]} == {
        "py/main.c",
        "./usr/lib/libffi.so.8.1.4",
        "./usr/lib/libffi.a",
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
    # Each depends_on edge is one DEPENDS_ON.
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
    # Nested components are contained by their parent.
    for name in ("./usr/lib/libffi.so.8.1.4", "./usr/lib/libffi.a"):
        assert (ids["libffi8"], "CONTAINS", files[name]["SPDXID"]) in relationships


def test_dynamic_links_become_unversioned_placeholder_packages():
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
        assert placeholder["licenseDeclared"] == "NOASSERTION"


def test_shared_soname_has_one_placeholder():
    model = sample_model()
    add(model, kind="firmware", ref="urn:mpy:firmware-image:second", name="second")
    model.dynamic_links["urn:mpy:firmware-image:second"] = ["libc.so.6"]
    spdx = convert(model)
    assert [item["name"] for item in spdx["packages"]].count("libc.so.6") == 1
    links = [item for item in spdx["relationships"] if item["relationshipType"] == "DYNAMIC_LINK"]
    assert len(links) == 3


@pytest.mark.parametrize("value", [[""], [1], "libc.so.6"])
def test_malformed_dynamic_links_are_rejected(value):
    model = sample_model()
    model.dynamic_links[FIRMWARE] = value
    with pytest.raises(ValueError, match="sonames"):
        convert(model)


def test_checksums_are_preserved():
    spdx = convert()
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["checksums"] == [
        {"algorithm": "SHA1", "checksumValue": SHA1_A},
        {"algorithm": "SHA256", "checksumValue": SHA256_A},
    ]
    assert files["./usr/lib/libffi.so.8.1.4"]["checksums"] == [
        {"algorithm": "SHA1", "checksumValue": SHA1_B},
        {"algorithm": "SHA256", "checksumValue": SHA256_B},
    ]
    assert by_name(spdx)["unix"]["checksums"] == [
        {"algorithm": "SHA1", "checksumValue": SHA1_B},
        {"algorithm": "SHA256", "checksumValue": SHA256_B},
    ]


def test_unknown_hash_algorithm_is_rejected():
    model = sample_model()
    model.components[REPO].hashes = {"crc32": "00"}
    with pytest.raises(ValueError, match="no SPDX equivalent"):
        convert(model)


def test_file_without_any_hash_becomes_file_purpose_package():
    model = sample_model()
    add(model, kind="file", ref="urn:x", name="unhashed.bin")
    spdx = convert(model)
    package = by_name(spdx)["unhashed.bin"]
    assert package["primaryPackagePurpose"] == "FILE"
    assert package["downloadLocation"] == "NOASSERTION"
    assert package["filesAnalyzed"] is False
    assert "checksums" not in package
    assert "unhashed.bin" not in {item["fileName"] for item in spdx["files"]}


def test_file_with_only_sha256_is_a_package_and_a_sha1_less_file_is_rejected():
    model = sample_model()
    add(model, kind="file", ref="urn:y", name="sha256only.bin", hashes={"sha256": SHA256_C})
    spdx = convert(model)
    package = by_name(spdx)["sha256only.bin"]
    assert package["primaryPackagePurpose"] == "FILE"
    assert package["checksums"] == [{"algorithm": "SHA256", "checksumValue": SHA256_C}]
    assert "sha256only.bin" not in {item["fileName"] for item in spdx["files"]}
    for item in spdx["files"]:
        item["checksums"] = [c for c in item["checksums"] if c["algorithm"] != "SHA1"]
    with pytest.raises(ValueError, match="SHA1"):
        sbom_spdx.validate_spdx(spdx)


def test_uppercase_hash_content_is_lowercased():
    model = sample_model()
    model.components[SOURCE_FILE].hashes["sha256"] = SHA256_A.upper()
    spdx = convert(model)
    main = next(item for item in spdx["files"] if item["fileName"] == "py/main.c")
    assert {"algorithm": "SHA256", "checksumValue": SHA256_A} in main["checksums"]


def test_purl_supplier_version_description_copyright_cpe():
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
    assert {
        "referenceCategory": "SECURITY",
        "referenceType": "cpe23Type",
        "referenceLocator": "cpe:2.3:a:vendor:lib:1:*:*:*:*:*:*:*",
    } in vendor["externalRefs"]
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["copyrightText"] == "Copyright Example"
    assert files["./usr/lib/libffi.a"]["copyrightText"] == "NOASSERTION"
    assert "versionInfo" not in packages["unix"]


def test_website_reference_is_the_homepage_and_other_references_keep_their_type():
    model = sample_model()
    model.components[REPO].external_references = [
        ExternalReference("website", "https://example.invalid/home"),
        ExternalReference("website", "https://example.invalid/other"),
        ExternalReference("vcs", "https://example.invalid/repo.git", comment="mirror"),
    ]
    package = by_name(convert(model))["micropython"]
    assert package["homepage"] == "https://example.invalid/home"
    refs = [item for item in package["externalRefs"] if item["referenceCategory"] == "OTHER"]
    assert [(item["referenceType"], item["referenceLocator"]) for item in refs] == [
        ("website", "https://example.invalid/other"),
        ("vcs", "https://example.invalid/repo.git"),
    ]
    assert refs[1]["comment"] == "mirror"


def test_author_is_the_originator_and_a_file_contributor():
    model = sample_model()
    model.components[REPO].author = "Ann"
    model.components[SOURCE_FILE].author = "Bob"
    spdx = convert(model)
    assert by_name(spdx)["micropython"]["originator"] == "Person: Ann"
    main = next(item for item in spdx["files"] if item["fileName"] == "py/main.c")
    assert main["fileContributors"] == ["Bob"]


def test_cpe_without_an_spdx_form_is_annotated():
    model = sample_model()
    model.components[VENDOR].cpe = "not-a-cpe"
    vendor = by_name(convert(model))["vendor-lib"]
    assert not any(item["referenceCategory"] == "SECURITY" for item in vendor.get("externalRefs", []))
    assert merged_annotations(vendor)["component:cpe"] == "not-a-cpe"


def test_component_kinds_without_a_purpose_are_annotated():
    model = sample_model()
    add(model, kind="data", ref="urn:data", name="dataset")
    package = by_name(convert(model))["dataset"]
    assert package["primaryPackagePurpose"] == "OTHER"
    assert merged_annotations(package)["component:kind"] == "data"


def test_file_types_follow_names():
    spdx = convert()
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["fileTypes"] == ["SOURCE"]
    assert files["./usr/lib/libffi.so.8.1.4"]["fileTypes"] == ["BINARY"]
    assert files["./usr/lib/libffi.a"]["fileTypes"] == ["BINARY"]
    model = sample_model()
    model.components[SOURCE_FILE].name = "data.unknownext"
    spdx = convert(model)
    assert "fileTypes" not in next(
        item for item in spdx["files"] if item["fileName"] == "data.unknownext"
    )


def test_licences_map_without_invention():
    spdx = convert()
    packages = by_name(spdx)
    assert packages["micropython"]["licenseDeclared"] == "MIT"
    assert packages["unix"]["licenseDeclared"] == "NOASSERTION"
    assert packages["unix"]["licenseConcluded"] == "NOASSERTION"
    assert packages["micropython"]["licenseConcluded"] == "NOASSERTION"
    assert packages["vendor-lib"]["licenseDeclared"] == "LicenseRef-vendor"
    assert spdx["hasExtractedLicensingInfos"] == [
        {"licenseId": "LicenseRef-vendor", "extractedText": LICENSE_TEXT}
    ]
    files = {item["fileName"]: item for item in spdx["files"]}
    assert files["py/main.c"]["licenseInfoInFiles"] == ["NOASSERTION"]
    assert files["py/main.c"]["licenseConcluded"] == "NOASSERTION"


def test_licence_expression_is_declared_as_given():
    model = sample_model()
    model.components[REPO].license = "Apache-2.0 OR GPL-2.0-or-later"
    package = by_name(convert(model))["micropython"]
    assert package["licenseDeclared"] == "Apache-2.0 OR GPL-2.0-or-later"
    assert package["licenseConcluded"] == "NOASSERTION"
    model.components[SOURCE_FILE].license = "MIT"
    main = next(item for item in convert(model)["files"] if item["fileName"] == "py/main.c")
    assert main["licenseInfoInFiles"] == ["MIT"]
    assert main["licenseConcluded"] == "NOASSERTION"


def test_licence_text_alone_does_not_declare_a_licence():
    model = sample_model()
    component = model.components[REPO]
    component.license = None
    component.license_texts = {"Proprietary EULA": "Terms."}
    spdx = convert(model)
    assert by_name(spdx)["micropython"]["licenseDeclared"] == "NOASSERTION"
    extracted = {item["licenseId"]: item for item in spdx["hasExtractedLicensingInfos"]}
    identifier = next(key for key in extracted if key != "LicenseRef-vendor")
    assert identifier.startswith("LicenseRef-Proprietary-EULA-")
    assert extracted[identifier]["name"] == "Proprietary EULA"
    assert extracted[identifier]["extractedText"] == "Terms."
    sbom_spdx.validate_spdx(spdx)


def test_conflicting_licence_texts_are_rejected():
    model = sample_model()
    model.components[REPO].license_texts = {"LicenseRef-vendor": "Different terms."}
    with pytest.raises(ValueError, match="conflicting licence text"):
        convert(model)


def test_every_property_is_recoverable_from_annotations():
    model = sample_model()
    spdx = convert(model)
    elements = {
        **{item["name"]: item for item in spdx["packages"]},
        **{item["fileName"]: item for item in spdx["files"]},
    }
    for component in model.components.values():
        recovered = merged_annotations(elements[sbom_spdx._file_name(component.name)])
        for name, value in component.properties.items():
            assert recovered[f"mpy:{name}"] == value
    vendor = merged_annotations(elements["vendor-lib"])
    assert vendor["component:publisher"] == "Vendor Publisher"
    assert vendor["component:group"] == "org.vendor"


def test_properties_are_not_annotated_as_dynamic_needed():
    spdx = convert()
    assert not any(
        key.endswith("dynamic-needed")
        for element in [*spdx["packages"], *spdx["files"]]
        for item in annotation_objects(element)
        for key in item
    )


def test_file_fields_without_an_spdx_home_are_annotated():
    model = sample_model()
    file_component = model.components[SOURCE_FILE]
    file_component.version = "9"
    file_component.group = "g"
    file_component.supplier = Party(name="Org")
    file_component.external_references = [ExternalReference("vcs", "https://example.invalid/r.git")]
    main = next(item for item in convert(model)["files"] if item["fileName"] == "py/main.c")
    extras = merged_annotations(main)
    assert extras["component:version"] == "9"
    assert extras["component:group"] == "g"
    assert extras["component:supplier"] == "Org"
    assert extras["component:external-references"] == [
        {"type": "vcs", "url": "https://example.invalid/r.git"}
    ]


def test_annotation_shape():
    spdx = convert()
    for element in [*spdx["packages"], *spdx["files"]]:
        for item in element.get("annotations", []):
            assert item["annotator"] == "Tool: mpy_analysis"
            assert item["annotationType"] == "OTHER"
            assert item["annotationDate"] == spdx["creationInfo"]["created"]
            assert isinstance(json.loads(item["comment"]), dict)


def test_deterministic_bytes_and_content_derived_namespace():
    first = sbom_spdx.serialize_spdx(convert())
    second = sbom_spdx.serialize_spdx(convert(copy.deepcopy(sample_model())))
    assert first == second
    changed = sample_model()
    changed.components[REPO].version = "9.9.9"
    assert convert(changed)["documentNamespace"] != convert()["documentNamespace"]
    assert convert()["documentNamespace"].startswith("https://spdx.org/spdxdocs/mpy-analysis-")


def test_serialisation_matches_sbom_json_style():
    spdx = convert()
    data = sbom_spdx.serialize_spdx(spdx)
    assert data == (json.dumps(spdx, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert json.loads(data) == spdx


def test_creation_info_is_never_wall_clock():
    spdx = convert()
    assert spdx["creationInfo"] == {"created": sbom_spdx.EPOCH, "creators": ["Tool: mpy_analysis"]}
    assert "comment" not in spdx["creationInfo"]


def test_orphan_top_level_file_is_contained_by_the_document():
    model = sample_model()
    add(
        model,
        kind="file",
        ref="urn:orphan",
        name="orphan.c",
        hashes={"sha256": SHA256_C, "sha1": SHA1_C},
    )
    spdx = convert(model)
    orphan = next(item for item in spdx["files"] if item["fileName"] == "orphan.c")
    contains = [
        item
        for item in spdx["relationships"]
        if item["relationshipType"] == "CONTAINS"
        and item["relatedSpdxElement"] == orphan["SPDXID"]
    ]
    assert [item["spdxElementId"] for item in contains] == ["SPDXRef-DOCUMENT"]


def test_package_containing_files_is_analysed_with_a_matching_verification_code():
    spdx = convert(sample_model())
    files = {item["SPDXID"]: item for item in spdx["files"]}
    containers = {
        item["spdxElementId"]
        for item in spdx["relationships"]
        if item["relationshipType"] == "CONTAINS" and item["relatedSpdxElement"] in files
    }
    assert containers
    for package in spdx["packages"]:
        analysed = package["SPDXID"] in containers
        assert package["filesAnalyzed"] is analysed
        if analysed:
            members = [
                next(c["checksumValue"] for c in files[item["relatedSpdxElement"]]["checksums"] if c["algorithm"] == "SHA1")
                for item in spdx["relationships"]
                if item["spdxElementId"] == package["SPDXID"]
                and item["relationshipType"] == "CONTAINS"
                and item["relatedSpdxElement"] in files
            ]
            assert package["packageVerificationCode"]["packageVerificationCodeValue"] == (
                sbom_spdx._verification_code(members)
            )
            assert package["licenseInfoFromFiles"]
        else:
            assert "packageVerificationCode" not in package


def test_validator_rejects_a_wrong_verification_code_and_unanalysed_container():
    spdx = convert(sample_model())
    analysed = next(item for item in spdx["packages"] if item["filesAnalyzed"])
    bad = copy.deepcopy(spdx)
    target = next(item for item in bad["packages"] if item["SPDXID"] == analysed["SPDXID"])
    target["packageVerificationCode"]["packageVerificationCodeValue"] = "0" * 40
    with pytest.raises(ValueError, match="verification code"):
        sbom_spdx.validate_spdx(bad)
    bad = copy.deepcopy(spdx)
    target = next(item for item in bad["packages"] if item["SPDXID"] == analysed["SPDXID"])
    target["filesAnalyzed"] = False
    del target["packageVerificationCode"]
    del target["licenseInfoFromFiles"]
    with pytest.raises(ValueError, match="filesAnalyzed is false"):
        sbom_spdx.validate_spdx(bad)


def test_absolute_host_path_is_written_relative_and_validated():
    model = sample_model()
    add(
        model,
        kind="file",
        ref="urn:system-header",
        name="/usr/include/alloca.h",
        hashes={"sha256": SHA256_C, "sha1": SHA1_C},
    )
    spdx = convert(model)
    entry = next(item for item in spdx["files"] if item["fileName"].endswith("usr/include/alloca.h"))
    assert entry["fileName"] == "./usr/include/alloca.h"
    assert "/usr/include/alloca.h" in entry["comment"]
    entry["fileName"] = "/usr/include/alloca.h"
    with pytest.raises(ValueError, match="relative"):
        sbom_spdx.validate_spdx(spdx)


def test_unknown_references_are_rejected():
    model = sample_model()
    model.depends_on[FIRMWARE].add("urn:missing")
    with pytest.raises(ValueError, match="unknown component"):
        convert(model)
    model = sample_model()
    model.depends_on["urn:missing"] = set()
    with pytest.raises(ValueError, match="unknown component"):
        convert(model)
    model = sample_model()
    model.components[LIBFFI_A].parent = "urn:missing"
    with pytest.raises(ValueError, match="unknown parent"):
        convert(model)
    model = sample_model()
    model.dynamic_links["urn:missing"] = ["libc.so.6"]
    with pytest.raises(ValueError, match="unknown component"):
        convert(model)


def test_component_must_be_keyed_by_its_reference():
    model = sample_model()
    model.components[REPO].ref = VENDOR
    with pytest.raises(ValueError, match="keyed by"):
        convert(model)


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
    model = sample_model()
    spdx = convert(model)
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
    original = {item["id"]: item for item in metadata_bom.read_bom(to_cyclonedx(model))[0]}[REPO]
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
