"""Source-derived bounded contracts and public composition regressions."""
from copy import deepcopy

import pytest

from mpy_api.frontend import syntax_adapter
from mpy_api.semantics import bind_public_contracts, semantics, checked_receiver_authority, lexical_constant
from mpy_api.bindings import definitions as source_definitions


HEADER = """typedef unsigned long mp_obj_t;
typedef long mp_int_t;
typedef unsigned long mp_uint_t;
typedef unsigned long size_t;
typedef struct { mp_obj_t (*slot_index_unary_op)(int, mp_obj_t); int (*slot_index_buffer)(mp_obj_t, void *, int); } mp_obj_type_t;
typedef struct { const mp_obj_type_t *type; } mp_obj_base_t;
typedef unsigned short mpz_dig_t;
typedef struct { mpz_dig_t *dig; size_t len; int neg; } mpz_t;
typedef struct { mp_obj_base_t base; mpz_t mpz; } mp_obj_int_t;
extern const mp_obj_type_t mp_type_int, mp_type_str, mp_type_bytes, mp_type_bytearray, mp_type_tuple, mp_type_list;
enum { MP_UNARY_OP_BOOL, MP_UNARY_OP_INT_MAYBE };
void *m_malloc(size_t size);
int mp_obj_is_obj(mp_obj_t o);
mp_obj_t mp_obj_len_maybe(mp_obj_t arg);
int mp_obj_get_int_maybe(mp_obj_t arg, mp_int_t *value);
mp_int_t mp_obj_int_get_checked(mp_obj_t value);
mp_obj_t mp_unary_op(int op, mp_obj_t value);
int mp_get_buffer(mp_obj_t obj, void *bufinfo, int flags);
void mp_raise_TypeError(const char *message);
void mp_raise_ValueError(const char *message);
void mp_obj_get_array(mp_obj_t o, size_t *len, mp_obj_t **items);
void mp_obj_get_array_fixed_n(mp_obj_t o, size_t len, mp_obj_t **items);
void mp_obj_tuple_get(mp_obj_t o, size_t *len, mp_obj_t **items);
void mp_obj_list_get(mp_obj_t o, size_t *len, mp_obj_t **items);
int mp_obj_is_true(mp_obj_t value);
mp_int_t mp_obj_get_int(mp_obj_t value);
mp_obj_t mp_obj_new_bool(int value);
mp_obj_t mp_obj_new_int(mp_int_t value);
mp_obj_t mp_obj_new_str(const char *value, size_t length);
mp_obj_t mp_obj_new_bytes(const char *value, size_t length);
mp_obj_t mp_obj_new_list(size_t count, const mp_obj_t *items);
mp_obj_t mp_obj_new_bytearray(size_t length, const void *value);
mp_obj_t mp_obj_new_tuple(size_t count, const mp_obj_t *items);
const mp_obj_type_t *mp_obj_get_type(mp_obj_t value);
mp_obj_t mp_obj_new_str_of_type(const mp_obj_type_t *type, const char *value, size_t length);
mp_obj_t mp_obj_new_str_type_from_vstr(const mp_obj_type_t *type, void *value);
void mp_obj_list_append(mp_obj_t list, mp_obj_t value);
void mp_get_buffer_raise(mp_obj_t value, void *buffer, int flags);
void mp_raise_OverflowError(const char *message);
void *mp_obj_malloc_helper(size_t size, const mp_obj_type_t *type);
void opaque(mp_obj_t *value);
void consume(mp_obj_t value);
"""
CONTRACTS = {'none': '99', 'true': '97', 'false': '98', 'null': '0',
             'rom_int': '( ( mp_obj_t ) ( ( ( ( mp_uint_t ) ( 7 ) ) << 1 ) | 1 ) )',
             'obj_reference': '( ( mp_obj_t ) ( & mp_type_type ) )',
             'buffer_read': '1', 'buffer_write': '2'}
HELPER_BODIES = [
    ('/fixture/py/obj.c', '''
int mp_obj_is_true(mp_obj_t arg) {
    if (arg == 98) return 0;
    if (arg == 97) return 1;
    if (arg == 99) return 0;
    const mp_obj_type_t *type = mp_obj_get_type(arg);
    if (type->slot_index_unary_op) {
        mp_obj_t result = type->slot_index_unary_op(MP_UNARY_OP_BOOL, arg);
        if (result != 0) return result == 97;
    }
    mp_obj_t len = mp_obj_len_maybe(arg);
    if (len != 0) return len != ((mp_obj_t)((((mp_uint_t)(0)) << 1) | 1));
    else return 1;
}
static void mp_raise_TypeError_int_conversion(mp_obj_t arg) { mp_raise_TypeError("conversion"); }
int mp_obj_get_int_maybe(mp_obj_t arg, mp_int_t *value) {
    if (mp_obj_get_type(arg) == &mp_type_int) { *value = mp_obj_int_get_checked(arg); return 1; }
    arg = mp_unary_op(MP_UNARY_OP_INT_MAYBE, (mp_obj_t)arg);
    if (arg != 0) { *value = mp_obj_int_get_checked(arg); return 1; }
    return 0;
}
mp_int_t mp_obj_get_int(mp_obj_t arg) {
    mp_int_t val;
    if (!mp_obj_get_int_maybe(arg, &val)) mp_raise_TypeError_int_conversion(arg);
    return val;
}
void *mp_obj_malloc_helper(size_t num_bytes, const mp_obj_type_t *type) {
    mp_obj_base_t *base = (mp_obj_base_t *)m_malloc(num_bytes);
    base->type = type;
    return base;
}
int mp_get_buffer(mp_obj_t obj, void *bufinfo, int flags) {
    const mp_obj_type_t *type = mp_obj_get_type(obj);
    if (type->slot_index_buffer && type->slot_index_buffer(obj, bufinfo, flags & (1 | 2)) == 0) return 1;
    if (flags & 4) mp_raise_TypeError("buffer");
    return 0;
}
void mp_obj_get_array(mp_obj_t o, size_t *len, mp_obj_t **items) {
    if (mp_obj_get_type(o) == &mp_type_tuple) mp_obj_tuple_get(o, len, items);
    else if (mp_obj_get_type(o) == &mp_type_list) mp_obj_list_get(o, len, items);
    else mp_raise_TypeError("array");
}
void mp_obj_get_array_fixed_n(mp_obj_t o, size_t len, mp_obj_t **items) {
    size_t seq_len;
    mp_obj_get_array(o, &seq_len, items);
    if (seq_len != len) mp_raise_ValueError("length");
}
'''),
    ('/fixture/py/obj.h', '''
void mp_get_buffer_raise(mp_obj_t obj, void *bufinfo, int flags) { mp_get_buffer(obj, bufinfo, flags | 4); }
'''),
    ('/fixture/py/objint.c', '''
mp_int_t mp_obj_int_get_checked(mp_obj_t value) { return ((mp_int_t)value) >> 1; }
'''),
    ('/fixture/py/objstr.c', '''
mp_obj_t mp_obj_new_str_of_type(const mp_obj_type_t *type, const char *data, size_t len) {
    if (type == &mp_type_str) return mp_obj_new_str(data, len);
    if (type == &mp_type_bytearray) return mp_obj_new_bytearray(len, data);
    return mp_obj_new_bytes(data, len);
}
'''),
]


def parsed(source, helper_bodies=HELPER_BODIES, physical='/fixture/fixture.c'):
    chunks = [('/fixture/py/runtime.h', HEADER), *helper_bodies, (physical, source)]
    code, origins = '', []
    for path, text in chunks:
        text = text.rstrip() + '\n'
        for number, line in enumerate(text.splitlines(), 1):
            origin = '/fixture/py/misc.h' if path.endswith('/runtime.h') and 'm_malloc(' in line else path
            origins.append((origin, number))
        code += text
    dependencies = {path: 'fixture' for path, _ in chunks}
    dependencies['/fixture/py/misc.h'] = 'fixture'
    _, functions, errors, _ = syntax_adapter(code, origins, physical,
        {'dependencies': dependencies, 'project_dependencies': list(dependencies)})
    assert not errors
    by_name = {}
    for function in functions:
        by_name.setdefault(function['name'], []).append(function)
        for call in function['events']['calls']:
            call['qualified_template_sources'] = list(dependencies)
    return by_name


def fixture_links(functions):
    """Join actual fixture declarations to their unique supplied source definition."""
    linked = {}
    for definitions in functions.values():
        for function in definitions:
            for call in function['events']['calls']:
                name = (call.get('callee') or {}).get('name')
                candidates = functions.get(name, [])
                if (call.get('callee') or {}).get('kind') != 'FUNCTION_DECL' or len(candidates) != 1:
                    continue
                definition = candidates[0]
                linked.setdefault(name, []).append({'function': definition, 'declaration_identity': call['callee'],
                    'authority': {'resolution': 'bounded-resolved', 'kind': 'fixture-unique-owned-source-definition',
                                  'source_span': definition['source_span']}})
    return linked


def analyse(source, name='probe', specialization=None, helper_bodies=HELPER_BODIES):
    functions = parsed(source, helper_bodies)
    function = functions[name][0]
    return function, semantics(function, functions, [], CONTRACTS, specialization, linked_functions=fixture_links(functions))


def bindings(function, receiver=None):
    return {'function_id': function['id'], 'parameters': [
        {'name': 'arg' + str(index), 'required': True, 'role': 'public',
         'source_refs': [{'kind': 'c-parameter', 'scope': function['id'], 'parameter': index}],
         'default_contract': None, 'binding_authority': {'resolution': 'bounded-resolved'}}
        for index in range(len(function['parameters']))], 'receiver': receiver}


def public(source):
    function, summary = analyse(source)
    return bind_public_contracts(summary, bindings(function))


def test_alias_identity_is_source_bound_and_composition_is_pure():
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; return alias; }')
    binding = bindings(function)
    before = deepcopy((summary, binding))
    result = bind_public_contracts(summary, binding)
    assert (summary, binding) == before
    assert result['parameters'] == ['value']
    assert result['returns'][0]['exported_argument'] == 'arg0'
    assert result['returns'][0]['source_ref']['alias_evidence']
    assert result['public_parameters'][0]['accepted_domain']['contract'] == {'kind': 'object'}


@pytest.mark.parametrize('body', [
    'mp_obj_t alias = value; alias = 99; return alias;',
    'mp_obj_t alias = value; if (mp_obj_is_true(value)) alias = 99; return alias;',
    'mp_obj_t alias = value; opaque(&alias); return alias;',
    'mp_obj_t alias = value; consume(alias); return alias;',
    'mp_obj_t alias; if (mp_obj_is_true(value)) alias = value; return alias;',
    'mp_obj_t alias = value; while (mp_obj_is_true(value)) alias = 99; return alias;',
])
def test_alias_mutation_escape_and_nondominating_definition_do_not_claim_identity(body):
    result = public('mp_obj_t probe(mp_obj_t value) { ' + body + ' }')
    assert result['return_resolution'] == 'unresolved'
    assert all(contract.get('exported_argument') != 'arg0' for contract in result['returns'])


def test_lexical_shadow_has_its_own_definition():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; { mp_obj_t alias = 99; return alias; } }')
    assert result['return_resolution'] == 'bounded-resolved'
    assert result['returns'][0]['kind'] == 'None'
    assert 'exported_argument' not in result['returns'][0]


def test_unrelated_inner_shadow_write_does_not_kill_outer_identity():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; { mp_obj_t alias = 99; alias = 99; } return alias; }')
    assert result['returns'][0]['exported_argument'] == 'arg0'


