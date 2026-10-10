"""Lossless syntax-only adaptation of qualified selected compiler GNU forms.

The parser spelling is never compiler input or expression evaluation authority.
Byte offsets and newlines are identical in the original and adapted source.
"""
import hashlib
from bisect import bisect_right
from importlib import metadata
import re


GRAMMAR_AUTHORITY = {
    'package': 'tree-sitter-c',
    'version': '0.24.1',
    'source_url': 'https://raw.githubusercontent.com/tree-sitter/tree-sitter-c/v0.24.1/grammar.js',
    'source_sha256': 'f362b9fe06db321464fc77f1e5ba2ce089baec9d18f4defddb0c9ca87885e546',
    'rules': {
        'offsetof_expression': 'grammar.js:1107-1110: offsetof ( type_descriptor , _field_identifier )',
        'type_descriptor': 'grammar.js:1087-1092: type_specifier with optional qualifiers and abstract declarator',
        'type_specifier': 'grammar.js:595-603: includes struct_specifier',
        'struct_specifier': 'grammar.js:697-711: permits anonymous field_declaration_list',
    },
    'compiler_syntax_url': 'https://gcc.gnu.org/onlinedocs/gcc/Offsetof.html',
    'equivalence': 'GCC documents offsetof(type, member) as __builtin_offsetof(type, member); only the operator spelling is adapted',
}

FALLTHROUGH_AUTHORITY = {
    'package': 'tree-sitter-c',
    'version': '0.24.1',
    'source_url': GRAMMAR_AUTHORITY['source_url'],
    'source_sha256': GRAMMAR_AUTHORITY['source_sha256'],
    'rules': {
        'attribute_specifier': 'grammar.js:310-315: GNU attributes occur in declaration modifiers',
        'attributed_statement': 'grammar.js:784-787: only standard [[...]] attributes precede statements',
        'expression_statement': 'grammar.js:841-847: permits a null statement',
    },
    'compiler_syntax_url': 'https://gcc.gnu.org/onlinedocs/gcc/Common-Attributes.html#index-fallthrough',
    'equivalence': 'argument-free standalone GNU fallthrough marks intentional switch fallthrough for diagnostics; its null statement performs no evaluation',
}

PROJECTION_AUTHORITY = {
    'package': 'tree-sitter-c',
    'version': '0.24.1',
    'source_url': GRAMMAR_AUTHORITY['source_url'],
    'source_sha256': GRAMMAR_AUTHORITY['source_sha256'],
    'rules': {
        'sizeof_expression': 'grammar.js:1097-1100: sizeof accepts a parenthesized type_descriptor',
        'attribute_declaration': 'grammar.js:317-327: [[ identifier optional(argument_list) ]]',
        'type_definition': 'grammar.js:280-290: typedef type/declarators followed by GNU attributes, not prefix attributes',
        'storage_class_specifier': 'grammar.js:555-566: includes __thread and thread_local, not C11 _Thread_local',
        'attributed_declarator': 'grammar.js:441-444: declarator followed by standard attribute declarations',
        'field_declaration': 'grammar.js:738-743: declaration specifiers with optional declarators',
        'compound_statement': 'compound_statement: braces surrounding native block items',
        'old_style_function_definition': 'grammar.js:254-260: identifier-list and declaration-list function',
        'goto_statement': 'grammar.js:922-926: goto requires a label identifier, not an expression',
    },
    'equivalence': 'parser syntax only; original source, ABI, attributes and runtime effects are separate authority',
}

