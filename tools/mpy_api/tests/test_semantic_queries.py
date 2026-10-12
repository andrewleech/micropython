"""Function-local provenance indexes preserve bounded query context and ordering."""
from copy import deepcopy

from mpy_api.semantics import (
    _QueryFunction, _input_binding_mention, _input_parameter_roots, _input_uses, _local,
)


def identifier(name):
    return {'kind': 'identifier', 'expression': name}


def variable(name, point, initial=None, declaration=None, scope=(0, 1000), shape=None):
    record = {'name': name, 'byte_start': point, 'scope_start': scope[0], 'scope_end': scope[1],
              'declaration_id': declaration or f'{name}:{point}', 'initializer_node': initial,
              'guards': [], 'declaration_specifiers': 'int', 'source_span': span(point)}
    if shape == 'scalar':
        record.update(type_expression='int', declarator_expression=name, array_dimensions=[])
    elif shape == 'pointer':
        record.update(type_expression='int', declarator_expression=f'* {name}', array_dimensions=[])
    elif shape == 'array':
        record.update(type_expression='int', declarator_expression=f'{name} [ 4 ]',
                      array_dimensions=[{'expression': '4'}])
    return record


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
    return list(roots)


def reference_parameters(references):
    return [reference['parameter'] for reference in references]


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


def test_roots_keep_initializer_write_order_and_report_each_root_once():
    local = variable('a', 2, identifier('q'))
    assignments = [write('a', 10, identifier('p')), write('a', 15, identifier('q'))]
    control = {'control_kind': 'while_statement', 'expression_node': identifier('a'),
               'byte_start': 30, 'byte_end': 31, 'guards': [], 'source_span': span(30)}
    context = _QueryFunction(function([local], assignments, controls=[control]))
    assert parameters(_input_parameter_roots(context, control['expression_node'], control)) == [1, 0]
    uses = _input_uses(context, lambda event: True)
    controlling = [use for use in uses if use.get('reason') ==
                   'native controlling-expression test requires an accepted-domain proof']
    assert reference_parameters([use['source_ref'] for use in controlling]) == [1, 0]


def test_call_consumers_and_controlling_tests_keep_first_root_order():
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
    assert reference_parameters(operands) == [1, 0]
    assert reference_parameters(controlling) == [1, 0]


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
    # The alias path reaches p, so the in-interval indirect store contributes its value roots.
    assert _input_binding_mention(context, indirect, writes[1], 'p', None)
    assert parameters(_input_parameter_roots(context, writes[1]['value_node'], writes[1])) == [0]
    assert parameters(_input_parameter_roots(context, identifier('a'), {'byte_start': 20})) == [0, 1]


def test_cycle_seen_and_depth_cutoff_keep_distinct_answers():
    local = variable('a', 2, identifier('p'))
    context = _QueryFunction(function([local], [write('a', 10, identifier('a'))]))
    node, use = identifier('a'), {'byte_start': 20}
    assert parameters(_input_parameter_roots(context, node, use)) == [0]
    assert _input_parameter_roots(context, node, use, seen=(('a', 'a:2'),)) == ()
    assert parameters(_input_parameter_roots(context, node, use, depth=15)) == [0, 1]
    assert parameters(_input_parameter_roots(context, node, use, depth=16)) == [0, 1]
    assert _input_parameter_roots(context, {}, use, depth=16) == ()
    assert parameters(_input_parameter_roots(context, identifier('p'), use, seen=(('p', None),))) == [0]
    assert _input_binding_mention(context, node, use, 'a', 'a:2', seen=(('a', 'a:2'),))
    seen = (('unrelated', None), ('a', 'a:2'))
    assert _input_parameter_roots(context, node, use, seen=seen) == ()
    assert _input_parameter_roots(context, node, use, seen=tuple(reversed(seen))) == ()


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
    assert _input_parameter_roots(context, node, use, seen=(('b', 'b:2'),)) == ()
    assert parameters(_input_parameter_roots(context, node, use, seen=(('b', 'b:2'),), depth=15)) == [0, 1]
    assert _input_binding_mention(context, node, use, 'p', None, seen=(('unrelated', None),))
    assert not _input_binding_mention(context, node, use, 'p', None, seen=(('b', 'b:2'),))


