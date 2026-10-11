"""Render build-derived declarations without supplying a host runtime surface."""

from __future__ import annotations

import hashlib
import json
import keyword
import unicodedata
from typing import Any

from .vocabulary import ATOM_KINDS, canonical_contract, canonical_default


_RESOLVED = {"resolved", "bounded-resolved", "source-resolved", "selected-source"}
_DYNAMIC = {
    "dynamic-provider", "potential-dynamic-provider-unassessed",
    "registered-delegation-selection-unassessed", "registered-delegation",
    "extensible-contributor-gap", "dynamic", "open",
}


def _identifier(value: Any) -> bool:
    return (isinstance(value, str) and value.isidentifier() and not keyword.iskeyword(value) and
            unicodedata.normalize("NFKC", value) == value)


def _qualified(value: Any) -> bool:
    return isinstance(value, str) and all(_identifier(part) for part in value.split("."))


def _status(value: Any) -> str:
    return value.get("status", "unresolved") if isinstance(value, dict) else str(value)


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _comment(value: Any) -> str:
    return _stable(value).replace("\n", " ").replace("\r", " ")


def _gaps(record: dict) -> list[str]:
    gaps = list(record.get("unresolved", []))
    for field, resolution in record.get("resolution", {}).items():
        if _status(resolution) not in _RESOLVED:
            gaps.append(f"{field}: {_comment(resolution)}")
    return sorted(set(str(gap).replace("\n", "\\n").replace("\r", "\\r") for gap in gaps))


def _available(symbol: dict) -> bool:
    return _status(symbol.get("resolution", {}).get("availability")) in _RESOLVED


def _policy_lines(policy: dict, indent: str, *, module: bool, names: set[str], any_name: str) -> list[str]:
    lines = []
    if policy:
        lines.append(f"{indent}# Attribute coverage: {_comment(policy)}")
    actual_dynamic = policy.get("kind") in _DYNAMIC or bool(policy.get("dynamic_providers"))
    if actual_dynamic and "__getattr__" not in names:
        args = f"name: {any_name}" if module else f"self, name: {any_name}"
        lines.append(f"{indent}def __getattr__({args}) -> {any_name}: ...")
    return lines