@pytest.mark.parametrize(('expression', 'kind', 'domain'), [
    ('mp_obj_new_bool(mp_obj_is_true(alias))', 'bool', 'object'),
    ('mp_obj_new_int(mp_obj_get_int(alias))', 'int', 'integer-conversion-capability'),
])
def test_alias_conversions_keep_supplied_domain_separate(expression, kind, domain):
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; return ' + expression + '; }')
    assert result['return_resolution'] == 'bounded-resolved'
    assert result['returns'][0]['kind'] == kind
    accepted = result['public_parameters'][0]['accepted_domain']
    assert accepted['contract']['kind'] == domain
    if domain != 'object':
        assert accepted['representation_resolution'] == 'unresolved'


def test_conditional_conversion_is_a_guarded_constraint_not_whole_domain():
    result = public('mp_obj_t probe(mp_obj_t value, mp_obj_t flag) { if (mp_obj_is_true(flag)) return mp_obj_new_int(mp_obj_get_int(value)); return 99; }')
    parameter = result['public_parameters'][0]
    assert parameter['accepted_domain']['resolution'] == 'unresolved'
    assert parameter['constraints'][0]['guards'][0]['branch'] == 'then'
    assert result['return_resolution'] == 'bounded-resolved'


def test_truth_check_cannot_erase_integer_constraint_or_unknown_consumer():
    for operation in ('mp_obj_get_int(value);', 'consume(value);'):
        result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_is_true(value); ' + operation + ' return mp_obj_new_bool(1); }')
        assert result['public_parameters'][0]['accepted_domain']['contract'] != {'kind': 'object'}
        assert result['returns'][0]['kind'] == 'bool'


def test_complementary_paths_close_but_missing_successful_path_does_not():
    closed = public('mp_obj_t probe(mp_obj_t value) { if (mp_obj_is_true(value)) return mp_obj_new_int(1); return mp_obj_new_str("x", 1); }')
    assert closed['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in closed['returns']} == {'int', 'str'}
    assert closed['successful_path_coverage']['resolution'] == 'bounded-resolved'
    incomplete = public('mp_obj_t probe(mp_obj_t value) { if (mp_obj_is_true(value)) return mp_obj_new_int(1); }')
    assert incomplete['return_resolution'] == 'unresolved'


def test_unknown_successful_callee_and_unqualified_raise_are_not_erased():
    result = public('mp_obj_t other(mp_obj_t value); mp_obj_t probe(mp_obj_t value) { if (mp_obj_is_true(value)) return mp_obj_new_int(1); return other(value); }')
    assert result['return_resolution'] == 'unresolved'
    assert any(contract['kind'] == 'unresolved' for contract in result['returns'])


def test_qualified_error_tail_does_not_add_successful_none():
    result = public('mp_obj_t probe(mp_obj_t value) { if (mp_obj_is_true(value)) return mp_obj_new_int(1); mp_raise_OverflowError("overflow"); return 99; }')
    assert result['return_resolution'] == 'bounded-resolved'
    assert [contract['kind'] for contract in result['returns']] == ['int']
    assert result['error_only_exits']


@pytest.mark.parametrize(('count', 'extent', 'resolved'), [(2, 2, True), (1, 2, False), (3, 2, False), (2, 3, False)])
def test_tuple_count_and_accessible_extent_are_independent(count, extent, resolved):
    result = public(f'mp_obj_t probe(mp_obj_t value) {{ mp_obj_t items[{extent}] = {{value, mp_obj_new_int(1)}}; return mp_obj_new_tuple({count}, items); }}')
    assert (result['return_resolution'] == 'bounded-resolved') == resolved
    if resolved:
        assert result['returns'][0]['length'] == 2
        assert result['returns'][0]['elements'][0]['exported_argument'] == 'arg0'


def test_fixed_vector_item_relation_preserves_reordering():
    function, summary = analyse('mp_obj_t probe(size_t count, const mp_obj_t *vector) { return mp_obj_new_tuple(2, (mp_obj_t[]){vector[1], vector[0]}); }')
    binding = {'parameters': [
        {'name': name, 'required': True, 'source_refs': [{'kind': 'vector-item', 'scope': function['id'], 'vector_parameter': 1, 'index': index}],
         'binding_authority': {'resolution': 'bounded-resolved'}} for index, name in enumerate(('first', 'second'))]}
    result = bind_public_contracts(summary, binding)
    assert [element['exported_argument'] for element in result['returns'][0]['elements']] == ['second', 'first']


@pytest.mark.parametrize('native', ['str', 'bytes', 'bytearray'])
def test_native_family_has_no_class_only_identity_or_subtype_certificate(native):
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { const mp_obj_type_t *type = mp_obj_get_type(value); return mp_obj_new_str_of_type(type, "x", 1); }')
    receiver = {'name': 'self', 'native_type': native, 'source_refs': [{'kind': 'c-parameter', 'scope': function['id'], 'parameter': 0}],
                'authority': {'resolution': 'bounded-resolved'}, 'required': True}
    result = bind_public_contracts(summary, {'parameters': [], 'receiver': receiver})
    returned = result['returns'][0]
    assert returned['kind'] == 'same-native-family'
    assert returned['identity'] is False
    assert returned['subtype_preservation'] is False
    assert returned['representation_resolution'] == 'unresolved'
    assert summary['returns'][0]['family_resolution'] == 'bounded-resolved'
    assert 'exported_argument' not in returned


def test_list_builder_tracks_all_append_sites_without_iteration_claim():
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { const mp_obj_type_t *type = mp_obj_get_type(value); mp_obj_t result = mp_obj_new_list(0, 0); while (mp_obj_is_true(value)) { mp_obj_list_append(result, mp_obj_new_str_of_type(type, "x", 1)); } return result; }')
    assert summary['return_resolution'] == 'bounded-resolved'
    assert summary['returns'][0]['kind'] == 'list'
    assert summary['returns'][0]['element_resolution'] == 'bounded-resolved'
    assert summary['returns'][0]['elements'][0]['kind'] == 'same-native-family'


def test_opaque_pointer_cast_never_establishes_integer_type():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_int_t *pointer = (mp_obj_int_t *)value; return ((mp_obj_t)(pointer)); }')
    assert result['return_resolution'] == 'unresolved'


def test_selected_typed_allocation_establishes_integer_wrapping():
    result = public('mp_obj_t probe(void) { mp_obj_int_t *pointer = (mp_obj_int_t *)mp_obj_malloc_helper(sizeof(mp_obj_int_t), &mp_type_int); return ((mp_obj_t)(pointer)); }')
    assert result['return_resolution'] == 'bounded-resolved'
    assert result['returns'][0]['kind'] == 'int'
    assert result['returns'][0]['allocation_authority']


@pytest.mark.parametrize(('flags', 'capability'), [('1', 'readable-buffer-capability'), ('2', 'writable-buffer-capability')])
def test_native_buffer_capability_survives_alias_mode_without_nominal_narrowing(flags, capability):
    result = public('mp_obj_t probe(mp_obj_t value) { int flags = ' + flags + '; int buffer; mp_get_buffer_raise(value, &buffer, flags); return 99; }')
    domain = result['public_parameters'][0]['accepted_domain']
    assert domain['contract'] == {'kind': capability}
    assert domain['representation_resolution'] == 'unresolved'


def test_mode_specialization_composes_helper_results():
    source = 'static mp_obj_t helper(mp_obj_t value, int mode) { if (mode == 1) return mp_obj_new_str("x", 1); return 99; } mp_obj_t probe(mp_obj_t value) { return helper(value, 1); }'
    result = public(source)
    assert result['return_resolution'] == 'bounded-resolved'
    assert [contract['kind'] for contract in result['returns']] == ['str']


def test_public_mapping_is_scope_qualified_and_rejects_conflicts():
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return value; }')
    binding = bindings(function)
    duplicate = deepcopy(binding['parameters'][0])
    duplicate['name'] = 'other'
    binding['parameters'].append(duplicate)
    result = bind_public_contracts(summary, binding)
    assert result['return_resolution'] == 'unresolved'
    binding['parameters'] = binding['parameters'][:1]
    binding['parameters'][0]['source_refs'][0]['scope'] = 'different-function'
    assert bind_public_contracts(summary, binding)['return_resolution'] == 'unresolved'


def test_single_dominating_assignment_is_a_reaching_alias_definition():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias; alias = value; return alias; }')
    assert result['returns'][0]['exported_argument'] == 'arg0'


def test_same_line_repeated_predicates_are_not_complementary_alias_certificates():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; if (mp_obj_is_true(value)) alias = 99; if (mp_obj_is_true(value)) return 99; else return alias; }')
    assert result['return_resolution'] == 'unresolved'
    assert all(contract.get('exported_argument') != 'arg0' for contract in result['returns'])


@pytest.mark.parametrize('operation', [
    'while (mp_obj_is_true(value)) { mp_obj_t local = alias; return local; }',
    'mp_obj_t *address = &alias; opaque(address); return alias;',
    'mp_obj_t (*mp_obj_is_true)(mp_obj_t) = callback; mp_obj_is_true(alias); return alias;',
])
def test_loop_address_and_callable_shadow_barriers_remain_visible(operation):
    result = public('int callback(mp_obj_t value); mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; ' + operation + ' }')
    assert result['return_resolution'] == 'unresolved'


def test_header_declarations_do_not_establish_a_whole_input_domain():
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_bool(mp_obj_is_true(value)); }', helper_bodies=[])
    result = bind_public_contracts(summary, bindings(function))
    assert result['returns'][0]['kind'] == 'bool'
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert result['public_parameters'][0]['constraints'][0]['domain_authority']['resolution'] == 'unresolved'


def test_prototype_only_malloc_does_not_establish_a_typed_allocation():
    function, summary = analyse('mp_obj_t probe(void) { mp_obj_int_t *pointer = (mp_obj_int_t *)mp_obj_malloc_helper(sizeof(mp_obj_int_t), &mp_type_int); return ((mp_obj_t)(pointer)); }', helper_bodies=[])
    result = bind_public_contracts(summary, bindings(function))
    assert result['return_resolution'] == 'unresolved'


def test_list_declaration_aliases_include_appends_after_alias_creation():
    result = public('mp_obj_t probe(void) { mp_obj_t result = mp_obj_new_list(0, 0); mp_obj_t alias = result; mp_obj_list_append(result, mp_obj_new_int(1)); mp_obj_list_append(alias, mp_obj_new_str("x", 1)); return alias; }')
    returned = result['returns'][0]
    assert returned['kind'] == 'list'
    assert {element['kind'] for element in returned['elements']} == {'int', 'str'}
    assert returned['element_resolution'] == 'bounded-resolved'


@pytest.mark.parametrize('operation', ['consume(alias);', 'opaque(&alias);', 'alias = 99;'])
def test_list_alias_escapes_and_mutation_prevent_a_complete_builder_claim(operation):
    result = public('mp_obj_t probe(void) { mp_obj_t result = mp_obj_new_list(0, 0); mp_obj_t alias = result; ' + operation + ' return result; }')
    assert result['return_resolution'] == 'unresolved'


def test_nonempty_list_initialization_is_not_an_empty_complete_append_union():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t items[1] = {value}; mp_obj_t result = mp_obj_new_list(1, items); return result; }')
    assert result['returns'][0]['kind'] == 'list'
    assert result['returns'][0]['element_resolution'] == 'unresolved'


def test_unknown_buffer_mode_does_not_become_a_resolved_domain():
    result = public('mp_obj_t probe(mp_obj_t value, int flags) { int buffer; mp_get_buffer_raise(value, &buffer, flags); return 99; }')
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    constraint = result['public_parameters'][0]['constraints'][0]
    assert constraint['domain_authority']['native_operation_resolution'] == 'bounded-resolved'
    assert constraint['domain_authority']['unresolved']


