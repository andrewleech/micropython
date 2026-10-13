"""Bounded MicroPython wrapper, dictionary and object binding normalization."""
from pathlib import Path
import re
from .frontend import canonical, constant
from .compiler import read_input, reconcile_inputs


def split_args(text):
    parts, depth, start = [], 0, 0
    tokens = text.split()
    for index, token in enumerate(tokens):
        if token in ('(', '[', '{'): depth += 1
        elif token in (')', ']', '}'): depth -= 1
        elif token == ',' and depth == 0:
            parts.append(' '.join(tokens[start:index])); start = index + 1
    parts.append(' '.join(tokens[start:]))
    return parts


def call_parts(expression):
    match = re.fullmatch(r'(\w+) \( (.*?)\)', expression)
    if not match:
        return None, []
    arguments = match[2].strip()
    return match[1], split_args(arguments) if arguments else []


def intersect(a, b):
    return bool(a and b) and a['source'] == b['source'] and a['start_line'] <= b['end_line'] and b['start_line'] <= a['end_line']


def errors_at(facts, location):
    return [{'reason': e['reason'], 'source_span': e.get('source_span')} for e in facts.get('errors', []) if intersect(e.get('source_span'), location)]


def strip_outer(expression, left='(', right=')'):
    """Remove ``left``/``right`` token pairs that enclose the whole remaining expression.

    A leading ``left`` encloses the rest when the nesting level it opens first closes at the final
    token, or never closes in unbalanced text."""
    tokens = expression.split()
    count = len(tokens)
    # First token at which the nesting level opened by each ``left`` closes again.
    closer, open_positions = {}, []
    for index, token in enumerate(tokens):
        if token == left:
            open_positions.append(index)
        elif token == right and open_positions:
            closer[open_positions.pop()] = index
    stripped = 0
    while (count - 2 * stripped >= 2 and tokens[stripped] == left and tokens[count - 1 - stripped] == right
           and closer.get(stripped, count) >= count - 1 - stripped):
        stripped += 1
    return ' '.join(tokens[stripped:count - stripped])


def address_reference(expression):
    expression = strip_outer(expression)
    cast = r'^\( (?:const )?(?:void|mp_obj_type_t|mp_obj_dict_t|mp_obj_module_t|struct _mp_obj_dict_t) \* \) (.*)$'
    while True:
        match = re.fullmatch(cast, expression)
        if not match:
            break
        expression = strip_outer(match[1])
    if expression.startswith('& '):
        target = strip_outer(expression[2:])
        if re.fullmatch(r'[A-Za-z_]\w*', target):
            return target
    return None


def initializer_members(nodes, initializer):
    """Designated members of an initializer list, resolved through the owner's ``initializer_nodes``."""
    members = {}
    if not initializer or initializer.get('syntax_kind') != 'initializer_list':
        return members
    for field in (nodes[position] for position in initializer.get('fields', [])):
        designators = [nodes[position] for position in field.get('designators', [])]
        match = re.fullmatch(r'\. (\w+)', designators[0]['expression']) if len(designators) == 1 else None
        if field.get('syntax_kind') == 'initializer_pair' and match:
            members.setdefault(match[1], []).append(nodes[field['value']] if field.get('value') is not None else None)
    return {name: values[0] for name, values in members.items() if len(values) == 1 and values[0] is not None}


def constant_value(expression, contracts):
    for key, kind, value in [('rom_none', 'None', None), ('none', 'None', None), ('rom_true', 'bool', True), ('true', 'bool', True), ('rom_false', 'bool', False), ('false', 'bool', False)]:
        if contracts.get(key) == expression:
            return {'kind': kind, 'value': value, 'evidence': expression, 'authority': 'actual compiler-expanded constant contract'}
    qstr = contracts.get('rom_qstr')
    if qstr:
        pattern = re.escape(qstr).replace(re.escape('MP_QSTR___name__'), r'MP_QSTR_(\w+)')
        matched = re.fullmatch(pattern, expression)
        if matched: return {'kind': 'str', 'qstr': matched[1], 'evidence': expression, 'authority': 'actual compiler-expanded ROM_QSTR contract'}
    integer = contracts.get('rom_int')
    if integer:
        pattern = re.escape(integer).replace(re.escape('7'), r'(-?\s*\d+)')
        matched = re.fullmatch(pattern, expression)
        if matched:
            value = constant(matched[1], {})
            if value is not None: return {'kind': 'int', 'value': value, 'evidence': expression, 'authority': 'actual compiler-expanded ROM_INT contract'}
    for key in ('rom_reference', 'obj_reference'):
        reference = contracts.get(key)
        if reference and reference.count('mp_type_type') == 1:
            matched = re.fullmatch(re.escape(reference).replace('mp_type_type', r'([A-Za-z_]\w*)'), expression)
            if matched:
                return {'kind': 'object-reference', 'target': matched[1], 'evidence': expression,
                        'authority': 'actual compiler-expanded object-reference contract'}
    return {'kind': 'unresolved', 'reason': 'unsupported constant expression', 'evidence': expression}


