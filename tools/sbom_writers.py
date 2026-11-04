# This file is part of the MicroPython project, http://micropython.org/
#
# SPDX-License-Identifier: MIT

"""Serialize the shared dependency inventory as SPDX or CycloneDX JSON."""

import hashlib
import io
import json
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cyclonedx.model import AttachedText, ExternalReference, ExternalReferenceType, Property, XsUri
from cyclonedx.model.bom import Bom, BomMetaData
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.model.component_evidence import ComponentEvidence
from cyclonedx.model.license import DisjunctiveLicense, LicenseAcknowledgement, LicenseExpression
from cyclonedx.model.lifecycle import LifecyclePhase, PredefinedLifecycle
from cyclonedx.output.json import JsonV1Dot6
from packageurl import PackageURL
from spdx_tools.common.spdx_licensing import spdx_licensing
from spdx_tools.spdx.model import (
    Actor,
    ActorType,
    Annotation,
    AnnotationType,
    CreationInfo,
    Checksum,
    ChecksumAlgorithm,
    Document,
    ExternalPackageRef,
    ExternalPackageRefCategory,
    ExtractedLicensingInfo,
    File,
    Package,
    Relationship,
    RelationshipType,
    SpdxNoAssertion,
)
from spdx_tools.spdx.writer.json.json_writer import write_document_to_stream


_TOOL = "micropython-sbom"
_NO_ASSERTION = SpdxNoAssertion()
_BUILD_SCOPE_DESCRIPTION = (
    "Components are selected build inputs; their presence does not assert that they are linked into "
    "the image or present at runtime."
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_:/\.])/(?!/)[^\s\"'<>(),;]+")
_PATH_FIELDS = {
    "path", "source", "sources", "originating_source", "object", "depfile", "owner_root", "declaration_root",
}


def _portable_model(model, root):
    """Replace host paths without changing the inventory used by the caller."""
    root = os.path.normpath(os.fspath(root))
    roots = [(root, ".")]
    external = {}

    def add_external(path):
        path = os.path.normpath(path)
        if path == root or path.startswith(root + os.sep):
            return
        if any(path == base or path.startswith(base + os.sep) for base in external):
            return
        external[path] = f"external-{len(external) + 1}"

    def collect(value, key=None):
        if isinstance(value, dict):
            for field, child in value.items():
                if field not in ("licence_texts", "license_texts"):
                    collect(child, field)
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and key in ("owner_root", "declaration_root") and os.path.isabs(value):
            add_external(value)

    collect(model)
    roots.extend(external.items())

    def portable_path(path):
        normalized = os.path.normpath(path)
        for base, label in sorted(roots, key=lambda item: len(item[0]), reverse=True):
            if normalized == base:
                return label
            if normalized.startswith(base + os.sep):
                suffix = normalized[len(base) + 1:].replace(os.sep, "/")
                return suffix if label == "." else label + "/" + suffix
        directory, name = os.path.split(normalized)
        if directory not in external:
            external[directory] = f"external-{len(external) + 1}"
            roots.append((directory, external[directory]))
        return external[directory] + "/" + name

    def convert(value, key=None):
        if isinstance(value, dict):
            return {
                field: child if field in ("licence_texts", "license_texts") else convert(child, field)
                for field, child in value.items()
            }
        if isinstance(value, list):
            return [convert(child, key) for child in value]
        if isinstance(value, str):
            if key in _PATH_FIELDS and os.path.isabs(value):
                return portable_path(value)

            def replace(match):
                path = match.group()
                trimmed = path.rstrip(".:")
                return portable_path(trimmed) + path[len(trimmed):]

            for base, label in sorted(roots, key=lambda item: len(item[0]), reverse=True):
                value = re.sub(
                    r"(?<![A-Za-z0-9_:/])" + re.escape(base) + r"(?=$|[/\s\"'<>(),;:.])",
                    lambda match: label, value,
                )
            return _ABSOLUTE_PATH.sub(replace, value)
        return value

    return convert(model)


def _identity(index, component):
    # The index also distinguishes otherwise identical records in the input.
    digest = hashlib.sha256(_json(component).encode("utf-8")).hexdigest()[:16]
    return f"{index}-{digest}"