def test_source_fixed_array_count_is_a_constraint_not_a_container_generic():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t *items; mp_obj_get_array_fixed_n(value, 2, &items); return 99; }')
    parameter = result['public_parameters'][0]
    assert parameter['accepted_domain']['contract'] == {'kind': 'tuple-or-list/exact-length'}
    assert parameter['accepted_domain']['representation_resolution'] == 'unresolved'
    assert parameter['constraints'][0]['extent']['value'] == 2


def test_native_builder_missing_definition_keeps_its_field_reason():
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_str_of_type(mp_obj_get_type(value), "x", 1); }', helper_bodies=[])
    contract = summary['returns'][0]
    assert contract['kind'] == 'same-native-family'
    assert contract['family_resolution'] == 'unresolved'
    assert contract['family_unresolved']


def test_extra_successful_native_builder_path_invalidates_family_qualification():
    bodies = [(path, text.replace('return mp_obj_new_bytes(data, len);',
              'if (len == 7) return unknown_builder(data, len); return mp_obj_new_bytes(data, len);'))
              for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t unknown_builder(const char *data, size_t len); mp_obj_t probe(mp_obj_t value) { return mp_obj_new_str_of_type(mp_obj_get_type(value), "x", 1); }', helper_bodies=bodies)
    assert summary['returns'][0]['family_resolution'] == 'unresolved'
    assert summary['returns'][0]['family_unresolved']


def test_selected_cross_tu_source_body_keeps_physical_declaration_identity():
    caller_functions = parsed('mp_obj_t probe(mp_obj_t value) { return helper(value); }',
        helper_bodies=[('/fixture/foreign.h', 'mp_obj_t helper(mp_obj_t value);')])
    provider_functions = parsed('mp_obj_t helper(mp_obj_t value) { return value; }',
        helper_bodies=[], physical='/fixture/provider.c')
    caller, provider = caller_functions['probe'][0], provider_functions['helper'][0]
    call = caller['events']['calls'][0]
    assert provider['declaration_identity'] != call['callee']
    original = deepcopy(provider['declaration_identity'])
    no_definition = semantics(caller, caller_functions, [], CONTRACTS, linked_functions={})
    assert no_definition['return_resolution'] == 'unresolved'
    selected = {'helper': [{'function': provider, 'declaration_identity': call['callee'],
        'authority': {'resolution': 'bounded-resolved', 'kind': 'fixture-selected-owned-source',
                      'source_span': provider['source_span']}}]}
    summary = semantics(caller, caller_functions, [], CONTRACTS, linked_functions=selected)
    result = bind_public_contracts(summary, bindings(caller))
    assert result['returns'][0]['exported_argument'] == 'arg0'
    assert result['return_resolution'] == 'bounded-resolved'
    assert provider['declaration_identity'] == original
    selected['helper'][0]['declaration_identity'] = original
    assert semantics(caller, caller_functions, [], CONTRACTS, linked_functions=selected)['return_resolution'] == 'unresolved'


def test_a_write_inside_return_operands_cannot_preserve_an_original_identity():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; return mp_obj_new_tuple(2, (mp_obj_t[]){mp_obj_new_bool((alias = 99)), alias}); }')
    assert result['return_resolution'] == 'unresolved'
    assert result['returns'][0]['elements'][1].get('exported_argument') != 'arg0'


