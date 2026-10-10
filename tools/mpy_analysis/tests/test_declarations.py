import base64
import hashlib
import json

import pytest

from mpy_analysis import sbom
from mpy_analysis.declarations import load_declarations, merge_declaration, selected_digest
from mpy_analysis.metadata import collect_metadata
from mpy_analysis.scope import FROZEN_GROUP
from test_sbom import MPY_ROOT


def selection(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    (root / "module.py").write_text("value = 1\n")
    scope = {
        "roots": {"external": str(root)},
        "groups": {
            FROZEN_GROUP: [
                {
                    "root": "external",
                    "path": "module.py",
                    "target_path": "module.py",
                    "owner": "dependency",
                    "provenance": {"kind": "fixture"},
                }
            ]
        },
    }
    return root, scope


def declaration(tmp_path, scope, root, **overrides):
    component = {
        "type": "library",
        "name": "declared-component",
        "version": "2.2",
        "licenses": [{"expression": "MIT"}],
        "properties": [
            {"name": "mpy:source:root", "value": "external"},
            {"name": "mpy:source:path", "value": "."},
            {"name": "mpy:source:sha256", "value": selected_digest(scope, root)[0]},
        ],
    }
    component.update(overrides)
    path = tmp_path / "dependencies.cdx.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [component],
            }
        )
    )
    return path


