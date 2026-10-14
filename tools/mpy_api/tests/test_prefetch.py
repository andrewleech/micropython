"""Semantics computed ahead in forked processes equal the ones the link stage computes where it reads them,
whatever the number of processes and the order they start in, and a function the processes cannot compute is
left to the serial path."""
import json
import pickle

import pytest

from mpy_api import prefetch, semantics
from mpy_api.facts import Unloaded
from mpy_api.inventory import _linked_functions, _normal_surface
from mpy_api.link import collect_ownership, join
from mpy_api.prefetch import prefetch_linked_semantics, prefetch_unit_semantics
from mpy_api.semantics import demand_semantics, linked_semantics_result, linked_semantics_unchanged
from mpy_api.structure import deep_equal
from test_demand_semantics import extracted, needs_gcc
from test_fact_objects import stored
from test_native_bindings import HANDLERS, index_view, linked_artifacts


@pytest.fixture
def pair(tmp_path):
    """Two stores of the same units without semantics, one to demand from serially and one to prefetch
    into, and the link ownership."""
    _, lazy, ownership = extracted(tmp_path)
    out = []
    for name in ('serial', 'pool'):
        (tmp_path / name).mkdir()
        out.append(stored(tmp_path / name, *pickle.loads(pickle.dumps(lazy))))
    return out[0], out[1], ownership


def records(indexes):
    return [record for index in indexes for record in index['functions']]


def assert_same_function(serial_store, serial, pool_store, pooled):
    complete, expected = pool_store.function(pooled), serial_store.function(serial)
    assert deep_equal(complete['semantics'], expected['semantics'])
    assert deep_equal(complete['semantic_helper_lookups'], expected['semantic_helper_lookups'])
    assert deep_equal(pooled['semantic_helper_lookups'], serial['semantic_helper_lookups'])
    assert pooled['frontend_unresolved'] == serial['frontend_unresolved']


@needs_gcc
@pytest.mark.parametrize('jobs', [2, 3, 8])
def test_prefetched_unit_semantics_equal_the_demanded_ones(pair, jobs):
    (serial_indexes, serial_store), (pool_indexes, pool_store), _ = pair
    for record in records(serial_indexes):
        demand_semantics(serial_store, record)
    prefetch_unit_semantics(pool_store, records(pool_indexes), jobs)
    assert pool_store.counts['unit_semantics_prefetched'] == pool_store.counts['unit_semantics_demanded'] == 5
    for serial, pooled in zip(records(serial_indexes), records(pool_indexes)):
        assert_same_function(serial_store, serial, pool_store, pooled)
        # A demand finds the installed semantics and computes nothing.
        assert demand_semantics(pool_store, pooled) is pool_store.function(pooled)
    assert pool_store.counts['unit_semantics_demanded'] == 5
    assert pool_store.report()['counts']['unit_semantics_prefetch_unread'] == 0


@needs_gcc
def test_a_prefetch_nobody_reads_is_counted_unread(pair):
    _, (pool_indexes, pool_store), _ = pair
    prefetch_unit_semantics(pool_store, records(pool_indexes), 2)
    assert pool_store.report()['counts']['unit_semantics_prefetch_unread'] == 5
    demand_semantics(pool_store, records(pool_indexes)[0])
    assert pool_store.report()['counts']['unit_semantics_prefetch_unread'] == 4


@needs_gcc
def test_the_order_the_processes_start_in_does_not_change_the_result(pair, monkeypatch):
    (serial_indexes, serial_store), (pool_indexes, pool_store), _ = pair
    for record in records(serial_indexes):
        demand_semantics(serial_store, record)
    sizes = iter([3, 9, 1, 9, 5])
    monkeypatch.setattr(prefetch, '_size', lambda function: next(sizes))
    prefetch_unit_semantics(pool_store, records(pool_indexes), 3)
    for serial, pooled in zip(records(serial_indexes), records(pool_indexes)):
        assert_same_function(serial_store, serial, pool_store, pooled)


@needs_gcc
def test_functions_with_semantics_and_repeated_functions_are_not_computed_again(pair):
    _, (pool_indexes, pool_store), _ = pair
    first = records(pool_indexes)[0]
    demand_semantics(pool_store, first)
    prefetch_unit_semantics(pool_store, [first, *records(pool_indexes), *records(pool_indexes)], 2)
    assert pool_store.counts['unit_semantics_demanded'] == 5
    assert pool_store.counts['unit_semantics_prefetched'] == 4
    prefetch_unit_semantics(pool_store, records(pool_indexes), 2)
    assert pool_store.counts['unit_semantics_prefetched'] == 4


