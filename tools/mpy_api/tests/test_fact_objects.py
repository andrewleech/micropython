"""Fact objects: the index the link stage holds, evidence loaded per function with its sharing intact,
integrity checks on every read, a parent that keeps only indexes, and unit semantics reused only where
linking cannot change them."""
import os
import pickle
import shutil
import subprocess
import tracemalloc
from pathlib import Path

import pytest

from mpy_api import compiler, inventory
from mpy_api.chains import ChainView, chain_root
from mpy_api.facts import FactStore, Unloaded, write
from mpy_api.inventory import _extract, _linked_functions
from mpy_api.link import collect_ownership, join
from mpy_api.semantics import linked_semantics_unchanged, semantics
from mpy_api.structure import deep_equal


ROOT = Path(__file__).resolve().parents[3]
needs_gcc = pytest.mark.skipif(shutil.which('gcc') is None, reason='needs the selected C compiler')


def function(unit, name, shared, payload):
    span = {'source': f'/src/{unit}.c', 'start_line': 1, 'end_line': 2}
    callee = {'kind': 'FUNCTION_DECL', 'name': 'callee', 'source_span': span}
    parents, items = [], []
    guards = chain_root(parents, items).extend({'condition': 'x', 'branch': 'then'})
    return {'id': f'{unit}::{name}', 'name': name, 'linkage': 'external', 'source_span': span,
            'declaration_identity': {'kind': 'FUNCTION_DECL', 'name': name, 'source_span': span},
            'translation_unit': unit, 'parameters': ['value'], 'handler_attributes': [],
            'semantic_helper_lookups': [], 'constants': {}, 'parameter_source_contract': {'kind': 'fixture'},
            'events': {'calls': [{'callee': callee, 'source_span': span, 'guards': guards,
                                  'expression_node': {'kind': 'call_expression', 'shared': shared}}],
                       'returns': [{'payload': payload}], 'variables': [], 'conditions': [], 'writes': [],
                       'semantic_unassessed': []},
            'semantics': {'function_id': f'{unit}::{name}', 'frontend_unresolved': [], 'shared': shared,
                          'diagnostic_stage': 'translation-unit-extraction'},
            'handler_attribute_tests': []}


def unit_facts(unit, functions=2, payload=0):
    definition = {'id': f'{unit}::table', 'name': 'table', 'kind': 'map-table', 'rows': [1, 2]}
    shared = {'nested': [1, 2, 3], 'definition': definition}
    snapshot = {'path': f'/src/{unit}.c', 'resolved': f'/src/{unit}.c', 'sha256': '0' * 64, '_stat': (1, 2, 3, 4, 5)}
    return {'translation_unit': unit, 'source': f'/src/{unit}.c', 'definitions': [definition],
            'functions': [function(unit, f'f{index}', shared, 'x' * payload) for index in range(functions)],
            'registrations': [], 'errors': [], 'contracts': {'null': '0'},
            'frontend': {'aggregate_interfaces': [{'name': 'mp_arg_t'}], 'contract_constants': {}},
            'coverage': {'status': 'processed', 'owned_declarations': 'bounded-recovered',
                         'project_headers': ['/src/h.h'], 'unsupported_definitions': []},
            'input': {'object': f'/build/{unit}.o', 'dependencies': {f'/src/{unit}.c': '0' * 64},
                      'compiler': '/usr/bin/gcc', 'compiler_sha256': '1' * 64, 'compiler_expanded_sha256': '2' * 64,
                      'arguments': ['gcc', '-c'], 'receipts': [{'argv': ['gcc']}],
                      'input_snapshots': {f'/src/{unit}.c': snapshot}}}


def stored(tmp_path, *units, level=1):
    """Write ``units`` and return their indexes as a worker's pipe delivers them, and their store."""
    store, indexes = FactStore(tmp_path, compression=level), []
    for ordinal, facts in enumerate(units):
        index, nodes, location = pickle.loads(pickle.dumps(write(store.path(ordinal), facts, level)))
        store.add(index, nodes, location)
        indexes.append(index)
    return indexes, store


@pytest.mark.parametrize('level', [0, 1, 6])
def test_loaded_evidence_equals_the_worker_facts_and_shares_as_they_did(tmp_path, level):
    facts = unit_facts('a', functions=3)
    (index,), store = stored(tmp_path, facts, level=level)
    loaded = [store.function(record) for record in index['functions']]
    for original, complete, record in zip(facts['functions'], loaded, index['functions']):
        assert deep_equal(complete, original)
        assert complete['source_span'] is record['source_span']
        assert complete['declaration_identity'] is record['declaration_identity']
        assert complete['events']['calls'][0]['callee'] is record['call_index'][0]['callee']
        assert isinstance(complete['events']['calls'][0]['guards'], ChainView)
    # A subtree shared by every function is one object in every loaded function.
    shared = loaded[0]['semantics']['shared']
    assert all(item['semantics']['shared'] is shared for item in loaded)
    assert all(item['events']['calls'][0]['expression_node']['shared'] is shared for item in loaded)
    # A definition in the index stays the index's object inside loaded evidence.
    assert shared['definition'] is index['definitions'][0]
    assert store.function(index['functions'][0]) is loaded[0]
    assert store.frontend(index) == facts['frontend']
    evidence = store.unit_evidence(index)
    assert evidence['input'] == facts['input'] and evidence['coverage'] == facts['coverage']


