"""Render build-derived declarations without supplying a host runtime surface."""

from __future__ import annotations

import hashlib
import keyword
import unicodedata
from typing import Any

from .vocabulary import ATOM_KINDS, canonical_contract, canonical_default, canonical_json


_RESOLVED = {"resolved", "bounded-resolved", "source-resolved", "selected-source"}
_DYNAMIC = {
    "dynamic-provider", "potential-dynamic-provider-unassessed",
    "registered-delegation-selection-unassessed", "registered-delegation",
    "extensible-contributor-gap", "dynamic", "open",
}
# Policy and provider kinds that record only that the declared names could not be proven complete: no handler,
# delegation or source `__getattr__` was found that supplies names. Every other kind, an absent kind and a kind
# not listed here counts as a found provider. `potential-dynamic-provider-unassessed` is not listed: a module
# `__getattr__` table entry is a found provider unless its feature gate is recorded as disabled (see `_found_provider`).
_UNPROVEN = {"closed-table-with-coverage-gap", "extensible-contributor-gap"}
OPEN_MODULES = ("all", "proven")


def _identifier(value: Any) -> bool:
    return (isinstance(value, str) and value.isidentifier() and not keyword.iskeyword(value) and
            unicodedata.normalize("NFKC", value) == value)


def _qualified(value: Any) -> bool:
    return isinstance(value, str) and all(_identifier(part) for part in value.split("."))


def _status(value: Any) -> str:
    return value.get("status", "unresolved") if isinstance(value, dict) else str(value)


_END = object()


def _distinct(group: list[dict]) -> list[dict]:
    """The symbols of ``group`` that differ in canonical JSON, one per distinct value, in order of first
    occurrence and each the last of its equal ones. A group of one symbol has no other to differ from,
    so its canonical JSON is not computed."""
    if len(group) == 1:
        return group
    return list({canonical_json(symbol): symbol for symbol in group}.values())


def _reasons(record: dict) -> list[str]:
    """The reasons ``record`` gives for being unresolved, each once and in text order: its own list of
    unresolved reasons and the reasons its resolution and the unresolved fields of its resolution give."""
    reasons = list(record.get("unresolved", []))
    resolution = record.get("resolution")
    if not isinstance(resolution, dict):
        resolution = {}
    reasons.extend(resolution.get("reasons", []))
    for field in resolution.values():
        if isinstance(field, dict) and _status(field) not in _RESOLVED:
            reasons.extend(field.get("reasons", []))
            if "reason" in field:
                reasons.append(field["reason"])
    return sorted(set(str(reason).replace("\n", "\\n").replace("\r", "\\r") for reason in reasons))


def _unresolved_lines(record: dict, indent: str) -> list[str]:
    return [f"{indent}# unresolved: {reason}" for reason in _reasons(record)]


def _available(symbol: dict) -> bool:
    return _status(symbol.get("resolution", {}).get("availability")) in _RESOLVED


def _providers(policy: dict) -> list:
    """The ``dynamic_providers`` records of ``policy``; a value that is not a list is one record that no kind describes."""
    providers = policy.get("dynamic_providers") or []
    return providers if isinstance(providers, list) else [None]


def _dynamic(policy: dict) -> bool:
    return policy.get("kind") in _DYNAMIC or bool(_providers(policy))


def _feature_disabled(policy: dict) -> bool:
    """Whether ``policy`` records the module ``__getattr__`` feature as disabled, so that its table entry dispatches nothing."""
    feature = policy.get("getattr_feature")
    value = feature.get("value") if isinstance(feature, dict) else None
    return type(value) is int and value == 0


def _found_provider(policy: dict) -> bool:
    """Whether some dynamic evidence of ``policy`` (its own kind when dynamic, and the kind of each of its dynamic
    providers) is not just an unproven table. A kind this module does not know counts as a found provider, and
    ``potential-dynamic-provider-unassessed`` counts as one unless the policy records its feature gate as 0."""
    kinds = [policy.get("kind")] if policy.get("kind") in _DYNAMIC else []
    kinds.extend(provider.get("kind") if isinstance(provider, dict) else None for provider in _providers(policy))
    disabled = _feature_disabled(policy)
    return any(kind not in _UNPROVEN and not (disabled and kind == "potential-dynamic-provider-unassessed") for kind in kinds)


