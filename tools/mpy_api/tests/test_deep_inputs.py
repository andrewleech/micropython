"""Deeply nested C reaches facts, semantics and the fact objects at the default recursion limit."""
import pickle
import shutil
import sys
from pathlib import Path

import pytest

from mpy_api import compiler, inventory
from mpy_api.chains import ChainView
from mpy_api.facts import FactStore


ROOT = Path(__file__).resolve().parents[3]
BRANCHES = 10_000
# gcc accepts these nesting depths in -E and full compilation; tree-sitter parses them without error.
DEPTH = 10_000

needs_gcc = pytest.mark.skipif(shutil.which('gcc') is None, reason='needs the selected C compiler')


def chain_source(branches):
    """The shape of MicroPython's MP_COMPRESSED_ROM_TEXT: one else-if branch per message."""
    rows = ['int strcmp(const char *a, const char *b);', 'const char *lookup(const char *msg) {']
    rows.extend(f'    if (strcmp(msg, "m{index}") == 0) {{ return "c{index}"; }} else' for index in range(branches))
    rows.extend(['    return msg;', '}'])
    return '\n'.join(rows) + '\n'


def nested_source(depth):
    return '\n'.join([
        'int blocks(int x) {', '{' * depth, 'x = x + 1;', '}' * depth, 'return x; }',
        'int parens(int x) { return ' + '(' * depth + 'x' + ')' * depth + '; }',
        'int sums(int x) { return ' + 'x + (' * depth + 'x' + ')' * depth + '; }',
        'int ifs(int x) {' + 'if (x) {' * depth + 'x++;' + '}' * depth + ' return x; }',
        'enum { E = ' + '(' * depth + '1' + ')' * depth + ' };',
        'int uses_e(void) { return E; }',
        'int grid' + '[1]' * 1000 + ' = ' + '{' * 1000 + '0' + '}' * 1000 + ';',
    ]) + '\n'


def write_record(directory, name, source):
    path = directory / name
    path.write_text(source)
    entry = {'directory': str(directory), 'arguments': ['gcc', '-c', name, '-o', path.stem + '.o'], 'file': name}
    _, snapshot = compiler.read_input(path)
    return {**entry, 'physical': str(path), 'identity': str(path), 'object': str(directory / (path.stem + '.o')),
            '_source_snapshot': snapshot, '_response_snapshots': {}}


@pytest.fixture(scope='module')
def deep_records(tmp_path_factory):
    directory = tmp_path_factory.mktemp('deep')
    return [write_record(directory, 'chain.c', chain_source(BRANCHES)),
            write_record(directory, 'nested.c', nested_source(DEPTH))]


@pytest.fixture(autouse=True)
def default_limit():
    assert sys.getrecursionlimit() <= 1000
    compiler.clear_resolution_cache()
    compiler.clear_replay_cache()
    yield
    compiler.clear_resolution_cache()
    compiler.clear_replay_cache()


def functions(facts):
    return {function['name']: function for function in facts['functions']}


def check_chain(facts):
    lookup = functions(facts)['lookup']
    events = lookup['events']
    assert len(events['conditions']) == BRANCHES and len(events['returns']) == BRANCHES + 1
    inner, last = events['returns'][-2:]
    assert isinstance(inner['guards'], ChainView) and len(inner['guards']) == BRANCHES
    assert [guard['branch'] for guard in inner['guards'][-2:]] == ['else', 'then']
    assert len(last['guards']) == BRANCHES and all(guard['branch'] == 'else' for guard in last['guards'])
    assert len(events['guard_table']['guards']) == 2 * BRANCHES
    flow = events['flow']
    assert flow[0]['kind'] == 'block' and sum(node['kind'] == 'if' for node in flow) == BRANCHES
    semantics = lookup['semantics']
    assert semantics['successful_path_coverage']['resolution'] == 'bounded-resolved'
    assert len(semantics['successful_path_coverage']['reachable_return_points']) == BRANCHES + 1
    selection = semantics['branch_selection'][-2]
    assert selection['selected'] and list(selection['evaluated_conditions']) == [None] * BRANCHES


def check_nested(facts):
    by_name = functions(facts)
    blocks = by_name['blocks']['events']
    assert [write['target'] for write in blocks['writes']] == ['x']
    # The body, DEPTH nested blocks, the assignment and the return.
    assert len(blocks['flow']) == DEPTH + 3
    assert len(by_name['ifs']['events']['writes'][0]['guards']) == DEPTH
    assert by_name['parens']['events']['returns'][0]['expression_node']['kind'] == 'parenthesized_expression'
    node, depth = by_name['sums']['events']['returns'][0]['expression_node'], 0
    while 'right' in node or 'children' in node:
        node = node['right'] if 'right' in node else node['children'][0]
        depth += 1
    assert depth == 24 and node['unresolved'] == 'expression nesting exceeds bounded def/use model'
    assert by_name['uses_e']['constants']['E']['value'] == 1
    grid = next(definition for definition in facts['definitions'] if definition['name'] == 'grid')
    assert len(grid['initializer_nodes']) == 1001 and len(grid['initializer_rows']) == 1


@needs_gcc
def test_deep_sources_extract_serially(deep_records):
    check_nested(inventory._extract(deep_records[1], compiler.authority(ROOT), ROOT, full_semantics=True))


@needs_gcc
def test_deep_sources_extract_into_fact_objects_through_two_workers(deep_records, tmp_path):
    store, results = FactStore(tmp_path), []
    inventory._extract_parallel(deep_records, compiler.authority(ROOT), ROOT, 2, store, results.append,
                                full_semantics=True)
    for index, nodes, location, _, _ in results:
        store.add(index, nodes, location)
    complete = [{**index, 'functions': [store.function(record) for record in index['functions']]}
                for index, _, _, _, _ in results]
    check_chain(complete[0])
    check_nested(complete[1])
    for facts in complete:
        restored = pickle.loads(pickle.dumps(facts['functions'], protocol=pickle.HIGHEST_PROTOCOL))
        assert restored[0]['events']['flow'] == facts['functions'][0]['events']['flow']