def test_the_index_holds_identity_and_call_index_and_names_every_evidence_field(tmp_path):
    facts = unit_facts('a')
    (index,), _ = stored(tmp_path, facts)
    record = index['functions'][0]
    assert record['call_index'] == [{'callee': facts['functions'][0]['events']['calls'][0]['callee'],
                                     'source_span': facts['functions'][0]['source_span']}]
    assert record['semantic_unassessed'] == [] and record['frontend_unresolved'] == [] and record['evidence'] == 0
    for field in ('events', 'semantics', 'constants', 'parameter_source_contract', 'handler_attribute_tests'):
        assert isinstance(record[field], Unloaded)
    assert isinstance(index['frontend'], Unloaded)
    assert isinstance(index['input']['input_snapshots'], Unloaded) and index['input']['object'] == '/build/a.o'
    assert isinstance(index['coverage']['project_headers'], Unloaded) and index['coverage']['status'] == 'processed'


@pytest.mark.parametrize('use', [
    lambda value: value.get('declarations', []),
    lambda value: value['calls'],
    lambda value: bool(value),
    lambda value: list(value),
    lambda value: 'calls' in value,
    lambda value: len(value),
    lambda value: value == {},
])
def test_reading_an_evidence_field_from_the_index_fails(tmp_path, use):
    (index,), _ = stored(tmp_path, unit_facts('a'))
    for value in (index['functions'][0]['events'], index['functions'][0].get('parameter_source_contract', {}),
                  index.get('frontend', {})):
        with pytest.raises(RuntimeError, match='is evidence'):
            use(value)


def test_comparing_index_records_that_hold_evidence_fields_fails(tmp_path):
    (tmp_path / 'one').mkdir()
    (tmp_path / 'two').mkdir()
    (first,), _ = stored(tmp_path / 'one', unit_facts('a'))
    (second,), _ = stored(tmp_path / 'two', unit_facts('a'))
    left, right = first['functions'][0], second['functions'][0]
    for compare in (lambda: left == right, lambda: left != right, lambda: deep_equal(left, right),
                    lambda: [left['events']] == [right['events']], lambda: left['events'] in [right['events']],
                    lambda: first == second, lambda: deep_equal(first, second)):
        with pytest.raises(RuntimeError, match='is evidence'):
            compare()
    # Each field of each record holds its own value, also after the pipe and within one unit.
    assert left['events'] is not right['events'] and left['events'] is not first['functions'][1]['events']


def test_fact_objects_hold_plain_data_only(tmp_path):
    facts = unit_facts('a')
    facts['functions'][0]['events']['unexpected'] = {1, 2}
    with pytest.raises(TypeError, match='plain data'):
        write(tmp_path / 'unit.facts', facts)