# Literal/comment alternatives precede identifiers, including prefixed literals.
# A spliced identifier is one token and cannot match the unspliced keyword.
LEXEME = re.compile(
    rb'(?P<trivia>\s+|\\\r?\n|/\*[\s\S]*?\*/|//(?:\\\r?\n|[^\r\n])*)'
    rb'|(?P<literal>(?:u8|u|U|L)?"(?:\\[\s\S]|[^"\\])*"|(?:u|U|L)?\'(?:\\[\s\S]|[^\'\\])*\')'
    rb'|(?P<identifier>[A-Za-z_$\x80-\xff][A-Za-z_0-9$\x80-\xff]*(?:\\\r?\n[A-Za-z_0-9$\x80-\xff]*)*)'
    rb'|(?P<number>(?:[0-9]|\.[0-9])(?:[eEpP][+-]|[A-Za-z_0-9.]|\\\r?\n)*)'
    rb'|(?P<punctuation>->|.)',
    re.S,
)
ORIGINAL = b'__builtin_offsetof'
KEYWORD = b'offsetof'
REPLACEMENT = KEYWORD + b' ' * (len(ORIGINAL) - len(KEYWORD))


def _tokens(data):
    result = []
    for match in LEXEME.finditer(data):
        if match.lastgroup == 'trivia':
            continue
        start, end = match.span()
        if data.startswith(b'/*', start) or match[0] in (b'"', b"'"):
            raise ValueError(f'unterminated C comment/literal at expanded byte {start}')
        result.append((match[0], start, end))
    return result


def _invocation(tokens, index):
    """Return a balanced operator range and its sole top-level comma."""
    stack, comma = [], None
    closers = {b')': b'(', b'}': b'{', b']': b'['}
    for position in range(index + 1, len(tokens)):
        token = tokens[position][0]
        if token in (b'(', b'{', b'['):
            stack.append(token)
        elif token in closers:
            if not stack or stack.pop() != closers[token]:
                raise ValueError(f'unbalanced {tokens[index][0].decode("utf-8")} at expanded byte {tokens[index][1]}')
            if not stack:
                if token != b')' or comma is None:
                    break
                return position, comma
        elif token == b',' and stack == [b'(']:
            if comma is not None:
                break
            comma = position
    raise ValueError(f'unsupported {tokens[index][0].decode("utf-8")} argument syntax at expanded byte {tokens[index][1]}')




def _location(line_starts, origins, start, end):
    start_row = bisect_right(line_starts, start) - 1
    end_row = bisect_right(line_starts, end - 1) - 1
    first, last = origins[start_row], origins[end_row]
    return {
        'source': first[0],
        'start_line': first[1],
        'end_line': last[1] if first[0] == last[0] else first[1],
        'expanded_span': {
            'start_byte': start, 'end_byte': end,
            'start_point': [start_row, start - line_starts[start_row]],
            'end_point': [end_row, end - line_starts[end_row]],
        },
        'line_origins': [list(origin) for origin in origins[start_row:end_row + 1]],
    }


def _fallthrough(tokens, index):
    """Bound only a sole argument-free attribute on an existing null statement."""
    if tokens[index][0] not in (b'__attribute__', b'__attribute'):
        return None
    if not index or tokens[index - 1][0] not in (b';', b'}'):
        return None
    tail = tokens[index + 1:index + 7]
    if [token[0] for token in tail] != [b'(', b'(', b'fallthrough', b')', b')', b';']:
        return None
    return tail[-2][2], tail[-1][2]


def _blank(data):
    return bytes(byte if byte in (10, 13) else 32 for byte in data)


def _balanced_end(tokens, opening):
    stack = []
    pairs = {b')': b'(', b']': b'[', b'}': b'{'}
    for position in range(opening, len(tokens)):
        token = tokens[position][0]
        if token in (b'(', b'[', b'{'):
            stack.append(token)
        elif token in pairs:
            if not stack or stack.pop() != pairs[token]:
                raise ValueError(f'unbalanced C delimiter at expanded byte {tokens[position][1]}')
            if not stack:
                return position
    raise ValueError(f'unclosed C delimiter at expanded byte {tokens[opening][1]}')


