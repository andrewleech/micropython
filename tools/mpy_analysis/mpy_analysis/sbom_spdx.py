"""SPDX 2.3 JSON written from the neutral SBOM model.

Mapping:

* Every component becomes an SPDX package, except components of kind ``file`` that carry a
  SHA-1 hash: those become SPDX files, because SPDX 2.3 requires a SHA-1 checksum on every file.
  A ``file`` component without SHA-1 is a package with purpose FILE and whatever checksums it
  has. Every package has ``downloadLocation``; licence fields are NOASSERTION unless the model
  carries a licence. A package that CONTAINS files has ``filesAnalyzed`` true, a
  ``packageVerificationCode`` over the SHA-1 of those files and ``licenseInfoFromFiles``; every
  other package has ``filesAnalyzed`` false. An SPDX file name is relative, so an absolute host
  path (a toolchain or system file) is written as ``./`` plus the path without its leading slash,
  with a file comment that records the original absolute path.
* A component with a parent is related by CONTAINS from that parent. A top-level file is
  contained by every package, other than firmware, application or file, that depends on it; a
  top-level file with no such package is contained by the document.
* Each ``depends_on`` edge becomes one DEPENDS_ON relationship.
* The root component is DESCRIBED by the document.
* Each soname in ``dynamic_links`` becomes a DYNAMIC_LINK relationship from the owning component
  to a placeholder package named by the soname. A soname has one placeholder per document; it
  has no version and no resolved identity.
* Hashes become checksums, a purl becomes a PACKAGE-MANAGER external reference, a CPE a SECURITY
  one, the supplier becomes the package supplier, and the author the originator. A licence
  expression is the declared licence; retained licence texts become extracted licensing
  information.
* Properties and fields with no SPDX field are kept in one JSON object annotation per element.
  Properties keep their names as ``mpy:<name>``; fields use ``component:<field>``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .metadata_bom import _validate
from .sbom_model import FILE, Component, Model

ANNOTATOR = "Tool: mpy_analysis"
EPOCH = "1970-01-01T00:00:00Z"

_SPDXID = re.compile(r"SPDXRef-[A-Za-z0-9.-]+\Z")
_LICENSE_REF = re.compile(r"LicenseRef-[A-Za-z0-9.-]+\Z")

_CHECKSUM_ALGORITHMS = {
    "md5": "MD5",
    "sha1": "SHA1",
    "sha256": "SHA256",
    "sha384": "SHA384",
    "sha512": "SHA512",
    "sha3-256": "SHA3-256",
    "sha3-384": "SHA3-384",
    "sha3-512": "SHA3-512",
    "blake2b-256": "BLAKE2b-256",
    "blake2b-384": "BLAKE2b-384",
    "blake2b-512": "BLAKE2b-512",
    "blake3": "BLAKE3",
}

_PURPOSES = {
    "application": "APPLICATION",
    "firmware": "FIRMWARE",
    "library": "LIBRARY",
    "framework": "FRAMEWORK",
    "container": "CONTAINER",
    "operating-system": "OPERATING_SYSTEM",
    "device": "DEVICE",
    "file": "FILE",
}

_FILE_TYPES_BY_SUFFIX = {
    "SOURCE": (
        ".c .h .cc .cpp .cxx .hh .hpp .hxx .s .py .pyi .ld .lds .rs .go .java .js .sh .mk "
        ".cmake .pl .rb"
    ),
    "BINARY": ".a .o .elf .bin .mpy .so .dll .dylib .exe .hex .axf .lib",
    "ARCHIVE": ".zip .tar .gz .tgz .xz .bz2 .7z .whl",
    "DOCUMENTATION": ".md .rst .adoc",
    "TEXT": ".txt .json .yaml .yml .toml .cfg .ini .csv .xml .conf",
    "IMAGE": ".png .jpg .jpeg .gif .bmp .svg .ico",
}
_FILE_TYPE_BY_SUFFIX = {
    suffix: kind for kind, suffixes in _FILE_TYPES_BY_SUFFIX.items() for suffix in suffixes.split()
}
_TEXT_NAMES = {"makefile", "readme", "license", "copying", "notice", "authors"}
_SHARED_OBJECT = re.compile(r"\.so(\.\d+)*\Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9.]+", "-", name).strip("-.")[:40].strip("-.")
    return slug or "x"


def _spdx_id(kind: str, name: str, key: str) -> str:
    return f"SPDXRef-{kind}-{_slug(name)}-{_digest(key)[:12]}"


def _checksums(hashes: dict[str, str]) -> list[dict[str, str]]:
    unknown = set(hashes) - set(_CHECKSUM_ALGORITHMS)
    if unknown:
        raise ValueError(f"hash algorithm has no SPDX equivalent: {sorted(unknown)[0]}")
    return [
        {"algorithm": _CHECKSUM_ALGORITHMS[algorithm], "checksumValue": hashes[algorithm].lower()}
        for algorithm in _CHECKSUM_ALGORITHMS
        if algorithm in hashes
    ]


def _contact(prefix: str, name: str, email: str | None) -> str:
    return f"{prefix}: {name} ({email})" if email else f"{prefix}: {name}"


def _file_types(name: str) -> list[str]:
    base = name.rsplit("/", 1)[-1].lower()
    if _SHARED_OBJECT.search(base):
        return ["BINARY"]
    suffix = "." + base.rsplit(".", 1)[-1] if "." in base else ""
    kind = _FILE_TYPE_BY_SUFFIX.get(suffix)
    if kind is None and base in _TEXT_NAMES:
        kind = "TEXT"
    return [kind] if kind else []


def _references(component: Component) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in (
                ("type", item.type),
                ("url", item.url),
                ("comment", item.comment),
                ("hashes", item.hashes or None),
            )
            if value is not None
        }
        for item in component.external_references
    ]


class _Writer:
    def __init__(self, model: Model):
        self.model = model
        self.is_file = {
            ref: item.kind == FILE and "sha1" in item.hashes for ref, item in model.components.items()
        }
        self.ids = {
            ref: _spdx_id("File" if self.is_file[ref] else "Package", item.name, ref)
            for ref, item in model.components.items()
        }
        self.extracted: dict[str, dict[str, Any]] = {}
        self.relationships: dict[tuple[str, str, str], str | None] = {}
        self.placeholders: dict[str, str] = {}

    def relate(self, left: str, kind: str, right: str, comment: str | None = None) -> None:
        self.relationships.setdefault((left, kind, right), comment)

    def ordered(self) -> list[Component]:
        """The root, then top-level components by reference, each followed by its descendants."""
        model = self.model
        nested = model.nested()
        result: list[Component] = []

        def visit(item: Component) -> None:
            result.append(item)
            for child in sorted(nested.get(item.ref, []), key=lambda entry: (entry.name, entry.ref)):
                visit(child)

        visit(model.components[model.root])
        for ref in sorted(model.components):
            item = model.components[ref]
            if ref != model.root and item.parent is None:
                visit(item)
        return result

    # Annotations

    def annotation(self, content: dict[str, Any]) -> dict[str, str]:
        return {
            "annotationDate": EPOCH,
            "annotationType": "OTHER",
            "annotator": ANNOTATOR,
            "comment": json.dumps(content, sort_keys=True),
        }

    def annotations(self, component: Component, extra: dict[str, Any]) -> list[dict[str, str]]:
        content = {f"mpy:{name}": value for name, value in component.properties.items()}
        content.update({key: value for key, value in extra.items() if value not in (None, [], {})})
        return [self.annotation(content)] if content else []

    # Licences

    def licenses(self, component: Component) -> str:
        """Record the component's retained licence texts and return its declared licence."""
        for name, text in sorted(component.license_texts.items()):
            identifier = name
            if not _LICENSE_REF.fullmatch(identifier):
                identifier = f"LicenseRef-{_slug(name)}-{_digest(name)[:8]}"
            entry: dict[str, Any] = {"licenseId": identifier, "extractedText": text}
            if identifier != name:
                entry["name"] = name
            if self.extracted.setdefault(identifier, entry) != entry:
                raise ValueError(f"conflicting licence text for {identifier}")
        return component.license or "NOASSERTION"

    # Elements

    def package(self, component: Component) -> dict[str, Any]:
        purpose = _PURPOSES.get(component.kind, "OTHER")
        extra: dict[str, Any] = {
            "component:group": component.group,
            "component:publisher": component.publisher,
        }
        if purpose == "OTHER":
            extra["component:kind"] = component.kind
        package: dict[str, Any] = {
            "SPDXID": self.ids[component.ref],
            "name": component.name,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "primaryPackagePurpose": purpose,
            "copyrightText": component.copyright or "NOASSERTION",
        }
        if component.version:
            package["versionInfo"] = component.version
        if component.description:
            package["description"] = component.description
        if component.comment:
            package["comment"] = component.comment
        if component.hashes:
            package["checksums"] = _checksums(component.hashes)
        supplier = component.supplier
        if supplier and supplier.name:
            email = next((item.email for item in supplier.contacts if item.email), None)
            package["supplier"] = _contact("Organization", supplier.name, email)
        if component.author:
            package["originator"] = f"Person: {component.author}"
        refs = []
        if component.purl:
            refs.append(
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": component.purl,
                }
            )
        cpe = component.cpe
        if cpe and cpe.startswith(("cpe:2.3:", "cpe:/")):
            refs.append(
                {
                    "referenceCategory": "SECURITY",
                    "referenceType": "cpe23Type" if cpe.startswith("cpe:2.3:") else "cpe22Type",
                    "referenceLocator": cpe,
                }
            )
        else:
            extra["component:cpe"] = cpe
        for reference in component.external_references:
            if reference.type == "website" and "homepage" not in package:
                package["homepage"] = reference.url
            else:
                item = {
                    "referenceCategory": "OTHER",
                    "referenceType": reference.type,
                    "referenceLocator": reference.url,
                }
                if reference.comment:
                    item["comment"] = reference.comment
                refs.append(item)
        extra["component:external-reference-hashes"] = [
            {"url": reference.url, "hashes": reference.hashes}
            for reference in component.external_references
            if reference.hashes
        ]
        if refs:
            package["externalRefs"] = refs
        package["licenseDeclared"] = self.licenses(component)
        package["licenseConcluded"] = "NOASSERTION"
        annotations = self.annotations(component, extra)
        if annotations:
            package["annotations"] = annotations
        return package

    def file(self, component: Component) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "component:version": component.version,
            "component:purl": component.purl,
            "component:cpe": component.cpe,
            "component:group": component.group,
            "component:publisher": component.publisher,
            "component:supplier": component.supplier.name if component.supplier else None,
            "component:external-references": _references(component),
        }
        entry: dict[str, Any] = {
            "SPDXID": self.ids[component.ref],
            "fileName": _file_name(component.name),
            "checksums": _checksums(component.hashes),
            "copyrightText": component.copyright or "NOASSERTION",
        }
        kinds = _file_types(component.name)
        if kinds:
            entry["fileTypes"] = kinds
        comment = "\n".join(
            item
            for item in (
                component.description,
                component.comment,
                f"Absolute path on the build host: {component.name}"
                if component.name.startswith("/")
                else None,
            )
            if item
        )
        if comment:
            entry["comment"] = comment
        if component.author:
            entry["fileContributors"] = [component.author]
        entry["licenseInfoInFiles"] = [self.licenses(component)]
        entry["licenseConcluded"] = "NOASSERTION"
        annotations = self.annotations(component, extra)
        if annotations:
            entry["annotations"] = annotations
        return entry

    def placeholder(self, soname: str) -> str:
        if soname not in self.placeholders:
            self.placeholders[soname] = _spdx_id("Package", soname, f"dynamic-needed:{soname}")
        return self.placeholders[soname]

    def _analyse_contained_files(
        self, packages: list[dict[str, Any]], files: list[dict[str, Any]]
    ) -> None:
        """Mark each package that CONTAINS files as analysed, with its verification code."""
        by_id = {item["SPDXID"]: item for item in files}
        contained: dict[str, set[str]] = {}
        for left, kind, right in self.relationships:
            if kind == "CONTAINS" and right in by_id:
                contained.setdefault(left, set()).add(right)
        for package in packages:
            members = contained.get(package["SPDXID"])
            if not members:
                continue
            sha1s = [
                checksum["checksumValue"]
                for identifier in members
                for checksum in by_id[identifier]["checksums"]
                if checksum["algorithm"] == "SHA1"
            ]
            licenses = sorted(
                {
                    expression
                    for identifier in members
                    for expression in by_id[identifier]["licenseInfoInFiles"]
                }
            )
            package["filesAnalyzed"] = True
            package["packageVerificationCode"] = {
                "packageVerificationCodeValue": _verification_code(sha1s)
            }
            package["licenseInfoFromFiles"] = licenses

    # Document

    def write(self) -> dict[str, Any]:
        model = self.model
        root = model.components[model.root]
        packages = []
        files = []
        for component in self.ordered():
            if self.is_file[component.ref]:
                files.append(self.file(component))
            else:
                packages.append(self.package(component))
            if component.parent is not None:
                self.relate(self.ids[component.parent], "CONTAINS", self.ids[component.ref])

        owned: set[str] = set()
        for owner in sorted(model.depends_on):
            for target in sorted(model.depends_on[owner]):
                self.relate(self.ids[owner], "DEPENDS_ON", self.ids[target])
                if (
                    model.components[target].parent is None
                    and model.components[target].kind == FILE
                    and model.components[owner].kind not in ("firmware", "application", FILE)
                ):
                    self.relate(self.ids[owner], "CONTAINS", self.ids[target])
                    owned.add(target)
        for ref, component in model.components.items():
            if (
                component.parent is None
                and component.kind == FILE
                and ref != model.root
                and ref not in owned
            ):
                self.relate(
                    "SPDXRef-DOCUMENT",
                    "CONTAINS",
                    self.ids[ref],
                    "file component not owned by any package",
                )
        self.relate("SPDXRef-DOCUMENT", "DESCRIBES", self.ids[root.ref])
        self._analyse_contained_files(packages, files)
        for ref in sorted(model.dynamic_links):
            for soname in model.dynamic_links[ref]:
                self.relate(self.ids[ref], "DYNAMIC_LINK", self.placeholder(soname))

        for soname in sorted(self.placeholders):
            packages.append(
                {
                    "SPDXID": self.placeholders[soname],
                    "name": soname,
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                    "primaryPackagePurpose": "LIBRARY",
                    "copyrightText": "NOASSERTION",
                    "licenseDeclared": "NOASSERTION",
                    "licenseConcluded": "NOASSERTION",
                    "comment": "Placeholder for a DT_NEEDED soname; the providing package is not resolved.",
                }
            )

        result: dict[str, Any] = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": model.name,
            "creationInfo": {"created": EPOCH, "creators": [ANNOTATOR]},
            "packages": packages,
            "relationships": [
                {
                    "spdxElementId": left,
                    "relationshipType": kind,
                    "relatedSpdxElement": right,
                    **({"comment": comment} if comment else {}),
                }
                for (left, kind, right), comment in sorted(self.relationships.items())
            ],
        }
        if files:
            result["files"] = files
        if self.extracted:
            result["hasExtractedLicensingInfos"] = [
                self.extracted[key] for key in sorted(self.extracted)
            ]
        result["documentNamespace"] = (
            f"https://spdx.org/spdxdocs/mpy-analysis-{_digest(_canonical(result))}"
        )
        return result


