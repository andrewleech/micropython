"""Function-local provenance indexes preserve bounded query context and ordering."""
from copy import deepcopy

from mpy_api.semantics import (
    _QueryFunction, _input_binding_mention, _input_parameter_roots, _input_uses, _local,
)


def identifier(name):
    return {'kind': 'identifier', 'expression': name}


def variable(name, point, initial=None, declaration=None, scope=(0, 1000)):
    return {'name': name, 'byte_start': point, 'scope_start': scope[0], 'scope_end': scope[1],
            'declaration_id': declaration or f'{name}:{point}', 'initializer_node': initial,
            'guards': [], 'declaration_specifiers': 'int', 'source_span': span(point)}


def span(point):
    return {'source': '/fixture/query.c', 'start_line': point + 1, 'end_line': point + 1}


def write(name, point, value, guards=(), target_node=None):
    return {'target': name, 'target_node': target_node or identifier(name), 'value_node': value,
            'expression': f'{name} = {value.get("expression", "value")}', 'operator': '=',
            'byte_start': point, 'byte_end': point + 1, 'guards': list(guards), 'source_span': span(point)}


def function(variables=(), writes=(), calls=(), controls=()):
    return {'id': 'query', 'parameters': ['p', 'q'], 'constants': {}, 'source_span': span(0),
            'events': {'variables': list(variables), 'writes': list(writes), 'calls': list(calls),
                       'conditions': [], 'returns': [], 'control_expressions': list(controls)}}


def parameters(roots):
    return [root['parameter'] for root in roots]


def test_local_strict_boundaries_shadowing_and_first_tie():
    outer = variable('a', 2)
    first = variable('a', 10, scope=(8, 20), declaration='first')
    tied = variable('a', 10, scope=(8, 20), declaration='tied')
    later = variable('a', 12, scope=(8, 20))
    evidence = function([outer, first, tied, later])
    context = _QueryFunction(evidence)
    assert _local(context, 'a', {'byte_start': 2}) is None
    assert _local(context, 'a', {'byte_start': 10}) == outer
    assert _local(context, 'a', {'byte_start': 11}) == first
    assert _local(context, 'a', {'byte_start': 12}) == first
    assert _local(context, 'a', {'byte_start': 13}) == later
    assert _local(context, 'a', {'byte_start': 20}) == outer
    assert _local(context, 'missing', {'byte_start': 20}) is None


def test_roots_keep_initializer_write_order_and_controlling_multiplicity():
    local = variable('a', 2, identifier('q'))
    assignments = [write('a', 10, identifier('p')), write('a', 15, identifier('q'))]
    control = {'control_kind': 'while_statement', 'expression_node': identifier('a'),
               'byte_start': 30, 'byte_end': 31, 'guards': [], 'source_span': span(30)}
    context = _QueryFunction(function([local], assignments, controls=[control]))
    assert parameters(_input_parameter_roots(context, control['expression_node'], control)) == [1, 0, 1]
    assert parameters(_input_parameter_roots(context, control['expression_node'], control, unique=True)) == [1, 0]
    assert parameters(_input_parameter_roots(context, control['expression_node'], control)) == [1, 0, 1]
    uses = _input_uses(context, lambda event: True)
    controlling = [use for use in uses if use.get('reason') ==
                   'native controlling-expression test requires an accepted-domain proof']
    assert parameters([use['source_ref'] for use in controlling]) == [1, 0, 1]


def test_call_consumers_keep_first_root_order_without_controlling_deduplication():
    local = variable('a', 2, identifier('q'))
    assignments = [write('a', 10, identifier('p')), write('a', 15, identifier('q'))]
    node = identifier('a')
    call = {'expression': 'consume ( a )', 'expression_node': {
                'kind': 'call_expression', 'arguments': {'children': [node]}},
            'callee': None, 'byte_start': 30, 'byte_end': 31, 'guards': [], 'source_span': span(30)}
    control = {'control_kind': 'while_statement', 'expression_node': node,
               'byte_start': 30, 'byte_end': 31, 'guards': [], 'source_span': span(30)}
    uses = _input_uses(function([local], assignments, [call], [control]), lambda event: True)
    operands = [use['source_ref'] for use in uses if use.get('helper') == 'consume']
    controlling = [use['source_ref'] for use in uses if use.get('reason') ==
                   'native controlling-expression test requires an accepted-domain proof']
    assert parameters(operands) == [1, 0]
    assert parameters(controlling) == [1, 0, 1]


def test_root_memo_distinguishes_guards_and_use_end():
    then = {'condition_id': 5, 'condition': 'flag', 'branch': 'then'}
    otherwise = {**then, 'branch': 'else'}
    local = variable('a', 2, identifier('p'))
    context = _QueryFunction(function([local], [write('a', 12, identifier('q'), [then])]))
    node = identifier('a')
    assert parameters(_input_parameter_roots(context, node, {'byte_start': 10, 'byte_end': 11})) == [0]
    assert parameters(_input_parameter_roots(context, node, {'byte_start': 10, 'byte_end': 14})) == [0, 1]
    assert parameters(_input_parameter_roots(context, node, {'byte_start': 10, 'byte_end': 14, 'guards': [otherwise]})) == [0]
    assert parameters(_input_parameter_roots(context, node, {'byte_start': 10, 'byte_end': 14, 'guards': [then]})) == [0, 1]