def test_source_bound_manual_metadata_becomes_stale_after_selected_bytes_change(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    records, gaps = load_declarations(scope, [path])
    assert records[0]["evidence"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert gaps == []
    (root / "module.py").write_text("value = 9\n")
    records, gaps = load_declarations(scope, [path])
    assert records == []
    assert gaps[0]["status"] == "stale"


def test_unselected_declaration_does_not_add_component(tmp_path):
    root, scope = selection(tmp_path)
    empty = root / "unselected"
    empty.mkdir()
    path = declaration(tmp_path, scope, root)
    data = json.loads(path.read_text())
    data["components"][0]["properties"][1]["value"] = "unselected"
    path.write_text(json.dumps(data))
    records, gaps = load_declarations(scope, [path])
    assert records == []
    assert gaps[0]["status"] == "unmatched"


@pytest.mark.parametrize("selector", ["..", "/outside", "nested/../../outside", "C:\\outside"])
def test_manual_selector_cannot_escape_source_root(tmp_path, selector):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    data = json.loads(path.read_text())
    data["components"][0]["properties"][1]["value"] = selector
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="within|contained"):
        load_declarations(scope, [path])


def test_duplicate_physical_declarations_rejected(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    with pytest.raises(ValueError, match="duplicate physical"):
        load_declarations(scope, [path, path])


def test_invalid_standard_component_schema_rejected(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, version={"not": "a string"})
    with pytest.raises(ValueError, match="invalid CycloneDX"):
        load_declarations(scope, [path])


def test_manual_value_cannot_silently_override_upstream(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    records, _ = load_declarations(scope, [path])
    metadata = {
        "fields": {"version": "1"},
        "evidence": [
            {
                "field": "version",
                "value": "1",
                "path": "metadata.txt",
                "sha256": "a" * 64,
                "status": "known",
            }
        ],
        "issues": [],
    }
    result = merge_declaration(metadata, records[0])
    assert "version" not in result["fields"]
    assert any(
        item["field"] == "version" and item["status"] == "conflicting" for item in result["issues"]
    )
    records[0]["corrections"] = {
        "version": {
            "reason": "upstream stale declaration",
            "supersedes": [{"path": "metadata.txt", "sha256": "a" * 64, "value": "1"}],
        }
    }
    result = merge_declaration(metadata, records[0])
    assert result["fields"]["version"] == "2.2"
    assert (
        next(
            item
            for item in result["evidence"]
            if item.get("origin") == "manual" and item["field"] == "version"
        )["correction"]["reason"]
        == "upstream stale declaration"
    )
    records[0]["corrections"]["version"]["supersedes"][0]["sha256"] = "b" * 64
    result = merge_declaration(metadata, records[0])
    assert "version" not in result["fields"]
    assert any(
        item["field"] == "version" and item["status"] == "conflicting" for item in result["issues"]
    )
    assert any(
        item["field"] == "version" and item["status"] == "stale" for item in result["issues"]
    )
    assert (
        next(
            item
            for item in result["evidence"]
            if item.get("origin") == "manual" and item["field"] == "version"
        )["status"]
        == "stale"
    )


def upstream(field, value, *, evidence_values=None):
    return {
        "fields": {field: value},
        "evidence": [
            {
                "field": field,
                "value": assertion,
                "path": f"metadata-{index}.txt",
                "sha256": str(index + 1) * 64,
                "status": "known",
            }
            for index, assertion in enumerate(evidence_values or [value])
        ],
        "issues": [],
    }


def correction_for(*rows):
    return {
        "reason": "source-bound metadata contains an incorrect assertion",
        "supersedes": [{key: row[key] for key in ("path", "sha256", "value")} for row in rows],
    }


def custom_license(content=None, **text_properties):
    license_data = {"name": "LicenseRef-Example"}
    if content is not None:
        license_data["text"] = {"content": content, **text_properties}
    return [{"license": license_data}]


@pytest.mark.parametrize("field", ["name", "version", "description", "author", "copyright"])
def test_whitespace_manual_facts_are_not_known(tmp_path, field):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, **{field: " \t\n "})
    records, issues = load_declarations(scope, [path])
    assert field not in records[0]["fields"]
    assert any(item["field"] == field and item["status"] == "unsupported" for item in issues)
    result = merge_declaration(upstream(field, "upstream fact"), records[0])
    assert result["fields"][field] == "upstream fact"
    assert not any(
        item["field"] == field and item.get("origin") == "manual" and item["status"] == "known"
        for item in result["evidence"]
    )


def test_manual_fact_outer_whitespace_is_normalized_without_rewriting_values(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(
        tmp_path, scope, root, name="  Unusual Project_Name  ", version=" \tcustom release  "
    )
    records, issues = load_declarations(scope, [path])
    assert issues == []
    assert records[0]["fields"]["name"] == "Unusual Project_Name"
    assert records[0]["fields"]["version"] == "custom release"


def test_custom_text_is_decoded_and_retained_with_field_evidence(tmp_path):
    root, scope = selection(tmp_path)
    content = base64.b64encode("  Licence terms: café\n".encode()).decode()
    path = declaration(tmp_path, scope, root, licenses=custom_license(content, encoding="base64"))
    records, issues = load_declarations(scope, [path])
    assert issues == []
    record = records[0]
    assert record["fields"]["license"] == "LicenseRef-Example"
    assert record["fields"]["license_text:LicenseRef-Example"] == "Licence terms: café"
    assert record["license_texts"]["LicenseRef-Example"]["text"] == "Licence terms: café"
    row = next(
        item
        for item in record["field_evidence"]
        if item["field"] == "license_text:LicenseRef-Example"
    )
    assert row["value"] == "Licence terms: café"
    assert row["path"] == str(path)
    assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert row["status"] == "known"


@pytest.mark.parametrize(
    ("content", "properties", "status"),
    [
        (None, {}, "missing"),
        ("", {}, "missing"),
        (" \n\t ", {}, "missing"),
        (base64.b64encode(b" \n\t ").decode(), {"encoding": "base64"}, "missing"),
        ("!not-base64!", {"encoding": "base64"}, "invalid"),
        ("/w==", {"encoding": "base64"}, "invalid"),
    ],
)
def test_missing_or_invalid_custom_terms_never_complete_licensing(
    tmp_path, content, properties, status
):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, licenses=custom_license(content, **properties))
    records, issues = load_declarations(scope, [path])
    assert "license" not in records[0]["fields"]
    assert "license_applicability" not in records[0]["fields"]
    assert records[0]["license_texts"] == {}
    assert any(item["field"] == "license" and item["status"] == status for item in issues)
    assert not any(
        item["field"] == "license_text:LicenseRef-Example" and item["status"] == "known"
        for item in records[0]["field_evidence"]
    )


def test_custom_expression_without_retained_terms_is_incomplete(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, licenses=[{"expression": "LicenseRef-Example"}])
    records, issues = load_declarations(scope, [path])
    assert "license" not in records[0]["fields"]
    assert any(item["field"] == "license" and item["status"] == "missing" for item in issues)


@pytest.mark.parametrize("source", ["bom", "license-file", "mapping"])
def test_manual_custom_terms_cannot_replace_upstream_same_identifier(tmp_path, source):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, licenses=custom_license("manual terms"))
    records, _ = load_declarations(scope, [path])
    metadata = upstream("license", "LicenseRef-Example")
    metadata["fields"]["license_applicability"] = "confirmed"
    text_path = "/source/LICENSES/LicenseRef-Example.txt"
    metadata["license_texts"] = {
        "LicenseRef-Example": {"path": text_path, "text": "upstream terms"}
    }
    if source != "mapping":
        row = {
            "path": text_path,
            "sha256": "a" * 64,
            "field": "license_text" if source == "bom" else "license",
            "value": "upstream terms" if source == "bom" else "LICENSES/LicenseRef-Example.txt",
            "status": "known" if source == "bom" else "candidate",
        }
        if source == "bom":
            row["identifier"] = "LicenseRef-Example"
        else:
            row["text"] = "upstream terms"
        metadata["evidence"].append(row)
    result = merge_declaration(metadata, records[0])
    assert "license" not in result["fields"]
    assert "license_applicability" not in result["fields"]
    assert "LicenseRef-Example" not in result["license_texts"]
    terms = {
        item["value"]
        for item in result["evidence"]
        if item["field"] == "license_text:LicenseRef-Example"
    }
    assert terms == {"upstream terms", "manual terms"}
    assert any(
        item["field"] == "license" and item["status"] == "conflicting" for item in result["issues"]
    )
    assert metadata["fields"]["license"] == "LicenseRef-Example"
    assert metadata["license_texts"]["LicenseRef-Example"]["text"] == "upstream terms"


@pytest.mark.parametrize("source", ["per-identifier", "bom", "license-file"])
def test_current_pinned_custom_terms_correction_resolves_conflict(tmp_path, source):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, licenses=custom_license("correct terms"))
    records, _ = load_declarations(scope, [path])
    field = "license_text:LicenseRef-Example"
    metadata = upstream(field, "wrong terms")
    metadata["fields"]["license"] = "LicenseRef-Example"
    metadata["fields"]["license_applicability"] = "confirmed"
    metadata["license_texts"] = {
        "LicenseRef-Example": {"path": "metadata-0.txt", "text": "wrong terms"}
    }
    records[0]["corrections"][field] = correction_for(metadata["evidence"][0])
    if source == "bom":
        metadata["fields"].pop(field)
        metadata["evidence"][0].update(field="license_text", identifier="LicenseRef-Example")
    elif source == "license-file":
        metadata["fields"].pop(field)
        metadata["evidence"][0].update(
            field="license",
            value="LICENSES/LicenseRef-Example.txt",
            path="/source/LICENSES/LicenseRef-Example.txt",
            status="candidate",
            text="wrong terms",
        )
        records[0]["corrections"][field]["supersedes"][0]["path"] = metadata["evidence"][0]["path"]
    result = merge_declaration(metadata, records[0])
    assert result["fields"]["license"] == "LicenseRef-Example"
    assert result["license_texts"]["LicenseRef-Example"]["text"] == "correct terms"
    assert not any(item["status"] == "conflicting" for item in result["issues"])
    assert {item["value"] for item in result["evidence"] if item["field"] == field} == {
        "wrong terms",
        "correct terms",
    }