def wrapper(declaration, interfaces=(), contracts=None):
    contracts = contracts or {}
    type_name = declaration.get('type_expression')
    initializer = declaration.get('initializer')
    nodes = declaration.get('initializer_nodes', [])
    members = initializer_members(nodes, initializer)
    positional = [nodes[position] for position in initializer.get('fields', [])] if initializer else []
    base = members.get('base') or (positional[0] if positional and positional[0].get('syntax_kind') == 'initializer_list' else None)
    base_members = initializer_members(nodes, base)
    base_fields = [nodes[position] for position in base.get('fields', [])] if base else []
    base_type = base_members.get('type') or (base_fields[0] if len(base_fields) == 1 else None)
    reference = address_reference(base_type['expression']) if base_type else None
    function = members.get('fun')
    functions = initializer_members(nodes, function)
    if type_name == 'mp_obj_fun_builtin_fixed_t':
        fixed = re.fullmatch(r'mp_type_fun_builtin_([0123])', reference or '')
        if not fixed:
            return None
        arity = int(fixed[1])
        target = strip_outer(functions.get('_' + str(arity), {}).get('expression', ''))
        return {'minimum': arity, 'maximum': arity, 'keywords': False,
                'function': target if re.fullmatch(r'[A-Za-z_]\w*', target) else None,
                'native_wrapper_type': reference, 'descriptor': 'function'}
    if type_name == 'mp_obj_fun_builtin_var_t' and reference == 'mp_type_fun_builtin_var':
        signature = members.get('sig', {}).get('expression', '')
        outer = re.fullmatch(r'\( uint32_t \) (.*)', strip_outer(signature))
        packed = strip_outer(outer[1]) if outer else ''
        pattern = (r'\( \( \( uint32_t \) \( (.*?) \) \) << 17 \) \| '
                   r'\( \( \( uint32_t \) \( (.*?) \) \) << 1 \) \| '
                   r'\( \( (.*?) \) \? 1 : 0 \)')
        match = re.fullmatch(pattern, packed)
        storage = scalar_field(interfaces, type_name, 'sig', 'uint32_t')
        values = [constant(expression, declaration.get('initializer_constants', {})) for expression in match.groups()] if match else []
        if storage and len(values) == 3 and all(isinstance(value, int) for value in values):
            bits = ((values[0] << 17) | (values[1] << 1) | bool(values[2])) & 0xffffffff
            keywords = bool(bits & 1)
            member = 'kw' if keywords else 'var'
            target = strip_outer(functions.get(member, {}).get('expression', ''))
            if re.fullmatch(r'[A-Za-z_]\w*', target):
                return {'minimum': bits >> 17, 'maximum': (bits >> 1) & 65535, 'keywords': keywords,
                        'function': target, 'native_wrapper_type': reference, 'descriptor': 'function',
                        'signature_expression': signature, 'signature_storage': storage}
        return {'descriptor': 'function', 'unresolved': ['unsupported packed wrapper signature/storage/function binding'],
                'expression': declaration['expression']}
    if type_name == 'mp_rom_obj_static_class_method_t' and reference in ('mp_type_classmethod', 'mp_type_staticmethod'):
        underlying = function or (positional[1] if len(positional) == 2 else None)
        value = constant_value(underlying['expression'], contracts) if underlying else {}
        target = value.get('target') if value.get('kind') == 'object-reference' else None
        return {'descriptor': 'classmethod' if reference == 'mp_type_classmethod' else 'staticmethod',
                'underlying_object': target,
                **({'unresolved': ['descriptor underlying object reference is unqualified']} if not target else {})}
    return None