def test_indirect_store_admissions_do_not_multiply_controlling_rows():
    base = variable('b', 2, identifier('p'))
    allocation = {'kind': 'call_expression', 'expression': 'alloc ( b )',
                  'arguments': {'kind': 'argument_list', 'children': [identifier('b')]}}
    slots = variable('o', 4, allocation)
    stores = [write('* o', 10 + 2 * index, identifier('q' if index % 2 else 'b'),
                    target_node={'kind': 'field_expression', 'operator': '->', 'expression': 'o -> f',
                                 'argument': identifier('o')})
              for index in range(12)]
    control = {'control_kind': 'if_statement', 'expression_node': identifier('b'),
               'byte_start': 60, 'byte_end': 61, 'guards': [], 'source_span': span(60)}
    context = _QueryFunction(function([base, slots], stores, controls=[control]))
    assert parameters(_input_parameter_roots(context, control['expression_node'], control)) == [0, 1]
    controlling = [use['source_ref'] for use in _input_uses(context, lambda event: True)
                   if use.get('reason') == 'native controlling-expression test requires an accepted-domain proof']
    assert reference_parameters(controlling) == [0, 1]


def test_repeated_root_queries_return_the_memoised_tuple():
    local = variable('a', 2, identifier('p'))
    assignments = [write('a', 10 + index, identifier('q' if index % 2 else 'p')) for index in range(40)]
    context = _QueryFunction(function([local], assignments))
    node, use = identifier('a'), {'byte_start': 100}
    roots = _input_parameter_roots(context, node, use)
    assert roots == (0, 1)
    assert _input_parameter_roots(context, node, use) is roots


def test_input_uses_are_distinct_by_value_across_overlapping_events():
    local = variable('a', 2, identifier('p'))
    operation = {'kind': 'binary_expression', 'expression': 'a + 1', 'left': identifier('a'),
                 'right': {'kind': 'number_literal', 'expression': '1'}}
    then = {'condition_id': 5, 'condition': 'flag', 'branch': 'then'}

    def view(guards):
        return {'expression_node': operation, 'byte_start': 20, 'byte_end': 25, 'guards': list(guards),
                'source_span': span(20)}
    evidence = function([local])
    evidence['events']['expressions'] = [view([then]), view([then]), view([])]
    uses = _input_uses(evidence, lambda event: True)
    derived = [use for use in uses if use.get('reason') == 'non-call derived operation requires an accepted-domain proof']
    assert [(use['source_ref']['parameter'], use['guards']) for use in derived] == [(0, [then]), (0, [])]


def address(name):
    return {'kind': 'pointer_expression', 'operator': '&', 'expression': f'& {name}', 'argument': identifier(name)}


def number(text='1'):
    return {'kind': 'number_literal', 'expression': text}


def conditional(condition, consequence, alternative):
    return {'kind': 'conditional_expression', 'expression': 'c ? a : b', 'condition': condition,
            'consequence': consequence, 'alternative': alternative}


def binary(operator, left, right):
    return {'kind': 'binary_expression', 'operator': operator, 'expression': f'l {operator} r',
            'left': left, 'right': right}


def member(name):
    return {'kind': 'field_expression', 'operator': '->', 'expression': f'{name} -> f', 'argument': identifier(name)}


def call_node(callee, *arguments):
    expression = f'{callee} ( {" , ".join(argument["expression"] for argument in arguments)} )'
    return {'kind': 'call_expression', 'expression': expression, 'function': identifier(callee),
            'arguments': {'kind': 'argument_list', 'children': list(arguments)}}


def call_event(callee, point, *arguments):
    node = call_node(callee, *arguments)
    return {'expression': node['expression'], 'expression_node': node, 'callee': None, 'byte_start': point,
            'byte_end': point + 8, 'guards': [], 'source_span': span(point)}


