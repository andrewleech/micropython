#!/usr/bin/env python3
"""Link evidence for an SBOM: the sources, headers and runtime files a linked image holds.

The inputs are what a finished build leaves behind, nothing is observed while it runs:

* a compilation database (compiledb over make, or CMake's CMAKE_EXPORT_COMPILE_COMMANDS), which
  names the source each object was compiled from through its `-o` operand;
* the GNU ld map of each image, whose allocated sections say which objects and archive members
  contribute bytes to the image;
* the compilers' dependency files (`-MF`, or the `.P` copy that py/mkrules.mk keeps), which say
  which headers the contributing compiles read;
* the ELF, for its DT_NEEDED list.

An archive member is tied to its source by the bytes of the member matching the bytes of one
compiled object. A contributing object that cannot be tied to a source, or a header outside every
declared root and the compiler's own directories, is reported as unresolved. Sources and headers
generated into the build directory are not components; they derive from inputs that are.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from .compdb import (
    DepfileError,
    arguments_of,
    assembly_source,
    depfile_prerequisites,
    entry_file,
    output_of,
    source_roots,
)
from .elf_evidence import dynamic_needed, loaded_sections
from .gnu_map import parse_gnu_map
from .toolchain_runtime import classify, in_compiler_headers, layout, machine_flags

SCHEMA = "mpy-link-evidence/v1"


@lru_cache(maxsize=None)
def _sha256(path: str) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _real(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(path)


def archive_members(path: str) -> dict[str, set[str]]:
    """Member name to the SHA-256 of each member of a GNU ar archive."""
    data = Path(path).read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise ValueError(f"not an ar archive: {path}")
    members: dict[str, set[str]] = {}
    names = b""
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset : offset + 60]
        size = int(header[48:58])
        body = data[offset + 60 : offset + 60 + size]
        name = header[:16].decode("ascii", "replace").strip()
        offset += 60 + size + (size & 1)
        if name == "//":
            names = body
        elif name in ("/", "/SYM64/"):
            continue
        else:
            if name.startswith("/") and name[1:].isdigit():
                start = int(name[1:])
                name = names[start : names.index(b"\n", start)].decode("ascii", "replace")
            members.setdefault(name.rstrip("/"), set()).add(hashlib.sha256(body).hexdigest())
    return members


class _Roots:
    def __init__(self, roots: dict[str, str], build_dir: str):
        self.roots = [(name, Path(_real(path))) for name, path in roots.items()]
        self.build_dir = Path(_real(build_dir))

    def bind(self, path: str) -> dict[str, str] | None:
        """The root and canonical relative path of a file, by the deepest root that contains it."""
        resolved = Path(_real(path))
        best = None
        for name, root in self.roots:
            if resolved.is_relative_to(root) and (best is None or len(root.parts) > len(best[1].parts)):
                best = (name, root)
        if best is None:
            return None
        return {"root": best[0], "path": resolved.relative_to(best[1]).as_posix()}

    def generated(self, path: str) -> bool:
        return Path(_real(path)).is_relative_to(self.build_dir)


def _compiler(entry: dict[str, Any]) -> str | None:
    return shutil.which(arguments_of(entry)[0])


def _compiler_record(path: str) -> dict[str, Any]:
    try:
        banner = subprocess.run([path, "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError):
        banner = ""
    return {
        "role": "cxx" if "++" in Path(path).name else "cc",
        "executable": {"path": path, "sha256": _sha256(path)},
        "version": banner,
    }


@lru_cache(maxsize=None)
def _ninja_deps(build_dir: str) -> dict[str, list[str]]:
    """Prerequisites per object from `ninja -t deps`; Ninja deletes the compilers' dependency files."""
    if not Path(build_dir, ".ninja_deps").is_file():
        return {}
    try:
        result = subprocess.run(["ninja", "-C", build_dir, "-t", "deps"], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return {}
    deps: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in result.stdout.splitlines():
        if line.startswith(" "):
            if current is not None:
                current.append(line.strip())
        elif ": #deps" in line:
            current = deps.setdefault(_real(os.path.join(build_dir, line.split(": #deps")[0])), [])
        else:
            current = None
    return deps


def _prerequisites(entry: dict[str, Any]) -> list[str] | None:
    """The files a compile read: its dependency file, the `.P` copy make kept of it, or Ninja's record."""
    args = arguments_of(entry)
    candidates = []
    if "-MF" in args:
        candidates.append(Path(entry["directory"], args[args.index("-MF") + 1]))
    obj = output_of(entry)
    if obj:
        candidates.append(Path(obj).with_suffix(".P"))
        candidates.append(Path(obj).with_suffix(".d"))
    depfile = next((path for path in candidates if path.is_file()), None)
    if depfile is not None:
        return depfile_prerequisites(depfile.read_text(encoding="utf-8"))
    return _ninja_deps(_real(entry["directory"])).get(_real(os.path.join(entry["directory"], obj))) if obj else None


def _headers(entries: list[dict[str, Any]], roots: _Roots) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Every header a contributing compile read: owned by a root, the compiler's own, or unowned."""
    seen: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, str]] = []
    for entry in entries:
        compiler = _compiler(entry)
        flags = machine_flags(arguments_of(entry))
        language = "c++" if compiler and "++" in Path(compiler).name else "c"
        if entry_file(entry).endswith(".s"):
            continue  # assembled without the preprocessor, so it reads no headers through a dependency file
        try:
            names = _prerequisites(entry)
        except (DepfileError, OSError, UnicodeDecodeError) as error:
            unresolved.append({"kind": "dependency-file", "path": entry_file(entry), "reason": str(error)})
            continue
        if names is None:
            unresolved.append({"kind": "dependency-file", "path": entry_file(entry), "reason": "no dependency file or Ninja record for the compile"})
            continue
        source = _real(entry_file(entry))
        for name in names:
            path = _real(os.path.join(entry["directory"], name))
            if path == source or path in seen:
                continue
            digest = _sha256(path)
            if digest is None:
                continue
            bound = roots.bind(path)
            record: dict[str, Any] = {"path": path, "sha256": digest}
            if roots.generated(path):
                continue
            if bound:
                record.update(disposition="source", source_bindings=[bound])
            else:
                directory = in_compiler_headers(path, compiler, flags, language) if compiler else None
                if directory is None:
                    record.update(disposition="unowned", reason="header lies outside every declared root and the compiler's own directories")
                else:
                    record.update(
                        disposition="toolchain",
                        toolchain={
                            "role": "cxx" if language == "c++" else "cc",
                            "executable": {"path": compiler, "sha256": _sha256(compiler)},
                            "include_dir": str(directory),
                            **({"release": layout(compiler, flags)["release"]} if layout(compiler, flags)["release"] else {}),
                        },
                    )
            seen[path] = record
    return [seen[path] for path in sorted(seen)], unresolved