@pytest.mark.parametrize('section', ['function', 'unit', 'shared'])
def test_a_changed_fact_object_is_refused_on_load_and_at_publication(tmp_path, section):
    (index,), store = stored(tmp_path, unit_facts('a'))
    location = store._units['a'].location
    offset, length, _ = location['sections'][{'function': 4, 'unit': 2, 'shared': 1}[section]]
    path = Path(location['path'])
    data = bytearray(path.read_bytes())
    data[offset + length // 2] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(RuntimeError, match='fact object changed'):
        store.verify()
    load = {'function': lambda: store.function(index['functions'][1]),
            'unit': lambda: store.unit_evidence(index),
            'shared': lambda: store.function(index['functions'][0])}[section]
    with pytest.raises(RuntimeError, match='fact object changed'):
        load()


def synthetic_extract(record, compdb, root, *, full_semantics):
    """A unit with about 4 MB of evidence and a small index."""
    return {**unit_facts(record['identity'], functions=8, payload=500_000), '_selection': []}


def parent_memory(tmp_path, monkeypatch, count):
    monkeypatch.setattr(inventory, '_extract', synthetic_extract)
    records = [{'identity': f'tu-{index:03d}'} for index in range(count)]
    directory = tmp_path / str(count)
    directory.mkdir()
    store, kept = FactStore(directory), []

    def consume(result):
        index, nodes, location, snapshots, constraints = result
        store.add(index, nodes, location)
        kept.append(index)
    tracemalloc.start()
    try:
        inventory._extract_parallel(records, None, '/root', 2, store, consume, full_semantics=False)
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return current, peak, len(pickle.dumps(kept[0]))


def test_parent_memory_grows_with_the_unit_count_by_the_index_only(tmp_path, monkeypatch):
    evidence = 8 * 500_000
    small_current, small_peak, index_bytes = parent_memory(tmp_path, monkeypatch, 8)
    large_current, large_peak, _ = parent_memory(tmp_path, monkeypatch, 32)
    per_unit = (large_current - small_current) / 24
    peak_per_unit = (large_peak - small_peak) / 24
    # The parent keeps each unit's index and fact object location (a few kB here); the evidence, about
    # 4 MB a unit, stays in the fact objects.
    assert index_bytes < 20_000
    assert per_unit < 20 * index_bytes < evidence / 10
    assert peak_per_unit < 20 * index_bytes
    assert large_peak < evidence


@needs_gcc
def test_unit_semantics_stand_for_the_linked_run_unless_a_lookup_finds_a_helper(tmp_path):
    header = tmp_path / 'shared.h'
    header.write_text('typedef unsigned long mp_obj_t;\nmp_obj_t external_identity(mp_obj_t);\n')
    facts, records = [], []
    for filename, source in (('helper.c', '#include "shared.h"\nstatic mp_obj_t backend_alias(mp_obj_t value) { return value; }\n'
                                          'mp_obj_t external_identity(mp_obj_t value) { return backend_alias(value); }\n'),
                             ('caller.c', '#include "shared.h"\nmp_obj_t selected(mp_obj_t value) { return external_identity(value); }\n'
                                          'mp_obj_t plain(mp_obj_t value) { return value; }\n'
                                          'int main(void) { return selected(0) != plain(0); }\n')):
        physical, obj = tmp_path / filename, tmp_path / (filename + '.o')
        physical.write_text(source)
        args = ['gcc', '-std=gnu99', '-O0', '-ffunction-sections', '-I', str(tmp_path), '-c', str(physical), '-o', str(obj)]
        _, snapshot = compiler.read_input(physical)
        record = {'directory': str(tmp_path), 'file': str(physical), 'physical': str(physical), 'identity': str(physical),
                  'object': str(obj), 'arguments': args, '_source_snapshot': snapshot, '_response_snapshots': {}}
        facts.append({key: value for key, value in _extract(record, compiler.authority(ROOT), tmp_path,
                                                            full_semantics=True).items()
                      if key != '_selection'})
        records.append(record)
        subprocess.run(args, check=True, capture_output=True)
    elf, map_path = tmp_path / 'fixture.elf', tmp_path / 'fixture.map'
    argv = ['gcc', *(record['object'] for record in records), '-Wl,-Map=' + str(map_path), '-o', str(elf)]
    subprocess.run(argv, check=True, capture_output=True)
    ownership = collect_ownership(records, {'directory': tmp_path, 'elf': elf, 'map': map_path, 'argv': argv})
    (directory := tmp_path / 'facts').mkdir()
    indexes, store = stored(directory, *facts)
    _, _, present = join(indexes, ownership, store)
    linked = _linked_functions(indexes, present, store)
    lookups = {record['name']: record['semantic_helper_lookups'] for index in indexes for record in index['functions']}
    # external_identity finds its static helper in its own unit; selected looks up a helper defined elsewhere.
    assert lookups['external_identity'] is None
    assert lookups['selected'] and lookups['plain'] == []
    decided = {}
    for index in indexes:
        functions = {}
        for record in index['functions']:
            functions.setdefault(record['name'], []).append(record)
        for record in index['functions']:
            complete = store.function(record)
            run = semantics(complete, functions, index['errors'], index['contracts'], linked_functions=linked)
            unit = {key: value for key, value in complete['semantics'].items() if key != 'diagnostic_stage'}
            decided[record['name']] = linked_semantics_unchanged(record['semantic_helper_lookups'], linked)
            if decided[record['name']]:
                assert deep_equal(run, unit)
    assert decided == {'backend_alias': True, 'external_identity': False, 'selected': False, 'plain': True, 'main': decided['main']}
    selected = store.function(next(record for index in indexes for record in index['functions'] if record['name'] == 'selected'))
    linked_run = semantics(selected, {}, [], indexes[1]['contracts'], linked_functions=linked)
    assert linked_run['return_resolution'] != selected['semantics']['return_resolution']


def test_store_paths_are_per_ordinal_in_its_directory(tmp_path):
    store = FactStore(tmp_path)
    assert store.path(3).parent == tmp_path and store.path(3) != store.path(4)
    assert os.path.basename(store.path(12)) == '000012.facts'