def test_unknown_extra_integer_argument_write_invalidates_domain_authority():
    bodies = [(path, text.replace('arg = mp_unary_op(MP_UNARY_OP_INT_MAYBE, (mp_obj_t)arg);',
                                 'arg = 99; arg = mp_unary_op(MP_UNARY_OP_INT_MAYBE, (mp_obj_t)arg);'))
              for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert result['returns'][0]['kind'] == 'int'


def test_active_object_type_macro_shape_is_qualified_only_as_a_conjunction():
    bodies = [(path, text.replace('mp_obj_get_type(o) == &mp_type_tuple',
                                  '(1 && 1 && (mp_obj_is_obj(o) && (((mp_obj_base_t *)((void *)(o)))->type == (&mp_type_tuple))))')
                       .replace('mp_obj_get_type(o) == &mp_type_list',
                                '(1 && 1 && (mp_obj_is_obj(o) && (((mp_obj_base_t *)((void *)(o)))->type == (&mp_type_list))))'))
              for path, text in HELPER_BODIES]
    source = 'mp_obj_t probe(mp_obj_t value) { mp_obj_t *items; mp_obj_get_array_fixed_n(value, 2, &items); return 99; }'
    function, summary = analyse(source, helper_bodies=bodies)
    assert bind_public_contracts(summary, bindings(function))['public_parameters'][0]['accepted_domain']['resolution'] == 'bounded-resolved'
    alternatives = [(path, text.replace('mp_obj_is_obj(o) &&', 'mp_obj_is_obj(o) ||')) for path, text in bodies]
    function, summary = analyse(source, helper_bodies=alternatives)
    parameter = bind_public_contracts(summary, bindings(function))['public_parameters'][0]
    assert parameter['accepted_domain']['resolution'] == 'unresolved'
    assert parameter['constraints'][0]['domain_authority']['unresolved']


def test_unassessed_longlong_backend_does_not_inherit_none_mpz_qualification():
    bodies = [(path.replace('/objint.c', '/objint_longlong.c'), text) for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    parameter = bind_public_contracts(summary, bindings(function))['public_parameters'][0]
    assert parameter['accepted_domain']['resolution'] == 'unresolved'
    assert any('NONE/MPZ' in reason for reason in parameter['accepted_domain']['reasons'])
    assert summary['returns'][0]['kind'] == 'int'


def test_shadowed_conversion_opcode_does_not_grant_integer_capability():
    bodies = [(path, text.replace('int mp_obj_get_int_maybe(mp_obj_t arg, mp_int_t *value) {',
                                 'int mp_obj_get_int_maybe(mp_obj_t arg, mp_int_t *value) { int MP_UNARY_OP_INT_MAYBE = 0;'))
              for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    assert bind_public_contracts(summary, bindings(function))['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'


def connected_framework_fixture(source_mutation=None, dependency_mutation=None):
    header = '''
typedef unsigned long mp_obj_t;
typedef unsigned long size_t;
typedef unsigned char uint8_t;
typedef long mp_int_t;
typedef unsigned long uintptr_t;
typedef unsigned char byte;
typedef unsigned short uint16_t;
typedef struct mp_obj_type_t mp_obj_type_t;
typedef struct { const mp_obj_type_t *type; } mp_obj_base_t;
struct mp_obj_type_t { mp_obj_base_t base; unsigned long name; uint16_t flags; uint8_t slot_index_call; const void *slots[1]; };
typedef struct { mp_obj_base_t base; const mp_obj_type_t *type; mp_obj_t fun; } mp_obj_checked_fun_t;
typedef struct { mp_obj_t value; } mp_map_elem_t;
enum { MP_TYPE_FLAG_BINDS_SELF = 1, MP_TYPE_FLAG_BUILTIN_FUN = 2, MP_TYPE_FLAG_INSTANCE_TYPE = 4, MP_QSTR_checked_fun = 3, GC_ALLOC_FLAG_HAS_FINALISER = 1 };
extern const mp_obj_type_t mp_type_type, mp_type_object, mp_type_checked_fun, mp_type_int, mp_type_str, mp_type_NoneType, mp_type_bool, mp_type_MemoryError;
const mp_obj_type_t *mp_obj_get_type(mp_obj_t obj);
static inline int mp_obj_is_obj(mp_obj_t obj) { return (((mp_int_t)obj) & 3) == 0; }
void mp_raise_TypeError(const char *message);
mp_obj_t mp_call_function_n_kw(mp_obj_t fun, size_t n_args, size_t n_kw, const mp_obj_t *args);
void *mp_obj_malloc_helper(size_t num_bytes, const mp_obj_type_t *type);
void *m_malloc(size_t num_bytes);
void m_malloc_fail(size_t num_bytes);
void *gc_alloc(size_t n_bytes, unsigned int alloc_flags);
int gc_is_locked(void);
typedef const char *mp_rom_error_text_t;
void mp_raise_msg(const mp_obj_type_t *type, mp_rom_error_text_t message);
void mp_raise_msg_varg(const mp_obj_type_t *type, mp_rom_error_text_t message, size_t num_bytes);
extern unsigned char static_storage[64];
typedef struct { byte *gc_alloc_table_start; byte *gc_pool_start; size_t gc_last_free_atb_index; size_t gc_alloc_table_byte_len; size_t gc_last_used_block; } mp_state_mem_area_t;
struct { struct { mp_state_mem_area_t area; int gc_auto_collect_enabled; } mem; } mp_state_ctx;
mp_map_elem_t *find_member(const mp_obj_type_t *type, unsigned long attr);
'''
    allocator = '''
const mp_obj_type_t *mp_obj_get_type(mp_obj_t o_in) {
    if (mp_obj_is_obj(o_in)) {
        const mp_obj_base_t *o = ((void *)(o_in));
        return o->type;
    } else {
        static const mp_obj_type_t *const types[] = {
            0, &mp_type_int, &mp_type_str, &mp_type_int,
            0, &mp_type_int, &mp_type_NoneType, &mp_type_int,
            0, &mp_type_int, &mp_type_str, &mp_type_int,
            0, &mp_type_int, &mp_type_bool, &mp_type_int,
        };
        return types[(uintptr_t)o_in & 0xf];
    }
}
void *mp_obj_malloc_helper(size_t num_bytes, const mp_obj_type_t *type) {
    mp_obj_base_t *base = (mp_obj_base_t *)m_malloc(num_bytes);
    base->type = type;
    return base;
}
'''
    malloc_body = '''
void *m_malloc(size_t num_bytes) {
    void *ptr = gc_alloc((num_bytes), 0);
    if (ptr == ((void *)0) && num_bytes != 0) m_malloc_fail(num_bytes);
    return ptr;
}
'''
    free_checks = '\n'.join('''
            if ((a & %d) == 0) {
                if (++n_free >= n_blocks) { i = i * 4 + %d; goto found; }
            } else { n_free = 0; }
''' % (3 << (2 * ordinal), ordinal) for ordinal in range(4))
    gc_body = '''
void *gc_alloc(size_t n_bytes, unsigned int alloc_flags) {
    int has_finaliser = alloc_flags & GC_ALLOC_FLAG_HAS_FINALISER;
    size_t n_blocks = ((n_bytes + 16 - 1) & ~(16 - 1)) / 16;
    if (n_blocks == 0) return ((void *)0);
    mp_state_mem_area_t *area;
    size_t i, end_block, start_block, n_free;
    int collected = !mp_state_ctx.mem.gc_auto_collect_enabled;
    for (;;) {
        area = &mp_state_ctx.mem.area;
        for (; area != ((void *)0); area = ((void *)0), i = 0) {
            n_free = 0;
            for (i = area->gc_last_free_atb_index; i < area->gc_alloc_table_byte_len; i++) {
                byte a = area->gc_alloc_table_start[i];
''' + free_checks + '''
            }
        }
        if (collected) return ((void *)0);
        collected = 1;
    }
found:
    end_block = i;
    start_block = i - n_free + 1;
    do { area->gc_alloc_table_start[start_block / 4] |= 1 << (2 * (start_block & (4 - 1))); } while (0);
    for (size_t bl = start_block + 1; bl <= end_block; bl++) {
        do { area->gc_alloc_table_start[bl / 4] |= 2 << (2 * (bl & (4 - 1))); } while (0);
    }
    void *ret_ptr = (void *)(area->gc_pool_start + start_block * 16);
    return ret_ptr;
}
'''
    framework = '''
void m_malloc_fail(size_t num_bytes) {
    (void)0;
    if (gc_is_locked()) {
        mp_raise_msg(&mp_type_MemoryError, (mp_rom_error_text_t)MP_COMPRESSED_ROM_TEXT("heap is locked"));
    }
    mp_raise_msg_varg(&mp_type_MemoryError, (mp_rom_error_text_t)MP_COMPRESSED_ROM_TEXT("allocating " "%lu" " bytes"), num_bytes);
}
static mp_obj_t checked_fun_call(mp_obj_t self_in, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    mp_obj_checked_fun_t *self = (void *)self_in;
    if (n_args > 0) {
        const mp_obj_type_t *arg0_type = mp_obj_get_type(args[0]);
        if (arg0_type != self->type) mp_raise_TypeError("receiver");
    }
    return mp_call_function_n_kw(self->fun, n_args, n_kw, args);
}
static mp_obj_t unchecked_fun_call(mp_obj_t self_in, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    mp_obj_checked_fun_t *self = (void *)self_in;
    return mp_call_function_n_kw(self->fun, n_args, n_kw, args);
}
const mp_obj_type_t mp_type_checked_fun = {
    .base = {.type = &mp_type_type}, .name = MP_QSTR_checked_fun, .flags = MP_TYPE_FLAG_BUILTIN_FUN,
    .slot_index_call = 1, .slots = {(const void *)checked_fun_call},
};
static mp_obj_t mp_obj_new_checked_fun(const mp_obj_type_t *type, mp_obj_t fun) {
    mp_obj_checked_fun_t *o = (mp_obj_checked_fun_t *)mp_obj_malloc_helper(sizeof(mp_obj_checked_fun_t), &mp_type_checked_fun);
    o->type = type;
    o->fun = fun;
    return ((mp_obj_t)(o));
}
void mp_convert_member_lookup(mp_obj_t self, const mp_obj_type_t *type, mp_obj_t member, mp_obj_t *dest) {
    if (mp_obj_is_obj(member)) {
        const mp_obj_type_t *m_type = ((mp_obj_base_t *)((void *)(member)))->type;
        if (m_type->flags & MP_TYPE_FLAG_BINDS_SELF) {
            if (m_type->flags & MP_TYPE_FLAG_BUILTIN_FUN) {
                if (type->flags & MP_TYPE_FLAG_INSTANCE_TYPE) {
                    dest[0] = member;
                } else if (self == 0 && type != &mp_type_object) {
                    dest[0] = mp_obj_new_checked_fun(type, member);
                } else {
                    dest[0] = member;
                    dest[1] = self;
                }
            } else {
                dest[0] = member;
                dest[1] = self;
            }
        }
    } else {
        dest[0] = member;
    }
}
void mp_load_method_maybe(mp_obj_t obj, unsigned long attr, mp_obj_t *dest) {
    const mp_obj_type_t *type = mp_obj_get_type(obj);
    mp_map_elem_t *elem = find_member(type, attr);
    if (elem != 0) mp_convert_member_lookup(obj, type, elem->value, dest);
}
'''
    if source_mutation:
        framework = source_mutation(framework)
    codec = 'inline __attribute__((always_inline)) const char *MP_COMPRESSED_ROM_TEXT(const char *msg) { return msg; }\n'
    chunks = [('/fixture/py/obj.h', header), ('/fixture/py/obj.c', allocator),
              ('/fixture/py/malloc.c', malloc_body), ('/fixture/py/gc.c', gc_body),
              ('/fixture/py/misc.h', codec), ('/fixture/py/runtime.c', framework)]
    if dependency_mutation:
        chunks = [(path, dependency_mutation(path, text)) for path, text in chunks]
    code, origins = '', []
    for path, text in chunks:
        text = text.rstrip() + '\n'
        origins.extend(('/fixture/py/misc.h' if path.endswith('/obj.h')
                        and ('void *m_malloc(' in line or 'void m_malloc_fail(' in line) else '/fixture/py/gc.h'
                        if path.endswith('/obj.h') and ('void *gc_alloc(' in line or 'int gc_is_locked(' in line) else path, index)
                       for index, line in enumerate(text.splitlines(), 1))
        code += text
    paths = {path: 'fixture' for path, _ in chunks}
    paths['/fixture/py/misc.h'] = 'fixture'
    paths['/fixture/py/gc.h'] = 'fixture'
    contracts = {**CONTRACTS, 'none': '( ( mp_obj_t ) ( ( ( 0 ) << 3 ) | 6 ) )',
                 'false': '( ( mp_obj_t ) ( ( ( 1 ) << 3 ) | 6 ) )',
                 'true': '( ( mp_obj_t ) ( ( ( 3 ) << 3 ) | 6 ) )',
                 'rom_qstr': '( ( mp_obj_t ) ( ( ( ( mp_uint_t ) ( MP_QSTR___name__ ) ) << 3 ) | 2 ) )',
                 'binds_self_flag': '1', 'builtin_fun_flag': '2', 'instance_type_flag': '4'}
    record = {'dependencies': paths, 'project_dependencies': list(paths), 'contracts': contracts}
    declarations, functions, errors, frontend = syntax_adapter(code, origins, '/fixture/py/runtime.c', record)
    assert not errors
    record['frontend'] = frontend
    objects = source_definitions(declarations, functions, [], '/fixture/py/runtime.c', '', origins, record)
    by_name = {function['name']: function for function in functions}
    for function in functions:
        for call in function['events']['calls']:
            call['qualified_template_sources'] = ['/fixture/py/obj.h', '/fixture/py/obj.c']
    rows = {name: {'function': by_name[name], 'authority': {'resolution': 'bounded-resolved',
            'kind': 'fixture-owned-source-definition', 'contracts': contracts}}
            for name in ('checked_fun_call', 'mp_convert_member_lookup', 'mp_obj_new_checked_fun', 'mp_obj_malloc_helper',
                         'mp_load_method_maybe', 'mp_obj_get_type', 'mp_obj_is_obj', 'm_malloc', 'gc_alloc',
                         'm_malloc_fail', 'MP_COMPRESSED_ROM_TEXT')}
    rows['dispatch_mp_obj_is_obj'] = rows['mp_obj_is_obj']
    checked_type = next(value for value in objects if value['name'] == 'mp_type_checked_fun')
    slot = next(slot for slot in checked_type['slots'] if slot['slot'] == 'call')
    candidates = [function for function in functions if slot['expression'] in
                  ('( const void * ) ' + function['name'], '( const void * ) ( ' + function['name'] + ' )')]
    assert len(candidates) == 1
    selected = candidates[0]
    rows['mp_type_checked_fun'] = {'definition': checked_type, 'authority': {'resolution': 'bounded-resolved'},
        'call_slot_authority': {'resolution': 'bounded-resolved', 'function_id': selected['id'],
                               'declaration_identity': selected['declaration_identity'], 'expression': slot['expression']}}
    return rows


@pytest.mark.parametrize(('path_suffix', 'old', 'new'), [
    ('/obj.c', 'return o->type;', 'return &mp_type_str;'),
    ('/obj.c', '&mp_type_NoneType', '&mp_type_str'),
    ('/obj.c', 'if (mp_obj_is_obj(o_in))', 'if (mp_obj_is_obj(o_in) && 0)'),
    ('/obj.c', 'if (mp_obj_is_obj(o_in))', 'if (!mp_obj_is_obj(o_in))'),
    ('/obj.c', 'if (mp_obj_is_obj(o_in))', 'while (1) {} if (mp_obj_is_obj(o_in))'),
    ('/obj.c', 'if (mp_obj_is_obj(o_in))', '(void)*(volatile mp_int_t *)o_in; if (mp_obj_is_obj(o_in))'),
    ('/obj.h', 'return (((mp_int_t)obj) & 3) == 0;', 'return 1;'),
    ('/obj.h', 'return (((mp_int_t)obj) & 3) == 0;', '(void)*(volatile mp_int_t *)obj; return (((mp_int_t)obj) & 3) == 0;'),
    ('/malloc.c', 'gc_alloc((num_bytes), 0)', 'static_storage'),
    ('/malloc.c', 'gc_alloc((num_bytes), 0)', 'gc_alloc(1, 0)'),
    ('/gc.c', 'return ret_ptr;', 'return static_storage;'),
    ('/gc.c', 'start_block * 16', 'end_block * 16'),
    ('/gc.c', 'start_block / 4] |= 1', 'start_block / 4] |= 2'),
    ('/gc.c', 'bl / 4] |= 2', 'bl / 4] |= 1'),
    ('/gc.c', 'if ((a & 3) == 0)', 'if ((a & 3) != 0)'),
    ('/gc.c', '} else { n_free = 0; }', '} else { n_free = 1; }'),
    ('/gc.c', 'byte a = area->gc_alloc_table_start[i];', 'i++; byte a = area->gc_alloc_table_start[i];'),
    ('/gc.c', 'byte a = area->gc_alloc_table_start[i];', 'byte a = area->gc_alloc_table_start[i]; area = &mp_state_ctx.mem.area;'),
    ('/gc.c', 'do { area->gc_alloc_table_start[start_block / 4]', 'if (0) do { area->gc_alloc_table_start[start_block / 4]'),
    ('/gc.c', '16', '(n_bytes * 16)'),
    ('/gc.c', 'size_t n_blocks =', 'typedef char mp_uint_t[n_bytes]; size_t n_blocks ='),
    ('/gc.c', 'i = i * 4 + 0; goto found;', 'i = i * 4 + 0; } else { goto found;'),
    ('/gc.c', 'start_block = i - n_free + 1;', 'if (0) start_block = i - n_free + 1;'),
    ('/gc.c', 'return ((void *)0);', 'return ((void *)1);'),
    ('/gc.c', 'size_t i, end_block, start_block, n_free;', 'static size_t i, end_block, start_block, n_free;'),
])
def test_receiver_certificate_requires_selected_getter_and_fresh_region_source(path_suffix, old, new):
    def mutate(path, source):
        return source.replace(old, new) if path.endswith(path_suffix) else source
    authority = checked_receiver_authority(connected_framework_fixture(dependency_mutation=mutate))
    assert authority['resolution'] == 'unresolved'
    assert authority['unresolved']


def test_receiver_certificate_retains_bounded_reservation_and_tag_witnesses():
    authority = checked_receiver_authority(connected_framework_fixture())
    assert authority['resolution'] == 'bounded-resolved'
    assert authority['native_getter_authority']['immediate_type_table'][6] == 'mp_type_NoneType'
    assert authority['fresh_region_authority']['reservation']['kind'] == 'selected-gc-free-block-reservation'
    assert authority['fresh_region_authority']['reservation']['limitations']


def test_selected_native_gc_null_paths_accept_literal_zero_without_object_narrowing():
    def mutate(path, source):
        return source.replace('return ((void *)0);', 'return 0;') if path.endswith('/gc.c') else source
    authority = checked_receiver_authority(connected_framework_fixture(dependency_mutation=mutate))
    assert authority['resolution'] == 'bounded-resolved'
    assert authority['fresh_region_authority']['reservation']['kind'] == 'selected-gc-free-block-reservation'


def test_gnu_typedef_prefix_attribute_preserves_unresolved_function_pointer_abi():
    source = '''
typedef unsigned long mp_obj_t;
typedef __attribute__((ms_abi)) mp_obj_t (*native_call_t)(mp_obj_t self);
static mp_obj_t ordinary(mp_obj_t self) { return self; }
'''
    path = '/fixture/py/obj.h'
    origins = [(path, index) for index, _ in enumerate(source.splitlines(), 1)]
    record = {'dependencies': {path: 'fixture'}, 'project_dependencies': [path], 'contracts': {}}
    _, _, errors, frontend = syntax_adapter(source, origins, path, record)
    assert not errors
    interface = next(item for item in frontend['function_pointer_interfaces'] if item['name'] == 'native_call_t')
    assert interface['status'] == 'unresolved'
    assert any('calling convention' in reason or 'signature' in reason for reason in interface['reasons'])
    projection = next(item for item in frontend['syntax_adaptation']['replacements']
                      if item['expression']['kind'] == 'gnu_typedef_prefix_attribute')
    assert projection['expression']['abi_semantics']['state'] == 'unassessed'
    assert 'ms_abi' in projection['original']


def test_source_modifier_evidence_distinguishes_static_inline_from_external_body():
    source = '''
static inline int header_predicate(unsigned long value) { return (value & 3) == 0; }
int external_predicate(unsigned long value) { return (value & 3) == 0; }
inline __attribute__((always_inline)) int forced_predicate(unsigned long value) { return (value & 3) == 0; }
'''
    path = '/fixture/py/obj.h'
    origins = [(path, index) for index, _ in enumerate(source.splitlines(), 1)]
    record = {'dependencies': {path: 'fixture'}, 'project_dependencies': [path], 'contracts': {}}
    _, functions, errors, _ = syntax_adapter(source, origins, path, record)
    assert not errors
    contracts = {function['name']: function['parameter_source_contract'] for function in functions}
    assert contracts['header_predicate']['declaration_modifiers'] == ['static', 'inline']
    assert contracts['header_predicate']['declaration_modifiers_resolution'] == 'resolved'
    assert contracts['external_predicate']['declaration_modifiers'] == []
    assert contracts['forced_predicate']['declaration_modifiers'] == ['inline']
    assert contracts['forced_predicate']['declaration_modifiers_resolution'] == 'resolved'
    assert 'always_inline' in contracts['forced_predicate']['declaration_prefix_expression']
    assert contracts['forced_predicate']['signature_resolution'] == 'unresolved'


def test_connected_checked_receiver_source_requires_executed_mismatch_failure():
    assert checked_receiver_authority(connected_framework_fixture())['resolution'] == 'bounded-resolved'
    disabled = connected_framework_fixture(lambda source: source.replace('mp_raise_TypeError("receiver");', 'if (0) mp_raise_TypeError("receiver");'))
    assert checked_receiver_authority(disabled)['resolution'] == 'unresolved'
    wrong_slot = connected_framework_fixture(lambda source: source.replace('.slots = {(const void *)checked_fun_call}', '.slots = {(const void *)unchecked_fun_call}'))
    assert checked_receiver_authority(wrong_slot)['resolution'] == 'unresolved'
    disabled_wrap = connected_framework_fixture(lambda source: source.replace(
        'dest[0] = mp_obj_new_checked_fun(type, member);', 'if (0) dest[0] = mp_obj_new_checked_fun(type, member);'))
    assert checked_receiver_authority(disabled_wrap)['resolution'] == 'unresolved'
    unrelated_member_type = connected_framework_fixture(lambda source: source.replace(
        '((mp_obj_base_t *)((void *)(member)))->type', '((mp_obj_base_t *)((void *)(self)))->type'))
    assert checked_receiver_authority(unrelated_member_type)['resolution'] == 'unresolved'


@pytest.mark.parametrize('operation', ['opaque(&(alias));', 'consume((alias));'])
def test_parenthesized_escape_never_preserves_the_returned_input_identity(operation):
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; ' + operation + ' return alias; }')
    assert result['return_resolution'] == 'unresolved'
    assert all(contract.get('exported_argument') != 'arg0' for contract in result['returns'])


def test_parenthesized_list_append_is_included_in_the_complete_element_union():
    result = public('mp_obj_t probe(void) { mp_obj_t result = mp_obj_new_list(0, 0); mp_obj_list_append(result, mp_obj_new_int(1)); mp_obj_list_append((result), mp_obj_new_str("x", 1)); return result; }')
    returned = result['returns'][0]
    assert returned['element_resolution'] == 'bounded-resolved'
    assert {element['kind'] for element in returned['elements']} == {'int', 'str'}
    escaped = public('mp_obj_t probe(void) { mp_obj_t result = mp_obj_new_list(0, 0); consume((result)); return result; }')
    assert escaped['return_resolution'] == 'unresolved'


@pytest.mark.parametrize('consumer', [
    'volatile mp_int_t observed = *(mp_int_t *)value;',
    '*(volatile mp_int_t *)value;',
    '((mp_int_t *)value)[0];',
])
def test_derived_initializer_and_standalone_reads_keep_identity_but_not_object_domain(consumer):
    result = public('mp_obj_t probe(mp_obj_t value) { ' + consumer + ' return value; }')
    assert result['returns'][0]['exported_argument'] == 'arg0'
    parameter = result['public_parameters'][0]
    assert parameter['accepted_domain']['resolution'] == 'unresolved'
    assert parameter['accepted_domain']['reasons']


@pytest.mark.parametrize('suffix', [
    '{ mp_obj_t value = 99; return mp_obj_new_tuple(1, items); }',
    'value = 99; return mp_obj_new_tuple(1, items);',
])
def test_tuple_local_storage_keeps_the_initializer_scope_and_captured_value(suffix):
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t items[1] = {value}; ' + suffix + ' }')
    assert result['return_resolution'] == 'bounded-resolved'
    assert result['returns'][0]['elements'][0]['exported_argument'] == 'arg0'


def test_for_initializer_shadow_scope_ends_with_the_loop():
    result = public('mp_obj_t probe(mp_obj_t value) { mp_obj_t alias = value; for (mp_obj_t alias = 99; alias == 0;) {} return alias; }')
    assert result['returns'][0]['exported_argument'] == 'arg0'
    assert result['returns'][0]['kind'] == 'argument'


def test_builder_selector_reassignment_blocks_all_direct_native_family_mappings():
    bodies = [(path, text.replace('if (type == &mp_type_str) return', 'type = &mp_type_str; if (type == &mp_type_str) return'))
              for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_str_of_type(mp_obj_get_type(value), "x", 1); }', helper_bodies=bodies)
    contract = summary['returns'][0]
    assert contract['family_resolution'] == 'unresolved'
    assert 'native_family_mapping' not in contract
    assert any('selector' in reason for reason in contract['family_unresolved'])


def test_selected_allocator_conditional_type_storage_is_not_a_native_integer_proof():
    bodies = [(path, text.replace('base->type = type;', 'if (num_bytes == 0) base->type = type;'))
              for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(void) { mp_obj_int_t *pointer = (mp_obj_int_t *)mp_obj_malloc_helper(sizeof(mp_obj_int_t), &mp_type_int); return ((mp_obj_t)(pointer)); }', helper_bodies=bodies)
    assert bind_public_contracts(summary, bindings(function))['return_resolution'] == 'unresolved'
    assert all(contract['kind'] != 'int' for contract in summary['returns'])


def test_selected_none_backend_cannot_delegate_its_decode_to_an_opaque_operation():
    bodies = [(path, text.replace('mp_int_t mp_obj_int_get_checked(mp_obj_t value) { return ((mp_int_t)value) >> 1; }',
                                 'mp_int_t opaque_integer(mp_obj_t value); mp_int_t mp_obj_int_get_checked(mp_obj_t value) { return opaque_integer(value); }'))
              for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    parameter = bind_public_contracts(summary, bindings(function))['public_parameters'][0]
    assert parameter['accepted_domain']['resolution'] == 'unresolved'
    assert any('checked integer body' in reason for reason in parameter['accepted_domain']['reasons'])
    assert summary['returns'][0]['kind'] == 'int'


def test_duplicate_public_names_do_not_make_distinct_refs_one_identity():
    function, summary = analyse('mp_obj_t probe(mp_obj_t first, mp_obj_t second) { return second; }')
    binding = bindings(function)
    binding['parameters'][0]['name'] = binding['parameters'][1]['name'] = 'same'
    result = bind_public_contracts(summary, binding)
    assert result['return_resolution'] == 'unresolved'
    assert all(contract.get('exported_argument') != 'same' for contract in result['returns'])
    assert [record['name'] for record in result['public_parameters']] == ['same', 'same']


def mpz_helper_fixture(operation=None):
    checked = '''
mp_int_t mp_obj_int_get_checked(mp_obj_t self_in) {
    if (mp_obj_is_small_int(self_in)) {
        return (((mp_int_t)(self_in)) >> 1);
    } else {
        const mp_obj_int_t *self = (void *)(self_in);
        mp_int_t value;
        if (mpz_as_int_checked(&self->mpz, &value)) return value;
        else mp_raise_OverflowError("range");
    }
}
'''
    native = '''
int mpz_as_int_checked(const mpz_t *i, mp_int_t *value) {
    mp_uint_t val = 0;
    mpz_dig_t *d = i->dig + i->len;
    while (d > i->dig) {
        if (val > (~(((mp_uint_t)1) << (sizeof(mp_obj_t) * 8 - 1)) >> (16))) return 0;
        d--;
        val = (val << (16)) | *d;
    }
    if (i->neg != 0) val = -val;
    *value = val;
    return 1;
}
'''
    declarations = '''
int mp_obj_is_small_int(mp_obj_t value);
int mpz_as_int_checked(const mpz_t *i, mp_int_t *value);
'''
    bodies = [(path, text) for path, text in HELPER_BODIES if not path.endswith('/objint.c')]
    return [('/fixture/py/mpz.h', declarations), *bodies,
            ('/fixture/py/objint_mpz.c', checked), ('/fixture/py/mpz.c', operation(native) if operation else native)]


def test_selected_mpz_checked_conversion_requires_the_actual_native_operation():
    source = 'mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }'
    function, summary = analyse(source, helper_bodies=mpz_helper_fixture())
    parameter = bind_public_contracts(summary, bindings(function))['public_parameters'][0]
    assert parameter['accepted_domain']['contract'] == {'kind': 'integer-conversion-capability'}
    assert parameter['accepted_domain']['representation_resolution'] == 'unresolved'
    opaque = lambda native: 'mp_int_t opaque_mpz(const mpz_t *i); int mpz_as_int_checked(const mpz_t *i, mp_int_t *value) { *value = opaque_mpz(i); return 1; }'
    function, summary = analyse(source, helper_bodies=mpz_helper_fixture(opaque))
    parameter = bind_public_contracts(summary, bindings(function))['public_parameters'][0]
    assert parameter['accepted_domain']['resolution'] == 'unresolved'
    assert summary['returns'][0]['kind'] == 'int'


def test_mpz_malformed_range_guard_is_not_source_checked_conversion():
    bodies = mpz_helper_fixture(lambda native: native.replace('~(((mp_uint_t)1) << (sizeof(mp_obj_t) * 8 - 1))', '~0'))
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    assert bind_public_contracts(summary, bindings(function))['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'


@pytest.mark.parametrize('operation', [
    'opaque_values((args));',
    'opaque_one(&(args[0].u_obj));',
    'mp_arg_val_t *alias = args; opaque_values((alias));',
])
def test_parser_storage_escape_never_preserves_the_supplied_field_identity(operation):
    prefix = '''
typedef union { mp_obj_t u_obj; mp_int_t u_int; int u_bool; } mp_arg_val_t;
typedef struct { unsigned long qst; unsigned long flags; mp_arg_val_t defval; } mp_arg_t;
enum { MP_QSTR_item = 1, MP_ARG_OBJ = 2 };
void mp_arg_parse_all(size_t n_args, const mp_obj_t *pos_args, void *kw_args,
    size_t n_allowed, const mp_arg_t *allowed, mp_arg_val_t *values);
void opaque_values(mp_arg_val_t *values);
void opaque_one(mp_obj_t *value);
'''
    body = '''
mp_obj_t probe(size_t n_args, const mp_obj_t *pos_args, void *kw_args) {
    const mp_arg_t allowed_args[] = {{MP_QSTR_item, MP_ARG_OBJ, {.u_obj = 99}}};
    mp_arg_val_t args[1];
    mp_arg_parse_all(n_args, pos_args, kw_args, 1, allowed_args, args);
    OPERATION
    return args[0].u_obj;
}
'''
    function, positive = analyse(prefix + body.replace('OPERATION', ''))
    assert positive['return_resolution'] == 'bounded-resolved'
    assert positive['returns'][0]['source_ref']['kind'] == 'parser-field'
    function, negative = analyse(prefix + body.replace('OPERATION', operation))
    assert negative['return_resolution'] == 'unresolved'
    assert all(contract.get('source_ref', {}).get('kind') != 'parser-field' for contract in negative['returns'])


@pytest.mark.parametrize('operation', [
    'mutate((pointer));',
    'mp_obj_int_t *alias = pointer; mutate((alias));',
    'mp_obj_int_t *alias = (mp_obj_int_t *)pointer; mutate(alias);',
    'mp_obj_int_t *alias = pointer; alias->base.type = &mp_type_str;',
])
def test_allocated_native_type_requires_complete_scoped_pointer_escape_accounting(operation):
    source = '''
void mutate(mp_obj_int_t *pointer);
mp_obj_t probe(void) {
    mp_obj_int_t *pointer = (mp_obj_int_t *)mp_obj_malloc_helper(sizeof(mp_obj_int_t), &mp_type_int);
    OPERATION
    return ((mp_obj_t)(pointer));
}
'''
    function, positive = analyse(source.replace('OPERATION', ''))
    assert positive['return_resolution'] == 'bounded-resolved'
    assert positive['returns'][0]['kind'] == 'int'
    function, negative = analyse(source.replace('OPERATION', operation))
    assert negative['return_resolution'] == 'unresolved'


def test_allocated_pointer_shadow_does_not_mutate_outer_storage_authority():
    function, summary = analyse('''
mp_obj_t probe(void) {
    mp_obj_int_t *pointer = (mp_obj_int_t *)mp_obj_malloc_helper(sizeof(mp_obj_int_t), &mp_type_int);
    { mp_obj_int_t *pointer = (mp_obj_int_t *)m_malloc(sizeof(mp_obj_int_t)); pointer->base.type = &mp_type_str; }
    return ((mp_obj_t)(pointer));
}
''')
    assert summary['return_resolution'] == 'bounded-resolved'
    assert summary['returns'][0]['kind'] == 'int'


def test_partial_str_builder_path_cannot_prove_exhaustive_native_family():
    bodies = [(path, text.replace(
        'if (type == &mp_type_str) return mp_obj_new_str(data, len);',
        'if (type == &mp_type_str) { if (len > 0) return mp_obj_new_str(data, len); }'))
        for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_str_of_type(mp_obj_get_type(value), "x", 1); }', helper_bodies=bodies)
    contract = summary['returns'][0]
    assert contract['family_resolution'] == 'unresolved'
    assert 'native_family_mapping' not in contract
    assert any('conflicting' in reason for reason in contract['family_unresolved'])


def test_comma_native_dereference_is_not_an_unrestricted_object_domain():
    result = public('mp_obj_t probe(mp_obj_t value) { (*(volatile mp_int_t *)value, 0); return value; }')
    assert result['returns'][0]['exported_argument'] == 'arg0'
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'


def test_parenthesized_indirect_callee_retains_its_unknown_operand_consumer():
    result = public('mp_obj_t probe(mp_obj_t value) { void (*callback)(mp_obj_t) = consume; (callback)(value); return value; }')
    assert result['return_resolution'] == 'unresolved'
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert all(contract.get('exported_argument') != 'arg0' for contract in result['returns'])


def test_mpz_range_failure_must_be_the_matching_then_branch():
    bodies = mpz_helper_fixture(lambda native: native.replace(') return 0;', ') {} else return 0;'))
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    assert bind_public_contracts(summary, bindings(function))['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'


@pytest.mark.parametrize('operation', [
    'mp_obj_int_t *alias; alias = pointer; alias->base.type = &mp_type_str;',
    'mp_obj_int_t *aliases[1] = {pointer}; mutate(aliases[0]);',
])
def test_unsupported_pointer_assignment_or_aggregate_storage_is_not_silently_discarded(operation):
    function, summary = analyse('''
void mutate(mp_obj_int_t *pointer);
mp_obj_t probe(void) {
    mp_obj_int_t *pointer = (mp_obj_int_t *)mp_obj_malloc_helper(sizeof(mp_obj_int_t), &mp_type_int);
    ''' + operation + '''
    return ((mp_obj_t)(pointer));
}
''')
    assert summary['return_resolution'] == 'unresolved'


def test_list_assignment_alias_cannot_hide_a_second_append():
    result = public('''
mp_obj_t probe(void) {
    mp_obj_t result = mp_obj_new_list(0, 0);
    mp_obj_t alias;
    alias = result;
    mp_obj_list_append(alias, mp_obj_new_int(1));
    return result;
}
''')
    assert result['return_resolution'] == 'unresolved'


@pytest.mark.parametrize('inner_allocation, expected', [
    ('result', {'int', 'str', 'bytes'}),
    ('mp_obj_new_list(0, 0)', {'int', 'str'}),
])
def test_list_alias_scope_collisions_preserve_every_matching_append(inner_allocation, expected):
    function, summary = analyse('''
mp_obj_t probe(void) {
    mp_obj_t result = mp_obj_new_list(0, 0);
    mp_obj_t alias = result;
    {
        mp_obj_t alias = ''' + inner_allocation + ''';
        mp_obj_list_append(alias, mp_obj_new_bytes("inner", 5));
    }
    mp_obj_list_append(result, mp_obj_new_int(1));
    mp_obj_list_append(alias, mp_obj_new_str("outer", 5));
    return result;
}
''')
    assert summary['return_resolution'] == 'bounded-resolved'
    returned = summary['returns'][0]
    assert returned['element_resolution'] == 'bounded-resolved'
    assert {element['kind'] for element in returned['elements']} == expected


def test_hidden_list_allocation_root_cannot_be_replaced_by_a_shadowed_declaration():
    function, summary = analyse('''
mp_obj_t probe(void) {
    mp_obj_t result = mp_obj_new_list(0, 0);
    mp_obj_t alias = result;
    {
        mp_obj_t result = mp_obj_new_list(0, 0);
        mp_obj_list_append(result, mp_obj_new_str("inner", 5));
        mp_obj_list_append(alias, mp_obj_new_int(1));
        return alias;
    }
}
''')
    assert summary['return_resolution'] == 'unresolved'
    assert summary['returns'][0]['kind'] == 'unresolved'
    assert 'root declaration' in summary['returns'][0]['reason']


def test_parenthesized_typedef_decode_is_qualified_by_its_actual_declaration():
    bodies = [(path, text.replace('((mp_int_t)value) >> 1', '((mp_int_t)(value)) >> 1'))
              for path, text in HELPER_BODIES]
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    parameter = bind_public_contracts(summary, bindings(function))['public_parameters'][0]
    assert parameter['accepted_domain']['contract'] == {'kind': 'integer-conversion-capability'}
    assert parameter['accepted_domain']['representation_resolution'] == 'unresolved'


def test_ordinary_binding_shadowing_a_typedef_remains_an_unknown_callable():
    result = public('''
mp_obj_t callback(mp_obj_t value);
mp_obj_t probe(mp_obj_t value) {
    mp_obj_t (*mp_int_t)(mp_obj_t) = callback;
    mp_obj_t result = (mp_int_t)(value);
    return result;
}
''')
    assert result['return_resolution'] == 'unresolved'
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'


def test_zero_argument_selected_helper_retains_typed_allocation_return_authority():
    bodies = [*HELPER_BODIES, ('/fixture/py/objint_mpz.c', '''
mp_obj_int_t *mp_obj_int_new_mpz(void) {
    mp_obj_int_t *pointer = (mp_obj_int_t *)mp_obj_malloc_helper(sizeof(mp_obj_int_t), &mp_type_int);
    return pointer;
}
''')]
    function, summary = analyse('''
mp_obj_int_t *mp_obj_int_new_mpz(void);
mp_obj_t probe(void) {
    mp_obj_int_t *pointer = mp_obj_int_new_mpz();
    return ((mp_obj_t)(pointer));
}
''', helper_bodies=bodies)
    assert summary['return_resolution'] == 'bounded-resolved'
    assert summary['returns'][0]['kind'] == 'int'


@pytest.mark.parametrize('parameters, shadow', [
    ('size_t n_args, const mp_obj_t *args', 'int SLOT = 1;'),
    ('size_t n_args, const mp_obj_t *args, int SLOT', ''),
])
def test_shadowed_vector_index_cannot_reuse_a_global_enum_for_return_identity(parameters, shadow):
    source = 'enum { SLOT = 0 };\nmp_obj_t probe(' + parameters + ') { ' + shadow + ' return args[SLOT]; }'
    function, summary = analyse(source)
    assert summary['return_resolution'] == 'unresolved'
    assert all(contract.get('source_ref', {}).get('kind') != 'vector-item' for contract in summary['returns'])


@pytest.mark.parametrize('parameters, shadow', [
    ('size_t n_args, const mp_obj_t *args', 'int SLOT = 1;'),
    ('size_t n_args, const mp_obj_t *args, int SLOT', ''),
])
def test_unknown_consumer_of_shadowed_vector_index_retains_whole_vector_input_coverage(parameters, shadow):
    source = 'enum { SLOT = 0 };\nmp_obj_t probe(' + parameters + ') { ' + shadow + ' consume(args[SLOT]); return 99; }'
    function, summary = analyse(source)
    assert summary['returns'][0]['kind'] == 'None'
    assert any(use['resolution'] == 'unresolved' and use['source_ref'] ==
               {'kind': 'c-parameter', 'scope': function['id'], 'parameter': 1} for use in summary['input_uses'])


def parser_index_source(operation, *, shadow='', parameter=False):
    return '''
typedef union { mp_obj_t u_obj; mp_int_t u_int; int u_bool; } mp_arg_val_t;
typedef struct { unsigned long qst; unsigned long flags; mp_arg_val_t defval; } mp_arg_t;
enum { SLOT = 0, MP_QSTR_first = 1, MP_QSTR_second = 2, MP_ARG_OBJ = 3 };
void mp_arg_parse_all(size_t n_args, const mp_obj_t *pos_args, void *kw_args,
    size_t n_allowed, const mp_arg_t *allowed, mp_arg_val_t *values);
mp_obj_t probe(size_t n_args, const mp_obj_t *pos_args, void *kw_args''' + (', int SLOT' if parameter else '') + ''') {
    const mp_arg_t allowed[] = {
        {MP_QSTR_first, MP_ARG_OBJ, {.u_obj = 99}},
        {MP_QSTR_second, MP_ARG_OBJ, {.u_obj = 99}},
    };
    mp_arg_val_t args[2];
    mp_arg_parse_all(n_args, pos_args, kw_args, 2, allowed, args);
    ''' + shadow + '\n' + operation + '''
}
'''


@pytest.mark.parametrize('shadow, parameter', [('int SLOT = 1;', False), ('', True)])
def test_shadowed_parser_index_never_preserves_wrong_field_return_identity(shadow, parameter):
    function, positive = analyse(parser_index_source('return args[SLOT].u_obj;'))
    assert positive['return_resolution'] == 'bounded-resolved'
    assert positive['returns'][0]['source_ref']['table_entry_index'] == 0
    function, negative = analyse(parser_index_source('return args[SLOT].u_obj;', shadow=shadow, parameter=parameter))
    assert negative['return_resolution'] == 'unresolved'
    assert all(contract.get('source_ref', {}).get('kind') != 'parser-field' for contract in negative['returns'])


@pytest.mark.parametrize('shadow, parameter', [('int SLOT = 1;', False), ('', True)])
def test_unknown_shadowed_parser_index_consumer_covers_both_potential_object_fields(shadow, parameter):
    function, summary = analyse(parser_index_source('consume(args[SLOT].u_obj); return 99;', shadow=shadow, parameter=parameter))
    affected = {use['source_ref']['table_entry_index'] for use in summary['input_uses']
                if use['resolution'] == 'unresolved' and use['source_ref'].get('kind') == 'parser-field'
                and use['source_ref']['storage_field'] == 'u_obj'}
    assert affected == {0, 1}
    assert summary['returns'][0]['kind'] == 'None'


def test_shadowed_tuple_constructor_count_never_claims_the_global_extent():
    function, summary = analyse('''
enum { COUNT = 2 };
mp_obj_t probe(mp_obj_t first, mp_obj_t second) {
    mp_obj_t items[2] = {first, second};
    int COUNT = 1;
    return mp_obj_new_tuple(COUNT, items);
}
''')
    assert summary['return_resolution'] == 'unresolved'
    assert summary['returns'][0]['kind'] == 'unresolved'


def test_expired_inner_enum_cannot_prune_a_supported_outer_return():
    result = public('''
enum { MODE = 0 };
mp_obj_t probe(void) {
    { enum { MODE = 1 }; }
    if (MODE == 1) return mp_obj_new_int(1);
    return mp_obj_new_str("x", 1);
}
''')
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'int', 'str'}
    assert all(entry['selected'] for entry in result['branch_selection'])


def test_condition_cache_keeps_distinct_lexical_use_points():
    result = public('''
enum { MODE = 0 };
mp_obj_t probe(void) {
    if (MODE == 1) return mp_obj_new_int(1);
    {
        enum { MODE = 1 };
        if (MODE == 1) return mp_obj_new_bytes("x", 1);
    }
    return mp_obj_new_str("x", 1);
}
''')
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'int', 'bytes'}
    evaluations = [value for entry in result['branch_selection']
                   for guard, value in zip(entry['guards'], entry['evaluated_conditions'])
                   if guard['condition'] == 'MODE == 1' and guard['branch'] == 'then']
    assert evaluations == [None, 1]


def test_expired_enum_cannot_specialize_a_forwarded_helper_argument():
    result = public('''
enum { MODE = 0 };
static mp_obj_t helper(int mode) {
    if (mode == 1) return mp_obj_new_int(1);
    return mp_obj_new_str("x", 1);
}
mp_obj_t probe(void) {
    { enum { MODE = 1 }; }
    return helper(MODE);
}
''')
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'int', 'str'}
    assert result['helper_propagation'][0]['constant_bindings'] == {}


@pytest.mark.parametrize('operation', [
    'mode = 0;',
    'opaque((mp_obj_t *)&mode);',
    '{ int mode = 0; consume(mode); }',
])
def test_shared_lexical_constants_cannot_revive_modified_or_shadowed_formals(operation):
    source = 'mp_obj_t probe(int mode) { ' + operation + ' return mp_obj_new_int(mode); }'
    function, summary = analyse(source)
    call = next(call for call in function['events']['calls'] if (call.get('callee') or {}).get('name') == 'mp_obj_new_int')
    use = call['expression_node']['arguments']['children'][0]
    expected = 1 if operation.startswith('{') else None
    assert lexical_constant(function, 'mode', use, {'mode': 1}) == expected


def test_shared_lexical_constants_mask_a_formal_at_an_inner_shadow_use():
    function, summary = analyse('''
enum { MODE = 0 };
mp_obj_t probe(int MODE) {
    { int MODE = 2; return mp_obj_new_int(MODE); }
}
''')
    call = next(call for call in function['events']['calls'] if (call.get('callee') or {}).get('name') == 'mp_obj_new_int')
    assert lexical_constant(function, 'MODE', call['expression_node']['arguments']['children'][0], {'MODE': 1}) is None


@pytest.mark.parametrize('operation', [
    'opaque_int(&(mode));',
    'opaque_int((int *)&(mode));',
    'int *alias = &(mode); opaque_int(alias);',
    'mode = 0;',
])
def test_semantic_specialization_cannot_prune_returns_after_formal_escape_or_mutation(operation):
    function, result = analyse('''
void opaque_int(int *value);
mp_obj_t probe(int mode) {
    ''' + operation + '''
    if (mode == 1) return mp_obj_new_int(1);
    return mp_obj_new_str("x", 1);
}
''', specialization={'mode': 1})
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'int', 'str'}
    assert all(entry['selected'] for entry in result['branch_selection'])


def test_supplied_formal_escape_blocks_downstream_helper_specialization():
    function, result = analyse('''
void opaque_int(int *value);
static mp_obj_t helper(int selected_mode) {
    if (selected_mode == 1) return mp_obj_new_int(1);
    return mp_obj_new_str("x", 1);
}
mp_obj_t probe(int mode) {
    opaque_int(&(mode));
    return helper(mode);
}
''', specialization={'mode': 1})
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'int', 'str'}
    assert result['helper_propagation'][0]['constant_bindings'] == {}


def test_later_formal_escape_does_not_invalidate_an_earlier_unchanged_condition():
    function, result = analyse('''
void opaque_int(int *value);
mp_obj_t probe(int mode) {
    if (mode == 1) return mp_obj_new_int(1);
    opaque_int(&(mode));
    return mp_obj_new_str("x", 1);
}
''', specialization={'mode': 1})
    assert result['return_resolution'] == 'bounded-resolved'
    assert [contract['kind'] for contract in result['returns']] == ['int']


def unordered_forwarding_source(argument, setup=''):
    return '''
int change(int *value);
static mp_obj_t helper(int selected_mode, int ignored) {
    if (selected_mode == 1) return mp_obj_new_int(1);
    return mp_obj_new_str("x", 1);
}
mp_obj_t probe(int mode) {
    ''' + setup + '''
    return helper(mode, ''' + argument + ''');
}
'''


@pytest.mark.parametrize('argument, setup', [
    ('change(&(mode))', ''),
    ('change((int *)&(mode))', ''),
    ('change(alias)', 'int *alias = &(mode);'),
    ('change(alias)', 'int *alias; alias = &(mode);'),
    ('(mode = 0)', ''),
])
def test_unordered_sibling_mutation_cannot_specialize_a_forwarded_formal(argument, setup):
    function, result = analyse(unordered_forwarding_source(argument, setup), specialization={'mode': 1})
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'int', 'str'}
    assert result['helper_propagation'][0]['constant_bindings'] == {}


