"""Bounded C syntax facts from the selected build compiler expansion."""
import re
from .gnu import adapt_source, restore_text

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|0[xX][0-9a-fA-F]+(?:[uUlL]+)?|\d+(?:[uUlL]+)?|==|!=|<=|>=|->|&&|\|\||<<|>>|\+=|-=|\+\+|--|[^\s]')
CANONICAL_TOKEN = re.compile(r'/\*.*?\*/|//[^\n]*|' + TOKEN.pattern, flags=re.S)


def canonical(text):
    return ' '.join(match[0] for match in CANONICAL_TOKEN.finditer(text)
                    if not match[0].startswith(('/*', '//')))

def constant_rejection(expression, environment):
    tokens = TOKEN.findall(expression)
    comparisons = {'==', '!=', '<', '<=', '>', '>='}
    if any(re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*[uU][uUlL]*', token) for token in tokens):
        return 'unsigned literal conversion/width unassessed'
    if comparisons.intersection(tokens) and {'&', '|', '^'}.intersection(tokens):
        return 'mixed C bitwise/comparison precedence unassessed'
    if comparisons.intersection(tokens) and '-' in tokens:
        return 'negative operand or subtraction signedness in C comparison unassessed'
    for name in tokens:
        value = environment.get(name)
        if isinstance(value, dict) and value.get('unresolved'): return value['unresolved']
        value = value.get('value') if isinstance(value, dict) else value
        if isinstance(value, int) and (value < 0 or value > 0x7fffffff):
            return 'integral operand C signedness/width unassessed'
    return None



def constant(expression, environment):
    """Evaluate bounded nonnegative C integers using C operator precedence."""
    if constant_rejection(expression, environment):
        return None
    tokens = TOKEN.findall(expression)
    position = 0
    precedence = {'||': 1, '&&': 2, '|': 3, '^': 4, '&': 5,
                  '==': 6, '!=': 6, '<': 7, '<=': 7, '>': 7, '>=': 7,
                  '<<': 8, '>>': 8, '+': 9, '-': 9, '*': 10, '/': 10, '%': 10}
    def atom():
        nonlocal position
        if position >= len(tokens): raise ValueError('missing operand')
        token = tokens[position]; position += 1
        if token == '(':
            value = expression_at(1)
            if position >= len(tokens) or tokens[position] != ')': raise ValueError('missing closer')
            position += 1
            return value
        if token in ('+', '!'):
            value = atom()
            return value if token == '+' else int(not value)
        if re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)[lL]*', token):
            literal = re.sub(r'[lL]+$', '', token)
            value = int(literal, 16 if literal.lower().startswith('0x') else 8 if len(literal) > 1 and literal.startswith('0') else 10)
        else:
            value = environment.get(token)
            if isinstance(value, dict): value = value.get('value')
        if not isinstance(value, (int, bool)) or not 0 <= value <= 0x7fffffff: raise ValueError('unbounded operand')
        return int(value)
    def expression_at(level):
        nonlocal position
        left = atom()
        while position < len(tokens) and precedence.get(tokens[position], 0) >= level:
            operator = tokens[position]; position += 1
            right = expression_at(precedence[operator] + 1)
            if operator == '+': value = left + right
            elif operator == '-': value = left - right
            elif operator == '*': value = left * right
            elif operator == '/':
                if not right or left % right: raise ValueError('division domain')
                value = left // right
            elif operator == '%': value = left % right
            elif operator in ('<<', '>>'):
                if not 0 <= right <= 30: raise ValueError('shift domain')
                value = left << right if operator == '<<' else left >> right
            elif operator == '|': value = left | right
            elif operator == '&': value = left & right
            elif operator == '^': value = left ^ right
            elif operator == '&&': value = int(bool(left) and bool(right))
            elif operator == '||': value = int(bool(left) or bool(right))
            else: value = int({'==': left == right, '!=': left != right, '<': left < right, '<=': left <= right, '>': left > right, '>=': left >= right}[operator])
            if not 0 <= value <= 0x7fffffff: raise ValueError('unbounded result')
            left = value
        return left
    try:
        value = expression_at(1)
        return value if position == len(tokens) else None
    except (ValueError, ArithmeticError, TypeError):
        return None


def span(origins, start, end):
    a = origins[min(max(start - 1, 0), len(origins) - 1)]
    b = origins[min(max(end - 1, 0), len(origins) - 1)]
    return {'source': a[0], 'start_line': a[1], 'end_line': b[1] if a[0] == b[0] else a[1]}


def owned(source, physical, record):
    return source == physical or source in record.get('project_dependencies', [])


def return_expression(text):
    tokens = text.split()
    if tokens and tokens[0] == 'return': tokens = tokens[1:]
    if tokens and tokens[-1] == ';': tokens = tokens[:-1]
    return ' '.join(tokens)


def function_id(physical, name, location):
    return physical + '::' + name + '@' + location['source'] + ':' + str(location['start_line'])


