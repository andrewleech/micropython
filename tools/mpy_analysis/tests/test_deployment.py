"""Deployment manifest verification: member bytes, link evidence binding, runtime fetch and SBOM contents.

The link evidence describes an application image (with a `.bin` deployment artifact) and a bootloader
image; the manifests name copies of those files and files of a device tree that exist only in the test.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from link_fixture import image_record, write_evidence
from mpy_analysis import deployment, sbom, sbom_policy
from test_sbom import compiled_selection

MPY_ROOT = Path(__file__).resolve().parents[3]
DEVICE_FILES = {
    "lib/driver.py": b"def drive():\n    return 1\n",
    "res/logo.bin": b"\x00logo-bytes",
    "modules/fast.mpy": b"M\x06\x00native-module",
    "examples/demo.py": b"print('example that is never deployed')\n",
}
DEPLOYED_FILES = {"lib/driver.py", "res/logo.bin", "modules/fast.mpy"}


@dataclass
class World:
    scope: dict
    evidence: Path
    images: dict[str, Path]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("deployment")
    inventory, _ = compiled_selection(tmp_path)
    build = tmp_path / "build"
    build.mkdir()
    binary = build / "application.bin"
    binary.write_bytes(b"application flash image")
    application = image_record(
        build,
        "app",
        "application",
        [{"root": "app", "path": "selected.c"}],
        artifacts=[{"role": "deployment", "path": str(binary), "sha256": sha256(binary.read_bytes())}],
    )
    bootloader = image_record(build, "boot", "bootloader", [])
    evidence = write_evidence(build / "evidence.json", [application, bootloader])
    images = {"app.bin": binary, "app.elf": build / "app.elf", "boot.elf": build / "boot.elf"}
    assert len({sha256(path.read_bytes()) for path in images.values()}) == 3
    return World(inventory, evidence, images)


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
    return {"id": ident, "kind": kind, "path": relative, "sha256": sha256((root / relative).read_bytes()), **extra}


def manifest(root: Path, **overrides) -> dict:
    data = {
        "schema": "mpy-deployment/v1",
        "name": "fixture-device",
        "target": "fixture",
        "members": [
            member(root, "app", "firmware-image", "images/app.bin", image="app"),
            member(root, "boot", "bootloader-image", "images/boot.elf", image="boot"),
            member(root, "driver", "python-package-file", "lib/driver.py"),
            member(root, "logo", "resource", "res/logo.bin"),
            member(root, "fast", "native-module", "modules/fast.mpy"),
        ],
        "runtime_fetch": {"mode": "none"},
        "exclusions": [{"path": "examples/demo.py", "reason": "example, not installed on the device"}],
    }
    return data | overrides


def write_manifest(root: Path, data: dict) -> Path:
    path = root / "deployment.json"
    path.write_text(json.dumps(data))
    return path


def build_sbom(world: World, path: Path | None, *, evidence=True):
    """Model and summary for the fixture scope, with or without its link evidence."""
    return sbom.generate_sbom(
        world.scope, micropython_root=MPY_ROOT, link_evidence=world.evidence if evidence else None, deployment=path
    )


def reasons(summary) -> set[tuple[str, str | None]]:
    return {(item["code"], item["member"]) for item in summary["deployment"]["reasons"]}


def rows(summary) -> dict[str, dict]:
    return {row["id"]: row for row in summary["deployment"]["members"]}


def file_names(model) -> set[str]:
    return {item.name for item in model.components.values() if item.kind == "file"}


def test_complete_deployment_binds_images_and_adds_only_deployed_files(world, tmp_path):
    root = device(tmp_path, world)

    model, summary = build_sbom(world, write_manifest(root, manifest(root)))

    assert summary["firmware_completeness"] == "established"
    assert summary["deployment_completeness"] == "established"
    block = summary["deployment"]
    assert block["status"] == "established" and block["reasons"] == []
    assert block["link_evidence"] == {"evidence_id": json.loads(world.evidence.read_text())["evidence_id"]}
    assert {row["digest"] for row in block["members"]} == {"match"}
    assert rows(summary)["app"]["image_binding"] == {
        "image": "app",
        "artifact_role": "deployment",
        "artifact_path": str(world.images["app.bin"].resolve()),
        "bound_by": "image",
    }
    assert rows(summary)["boot"]["image_binding"]["artifact_role"] == "elf"
    assert [(item["id"], item["members"]) for item in block["images"]] == [("app", ["app"]), ("boot", ["boot"])]
    names = file_names(model)
    assert DEPLOYED_FILES <= names
    assert "examples/demo.py" not in names
    assert not any(name.startswith("images/") for name in names)


@pytest.mark.parametrize("file, role", [("images/app.bin", "deployment"), ("images/app.elf", "elf")])
def test_unnamed_image_binds_by_the_hash_of_an_evidence_artifact(world, tmp_path, file, role):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"][0] = member(root, "app", "firmware-image", file)

    _, summary = build_sbom(world, write_manifest(root, data))

    assert summary["deployment_completeness"] == "established"
    assert rows(summary)["app"]["image_binding"]["artifact_role"] == role
    assert rows(summary)["app"]["image_binding"]["bound_by"] == "hash"


def test_without_a_manifest_deployment_is_not_declared_and_no_file_is_added(world):
    model, summary = build_sbom(world, None)

    assert summary["deployment_completeness"] == "not-declared"
    assert summary["deployment"] is None
    assert not file_names(model) & set(DEVICE_FILES)


@pytest.mark.parametrize("what", ["file-bytes", "declared-hash"])
def test_modified_member_invalidates_closure_and_keeps_the_rest(world, tmp_path, what):
    root = device(tmp_path, world)
    data = manifest(root)
    if what == "file-bytes":
        (root / "lib/driver.py").write_bytes(DEVICE_FILES["lib/driver.py"] + b"# tampered\n")
    else:
        data["members"][2]["sha256"] = sha256(b"other bytes")

    model, summary = build_sbom(world, write_manifest(root, data))

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("member-hash-mismatch", "driver")}
    assert rows(summary)["driver"]["actual_sha256"] == sha256((root / "lib/driver.py").read_bytes())
    names = file_names(model)
    assert "lib/driver.py" not in names and {"res/logo.bin", "modules/fast.mpy"} <= names
    assert summary["firmware_completeness"] == "established"


@pytest.mark.parametrize("directory", [False, True], ids=["missing", "directory"])
def test_missing_or_non_file_member(world, tmp_path, directory):
    root = device(tmp_path, world)
    data = manifest(root)
    (root / "res/logo.bin").unlink()
    if directory:
        (root / "res/logo.bin").mkdir()

    model, summary = build_sbom(world, write_manifest(root, data))

    assert reasons(summary) == {("member-not-a-file" if directory else "member-missing", "logo")}
    assert rows(summary)["logo"]["actual_sha256"] is None
    assert "res/logo.bin" not in file_names(model)


def test_firmware_image_that_the_evidence_does_not_contain_is_not_established(world, tmp_path):
    root = device(tmp_path, world)
    (root / "images/other.bin").write_bytes(b"an application image nobody built")
    data = manifest(root)
    data["members"][0] = member(root, "app", "firmware-image", "images/other.bin")

    _, summary = build_sbom(world, write_manifest(root, data))

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("firmware-image-not-in-evidence", "app"), ("image-not-in-deployment", None)}
    assert rows(summary)["app"]["binding"] == "rejected"


@pytest.mark.parametrize(
    "image, code",
    [("nope", "image-unknown"), ("app", "image-hash-mismatch"), ("boot", "image-role-mismatch")],
)
def test_named_image_must_exist_with_its_role_and_a_matching_hash(world, tmp_path, image, code):
    root = device(tmp_path, world)
    shutil.copy(root / "images/boot.elf", root / "images/copy.elf")
    data = manifest(root)
    data["members"][0] = member(root, "app", "firmware-image", "images/copy.elf", image=image)

    _, summary = build_sbom(world, write_manifest(root, data))

    assert summary["deployment_completeness"] == "not-established"
    assert (code, "app") in reasons(summary)


def test_image_missing_from_manifest_needs_a_stated_exclusion(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"].pop(1)
    _, summary = build_sbom(world, write_manifest(root, data))
    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("image-not-in-deployment", None)}

    data["exclusions"].append({"image": "boot", "reason": "flashed by the factory programmer"})
    _, summary = build_sbom(world, write_manifest(root, data))
    assert summary["deployment_completeness"] == "established"
    [boot] = [item for item in summary["deployment"]["images"] if item["id"] == "boot"]
    assert boot["excluded"] and boot["exclusion_reason"] == "flashed by the factory programmer"

    data["exclusions"][-1]["image"] = "unknown"
    _, summary = build_sbom(world, write_manifest(root, data))
    assert ("exclusion-image-unknown", None) in reasons(summary)


def test_excluding_an_application_image_is_rejected(world, tmp_path):
    root = device(tmp_path, world)
    data = manifest(root)
    data["members"].pop(0)
    data["exclusions"].append({"image": "app", "reason": "not needed"})
    with pytest.raises(deployment.DeploymentError, match="firmware image bytes cannot be left out"):
        build_sbom(world, write_manifest(root, data))

    data = manifest(root)
    data["exclusions"].append({"path": "images/app.bin", "reason": "not needed"})
    with pytest.raises(deployment.DeploymentError, match="firmware image member app"):
        deployment.load_deployment(write_manifest(root, data))


@pytest.mark.parametrize(
    "fetch, code",
    [({"mode": "unrestricted"}, "runtime-fetch-unrestricted"), (None, "runtime-fetch-undeclared")],
    ids=["unrestricted", "absent"],
)
def test_unrestricted_or_undeclared_runtime_fetch_blocks_the_claim(world, tmp_path, fetch, code):
    root = device(tmp_path, world)
    data = manifest(root)
    del data["runtime_fetch"]
    if fetch:
        data["runtime_fetch"] = fetch

    model, summary = build_sbom(world, write_manifest(root, data))

    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {(code, None)}
    assert {row["digest"] for row in summary["deployment"]["members"]} == {"match"}
    assert "lib/driver.py" in file_names(model)


def test_manifest_for_another_target_is_invalid_input(world, tmp_path):
    root = device(tmp_path, world)
    path = write_manifest(root, manifest(root, target="stm32-other"))
    for evidence in (True, False):
        with pytest.raises(deployment.DeploymentError, match="does not match the SBOM target"):
            build_sbom(world, path, evidence=evidence)


def test_without_link_evidence_members_verify_but_images_stay_unaccounted(world, tmp_path):
    root = device(tmp_path, world)

    model, summary = build_sbom(world, write_manifest(root, manifest(root)), evidence=False)

    assert summary["firmware_completeness"] == "not-established"
    assert summary["deployment_completeness"] == "not-established"
    assert reasons(summary) == {("link-evidence-required", "app"), ("link-evidence-required", "boot")}
    assert {row["digest"] for row in summary["deployment"]["members"]} == {"match"}
    assert summary["deployment"]["link_evidence"] is None
    assert DEPLOYED_FILES <= file_names(model)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["members"][2].update(sha256="ABC"),
        lambda d: d["members"][3].update(path="lib/driver.py"),
        lambda d: d["exclusions"].append({"path": "lib/driver.py", "reason": "a member cannot be excluded"}),
        lambda d: d.update(runtime_fetch={"mode": "restricted"}),
    ],
    ids=["bad-digest", "duplicate-path", "excluded-member", "restricted-without-statement"],
)
def test_invalid_manifest_is_rejected(world, tmp_path, change):
    root = device(tmp_path, world)
    data = manifest(root)
    change(data)
    with pytest.raises(deployment.DeploymentError):
        deployment.load_deployment(write_manifest(root, data))


@pytest.mark.parametrize("text", ["{", '{"name": 1, "name": 2}', "[]", None], ids=["syntax", "duplicate-key", "not-object", "absent"])
def test_unreadable_manifest_is_rejected(tmp_path, text):
    path = tmp_path / "deployment.json"
    if text is not None:
        path.write_text(text)
    with pytest.raises(deployment.DeploymentError):
        deployment.load_deployment(path)


def test_schema_file_is_a_valid_draft7_schema():
    from jsonschema import Draft7Validator

    schema = json.loads((Path(deployment.__file__).parent / "data" / "deployment-v1.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    assert schema["properties"]["schema"] == {"const": deployment.SCHEMA}


def test_documented_h563_example_is_a_valid_manifest():
    example = Path(deployment.__file__).resolve().parents[1] / "examples" / "deployment-nucleo-h563zi.example.json"
    loaded = deployment.load_deployment(example)
    assert [item["kind"] for item in loaded.data["members"]] == ["firmware-image", "bootloader-image"]


def test_cli_writes_the_deployment_into_both_documents_and_the_policy_gate_judges_it(world, tmp_path, capsys):
    root = device(tmp_path, world)
    path = write_manifest(root, manifest(root))
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(world.scope))
    documents = {}
    for form, name in (("spdx", "sbom.spdx.json"), ("cyclonedx", "sbom.json")):
        output = tmp_path / f"out-{form}"
        argv = ["--scope", str(scope_path), "--micropython-root", str(MPY_ROOT)]
        argv += ["--link-evidence", str(world.evidence), "--deployment", str(path)]
        assert sbom.main(argv + ["--format", form, "--output-dir", str(output)]) == 0
        documents[form] = json.loads((output / name).read_text())
    assert DEPLOYED_FILES <= {item["fileName"] for item in documents["spdx"]["files"]}
    top = next(item for item in documents["cyclonedx"]["components"] if item["name"] == "fixture-device")
    assert {item["name"] for item in top["components"]} == DEPLOYED_FILES
    capsys.readouterr()

    summary = tmp_path / "out-spdx" / "sbom-summary.json"
    argv = ["--summary", str(summary), "--link-evidence", str(world.evidence), "--deployment", str(path)]
    assert sbom_policy.main(argv + ["--profile", "deployable"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "satisfied"
    assert result["dimensions"]["deployment"]["evidence"]["members_verified"] == 5

    (root / "lib/driver.py").write_bytes(b"changed after the SBOM was generated\n")
    assert sbom_policy.main(argv + ["--profile", "deployable"]) == 2
    assert json.loads(capsys.readouterr().out)["result"] == "invalid"
