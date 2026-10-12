"""Validate versioned build/link evidence before associating it with an SBOM.

This module accepts evidence captured by a build adapter. It does not infer a
build from a compilation database, existing linker map, or artifact timestamps.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

SCHEMA = "mpy-firmware-build/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class FirmwareReceiptError(ValueError):
    """The receipt is malformed, stale, or internally inconsistent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def receipt_id(receipt: dict[str, Any]) -> str:
    body = {key: value for key, value in receipt.items() if key not in {"receipt_id"}}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FirmwareReceiptError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise FirmwareReceiptError(f"{label} must be a nonempty string without NUL")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise FirmwareReceiptError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _path_record(value: Any, label: str, *, verify_files: bool) -> dict[str, Any]:
    record = _object(value, label)
    path = Path(_text(record.get("path"), f"{label}.path"))
    if not path.is_absolute():
        raise FirmwareReceiptError(f"{label}.path must be absolute: {path}")
    digest = _digest(record.get("sha256"), f"{label}.sha256")
    if verify_files:
        try:
            with path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise FirmwareReceiptError(f"{label} is not a regular file: {path}")
                hasher = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
                after = os.fstat(stream.fileno())
        except OSError as exc:
            raise FirmwareReceiptError(f"{label} is unavailable: {path}") from exc
        before_state = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_state = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_state != after_state:
            raise FirmwareReceiptError(f"{label} changed while being verified: {path}")
        if hasher.hexdigest() != digest:
            raise FirmwareReceiptError(f"{label} is stale: {path}")
    return {**record, "path": str(path.resolve(strict=False)), "sha256": digest}


def _records(values: Any, label: str, *, verify_files: bool) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise FirmwareReceiptError(f"{label} must be an array")
    return [_path_record(value, f"{label}[{index}]", verify_files=verify_files)
            for index, value in enumerate(values)]