def _projection(data, line_starts, origins, start, end, transformed, kind, reason, **facts):
    original = data[start:end]
    if len(original) != len(transformed) or [i for i, b in enumerate(original) if b in (10, 13)] != [i for i, b in enumerate(transformed) if b in (10, 13)]:
        raise ValueError(f'nonidentity GNU syntax projection at expanded byte {start}')
    expression = {
        'kind': kind, 'original_text': original.decode('utf-8'),
        'source_span': _location(line_starts, origins, start, end), **facts,
    }
    return {
        'start_byte': start, 'end_byte': end,
        'original': original.decode('utf-8'), 'transformed': transformed.decode('utf-8'),
        'transformed_token_end_byte': start,
        'source_span': expression['source_span'], 'expression': expression,
        'reason': reason, 'grammar_authority': dict(PROJECTION_AUTHORITY),
        'restoration': 'identity original byte slice; projection is not semantic authority',
    }


def _legacy_headers(data, tokens, line_starts, origins):
    """Keep original declaration-list syntax as a parser-only body prefix.

    The source identifier list is signature authority. The projected empty
    parameter list is not a prototype, and declaration-list nodes are not locals.
    """
    replacements = []
    depth, index = 0, 0
    identifier = rb'[A-Za-z_][A-Za-z_0-9]*'
    while index < len(tokens):
        token, start, end = tokens[index]
        if token == b'{':
            depth += 1
        elif token == b'}':
            depth -= 1
        if depth or not re.fullmatch(identifier, token) or index + 1 == len(tokens) or tokens[index + 1][0] != b'(':
            index += 1
            continue
        close = _balanced_end(tokens, index + 1)
        names = tokens[index + 2:close]
        if not names or any(not re.fullmatch(identifier, t[0]) if n % 2 == 0 else t[0] != b',' for n, t in enumerate(names)) or len(names) % 2 == 0:
            index = close + 1
            continue
        parameter_names = [t[0].decode('utf-8') for t in names[::2]]
        cursor, declarations, parts, declaration_start = close + 1, [], [], close + 1
        while cursor < len(tokens):
            value = tokens[cursor][0]
            if value in (b'(', b'['):
                cursor = _balanced_end(tokens, cursor) + 1
                continue
            if value == b';':
                if declaration_start == cursor:
                    break
                parts.append((declaration_start, cursor))
                declaration_start = cursor + 1
            elif value in (b'{', b'}', b'='):
                break
            cursor += 1
        if not parts or cursor == len(tokens) or tokens[cursor][0] != b'{' or declaration_start != cursor:
            index = close + 1
            continue
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError(f'duplicate old-style parameter identifier at expanded byte {start}')
        bound = []
        for declaration_order, (first, last) in enumerate(parts):
            declaration_tokens = tokens[first:last]
            found = [t for t in declaration_tokens if t[0].decode('utf-8') in parameter_names]
            if not found:
                raise ValueError(f'unbound old-style parameter declaration at expanded byte {tokens[first][1]}')
            # Every original declaration is retained, including shared specifiers
            # and nested function-pointer declarators. Binding is lexical only.
            for name in found:
                spelling = name[0].decode('utf-8')
                bound.append(spelling)
                declarations.append({
                    'name': spelling,
                    'expression': data[tokens[first][1]:tokens[last][2]].decode('utf-8'),
                    'original_text': data[tokens[first][1]:tokens[last][2]].decode('utf-8'),
                    'source_span': _location(line_starts, origins, tokens[first][1], tokens[last][2]),
                    'identifier_source_span': _location(line_starts, origins, name[1], name[2]),
                    'identifier_order': parameter_names.index(spelling),
                    'declaration_order': declaration_order,
                    'typing': 'original declaration-list syntax; lexical name candidate requires native declarator validation, no prototype or ABI assertion',
                })
        if len(bound) != len(set(bound)) or set(bound) != set(parameter_names):
            raise ValueError(f'incomplete or ambiguous old-style parameter declarations at expanded byte {start}')
        header_end = tokens[cursor][2]
        projected = bytearray(data[start:header_end])
        list_start, list_end = names[0][1], tokens[close][2]
        projected[list_start - start:list_end - start] = _blank(data[list_start:list_end])
        projected[list_start - start] = ord(')')
        projected[tokens[close][1] - start] = ord('{')
        projected[tokens[cursor][1] - start] = ord(' ')
        replacements.append(_projection(
            data, line_starts, origins, start, header_end, bytes(projected),
            'old_style_function_header', 'original K&R declaration-list retained as parser-only compound prefix',
            function_name=token.decode('utf-8'), identifier_order=parameter_names,
            parameter_names=parameter_names, parameter_declarations=declarations,
            original_body_start_byte=tokens[cursor][1],
            parameter_binding={'state': 'lexical-correspondence-only', 'authority': 'unique lexical occurrences; native declaration AST must validate declarator names and types'},
            signature_semantics={'state': 'unassessed', 'reason': 'old-style function, no modern prototype or ABI inferred'},
        ))
        if any(t[0] in (b'[', b'typeof', b'__typeof__', b'__typeof') for first, last in parts for t in tokens[first:last]):
            replacements[-1]['semantic_barrier'] = 'old-style array/type-expression parameter declarations may evaluate nested entry effects; projected declaration prefix is not body-effect authority'
        depth = 1
        index = cursor + 1
    return replacements


