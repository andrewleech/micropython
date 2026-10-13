"""Build-selected MicroPython API inventory, with explicit bounded coverage."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import functools
import gc
import hashlib
import importlib.metadata
import json
import multiprocessing
import re
import subprocess
import traceback

from . import compiler
from .frontend import syntax_adapter, constant
from .bindings import definitions, registration_records, call_parts, scalar_field, strip_outer, constant_value
from .semantics import semantics, direct_parameter, bind_public_contracts, checked_receiver_authority, input_helper_authority, lexical_constant, event_applicability
from .link import collect_ownership, join
from .frozen_payload import collect_frozen_payload
from .structure import deep_copy


def _resolution(status, reasons=(), **evidence):
    return {'status': status, 'reasons': sorted(set(reasons)), **evidence}


def _extract(record, compdb, root):
    try:
        return _extract_impl(record, compdb, root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f'API extraction failed for compiled TU {record["identity"]}; '
                           f'physical source {record["physical"]}: {exc}') from exc


def _extract_impl(record, compdb, root):
    physical = record['physical']
    if Path(physical).suffix != '.c':
        arguments = compdb.arguments_of(record)
        response_snapshots = record['_response_snapshots']
        if '_expanded_arguments' in record:
            expanded, response_inputs = record['_expanded_arguments'], record['_response_inputs']
        else:
            response_snapshots = dict(response_snapshots)
            expanded, response_inputs = compiler.expand_arguments(arguments, record['directory'], snapshots=response_snapshots)
        executable = compiler.executable(expanded[0], record['directory'])
        snapshots = compiler.reconcile_inputs([
            ('selected source', {record['_source_snapshot']['path']: record['_source_snapshot']}),
            ('selected response operands', response_snapshots),
            ('current non-C inputs', compiler.recorded_inputs([compdb.entry_file(record), executable]))])
        return {'translation_unit': record['identity'], 'source': physical, 'definitions': [], 'functions': [],
                'registrations': [], 'errors': [], 'coverage': {'status': 'unassessed', 'reason': 'non-C compiler record; syntax and dependency extraction unassessed'},
                'input': {'arguments': arguments, 'directory': record['directory'],
                          'dependencies': {physical: record['_source_snapshot']['sha256']},
                          'compiler': executable, 'compiler_sha256': snapshots[str(Path(executable).absolute())]['sha256'],
                          'object': record['object'], 'response_inputs': response_inputs, 'receipts': [],
                          'input_snapshots': snapshots}}
    prepared = compiler.prepare(record, compdb)
    expanded, code, origins, contracts, expansion_receipts, expansion = compiler.expand(prepared, compdb)
    prepared['receipts'].extend(expansion_receipts)
    prepared['contracts'] = contracts
    compiler.validate_selection(prepared, compdb)
    directive_code, directive_origins = compiler.unmark(prepared['_scan']['source'], prepared['directory'])
    try:
        declarations, functions, errors, frontend = syntax_adapter(code, origins, physical, prepared)
    except Exception as exc:
        raise RuntimeError(f'C syntax frontend failed for {physical}: {type(exc).__name__}: {exc}') from exc
    if errors:
        raise RuntimeError(f'C syntax frontend failed for {physical}: ' + json.dumps(errors, sort_keys=True))
    template_sources = [str(root / name) for name in (
        'py/obj.h', 'py/runtime.h', 'py/objstr.h', 'py/objint.h', 'py/objmodule.h', 'py/objlist.h', 'py/objtuple.h', 'py/mpz.h',
        'py/obj.c', 'py/argcheck.c', 'py/objstr.c', 'py/objint.c',
        'py/objint_mpz.c', 'py/objint_longlong.c', 'py/objmodule.c')]
    for function in functions:
        function['id'] = record['identity'] + function['id'][len(physical):]
        function['translation_unit'] = record['identity']
        for call in function['events']['calls']:
            call['qualified_template_sources'] = template_sources
    by_name = {}
    for function in functions:
        by_name.setdefault(function['name'], []).append(function)
    for function in functions:
        tests = [{'name': name, 'expression': condition['expression'], 'source_span': condition['source_span']}
                 for condition in (function['events']['conditions'] if not function['events'].get('semantic_unassessed') else [])
                 for name in re.findall(r'MP_QSTR_(\w+)', condition['expression'])]
        function.update(
            semantics={**semantics(function, by_name, errors, contracts),
                       'diagnostic_stage': 'translation-unit-extraction'},
            handler_attributes=sorted({test['name'] for test in tests}),
            handler_attribute_tests=tests)
    prepared['frontend'] = frontend
    object_definitions = definitions(declarations, functions, errors, record['identity'], directive_code, directive_origins, prepared)
    registrations = registration_records(directive_code, directive_origins, physical, prepared)
    empty_program = not code.strip()
    return {'translation_unit': record['identity'], 'source': physical,
            'definitions': object_definitions,
            'functions': functions,
            'registrations': registrations,
            'errors': errors, 'contracts': contracts, 'frontend': frontend,
            'coverage': {'status': 'processed-empty' if empty_program else 'processed',
                         'owned_declarations': 'bounded-recovered', 'empty_selected_program': empty_program,
                         'no_active_bindings': not registrations and not any(
                             definition['kind'] in ('module', 'type', 'wrapper', 'dictionary', 'map-table')
                             for definition in object_definitions),
                         'project_header_definitions': 'all compiler non-system dependencies traversed',
                         'project_headers': [p for p in prepared['project_dependencies'] if p != physical],
                         'system_header_definitions': 'unassessed; excluded by the compiler system-header classification (-MM set)',
                         'unsupported_definitions': [{'identity': d['name'], 'source': d['source_span'],
                                                      'reason': 'no supported initialized object template'}
                                                     for d in declarations if '=' not in d['expression']]},
            'input': {'arguments': prepared['arguments'], 'directory': prepared['directory'],
                      'object': record['object'], 'source_sha256': record['_source_snapshot']['sha256'],
                      'dependencies': prepared['dependencies'], 'compiler': prepared['compiler'],
                      'compiler_sha256': prepared['compiler_sha256'], 'response_inputs': prepared['response_inputs'],
                      'input_snapshots': prepared['input_snapshots'],
                      'compiler_target': prepared['compiler_target'], 'compiler_version': prepared['compiler_version'],
                      'compiler_expanded_sha256': hashlib.sha256(expanded.encode()).hexdigest(),
                      'compiler_directives_sha256': hashlib.sha256(prepared['_scan']['source'].encode()).hexdigest(),
                      'expansion': expansion, 'selection_check': prepared['selection_check'],
                      'receipts': prepared['receipts']},
            '_selection': prepared['_selection']}


def _parameter_declaration(function, index):
    contract = function.get('parameter_source_contract', {})
    if contract.get('declaration_binding_resolution') != 'resolved':
        return None
    declarations = contract.get('declarations', [])
    name = function['parameters'][index] if index < len(function['parameters']) else None
    candidates = [entry for entry in declarations if entry.get('name') == name]
    return candidates[0] if name and len(candidates) == 1 else None


def _parameter_family(function):
    """Qualify object-vector ABI relations from original C declarators."""
    declarations = [_parameter_declaration(function, index) for index in range(len(function['parameters']))]
    if any(entry is None for entry in declarations):
        return None
    spellings = [entry.get('expression', entry.get('declaration_expression', '')) for entry in declarations]
    counts = [index for index, spelling in enumerate(spellings)
              if re.fullmatch(r'(?:const )?size_t \w+', spelling)]
    vectors = [index for index, spelling in enumerate(spellings)
               if re.fullmatch(r'const mp_obj_t \* \w+', spelling)]
    keywords = [index for index, spelling in enumerate(spellings)
                if re.fullmatch(r'mp_map_t \* \w+', spelling)]
    if len(counts) != 1 or len(vectors) != 1 or len(keywords) > 1:
        return None
    return {'count': counts[0], 'vector': vectors[0],
            'keywords': keywords[0] if keywords else None,
            'declarations': declarations}


def _parser_forwarding(parser, function, public_function, shape):
    family = _parameter_family(function)
    public_family = _parameter_family(public_function)
    args = parser['arguments']
    if (not family or not public_family or len(args) != 6 or parser.get('guards')
            or parser.get('successful_path_applicability', {}).get('resolution') != 'bounded-resolved'):
        return None
    params = function['parameters']
    bindings = parser.get('caller_parameter_bindings', {})
    argument_nodes = parser.get('argument_nodes', [])
    if len(argument_nodes) != len(args):
        return None
    specialization = dict(parser.get('constant_bindings', {}))
    for name, binding in bindings.items():
        if binding.get('constant') is not None:
            if name in specialization and specialization[name] != binding['constant']:
                return None
            specialization[name] = binding['constant']
    environment = dict(function['constants'])
    for name in specialization:
        value = lexical_constant(function, name, argument_nodes[0], specialization)
        if value is not None:
            environment[name] = value
    count_name, vector_name = params[family['count']], params[family['vector']]
    count_arg, vector_arg = strip_outer(args[0]), strip_outer(args[1])
    if count_arg == count_name and vector_arg == vector_name:
        shift = 0
    else:
        count = re.fullmatch(re.escape(count_name) + r' - (.+)', count_arg)
        vector = re.fullmatch(re.escape(vector_name) + r' \+ (.+)', vector_arg)
        if not count or not vector or strip_outer(count[1]) != strip_outer(vector[1]):
            return None
        shift = lexical_constant(function, count[1], argument_nodes[0], specialization)
        vector_shift = lexical_constant(function, vector[1], argument_nodes[1], specialization)
        if type(shift) is not int or shift not in (1, 2) or vector_shift != shift:
            return None
    if any(direct_parameter(function, params[family[role]], argument_nodes[argument]) != family[role]
           for role, argument in (('count', 0), ('vector', 1))):
        return None
    keyword = family['keywords']
    if (keyword is None or args[2] != params[keyword]
            or direct_parameter(function, params[keyword], argument_nodes[2]) != keyword):
        return None
    if function['id'] != public_function['id']:
        for role in ('count', 'vector', 'keywords'):
            binding = bindings.get(params[family[role]], {})
            if binding.get('direct_outer_parameter') != public_family[role]:
                return None
    if type(shape.get('minimum')) is not int or shape['minimum'] < shift:
        return None
    return {'shift': shift, 'family': family, 'public_family': public_family,
            'environment': environment, 'specialization': specialization, 'parser_scope': function['id'],
            'public_scope': public_function['id'],
            'authority': 'selected unchanged count/vector/keyword declarations with equal bounded parser offset'}


def _parser_protocol(facts, function):
    """Certify the selected indexed parser loop and its forwarding branches."""
    events, params = function['events'], function['parameters']
    if len(params) != 6 or events.get('semantic_unassessed'):
        return None
    types = ('size_t', 'const mp_obj_t *', 'mp_map_t *', 'size_t', 'const mp_arg_t *', 'mp_arg_val_t *')
    if any(not (declaration := _parameter_declaration(function, index))
           or declaration.get('declaration_expression') != c_type + ' ' + params[index]
           for index, c_type in enumerate(types)):
        return None
    n_pos, pos, kws, n_allowed, allowed, out = params
    flow = events.get('flow')
    if not flow:
        return None
    root = flow[0]
    nodes = flow
    loops = [node for node in nodes if node.get('kind') == 'loop']
    if len(loops) != 1:
        return None
    loop = loops[0]
    if (loop.get('loop_kind') != 'for_statement'
            or loop.get('initializer_expression') != 'size_t i = 0 ;'
            or loop.get('condition_expression') != 'i < ' + n_allowed
            or loop.get('update_expression') != 'i ++'):
        return None

    def children(block):
        while (block.get('kind') == 'block' and len(block.get('children', [])) == 1
               and flow[block['children'][0]].get('kind') == 'block'):
            block = flow[block['children'][0]]
        return [flow[position] for position in block.get('children', [])]

    def branch(block, condition):
        candidates = [block] if block.get('kind') == 'if' else children(block)
        matches = [node for node in candidates if node.get('kind') == 'if'
                   and strip_outer(node['condition']) == condition]
        return matches[0] if len(matches) == 1 else None

    def guard_key(guards):
        return [(guard.get('condition_id'), guard['branch']) for guard in guards]

    def guarded_write(expression, expected):
        matches = [write for write in events['writes'] if write['expression'] == expression]
        return matches[0] if len(matches) == 1 and guard_key(matches[0].get('guards', [])) == expected else None

    body = flow[loop['body']]
    positional = branch(body, 'i < ' + n_pos)
    conversion = branch(body, '( ' + allowed + ' [ i ] . flags & MP_ARG_KIND_MASK ) == MP_ARG_BOOL')
    if not positional or not conversion:
        return None
    integer = branch(flow[conversion['else']], '( ' + allowed + ' [ i ] . flags & MP_ARG_KIND_MASK ) == MP_ARG_INT')
    if not integer:
        return None
    boolean_guard = [(conversion['byte_start'], 'then')]
    integer_guard = [(conversion['byte_start'], 'else'), (integer['byte_start'], 'then')]
    object_guard = [(conversion['byte_start'], 'else'), (integer['byte_start'], 'else')]
    if (not guarded_write(out + ' [ i ] . u_bool = mp_obj_is_true ( given_arg )', boolean_guard)
            or not guarded_write(out + ' [ i ] . u_int = mp_obj_get_int ( given_arg )', integer_guard)
            or not guarded_write(out + ' [ i ] . u_obj = given_arg', object_guard)):
        return None
    top_ifs = [flow[position] for position in body.get('children', []) if flow[position].get('kind') == 'if']
    if len(top_ifs) != 2:
        return None
    keyword_only = branch(flow[positional['then']], allowed + ' [ i ] . flags & MP_ARG_KW_ONLY')
    missing_candidates = [node for node in children(flow[positional['else']]) if node.get('kind') == 'if'
                          and strip_outer(node['condition']) in ('kw == 0', 'kw == ( ( void * ) 0 )')]
    if not keyword_only or len(missing_candidates) != 1:
        return None
    missing = missing_candidates[0]
    required = branch(flow[missing['then']], allowed + ' [ i ] . flags & MP_ARG_REQUIRED')
    if not required:
        return None
    positional_guard = [(positional['byte_start'], 'then')]
    keyword_guard = [(positional['byte_start'], 'else'), (missing['byte_start'], 'else')]
    default_guard = [(positional['byte_start'], 'else'), (missing['byte_start'], 'then')]
    given_pos = guarded_write('given_arg = ' + pos + ' [ i ]', positional_guard)
    given_kw = guarded_write('given_arg = kw -> value', keyword_guard)
    default = guarded_write(out + ' [ i ] = ' + allowed + ' [ i ] . defval', default_guard)
    if not given_pos or not given_kw or not default:
        return None
    continues = [node for node in nodes if node.get('kind') == 'continue']
    missing_then = flow[missing['then']].get('children', [])
    if (len(continues) != 1 or not any(flow[position] is continues[0] for position in missing_then)
            or continues[0]['byte_start'] <= default.get('byte_end', -1)
            or flow[missing_then[-1]] is not continues[0]):
        return None
    gotos = [node for node in nodes if node.get('kind') == 'goto']
    labels = [node for node in nodes if node.get('kind') == 'label']
    if (len(gotos) != 1 or gotos[0].get('target') != 'extra_positional'
            or len(labels) != 1 or labels[0].get('name') != 'extra_positional'
            or not any(flow[position] is gotos[0] for position in flow[keyword_only['then']].get('children', []))):
        return None
    pos_excess = branch(root, 'pos_found < ' + n_pos)
    kw_excess = branch(root, 'kws_found < ' + kws + ' -> used')
    if not pos_excess or not kw_excess or not any(flow[position] is labels[0] for position in flow[pos_excess['then']].get('children', [])):
        return None
    root_control = [node for node in children(root) if node.get('kind') != 'fallthrough']
    if root_control != [loop, pos_excess, kw_excess]:
        return None
    if any(node.get('kind') in ('return', 'break') for node in nodes):
        return None
    variables = {name: [variable for variable in events['variables'] if variable['name'] == name]
                 for name in ('pos_found', 'kws_found', 'i', 'given_arg', 'kw')}
    if any(len(values) != 1 for values in variables.values()):
        return None
    if any((variables[name][0].get('initializer_node') or {}).get('expression') != '0'
           or variables[name][0].get('type_expression') != 'size_t'
           or not 0 <= variables[name][0].get('byte_start', -1) < loop['byte_start']
           for name in ('pos_found', 'kws_found')):
        return None
    if not guarded_write('pos_found ++', positional_guard) or not guarded_write('kws_found ++', keyword_guard):
        return None
    index_writes = [write for write in events['writes'] if write['target'] == 'i']
    if (len(index_writes) != 1 or index_writes[0]['expression'] != 'i ++'
            or index_writes[0].get('guards') or index_writes[0].get('loops') != [loop['byte_start']]
            or index_writes[0].get('byte_start', -1) >= given_pos.get('byte_start', -1)):
        return None
    expected_writes = {'i ++', 'pos_found ++', 'kws_found ++',
                       'given_arg = ' + pos + ' [ i ]', 'given_arg = kw -> value',
                       out + ' [ i ] = ' + allowed + ' [ i ] . defval',
                       out + ' [ i ] . u_bool = mp_obj_is_true ( given_arg )',
                       out + ' [ i ] . u_int = mp_obj_get_int ( given_arg )',
                       out + ' [ i ] . u_obj = given_arg'}
    if (len(events['writes']) != len(expected_writes)
            or {write['expression'] for write in events['writes']} != expected_writes):
        return None
    lookups = [call for call in events['calls'] if call_parts(call['expression'])[0] == 'mp_map_lookup']
    if len(lookups) != 1 or guard_key(lookups[0].get('guards', [])) != [(positional['byte_start'], 'else')]:
        return None
    lookup = lookups[0]
    callee = lookup.get('callee') or {}
    source_dir = Path(function['source_span']['source']).parent
    if callee.get('kind') != 'FUNCTION_DECL' or callee.get('source_span', {}).get('source') not in {
            str(source_dir / 'runtime.h'), str(source_dir / 'obj.h')}:
        return None
    arguments = call_parts(lookup['expression'])[1]
    environment = {**facts.get('frontend', {}).get('contract_constants', {}), **function['constants']}
    lookup_mode = constant('MP_MAP_LOOKUP', environment)
    qstr_template = facts.get('contracts', {}).get('rom_qstr', '')
    expected_key = qstr_template.replace('MP_QSTR___name__', allowed + ' [ i ] . qst').replace('mp_rom_obj_t', 'mp_obj_t')
    if (len(arguments) != 3 or arguments[0] != kws or arguments[1] != expected_key
            or lookup_mode is None or constant(arguments[2], environment) != lookup_mode):
        return None
    if (variables['kw'][0].get('type_expression') != 'mp_map_elem_t'
            or (variables['kw'][0].get('initializer_node') or {}).get('expression') != lookup['expression']):
        return None
    errors = []
    error_calls = []
    error_names = {'mp_arg_error_terse_mismatch', 'mp_raise_msg_varg', 'mp_raise_msg', 'mp_raise_TypeError'}
    expected_errors = [
        [(positional['byte_start'], 'else'), (missing['byte_start'], 'then'), (required['byte_start'], 'then')],
        [(pos_excess['byte_start'], 'then')], [(kw_excess['byte_start'], 'then')]]
    for expected in expected_errors:
        matches = [call for call in events['calls'] if call_parts(call['expression'])[0] in error_names
                   and guard_key(call.get('guards', [])) == expected]
        if len(matches) != 1:
            return None
        call = matches[0]
        helper, error_arguments = call_parts(call['expression'])
        declaration = call.get('callee') or {}
        if (declaration.get('kind') != 'FUNCTION_DECL'
                or declaration.get('source_span', {}).get('source') not in {
                    str(source_dir / 'runtime.h'), str(source_dir / 'argcheck.c')}):
            return None
        if helper in ('mp_raise_msg', 'mp_raise_msg_varg') and (not error_arguments or error_arguments[0] != '& mp_type_TypeError'):
            return None
        errors.append(call['source_span'])
        error_calls.append(call)
    payloads = []
    for error in error_calls:
        helper, _ = call_parts(error['expression'])
        message_index = 1 if helper in ('mp_raise_msg', 'mp_raise_msg_varg') else 0
        message_arguments = error.get('expression_node', {}).get('arguments', {}).get('children', [])
        if message_index >= len(message_arguments):
            continue
        message = message_arguments[message_index]
        while message.get('kind') in ('cast_expression', 'parenthesized_expression'):
            message = (message.get('value') if message['kind'] == 'cast_expression'
                       else (message.get('children') or [None])[0])
            if not isinstance(message, dict):
                break
        if not isinstance(message, dict) or message.get('kind') != 'call_expression':
            continue
        candidates = [call for call in events['calls']
                      if call['byte_start'] == message['byte_start'] and call['byte_end'] == message['byte_end']]
        if len(candidates) != 1:
            return None
        payload = candidates[0]
        declaration = payload.get('callee') or {}
        operands = message.get('arguments', {}).get('children', [])
        if (declaration.get('kind') != 'FUNCTION_DECL' or declaration.get('name') != 'MP_COMPRESSED_ROM_TEXT'
                or declaration.get('source_span', {}).get('source') != str(source_dir / 'misc.h')
                or guard_key(payload.get('guards', [])) != guard_key(error.get('guards', []))
                or not error['byte_start'] < payload['byte_start'] < payload['byte_end'] < error['byte_end']
                or len(operands) != 1 or operands[0].get('kind') != 'string_literal'):
            return None
        payloads.append(payload)
    known_calls = error_names | {'mp_map_lookup', 'mp_obj_is_true', 'mp_obj_get_int',
                                 '__assert_fail', '__assert_func', 'mp_assert_fail'}
    if any(call_parts(call['expression'])[0] not in known_calls and not any(call is payload for payload in payloads)
           for call in events['calls']):
        return None
    return {'resolution': 'bounded-resolved', 'kind': 'selected-indexed-parser-protocol',
            'parser_identity': function['id'], 'source': function['source_span'],
            'loop': {key: loop[key] for key in ('byte_start', 'initializer_expression', 'condition_expression', 'update_expression')},
            'lookup': {'source': lookup['source_span'], 'callee': callee, 'arguments': arguments,
                       'qstr_template': qstr_template, 'lookup_mode': lookup_mode},
            'supplied_writes': [given_pos['source_span'], given_kw['source_span']],
            'default_write': default['source_span'], 'default_continue': continues[0]['byte_start'],
            'required_and_excess_errors': errors,
            'terminal_literal_message_payloads': [payload['source_span'] for payload in payloads]}


def _parser_conversions(facts, function, linked_functions=None, protocol=None):
    """Require conversion operations in the selected parser, not flag names."""
    params = function['parameters']
    if len(params) != 6 or function['events'].get('semantic_unassessed'):
        return None
    allowed, out = params[4], params[5]
    writes = function['events']['writes']
    supplied = [variable for variable in function['events']['variables'] if variable['name'] == 'given_arg']
    if len(supplied) != 1 or supplied[0].get('type_expression') != 'mp_obj_t':
        return None
    supplied_writes = {write['expression'] for write in writes if write['target'] == 'given_arg'}
    if supplied_writes != {'given_arg = ' + params[1] + ' [ i ]', 'given_arg = kw -> value'}:
        return None
    destination_writes = {out + ' [ i ] = ' + allowed + ' [ i ] . defval',
                          out + ' [ i ] . u_bool = mp_obj_is_true ( given_arg )',
                          out + ' [ i ] . u_int = mp_obj_get_int ( given_arg )',
                          out + ' [ i ] . u_obj = given_arg'}
    if any(re.search(r'\b' + re.escape(out) + r'\b', write['target'])
           and write['expression'] not in destination_writes for write in writes):
        return None
    for call in function['events']['calls']:
        _, arguments = call_parts(call['expression'])
        for argument in arguments:
            argument = strip_outer(argument)
            if re.match(r'(?:& )?(?:' + re.escape(out) + '|' + re.escape(allowed) + r')\b', argument):
                if argument not in (allowed + ' [ i ] . qst', allowed + ' [ i ] . flags'):
                    return None
    conversions = {}
    functions = {}
    for definition in facts['functions']:
        functions.setdefault(definition['name'], []).append(definition)
    for kind, field, helper in (('bool', 'u_bool', 'mp_obj_is_true'),
                                ('int', 'u_int', 'mp_obj_get_int')):
        expression = out + ' [ i ] . ' + field + ' = ' + helper + ' ( given_arg )'
        matches = [write for write in writes if write['expression'] == expression
                   and any(guard['branch'] == 'then' and
                           strip_outer(guard['condition']) == '( ' + allowed + ' [ i ] . flags & MP_ARG_KIND_MASK ) == MP_ARG_' + kind.upper()
                           for guard in write.get('guards', []))]
        calls = [call for call in function['events']['calls']
                 if call['expression'] == helper + ' ( given_arg )'
                 and (call.get('callee') or {}).get('kind') == 'FUNCTION_DECL'
                 and call['callee']['source_span']['source'] in {
                     str(Path(function['source_span']['source']).parent / 'runtime.h'),
                     str(Path(function['source_span']['source']).parent / 'obj.h')}]
        if len(matches) != 1 or len(calls) != 1:
            return None
        helper_authority = input_helper_authority(
            calls[0], function, functions,
            facts.get('contracts', {}), linked_functions)
        conversions[kind] = {'kind': 'object' if kind == 'bool' else 'integer-conversion-capability',
                             'resolution': 'bounded-resolved', 'helper': helper,
                             'parser_identity': function['id'], 'write': matches[0],
                             'callee': calls[0]['callee'], 'helper_contract_authority': helper_authority}
    carriers = [write for write in writes if write['expression'] == out + ' [ i ] . u_obj = given_arg']
    if len(carriers) != 1:
        return None
    conversions['obj'] = {'kind': 'object-carrier', 'resolution': 'bounded-resolved',
                          'parser_identity': function['id'], 'write': carriers[0],
                          'helper_contract_authority': {
                              'resolution': 'bounded-resolved' if protocol else 'unresolved',
                              'kind': 'object', 'parser_protocol': protocol,
                              'source': carriers[0]['source_span']}}
    return conversions


def _parameters(shape, semantic, facts, parser_owner=None, linked_functions=None):
    """Compose a complete parser table with selected count/vector relations."""
    rows = semantic.get('argument_tables', []) if semantic else []
    parsers = semantic.get('parser_calls', []) if semantic else []
    if not rows:
        return

    def unresolved(reason):
        shape['parameters_resolution'] = 'unresolved'
        shape.setdefault('unresolved', []).append(reason)

    if len({row['table_id'] for row in rows}) != 1 or len(parsers) != 1:
        unresolved('argument table/parser identity is ambiguous')
        return
    parser = parsers[0]
    args = parser['arguments']
    function = next((fn for fn in facts['functions'] if fn['id'] == parser['scope']), None)
    if not function:
        unresolved('forwarded parser count/vector/default mapping unassessed')
        return
    public_function = next((fn for fn in facts['functions'] if fn['id'] == semantic['function_id']), None)
    forwarding = _parser_forwarding(parser, function, public_function, shape) if public_function else None
    if not forwarding or args[4] != rows[0]['table_id'].rsplit('::', 1)[-1]:
        unresolved('parser count/vector/keyword/table relation is not qualified equal-offset forwarding')
        return
    env = forwarding['environment']
    if not parser_owner:
        unresolved('selected allocated argument parser body is unavailable')
        return
    parser_facts, parser_function = parser_owner
    parser_params = parser_function['parameters']
    if parser_function['events'].get('semantic_unassessed'):
        unresolved('selected parser body contains a syntax-only semantic projection')
        return
    if len(parser_params) != 6:
        unresolved('argument parser parameter family is unsupported')
        return
    protocol = _parser_protocol(parser_facts, parser_function)
    if not protocol:
        unresolved('selected parser complete extent/lookup/default/required/excess protocol is unsupported')
        return
    conversions = _parser_conversions(parser_facts, parser_function, linked_functions, protocol)
    if not conversions or parser.get('callee') != parser_function['declaration_identity']:
        unresolved('selected parser conversion operations or caller declaration identity are unqualified')
        return
    storage = []
    for owner in (facts, parser_facts):
        interfaces = owner.get('frontend', {}).get('aggregate_interfaces', [])
        fields = [scalar_field(interfaces, 'mp_arg_t', name, c_type)
                  for name, c_type in (('qst', 'uint16_t'), ('flags', 'uint16_t'), ('defval', 'mp_arg_val_t'))]
        if not all(fields):
            unresolved('selected caller/parser mp_arg_t scalar field layout is unqualified')
            return
        storage.append(fields)
    helper_env = {**parser_facts.get('frontend', {}).get('contract_constants', {}), **parser_function['constants']}
    flags_values = {name: constant(parser_facts.get('contracts', {}).get('arg_' + name, ''), helper_env)
                    for name in ('required', 'kw_only', 'kind_mask', 'bool', 'int', 'obj')}
    if any(value is None or not 0 <= value <= 65535 for value in flags_values.values()):
        unresolved('actual selected parser flag values are unresolved/outside the qualified flag field')
        return
    required_flag, keyword_flag = flags_values['required'], flags_values['kw_only']
    shape['parameters_authority'] = {'resolution': 'bounded-resolved',
                                     'parser_identity': parser_function['id'], 'source': parser_function['source_span'],
                                     'flags': flags_values, 'scalar_storage': storage,
                                     'forwarding': forwarding, 'conversions': conversions, 'protocol': protocol}
    counts = {row.get('row_count') for row in rows}
    if len(counts) != 1:
        unresolved('argument table complete row count is conflicting')
        return
    row_count = next(iter(counts))
    rows = sorted(rows, key=lambda row: row['table_entry_index'])
    if (not isinstance(row_count, int) or row_count < 0
            or any(row.get('table_count_resolution') != 'resolved' for row in rows)
            or [row['table_entry_index'] for row in rows] != list(range(row_count))):
        unresolved('argument table rows do not cover the complete declared array by original index')
        return
    table_name = args[4]
    sizeof_pattern = (r'sizeof \( ' + re.escape(table_name)
                      + r' \) / sizeof \( \( ' + re.escape(table_name) + r' \) \[ 0 \] \)')
    count_expr = re.sub(sizeof_pattern, str(row_count), args[3])
    if lexical_constant(function, count_expr,
                        parser['argument_nodes'][3], forwarding['specialization']) != row_count:
        unresolved('parser table count is not the complete selected table')
        return
    shift = forwarding['shift']
    offset = shape.get('implicit_argument_offset', 0)
    parameters = []
    if type(offset) is not int or offset < 0 or offset > shape['minimum']:
        unresolved('implicit receiver offset is not guaranteed by the wrapper minimum')
        return
    for index in range(shift):
        parameters.append({'name': 'self' if index < offset else 'arg' + str(index - offset),
                           'kind': 'positional_only', 'required': True, 'default_contract': None,
                           'source_refs': [{'kind': 'vector-item', 'scope': public_function['id'],
                                            'vector_parameter': forwarding['public_family']['vector'], 'index': index}],
                           'source': public_function['source_span'],
                           'binding_authority': shape['parameters_authority']})
    positional_prefix = row_count
    for row in rows:
        if row.get('row_resolution') != 'resolved' or not row.get('name'):
            unresolved('argument table row fields unresolved at original index ' + str(row['table_entry_index']))
            return
        flags = constant(row['flags'], env)
        if flags is None:
            unresolved('argument flags expression unresolved')
            return
        flags &= 65535
        qstr_identifier = row.get('fields', {}).get('qst', {}).get('expression', '')
        qstr_value = constant(qstr_identifier, env)
        if qstr_value is None or not 0 <= qstr_value <= 65535:
            unresolved('argument name qstr ordinal is outside qualified scalar storage')
            return
        index = row['table_entry_index']
        if flags & keyword_flag:
            positional_prefix = min(positional_prefix, index)
        required = bool(flags & required_flag)
        default = dict(row['default_storage_contract'])
        if default.get('kind') == 'bool-storage':
            default['kind'] = 'bool'
        elif default.get('kind') == 'integer-storage-literal':
            default.update(kind='int', value=default['literal_value'])
        parsed_kind = flags & flags_values['kind_mask']
        expected_storage = ({'u_bool'} if parsed_kind == flags_values['bool'] else
                            {'u_int'} if parsed_kind == flags_values['int'] else {'u_obj', 'u_rom_obj'})
        if not required and row.get('storage_field') not in expected_storage:
            default = {'kind': 'unresolved', 'reason': 'default union member is not the selected parser flag interpretation',
                       'storage_contract': default}
        # Aggregate zero initialization is storage evidence, not a Python literal.
        if not required and row.get('default_omitted'):
            default = {'kind': 'unresolved', 'reason': 'omitted zero-initialized storage has no qualified Python default interpretation',
                       'storage_contract': default}
        conversion_key = ('bool' if parsed_kind == flags_values['bool'] else
                          'int' if parsed_kind == flags_values['int'] else
                          'obj' if parsed_kind == flags_values['obj'] else None)
        if conversion_key is None:
            unresolved('selected parser kind has no qualified conversion operation')
            return
        field = {'bool': 'u_bool', 'int': 'u_int', 'obj': 'u_obj'}[conversion_key]
        refs = [{'kind': 'parser-field', 'scope': function['id'], 'table_id': row['table_id'],
                 'table_entry_index': index, 'storage_field': field}]
        if index < positional_prefix:
            refs.append({'kind': 'vector-item', 'scope': public_function['id'],
                         'vector_parameter': forwarding['public_family']['vector'], 'index': shift + index})
        parameters.append({'name': row['name'],
                           'kind': 'positional_or_keyword' if index < positional_prefix else 'keyword_only',
                           'table_entry_index': index, 'required': required,
                           'default_contract': None if required else default,
                           'source_refs': refs, 'parser_conversion': conversions[conversion_key],
                           'result_storage': {'kind': {'bool': 'bool', 'int': 'int', 'obj': 'object-carrier'}[conversion_key],
                                              'authority': conversions[conversion_key]},
                           'binding_authority': shape['parameters_authority'],
                           'source': row['source_span'],
                           'resolution': _resolution('resolved', authority='selected complete mp_arg_parse_all table and conversion body')})
    minimum, maximum = shape.get('minimum'), shape.get('maximum')
    if (not isinstance(minimum, int) or minimum < shift or minimum > shift + positional_prefix
            or not isinstance(maximum, int) or maximum < minimum):
        unresolved('wrapper positional bounds cannot be composed with the parser table prefix')
        return
    for parameter in parameters:
        index = shift + parameter['table_entry_index'] if 'table_entry_index' in parameter else parameters.index(parameter)
        if index < minimum:
            parameter.update(kind='positional_only', required=True, default_contract=None)
        elif index >= maximum:
            parameter['kind'] = 'keyword_only'
    offset = shape.get('implicit_argument_offset', 0)
    if not isinstance(offset, int) or offset < 0 or offset > min(shift + positional_prefix, minimum):
        unresolved('implicit receiver is not guaranteed in the parser positional prefix')
        return
    conversions['obj']['consumer_authority'] = {
        'resolution': 'bounded-resolved',
        'parser_scope': parser['scope'],
        'public_scope': public_function['id'],
        'vector_parameter': forwarding['family']['vector'],
        'operand': 1,
        'callee': parser['callee'],
        'source_span': parser['source_span'],
        'byte_start': parser.get('byte_start'),
        'byte_end': parser.get('byte_end'),
        'guards': parser.get('guards', []),
        'helper_contract_authority': {
            'resolution': protocol['resolution'],
            'kind': protocol['kind'],
            'parser_identity': protocol['parser_identity'],
            'source': protocol['source']}}
    shape['parser_parameters'] = parameters
    shape['parameters'] = [dict(parameter) for parameter in parameters[offset:]]
    shape['parameters_resolution'] = 'resolved'


def _public_contracts(shape, semantic, function, identity, receiver=None):
    """Supply source references only after wrapper and C declarations agree."""
    minimum, maximum = shape.get('minimum'), shape.get('maximum')
    offset = shape.get('implicit_argument_offset')
    authority = {'resolution': 'bounded-resolved', 'wrapper_identity': identity.get('object_id'),
                 'wrapper_source': identity.get('object_source_span'),
                 'function_identity': function['id'],
                 'parameter_source_contract': function.get('parameter_source_contract'),
                 'minimum': minimum, 'maximum': maximum, 'keywords': shape.get('keywords'),
                 'implicit_argument_offset': offset, 'ownership': shape.get('function_ownership')}
    complete = shape.get('parser_parameters')
    if not isinstance(complete, list) or shape.get('parameters_resolution') != 'resolved':
        complete = []
        valid_bounds = (type(minimum) is int and type(maximum) is int
                        and 0 <= minimum <= maximum <= 64 and type(offset) is int
                        and 0 <= offset <= minimum and not shape.get('unresolved'))
        family = _parameter_family(function)
        fixed = (valid_bounds and shape.get('keywords') is False and minimum == maximum
                 and len(function['parameters']) == maximum
                 and all((entry := _parameter_declaration(function, index)) is not None
                         and re.fullmatch(r'mp_obj_t \w+', entry.get('declaration_expression', entry.get('expression', '')))
                         for index in range(maximum)))
        if valid_bounds and (fixed or family and (shape.get('keywords') is False or family['keywords'] is not None)):
            for index in range(maximum if shape.get('keywords') is False else minimum):
                reference = ({'kind': 'c-parameter', 'scope': function['id'], 'parameter': index}
                             if fixed else {'kind': 'vector-item', 'scope': function['id'],
                                            'vector_parameter': family['vector'], 'index': index})
                complete.append({'name': 'self' if index < offset else 'arg' + str(index - offset),
                                 'kind': 'positional_only', 'required': index < minimum,
                                 'default_contract': None, 'source_refs': [reference],
                                 'source': function['source_span'], 'binding_authority': authority})
            shape['parser_parameters'] = complete
            if shape.get('keywords') is False:
                shape['parameters'] = [dict(parameter) for parameter in complete[offset:]]
                shape['parameters_resolution'] = 'resolved'
                shape['parameters_authority'] = authority
    if not complete or type(offset) is not int or not 0 <= offset <= len(complete):
        return bind_public_contracts(semantic, {'function_id': function['id'], 'parameters': [], 'receiver': None})
    public = []
    for parameter in complete[offset:]:
        public.append({**parameter, 'role': 'public', 'binding_authority': parameter.get('binding_authority', authority)})
    receiver_binding = None
    if offset == 1 and receiver:
        receiver_binding = {**complete[0], **receiver, 'role': 'receiver',
                            'binding_authority': complete[0].get('binding_authority', authority)}
    mapped = bind_public_contracts(semantic, {'function_id': function['id'],
                                            'parameters': public, 'receiver': receiver_binding})
    public_parameters = mapped.get('public_parameters', [])
    for index, parameter in enumerate(shape.get('parameters', [])):
        evidence = public_parameters[index] if index < len(public_parameters) else {}
        if evidence.get('name') != parameter['name']:
            evidence = {}
        domain = evidence.get('accepted_domain', {})
        parameter['accepted_domain'] = domain
        parameter['constraints'] = evidence.get('constraints', [])
        if evidence.get('result_storage'):
            parameter['result_storage'] = evidence['result_storage']
        parameter['type_contract'] = domain.get('contract')
        parameter['type_resolution'] = domain.get('resolution', 'unresolved')
        if (domain.get('contract') or {}).get('kind') in (
                'integer-conversion-capability', 'readable-buffer-capability', 'writable-buffer-capability',
                'tuple-or-list/exact-length'):
            parameter['representation_resolution'] = 'unresolved'
            parameter['representation_unresolved'] = ['target native capability has no qualified declaration representation']
    return mapped


def _receiver_offset(shape, offset):
    """Keep qualified public parameters aligned with their complete parser table."""
    shape['implicit_argument_offset'] = offset
    if shape.get('parameters_resolution') != 'resolved':
        return
    complete = shape.get('parser_parameters')
    if not isinstance(complete, list) or not isinstance(offset, int) or not 0 <= offset <= len(complete):
        shape.pop('parameters', None)
        shape['parameters_resolution'] = 'unresolved'
        shape.setdefault('unresolved', []).append('complete parser table is unavailable for receiver rebinding')
        return
    shape['parameters'] = [dict(parameter) for parameter in complete[offset:]]


def _handler_dispatch_authority(framework):
    """Qualify the bounded NULL-initialized native attr-slot dispatch protocol."""
    unresolved = {'status': 'unresolved', 'reason': 'selected native NULL/sentinel/getter dispatch protocol is unqualified'}
    if not framework:
        return unresolved
    facts, function = framework
    contracts, events = facts.get('contracts', {}), function['events']
    null, sentinel = contracts.get('null'), contracts.get('sentinel')
    if (not null or not sentinel or null == sentinel or function['parameters'] != ['obj', 'attr', 'dest']
            or events.get('semantic_unassessed') or events.get('control_flow_unresolved')):
        return unresolved
    callbacks = [call for call in events['calls']
                 if re.search(r'\bmp_attr_fun_t\b', call['expression'])
                 and re.search(r'\bslot_index_attr\b', call['expression'])
                 and re.search(r'\bslots\b', call['expression'])
                 and call['expression'].endswith('( obj , attr , dest )')]
    if len(callbacks) != 1:
        return unresolved
    callback = callbacks[0]
    def before(left, right):
        return left['source'] == right['source'] and left['end_line'] < right['start_line']
    initialized = [
        write for write in events['writes'] if write['expression'] in
        ('dest [ 0 ] = ' + null, 'dest [ 1 ] = ' + null)
        and not write['guards'] and before(write['source_span'], callback['source_span'])]
    if len(initialized) != 2 or {write['target'] for write in initialized} != {'dest [ 0 ]', 'dest [ 1 ]'}:
        return unresolved
    if any(write['target'] in function['parameters'] for write in events['writes']):
        return unresolved
    if any(call is not callback and re.search(r'\bdest\b', call['expression'])
           and before(call['source_span'], callback['source_span']) for call in events['calls']):
        return unresolved
    condition = 'dest [ 1 ] != ' + sentinel
    stopped = [returned for returned in events['returns'] if not returned['expression']
               and any(guard['condition'] == condition and guard['branch'] == 'then'
                       for guard in returned['guards'])]
    reset = [write for write in events['writes'] if write['expression'] == 'dest [ 1 ] = ' + null
             and before(callback['source_span'], write['source_span'])]
    if not stopped or not reset:
        return unresolved
    return {'status': 'bounded-resolved', 'authority': function['id'], 'callback': callback,
            'initialization': initialized, 'sentinel_returns': stopped, 'sentinel_resets': reset,
            'null': null, 'sentinel': sentinel,
            'scope': 'supported native slot-dispatch body family, not arbitrary C dataflow'}


def _handler_write_contract(write, function, contracts, dispatch, resolve, present, translation_unit):
    """Keep load candidates distinct from success indicators and method bindings."""
    null, sentinel = contracts.get('null'), contracts.get('sentinel')
    if null and write['expression'] == null:
        return {'indicator': 'NULL/no-value-or-store-success', 'source': write['source_span']}
    reasons = []
    if not null or not sentinel or null == sentinel:
        reasons.append('selected handler NULL/sentinel object contracts are unqualified')
    getter = bool(null and any(
        (guard['condition'] == 'dest [ 0 ] == ' + null and guard['branch'] == 'then')
        or (guard['condition'] == 'dest [ 0 ] != ' + null and guard['branch'] == 'else')
        for guard in write['guards']))
    if not getter:
        reasons.append('handler write is not proven to be in the NULL-input getter branch')
    getter_guards = [
        guard for guard in write['guards']
        if ((null and guard['condition'] == 'dest [ 0 ] == ' + null and guard['branch'] == 'then')
            or (null and guard['condition'] == 'dest [ 0 ] != ' + null and guard['branch'] == 'else')
            or re.fullmatch(r'attr == MP_QSTR_\w+', guard['condition']))]
    applicability = event_applicability(function, write, assumed_guards=getter_guards)
    if applicability['resolution'] != 'bounded-resolved':
        reasons.extend(applicability['unresolved'])
    if dispatch['status'] != 'bounded-resolved':
        reasons.append(dispatch['reason'])
    def compatible(left, right):
        left_guards = {(guard['condition'], json.dumps(guard['source_span'], sort_keys=True)): guard['branch']
                       for guard in left['guards']}
        return all(left_guards.get((guard['condition'], json.dumps(guard['source_span'], sort_keys=True)),
                                   guard['branch']) == guard['branch'] for guard in right['guards'])
    receivers = [other for other in function['semantics']['destination_writes']
                 if other['destination_index'] == 1 and compatible(write, other)
                 and (not null or other['expression'] != null)]
    if receivers:
        reasons.append('handler destination[1] binding/fallback protocol does not establish an ordinary value or callable signature')
    escaped = [call for call in function['events']['calls']
               if re.search(r'\bdest\b', call['expression']) and compatible(write, call)]
    if escaped:
        reasons.append('handler destination escapes to a call with unassessed value/binding effects')
    receiver_presence = all(other['expression'] != sentinel and direct_parameter(function, other['expression']) == 0
                            for other in receivers)
    if not receiver_presence:
        reasons.append('handler destination[1] may request fallback or supply an unqualified receiver')
    if function['events'].get('control_flow_unresolved'):
        reasons.append('handler result selection has unassessed control flow')
    value = write['value_contract']
    value_resolved = value.get('kind') not in ('unresolved', 'void-C-return', 'argument', 'argument-table')
    if not value_resolved:
        reasons.append(value.get('reason', 'handler result value/dataflow contract is unassessed'))
    reference = constant_value(write['expression'], {
        name: contracts[name] for name in ('obj_reference', 'rom_reference') if name in contracts})
    target, ownership = resolve(reference.get('target'), translation_unit) if reference.get('kind') == 'object-reference' else (None, 'non-NULL result unassessed')
    nonnull = bool(target and present(*target) and target[1].get('initialization_status') == 'initialized')
    if not nonnull:
        reasons.append('handler result is not a qualified non-NULL allocated object reference')
    if target and target[1]['kind'] == 'wrapper':
        reasons.append('handler callable object result has no qualified public binding/callshape')
    availability = {'status': 'bounded-resolved' if getter and nonnull and dispatch['status'] == 'bounded-resolved'
                    and null and sentinel and null != sentinel and receiver_presence and not escaped
                    and applicability['resolution'] == 'bounded-resolved'
                    and not function['events'].get('control_flow_unresolved') else 'unresolved',
                    'authority': dispatch, 'object_ownership': ownership,
                    'result_source': write['source_span'], 'successful_path_applicability': applicability}
    return {'resolved': not reasons, 'reasons': reasons, 'availability': availability,
            'binding': {'kind': 'handler-method-or-fallback' if receivers else 'ordinary-value-candidate',
                        'destination_one_writes': receivers,
                        'destination_escape_calls': escaped},
            'value_contract': value, 'getter': getter}


def _linked_functions(inputs, present):
    """Join exact declarations to allocated bodies and their direct static helpers."""
    selected, anchors = [], {}
    for facts in inputs:
        functions = facts['functions']
        statics = {}
        for function in functions:
            if function['linkage'] == 'static' and not function['events'].get('semantic_unassessed'):
                statics.setdefault(function['name'], []).append(function)
        roots = [function for function in functions
                 if present(facts, {'name': function['name']})
                 and not function['events'].get('semantic_unassessed')]
        pending = [(function, {'allocated_function': function['id'], 'source': function['source_span'],
                               'direct_static_call_path': []}) for function in roots]
        while pending:
            function, anchor = pending.pop()
            if function['id'] in anchors:
                continue
            anchors[function['id']] = anchor
            selected.append((facts, function))
            for call in function['events']['calls']:
                declaration = call.get('callee') or {}
                candidates = [child for child in statics.get(declaration.get('name'), [])
                              if child['declaration_identity'] == declaration]
                if len(candidates) == 1:
                    pending.append((candidates[0], {
                        **anchor, 'direct_static_call_path': [*anchor['direct_static_call_path'], call['source_span']]}))
    locals_by_name, externals_by_name = {}, {}
    for facts, function in selected:
        locals_by_name.setdefault((facts['translation_unit'], function['name']), []).append((facts, function))
        if function['linkage'] == 'external':
            externals_by_name.setdefault(function['name'], []).append((facts, function))
    linked, seen = {}, set()
    for caller_facts in inputs:
        for caller in caller_facts['functions']:
            for call in caller['events']['calls']:
                declaration = call.get('callee') or {}
                if declaration.get('kind') != 'FUNCTION_DECL':
                    continue
                name = declaration.get('name')
                local = locals_by_name.get((caller_facts['translation_unit'], name), [])
                candidates = local if local else externals_by_name.get(name, [])
                if len(candidates) != 1:
                    continue
                facts, function = candidates[0]
                if declaration != function['declaration_identity']:
                    continue
                key = (function['id'], json.dumps(declaration, sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                linked.setdefault(name, []).append({
                    'function': function,
                    'declaration_identity': declaration,
                    'authority': {'resolution': 'bounded-resolved',
                                  'function_identity': function['id'],
                                  'translation_unit': facts['translation_unit'],
                                  'object': facts.get('input', {}).get('object'),
                                  'source': function['source_span'],
                                  'ownership': 'selected allocated body or exact declaration-bound direct static helper',
                                  'allocated_caller_authority': anchors[function['id']],
                                  'contracts': facts.get('contracts', {})}})
    return linked


def _header_helper_authority(facts, function, caller, linked_functions, present, *, terminal_literal=False):
    """Keep source-inline exceptions distinct from allocated/static-call bodies."""
    caller_function = caller['function']
    if (caller['authority'].get('translation_unit') != facts['translation_unit']
            or not any(call.get('callee') == function['declaration_identity']
                       for call in caller_function['events']['calls'])):
        return None
    authority = {'resolution': 'bounded-resolved', 'function_identity': function['id'],
                 'translation_unit': facts['translation_unit'], 'source': function['source_span'],
                 'header_declaration_identity': function['declaration_identity'],
                 'selected_caller_identity': caller_function['id'], 'contracts': facts.get('contracts', {})}
    if present(facts, {'name': function['name']}):
        return {**authority, 'selection': 'selected allocated header helper body'}
    contract = function.get('parameter_source_contract', {})
    modifiers = contract.get('declaration_modifiers', [])
    inline = (contract.get('declaration_modifiers_resolution') == 'resolved'
              and any(modifier in modifiers for modifier in ('inline', '__inline__', '__inline')))
    if inline and function['linkage'] == 'static' and 'static' in modifiers and contract.get('signature_resolution') == 'resolved':
        return {**authority, 'selection': 'source-qualified static inline body in the exact caller translation unit',
                'inline_declaration': {'modifiers': modifiers, 'source': function['source_span']}}
    if terminal_literal and inline and function['linkage'] == 'external':
        prefix = contract.get('declaration_prefix_expression', '')
        if (re.fullmatch(r'inline __attribute__ \( \( always_inline \) \) const char', prefix)
                and facts.get('input', {}).get('compiler_expanded_sha256')
                and function['source_span']['source'] in facts.get('input', {}).get('dependencies', {})):
            return {**authority, 'selection': 'source-forced inline literal terminal-message body in the exact compiled caller translation unit',
                    'domain_scope': 'literal terminal-error payload only; no normal return or input domain authority',
                    'inline_declaration': {'modifiers': modifiers, 'prefix': prefix,
                                           'source': function['source_span']}}
    if function['linkage'] == 'static':
        rows = [row for row in linked_functions.get(function['name'], [])
                if row['function']['id'] == function['id']
                and row['authority']['translation_unit'] == facts['translation_unit']
                and row.get('declaration_identity') == function['declaration_identity']]
        if len(rows) == 1:
            return {**rows[0]['authority'], **authority,
                    'selection': 'exact declaration-bound direct static caller path'}
    return None


def _checked_wrapper_type_authority(native_definition, receiver_definitions, native_contracts=None):
    """Match both selected lookup masks against the actual wrapper type flags."""
    probes = {'MP_TYPE_FLAG_BINDS_SELF': 'binds_self_flag', 'MP_TYPE_FLAG_BUILTIN_FUN': 'builtin_fun_flag'}
    native_contracts = native_contracts or {}
    lookup_row = receiver_definitions.get('mp_convert_member_lookup', {})
    lookup = lookup_row.get('function', {})
    runtime_contracts = lookup_row.get('authority', {}).get('contracts', {})
    masks = {name: constant(native_contracts[probe], native_definition.get('constant_environment', {})) if probe in native_contracts
             else constant(name, native_definition.get('constant_environment', {}))
             for name, probe in probes.items()}
    runtime_masks = {name: constant(runtime_contracts[probe], lookup.get('constants', {})) if probe in runtime_contracts
                     else constant(name, lookup.get('constants', {})) for name, probe in probes.items()}
    flags = native_definition.get('type_flags_value')
    if (all(type(mask) is int and mask > 0 and runtime_masks[name] == mask
            and type(flags) is int and flags & mask for name, mask in masks.items())
            and native_definition.get('type_flags_storage')):
        return {'identity': native_definition['id'], 'source': native_definition['source_span'],
                'flags': flags, 'masks': masks, 'lookup_masks': runtime_masks,
                'scalar_storage': native_definition['type_flags_storage']}
    return None


def _call_slot_binding(facts, definition, slot):
    """Resolve a call-slot reference through its actual vector and typedef ABI."""
    source_dir = Path(definition['source_span']['source']).parent
    interfaces = facts.get('frontend', {}).get('aggregate_interfaces', [])
    names = re.findall(r'\bmp_obj_(?:full_|empty_)?type_t\b', definition.get('declaration_specifiers', ''))
    selected = [interface for interface in interfaces
                if len(names) == 1 and interface['name'] == names[0] and interface['status'] == 'resolved']
    if len(selected) != 1 or not slot.get('storage_authority'):
        return None
    fields = [field for field in selected[0]['fields'] if field['name'] == 'slots']
    if (len(fields) != 1 or fields[0]['type_expression'] != 'void'
            or not re.fullmatch(r'\* slots \[ (?:\d+ )?\]', fields[0]['declarator_expression'])
            or fields[0]['source_span']['source'] != str(source_dir / 'obj.h')):
        return None
    expression = strip_outer(slot.get('expression') or '')
    if expression.startswith('( const void * ) '):
        expression = strip_outer(expression[len('( const void * ) '):])
    typed = re.fullmatch(r'\( ([A-Za-z_]\w*) \) (.*)', expression)
    abi = None
    if typed:
        candidates = [interface for interface in facts.get('frontend', {}).get('function_pointer_interfaces', [])
                      if interface['name'] == typed[1] and interface['status'] == 'resolved'
                      and interface['source_span']['source'] == str(source_dir / 'obj.h')]
        if len(candidates) != 1:
            return None
        abi = candidates[0]
        expression = strip_outer(typed[2])
    if not re.fullmatch(r'[A-Za-z_]\w*', expression):
        return None
    return {'function': expression, 'vector_storage': fields[0], 'typedef_abi': abi}


def _call_slot_abi_matches(function, binding):
    abi = binding['typedef_abi']
    if abi is None:
        return False
    contract = function.get('parameter_source_contract', {})
    declarations = contract.get('declarations', [])
    parameters = abi['parameters']
    if (contract.get('declaration_binding_resolution') != 'resolved'
            or contract.get('prototype_resolution') != 'resolved'
            or contract.get('variadic') is not False
            or contract.get('signature_resolution') != 'resolved'
            or contract.get('return_type_resolution') != 'resolved'
            or contract.get('return_type_expression') != abi['return_type_expression']
            or len(declarations) != len(parameters)):
        return False
    def declaration_type(parameter):
        expression = parameter.get('declaration_expression', '')
        name = parameter.get('name')
        return re.sub(r'\b' + re.escape(name) + r'$', '', expression).strip() if name else expression
    return all(declaration_type(actual) == declaration_type(expected)
               for actual, expected in zip(declarations, parameters))


def _native_receiver_contract(owner, shape, wrapper_definition, receiver_check, receiver_definitions, root, wrapper_contracts=None):
    """Keep native receiver source qualification independent of return contracts."""
    if not owner or owner['kind'] != 'type':
        return None
    contract = {'native_type_identity': owner['id'],
                'nominal_authority_resolution': 'unresolved',
                'authority': {'resolution': 'bounded-resolved', 'type_identity': owner['id'],
                              'source': owner['source_span'],
                              'selection': 'selected allocated native type containing this method occurrence',
                              'domain_scope': 'bound occurrence; complete unbound acceptance unqualified'}}
    native = {'mp_type_str': 'str', 'mp_type_bytes': 'bytes', 'mp_type_bytearray': 'bytearray'}
    if (owner['name'] not in native or owner['source_span']['source'] not in {
            str(root / path) for path in ('py/objstr.c', 'py/objstrunicode.c', 'py/objarray.c')}):
        return contract
    witness = (_checked_wrapper_type_authority(wrapper_definition, receiver_definitions, wrapper_contracts)
               if wrapper_definition and wrapper_definition['kind'] == 'type' else None)
    checked = (receiver_check.get('resolution') in ('resolved', 'bounded-resolved')
               and witness is not None and shape.get('descriptor') == 'function')
    contract.update({'native_type': native[owner['name']],
                     'nominal_authority_resolution': 'bounded-resolved' if checked else 'unresolved',
                     'selected_receiver_check': receiver_check, 'wrapper_type_authority': witness})
    if checked:
        contract['authority']['domain_scope'] = 'selected checked native receiver'
    return contract


def _normal_surface(raw, inputs, resolve, present, root):
    containers = {c['id']: c for c in raw['containers']}
    facts_by_tu = {f['translation_unit']: f for f in inputs}
    bindings_by_container = {}
    for binding in raw['bindings']:
        key = (binding['identity']['container_translation_unit'], binding['identity']['container'])
        bindings_by_container.setdefault(key, []).append(binding)
    types, diagnostics = {}, []
    constructing = set()
    linked_functions = _linked_functions(inputs, present)
    semantic_cache = {}

    def selected_semantics(facts, function):
        if function['id'] not in semantic_cache:
            functions = {}
            for item in facts['functions']:
                functions.setdefault(item['name'], []).append(item)
            semantic_cache[function['id']] = {
                **semantics(function, functions, facts.get('errors', []), facts.get('contracts', {}),
                            linked_functions=linked_functions),
                'diagnostic_stage': 'selected-linked-semantics'}
        return semantic_cache[function['id']]

    def complete(steps):
        """Run a ``symbol``/``class_symbol`` generator to its return value.

        A generator yields ``(function, argument)`` to receive the return value of
        ``function(argument)``; suspended generators wait on an explicit stack."""
        stack, value = [steps], None
        while True:
            try:
                function, argument = stack[-1].send(value)
            except StopIteration as stop:
                stack.pop()
                if not stack:
                    return stop.value
                value = stop.value
            else:
                stack.append(function(argument))
                value = None

    def symbol(binding):
        identity = binding['identity']
        target_id = identity.get('object_id')
        source = identity['source_span']
        shape = deep_copy(binding.get('callshape'))
        kind = 'function' if shape and shape.get('callable') is not False else 'constant' if binding['value_contract']['kind'] not in ('object-reference', 'unresolved') else 'unknown'
        availability = deep_copy(binding['availability'])
        availability['status'] = 'resolved' if identity.get('object_kind') in ('wrapper', 'type', 'module') or binding['value_contract']['kind'] not in ('object-reference', 'unresolved') else 'unresolved'
        if any('ambiguous repeated export' in reason for reason in binding['unresolved']):
            availability['status'] = 'conflict'
        result = {'name': identity['export'], 'kind': kind, 'identity': identity, 'source': source,
                  'resolution': _resolution('resolved' if not binding['unresolved'] else 'unresolved', binding['unresolved'],
                                            availability=availability),
                  'value_contract': binding['value_contract'], 'dictionary_window': binding['dictionary_window']}
        if shape:
            facts = facts_by_tu.get(identity['translation_unit'])
            function_id = (binding.get('semantics') or {}).get('function_id')
            semantic_facts = next((f for f in inputs if any(fn['id'] == function_id for fn in f['functions'])), facts)
            function = next((fn for fn in semantic_facts['functions'] if fn['id'] == function_id), None) if semantic_facts else None
            summary = selected_semantics(semantic_facts, function) if function else binding.get('semantics')
            _parameters(shape, summary, semantic_facts,
                        selected_function('mp_arg_parse_all', source='py/argcheck.c'), linked_functions)
            owner = containers.get(identity.get('container_id'))
            if owner is None:
                owner = next((container for container in raw['containers']
                              if container['translation_unit'] == identity['container_translation_unit']
                              and container['name'] == identity['container']), None)
            wrapper_type = None
            if owner and owner['kind'] == 'type' and shape.get('native_wrapper_type'):
                wrapper_type, _ = resolve(shape['native_wrapper_type'], identity['translation_unit'])
            wrapper_definition = wrapper_type[1] if wrapper_type and present(*wrapper_type) else None
            receiver = _native_receiver_contract(owner, shape, wrapper_definition, receiver_check,
                                                 receiver_definitions, root,
                                                 wrapper_type[0].get('contracts', {}) if wrapper_definition else None)
            if receiver is not None:
                result['receiver_contract'] = receiver
            if function and summary:
                summary = _public_contracts(shape, summary, function, identity, receiver)
            result['callshape'] = shape
            result['semantics'] = summary
            shape_status = 'non-callable' if shape.get('callable') is False else 'resolved' if isinstance(shape.get('minimum'), int) and isinstance(shape.get('maximum'), int) and isinstance(shape.get('keywords'), bool) and not shape.get('unresolved') else 'unresolved'
            result['resolution']['callshape'] = _resolution(shape_status, shape.get('unresolved', []))
            result['resolution']['types'] = {'inputs': (summary or {}).get('input_resolution', 'unresolved'),
                                             'returns': (summary or {}).get('return_resolution', 'unresolved')}
        target = containers.get(target_id)
        if target and target['kind'] == 'type':
            definition = yield class_symbol, target
            result.update(kind='class', type_identity=target_id)
            if target_id not in constructing:
                result.update({key: deep_copy(definition[key]) for key in ('members', 'bases', 'declared_bases', 'base_identities', 'base_resolution', 'slots', 'attribute_policy', 'constructor')})
        elif target and target['kind'] == 'module':
            result.update(kind='module', module_identity=target_id)
        return result

    def attribute(name, provider, source, reasons=(), value=None):
        resolved = isinstance(value, dict) and value.get('kind') not in ('unresolved', 'void-C-return')
        explanation = list(reasons)
        if not resolved:
            explanation.append((value or {}).get('reason', 'attribute result/assignment contract unassessed'))
        return {'name': name, 'kind': 'constant' if resolved else 'unknown',
                'identity': {'provider': provider, 'attribute': name}, 'source': source,
                'resolution': _resolution('bounded-resolved' if resolved else 'unresolved', explanation,
                                          authority='selected attribute-handler condition and destination write'),
                'value_contract': value or {'kind': 'unresolved', 'reason': 'attribute result/assignment contract unassessed'}}

    functions_by_name = {}
    for f in inputs:
        for fn in f['functions']:
            functions_by_name.setdefault(fn['name'], []).append((f, fn))

    def selected_function(name, *, source=None, translation_unit=None):
        candidates = [(f, fn) for f, fn in functions_by_name.get(name, ())
                      if present(f, {'name': name})
                      and (source is None or fn['source_span']['source'] == str(root / source))
                      and not fn['events'].get('semantic_unassessed')]
        if translation_unit is not None:
            local = [item for item in candidates if item[0]['translation_unit'] == translation_unit]
            if len(local) == 1:
                return local[0]
            if local:
                return None
            candidates = [item for item in candidates if item[1]['linkage'] == 'external']
        return candidates[0] if len(candidates) == 1 else None

    framework = selected_function('mp_load_method_maybe', source='py/runtime.c')
    type_framework = selected_function('type_attr', source='py/objtype.c')
    module_framework = selected_function('module_attr', source='py/objmodule.c')
    handler_dispatch = _handler_dispatch_authority(framework)
    receiver_definitions = {}
    for name, source_path in (('checked_fun_call', 'py/runtime.c'),
                              ('mp_obj_new_checked_fun', 'py/runtime.c'),
                              ('mp_convert_member_lookup', 'py/runtime.c'),
                              ('mp_obj_malloc_helper', 'py/obj.c'),
                              ('mp_obj_get_type', 'py/obj.c'),
                              ('m_malloc', 'py/malloc.c'),
                              ('m_malloc_fail', 'py/runtime.c'),
                              ('gc_alloc', 'py/gc.c'),
                              ('mp_load_method_maybe', 'py/runtime.c')):
        selected = selected_function(name, source=source_path)
        if selected:
            facts, function = selected
            receiver_definitions[name] = {
                'function': function,
                'authority': {'resolution': 'bounded-resolved', 'function_identity': function['id'],
                              'translation_unit': facts['translation_unit'],
                              'object': facts.get('input', {}).get('object'),
                              'source': function['source_span'], 'contracts': facts.get('contracts', {}),
                              'header_declaration_identity': function['declaration_identity'],
                              'selection': 'selected allocated helper body'}}
        else:
            candidates = [row for row in linked_functions.get(name, [])
                          if row['function']['source_span']['source'] == str(root / source_path)]
            if len(candidates) == 1:
                receiver_definitions[name] = candidates[0]
        row = receiver_definitions.get(name)
        if row:
            row['authority']['header_declaration_identity'] = row['function'].get('declaration_identity')
    checked_call = receiver_definitions.get('checked_fun_call')
    if checked_call:
        checked_tu = checked_call['authority'].get('translation_unit')
        facts = facts_by_tu.get(checked_tu)
        candidates = [function for function in facts.get('functions', []) if
                      function['name'] == 'MP_COMPRESSED_ROM_TEXT'
                      and function['source_span']['source'] == str(root / 'py/misc.h')] if facts else []
        if len(candidates) == 1:
            function = candidates[0]
            authority = _header_helper_authority(
                facts, function, checked_call, linked_functions, present, terminal_literal=True)
            if authority:
                receiver_definitions['MP_COMPRESSED_ROM_TEXT'] = {
                    'function': function, 'authority': authority}
    if checked_call and 'mp_obj_get_type' not in receiver_definitions:
        candidates = []
        for call in checked_call['function']['events']['calls']:
            declaration = call.get('callee') or {}
            if (declaration.get('kind') != 'FUNCTION_DECL'
                    or declaration.get('source_span', {}).get('source') != str(root / 'py/obj.h')):
                continue
            selected = selected_function(declaration['name'], source='py/obj.c')
            if selected and selected[1]['declaration_identity'] == declaration:
                candidates.append(selected)
        if len(candidates) == 1:
            facts, function = candidates[0]
            receiver_definitions['mp_obj_get_type'] = {
                'function': function,
                'authority': {'resolution': 'bounded-resolved', 'function_identity': function['id'],
                              'translation_unit': facts['translation_unit'], 'source': function['source_span'],
                              'object': facts.get('input', {}).get('object'),
                              'header_declaration_identity': function['declaration_identity'],
                              'selected_caller_identity': checked_call['function']['id'],
                              'selection': 'selected allocated body connected to the checked callback header declaration',
                              'contracts': facts.get('contracts', {})}}
    type_getter = receiver_definitions.get('mp_obj_get_type')
    if type_getter:
        getter_tu = type_getter['authority'].get('translation_unit')
        facts = facts_by_tu.get(getter_tu)
        type_getter['authority']['header_declaration_identity'] = type_getter['function'].get('declaration_identity')
        for name in ('mp_obj_is_obj', 'mp_obj_is_small_int', 'mp_obj_is_qstr', 'mp_obj_is_immediate_obj'):
            candidates = [function for function in facts.get('functions', []) if
                          function['name'] == name and function['source_span']['source'] == str(root / 'py/obj.h')] if facts else []
            if len(candidates) != 1:
                continue
            function = candidates[0]
            authority = _header_helper_authority(facts, function, type_getter, linked_functions, present)
            if authority:
                receiver_definitions[name] = {
                    'function': function, 'authority': authority}
    dispatch = receiver_definitions.get('mp_convert_member_lookup')
    if dispatch:
        dispatch_tu = dispatch['authority'].get('translation_unit')
        facts = facts_by_tu.get(dispatch_tu)
        candidates = [function for function in facts.get('functions', []) if
                      function['name'] == 'mp_obj_is_obj'
                      and function['source_span']['source'] == str(root / 'py/obj.h')] if facts else []
        if len(candidates) == 1:
            function = candidates[0]
            authority = _header_helper_authority(facts, function, dispatch, linked_functions, present)
            if authority:
                receiver_definitions['dispatch_mp_obj_is_obj'] = {
                    'function': function, 'authority': authority}
    factory = receiver_definitions.get('mp_obj_new_checked_fun')
    if factory:
        factory_tu = factory['authority']['translation_unit']
        checked_type, ownership = resolve('mp_type_checked_fun', factory_tu)
        if checked_type and present(*checked_type) and checked_type[1]['kind'] == 'type':
            facts, definition = checked_type
            slot_authority = None
            slots = [slot for slot in definition.get('slots', []) if slot['slot'] == 'call']
            slot_binding = _call_slot_binding(facts, definition, slots[0]) if len(slots) == 1 else None
            slot_name = slot_binding['function'] if slot_binding else None
            if slot_name:
                selected = selected_function(slot_name, translation_unit=facts['translation_unit'])
                if selected and _call_slot_abi_matches(selected[1], slot_binding):
                    call_facts, call_function = selected
                    slot_authority = {'resolution': 'bounded-resolved', 'function_id': call_function['id'],
                                      'declaration_identity': call_function['declaration_identity'],
                                      'expression': slots[0]['expression'], 'binding': slot_binding,
                                      'object': call_facts.get('input', {}).get('object'),
                                      'source': call_function['source_span']}
            receiver_definitions['mp_type_checked_fun'] = {
                'definition': definition, 'call_slot_authority': slot_authority,
                'authority': {'resolution': 'bounded-resolved', 'identity': definition['id'],
                              'translation_unit': facts['translation_unit'],
                              'object': facts.get('input', {}).get('object'),
                              'source': definition['source_span'], 'ownership': ownership}}
    receiver_check = checked_receiver_authority(receiver_definitions)

    def compose_policy(container, members):
        policy = deep_copy(container['attribute_policy'])
        framework_attributes = []
        if framework and any(test['name'] == '__class__' for test in framework[1]['handler_attribute_tests']):
            test = next(test for test in framework[1]['handler_attribute_tests'] if test['name'] == '__class__')
            framework_attributes.append(attribute('__class__', framework[1]['id'], test['source_span'],
                                                  value={'kind': 'object-reference', 'target': 'selected runtime type'}))
        if framework and container['kind'] == 'type':
            flags = container.get('type_flags_value')
            mask = constant(framework[0].get('contracts', {}).get('iterator_mask', ''),
                            framework[0].get('frontend', {}).get('contract_constants', {}))
            next_tests = [test for test in framework[1]['handler_attribute_tests'] if test['name'] == '__next__']
            writes = [write for write in framework[1]['semantics']['destination_writes']
                      if write['destination_index'] == 0 and '& mp_builtin_next_obj' in write['expression']
                      and any('MP_QSTR___next__' in guard['condition'] and 'type -> flags &' in guard['condition']
                              for guard in write['guards'])]
            if flags is not None and mask is not None and flags & mask and next_tests and writes:
                selected_next, status = resolve('mp_builtin_next_obj', framework[0]['translation_unit'])
                if selected_next and selected_next[1]['kind'] == 'wrapper':
                    next_shape = dict(selected_next[1]['callshape'])
                    next_shape['implicit_argument_offset'] = 1
                    member = attribute('__next__', framework[1]['id'], next_tests[0]['source_span'],
                                       ['iterator result contract unassessed'])
                    member.update(kind='function', callshape=next_shape, attribute_scope='instance')
                    member['receiver_contract'] = _native_receiver_contract(
                        container, next_shape, None, receiver_check, receiver_definitions, root)
                    framework_attributes.append(member)
                else:
                    policy['unresolved'].append('generic iterator wrapper ownership ' + status)
        provider = module_framework if container['kind'] == 'module' else type_framework
        if provider:
            for test in provider[1]['handler_attribute_tests']:
                if test['name'] in ('__dict__', '__name__', '__bases__'):
                    framework_member = attribute(test['name'], provider[1]['id'], test['source_span'],
                                                 ['framework attribute result type unassessed'])
                    framework_member['attribute_scope'] = 'module' if container['kind'] == 'module' else 'class'
                    framework_attributes.append(framework_member)
        elif container['kind'] == 'module' or container['kind'] == 'type':
            policy['unresolved'].append('selected framework attribute provider ownership unassessed')
        existing = {m['name'] for m in members}
        for member in framework_attributes:
            if member['name'] not in existing:
                members.append(member)
                existing.add(member['name'])
        policy['framework_attributes'] = framework_attributes
        if not framework:
            policy['unresolved'].append('generic runtime attribute framework unassessed')
        for slot in container.get('slots', []):
            if slot['slot'] != 'attr': continue
            names = re.findall(r'\b[A-Za-z_]\w*\b', slot.get('expression') or '')
            provider = selected_function(names[-1], translation_unit=container['translation_unit']) if names else None
            if not provider:
                policy['unresolved'].append('type attribute handler owner/definition unassessed')
                continue
            for write in provider[1]['semantics']['destination_writes']:
                if write['destination_index'] != 0: continue
                qualification = _handler_write_contract(
                    write, provider[1], provider[0].get('contracts', {}), handler_dispatch,
                    resolve, present, provider[0]['translation_unit'])
                if qualification.get('indicator'):
                    continue
                for guard in write['guards']:
                    match = re.fullmatch(r'attr == MP_QSTR_(\w+)', guard['condition'])
                    if match and guard['branch'] == 'then' and match[1] not in existing:
                        member = attribute(match[1], provider[1]['id'], guard['source_span'],
                                           ['handler attribute domain/assignment contract remains bounded'],
                                           write['value_contract'] if qualification['resolved'] else None)
                        member['value_contract'] = write['value_contract']
                        member['handler_binding'] = qualification['binding']
                        member['resolution'] = _resolution(
                            'bounded-resolved' if qualification['resolved'] else 'unresolved',
                            qualification['reasons'], availability=qualification['availability'],
                            authority=provider[1]['id'])
                        members.append(member); existing.add(member['name'])
        for row in policy.get('delegation_evidence', []):
            if row['status'] != 'selected-dispatch-and-handler': continue
            for table in row['qualified_key_tables']:
                for name in table['keys']:
                    if name not in existing:
                        members.append(attribute(name, table['definition'], table['source'],
                                                 ['delegated mutable attribute result type unassessed']))
                        existing.add(name)
        policy['unresolved'] = sorted(set(policy['unresolved']))
        providers = []
        if policy.get('provider'):
            providers.append({'provider': policy['provider'], 'kind': policy['kind'], 'source': container['source_span']})
        if policy['kind'] in ('type-specific-handler', 'generic-class-and-parent-lookup'):
            providers.extend({'provider': handler['name'], 'source': handler.get('source_span'),
                              'kind': policy['kind']} for handler in policy.get('handlers', []))
        if container.get('import_policy', {}).get('kind') == 'extensible-contributor-gap':
            providers.append({'provider': container['id'], 'kind': 'extensible-contributor-gap',
                              'source': container['source_span']})
        policy['dynamic_providers'] = providers
        policy['closed'] = not policy['unresolved'] and policy['kind'] == 'closed-table'
        if policy['kind'] == 'closed-table' and policy['unresolved']:
            policy['kind'] = 'closed-table-with-coverage-gap'
        return policy

    def aliases(members):
        first = {}
        for member in members:
            identity = member['identity']
            object_id = identity.get('object_id') if isinstance(identity, dict) else None
            if object_id:
                if object_id in first:
                    member['alias_of'] = first[object_id]
                else:
                    first[object_id] = member['name']
    def constructor(container):
        slot = next((slot for slot in container.get('slots', []) if slot['slot'] == 'make_new'), None)
        if slot is None:
            dispatch = selected_function('type_call', source='py/objtype.c')
            return {'callable': False if dispatch else None, 'source': container['source_span'],
                    'resolution': _resolution('non-callable' if dispatch else 'unresolved',
                                              [] if dispatch else ['selected type-call dispatch unassessed'],
                                              authority=dispatch[1]['id'] if dispatch else None)}
        names = re.findall(r'\b[A-Za-z_]\w*\b', slot.get('expression') or '')
        name = names[-1] if names else None
        provider = selected_function(name, translation_unit=container['translation_unit'])
        unresolved = {'source': container['source_span'], 'callable': True,
                      'resolution': _resolution('unresolved', ['constructor count/parser/control-flow contract unassessed'])}
        checker = selected_function('mp_arg_check_num_sig', source='py/argcheck.c')
        if not provider or not checker or checker[1]['source_span']['source'] != str(root / 'py/argcheck.c'):
            return unresolved
        facts, function = provider
        checks = []
        for event in function['events']['calls']:
            helper, args = call_parts(event['expression'])
            callee = event.get('callee') or {}
            if (helper != 'mp_arg_check_num' or len(args) != 5 or event.get('guards')
                    or callee.get('kind') != 'FUNCTION_DECL'
                    or callee.get('source_span', {}).get('source') != str(root / 'py/runtime.h')
                    or direct_parameter(function, args[0]) != 1
                    or direct_parameter(function, args[1]) != 2):
                continue
            bounds = [constant(expression, function['constants']) for expression in args[2:]]
            if all(value is not None for value in bounds):
                checks.append((event, bounds))
        if len(checks) != 1:
            return unresolved
        event, (minimum, maximum, keywords) = checks[0]
        shape = {'minimum': minimum, 'maximum': maximum, 'keywords': bool(keywords),
                 'implicit_argument_offset': 0, 'descriptor': 'constructor', 'function': function['name'],
                 'parameters_resolution': 'unresolved',
                 'unresolved': ['constructor argument input/default domain unassessed']}
        return {'callable': True, 'callshape': shape, 'semantics': function['semantics'],
                'source': function['source_span'],
                'resolution': _resolution('bounded-resolved', authority='allocated make_new and selected direct argument-count checker',
                                          checker=checker[1]['id'], check_source=event['source_span'])}


    def class_symbol(container):
        identity = container['id']
        if identity in types: return types[identity]
        result = {'name': container.get('python_name') or container['name'], 'kind': 'class', 'identity': identity,
                  'source': container['source_span'], 'resolution': _resolution('resolved', availability={'status': 'resolved', 'authority': 'allocated selected type object'}),
                  'members': [], 'bases': [], 'declared_bases': [], 'base_identities': [],
                  'base_resolution': _resolution('resolved', authority='no explicit parent slot'),
                  'slots': container.get('slots', []), 'attribute_policy': {}}
        result['constructor'] = constructor(container)
        types[identity] = result
        constructing.add(identity)
        members = []
        for binding in bindings_by_container.get((container['translation_unit'], container['name']), []):
            members.append((yield symbol, binding))
        result['members'] = members
        policy = compose_policy(container, result['members'])
        flags = container.get('type_flags_value')
        instance_flag = constant(facts_by_tu[container['translation_unit']].get('contracts', {}).get('instance_type_flag', ''),
                                 facts_by_tu[container['translation_unit']].get('frontend', {}).get('contract_constants', {}))
        instance_inheritance = flags is not None and instance_flag is not None and bool(flags & instance_flag) and any(
            handler['name'] in ('mp_obj_instance_attr', 'mp_obj_class_attr')
            and selected_function(handler['name'], source='py/objtype.c', translation_unit=container['translation_unit'])
            for handler in policy.get('handlers', []))
        for slot in container.get('slots', []):
            if slot['slot'] != 'parent': continue
            names = re.findall(r'& (\w+)', slot.get('expression') or '')
            parent, status = resolve(names[-1], container['translation_unit']) if len(names) == 1 else (None, 'unsupported parent slot')
            if parent and parent[1]['kind'] == 'type' and parent[1]['id'] in containers and parent[1]['id'] not in constructing:
                parent_type = yield class_symbol, containers[parent[1]['id']]
                result['declared_bases'].append(parent_type['name'])
                result['base_identities'].append(parent_type['identity'])
                lookup_stops = [condition for fn in (type_framework[0]['functions'] if type_framework else [])
                                if fn['name'] == 'mp_obj_class_lookup' and fn['source_span']['source'] == str(root / 'py/objtype.c')
                                for condition in fn['events']['conditions'] if condition['expression'] == 'type == & mp_type_object']
                parent_lookup_stops = parent[1]['name'] == 'mp_type_object' and bool(lookup_stops)
                if parent_lookup_stops:
                    policy['parent_lookup_stop'] = {'identity': parent_type['identity'], 'source': lookup_stops[0]['source_span'],
                                                    'reason': 'selected class lookup terminates before the object root table'}
                result['base_resolution'] = _resolution('resolved' if instance_inheritance and not parent_lookup_stops else 'class-object-only',
                                                       ['object root is a lookup terminator, not an inherited member surface'] if parent_lookup_stops else [] if instance_inheritance else ['native instance dispatch does not imply parent member inheritance'],
                                                       source=slot, runtime_framework=framework[1]['id'] if framework else None,
                                                       class_framework=type_framework[1]['id'] if type_framework else None)
                if instance_inheritance and not parent_lookup_stops:
                    result['bases'].append(parent_type['name'])
                own = {m['name'] for m in result['members']}
                for member in ([] if parent_lookup_stops else parent_type['members']):
                    if member['name'] in own:
                        continue
                    inherited = {**deep_copy(member), 'inherited_from': parent_type['identity']}
                    if not instance_inheritance:
                        if not type_framework:
                            continue
                        inherited['attribute_scope'] = 'class'
                        if inherited.get('callshape', {}).get('descriptor') == 'function':
                            _receiver_offset(inherited['callshape'], 0)
                    result['members'].append(inherited)
                    own.add(member['name'])
                policy['unresolved'] = [reason for reason in policy['unresolved'] if reason != 'parent attribute surface unassessed']
                if instance_inheritance:
                    policy['unresolved'].extend(parent_type['attribute_policy'].get('unresolved', []))
                elif not framework or not type_framework:
                    policy['unresolved'].append('native parent class/instance framework composition unassessed')
            else:
                policy['unresolved'].append('parent surface ownership ' + status)
                result['base_resolution'] = _resolution('unresolved', ['parent owner/type or cyclic relation unassessed'],
                                                        ownership=status, source=slot)
        aliases(result['members'])
        policy['unresolved'] = sorted(set(policy['unresolved']))
        policy['closed'] = not policy['unresolved'] and policy['kind'] in ('closed-table', 'closed-local-table-with-parent-gap')
        result['attribute_policy'] = policy
        constructing.remove(identity)
        return result

    for container in raw['containers']:
        if container['kind'] == 'type': complete(class_symbol(container))
    modules = []
    for container in raw['containers']:
        if container['kind'] != 'module': continue
        exports = [complete(symbol(b)) for b in bindings_by_container.get((container['translation_unit'], container['name']), [])]
        rows = container['import_policy'].get('selected_table_evidence', [])
        names = sorted({row['name'] for row in rows})
        if not names:
            diagnostics.append({'kind': 'unassessed-module-import-identity', 'identity': container['id'],
                                'source': container['source_span'], 'reason': 'allocated module lacks selected import table identity'})
            continue
        policy = compose_policy(container, exports)
        aliases(exports)
        for name in names:
            modules.append({'name': name, 'identity': container['id'], 'source': container['source_span'],
                            'exports': exports, 'attribute_policy': policy, 'import_policy': container['import_policy'],
                            'resolution': _resolution('resolved', availability={'status': 'resolved', 'authority': 'selected module table and allocated owner'})})
    return sorted(modules, key=lambda m: (m['name'], m['identity'])), sorted(types.values(), key=lambda t: t['identity']), diagnostics


def _nested_initializer(nodes, position):
    """Initializer fact at ``position`` with its table references replaced by nested facts.

    Only a row keeps its ``initializer_ordinal``; facts nested below it are published without one."""
    root = dict(nodes[position])
    stack = [root]
    while stack:
        fact = stack.pop()
        nested = []
        for key in ('fields', 'designators'):
            if key in fact:
                fact[key] = [dict(nodes[child]) for child in fact[key]]
                nested.extend(fact[key])
        if fact.get('value') is not None:
            fact['value'] = dict(nodes[fact['value']])
            nested.append(fact['value'])
        for child in nested:
            child.pop('initializer_ordinal', None)
        stack.extend(nested)
    return root


def _publish_initializers(inventory):
    """Replace initializer table references in ``inventory`` with nested facts, dropping the tables.

    Definitions and argument-table rows carry the ``initializer_nodes`` table their initializer facts
    refer into; the published inventory holds the nested facts instead, so it needs no table."""
    # Position maps per shared table; each entry holds its table so the id stays unique during the walk.
    positions = {}
    stack = [inventory]
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            stack.extend(item for item in value if isinstance(item, (dict, list)))
            continue
        nodes = value.pop('initializer_nodes', None)
        if nodes is not None:
            position = positions.get(id(nodes))
            if position is None:
                position = positions[id(nodes)] = (nodes, {id(node): index for index, node in enumerate(nodes)})
            position = position[1]
            if value.get('initializer_rows'):
                value['initializer_rows'] = [_nested_initializer(nodes, position[id(row)]) for row in value['initializer_rows']]
            if 'raw_type_members' in value:
                value['raw_type_members'] = {
                    name: [_nested_initializer(nodes, position[id(member)]) if member is not None else None for member in members]
                    for name, members in value['raw_type_members'].items()}
            if 'table_id' in value and isinstance(value.get('initializer'), dict):
                value['initializer'] = _nested_initializer(nodes, position[id(value['initializer'])])
                value['fields'] = {name: _nested_initializer(nodes, position[id(field)]) for name, field in value['fields'].items()}
            continue
        stack.extend(item for item in value.values() if isinstance(item, (dict, list)))


def _public_translation_unit(facts):
    """Retain extraction coverage without duplicating internal branch graphs."""
    functions = []
    for function in facts['functions']:
        summary = {key: function[key] for key in (
            'id', 'declaration_identity', 'name', 'linkage', 'source_span',
            'parameters', 'constants', 'parameter_source_contract')}
        events = function['events']
        summary['events'] = {
            'counts': {key: len(events[key]) for key in (
                'calls', 'returns', 'variables', 'conditions', 'writes')},
            'semantic_unassessed': events.get('semantic_unassessed', []),
            'control_flow_unresolved': events.get('control_flow_unresolved', []),
        }
        semantics = function['semantics']
        summary['semantics'] = {key: semantics[key] for key in (
            'function_id', 'parameters', 'status', 'input_coverage',
            'input_resolution', 'return_resolution', 'input_unresolved',
            'return_unresolved', 'frontend_unresolved', 'unresolved',
            'syntax_semantic_barriers', 'diagnostic_stage') if key in semantics}
        summary['evidence_role'] = 'extraction overview; selected API fields retain branch/value evidence'
        functions.append(summary)
    return {**facts, 'functions': functions}


class _WorkerTraceback(Exception):
    """Carries the formatted traceback of an exception raised in an extraction worker process."""


_EXTRACTION = None


def _extract_worker(index):
    gc.disable()
    records, compdb, root = _EXTRACTION
    try:
        return True, _extract(records[index], compdb, root), None
    except Exception as exc:
        return False, exc, traceback.format_exc()


def _extract_parallel(records, compdb, root, jobs, concurrent=None):
    """Extract translation units in forked worker processes, returning results in record order.

    Workers inherit the records and the compiler authority module through fork. The first failing
    record in order raises its own exception, as in serial extraction; later records are cancelled.
    ``concurrent`` is an independent callable run in this process while the workers extract; its
    result is returned alongside, and its failure is raised only after every record has extracted.
    """
    global _EXTRACTION
    _EXTRACTION = (records, compdb, root)
    try:
        with ProcessPoolExecutor(max_workers=min(jobs, len(records)),
                                 mp_context=multiprocessing.get_context('fork')) as pool:
            futures = [pool.submit(_extract_worker, index) for index in range(len(records))]
            side, side_error = None, None
            if concurrent is not None:
                try:
                    side = concurrent()
                except Exception as exc:
                    side_error = exc
            results = []
            for future in futures:
                succeeded, value, formatted = future.result()
                if not succeeded:
                    for pending in futures:
                        pending.cancel()
                    raise value from _WorkerTraceback('\n' + formatted)
                results.append(value)
            if side_error is not None:
                raise side_error
            return results, side
    finally:
        _EXTRACTION = None


def _extract_serial(records, compdb, root):
    """Extract translation units in record order."""
    return [_extract(record, compdb, root) for record in records]


def _gc_paused(function):
    """Run ``function`` without automatic cyclic collection.

    Extraction allocates millions of long-lived containers, which automatic collection rescans
    repeatedly. Per-record extraction creates no reference cycles, so its garbage is freed by
    reference counting without collection."""
    @functools.wraps(function)
    def paused(*args, **kwargs):
        enabled = gc.isenabled()
        gc.disable()
        try:
            return function(*args, **kwargs)
        finally:
            if enabled:
                gc.enable()
    return paused


def _predicted_inputs(directory, obj, compdb):
    """The prerequisites the build's dependency file for ``obj`` (.d, else .P, beside it) names."""
    for suffix in ('.d', '.P'):
        try:
            text = Path(obj).with_suffix(suffix).read_text(errors='surrogateescape')
        except OSError:
            continue
        return [Path(directory) / path for path in compdb.first_rule(text) or ()]
    return []