@needs_gcc
def test_one_process_or_nothing_pending_starts_no_processes(pair, monkeypatch):
    _, (pool_indexes, pool_store), _ = pair
    monkeypatch.setattr(prefetch, '_run', lambda *args: pytest.fail('a pool was started'))
    prefetch_unit_semantics(pool_store, records(pool_indexes), 1)
    assert pool_store.counts['unit_semantics_prefetched'] == 0
    assert prefetch_linked_semantics(pool_store, {}, [(pool_indexes[0], records(pool_indexes)[0])], 1) == {}
    assert prefetch_linked_semantics(pool_store, {}, [], 4) == {}
    prefetch_unit_semantics(pool_store, [], 4)


@needs_gcc
def test_a_function_a_process_cannot_compute_is_left_to_the_demand_that_raises(pair, monkeypatch):
    _, (pool_indexes, pool_store), _ = pair
    target = records(pool_indexes)[1]
    real = semantics.unit_semantics

    def fail_for_target(function, *args):
        if function['id'] == target['id']:
            raise ValueError('semantics broke')
        return real(function, *args)
    monkeypatch.setattr(semantics, 'unit_semantics', fail_for_target)
    prefetch_unit_semantics(pool_store, records(pool_indexes), 2)
    assert pool_store.counts['unit_semantics_prefetched'] == 4
    assert isinstance(pool_store.function(target)['semantics'], Unloaded)
    with pytest.raises(RuntimeError, match=r'API extraction failed for compiled TU .*physical source .*: semantics broke'):
        demand_semantics(pool_store, target)
    monkeypatch.setattr(semantics, 'unit_semantics', real)
    assert deep_equal(demand_semantics(pool_store, target)['semantics']['returns'],
                      demand_semantics(pair[0][1], records(pair[0][0])[1])['semantics']['returns'])


@needs_gcc
def test_a_result_that_cannot_be_returned_is_left_to_the_demand(pair, monkeypatch):
    _, (pool_indexes, pool_store), _ = pair
    monkeypatch.setattr(prefetch, 'computed_unit_semantics', lambda store, function: (lambda: None, None))
    prefetch_unit_semantics(pool_store, records(pool_indexes), 2)
    assert pool_store.counts['unit_semantics_prefetched'] == 0
    assert all(isinstance(pool_store.function(record)['semantics'], Unloaded) for record in records(pool_indexes))
    monkeypatch.undo()
    for record in records(pool_indexes):
        demand_semantics(pool_store, record)
    assert pool_store.counts['unit_semantics_demanded'] == 5


@needs_gcc
def test_a_process_that_dies_leaves_every_function_to_the_demand(pair, monkeypatch):
    _, (pool_indexes, pool_store), _ = pair

    def die(store, function):
        import os
        os._exit(1)
    monkeypatch.setattr(prefetch, 'computed_unit_semantics', die)
    prefetch_unit_semantics(pool_store, records(pool_indexes), 2)
    assert pool_store.counts['unit_semantics_prefetched'] == 0
    monkeypatch.undo()
    demand_semantics(pool_store, records(pool_indexes)[0])
    assert pool_store.counts['unit_semantics_demanded'] == 1


@needs_gcc
@pytest.mark.parametrize('jobs', [2, 4])
def test_prefetched_linked_semantics_equal_the_linked_runs(pair, jobs):
    (serial_indexes, serial_store), (pool_indexes, pool_store), ownership = pair
    _, _, serial_present = join(serial_indexes, ownership, serial_store)
    _, _, pool_present = join(pool_indexes, ownership, pool_store, jobs)
    serial_linked = _linked_functions(serial_indexes, serial_present, serial_store)
    pool_linked = _linked_functions(pool_indexes, pool_present, pool_store)
    items = []
    for index, record in ((i, r) for i in pool_indexes for r in i['functions']):
        demand_semantics(pool_store, record)
        if not linked_semantics_unchanged(record['semantic_helper_lookups'], pool_linked):
            items.append((index, record))
    assert sorted(record['name'] for _, record in items) == ['external_identity', 'selected']
    results = prefetch_linked_semantics(pool_store, pool_linked, items, jobs)
    assert sorted(results) == sorted(record['id'] for _, record in items)
    serial = {record['id']: (index, record) for index in serial_indexes for record in index['functions']}
    for _, record in items:
        index, serial_record = serial[record['id']]
        demand_semantics(serial_store, serial_record)
        expected, reused = linked_semantics_result(serial_store, index, serial_record, serial_linked)
        assert not reused and deep_equal(results[record['id']], expected)


