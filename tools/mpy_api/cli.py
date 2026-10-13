# SPDX-License-Identifier: MIT
"""Generate compiler-derived MicroPython API inventory and declarations."""

from __future__ import annotations

import argparse
from itertools import chain
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


# Canonical JSON is compact, key-sorted and ASCII-only (valid UTF-8 for any str, lone surrogates
# included). With no ``indent`` the encoder runs its C implementation.
_ENCODER = json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
_SPLIT_DEPTH = 4
_BLOCK_CHARS = 1 << 20


def canonical_chunks(value, depth=0):
    """Yield the canonical JSON text of ``value`` in pieces whose concatenation is ``_ENCODER.encode(value)``.

    Non-empty containers down to ``_SPLIT_DEPTH`` are walked here, in sorted key order for
    dicts whose keys are all ``str``; every other value is encoded whole by the C encoder, so
    the text held at once is bounded by the largest value at that depth."""
    if depth < _SPLIT_DEPTH and value:
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            separator = "{"
            for key in sorted(value):
                yield separator + _ENCODER.encode(key) + ":"
                separator = ","
                yield from canonical_chunks(value[key], depth + 1)
            yield "}"
            return
        if isinstance(value, (list, tuple)):
            separator = "["
            for item in value:
                yield separator
                separator = ","
                yield from canonical_chunks(item, depth + 1)
            yield "]"
            return
    yield _ENCODER.encode(value)


def _blocks(pieces):
    pending, chars = [], 0
    for piece in pieces:
        pending.append(piece)
        chars += len(piece)
        if chars >= _BLOCK_CHARS:
            yield "".join(pending).encode("ascii")
            pending, chars = [], 0
    if pending:
        yield "".join(pending).encode("ascii")


def write_canonical(value, stream):
    """Write canonical JSON and a final newline to binary ``stream`` and return its ``bytes`` and ``sha256``.

    The text is produced and written in blocks, so neither the document text nor its bytes
    are held whole and the identity needs no second read."""
    digest = hashlib.sha256()
    size = 0
    for block in _blocks(chain(canonical_chunks(value), ("\n",))):
        stream.write(block)
        digest.update(block)
        size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def canonical_bytes(value):
    """Canonical JSON bytes of a value small enough to hold whole."""
    return (_ENCODER.encode(value) + "\n").encode("ascii")


def file_identity(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"input must be a file: {path}")
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def verify_identities(identities):
    for identity in identities:
        if file_identity(identity["path"]) != identity:
            raise RuntimeError(f"consumed input changed during generation: {identity['path']}")


def producer_tools(root):
    tools = [file_identity(path) for path in sorted(Path(__file__).parent.glob("*.py"))]
    tools.extend(file_identity(root / "tools" / "mpy_analysis" / "mpy_analysis" / (name + ".py"))
                 for name in ("__init__", "compdb", "scope"))
    tools.append(file_identity(root / "tools" / "manifestfile.py"))
    return tools


def assignments(values, label):
    result = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path or name in result:
            raise ValueError(f"{label} must have unique NAME=VALUE entries: {value}")
        result[name] = path
    return result


def authority_modules(root):
    authority = root / "tools" / "mpy_analysis"
    if not (authority / "mpy_analysis" / "compdb.py").is_file():
        raise ValueError(f"selected checkout has no shared compiler-input authority: {root}")
    sys.path.insert(0, str(authority))
    result = []
    for name in ("compdb", "scope"):
        module = importlib.import_module("mpy_analysis." + name)
        expected = authority / "mpy_analysis" / (name + ".py")
        if Path(module.__file__).resolve() != expected.resolve():
            raise ValueError(f"loaded {name} authority belongs to a different checkout")
        result.append(module)
    return tuple(result)