def select_records(*, database: Path, root: Path, build_dir: Path) -> dict:
    """Read and validate the selected compiler database once: one record per entry, with every
    source, response operand and object output captured, and the database's completeness for the
    selected build. Captures start a new input table for the run."""
    compiler.clear_resolution_cache()
    compiler.clear_replay_cache()
    compiler.clear_input_table()
    database_content, database_snapshot = compiler.read_recorded(database)
    root, build_dir, database = Path(root).resolve(), Path(build_dir).resolve(), Path(database).resolve()
    if not root.is_dir() or not build_dir.is_dir():
        raise ValueError('MicroPython root and selected build directory must exist')
    compdb = compiler.authority(root)
    entries = json.loads(database_content)
    if not isinstance(entries, list) or not entries:
        raise ValueError('selected compiler database must be a nonempty list')
    records, outputs = [], set()
    for index, entry in enumerate(entries):
        compdb.validate_entry(entry, f'compiler database entry {index}')
        obj = compdb.output_of(entry)
        if obj is None: raise ValueError(f'compiler database entry {index} has no selected object output')
        obj = str(Path(obj).resolve())
        if obj in outputs: raise ValueError(f'ambiguous repeated selected object output: {obj}')
        outputs.add(obj)
        source_snapshot = compiler.recorded(compdb.entry_file(entry))
        # The source as the compiler opens it, so it is captured before any compiler run reads it.
        compiler.recorded(Path(entry['directory']) / entry['file'])
        # The build's own dependency record for the object predicts what pass 1 will read; capturing
        # it now lets pass 1 start after the capture of what it reads. It is a prediction only: an
        # input it omits is captured after pass 1, which then runs again.
        for path in _predicted_inputs(entry['directory'], obj, compdb):
            try:
                compiler.recorded(path)
            except OSError:
                pass
        physical = source_snapshot['resolved']
        arguments = compdb.arguments_of(entry)
        response_snapshots = {}
        expanded_arguments, response_inputs = compiler.expand_arguments(arguments, entry['directory'], snapshots=response_snapshots)
        argv_identity = hashlib.sha256(json.dumps({'arguments': expanded_arguments, 'directory': entry['directory'],
                                                   'response_inputs': response_inputs}, sort_keys=True).encode()).hexdigest()
        records.append({**entry, 'object': obj, 'physical': physical, 'arguments_sha256': argv_identity,
                        'identity': physical + '::object:' + obj + '::argv:' + argv_identity,
                        '_source_snapshot': source_snapshot, '_response_snapshots': response_snapshots,
                        '_expanded_arguments': expanded_arguments, '_response_inputs': response_inputs})
    completeness = compdb.completeness(entries, build_dir)
    if not completeness['complete']:
        raise ValueError('selected compiler database is incomplete: ' + json.dumps(completeness, sort_keys=True))
    records.sort(key=lambda record: record['identity'])
    inputs = compiler.reconcile_inputs([
        ('selected compiler database', {database_snapshot['path']: database_snapshot}),
        ('compiler database authority', {compdb.__mpy_api_input__['path']: compdb.__mpy_api_input__}),
        ('selected object outputs', compiler.recorded_inputs(record['object'] for record in records)),
        *[(record['identity'] + ' source', {record['_source_snapshot']['path']: record['_source_snapshot']})
          for record in records],
        *[(record['identity'] + ' response operands', record['_response_snapshots']) for record in records]])
    return {'database': database, 'database_sha256': database_snapshot['sha256'], 'root': root,
            'build_dir': build_dir, 'compdb': compdb, 'entries': entries, 'records': records,
            'completeness': completeness, 'inputs': inputs}


