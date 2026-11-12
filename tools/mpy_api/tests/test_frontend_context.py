"""Strict syntax ancestry, binding scopes and byte-qualified evaluation contexts."""
from tree_sitter import Language, Parser
import tree_sitter_c

from mpy_api.frontend import _context_index, canonical, syntax_adapter


REPRESENTATIVE_SOURCE = """typedef int value_t;
int prototype(int callback(int nested), int value);
int selected(int input, int callback(int nested)) {
    typedef long value_t;
    value_t result = input;
    for (int index = 0; index < input; ++index) {
        while (result) {
            do {
                result = input && callback(input) || (input ? callback(result) : callback(index));
                { int input = index; result += input; }
            } while (result > 2);
            break;
        }
    }
    return result;
}
int legacy(first, second) int second; int first; { return first + second; }
"""


def nested_branches(depth):
    return ('int nested(int value) {\n'
            + ''.join(f'if (value == {index}) {{ return {index}; }} else\n' for index in range(depth))
            + 'return value;\n}\n')


def nodes(tree):
    cursor = tree.walk()
    while True:
        yield cursor.node
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return


def chain(head):
    result = []
    while head:
        result.append(head[:-1])
        head = head[-1]
    return result


def assert_contexts(source):
    data = source.encode()
    tree = Parser(Language(tree_sitter_c.language())).parse(data)
    text = lambda node: canonical(data[node.start_byte:node.end_byte].decode()) if node else ''
    contexts = _context_index(tree, text)
    for node in nodes(tree):
        parents = []
        parent = node.parent
        while parent:
            parents.append(parent)
            parent = parent.parent
        context = contexts[node.id]
        assert context.scope == next((parent for parent in parents if parent.type in
                                     ('compound_statement', 'function_definition', 'for_statement')), None)
        assert context.function_definition == next((parent for parent in parents
                                                    if parent.type == 'function_definition'), None)
        assert context.function_declarator == next((parent for parent in parents
                                                    if parent.type == 'function_declarator'), None)
        assert [item[0] for item in chain(context.loops)] == [
            parent.start_byte for parent in parents
            if parent.type in ('for_statement', 'while_statement', 'do_statement')]
        expected = []
        for parent in parents:
            consequence = (parent.child_by_field_name('consequence')
                           or parent.child_by_field_name('alternative')) if parent.type == 'conditional_expression' else None
            if (consequence and node.start_byte >= consequence.start_byte) or (
                    parent.type == 'binary_expression' and text(parent.child_by_field_name('operator')) in ('&&', '||')
                    and parent.child_by_field_name('right').start_byte <= node.start_byte):
                expected.append(parent.start_byte)
        assert [start for start, threshold in chain(context.conditionals) if node.start_byte >= threshold] == expected
        if node.type == 'parameter_declaration':
            expected_scope = (next((parent for parent in parents if parent.type == 'function_definition'), None)
                              or next((parent for parent in parents if parent.type == 'function_declarator'), None))
            assert (context.function_definition or context.function_declarator) == expected_scope


def test_contexts_match_strict_ancestor_and_byte_predicates():
    assert_contexts(REPRESENTATIVE_SOURCE)
    assert_contexts('int f(int a) { return (a ? (a && f(a)) : (a || f(a))); }')
    assert_contexts('int f(int a) { return a ? : f(a); }')
    assert_contexts('int f(int a) { if (a) { return a; }')


def test_increasing_else_if_depths_keep_complete_function_facts():
    physical = '/fixture/frontend-context.c'
    for depth in (1, 8, 32, 64, 128, 238):
        source = nested_branches(depth)
        assert_contexts(source)
        origins = [(physical, line) for line in range(1, len(source.splitlines()) + 1)]
        declarations, functions, errors, evidence = syntax_adapter(
            source, origins, physical, {'dependencies': {physical: 'fixture'}})
        assert not declarations and not errors
        function = functions[0]
        assert function['name'] == 'nested'
        assert len(function['events']['conditions']) == depth
        assert len(function['events']['returns']) == depth + 1
        assert all(not event['loops'] and not event['conditional_evaluation']
                   for event in function['events']['returns'])


def test_frontend_events_keep_innermost_loop_order_and_conditional_branches():
    physical = '/fixture/frontend-context.c'
    source = REPRESENTATIVE_SOURCE
    origins = [(physical, line) for line in range(1, len(source.splitlines()) + 1)]
    _, functions, errors, _ = syntax_adapter(source, origins, physical, {'dependencies': {physical: 'fixture'}})
    assert not errors
    selected = next(function for function in functions if function['name'] == 'selected')
    calls = selected['events']['calls']
    assert [call['expression'] for call in calls] == [
        'callback ( input )', 'callback ( result )', 'callback ( index )']
    expected_loops = [source.index('do {'), source.index('while (result)'), source.index('for (int index')]
    assert all(call['loops'] == expected_loops for call in calls)
    short_circuit = source.index('input &&')
    conditional = source.index('input ?')
    assert calls[0]['conditional_evaluation'] == [short_circuit]
    assert calls[1]['conditional_evaluation'] == [conditional, short_circuit]
    assert calls[2]['conditional_evaluation'] == [conditional, short_circuit]
    assert selected['events']['returns'][0]['loops'] == []
