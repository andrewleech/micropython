"""Bounded source-qualified runtime helper and argument-table contracts."""
import json
import re
from .frontend import constant, constant_rejection
from .bindings import call_parts, constant_value, errors_at, intersect

CONSTRUCTORS = {'mp_obj_new_bool': 'bool', 'mp_obj_new_int': 'int', 'mp_obj_new_int_from_uint': 'int', 'mp_obj_new_int_from_ll': 'int', 'mp_obj_new_int_from_ull': 'int', 'mp_obj_new_bytes': 'bytes', 'mp_obj_new_bytes_from_vstr': 'bytes', 'mp_obj_new_str': 'str', 'mp_obj_new_str_from_utf8_vstr': 'str'}
CONSTRUCTORS.update({'mp_obj_new_list': 'list', 'mp_obj_new_float': 'float'})
INPUT_HELPERS = {'mp_obj_is_true': 'object/truth-tested', 'mp_obj_get_int': 'integer-conversion/helper-boundary', 'mp_get_buffer_raise': 'buffer-capability', 'mp_obj_get_array_fixed_n': 'tuple-or-list/exact-length', 'mp_obj_str_get_str': 'str-or-bytes', 'mp_module_generic_attr': 'registered-module-key-table'}

def qualified_template(call):
    callee = call.get('callee') if call else None
    return bool(callee and callee['kind'] == 'FUNCTION_DECL' and callee['source_span']['source'] in call.get('qualified_template_sources', []))


def event_call(function, expression, location=None):
    if function['events'].get('semantic_unassessed'):
        return None
    candidates = [c for c in function['events']['calls'] if c['expression'] == expression and (location is None or intersect(c['source_span'], location))]
    identities = {json.dumps(c.get('callee'), sort_keys=True) for c in candidates}
    return candidates[0] if candidates and len(identities) == 1 else None


def direct_parameter(function, expression):
    if function['events'].get('semantic_unassessed'):
        return None
    if expression not in function['parameters']: return None
    if any(v['name'] == expression for v in function['events']['variables']): return None
    if any(re.search(r'\b' + re.escape(expression) + r'\b', w.get('target', '')) for w in function['events']['writes']): return None
    if any(re.search(r'& ' + re.escape(expression) + r'\b', c['expression']) for c in function['events']['calls']): return None
    if any(re.search(r'& ' + re.escape(expression) + r'\b', v['expression']) for v in function['events']['variables']): return None
    return function['parameters'].index(expression)


def return_contract(expression, function, contracts, location=None):
    if not expression: return {'kind': 'void-C-return', 'reason': 'bare C return does not denote Python None', 'evidence': expression}
    value = constant_value(expression, contracts)
    if value['kind'] in ('None', 'bool', 'int', 'str'): return value
    parameter_index = direct_parameter(function, expression)
    if parameter_index is not None:
        return {'kind': 'argument', 'argument': parameter_index, 'evidence': expression, 'scope': function['id']}
    name, arguments = call_parts(expression)
    call = event_call(function, expression, location)
    if name in CONSTRUCTORS and qualified_template(call):
        return {'kind': CONSTRUCTORS[name], 'evidence': expression, 'callee': call['callee']}
    if name == 'mp_obj_new_str_type_from_vstr' and arguments and qualified_template(call):
        helper, values = call_parts(arguments[0])
        helper_call = event_call(function, arguments[0], location)
        if helper == 'mp_obj_get_type' and values and qualified_template(helper_call):
            return {'kind': 'same-str-or-bytes-type', 'argument_expression': values[0], 'evidence': expression, 'scope': function['id'], 'callee': call['callee']}
    if name == 'mp_obj_new_tuple' and len(arguments) == 2 and qualified_template(call):
        matched = re.search(r'\{ (.*) \}', arguments[1])
        if matched:
            from .bindings import split_args
            elements = []
            for expression_item in split_args(matched[1]):
                relation = re.fullmatch(r'args \[ ARG_(\w+) \] \. u_obj', expression_item)
                elements.append({'kind': 'argument-table', 'argument': relation[1], 'scope': function['id'], 'evidence': expression_item} if relation else return_contract(expression_item, function, contracts, location))
            return {'kind': 'tuple', 'length_expression': arguments[0], 'elements': elements, 'evidence': expression, 'callee': call['callee']}
    if name and call and not qualified_template(call):
        return {'kind': 'unresolved', 'reason': 'callee is not a qualified runtime helper template', 'evidence': expression, 'callee': call.get('callee')}
    if expression in {v['name'] for v in function['events']['variables']}:
        return {'kind': 'unresolved', 'reason': 'local-result dataflow and complete element union not established', 'evidence': expression}
    return {'kind': 'unresolved', 'reason': 'unsupported return/helper expression', 'evidence': expression}