def _provider_reasons(policy: dict) -> list[str]:
    """The reasons ``policy`` gives for its names not being enumerated: its unresolved reasons and the reasons its
    dynamic providers state. A policy that states none gets a fixed reason."""
    unresolved = policy.get("unresolved") or []
    reasons = list(unresolved) if isinstance(unresolved, list) else [unresolved]
    reasons.extend(provider["reason"] for provider in _providers(policy) if isinstance(provider, dict) and "reason" in provider)
    reasons = sorted({str(reason).replace("\n", "\\n").replace("\r", "\\r") for reason in reasons})
    return reasons or ["attribute provider names are not enumerated"]


def _opens(policy: dict, names: set[str], open_modules: str) -> bool:
    """Whether a module or class with ``policy`` gets a catch-all ``__getattr__``. ``all`` opens every one with
    dynamic evidence; ``proven`` opens only those with a found provider. One whose ``names`` declare
    ``__getattr__`` already has it."""
    return _dynamic(policy) and (open_modules == "all" or _found_provider(policy)) and "__getattr__" not in names


def _policy_lines(policy: dict, indent: str, *, module: bool, names: set[str], any_name: str, open_modules: str) -> list[str]:
    """The catch-all ``__getattr__`` of a module or class, with the reasons for it."""
    lines = []
    if _opens(policy, names, open_modules):
        args = f"name: {any_name}" if module else f"self, name: {any_name}"
        lines.extend(f"{indent}# unresolved: {reason}" for reason in _provider_reasons(policy))
        lines.append(f"{indent}def __getattr__({args}) -> {any_name}: ...")
    return lines


def _table_row(symbol: dict) -> bool:
    """Whether ``symbol`` is a row of a static module or class attribute table, as opposed to a framework
    attribute (``__class__``, ``__dict__``) that a handler tests for."""
    identity = symbol.get("identity")
    return "dictionary_window" in symbol or (isinstance(identity, dict) and "entry_index" in identity)


def _class_names(definition: dict) -> set[str]:
    return {m.get("name") for m in definition.get("members", [])
            if _available(m) and m.get("attribute_scope") != "class"}


def _closed(policy: dict, names: set[str], open_modules: str) -> bool:
    """Whether a module or class with ``policy`` is declared by its named exports alone: it has neither a
    catch-all it gets nor one it declares."""
    return "__getattr__" not in names and not _opens(policy, names, open_modules)