def link_inputs(path, elf, map_path, nm):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("link command must be a JSON object")
    argv = data.get("argv", data.get("link_argv"))
    directory = data.get("directory", data.get("cwd"))
    if not isinstance(argv, list) or not argv or any(
        not isinstance(value, str) or not value or "\x00" in value for value in argv
    ):
        raise ValueError("link command requires a nonempty argv list of strings")
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise ValueError("link command requires its actual absolute working directory")
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("link command working directory is not a directory")
    if data.get("exit", data.get("returncode", 0)) != 0:
        raise ValueError("link command records a failed invocation")
    return {"argv": argv, "directory": directory, "elf": elf, "map": map_path, "nm": nm}


def parser_identity():
    result = {}
    for name in ("tree-sitter", "tree-sitter-c"):
        distribution = importlib.metadata.distribution(name)
        binaries = []
        for relative in distribution.files or ():
            if str(relative).endswith((".so", ".pyd", ".dll", ".dylib")):
                binaries.append(file_identity(distribution.locate_file(relative)))
        if not binaries:
            raise ValueError(f"parser distribution has no assessable native binary: {name}")
        result[name] = {"version": distribution.version, "binaries": sorted(binaries, key=lambda x: x["path"])}
    return result


def source_revision(root):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return {"revision": None, "status": "revision-unavailable; selected input content is authoritative"}
    if result.returncode:
        return {"revision": None, "status": "revision-unavailable; selected input content is authoritative"}
    return {"revision": result.stdout.strip(), "status": "revision plus selected input content identities"}


def collect_frozen(args, root, scope_module):
    if not args.manifest:
        return [], {"manifests": [], "sources": [], "scope": None}
    roots = {name: str(Path(path).resolve(strict=True)) for name, path in assignments(args.source_root, "source root").items()}
    if not roots:
        roots = {"micropython": str(root)}
    owners = assignments(args.source_owner, "source owner")
    for name in roots:
        owners.setdefault(name, name)
    variables = assignments(args.path_var, "manifest path variable")
    manifest_paths = [Path(path).resolve(strict=True) for path in args.manifest]
    # Observe actual authority execution, including selected include/require manifests.
    # Scope owns resolution; the profile hook does not resolve a second manifest graph.
    consumed = {str(path): file_identity(path) for path in manifest_paths}
    authority_path = str((root / "tools" / "manifestfile.py").resolve())
    previous_profile = sys.getprofile()

    def observe(frame, event, arg):
        if previous_profile is not None:
            previous_profile(frame, event, arg)
        if (event == "c_call" and frame.f_code.co_filename == authority_path
                and frame.f_code.co_name == "include"
                and getattr(arg, "__name__", None) == "open"):
            identity = file_identity(frame.f_locals["manifest_path"])
            previous = consumed.setdefault(identity["path"], identity)
            if previous != identity:
                raise RuntimeError(f"manifest changed during collection: {identity['path']}")
        return None

    try:
        sys.setprofile(observe)
        scope = scope_module.collect_manifest_scope(
            root, manifest_paths, roots=roots, path_vars=variables, owners=owners,
            target=args.target, variant=args.variant, port=args.port,
        )
    finally:
        sys.setprofile(previous_profile)
    verify_identities(consumed.values())
    from .frozen import collect_frozen_modules
    modules = collect_frozen_modules(scope)
    sources = [{"path": module["provenance"]["physical_path"],
                "bytes": module["provenance"]["bytes"],
                "sha256": module["provenance"]["sha256"]} for module in modules]
    verify_identities(sources)
    return modules, {
        "manifests": [consumed[path] for path in sorted(consumed)],
        "sources": sorted(sources, key=lambda x: x["path"]), "scope": scope,
    }