def adapt_source(code: str, origins: list) -> tuple[str, dict]:
    """Project qualified GNU/legacy syntax without changing source byte identity.

    ``code`` is selected compiler-expanded C after line-marker extraction;
    ``origins`` maps each physical expanded line to (source, original line).
    Unsupported forms remain explicit unassessed facts or actionable failures.
    Every projected or native form must pass the frontend's strict real parser.
    Non-call references, member identifiers, comments and literals are intact.
    """
    data = code.encode('utf-8')
    line_starts = [0, *(match.end() for match in re.finditer(b'\n', data))]
    line_count = len(line_starts) - (1 if not data or data.endswith(b'\n') else 0)
    if len(origins) != line_count:
        raise ValueError('GNU syntax adapter requires one origin per expanded source line')
    tokens = _tokens(data)
    replacements = _legacy_headers(data, tokens, line_starts, origins)
    unassessed, attribute_ends = [], set()
    hidden_until = -1
    aggregate_ranges = []
    for index, (token, start, end) in enumerate(tokens):
        if token not in (b'struct', b'union'):
            continue
        opening = index + 1
        if opening < len(tokens) and re.fullmatch(rb'[A-Za-z_][A-Za-z_0-9]*', tokens[opening][0]):
            opening += 1
        if opening < len(tokens) and tokens[opening][0] == b'{':
            aggregate_ranges.append((opening, _balanced_end(tokens, opening)))
    for index, (token, start, end) in enumerate(tokens):
        if start < hidden_until:
            continue
        if token == b'_Thread_local' and (not index or tokens[index - 1][0] not in (b'.', b'->')):
            keyword = b'__thread'
            fact = _projection(
                data, line_starts, origins, start, end, keyword + b' ' * (end - start - len(keyword)),
                'c11_thread_local_keyword', 'C11 storage keyword parsed with supported GNU storage-class spelling',
                storage_keyword={'original': '_Thread_local', 'parser_spelling': '__thread'},
                abi_semantics={'state': 'unassessed', 'reason': 'parser spelling does not qualify TLS ABI, linkage or placement'},
            )
            fact['transformed_token_end_byte'] = start + len(keyword)
            fact['original_token_end_byte'] = end
            replacements.append(fact)
            continue
        if token == b'__builtin_va_arg' and index + 1 < len(tokens) and tokens[index + 1][0] == b'(' and (not index or tokens[index - 1][0] not in (b'.', b'->')):
            close, comma = _invocation(tokens, index)
            if comma == index + 2 or comma + 1 == close:
                raise ValueError(f'empty GNU va_arg operand at expanded byte {start}')
            expression_end = tokens[close][2]
            type_start, type_end = tokens[comma + 1][1], tokens[close - 1][2]
            value_start, value_end = tokens[index + 2][1], tokens[comma - 1][2]
            projected = bytearray(data[start:expression_end])
            projected[:end - start] = b'sizeof' + b' ' * (end - start - 6)
            erased_start, erased_end = tokens[index + 1][2], tokens[comma][2]
            projected[erased_start - start:erased_end - start] = _blank(data[erased_start:erased_end])
            fact = _projection(
                data, line_starts, origins, start, expression_end, bytes(projected),
                'gnu_va_arg', 'type operand parsed by sizeof type_descriptor, not sizeof semantics',
                value_operand={'expression': data[value_start:value_end].decode('utf-8'), 'source_span': _location(line_starts, origins, value_start, value_end)},
                type_operand={'expression': data[type_start:type_end].decode('utf-8'), 'source_span': _location(line_starts, origins, type_start, type_end), 'resolution': 'syntax only; target type/ABI unassessed'},
                runtime_semantics={'state': 'unassessed', 'reason': 'variadic argument retrieval and all value-operand effects are absent from projected AST'},
                abi_semantics={'state': 'unassessed', 'reason': 'selected source type is not variadic calling-convention authority'},
            )
            fact['semantic_barrier'] = 'GNU va_arg runtime retrieval and nested value-operand effects require original function-body semantics'
            fact['transformed_token_end_byte'] = start + 6
            fact['original_token_end_byte'] = end
            replacements.append(fact)
            hidden_until = expression_end
            continue
        if token in (b'__attribute__', b'__attribute') and index and tokens[index - 1][0] == b'typedef':
            if [entry[0] for entry in tokens[index + 1:index + 3]] == [b'(', b'(']:
                close = _balanced_end(tokens, index + 1)
                tail = tokens[close + 1:close + 8]
                if (len(tail) >= 7 and re.fullmatch(rb'[A-Za-z_][A-Za-z_0-9]*', tail[0][0])
                        and [entry[0] for entry in tail[1:3]] == [b'(', b'*']
                        and re.fullmatch(rb'[A-Za-z_][A-Za-z_0-9]*', tail[3][0])
                        and [entry[0] for entry in tail[4:6]] == [b')', b'(']):
                    parameters_end = _balanced_end(tokens, close + 6)
                    if parameters_end + 1 < len(tokens) and tokens[parameters_end + 1][0] == b';':
                        attribute_end = tokens[close][2]
                        content_start, content_end = tokens[index + 2][2], tokens[close - 1][1]
                        fact = _projection(
                            data, line_starts, origins, start, attribute_end, _blank(data[start:attribute_end]),
                            'gnu_typedef_prefix_attribute', 'direct function-pointer typedef prefix attributes retained outside parser spelling',
                            attribute_list={'expression': data[content_start:content_end].decode('utf-8'),
                                            'source_span': _location(line_starts, origins, content_start, content_end)},
                            source_binding={'position': 'between typedef and type specifier',
                                            'authority': 'original selected compiler-expanded attribute spelling and position'},
                            abi_semantics={'state': 'unassessed', 'reason': 'original typedef attributes require qualified calling-convention authority'},
                            parser_semantics='attribute projection supplies syntax only, never ABI equivalence',
                        )
                        fact['semantic_barrier'] = 'GNU function-pointer typedef prefix attributes require original source ABI qualification'
                        replacements.append(fact)
                        hidden_until = attribute_end
                        continue
        if token in (b'__attribute__', b'__attribute') and index and (tokens[index - 1][0] == b']' or index - 1 in attribute_ends):
            if [t[0] for t in tokens[index + 1:index + 3]] != [b'(', b'(']:
                raise ValueError(f'unsupported post-declarator GNU attribute at expanded byte {start}')
            close = _balanced_end(tokens, index + 1)
            if tokens[close - 1][0] != b')' or tokens[close - 1][2] != tokens[close][1]:
                raise ValueError(f'unsupported separated GNU attribute closing delimiters at expanded byte {start}')
            attribute_end = tokens[close][2]
            projected = bytearray(data[start:attribute_end])
            projected[:end - start] = b'[[' + b' ' * (end - start - 2)
            for position in (index + 1, index + 2):
                a, b = tokens[position][1:3]
                projected[a - start:b - start] = _blank(data[a:b])
            projected[tokens[close - 1][1] - start:attribute_end - start] = b']]'
            content_start, content_end = tokens[index + 2][2], tokens[close - 1][1]
            replacements.append(_projection(
                data, line_starts, origins, start, attribute_end, bytes(projected),
                'gnu_post_declarator_attribute', 'GNU post-array attribute list parsed as native standard attribute_declaration',
                attribute_list={'expression': data[content_start:content_end].decode('utf-8'), 'source_span': _location(line_starts, origins, content_start, content_end)},
                source_binding={'position': 'after array declarator', 'authority': 'original selected compiler-expanded attribute spelling and position'},
                placement_semantics={'state': 'unassessed', 'reason': 'source section/alignment requests are not actual linker placement or ABI authority'},
                parser_semantics='standard attribute node is syntax only, not a C standard attribute claim',
            ))
            attribute_ends.add(close)
            hidden_until = attribute_end
            continue
        if token in (b'asm', b'__asm__', b'__asm') and index and re.fullmatch(rb'[A-Za-z_][A-Za-z_0-9]*', tokens[index - 1][0]) and index + 1 < len(tokens) and tokens[index + 1][0] == b'(':
            close = _balanced_end(tokens, index + 1)
            previous = index - 1
            while previous and tokens[previous - 1][0] not in (b';', b'{', b'}'):
                previous -= 1
            declaration = tokens[previous:index]
            if any(t[0] == b'register' for t in declaration) and close + 1 < len(tokens) and tokens[close + 1][0] in (b'=', b';', b','):
                if close != index + 3 or not re.fullmatch(rb'(?:u8|u|U|L)?"(?:\\.|[^"\\])*"', tokens[index + 2][0]):
                    raise ValueError(f'unsupported register asm binding at expanded byte {start}')
                binding_end = tokens[close][2]
                fact = _projection(
                    data, line_starts, origins, start, binding_end, _blank(data[start:binding_end]),
                    'gnu_register_asm', 'original local register binding retained outside parser declarator syntax',
                    declaration_name=tokens[index - 1][0].decode('utf-8'),
                    register_operand={'expression': tokens[index + 2][0].decode('utf-8'), 'source_span': _location(line_starts, origins, tokens[index + 2][1], tokens[index + 2][2])},
                    declaration_source_span=_location(line_starts, origins, tokens[previous][1], binding_end),
                    abi_semantics={'state': 'unassessed', 'reason': 'explicit register binding is selected source evidence, not inferred calling convention'},
                )
                fact['semantic_barrier'] = 'local explicit register binding can affect inline assembly and function-body effects'
                replacements.append(fact)
                hidden_until = binding_end
                continue
        if token == b'goto' and index + 1 < len(tokens) and tokens[index + 1][0] == b'*':
            cursor = index + 2
            while cursor < len(tokens) and tokens[cursor][0] != b';':
                if tokens[cursor][0] in (b'(', b'[', b'{'):
                    cursor = _balanced_end(tokens, cursor)
                cursor += 1
            if cursor == len(tokens) or cursor == index + 2:
                raise ValueError(f'unsupported computed goto at expanded byte {start}')
            statement_end = tokens[cursor][2]
            projected = bytearray(data[start:statement_end])
            projected[:tokens[index + 1][2] - start] = _blank(data[start:tokens[index + 1][2]])
            operand_start, operand_end = tokens[index + 2][1], tokens[cursor - 1][2]
            fact = _projection(
                data, line_starts, origins, start, statement_end, bytes(projected),
                'gnu_computed_goto', 'computed target parsed as native expression statement, not original control flow',
                target_operand={'expression': data[operand_start:operand_end].decode('utf-8'), 'source_span': _location(line_starts, origins, operand_start, operand_end)},
                runtime_semantics={'state': 'unassessed', 'reason': 'computed branch and nested target effects cannot be inferred from an expression statement'},
            )
            fact['semantic_barrier'] = 'GNU computed goto changes function control flow; projected expression is not branch authority'
            replacements.append(fact)
            hidden_until = statement_end
            continue
        if token == b':' and any(a < index < b for a, b in aggregate_ranges):
            first = index - 1
            while first and tokens[first - 1][0] not in (b';', b'{', b'}'):
                first -= 1
            specifiers = tokens[first:index]
            allowed = {b'int', b'unsigned', b'signed', b'char', b'short', b'long', b'_Bool', b'const', b'volatile'}
            if specifiers and all(t[0] in allowed for t in specifiers):
                cursor = index + 1
                while cursor < len(tokens) and tokens[cursor][0] != b';':
                    if tokens[cursor][0] in (b'(', b'[', b'{'):
                        cursor = _balanced_end(tokens, cursor)
                    if tokens[cursor][0] in (b',', b'}'):
                        break
                    cursor += 1
                if cursor == len(tokens) or tokens[cursor][0] != b';' or cursor == index + 1:
                    raise ValueError(f'unsupported unnamed bitfield at expanded byte {start}')
                width_start, width_end = tokens[index + 1][1], tokens[cursor - 1][2]
                fact = _projection(
                    data, line_starts, origins, start, width_end, _blank(data[start:width_end]),
                    'unnamed_bitfield', 'unnamed bitfield retains native field type without an invented field identifier',
                    named=False, original_field_text=data[tokens[first][1]:tokens[cursor][2]].decode('utf-8'),
                    field_source_span=_location(line_starts, origins, tokens[first][1], tokens[cursor][2]),
                    type_operand={'expression': data[tokens[first][1]:tokens[index - 1][2]].decode('utf-8'), 'source_span': _location(line_starts, origins, tokens[first][1], tokens[index - 1][2])},
                    width_operand={'expression': data[width_start:width_end].decode('utf-8'), 'source_span': _location(line_starts, origins, width_start, width_end)},
                    layout_semantics={'state': 'unassessed', 'reason': 'unnamed storage/padding width is original source only; aggregate layout cannot come from projected fields'},
                )
                if cursor != index + 2 or not re.fullmatch(rb'[0-9]+', tokens[index + 1][0]):
                    fact['semantic_barrier'] = 'erased unnamed bitfield width expression may contain nested type/value effects'
                replacements.append(fact)
                hidden_until = width_end
                continue
        fallthrough = _fallthrough(tokens, index)
        if fallthrough:
            attribute_end, statement_end = fallthrough
            original = data[start:attribute_end]
            transformed = bytes(byte if byte in (10, 13) else 32 for byte in original)
            replacements.append({
                'start_byte': start, 'end_byte': attribute_end,
                'original': original.decode('utf-8'), 'transformed': transformed.decode('utf-8'),
                'transformed_token_end_byte': start,
                'source_span': _location(line_starts, origins, start, attribute_end),
                'reason': 'sole argument-free GNU fallthrough attribute on a null statement after a statement boundary',
                'grammar_authority': dict(FALLTHROUGH_AUTHORITY),
                'expression': {
                    'kind': 'gnu_fallthrough_null_statement',
                    'original_text': data[start:statement_end].decode('utf-8'),
                    'source_span': _location(line_starts, origins, start, statement_end),
                    'runtime_semantics': 'no evaluation; original null statement and control flow are retained',
                    'diagnostic_semantics': 'intentional switch fallthrough hint remains recorded in original compiler evidence',
                },
            })
            continue
        if token != ORIGINAL:
            continue
        if index and tokens[index - 1][0] in (b'.', b'->'):
            continue
        if index + 1 == len(tokens) or tokens[index + 1][0] != b'(':
            continue
        close, comma = _invocation(tokens, index)
        type_tokens = tokens[index + 2:comma]
        member_tokens = tokens[comma + 1:close]
        if not type_tokens or len(member_tokens) != 1 or not re.fullmatch(rb'[A-Za-z_][A-Za-z_0-9]*', member_tokens[0][0]):
            unassessed.append({
                'kind': 'gnu_offsetof', 'original_text': data[start:tokens[close][2]].decode('utf-8'),
                'source_span': _location(line_starts, origins, start, tokens[close][2]),
                'syntax_adaptation': 'unchanged; original syntax remains subject to strict parsing',
                'layout_semantics': {'state': 'unassessed', 'reason': 'member designator is outside the qualified bare-identifier grammar'},
            })
            continue
        expression_end = tokens[close][2]
        replacements.append({
            'start_byte': start, 'end_byte': end,
            'original': ORIGINAL.decode(), 'transformed': REPLACEMENT.decode(),
            'transformed_token_end_byte': start + len(KEYWORD),
            'source_span': _location(line_starts, origins, start, end),
            'reason': 'reserved GNU offsetof operator matches qualified offsetof_expression type/member grammar',
            'grammar_authority': dict(GRAMMAR_AUTHORITY),
            'expression': {
                'kind': 'gnu_offsetof',
                'original_text': data[start:expression_end].decode('utf-8'),
                'source_span': _location(line_starts, origins, start, expression_end),
                'type_operand': {'expression': data[type_tokens[0][1]:type_tokens[-1][2]].decode('utf-8'), 'source_span': _location(line_starts, origins, type_tokens[0][1], type_tokens[-1][2])},
                'member_operand': {'expression': member_tokens[0][0].decode('utf-8'), 'source_span': _location(line_starts, origins, member_tokens[0][1], member_tokens[0][2])},
                'layout_semantics': {
                    'state': 'unassessed',
                    'reason': 'target ABI/layout-dependent offsetof is not evaluated by the syntax adapter',
                },
            },
        })
    replacements.sort(key=lambda fact: (fact['start_byte'], fact['end_byte']))
    for left, right in zip(replacements, replacements[1:]):
        if left['end_byte'] > right['start_byte']:
            raise ValueError(f'overlapping GNU syntax projections at expanded byte {right["start_byte"]}')
    if replacements:
        version = metadata.version('tree-sitter-c')
        if version != GRAMMAR_AUTHORITY['version']:
            raise ValueError(f'GNU syntax adaptation is qualified for tree-sitter-c {GRAMMAR_AUTHORITY["version"]}, installed {version}')
    parts, cursor = [], 0
    for replacement in replacements:
        parts.extend((data[cursor:replacement['start_byte']], replacement['transformed'].encode('utf-8')))
        cursor = replacement['end_byte']
    parts.append(data[cursor:])
    transformed = b''.join(parts)
    return transformed.decode('utf-8'), {
        'adapter': 'qualified-gnu-syntax',
        'input_authority': 'selected compiler-expanded C after line-marker extraction; original compiler output receipt remains separate',
        'original_sha256': hashlib.sha256(data).hexdigest(),
        'transformed_sha256': hashlib.sha256(transformed).hexdigest(),
        'offset_mapping': 'identity UTF-8 byte offsets and physical lines; original slices are authority for every projected token and node',
        'grammar_authority': dict(GRAMMAR_AUTHORITY),
        'fallthrough_authority': dict(FALLTHROUGH_AUTHORITY),
        'projection_authority': dict(PROJECTION_AUTHORITY),
        'replacements': replacements,
        'unassessed': unassessed,
    }


def restore_text(original_data: bytes, evidence: dict, start_byte: int, end_byte: int) -> str:
    """Read exact original spelling for a parsed UTF-8 byte range.

    Whole expressions use identical offsets. A parsed replacement keyword's
    shorter token boundary is extended to include the complete original token.
    Never canonicalize transformed text and attempt a spelling-wide reversal.
    """
    if not 0 <= start_byte <= end_byte <= len(original_data):
        raise ValueError('original source restoration range is outside the expanded input')
    for replacement in evidence['replacements']:
        start = replacement['start_byte']
        if start_byte == start and end_byte > start and end_byte == replacement['transformed_token_end_byte']:
            end_byte = replacement.get('original_token_end_byte', replacement['end_byte'])
    return original_data[start_byte:end_byte].decode('utf-8')
