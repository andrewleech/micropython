"""Build-selected MicroPython API inventory, with explicit bounded coverage."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import copy
import hashlib
import importlib.metadata
import json
import re
import subprocess

from . import compiler
from .frontend import syntax_adapter, constant
from .bindings import definitions, registration_records, call_parts, scalar_field, strip_outer, constant_value
from .semantics import semantics, direct_parameter
from .link import collect_ownership, join
from .frozen_payload import collect_frozen_payload


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
        response_snapshots = {}
        expanded, response_inputs = compiler.expand_arguments(arguments, record['directory'], snapshots=response_snapshots)
        executable = compiler.executable(expanded[0], record['directory'])
        snapshots = compiler.reconcile_inputs([
            ('selected source', {record['_source_snapshot']['path']: record['_source_snapshot']}),
            ('selected response operands', record['_response_snapshots']),
            ('current non-C inputs', compiler.capture_inputs([compdb.entry_file(record), executable])),
            ('current response operands', response_snapshots)])
        compiler.verify_inputs(snapshots)
        return {'translation_unit': record['identity'], 'source': physical, 'definitions': [], 'functions': [],
                'registrations': [], 'errors': [], 'coverage': {'status': 'unassessed', 'reason': 'non-C compiler record; syntax and dependency extraction unassessed'},
                'input': {'arguments': arguments, 'directory': record['directory'],
                          'dependencies': {physical: record['_source_snapshot']['sha256']},
                          'compiler': executable, 'compiler_sha256': snapshots[str(Path(executable).absolute())]['sha256'],
                          'object': record['object'], 'response_inputs': response_inputs, 'receipts': [],
                          'input_snapshots': snapshots}}
    prepared = compiler.prepare(record, compdb)
    expanded, expansion_receipt = compiler.invoke(prepared, ['-E'])
    directives, directive_receipt = compiler.invoke(prepared, ['-E', '-fdirectives-only'])
    compiler.validate_include_markers(expanded, prepared)
    compiler.validate_include_markers(directives, prepared)
    contracts, contract_receipt = compiler.compiler_contracts(prepared)
    prepared['contracts'] = contracts
    compiler.validate_dependencies(prepared, compdb)
    code, origins = compiler.unmark(expanded, prepared['directory'])
    directive_code, directive_origins = compiler.unmark(directives, prepared['directory'])
    try:
        declarations, functions, errors, frontend = syntax_adapter(code, origins, physical, prepared)
    except Exception as exc:
        raise RuntimeError(f'C syntax frontend failed for {physical}: {type(exc).__name__}: {exc}') from exc
    if errors:
        raise RuntimeError(f'C syntax frontend failed for {physical}: ' + json.dumps(errors, sort_keys=True))
    template_sources = [str(root / name) for name in ('py/obj.h', 'py/runtime.h', 'py/objstr.h', 'py/objmodule.h',
                                                     'py/objstr.c', 'py/objint.c', 'py/objint_mpz.c',
                                                     'py/objint_longlong.c', 'py/objmodule.c')]
    for function in functions:
        function['id'] = record['identity'] + function['id'][len(physical):]
        for call in function['events']['calls']:
            call['qualified_template_sources'] = template_sources
    by_name = {}
    for function in functions:
        by_name.setdefault(function['name'], []).append(function)
    normalized = []
    for function in functions:
        tests = [{'name': name, 'expression': condition['expression'], 'source_span': condition['source_span']}
                 for condition in (function['events']['conditions'] if not function['events'].get('semantic_unassessed') else [])
                 for name in re.findall(r'MP_QSTR_(\w+)', condition['expression'])]
        normalized.append({**{key: function[key] for key in ('id', 'declaration_identity', 'name', 'linkage', 'source_span')},
                           'parameters': function['parameters'], 'constants': function['constants'],
                           'events': function['events'], 'body_expression': function['body_expression'],
                           'parameter_source_contract': function['parameter_source_contract'],
                           'semantics': semantics(function, by_name, errors, contracts),
                           'handler_attributes': sorted({test['name'] for test in tests}), 'handler_attribute_tests': tests})
    for function in functions:
        for call in function['events']['calls']:
            call.pop('qualified_template_sources', None)
    prepared['frontend'] = frontend
    object_definitions = definitions(declarations, functions, errors, record['identity'], directive_code, directive_origins, prepared)
    registrations = registration_records(directive_code, directive_origins, physical, prepared)
    compiler.verify_inputs(prepared['input_snapshots'])
    empty_program = not code.strip()
    return {'translation_unit': record['identity'], 'source': physical,
            'definitions': object_definitions,
            'functions': normalized,
            'registrations': registrations,
            'errors': errors, 'contracts': contracts, 'frontend': frontend,
            'coverage': {'status': 'processed-empty' if empty_program else 'processed',
                         'owned_declarations': 'bounded-recovered', 'empty_selected_program': empty_program,
                         'no_active_bindings': not registrations and not any(
                             definition['kind'] in ('module', 'type', 'wrapper', 'dictionary', 'map-table')
                             for definition in object_definitions),
                         'project_header_definitions': 'all compiler non-system dependencies traversed',
                         'project_headers': [p for p in prepared['project_dependencies'] if p != physical],
                         'system_header_definitions': 'unassessed; excluded by compiler -MM authority',
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
                      'compiler_directives_sha256': hashlib.sha256(directives.encode()).hexdigest(),
                      'receipts': [*prepared['receipts'], expansion_receipt, directive_receipt, contract_receipt]}}


def _parameters(shape, semantic, facts, parser_owner=None):
    """Expose parameters only for direct wrappers and complete indexed tables."""
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
    params = function['parameters']
    if (len(args) != 6 or len(params) != 3 or args[:3] != params or parser.get('guards')
            or any(row.get('via_helper') for row in rows)
            or args[4] != rows[0]['table_id'].rsplit('::', 1)[-1]):
        unresolved('parser count/vector/keyword/table relation is not direct unchanged wrapper forwarding')
        return
    env = function['constants']
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
    n_pos, pos, kws, n_allowed, allowed, out_vals = parser_params
    conditions = {strip_outer(condition['expression']) for condition in parser_function['events']['conditions']}
    writes = {write['expression'] for write in parser_function['events']['writes']}
    expected = {'i < ' + n_pos, allowed + ' [ i ] . flags & MP_ARG_KW_ONLY',
                allowed + ' [ i ] . flags & MP_ARG_REQUIRED'}
    if not expected <= conditions or out_vals + ' [ i ] = ' + allowed + ' [ i ] . defval' not in writes:
        unresolved('selected parser positional/keyword/required/default forwarding family is unsupported')
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
    shape['parameters_authority'] = {'parser_identity': parser_function['id'], 'source': parser_function['source_span'],
                                     'flags': flags_values, 'scalar_storage': storage}
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
    if constant(count_expr, env) != row_count:
        unresolved('parser table count is not the complete selected table')
        return
    parameters = []
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
        parameters.append({'name': row['name'],
                           'kind': 'positional_or_keyword' if index < positional_prefix else 'keyword_only',
                           'table_entry_index': index, 'required': required,
                           'default_contract': None if required else default,
                           'source': row['source_span'],
                           'resolution': _resolution('resolved', authority='selected direct mp_arg_parse_all table and actual enum flags')})
        row['python_parameter_default_resolution'] = ('required-no-python-default' if required else
            'unresolved-storage-default' if default.get('kind') == 'unresolved' else 'selected-direct-parser-default')
    minimum, maximum = shape.get('minimum'), shape.get('maximum')
    if (not isinstance(minimum, int) or minimum < 0 or minimum > positional_prefix
            or not isinstance(maximum, int) or maximum < minimum):
        unresolved('wrapper positional bounds cannot be composed with the parser table prefix')
        return
    for parameter in parameters:
        index = parameter['table_entry_index']
        if index < minimum:
            parameter.update(kind='positional_only', required=True, default_contract=None)
        elif index >= maximum:
            parameter['kind'] = 'keyword_only'
    offset = shape.get('implicit_argument_offset', 0)
    if not isinstance(offset, int) or offset < 0 or offset > min(positional_prefix, minimum):
        unresolved('implicit receiver is not guaranteed in the parser positional prefix')
        return
    shape['parser_parameters'] = parameters
    shape['parameters'] = parameters[offset:]
    shape['parameters_resolution'] = 'resolved'


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
    shape['parameters'] = complete[offset:]


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
                    and not function['events'].get('control_flow_unresolved') else 'unresolved',
                    'authority': dispatch, 'object_ownership': ownership,
                    'result_source': write['source_span']}
    return {'resolved': not reasons, 'reasons': reasons, 'availability': availability,
            'binding': {'kind': 'handler-method-or-fallback' if receivers else 'ordinary-value-candidate',
                        'destination_one_writes': receivers,
                        'destination_escape_calls': escaped},
            'value_contract': value, 'getter': getter}


def _normal_surface(raw, inputs, resolve, present, root):
    containers = {c['id']: c for c in raw['containers']}
    facts_by_tu = {f['translation_unit']: f for f in inputs}
    bindings_by_container = {}
    for binding in raw['bindings']:
        key = (binding['identity']['container_translation_unit'], binding['identity']['container'])
        bindings_by_container.setdefault(key, []).append(binding)
    types, diagnostics = {}, []
    constructing = set()

    def symbol(binding):
        identity = binding['identity']
        target_id = identity.get('object_id')
        source = identity['source_span']
        shape = copy.deepcopy(binding.get('callshape'))
        kind = 'function' if shape and shape.get('callable') is not False else 'constant' if binding['value_contract']['kind'] not in ('object-reference', 'unresolved') else 'unknown'
        availability = copy.deepcopy(binding['availability'])
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
            _parameters(shape, binding.get('semantics'), semantic_facts,
                        selected_function('mp_arg_parse_all', source='py/argcheck.c'))
            result['callshape'] = shape
            result['semantics'] = binding.get('semantics')
            shape_status = 'non-callable' if shape.get('callable') is False else 'resolved' if isinstance(shape.get('minimum'), int) and isinstance(shape.get('maximum'), int) and isinstance(shape.get('keywords'), bool) and not shape.get('unresolved') else 'unresolved'
            result['resolution']['callshape'] = _resolution(shape_status, shape.get('unresolved', []))
            result['resolution']['types'] = {'inputs': (binding.get('semantics') or {}).get('input_resolution', 'unresolved'),
                                             'returns': (binding.get('semantics') or {}).get('return_resolution', 'unresolved')}
        target = containers.get(target_id)
        if target and target['kind'] == 'type':
            definition = class_symbol(target)
            result.update(kind='class', type_identity=target_id)
            if target_id not in constructing:
                result.update({key: copy.deepcopy(definition[key]) for key in ('members', 'bases', 'declared_bases', 'base_identities', 'base_resolution', 'slots', 'attribute_policy', 'constructor')})
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

    def selected_function(name, *, source=None, translation_unit=None):
        candidates = [(f, fn) for f in inputs for fn in f['functions']
                      if fn['name'] == name and present(f, {'name': name})
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

    def compose_policy(container, members):
        policy = copy.deepcopy(container['attribute_policy'])
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
                    or direct_parameter(function, args[0]) != 1 or direct_parameter(function, args[1]) != 2):
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
        result['members'] = [symbol(b) for b in bindings_by_container.get((container['translation_unit'], container['name']), [])]
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
                parent_type = class_symbol(containers[parent[1]['id']])
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
                    inherited = {**copy.deepcopy(member), 'inherited_from': parent_type['identity']}
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
        if container['kind'] == 'type': class_symbol(container)
    modules = []
    for container in raw['containers']:
        if container['kind'] != 'module': continue
        exports = [symbol(b) for b in bindings_by_container.get((container['translation_unit'], container['name']), [])]
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
            'syntax_semantic_barriers') if key in semantics}
        summary['evidence_role'] = 'extraction overview; selected API fields retain branch/value evidence'
        functions.append(summary)
    return {**facts, 'functions': functions}


def collect_inventory(*, database: Path, root: Path, build_dir: Path, port: str,
                      target: str, variant: str, link: dict, jobs: int = 1) -> dict:
    """Collect all selected compiler records and join only qualified allocated owners."""
    database_content, database_snapshot = compiler.read_input(database)
    root, build_dir, database = Path(root).resolve(), Path(build_dir).resolve(), Path(database).resolve()
    if not root.is_dir() or not build_dir.is_dir():
        raise ValueError('MicroPython root and selected build directory must exist')
    if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1:
        raise ValueError('inventory jobs must be a positive integer')
    for key, value in (('port', port), ('target', target), ('variant', variant)):
        if not isinstance(value, str) or not value.strip(): raise ValueError(f'{key} must be a nonempty build identity')
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
        _, source_snapshot = compiler.read_input(compdb.entry_file(entry))
        physical = source_snapshot['resolved']
        arguments = compdb.arguments_of(entry)
        response_snapshots = {}
        expanded_arguments, response_inputs = compiler.expand_arguments(arguments, entry['directory'], snapshots=response_snapshots)
        argv_identity = hashlib.sha256(json.dumps({'arguments': expanded_arguments, 'directory': entry['directory'],
                                                   'response_inputs': response_inputs}, sort_keys=True).encode()).hexdigest()
        records.append({**entry, 'object': obj, 'physical': physical, 'arguments_sha256': argv_identity,
                        'identity': physical + '::object:' + obj + '::argv:' + argv_identity,
                        '_source_snapshot': source_snapshot, '_response_snapshots': response_snapshots,
                        '_expanded_arguments': expanded_arguments})
    completeness = compdb.completeness(entries, build_dir)
    if not completeness['complete']:
        raise ValueError('selected compiler database is incomplete: ' + json.dumps(completeness, sort_keys=True))
    records.sort(key=lambda record: record['identity'])
    selection_inputs = compiler.reconcile_inputs([
        ('selected compiler database', {database_snapshot['path']: database_snapshot}),
        ('compiler database authority', {compdb.__mpy_api_input__['path']: compdb.__mpy_api_input__}),
        ('selected object outputs', compiler.capture_inputs(record['object'] for record in records)),
        *[(record['identity'] + ' source', {record['_source_snapshot']['path']: record['_source_snapshot']})
          for record in records],
        *[(record['identity'] + ' response operands', record['_response_snapshots']) for record in records]])
    for package in ('tree-sitter', 'tree-sitter-c'):
        try: importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f'API syntax frontend prerequisite is missing: {package}') from exc
    if jobs == 1:
        inputs = [_extract(record, compdb, root) for record in records]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as workers:
            inputs = list(workers.map(lambda record: _extract(record, compdb, root), records))
    # Ownership uses record identities, including the object for repeated physical sources.
    ownership_records = [{**r, 'physical': r['identity']} for r in records]
    ownership = collect_ownership(ownership_records, link)
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
    def normalize_names(value):
        if isinstance(value, list):
            for item in value:
                normalize_names(item)
        elif isinstance(value, dict):
            for field in ('name', 'export', 'alias_of', 'qstr'):
                if isinstance(value.get(field), str):
                    encoded = value[field]
                    decoded = runtime_name(encoded)
                    if decoded != encoded:
                        value.setdefault('qstr_identifiers', {})[field] = encoded
                        value[field] = decoded
            for field in ('bases', 'declared_bases'):
                if isinstance(value.get(field), list):
                    value[field] = [runtime_name(name) for name in value[field]]
            for field, item in list(value.items()):
                if field != 'qstr_identifiers':
                    normalize_names(item)
    normalize_names(modules)
    normalize_names(types)
    modules.sort(key=lambda module: (module['name'], module['identity']))
    revision = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                              capture_output=True, text=True)
    source_revision = revision.stdout.strip() if revision.returncode == 0 else None
    compiler.verify_inputs(consumed_inputs)
    for facts in inputs:
        facts['input']['input_snapshots'] = compiler.public_inputs(facts['input']['input_snapshots'])
    ownership['input_snapshots'] = compiler.public_inputs(ownership['input_snapshots'])
    return {'schema_version': 1,
            'build': {'port': port, 'target': target, 'variant': variant, 'root': str(root), 'build_dir': str(build_dir),
                      'source_revision': source_revision,
                      'compiler_database': {'path': str(database), 'sha256': database_snapshot['sha256']},
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