@needs_gcc
def test_prefetched_results_survive_pickling_and_do_not_alias_the_facts(pair):
    (_, _), (pool_indexes, pool_store), ownership = pair
    _, _, present = join(pool_indexes, ownership, pool_store, 2)
    linked = _linked_functions(pool_indexes, present, pool_store)
    items = [(index, record) for index in pool_indexes for record in index['functions'] if record['name'] == 'selected']
    for _, record in items:
        demand_semantics(pool_store, record)
    result = prefetch_linked_semantics(pool_store, linked, items, 2)[items[0][1]['id']]
    assert deep_equal(pickle.loads(pickle.dumps(result)), result)
    unit = pool_store.function(pool_store.index_record(items[0][1]))['semantics']
    assert result is not unit
    before = pickle.dumps(unit)
    result['aliasing_probe'] = []
    assert 'aliasing_probe' not in unit and pickle.dumps(unit) == before


def sample_source():
    """The handler fixture with the ``method`` function bound in the locals table of its type."""
    source = HANDLERS.replace(
        "uint8_t slot_index_attr;\n    const void *slots[1];",
        "uint8_t slot_index_attr, slot_index_locals_dict;\n    const void *slots[2];")
    source = source.replace("static const mp_obj_type_t sample = {", """
#define MP_ROM_QSTR(value) (((mp_rom_obj_t)(value) << 3) | 2)
typedef struct { mp_rom_obj_t key, value; } mp_rom_map_elem_t;
typedef struct { unsigned used; const mp_rom_map_elem_t *table; } fixture_map_t;
typedef struct { mp_obj_base_t base; fixture_map_t map; } mp_obj_dict_t;
static const mp_rom_map_elem_t sample_table[] = {
    { MP_ROM_QSTR(MP_QSTR_method), MP_ROM_PTR(&method_obj) },
};
static const mp_obj_dict_t sample_locals = {
    .base = { &mp_type_type }, .map = { .used = 1, .table = sample_table },
};
static const mp_obj_type_t sample = {""")
    return source.replace(
        ".slot_index_attr = 1, .slots = { (const void *)sample_attr },",
        ".slot_index_attr = 1, .slot_index_locals_dict = 2, .slots = { (const void *)sample_attr, &sample_locals },")


def surface_text(directory, jobs):
    """The composed surface of the fixture and how its semantics were obtained."""
    facts, record, link = linked_artifacts(directory, sample_source(), 'py/runtime.c', full_semantics=False)
    ownership = collect_ownership([record], link)
    (index,), store = index_view(directory / 'facts', facts)
    raw, resolve, present = join([index], ownership, store, jobs)
    surface = _normal_surface(raw, [index], resolve, present, directory, store, jobs=jobs)
    text = json.dumps(surface, sort_keys=True, default=list).replace(str(directory), '<root>')
    return text, store.counts


@needs_gcc
@pytest.mark.parametrize('jobs', [2, 3, 8])
@pytest.mark.parametrize('largest_first', [True, False])
def test_composition_with_prefetched_semantics_equals_serial_composition(tmp_path, monkeypatch, jobs, largest_first):
    # Every bound function takes the linked run, so that the linked batch is not empty.
    monkeypatch.setattr(semantics, 'linked_semantics_unchanged', lambda lookups, linked: False)
    import mpy_api.inventory
    monkeypatch.setattr(mpy_api.inventory, 'linked_semantics_unchanged', lambda lookups, linked: False)
    if not largest_first:
        size = prefetch._size
        monkeypatch.setattr(prefetch, '_size', lambda function: -size(function))
    serial_text, serial = surface_text(tmp_path / "serial", 1)
    pooled_text, pooled = surface_text(tmp_path / "pool", jobs)
    assert pooled_text == serial_text
    assert serial['unit_semantics_prefetched'] == serial['linked_semantics_prefetched'] == 0
    assert pooled['unit_semantics_prefetched'] > 0 and pooled['linked_semantics_prefetched'] > 0
    assert pooled['unit_semantics_demanded'] == serial['unit_semantics_demanded']
    assert pooled['linked_semantics_runs'] == serial['linked_semantics_runs'] > 0
    assert pooled['unit_semantics_reused'] == serial['unit_semantics_reused']


@needs_gcc
def test_composition_without_forced_runs_equals_serial_composition(tmp_path):
    serial_text, serial = surface_text(tmp_path / "serial", 1)
    pooled_text, pooled = surface_text(tmp_path / "pool", 2)
    assert pooled_text == serial_text
    assert pooled['unit_semantics_prefetched'] > 0
    assert pooled['unit_semantics_demanded'] == serial['unit_semantics_demanded']