def to_spdx(model: Model) -> dict[str, Any]:
    """Return the validated SPDX 2.3 JSON document for the model."""
    model.check()
    result = _Writer(model).write()
    validate_spdx(result)
    return result


def validate_spdx(document: dict[str, Any]) -> None:
    """Raise ValueError unless the document satisfies the bundled SPDX 2.3 schema and its
    identifier and relationship integrity rules."""
    _validate(document, ("spdx-2.3.schema.json",))
    identifiers = [document["SPDXID"]]
    identifiers += [item["SPDXID"] for item in document.get("packages", [])]
    identifiers += [item["SPDXID"] for item in document.get("files", [])]
    for identifier in identifiers:
        if identifier != "SPDXRef-DOCUMENT" and not _SPDXID.fullmatch(identifier):
            raise ValueError(f"invalid SPDX identifier: {identifier}")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate SPDX element identifier")
    file_sha1: dict[str, str] = {}
    for item in document.get("files", []):
        sha1 = next(
            (c["checksumValue"] for c in item.get("checksums", []) if c["algorithm"] == "SHA1"), None
        )
        if sha1 is None:
            raise ValueError(f"SPDX file lacks the required SHA1 checksum: {item['fileName']}")
        if item["fileName"].startswith("/"):
            raise ValueError(f"SPDX file name must be relative: {item['fileName']}")
        file_sha1[item["SPDXID"]] = sha1
    known = set(identifiers)
    for relationship in document.get("relationships", []):
        for field in ("spdxElementId", "relatedSpdxElement"):
            if relationship[field] not in known:
                raise ValueError(
                    f"SPDX relationship references unknown element: {relationship[field]}"
                )
    contained: dict[str, set[str]] = {}
    for relationship in document.get("relationships", []):
        if relationship["relationshipType"] == "CONTAINS" and (
            relationship["relatedSpdxElement"] in file_sha1
        ):
            contained.setdefault(relationship["spdxElementId"], set()).add(
                relationship["relatedSpdxElement"]
            )
    for item in document.get("packages", []):
        members = contained.get(item["SPDXID"], set())
        if members and not item.get("filesAnalyzed"):
            raise ValueError(f"SPDX package contains files but filesAnalyzed is false: {item['name']}")
        if item.get("filesAnalyzed"):
            code = item.get("packageVerificationCode", {}).get("packageVerificationCodeValue")
            if code != _verification_code([file_sha1[identifier] for identifier in members]):
                raise ValueError(f"SPDX package verification code does not match: {item['name']}")
    licenses = [item["licenseId"] for item in document.get("hasExtractedLicensingInfos", [])]
    if len(set(licenses)) != len(licenses):
        raise ValueError("duplicate SPDX extracted licence identifier")
    for item in licenses:
        if not _LICENSE_REF.fullmatch(item):
            raise ValueError(f"invalid SPDX extracted licence identifier: {item}")


def serialize_spdx(document: dict[str, Any]) -> bytes:
    """Serialise as sbom.spdx.json is written: sorted keys, indent 2, trailing newline."""
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _file_name(name: str) -> str:
    return "./" + name.lstrip("/") if name.startswith("/") else name


def _verification_code(file_checksums: list[str]) -> str:
    return hashlib.sha1("".join(sorted(file_checksums)).encode("ascii")).hexdigest()
