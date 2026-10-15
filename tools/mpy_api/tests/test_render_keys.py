"""Declaration rendering computes a canonical grouping key only where one symbol can differ from another,
and the shared type table once per rendering; the output is the one the always-keyed rendering gives."""
import copy
import random

import pytest

from mpy_api import declarations
from mpy_api.declarations import _distinct, render_declarations
from mpy_api.vocabulary import canonical_json
from test_declarations_frozen import function, inventory, module


def always_keyed(group):
    return list({canonical_json(symbol): symbol for symbol in group}.values())


def test_distinct_equals_the_keyed_comprehension_on_generated_groups():
    values = [1, 1.0, True, "1", None, [1], [1.0], {"a": 1, "b": 2}, {"b": 2, "a": 1}, {"a": [True]}]
    rng = random.Random(7)
    for _ in range(300):
        group = [{"name": "n", "value": rng.choice(values), "extra": rng.choice(values)} for _ in range(rng.randint(1, 5))]
        assert always_keyed(group) == _distinct(group)
        # Same objects, in the same order: the survivors are the last symbol of each key.
        assert [id(s) for s in always_keyed(group)] == [id(s) for s in _distinct(group)]


def test_a_singleton_group_is_returned_without_its_key():
    symbol = {"name": "n", "value": float("nan")}
    group = [symbol]
    assert _distinct(group) is group


def conflicting_inventory():
    shared = {"identity": {"kind": "type", "name": "Shared"}, "members": [function("method")], "bases": [],
              "resolution": {"availability": {"status": "resolved"}}}
    other = {"identity": {"kind": "type", "name": "Other"}, "members": [], "bases": []}
    klass = {"name": "C", "kind": "class", "type_identity": shared["identity"], "members": [function("own")],
             "resolution": {"availability": {"status": "resolved"}}}
    plain = function("same")
    modules = [module("builtins", [function("same"), function("same"), function("two", 1, 2), function("two", 1, 3)]),
               module("a.b", [klass, copy.deepcopy(plain), function("alias_source")], is_package=True),
               module("c", [{**function("target"), "alias_of": None}, {**function("name"), "alias_of": "target"}])]
    data = inventory(*modules)
    data["types"] = [shared, other]
    return data


def test_rendering_equals_the_always_keyed_rendering(monkeypatch):
    expected_input, actual_input = conflicting_inventory(), conflicting_inventory()
    monkeypatch.setattr(declarations, "_distinct", always_keyed)
    expected = render_declarations(expected_input)
    monkeypatch.undo()
    assert render_declarations(actual_input) == expected
    # Both renderings leave the same gaps in the inventory.
    assert canonical_json(actual_input) == canonical_json(expected_input)
    assert any("conflicting export definitions" in text for text in expected.values())


def test_canonical_json_is_not_computed_for_singleton_symbols_or_per_module_types(monkeypatch):
    data = conflicting_inventory()
    seen = []
    real = declarations.canonical_json
    monkeypatch.setattr(declarations, "canonical_json", lambda value: seen.append(value) or real(value))
    render_declarations(data)
    names = [value["name"] for value in seen if isinstance(value, dict) and "callshape" in value]
    # Only the groups of two or more symbols are keyed, in each of the two passes over their module.
    assert sorted(set(names)) == ["same", "two"] and len(names) == 2 * 4
    identities = [value for value in seen if isinstance(value, dict) and value.get("kind") == "type"]
    # One per type in the table, built once, and one per lookup of a class symbol's type, in each of the two passes.
    assert len(identities) == len(data["types"]) + 2


@pytest.mark.parametrize("name", ["builtins", "a.b", "c"])
def test_a_module_renders_the_same_alone_and_with_the_others(name):
    together = render_declarations(conflicting_inventory())
    data = conflicting_inventory()
    data["modules"] = [m for m in data["modules"] if m["name"] == name]
    alone = render_declarations(data)
    assert alone.items() <= together.items()