def _provenance(component, concluded):
    provenance = {
        key: component[key]
        for key in (
            "path", "revision", "selection", "sources", "licence_comments", "licence_details",
            "properties", "externalReferences", "manifest", "manifest_license", "kind",
            "declaration_root", "owner_root", "scope", "group", "licence_declared",
            "licence_status", "licence_issues", "licence_mixed", "file_licences", "source_evidence",
        )
        if key in component
    }
    if "licence_declared" in component:
        provenance["licence_declared"] = _declared_licence(component)
    if "licence_concluded" in component:
        provenance["licence_concluded"] = concluded
    return provenance


def _components(components, prefix=""):
    for index, item in enumerate(components):
        identity = prefix + _identity(index, item)
        yield identity, item, prefix
        yield from _components(item.get("components", []), identity + "/")


def _extracted_licences(model):
    extracted = {}
    for _, item, _ in _components(model["components"]):
        for identifier, text in item["licence_texts"].items():
            if identifier in extracted and extracted[identifier] != text:
                raise ValueError(f"Conflicting extracted text for {identifier}")
            extracted[identifier] = text
    return extracted


def _purl(item):
    return str(PackageURL.from_string(item["purl"])) if item.get("purl") else None


def _write_output(path, output):
    if str(path) == "-":
        sys.stdout.write(output)
        return
    path = Path(path)
    try:
        target_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        target_mode = None
    temporary = None
    try:
        while True:
            candidate = path.parent / (".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
            try:
                stream = open(candidate, "x", encoding="utf-8")
            except FileExistsError:
                continue
            temporary = candidate
            break
        with stream:
            stream.write(output)
        if target_mode is not None:
            os.chmod(temporary, target_mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _declared_licence(item):
    issues = item.get("licence_issues", [])
    if any(issue.get("kind") not in ("licence_gap", "declaration_identity") for issue in issues):
        return None
    if any(issue.get("kind") == "declaration_identity" for issue in issues):
        files = item.get("file_licences", [])
        if not item.get("licence_declared") or not files or not all(file.get("trusted") for file in files):
            return None
    if item["licence"] is None and not issues:
        return None
    return item.get("licence_declared", item["licence"])


def _licence_comments(item):
    comments = list(item["licence_comments"])
    for file in item.get("file_licences", []):
        expression = file.get("expression") or "NOASSERTION"
        provenance = file.get("provenance") or "unknown"
        comments.append(f"{file['source']}: {expression} (provenance: {provenance}; trusted: {file.get('trusted', False)})")
    return comments


def _same_licence_terms(left, right):
    return (
        {symbol.key for symbol in spdx_licensing.license_symbols(left)}
        == {symbol.key for symbol in spdx_licensing.license_symbols(right)}
        and spdx_licensing.is_equivalent(left, right)
    )


def _safe_conclusions(model, original, root):
    """Check conclusions against the selected records, including shared sources."""
    items = list(_components(model["components"]))
    originals = list(_components(original["components"]))
    if len(items) != len(originals):
        return {}

    sources = {}
    records = {}
    for (identity, item, _), (_, original_item, _) in zip(items, originals):
        evidence = item.get("file_licences", [])
        original_evidence = original_item.get("file_licences", [])
        if len(evidence) != len(original_evidence):
            return {}
        keys = []
        valid = bool(evidence) and not item.get("licence_issues")
        for file, original_file in zip(evidence, original_evidence):
            source = original_file.get("source")
            expression = file.get("expression")
            trusted = bool(file.get("trusted") and expression)
            if (not source or file.get("expression") != original_file.get("expression")
                    or file.get("trusted") != original_file.get("trusted")):
                valid = False
                continue
            source = Path(source)
            if not source.is_absolute():
                source = (root if root is not None else Path.cwd()) / source
            source = source.resolve()
            keys.append(source)
            licence = spdx_licensing.parse(expression) if trusted else None
            if source not in sources:
                sources[source] = [licence, not trusted]
            else:
                previous = sources[source]
                if not trusted or previous[0] is None or not _same_licence_terms(previous[0], licence):
                    previous[1] = True
            valid &= trusted
        records[identity] = (item, evidence, keys, valid)

    conclusions = {}
    for identity, (item, evidence, keys, valid) in records.items():
        conclusion = item.get("licence_concluded")
        if not conclusion or not valid or any(sources[key][1] for key in keys):
            continue
        selected = " AND ".join(f"({file['expression']})" for file in evidence)
        if _same_licence_terms(spdx_licensing.parse(conclusion), spdx_licensing.parse(selected)):
            conclusions[identity] = conclusion
    return conclusions


def _write_spdx(model, path, extracted, original, root, conclusions):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    tool = Actor(ActorType.TOOL, _TOOL)
    build_inputs = bool(model.get("subject", {}).get("image"))
    subject = model.get("subject", {})
    packages = [
        Package(
            spdx_id="SPDXRef-MicroPython",
            name="MicroPython",
            version=subject.get("repo_version"),
            download_location=_NO_ASSERTION,
            files_analyzed=False,
            license_concluded=_NO_ASSERTION,
            license_declared=_NO_ASSERTION,
            copyright_text=_NO_ASSERTION,
            comment=_BUILD_SCOPE_DESCRIPTION if build_inputs else None,
        )
    ]
    relationships = [Relationship("SPDXRef-DOCUMENT", RelationshipType.DESCRIBES, "SPDXRef-MicroPython")]
    scope = dict(subject)
    if build_inputs:
        scope.update(scope="build-input", scope_description=_BUILD_SCOPE_DESCRIPTION)
    annotations = [Annotation("SPDXRef-MicroPython", AnnotationType.OTHER, tool, now, _json(scope))]
    details = {}
    files = []
    file_evidence = {}
    original_items = iter(_components(original["components"]))
    for identity, item, parent in _components(model["components"]):
        _, original_item, _ = next(original_items)
        identifier = "SPDXRef-Package-" + identity.replace("/", "-")
        references = []
        purl = _purl(item)
        if purl:
            references.append(ExternalPackageRef(ExternalPackageRefCategory.PACKAGE_MANAGER, "purl", purl))
        if item.get("cpe"):
            references.append(ExternalPackageRef(ExternalPackageRefCategory.SECURITY, "cpe23Type", item["cpe"]))
        declared = _declared_licence(item)
        concluded = conclusions.get(identity)
        comments = _licence_comments(item)
        if concluded and (not declared or not _same_licence_terms(
            spdx_licensing.parse(concluded), spdx_licensing.parse(declared)
        )):
            comments.append("Licence conclusion covers the selected source files, not the entire dependency.")
        packages.append(
            Package(
                spdx_id=identifier,
                name=item["name"],
                version=item["version"],
                download_location=_NO_ASSERTION,
                files_analyzed=False,
                license_concluded=spdx_licensing.parse(concluded) if concluded else _NO_ASSERTION,
                license_declared=spdx_licensing.parse(declared) if declared else _NO_ASSERTION,
                license_comment="\n".join(comments) or None,
                copyright_text=_NO_ASSERTION,
                external_references=references,
            )
        )
        if parent:
            parent_id = "SPDXRef-Package-" + parent.rstrip("/").replace("/", "-")
            relationships.append(Relationship(parent_id, RelationshipType.CONTAINS, identifier))
        elif build_inputs:
            # Build selection does not establish that a component is linked into the image.
            relationships.append(Relationship(identifier, RelationshipType.BUILD_DEPENDENCY_OF, "SPDXRef-MicroPython"))
        else:
            relationships.append(Relationship("SPDXRef-MicroPython", RelationshipType.CONTAINS, identifier))
        annotations.append(Annotation(identifier, AnnotationType.OTHER, tool, now, _json(_provenance(item, concluded))))
        if build_inputs:
            for index, (evidence, original_evidence) in enumerate(zip(
                item.get("file_licences", []), original_item.get("file_licences", [])
            )):
                expression = evidence.get("expression")
                trusted = bool(evidence.get("trusted") and expression)
                source = Path(original_evidence["source"])
                if not source.is_absolute():
                    if root is None:
                        raise ValueError("root is required for relative build-input source paths")
                    source = root / source
                source = source.resolve()
                if source.is_dir():
                    continue
                licence = spdx_licensing.parse(expression) if trusted else None
                entry = file_evidence.get(source)
                if entry is None:
                    entry = file_evidence[source] = {
                        "evidence": evidence, "licence": licence, "identity": identity,
                        "identifier": identifier, "index": index, "conflict": not trusted,
                        "in_file": trusted and evidence.get("provenance") in (
                            "source SPDX header", "source MIT header"
                        ),
                    }
                else:
                    if not trusted:
                        entry["conflict"] = True
                    elif entry["licence"] is None:
                        entry["licence"] = licence
                    elif not _same_licence_terms(entry["licence"], licence):
                        entry["conflict"] = True
                    entry["in_file"] |= trusted and evidence.get("provenance") in (
                        "source SPDX header", "source MIT header"
                    )
                    if identity.count("/") > entry["identity"].count("/"):
                        entry.update(evidence=evidence, identity=identity, identifier=identifier, index=index)
        for licence_id in item["licence_texts"]:
            detail = item.get("licence_details", {}).get(licence_id, {})
            entry = details.setdefault(licence_id, {"names": set(), "comments": set(), "urls": set()})
            if detail.get("name"):
                entry["names"].add(detail["name"])
            if detail.get("comment"):
                entry["comments"].add(detail["comment"])
            entry["urls"].update(detail.get("seeAlso", ()))
    for source, entry in file_evidence.items():
        if entry["licence"] is None:
            continue
        digest = hashlib.sha1()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        name = entry["evidence"]["source"].replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            name = "external-source-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        file_id = "SPDXRef-File-" + entry["identity"].replace("/", "-") + "-" + str(entry["index"])
        licence = _NO_ASSERTION if entry["conflict"] else entry["licence"]
        files.append(File(
            name=name,
            spdx_id=file_id,
            checksums=[Checksum(ChecksumAlgorithm.SHA1, digest.hexdigest())],
            license_concluded=licence,
            license_info_in_file=[licence if entry["in_file"] else _NO_ASSERTION],
            copyright_text=_NO_ASSERTION,
        ))
        relationships.append(Relationship(
            file_id, RelationshipType.OTHER, entry["identifier"],
            comment="Selected source file provides licence evidence for this build input.",
        ))
    if model["issues"]:
        annotations.append(
            Annotation("SPDXRef-DOCUMENT", AnnotationType.OTHER, tool, now, _json({"issues": model["issues"]}))
        )
    licensing_info = []
    for identifier, text in sorted(extracted.items()):
        detail = details.get(identifier, {})
        names = sorted(detail.get("names", ()))
        comments = sorted(detail.get("comments", ()))
        if len(names) > 1:
            comments.append("Other names: " + "; ".join(names[1:]))
        licensing_info.append(
            ExtractedLicensingInfo(
                identifier, text, license_name=names[0] if names else None,
                cross_references=sorted(detail.get("urls", ())), comment="\n".join(comments) or None,
            )
        )
    seed = _json(model)
    document = Document(
        CreationInfo(
            spdx_version="SPDX-2.3",
            spdx_id="SPDXRef-DOCUMENT",
            name=model["name"],
            document_namespace="https://spdx.org/spdxdocs/micropython-" + hashlib.sha256(seed.encode("utf-8")).hexdigest(),
            creators=[tool],
            created=now,
            document_comment=_BUILD_SCOPE_DESCRIPTION if build_inputs else None,
        ),
        packages=packages,
        files=files,
        relationships=relationships,
        annotations=annotations,
        extracted_licensing_info=licensing_info,
    )
    stream = io.StringIO()
    write_document_to_stream(document, stream, validate=True)
    _write_output(path, stream.getvalue() + "\n")


def _cyclonedx_component(item, identity, conclusions):
    concluded = conclusions.get(identity)
    provenance = _provenance(item, concluded)
    properties = [Property(name="micropython:" + key, value=_json(value)) for key, value in provenance.items()]
    properties.extend(Property(name=prop["name"], value=prop["value"]) for prop in item.get("properties", []))
    evidence = None
    if item["licence_texts"]:
        licences = []
        for identifier, text in sorted(item["licence_texts"].items()):
            detail = item.get("licence_details", {}).get(identifier, {})
            urls = detail.get("seeAlso", ())
            licence_properties = [Property(name="micropython:licence-id", value=identifier)]
            if detail.get("comment"):
                licence_properties.append(Property(name="micropython:licence-comment", value=detail["comment"]))
            licences.append(
                DisjunctiveLicense(
                    name=detail.get("name") or identifier, text=AttachedText(content=text),
                    url=XsUri(urls[0]) if urls else None, properties=licence_properties,
                )
            )
        evidence = ComponentEvidence(licenses=licences)
    declared = _declared_licence(item)
    return Component(
        name=item["name"],
        group=item.get("group"),
        type=ComponentType.LIBRARY,
        bom_ref="component-" + identity,
        version=item["version"],
        purl=PackageURL.from_string(_purl(item)) if item.get("purl") else None,
        cpe=item.get("cpe"),
        licenses=([LicenseExpression(declared, acknowledgement=LicenseAcknowledgement.DECLARED)] if declared else
                  [LicenseExpression(concluded, acknowledgement=LicenseAcknowledgement.CONCLUDED)] if concluded else None),
        evidence=evidence,
        properties=properties,
        external_references=[
            ExternalReference(type=ExternalReferenceType(ref["type"]), url=XsUri(ref["url"]))
            for ref in item.get("externalReferences", [])
        ] + [
            ExternalReference(type=ExternalReferenceType.LICENSE, url=XsUri(url))
            for url in sorted({
                url
                for identifier in item["licence_texts"]
                for url in item.get("licence_details", {}).get(identifier, {}).get("seeAlso", ())
            })
        ],
        components=[
            _cyclonedx_component(child, identity + "/" + _identity(index, child), conclusions)
            for index, child in enumerate(item.get("components", []))
        ],
    )


def _write_cyclonedx(model, path, conclusions):
    subject = model.get("subject", {})
    build_inputs = bool(subject.get("image"))
    components = [
        _cyclonedx_component(item, _identity(index, item), conclusions)
        for index, item in enumerate(model["components"])
    ]
    bom = Bom(
        metadata=BomMetaData(
            component=Component(
                name="MicroPython",
                type=ComponentType.APPLICATION,
                bom_ref="micropython",
                version=subject.get("repo_version"),
                properties=[
                    Property(name="micropython:" + key, value=value)
                    for key, value in subject.items() if value is not None
                ] + ([Property(name="micropython:scope", value="build-input"),
                      Property(name="micropython:scope_description", value=_BUILD_SCOPE_DESCRIPTION)]
                     if build_inputs else []),
            ),
            lifecycles=[PredefinedLifecycle(LifecyclePhase.BUILD)] if build_inputs else None,
            properties=[
                Property(name="micropython:document-name", value=model["name"]),
                Property(name="micropython:issues", value=_json(model["issues"])),
            ]
        ),
        components=components,
        serial_number=uuid.uuid5(uuid.NAMESPACE_URL, _json(model)),
    )

    def register_children(parent, children):
        bom.register_dependency(parent, children)
        for child in children:
            if child.components:
                register_children(child, child.components)

    if build_inputs:
        # Preserve collection relationships without asserting runtime dependencies of the image.
        for component in components:
            if component.components:
                register_children(component, component.components)
    else:
        register_children(bom.metadata.component, components)
    output = JsonV1Dot6(bom).output_as_string(indent=2) + "\n"
    _write_output(path, output)


def write_sbom(model, path, format, *, root=None):
    """Write the supplied inventory as an SPDX 2.3 or CycloneDX 1.6 JSON document."""
    root = Path(root) if root is not None else None
    original = model
    model = _portable_model(model, root if root is not None else Path(__file__).resolve().parent.parent)
    conclusions = _safe_conclusions(model, original, root)
    extracted = _extracted_licences(model)
    if format == "spdx":
        _write_spdx(model, path, extracted, original, root, conclusions)
    elif format == "cyclonedx":
        _write_cyclonedx(model, path, conclusions)
    else:
        raise ValueError(f"Unsupported SBOM format: {format}")