def _image(
    spec: dict[str, str], entries: list[dict[str, Any]], roots: _Roots, link_dir: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    map_text = Path(spec["map"]).read_text(encoding="utf-8", errors="replace")
    parsed = parse_gnu_map(map_text, base_dir=link_dir)
    outputs = {}
    for entry in entries:
        obj = output_of(entry)
        if obj:
            outputs[_real(obj)] = entry
    asm_roots = source_roots(entries, str(roots.build_dir))

    loaded = loaded_sections(Path(spec["elf"]))
    contributing = [
        group["identity"]
        for group in parsed["allocated_sections"]
        if any(row["size"] and row["output_section"] in loaded for row in group["sections"])
    ]
    flag_sets = Counter(
        (_compiler(entry), machine_flags(arguments_of(entry))) for entry in entries if _compiler(entry)
    )
    compiler, flags = flag_sets.most_common(1)[0][0] if flag_sets else (None, ())

    compiled: dict[str, dict[str, Any]] = {}
    runtime: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, str]] = []
    wanted: dict[str, set[str]] = {}

    def runtime_file(path: str, member: str | None = None) -> bool:
        kind, record, _ = classify(path, compiler, flags) if compiler else (None, None, "")
        if kind is None:
            return False
        item = runtime.setdefault(path, {"path": path, "sha256": _sha256(path), "contributing_members": []})
        if member:
            item["contributing_members"].append(member)
        if kind == "c-library":
            item["c_library"] = record
        else:
            item["toolchain"] = {
                "role": "cc",
                "executable": {"path": compiler, "sha256": _sha256(compiler)},
                "release": record["release"],
            }
        return True

    for identity in contributing:
        if identity["path"] == "linker stubs":
            continue
        path = os.path.normpath(os.path.join(link_dir, identity["path"]))
        if identity["kind"] == "archive_member":
            if not runtime_file(path, identity["member"]):
                wanted.setdefault(path, set()).add(identity["member"])
            continue
        entry = outputs.get(_real(path))
        if entry is not None:
            compiled[_real(path)] = entry
        elif (source := assembly_source(path, str(roots.build_dir), asm_roots)) is not None:
            compiled[_real(path)] = {"directory": link_dir, "file": source, "arguments": [compiler or "cc", "-c", source]}
        elif not runtime_file(path):
            unresolved.append({"kind": "object", "path": path, "reason": "no compiled source and not a toolchain file"})

    by_name: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for obj, entry in outputs.items():
        by_name.setdefault(os.path.basename(obj), []).append((obj, entry))
    for archive, names in sorted(wanted.items()):
        try:
            members = archive_members(archive)
        except (OSError, ValueError) as error:
            unresolved.extend({"kind": "archive-member", "path": f"{archive}({name})", "reason": str(error)} for name in sorted(names))
            continue
        for name in sorted(names):
            matches = {
                obj: entry
                for obj, entry in by_name.get(name, ())
                if _sha256(obj) in members.get(name, set())
            }
            origins = {entry_file(entry) for entry in matches.values()}
            sources_by_hash: dict[str | None, set[str]] = {}
            for obj, entry in matches.items():
                sources_by_hash.setdefault(_sha256(obj), set()).add(entry_file(entry))
            # An archive may hold several members of one name (the map cannot tell them apart); each needs exactly one source.
            if sources_by_hash and set(sources_by_hash) == members.get(name) and all(len(found) == 1 for found in sources_by_hash.values()):
                compiled.update(matches)
            else:
                unresolved.append(
                    {
                        "kind": "archive-member",
                        "path": f"{archive}({name})",
                        "reason": "no compiled object has the member's bytes" if not origins else "several sources match the member's bytes",
                    }
                )

    sources: dict[tuple[str, str], dict[str, str]] = {}
    generated: set[str] = set()
    unbound: set[str] = set()
    for entry in compiled.values():
        source = entry_file(entry)
        if roots.generated(source):
            generated.add(source)
            continue
        bound = roots.bind(source)
        if bound:
            sources[(bound["root"], bound["path"])] = bound
        else:
            unbound.add(source)
    headers, header_gaps = _headers(list({id(entry): entry for entry in compiled.values()}.values()), roots)
    unresolved.extend(header_gaps)
    tools = {}
    for entry in compiled.values():
        executable = _compiler(entry)
        if executable:
            tools[executable] = _compiler_record(executable)
    image = {
        "id": spec["id"],
        "role": spec["role"],
        "elf": {"path": _real(spec["elf"]), "sha256": _sha256(_real(spec["elf"]))},
        "map": {"path": _real(spec["map"]), "sha256": _sha256(_real(spec["map"]))},
        "artifacts": [
            {"role": "deployment", "path": _real(path), "sha256": _sha256(_real(path))}
            for path in spec.get("artifacts", ())
        ],
        "dynamic_needed": dynamic_needed(Path(spec["elf"])),
        "sources": [sources[key] for key in sorted(sources)],
        "generated_sources": sorted(generated),
        "unbound_sources": sorted(unbound),
        "header_accounting": headers,
        "runtime": [runtime[key] for key in sorted(runtime)],
        "unresolved": unresolved,
    }
    selected = [{**entry, "output": obj} for obj, entry in compiled.items() if not roots.generated(entry_file(entry))]
    return image, tools, selected


