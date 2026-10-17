"""The link join finds definitions, functions and allocated names through indexes; each answer equals the one
a scan of every definition, function and allocated symbol gives."""
import itertools

from mpy_api import link
from mpy_api.link import coalesce_definitions, join
from test_demand_semantics import extracted, needs_gcc
from test_fact_objects import stored
from test_prefetch import sample_source


def scanned(inputs, ownership):
    """Reference answers by scanning every definition, function and allocated symbol."""
    selected = {o['translation_unit']: o for o in ownership['objects']}
    definitions = [(f, d) for f in inputs for d in coalesce_definitions(f['definitions'])]
    strong = {}
    for obj in ownership['objects']:
        for symbol in obj['symbols']:
            if symbol['kind'].isupper() and symbol['kind'] not in ('U', 'W', 'V'):
                strong.setdefault(symbol['name'], []).append(obj)

    def present(facts, definition):
        owner = selected.get(facts['translation_unit'])
        return bool(owner and any(s['name'] == definition['name'] for s in owner['allocated_symbols']))

    def resolve(name, from_tu):
        local = [(f, d) for f, d in definitions if f['translation_unit'] == from_tu and d['name'] == name]
        if len(local) == 1 and present(*local[0]):
            return local[0], 'same-translation-unit-allocated-object'
        if len(local) > 1:
            return None, 'ambiguous-translation-unit-definition'
        candidates = [(f, d) for f, d in definitions if d['name'] == name and d['linkage'] == 'external' and present(f, d)]
        owners = strong.get(name, [])
        if len(owners) == 1:
            matched = [(f, d) for f, d in candidates if f['translation_unit'] == owners[0]['translation_unit']]
            if len(matched) == 1:
                return matched[0], 'unique-selected-allocated-external'
        return None, 'missing-ambiguous-or-unallocated-owner'

    def resolve_function(name, from_tu):
        local = [(facts, fn) for facts in inputs for fn in facts['functions']
                 if facts['translation_unit'] == from_tu and fn['name'] == name and present(facts, fn)]
        if len(local) == 1:
            return local[0], 'same-translation-unit-allocated-function'
        if len(local) > 1:
            return None, 'ambiguous-local-function'
        owners = strong.get(name, [])
        candidates = [(facts, fn) for facts in inputs for fn in facts['functions']
                      if fn['name'] == name and fn['linkage'] == 'external' and present(facts, fn)
                      and len(owners) == 1 and facts['translation_unit'] == owners[0]['translation_unit']]
        return (candidates[0], 'unique-selected-allocated-external-function') if len(candidates) == 1 \
            else (None, 'missing-ambiguous-or-unallocated-function')
    return present, resolve, resolve_function, definitions


def same(left, right):
    (found, status), (expected, expected_status) = left, right
    assert status == expected_status
    assert (found is None) == (expected is None)
    if found is not None:
        assert found[0] is expected[0] and found[1] is expected[1]


@needs_gcc
def test_indexed_lookups_equal_the_scans(tmp_path, monkeypatch):
    _, lazy, ownership = extracted(tmp_path, (('typed.c', sample_source()), ('other.c', 'int other(void) { return 2; }\n')))
    (tmp_path / 'store').mkdir()
    indexes, store = stored(tmp_path / 'store', *lazy)
    captured = {}
    real = link.materialize

    def capture(inputs, resolve, selected, present, resolve_function, frontend_unresolved):
        captured.update(resolve_function=resolve_function)
        return real(inputs, resolve, selected, present, resolve_function, frontend_unresolved)
    monkeypatch.setattr(link, 'materialize', capture)
    _, resolve, present = join(indexes, ownership, store)
    reference_present, reference_resolve, reference_function, definitions = scanned(indexes, ownership)
    units = [index['translation_unit'] for index in indexes] + ['<other unit>']
    names = sorted({d['name'] for _, d in definitions} | {f['name'] for index in indexes for f in index['functions']}
                   | {'<missing>'})
    assert len(names) >= 6
    for index in indexes:
        for item in itertools.chain(index['definitions'], index['functions'], [{'name': '<missing>'}]):
            assert present(index, item) is reference_present(index, item)
    for name, unit in itertools.product(names, units):
        same(resolve(name, unit), reference_resolve(name, unit))
        same(captured['resolve_function'](name, unit), reference_function(name, unit))
    statuses = {resolve(name, unit)[1] for name, unit in itertools.product(names, units)}
    assert {'same-translation-unit-allocated-object', 'missing-ambiguous-or-unallocated-owner'} <= statuses
    function_statuses = {captured['resolve_function'](name, unit)[1] for name, unit in itertools.product(names, units)}
    assert {'same-translation-unit-allocated-function', 'unique-selected-allocated-external-function',
            'missing-ambiguous-or-unallocated-function'} <= function_statuses


def test_a_unit_without_an_allocated_object_has_nothing_present():
    owner = {'translation_unit': 'a', 'symbols': [], 'allocated_symbols': [{'name': 'f'}]}
    indexes = [{'translation_unit': 'a', 'definitions': [], 'functions': []},
               {'translation_unit': 'b', 'definitions': [], 'functions': []}]
    _, _, present = join(indexes, {'objects': [owner]}, None)
    assert present(indexes[0], {'name': 'f'}) and not present(indexes[0], {'name': 'g'})
    assert not present(indexes[1], {'name': 'f'})