def test_duplicate_manual_identifier_terms_remain_conflicting(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(
        tmp_path,
        scope,
        root,
        licenses=[*custom_license("first terms"), *custom_license("other terms")],
    )
    records, issues = load_declarations(scope, [path])
    assert "license" not in records[0]["fields"]
    assert records[0]["license_texts"] == {}
    result = merge_declaration(upstream("license", "MIT"), records[0])
    assert "license" not in result["fields"]
    assert any(item["field"] == "license" and item["status"] == "conflicting" for item in issues)
    assert {
        item["value"]
        for item in result["evidence"]
        if item["field"] == "license_text:LicenseRef-Example" and item["status"] == "known"
    } == {"first terms", "other terms"}


def test_aggregate_copyright_matches_individual_evidence_notices(tmp_path):
    root, scope = selection(tmp_path)
    first, second = "Copyright 2024 A", "Copyright 2025 B"
    path = declaration(tmp_path, scope, root, copyright=f" {second}\n{first} ")
    records, _ = load_declarations(scope, [path])
    metadata = upstream("copyright", f"{first}\n{second}", evidence_values=[first, second])
    result = merge_declaration(metadata, records[0])
    assert result["fields"]["copyright"] == f"{second}\n{first}"
    assert not any(
        item["field"] == "copyright" and item["status"] == "conflicting"
        for item in result["issues"]
    )
    assert len([item for item in result["evidence"] if item["field"] == "copyright"]) == 3


def test_manual_copyright_aggregate_cannot_overwrite_or_extend_upstream(tmp_path):
    root, scope = selection(tmp_path)
    first, second = "Copyright 2024 A", "Copyright 2025 B"
    path = declaration(tmp_path, scope, root, copyright=f"{first}\n{second}")
    records, _ = load_declarations(scope, [path])
    metadata = upstream("copyright", first)
    result = merge_declaration(metadata, records[0])
    assert "copyright" not in result["fields"]
    assert any(
        item["field"] == "copyright" and item["status"] == "conflicting"
        for item in result["issues"]
    )
    assert {item["value"] for item in result["evidence"] if item["field"] == "copyright"} == {
        first,
        f"{first}\n{second}",
    }
    records[0]["corrections"]["copyright"] = correction_for(metadata["evidence"][0])
    result = merge_declaration(metadata, records[0])
    assert result["fields"]["copyright"] == f"{first}\n{second}"


@pytest.mark.parametrize("change", ["hash", "path", "value", "retired"])
def test_stale_correction_withholds_disputed_value_and_retains_current_agreement(tmp_path, change):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    records, _ = load_declarations(scope, [path])
    metadata = upstream("version", "1")
    records[0]["corrections"]["version"] = correction_for(metadata["evidence"][0])
    if change == "hash":
        metadata["evidence"][0]["sha256"] = "f" * 64
    elif change == "path":
        metadata["evidence"][0]["path"] = "replacement-metadata.txt"
    elif change == "value":
        metadata["evidence"][0]["value"] = metadata["fields"]["version"] = "3"
    else:
        metadata["evidence"][0]["value"] = metadata["fields"]["version"] = "2.2"
    result = merge_declaration(metadata, records[0])
    if change == "retired":
        assert result["fields"]["version"] == metadata["fields"]["version"]
        assert not any(
            item["field"] == "version" and item["status"] == "conflicting"
            for item in result["issues"]
        )
    else:
        assert "version" not in result["fields"]
        assert any(
            item["field"] == "version" and item["status"] == "conflicting"
            for item in result["issues"]
        )
    issue = next(
        item
        for item in result["issues"]
        if item["field"] == "version" and item["status"] == "stale"
    )
    assert issue["status"] == "stale"
    assert "refresh or retire" in issue["reason"]
    manual = next(
        item
        for item in result["evidence"]
        if item["field"] == "version" and item.get("origin") == "manual"
    )
    assert manual["status"] == "stale"
    assert manual["correction"] == records[0]["corrections"]["version"]
    if change == "retired":
        assert issue["current_evidence"] == []


@pytest.mark.parametrize("retired", [False, True])
def test_stale_custom_terms_correction_withholds_disagreement(tmp_path, retired):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, licenses=custom_license("manual terms"))
    records, _ = load_declarations(scope, [path])
    field = "license_text:LicenseRef-Example"
    metadata = upstream(field, "upstream terms")
    metadata["fields"]["license"] = "LicenseRef-Example"
    metadata["fields"]["license_applicability"] = "confirmed"
    metadata["license_texts"] = {
        "LicenseRef-Example": {"path": "metadata-0.txt", "text": "upstream terms"}
    }
    records[0]["corrections"][field] = correction_for(metadata["evidence"][0])
    if retired:
        current = "  manual terms\n"
        metadata["evidence"][0]["value"] = metadata["fields"][field] = current
        metadata["license_texts"]["LicenseRef-Example"]["text"] = current
    else:
        metadata["evidence"][0]["sha256"] = "f" * 64
    result = merge_declaration(metadata, records[0])
    assert any(item["field"] == field and item["status"] == "stale" for item in result["issues"])
    if retired:
        assert result["fields"][field] == current
        assert result["license_texts"]["LicenseRef-Example"]["text"] == current
        assert result["fields"]["license"] == "LicenseRef-Example"
        assert not any(
            item["field"] == field and item["status"] == "conflicting" for item in result["issues"]
        )
    else:
        assert field not in result["fields"]
        assert "LicenseRef-Example" not in result["license_texts"]
        assert "license" not in result["fields"]
        assert "license_applicability" not in result["fields"]
        assert any(
            item["field"] == field and item["status"] == "conflicting" for item in result["issues"]
        )
    assert {item["value"] for item in result["evidence"] if item["field"] == field} == {
        metadata["evidence"][0]["value"],
        "manual terms",
    }