def build_evidence(
    compile_databases: list[str],
    images: list[dict[str, str]],
    roots: dict[str, str],
    *,
    build_dir: str | None = None,
    link_dir: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the evidence for the images, with its identity digest, and the compilation database
    entries of the objects that contribute to them (the selection for `mpy_analysis.scope`)."""
    entries = [entry for path in compile_databases for entry in json.loads(Path(path).read_text(encoding="utf-8"))]
    if not entries:
        raise ValueError("the compilation databases are empty")
    result_images, tools, selected = [], {}, {}
    for spec in images:
        scope = _Roots(roots, build_dir or str(Path(spec["elf"]).resolve().parent))
        local = [entry for entry in entries if (obj := output_of(entry)) and Path(_real(obj)).is_relative_to(scope.build_dir)]
        if not local:
            raise ValueError(f"no compile command writes an object under {scope.build_dir}")
        link = link_dir or local[0]["directory"]
        image, used, entries_used = _image(spec, local, scope, link)
        result_images.append(image)
        tools.update(used)
        selected.update({entry["output"]: entry for entry in entries_used})
    body = {
        "schema": SCHEMA,
        "toolchain": [tools[key] for key in sorted(tools)],
        "images": result_images,
    }
    body["evidence_id"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body, [selected[key] for key in sorted(selected)]


def load_link_evidence(path: Path) -> dict[str, Any]:
    """Read an evidence file written by `main`; ValueError when it is not one."""
    try:
        evidence = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read link evidence {path}: {error}") from error
    if not isinstance(evidence, dict) or evidence.get("schema") != SCHEMA:
        raise ValueError(f"{path} is not {SCHEMA}")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compile-database", action="append", required=True, help="repeat for images built by separate make runs")
    parser.add_argument("--image", action="append", required=True, metavar="ID:ROLE:ELF:MAP")
    parser.add_argument("--source-root", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--build-dir", help="where the objects are built; default: the directory of each ELF")
    parser.add_argument("--link-dir", help="directory the link ran in, for relative map paths; default: the directory of the image's compiles")
    parser.add_argument("--artifact", action="append", default=[], metavar="IMAGE_ID=PATH", help="a deployment file derived from the image's ELF (.bin, .hex, .dfu)")
    parser.add_argument("--output", required=True, help="the link evidence file")
    parser.add_argument("--selected-database", required=True, help="compilation database of the contributing objects, for mpy_analysis.scope")
    args = parser.parse_args(argv)
    try:
        images = []
        for text in args.image:
            image_id, role, elf, map_path = text.split(":", 3)
            images.append({"id": image_id, "role": role, "elf": elf, "map": map_path, "artifacts": []})
        for item in args.artifact:
            image_id, artifact = item.split("=", 1)
            next(spec for spec in images if spec["id"] == image_id)["artifacts"].append(artifact)
        roots = dict(item.split("=", 1) for item in args.source_root)
        evidence, selected = build_evidence(args.compile_database, images, roots, build_dir=args.build_dir, link_dir=args.link_dir)
        Path(args.output).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        Path(args.selected_database).write_text(json.dumps(selected, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as error:
        print(f"link-evidence: {error}", file=sys.stderr)
        return 2
    unresolved = sum(len(image["unresolved"]) for image in evidence["images"])
    print(f"link evidence: {len(evidence['images'])} images, {unresolved} unresolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
