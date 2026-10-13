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