def test_out_parameter_call_defines_the_addressed_binding():
    count = variable('n', 2, shape='scalar')
    fetch = call_event('fetch', 10, identifier('p'), address('n'))
    context = _QueryFunction(function([count], calls=[fetch]))
    assert parameters(_input_parameter_roots(context, identifier('n'), {'byte_start': 30})) == [0]
    # The callee defines after its operands are evaluated: earlier uses and the call itself do not see it.
    assert _input_parameter_roots(context, identifier('n'), {'byte_start': 5}) == ()
    assert _input_parameter_roots(context, identifier('n'), fetch) == ()


def test_out_parameter_definition_reaches_input_use_rows():
    count = variable('n', 2, shape='scalar')
    fetch = call_event('fetch', 10, identifier('p'), address('n'))
    control = {'control_kind': 'if_statement', 'expression_node': identifier('n'),
               'byte_start': 30, 'byte_end': 31, 'guards': [], 'source_span': span(30)}
    uses = _input_uses(function([count], calls=[fetch], controls=[control]), lambda event: True)
    controlling = [use['source_ref'] for use in uses if use.get('reason') ==
                   'native controlling-expression test requires an accepted-domain proof']
    assert reference_parameters(controlling) == [0]


def test_read_inside_a_calls_own_operands_does_not_see_its_definition():
    count = variable('n', 2, shape='scalar')
    fetch = call_event('fetch', 10, identifier('p'), address('n'))
    statement = {'byte_start': 8, 'byte_end': 30, 'guards': [], 'source_span': span(8)}
    inside = {**identifier('n'), 'byte_start': 15}
    after = {**identifier('n'), 'byte_start': 25}
    context = _QueryFunction(function([count], calls=[fetch]))
    assert _input_parameter_roots(context, inside, statement) == ()
    assert parameters(_input_parameter_roots(context, after, statement)) == [0]


def test_out_parameter_through_pointer_alias_array_decay_and_unknown_storage():
    target = variable('x', 2, shape='scalar')
    pointer = variable('ptr', 4, address('x'), shape='pointer')
    buffer = variable('buf', 6, shape='array')
    fill = call_event('fill', 10, identifier('ptr'), identifier('q'))
    copy = call_event('copy', 12, identifier('buf'), identifier('p'))
    # `table` has no declaration record, so it may be an array decaying to its address.
    unknown = call_event('load', 14, identifier('table'), identifier('q'))
    context = _QueryFunction(function([target, pointer, buffer], calls=[fill, copy, unknown]))
    use = {'byte_start': 40}
    assert parameters(_input_parameter_roots(context, identifier('x'), use)) == [1]
    assert parameters(_input_parameter_roots(context, identifier('buf'), use)) == [0]
    assert parameters(_input_parameter_roots(context, identifier('table'), use)) == [1]


def test_by_value_operands_do_not_define_the_binding():
    count = variable('n', 2, shape='scalar')
    pointer = variable('ptr', 3, shape='pointer')
    consume = call_event('consume', 10, identifier('n'), identifier('ptr'), identifier('p'))
    context = _QueryFunction(function([count, pointer], calls=[consume]))
    use = {'byte_start': 30}
    assert _input_parameter_roots(context, identifier('n'), use) == ()
    assert _input_parameter_roots(context, identifier('ptr'), use) == ()


def test_sibling_out_parameters_are_sources_of_each_other():
    first = variable('a', 2, shape='scalar')
    second = variable('b', 3, identifier('q'), shape='scalar')
    swap = call_event('swap', 10, address('a'), address('b'))
    context = _QueryFunction(function([first, second], calls=[swap]))
    use = {'byte_start': 30}
    assert parameters(_input_parameter_roots(context, identifier('a'), use)) == [1]
    assert parameters(_input_parameter_roots(context, identifier('b'), use)) == [1]


def test_pointer_binding_defined_by_an_out_parameter_aliases_the_other_operands():
    obj = variable('obj', 2, identifier('p'), shape='pointer')
    items = variable('items', 3, shape='pointer')
    fetch = call_event('fetch', 10, identifier('obj'), address('items'))
    store = write('* items', 20, identifier('q'), target_node=member('items'))
    context = _QueryFunction(function([obj, items], [store], [fetch]))
    assert _input_binding_mention(context, store['target_node'], store, 'obj', 'obj:2')
    assert parameters(_input_parameter_roots(context, identifier('obj'), {'byte_start': 40})) == [0, 1]


