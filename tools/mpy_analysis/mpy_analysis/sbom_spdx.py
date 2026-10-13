"""SPDX 2.3 JSON derived from the CycloneDX 1.6 SBOM document.

The conversion reads only the CycloneDX document, so the two formats cannot drift.

Mapping:

* ``metadata.component`` and every entry of ``components`` become SPDX packages, except
  components of type ``file`` that carry a hash: those become SPDX files.
* Nested components become CONTAINS relationships from the enclosing component. A top-level
  file is contained by every non-firmware, non-application package that depends on it; a file
  with no such package is contained by the document.
* Each CycloneDX ``dependsOn`` edge becomes one DEPENDS_ON relationship.
* The root component is DESCRIBED by the document.
* A ``mpy:dynamic-needed`` property (a JSON list of sonames) becomes one DYNAMIC_LINK
  relationship per soname to a placeholder package named by the soname. The placeholder has no
  version and no resolved identity.
* Hashes become checksums, a purl becomes a PACKAGE-MANAGER externalRef, ``supplier`` becomes
  the package supplier, and licences map only when they are SPDX identifiers, expressions, or
  ``LicenseRef-`` licences that carry their text. An absent licence is NOASSERTION.
* Every property and every CycloneDX field with no native SPDX field is kept in package or file
  annotations as one JSON object comment. ``mpy:*`` properties keep their names and string
  values, so the object is what ``metadata_bom`` reads back as the source binding. Other fields
  are kept under ``cyclonedx:<field>``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from .metadata_bom import _validate, validate_cyclonedx

ANNOTATOR = "Tool: mpy_analysis"
DYNAMIC_NEEDED = "mpy:dynamic-needed"
EPOCH = "1970-01-01T00:00:00Z"

_SPDXID = re.compile(r"SPDXRef-[A-Za-z0-9.-]+\Z")
_LICENSE_REF = re.compile(r"LicenseRef-[A-Za-z0-9.-]+\Z")

_CHECKSUM_ALGORITHMS = {
    "MD5": "MD5",
    "SHA-1": "SHA1",
    "SHA-256": "SHA256",
    "SHA-384": "SHA384",
    "SHA-512": "SHA512",
    "SHA3-256": "SHA3-256",
    "SHA3-384": "SHA3-384",
    "SHA3-512": "SHA3-512",
    "BLAKE2b-256": "BLAKE2b-256",
    "BLAKE2b-384": "BLAKE2b-384",
    "BLAKE2b-512": "BLAKE2b-512",
    "BLAKE3": "BLAKE3",
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

# Component keys with a native SPDX home (or consumed structurally). Every other key is kept
# in the annotation.
_NATIVE_PACKAGE_KEYS = frozenset(
    {
        "type",
        "bom-ref",
        "name",
        "version",
        "description",
        "copyright",
        "hashes",
        "purl",
        "licenses",
        "supplier",
        "author",
        "authors",
        "externalReferences",
        "components",
        "properties",
    }
)
_NATIVE_FILE_KEYS = frozenset(
    {
        "type",
        "bom-ref",
        "name",
        "description",
        "copyright",
        "hashes",
        "licenses",
        "author",
        "components",
        "properties",
    }
)

_DOCUMENT_NATIVE_KEYS = frozenset(
    {"bomFormat", "specVersion", "components", "dependencies", "metadata"}
)
_METADATA_NATIVE_KEYS = frozenset({"component", "timestamp", "tools", "authors"})

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


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("CycloneDX metadata.timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"invalid CycloneDX metadata.timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _creators(metadata: dict[str, Any]) -> list[str]:
    creators = [ANNOTATOR]
    tools = metadata.get("tools") or []
    if isinstance(tools, dict):
        tools = [*tools.get("components", []), *tools.get("services", [])]
    for tool in tools:
        name = tool.get("name")
        if name:
            version = tool.get("version")
            creators.append(f"Tool: {name}-{version}" if version else f"Tool: {name}")
    for author in metadata.get("authors") or []:
        name = author.get("name")
        if name:
            email = author.get("email")
            creators.append(f"Person: {name} ({email})" if email else f"Person: {name}")
    return list(dict.fromkeys(creators))


def _checksums(hashes: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    for item in hashes:
        algorithm = _CHECKSUM_ALGORITHMS.get(item["alg"])
        if algorithm is None:
            raise ValueError(f"hash algorithm has no SPDX equivalent: {item['alg']}")
        result.append({"algorithm": algorithm, "checksumValue": item["content"].lower()})
    return result


def _contact(prefix: str, name: str, email: str | None) -> str:
    return f"{prefix}: {name} ({email})" if email else f"{prefix}: {name}"


def _file_types(name: str, mime: str | None) -> list[str]:
    base = name.rsplit("/", 1)[-1].lower()
    if _SHARED_OBJECT.search(base):
        return ["BINARY"]
    suffix = "." + base.rsplit(".", 1)[-1] if "." in base else ""
    kind = _FILE_TYPE_BY_SUFFIX.get(suffix)
    if kind is None and base in _TEXT_NAMES:
        kind = "TEXT"
    if kind is None and mime:
        major = mime.split("/", 1)[0]
        kind = {"text": "TEXT", "image": "IMAGE", "audio": "AUDIO", "video": "VIDEO"}.get(major)
    return [kind] if kind else []


class _Node:
    def __init__(self, component: dict[str, Any], key: str, parent: _Node | None):
        self.component = component
        self.key = key
        self.parent = parent
        self.is_file = component["type"] == "file" and any(
            item["alg"] == "SHA-1" for item in component.get("hashes", [])
        )
        self.id = _spdx_id("File" if self.is_file else "Package", component["name"], key)


class _Converter:
    def __init__(self, document: dict[str, Any]):
        self.document = document
        metadata = document.get("metadata", {})
        self.created = _timestamp(metadata["timestamp"]) if "timestamp" in metadata else EPOCH
        self.nodes: list[_Node] = []
        self.by_ref: dict[str, _Node] = {}
        self.extracted: dict[str, dict[str, Any]] = {}
        self.relationships: dict[tuple[str, str, str], str | None] = {}
        self.placeholders: dict[str, str] = {}

    def relate(self, left: str, kind: str, right: str, comment: str | None = None) -> None:
        self.relationships.setdefault((left, kind, right), comment)

    # Nodes

    def register(self, component: dict[str, Any], parent: _Node | None, used: set[str]) -> None:
        reference = component.get("bom-ref")
        if reference:
            if reference in self.by_ref:
                raise ValueError(f"duplicate CycloneDX bom-ref: {reference}")
            key = f"ref:{reference}"
        else:
            key = f"{parent.key if parent else ''}/{component['type']}:{component['name']}"
        base, count = key, 1
        while key in used:
            count += 1
            key = f"{base}#{count}"
        used.add(key)
        node = _Node(component, key, parent)
        self.nodes.append(node)
        if reference:
            self.by_ref[reference] = node
        for child in component.get("components", []):
            self.register(child, node, used)

    # Annotations

    def annotation(self, content: dict[str, Any]) -> dict[str, str]:
        return {
            "annotationDate": self.created,
            "annotationType": "OTHER",
            "annotator": ANNOTATOR,
            "comment": json.dumps(content, sort_keys=True),
        }

    def annotations(self, component: dict[str, Any], extra: dict[str, Any]) -> list[dict]:
        """One JSON object per repeated property name so no property is overwritten."""
        objects: list[dict[str, Any]] = [dict(extra)]
        for item in component.get("properties", []):
            name = item["name"]
            target = next((obj for obj in objects if name not in obj), None)
            if target is None:
                target = {}
                objects.append(target)
            target[name] = item["value"]
        return [self.annotation(obj) for obj in objects if obj]

    # Licences

    def licenses(
        self, component: dict[str, Any]
    ) -> tuple[list[str], list[str], dict[str, Any] | None]:
        """Return declared and concluded expressions plus the licence list when lossy."""
        declared: list[str] = []
        concluded: list[str] = []
        lossy = False
        for choice in component.get("licenses", []):
            if "expression" in choice:
                expression = choice["expression"]
                lossy |= bool(set(choice) - {"expression", "acknowledgement"})
                acknowledgement = choice.get("acknowledgement")
            else:
                data = choice["license"]
                acknowledgement = data.get("acknowledgement")
                text = data.get("text")
                name = data.get("name", "")
                if "id" in data:
                    expression = data["id"]
                    lossy |= bool(set(data) - {"id", "acknowledgement"})
                elif text and text.get("content") is not None:
                    expression = self.extracted_license(name, data, text)
                    lossy |= bool(set(data) - {"name", "text", "url", "acknowledgement"})
                    lossy |= expression != name
                else:
                    lossy = True
                    continue
            (concluded if acknowledgement == "concluded" else declared).append(expression)
        return declared, concluded, ({"licenses": component["licenses"]} if lossy else None)

    def extracted_license(self, name: str, data: dict[str, Any], text: dict[str, Any]) -> str:
        content = text["content"]
        if text.get("encoding") == "base64":
            try:
                content = base64.b64decode(content, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError(f"licence text is not valid base64 UTF-8: {name}") from error
        elif text.get("encoding") is not None:
            raise ValueError(f"unsupported licence text encoding: {text['encoding']}")
        identifier = name
        if not _LICENSE_REF.fullmatch(identifier):
            identifier = f"LicenseRef-{_slug(name)}-{_digest(name)[:8]}"
        entry: dict[str, Any] = {"licenseId": identifier, "extractedText": content}
        if identifier != name:
            entry["name"] = name
        if data.get("url"):
            entry["seeAlsos"] = [data["url"]]
        previous = self.extracted.setdefault(identifier, entry)
        if previous != entry:
            raise ValueError(f"conflicting licence text for {identifier}")
        return identifier

    @staticmethod
    def join_licenses(expressions: list[str]) -> str:
        if not expressions:
            return "NOASSERTION"
        if len(expressions) == 1:
            return expressions[0]
        return " AND ".join(
            f"({item})" if re.search(r"\s", item) else item for item in expressions
        )

    # Elements

    def package(self, node: _Node) -> dict[str, Any]:
        component = node.component
        extra: dict[str, Any] = {
            f"cyclonedx:{key}": value
            for key, value in component.items()
            if key not in _NATIVE_PACKAGE_KEYS
        }
        purpose = _PURPOSES.get(component["type"], "OTHER")
        if purpose in ("OTHER", "FILE"):
            extra["cyclonedx:type"] = component["type"]
        package: dict[str, Any] = {
            "SPDXID": node.id,
            "name": component["name"],
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "primaryPackagePurpose": purpose,
            "copyrightText": component.get("copyright") or "NOASSERTION",
        }
        if "version" in component:
            package["versionInfo"] = component["version"]
        if component.get("description"):
            package["description"] = component["description"]
        if component.get("hashes"):
            package["checksums"] = _checksums(component["hashes"])
        supplier = component.get("supplier")
        if supplier and supplier.get("name"):
            contacts = supplier.get("contact") or []
            email = next((item["email"] for item in contacts if item.get("email")), None)
            package["supplier"] = _contact("Organization", supplier["name"], email)
            if set(supplier) - {"name"}:
                extra["cyclonedx:supplier"] = supplier
        elif supplier:
            extra["cyclonedx:supplier"] = supplier
        authors = [item for item in component.get("authors", []) if item.get("name")]
        if authors:
            package["originator"] = _contact("Person", authors[0]["name"], authors[0].get("email"))
            if len(component.get("authors", [])) > 1 or set(authors[0]) - {"name", "email"}:
                extra["cyclonedx:authors"] = component["authors"]
        elif component.get("authors"):
            extra["cyclonedx:authors"] = component["authors"]
        elif component.get("author"):
            package["originator"] = f"Person: {component['author']}"
        refs = []
        if component.get("purl"):
            refs.append(
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": component["purl"],
                }
            )
        cpe = component.get("cpe")
        if cpe:
            kind = "cpe23Type" if cpe.startswith("cpe:2.3:") else "cpe22Type"
            if cpe.startswith(("cpe:2.3:", "cpe:/")):
                refs.append(
                    {
                        "referenceCategory": "SECURITY",
                        "referenceType": kind,
                        "referenceLocator": cpe,
                    }
                )
            else:
                extra["cyclonedx:cpe"] = cpe
        for reference in component.get("externalReferences", []):
            if reference["type"] == "website" and "homepage" not in package:
                package["homepage"] = reference["url"]
            else:
                item = {
                    "referenceCategory": "OTHER",
                    "referenceType": reference["type"],
                    "referenceLocator": reference["url"],
                }
                if reference.get("comment"):
                    item["comment"] = reference["comment"]
                refs.append(item)
            if set(reference) - {"type", "url", "comment"}:
                extra.setdefault("cyclonedx:externalReferences", []).append(reference)
        if refs:
            package["externalRefs"] = refs
        declared, concluded, lossy = self.licenses(component)
        package["licenseDeclared"] = self.join_licenses(declared)
        package["licenseConcluded"] = self.join_licenses(concluded)
        if lossy:
            extra["cyclonedx:licenses"] = lossy["licenses"]
        annotations = self.annotations(component, extra)
        if annotations:
            package["annotations"] = annotations
        return package

    def file(self, node: _Node) -> dict[str, Any]:
        component = node.component
        extra: dict[str, Any] = {
            f"cyclonedx:{key}": value
            for key, value in component.items()
            if key not in _NATIVE_FILE_KEYS
        }
        entry: dict[str, Any] = {
            "SPDXID": node.id,
            "fileName": component["name"],
            "checksums": _checksums(component["hashes"]),
            "copyrightText": component.get("copyright") or "NOASSERTION",
        }
        kinds = _file_types(component["name"], component.get("mime-type"))
        if kinds:
            entry["fileTypes"] = kinds
        if component.get("description"):
            entry["comment"] = component["description"]
        if component.get("author"):
            entry["fileContributors"] = [component["author"]]
        declared, concluded, lossy = self.licenses(component)
        entry["licenseInfoInFiles"] = declared or ["NOASSERTION"]
        entry["licenseConcluded"] = self.join_licenses(concluded)
        if lossy:
            extra["cyclonedx:licenses"] = lossy["licenses"]
        annotations = self.annotations(component, extra)
        if annotations:
            entry["annotations"] = annotations
        return entry

    def placeholder(self, soname: str) -> str:
        if soname not in self.placeholders:
            self.placeholders[soname] = _spdx_id("Package", soname, f"dynamic-needed:{soname}")
        return self.placeholders[soname]

    def dynamic_links(self, node: _Node) -> None:
        values = [
            item["value"]
            for item in node.component.get("properties", [])
            if item["name"] == DYNAMIC_NEEDED
        ]
        for value in values:
            try:
                sonames = json.loads(value)
            except ValueError as error:
                raise ValueError(
                    f"{DYNAMIC_NEEDED} on {node.component['name']} is not JSON"
                ) from error
            if not isinstance(sonames, list) or not all(
                isinstance(item, str) and item for item in sonames
            ):
                raise ValueError(
                    f"{DYNAMIC_NEEDED} on {node.component['name']} is not a list of sonames"
                )
            for soname in sonames:
                self.relate(node.id, "DYNAMIC_LINK", self.placeholder(soname))

    # Document

    def convert(self) -> dict[str, Any]:
        document = self.document
        metadata = document.get("metadata", {})
        used: set[str] = set()
        root = metadata.get("component")
        if root:
            self.register(root, None, used)
        for component in document.get("components", []):
            self.register(component, None, used)
        root_node = self.nodes[0] if root else None

        packages = []
        files = []
        for node in self.nodes:
            (files if node.is_file else packages).append(
                self.file(node) if node.is_file else self.package(node)
            )
            if node.parent:
                self.relate(node.parent.id, "CONTAINS", node.id)
            self.dynamic_links(node)

        depended: set[str] = set()
        owned: set[str] = set()
        for edge in document.get("dependencies", []):
            owner = self.by_ref.get(edge["ref"])
            if owner is None:
                raise ValueError(f"dependency from unknown bom-ref: {edge['ref']}")
            for reference in dict.fromkeys(edge.get("dependsOn", [])):
                target = self.by_ref.get(reference)
                if target is None:
                    raise ValueError(f"dependency on unknown bom-ref: {reference}")
                scope = target.component.get("scope")
                comment = (
                    f"CycloneDX scope: {scope}" if scope in ("optional", "excluded") else None
                )
                self.relate(owner.id, "DEPENDS_ON", target.id, comment)
                depended.add(target.id)
                if (
                    target.parent is None
                    and target.component["type"] == "file"
                    and owner.component["type"] not in ("firmware", "application", "file")
                ):
                    self.relate(owner.id, "CONTAINS", target.id)
                    owned.add(target.id)
        for node in self.nodes:
            if (
                node.parent is None
                and node.component["type"] == "file"
                and node is not root_node
                and node.id not in owned
            ):
                self.relate(
                    "SPDXRef-DOCUMENT",
                    "CONTAINS",
                    node.id,
                    "file component not owned by any package",
                )

        if root_node:
            described = [root_node]
        else:
            top = [node for node in self.nodes if node.parent is None]
            described = [
                node
                for node in top
                if node.component["type"] != "file" and node.id not in depended
            ] or top
        for node in described:
            self.relate("SPDXRef-DOCUMENT", "DESCRIBES", node.id)

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

        digest = _digest(_canonical(document))
        name = root["name"] if root else "mpy-analysis-sbom"
        result: dict[str, Any] = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": name,
            "documentNamespace": f"https://spdx.org/spdxdocs/mpy-analysis-{digest}",
            "creationInfo": {
                "created": self.created,
                "creators": _creators(metadata),
                "comment": f"Converted from CycloneDX {document['specVersion']} document sha256:{digest}",
            },
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
        unmapped = {
            f"cyclonedx:{key}": value
            for key, value in document.items()
            if key not in _DOCUMENT_NATIVE_KEYS
        }
        unmapped.update(
            {
                f"cyclonedx:metadata.{key}": value
                for key, value in metadata.items()
                if key not in _METADATA_NATIVE_KEYS
            }
        )

        provides = [
            {"ref": edge["ref"], "provides": edge["provides"]}
            for edge in document.get("dependencies", [])
            if edge.get("provides")
        ]
        if provides:
            unmapped["cyclonedx:dependencies.provides"] = provides
        if unmapped:
            result["annotations"] = [self.annotation(unmapped)]
        return result


def cyclonedx_to_spdx(document: dict[str, Any]) -> dict[str, Any]:
    """Return the SPDX 2.3 JSON document equivalent to a CycloneDX 1.6 document."""
    validate_cyclonedx(document)
    result = _Converter(document).convert()
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
    for item in document.get("files", []):
        if not any(checksum["algorithm"] == "SHA1" for checksum in item.get("checksums", [])):
            raise ValueError(f"SPDX file lacks the required SHA1 checksum: {item['fileName']}")
    known = set(identifiers)
    for relationship in document.get("relationships", []):
        for field in ("spdxElementId", "relatedSpdxElement"):
            if relationship[field] not in known:
                raise ValueError(
                    f"SPDX relationship references unknown element: {relationship[field]}"
                )
    licenses = [item["licenseId"] for item in document.get("hasExtractedLicensingInfos", [])]
    if len(set(licenses)) != len(licenses):
        raise ValueError("duplicate SPDX extracted licence identifier")
    for item in licenses:
        if not _LICENSE_REF.fullmatch(item):
            raise ValueError(f"invalid SPDX extracted licence identifier: {item}")


def serialize_spdx(document: dict[str, Any]) -> bytes:
    """Serialise exactly as sbom.py writes sbom.json: sorted keys, indent 2, trailing newline."""
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