def test_shared_constant_argument_use_accounts_for_an_escaping_sibling():
    function, summary = analyse(unordered_forwarding_source('change(&(mode))'))
    call = next(call for call in function['events']['calls'] if (call.get('callee') or {}).get('name') == 'helper')
    use = call['expression_node']['arguments']['children'][0]
    assert lexical_constant(function, 'mode', use, {'mode': 1}) is None


def test_pure_outer_by_value_forwarding_keeps_an_unchanged_formal_specialization():
    function, result = analyse(unordered_forwarding_source('0'), specialization={'mode': 1})
    assert result['return_resolution'] == 'bounded-resolved'
    assert [contract['kind'] for contract in result['returns']] == ['int']
    assert result['helper_propagation'][0]['constant_bindings']['selected_mode'] == 1


def test_nested_unknown_vector_consumer_cannot_preserve_a_forwarded_element_identity():
    function, result = analyse('''
int mutate_vector(const mp_obj_t *args);
static mp_obj_t echo(mp_obj_t value, int ignored) { return value; }
mp_obj_t probe(const mp_obj_t *args) {
    return echo(args[0], mutate_vector(args));
}
''')
    assert result['return_resolution'] == 'unresolved'
    assert all(contract.get('source_ref', {}).get('kind') != 'vector-item' for contract in result['returns'])


