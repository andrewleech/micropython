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
import re
import subprocess
import sys
import tempfile

from .declarations import OPEN_MODULES
from .paths import SYSTEM_PREFIXES, PathNames, argument_paths, is_host_path
from .structure import json_pieces


# Canonical JSON is compact, key-sorted and ASCII-only (valid UTF-8 for any str, lone surrogates
# included). With no ``indent`` the encoder runs its C implementation.
_ENCODER = json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
_SPLIT_DEPTH = 4
_BLOCK_CHARS = 1 << 20


def canonical_chunks(value):
    """An iterator over the canonical JSON text of ``value`` in pieces whose concatenation is
    ``_ENCODER.encode(value)``.

    Containers above ``_SPLIT_DEPTH`` are walked with an explicit stack (``json_pieces``), so pieces
    are bounded by the containers from ``_SPLIT_DEPTH`` down; those are encoded whole by the C encoder
    unless they are too high for its recursion, in which case they are walked as well."""
    return json_pieces(value, _ENCODER, _SPLIT_DEPTH)


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
    return ("".join(canonical_chunks(value)) + "\n").encode("ascii")


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
    tools.extend(file_identity(root / "tools" / "mpy_build_inputs" / (name + ".py"))
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
    authority = root / "tools"
    if not (authority / "mpy_build_inputs" / "compdb.py").is_file():
        raise ValueError(f"selected checkout has no shared compiler-input authority: {root}")
    sys.path.insert(0, str(authority))
    result = []
    for name in ("compdb", "scope"):
        module = importlib.import_module("mpy_build_inputs." + name)
        expected = authority / "mpy_build_inputs" / (name + ".py")
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


# Names the generation gives its directories and files in everything it publishes. A --source-root name
# may not be one of them.
_RESERVED_NAMES = frozenset(("build", "compdb", "elf", "enrichment", "link-command", "manifest", "map",
                             "mpy_api", "path-var", "python-packages", "system"))


def _forms(path):
    """The spellings of ``path`` the collected records can use: lexical and symlink-resolved."""
    path = Path(path)
    return {str(path.absolute()), str(path.resolve())}


def _relocated(build_forms, directories):
    """Rules for the directories whose absolute path a build repeats below its build directory (the object
    of a source outside the build is ``<build>/<absolute source directory>``, with or without a doubled slash)."""
    rules = []
    for build in build_forms:
        for path, name in directories:
            rules.append((build + path, "build/" + name))
            rules.append((build + "/" + path, "build/" + name))
    return rules


def path_names(args, root, build_dir):
    """The names under which the generation publishes the paths of its inputs: the ``--source-root``
    names, ``micropython`` for the selected checkout when no source root is that directory, ``build``
    for the build directory, and a role name for every other input given on the command line."""
    claims = {}
    rules = []
    owners = {}

    def claim(name, path):
        for form in _forms(path):
            if claims.setdefault(form, name) != name:
                raise ValueError(f"{form} would be published as both {claims[form]} and {name}")
            rules.append((form, name))
        if owners.setdefault(name, path) != path:
            raise ValueError(f"{path} and {owners[name]} would both be published as {name}; give one a --source-root name")

    def named_directory(path):
        return any(path == directory or path.startswith(directory + "/") for directory in claims)

    def named_under(path, directory):
        return any(path == form or path.startswith(form + "/") for form in _forms(directory))

    for name, path in assignments(args.source_root, "source root").items():
        if name in _RESERVED_NAMES or "/" in name:
            raise ValueError(f"source root name {name!r} is reserved or not a single path component")
        claim(name, Path(path).resolve(strict=True))
    if str(root) not in claims:
        if any(name == "micropython" for name in claims.values()):
            raise ValueError("--source-root micropython names a directory other than --micropython-root")
        claim("micropython", root)
    if str(build_dir) not in claims:
        claim("build", build_dir)
    for name, path in assignments(args.path_var, "manifest path variable").items():
        if not named_directory(str(Path(path).resolve())):
            claim("path-var/" + name, Path(path).resolve(strict=True))
    for index, manifest in enumerate(args.enrichment, 1):
        if not named_directory(str(manifest.resolve())):
            claim(f"enrichment/{index}", manifest.resolve(strict=True).parent)
    for role, paths in (("compdb", [args.compdb]), ("elf", [args.elf]), ("map", [args.map_path]),
                        ("link-command", [args.link_command]), ("manifest", args.manifest)):
        for path in paths:
            path = Path(path).resolve(strict=True)
            if not named_directory(str(path)):
                claim(f"{role}/{path.name}", path)
    claim("mpy_api", Path(__file__).resolve().parent)
    rules += _relocated(_forms(build_dir), [(form, name) for form, name in claims.items()
                                            if not named_under(form, build_dir)])
    for name in ("tree-sitter", "tree-sitter-c"):
        packages = Path(importlib.metadata.distribution(name).locate_file("")).resolve()
        for form in _forms(packages):
            rules.append((form, "python-packages"))
    return PathNames(rules)


# An absolute path that is a whole string or follows a flag such as -L or an assignment such as -Map=.
_OPTION_PATH = re.compile(r"(?:^(?:-[A-Za-z]+)?|[=,])(/[\w.+~/-]+)$")


def _host_paths(surface, arguments):
    """The absolute paths of the build record that name files of the host: the compiler and link tools,
    the headers and libraries the compiler and linker consumed, and the paths in the compile ``arguments``."""
    build, coverage = surface["build"], surface["coverage"]
    link = build["link"]
    strings = [*build["consumed_inputs"], *build["source_inputs"], *(compiler for compiler, _ in build["compilers"]),
               *(link[key] for key in ("linker", "nm", "objdump")), *link["link_inputs"],
               *link["argv"], *link["expanded_argv"], *link["linker_arguments"],
               *coverage["unassessed_link_objects"], *arguments]
    for record in build["consumed_inputs"].values():
        strings.extend((record["path"], record["resolved"]))
    for text in strings:
        match = _OPTION_PATH.search(text.partition("(")[0])
        if match:
            yield match.group(1)


# The inputs that record the host paths of the build. Their published identity is that of their text
# with the host paths named.
_PATH_BEARING = ("compdb", "link_command", "map")


def path_bearing_texts(inputs):
    """The text of each path-bearing input, verified against its captured identity, as (role, text) pairs.
    A JSON input is given in its canonical form with its strings unescaped, so a path with non-ASCII
    characters is found whichever way the file wrote it."""
    result = []
    for role in _PATH_BEARING:
        identity = inputs[role]
        data = Path(identity["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != identity["sha256"]:
            raise RuntimeError(f"consumed input changed during generation: {identity['path']}")
        if role == "map":
            result.append((role, data.decode("utf-8", "surrogateescape")))
        else:
            result.append((role, json.dumps(json.loads(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    return result


def require_covered(texts, inputs, names):
    """Raise when the text of a path-bearing input holds a host path on this machine that no root covers."""
    for role, text in texts:
        names.require(names.unnamed(text, existing=True), f"{role} {inputs[role]['path']}")


def named_identities(texts, inputs, names):
    """The published identity of each path-bearing input: the size and sha256 of its text with the host paths
    named, under ``named_*`` keys, since they are not the digests of the file."""
    result = {}
    for role, text in texts:
        named = names.text(text).encode("utf-8", "surrogatepass")
        result[role] = {"path": inputs[role]["path"], "named_bytes": len(named),
                        "named_sha256": hashlib.sha256(named).hexdigest()}
    return result


def host_names(surface, names, arguments):
    """``names`` extended with ``system/<path>`` for each toolchain and library path of the build record and
    of the compile ``arguments``. Any other host path that no name covers is an error."""
    rules, unnamed = {}, []
    for path in sorted(set(_host_paths(surface, arguments))):
        if names.text(path) != path:
            continue
        if path.startswith(SYSTEM_PREFIXES):
            rules[path] = "system" + path
        elif is_host_path(path):
            unnamed.append(path)
    names.require(unnamed, "the build record")
    return names.extended(rules.items())


def write_generation(output, surface, declarations, identities, *, notices, verify, inventory=None, pack=None):
    """Publish the declarations and notices with their ``generation.json`` receipt, and ``inventory``
    as ``inventory.json`` when it is given. ``declarations`` maps output paths to text or bytes. With a
    ``pack`` (see ``pack.assemble_pack``) they are its files: its records add to the receipt entry of each
    file, and its ``pack`` section joins the receipt."""
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists; use a fresh generation directory: {output}")
    extra = pack.records if pack is not None else {}
    artifacts = {}
    for name, content in declarations.items():
        path = Path(name)
        if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
            raise ValueError(f"unsafe declaration output path: {name}")
        if (path.suffix != ".pyi" and name not in extra) or name in artifacts:
            raise ValueError(f"invalid or duplicate declaration output path: {name}")
        artifacts[name] = content.encode("utf-8") if isinstance(content, str) else content
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
        records = {}
        if inventory is not None:
            with (staging / "inventory.json").open("xb") as stream:
                identity = write_canonical(inventory, stream)
            records["inventory.json"] = {"path": "inventory.json", **identity, "role": "inventory"}
        for name, data in artifacts.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            records[name] = {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                             "role": "enrichment-license-notice" if name.startswith("enrichment-notices/") else
                             "runtime-builtins" if name == "typeshed/stdlib/builtins.pyi" else "runtime-module",
                             **extra.get(name, {})}
        body = {"schema_version": 1, "producer": "mpy-api", "build": surface["build"],
                "inputs": identities, "artifacts": [records[name] for name in sorted(records)],
                "coverage": surface["coverage"],
                **({"pack": pack.receipt} if pack is not None else {}),
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
    parser.add_argument("--enrichment", type=Path, action="append", default=[],
                        help="additional schema-v1 reference manifest; later references enrich fields left unknown by the build")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--open-modules", choices=OPEN_MODULES, default="proven",
                        help="which modules and classes with dynamic attribute evidence get a catch-all __getattr__: "
                             "'proven' only where a provider was found, 'all' also where the declared names are merely unproven")
    parser.add_argument("--inventory", action="store_true",
                        help="also publish inventory.json, a diagnostic projection of the collected facts")
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
    from .docstubs import default_enrichment_path
    from .enrichment import load_enrichment, merge_enrichment, validate_enrichment_target, verify_enrichment
    enrichments = {"bundled": load_enrichment(default_enrichment_path())}
    enrichments.update((f"reference-{index}", load_enrichment(path))
                       for index, path in enumerate(args.enrichment, 1))
    for enrichment in enrichments.values():
        validate_enrichment_target(enrichment, {key: getattr(args, key) for key in ("port", "target", "variant")})
    tools = producer_tools(root)
    inputs = {"compdb": file_identity(args.compdb), "elf": file_identity(args.elf),
              "map": file_identity(args.map_path), "link_command": file_identity(args.link_command)}
    parser = parser_identity()
    revision = source_revision(root)
    paths = path_names(args, root, build_dir)
    # A missing root fails here, before any extraction.
    texts = path_bearing_texts(inputs)
    require_covered(texts, inputs, paths)
    _, scope = authority_modules(root)
    from .inventory import collect_inventory, select_records
    selection = select_records(database=args.compdb.resolve(strict=True), root=root, build_dir=build_dir, names=paths)
    entries, completeness = selection["entries"], selection["completeness"]
    has_frozen = any(Path(selection["compdb"].entry_file(entry)).name == "frozen_content.c" for entry in entries)
    if has_frozen and not args.manifest:
        raise ValueError("selected frozen_content.c requires explicit selected --manifest inputs")
    if args.manifest and not has_frozen:
        raise ValueError("manifest inputs require frozen_content.c in the selected build")
    link = link_inputs(args.link_command, args.elf.resolve(strict=True), args.map_path.resolve(strict=True), args.nm)
    frozen, frozen_inputs = collect_frozen(args, root, scope)
    from .declarations import render_declarations
    from .facts import FactStore
    from .pack import assemble_pack
    from . import __version__
    # Fact objects live beside the output for the run; they are inputs to publication, not part of it.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mpy-api-facts-", dir=output.parent) as directory:
        store = FactStore(directory)
        surface, verification, document = collect_inventory(
            selection, port=args.port, target=args.target, variant=args.variant, link=link,
            store=store, jobs=args.jobs, inventory=args.inventory)
        if args.manifest:
            qualify_frozen_payload(surface, frozen)
        names = {module["name"] for module in surface["modules"]}
        for module in frozen:
            if module["name"] in names:
                raise ValueError(f"frozen/native import conflict is not qualified: {module['name']}")
            names.add(module["name"])
            surface["modules"].append(module)
        surface["modules"].sort(key=lambda module: module["name"])
        surface["build"]["source"] = revision
        surface["build"]["compiler_database_completeness"] = completeness
        surface["coverage"]["frozen"] = {"processed_modules": len(frozen),
                                         "records": [module["provenance"] for module in frozen]}
        identities = {**inputs, "tool_version": __version__, "python": sys.version,
                      "tools": tools, "parser": parser, "frozen": frozen_inputs,
                      "declaration_options": {"open_modules": args.open_modules}}
        surface["provenance"]["producer_inputs"] = identities
        enrichment_inputs, comparisons = {}, {}
        for label, enrichment in enrichments.items():
            comparisons[label] = merge_enrichment(surface, enrichment, compare=args.inventory)
            enrichment_inputs[label] = identities["enrichment"]
            if args.inventory:
                comparisons[label]["inputs_ref"] = f"provenance.producer_inputs.enrichment.{label}.inputs"
        identities["enrichment"] = enrichment_inputs
        if args.inventory:
            surface["enrichment"] = comparisons
        # Everything from here on is published: the host paths of the records become names, so the
        # declarations (typing variable names derive from identities) and the receipt do not depend on
        # where the inputs live.
        paths = host_names(surface, paths,
                           list(chain.from_iterable(argument_paths(record["_expanded_arguments"]) for record in selection["records"])))
        published = named_identities(texts, inputs, paths)
        identities.update(published)
        paths = paths.extended(digests=[(inputs[role]["sha256"], "named-sha256:" + published[role]["named_sha256"])
                                        for role in _PATH_BEARING])
        surface = paths.value(surface)
        identities = paths.value(identities)
        declarations = render_declarations(surface, args.open_modules)
        if "typeshed/stdlib/builtins.pyi" not in declarations:
            raise ValueError("selected inventory has no generated builtin replacement")
        # The published tree is the generated declarations assembled with the support layer; the support
        # inputs are part of what generation_id identifies.
        pack = assemble_pack(declarations)
        from .docmerge import enrich_declarations
        available = {name: data.decode("utf-8") for name, data in pack.files.items() if name.endswith(".pyi")}
        selected_modules = {module["name"] for module in surface["modules"]} | {"builtins"}
        selected = {
            name for name in available if name.startswith("typeshed/stdlib/")
            and name[len("typeshed/stdlib/"):-4].removesuffix("/__init__").replace("/", ".") in selected_modules
        }
        applications = {}
        for label, enrichment in enrichments.items():
            enriched, application = enrich_declarations(
                {name: available[name] for name in sorted(selected)}, enrichment, surface,
                generated_declarations=declarations, available_declarations=available)
            for name, text in enriched.items():
                text = paths.text(text)
                if text != available[name]:
                    pack.files[name] = text.encode("utf-8")
                    pack.records[name]["documentation_enriched"] = True
                    available[name] = text
            application.pop("references")
            application.pop("lineage")
            application["provenance_ref"] = f"inputs.enrichment.{label}.provenance"
            application["target_match"] = comparisons[label]["target_match"]
            applications[label] = application
        pack.receipt["enrichment"] = paths.value({"applications": applications})
        identities = {**identities, "support": pack.inputs}
        def verify_publication_inputs():
            verify_identities([*inputs.values(), *frozen_inputs["manifests"],
                               *frozen_inputs["sources"]])
            from . import compiler
            compiler.verify_collection(verification)
            store.verify()
            if producer_tools(root) != tools:
                raise RuntimeError("producer code graph changed during generation")
            if parser_identity() != parser:
                raise RuntimeError("parser installation changed during generation")
            if source_revision(root) != revision:
                raise RuntimeError("source revision changed during generation")
            for enrichment in enrichments.values():
                verify_enrichment(enrichment)

        receipt = write_generation(output, surface, pack.files, identities, pack=pack,
                                   notices={name: data for enrichment in enrichments.values()
                                            for name, data in enrichment["notice_artifacts"].items()},
                                   verify=verify_publication_inputs,
                                   inventory=document.value(surface, paths) if document else None)
        return output, receipt, store.report()


def main(argv=None):
    args = argument_parser().parse_args(argv)
    try:
        output, receipt, facts = generate(args)
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f"mpy-api: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(output), "generation_id": receipt["generation_id"],
                      "artifacts": len(receipt["artifacts"]), "typing_compatibility": "unverified",
                      "fact_objects": facts}, sort_keys=True))
    return 0
