"""Exact declaration kinds shared by rendering and reference comparison."""

from __future__ import annotations

import json


ATOM_KINDS = frozenset({"None", "bool", "int", "str", "bytes", "bytearray", "float", "complex",
                        "object", "list", "dict", "set", "tuple"})
_KIND_ALIASES = {"none": "None"}
_KIND_UNIONS = {"str-or-bytes": ("str", "bytes")}
_DEFAULT_KINDS = {"bool-storage": "bool", "integer-storage-literal": "int"}


def canonical_contract(contract):
    kind = contract.get("kind")
    if kind in _KIND_ALIASES:
        return {**contract, "kind": _KIND_ALIASES[kind]}
    if kind in _KIND_UNIONS:
        return {**contract, "kind": "union",
                "alternatives": [{"kind": child} for child in _KIND_UNIONS[kind]]}
    return contract


def canonical_default(contract):
    contract = canonical_contract(contract)
    kind = contract.get("kind")
    return {**contract, "kind": _DEFAULT_KINDS[kind]} if kind in _DEFAULT_KINDS else contract


def union_descriptor(descriptors):
    atoms = {}
    for descriptor in descriptors:
        children = descriptor["alternatives"] if descriptor.get("kind") == "union" else [descriptor]
        for child in children:
            key = json.dumps(child, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
            atoms[key] = child
    values = [atoms[key] for key in sorted(atoms)]
    return values[0] if len(values) == 1 else {"kind": "union", "alternatives": values}