def remap_return(contract, arguments, outer, callee):
    mapped = dict(contract)
    if contract['kind'] == 'argument':
        index = contract['argument']; actual = arguments[index] if index < len(arguments) else None
        mapped.update({'callee_relation': contract, 'forwarded_expression': actual})
        parameter_index = direct_parameter(outer, actual)
        if parameter_index is not None: mapped.update({'argument': parameter_index, 'scope': outer['id']})
        else:
            mapped.pop('argument', None)
            mapped.update({'kind': 'unresolved', 'reason': 'helper argument forwarding is not a proven direct outer parameter'})
    elif contract['kind'] == 'tuple':
        mapped['elements'] = [remap_return(e, arguments, outer, callee) for e in contract['elements']]
        if any(e['kind'] in ('unresolved', 'void-C-return') for e in mapped['elements']): mapped.update({'kind': 'unresolved', 'reason': 'helper tuple element forwarding unresolved'})
    elif contract['kind'] == 'argument-table':
        if contract.get('table_id') and len(arguments) == len(callee['parameters']) and all(direct_parameter(outer, argument) is not None for argument in arguments):
            mapped['caller_forwarding'] = {'scope': outer['id'], 'callee_scope': callee['id'], 'parameters': {parameter: direct_parameter(outer, argument) for parameter, argument in zip(callee['parameters'], arguments)}, 'authority': 'direct unchanged C count/vector/keyword parameter forwarding'}
        else:
            mapped.update({'kind': 'unresolved', 'reason': 'helper argument table relation is not bound to unchanged outer count/vector/keyword parameters'})
    elif contract['kind'] == 'same-str-or-bytes-type':
        expression = contract['argument_expression']
        index = direct_parameter(callee, expression)
        actual = arguments[index] if index is not None and index < len(arguments) else None
        mapped.update({'callee_relation': contract, 'forwarded_expression': actual})
        if direct_parameter(outer, actual) is not None: mapped.update({'argument_expression': actual, 'scope': outer['id']})
        else: mapped.update({'kind': 'unresolved', 'reason': 'helper subtype relation is not bound to a direct outer argument'})
    return mapped


def constraint_binding(expression, function):
    parameter_index = direct_parameter(function, expression)
    if parameter_index is not None:
        return {'kind': 'direct-C-parameter', 'parameter': parameter_index, 'scope': function['id'], 'expression': expression}
    return {'kind': 'callee-local-or-derived-expression', 'scope': function['id'], 'expression': expression, 'unresolved': 'not a proven direct Python parameter relation'}