@_gc_paused
def collect_inventory(selection: dict, *, port: str, target: str, variant: str, link: dict, jobs: int = 1):
    """Collect the selected compiler records and join only qualified allocated owners.

    Returns the inventory and what the final verification before publication checks
    (``compiler.verify_collection``): every consumed input snapshot and the include selection
    constraints of every translation unit."""
    if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1:
        raise ValueError('inventory jobs must be a positive integer')
    for key, value in (('port', port), ('target', target), ('variant', variant)):
        if not isinstance(value, str) or not value.strip(): raise ValueError(f'{key} must be a nonempty build identity')
    root, build_dir, database = selection['root'], selection['build_dir'], selection['database']
    compdb, records, completeness = selection['compdb'], selection['records'], selection['completeness']
    selection_inputs = selection['inputs']
    for package in ('tree-sitter', 'tree-sitter-c'):
        try: importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f'API syntax frontend prerequisite is missing: {package}') from exc
    # Ownership uses record identities, including the object for repeated physical sources.
    ownership_records = [{**r, 'physical': r['identity']} for r in records]
    if jobs == 1:
        inputs = _extract_serial(records, compdb, root)
        ownership = collect_ownership(ownership_records, link)
    else:
        inputs, ownership = _extract_parallel(records, compdb, root, jobs,
                                              lambda: collect_ownership(ownership_records, link))
    selection_constraints = compiler.merge_selection(facts.pop('_selection', ()) for facts in inputs)
    consumed_inputs = compiler.reconcile_inputs([
        ('selected compiler records', selection_inputs),
        *[(facts['translation_unit'], facts['input']['input_snapshots']) for facts in inputs],
        ('selected linked inputs', ownership['input_snapshots'])])
    raw, resolve, present = join(inputs, ownership)
    frozen_payload = collect_frozen_payload(inputs, present)
    modules, types, diagnostics = _normal_surface(raw, inputs, resolve, present, root)
    if not any(module['name'] == 'builtins' for module in modules):
        raise RuntimeError('selected linked build has no qualified builtin singleton; inspect modbuiltins compiler and allocated owner evidence')
    unassessed = [f['translation_unit'] for f in inputs if f['coverage']['status'] not in ('processed', 'processed-empty')]
    unresolved = [{'container': b['identity']['container'], 'name': b['identity']['export'],
                   'identity': b['identity'], 'reasons': b['unresolved']} for b in raw['bindings'] if b['unresolved']]
    dependencies = {}
    for facts in inputs:
        for path, digest in facts['input']['dependencies'].items():
            if path in dependencies and dependencies[path] != digest:
                raise RuntimeError(f'conflicting per-TU compiler dependency content: {path}')
            dependencies[path] = digest
    dependencies = dict(sorted(dependencies.items()))
    qstrs = compiler.qstr_strings(dependencies, consumed_inputs)
    def runtime_name(name):
        evidence = qstrs.get(name, {})
        return evidence.get('value', name) if evidence.get('status') == 'resolved' else name
    def normalize_names(root):
        """Decode qstr identifiers in every dict reachable from ``root``, visiting dicts in preorder."""
        stack = [root]
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(item for item in reversed(value) if isinstance(item, (list, dict)))
                continue
            for field in ('name', 'export', 'alias_of', 'qstr', 'exported_argument'):
                if isinstance(value.get(field), str):
                    encoded = value[field]
                    decoded = runtime_name(encoded)
                    if decoded != encoded:
                        value.setdefault('qstr_identifiers', {})[field] = encoded
                        value[field] = decoded
            for field in ('bases', 'declared_bases'):
                if isinstance(value.get(field), list):
                    value[field] = [runtime_name(name) for name in value[field]]
            stack.extend(item for field, item in reversed(list(value.items()))
                         if field != 'qstr_identifiers' and isinstance(item, (list, dict)))
    normalize_names(modules)
    normalize_names(types)
    modules.sort(key=lambda module: (module['name'], module['identity']))
    revision = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                              capture_output=True, text=True)
    source_revision = revision.stdout.strip() if revision.returncode == 0 else None
    for facts in inputs:
        facts['input']['input_snapshots'] = compiler.public_inputs(facts['input']['input_snapshots'])
    ownership['input_snapshots'] = compiler.public_inputs(ownership['input_snapshots'])
    inventory = {'schema_version': 1,
            'build': {'port': port, 'target': target, 'variant': variant, 'root': str(root), 'build_dir': str(build_dir),
                      'source_revision': source_revision,
                      'compiler_database': {'path': str(database), 'sha256': selection['database_sha256']},
                      'compdb_authority': {'path': str(Path(compdb.__file__).resolve()), 'sha256': compdb.__mpy_api_input__['sha256']},
                      'compilers': sorted({(f['input']['compiler'], f['input']['compiler_sha256']) for f in inputs}),
                      'source_inputs': dependencies, 'qstr_strings': qstrs,
                      'consumed_inputs': compiler.public_inputs(consumed_inputs),
                      'frozen_payload': frozen_payload, 'link': ownership['selected_link_authority']},
            'modules': modules, 'types': types,
            'coverage': {'selected_translation_units': [f['translation_unit'] for f in inputs],
                         'processed_translation_units': [f['translation_unit'] for f in inputs if f['coverage']['status'] in ('processed', 'processed-empty')],
                         'processed_empty_translation_units': [f['translation_unit'] for f in inputs if f['coverage']['status'] == 'processed-empty'],
                         'unassessed_translation_units': unassessed, 'compiler_database_completeness': completeness,
                         'unresolved_bindings': unresolved, 'unassessed_containers': raw['unassessed_containers'],
                         'unassessed_entries': raw['unassessed_entries'],
                         'unassessed_link_objects': ownership['unassessed_objects'],
                         'dynamic_contributors': [{'identity': item['identity'], 'attribute_policy': item['attribute_policy']}
                                                  for item in [*modules, *types] if item['attribute_policy'].get('dynamic_providers')],
                         'parent_relations': [{'identity': item['identity'], 'declared_bases': item['declared_bases'],
                                               'base_identities': item['base_identities'], 'resolution': item['base_resolution']}
                                              for item in types if item['declared_bases'] or item['base_resolution']['status'] != 'resolved'],
                         'unassessed_qstr_strings': [evidence for evidence in qstrs.values() if evidence['status'] != 'resolved'],
                         'typing': {'status': 'unassessed', 'reason': 'inventory generation is not checker compatibility acceptance'},
                         'limits': ['bounded C helper/control-flow normalization only', 'weak/COMDAT ownership unassessed',
                                    'system-header definitions unassessed', 'complete accepted callable input domains unassessed',
                                    'native parent slots describe class lookup, not implied native instance inheritance']},
            'diagnostics': diagnostics, 'translation_units': [_public_translation_unit(facts) for facts in inputs],
            'provenance': {'frontend': 'tree-sitter-c on selected actual compiler expansion',
                           'packages': {package: importlib.metadata.version(package) for package in ('tree-sitter', 'tree-sitter-c')},
                           'ownership': ownership, 'binding_ownership_resolutions': raw['ownership_resolutions'],
                           'raw_bindings': raw['bindings'], 'selected_import_rows': raw['import_rows']}}
    _publish_initializers(inventory)
    return inventory, {'inputs': consumed_inputs, 'selection': selection_constraints}