def dictionary_fields(expression):
    table = re.search(r'\. table = ([^,}]+)', expression)
    used = re.search(r'\. used = ([^,}]+)', expression)
    pointer = table[1].strip() if table else None
    cleaned = re.sub(r'\( (?:mp_map_elem_t|mp_rom_map_elem_t) \* \) ', '', pointer or '')
    cleaned = strip_outer(cleaned)
    target = re.match(r'(\w+)(.*)$', cleaned)
    return {'table': target[1] if target else None, 'offset_expression': '0' + target[2] if target else None, 'used_expression': used[1].strip() if used else None, 'pointer_expression': pointer}


def dictionary_window(dictionary, table):
    fields = dictionary.get('table_window', {})
    row_count = table.get('row_count')
    complete_rows = table.get('row_count_resolution') == 'resolved' and isinstance(row_count, int) and row_count == len(table.get('map_entries', []))
    offset = constant(fields.get('offset_expression') or '', {})
    used_expression = fields.get('used_expression') or ''
    name = re.escape(table['name'])
    array_size = r'sizeof \( ' + name + r' \) / sizeof \( \( ' + name + r' \) \[ 0 \] \)'
    used_expression = re.sub(array_size, str(row_count), used_expression) if complete_rows else used_expression
    used = constant(used_expression, {})
    valid = complete_rows and isinstance(offset, int) and not isinstance(offset, bool) and isinstance(used, int) and not isinstance(used, bool) and 0 <= offset <= row_count and 0 <= used <= row_count - offset
    return {**fields, 'offset': offset, 'used': used, 'underlying_row_count': row_count, 'status': 'resolved' if valid else 'unresolved', 'unresolved': [] if valid else ['dictionary pointer offset/used cannot be bounded against independently traversed rows']}


def scalar_field(interfaces, type_name, field_name, c_type):
    candidates = [interface for interface in interfaces if interface['name'] == type_name and interface['status'] == 'resolved']
    if len(candidates) != 1:
        return None
    fields = [field for field in candidates[0]['fields'] if field['name'] == field_name]
    if len(fields) != 1 or fields[0]['type_expression'] != c_type or fields[0]['declarator_expression'] != field_name:
        return None
    return {'aggregate_source': candidates[0]['source_span'], 'field': fields[0]}


def coalesce_definitions(definitions):
    """Join exact compatible internal tentative declarations to one initializer."""
    grouped = {}
    for definition in definitions:
        grouped.setdefault((definition['translation_unit'], definition['name']), []).append(definition)
    replacements, omitted = {}, set()
    for group in grouped.values():
        initialized = [definition for definition in group if definition.get('initialization_status') == 'initialized']
        if len(group) < 2 or len(initialized) != 1:
            continue
        owner = initialized[0]
        signature = (owner.get('declaration_specifiers'), owner.get('declarator_expression'))
        if owner['linkage'] != 'static' or not all(signature):
            continue
        tentative = [definition for definition in group if definition is not owner]
        if not all(definition['linkage'] == 'static'
                   and definition.get('initialization_status') == 'tentative'
                   and (definition.get('declaration_specifiers'), definition.get('declarator_expression')) == signature
                   for definition in tentative):
            continue
        joined = dict(owner)
        joined['tentative_declarations'] = [
            {key: definition[key] for key in ('id', 'source_span', 'declaration_expression',
                                             'declaration_specifiers', 'declarator_expression')}
            for definition in tentative]
        joined['declaration_ownership'] = 'one initialized internal object with exact compatible same-compiled-TU tentative declarations'
        replacements[id(owner)] = joined
        omitted.update(id(definition) for definition in tentative)
    return [replacements.get(id(definition), definition) for definition in definitions
            if id(definition) not in omitted]


