"""Account for the headers a captured compile read, from its compiler dependency file.

A header is attributed once, by the first rule that applies:

1. it lies in a directory the captured compiler searches on its own (its installation or
   sysroot): a toolchain input bound to that compiler;
2. it lies in the build directory: a generated header, attributed through the inputs of the
   captured action that wrote it;
3. it lies under a declared source root: attributed to that root and path (the deepest root
   wins, never a basename);
4. otherwise it is unattributed, with the reason.

Only headers consumed by compiles whose objects contribute to the image are listed, so a header
that exists in a checkout but that no contributing compile read is never mentioned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .argv_tokens import flatten
from .toolchain_runtime import in_compiler_headers, layout, machine_flags

# Compiler executable path, its SHA-256, toolchain role and the machine flags of one compile.
_CompilerKey = tuple[str, str, str, tuple[str, ...]]


def consumed_headers(action: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Dependency-file prerequisites of a compile action other than its own source, by path."""
    sources = {item["path"] for item in action["inputs"] if item["role"] == "source"}
    headers: dict[str, dict[str, Any]] = {}
    for evidence in action.get("dependency_evidence", []):
        for dependency in evidence["dependencies"]:
            if dependency["path"] not in sources:
                headers.setdefault(dependency["path"], dependency)
    return headers


def _toolchain_binding(path: str, keys: list[_CompilerKey]) -> dict[str, Any] | None:
    for compiler, sha256, role, flags in keys:
        directory = in_compiler_headers(path, compiler, flags, "c++" if role == "cxx" else "c")
        if directory is not None:
            binding: dict[str, Any] = {
                "role": role,
                "executable": {"path": compiler, "sha256": sha256},
                "include_dir": str(directory),
            }
            release = layout(compiler, flags)["release"]
            if release:
                binding["release"] = release
            return binding
    return None