def test_store_defines_a_binding_only_through_its_base_pointer_not_a_size_mention():
    count = variable('n', 2, identifier('p'), shape='scalar')
    flag = variable('flag', 3, identifier('p'), shape='pointer')
    size = binary('*', number('8'), binary('+', identifier('n'), conditional(identifier('flag'), number(), number('0'))))
    slots = variable('o', 5, call_node('alloc', size), shape='pointer')
    store = write('* o', 12, identifier('q'), target_node=member('o'))
    context = _QueryFunction(function([count, flag, slots], [store]))
    use = {'byte_start': 40}
    target = store['target_node']
    # n is a by-value integer in the size and flag only selects a branch: neither is the base pointer or an alias.
    assert not _input_binding_mention(context, target, store, 'n', 'n:2')
    assert not _input_binding_mention(context, target, store, 'flag', 'flag:3')
    assert parameters(_input_parameter_roots(context, identifier('n'), use)) == [0]
    assert parameters(_input_parameter_roots(context, identifier('flag'), use)) == [0]
    assert _input_binding_mention(context, target, store, 'o', 'o:5')
    assert parameters(_input_parameter_roots(context, identifier('o'), use)) == [0, 1]


def test_alias_flows_follow_assignment_and_conditional_branches_but_not_the_condition():
    base = variable('base', 2, identifier('p'), shape='pointer')
    other = variable('other', 3, identifier('q'), shape='pointer')
    alias = variable('alias', 5, conditional(identifier('guard'), identifier('base'), identifier('other')), shape='pointer')
    guard = variable('guard', 4, shape='pointer')
    store = write('* alias', 12, identifier('q'), target_node=member('alias'))
    context = _QueryFunction(function([base, other, guard, alias], [store]))
    target = store['target_node']
    assert _input_binding_mention(context, target, store, 'base', 'base:2')
    assert _input_binding_mention(context, target, store, 'other', 'other:3')
    assert not _input_binding_mention(context, target, store, 'guard', 'guard:4')


def test_comparison_logical_and_not_operands_carry_no_pointer():
    base = variable('base', 2, identifier('p'), shape='pointer')
    context_nodes = {
        'less': binary('<', identifier('base'), number('4')),
        'and': binary('&&', identifier('base'), number('1')),
        'not': {'kind': 'unary_expression', 'operator': '!', 'expression': '! base', 'argument': identifier('base')},
        'sizeof': {'kind': 'sizeof_expression', 'expression': 'sizeof ( base )', 'argument': identifier('base')},
    }
    arithmetic = binary('+', identifier('base'), number('4'))
    for label, expression in context_nodes.items():
        alias = variable('alias', 5, expression, shape='pointer')
        store = write('* alias', 12, identifier('q'), target_node=member('alias'))
        context = _QueryFunction(function([base, alias], [store]))
        assert not _input_binding_mention(context, store['target_node'], store, 'base', 'base:2'), label
    alias = variable('alias', 5, arithmetic, shape='pointer')
    store = write('* alias', 12, identifier('q'), target_node=member('alias'))
    context = _QueryFunction(function([base, alias], [store]))
    assert _input_binding_mention(context, store['target_node'], store, 'base', 'base:2')


def subscript(name, index='0'):
    return {'kind': 'subscript_expression', 'expression': f'{name} [ {index} ]', 'argument': identifier(name),
            'index': number(index)}


def alias_chain(length):
    """x scalar, p1 = &x, p2 = p1, ..., fill(p<length>, q); returns the context."""
    target = variable('x', 2, shape='scalar')
    pointers = [variable('p1', 3, address('x'), shape='pointer')]
    pointers += [variable(f'p{index}', 2 + index, identifier(f'p{index - 1}'), shape='pointer')
                 for index in range(2, length + 1)]
    fill = call_event('fill', 100, identifier(f'p{length}'), identifier('q'))
    return _QueryFunction(function([target, *pointers], calls=[fill]))