def syntax_adapter(code, origins, physical, record):
    from tree_sitter import Language, Parser
    import tree_sitter_c
    original_data = code.encode()
    parse_code, syntax_evidence = adapt_source(code, origins)
    data = parse_code.encode()
    tree = Parser(Language(tree_sitter_c.language())).parse(data)
    def walk(n):
        yield n
        for child in n.children: yield from walk(child)
    def raw(n): return restore_text(original_data, syntax_evidence, n.start_byte, n.end_byte) if n else ''
    def text(n): return canonical(raw(n))
    def selected_constants(expression, environment):
        names = {token for token in TOKEN.findall(expression) if re.fullmatch(r'[A-Za-z_]\w*', token)}
        return {name: environment[name] for name in sorted(names) if name in environment}
    def loc(n): return span(origins, n.start_point.row + 1, n.end_point.row + 1)
    def adapted_expressions(node, *, kind=None, barriers=False):
        result, seen = [], set()
        for replacement in syntax_evidence['replacements']:
            expression = replacement.get('expression', {})
            bounds = expression.get('source_span', {}).get('expanded_span', {})
            start, end = bounds.get('start_byte'), bounds.get('end_byte')
            if start is None or end is None or not (node.start_byte < end and start < node.end_byte):
                continue
            if kind is not None and expression.get('kind') != kind:
                continue
            if barriers and not replacement.get('semantic_barrier'):
                continue
            identity = (expression.get('kind'), start, end)
            if identity not in seen:
                seen.add(identity)
                result.append((replacement, expression))
        return result
    def name_of(declarator):
        if declarator is None: return ''
        if declarator.type in ('identifier', 'field_identifier', 'type_identifier'): return text(declarator)
        child = declarator.child_by_field_name('declarator')
        if child: return name_of(child)
        for candidate in declarator.named_children:
            value = name_of(candidate)
            if value: return value
        return ''
    def declarators(node):
        declared = node.children_by_field_name('declarator')
        return [c for c in node.named_children if c.type == 'init_declarator' or c in declared or c.type in ('pointer_declarator', 'array_declarator', 'function_declarator', 'identifier')]
    def parameters(node):
        legacy = adapted_expressions(node, kind='old_style_function_header')
        if legacy:
            return legacy[0][1]['parameter_names']
        decl = node.child_by_field_name('declarator')
        while decl and decl.type != 'function_declarator': decl = decl.child_by_field_name('declarator')
        listing = decl.child_by_field_name('parameters') if decl else None
        result = []
        for param in listing.named_children if listing else []:
            if param.type == 'parameter_declaration':
                name = name_of(param.child_by_field_name('declarator'))
                if name or text(param) != 'void': result.append(name)
            elif param.type == 'identifier':
                result.append(text(param))
        return result
    def point_for_name(node, name):
        identifier = next((n for n in walk(node) if n.type == 'identifier' and text(n) == name), node)
        location = loc(identifier)
        return {**location, 'end_line': location['start_line']}


    def scope_node(node):
        parent = node.parent
        while parent and parent.type not in ('compound_statement', 'function_definition', 'for_statement'):
            parent = parent.parent
        return parent or node

    def metadata(node):
        scope = scope_node(node)
        loops = []
        parent = node.parent
        while parent:
            if parent.type in ('for_statement', 'while_statement', 'do_statement'):
                loops.append(parent.start_byte)
            parent = parent.parent
        return {'byte_start': node.start_byte, 'byte_end': node.end_byte,
                'scope_id': scope.start_byte, 'scope_start': scope.start_byte,
                'scope_end': scope.end_byte, 'loops': loops}

    def binding_scope(node):
        parents = []
        parent = node.parent
        while parent:
            parents.append(parent)
            parent = parent.parent
        if node.type == 'parameter_declaration':
            scope = next((parent for parent in parents if parent.type == 'function_definition'), None)
            scope = scope or next((parent for parent in parents if parent.type == 'function_declarator'), None)
        else:
            scope = next((parent for parent in parents if parent.type in
                          ('compound_statement', 'function_definition', 'for_statement')), None)
        return (scope.start_byte, scope.end_byte) if scope else (0, len(data))

    ordinary_bindings = {}
    for declaration in walk(tree.root_node):
        if declaration.type in ('type_definition', 'declaration'):
            names = declaration.children_by_field_name('declarator')
        elif declaration.type in ('parameter_declaration', 'function_definition'):
            names = [declaration.child_by_field_name('declarator')]
        elif declaration.type == 'enumerator':
            names = [declaration.child_by_field_name('name')]
        else:
            continue
        for declarator in names:
            name = name_of(declarator)
            if not name:
                continue
            start, end = binding_scope(declaration)
            ordinary_bindings.setdefault(name, []).append({
                'name': name, 'typedef': declaration.type == 'type_definition',
                'declaration_id': declarator.start_byte, 'source_span': loc(declaration),
                'visible_start': declarator.end_byte if declaration.type == 'type_definition' else declarator.start_byte,
                'scope_start': start, 'scope_end': end})

    def typedef_cast(node):
        if node.type != 'call_expression':
            return None
        function = node.child_by_field_name('function')
        if not function or function.type != 'parenthesized_expression':
            return None
        identifiers = [child for child in function.named_children if child.type != 'comment']
        arguments = node.child_by_field_name('arguments')
        operands = [child for child in arguments.named_children if child.type != 'comment'] if arguments else []
        if len(identifiers) != 1 or identifiers[0].type != 'identifier' or len(operands) != 1:
            return None
        name = text(identifiers[0])
        bindings = [binding for binding in ordinary_bindings.get(name, [])
                    if binding['visible_start'] <= node.start_byte
                    and binding['scope_start'] <= node.start_byte < binding['scope_end']]
        selected = max(bindings, key=lambda binding: (binding['scope_start'], binding['visible_start'])) if bindings else None
        if not selected or not selected['typedef']:
            return None
        authority = {'resolution': 'source-resolved', 'name': name,
                     'declaration_id': selected['declaration_id'], 'source_span': selected['source_span']}
        return identifiers[0], operands[0], authority

    def expression_fact(node, depth=0):
        if node is None:
            return None
        fact = {'kind': node.type, 'expression': text(node), **metadata(node)}
        if depth >= 24:
            fact['unresolved'] = 'expression nesting exceeds bounded def/use model'
            return fact
        cast = typedef_cast(node)
        if cast:
            type_node, value_node, authority = cast
            fact.update(kind='cast_expression', type={
                'kind': 'type_descriptor', 'expression': text(type_node), **metadata(type_node)},
                value=expression_fact(value_node, depth + 1), typedef_authority=authority)
            return fact
        operator = node.child_by_field_name('operator')
        if operator is not None:
            fact['operator'] = text(operator)
        fields = {
            'call_expression': ('function', 'arguments'),
            'subscript_expression': ('argument', 'index'),
            'field_expression': ('argument', 'field'),
            'pointer_expression': ('argument',),
            'unary_expression': ('argument',),
            'cast_expression': ('type', 'value'),
            'assignment_expression': ('left', 'right'),
            'binary_expression': ('left', 'right'),
            'comma_expression': ('left', 'right'),
            'conditional_expression': ('condition', 'consequence', 'alternative'),
        }.get(node.type, ())
        for field in fields:
            child = node.child_by_field_name(field)
            if child:
                fact[field] = expression_fact(child, depth + 1)
        if node.type in ('argument_list', 'initializer_list', 'parenthesized_expression', 'compound_literal_expression', 'concatenated_string'):
            fact['children'] = [expression_fact(child, depth + 1) for child in node.named_children if child.type != 'comment']
        operator = node.child_by_field_name('operator')
        if operator:
            fact['operator'] = text(operator)
        elif node.type in ('pointer_expression', 'field_expression'):
            fact['operator'] = next((text(child) for child in node.children if not child.is_named), '')
        return fact
    def initializer_fact(node):
        fact = {'expression': text(node), 'raw_expression': raw(node),
                'source_span': loc(node), 'syntax_kind': node.type}
        if node.type == 'initializer_list':
            fact['fields'] = [initializer_fact(child) for child in node.named_children if child.type != 'comment']
        elif node.type == 'initializer_pair':
            value = node.child_by_field_name('value')
            fact['value'] = initializer_fact(value) if value else None
            fact['designators'] = [initializer_fact(child) for child in node.named_children if child != value and child.type != 'comment']
        return fact

    def variable_initializer(declaration, declarator):
        actual = declarator.child_by_field_name('declarator') if declarator.type == 'init_declarator' else declarator
        initializer = declarator.child_by_field_name('value') if declarator.type == 'init_declarator' else None
        arrays = [node for node in walk(actual) if node.type == 'array_declarator']
        rows = [dict(initializer_fact(child), initializer_ordinal=index) for index, child in enumerate(
            child for child in initializer.named_children if child.type != 'comment')] if initializer and initializer.type == 'initializer_list' else []
        return {'type_expression': text(declaration.child_by_field_name('type')),
                'declaration_specifiers': canonical(original_data[declaration.start_byte:actual.start_byte].decode('utf-8')),
                'declarator_expression': text(actual), 'array_dimensions': [
                    {'expression': text(node.child_by_field_name('size')), 'source_span': loc(node)} for node in arrays],
                'initializer': initializer_fact(initializer) if initializer else None,
                'initializer_rows': rows, 'initializer_row_count': len(rows),
                'initializer_constants': selected_constants(text(actual) + ' ' + text(initializer), constants)}

    def enum_constants(node, base):
        values = dict(base)
        for enum in walk(node):
            if enum.type != 'enum_specifier': continue
            previous = -1
            for child in walk(enum):
                if child.type != 'enumerator': continue
                expression = child.child_by_field_name('value')
                value = constant(text(expression), values) if expression else previous + 1 if previous is not None else None
                name = text(child.child_by_field_name('name'))
                scope_start, scope_end = binding_scope(child)
                values[name] = {'value': value, 'source_span': loc(child),
                                'visible_start': child.end_byte, 'scope_start': scope_start, 'scope_end': scope_end,
                                **({'unresolved': 'explicit enum expression or preceding implicit enum value unassessed'} if value is None else {})}
                previous = value
        return values

    def aggregate_interfaces():
        wanted = {'mp_arg_t', 'mp_arg_val_t', 'mp_obj_type_t', 'mp_obj_full_type_t', 'mp_obj_empty_type_t',
                  'mp_obj_fun_builtin_var_t'}
        globals_ = [node for node in tree.root_node.named_children
                    if node.type != 'function_definition' and owned(loc(node)['source'], physical, record)]
        aggregates = [candidate for node in globals_ for candidate in walk(node)
                      if candidate.type in ('struct_specifier', 'union_specifier')
                      and candidate.child_by_field_name('body')]
        result = []
        for node in globals_:
            if node.type != 'type_definition':
                continue
            alias = node.child_by_field_name('declarator')
            name = text(alias)
            if name not in wanted:
                continue
            type_node = node.child_by_field_name('type')
            fact = {'name': name, 'source_span': loc(node), 'status': 'unresolved', 'fields': [], 'reasons': []}
            if alias.type != 'type_identifier' or not type_node or type_node.type not in ('struct_specifier', 'union_specifier'):
                fact['reasons'].append('helper typedef is not a direct struct/union alias')
                result.append(fact)
                continue
            body = type_node.child_by_field_name('body')
            if body is None:
                tag = text(type_node.child_by_field_name('name'))
                matches = [candidate for candidate in aggregates
                           if candidate.type == type_node.type and text(candidate.child_by_field_name('name')) == tag]
                if len(matches) != 1:
                    fact['reasons'].append('helper aggregate tag body is missing or ambiguous')
                    result.append(fact)
                    continue
                type_node = matches[0]
                body = type_node.child_by_field_name('body')
            fact.update(status='resolved', kind=type_node.type, definition_source=loc(type_node))
            projected_fields = [expression for _, expression in
                                adapted_expressions(body, kind='unnamed_bitfield')]
            if projected_fields:
                fact.update(status='unresolved', syntax_source_gaps=projected_fields)
                fact['reasons'].append('unnamed bit-field syntax is retained as source evidence; aggregate storage/layout is unassessed')
            for field in body.named_children:
                if field.type != 'field_declaration':
                    continue
                for declarator in field.children_by_field_name('declarator'):
                    fact['fields'].append({'name': name_of(declarator), 'type_expression': text(field.child_by_field_name('type')),
                                           'declarator_expression': text(declarator), 'source_span': loc(field)})
            result.append(fact)
        return result

    def signature_reasons(node, end=None):
        end = node.end_byte if end is None else end
        original = restore_text(original_data, syntax_evidence, node.start_byte, end)
        reasons = []
        if (re.search(r'\b(?:__attribute(?:__)?|__declspec|__cdecl|__stdcall|__fastcall|__vectorcall|__thiscall|__regcall|__syscall|__pascal|asm|__asm(?:__)?)\b', original)
                or any(child.end_byte <= end and ('attribute' in child.type or child.type in
                                                 ('ms_call_modifier', 'ms_declspec_modifier', 'gnu_asm_expression'))
                       for child in walk(node))):
            reasons.append('signature attributes or calling convention are outside the bounded ABI witness')
        if any(expression.get('source_span', {}).get('expanded_span', {}).get('start_byte', end) < end
               for _, expression in adapted_expressions(node)):
            reasons.append('adapted signature syntax does not supply qualified ABI equivalence')
        return reasons

    def function_pointer_interfaces():
        result = []
        for node in tree.root_node.named_children:
            if node.type != 'type_definition' or not owned(loc(node)['source'], physical, record):
                continue
            for alias in node.children_by_field_name('declarator'):
                if not any(child.type == 'function_declarator' for child in walk(alias)):
                    continue
                start, end = binding_scope(node)
                fact = {'name': name_of(alias), 'status': 'unresolved', 'source_span': loc(node),
                        'declaration_id': alias.start_byte, 'visible_start': alias.end_byte,
                        'scope_start': start, 'scope_end': end,
                        'return_type_expression': text(node.child_by_field_name('type')),
                        'parameters': [], 'reasons': []}
                fact['reasons'].extend(signature_reasons(node))
                pointer = alias.child_by_field_name('declarator')
                if pointer and pointer.type == 'parenthesized_declarator':
                    children = [child for child in pointer.named_children if child.type != 'comment']
                    pointer = children[0] if len(children) == 1 else None
                target = pointer.child_by_field_name('declarator') if pointer else None
                if (alias.type != 'function_declarator' or not pointer or pointer.type != 'pointer_declarator'
                        or not target or target.type not in ('type_identifier', 'identifier')
                        or any(child.type == 'type_qualifier' for child in pointer.named_children)):
                    fact['reasons'].append('typedef is not a direct single function-pointer declarator')
                    result.append(fact)
                    continue
                listing = alias.child_by_field_name('parameters')
                parameters = [parameter for parameter in listing.named_children if parameter.type != 'comment'] if listing else []
                if not parameters:
                    fact['reasons'].append('function-pointer typedef lacks a prototype')
                if len(parameters) == 1 and text(parameters[0]) == 'void':
                    parameters = []
                for parameter in parameters:
                    declarator = parameter.child_by_field_name('declarator')
                    if (parameter.type != 'parameter_declaration' or
                            declarator and any(child.type in ('array_declarator', 'function_declarator')
                                               for child in walk(declarator))):
                        fact['reasons'].append('function-pointer parameter declarator is outside the bounded prototype')
                        continue
                    fact['parameters'].append({'name': name_of(declarator),
                                               'type_expression': text(parameter.child_by_field_name('type')),
                                               'declarator_expression': text(declarator),
                                               'declaration_expression': text(parameter), 'source_span': loc(parameter)})
                if not fact['return_type_expression']:
                    fact['reasons'].append('function-pointer return type is absent')
                if adapted_expressions(node, barriers=True):
                    fact['reasons'].append('function-pointer typedef contains unassessed syntax')
                if not fact['reasons']:
                    fact['status'] = 'resolved'
                result.append(fact)
        return result


    def function_constants(node, body):
        environment = enum_constants(body, constants)
        declarations = {}
        for enum in walk(body):
            if enum.type != 'enum_specifier':
                continue
            for child in walk(enum):
                if child.type == 'enumerator':
                    declarations.setdefault(text(child.child_by_field_name('name')), []).append(loc(child))
        for name, locations in declarations.items():
            if len(locations) > 1:
                environment[name] = {'value': None, 'source_span': locations[-1],
                                     'unresolved': 'repeated local enumerator names require lexical-scope binding',
                                     'conflicting_sources': locations}
        return selected_constants(text(node), environment)

    global_functions, constants = {}, {}
    for node in tree.root_node.named_children:
        if node.type == 'function_definition':
            name = name_of(node.child_by_field_name('declarator'))
            global_functions.setdefault(name, []).append({'name': name, 'kind': 'FUNCTION_DECL', 'source_span': point_for_name(node.child_by_field_name('declarator'), name)})
        elif node.type == 'declaration' and '=' not in text(node):
            for decl in declarators(node):
                name = name_of(decl.child_by_field_name('declarator') if decl.type == 'init_declarator' else decl)
                if name and re.search(r'\b' + re.escape(name) + r' \(', text(decl)):
                    global_functions.setdefault(name, []).append({'name': name, 'kind': 'FUNCTION_DECL', 'source_span': point_for_name(decl, name)})
        if node.type != 'function_definition' and owned(loc(node)['source'], physical, record):
            constants = enum_constants(node, constants)
    declarations, functions = [], []
    for node in tree.root_node.named_children:
        if not owned(loc(node)['source'], physical, record): continue
        if node.type == 'declaration':
            children = declarators(node)
            prefix = canonical(restore_text(original_data, syntax_evidence, node.start_byte, children[0].start_byte)) if children else ''
            for child in children:
                declared_name = name_of(child)
                if child.type != 'init_declarator' and declared_name and re.search(r'\b' + re.escape(declared_name) + r' \(', text(child)): continue
                if child.type != 'init_declarator' and 'extern' in prefix.split(): continue
                initializer = child.child_by_field_name('value') if child.type == 'init_declarator' else None
                entries = [{'expression': text(c), 'source_span': loc(c)} for c in initializer.named_children if c.type == 'initializer_list'] if initializer and initializer.type == 'initializer_list' else []
                declaration = child.child_by_field_name('declarator') if child.type == 'init_declarator' else child
                source_span = {**loc(node), 'end_line': loc(child)['end_line']}
                declarations.append({'name': name_of(declaration), 'linkage': 'static' if prefix.startswith('static ') else 'external',
                                     'expression': prefix + ' ' + text(child), 'source_span': source_span, 'map_entries': entries,
                                     **variable_initializer(node, child)})
        elif node.type == 'function_definition':
            name = name_of(node.child_by_field_name('declarator'))
            params = parameters(node)
            return_type_expression = text(node.child_by_field_name('type'))
            return_type_resolution = 'resolved' if node.child_by_field_name('declarator').type == 'function_declarator' else 'unresolved'
            body = node.child_by_field_name('body')
            legacy_headers = [expression for _, expression in
                              adapted_expressions(node, kind='old_style_function_header')]
            body_start = legacy_headers[0]['original_body_start_byte'] if legacy_headers else body.start_byte
            body_expression = canonical(original_data[body_start:body.end_byte].decode('utf-8'))
            parameter_declarations = []
            for declaration in body.named_children if legacy_headers else []:
                if declaration.type != 'declaration' or declaration.end_byte > body_start:
                    continue
                for declarator in declarators(declaration):
                    parameter_declarations.append({
                        'name': name_of(declarator),
                        'type_expression': text(declaration.child_by_field_name('type')),
                        'declarator_expression': text(declarator),
                        'declaration_expression': text(declaration),
                        'source_span': loc(declaration)})
            prototype_unresolved = ['original identifier-list function does not supply a fixed prototype'] if legacy_headers else []
            signature_unresolved = signature_reasons(node, body_start)
            original_prefix = canonical(restore_text(original_data, syntax_evidence, node.start_byte,
                                                    node.child_by_field_name('declarator').start_byte))
            declaration_modifiers = re.findall(r'\b(?:static|extern|inline|__inline__|__inline)\b', original_prefix)
            parsed_modifiers = [text(child) for child in node.named_children
                                if child.end_byte <= node.child_by_field_name('declarator').start_byte
                                and child.type in ('storage_class_specifier', 'function_specifier')]
            modifiers_resolved = not legacy_headers and declaration_modifiers == parsed_modifiers
            variadic = False
            if not legacy_headers:
                function_declarator = node.child_by_field_name('declarator')
                while function_declarator and function_declarator.type != 'function_declarator':
                    function_declarator = function_declarator.child_by_field_name('declarator')
                listing = function_declarator.child_by_field_name('parameters') if function_declarator else None
                entries = [parameter for parameter in listing.named_children if parameter.type != 'comment'] if listing else []
                variadic = any(parameter.type == 'variadic_parameter' or text(parameter) == '...' for parameter in entries)
                if not entries:
                    prototype_unresolved.append('function declaration lacks a complete fixed prototype')
                for parameter in entries:
                    if len(entries) == 1 and text(parameter) == 'void':
                        continue
                    declarator = parameter.child_by_field_name('declarator')
                    if (parameter.type != 'parameter_declaration' or not name_of(declarator)
                            or not parameter.child_by_field_name('type')
                            or any(child.type in ('array_declarator', 'function_declarator') for child in walk(declarator))):
                        prototype_unresolved.append('variadic, unnamed, or complex function parameter is outside the bounded fixed prototype')
                for parameter in listing.named_children if listing else []:
                    if parameter.type != 'parameter_declaration' or text(parameter) == 'void':
                        continue
                    declarator = parameter.child_by_field_name('declarator')
                    parameter_declarations.append({
                        'name': name_of(declarator),
                        'type_expression': text(parameter.child_by_field_name('type')),
                        'declarator_expression': text(declarator),
                        'declaration_expression': text(parameter), 'source_span': loc(parameter),
                        'declaration_id': parameter.start_byte})
            declared_parameters = [declaration['name'] for declaration in parameter_declarations]
            parameter_declarations_resolved = not legacy_headers or (
                len(declared_parameters) == len(params)
                and len(set(declared_parameters)) == len(declared_parameters)
                and set(declared_parameters) == set(params))
            body_barriers = [
                {'reason': replacement['semantic_barrier'],
                 'source_span': expression['source_span'],
                 'syntax_kind': expression['kind'],
                 'original_expression': expression.get('original_text', '')}
                for replacement, expression in adapted_expressions(body, barriers=True)]
            variables = []
            for child in walk(body):
                if child.start_byte < body_start:
                    continue
                if child.type == 'type_definition':
                    body_barriers.append({'reason': 'local C type alias storage and representation are outside the bounded semantic model',
                                          'source_span': loc(child), 'syntax_kind': child.type,
                                          'original_expression': text(child)})
                if child.type == 'declaration':
                    declarations_here = declarators(child)
                    prefix = canonical(restore_text(original_data, syntax_evidence, child.start_byte, declarations_here[0].start_byte)) if declarations_here else ''
                    for decl in declarations_here:
                        variable_name = name_of(decl.child_by_field_name('declarator') if decl.type == 'init_declarator' else decl)
                        if variable_name:
                            variables.append({'name': variable_name, 'expression': prefix + ' ' + text(decl),
                                              'source_span': {**loc(child), 'end_line': loc(decl)['end_line']},
                                              'declaration_point': point_for_name(decl, variable_name),
                                              'declaration_id': decl.start_byte, **metadata(decl),
                                              'initializer_node': expression_fact(decl.child_by_field_name('value')),
                                              **variable_initializer(child, decl)})
                elif child.type == 'assignment_expression':
                    left = child.child_by_field_name('left')
                    if left and left.type == 'call_expression':
                        for pointer in walk(left):
                            if pointer.type != 'pointer_expression': continue
                            for identifier in walk(pointer):
                                possible_name = text(identifier) if identifier.type == 'identifier' else None
                                if possible_name in global_functions:
                                    variables.append({'name': possible_name, 'expression': text(child), 'source_span': loc(child), 'declaration_point': point_for_name(pointer, possible_name), 'unresolved': 'typedef-dependent declaration/expression ambiguity; possible local binding'})
            shadows = {v['name'] for v in variables} | set(params)
            events = {'calls': [], 'returns': [], 'variables': variables, 'conditions': [], 'writes': [], 'expressions': []}
            if body_barriers:
                events['semantic_unassessed'] = body_barriers
            if any(child.type in ('goto_statement', 'labeled_statement', 'switch_statement') for child in walk(body)):
                events['control_flow_unresolved'] = ['jump/label/switch flow unassessed']
            def terminates(child):
                if child.type == 'return_statement': return True
                if child.type == 'compound_statement': return any(terminates(nested) for nested in child.named_children)
                if child.type == 'else_clause': return any(terminates(nested) for nested in child.named_children)
                if child.type == 'if_statement':
                    consequence, alternative = child.child_by_field_name('consequence'), child.child_by_field_name('alternative')
                    return consequence is not None and alternative is not None and terminates(consequence) and terminates(alternative)
                return False
            def visit(child, guards):
                if child.end_byte <= body_start:
                    return
                if child.type == 'declaration':
                    for variable in variables:
                        if child.start_byte <= variable.get('byte_start', -1) < child.end_byte:
                            variable['guards'] = list(guards)
                if child.type == 'compound_statement':
                    current = list(guards)
                    for statement in child.named_children:
                        visit(statement, current)
                        if terminates(statement): break
                        if statement.type == 'if_statement':
                            consequence, alternative = statement.child_by_field_name('consequence'), statement.child_by_field_name('alternative')
                            then_end = consequence is not None and terminates(consequence)
                            else_end = alternative is not None and terminates(alternative)
                            if then_end != else_end:
                                condition = text(statement.child_by_field_name('condition'))
                                if condition.startswith('( ') and condition.endswith(' )'): condition = condition[2:-2]
                                current = current + [{'condition': condition, 'branch': 'else' if then_end else 'then',
                                                      'condition_id': statement.start_byte, 'source_span': loc(statement)}]
                    return
                if child.type == 'if_statement':
                    condition_node = child.child_by_field_name('condition')
                    condition = text(condition_node)
                    if condition.startswith('( ') and condition.endswith(' )'): condition = condition[2:-2]
                    identity = loc(child)
                    events['conditions'].append({'expression': condition, 'expression_node': expression_fact(condition_node),
                                                 'source_span': identity, 'guards': guards, **metadata(condition_node)})
                    events['conditions'][-1]['condition_id'] = child.start_byte
                    visit(condition_node, guards)
                    consequence = child.child_by_field_name('consequence')
                    alternative = child.child_by_field_name('alternative')
                    if consequence: visit(consequence, guards + [{'condition': condition, 'branch': 'then', 'condition_id': child.start_byte, 'source_span': identity}])
                    if alternative: visit(alternative, guards + [{'condition': condition, 'branch': 'else', 'condition_id': child.start_byte, 'source_span': identity}])
                    return
                if child.type == 'call_expression' and typedef_cast(child) is None:
                    expression = text(child)
                    callee_name = text(child.child_by_field_name('function'))
                    candidates = global_functions.get(callee_name, [])
                    visible = [v for v in variables if v['name'] == callee_name
                               and v.get('byte_start', -1) <= child.start_byte
                               and v.get('scope_start', -1) <= child.start_byte < v.get('scope_end', body.end_byte)]
                    shadow = max(visible, key=lambda v: v.get('scope_start', -1))['declaration_point'] if visible else point_for_name(node.child_by_field_name('declarator'), callee_name)
                    callee = {'name': callee_name, 'kind': 'NON_FUNCTION_BINDING', 'source_span': shadow} if visible or callee_name in params else candidates[0] if candidates else None
                    events['calls'].append({'expression': expression, 'expression_node': expression_fact(child),
                                            'source_span': loc(child), 'guards': guards, 'callee': callee, **metadata(child)})
                elif child.type == 'return_statement':
                    value = next(iter(child.named_children), None)
                    events['returns'].append({'expression': return_expression(text(child)), 'expression_node': expression_fact(value),
                                              'source_span': loc(child), 'guards': guards, **metadata(child)})
                elif (child.type == 'expression_statement' and len(child.named_children) == 1
                      and child.named_children[0].type not in ('assignment_expression', 'update_expression')
                      and (child.named_children[0].type != 'call_expression' or typedef_cast(child.named_children[0]))):
                    expression_node = child.named_children[0]
                    events['expressions'].append({'expression': text(expression_node), 'expression_node': expression_fact(expression_node),
                                                   'source_span': loc(child), 'guards': guards, **metadata(expression_node)})
                elif child.type in ('assignment_expression', 'update_expression'):
                    target = child.child_by_field_name('left') or child.child_by_field_name('argument')
                    value = child.child_by_field_name('right')
                    operator = child.child_by_field_name('operator')
                    events['writes'].append({'expression': text(child), 'target': text(target) if target else re.sub(r'\+\+|--', '', text(child)).strip(),
                                            'target_node': expression_fact(target), 'value_node': expression_fact(value),
                                            'operator': text(operator) if operator else 'update',
                                            'source_span': loc(child), 'guards': guards, **metadata(child)})
                for nested in child.named_children: visit(nested, guards)
            visit(body, [])
            def flow(child):
                if child.type in ('compound_statement', 'else_clause'):
                    return {'kind': 'block', 'children': [flow(nested) for nested in child.named_children if nested.type != 'comment']}
                if child.type == 'if_statement':
                    condition = text(child.child_by_field_name('condition'))
                    if condition.startswith('( ') and condition.endswith(' )'):
                        condition = condition[2:-2]
                    return {'kind': 'if', 'condition': condition, 'byte_start': child.start_byte,
                            'then': flow(child.child_by_field_name('consequence')),
                            'else': flow(child.child_by_field_name('alternative')) if child.child_by_field_name('alternative') else {'kind': 'fallthrough'}}
                if child.type == 'return_statement':
                    return {'kind': 'return', 'byte_start': child.start_byte, 'loops': metadata(child)['loops']}
                if (child.type == 'expression_statement' and len(child.named_children) == 1
                        and child.named_children[0].type == 'call_expression'
                        and typedef_cast(child.named_children[0]) is None):
                    return {'kind': 'call', 'byte_start': child.named_children[0].start_byte}
                if child.type in ('for_statement', 'while_statement', 'do_statement'):
                    return {'kind': 'loop', 'loop_kind': child.type, 'byte_start': child.start_byte,
                            'initializer_expression': text(child.child_by_field_name('initializer')) if child.child_by_field_name('initializer') else None,
                            'condition_expression': text(child.child_by_field_name('condition')) if child.child_by_field_name('condition') else None,
                            'update_expression': text(child.child_by_field_name('update')) if child.child_by_field_name('update') else None,
                            'body': flow(child.child_by_field_name('body'))}
                if child.type in ('continue_statement', 'break_statement'):
                    return {'kind': child.type.removesuffix('_statement'), 'byte_start': child.start_byte}
                if child.type == 'goto_statement':
                    return {'kind': 'goto', 'target': text(child.child_by_field_name('label')), 'byte_start': child.start_byte}
                if child.type == 'labeled_statement':
                    return {'kind': 'label', 'name': text(child.child_by_field_name('label')), 'byte_start': child.start_byte,
                            'children': [flow(nested) for nested in child.named_children if nested.type != 'statement_identifier']}
                return {'kind': 'fallthrough'}
            events['flow'] = flow(body)
            declaration_identity = global_functions.get(name, [{'name': name, 'kind': 'FUNCTION_DECL', 'source_span': point_for_name(node.child_by_field_name('declarator'), name)}])[0]
            functions.append({'id': function_id(physical, name, loc(node)), 'declaration_identity': declaration_identity, 'name': name, 'linkage': 'static' if text(node).startswith('static ') else 'external', 'parameters': params, 'source_span': loc(node), 'events': events, 'constants': function_constants(node, body), 'body_expression': body_expression,
                              'parameter_source_contract': {
                                  'return_type_expression': return_type_expression,
                                  'return_type_resolution': return_type_resolution,
                                  'prototype_resolution': 'unresolved' if prototype_unresolved else 'resolved',
                                  'variadic': variadic, 'prototype_unresolved': prototype_unresolved,
                                  'signature_resolution': 'unresolved' if signature_unresolved else 'resolved',
                                  'signature_unresolved': signature_unresolved,
                                  'declaration_modifiers': declaration_modifiers,
                                  'declaration_modifiers_resolution': 'resolved' if modifiers_resolved else 'unresolved',
                                  'declaration_modifiers_source': loc(node),
                                  'declaration_prefix_expression': original_prefix,
                                  'kind': 'old-style-identifier-list' if legacy_headers else 'parsed-C-declaration',
                                  'parameter_names': params, 'source_evidence': legacy_headers,
                                  'declarations': parameter_declarations,
                                  'declaration_binding_resolution': 'resolved' if parameter_declarations_resolved else 'unresolved',
                                  'unresolved': [] if parameter_declarations_resolved else ['original identifier list and parsed declaration-list declarators do not form a complete unique binding'],
                                  'abi_resolution': 'unassessed'}})
            functions[-1].update(return_type_expression=return_type_expression, return_type_resolution=return_type_resolution)
    errors = [{'source_span': loc(n), 'reason': 'tree-sitter ' + n.type, 'operational': True} for n in walk(tree.root_node) if n.type == 'ERROR' or n.is_missing]
    unassessed_origins = [
        {'source_span': loc(node), 'syntax_kind': node.type,
         'declaration_expression': text(node), 'reason': 'compiler line-remapped/unqualified source origin is not a captured physical dependency'}
        for node in tree.root_node.named_children
        if node.type in ('declaration', 'function_definition', 'type_definition')
        and not owned(loc(node)['source'], physical, record)
        and loc(node)['source'] not in record['dependencies']]
    return declarations, functions, errors, {'frontend': 'tree-sitter-c', 'input': 'actual build-compiler-expanded C',
                                            'syntax_adaptation': syntax_evidence,
                                            'aggregate_interfaces': aggregate_interfaces(),
                                            'function_pointer_interfaces': function_pointer_interfaces(),
                                            'contract_constants': selected_constants(' '.join(record.get('contracts', {}).values()), constants),
                                            'unassessed_source_declarations': unassessed_origins}