def argument_table_rows(variable, function, environment, contracts):
    """Retain every initializer ordinal, including unsupported field syntax."""
    initializers = variable.get('initializer_rows', [])
    dimensions = variable.get('array_dimensions', [])
    declared_count = constant(dimensions[0]['expression'], environment) if len(dimensions) == 1 and dimensions[0]['expression'] else None
    inferred = len(dimensions) == 1 and not dimensions[0]['expression']
    row_count = len(initializers) if inferred else declared_count
    count_resolved = (inferred or declared_count == len(initializers)) and all(row['syntax_kind'] == 'initializer_list' for row in initializers)
    for initializer in initializers:
        reasons = []
        fields = {}
        next_field = 0
        field_names = ('qst', 'flags', 'defval')
        if initializer['syntax_kind'] != 'initializer_list':
            reasons.append('array row is not an ordinary aggregate initializer; original ordinal is not a proven array index')
        for field in initializer.get('fields', []):
            target = None
            value = field
            if field['syntax_kind'] == 'initializer_pair':
                designators = field.get('designators', [])
                spelling = designators[0]['expression'] if len(designators) == 1 else ''
                target = spelling.replace(' ', '').removeprefix('.') if spelling.startswith('.') else None
                value = field.get('value')
                if target in field_names: next_field = field_names.index(target) + 1
            elif next_field < len(field_names):
                target = field_names[next_field]
                next_field += 1
            if target not in field_names or value is None:
                reasons.append('unsupported argument row field: ' + field['expression'])
            elif target in fields:
                reasons.append('repeated argument row field: ' + target)
            else:
                fields[target] = value
        qstr = re.fullmatch(r'MP_QSTR_(\w+)', fields.get('qst', {}).get('expression', ''))
        flags = fields.get('flags', {}).get('expression', '0')
        if not qstr: reasons.append('argument name is not a supported qstr identifier')
        if 'flags' not in fields: reasons.append('argument flags field is omitted')
        if constant(flags, environment) is None:
            reasons.append('argument flags expression is outside the bounded constant evaluator: ' + flags)
        default = fields.get('defval')
        default_expression = default['expression'] if default else None
        storage_field = None
        default_reasons = []
        if default is None:
            default_contract = {'kind': 'zero-initialized-aggregate-storage', 'evidence': initializer['expression'],
                                'reason': 'omitted aggregate member is C zero-initialized; no Python default or accepted domain is inferred'}
        else:
            members = default.get('fields', []) if default['syntax_kind'] == 'initializer_list' else []
            member = members[0] if len(members) == 1 else None
            designators = member.get('designators', []) if member else []
            spelling = designators[0]['expression'].replace(' ', '') if len(designators) == 1 else ''
            value = member.get('value') if member and member['syntax_kind'] == 'initializer_pair' else None
            if spelling in ('.u_rom_obj', '.u_obj', '.u_bool', '.u_int') and value is not None:
                storage_field = spelling[1:]
                expression = value['expression']
                default_contract = constant_value(expression, contracts)
                numeric_default = constant(expression, environment)
                if storage_field == 'u_bool' and numeric_default is not None:
                    default_contract = {'kind': 'bool-storage', 'value': bool(numeric_default), 'evidence': expression}
                elif storage_field == 'u_int' and numeric_default is not None:
                    default_contract = {'kind': 'integer-storage-literal', 'literal_value': numeric_default, 'evidence': expression}
                if default_contract.get('kind') == 'unresolved':
                    default_reasons.append(default_contract.get('reason', 'storage expression unresolved'))
            else:
                default_reasons.append('default union member/value initializer is unsupported')
                default_contract = {'kind': 'unresolved', 'reason': default_reasons[-1], 'evidence': default_expression}
        yield {'table_id': function['id'] + '::' + variable['name'],
               'table_entry_index': initializer['initializer_ordinal'], 'initializer_ordinal': initializer['initializer_ordinal'],
               'row_count': row_count, 'initializer_row_count': variable['initializer_row_count'],
               'table_count_resolution': 'resolved' if count_resolved else 'unresolved',
               'table_declarator': variable['declarator_expression'], 'table_array_dimensions': dimensions,
               'name': qstr[1] if qstr else None, 'flags': flags, 'fields': fields, 'initializer': initializer,
               'default_expression': default_expression, 'default_omitted': default is None,
               'default_storage_contract': default_contract, 'storage_field': storage_field,
               'row_resolution': 'unresolved' if reasons else 'resolved', 'row_unresolved': reasons,
               'default_resolution': 'unresolved' if default_reasons else 'storage-only',
               'default_unresolved': default_reasons, 'accepted_input': 'carrier/accepted-domain-unresolved',
               'python_parameter_default_resolution': 'unassessed parser flag/default interpretation',
               'source_span': initializer['source_span'], 'scope': function['id']}