def test_containing_calls_include_sibling_writes_but_not_end_boundary():
    calls = [{'expression': 'outer ( inner ( ) )', 'byte_start': 8, 'byte_end': 30},
             {'expression': 'inner ( )', 'byte_start': 10, 'byte_end': 12}]
    context = _QueryFunction(function([variable('a', 2, identifier('p'))],
        [write('a', 20, identifier('q')), write('a', 30, identifier('p'))], calls))
    assert parameters(_input_parameter_roots(context, identifier('a'), {'byte_start': 10, 'byte_end': 11})) == [0, 1]


def test_alias_target_shadow_and_depth_keys():
    outer = variable('a', 2, identifier('p'), declaration='outer')
    pointer = variable('ptr', 4, {'kind': 'pointer_expression', 'expression': '& a', 'argument': identifier('a')})
    inner = variable('a', 10, identifier('q'), declaration='inner', scope=(8, 20))
    context = _QueryFunction(function([outer, pointer, inner]))
    use = {'byte_start': 15}
    assert _input_binding_mention(context, identifier('ptr'), use, 'a', 'outer')
    assert not _input_binding_mention(context, identifier('ptr'), use, 'a', 'inner')
    assert _input_binding_mention(context, identifier('unrelated'), use, 'a', 'outer', depth=16)
    assert not _input_binding_mention(context, identifier('unrelated'), use, 'a', 'outer', depth=15)
    assert not _input_binding_mention(context, {}, use, 'a', 'outer', depth=16)


def test_indirect_write_binding_index_preserves_order():
    local = variable('a', 2, identifier('p'))
    pointer = variable('ptr', 4, {'kind': 'pointer_expression', 'expression': '& a', 'argument': identifier('a')})
    indirect = {'kind': 'pointer_expression', 'expression': '* ptr', 'argument': identifier('ptr')}
    writes = [write('a', 10, identifier('q')), write('* ptr', 12, identifier('p'), target_node=indirect)]
    context = _QueryFunction(function([local, pointer], writes))
    # The alias path reaches p; its in-interval indirect store emits p again before cycle stopping.
    assert _input_binding_mention(context, indirect, writes[1], 'p', None)
    assert parameters(_input_parameter_roots(context, writes[1]['value_node'], writes[1])) == [0, 0]
    assert parameters(_input_parameter_roots(context, identifier('a'), {'byte_start': 20})) == [0, 1, 0, 0]


def test_cycle_seen_and_depth_cutoff_keep_distinct_answers():
    local = variable('a', 2, identifier('p'))
    context = _QueryFunction(function([local], [write('a', 10, identifier('a'))]))
    node, use = identifier('a'), {'byte_start': 20}
    assert parameters(_input_parameter_roots(context, node, use)) == [0]
    assert _input_parameter_roots(context, node, use, seen=(('a', 'a:2'),)) == []
    assert parameters(_input_parameter_roots(context, node, use, depth=15)) == [0, 1, 0, 1]
    assert parameters(_input_parameter_roots(context, node, use, depth=16)) == [0, 1]
    assert _input_parameter_roots(context, {}, use, depth=16) == []
    assert parameters(_input_parameter_roots(context, identifier('p'), use, seen=(('p', None),))) == [0]
    assert _input_binding_mention(context, node, use, 'a', 'a:2', seen=(('a', 'a:2'),))
    seen = (('unrelated', None), ('a', 'a:2'))
    assert _input_parameter_roots(context, node, use, seen=seen) == []
    assert _input_parameter_roots(context, node, use, seen=tuple(reversed(seen))) == []


def test_consumer_analysis_does_not_change_evidence():
    evidence = function([variable('a', 2, identifier('p'))])
    original = deepcopy(evidence)
    _input_uses(evidence, lambda event: True)
    assert evidence == original


def test_only_reachable_ancestry_can_block_provenance():
    b = variable('b', 2, identifier('p'))
    a = variable('a', 4, identifier('b'))
    context = _QueryFunction(function([b, a]))
    node, use = identifier('a'), {'byte_start': 20}
    assert parameters(_input_parameter_roots(context, node, use, seen=(('unrelated', None),))) == [0]
    assert parameters(_input_parameter_roots(context, node, use, seen=(('another', None),))) == [0]
    assert _input_parameter_roots(context, node, use, seen=(('b', 'b:2'),)) == []
    assert parameters(_input_parameter_roots(context, node, use, seen=(('b', 'b:2'),), depth=15)) == [0, 1]
    assert _input_binding_mention(context, node, use, 'p', None, seen=(('unrelated', None),))
    assert not _input_binding_mention(context, node, use, 'p', None, seen=(('b', 'b:2'),))