def qualify_frozen_payload(inventory, modules):
    payload = inventory["build"].get("frozen_payload", {})
    if payload.get("status") != "resolved":
        raise ValueError("selected frozen payload namespace is not qualified")
    selected = {module["provenance"]["target_path"]: module for module in modules}
    if set(selected) != set(payload["names"]):
        raise ValueError("manifest target paths do not match the selected compiled frozen payload")
    for module in modules:
        module["resolution"]["availability"] = {
            "status": "resolved", "authority": "qualified compiled frozen namespace",
            "source": payload["names_source"]}
        module["provenance"]["compiled_content"] = {
            "status": "unassessed",
            "reason": "MPY body equivalence is not decoded; source declarations require a fresh matching build"}
    for source in payload.get("str_sources", []):
        provenance = selected[source["target_path"]]["provenance"]
        if source["sha256"] != provenance["sha256"]:
            raise ValueError(f"frozen STR source does not match compiled payload: {source['target_path']}")
        provenance["compiled_content"] = {
            "status": "resolved", "sha256": source["sha256"], "source": source["source"]}


def write_generation(output, inventory, declarations, identities, *, notices, verify):
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists; use a fresh generation directory: {output}")
    artifacts = {}
    for name, content in declarations.items():
        path = Path(name)
        if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
            raise ValueError(f"unsafe declaration output path: {name}")
        if path.suffix != ".pyi" or name in artifacts:
            raise ValueError(f"invalid or duplicate declaration output path: {name}")
        artifacts[name] = content.encode("utf-8")
    for name, data in sorted(notices.items()):
        path = Path(name)
        if (not isinstance(data, bytes) or path.is_absolute() or
                len(path.parts) < 2 or path.parts[0] != "enrichment-notices" or
                any(part in ("", ".", "..") for part in path.parts) or
                path.as_posix() != name or name in artifacts):
            raise ValueError(f"invalid enrichment notice output path or bytes: {name}")
        artifacts[name] = data
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mpy-api-", dir=output.parent) as temporary:
        staging = Path(temporary) / "generation"
        staging.mkdir()
        with (staging / "inventory.json").open("xb") as stream:
            identity = write_canonical(inventory, stream)
        records = {"inventory.json": {"path": "inventory.json", **identity, "role": "inventory"}}
        for name, data in artifacts.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            records[name] = {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                             "role": "enrichment-license-notice" if name.startswith("enrichment-notices/") else
                             "runtime-builtins" if name == "typeshed/stdlib/builtins.pyi" else "runtime-module"}
        body = {"schema_version": 1, "producer": "mpy-api", "build": inventory["build"],
                "inputs": identities, "artifacts": [records[name] for name in sorted(records)],
                "coverage": inventory["coverage"],
                "typing_compatibility": {"status": "unverified", "reason": "generation is not supported builtin-aware checker approval"}}
        body["generation_id"] = hashlib.sha256(canonical_bytes(body)).hexdigest()
        (staging / "generation.json").write_bytes(canonical_bytes(body))
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"output appeared during generation: {output}")
        verify()
        os.replace(staging, output)
    return body


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compdb", required=True, type=Path)
    parser.add_argument("--micropython-root", required=True, type=Path)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--port", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--map", required=True, dest="map_path", type=Path)
    parser.add_argument("--link-command", required=True, type=Path, help="JSON actual argv and absolute directory/cwd")
    parser.add_argument("--nm", default="nm", help="matching target nm executable")
    parser.add_argument("--manifest", action="append", default=[])
    parser.add_argument("--path-var", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--source-root", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--source-owner", action="append", default=[], metavar="NAME=OWNER")
    parser.add_argument("--enrichment", type=Path, help="optional schema-v1 manifest pinning a .pyi enrichment tree and its source/licence inputs")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def generate(args):
    if args.jobs < 1:
        raise ValueError("jobs must be positive")
    for name in ("port", "target", "variant"):
        value = getattr(args, name)
        if not value.strip() or "\x00" in value:
            raise ValueError(f"{name} must be a nonempty name without NUL")
    root = args.micropython_root.resolve(strict=True)
    build_dir = args.build_dir.resolve(strict=True)
    if not root.is_dir() or not build_dir.is_dir():
        raise ValueError("MicroPython root and selected build must be directories")
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output}")
    from .enrichment import load_enrichment, merge_enrichment, validate_enrichment_target, verify_enrichment
    enrichment = load_enrichment(args.enrichment) if args.enrichment else None
    if enrichment:
        validate_enrichment_target(enrichment, {key: getattr(args, key) for key in ("port", "target", "variant")})
    tools = producer_tools(root)
    inputs = {"compdb": file_identity(args.compdb), "elf": file_identity(args.elf),
              "map": file_identity(args.map_path), "link_command": file_identity(args.link_command)}
    parser = parser_identity()
    revision = source_revision(root)
    compdb, scope = authority_modules(root)
    entries = json.loads(args.compdb.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("compilation database must be a nonempty list")
    for index, entry in enumerate(entries):
        compdb.validate_entry(entry, f"compiler entry {index}")
    completeness = compdb.completeness(entries, build_dir)
    if not completeness["complete"]:
        raise ValueError("compilation database does not cover the selected build: " + json.dumps(completeness, sort_keys=True))
    has_frozen = any(Path(compdb.entry_file(entry)).name == "frozen_content.c" for entry in entries)
    if has_frozen and not args.manifest:
        raise ValueError("selected frozen_content.c requires explicit selected --manifest inputs")
    if args.manifest and not has_frozen:
        raise ValueError("manifest inputs require frozen_content.c in the selected build")
    link = link_inputs(args.link_command, args.elf.resolve(strict=True), args.map_path.resolve(strict=True), args.nm)
    frozen, frozen_inputs = collect_frozen(args, root, scope)
    from .inventory import collect_inventory
    from .declarations import render_declarations
    from . import __version__
    inventory = collect_inventory(database=args.compdb.resolve(strict=True), root=root,
                                  build_dir=build_dir, port=args.port, target=args.target,
                                  variant=args.variant, link=link, jobs=args.jobs)
    if args.manifest:
        qualify_frozen_payload(inventory, frozen)
    names = {module["name"] for module in inventory["modules"]}
    for module in frozen:
        if module["name"] in names:
            raise ValueError(f"frozen/native import conflict is not qualified: {module['name']}")
        names.add(module["name"])
        inventory["modules"].append(module)
    inventory["modules"].sort(key=lambda module: module["name"])
    inventory["build"]["source"] = revision
    inventory["build"]["compiler_database_completeness"] = completeness
    inventory["coverage"]["frozen"] = {"processed_modules": len(frozen),
                                         "records": [module["provenance"] for module in frozen]}
    identities = {**inputs, "tool_version": __version__, "python": sys.version,
                  "tools": tools, "parser": parser, "frozen": frozen_inputs,
                  "enrichment": None}
    inventory.setdefault("provenance", {})["producer_inputs"] = identities
    if enrichment:
        merge_enrichment(inventory, enrichment)
    declarations = render_declarations(inventory)
    if "typeshed/stdlib/builtins.pyi" not in declarations:
        raise ValueError("selected inventory has no generated builtin replacement")
    def verify_publication_inputs():
        verify_identities([*inputs.values(), *frozen_inputs["manifests"],
                           *frozen_inputs["sources"]])
        from . import compiler
        consumed = inventory["build"]["consumed_inputs"]
        if compiler.public_inputs(compiler.capture_inputs(consumed)) != consumed:
            raise RuntimeError("selected compiler/link inputs changed during generation")
        if producer_tools(root) != tools:
            raise RuntimeError("producer code graph changed during generation")
        if parser_identity() != parser:
            raise RuntimeError("parser installation changed during generation")
        if source_revision(root) != revision:
            raise RuntimeError("source revision changed during generation")
        if enrichment:
            verify_enrichment(enrichment)

    return output, write_generation(output, inventory, declarations, identities,
                                    notices=enrichment["notice_artifacts"] if enrichment else {},
                                    verify=verify_publication_inputs)


def main(argv=None):
    args = argument_parser().parse_args(argv)
    try:
        output, receipt = generate(args)
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f"mpy-api: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(output), "generation_id": receipt["generation_id"],
                      "artifacts": len(receipt["artifacts"]), "typing_compatibility": "unverified"}, sort_keys=True))
    return 0