def scalar_read_source(mode_type='unsigned', operation=''):
    return '''
void read_native(size_t length, const mp_obj_t *items, unsigned mode);
void write_native(unsigned *mode);
mp_obj_t probe(size_t n_args, const mp_obj_t *pos_args, ''' + mode_type + ''' mode) {
    read_native(n_args - mode, pos_args + mode, mode);
    ''' + operation + '''
    if (mode == 1) return mp_obj_new_str("ok", 2);
    return 99;
}
'''


@pytest.mark.parametrize('mode, kind', [(1, 'str'), (2, 'None')])
def test_source_declared_c_integer_reads_preserve_selected_mode_without_a_callee_whitelist(mode, kind):
    function, result = analyse(scalar_read_source(), specialization={'mode': mode})
    assert result['return_resolution'] == 'bounded-resolved'
    assert [contract['kind'] for contract in result['returns']] == [kind]


def test_opaque_object_typedef_is_not_a_c_integer_read_certificate():
    function, result = analyse(scalar_read_source('mp_obj_t'), specialization={'mode': 1})
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'str', 'None'}
    composed = bind_public_contracts(result, bindings(function))
    assert composed['public_parameters'][2]['accepted_domain']['contract'] != {'kind': 'object'}


@pytest.mark.parametrize('operation', [
    'mode = 2;',
    'unsigned *alias = &(mode); write_native(alias);',
    'write_native(&(mode));',
])
def test_declared_scalar_read_authority_cannot_override_mutation_or_address_escape(operation):
    function, result = analyse(scalar_read_source(operation=operation), specialization={'mode': 1})
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'str', 'None'}