def definitions(declarations, functions, errors, physical, directives, origins, record):
    active = {origin for row, origin in zip(directives.splitlines(), origins) if row.strip()}
    sources = {}
    result = []
    for declaration in declarations:
        d = dict(declaration)
        source = Path(d['source_span']['source'])
        if (str(source), d['source_span']['start_line']) in active and source.is_file():
            if source not in sources:
                content, snapshot = read_input(source)
                expected = record['dependencies'].get(str(source))
                if expected is None or snapshot['sha256'] != expected:
                    raise RuntimeError(f'definition source differs from compiler dependency snapshot: {source}')
                record['input_snapshots'] = reconcile_inputs([
                    ('compiler inputs', record['input_snapshots']), ('definition source', {snapshot['path']: snapshot})])
                sources[source] = content.decode().splitlines(keepends=True)
            occurrence = ''.join(sources[source][d['source_span']['start_line'] - 1:])
            if re.match(r'\s*(?:static\s+)?[A-Z][A-Z0-9_]+\s*\(', occurrence) and ';' in occurrence:
                d['source_span'] = {**d['source_span'], 'end_line': d['source_span']['start_line'] + occurrence[:occurrence.find(';') + 1].count('\n')}
        value = {k: d[k] for k in ('name', 'linkage', 'source_span')}
        value['declaration_expression'] = d['expression']
        value['declaration_specifiers'] = d.get('declaration_specifiers')
        value['declarator_expression'] = d.get('declarator_expression')
        value['initialization_status'] = 'initialized' if d.get('initializer') is not None else 'tentative'
        value['initializer_rows'] = d.get('initializer_rows', [])
        value['initializer_nodes'] = d.get('initializer_nodes', [])
        value['array_dimensions'] = d.get('array_dimensions', [])
        value['constant_environment'] = d.get('initializer_constants', {})
        value['translation_unit'] = physical
        value['id'] = physical + '::' + d['name'] + '@' + d['source_span']['source'] + ':' + str(d['source_span']['start_line'])
        value['errors'] = errors_at({'errors': errors}, d['source_span'])
        value['unresolved'] = ['frontend error overlaps definition'] if value['errors'] else []
        signature = wrapper(d, record.get('frontend', {}).get('aggregate_interfaces', []), record.get('contracts', {}))
        prefix = d['expression'].split('=', 1)[0]
        if signature:
            value.update({'kind': 'wrapper', 'callshape': signature})
        elif 'mp_rom_map_elem_t' in prefix:
            rows = d.get('initializer_rows', [])
            row_count = d.get('initializer_row_count')
            dimensions = d.get('array_dimensions', [])
            complete = d.get('initializer', {}).get('syntax_kind') == 'initializer_list' if d.get('initializer') else False
            complete = complete and isinstance(row_count, int) and row_count == len(rows) == len(d['map_entries']) and all(
                row.get('syntax_kind') == 'initializer_list' and row.get('initializer_ordinal') == index
                for index, row in enumerate(rows))
            if len(dimensions) != 1:
                complete = False
            elif dimensions[0]['expression']:
                complete = complete and constant(dimensions[0]['expression'], value['constant_environment']) == row_count
            value.update({'kind': 'map-table', 'map_entries': d['map_entries'], 'row_count': row_count,
                          'row_count_resolution': 'resolved' if complete else 'unresolved'})
            if not complete:
                value['unresolved'].append('complete positional map-table initializer/declared array extent unassessed')
        elif 'mp_module_delegation_entry_t' in prefix:
            value.update({'kind': 'module-delegation-table', 'map_entries': d['map_entries']})
        elif 'mp_obj_dict_t' in prefix and '. table =' in d['expression']:
            fields = dictionary_fields(d['expression'])
            value.update({'kind': 'dictionary', 'table_refs': [fields['table']] if fields['table'] else [], 'table_window': fields})
            if fields['table'] is None: value['unresolved'].append('dictionary table reference unresolved')
        elif re.search(r'\bmp_obj_(?:full_|empty_)?type_t\b', prefix) and '. name =' in d['expression'] and '& mp_type_type' in d['expression']:
            interfaces = record.get('frontend', {}).get('aggregate_interfaces', [])
            type_name = d.get('type_expression')
            members = {}
            nodes = d.get('initializer_nodes', [])
            for field in (nodes[position] for position in d.get('initializer', {}).get('fields', [])):
                designators = [nodes[position] for position in field.get('designators', [])]
                match = re.fullmatch(r'\. (\w+)', designators[0]['expression']) if len(designators) == 1 else None
                if field.get('syntax_kind') != 'initializer_pair' or not match:
                    value['unresolved'].append('type initializer member designator unassessed')
                    continue
                members.setdefault(match[1], []).append(nodes[field['value']] if field.get('value') is not None else None)
            fields = {name: rows[0] for name, rows in members.items() if len(rows) == 1 and rows[0] is not None}
            if any(len(rows) != 1 for rows in members.values()):
                value['unresolved'].append('repeated type initializer member assignments unassessed')
            vector = fields.get('slots')
            operands = [nodes[position] for position in vector.get('fields', [])] if vector and vector.get('syntax_kind') == 'initializer_list' else []
            ordinary = vector is None or (vector.get('syntax_kind') == 'initializer_list' and all(
                operand.get('syntax_kind') != 'initializer_pair' for operand in operands))
            slots = []
            for name, field in fields.items():
                if not name.startswith('slot_index_'):
                    continue
                index = constant(field['expression'], value['constant_environment'])
                storage = scalar_field(interfaces, type_name, name, 'uint8_t')
                if storage and isinstance(index, int):
                    index &= 255
                else:
                    index = None
                if index == 0:
                    continue
                valid = ordinary and isinstance(index, int) and 1 <= index <= len(operands)
                slots.append({'slot': name.removeprefix('slot_index_'), 'index': index,
                              'index_expression': field['expression'], 'index_source': field['source_span'],
                              'storage_authority': storage, 'expression': operands[index - 1]['expression'] if valid else None})
                if not valid:
                    value['unresolved'].append('type slot index/vector ownership unassessed: ' + name)
            if not ordinary:
                value['unresolved'].append('non-positional type slot array initializer unassessed')
            if not slots and any(name in fields for name in ('locals_dict', 'make_new', 'attr', 'parent')):
                value['unresolved'].append('direct-field type slot layout is outside the selected slot-index layout')
            value.update({'kind': 'type', 'slots': slots, 'raw_type_members': members})
            locals_expression = next((slot['expression'] for slot in slots if slot['slot'] == 'locals_dict'), None)
            locals_reference = address_reference(locals_expression) if locals_expression else None
            value['dictionary_refs'] = [locals_reference] if locals_reference else []
            if locals_expression and not locals_reference:
                value['unresolved'].append('locals dictionary pointer is not a qualified direct object address')
            typename = re.fullmatch(r'MP_QSTR_(\w+)', fields.get('name', {}).get('expression', ''))
            value['python_name'] = typename[1] if typename else None
            flag_field = fields.get('flags')
            value['type_flags_expression'] = flag_field['expression'] if flag_field else None
            flag_value = constant(flag_field['expression'], value['constant_environment']) if flag_field else 0
            value['type_flags_storage'] = scalar_field(interfaces, type_name, 'flags', 'uint16_t')
            value['type_flags_value'] = flag_value & 65535 if isinstance(flag_value, int) and value['type_flags_storage'] else None
            if value['type_flags_value'] is None:
                value['unresolved'].append('selected type flags storage/constant interpretation unassessed')
        elif 'mp_obj_module_t' in prefix and '. globals =' in d['expression']:
            members = initializer_members(d.get('initializer_nodes', []), d.get('initializer'))
            globals_expression = members.get('globals', {}).get('expression', '')
            globals_reference = address_reference(globals_expression)
            value.update({'kind': 'module', 'dictionary_refs': [globals_reference] if globals_reference else []})
            if not globals_reference:
                value['unresolved'].append('module globals pointer is not a qualified direct object address')
        elif 'uint16_t' in prefix and 'MP_QSTR_' in d['expression']:
            value.update({'kind': 'qstr-key-table', 'keys': re.findall(r'MP_QSTR_(\w+)', d['expression'])})
        else:
            value.update({'kind': 'object', 'value_expression': d['expression'].split('=', 1)[1].strip() if '=' in d['expression'] else None})
            value['unresolved'].append('object value/type family contract unassessed')
            if value['value_expression'] is None: value['unresolved'].append('object initialization contract unassessed')
        result.append(value)
    return coalesce_definitions(result)


