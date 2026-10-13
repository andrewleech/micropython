"""Deployment manifest verification: stale bytes, receipt binding, runtime fetch and SBOM contents.

The receipt comes from real GNU builds (an application image and a bootloader image of one build)
captured through `capture_make`; the manifests name copies of the images the build wrote and files
of a device tree that exist only in the test.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from firmware_build import (
    LINK,
    MPY_ROOT,
    PREAMBLE,
    Build,
    capture,
    generate,
    requires_gnu_build,
    scope_of,
)
from mpy_analysis import deployment, sbom, sbom_policy
from mpy_analysis.firmware_capture import capture_make
from mpy_analysis.firmware_capture_receipt import merge_receipts
from mpy_analysis.sbom_cyclonedx import to_cyclonedx
from mpy_analysis.sbom_spdx import to_spdx

pytestmark = requires_gnu_build

MAIN = "int main(void) { return VAL; }\n"
MAKEFILE = (
    PREAMBLE
    + LINK.format(objects="$(BUILD)/main.o")
    + "\t$(OBJCOPY) -O binary $@ $(BUILD)/firmware.bin\n"
)
DEVICE_FILES = {
    "lib/driver.py": b"def drive():\n    return 1\n",
    "res/logo.bin": b"\x00logo-bytes",
    "modules/fast.mpy": b"M\x06\x00native-module",
    "examples/demo.py": b"print('example that is never deployed')\n",
}


@dataclass
class World:
    app: Build
    receipt: dict
    receipt_path: Path
    images: dict[str, Path]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("deployment")
    app = capture(
        tmp_path, files={"src/main.c": MAIN}, makefile=MAKEFILE, name="app", variables=("CFLAGS=-DVAL=3",)
    )
    assert app.exit_code == 0
    build = tmp_path / "boot-build"
    assert (
        capture_make(
            micropython_root=app.root,
            make_dir=app.root / "make",
            build_dir=build,
            journal=tmp_path / "boot.jsonl",
            receipt_path=tmp_path / "boot-receipt.json",
            elf=build / "firmware.elf",
            map_file=build / "firmware.map",
            image_id="boot",
            image_role="bootloader",
            build_id="a" * 64,
            port="native",
            target="native",
            variant="controls",
            source_roots=app.roots,
            cross_compile="",
            variables=["CFLAGS=-DVAL=5"],
            targets=["all"],
        )
        == 0
    )
    boot = json.loads((tmp_path / "boot-receipt.json").read_text())
    receipt = merge_receipts([app.receipt, boot])
    receipt_path = tmp_path / "merged-receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    images = {
        "app.bin": app.build / "firmware.bin",
        "app.elf": app.build / "firmware.elf",
        "boot.bin": build / "firmware.bin",
        "boot.elf": build / "firmware.elf",
    }
    assert len({sha256(path.read_bytes()) for path in images.values()}) == 4
    return World(app, receipt, receipt_path, images)


def device(tmp_path: Path, world: World) -> Path:
    """A device tree: copies of the images plus the payload files; returns its directory."""
    root = tmp_path / "device"
    for name, content in DEVICE_FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    (root / "images").mkdir()
    for name, path in world.images.items():
        shutil.copy(path, root / "images" / name)
    return root


def member(root: Path, ident: str, kind: str, relative: str, **extra) -> dict:
    return {
        "id": ident,
        "kind": kind,
        "path": relative,
        "sha256": sha256((root / relative).read_bytes()),
        **extra,
    }


def manifest(root: Path, **overrides) -> dict:
    data = {
        "schema": "mpy-deployment/v1",
        "name": "fixture-device",
        "target": "native",
        "members": [
            member(root, "app", "firmware-image", "images/app.bin", receipt_image="app"),
            member(root, "boot", "bootloader-image", "images/boot.bin", receipt_image="boot"),
            member(root, "driver", "python-package-file", "lib/driver.py"),
            member(root, "logo", "resource", "res/logo.bin"),
            member(root, "fast", "native-module", "modules/fast.mpy"),
        ],
        "runtime_fetch": {"mode": "none"},
        "exclusions": [{"path": "examples/demo.py", "reason": "example, not installed on the device"}],
    }
    data.update(overrides)
    return data


def write_manifest(root: Path, data: dict) -> Path:
    path = root / "deployment.json"
    path.write_text(json.dumps(data))
    return path


def build_sbom(world: World, path: Path | None, *, receipt=True):
    """Model and summary for the fixture build, with or without its firmware receipt."""
    if receipt:
        return generate(world.app, receipt=world.receipt_path, deployment=path)
    return sbom.generate_sbom(
        scope_of(world.app), micropython_root=MPY_ROOT, build_receipt=None, deployment=path
    )


def reasons(summary) -> set[tuple[str, str | None]]:
    return {(item["code"], item["member"]) for item in summary["deployment"]["reasons"]}


def rows(summary) -> dict[str, dict]:
    return {row["id"]: row for row in summary["deployment"]["members"]}


def file_names(model) -> set[str]:
    return {item.name for item in model.components.values() if item.kind == "file"}


def policy(tmp_path, summary, profile, *, receipt=None, manifest_path=None, capsys):
    summary_path = tmp_path / "policy-summary.json"
    summary_path.write_text(json.dumps(summary))
    argv = ["--summary", str(summary_path), "--profile", profile]
    if receipt:
        argv += ["--receipt", str(receipt)]
    if manifest_path:
        argv += ["--deployment", str(manifest_path)]
    code = sbom_policy.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_complete_deployment_binds_images_and_adds_only_deployed_files(world, tmp_path):
    root = device(tmp_path, world)
    path = write_manifest(root, manifest(root))

    model, summary = build_sbom(world, path)

    assert summary["firmware_completeness"] == "established"
    assert summary["deployment_completeness"] == "established"
    block = summary["deployment"]
    assert block["status"] == "established" and block["reasons"] == []
    assert block["receipt"] == {
        "receipt_id": world.receipt["receipt_id"],
        "build_id": world.receipt["build_id"],
    }
    assert {row["digest"] for row in block["members"]} == {"match"}
    assert rows(summary)["app"]["receipt_binding"] == {
        "image": "app",
        "artifact_role": "deployment",
        "artifact_path": str(world.images["app.bin"].resolve()),
        "bound_by": "receipt_image",
    }
    assert rows(summary)["boot"]["receipt_binding"]["image"] == "boot"
    assert [(item["id"], item["members"]) for item in block["receipt_images"]] == [
        ("app", ["app"]),
        ("boot", ["boot"]),
    ]
    assert block["runtime_fetch"]["mode"] == "none"

    names = file_names(model)
    assert {"lib/driver.py", "res/logo.bin", "modules/fast.mpy"} <= names
    assert "examples/demo.py" not in names
    assert not any(name.startswith("images/") for name in names)

    document = to_spdx(model)
    spdx_files = {item["fileName"]: item for item in document["files"]}
    driver = spdx_files["lib/driver.py"]
    assert {item["algorithm"] for item in driver["checksums"]} == {"SHA1", "SHA256"}
    assert {
        item["checksumValue"] for item in driver["checksums"] if item["algorithm"] == "SHA256"
    } == {sha256(DEVICE_FILES["lib/driver.py"])}
    deployment_package = next(item for item in document["packages"] if item["name"] == "fixture-device")
    contained = {
        item["relatedSpdxElement"]
        for item in document["relationships"]
        if item["spdxElementId"] == deployment_package["SPDXID"] and item["relationshipType"] == "CONTAINS"
    }
    assert {spdx_files[name]["SPDXID"] for name in ("lib/driver.py", "res/logo.bin", "modules/fast.mpy")} == contained
    assert "examples/demo.py" not in spdx_files
    described = next(item for item in document["relationships"] if item["relationshipType"] == "DESCRIBES")
    assert any(
        item["spdxElementId"] == described["relatedSpdxElement"]
        and item["relationshipType"] == "DEPENDS_ON"
        and item["relatedSpdxElement"] == deployment_package["SPDXID"]
        for item in document["relationships"]
    )

    cyclonedx = to_cyclonedx(model)
    top = next(item for item in cyclonedx["components"] if item["name"] == "fixture-device")
    nested = {item["name"]: item for item in top["components"]}
    assert set(nested) == {"lib/driver.py", "res/logo.bin", "modules/fast.mpy"}
    roles = {
        prop["value"] for prop in nested["modules/fast.mpy"]["properties"] if prop["name"] == "mpy:role"
    }
    assert roles == {"deployed-native-module"}
    assert {item["alg"] for item in nested["lib/driver.py"]["hashes"]} == {"SHA-1", "SHA-256"}
    depends = {item["ref"]: item["dependsOn"] for item in cyclonedx["dependencies"]}
    firmware = [item["bom-ref"] for item in cyclonedx["components"] if item["type"] == "firmware"]
    assert set(depends[top["bom-ref"]]) == set(firmware) and len(firmware) == 2


def test_elf_hash_and_unbound_hash_are_accepted_as_receipt_consistent(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"][0] = member(root, "app", "firmware-image", "images/app.elf")
    data["members"][1] = member(root, "boot", "bootloader-image", "images/boot.bin")
    path = write_manifest(root, data)

    _, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "established"
    assert rows(summary)["app"]["receipt_binding"]["artifact_role"] == "elf"
    assert rows(summary)["app"]["receipt_binding"]["bound_by"] == "hash"
    assert rows(summary)["boot"]["receipt_binding"]["bound_by"] == "hash"


def test_without_a_manifest_deployment_is_not_declared_and_no_file_is_added(world):
    model, summary = build_sbom(world, None)

    assert summary["deployment_completeness"] == "not-declared"
    assert summary["deployment"] is None
    assert not file_names(model) & set(DEVICE_FILES)
    assert "deployment-completeness" not in model.components[model.root].properties


@pytest.mark.parametrize("what", ["file-bytes", "declared-hash"])
def test_modified_member_invalidates_closure_and_keeps_the_rest(world, tmp_path, what):
    root = device(tmp_path, world)
    data = manifest(root)
    if what == "file-bytes":
        (root / "lib/driver.py").write_bytes(DEVICE_FILES["lib/driver.py"] + b"# tampered\n")
    else:
        data["members"][2]["sha256"] = sha256(b"other bytes")
    path = write_manifest(root, data)

    model, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("member-hash-mismatch", "driver")}
    assert rows(summary)["driver"]["digest"] == "hash-mismatch"
    assert rows(summary)["driver"]["actual_sha256"] == sha256(
        (root / "lib/driver.py").read_bytes()
    )
    assert {row["digest"] for row in summary["deployment"]["members"] if row["id"] != "driver"} == {"match"}
    names = file_names(model)
    assert "lib/driver.py" not in names and {"res/logo.bin", "modules/fast.mpy"} <= names
    assert summary["firmware_completeness"] == "established"


def test_stale_partition_and_image_bytes_are_not_established(world, tmp_path):
    root = device(tmp_path, world)
    (root / "res/part.img").write_bytes(b"filesystem partition image")
    data = manifest(root)
    data["members"].append(member(root, "data", "partition-image", "res/part.img"))
    (root / "res/part.img").write_bytes(b"filesystem partition image, modified")
    (root / "images/app.bin").write_bytes(b"flashed something else")
    path = write_manifest(root, data)

    model, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("member-hash-mismatch", "app"), ("member-hash-mismatch", "data")}
    assert rows(summary)["app"]["binding"] == "bound"
    assert "res/part.img" not in file_names(model)


@pytest.mark.parametrize(
    "setup, code",
    [
        (lambda root: (root / "res/logo.bin").unlink(), "member-missing"),
        (lambda root: (root / "res/logo.bin").unlink() or (root / "res/logo.bin").mkdir(), "member-not-a-file"),
    ],
    ids=["missing", "directory"],
)
def test_missing_or_non_file_member(world, tmp_path, setup, code):
    root = device(tmp_path, world)
    data = manifest(root)
    setup(root)
    path = write_manifest(root, data)

    model, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {(code, "logo")}
    assert rows(summary)["logo"]["actual_sha256"] is None
    assert "res/logo.bin" not in file_names(model)


def test_application_image_that_the_receipt_does_not_contain_is_not_established(world, tmp_path):
    root = device(tmp_path, world)
    (root / "images/other.bin").write_bytes(b"an application image nobody built")
    data = manifest(root)
    data["members"][0] = member(root, "app", "firmware-image", "images/other.bin")
    path = write_manifest(root, data)

    _, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {
        ("firmware-image-not-in-receipt", "app"),
        ("receipt-image-not-in-deployment", None),
    }
    assert rows(summary)["app"]["binding"] == "rejected"


def test_named_receipt_image_must_exist_and_hash_consistently(world, tmp_path):
    root = device(tmp_path, world)
    shutil.copy(root / "images/boot.bin", root / "images/boot-copy.bin")
    cases = {
        "unknown": (
            member(root, "app", "firmware-image", "images/app.bin", receipt_image="nope"),
            "receipt-image-unknown",
        ),
        "other-image-hash": (
            member(root, "app", "firmware-image", "images/boot-copy.bin", receipt_image="app"),
            "receipt-image-hash-mismatch",
        ),
        "role": (
            member(root, "app", "firmware-image", "images/boot-copy.bin", receipt_image="boot"),
            "receipt-image-role-mismatch",
        ),
    }
    for name, (replacement, code) in cases.items():
        data = manifest(root)
        data["members"][0] = replacement
        path = write_manifest(root, data)
        _, summary = build_sbom(world, path)
        assert summary["deployment_completeness"] == "not-established", name
        assert (code, "app") in reasons(summary), name


def test_receipt_image_missing_from_manifest_needs_a_stated_exclusion(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"].pop(1)
    path = write_manifest(root, data)
    _, summary = build_sbom(world, path)
    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("receipt-image-not-in-deployment", None)}

    data["exclusions"].append({"receipt_image": "boot", "reason": "flashed by the factory programmer"})
    path = write_manifest(root, data)
    _, summary = build_sbom(world, path)
    assert summary["deployment_completeness"] == "established"
    [boot] = [item for item in summary["deployment"]["receipt_images"] if item["id"] == "boot"]
    assert boot["excluded"] and boot["exclusion_reason"] == "flashed by the factory programmer"


def test_excluding_an_application_image_is_rejected(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"].pop(0)
    data["exclusions"].append({"receipt_image": "app", "reason": "not needed"})
    path = write_manifest(root, data)
    with pytest.raises(deployment.DeploymentError, match="firmware image bytes cannot be left out"):
        build_sbom(world, path)

    data = manifest(root)
    data["exclusions"].append({"path": "images/app.bin", "reason": "not needed"})
    with pytest.raises(deployment.DeploymentError, match="firmware image member app"):
        deployment.load_deployment(write_manifest(root, data))

    data = manifest(root)
    data["members"].pop(0)
    data["exclusions"].append({"path": str(world.images["app.elf"]), "reason": "not needed"})
    with pytest.raises(deployment.DeploymentError, match="exclude the image by receipt_image"):
        build_sbom(world, write_manifest(root, data))


def test_a_stated_exclusion_names_its_file_but_cannot_hide_a_member(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["exclusions"].append({"path": "lib/driver.py", "reason": "contradiction"})
    with pytest.raises(deployment.DeploymentError, match="a deployed member cannot be excluded"):
        deployment.load_deployment(write_manifest(root, data))


@pytest.mark.parametrize(
    "fetch, code",
    [
        ({"mode": "unrestricted"}, "runtime-fetch-unrestricted"),
        (None, "runtime-fetch-undeclared"),
    ],
    ids=["unrestricted", "absent"],
)
def test_unrestricted_or_undeclared_runtime_fetch_blocks_the_claim(world, tmp_path, fetch, code):
    root = device(tmp_path, world)
    data = manifest(root)
    if fetch is None:
        del data["runtime_fetch"]
    else:
        data["runtime_fetch"] = fetch
    path = write_manifest(root, data)

    model, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {(code, None)}
    assert {row["digest"] for row in summary["deployment"]["members"]} == {"match"}
    assert "lib/driver.py" in file_names(model)


def test_restricted_runtime_fetch_is_declared_not_observed(world, tmp_path):
    root = device(tmp_path, world)
    statement = {"hash_pinning": "sha256-allowlist", "sources": ["https://updates.example.invalid/pkg"]}
    path = write_manifest(root, manifest(root, runtime_fetch={"mode": "restricted", "immutable_by": statement}))

    _, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "established"
    fetch = summary["deployment"]["runtime_fetch"]
    assert fetch["mode"] == "restricted" and fetch["immutable_by"] == statement
    assert "not observed" in fetch["basis"]


def test_manifest_for_another_target_is_invalid_input(world, tmp_path):
    root = device(tmp_path, world)
    path = write_manifest(root, manifest(root, target="stm32-other"))
    with pytest.raises(deployment.DeploymentError, match="does not match the SBOM target"):
        build_sbom(world, path)
    with pytest.raises(deployment.DeploymentError, match="does not match the SBOM target"):
        build_sbom(world, path, receipt=False)


def test_without_a_firmware_receipt_members_verify_but_images_stay_unaccounted(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"][0].pop("receipt_image")
    path = write_manifest(root, data)

    model, summary = build_sbom(world, path, receipt=False)

    assert summary["firmware_completeness"] == "not-established"
    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("receipt-required", "app"), ("receipt-required", "boot")}
    assert {row["digest"] for row in summary["deployment"]["members"]} == {"match"}
    assert summary["deployment"]["receipt"] is None
    assert {"lib/driver.py", "res/logo.bin", "modules/fast.mpy"} <= file_names(model)


def test_manifest_without_a_firmware_image_member_accounts_no_firmware(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"] = data["members"][1:]
    path = write_manifest(root, data)

    _, summary = build_sbom(world, path)

    assert ("no-firmware-image-member", None) in reasons(summary)
    assert ("receipt-image-not-in-deployment", None) in reasons(summary)
    assert summary["deployment_completeness"] == "not-established"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(schema="mpy-deployment/v2"),
        lambda d: d.pop("name"),
        lambda d: d.update(members=[]),
        lambda d: d["members"][2].update(kind="dynamic-library"),
        lambda d: d["members"][2].update(sha256="ABC"),
        lambda d: d["members"][2].update(receipt_image="app"),
        lambda d: d["members"][2].update(extra="field"),
        lambda d: d["members"][3].update(id="driver"),
        lambda d: d["members"][3].update(path="lib/driver.py"),
        lambda d: d.update(unknown=1),
        lambda d: d["exclusions"][0].pop("reason"),
        lambda d: d["exclusions"][0].update(reason="   "),
        lambda d: d["exclusions"][0].update(receipt_image="boot"),
        lambda d: d.update(runtime_fetch={"mode": "restricted"}),
        lambda d: d.update(runtime_fetch={"mode": "restricted", "immutable_by": {"hash_pinning": "p", "sources": []}}),
        lambda d: d.update(runtime_fetch={"mode": "restricted", "immutable_by": {"sources": ["s"]}}),
        lambda d: d.update(runtime_fetch={"mode": "none", "immutable_by": {"hash_pinning": "p", "sources": ["s"]}}),
        lambda d: d.update(runtime_fetch={"mode": "sometimes"}),
    ],
    ids=[
        "schema-version",
        "no-name",
        "no-members",
        "unknown-kind",
        "bad-digest",
        "receipt-image-on-file",
        "extra-member-key",
        "duplicate-id",
        "duplicate-path",
        "extra-top-key",
        "exclusion-without-reason",
        "exclusion-blank-reason",
        "exclusion-two-targets",
        "restricted-without-statement",
        "restricted-without-sources",
        "restricted-without-policy",
        "statement-on-none",
        "unknown-fetch-mode",
    ],
)
def test_malformed_manifest_is_rejected(world, tmp_path, change):
    root = device(tmp_path, world)
    data = manifest(root)
    change(data)
    with pytest.raises(deployment.DeploymentError):
        deployment.load_deployment(write_manifest(root, data))


def test_unparseable_manifest_is_rejected(tmp_path):
    for text in ("{", '{"name": 1, "name": 2}', "[]"):
        (tmp_path / "deployment.json").write_text(text)
        with pytest.raises(deployment.DeploymentError):
            deployment.load_deployment(tmp_path / "deployment.json")
    with pytest.raises(deployment.DeploymentError, match="cannot read"):
        deployment.load_deployment(tmp_path / "absent.json")


def test_cli_writes_the_deployment_into_both_documents_and_the_policy_gate_judges_it(
    world, tmp_path, capsys
):
    root = device(tmp_path, world)
    path = write_manifest(root, manifest(root))
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(scope_of(world.app)))
    outputs = {}
    for form, document in (("spdx", "sbom.spdx.json"), ("cyclonedx", "sbom.json")):
        output = tmp_path / f"out-{form}"
        code = sbom.main(
            [
                "--scope", str(scope_path),
                "--micropython-root", str(MPY_ROOT),
                "--build-receipt", str(world.receipt_path),
                "--deployment", str(path),
                "--format", form,
                "--output-dir", str(output),
            ]
        )
        assert code == 0
        outputs[form] = (json.loads((output / document).read_text()), output / "sbom-summary.json")
    assert "lib/driver.py" in {item["fileName"] for item in outputs["spdx"][0]["files"]}
    cyclonedx_top = next(item for item in outputs["cyclonedx"][0]["components"] if item["name"] == "fixture-device")
    assert "lib/driver.py" in {item["name"] for item in cyclonedx_top["components"]}
    summary_path = outputs["spdx"][1]
    summary = json.loads(summary_path.read_text())
    assert summary["deployment_completeness"] == "established"
    capsys.readouterr()

    for profile in ("deployable", "firmware-linked"):
        code = sbom_policy.main(
            [
                "--summary", str(summary_path),
                "--receipt", str(world.receipt_path),
                "--deployment", str(path),
                "--profile", profile,
            ]
        )
        result = json.loads(capsys.readouterr().out)
        assert code == 0 and result["result"] == "satisfied", profile
    assert result["dimensions"]["deployment"]["required"] is False
    assert result["dimensions"]["deployment"]["evidence"]["members_verified"] == 5

    (root / "lib/driver.py").write_bytes(b"changed after the SBOM was generated\n")
    code = sbom_policy.main(
        [
            "--summary", str(summary_path),
            "--receipt", str(world.receipt_path),
            "--deployment", str(path),
            "--profile", "deployable",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 2 and result["result"] == "invalid"


def test_cli_exits_2_for_a_manifest_that_cannot_be_used(world, tmp_path, capsys):
    root = device(tmp_path, world)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(scope_of(world.app)))
    for data in (manifest(root, target="elsewhere"), {"schema": "mpy-deployment/v1"}):
        path = write_manifest(root, data)
        output = tmp_path / "out"
        code = sbom.main(
            [
                "--scope", str(scope_path),
                "--micropython-root", str(MPY_ROOT),
                "--build-receipt", str(world.receipt_path),
                "--deployment", str(path),
                "--output-dir", str(output),
            ]
        )
        assert code == 2
        assert not (output / "sbom-summary.json").exists()
    assert "deployment" in capsys.readouterr().err


def test_deployable_profile_requires_both_dimensions(world, tmp_path, capsys):
    root = device(tmp_path, world)
    cases = {
        "established": (manifest(root), 0),
        "not-established": (manifest(root, runtime_fetch={"mode": "unrestricted"}), 1),
    }
    for name, (data, expected) in cases.items():
        path = write_manifest(root, data)
        _, summary = build_sbom(world, path)
        code, result = policy(
            tmp_path, summary, "deployable", receipt=world.receipt_path, manifest_path=path, capsys=capsys
        )
        assert code == expected, name
        assert result["dimensions"]["deployment"]["observed"] == (
            "satisfied" if expected == 0 else "unsatisfied"
        )
        linked, linked_result = policy(
            tmp_path, summary, "firmware-linked", receipt=world.receipt_path, capsys=capsys
        )
        assert linked == 0 and linked_result["dimensions"]["deployment"]["status"] == "not-applicable"

    _, summary = build_sbom(world, None)
    code, result = policy(tmp_path, summary, "deployable", receipt=world.receipt_path, capsys=capsys)
    assert code == 1 and result["unsatisfied"] == ["deployment"]
    assert result["dimensions"]["deployment"]["evidence"] == {"value": "not-declared"}
    code, _ = policy(tmp_path, summary, "firmware-linked", receipt=world.receipt_path, capsys=capsys)
    assert code == 0


def test_deployment_state_never_fails_the_firmware_profiles(tmp_path, capsys):
    from test_firmware_receipt import make_receipt
    from test_sbom_policy import make_summary

    receipt, _ = make_receipt(tmp_path / "build-tree")
    block = {
        "schema": "mpy-deployment/v1",
        "status": "not-established",
        "reasons": [{"code": "runtime-fetch-undeclared", "member": None, "detail": "undeclared"}],
        "members": [],
        "runtime_fetch": {"mode": "undeclared"},
    }
    states = {
        "key absent": {},
        "not-declared": {"deployment_completeness": "not-declared", "deployment": None},
        "not-established": {"deployment_completeness": "not-established", "deployment": block},
    }
    for name, extra in states.items():
        summary = make_summary(receipt) | extra
        for profile in ("firmware-linked", "firmware-complete"):
            code, result = policy(tmp_path, summary, profile, capsys=capsys)
            assert code == 0 and result["result"] == "satisfied", (name, profile)
        code, result = policy(tmp_path, summary, "deployable", capsys=capsys)
        assert code == 1 and result["unsatisfied"] == ["deployment"], name


def test_policy_rejects_a_summary_that_overclaims_deployment(world, tmp_path, capsys):
    root = device(tmp_path, world)
    path = write_manifest(root, manifest(root, runtime_fetch={"mode": "unrestricted"}))
    _, summary = build_sbom(world, path)

    forged = json.loads(json.dumps(summary))
    forged["deployment_completeness"] = "established"
    code, result = policy(tmp_path, forged, "deployable", capsys=capsys)
    assert code == 2 and result["result"] == "invalid"

    forged = json.loads(json.dumps(summary))
    forged["deployment_completeness"] = forged["deployment"]["status"] = "established"
    forged["deployment"]["reasons"] = []
    code, result = policy(tmp_path, forged, "deployable", capsys=capsys)
    assert code == 2 and any("runtime fetch" in error for error in result["errors"])

    forged = json.loads(json.dumps(summary))
    forged["deployment_completeness"] = "not-declared"
    code, result = policy(tmp_path, forged, "deployable", capsys=capsys)
    assert code == 2


def test_schema_file_is_a_valid_draft7_schema():
    from jsonschema import Draft7Validator

    schema = json.loads((Path(deployment.__file__).parent / "data" / "deployment-v1.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    assert schema["properties"]["schema"] == {"const": deployment.SCHEMA}


def test_other_artifacts_of_a_bound_image_can_be_excluded_by_path(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["exclusions"].append(
        {"path": str(world.images["app.elf"]), "reason": "debug ELF of the deployed application binary"}
    )
    path = write_manifest(root, data)

    _, summary = build_sbom(world, path)

    assert summary["deployment_completeness"] == "established"
    assert len(summary["deployment"]["exclusions"]) == 2


def test_documented_h563_example_is_a_valid_manifest_and_not_an_accepted_deployment():
    example = Path(deployment.__file__).resolve().parents[1] / "examples" / "deployment-nucleo-h563zi.example.json"
    loaded = deployment.load_deployment(example)
    assert loaded.data["name"].startswith("example-")
    assert [item["kind"] for item in loaded.data["members"]] == ["firmware-image", "bootloader-image"]