def test_c_scalar_read_certificate_does_not_change_generic_object_input_domains():
    function, result = analyse('''
void read_scalar(unsigned mode);
mp_obj_t probe(unsigned mode, mp_obj_t value) {
    read_scalar(mode + 1);
    consume(value);
    if (mode == 1) return mp_obj_new_str("ok", 2);
    return 99;
}
''', specialization={'mode': 1})
    composed = bind_public_contracts(result, bindings(function))
    assert [contract['kind'] for contract in composed['returns']] == ['str']
    assert composed['public_parameters'][1]['accepted_domain']['resolution'] == 'unresolved'
    assert composed['public_parameters'][1]['accepted_domain']['contract'] != {'kind': 'object'}


def test_selected_allocator_failure_closes_only_source_proven_terminal_errors():
    authority = checked_receiver_authority(connected_framework_fixture())
    assert authority['resolution'] == 'bounded-resolved'
    failure = authority['fresh_region_authority']['failure_authority'][0]
    assert failure['kind'] == 'selected-terminal-allocation-failure'
    assert failure['source_span']['source'] == '/fixture/py/runtime.c'
    assert len(failure['terminal_errors']) == 2
    assert len(failure['terminal_message_authority']) == 2


@pytest.mark.parametrize('old,new', [
    ('mp_raise_msg_varg(&mp_type_MemoryError, (mp_rom_error_text_t)MP_COMPRESSED_ROM_TEXT("allocating " "%lu" " bytes"), num_bytes);', 'return;'),
    ('mp_raise_msg_varg(&mp_type_MemoryError,', 'if (num_bytes == 0) mp_raise_msg_varg(&mp_type_MemoryError,'),
    ('    (void)0;\n    if (gc_is_locked())', '    if (num_bytes == 0) return;\n    if (gc_is_locked())'),
    ('MP_COMPRESSED_ROM_TEXT("allocating " "%lu" " bytes")', 'MP_COMPRESSED_ROM_TEXT((const char *)num_bytes)'),
    ('mp_raise_msg_varg(&mp_type_MemoryError,', 'mp_raise_msg_varg(&mp_type_int,'),
    ('    (void)0;\n    if (gc_is_locked())', '    opaque_count(num_bytes);\n    if (gc_is_locked())'),
])
def test_selected_allocator_failure_unknown_or_successful_paths_do_not_certify_receiver(old, new):
    rows = connected_framework_fixture(lambda source: source.replace(old, new))
    authority = checked_receiver_authority(rows)
    assert authority['resolution'] == 'unresolved'
    assert authority['unresolved']


def test_selected_allocator_requires_the_actual_nonzero_null_failure_branch():
    def mutate(path, source):
        return source.replace('    if (ptr == ((void *)0) && num_bytes != 0) m_malloc_fail(num_bytes);\n', '') if path.endswith('/malloc.c') else source
    authority = checked_receiver_authority(connected_framework_fixture(dependency_mutation=mutate))
    assert authority['resolution'] == 'unresolved'
    assert authority['unresolved']


