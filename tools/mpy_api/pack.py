# SPDX-License-Identifier: MIT
"""Assemble the generated declarations and the vendored support layer into one standalone typeshed tree.

The tree is ``typeshed/stdlib`` (every module, support modules included, nothing outside it),
``typeshed/stubs/mypy-extensions/mypy_extensions.pyi`` and a generated ``typeshed/stdlib/VERSIONS``, so that
Pyrefly, mypy and pyright can use it as their only standard-library and builtins stubs.

The support layer is the trimmed copy of micropython-stdlib-stubs under ``vendor/``, described by its
provenance file, plus the small modules under ``overlay/``. The assembly reads them as follows:

- the roots of the tree are the generated modules, ``builtins``, the ``bootstrap-module`` residue entries (modules
  the checkers need although nothing imports them) and the modules with forced names (see below); every module a
  root imports joins the tree, so a vendored module appears only when something needs it;
- a vendored module no generated module replaces is copied as it is;
- ``builtins`` and every module that is both generated and vendored is the merge of the two
  (``merge.merge_template``): the vendored file is the template and the generated one supplies the facts;
- the support residue of the provenance file lists what the checkers need although the build may not have it,
  each entry with the failure observed without it: a ``bare-class`` name of ``builtins`` is a class without
  members when the build lacks it, a ``forced-name`` stays in its module's merge whatever the generated module
  declares, a ``bootstrap-module`` is a root, and a ``kept-import`` is a name the trimmed vendored file keeps
  although nothing in it reads it (recorded, not acted on).

The names other modules import from a merged module are kept in it, and which modules exist depends on what
is kept, so the outputs are rebuilt until those sets stop growing. Nothing in the result depends on
iteration order, time or the location of any file."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .merge import ClassInfo, Merged, class_table, merge_template
from .stubast import Edge, decode, has_star_import, import_edges, module_definitions, module_names, parse

HERE = Path(__file__).resolve().parent
VENDOR_ROOT = HERE / "vendor" / "micropython_stdlib_stubs"
PROVENANCE = HERE / "vendor" / "micropython_stdlib_stubs.provenance.json"
OVERLAY_ROOT = HERE / "overlay"

BUILTINS_PATH = "typeshed/stdlib/builtins.pyi"
STDLIB = "typeshed/stdlib"
MYPY_EXTENSIONS = "stubs/mypy-extensions/mypy_extensions.pyi"
LICENSE_FILES = ("LICENSE.md", "LICENSE_typeshed")
LICENSE_DIRECTORY = "licenses/micropython-stdlib-stubs"
OVERLAY_ORIGIN = "mpy-api overlay"

RESIDUE_FORMS = ("bare-class", "forced-name", "bootstrap-module", "kept-import")
RESIDUE_KEYS = ("name", "module", "form", "checker", "failure")


class PackError(ValueError):
    """The pack cannot be assembled or does not stand on its own."""


@dataclass(frozen=True)
class Located:
    """Where a support module lives: ``origin`` is ``vendor`` or ``overlay``, ``path`` is relative to that root."""
    origin: str
    path: str
    is_package: bool


@dataclass(frozen=True)
class Source:
    data: bytes
    text: str
    located: Located


class Sources:
    """The support layer: vendored and overlay modules and the provenance of the vendored ones. Every file
    read is recorded with its sha256."""

    def __init__(self, vendor: Path = VENDOR_ROOT, provenance: Path = PROVENANCE, overlay: Path = OVERLAY_ROOT) -> None:
        self.vendor, self.overlay = Path(vendor), Path(overlay)
        loaded = json.loads(Path(provenance).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or any(not isinstance(loaded.get(key), expected) for key, expected in (
                ("upstream", dict), ("files", dict), ("removed", dict), ("overlay", dict), ("support_residue", list))):
            raise PackError(f"provenance file {provenance} is malformed")
        for entry in loaded["support_residue"]:
            if (not isinstance(entry, dict) or set(entry) != set(RESIDUE_KEYS) or entry["form"] not in RESIDUE_FORMS
                    or any(not isinstance(entry[key], str) or not entry[key] for key in RESIDUE_KEYS)):
                raise PackError(f"support residue entry is malformed: {entry!r}")
        self.provenance = loaded
        self.consumed: dict[tuple[str, str], str] = {}
        self._sources: dict[str, Source | None] = {}

    def label(self) -> str:
        return f"{self.provenance['upstream']['name']} {self.provenance['upstream']['version']}"

    def locate(self, module: str) -> Located | None:
        relative = module.replace(".", "/")
        for origin, root in (("overlay", self.overlay), ("vendor", self.vendor)):
            for candidate, package in ((f"stdlib/{relative}.pyi", False), (f"stdlib/{relative}/__init__.pyi", True)):
                if (root / candidate).is_file():
                    return Located(origin, candidate, package)
        return None

    def read(self, origin: str, path: str) -> bytes:
        data = ((self.vendor if origin == "vendor" else self.overlay) / path).read_bytes()
        self.consumed[(origin, path)] = hashlib.sha256(data).hexdigest()
        return data

    def source(self, module: str) -> Source | None:
        if module not in self._sources:
            located = self.locate(module)
            if located is None:
                self._sources[module] = None
            else:
                data = self.read(located.origin, located.path)
                self._sources[module] = Source(data, decode(data), located)
        return self._sources[module]

    def residue(self, form: str) -> dict[str, frozenset[str]]:
        """Module to the names of the residue entries of ``form``."""
        found: dict[str, set[str]] = {}
        for entry in self.provenance["support_residue"]:
            if entry["form"] == form:
                found.setdefault(entry["module"], set()).add(entry["name"])
        return {module: frozenset(names) for module, names in found.items()}


@dataclass
class Output:
    """One module of the tree: its bytes and how it was made."""
    data: bytes
    treatment: str
    source: Source | None = None
    merge: Merged | None = None


@dataclass(frozen=True)
class Pack:
    """The assembled tree: ``files`` by path, the receipt fields of each file, the consumed support inputs
    and the ``pack`` section of the receipt."""
    files: dict[str, bytes]
    records: dict[str, dict]
    inputs: dict
    receipt: dict


def _generated_modules(declarations: dict[str, str]) -> tuple[dict[str, str], set[str], str]:
    """The generated modules by dotted name, which of them are packages, and the generated ``builtins``."""
    if BUILTINS_PATH not in declarations:
        raise PackError("the generation has no builtins declarations")
    modules: dict[str, str] = {}
    packages: set[str] = set()
    for name, text in sorted(declarations.items()):
        if name == BUILTINS_PATH:
            continue
        if not name.startswith("modules/") or not name.endswith(".pyi"):
            raise PackError(f"unexpected declaration path: {name}")
        parts = name[len("modules/"):-len(".pyi")].split("/")
        package = parts[-1] == "__init__"
        dotted = ".".join(parts[:-1] if package else parts)
        if not dotted or dotted in modules:
            raise PackError(f"declarations name module {dotted!r} twice or not at all")
        modules[dotted] = text
        if package:
            packages.add(dotted)
    return modules, packages, declarations[BUILTINS_PATH]


class _Assembler:
    def __init__(self, declarations: dict[str, str], sources: Sources) -> None:
        self.sources = sources
        self.generated, self.packages, self.builtins = _generated_modules(declarations)
        self.bare = sources.residue("bare-class").get("builtins", frozenset())
        self.forced = sources.residue("forced-name")
        self.bootstrap = frozenset(sources.residue("bootstrap-module"))
        self.refs: dict[str, frozenset[str]] = {}
        self.typing: dict[str, ClassInfo] | None = None
        self._built: dict[tuple, Output] = {}

    def roots(self) -> frozenset[str]:
        return frozenset(self.generated) | self.bootstrap | frozenset(self.forced) | {"builtins"}

    def available(self, module: str) -> bool:
        return module in self.generated or module == "builtins" or self.sources.locate(module) is not None

    def typing_table(self) -> dict[str, ClassInfo]:
        if self.typing is None:
            source = self.sources.source("typing")
            if source is None:
                raise PackError("the support layer has no typing module")
            self.typing = class_table(parse(source.text, "typing"))
        return self.typing

    def wanted(self, module: str) -> frozenset[str]:
        names = set(self.refs.get(module, ())) | set(self.forced.get(module, ()))
        return frozenset(names)

    def build(self, module: str) -> Output:
        key = (module, self.wanted(module))
        if key not in self._built:
            self._built[key] = self._build(module)
        return self._built[key]

    def builtin_names(self) -> frozenset[str]:
        return frozenset(module_definitions(parse(self.build("builtins").data.decode("utf-8"), "builtins")))

    def _build(self, module: str) -> Output:
        generated = self.generated.get(module)
        if module == "builtins":
            template = self.sources.source("builtins")
            if template is None:
                raise PackError("the support layer has no builtins module")
            merged = merge_template(self.builtins, template.text, what="builtins", keep=sorted(self.wanted(module)),
                                    bare=sorted(self.bare), typing=self.typing_table())
            return Output(merged.text.encode("utf-8"), "merged", template, merged)
        source = self.sources.source(module)
        if generated is None:
            if source is None:
                return Output(b"", "package")
            return Output(source.data, "overlay" if source.located.origin == "overlay" else "copied", source)
        if source is None:
            return Output(generated.encode("utf-8"), "generated")
        merged = merge_template(generated, source.text, what=module, keep=sorted(self.wanted(module)),
                                typing=self.typing_table(), builtin_names=self.builtin_names())
        return Output(merged.text.encode("utf-8"), "merged", source, merged)

    def edges(self, module: str, output: Output) -> list[Edge]:
        if not output.data:
            return []
        package = module in self.packages or (output.source is not None and output.source.located.is_package)
        return import_edges(parse(output.data.decode("utf-8"), module), module, package)

    def run(self) -> dict[str, Output]:
        while True:
            outputs: dict[str, Output] = {}
            work = sorted(self.roots())
            while work:
                module = work.pop()
                if module in outputs:
                    continue
                parts = module.split(".")
                work += [".".join(parts[:index]) for index in range(1, len(parts))]
                outputs[module] = output = self.build(module)
                for edge in self.edges(module, output):
                    if self.available(edge.module):
                        work.append(edge.module)
                        work += [f"{edge.module}.{name}" for name in edge.names if self.available(f"{edge.module}.{name}")]
            refs: dict[str, set[str]] = {}
            for module, output in outputs.items():
                for edge in self.edges(module, output):
                    if edge.module != module:
                        refs.setdefault(edge.module, set()).update(name for name in edge.names if name != "*")
            grown = {module: frozenset(names | self.refs.get(module, frozenset())) for module, names in refs.items()}
            for module, names in self.refs.items():
                grown.setdefault(module, names)
            if grown == self.refs:
                return outputs
            self.refs = grown


def _required(outputs: dict[str, Output], assembler: _Assembler) -> set[str]:
    """The modules that must exist as files: the roots and every module another module imports as a module."""
    required = set(assembler.roots())
    for module, output in outputs.items():
        for edge in assembler.edges(module, output):
            if edge.bare:
                required.add(edge.module)
            required.update(f"{edge.module}.{name}" for name in edge.names if f"{edge.module}.{name}" in outputs)
    return required


def _validate(files: dict[str, Output], packages: set[str]) -> dict[str, list[str]]:
    """Check that every import of the tree resolves to a module and a name of the tree. Problems in
    support and merged modules raise; those in modules that are only generated (a frozen module importing
    something the build lacks) are returned."""
    trees = {module: parse(output.data.decode("utf-8"), module) for module, output in files.items()}
    names = {module: module_names(tree) for module, tree in trees.items()}
    open_modules = {module for module, tree in trees.items() if has_star_import(tree) or "__getattr__" in names[module]}
    problems: dict[str, list[str]] = {}
    for module, output in sorted(files.items()):
        package = module in packages or (output.source is not None and output.source.located.is_package)
        for edge in import_edges(trees[module], module, package):
            if edge.module not in files:
                problems.setdefault(module, []).append(f"imports module {edge.module}, which the pack does not have")
                continue
            if edge.module in open_modules:
                continue
            for name in edge.names:
                if name != "*" and name not in names[edge.module] and f"{edge.module}.{name}" not in files:
                    problems.setdefault(module, []).append(f"imports {name} from {edge.module}, which does not define it")
    fatal = {module: found for module, found in problems.items() if files[module].treatment != "generated"}
    if fatal:
        raise PackError("the pack does not stand on its own: " + "; ".join(
            f"{module} {problem}" for module, found in sorted(fatal.items()) for problem in found))
    return dict(sorted(problems.items()))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Report entries that are listed by name in the receipt; the others are counted.
_LISTED = frozenset(("generated_wins", "return_overrides", "return_conflicts", "abstract_demoted", "abstract_unresolved",
                     "rescued_imports", "bare", "dropped_readers", "dangling", "template_constructor"))


def assemble_pack(declarations: dict[str, str], sources: Sources | None = None) -> Pack:
    """The standalone typeshed tree for generated ``declarations`` (``modules/...`` and
    ``typeshed/stdlib/builtins.pyi`` as ``render_declarations`` returns them)."""
    sources = sources or Sources()
    assembler = _Assembler(declarations, sources)
    outputs = assembler.run()
    required = _required(outputs, assembler)
    parents = {".".join(module.split(".")[:index]) for module in outputs if outputs[module].data or module in required
               for index in range(1, len(module.split(".")))}
    kept = {module: output for module, output in outputs.items()
            if output.data or module in required or module in parents}
    unresolved = _validate(kept, assembler.packages)

    files: dict[str, bytes] = {}
    records: dict[str, dict] = {}
    for module, output in sorted(kept.items()):
        children = any(other.startswith(module + ".") for other in kept)
        relative = module.replace(".", "/")
        path = f"{STDLIB}/{relative}/__init__.pyi" if children else f"{STDLIB}/{relative}.pyi"
        files[path] = output.data
        records[path] = _record(module, output, sources)
    top = sorted({module.split(".")[0] for module in kept})
    files[f"{STDLIB}/VERSIONS"] = "".join(f"{name}: 3.0-\n" for name in top).encode("ascii")
    records[f"{STDLIB}/VERSIONS"] = {"role": "typeshed-versions"}
    extensions = sources.read("vendor", MYPY_EXTENSIONS)
    files[f"typeshed/{MYPY_EXTENSIONS}"] = extensions
    records[f"typeshed/{MYPY_EXTENSIONS}"] = _support_record(
        sources, Located("vendor", MYPY_EXTENSIONS, False), extensions, "copied")
    for name in LICENSE_FILES:
        data = sources.read("vendor", name)
        path = f"{LICENSE_DIRECTORY}/{name}"
        files[path] = data
        records[path] = {"role": "support-license-notice", "origin": sources.label(), "source": name,
                         "source_sha256": _digest(data)}

    merges = {}
    for module, output in sorted(kept.items()):
        if output.merge is None:
            continue
        entry: dict = {"template": output.source.located.path if output.source else None}
        for key, value in sorted(output.merge.report.items()):
            if isinstance(value, list):
                entry[key] = value if key in _LISTED else len(value)
        merges[module] = entry
    consumed = []
    for (origin, path), digest in sorted(sources.consumed.items()):
        entry = {"origin": origin, "path": path, "sha256": digest}
        entry.update(_changes(sources, origin, path))
        consumed.append(entry)
    residue = sorted(sources.provenance["support_residue"], key=lambda item: (item["module"], item["name"]))
    inputs = {"origin": sources.provenance["upstream"], "files": consumed}
    receipt = {"layout": "typeshed-standalone", "stdlib": STDLIB, "modules": sorted(kept), "merges": merges,
               "support_residue": residue, "unresolved_generated_imports": unresolved}
    return Pack(files, records, inputs, receipt)


def _changes(sources: Sources, origin: str, path: str) -> dict:
    """The provenance of a vendored file this tree changed: its pristine sha256 and the trim summary."""
    if origin != "vendor":
        return {}
    change = sources.provenance["files"].get(path)
    return {"pristine_sha256": change["pristine_sha256"], "trim": change["trim"]} if change else {}


def _support_record(sources: Sources, located: Located, data: bytes, treatment: str) -> dict:
    record = {"role": "support", "treatment": treatment}
    if located.origin == "vendor":
        record.update(origin=sources.label(), source=located.path, source_sha256=_digest(data))
        record.update(_changes(sources, "vendor", located.path))
    else:
        attribution = sources.provenance["overlay"].get(located.path)
        if not isinstance(attribution, dict) or set(attribution) != {"derived_from", "licence"}:
            raise PackError(f"overlay file {located.path} has no attribution in the provenance file")
        record.update(origin=OVERLAY_ORIGIN, source=f"overlay/{located.path}", source_sha256=_digest(data), **attribution)
    return record


def _record(module: str, output: Output, sources: Sources) -> dict:
    if output.treatment == "generated":
        return {"role": "runtime-module", "treatment": "generated"}
    if output.treatment == "package":
        return {"role": "support", "treatment": "package"}
    assert output.source is not None
    if output.treatment == "merged":
        located = output.source.located
        record = {"role": "runtime-builtins" if module == "builtins" else "runtime-module", "treatment": "merged",
                  "template": located.path, "template_sha256": _digest(output.source.data)}
        record.update(_changes(sources, located.origin, located.path))
        return record
    return _support_record(sources, output.source.located, output.source.data, output.treatment)