@pytest.mark.parametrize(
    "correction",
    [
        None,
        {},
        {"reason": " ", "supersedes": [{"path": "m", "sha256": "a" * 64, "value": "1"}]},
        {"reason": "why", "supersedes": []},
        {"reason": "why", "supersedes": ["not an object"]},
        {"reason": "why", "supersedes": [{"path": "m", "value": "1"}]},
        {"reason": "why", "supersedes": [{"path": "", "sha256": "a" * 64, "value": "1"}]},
        {"reason": "why", "supersedes": [{"path": "m", "sha256": "A" * 64, "value": "1"}]},
        {"reason": "why", "supersedes": [{"path": "m", "sha256": "a" * 63, "value": "1"}]},
        {"reason": "why", "supersedes": [{"path": "m", "sha256": "a" * 64, "value": " "}]},
        {
            "reason": "why",
            "supersedes": [{"path": "m", "sha256": "a" * 64, "value": "1", "extra": "x"}],
        },
    ],
)
def test_malformed_correction_is_operational_failure(tmp_path, correction):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    data = json.loads(path.read_text())
    data["components"][0]["properties"].append(
        {"name": "mpy:metadata:corrections", "value": json.dumps({"version": correction})}
    )
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="correction"):
        load_declarations(scope, [path])


def test_semantically_equivalent_licenses_do_not_conflict(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root, licenses=[{"expression": "Apache-2.0 OR MIT"}])
    records, _ = load_declarations(scope, [path])
    metadata = upstream("license", "MIT OR Apache-2.0")
    result = merge_declaration(metadata, records[0])
    assert "license" in result["fields"]
    assert not any(
        item["field"] == "license" and item["status"] == "conflicting" for item in result["issues"]
    )


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, " sha256 ", "g" * 64])
def test_source_binding_requires_canonical_sha256(tmp_path, digest):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    data = json.loads(path.read_text())
    data["components"][0]["properties"][2]["value"] = digest
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="canonical SHA256"):
        load_declarations(scope, [path])