def _source_bindings(value: Any, label: str, repository_names: set[str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise FirmwareReceiptError(f"{label} must identify at least one source boundary")
    result = []
    for index, raw in enumerate(value):
        binding = _object(raw, f"{label}[{index}]")
        root = _text(binding.get("root"), f"{label}[{index}].root")
        path = _text(binding.get("path"), f"{label}[{index}].path")
        relative = Path(path)
        if (
            root not in repository_names
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in path
            or relative.as_posix() != path
        ):
            raise FirmwareReceiptError(
                f"{label}[{index}] must bind a canonical contained path under a declared source root"
            )
        via = binding.get("via")
        if via not in (None, "generator"):
            raise FirmwareReceiptError(f"{label}[{index}].via must be absent or 'generator'")
        result.append({"root": root, "path": path, **({"via": via} if via else {})})
    if len({(item["root"], item["path"]) for item in result}) != len(result):
        raise FirmwareReceiptError(f"{label} repeats a source boundary")
    return result



def _validate_actions(actions, build_id, verify_files):
    if not isinstance(actions, list) or not actions:
        raise FirmwareReceiptError("actions must record compiler, assembler, archive, link and transform invocations")
    action_ids = set()
    action_inputs = {}
    outputs = {}
    output_versions = {}
    compiled_objects = {}
    allowed_kinds = {"compile", "assemble", "archive", "link", "generate", "transform", "sign", "package", "deploy"}
    for index, raw in enumerate(actions):
        action = _object(raw, f"actions[{index}]")
        action_id = _text(action.get("id"), f"actions[{index}].id")
        if action_id in action_ids:
            raise FirmwareReceiptError(f"duplicate action id: {action_id}")
        action_ids.add(action_id)
        if action.get("build_id") != build_id:
            raise FirmwareReceiptError(f"action {action_id} belongs to a different build")
        kind = _text(action.get("kind"), f"actions[{index}].kind")
        if kind not in allowed_kinds:
            raise FirmwareReceiptError(f"action {action_id} has unsupported kind: {kind}")
        argv = action.get("argv")
        if not isinstance(argv, list) or not argv or any(
            not isinstance(arg, str) or "\x00" in arg for arg in argv
        ):
            raise FirmwareReceiptError(f"action {action_id} argv must be a nonempty string array")
        cwd = Path(_text(action.get("cwd"), f"action {action_id}.cwd"))
        if not cwd.is_absolute():
            raise FirmwareReceiptError(f"action {action_id}.cwd must be absolute")
        if action.get("exit_code") != 0:
            raise FirmwareReceiptError(f"action {action_id} did not succeed")
        _path_record(action.get("executable"), f"action {action_id}.executable", verify_files=verify_files)
        inputs = _records(action.get("inputs"), f"action {action_id}.inputs", verify_files=False)
        for item in inputs:
            _text(item.get("role"), f"action {action_id} input role")
            previous = outputs.get(item["path"])
            if previous is not None:
                if previous[0] != item["sha256"]:
                    raise FirmwareReceiptError(
                        f"action {action_id} consumes a stale output version: {item['path']}"
                    )
            elif verify_files:
                _path_record(item, f"action {action_id} input", verify_files=True)
        action_inputs[action_id] = {item["path"]: item for item in inputs}
        if len(action_inputs[action_id]) != len(inputs):
            raise FirmwareReceiptError(f"action {action_id} repeats an input path")
        evidence = action.get("dependency_evidence", [])
        if not isinstance(evidence, list):
            raise FirmwareReceiptError(f"action {action_id}.dependency_evidence must be an array")
        evidence_paths = set()
        for evidence_index, raw_evidence in enumerate(evidence):
            label = f"action {action_id}.dependency_evidence[{evidence_index}]"
            item = _object(raw_evidence, label)
            path = Path(_text(item.get("path"), f"{label}.path"))
            if not path.is_absolute():
                raise FirmwareReceiptError(f"{label}.path must be absolute")
            if str(path) in evidence_paths:
                raise FirmwareReceiptError(f"{label}.path is duplicated")
            evidence_paths.add(str(path))
            _digest(item.get("sha256"), f"{label}.sha256")
            captured_ns = item.get("captured_ns")
            if not isinstance(captured_ns, int) or isinstance(captured_ns, bool) or captured_ns <= 0:
                raise FirmwareReceiptError(f"{label}.captured_ns must be a positive integer")
            dependencies = item.get("dependencies")
            if not isinstance(dependencies, list):
                raise FirmwareReceiptError(f"{label}.dependencies must be an array")
            for dependency_index, raw_dependency in enumerate(dependencies):
                dependency_label = f"{label}.dependencies[{dependency_index}]"
                dependency = _object(raw_dependency, dependency_label)
                dependency_path = str(Path(_text(dependency.get("path"), f"{dependency_label}.path")))
                if not Path(dependency_path).is_absolute():
                    raise FirmwareReceiptError(f"{dependency_label}.path must be absolute")
                digest = _digest(dependency.get("sha256"), f"{dependency_label}.sha256")
                captured_input = action_inputs[action_id].get(dependency_path)
                if captured_input is None or captured_input["sha256"] != digest:
                    raise FirmwareReceiptError(f"{dependency_label} is not bound to a captured action input")
        action_outputs = _records(action.get("outputs"), f"action {action_id}.outputs", verify_files=False)
        for output in action_outputs:
            role = _text(output.get("role"), f"action {action_id} output role")
            path = output["path"]
            previous = outputs.get(path)
            if previous and previous[0] != output["sha256"]:
                consumed = action_inputs[action_id].get(path)
                if consumed is None or consumed["sha256"] != previous[0]:
                    raise FirmwareReceiptError(f"output path was overwritten without consuming its prior version: {path}")
            outputs[path] = (output["sha256"], action_id)
            output_versions.setdefault(path, []).append((output["sha256"], action_id))
            if kind in ("compile", "assemble") and role == "object":
                image = _text(action.get("image"), f"action {action_id}.image")
                compiled_objects[(image, path)] = output
        if kind == "link" and not action_outputs:
            raise FirmwareReceiptError(f"link action {action_id} has no output")
    if verify_files:
        for path, (digest, action_id) in outputs.items():
            _path_record(
                {"path": path, "sha256": digest},
                f"action {action_id} final output",
                verify_files=True,
            )
    return action_inputs, outputs, output_versions, compiled_objects

def _validate_toolchain_library(operand, path, image_id, toolchain_executables):
    toolchain = _object(operand.get("toolchain"), f"image {image_id} toolchain library {path}")
    executable = _object(toolchain.get("executable"), f"image {image_id} toolchain library executable")
    key = (toolchain.get("role"), executable.get("path"))
    if toolchain_executables.get(key) != executable.get("sha256"):
        raise FirmwareReceiptError(f"toolchain library is not bound to a captured toolchain tool: {path}")
    members = operand.get("contributing_members")
    if not isinstance(members, list) or not members or not all(isinstance(item, str) and item for item in members):
        raise FirmwareReceiptError(f"toolchain library must list contributing members: {path}")
    _text(operand.get("evidence"), f"image {image_id} toolchain library evidence")


def _validate_system_package(package, path, label, verify_files):
    package = _object(package, f"{label} package")
    if package.get("manager") != "dpkg":
        raise FirmwareReceiptError(f"{label} package manager is unsupported: {path}")
    for field in ("name", "version"):
        _text(package.get(field), f"{label} package {field}")
    for field in ("architecture", "maintainer"):
        if not isinstance(package.get(field), str):
            raise FirmwareReceiptError(f"{label} package {field} must be text: {path}")
    md5sum = package.get("md5sum")
    if not isinstance(md5sum, str) or len(md5sum) != 32 or any(c not in "0123456789abcdef" for c in md5sum):
        raise FirmwareReceiptError(f"{label} package md5sum must be a lowercase MD5 digest: {path}")
    distro = _object(package.get("distro"), f"{label} package distro")
    for field in ("id", "version_id"):
        if not isinstance(distro.get(field), str):
            raise FirmwareReceiptError(f"{label} package distro {field} must be text: {path}")
    if verify_files:
        digest = hashlib.md5(usedforsecurity=False)
        try:
            digest.update(Path(path).read_bytes())
        except OSError as exc:
            raise FirmwareReceiptError(f"cannot verify package checksum: {path}") from exc
        if digest.hexdigest() != md5sum:
            raise FirmwareReceiptError(f"stale system file does not match its package checksum: {path}")


def _validate_implicit_inputs(link, image_id, operand_paths, verify_files):
    entries = link.get("implicit_inputs", [])
    if not isinstance(entries, list):
        raise FirmwareReceiptError(f"image {image_id}.link.implicit_inputs must be an array")
    seen = set()
    for index, raw in enumerate(entries):
        label = f"image {image_id}.link.implicit_inputs[{index}]"
        entry = _object(raw, label)
        record = _path_record(
            {"path": entry.get("path"), "sha256": entry.get("sha256")}, label, verify_files=verify_files
        )
        path = record["path"]
        if path in seen or path in operand_paths:
            raise FirmwareReceiptError(f"{label} repeats a link input: {path}")
        seen.add(path)
        if entry.get("origin") != "gnu-map-load":
            raise FirmwareReceiptError(f"{label}.origin is unsupported")
        if entry.get("kind") not in ("object", "archive", "shared-library", "script"):
            raise FirmwareReceiptError(f"{label}.kind is unsupported")
        _text(entry.get("evidence"), f"{label}.evidence")
        disposition = entry.get("disposition")
        if disposition == "no-contribution":
            _text(entry.get("reason"), f"{label}.reason")
        elif disposition == "contributed":
            if entry["kind"] == "script" or entry.get("linkage") not in ("static", "dynamic"):
                raise FirmwareReceiptError(f"{label} contributes without a code linkage")
            if entry["kind"] == "archive":
                members = entry.get("contributing_members")
                if not isinstance(members, list) or not members or not all(
                    isinstance(item, str) and item for item in members
                ):
                    raise FirmwareReceiptError(f"{label} archive must list contributing members")
            _validate_system_package(entry.get("package"), path, label, verify_files)
        else:
            raise FirmwareReceiptError(f"{label}.disposition must be explicit")



def _validate_link_operands(link, image_id, link_id, action_inputs, repo_names, verify_files, toolchain_executables):
    operands = link.get("operands")
    if not isinstance(operands, list):
        raise FirmwareReceiptError(f"image {image_id}.link.operands must enumerate link inputs")
    by_operand = {}
    for row_index, raw_operand in enumerate(operands):
        operand = _object(raw_operand, f"image {image_id}.link.operands[{row_index}]")
        record = _path_record(
            operand.get("input"),
            f"image {image_id}.link.operands[{row_index}].input",
            verify_files=verify_files,
        )
        path = record["path"]
        if path in by_operand:
            raise FirmwareReceiptError(f"image {image_id} repeats link operand: {path}")
        action_input = action_inputs[link_id].get(path)
        if action_input is None or action_input["sha256"] != record["sha256"]:
            raise FirmwareReceiptError(f"link operand is not bound to captured action input: {path}")
        kind = _text(operand.get("kind"), f"image {image_id}.link.operands[{row_index}].kind")
        if kind not in ("object", "archive", "prebuilt", "script", "response", "library", "other"):
            raise FirmwareReceiptError(f"image {image_id} has unsupported link operand kind: {kind}")
        disposition = operand.get("disposition")
        if disposition not in ("contributed", "discarded", "no-contribution", "unsupported"):
            raise FirmwareReceiptError(f"image {image_id} operand disposition must be explicit")
        if disposition == "no-contribution":
            _text(operand.get("reason"), f"image {image_id} no-contribution reason")
        if disposition == "unsupported":
            _text(operand.get("reason"), f"image {image_id} unsupported-input reason")
        if disposition == "contributed" and kind == "library" and "system_package" in operand:
            _validate_system_package(
                operand["system_package"], path, f"image {image_id} system library", verify_files
            )
            members = operand.get("contributing_members")
            if not isinstance(members, list) or not members or not all(
                isinstance(item, str) and item for item in members
            ):
                raise FirmwareReceiptError(f"system library must list contributing members: {path}")
            _text(operand.get("evidence"), f"image {image_id} system library evidence")
        elif disposition == "contributed" and kind == "library" and "toolchain" in operand:
            _validate_toolchain_library(operand, path, image_id, toolchain_executables)
        elif disposition == "contributed" and kind in ("object", "archive", "prebuilt", "library"):
            _source_bindings(
                operand.get("source_bindings"),
                f"image {image_id} operand source_bindings",
                repo_names,
            )
        if kind == "archive":
            _validate_archive_members(operand, path, image_id, disposition, repo_names)
        elif "members" in operand or "members_complete" in operand:
            raise FirmwareReceiptError(f"non-archive link operand carries archive members: {path}")
        by_operand[path] = operand
    for path, input_record in action_inputs[link_id].items():
        if input_record.get("role") == "link-operand" and path not in by_operand:
            raise FirmwareReceiptError(f"captured link operand has no contribution decision: {path}")
    return by_operand


def _validate_archive_members(operand, path, image_id, disposition, repo_names):
    members = operand.get("members")
    if operand.get("members_complete") is not True or not isinstance(members, list):
        raise FirmwareReceiptError(f"archive member inventory is not complete: {path}")
    names = set()
    member_dispositions = set()
    for member_index, raw_member in enumerate(members):
        member = _object(raw_member, f"image {image_id} archive member {member_index}")
        name = _text(member.get("name"), f"image {image_id} archive member name")
        if name in names:
            raise FirmwareReceiptError(f"archive repeats member name {name}: {path}")
        names.add(name)
        _digest(member.get("sha256"), f"archive member {name} sha256")
        member_disposition = member.get("disposition")
        if member_disposition not in ("contributed", "discarded", "not-extracted"):
            raise FirmwareReceiptError(f"archive member {name} has no inclusion decision")
        member_dispositions.add(member_disposition)
        if member_disposition == "contributed":
            _source_bindings(
                member.get("source_bindings"),
                f"image {image_id} archive member {name} source_bindings",
                repo_names,
            )
        _text(member.get("evidence"), f"archive member {name} evidence")
    if disposition == "contributed" and "contributed" not in member_dispositions:
        raise FirmwareReceiptError(f"archive has no contributing members: {path}")
    if disposition != "contributed" and "contributed" in member_dispositions:
        raise FirmwareReceiptError(f"archive disposition conflicts with contributing members: {path}")

def _validate_object_accounting(image, image_id, repo_names, compiled_objects, by_operand, verify_files):
    object_rows = image.get("object_accounting")
    if not isinstance(object_rows, list):
        raise FirmwareReceiptError(f"image {image_id}.object_accounting must enumerate compiler/assembler outputs")
    image_objects = {path: value for (owner, path), value in compiled_objects.items() if owner == image_id}
    accounted = {}
    for row_index, raw_object in enumerate(object_rows):
        row = _object(raw_object, f"image {image_id}.object_accounting[{row_index}]")
        obj = _path_record(row.get("object"), f"image {image_id} object", verify_files=verify_files)
        if obj["path"] in accounted:
            raise FirmwareReceiptError(f"image {image_id} repeats object accounting: {obj['path']}")
        captured = image_objects.get(obj["path"])
        if not captured or captured.get("sha256") != obj["sha256"]:
            raise FirmwareReceiptError(f"object is not bound to a captured compile/assemble action: {obj['path']}")
        disposition = row.get("disposition")
        if disposition not in ("contributed", "discarded", "not-linked", "unsupported"):
            raise FirmwareReceiptError(f"image {image_id} object disposition must be explicit")
        if disposition in ("not-linked", "unsupported"):
            _text(row.get("reason"), f"image {image_id} object disposition reason")
        bindings = row.get("source_bindings")
        if disposition == "unsupported" and bindings == []:
            pass
        else:
            _source_bindings(bindings, f"image {image_id} object source_bindings", repo_names)
        _text(row.get("evidence"), f"image {image_id} object accounting evidence")
        accounted[obj["path"]] = disposition
    if set(accounted) != set(image_objects):
        missing = sorted(set(image_objects) - set(accounted))
        raise FirmwareReceiptError(
            f"image {image_id} compiler/assembler objects lack link decisions: {missing}"
        )
    for path, operand in by_operand.items():
        if (
            operand["kind"] == "object"
            and operand["disposition"] == "contributed"
            and accounted.get(path) != "contributed"
        ):
            raise FirmwareReceiptError(
                f"linked object disposition disagrees with object accounting: {path}"
            )
def validate_firmware_receipt(
    value: dict[str, Any], *, verify_files: bool = True
) -> dict[str, Any]:
    """Validate identity, successful actions, linked contributions and current artifact hashes.

    A complete receipt must be produced by a qualified adapter and carry explicit
    inclusion decisions. Unknown linker formats, LTO transformations, or inputs
    remain incomplete instead of being guessed from filenames.
    """
    receipt = _object(value, "firmware receipt")
    if receipt.get("schema") != SCHEMA:
        raise FirmwareReceiptError(f"unsupported firmware receipt schema: {receipt.get('schema')!r}")
    build_id = _digest(receipt.get("build_id"), "build_id")
    if receipt.get("receipt_id") != receipt_id(receipt):
        raise FirmwareReceiptError("receipt_id does not match canonical receipt content")

    configuration = _object(receipt.get("configuration"), "configuration")
    for field in ("port", "target", "variant"):
        _text(configuration.get(field), f"configuration.{field}")
    if not isinstance(configuration.get("variables"), dict):
        raise FirmwareReceiptError("configuration.variables must be an object of effective build variables")

    repositories = receipt.get("source_repositories")
    if not isinstance(repositories, list) or not repositories:
        raise FirmwareReceiptError("source_repositories must record the build's source authorities")
    repo_names = set()
    for index, raw in enumerate(repositories):
        repo = _object(raw, f"source_repositories[{index}]")
        name = _text(repo.get("name"), f"source_repositories[{index}].name")
        if name in repo_names:
            raise FirmwareReceiptError(f"duplicate source repository name: {name}")
        repo_names.add(name)
        path = Path(_text(repo.get("path"), f"source_repositories[{index}].path"))
        if not path.is_absolute():
            raise FirmwareReceiptError(f"source_repositories[{index}].path must be absolute")
        _text(repo.get("revision"), f"source_repositories[{index}].revision")
        _digest(repo.get("tree_sha256"), f"source_repositories[{index}].tree_sha256")
        if not isinstance(repo.get("dirty"), bool):
            raise FirmwareReceiptError(f"source_repositories[{index}].dirty must be boolean")
        submodules = repo.get("unverified_submodules", [])
        if not isinstance(submodules, list) or any(
            not isinstance(item, str) or not item or "\x00" in item for item in submodules
        ):
            raise FirmwareReceiptError(f"source_repositories[{index}].unverified_submodules must be a string array")

    toolchain = receipt.get("toolchain")
    if not isinstance(toolchain, list) or not toolchain:
        raise FirmwareReceiptError("toolchain must identify the selected compiler/linker tools")
    toolchain_executables = {}
    for index, item in enumerate(toolchain):
        tool = _object(item, f"toolchain[{index}]")
        _text(tool.get("role"), f"toolchain[{index}].role")
        _text(tool.get("version"), f"toolchain[{index}].version")
        executable = _path_record(tool.get("executable"), f"toolchain[{index}].executable", verify_files=verify_files)
        toolchain_executables[(tool["role"], executable["path"])] = executable["sha256"]

    actions = receipt.get("actions")
    action_inputs, outputs, output_versions, compiled_objects = _validate_actions(
        actions, build_id, verify_files
    )

    images = receipt.get("images")
    if not isinstance(images, list) or not images:
        raise FirmwareReceiptError("images must describe each independent firmware image")
    image_ids = set()
    for index, raw in enumerate(images):
        image = _object(raw, f"images[{index}]")
        image_id = _text(image.get("id"), f"images[{index}].id")
        if image_id in image_ids:
            raise FirmwareReceiptError(f"duplicate image id: {image_id}")
        image_ids.add(image_id)
        if image.get("build_id") != build_id:
            raise FirmwareReceiptError(f"image {image_id} belongs to a different build")
        _text(image.get("role"), f"image {image_id}.role")
        link_id = _text(image.get("link_action"), f"image {image_id}.link_action")
        matches = [action for action in actions if action.get("id") == link_id and action.get("kind") == "link"]
        if len(matches) != 1:
            raise FirmwareReceiptError(f"image {image_id} must name exactly one successful link action")
        link = _object(image.get("link"), f"image {image_id}.link")
        elf = _path_record(link.get("elf"), f"image {image_id}.link.elf", verify_files=verify_files)
        map_file = _path_record(link.get("map"), f"image {image_id}.link.map", verify_files=verify_files)
        elf_versions = output_versions.get(elf["path"], [])
        if not any(action_id == link_id for _, action_id in elf_versions):
            raise FirmwareReceiptError(f"image {image_id} ELF is not the output path of its captured link action")
        if not elf_versions or elf_versions[-1][0] != elf["sha256"]:
            raise FirmwareReceiptError(f"image {image_id} ELF is not the final captured version of its output path")
        if outputs.get(map_file["path"]) != (map_file["sha256"], link_id):
            raise FirmwareReceiptError(f"image {image_id} map is not the output of its captured link action")
        _text(link.get("adapter"), f"image {image_id}.link.adapter")
        _text(link.get("linker_format"), f"image {image_id}.link.linker_format")
        if link.get("status") not in ("complete", "incomplete", "unsupported"):
            raise FirmwareReceiptError(f"image {image_id} link status must be explicit")
        if link.get("linker_format") == "unknown" and link.get("status") == "complete":
            raise FirmwareReceiptError(f"image {image_id} cannot claim complete closure with an unknown linker format")

        by_operand = _validate_link_operands(
            link, image_id, link_id, action_inputs, repo_names, verify_files, toolchain_executables
        )
        _validate_implicit_inputs(link, image_id, set(by_operand), verify_files)
        _validate_object_accounting(
            image, image_id, repo_names, compiled_objects, by_operand, verify_files
        )
        if not isinstance(link.get("unresolved"), list):
            raise FirmwareReceiptError(f"image {image_id}.link.unresolved must be an array")
        discarded_sections = link.get("discarded_sections")
        if not isinstance(discarded_sections, list):
            raise FirmwareReceiptError(f"image {image_id}.link.discarded_sections must be an array")
        for row_index, row in enumerate(discarded_sections):
            section = _object(row, f"image {image_id}.link.discarded_sections[{row_index}]")
            _text(section.get("operand"), f"image {image_id} discarded section operand")
            _text(section.get("section"), f"image {image_id} discarded section name")
            _text(section.get("evidence"), f"image {image_id} discarded section evidence")
            if section["operand"] not in by_operand:
                raise FirmwareReceiptError(
                    f"discarded section references an uncaptured link operand: {section['operand']}"
                )
        lto = _text(link.get("lto"), f"image {image_id}.link.lto")
        if lto not in ("disabled", "qualified", "unsupported"):
            raise FirmwareReceiptError(f"image {image_id} has an unknown LTO status: {lto}")
        if lto == "qualified":
            _text(link.get("lto_evidence"), f"image {image_id} LTO qualification evidence")
    if any(owner not in image_ids for owner, _ in compiled_objects):
        raise FirmwareReceiptError("compiler/assembler action names an unknown image")
    status = receipt.get("status")
    if status not in ("complete", "incomplete"):
        raise FirmwareReceiptError("receipt status must be complete or incomplete")
    if status == "complete":
        for image in images:
            link = image["link"]
            if link["status"] != "complete" or link["lto"] == "unsupported" or link["unresolved"]:
                raise FirmwareReceiptError(f"complete receipt contains unresolved link evidence: {image['id']}")
            if link["linker_format"] == "unknown" or any(
                operand["disposition"] == "unsupported" for operand in link["operands"]
            ):
                raise FirmwareReceiptError(f"complete receipt contains unsupported link evidence: {image['id']}")
            if any(row["disposition"] == "unsupported" for row in image["object_accounting"]):
                raise FirmwareReceiptError(f"complete receipt contains unsupported object accounting: {image['id']}")
            lto_enabled = str(configuration["variables"].get("LTO", "")).lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            lto_enabled = lto_enabled or any(
                argument == "-flto" or argument.startswith("-flto=")
                for action in actions
                if action["kind"] in ("compile", "assemble", "link")
                for argument in action["argv"]
            )
            if lto_enabled and link["lto"] != "qualified":
                raise FirmwareReceiptError(
                    f"complete image {image['id']} enables LTO without a qualified adapter"
                )
            if not lto_enabled and link["lto"] == "qualified":
                raise FirmwareReceiptError(
                    f"complete image {image['id']} claims LTO without captured LTO flags"
                )
    return receipt


def load_firmware_receipt(path: Path) -> dict[str, Any]:
    """Load and verify a receipt and every path-bound artifact it names."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FirmwareReceiptError(f"cannot load firmware receipt: {path}") from exc
    return validate_firmware_receipt(value)