class _Renderer:
    def __init__(self, inventory: dict, module: dict):
        self.inventory = inventory
        self.module = module
        self.typevars: dict[str, str] = {}
        self.types = {_stable(item.get("identity")): item for item in inventory.get("types", [])}
        self.names: set[str] = set()

        def visit(symbols: list[dict]) -> None:
            for symbol in symbols:
                self.names.add(str(symbol.get("name")))
                visit(symbol.get("members", []))

        visit(module.get("exports", []))
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
        if not isinstance(contract, dict):
            return self.any_name
        if "resolution" in contract and _status(contract["resolution"]) not in _RESOLVED:
            return self.any_name
        contract = canonical_contract(contract)
        kind = contract.get("kind")
        if kind in ("argument", "argument-table"):
            name = contract.get("exported_argument")
            return (relations or {}).get(name, self.any_name)
        if kind in ("integer-conversion-capability", "readable-buffer-capability", "writable-buffer-capability", "tuple-or-list/exact-length"):
            self.representation_gap(self.module, f"{kind} is source-qualified; target native acceptance has no qualified declaration representation")
            return self.any_name
        if kind == "tuple" and "elements" in contract:
            self.representation_gap(self.module, "tuple element contracts are retained in inventory; target builtin type parameters are unqualified")
        if kind == "union":
            parts = sorted(set(self.contract(item, relations) for item in contract.get("alternatives", [])))
            return " | ".join(parts) if parts and self.any_name not in parts else self.any_name
        if kind not in ATOM_KINDS:
            return self.any_name
        atom = kind
        nominal = atom
        if nominal in self.names and self.module.get("name") != "builtins":
            self.representation_gap(self.module, f"annotation type {nominal} is shadowed by a runtime declaration")
            return self.any_name
        return atom

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

        def visit(contract: dict) -> None:
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
            for child in contract.get("alternatives", []):
                visit(child)

        for contract in semantics.get("returns", []):
            visit(contract)
        result = {}
        for parameter in parameters:
            if parameter["name"] not in found and _status(parameter.get("type_resolution")) in _RESOLVED:
                annotation = self.contract(parameter.get("type_contract"))
                if annotation != self.any_name:
                    result[parameter["name"]] = annotation
        for name in sorted(found):
            key = _stable([self.module.get("identity"), symbol.get("identity"), name])
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

    def signature(self, symbol: dict, in_class: bool) -> tuple[str, list[str], dict[str, str]]:
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
                return ", ".join(prefix + fallback), gaps, {}
            if any(not _identifier(p.get("name")) for p in parameters):
                gaps.append("callshape: a runtime keyword name is not a Python declaration identifier")
                self.representation_gap(symbol, gaps[-1])
                return ", ".join(prefix + [f"*args: {self.any_name}", f"**kwargs: {self.any_name}"]), gaps, {}
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
            return ", ".join(parts), gaps, relations
        minimum, maximum = shape.get("minimum"), shape.get("maximum")
        valid_bounds = (type(minimum) is int and minimum >= 0 and
                        (maximum is None or type(maximum) is int and maximum >= minimum))
        if not valid_bounds or type(implicit) is not int or implicit < 0 or minimum < implicit:
            gaps.append("callshape: public wrapper bounds or binding offset unresolved")
            self.representation_gap(symbol, gaps[-1])
            variadic = [f"*args: {self.any_name}"]
            if shape.get("keywords") is not False:
                variadic.append(f"**kwargs: {self.any_name}")
            return ", ".join(prefix + variadic), gaps, {}
        minimum -= implicit
        maximum = None if maximum is None else maximum - implicit
        if shape.get("keywords") is not False:
            gaps.append("callshape: keyword parser names/defaults unresolved; wrapper bounds retained in inventory")
            self.representation_gap(symbol, gaps[-1])
            return ", ".join(prefix + [f"*args: {self.any_name}", f"**kwargs: {self.any_name}"]), gaps, {}
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
            return ", ".join(parts), gaps, {}
        for index in range(count):
            text = f"arg{index}: {self.any_name}"
            if index >= minimum:
                text += " = ..."
            parts.append(text)
        if parts:
            parts.append("/")
        if maximum is None:
            parts.append(f"*args: {self.any_name}")
        return ", ".join(parts), gaps, {}

    def result(self, symbol: dict, relations: dict[str, str]) -> str:
        semantics = symbol.get("semantics") or {}
        if _status(semantics.get("return_resolution")) not in _RESOLVED:
            return self.any_name
        if semantics.get("return_unresolved") or semantics.get("frontend_unresolved"):
            return self.any_name
        returns = semantics.get("returns", [])
        parts = sorted(set(self.contract(item, relations) for item in returns))
        return " | ".join(parts) if parts and self.any_name not in parts else self.any_name

    def symbol(self, symbol: dict, indent: str = "", *, in_class: bool = False) -> list[str]:
        name = symbol.get("name")
        if not _identifier(name):
            self.representation_gap(symbol, "runtime export name is not a Python declaration identifier")
            return [f"{indent}# Unrenderable runtime name: {_comment(symbol)}"]
        lines = [f"{indent}# Resolution gap: {gap}" for gap in _gaps(symbol)]
        kind = symbol.get("kind", "unknown")
        if in_class and symbol.get("attribute_scope") == "class":
            self.representation_gap(symbol, "metaclass-only attribute lookup cannot be expressed as an ordinary instance field")
            return [f"{indent}# Class-only attribute declaration gap: {_comment(symbol)}"]
        if not _available(symbol):
            self.representation_gap(symbol, "runtime name availability is unqualified; no named declaration is emitted")
            return lines + [f"{indent}# Unqualified availability candidate: {_comment(symbol)}"]
        alias = symbol.get("alias_binding") or {}
        if _status(alias) in _RESOLVED and _identifier(alias.get("target_name")):
            return lines + [f"{indent}{name} = {alias['target_name']}"]
        binding = symbol.get("import_binding")
        if binding and not in_class:
            imported = binding.get("module", "")
            imported_name = binding.get("name")
            level = binding.get("level", 0)
            if (not imported or _qualified(imported)) and type(level) is int and level >= 0:
                if imported_name and _identifier(imported_name):
                    return lines + [f"from {'.' * level}{imported} import {imported_name} as {name}"]
                if imported and not level:
                    return lines + [f"import {imported} as {name}"]
            return lines + [f"{indent}# Import binding is unresolved: {_comment(binding)}", f"{indent}{name}: {self.any_name}"]
        if kind == "function" and (symbol.get("callshape") or {}).get("callable") is not False:
            signature, gaps, relations = self.signature(symbol, in_class)
            lines.extend(f"{indent}# Resolution gap: {gap}" for gap in gaps)
            descriptor = (symbol.get("callshape") or {}).get("descriptor")
            if in_class and descriptor in ("classmethod", "staticmethod"):
                lines.append(f"{indent}@{descriptor}")
            lines.append(f"{indent}def {name}({signature}) -> {self.result(symbol, relations)}: ...")
        elif kind == "class":
            shared = self.types.get(_stable(symbol.get("type_identity")), {})
            definition = {**shared, **symbol}
            represented_constructor = any(
                member.get("name") in ("__new__", "__init__") and member.get("kind") == "function" and
                _status(member.get("resolution", {}).get("availability", "resolved")) in _RESOLVED
                for member in definition.get("members", []))
            if definition.get("constructor") and not represented_constructor:
                self.representation_gap(symbol, "class constructor callshape has no source-qualified __new__/__init__ declaration representation")
                lines.append(f"{indent}# Constructor declaration gap: {_comment(definition['constructor'])}")
            bases = definition.get("bases", [])
            if any(not _qualified(base) for base in bases):
                raise ValueError(f"unsafe normalized base for {name}: {bases!r}")
            if _status(definition.get("resolution", {}).get("bases", "resolved")) not in _RESOLVED:
                bases = []
            lines.append(f"{indent}class {name}{'(' + ', '.join(bases) + ')' if bases else ''}:")
            members = self.symbols(definition.get("members", []), indent + "    ", in_class=True)
            members += _policy_lines(definition.get("attribute_policy", {}), indent + "    ",
                                     module=False, names={m.get("name") for m in definition.get("members", [])
                                                          if _available(m) and m.get("attribute_scope") != "class"}, any_name=self.any_name)
            if definition.get("slots"):
                members.append(f"{indent}    # Protocol slot evidence: {_comment(definition['slots'])}")
            lines.extend(members + [indent + "    ..."])
        else:
            lines.append(f"{indent}{name}: {self.contract(symbol.get('value_contract'))}")
        return lines

    def symbols(self, symbols: list[dict], indent: str = "", *, in_class: bool = False) -> list[str]:
        grouped: dict[str, list[dict]] = {}
        for symbol in symbols:
            grouped.setdefault(str(symbol.get("name")), []).append(symbol)
        lines = []
        for name, group in sorted(grouped.items()):
            unique = {_stable(symbol): symbol for symbol in group}
            if len(unique) > 1:
                for candidate in group:
                    self.representation_gap(candidate, "conflicting export definitions cannot be expressed as one qualified declaration")
                lines.append(f"{indent}# Conflicting export definitions: {_comment(group)}")
                if _identifier(name) and any(_available(candidate) and
                                             (not in_class or candidate.get("attribute_scope") != "class") for candidate in group):
                    lines.append(f"{indent}{name}: {self.any_name}")
            else:
                symbol = next(iter(unique.values()))
                alias = symbol.get("alias_of")
                if (_identifier(alias) and alias != name and alias in grouped and
                        "alias_target_identity" not in symbol and any(_available(target) for target in grouped[alias])):
                    symbol = {**symbol, "alias_binding": {"target_name": alias, "status": "resolved"}}
                lines.extend(self.symbol(symbol, indent, in_class=in_class))
        return lines

    def render(self) -> str:
        body = self.symbols(self.module.get("exports", []))
        body += _policy_lines(self.module.get("attribute_policy", {}), "", module=True,
                              names={s.get("name") for s in self.module.get("exports", []) if _available(s)}, any_name=self.any_name)
        headers = ["# Generated from the selected MicroPython build; not a CPython runtime overlay.",
                   f"# Module identity: {_comment(self.module.get('identity'))}"]
        headers.extend(f"# Resolution gap: {gap}" for gap in _gaps(self.module))
        headers.append(f"from typing import Any as {self.any_name}" +
                       (f", TypeVar as {self.typevar_name}" if self.typevars else ""))
        headers.extend(f'{name} = {self.typevar_name}("{name}")' for name in sorted(self.typevars))
        return "\n".join(headers + [""] + body) + "\n"


def render_declarations(inventory: dict) -> dict[str, str]:
    """Return deterministic safe relative paths and UTF-8 declaration content.

    Annotation imports are support syntax, not assertions that the target exports
    the corresponding CPython modules. Coverage comments preserve gaps without
    opening static objects that have no selected dynamic contributor.
    The input inventory gains per-symbol ``resolution.declaration`` gaps where
    pyi syntax cannot represent a qualified runtime fact. Serialize it afterwards.
    """
    if inventory.get("schema_version") != 1:
        raise ValueError("declaration rendering requires inventory schema_version=1")
    result: dict[str, str] = {}
    for module in sorted(inventory.get("modules", []), key=lambda m: (m.get("name", ""), _stable(m.get("identity")))):
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
        _Renderer(inventory, module).render()
        result[path] = _Renderer(inventory, module).render()
    paths = set(result)
    for path in paths:
        if path.endswith(".pyi") and not path.endswith("/__init__.pyi"):
            package_path = path[:-4] + "/__init__.pyi"
            if package_path in paths:
                raise ValueError(f"module/package declaration collision: {path}")
    return dict(sorted(result.items()))