def registration_records(directives, origins, physical, record):
    from .frontend import owned, span
    result = []
    pattern = r'\b(MP_REGISTER_(?:EXTENSIBLE_MODULE|MODULE(?:_DELEGATION)?))\s*\((.*?)\)\s*;'
    for match in re.finditer(pattern, directives, re.S):
        row = directives.count('\n', 0, match.start()) + 1
        location = span(origins, row, row + match[0].count('\n'))
        if owned(location['source'], physical, record):
            operands = split_args(canonical(match[2]))
            result.append({'macro': match[1], 'operands': operands, 'module': operands[0] if match[1] == 'MP_REGISTER_MODULE_DELEGATION' else operands[1] if len(operands) > 1 else None, 'handler': operands[1] if match[1] == 'MP_REGISTER_MODULE_DELEGATION' and len(operands) > 1 else None, 'source_span': location, 'authority': 'actual compiler directives-only selected occurrence'})
    return result


def base_attribute_policy(container, facts):
    handlers = []
    for slot in container.get('slots', []):
        if slot['slot'] != 'attr': continue
        identifiers = re.findall(r'\b[A-Za-z_]\w*\b', slot['expression'] or '')
        name = identifiers[-1] if identifiers else None
        candidates = [f for f in facts.get('functions', []) if f['name'] == name]
        handler = candidates[0] if len(candidates) == 1 else None
        handlers.append({'translation_unit': facts['translation_unit'], 'name': name, 'source_span': handler['source_span'] if handler else None, 'tested_attributes': handler.get('handler_attributes', []) if handler else []})
    names = {h['name'] for h in handlers}
    parent = [s for s in container.get('slots', []) if s['slot'] == 'parent']
    gaps = list(container.get('unresolved', []))
    if names & {'type_attr', 'mp_obj_instance_attr', 'mp_obj_class_attr'}:
        kind = 'generic-class-and-parent-lookup'
        gaps.append('receiving class locals/parent lookup is not the container static table; special attribute results unresolved')
    elif handlers:
        kind = 'type-specific-handler'
        gaps.append('handler assignment/result contracts unassessed')
    elif parent:
        kind = 'closed-local-table-with-parent-gap'
        gaps.append('parent attribute surface unassessed')
    else:
        kind = 'closed-table'
    return {'kind': kind, 'handlers': handlers, 'parent_slots': parent, 'provider': None, 'unknown_attribute_return': None, 'unresolved': gaps}


