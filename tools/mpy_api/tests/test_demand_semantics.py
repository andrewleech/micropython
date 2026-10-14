"""Unit semantics computed on demand by the link stage: a worker leaves them unloaded, a demand
computes what the worker would have, composition demands only what it reads, and what nobody demands
stays unreadable."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mpy_api import compiler
from mpy_api.facts import Unloaded
from mpy_api.inventory import _extract, _linked_functions, _normal_surface
from mpy_api.link import collect_ownership, join
from mpy_api.semantics import demand_semantics, frontend_unresolved, linked_semantics_unchanged
from mpy_api.structure import deep_equal
from test_fact_objects import function, stored, unit_facts
from test_native_bindings import HANDLERS, linked_c


ROOT = Path(__file__).resolve().parents[3]
needs_gcc = pytest.mark.skipif(shutil.which('gcc') is None, reason='needs the selected C compiler')

SHARED = 'typedef unsigned long mp_obj_t;\nmp_obj_t external_identity(mp_obj_t);\n'
SOURCES = (('helper.c', '#include "shared.h"\nstatic mp_obj_t backend_alias(mp_obj_t value) { return value; }\n'
                        'mp_obj_t external_identity(mp_obj_t value) { return backend_alias(value); }\n'),
           ('caller.c', '#include "shared.h"\nmp_obj_t selected(mp_obj_t value) { return external_identity(value); }\n'
                        'mp_obj_t plain(mp_obj_t value) { return value; }\n'
                        'int main(void) { return selected(0) != plain(0); }\n'))


def extracted(tmp_path, sources=SOURCES):
    """The two fixture units (or ``sources``) extracted twice, with every unit semantics (``full``) and with none
    (``lazy``), and their link ownership."""
    (tmp_path / 'shared.h').write_text(SHARED)
    full, lazy, records = [], [], []
    for filename, source in sources:
        physical, obj = tmp_path / filename, tmp_path / (filename + '.o')
        physical.write_text(source)
        args = ['gcc', '-std=gnu99', '-O0', '-ffunction-sections', '-I', str(tmp_path), '-c', str(physical), '-o', str(obj)]
        _, snapshot = compiler.read_input(physical)
        record = {'directory': str(tmp_path), 'file': str(physical), 'physical': str(physical), 'identity': str(physical),
                  'object': str(obj), 'arguments': args, '_source_snapshot': snapshot, '_response_snapshots': {}}
        for variant, full_semantics in ((full, True), (lazy, False)):
            facts = _extract(record, compiler.authority(ROOT), tmp_path, full_semantics=full_semantics)
            variant.append({key: value for key, value in facts.items() if key != '_selection'})
        records.append(record)
        subprocess.run(args, check=True, capture_output=True)
    elf, map_path = tmp_path / 'fixture.elf', tmp_path / 'fixture.map'
    argv = ['gcc', *(record['object'] for record in records), '-Wl,-Map=' + str(map_path), '-o', str(elf)]
    subprocess.run(argv, check=True, capture_output=True)
    ownership = collect_ownership(records, {'directory': tmp_path, 'elf': elf, 'map': map_path, 'argv': argv})
    return full, lazy, ownership


@pytest.fixture
def units(tmp_path):
    full, lazy, ownership = extracted(tmp_path)
    (tmp_path / 'full').mkdir()
    (tmp_path / 'lazy').mkdir()
    full_indexes, full_store = stored(tmp_path / 'full', *full)
    lazy_indexes, lazy_store = stored(tmp_path / 'lazy', *lazy)
    return full_indexes, full_store, lazy_indexes, lazy_store, ownership


@needs_gcc
def test_a_worker_without_full_semantics_leaves_them_unloaded(units):
    full_indexes, full_store, lazy_indexes, lazy_store, _ = units
    for full_index, lazy_index in zip(full_indexes, lazy_indexes):
        for full_record, lazy_record in zip(full_index['functions'], lazy_index['functions']):
            for field in ('semantic_helper_lookups', 'frontend_unresolved'):
                assert not isinstance(full_record[field], Unloaded)
                assert isinstance(lazy_record[field], Unloaded)
            complete = lazy_store.function(lazy_record)
            assert isinstance(complete['semantics'], Unloaded) and isinstance(complete['semantic_helper_lookups'], Unloaded)
            assert not isinstance(full_store.function(full_record)['semantics'], Unloaded)
            # The call index, barriers and identity are in the index either way.
            assert lazy_record['call_index'] == full_record['call_index']
            assert lazy_record['semantic_unassessed'] == full_record['semantic_unassessed']


@needs_gcc
def test_demanded_semantics_equal_the_ones_a_worker_computes(units):
    full_indexes, full_store, lazy_indexes, lazy_store, _ = units
    demanded = 0
    for full_index, lazy_index in zip(full_indexes, lazy_indexes):
        for full_record, lazy_record in zip(full_index['functions'], lazy_index['functions']):
            complete = demand_semantics(lazy_store, lazy_record)
            demanded += 1
            expected = full_store.function(full_record)
            assert deep_equal(complete['semantics'], expected['semantics'])
            assert deep_equal(complete['semantic_helper_lookups'], expected['semantic_helper_lookups'])
            assert deep_equal(lazy_record['semantic_helper_lookups'], full_record['semantic_helper_lookups'])
            assert lazy_record['frontend_unresolved'] == full_record['frontend_unresolved']
            # Demanding again, through either kind of record, computes nothing.
            assert demand_semantics(lazy_store, lazy_record) is complete
            assert demand_semantics(lazy_store, complete) is complete
    assert lazy_store.counts['unit_semantics_demanded'] == demanded == 5


@needs_gcc
def test_frontend_unresolved_demands_the_functions_semantics(units):
    full_indexes, _, lazy_indexes, lazy_store, _ = units
    lazy_record = lazy_indexes[1]['functions'][0]
    assert isinstance(lazy_record['frontend_unresolved'], Unloaded)
    assert frontend_unresolved(lazy_store, lazy_record) == full_indexes[1]['functions'][0]['frontend_unresolved']
    assert lazy_store.counts['unit_semantics_demanded'] == 1


@needs_gcc
def test_what_nobody_demands_stays_unreadable(units):
    _, _, lazy_indexes, lazy_store, _ = units
    caller = lazy_indexes[1]
    selected = next(record for record in caller['functions'] if record['name'] == 'selected')
    demand_semantics(lazy_store, selected)
    for record in caller['functions']:
        if record is selected:
            continue
        complete = lazy_store.function(record)
        assert isinstance(complete['semantics'], Unloaded) and isinstance(record['frontend_unresolved'], Unloaded)
        with pytest.raises(RuntimeError, match='is evidence'):
            complete['semantics']['returns']
        with pytest.raises(RuntimeError, match='is evidence'):
            record['frontend_unresolved'] == []
    assert lazy_store.counts['unit_semantics_demanded'] == 1


@needs_gcc
def test_demanded_unit_semantics_stand_for_the_linked_run_unless_a_lookup_finds_a_helper(units):
    full_indexes, full_store, lazy_indexes, lazy_store, ownership = units
    _, _, present = join(lazy_indexes, ownership, lazy_store)
    linked = _linked_functions(lazy_indexes, present, lazy_store)
    _, _, full_present = join(full_indexes, ownership, full_store)
    full_linked = _linked_functions(full_indexes, full_present, full_store)
    decided = {}
    for lazy_index, full_index in zip(lazy_indexes, full_indexes):
        for lazy_record, full_record in zip(lazy_index['functions'], full_index['functions']):
            demand_semantics(lazy_store, lazy_record)
            decided[lazy_record['name']] = linked_semantics_unchanged(lazy_record['semantic_helper_lookups'], linked)
            assert decided[lazy_record['name']] == linked_semantics_unchanged(full_record['semantic_helper_lookups'], full_linked)
    assert decided == {'backend_alias': True, 'external_identity': False, 'selected': False, 'plain': True,
                       'main': True}


@needs_gcc
def test_composition_with_demanded_semantics_equals_composition_with_every_semantics(tmp_path):
    composed, text = {}, {}
    for full_semantics in (True, False):
        directory = tmp_path / ('full' if full_semantics else 'lazy')
        index, raw, resolve, present, store = linked_c(directory, HANDLERS, 'py/runtime.c', full_semantics=full_semantics)
        surface = _normal_surface(raw, [index], resolve, present, directory, store)
        composed[full_semantics] = (index, store)
        text[full_semantics] = json.dumps(surface, sort_keys=True, default=list).replace(str(directory), '<root>')
    assert text[False] == text[True]
    (_, full_store), (lazy_index, lazy_store) = composed[True], composed[False]
    assert full_store.counts['unit_semantics_demanded'] == 0
    # Composition read the unit semantics of the bound and framework functions only.
    names = {record['name'] for record in lazy_index['functions']}
    unread = {record['name'] for record in lazy_index['functions']
              if isinstance(lazy_store.function(record)['semantics'], Unloaded)}
    assert {'main', 'method'} <= unread and unread < names
    assert lazy_store.counts['unit_semantics_demanded'] == len(names) - len(unread) > 0


def test_a_repeated_function_identity_in_a_unit_is_refused(tmp_path):
    facts = unit_facts('a', functions=2)
    facts['functions'][1] = function('a', 'f0', {'nested': []}, '')
    (index,), store = stored(tmp_path, facts)
    with pytest.raises(RuntimeError, match='repeated function identity a::f0'):
        store.index_record(index['functions'][0])


@needs_gcc
def test_a_demanded_function_whose_semantics_raise_fails_with_its_units_extraction_context(units, monkeypatch):
    _, _, lazy_indexes, lazy_store, _ = units

    def fail(*args):
        raise ValueError('semantics broke')
    monkeypatch.setattr('mpy_api.semantics.unit_semantics', fail)
    record = lazy_indexes[1]['functions'][0]
    with pytest.raises(RuntimeError, match=r'API extraction failed for compiled TU .*caller\.c; physical source .*caller\.c: semantics broke'):
        demand_semantics(lazy_store, record)
