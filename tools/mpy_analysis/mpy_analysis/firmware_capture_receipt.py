"""Bind captured Make-tool actions to an explicitly incomplete firmware receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .argv_tokens import enables_lto, flatten
from .elf_evidence import dynamic_needed
from .firmware_receipt import receipt_id, validate_firmware_receipt
from .gnu_map import parse_gnu_map
from .toolchain_runtime import classify, machine_flags


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _record(path: Path, role: str) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"captured evidence path is not a file: {resolved}")
    return {"path": str(resolved), "sha256": _digest(resolved), "role": role}


def _gitlink_revision(root: Path) -> tuple[str, bytes] | None:
    for parent in root.parents:
        top_result = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if top_result.returncode:
            continue
        top = Path(top_result.stdout.strip()).resolve()
        try:
            relative = root.relative_to(top).as_posix()
        except ValueError:
            continue
        index = subprocess.run(
            ["git", "-C", str(top), "ls-files", "--stage", "-z", "--", relative],
            check=True,
            capture_output=True,
        ).stdout
        entries = [entry for entry in index.split(b"\0") if entry]
        matches = [entry for entry in entries if entry.startswith(b"160000 ")]
        if len(matches) == 1:
            fields = matches[0].split(b" ", 2)
            return fields[1].decode("ascii"), matches[0]
    return None


def _unverified_gitlink_record(name: str, root: Path, gitlink: tuple[str, bytes]) -> dict[str, Any]:
    revision, index_entry = gitlink
    return {
        "name": name,
        "path": str(root),
        "revision": revision,
        "tree_sha256": hashlib.sha256(index_entry).hexdigest(),
        "dirty": True,
        "unverified_submodules": ["."],
        "provenance_status": "unverified-gitlink-checkout",
    }


def _repositories(source_roots: dict[str, Path]) -> list[dict[str, Any]]:
    records = []
    declared_roots = {root.resolve(strict=True) for root in source_roots.values()}
    for name, root in sorted(source_roots.items()):
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"source root is not a directory: {root}")
        top_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        gitlink = _gitlink_revision(root)
        if gitlink is not None and (
            top_result.returncode or Path(top_result.stdout.strip()).resolve() != root
        ):
            records.append(_unverified_gitlink_record(name, root, gitlink))
            continue
        revision_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        if revision_result.returncode:
            if gitlink is None:
                revision_result.check_returncode()
            records.append(_unverified_gitlink_record(name, root, gitlink))
            continue
        revision = revision_result.stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--ignore-submodules=all",
            ],
            check=True,
            capture_output=True,
        ).stdout
        index = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--stage"],
            check=True,
            capture_output=True,
        ).stdout
        gitlinks = [
            path
            for entry in index.split(b"\0")
            if entry.startswith(b"160000 ")
            for path in [entry.split(b"\t", 1)[1].decode("utf-8", errors="strict")]
            if (root / path).resolve(strict=False) not in declared_roots
        ]
        records.append(
            {
                "name": name,
                "path": str(root),
                "revision": revision,
                "tree_sha256": hashlib.sha256(index).hexdigest(),
                "dirty": bool(status),
                "unverified_submodules": gitlinks,
            }
        )
    if not records:
        raise ValueError("at least one source root is required")
    return records


def _bindings(path: str, source_roots: dict[str, Path]) -> list[dict[str, str]]:
    source = Path(path).resolve(strict=False)
    matches = []
    for name, raw_root in source_roots.items():
        root = raw_root.resolve(strict=True)
        if source.is_relative_to(root):
            relative = source.relative_to(root).as_posix()
            if relative and relative != ".":
                matches.append((len(root.parts), name, relative))
    if not matches:
        return []
    depth = max(item[0] for item in matches)
    winners = [(name, relative) for match_depth, name, relative in matches if match_depth == depth]
    if len(winners) != 1:
        raise ValueError(f"source path has ambiguous declared source roots: {path}")
    name, relative = winners[0]
    return [{"root": name, "path": relative}]


def _compiler(toolchain: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    """The one captured compiler, or None when the capture did not observe exactly one."""
    compilers = [record for (role, _), record in sorted(toolchain.items()) if role == "cc"]
    return compilers[0] if len(compilers) == 1 else None


def _flag_sets(actions: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    """Distinct multilib-selecting flag sets of the captured compile, assemble and link commands.

    A link command need not repeat the machine flags the sources were compiled with, while the
    build still names runtime files from the multilib directory those flags select.
    """
    sets: list[tuple[str, ...]] = []
    for action in actions:
        if action["kind"] in ("link", "compile", "assemble"):
            flags = machine_flags(flatten(action["argv"], Path(action["cwd"])))
            if flags not in sets:
                sets.append(flags)
    return sets


def _runtime_identity(
    path: str, compiler: dict[str, Any] | None, flag_sets: list[tuple[str, ...]]
) -> tuple[dict[str, Any] | None, str]:
    """Identify a statically linked compiler runtime or C library file through the compiler."""
    if compiler is None:
        return None, "no single captured compiler to identify the runtime"
    reason = "no captured command flags to query the compiler with"
    for flags in flag_sets:
        kind, library, reason = classify(path, compiler["path"], flags)
        if kind == "compiler-runtime":
            return {
                "toolchain": {
                    "role": "cc",
                    "executable": {"path": compiler["path"], "sha256": compiler["sha256"]},
                    "release": library["release"],
                }
            }, ""
        if kind == "c-library":
            return {"c_library": library}, ""
    return None, reason


def _implicit_input(
    path: str,
    *,
    allocated: set[str],
    archive_members: dict[str, set[str]],
    compiler: dict[str, Any] | None,
    flag_sets: list[tuple[str, ...]],
) -> tuple[dict[str, Any] | None, str]:
    """Account for a file the linker loaded that was not a captured link operand.

    Static files that contribute must be identified as compiler runtime or C library; shared
    libraries are resolved at load time and recorded through the ELF's DT_NEEDED list instead.
    """
    file = Path(path)
    if not file.is_file():
        return None, "GNU map LOAD input is not a regular file"
    entry: dict[str, Any] = {
        "path": path,
        "sha256": _digest(file),
        "origin": "gnu-map-load",
        "evidence": "GNU ld map LOAD record without a captured link command operand",
    }
    suffix = file.suffix.lower()
    if suffix in (".o", ".obj"):
        entry["kind"] = "object"
        contributes = path in allocated
    elif suffix in (".a", ".lib"):
        entry["kind"] = "archive"
        contributes = path in archive_members
        if contributes:
            entry["contributing_members"] = sorted(archive_members[path])
    elif file.read_bytes()[:4] == b"\x7fELF":
        entry["kind"] = "shared-library"
        entry["disposition"] = "dynamic"
        entry["reason"] = "resolved at load time; the final ELF's DT_NEEDED list records the requirement"
        return entry, ""
    else:
        entry["kind"] = "script"
        contributes = False
    if not contributes:
        entry["disposition"] = "no-contribution"
        entry["reason"] = (
            "linker script"
            if entry["kind"] == "script"
            else "GNU ld records no allocated sections from this input"
        )
        return entry, ""
    identity, failure = _runtime_identity(path, compiler, flag_sets)
    if identity is None:
        return None, f"GNU map LOAD input contributes but is not identified: {failure}"
    entry["disposition"] = "contributed"
    entry["linkage"] = "static"
    entry.update(identity)
    return entry, ""


def _archive_members(
    archive_path: str,
    archive_action: dict[str, Any],
    parsed: dict[str, Any] | None,
    source_for_object: dict[str, list[dict[str, str]]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]] | None:
    """Bind GNU ld archive-member identities to captured archive inputs."""
    if parsed is None:
        return None
    archive = Path(archive_path).resolve(strict=True)
    archiver = archive_action["executable"]["path"]
    try:
        listed = subprocess.run(
            [archiver, "t", str(archive)], check=True, capture_output=True, timeout=10
        ).stdout.decode("utf-8", errors="strict").splitlines()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    if not listed or len(set(listed)) != len(listed):
        return None

    input_objects = [
        item for item in archive_action["inputs"] if item["role"] == "archive-member"
    ]
    inputs_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in input_objects:
        inputs_by_name.setdefault(Path(item["path"]).name, []).append(item)

    extracted = {
        (str(Path(item["archive"]["resolved"]).resolve(strict=False)), item["member"])
        for item in parsed["extracted_archive_members"]
    }
    allocated = set()
    for group in parsed["allocated_sections"]:
        identity = group["identity"]
        if identity["kind"] == "archive_member":
            allocated.add(
                (
                    str(Path(identity["path"]).resolve(strict=False)),
                    identity["member"],
                )
            )
    discarded = set()
    for row in parsed["discarded_sections"]:
        identity = row["identity"]
        if identity["kind"] == "archive_member":
            discarded.add(
                (
                    str(Path(identity["path"]).resolve(strict=False)),
                    identity["member"],
                )
            )

    members = []
    object_accounting = {}
    for name in listed:
        key = (str(archive), name)
        is_extracted = key in extracted
        source = inputs_by_name.get(name, [])
        source_path = source[0]["path"] if len(source) == 1 else None
        source_bindings = source_for_object.get(source_path, []) if source_path else []
        try:
            content = subprocess.run(
                [archiver, "p", str(archive), name],
                check=True,
                capture_output=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        digest = hashlib.sha256(content).hexdigest()
        if source_path and len(source) == 1 and source[0]["sha256"] != digest:
            return None
        if not is_extracted:
            disposition = "not-extracted"
        elif key in allocated and source_bindings:
            disposition = "contributed"
        elif key in discarded:
            disposition = "discarded"
        else:
            return None
        member: dict[str, Any] = {
            "name": name,
            "sha256": digest,
            "disposition": disposition,
            "evidence": "captured archive bytes and GNU ld member-selection/section map",
        }
        if disposition == "contributed":
            member["source_bindings"] = source_bindings
        members.append(member)
        if source_path and source_path in source_for_object:
            object_accounting[source_path] = {
                "disposition": (
                    "not-linked" if disposition == "not-extracted" else disposition
                ),
                "source_bindings": source_bindings,
                "reason": (
                    "archive member was not extracted"
                    if disposition == "not-extracted"
                    else None
                ),
            }
    return members, object_accounting


def _load_events(journal: Path, build_id: str, image_id: str) -> list[dict[str, Any]]:
    events = []
    for number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid action journal JSON on line {number}") from error
        if event.get("schema") != "mpy-firmware-action-event/v1":
            raise ValueError(f"unsupported action journal event on line {number}")
        if event.get("build_id") != build_id:
            raise ValueError(f"action journal line {number} has a different build ID")
        if event.get("image_id") != image_id:
            continue
        events.append(event)
    if not events:
        raise ValueError(f"action journal contains no events for image {image_id}")
    if any(event.get("exit_code") != 0 for event in events):
        raise ValueError("failed tool actions cannot be emitted as a successful firmware receipt")
    return sorted(events, key=lambda event: event["started_ns"])


def receipt_from_journal(
    journal: Path,
    *,
    build_id: str,
    image_id: str,
    image_role: str,
    port: str,
    target: str,
    variant: str,
    variables: dict[str, str],
    source_roots: dict[str, Path],
    build_dir: Path,
    elf: Path,
    map_file: Path,
) -> dict[str, Any]:
    """Create a receipt from a wrapped make run; complete only when no link evidence is unresolved."""
    events = _load_events(Path(journal), build_id, image_id)
    build_root = Path(build_dir).resolve(strict=True)
    elf_record = _record(Path(elf), "elf")
    map_record = _record(Path(map_file), "map")
    root_records = _repositories(source_roots)

    def in_build(path: str) -> bool:
        try:
            Path(path).resolve(strict=False).relative_to(build_root)
            return True
        except ValueError:
            return False

    selected_events = []
    for event in events:
        outputs = event["outputs"]
        output_paths = {item["path"] for item in outputs}
        relevant = event["kind"] in ("compile", "assemble", "archive") and any(
            item["path"].endswith((".o", ".obj", ".a", ".lib")) and in_build(item["path"])
            for item in outputs
        )
        relevant = relevant or elf_record["path"] in output_paths or map_record["path"] in output_paths
        relevant = relevant or any(
            in_build(item["path"])
            and Path(item["path"]).suffix.lower() in (".bin", ".hex", ".dfu", ".pack")
            for item in outputs
        )
        relevant = relevant or event["kind"] in ("transform", "generate") and any(
            in_build(item["path"]) for item in outputs
        )
        if relevant:
            selected_events.append(event)

    actions = []
    toolchain = {}
    image_artifacts = {elf_record["path"]: elf_record, map_record["path"]: map_record}
    for event in selected_events:
        event_id = f"action-{len(actions) + 1:05d}"
        kind = event["kind"]
        inputs = [dict(item) for item in event["inputs"]]
        outputs = []
        for raw in event["outputs"]:
            path = raw["path"]
            if kind in ("compile", "assemble"):
                role = "object"
            elif path == elf_record["path"]:
                role = "elf"
            elif path == map_record["path"]:
                role = "map"
            elif Path(path).suffix.lower() in (".bin", ".hex", ".dfu", ".pack"):
                role = "deployment"
            else:
                role = "output"
            record = {key: value for key, value in raw.items() if key != "role"}
            record["role"] = role
            outputs.append(record)
            if role in ("elf", "map", "deployment"):
                image_artifacts[path] = record
        action = {
            "id": event_id,
            "build_id": build_id,
            "kind": kind,
            **({"image": image_id} if kind in ("compile", "assemble") else {}),
            "argv": event["argv"],
            "cwd": event["cwd"],
            "exit_code": event["exit_code"],
            "executable": event["executable"],
            "inputs": inputs,
            "outputs": outputs,
            "capture_started_ns": event["started_ns"],
            "capture_finished_ns": event["finished_ns"],
            "dependency_evidence": event.get("dependency_evidence", []),
            **({"dependency_capture": event["dependency_capture"]} if "dependency_capture" in event else {}),
        }
        actions.append(action)
        tool = event["executable"]
        toolchain[(event["role"], tool["path"])] = tool

    matching_links = [
        action
        for action in actions
        if action["kind"] == "link"
        and any(item["path"] == elf_record["path"] for item in action["outputs"])
    ]
    if len(matching_links) != 1:
        raise ValueError("captured actions must contain exactly one link producing the selected ELF")
    link_action = matching_links[0]
    try:
        parsed = parse_gnu_map(Path(map_file).read_text(encoding="utf-8", errors="strict"), base_dir=Path(map_file).parent)
        linker_format = parsed["format"]
    except (OSError, UnicodeError, ValueError) as error:
        parsed = None
        linker_format = "unknown"
        map_error = str(error)
    else:
        map_error = None

    generated_source_inputs = {}
    for action in actions:
        if action["kind"] != "generate":
            continue
        producer_bindings = []
        for item in action["inputs"]:
            for binding in _bindings(item["path"], source_roots):
                marked = {**binding, "via": "generator"}
                if marked not in producer_bindings:
                    producer_bindings.append(marked)
        for output in action["outputs"]:
            generated_source_inputs[output["path"]] = producer_bindings

    source_for_object = {}
    object_records = {}
    object_action = {}
    for action in actions:
        if action["kind"] not in ("compile", "assemble"):
            continue
        source_inputs = [item for item in action["inputs"] if item["role"] == "source"]
        for output in action["outputs"]:
            if output["role"] == "object":
                object_records[output["path"]] = output
                object_action[output["path"]] = action
                bindings = []
                if len(source_inputs) == 1:
                    bindings = _bindings(source_inputs[0]["path"], source_roots)
                    if not bindings:
                        bindings = generated_source_inputs.get(source_inputs[0]["path"], [])
                source_for_object[output["path"]] = bindings

    allocated = set()
    toolchain_archive_members: dict[str, set[str]] = {}
    discarded = []
    linker_generated = []
    if parsed is not None:
        for group in parsed["allocated_sections"]:
            identity = group["identity"]
            if identity.get("raw") == "linker stubs":
                linker_generated.extend(
                    {
                        "section": section["section"],
                        "address": section["address"],
                        "size": section["size"],
                        "evidence": f"GNU map allocates linker-generated stub section at line {section['line']}",
                    }
                    for section in group["sections"]
                )
                continue
            path = Path(identity["path"])
            resolved = str((path if path.is_absolute() else Path(map_file).parent / path).resolve())
            if identity["kind"] == "object":
                allocated.add(resolved)
            elif identity["kind"] == "archive_member":
                toolchain_archive_members.setdefault(resolved, set()).add(identity["member"])
        for row in parsed["discarded_sections"]:
            identity = row["identity"]
            operand_path = identity["path"]
            path = Path(operand_path)
            absolute = str((path if path.is_absolute() else Path(map_file).parent / path).resolve())
            if absolute in object_records:
                discarded.append(
                    {
                        "operand": absolute,
                        "section": row["section"],
                        "evidence": f"GNU map discarded input section at line {row['line']}",
                    }
                )

    link_inputs = {item["path"]: item for item in link_action["inputs"] if item["role"] == "link-operand"}
    map_only_loads = set()
    if parsed is not None:
        for load in parsed["loads"]:
            if load.get("kind") != "path":
                continue
            path = str(Path(load["resolved"]).resolve(strict=False))
            if path not in link_inputs:
                map_only_loads.add(path)
    operands = []
    unresolved = []
    implicit_inputs = []
    compiler = _compiler(toolchain)
    flag_sets = _flag_sets(actions)
    for path in sorted(map_only_loads):
        entry, failure = _implicit_input(
            path,
            allocated=allocated,
            archive_members=toolchain_archive_members,
            compiler=compiler,
            flag_sets=flag_sets,
        )
        if entry is not None:
            implicit_inputs.append(entry)
        else:
            unresolved.append({"path": path, "reason": failure})
    consumed_paths = [
        Path(item["path"])
        for action in actions
        for item in action["inputs"]
    ]
    unresolved.extend(
        {
            "path": str(Path(repository["path"]) / submodule),
            "reason": "submodule working tree was not assessed as part of this source root",
        }
        for repository in root_records
        for submodule in repository["unverified_submodules"]
        if any((Path(repository["path"]) / submodule) in path.parents for path in consumed_paths)
    )
    archive_actions = {}
    for action in actions:
        if action["kind"] == "archive":
            for output in action["outputs"]:
                archive_actions[output["path"]] = action
    archive_object_accounting = {}
    for path, input_record in sorted(link_inputs.items()):
        suffix = Path(path).suffix.lower()
        bindings = source_for_object.get(path, [])
        if suffix in (".o", ".obj"):
            if path in allocated and bindings:
                disposition, kind = "contributed", "object"
                reason = None
            elif path in allocated:
                disposition, kind = "unsupported", "object"
                reason = "contributing object source is outside declared roots"
            elif any(row["operand"] == path for row in discarded):
                disposition, kind = "discarded", "object"
                reason = None
            else:
                disposition, kind = "no-contribution", "object"
                reason = "GNU map records no allocated or discarded input sections"
            operand = {
                "kind": kind,
                "input": input_record,
                "disposition": disposition,
                "evidence": "captured link command and GNU ld map",
            }
            if disposition == "contributed":
                operand["source_bindings"] = bindings
            if reason:
                operand["reason"] = reason
                unresolved.append({"path": path, "reason": reason})
        elif suffix in (".a", ".lib") and path in archive_actions:
            accounting = _archive_members(path, archive_actions[path], parsed, source_for_object)
            if accounting is None:
                reason = "archive member inventory or source attribution could not be qualified"
                operand = {
                    "kind": "other",
                    "input": input_record,
                    "disposition": "unsupported",
                    "reason": reason,
                    "evidence": "captured archive action and GNU ld map",
                }
                unresolved.append({"path": path, "reason": reason})
            else:
                members, member_accounting = accounting
                archive_object_accounting.update(member_accounting)
                contributed_bindings = [
                    binding
                    for member in members
                    if member["disposition"] == "contributed"
                    for binding in member.get("source_bindings", [])
                ]
                disposition = "contributed" if contributed_bindings else "no-contribution"
                operand = {
                    "kind": "archive",
                    "input": input_record,
                    "disposition": disposition,
                    "members": members,
                    "members_complete": True,
                    "evidence": "captured archive action and GNU ld member-selection/section map",
                }
                if disposition == "contributed":
                    operand["source_bindings"] = contributed_bindings
                else:
                    operand["reason"] = "GNU ld records no allocated sections from this archive"
        elif suffix in (".ld", ".lds", ".ldscript"):
            operand = {
                "kind": "script",
                "input": input_record,
                "disposition": "contributed",
                "evidence": "captured linker-script argument",
            }
        elif (
            suffix in (".a", ".lib")
            and path in toolchain_archive_members
            and _runtime_identity(path, compiler, flag_sets)[0] is not None
        ):
            operand = {
                "kind": "library",
                "input": input_record,
                "disposition": "contributed",
                **_runtime_identity(path, compiler, flag_sets)[0],
                "contributing_members": sorted(toolchain_archive_members[path]),
                "evidence": "GNU ld map allocates sections from this archive; the captured compiler resolves it as its own runtime or C library",
            }
        else:
            reason = "system library, response file or prebuilt input lacks qualified source accounting"
            operand = {
                "kind": "other",
                "input": input_record,
                "disposition": "unsupported",
                "reason": reason,
                "evidence": "captured command input or GNU map LOAD record",
            }
            unresolved.append({"path": path, "reason": reason})
        operands.append(operand)

    archive_object_inputs = set()
    for action in actions:
        if action["kind"] == "archive":
            archive_object_inputs.update(
                item["path"] for item in action["inputs"] if item["role"] == "archive-member"
            )
    object_accounting = []
    for path, record in sorted(object_records.items()):
        bindings = source_for_object.get(path, [])
        if path in archive_object_accounting:
            accounting = archive_object_accounting[path]
            disposition = accounting["disposition"]
            reason = accounting["reason"]
        elif path in allocated and path in link_inputs and bindings:
            disposition, reason = "contributed", None
        elif path in allocated and path in link_inputs:
            disposition, reason = "unsupported", "contributing object source is outside declared roots"
        elif any(row["operand"] == path for row in discarded) and bindings:
            disposition, reason = "discarded", None
        elif path in archive_object_inputs:
            disposition, reason = "unsupported", "archive-member extraction and contribution are not qualified"
        elif path in link_inputs:
            disposition, reason = "unsupported", "object contribution is not established by parsed map sections"
        elif bindings:
            disposition, reason = "not-linked", "compiler output is absent from captured link operands and archives"
        else:
            disposition, reason = "unsupported", "object source is outside declared roots"
        row = {
            "object": record,
            "disposition": disposition,
            "source_bindings": bindings,
            "evidence": "captured compiler/assembler output and final link map",
        }
        if reason:
            row["reason"] = reason
            unresolved.append({"path": path, "reason": reason})
        object_accounting.append(row)
        action = object_action[path]
        capture = action.get("dependency_capture") if action["kind"] == "compile" else None
        if disposition == "contributed" and action["kind"] == "compile" and (
            capture is None or capture["status"] != "collected"
        ):
            unresolved.append(
                {
                    "path": path,
                    "reason": "headers consumed by this compile are not captured: "
                    + (capture["reason"] if capture else "the action recorded no dependency capture"),
                }
            )

    if map_error:
        unresolved.append({"path": str(Path(map_file).resolve()), "reason": f"unsupported map evidence: {map_error}"})
    if parsed is not None:
        for ambiguity in parsed["ambiguities"]:
            unresolved.append({"path": ambiguity.get("archive_path", str(map_file)), "reason": f"ambiguous GNU map identity: {ambiguity}"})
    changed_input_paths = sorted(
        {
            path
            for event in selected_events
            for path in event.get("changed_inputs_during_action", [])
        }
    )
    unresolved.extend(
        {"path": path, "reason": "input bytes changed while the wrapped tool action ran"}
        for path in changed_input_paths
    )
    if not parsed:
        linker_format = "unknown"
    lto_variable = str(variables.get("LTO", "")).lower() in ("1", "true", "yes", "on")
    lto = "unsupported" if lto_variable or any(
        enables_lto(action["argv"], Path(action["cwd"])) for action in actions
    ) else "disabled"
    unresolved_rows = [dict(item) for item in {(row["path"], row["reason"]): row for row in unresolved}.values()]
    repositories_verified = not any(
        record["dirty"] or record.get("provenance_status") for record in root_records
    )
    link_complete = (
        not unresolved_rows
        and linker_format != "unknown"
        and lto == "disabled"
        and repositories_verified
        and all(row["disposition"] != "unsupported" for row in object_accounting)
        and all(operand["disposition"] != "unsupported" for operand in operands)
    )
    image = {
        "configuration": {
            "port": port,
            "target": target,
            "variant": variant,
            "variables": variables,
        },
        "id": image_id,
        "build_id": build_id,
        "role": image_role,
        "link_action": link_action["id"],
        "link": {
            "adapter": "mpy-make-wrapper/v1",
            "linker_format": linker_format,
            "status": "complete" if link_complete else "incomplete",
            "elf": elf_record,
            "map": map_record,
            "operands": operands,
            "discarded_sections": discarded,
            "implicit_inputs": implicit_inputs,
            "dynamic_needed": dynamic_needed(Path(elf_record["path"])),
            "linker_generated_sections": linker_generated,
            "unresolved": unresolved_rows,
            "lto": lto,
        },
        "object_accounting": object_accounting,
        "artifacts": list(image_artifacts.values()),
    }
    receipt = {
        "schema": "mpy-firmware-build/v1",
        "build_id": build_id,
        "configuration": {
            "port": port,
            "target": target,
            "variant": variant,
            "variables": variables,
        },
        "source_repositories": root_records,
        "toolchain": [
            {
                "role": role,
                "version": _tool_version(Path(path)),
                "executable": {**record, "role": role},
            }
            for (role, path), record in sorted(toolchain.items())
        ],
        "actions": actions,
        "images": [image],
        "status": "complete" if link_complete else "incomplete",
    }
    receipt["receipt_id"] = receipt_id(receipt)
    return validate_firmware_receipt(receipt)

def merge_receipts(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine separately captured images from one firmware build without losing config."""
    if len(receipts) < 2:
        raise ValueError("merging requires at least two image receipts")
    checked = [validate_firmware_receipt(receipt) for receipt in receipts]
    build_id = checked[0]["build_id"]
    configuration = checked[0]["configuration"]
    repositories = checked[0]["source_repositories"]
    for receipt in checked:
        if receipt["build_id"] != build_id:
            raise ValueError("image receipts have different build IDs")
        for key in ("port", "target", "variant"):
            if receipt["configuration"][key] != configuration[key]:
                raise ValueError(f"image receipts have different {key} values")
        if receipt["source_repositories"] != repositories:
            raise ValueError("image receipts have different source repository authorities")
        if len(receipt["images"]) != 1:
            raise ValueError("each input receipt must describe exactly one image")

    image_ids = [receipt["images"][0]["id"] for receipt in checked]
    if len(set(image_ids)) != len(image_ids):
        raise ValueError("image receipts contain duplicate image IDs")

    sequenced_actions = sorted(
        (
            (action["capture_started_ns"], receipt_index, action)
            for receipt_index, receipt in enumerate(checked)
            for action in receipt["actions"]
        ),
        key=lambda item: (item[0], item[1], item[2]["id"]),
    )
    action_ids = {}
    actions = []
    images = []
    for ordinal, (_, receipt_index, original) in enumerate(sequenced_actions, 1):
        action = json.loads(json.dumps(original))
        action_ids[(receipt_index, original["id"])] = f"action-{ordinal:05d}"
        action["id"] = f"action-{ordinal:05d}"
        action["image"] = image_ids[receipt_index]
        actions.append(action)
    for receipt_index, receipt in enumerate(checked):
        image = json.loads(json.dumps(receipt["images"][0]))
        image["link_action"] = action_ids[(receipt_index, image["link_action"])]
        image["configuration"] = receipt["configuration"]
        images.append(image)

    toolchain_by_identity = {}
    for receipt in checked:
        for tool in receipt["toolchain"]:
            executable = tool["executable"]
            identity = (tool["role"], executable["path"], executable["sha256"])
            previous = toolchain_by_identity.get(identity)
            if previous is not None and previous != tool:
                raise ValueError(f"conflicting toolchain evidence for {executable['path']}")
            toolchain_by_identity[identity] = tool

    common_variables = {
        name: value
        for name, value in configuration["variables"].items()
        if all(receipt["configuration"]["variables"].get(name) == value for receipt in checked)
    }
    merged = {
        "schema": "mpy-firmware-build/v1",
        "build_id": build_id,
        "configuration": {
            "port": configuration["port"],
            "target": configuration["target"],
            "variant": configuration["variant"],
            "variables": common_variables,
        },
        "source_repositories": repositories,
        "toolchain": [toolchain_by_identity[key] for key in sorted(toolchain_by_identity)],
        "actions": actions,
        "images": images,
        "status": "complete" if all(item["status"] == "complete" for item in checked) else "incomplete",
    }
    merged["receipt_id"] = receipt_id(merged)
    return validate_firmware_receipt(merged)


def _tool_version(path: Path) -> str:
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "version-unavailable"
    first = (result.stdout or result.stderr).splitlines()
    return first[0].strip() if first else "version-unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge image receipts from one firmware build")
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        receipts = [json.loads(path.read_text(encoding="utf-8")) for path in arguments.input]
        merged = merge_receipts(receipts)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"firmware-receipt-merge: {error}", file=sys.stderr)
        return 2
    print(f"Merged firmware receipt: {arguments.output} ({merged['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