def test_unknown_consumers_retain_formal_provenance_after_prior_escape():
    function, summary = analyse('''mp_obj_t probe(mp_obj_t value) {
        consume(value);
        opaque(&value);
        consume(value);
        return 99;
    }''')
    consumers = [use for use in summary['input_uses'] if use.get('helper') == 'consume']
    assert len(consumers) == 2
    assert all(use['resolution'] == 'unresolved' and use['source_ref'] ==
               {'kind': 'c-parameter', 'scope': function['id'], 'parameter': 0}
               for use in consumers)
    assert consumers[0]['byte_start'] != consumers[1]['byte_start']
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'


def test_unknown_mutated_alias_consumer_keeps_all_possible_original_inputs():
    function, summary = analyse('''mp_obj_t probe(mp_obj_t first, mp_obj_t second) {
        mp_obj_t alias = first;
        alias = second;
        consume(alias);
        return 99;
    }''')
    affected = {use['source_ref']['parameter'] for use in summary['input_uses']
                if use.get('helper') == 'consume' and use['resolution'] == 'unresolved'}
    assert affected == {0, 1}
    result = bind_public_contracts(summary, bindings(function))
    assert all(parameter['accepted_domain']['resolution'] == 'unresolved'
               for parameter in result['public_parameters'])


def test_local_shadow_consumer_does_not_taint_an_unrelated_original_input():
    function, summary = analyse('''mp_obj_t probe(mp_obj_t value) {
        mp_obj_is_true(value);
        { mp_obj_t value = 99; consume(value); }
        return 99;
    }''')
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'bounded-resolved'
    assert result['public_parameters'][0]['accepted_domain']['contract'] == {'kind': 'object'}


@pytest.mark.parametrize('operation', [
    'first = second; consume(first);',
    'first |= second; consume(first);',
    'mp_obj_t alias = first; alias |= second; consume(alias);',
    'mp_obj_t *slot = &first; *slot = second; consume(first);',
    'mp_obj_t alias = first; mp_obj_t *slot = &alias; *slot = second; consume(alias);',
])
def test_unknown_consumer_keeps_assignment_rhs_provenance_without_identity_precision(operation):
    function, summary = analyse('mp_obj_t probe(mp_obj_t first, mp_obj_t second) { mp_obj_is_true(second); ' + operation + ' return 99; }')
    affected = {use['source_ref']['parameter'] for use in summary['input_uses']
                if use.get('helper') == 'consume' and use['resolution'] == 'unresolved'}
    assert affected == {0, 1}
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][1]['accepted_domain']['resolution'] == 'unresolved'


@pytest.mark.parametrize('shadow', [
    '{ mp_obj_t first = 99; mp_obj_t *slot = &first; *slot = second; }',
    '{ mp_obj_t alias = 99; mp_obj_t *slot = &alias; *slot = second; }',
])
def test_unrelated_shadowed_address_store_does_not_change_outer_consumer_origins(shadow):
    function, summary = analyse('mp_obj_t probe(mp_obj_t first, mp_obj_t second) { mp_obj_is_true(second); mp_obj_t alias = first; ' + shadow + ' consume(alias); return 99; }')
    affected = {use['source_ref']['parameter'] for use in summary['input_uses']
                if use.get('helper') == 'consume' and use['resolution'] == 'unresolved'}
    assert affected == {0}
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][1]['accepted_domain']['contract'] == {'kind': 'object'}


def test_unordered_call_arguments_keep_sibling_assignment_rhs_provenance():
    function, summary = analyse('''void consume_pair(mp_obj_t first, mp_obj_t second);
    mp_obj_t probe(mp_obj_t first, mp_obj_t second) {
        mp_obj_is_true(second);
        consume_pair(first, first = second);
        return 99;
    }''')
    affected = {use['source_ref']['parameter'] for use in summary['input_uses']
                if use.get('helper') == 'consume_pair' and use['operand'] == 0
                and use['resolution'] == 'unresolved'}
    assert affected == {0, 1}
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][1]['accepted_domain']['resolution'] == 'unresolved'


def test_native_write_target_does_not_preserve_a_whole_object_domain():
    function, summary = analyse('''mp_obj_t probe(mp_obj_t value) {
        mp_obj_is_true(value);
        *(volatile mp_int_t *)value = 0;
        return 99;
    }''')
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert any(use.get('helper') is None and use['resolution'] == 'unresolved'
               and use['source_ref'] == {'kind': 'c-parameter', 'scope': function['id'], 'parameter': 0}
               for use in summary['input_uses'])


@pytest.mark.parametrize('declarations,operation', [
    ('mp_obj_t pending;', 'pending = value;'),
    ('mp_obj_t pending[1];', 'pending[0] = value;'),
    ('struct { mp_obj_t item; } pending;', 'pending.item = value;'),
    ('mp_obj_t *get_slot(void);', 'mp_obj_t *slot = get_slot(); *slot = value;'),
    ('mp_obj_t *pending;', 'mp_obj_t local = 99; pending = &local; local = value;'),
    ('mp_obj_t *pending;', 'mp_obj_t local = 99; mp_obj_t *slot = &local; pending = slot; *slot = value;'),
    ('void export_slot(mp_obj_t *);', 'mp_obj_t local = 99; mp_obj_t *slot = &local; export_slot(slot); *slot = value;'),
    ('void export_slot(mp_obj_t *);', 'mp_obj_t local[1]; export_slot(local); local[0] = value;'),
    ('void export_slot(void *);', 'struct { mp_obj_t item; } local; export_slot(&local); local.item = value;'),
])
def test_unqualified_global_and_escaped_storage_keeps_original_input_unknown(declarations, operation):
    function, summary = analyse(declarations + '''
    void consume_pending(void);
    mp_obj_t probe(mp_obj_t value) {
        mp_obj_is_true(value);
        ''' + operation + '''
        consume_pending();
        return 99;
    }''')
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert any(use.get('reason') == 'input value escapes through unqualified storage'
               and use['source_ref'] == {'kind': 'c-parameter', 'scope': function['id'], 'parameter': 0}
               for use in summary['input_uses'])


@pytest.mark.parametrize('operation', [
    'mp_obj_t copy; copy = value;',
    'mp_obj_t copy = 99; mp_obj_t *slot = &copy; *slot = value;',
    'mp_obj_t copy[1]; copy[0] = value;',
    'struct { mp_obj_t item; } copy; copy.item = value;',
])
def test_bounded_private_stack_storage_does_not_invent_an_input_consumer(operation):
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { mp_obj_is_true(value); ' + operation + ' return 99; }')
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['contract'] == {'kind': 'object'}


def test_unknown_nested_scalar_result_is_not_a_qualified_converter_boundary():
    function, summary = analyse('''mp_int_t unknown_scalar(mp_obj_t);
    mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(unknown_scalar(value)); }''')
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert any(use.get('helper') == 'unknown_scalar' and use['resolution'] == 'unresolved'
               for use in summary['input_uses'])


def test_local_extern_declaration_does_not_hide_a_global_input_escape():
    function, summary = analyse('''mp_obj_t pending;
    void consume_pending(void);
    mp_obj_t probe(mp_obj_t value) {
        extern mp_obj_t pending;
        mp_obj_is_true(value);
        pending = value;
        consume_pending();
        return 99;
    }''')
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert any(use.get('reason') == 'input value escapes through unqualified storage'
               and use['source_ref'] == {'kind': 'c-parameter', 'scope': function['id'], 'parameter': 0}
               for use in summary['input_uses'])


@pytest.mark.parametrize('storage', ['static', 'static _Thread_local', '_Thread_local static', 'static __thread'])
def test_nonautomatic_storage_does_not_discard_cross_invocation_address_escape(storage):
    function, summary = analyse('''void export_slot(mp_obj_t *);
    void consume_pending(void);
    mp_obj_t probe(mp_obj_t value, mp_obj_t remember) {
        mp_obj_is_true(value);
        ''' + storage + ''' mp_obj_t saved;
        if (remember) {
            export_slot(&saved);
        } else {
            saved = value;
            consume_pending();
        }
        return 99;
    }''')
    saved = next(variable for variable in function['events']['variables'] if variable['name'] == 'saved')
    assert set(storage.split()) <= set(saved['declaration_specifiers'].split())
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
    assert any(use.get('reason') == 'input value escapes through unqualified storage'
               and use['source_ref'] == {'kind': 'c-parameter', 'scope': function['id'], 'parameter': 0}
               for use in summary['input_uses'])


@pytest.mark.parametrize('storage', ['auto', 'register'])
def test_automatic_storage_specifiers_preserve_a_bounded_local_copy(storage):
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { mp_obj_is_true(value); ' + storage + ' mp_obj_t copy; copy = value; return 99; }')
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['contract'] == {'kind': 'object'}


@pytest.mark.parametrize('storage', ['static', 'static _Thread_local', 'static __thread'])
def test_nonautomatic_initializer_is_not_a_current_call_return_definition(storage):
    result = public('''
    mp_obj_t probe(int update) {
        ''' + storage + ''' mp_obj_t saved = 99;
        if (update) {
            saved = mp_obj_new_str("x", 1);
            return 99;
        }
        return saved;
    }''')
    assert result['return_resolution'] == 'unresolved'
    assert any(contract.get('kind') == 'unresolved'
               and 'nonautomatic' in contract.get('reason', '') for contract in result['returns'])


@pytest.mark.parametrize('storage', ['auto', 'register'])
def test_automatic_initializer_keeps_its_complementary_branch_value(storage):
    result = public('''
    mp_obj_t probe(int update) {
        ''' + storage + ''' mp_obj_t saved = 99;
        if (update) {
            saved = mp_obj_new_str("x", 1);
            return 99;
        }
        return saved;
    }''')
    assert result['return_resolution'] == 'bounded-resolved'
    assert {contract['kind'] for contract in result['returns']} == {'None'}


@pytest.mark.parametrize('storage', ['static', 'static _Thread_local'])
def test_nonautomatic_mode_initializer_cannot_specialize_a_forwarded_helper(storage):
    result = public('''
    enum { MODE = 0 };
    static mp_obj_t helper(int selected_mode) {
        if (selected_mode == 1) return mp_obj_new_int(1);
        return mp_obj_new_str("x", 1);
    }
    mp_obj_t probe(int update) {
        ''' + storage + ''' int MODE = 1;
        if (update) {
            MODE = 0;
            return 99;
        }
        return helper(MODE);
    }''')
    assert result['helper_propagation'][0]['constant_bindings'] == {}
    assert {contract['kind'] for contract in result['returns']} == {'None', 'int', 'str'}


def test_persistent_tuple_storage_is_not_folded_from_its_one_time_initializer():
    result = public('''
    mp_obj_t probe(int update) {
        static mp_obj_t items[1] = {99};
        if (update) {
            items[0] = mp_obj_new_str("x", 1);
            return 99;
        }
        return mp_obj_new_tuple(1, items);
    }''')
    assert result['return_resolution'] == 'unresolved'
    assert not any(contract['kind'] == 'tuple' for contract in result['returns'])


def test_persistent_mpz_accumulator_is_not_a_per_call_checked_conversion():
    bodies = mpz_helper_fixture(lambda native: native.replace('mp_uint_t val = 0;', 'static mp_uint_t val = 0;'))
    function, summary = analyse('mp_obj_t probe(mp_obj_t value) { return mp_obj_new_int(mp_obj_get_int(value)); }', helper_bodies=bodies)
    result = bind_public_contracts(summary, bindings(function))
    assert result['public_parameters'][0]['accepted_domain']['resolution'] == 'unresolved'
