# SPDX-FileCopyrightText: 2025 The MicroPython Contributors
# SPDX-License-Identifier: MIT

"""SPDX document generation."""

import json
import uuid
from datetime import datetime, timezone


def generate_document_namespace(project_name):
    """Generate a unique SPDX document namespace."""
    unique_id = uuid.uuid4().hex[:8]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"https://micropython.org/spdx/{project_name}-{timestamp}-{unique_id}"


def generate_spdx_tv(files_info, project_name, output_path, extra_metadata=None):
    """
    Generate SPDX document in tag-value format.

    Args:
        files_info: List of file info dicts with keys:
            - path: File path
            - sha256: SHA256 hash
            - sha1: SHA1 hash (optional)
            - license: SPDX license expression or None
            - copyrights: List of copyright strings
        project_name: Name for the SBOM package
        output_path: Path to write the SPDX document
        extra_metadata: Optional dict with additional package metadata
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc_namespace = generate_document_namespace(project_name)

    lines = [
        "SPDXVersion: SPDX-2.3",
        "DataLicense: CC0-1.0",
        "SPDXID: SPDXRef-DOCUMENT",
        f"DocumentName: {project_name}",
        f"DocumentNamespace: {doc_namespace}",
        "Creator: Tool: mkspdx",
        f"Created: {now}",
        "",
    ]

    # Package info
    lines.extend(
        [
            "##### Package",
            "",
            f"PackageName: {project_name}",
            "SPDXID: SPDXRef-Package",
            "PackageDownloadLocation: https://github.com/micropython/micropython",
            "FilesAnalyzed: true",
        ]
    )

    if extra_metadata:
        if "version" in extra_metadata:
            lines.append(f"PackageVersion: {extra_metadata['version']}")
        if "supplier" in extra_metadata:
            lines.append(f"PackageSupplier: {extra_metadata['supplier']}")

    # Compute package verification code (SHA1 of sorted file SHA1s)
    if files_info:
        import hashlib

        file_sha1s = sorted(f.get("sha1", f.get("sha256", ""))[:40] for f in files_info)
        verification = hashlib.sha1("".join(file_sha1s).encode()).hexdigest()
        lines.append(f"PackageVerificationCode: {verification} (excludes: )")

    # Determine concluded license for package
    licenses_found = set()
    for finfo in files_info:
        if finfo.get("license"):
            licenses_found.add(finfo["license"])

    if len(licenses_found) == 1:
        lines.append(f"PackageLicenseConcluded: {list(licenses_found)[0]}")
    elif len(licenses_found) > 1:
        lines.append(f"PackageLicenseConcluded: ({' AND '.join(sorted(licenses_found))})")
    else:
        lines.append("PackageLicenseConcluded: NOASSERTION")

    lines.append("PackageLicenseDeclared: NOASSERTION")
    lines.append("PackageCopyrightText: NOASSERTION")
    lines.append("")

    # Relationship
    lines.append("Relationship: SPDXRef-DOCUMENT DESCRIBES SPDXRef-Package")
    lines.append("")

    # File entries
    lines.append("##### Files")
    lines.append("")

    for i, finfo in enumerate(files_info):
        file_id = f"SPDXRef-File-{i}"
        lines.append(f"FileName: ./{finfo['path']}")
        lines.append(f"SPDXID: {file_id}")

        if finfo.get("sha256"):
            lines.append(f"FileChecksum: SHA256: {finfo['sha256']}")
        if finfo.get("sha1"):
            lines.append(f"FileChecksum: SHA1: {finfo['sha1']}")

        license_id = finfo.get("license") or "NOASSERTION"
        lines.append(f"LicenseConcluded: {license_id}")

        if finfo.get("license"):
            lines.append(f"LicenseInfoInFile: {finfo['license']}")
        else:
            lines.append("LicenseInfoInFile: NOASSERTION")

        copyrights = finfo.get("copyrights", [])
        if copyrights:
            for copyright_text in copyrights:
                lines.append(f"FileCopyrightText: {copyright_text}")
        else:
            lines.append("FileCopyrightText: NOASSERTION")

        # Add relationship to package
        lines.append(f"Relationship: SPDXRef-Package CONTAINS {file_id}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_spdx_json(files_info, project_name, output_path, extra_metadata=None):
    """
    Generate SPDX document in JSON format.

    Args:
        files_info: List of file info dicts (same format as generate_spdx_tv)
        project_name: Name for the SBOM package
        output_path: Path to write the SPDX document
        extra_metadata: Optional dict with additional package metadata
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc_namespace = generate_document_namespace(project_name)

    # Determine concluded license
    licenses_found = set()
    for finfo in files_info:
        if finfo.get("license"):
            licenses_found.add(finfo["license"])

    if len(licenses_found) == 1:
        concluded_license = list(licenses_found)[0]
    elif len(licenses_found) > 1:
        concluded_license = f"({' AND '.join(sorted(licenses_found))})"
    else:
        concluded_license = "NOASSERTION"

    doc = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": project_name,
        "documentNamespace": doc_namespace,
        "creationInfo": {
            "created": now,
            "creators": ["Tool: mkspdx"],
        },
        "packages": [
            {
                "name": project_name,
                "SPDXID": "SPDXRef-Package",
                "downloadLocation": "https://github.com/micropython/micropython",
                "filesAnalyzed": True,
                "licenseConcluded": concluded_license,
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        ],
        "files": [],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": "SPDXRef-Package",
                "relationshipType": "DESCRIBES",
            }
        ],
    }

    if extra_metadata:
        if "version" in extra_metadata:
            doc["packages"][0]["versionInfo"] = extra_metadata["version"]
        if "supplier" in extra_metadata:
            doc["packages"][0]["supplier"] = extra_metadata["supplier"]

    for i, finfo in enumerate(files_info):
        file_id = f"SPDXRef-File-{i}"
        checksums = []
        if finfo.get("sha256"):
            checksums.append({"algorithm": "SHA256", "checksumValue": finfo["sha256"]})
        if finfo.get("sha1"):
            checksums.append({"algorithm": "SHA1", "checksumValue": finfo["sha1"]})

        file_entry = {
            "fileName": f"./{finfo['path']}",
            "SPDXID": file_id,
            "checksums": checksums,
            "licenseConcluded": finfo.get("license") or "NOASSERTION",
            "licenseInfoInFiles": [finfo["license"]] if finfo.get("license") else ["NOASSERTION"],
            "copyrightText": "\n".join(finfo.get("copyrights", [])) or "NOASSERTION",
        }
        doc["files"].append(file_entry)

        doc["relationships"].append(
            {
                "spdxElementId": "SPDXRef-Package",
                "relatedSpdxElement": file_id,
                "relationshipType": "CONTAINS",
            }
        )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def validate_spdx_document(spdx_path):
    """
    Basic validation of an SPDX document.

    Args:
        spdx_path: Path to SPDX document (tag-value or JSON)

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    try:
        with open(spdx_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, OSError) as e:
        return False, [f"Cannot read file: {e}"]

    # Detect format
    if content.strip().startswith("{"):
        # JSON format
        try:
            doc = json.loads(content)
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON: {e}"]

        required_fields = ["spdxVersion", "SPDXID", "name", "documentNamespace"]
        for field in required_fields:
            if field not in doc:
                errors.append(f"Missing required field: {field}")

        if doc.get("spdxVersion") not in ["SPDX-2.2", "SPDX-2.3"]:
            errors.append(f"Unsupported SPDX version: {doc.get('spdxVersion')}")

    else:
        # Tag-value format
        if "SPDXVersion:" not in content:
            errors.append("Missing SPDXVersion tag")
        if "SPDXID:" not in content:
            errors.append("Missing SPDXID tag")
        if "DocumentName:" not in content and "name:" not in content:
            errors.append("Missing DocumentName tag")
        if "DocumentNamespace:" not in content:
            errors.append("Missing DocumentNamespace tag")

    return len(errors) == 0, errors