class _Renderer:
    def __init__(self, inventory: dict, module: dict, types: dict[str, dict], open_modules: str):
        self.inventory = inventory
        self.open_modules = open_modules
        self.module = module
        self.typevars: dict[str, str] = {}
        self.types = types
        self.names: set[str] = set()

        pending = list(reversed(list(module.get("exports", []))))
        while pending:
            symbol = pending.pop()
            self.names.add(str(symbol.get("name")))
            pending.extend(reversed(list(symbol.get("members", []))))
        self.any_name = self.support_name("_mpy_Any")
        self.typevar_name = self.support_name("_mpy_TypeVar")

    def support_name(self, candidate: str) -> str:
        while candidate in self.names:
            candidate += "_"
        self.names.add(candidate)
        return candidate

    def representation_gap(self, record: dict, reason: str) -> None:
        resolution = record.setdefault("resolution", {})
        declaration = resolution.setdefault("declaration", {"status": "unresolved", "reasons": []})
        declaration["status"] = "unresolved"
        declaration["reasons"] = sorted(set(declaration.get("reasons", []) + [reason]))

    def contract(self, contract: Any, relations: dict[str, str] | None = None) -> str:
        # Union alternatives render depth first through an explicit stack of
        # (pending alternatives, rendered parts) frames.
        text, alternatives = self._contract_step(contract, relations)
        if alternatives is None:
            return text
        stack = [(alternatives, [])]
        while True:
            pending, parts = stack[-1]
            child = next(pending, _END)
            if child is not _END:
                text, alternatives = self._contract_step(child, relations)
                if alternatives is None:
                    parts.append(text)
                else:
                    stack.append((alternatives, []))
                continue
            stack.pop()
            parts = sorted(set(parts))
            text = " | ".join(parts) if parts and self.any_name not in parts else self.any_name
            if not stack:
                return text
            stack[-1][1].append(text)

    def _contract_step(self, contract: Any, relations: dict[str, str] | None) -> tuple[str | None, Any]:
        """Return (annotation, None), or (None, an iterator over the alternatives)
        for a union whose alternatives still need rendering."""
        if not isinstance(contract, dict):
            return self.any_name, None
        if "resolution" in contract and _status(contract["resolution"]) not in _RESOLVED:
            return self.any_name, None
        contract = canonical_contract(contract)
        kind = contract.get("kind")
        if kind in ("argument", "argument-table"):
            name = contract.get("exported_argument")
            return (relations or {}).get(name, self.any_name), None
        if kind in ("integer-conversion-capability", "readable-buffer-capability", "writable-buffer-capability", "tuple-or-list/exact-length"):
            self.representation_gap(self.module, f"{kind} is source-qualified; target native acceptance has no qualified declaration representation")
            return self.any_name, None
        if kind == "tuple" and "elements" in contract:
            self.representation_gap(self.module, "tuple element contracts are retained in inventory; target builtin type parameters are unqualified")
        if kind == "union":
            return None, iter(contract.get("alternatives", []))
        if kind not in ATOM_KINDS:
            return self.any_name, None
        atom = kind
        nominal = atom
        if nominal in self.names and self.module.get("name") != "builtins":
            self.representation_gap(self.module, f"annotation type {nominal} is shadowed by a runtime declaration")
            return self.any_name, None
        return atom, None

    def default(self, parameter: dict) -> str:
        contract = parameter.get("default_contract", {})
        if isinstance(contract, dict) and _status(contract.get("resolution", "resolved")) in _RESOLVED:
            contract = canonical_default(contract)
            kind = contract.get("kind")
            if kind == "None":
                return "None"
            if kind == "bool" and type(contract.get("value")) is bool:
                return repr(contract["value"])
            value = contract.get("value", contract.get("literal_value"))
            if kind == "int" and type(value) is int:
                return repr(value)
            if kind in ("str", "bytes") and isinstance(value, (str, bytes)):
                return repr(value)
        return "..."

    def relations(self, symbol: dict, parameters: list[dict]) -> dict[str, str]:
        semantics = symbol.get("semantics", {})
        if _status(semantics.get("return_resolution")) not in _RESOLVED:
            return {}
        required = {parameter["name"]: parameter for parameter in parameters
                    if parameter.get("kind") not in ("var_positional", "var_keyword") and parameter.get("required") is True}
        names = set(required)
        found: set[str] = set()

        pending = list(reversed(list(semantics.get("returns", []))))
        while pending:
            contract = pending.pop()
            if contract.get("kind") == "argument" or contract.get("kind") == "argument-table" and contract.get("storage_field") == "u_obj":
                name = contract.get("exported_argument")
                parameter = required.get(name)
                broad = parameter and (_status(parameter.get("type_resolution")) in _RESOLVED
                                       and (parameter.get("type_contract") or {}).get("kind") == "object")
                frozen_identity = contract.get("identity") == {"kind": "unchanged-parameter", "domain": "python-object"}
                if name in names and (broad or frozen_identity):
                    found.add(name)
            if name := contract.get("exported_argument"):
                if name not in names and any(p["name"] == name and p.get("required") is not True for p in parameters):
                    self.representation_gap(symbol, "optional passthrough parameter/default relation is not expressible by an unconstrained TypeVar")
            pending.extend(reversed(list(contract.get("alternatives", []))))
        result = {}
        for parameter in parameters:
            if parameter["name"] not in found and _status(parameter.get("type_resolution")) in _RESOLVED:
                annotation = self.contract(parameter.get("type_contract"))
                if annotation != self.any_name:
                    result[parameter["name"]] = annotation
        for name in sorted(found):
            key = canonical_json([self.module.get("identity"), symbol.get("identity"), name])
            variable = self.support_name("_T_" + hashlib.sha256(key.encode()).hexdigest()[:16])
            self.typevars[variable] = name
            result[name] = variable
        return result

    def parameter_annotation(self, parameter: dict, relations: dict[str, str]) -> str:
        if parameter["name"] in relations:
            return relations[parameter["name"]]
        contract = parameter.get("type_contract", {})
        if _status(parameter.get("type_resolution")) in _RESOLVED:
            return self.contract(contract)
        return self.any_name

    def signature(self, symbol: dict, in_class: bool) -> tuple[str, dict[str, str]]:
        shape = symbol.get("callshape") or {}
        descriptor = shape.get("descriptor", "function")
        implicit = shape.get("implicit_argument_offset")
        bound = in_class and descriptor != "staticmethod" and implicit != 0
        receiver = shape.get("receiver_name") or ("cls" if descriptor == "classmethod" else "self")
        parameters = shape.get("parameters")
        parameter_names = {p.get("name") for p in parameters or []}
        while receiver in parameter_names:
            receiver = "_" + receiver
        prefix = [receiver] if bound else []
        receiver_contract = symbol.get("receiver_contract")
        if bound and isinstance(receiver_contract, dict):
            annotation = self.any_name
            native_type = receiver_contract.get("native_type")
            if (_status(receiver_contract.get("nominal_authority_resolution")) in _RESOLVED
                    and native_type in ("str", "bytes", "bytearray")):
                annotation = self.contract({"kind": native_type})
            prefix = [f"{receiver}: {annotation}"]
        gaps: list[str] = []
        if isinstance(parameters, list) and _status(shape.get("parameters_resolution")) in _RESOLVED:
            if len(parameter_names) != len(parameters):
                gaps.append("callshape: repeated runtime parameter names cannot be represented by one Python signature")
            optional_positional = False
            keyword_only_seen = False
            for parameter in parameters:
                if parameter.get("kind") == "keyword_only":
                    keyword_only_seen = True
                    continue
                if parameter.get("kind") not in ("positional_only", "positional_or_keyword"):
                    continue
                if keyword_only_seen:
                    gaps.append("callshape: positional parameter follows a keyword-only runtime parameter")
                    break
                if parameter.get("required", True):
                    if optional_positional:
                        gaps.append("callshape: required positional parameter follows an optional positional parameter")
                        break
                else:
                    optional_positional = True
            if gaps:
                for reason in gaps:
                    self.representation_gap(symbol, reason)
                fallback = [f"*args: {self.any_name}"]
                if shape.get("keywords") is not False:
                    fallback.append(f"**kwargs: {self.any_name}")
                return ", ".join(prefix + fallback), {}
            if any(not _identifier(p.get("name")) for p in parameters):
                gaps.append("callshape: a runtime keyword name is not a Python declaration identifier")
                self.representation_gap(symbol, gaps[-1])
                return ", ".join(prefix + [f"*args: {self.any_name}", f"**kwargs: {self.any_name}"]), {}
            relations = self.relations(symbol, parameters)
            parts = list(prefix)
            positional_only = [p for p in parameters if p.get("kind") == "positional_only"]
            if prefix and shape.get("receiver_kind") == "positional_only" and not positional_only:
                parts.append("/")
            has_star = False
            for index, parameter in enumerate(parameters):
                kind = parameter.get("kind", "positional_or_keyword")
                name = parameter["name"]
                annotation = self.parameter_annotation(parameter, relations)
                if kind == "keyword_only" and not has_star:
                    parts.append("*")
                    has_star = True
                if kind == "var_positional":
                    name = "*" + name
                    has_star = True
                elif kind == "var_keyword":
                    name = "**" + name
                elif kind not in ("positional_only", "positional_or_keyword", "keyword_only"):
                    raise ValueError(f"unsupported normalized parameter kind: {kind}")
                text = f"{name}: {annotation}"
                if kind not in ("var_positional", "var_keyword") and not parameter.get("required", True):
                    text += " = " + self.default(parameter)
                parts.append(text)
                if positional_only and parameter is positional_only[-1]:
                    parts.append("/")
            return ", ".join(parts), relations
        minimum, maximum = shape.get("minimum"), shape.get("maximum")
        valid_bounds = (type(minimum) is int and minimum >= 0 and
                        (maximum is None or type(maximum) is int and maximum >= minimum))
        if not valid_bounds or type(implicit) is not int or implicit < 0 or minimum < implicit:
            gaps.append("callshape: public wrapper bounds or binding offset unresolved")
            self.representation_gap(symbol, gaps[-1])
            variadic = [f"*args: {self.any_name}"]
            if shape.get("keywords") is not False:
                variadic.append(f"**kwargs: {self.any_name}")
            return ", ".join(prefix + variadic), {}
        minimum -= implicit
        maximum = None if maximum is None else maximum - implicit
        if shape.get("keywords") is not False:
            gaps.append("callshape: keyword parser names/defaults unresolved; wrapper bounds retained in inventory")
            self.representation_gap(symbol, gaps[-1])
            return ", ".join(prefix + [f"*args: {self.any_name}", f"**kwargs: {self.any_name}"]), {}
        # A bounded wrapper is positional-only even when its C arguments have names.
        parts = list(prefix)
        count = minimum if maximum is None else maximum
        if count > 64:
            gaps.append("callshape: finite large positional maximum cannot be expressed by a variadic pyi signature")
            self.representation_gap(symbol, gaps[-1])
            parts.extend(f"arg{index}: {self.any_name}" for index in range(minimum if minimum <= 64 else 0))
            if parts:
                parts.append("/")
            parts.append(f"*args: {self.any_name}")
            return ", ".join(parts), {}
        for index in range(count):
            text = f"arg{index}: {self.any_name}"
            if index >= minimum:
                text += " = ..."
            parts.append(text)
        if parts:
            parts.append("/")
        if maximum is None:
            parts.append(f"*args: {self.any_name}")
        return ", ".join(parts), {}

    def result(self, symbol: dict, relations: dict[str, str]) -> str:
        semantics = symbol.get("semantics") or {}
        if _status(semantics.get("return_resolution")) not in _RESOLVED:
            return self.any_name
        if semantics.get("return_unresolved") or semantics.get("frontend_unresolved"):
            return self.any_name
        returns = semantics.get("returns", [])
        parts = sorted(set(self.contract(item, relations) for item in returns))
        return " | ".join(parts) if parts and self.any_name not in parts else self.any_name

    def symbol(self, symbol: dict, indent: str = "", *, in_class: bool = False, closed: bool = False) -> tuple[list[str], dict | None]:
        """Return the declaration lines and, for a class, its merged definition.

        A class's lines end at its header; ``symbols`` renders the definition's
        members and completes the class with ``class_lines``.
        """
        name = symbol.get("name")
        if not _identifier(name):
            self.representation_gap(symbol, "runtime export name is not a Python declaration identifier")
            return [f"{indent}# unresolved: runtime export name {canonical_json(name)} is not a Python declaration identifier"], None
        lines = _unresolved_lines(symbol, indent)
        kind = symbol.get("kind", "unknown")
        if in_class and symbol.get("attribute_scope") == "class":
            self.representation_gap(symbol, "metaclass-only attribute lookup cannot be expressed as an ordinary instance field")
            return [], None
        if not _available(symbol):
            self.representation_gap(symbol, "runtime name availability is unqualified")
            if closed and _table_row(symbol):
                # A closed container accepts exactly its declared names, so a table row that is present in the
                # selected build but whose declaration cannot be qualified is declared with an unknown type.
                return _unresolved_lines(symbol, indent) + [f"{indent}{name}: {self.any_name}"], None
            return [], None
        alias = symbol.get("alias_binding") or {}
        if _status(alias) in _RESOLVED and _identifier(alias.get("target_name")):
            return lines + [f"{indent}{name} = {alias['target_name']}"], None
        binding = symbol.get("import_binding")
        if binding and not in_class:
            imported = binding.get("module", "")
            imported_name = binding.get("name")
            level = binding.get("level", 0)
            if (not imported or _qualified(imported)) and type(level) is int and level >= 0:
                if imported_name and _identifier(imported_name):
                    return lines + [f"from {'.' * level}{imported} import {imported_name} as {name}"], None
                if imported and not level:
                    return lines + [f"import {imported} as {name}"], None
            return lines + [f"{indent}# unresolved: import binding is unresolved", f"{indent}{name}: {self.any_name}"], None
        if kind == "function" and (symbol.get("callshape") or {}).get("callable") is not False:
            signature, relations = self.signature(symbol, in_class)
            descriptor = (symbol.get("callshape") or {}).get("descriptor")
            if in_class and descriptor in ("classmethod", "staticmethod"):
                lines.append(f"{indent}@{descriptor}")
            lines.append(f"{indent}def {name}({signature}) -> {self.result(symbol, relations)}: ...")
        elif kind == "class":
            shared = self.types.get(canonical_json(symbol.get("type_identity")), {})
            definition = {**shared, **symbol}
            represented_constructor = any(
                member.get("name") in ("__new__", "__init__") and member.get("kind") == "function" and
                _status(member.get("resolution", {}).get("availability", "resolved")) in _RESOLVED
                for member in definition.get("members", []))
            if definition.get("constructor") and not represented_constructor:
                self.representation_gap(symbol, "class constructor callshape has no source-qualified __new__/__init__ declaration representation")
                reasons = "; ".join(_reasons(definition["constructor"]))
                lines.append(f"{indent}# Constructor declaration gap" + (f": {reasons}" if reasons else ""))
            bases = definition.get("bases", [])
            if any(not _qualified(base) for base in bases):
                raise ValueError(f"unsafe normalized base for {name}: {bases!r}")
            if _status(definition.get("resolution", {}).get("bases", "resolved")) not in _RESOLVED:
                bases = []
            lines.append(f"{indent}class {name}{'(' + ', '.join(bases) + ')' if bases else ''}:")
            return lines, definition
        else:
            lines.append(f"{indent}{name}: {self.contract(symbol.get('value_contract'))}")
        return lines, None

    def class_lines(self, lines: list[str], definition: dict, indent: str, members: list[str]) -> list[str]:
        """Complete a class declaration from ``symbol`` with its rendered member lines."""
        members += _policy_lines(definition.get("attribute_policy", {}), indent + "    ",
                                 module=False, names=_class_names(definition),
                                 any_name=self.any_name, open_modules=self.open_modules)
        lines.extend(members + [indent + "    ..."])
        return lines

    def symbols(self, symbols: list[dict], indent: str = "", *, in_class: bool = False, closed: bool = False) -> list[str]:
        # Class members render depth first through an explicit stack of member
        # list frames; a frame with a pending class completes it once the frame
        # for that class's members is exhausted.
        frames = [self._symbols_frame(symbols, indent, in_class, closed)]
        while True:
            frame = frames[-1]
            entry = next(frame["groups"], None)
            if entry is None:
                frames.pop()
                if not frames:
                    return frame["lines"]
                parent = frames[-1]
                lines, definition = parent["pending"]
                parent["pending"] = None
                parent["lines"].extend(self.class_lines(lines, definition, parent["indent"], frame["lines"]))
                continue
            lines, definition = self._group_lines(frame, *entry)
            if definition is None:
                frame["lines"].extend(lines)
            else:
                frame["pending"] = (lines, definition)
                frames.append(self._symbols_frame(
                    definition.get("members", []), frame["indent"] + "    ", True,
                    _closed(definition.get("attribute_policy", {}), _class_names(definition), self.open_modules)))

    def _symbols_frame(self, symbols: list[dict], indent: str, in_class: bool, closed: bool) -> dict:
        grouped: dict[str, list[dict]] = {}
        for symbol in symbols:
            grouped.setdefault(str(symbol.get("name")), []).append(symbol)
        return {"grouped": grouped, "groups": iter(sorted(grouped.items())), "indent": indent,
                "in_class": in_class, "closed": closed, "lines": [], "pending": None}

    def _group_lines(self, frame: dict, name: str, group: list[dict]) -> tuple[list[str], dict | None]:
        """Return the lines for one name of a member list and, for a class, its definition."""
        indent, in_class, grouped = frame["indent"], frame["in_class"], frame["grouped"]
        unique = _distinct(group)
        if len(unique) > 1:
            for candidate in group:
                self.representation_gap(candidate, "conflicting export definitions cannot be expressed as one qualified declaration")
            if _identifier(name) and any((_available(candidate) or (frame["closed"] and _table_row(candidate))) and
                                         (not in_class or candidate.get("attribute_scope") != "class") for candidate in group):
                return [f"{indent}# unresolved: conflicting export definitions cannot be expressed as one qualified declaration",
                        f"{indent}{name}: {self.any_name}"], None
            return [], None
        symbol = unique[0]
        alias = symbol.get("alias_of")
        if (_identifier(alias) and alias != name and alias in grouped and
                "alias_target_identity" not in symbol and any(_available(target) for target in grouped[alias])):
            symbol = {**symbol, "alias_binding": {"target_name": alias, "status": "resolved"}}
        return self.symbol(symbol, indent, in_class=in_class, closed=frame["closed"])

    def render(self) -> str:
        names = {s.get("name") for s in self.module.get("exports", []) if _available(s)}
        policy = self.module.get("attribute_policy", {})
        body = self.symbols(self.module.get("exports", []), closed=_closed(policy, names, self.open_modules))
        body += _policy_lines(policy, "", module=True, names=names, any_name=self.any_name, open_modules=self.open_modules)
        headers = ["# Generated from the selected MicroPython build; not a CPython runtime overlay."]
        headers.extend(_unresolved_lines(self.module, ""))
        headers.append(f"from typing import Any as {self.any_name}" +
                       (f", TypeVar as {self.typevar_name}" if self.typevars else ""))
        headers.extend(f'{name} = {self.typevar_name}("{name}")' for name in sorted(self.typevars))
        return "\n".join(headers + [""] + body) + "\n"