def test_current_pinned_correction_loaded_from_document(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    metadata = upstream("version", "1")
    data = json.loads(path.read_text())
    data["components"][0]["properties"].append(
        {
            "name": "mpy:metadata:corrections",
            "value": json.dumps({"version": correction_for(metadata["evidence"][0])}),
        }
    )
    path.write_text(json.dumps(data))
    records, issues = load_declarations(scope, [path])
    assert issues == []
    result = merge_declaration(metadata, records[0])
    assert result["fields"]["version"] == "2.2"
    assert not any(
        item["field"] == "version" and item["status"] in {"stale", "conflicting"}
        for item in result["issues"]
    )


def test_correction_must_pin_every_conflicting_upstream_assertion(tmp_path):
    root, scope = selection(tmp_path)
    path = declaration(tmp_path, scope, root)
    records, _ = load_declarations(scope, [path])
    metadata = upstream("version", "1", evidence_values=["1", "3"])
    metadata["fields"].pop("version")
    metadata["issues"].append({"field": "version", "status": "conflicting"})
    records[0]["corrections"]["version"] = correction_for(metadata["evidence"][0])
    result = merge_declaration(metadata, records[0])
    assert "version" not in result["fields"]
    assert any(
        item["field"] == "version" and item["status"] == "stale" for item in result["issues"]
    )
    records[0]["corrections"]["version"] = correction_for(*metadata["evidence"])
    result = merge_declaration(metadata, records[0])
    assert result["fields"]["version"] == "2.2"
    assert not any(
        item["field"] == "version" and item["status"] in {"stale", "conflicting"}
        for item in result["issues"]
    )


@pytest.mark.parametrize("distribution", [None, "MIT AND Apache-2.0", "BSD-3-Clause"])
def test_manual_license_aggregate_preserves_individual_selected_file_proof(tmp_path, distribution):
    root, scope = selection(tmp_path)
    (root / "module.py").write_text("# SPDX-License-Identifier: MIT\nvalue = 1\n")
    (root / "apache.py").write_text("# SPDX-License-Identifier: Apache-2.0\nvalue = 2\n")
    scope["groups"][FROZEN_GROUP].append(
        {**scope["groups"][FROZEN_GROUP][0], "path": "apache.py", "target_path": "apache.py"}
    )
    if distribution:
        (root / "setup.py").write_text(
            f"from setuptools import setup\nsetup(license={distribution!r})\n"
        )
    path = declaration(tmp_path, scope, root, licenses=[{"expression": "Apache-2.0 AND MIT"}])
    records, _ = load_declarations(scope, [path])
    metadata = collect_metadata(root, selected_files=[root / "module.py", root / "apache.py"])
    result = merge_declaration(metadata, records[0])
    assert {
        (row["value"], row["selected_path"])
        for row in result["evidence"]
        if row.get("field") == "license" and row.get("applicability") == "selected-file"
    } == {("MIT", str(root / "module.py")), ("Apache-2.0", str(root / "apache.py"))}
    assert any(
        row.get("origin") == "manual" and row["value"] == "Apache-2.0 AND MIT"
        for row in result["evidence"]
    )
    if distribution == "BSD-3-Clause":
        assert "license" not in result["fields"]
        assert any(
            item["field"] == "license" and item["status"] == "conflicting"
            for item in result["issues"]
        )
        contrary = [
            row
            for row in metadata["evidence"]
            if row.get("field") == "license" and row.get("applicability") == "distribution"
        ]
        assert [row["value"] for row in contrary] == ["BSD-3-Clause"]
        records[0]["corrections"]["license"] = correction_for(*contrary)
        corrected = merge_declaration(metadata, records[0])
        assert corrected["fields"]["license"] == "Apache-2.0 AND MIT"
        assert not any(
            item["field"] == "license" and item["status"] == "conflicting"
            for item in corrected["issues"]
        )
        assert all(row in corrected["evidence"] for row in metadata["evidence"])
    else:
        assert result["fields"]["license"] == "Apache-2.0 AND MIT"
        assert not any(
            item["field"] == "license" and item["status"] == "conflicting"
            for item in result["issues"]
        )


@pytest.mark.parametrize("expression_conflict", [False, True])
@pytest.mark.parametrize("license_first", [False, True])
def test_exact_custom_text_correction_recomputes_only_its_structural_causes(
    tmp_path, expression_conflict, license_first
):
    root, scope = selection(tmp_path)
    (root / "module.py").write_text("# SPDX-License-Identifier: LicenseRef-Example\n")
    nested = root / "nested"
    nested.mkdir()
    (nested / "other.py").write_text("# SPDX-License-Identifier: LicenseRef-Example\n")
    scope["groups"][FROZEN_GROUP].append(
        {
            **scope["groups"][FROZEN_GROUP][0],
            "path": "nested/other.py",
            "target_path": "nested/other.py",
        }
    )
    for directory, terms in [(root, "first upstream terms"), (nested, "other upstream terms")]:
        (directory / "LICENSES").mkdir()
        (directory / "LICENSES/LicenseRef-Example.txt").write_text(terms)
    if expression_conflict:
        (root / "setup.py").write_text("from setuptools import setup\nsetup(license='MIT')\n")
    path = declaration(tmp_path, scope, root, licenses=custom_license("correct terms"))
    records, _ = load_declarations(scope, [path])
    metadata = collect_metadata(root, selected_files=[root / "module.py", nested / "other.py"])
    field = "license_text:LicenseRef-Example"
    assert any(
        issue["field"] == "license" and field in issue.get("cause_fields", [])
        for issue in metadata["issues"]
    )
    pins = {
        (row["path"], row["sha256"], row["text"])
        for row in metadata["evidence"]
        if row.get("status") == "candidate"
        and row.get("path", "").endswith("/LICENSES/LicenseRef-Example.txt")
    }
    assert {pin[2] for pin in pins} == {"first upstream terms", "other upstream terms"}
    records[0]["corrections"][field] = {
        "reason": "retained terms use the same identifier for different text",
        "supersedes": [
            {"path": path, "sha256": digest, "value": value}
            for path, digest, value in sorted(pins)
        ],
    }
    if license_first:
        fields = records[0]["fields"]
        records[0]["fields"] = {
            "license": fields["license"],
            **{key: value for key, value in fields.items() if key != "license"},
        }
    for issue in metadata["issues"]:
        issue["reason"] = "presentation wording does not identify the issue cause"
    result = merge_declaration(metadata, records[0])
    assert result["license_texts"]["LicenseRef-Example"]["text"] == "correct terms"
    assert all(row in result["evidence"] for row in metadata["evidence"])
    assert {row["value"] for row in result["evidence"] if row["field"] == field} == {
        "first upstream terms",
        "other upstream terms",
        "correct terms",
    }
    assert not any(field in issue.get("cause_fields", []) for issue in result["issues"])
    assert "license" not in records[0]["corrections"]
    if expression_conflict:
        assert "license" not in result["fields"]
        assert "license_applicability" not in result["fields"]
        assert any(
            issue["field"] == "license"
            and issue["status"] == "conflicting"
            and "license" in issue.get("cause_fields", [])
            for issue in result["issues"]
        )
        assert {
            row["value"]
            for row in result["evidence"]
            if row.get("field") == "license" and row.get("status") == "known"
        } == {"MIT", "LicenseRef-Example"}
    else:
        assert result["fields"]["license"] == "LicenseRef-Example"
        assert result["fields"]["license_applicability"] == "confirmed"
        assert not any(
            issue["field"] in {"license", "license_applicability", field}
            for issue in result["issues"]
        )


@pytest.mark.parametrize("retired", [False, True])
def test_stale_copyright_correction_compares_the_current_notice_aggregate(tmp_path, retired):
    root, scope = selection(tmp_path)
    first, second = "Copyright 2024 A", "Copyright 2025 B"
    manual = f"{second}\n{first}"
    path = declaration(tmp_path, scope, root, copyright=manual)
    records, _ = load_declarations(scope, [path])
    metadata = upstream("copyright", first)
    records[0]["corrections"]["copyright"] = correction_for(metadata["evidence"][0])
    if retired:
        metadata = upstream("copyright", f"{first}\n{second}", evidence_values=[first, second])
    else:
        metadata["evidence"][0]["sha256"] = "f" * 64
    result = merge_declaration(metadata, records[0])
    assert any(
        item["field"] == "copyright" and item["status"] == "stale" for item in result["issues"]
    )
    if retired:
        assert result["fields"]["copyright"] == f"{first}\n{second}"
        assert not any(
            item["field"] == "copyright" and item["status"] == "conflicting"
            for item in result["issues"]
        )
    else:
        assert "copyright" not in result["fields"]
        assert any(
            item["field"] == "copyright" and item["status"] == "conflicting"
            for item in result["issues"]
        )
    assert all(row in result["evidence"] for row in metadata["evidence"])


@pytest.mark.parametrize(
    "field", ["version", "copyright", "license", "license_text:LicenseRef-Example"]
)
def test_stale_disputed_correction_is_absent_from_bom_and_explicit_in_facts_matrix(
    tmp_path, field
):
    root, scope = selection(tmp_path)
    scope.update(
        schema_version=1,
        target="fixture",
        variant="test",
        port="unix",
        packages=[],
        package_requirements=[],
    )
    expression = (
        "LicenseRef-Example"
        if field.startswith("license_text:")
        else "Apache-2.0"
        if field == "license"
        else "MIT"
    )
    setup = root / "setup.py"
    setup.write_text(
        "from setuptools import setup\n"
        f"setup(name='declared-component', version='1', license={expression!r})\n"
    )
    sidecar = root / "module.py.license"
    sidecar.write_text(
        f"SPDX-License-Identifier: {expression}\nSPDX-FileCopyrightText: Copyright 2024 Upstream\n"
    )
    overrides = {}
    changed_path = setup
    if field == "copyright":
        overrides["copyright"] = "Copyright 2025 Manual"
        changed_path = sidecar
    elif field.startswith("license_text:"):
        (root / "LICENSES").mkdir()
        changed_path = root / "LICENSES/LicenseRef-Example.txt"
        changed_path.write_text("upstream terms")
        overrides["licenses"] = custom_license("manual terms")
    path = declaration(tmp_path, scope, root, **overrides)
    current = collect_metadata(root, selected_files=[root / "module.py"])
    if field.startswith("license_text:"):
        pins = [
            {**row, "value": row["text"]}
            for row in current["evidence"]
            if row.get("status") == "candidate" and row.get("path") == str(changed_path)
        ]
    else:
        pins = [
            row
            for row in current["evidence"]
            if row.get("field") == field and row.get("status") == "known"
        ]
    pins = list({(row["path"], row["sha256"], row["value"]): row for row in pins}.values())
    assert pins
    correction = correction_for(*pins)
    data = json.loads(path.read_text())
    data["components"][0]["properties"].append(
        {"name": "mpy:metadata:corrections", "value": json.dumps({field: correction})}
    )
    path.write_text(json.dumps(data))
    changed_path.write_text(changed_path.read_text() + "\n")
    document, receipt = sbom.generate_sbom(
        scope, micropython_root=MPY_ROOT, declaration_files=[path]
    )
    row = next(item for item in receipt["metadata"] if item["root"] == "external")
    component = next(
        item for item in document["components"] if item["bom-ref"] == row["component"]
    )
    matrix = row["fields"][field]
    assert matrix["status"] == "conflicting"
    assert {"conflicting", "stale"} <= {issue["status"] for issue in matrix["issues"]}
    manual = next(item for item in matrix["evidence"] if item.get("origin") == "manual")
    assert manual["status"] == "stale"
    assert manual["correction"] == correction
    assert any(item["status"] == "known" for item in matrix["evidence"])
    if field.startswith("license_text:"):
        assert "licenses" not in component
        assert "evidence" not in component
    else:
        assert ("licenses" if field == "license" else field) not in component