def row_pair(entry, contracts):
    fields = split_args(strip_outer(entry['expression'], '{', '}'))
    if len(fields) != 2: return None
    key = constant_value(fields[0], contracts)
    if key.get('kind') != 'str' or 'qstr' not in key: return None
    return key['qstr'], fields[1]


def materialize(inputs, resolve, selected=None, present=None, resolve_function=None):
    """One object-binding path for TU and selected-link views, preserving gaps."""
    linked = selected is not None
    bindings, containers, resolutions, unassessed_entries = [], [], [], []
    runtime_module, runtime_status = resolve('mp_type_module', '<runtime-module-framework>') if linked else (None, 'translation-unit-configuration-only')
    for facts in inputs:
        for original in facts['definitions']:
            if original['kind'] not in ('type', 'module'): continue
            if linked and (facts['translation_unit'] not in selected or not present(facts, original)): continue
            container = dict(original)
            policy = base_attribute_policy(container, facts)
            coverage = facts.get('coverage', {})
            if coverage.get('owned_declarations') == 'incomplete': policy['unresolved'].append('translation unit has own-source errors; lost declarations unassessed')
            local_bindings = []
            for reference in container.get('dictionary_refs', []):
                resolved, status = resolve(reference, facts['translation_unit'])
                resolutions.append({'from': container['id'], 'name': reference, 'status': status, 'definition': resolved[1]['id'] if resolved else None})
                if not resolved:
                    policy['unresolved'].append('dictionary ownership ' + status); continue
                dictionary_facts, dictionary = resolved
                if dictionary_facts.get('coverage', {}).get('owned_declarations') == 'incomplete': policy['unresolved'].append('dictionary translation unit own-source coverage incomplete')
                policy['unresolved'].extend(dictionary.get('unresolved', []))
                table_reference = dictionary.get('table_window', {}).get('table')
                table_resolved, table_status = resolve(table_reference, dictionary_facts['translation_unit']) if table_reference else (None, 'missing-table-reference')
                if not table_resolved:
                    policy['unresolved'].append('table ownership ' + table_status); continue
                table_facts, table = table_resolved
                if table_facts.get('coverage', {}).get('owned_declarations') == 'incomplete': policy['unresolved'].append('table translation unit own-source coverage incomplete')
                window = dictionary_window(dictionary, table)
                policy['unresolved'].extend(table.get('unresolved', []) + window['unresolved'])
                if window['status'] != 'resolved': continue
                rows = table.get('map_entries', [])[window['offset']:window['offset'] + window['used']]
                for index, entry in enumerate(rows):
                    pair = row_pair(entry, table_facts.get('contracts', {}))
                    if pair is None:
                        reason = 'selected map entry key/value shape unresolved'
                        policy['unresolved'].append(reason)
                        unassessed_entries.append({'container': container['id'], 'table': table['id'],
                                                   'table_entry_index': window['offset'] + index,
                                                   'source': entry['source_span'], 'expression': entry['expression'],
                                                   'reason': reason})
                        continue
                    export, expression = pair
                    value = constant_value(expression, table_facts.get('contracts', {}))
                    target = value.get('target') if value['kind'] == 'object-reference' else None
                    identity = {'translation_unit': table_facts['translation_unit'], 'source_span': entry['source_span'], 'entry_index': index, 'table_entry_index': window['offset'] + index, 'object': target if target else 'constant:' + expression, 'container': container['name'], 'container_translation_unit': facts['translation_unit'], 'dictionary': dictionary['name'], 'table': table['name']}
                    identity['export'] = export
                    unresolved = list(container.get('unresolved', []) + dictionary.get('unresolved', []) + table.get('unresolved', []))
                    field_errors = {'container': container.get('errors', []), 'dictionary': dictionary.get('errors', []), 'table': table.get('errors', []), 'entry': errors_at(table_facts, entry['source_span']), 'wrapper': []}
                    for participant in (facts, dictionary_facts, table_facts):
                        if participant.get('coverage', {}).get('owned_declarations') == 'incomplete': unresolved.append('ownership translation unit own-source coverage incomplete: ' + participant['translation_unit'])
                    shape, semantic, object_status = None, None, 'immediate-constant' if not target else 'unresolved'
                    if target:
                        wrapper_resolved, object_status = resolve(target, table_facts['translation_unit'])
                        if wrapper_resolved:
                            wrapper_facts, object_definition = wrapper_resolved
                            unresolved.extend(object_definition.get('unresolved', []))
                            if wrapper_facts.get('coverage', {}).get('owned_declarations') == 'incomplete': unresolved.append('wrapper translation unit own-source coverage incomplete')
                            field_errors['wrapper'] = list(object_definition.get('errors', []))
                            identity['object_source_span'] = object_definition['source_span']
                            identity['object_id'] = object_definition['id']
                            identity['object_kind'] = object_definition['kind']
                            if object_definition['kind'] == 'wrapper':
                                shape = dict(object_definition['callshape'])
                                descriptor = shape.get('descriptor')
                                if descriptor in ('classmethod', 'staticmethod') and container['kind'] != 'type':
                                    unresolved.append('module attribute lookup does not bind descriptor object as a callable')
                                    shape = {'descriptor': descriptor, 'underlying_object': shape.get('underlying_object'), 'callable': False, 'implicit_argument_offset': None}
                                else:
                                    if shape.get('underlying_object'):
                                        underlying, underlying_status = resolve(shape['underlying_object'], wrapper_facts['translation_unit'])
                                        if underlying:
                                            underlying_facts, underlying_definition = underlying
                                            if underlying_definition.get('kind') == 'wrapper':
                                                shape = {**underlying_definition['callshape'], 'descriptor': descriptor, 'underlying_object': shape['underlying_object']}
                                            else:
                                                unresolved.append('descriptor underlying object is not a supported callable wrapper')
                                            unresolved.extend(underlying_definition.get('unresolved', []))
                                            field_errors['wrapper'].extend(underlying_definition.get('errors', []))
                                            wrapper_facts = underlying_facts
                                        else: unresolved.append('descriptor underlying ownership ' + underlying_status)
                                    shape['implicit_argument_offset'] = 1 if container['kind'] == 'type' and descriptor != 'staticmethod' else 0
                                    if resolve_function:
                                        function, function_status = resolve_function(shape.get('function'), wrapper_facts['translation_unit'])
                                        candidates = [function[1]] if function else []
                                        shape['function_ownership'] = function_status
                                    else:
                                        candidates = [f for f in wrapper_facts.get('functions', []) if f['name'] == shape.get('function')]
                                    if len(candidates) == 1:
                                        semantic = candidates[0]['semantics']
                                        if semantic.get('frontend_unresolved'): unresolved.append('wrapper function has frontend errors')
                                    else: unresolved.append('wrapper function missing, ambiguous or unallocated')
                            elif object_definition['kind'] not in ('type', 'module'):
                                unresolved.append('unsupported exported object family ' + object_definition['kind'])
                        else: unresolved.append('export object ownership ' + object_status)
                    elif value['kind'] == 'unresolved': unresolved.append(value['reason'])
                    if any(field_errors.values()): unresolved.append('frontend error overlaps binding ownership field')
                    if coverage.get('owned_declarations') == 'incomplete': unresolved.append('translation unit own-source coverage incomplete')
                    presence = 'selected-linked-owner' if linked else 'compiler-preprocessed-occurrence'
                    availability = {'status': presence, 'presence': presence, 'authority': 'qualified container/dictionary window/initializer entry' + (' and selected object/link-image ownership' if linked else '; not a compilation or link proof'), 'dictionary_ownership': status, 'object_ownership': object_status}
                    if linked: availability['container_object'] = selected[facts['translation_unit']]['object']
                    if unresolved: availability['status'] = 'unresolved-owned-occurrence'
                    local_bindings.append({'identity': identity, 'callshape': shape, 'semantics': semantic, 'value_contract': value if not target else {'kind': 'object-reference', 'target': target}, 'dictionary_window': window, 'availability': availability, 'field_errors': field_errors, 'unresolved': sorted(set(unresolved))})
                    if any(field_errors[k] for k in ('container', 'dictionary', 'table', 'entry')): policy['unresolved'].append('frontend error overlaps static attribute inventory ownership')
                    if export == '__getattr__' and container['kind'] == 'module':
                        runtime_contracts = runtime_module[0].get('contracts', {}) if runtime_module else facts.get('contracts', {})
                        gate = constant(runtime_contracts.get('module_getattr', ''), {})
                        if linked and not runtime_module: gate = None
                        policy['getattr_feature'] = {'value': gate, 'authority': runtime_status, 'runtime_definition': runtime_module[1]['id'] if runtime_module else None}
                        if gate != 0 and (target or value['kind'] == 'unresolved'):
                            proven_callable = shape is not None and shape.get('callable') is not False
                            policy['kind'] = 'dynamic-provider' if gate == 1 and proven_callable else 'potential-dynamic-provider-unassessed'
                            policy['provider'] = target or expression
                            policy['unknown_attribute_return'] = 'Any'
                            policy['unresolved'].append('dynamic provider feature/callability/domain/results unassessed' if policy['kind'] != 'dynamic-provider' else 'dynamic attribute domain and results unassessed')
                        else:
                            policy['non_dispatching_getattr_entry'] = 'feature disabled' if gate == 0 else 'known non-callable constant'
            for binding in local_bindings:
                if sum(b['identity']['export'] == binding['identity']['export'] for b in local_bindings) > 1:
                    binding['unresolved'].append('ambiguous repeated export in selected container')
                    binding['availability']['status'] = 'unresolved-ambiguous-export'
                    policy['unresolved'].append('ambiguous repeated export in selected container')
            registrations = [r for r in facts.get('registrations', []) if r['module'] == container['name']]
            container['registrations'] = registrations
            delegation = [r for r in registrations if r['macro'] == 'MP_REGISTER_MODULE_DELEGATION']
            extensible = [r for r in registrations if r['macro'] == 'MP_REGISTER_EXTENSIBLE_MODULE']
            if delegation:
                policy['kind'] = 'registered-delegation-selection-unassessed'
                policy['provider'] = delegation[0]['handler']; policy['unknown_attribute_return'] = 'Any'
                policy['unresolved'].append('registered delegation dispatch selection and returned attributes unassessed')
            container['import_policy'] = {'kind': 'extensible-contributor-gap' if extensible else 'ordinary-builtin-registration' if registrations else 'registration-unassessed', 'unresolved': ['filesystem/frozen contributor selection unassessed'] if extensible else []}
            if extensible:
                policy['unresolved'].append('extensible import contributors unassessed; builtin table is not the complete import surface')
            policy['unresolved'] = sorted(set(policy['unresolved']))
            if policy['unresolved'] and policy['kind'] == 'closed-table': policy['kind'] = 'closed-table-with-coverage-gap'
            container['attribute_policy'] = policy
            containers.append(container); bindings.extend(local_bindings)
    return {'bindings': bindings, 'containers': containers, 'ownership_resolutions': resolutions,
            'unassessed_entries': unassessed_entries}