def render_declarations(inventory: dict, open_modules: str = "proven") -> dict[str, str]:
    """Return deterministic safe relative paths and UTF-8 declaration content.

    Annotation imports are support syntax, not assertions that the target exports
    the corresponding CPython modules. Gap comments are one line per reason of an
    unresolved declaration; the evidence behind them stays in the inventory. A
    name without a qualified availability has no declaration and no comment, except a
    static table row of a module or class that has no catch-all: it is
    declared ``NAME: Any`` after its reasons, so the closed declarations still accept it.
    A module or class with dynamic attribute evidence gets a catch-all ``__getattr__``
    preceded by its reasons. ``open_modules="all"`` gives one to every such module and
    class. ``"proven"`` gives one only where a provider was found; a module or class whose
    evidence only records an unproven table is left to its named exports, and evidence of
    a kind not known to be unproven counts as a found provider. A
    ``potential-dynamic-provider-unassessed`` policy counts as one unless its recorded
    ``getattr_feature`` value is 0.
    The input inventory gains per-symbol ``resolution.declaration`` gaps where
    pyi syntax cannot represent a qualified runtime fact. Serialize it afterwards.
    """
    if open_modules not in OPEN_MODULES:
        raise ValueError(f"open_modules must be one of {', '.join(OPEN_MODULES)}: {open_modules!r}")
    if inventory.get("schema_version") != 1:
        raise ValueError("declaration rendering requires inventory schema_version=1")
    # The shared types by canonical identity; rendering adds gaps to symbol and member resolutions, never to a type's identity.
    types = {canonical_json(item.get("identity")): item for item in inventory.get("types", [])}
    result: dict[str, str] = {}
    for module in sorted(inventory.get("modules", []), key=lambda m: (m.get("name", ""), canonical_json(m.get("identity")))):
        name = module.get("name")
        if not _qualified(name):
            raise ValueError(f"module has no safe Python import path: {name!r}")
        provenance = module.get("provenance") or {}
        package = module.get("is_package", provenance.get("is_package", False))
        if name == "builtins":
            path = "typeshed/stdlib/builtins.pyi"
        else:
            path = "modules/" + name.replace(".", "/") + ("/__init__.pyi" if package else ".pyi")
        if path in result:
            raise ValueError(f"multiple selected module identities would overwrite {path}")
        _Renderer(inventory, module, types, open_modules).render()
        result[path] = _Renderer(inventory, module, types, open_modules).render()
    paths = set(result)
    for path in paths:
        if path.endswith(".pyi") and not path.endswith("/__init__.pyi"):
            package_path = path[:-4] + "/__init__.pyi"
            if package_path in paths:
                raise ValueError(f"module/package declaration collision: {path}")
    return dict(sorted(result.items()))