def test_out_parameter_through_alias_chains_at_and_around_the_alias_depth():
    # The address is taken at alias depth length: the prefilter must still find it at every length up to the bound.
    for length in range(10, 19):
        context = alias_chain(length)
        # Past the bound the call is assumed to reach every binding; either way the definition is found.
        assert parameters(_input_parameter_roots(context, identifier('x'), {'byte_start': 200})) == [1], length


def test_array_element_passed_by_value_does_not_define_the_array():
    buffer = variable('arr', 2, shape='array')
    consume = call_event('consume', 10, subscript('arr'), identifier('q'))
    member = call_event('consume', 12, {'kind': 'field_expression', 'operator': '.', 'expression': 'arr [ 0 ] . f',
                                        'argument': subscript('arr')}, identifier('q'))
    decay = call_event('fill', 14, identifier('arr'), identifier('p'))
    use = {'byte_start': 40}
    assert _input_parameter_roots(_QueryFunction(function([buffer], calls=[consume])), identifier('arr'), use) == ()
    assert _input_parameter_roots(_QueryFunction(function([buffer], calls=[member])), identifier('arr'), use) == ()
    assert parameters(_input_parameter_roots(_QueryFunction(function([buffer], calls=[decay])), identifier('arr'), use)) == [0]
    element = call_event('fill', 16, {'kind': 'pointer_expression', 'operator': '&', 'expression': '& arr [ 1 ]',
                                      'argument': subscript('arr', '1')}, identifier('q'))
    assert parameters(_input_parameter_roots(_QueryFunction(function([buffer], calls=[element])), identifier('arr'), use)) == [1]


def test_operand_past_the_alias_bound_may_define_any_binding():
    far = alias_chain(17)
    unrelated = variable('y', 1, shape='scalar')
    context = _QueryFunction(function([unrelated, *far.function['events']['variables']], calls=far.function['events']['calls']))
    assert parameters(_input_parameter_roots(context, identifier('y'), {'byte_start': 200})) == [1]
    near = _QueryFunction(function([unrelated, *alias_chain(5).function['events']['variables']],
                                   calls=alias_chain(5).function['events']['calls']))
    assert _input_parameter_roots(near, identifier('y'), {'byte_start': 200}) == ()


def test_bounded_operand_that_does_not_name_the_binding_stays_a_source():
    # `c` is a pointer whose initialiser is a 14-link alias chain from parameter p; `f(c - v, w)` reads c by value next
    # to v (from q). The operand's alias flow runs past the bound through c's own chain, so the call may define any
    # binding, but the exact query for c on that operand is False (c is not a pointer into itself). The operand must
    # remain a source of what the call stores, otherwise c would lose the roots the call's other operands carry.
    chain = [variable('z0', 1, identifier('p'), shape='pointer')]
    chain += [variable(f'z{index}', 1 + index, identifier(f'z{index - 1}'), shape='pointer') for index in range(1, 14)]
    held = variable('c', 40, identifier('z13'), shape='pointer')
    other = variable('v', 41, identifier('q'), shape='pointer')
    call = call_event('f', 100, binary('-', identifier('c'), identifier('v')), identifier('w'))
    context = _QueryFunction(function([*chain, held, other], calls=[call]))
    assert parameters(_input_parameter_roots(context, identifier('c'), {'byte_start': 200})) == [0, 1]


def test_array_element_in_a_call_operand_is_not_an_alias_origin_of_the_array():
    buffer = variable('arr', 2, shape='array')
    held = variable('v', 3, identifier('q'), shape='pointer')
    consume = call_event('consume', 10, subscript('arr'), identifier('v'))
    store = write('* arr', 30, identifier('p'), target_node={
        'kind': 'field_expression', 'operator': '->', 'expression': 'arr [ 1 ] -> f', 'argument': subscript('arr', '1')})
    context = _QueryFunction(function([buffer, held], [store], [consume]))
    assert parameters(_input_parameter_roots(context, identifier('v'), {'byte_start': 60})) == [1]