def semantics(function, functions, errors, contracts, specialization=None, depth=0):
    events = function['events']
    result = {'function_id': function['id'], 'parameters': function['parameters'], 'inputs': [], 'argument_tables': [], 'parser_calls': [], 'returns': [], 'helper_propagation': [], 'destination_writes': [], 'branch_selection': [], 'input_unresolved': [], 'return_unresolved': [], 'frontend_unresolved': [], 'input_coverage': 'bounded helper constraints, not a complete accepted callable domain'}
    barriers = events.get('semantic_unassessed', [])
    if barriers:
        reasons = [barrier['reason'] for barrier in barriers]
        result.update(constructor_calls=[], syntax_semantic_barriers=barriers,
                      input_unresolved=reasons, return_unresolved=reasons,
                      frontend_unresolved=reasons,
                      input_resolution='unresolved', return_resolution='unresolved')
        return result
    result['return_unresolved'].extend(events.get('control_flow_unresolved', []))
    result['constructor_calls'] = [{'kind': CONSTRUCTORS[call_parts(call['expression'])[0]], 'expression': call['expression'], 'callee': call['callee'], 'source_span': call['source_span'], 'guards': call.get('guards', [])} for call in events['calls'] if call_parts(call['expression'])[0] in CONSTRUCTORS and qualified_template(call)]
    shadowed = set(function['parameters']) | {v['name'] for v in events['variables']}
    environment = {name: value for name, value in function['constants'].items() if name not in shadowed}
    for name, value in (specialization or {}).items():
        escaped = any(re.search(r'& ' + re.escape(name) + r'\b', c['expression']) for c in events['calls']) or any(re.search(r'& ' + re.escape(name) + r'\b', v['expression']) for v in events['variables'])
        written = escaped or any(re.search(r'\b' + re.escape(name) + r'\b', w.get('target', '')) for w in events['writes']) or any(v['name'] == name for v in events['variables'])
        if written: result['return_unresolved'].append('constant forwarding to written/shadowed parameter not evaluated')
        else: environment[name] = value
    for condition in events['conditions']:
        rejection = constant_rejection(condition['expression'], environment)
        if rejection: result['return_unresolved'].append('branch evaluator unresolved: ' + rejection)
    condition_values = {}
    def condition_value(expression):
        if expression not in condition_values:
            condition_values[expression] = constant(expression, environment)
        return condition_values[expression]
    def selected(event):
        for guard in event.get('guards', []):
            value = condition_value(guard['condition'])
            if value is not None and bool(value) != (guard['branch'] == 'then'): return False
        return True
    for variable in events['variables']:
        if variable.get('type_expression') != 'mp_arg_t': continue
        result['argument_tables'].extend(argument_table_rows(variable, function, environment, contracts))
    for call in events['calls']:
        if not selected(call): continue
        helper, args = call_parts(call['expression'])
        if helper in INPUT_HELPERS and qualified_template(call):
            contract = INPUT_HELPERS[helper]
            if helper == 'mp_get_buffer_raise' and len(args) >= 3:
                mode = constant(args[2], {})
                read_mode, write_mode = constant(contracts.get('buffer_read', ''), {}), constant(contracts.get('buffer_write', ''), {})
                contract = 'readable-buffer-capability' if mode == read_mode and read_mode is not None else 'writable-buffer-capability' if mode == write_mode and write_mode is not None else 'buffer-mode-unresolved'
            result['inputs'].append({'helper': helper, 'arguments': args, 'contract': contract, 'bindings': [constraint_binding(a, function) for a in args], 'callee': call['callee'], 'source_span': call['source_span'], 'scope': function['id']})
        if helper == 'mp_arg_parse_all' and qualified_template(call):
            result['parser_calls'].append({'arguments': args, 'source_span': call['source_span'], 'guards': call.get('guards', []), 'scope': function['id'], 'callee': call['callee']})
    for write in events['writes']:
        if not selected(write): continue
        assigned = re.match(r'dest \[ (\d+) \] = (.*)$', write['expression'])
        if assigned:
            result['destination_writes'].append({'destination_index': int(assigned[1]), 'expression': assigned[2], 'value_contract': return_contract(assigned[2], function, contracts, write['source_span']), 'source_span': write['source_span'], 'guards': write.get('guards', []), 'scope': function['id']})
    for returned in events['returns']:
        result['branch_selection'].append({'source_span': returned['source_span'], 'selected': selected(returned), 'guards': returned.get('guards', []), 'evaluated_conditions': [condition_value(g['condition']) for g in returned.get('guards', [])]})
        if not selected(returned): continue
        helper, args = call_parts(returned['expression'])
        contract = return_contract(returned['expression'], function, contracts, returned['source_span'])
        for element in contract.get('elements', []):
            if element['kind'] != 'argument-table': continue
            index_name = 'ARG_' + element['argument']
            index = constant(index_name, environment)
            parsers = [p for p in result['parser_calls'] if len(p['arguments']) >= 2 and p['arguments'][-1] == 'args' and not p['guards'] and p['source_span']['end_line'] <= returned['source_span']['start_line']]
            carriers = [v for v in events['variables'] if v['name'] == 'args' and 'mp_arg_val_t' in v['expression']]
            modified = any(re.search(r'\bargs\b', w.get('target', '')) for w in events['writes'])
            if len(parsers) == 1 and len(carriers) == 1 and index is not None and not modified:
                table_id = function['id'] + '::' + parsers[0]['arguments'][-2]
                matches = [r for r in result['argument_tables'] if r['table_id'] == table_id and r['table_entry_index'] == index]
                if len(matches) == 1 and matches[0]['row_resolution'] == 'resolved' and matches[0]['table_count_resolution'] == 'resolved':
                    element.update({'table_id': table_id, 'table_entry_index': index, 'exported_argument': matches[0]['name'], 'parser_callee': parsers[0]['callee'], 'parser_source_span': parsers[0]['source_span'], 'storage_field': 'u_obj', 'authority': 'qualified parser destination, local enum index, independently traversed table row'})
                    continue
            element.update({'kind': 'unresolved', 'reason': 'tuple argument storage/table/parser identity or unchanged destination not established'})
        call = event_call(function, returned['expression'], returned['source_span'])
        candidates = functions.get(helper, [])
        if contract['kind'] == 'unresolved' and call and (call.get('callee') or {}).get('kind') == 'FUNCTION_DECL' and len(candidates) == 1 and depth < 2:
            callee = candidates[0]
            if call['callee'] != callee['declaration_identity']:
                result['returns'].append({**contract, 'source_span': returned['source_span']}); result['return_unresolved'].append('helper declaration does not resolve to selected function definition'); continue
            constant_bindings = {p: value for p, arg in zip(callee['parameters'], args) if (value := constant(arg, environment)) is not None}
            child = semantics(callee, functions, errors, contracts, constant_bindings, depth + 1)
            bindings = {p: {'expression': arg, 'direct_outer_parameter': direct_parameter(function, arg), 'constant': constant(arg, environment)} for p, arg in zip(callee['parameters'], args)}
            constant_sources = {name: evidence for name, evidence in function['constants'].items() if any(re.search(r'\b' + re.escape(name) + r'\b', arg) for arg in args)}
            result['helper_propagation'].append({'helper': callee['id'], 'arguments': args, 'parameter_bindings': bindings, 'constant_bindings': constant_bindings, 'constant_sources': constant_sources, 'facts': child})
            for child_return in child['returns']:
                mapped = remap_return(child_return, args, function, callee)
                result['returns'].append(mapped)
                if mapped['kind'] in ('unresolved', 'void-C-return'): result['return_unresolved'].append(mapped.get('reason', 'propagated return unresolved'))
            result['return_unresolved'].extend(child['return_unresolved'])
            result['frontend_unresolved'].extend(child['frontend_unresolved'])
            if child['argument_tables'] or child['parser_calls'] or child['inputs']:
                forwarded = len(bindings) == len(callee['parameters']) == len(args) and all(binding['direct_outer_parameter'] is not None or binding['constant'] is not None for binding in bindings.values())
                if forwarded:
                    if child['argument_tables']:
                        result['argument_tables'].extend({**row, 'via_helper': callee['id'], 'caller_parameter_bindings': bindings} for row in child['argument_tables'])
                    result['parser_calls'].extend({**row, 'via_helper': callee['id'], 'caller_parameter_bindings': bindings} for row in child['parser_calls'])
                    result['inputs'].extend({**row, 'via_helper': callee['id'], 'caller_parameter_bindings': bindings} for row in child['inputs'])
                else: result['input_unresolved'].append('helper constraints/table not lifted: operands are not direct parameters or proven constants')
        else:
            contract['source_span'] = returned['source_span']; contract['guards'] = returned.get('guards', [])
            result['returns'].append(contract)
            if contract['kind'] in ('unresolved', 'void-C-return'): result['return_unresolved'].append(contract.get('reason', 'return unresolved'))
            if any(e['kind'] in ('unresolved', 'void-C-return') for e in contract.get('elements', [])): result['return_unresolved'].append('tuple element relation unresolved')
    if not result['returns']: result['return_unresolved'].append('no applicable return contract extracted')
    if events['returns'] and not any(not r.get('guards') or all((value := condition_value(g['condition'])) is not None and bool(value) == (g['branch'] == 'then') for g in r.get('guards', [])) for r in events['returns']):
        result['return_unresolved'].append('conditional-only return coverage/fallthrough unassessed')
    if errors_at({'errors': errors}, function['source_span']): result['frontend_unresolved'].append('frontend error overlaps function region')
    result['input_unresolved'].append('complete accepted callable input domain unassessed')
    for axis in ('input_unresolved', 'return_unresolved', 'frontend_unresolved'):
        result[axis] = sorted(set(result[axis]))
    result['input_resolution'] = 'unresolved'
    result['return_resolution'] = 'unresolved' if result['return_unresolved'] or result['frontend_unresolved'] else 'bounded-resolved'
    result['status'] = 'unresolved-input-domain' if result['return_resolution'] == 'bounded-resolved' else 'unresolved'
    result['unresolved'] = sorted(set(result['input_unresolved'] + result['return_unresolved'] + result['frontend_unresolved']))
    return result