def header_accounting(
    contributed: list[tuple[str, dict[str, Any]]],
    *,
    build_root: Path,
    bindings_for: Callable[[str], list[dict[str, str]]],
    producers: dict[str, tuple[str, list[dict[str, str]]]],
    roles: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return the per-header entries and the unresolved rows for headers that are unattributed.

    ``contributed`` pairs each contributing object with its compile action. ``producers`` maps a
    generated path to the captured action that wrote it and that action's source bindings;
    ``roles`` maps a compiler executable path to its toolchain role.
    """
    consumers: dict[str, set[str]] = {}
    records: dict[str, dict[str, Any]] = {}
    compilers: dict[str, list[_CompilerKey]] = {}
    aliases: dict[str, set[str]] = {}
    seen_actions: set[str] = set()
    for _, action in contributed:
        if action["kind"] != "compile" or action["id"] in seen_actions:
            continue
        seen_actions.add(action["id"])
        executable = action["executable"]
        role = roles.get(executable["path"])
        key = (
            (
                executable["path"],
                executable["sha256"],
                role,
                machine_flags(flatten(action["argv"], Path(action["cwd"]))),
            )
            if role is not None
            else None
        )
        for path, dependency in consumed_headers(action).items():
            consumers.setdefault(path, set()).add(action["id"])
            records.setdefault(path, dependency)
            if key is not None and key not in compilers.setdefault(path, []):
                compilers[path].append(key)
            if dependency.get("written_path"):
                aliases.setdefault(path, set()).add(dependency["written_path"])

    entries: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for path in sorted(records):
        entry: dict[str, Any] = {
            "path": path,
            "sha256": records[path]["sha256"],
            "consumed_by": sorted(consumers[path]),
            "evidence": "compiler dependency file of a compile that produced a contributing object",
        }
        if path in aliases:
            entry["aliases"] = sorted(aliases[path])
        toolchain = _toolchain_binding(path, compilers.get(path, []))
        reason = None
        if toolchain is not None:
            entry["disposition"] = "toolchain"
            entry["toolchain"] = toolchain
        elif Path(path).is_relative_to(build_root):
            producer = producers.get(path)
            if producer is None:
                reason = "generated header has no captured producing action"
            elif not producer[1]:
                reason = "the producing action has no input under a declared source root"
            else:
                entry["disposition"] = "generated"
                entry["producer"] = producer[0]
                entry["source_bindings"] = producer[1]
        else:
            bindings = bindings_for(path)
            if bindings:
                entry["disposition"] = "source"
                entry["source_bindings"] = bindings
            else:
                reason = (
                    "header lies outside the declared source roots, the build directory "
                    "and the compiler's own header directories"
                )
        if reason is not None:
            if path in aliases:
                reason += f" (reached through {', '.join(sorted(aliases[path]))})"
            entry["disposition"] = "unowned"
            entry["reason"] = reason
            unresolved.append({"path": path, "reason": f"header is not attributed: {reason}"})
        entries.append(entry)
    return entries, unresolved


def _embedded_row(
    blob: dict[str, str],
    origin: str,
    action: str,
    obj: dict[str, str],
    *,
    contributes: bool,
    bindings_for: Callable[[str], list[dict[str, str]]],
    producers: dict[str, tuple[str, list[dict[str, str]]]],
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """One embedded blob: its hash bound to the action that read it, and the boundary owning it."""
    row: dict[str, Any] = {
        "blob": {"path": blob["path"], "sha256": blob["sha256"]},
        "origin": origin,
        "action": action,
        "object": {"path": obj["path"], "sha256": obj["sha256"]},
        "evidence": "bytes read by a captured action that produced or loaded the object",
    }
    if not contributes:
        row["disposition"] = "no-contribution"
        row["reason"] = "GNU map records no allocated sections from the object holding the blob"
        return row, None
    bindings = bindings_for(blob["path"])
    if not bindings and blob["path"] in producers:
        bindings = producers[blob["path"]][1]
    if bindings:
        row["disposition"] = "contributed"
        row["source_bindings"] = bindings
        return row, None
    row["disposition"] = "unsupported"
    row["reason"] = (
        "embedded content lies outside the declared source roots and has no captured "
        "producer with inputs under them"
    )
    return row, {"path": blob["path"], "reason": f"embedded content is not attributed: {row['reason']}"}


def linked_embedded_inputs(
    actions: list[dict[str, Any]],
    link_action: dict[str, Any],
    *,
    link_inputs: dict[str, dict[str, Any]],
    allocated: set[str],
    bindings_for: Callable[[str], list[dict[str, str]]],
    producers: dict[str, tuple[str, list[dict[str, str]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Raw binary content that reaches the image as a link operand.

    Two routes are observable: a captured transform or relocatable link that read a raw binary
    (``objcopy -I binary``, ``ld -r -b binary``) and wrote an object the final link takes, and the
    final link reading the binary itself (``-b binary``, ``--format=binary``).
    """
    archived = {
        item["path"]
        for action in actions
        if action["kind"] == "archive"
        for item in action["inputs"]
        if item["role"] == "archive-member"
    }
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []

    def add(row: dict[str, Any], failure: dict[str, str] | None) -> None:
        rows.append(row)
        if failure is not None:
            unresolved.append(failure)

    for action in actions:
        blobs = [item for item in action["inputs"] if item["role"] == "embedded-input"]
        if not blobs:
            continue
        if action is link_action:
            for blob in blobs:
                add(
                    *_embedded_row(
                        blob,
                        "ld-format-binary",
                        action["id"],
                        blob,
                        contributes=blob["path"] in allocated,
                        bindings_for=bindings_for,
                        producers=producers,
                    )
                )
            continue
        origin = "objcopy-input-binary" if action["kind"] == "transform" else "ld-format-binary"
        for obj in (item for item in action["outputs"] if item["role"] == "output"):
            if obj["path"] in archived:
                for blob in blobs:
                    row, _ = _embedded_row(
                        blob, origin, action["id"], obj, contributes=False, bindings_for=bindings_for, producers=producers
                    )
                    row["disposition"] = "unsupported"
                    row["reason"] = "the object holding the blob enters the image through an archive"
                    add(row, {"path": blob["path"], "reason": f"embedded content is not attributed: {row['reason']}"})
            elif obj["path"] in link_inputs:
                for blob in blobs:
                    add(
                        *_embedded_row(
                            blob,
                            origin,
                            action["id"],
                            obj,
                            contributes=obj["path"] in allocated,
                            bindings_for=bindings_for,
                            producers=producers,
                        )
                    )
    return rows, unresolved


def assembler_embedded_inputs(
    contributed: list[tuple[str, dict[str, Any]]],
    *,
    bindings_for: Callable[[str], list[dict[str, str]]],
    producers: dict[str, tuple[str, list[dict[str, str]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Files GNU as read for contributing objects (``.incbin`` data, ``.include`` files).

    A contributing compile or assemble whose assembler inputs were not captured is unresolved.
    """
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path, action in contributed:
        evidence = action.get("assembler_dependencies")
        if evidence is None or evidence["status"] != "collected":
            reason = evidence["reason"] if evidence else "the action recorded no assembler dependency capture"
            unresolved.append({"path": path, "reason": f"files read by the assembler for this object are not captured: {reason}"})
            continue
        obj = next(item for item in action["outputs"] if item["path"] == path)
        for blob in evidence["dependencies"]:
            if (blob["path"], action["id"]) in seen:
                continue
            seen.add((blob["path"], action["id"]))
            row, failure = _embedded_row(
                blob,
                "assembler-dependency",
                action["id"],
                obj,
                contributes=True,
                bindings_for=bindings_for,
                producers=producers,
            )
            rows.append(row)
            if failure is not None:
                unresolved.append(failure)
    return rows, unresolved


def bindings_for_path(path: str, source_roots: dict[str, Path]) -> list[dict[str, str]]:
    """The declared source root and canonical relative path that contain a file, or none.

    The deepest containing root wins; two roots at that depth are ambiguous and raise ValueError.
    """
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
