"""Bounded C syntax facts from the selected build compiler expansion."""
import hashlib
import os
import pickle
import re
from collections import namedtuple
from bisect import bisect_left, bisect_right
from itertools import chain
from .chains import chain_root
from .gnu import adapt_source, restore_text

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|0[xX][0-9a-fA-F]+(?:[uUlL]+)?|\d+(?:[uUlL]+)?|==|!=|<=|>=|->|&&|\|\||<<|>>|\+=|-=|\+\+|--|[^\s]')
CANONICAL_TOKEN = re.compile(r'/\*.*?\*/|//[^\n]*|' + TOKEN.pattern, flags=re.S)
_TOKEN_DOTALL = re.compile(TOKEN.pattern, flags=re.S)
_IDENTIFIER_TOKEN = re.compile(r'[A-Za-z_]\w*')
_UNSIGNED_LITERAL = re.compile(r'(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*[uU][uUlL]*')
_INTEGER_LITERAL = re.compile(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)[lL]*')
_LONG_SUFFIX = re.compile(r'[lL]+$')
_SIGNATURE_ATTRIBUTES = re.compile(r'\b(?:__attribute(?:__)?|__declspec|__cdecl|__stdcall|__fastcall|__vectorcall|__thiscall|__regcall|__syscall|__pascal|asm|__asm(?:__)?)\b')
_DECLARATION_MODIFIERS = re.compile(r'\b(?:static|extern|inline|__inline__|__inline)\b')
_BINDING_TYPES = ('type_definition', 'declaration', 'parameter_declaration', 'function_definition', 'enumerator')
_DECLARATOR_SHAPES = ('array_declarator', 'function_declarator')
_NAME_CALLS = {}


def _name_call(name):
    """Compiled pattern for ``name`` used as a call, memoized per process."""
    pattern = _NAME_CALLS.get(name)
    if pattern is None:
        pattern = _NAME_CALLS[name] = re.compile(r'\b' + re.escape(name) + r' \(')
    return pattern


def canonical(text):
    if '/' not in text:
        # Comments need a slash, so without one only the token alternatives apply. DOTALL
        # matches CANONICAL_TOKEN, where a backslash-newline can continue a literal.
        return ' '.join(_TOKEN_DOTALL.findall(text))
    return ' '.join([token for token in CANONICAL_TOKEN.findall(text)
                     if not token.startswith(('/*', '//'))])

def constant_rejection(expression, environment):
    tokens = TOKEN.findall(expression)
    comparisons = {'==', '!=', '<', '<=', '>', '>='}
    if any(_UNSIGNED_LITERAL.fullmatch(token) for token in tokens):
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
    """Evaluate bounded nonnegative C integers using C operator precedence.

    Precedence climbing runs on an explicit task stack: ``tasks`` holds pending parse steps and
    ``values`` the operands already evaluated, so nesting depth in the expression costs no frames."""
    if constant_rejection(expression, environment):
        return None
    tokens = TOKEN.findall(expression)
    count = len(tokens)
    position = 0
    precedence = {'||': 1, '&&': 2, '|': 3, '^': 4, '&': 5,
                  '==': 6, '!=': 6, '<': 7, '<=': 7, '>': 7, '>=': 7,
                  '<<': 8, '>>': 8, '+': 9, '-': 9, '*': 10, '/': 10, '%': 10}
    # Tasks: ('expression', level) parses an operand then its operators of at least ``level``;
    # ('atom',) parses one operand; ('close',) consumes a closing parenthesis; ('unary', token)
    # applies a prefix operator; ('operators', level) continues an expression after an operand;
    # ('combine', operator, level) applies a binary operator to the two newest values.
    tasks, values = [('expression', 1)], []
    try:
        while tasks:
            task = tasks.pop()
            kind = task[0]
            if kind == 'expression':
                tasks.append(('operators', task[1]))
                tasks.append(('atom',))
            elif kind == 'atom':
                if position >= count: raise ValueError('missing operand')
                token = tokens[position]; position += 1
                if token == '(':
                    tasks.append(('close',))
                    tasks.append(('expression', 1))
                elif token in ('+', '!'):
                    tasks.append(('unary', token))
                    tasks.append(('atom',))
                else:
                    if _INTEGER_LITERAL.fullmatch(token):
                        literal = _LONG_SUFFIX.sub('', token)
                        value = int(literal, 16 if literal.lower().startswith('0x') else 8 if len(literal) > 1 and literal.startswith('0') else 10)
                    else:
                        value = environment.get(token)
                        if isinstance(value, dict): value = value.get('value')
                    if not isinstance(value, (int, bool)) or not 0 <= value <= 0x7fffffff: raise ValueError('unbounded operand')
                    values.append(int(value))
            elif kind == 'close':
                if position >= count or tokens[position] != ')': raise ValueError('missing closer')
                position += 1
            elif kind == 'unary':
                if task[1] == '!':
                    values[-1] = int(not values[-1])
            elif kind == 'operators':
                level = task[1]
                if position < count and precedence.get(tokens[position], 0) >= level:
                    operator = tokens[position]; position += 1
                    tasks.append(('combine', operator, level))
                    tasks.append(('expression', precedence[operator] + 1))
            else:
                _, operator, level = task
                right = values.pop()
                left = values.pop()
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
                values.append(value)
                tasks.append(('operators', level))
        return values[0] if position == count else None
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


_NodeContext = namedtuple('_NodeContext', 'scope function_definition function_declarator loops conditionals')


class _TreeIndex:
    """Preorder node list of one tree with per-type positions and subtree extents.

    ``select`` and ``has`` answer the questions a recursive preorder walk of a subtree answers
    (which nodes of these types lie at or below this node, in preorder) without visiting every node.
    """
    __slots__ = ('nodes', 'position', 'extent', 'by_type', 'missing', 'contexts')

    def __init__(self):
        self.nodes, self.position, self.extent = [], {}, []
        self.by_type, self.missing, self.contexts = {}, [], {}

    def _bounds(self, root):
        low = self.position[root.id]
        return low, self.extent[low]

    def _runs(self, types, low, high):
        runs = []
        for name in types:
            positions = self.by_type.get(name)
            if positions:
                first = bisect_left(positions, low)
                last = bisect_left(positions, high, first)
                if first < last:
                    runs.append((first, last, positions))
        return runs

    def select(self, types, root):
        """Nodes whose type is in ``types`` at or below ``root``, in preorder."""
        runs = self._runs(types, *self._bounds(root))
        if not runs:
            return []
        nodes = self.nodes
        if len(runs) == 1:
            first, last, positions = runs[0]
            return [nodes[i] for i in positions[first:last]]
        return [nodes[i] for i in sorted(chain.from_iterable(positions[first:last] for first, last, positions in runs))]

    def select_all(self, types):
        """Nodes whose type is in ``types`` anywhere in the tree, in preorder."""
        nodes = self.nodes
        return [nodes[i] for i in sorted(chain.from_iterable(self.by_type.get(name, ()) for name in types))]

    def has(self, types, root):
        return bool(self._runs(types, *self._bounds(root)))

    def errors(self):
        """Syntax error and missing nodes in preorder."""
        nodes = self.nodes
        return [nodes[i] for i in sorted(set(self.by_type.get('ERROR', ())).union(self.missing))]


_CONTEXT_TYPES = frozenset(('compound_statement', 'function_definition', 'for_statement', 'function_declarator',
                            'while_statement', 'do_statement', 'conditional_expression', 'binary_expression'))


def _tree_index(tree, text):
    """Index one tree: preorder positions plus strict ancestors, sharing immutable loop and byte-predicate chains."""
    index = _TreeIndex()
    nodes, position, extent, by_type, contexts = index.nodes, index.position, index.extent, index.by_type, index.contexts
    stack = [_NodeContext(None, None, None, None, None)]
    open_positions = []
    cursor = tree.walk()
    goto_first_child, goto_next_sibling, goto_parent = cursor.goto_first_child, cursor.goto_next_sibling, cursor.goto_parent
    while True:
        node = cursor.node
        node_type = node.type
        node_id = node.id
        current = len(nodes)
        nodes.append(node)
        position[node_id] = current
        extent.append(current + 1)
        run = by_type.get(node_type)
        if run is None:
            by_type[node_type] = [current]
        else:
            run.append(current)
        context = stack[-1]
        contexts[node_id] = context
        changes_context = node_type in _CONTEXT_TYPES
        if changes_context:
            scope, definition, declarator, loops, conditionals = context
            if node_type in ('compound_statement', 'function_definition', 'for_statement'):
                scope = node
            if node_type == 'function_definition':
                definition = node
            if node_type == 'function_declarator':
                declarator = node
            if node_type in ('for_statement', 'while_statement', 'do_statement'):
                loops = (node.start_byte, loops)
            consequence = (node.child_by_field_name('consequence')
                           or node.child_by_field_name('alternative')) if node_type == 'conditional_expression' else None
            if consequence:
                conditionals = (node.start_byte, consequence.start_byte, conditionals)
            elif (node_type == 'binary_expression'
                  and text(node.child_by_field_name('operator')) in ('&&', '||')):
                conditionals = (node.start_byte, node.child_by_field_name('right').start_byte, conditionals)
        if goto_first_child():
            if changes_context:
                unchanged = (scope is context.scope and definition is context.function_definition
                             and declarator is context.function_declarator
                             and loops is context.loops and conditionals is context.conditionals)
                stack.append(context if unchanged else _NodeContext(scope, definition, declarator, loops, conditionals))
            else:
                stack.append(context)
            open_positions.append(current)
            continue
        while not goto_next_sibling():
            if not goto_parent():
                if tree.root_node.has_error:
                    index.missing = [position_ for position_, candidate in enumerate(nodes) if candidate.is_missing]
                return index
            stack.pop()
            extent[open_positions.pop()] = len(nodes)


def _context_index(tree, text):
    """Strict ancestor contexts keyed by node id."""
    return _tree_index(tree, text).contexts


# Bytes of a run of header text blanked for the parser: every byte but a line break becomes a space.
_BLANK = bytes(value if value in (10, 13) else 32 for value in range(256))
_NEWLINE = re.compile(b'\n')
# Declaration tables of system header text by content, one per distinct run (``_system_runs``); None
# marks a run that stays in the unit's text.
_RUN_TABLES = {}
# The directory in which the processes of one run share those tables, or None.
_RUN_DIRECTORY = None


def clear_run_tables():
    """Forget the declaration tables of the system header runs analysed so far, and stop sharing
    them through a directory."""
    global _RUN_DIRECTORY
    _RUN_TABLES.clear()
    _RUN_DIRECTORY = None


def share_run_tables(directory):
    """Store the tables of each run analysed from now on in ``directory`` (None: stop), and take the
    ones another process stored there instead of analysing the run again.

    Each table is a file named by the hash of the run's key, written whole to a temporary name and
    renamed into place, and read back only after its sha256 matches. The directory belongs to one
    run: it is created by the caller, empty, and removed with the run's fact objects."""
    global _RUN_DIRECTORY
    _RUN_DIRECTORY = directory


def _shared_run_tables(key, data, origins):
    """The tables of the run ``data`` with ``origins``, from the run directory when it holds them."""
    if _RUN_DIRECTORY is None:
        return _run_tables(data, origins)
    path = os.path.join(_RUN_DIRECTORY, hashlib.sha256(key[0] + key[1]).hexdigest())
    try:
        with open(path, 'rb') as stream:
            blob = stream.read()
    except FileNotFoundError:
        tables = _run_tables(data, origins)
        payload = pickle.dumps(tables, protocol=pickle.HIGHEST_PROTOCOL)
        temporary = f'{path}.{os.getpid()}'
        with open(temporary, 'xb') as stream:
            stream.write(hashlib.sha256(payload).digest() + payload)
        os.replace(temporary, path)
        return tables
    if hashlib.sha256(blob[32:]).digest() != blob[:32]:
        raise RuntimeError(f'system header run tables changed during generation: {path}')
    return pickle.loads(blob[32:])


def _parse(data):
    from tree_sitter import Language, Parser
    import tree_sitter_c
    return Parser(Language(tree_sitter_c.language())).parse(data)


class _Syntax:
    """The text, location and declarator helpers of one parsed text, and its tree index.

    ``whole`` is the scope of a declaration outside every block: the text's byte range, or
    ``(None, None)`` for a run of header text, whose range depends on the unit it joins."""
    __slots__ = ('raw', 'text', 'loc', 'name_of', 'declarators', 'point_for_name', 'binding_scope',
                 'index', 'contexts')

    def __init__(self, original_data, origins, evidence, tree, whole):
        text_cache = {}
        def raw(n): return restore_text(original_data, evidence, n.start_byte, n.end_byte) if n else ''
        def text(n):
            if not n: return ''
            key = (n.start_byte, n.end_byte)
            value = text_cache.get(key)
            if value is None:
                value = text_cache[key] = canonical(restore_text(original_data, evidence, key[0], key[1]))
            return value
        def loc(n): return span(origins, n.start_point.row + 1, n.end_point.row + 1)
        def name_of(declarator):
            """First declared identifier in preorder, following a ``declarator`` field where one exists."""
            stack = [declarator] if declarator is not None else []
            while stack:
                node = stack.pop()
                if node.type in ('identifier', 'field_identifier', 'type_identifier'):
                    value = text(node)
                    if value: return value
                    continue
                child = node.child_by_field_name('declarator')
                if child:
                    stack.append(child)
                else:
                    stack.extend(reversed(node.named_children))
            return ''
        def declarators(node):
            declared = node.children_by_field_name('declarator')
            return [c for c in node.named_children if c.type == 'init_declarator' or c in declared or c.type in ('pointer_declarator', 'array_declarator', 'function_declarator', 'identifier')]
        index = _tree_index(tree, text)
        contexts = index.contexts
        def point_for_name(node, name):
            identifier = next((n for n in index.select(('identifier',), node) if text(n) == name), node)
            location = loc(identifier)
            return {**location, 'end_line': location['start_line']}
        def binding_scope(node):
            context = contexts[node.id]
            if node.type == 'parameter_declaration':
                # A definition owns all of its parameters, including nested prototype declarators.
                scope = context.function_definition or context.function_declarator
            else:
                scope = context.scope
            return (scope.start_byte, scope.end_byte) if scope else whole
        self.raw, self.text, self.loc, self.name_of, self.declarators = raw, text, loc, name_of, declarators
        self.point_for_name, self.binding_scope, self.index, self.contexts = point_for_name, binding_scope, index, contexts


def _ordinary_bindings(syntax):
    """The names declared anywhere in the parsed text, each with the scope in which it is visible."""
    bindings = {}
    for declaration in syntax.index.select_all(_BINDING_TYPES):
        if declaration.type in ('type_definition', 'declaration'):
            names = declaration.children_by_field_name('declarator')
        elif declaration.type in ('parameter_declaration', 'function_definition'):
            names = [declaration.child_by_field_name('declarator')]
        elif declaration.type == 'enumerator':
            names = [declaration.child_by_field_name('name')]
        else:
            continue
        for declarator in names:
            name = syntax.name_of(declarator)
            if not name:
                continue
            start, end = syntax.binding_scope(declaration)
            bindings.setdefault(name, []).append({
                'name': name, 'typedef': declaration.type == 'type_definition',
                'declaration_id': declarator.start_byte, 'source_span': syntax.loc(declaration),
                'visible_start': declarator.end_byte if declaration.type == 'type_definition' else declarator.start_byte,
                'scope_start': start, 'scope_end': end})
    return bindings


def _global_functions(syntax, nodes):
    """The function definitions and function declarations among the top-level ``nodes``, in order, as
    ``(position, name, declaration)``."""
    found = []
    for node in nodes:
        if node.type == 'function_definition':
            name = syntax.name_of(node.child_by_field_name('declarator'))
            found.append((node.start_byte, name, {'name': name, 'kind': 'FUNCTION_DECL', 'source_span': syntax.point_for_name(node.child_by_field_name('declarator'), name)}))
        elif node.type == 'declaration' and '=' not in syntax.text(node):
            for decl in syntax.declarators(node):
                name = syntax.name_of(decl.child_by_field_name('declarator') if decl.type == 'init_declarator' else decl)
                if name and _name_call(name).search(syntax.text(decl)):
                    found.append((node.start_byte, name, {'name': name, 'kind': 'FUNCTION_DECL', 'source_span': syntax.point_for_name(decl, name)}))
    return found


def _complete(node, data):
    """Whether no token that follows the top-level ``node`` in ``data`` continues it: it is a function
    definition or ends with a semicolon. (A bare ``struct s { ... }`` is a top-level node too, which a
    declarator after it continues.)"""
    return node.type == 'function_definition' or data[node.end_byte - 1:node.end_byte] == b';'


class _RunTables:
    """What the frontend looks up by name in a run of system header text, as tuples that take little
    memory: ``functions`` maps a name to ``(position, source, first line, last line)`` for each function
    declaration, and ``bindings`` maps a name to ``(typedef, declaration id, visible start, scope start,
    scope end, source, first line, last line)`` for each ordinary binding. Byte positions are relative
    to the start of the run, and a scope start and end of None is the scope of the whole text."""
    __slots__ = ('functions', 'bindings')

    def __init__(self, functions, bindings):
        self.functions, self.bindings = functions, bindings


def _run_tables(data, origins):
    """The tables of the run of header text ``data`` whose lines come from ``origins``, or None when
    the run cannot stand for itself in the unit's text.

    A run stands for itself when its own GNU adaptation succeeds without projecting anything, its own
    parse has no error, and its last top-level node is complete (``_complete``), so that it holds only
    whole top-level declarations and the text around it is parsed as when it is present."""
    try:
        _, evidence = adapt_source(data.decode('utf-8'), origins)
    except ValueError:
        return None
    if evidence['replacements'] or evidence['unassessed']:
        return None
    tree = _parse(data)
    children = tree.root_node.children
    if tree.root_node.has_error or not children or not _complete(children[-1], data):
        return None
    syntax = _Syntax(data, origins, evidence, tree, (None, None))
    functions = {}
    for position, name, declaration in _global_functions(syntax, tree.root_node.named_children):
        span = declaration['source_span']
        functions.setdefault(name, []).append((position, span['source'], span['start_line'], span['end_line']))
    bindings = {name: [(binding['typedef'], binding['declaration_id'], binding['visible_start'],
                        binding['scope_start'], binding['scope_end'], binding['source_span']['source'],
                        binding['source_span']['start_line'], binding['source_span']['end_line'])
                       for binding in found]
                for name, found in _ordinary_bindings(syntax).items()}
    return _RunTables(functions, bindings)


def _system_runs(original_data, origins, physical, record):
    """The runs of consecutive lines of system header text in a unit that have tables, as
    ``(start byte, end byte, tables)`` in order.

    System header text is every line that comes from a captured dependency that is neither the unit's
    own file nor a project header. Each distinct run (its bytes and the origins of its lines) is
    analysed once per process."""
    owned_sources = {physical, *record.get('project_dependencies', ())}
    system = {path for path in record['dependencies'] if path not in owned_sources}
    if not system:
        return []
    line_starts = [0, *(match.end() for match in _NEWLINE.finditer(original_data))]
    lines = len(line_starts) - (1 if not original_data or original_data.endswith(b'\n') else 0)
    if len(origins) != lines:
        return []
    view = memoryview(original_data)
    runs, first = [], None
    for row, origin in enumerate(origins):
        if origin[0] in system:
            if first is None:
                first = row
        elif first is not None:
            runs.append((first, row))
            first = None
    if first is not None:
        runs.append((first, lines))
    found = []
    for first, last in runs:
        start = line_starts[first]
        end = line_starts[last] if last < len(line_starts) else len(original_data)
        key = (hashlib.sha256(view[start:end]).digest(), hashlib.sha256(repr(origins[first:last]).encode()).digest())
        if key not in _RUN_TABLES:
            _RUN_TABLES[key] = _shared_run_tables(key, bytes(view[start:end]), origins[first:last])
        if _RUN_TABLES[key] is not None:
            found.append((start, end, _RUN_TABLES[key]))
    return found


def _isolated(tree, data, runs):
    """Whether the text ``data`` parsed as ``tree`` with ``runs`` blanked is the text around them as it
    is parsed with them present: it has no error, the top-level node before each run is complete (or
    there is none), and no top-level node spans a run. A run's own last top-level node is complete
    (``_run_tables``)."""
    root = tree.root_node
    if root.has_error:
        return False
    children = root.children
    starts, ends = [child.start_byte for child in children], [child.end_byte for child in children]
    for start, end, _ in runs:
        before = bisect_right(ends, start) - 1
        if before >= 0 and not _complete(children[before], data):
            return False
        after = bisect_left(starts, end) - 1
        if after >= 0 and ends[after] > start:
            return False
    return True


class _SystemTables:
    """The tables of a unit's blanked system runs, looked up by name in the unit's byte positions."""
    __slots__ = ('runs', 'length', '_functions', '_bindings')

    def __init__(self, runs, length):
        self.runs, self.length = runs, length
        self._functions, self._bindings = {}, {}

    def functions(self, name):
        """The function declarations of ``name`` in the runs, as ``(position, declaration)``."""
        found = self._functions.get(name)
        if found is None:
            found = self._functions[name] = [
                (start + position, {'name': name, 'kind': 'FUNCTION_DECL',
                                    'source_span': {'source': source, 'start_line': first, 'end_line': last}})
                for start, _, tables in self.runs
                for position, source, first, last in tables.functions.get(name, ())]
        return found

    def bindings(self, name):
        """The ordinary bindings of ``name`` in the runs, with the unit's byte positions."""
        found = self._bindings.get(name)
        if found is None:
            found = self._bindings[name] = [
                {'name': name, 'typedef': typedef, 'declaration_id': start + declaration,
                 'source_span': {'source': source, 'start_line': first, 'end_line': last},
                 'visible_start': start + visible,
                 'scope_start': 0 if scope_start is None else start + scope_start,
                 'scope_end': self.length if scope_end is None else start + scope_end}
                for start, _, tables in self.runs
                for typedef, declaration, visible, scope_start, scope_end, source, first, last
                in tables.bindings.get(name, ())]
        return found


class _GlobalFunctions:
    """The function declarations of a unit by name, in text order: those of its own parsed text and
    those in its blanked system runs."""
    __slots__ = ('_own', '_system', '_merged')

    def __init__(self, own, system):
        self._own, self._system, self._merged = own, system, {}

    def get(self, name, default):
        merged = self._merged.get(name)
        if merged is None:
            found = [*self._own.get(name, ()), *(self._system.functions(name) if self._system else ())]
            merged = self._merged[name] = [declaration for _, declaration in sorted(found, key=lambda item: item[0])]
        return merged or default

    def __contains__(self, name):
        return bool(self.get(name, ()))


def syntax_adapter(code, origins, physical, record, *, cached_runs=True):
    """Declarations, functions, errors and frontend tables of the expanded unit ``code``.

    With ``cached_runs`` the parts of ``code`` that come from system headers are not adapted, parsed
    or indexed with the unit when they can stand for themselves (``_run_tables``): the parser sees them
    blanked, and the frontend looks the names they declare up in tables made once per distinct run.
    A unit whose blanked text is not isolated from them, or fails to adapt, is processed whole."""
    original_data = code.encode()
    runs = _system_runs(original_data, origins, physical, record) if cached_runs else []
    if runs:
        blanked = bytearray(original_data)
        for start, end, _ in runs:
            blanked[start:end] = original_data[start:end].translate(_BLANK)
        blanked = bytes(blanked)
        try:
            parse_code, syntax_evidence = adapt_source(blanked.decode('utf-8'), origins)
        except ValueError:
            return syntax_adapter(code, origins, physical, record, cached_runs=False)
    else:
        parse_code, syntax_evidence = adapt_source(code, origins)
    data = parse_code.encode()
    tree = _parse(data)
    if runs:
        if not _isolated(tree, data, runs):
            return syntax_adapter(code, origins, physical, record, cached_runs=False)
        whole = bytearray(data)
        for start, end, _ in runs:
            whole[start:end] = original_data[start:end]
        syntax_evidence['original_sha256'] = hashlib.sha256(original_data).hexdigest()
        syntax_evidence['transformed_sha256'] = hashlib.sha256(whole).hexdigest()
    syntax = _Syntax(original_data, origins, syntax_evidence, tree, (0, len(data)))
    raw, text, loc, name_of, declarators = syntax.raw, syntax.text, syntax.loc, syntax.name_of, syntax.declarators
    point_for_name, binding_scope = syntax.point_for_name, syntax.binding_scope
    tree_index, contexts = syntax.index, syntax.contexts
    system_tables = _SystemTables(runs, len(data)) if runs else None
    def selected_constants(expression, environment):
        names = {token for token in TOKEN.findall(expression) if _IDENTIFIER_TOKEN.fullmatch(token)}
        return {name: environment[name] for name in sorted(names) if name in environment}
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
    signature_attribute_types = tuple(name for name in tree_index.by_type if 'attribute' in name or name in (
        'ms_call_modifier', 'ms_declspec_modifier', 'gnu_asm_expression'))

    def scope_node(node):
        return contexts[node.id].scope or node

    def metadata(node):
        scope = scope_node(node)
        loops, conditional_evaluation = [], []
        context = contexts[node.id]
        loop = context.loops
        while loop:
            start, loop = loop
            loops.append(start)
        conditional = context.conditionals
        while conditional:
            start, threshold, conditional = conditional
            if node.start_byte >= threshold:
                conditional_evaluation.append(start)
        return {'byte_start': node.start_byte, 'byte_end': node.end_byte,
                'scope_id': scope.start_byte, 'scope_start': scope.start_byte,
                'scope_end': scope.end_byte, 'loops': loops,
                'conditional_evaluation': conditional_evaluation}

    ordinary_bindings = _ordinary_bindings(syntax)

    def binding_candidates(name):
        found = ordinary_bindings.get(name, [])
        return [*found, *system_tables.bindings(name)] if system_tables else found

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
        bindings = [binding for binding in binding_candidates(name)
                    if binding['visible_start'] <= node.start_byte
                    and binding['scope_start'] <= node.start_byte < binding['scope_end']]
        selected = max(bindings, key=lambda binding: (binding['scope_start'], binding['visible_start'])) if bindings else None
        if not selected or not selected['typedef']:
            return None
        authority = {'resolution': 'source-resolved', 'name': name,
                     'declaration_id': selected['declaration_id'], 'source_span': selected['source_span']}
        return identifiers[0], operands[0], authority

    def expression_fact(root):
        """Nested expression fact of ``root``; nodes 24 levels below it are recorded unresolved."""
        if root is None:
            return None
        result = None
        # Each entry is (syntax node, depth, container, key): the fact built for the node is stored
        # at container[key], or becomes the result for the root.
        stack = [(root, 0, None, None)]
        while stack:
            node, depth, container, key = stack.pop()
            fact = {'kind': node.type, 'expression': text(node), **metadata(node)}
            if container is None:
                result = fact
            else:
                container[key] = fact
            if depth >= 24:
                fact['unresolved'] = 'expression nesting exceeds bounded def/use model'
                continue
            cast = typedef_cast(node)
            if cast:
                type_node, value_node, authority = cast
                fact.update(kind='cast_expression', type={
                    'kind': 'type_descriptor', 'expression': text(type_node), **metadata(type_node)},
                    value=None, typedef_authority=authority)
                if value_node is not None:
                    stack.append((value_node, depth + 1, fact, 'value'))
                continue
            operator = node.child_by_field_name('operator')
            if operator is not None:
                fact['operator'] = text(operator)
            fields = {
                'call_expression': ('function', 'arguments'),
                'subscript_expression': ('argument', 'index'),
                'field_expression': ('argument', 'field'),
                'pointer_expression': ('argument',),
                'unary_expression': ('argument',),
                'update_expression': ('argument',),
                'initializer_pair': ('value',),
                'cast_expression': ('type', 'value'),
                'assignment_expression': ('left', 'right'),
                'binary_expression': ('left', 'right'),
                'comma_expression': ('left', 'right'),
                'conditional_expression': ('condition', 'consequence', 'alternative'),
            }.get(node.type, ())
            pending = []
            for field in fields:
                child = node.child_by_field_name(field)
                if child:
                    fact[field] = None
                    pending.append((child, depth + 1, fact, field))
            if node.type in ('argument_list', 'initializer_list', 'parenthesized_expression', 'compound_literal_expression', 'concatenated_string'):
                children = [child for child in node.named_children if child.type != 'comment']
                slots = fact['children'] = [None] * len(children)
                pending.extend((child, depth + 1, slots, index) for index, child in enumerate(children))
            if operator:
                fact['operator'] = text(operator)
            elif node.type in ('pointer_expression', 'field_expression'):
                fact['operator'] = next((text(child) for child in node.children if not child.is_named), '')
            stack.extend(reversed(pending))
        return result
    def initializer_table(root):
        """Initializer facts of ``root`` and of its nested initializer syntax, in preorder.

        A fact refers to nested facts by position in the returned list: ``fields`` and
        ``designators`` hold lists of positions and ``value`` a position or None."""
        nodes = []
        stack = [(root, None, None)]
        while stack:
            node, parent, slot = stack.pop()
            position = len(nodes)
            fact = {'expression': text(node), 'raw_expression': raw(node),
                    'source_span': loc(node), 'syntax_kind': node.type}
            nodes.append(fact)
            if slot == 'value':
                parent['value'] = position
            elif slot is not None:
                parent[slot].append(position)
            pending = []
            if node.type == 'initializer_list':
                fact['fields'] = []
                pending = [(child, fact, 'fields') for child in node.named_children if child.type != 'comment']
            elif node.type == 'initializer_pair':
                value = node.child_by_field_name('value')
                fact['value'] = None
                fact['designators'] = []
                pending = [(value, fact, 'value')] if value else []
                pending.extend((child, fact, 'designators') for child in node.named_children if child != value and child.type != 'comment')
            stack.extend(reversed(pending))
        return nodes

    def variable_initializer(declaration, declarator):
        actual = declarator.child_by_field_name('declarator') if declarator.type == 'init_declarator' else declarator
        initializer = declarator.child_by_field_name('value') if declarator.type == 'init_declarator' else None
        arrays = tree_index.select(('array_declarator',), actual)
        nodes = initializer_table(initializer) if initializer else []
        rows = [nodes[position] for position in nodes[0]['fields']] if initializer and initializer.type == 'initializer_list' else []
        for ordinal, row in enumerate(rows):
            row['initializer_ordinal'] = ordinal
        return {'type_expression': text(declaration.child_by_field_name('type')),
                'declaration_specifiers': canonical(original_data[declaration.start_byte:actual.start_byte].decode('utf-8')),
                'declarator_expression': text(actual), 'array_dimensions': [
                    {'expression': text(node.child_by_field_name('size')), 'source_span': loc(node)} for node in arrays],
                'initializer': nodes[0] if nodes else None, 'initializer_nodes': nodes,
                'initializer_rows': rows, 'initializer_row_count': len(rows),
                'initializer_constants': selected_constants(text(actual) + ' ' + text(initializer), constants)}

    def enum_constants(node, base, *, inplace=False):
        values = base if inplace else dict(base)
        for enum in tree_index.select(('enum_specifier',), node):
            previous = -1
            for child in tree_index.select(('enumerator',), enum):
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
        aggregates = [candidate for node in globals_
                      for candidate in tree_index.select(('struct_specifier', 'union_specifier'), node)
                      if candidate.child_by_field_name('body')]
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
        if (_SIGNATURE_ATTRIBUTES.search(original)
                or any(child.end_byte <= end for child in tree_index.select(signature_attribute_types, node))):
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
                if not tree_index.has(('function_declarator',), alias):
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
                            declarator and tree_index.has(_DECLARATOR_SHAPES, declarator)):
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
        for enum in tree_index.select(('enum_specifier',), body):
            for child in tree_index.select(('enumerator',), enum):
                declarations.setdefault(text(child.child_by_field_name('name')), []).append(loc(child))
        for name, locations in declarations.items():
            if len(locations) > 1:
                environment[name] = {'value': None, 'source_span': locations[-1],
                                     'unresolved': 'repeated local enumerator names require lexical-scope binding',
                                     'conflicting_sources': locations}
        return selected_constants(text(node), environment)

    own_functions, constants = {}, {}
    for position, name, declaration in _global_functions(syntax, tree.root_node.named_children):
        own_functions.setdefault(name, []).append((position, declaration))
    global_functions = _GlobalFunctions(own_functions, system_tables)
    for node in tree.root_node.named_children:
        if node.type != 'function_definition' and owned(loc(node)['source'], physical, record):
            constants = enum_constants(node, constants, inplace=True)
    declarations, functions = [], []
    for node in tree.root_node.named_children:
        if not owned(loc(node)['source'], physical, record): continue
        if node.type == 'declaration':
            children = declarators(node)
            prefix = canonical(restore_text(original_data, syntax_evidence, node.start_byte, children[0].start_byte)) if children else ''
            for child in children:
                declared_name = name_of(child)
                if child.type != 'init_declarator' and declared_name and _name_call(declared_name).search(text(child)): continue
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
            declaration_modifiers = _DECLARATION_MODIFIERS.findall(original_prefix)
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
                            or tree_index.has(_DECLARATOR_SHAPES, declarator)):
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
            for child in tree_index.select(('type_definition', 'declaration', 'assignment_expression'), body):
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
                        for pointer in tree_index.select(('pointer_expression',), left):
                            for identifier in tree_index.select(('identifier',), pointer):
                                possible_name = text(identifier)
                                if possible_name in global_functions:
                                    variables.append({'name': possible_name, 'expression': text(child), 'source_span': loc(child), 'declaration_point': point_for_name(pointer, possible_name), 'unresolved': 'typedef-dependent declaration/expression ambiguity; possible local binding'})
            shadows = {v['name'] for v in variables} | set(params)
            events = {'calls': [], 'returns': [], 'variables': variables, 'conditions': [], 'control_expressions': [], 'writes': [], 'expressions': []}
            if body_barriers:
                events['semantic_unassessed'] = body_barriers
            if tree_index.has(('goto_statement', 'labeled_statement', 'switch_statement'), body):
                events['control_flow_unresolved'] = ['jump/label/switch flow unassessed']
            terminated = {}
            def terminates(statement):
                """Whether every path through ``statement`` reaches a return, memoized per syntax node."""
                stack = [statement]
                while stack:
                    child = stack[-1]
                    if child.id in terminated:
                        stack.pop()
                        continue
                    if child.type in ('compound_statement', 'else_clause'):
                        parts = child.named_children
                    elif child.type == 'if_statement':
                        consequence, alternative = child.child_by_field_name('consequence'), child.child_by_field_name('alternative')
                        parts = [consequence, alternative] if consequence is not None and alternative is not None else None
                    else:
                        terminated[child.id] = child.type == 'return_statement'
                        stack.pop()
                        continue
                    pending = [part for part in parts or () if part.id not in terminated]
                    if pending:
                        stack.extend(reversed(pending))
                        continue
                    stack.pop()
                    if child.type == 'if_statement':
                        terminated[child.id] = parts is not None and all(terminated[part.id] for part in parts)
                    else:
                        terminated[child.id] = any(terminated[part.id] for part in parts)
                return terminated[statement.id]
            guard_parents, guard_entries = [], []
            events['guard_table'] = {'parents': guard_parents, 'guards': guard_entries}
            def visit(root):
                """Record events in source order with the guards under which each is reached.

                Guards are views into the function's ``guard_table``: each branch adds one entry
                under the guards of its enclosing branch, so nested paths share their prefixes.
                Work items are ('visit', node, guards) or ('statements', statements, index, guards),
                the latter resuming a compound statement after its statement ``index`` was visited."""
                work = [('visit', root, chain_root(guard_parents, guard_entries))]
                while work:
                    item = work.pop()
                    if item[0] == 'statements':
                        _, statements, index, current = item
                        statement = statements[index]
                        if terminates(statement):
                            continue
                        if statement.type == 'if_statement':
                            consequence, alternative = statement.child_by_field_name('consequence'), statement.child_by_field_name('alternative')
                            then_end = consequence is not None and terminates(consequence)
                            else_end = alternative is not None and terminates(alternative)
                            if then_end != else_end:
                                condition = text(statement.child_by_field_name('condition'))
                                if condition.startswith('( ') and condition.endswith(' )'): condition = condition[2:-2]
                                current = current.extend({'condition': condition, 'branch': 'else' if then_end else 'then',
                                                          'condition_id': statement.start_byte, 'source_span': loc(statement)})
                        if index + 1 < len(statements):
                            work.append(('statements', statements, index + 1, current))
                            work.append(('visit', statements[index + 1], current))
                        continue
                    _, child, guards = item
                    if child.end_byte <= body_start:
                        continue
                    if child.type == 'declaration':
                        for variable in variables:
                            if child.start_byte <= variable.get('byte_start', -1) < child.end_byte:
                                variable['guards'] = guards
                    if child.type == 'compound_statement':
                        statements = child.named_children
                        if statements:
                            current = guards
                            work.append(('statements', statements, 0, current))
                            work.append(('visit', statements[0], current))
                        continue
                    if child.type == 'if_statement':
                        condition_node = child.child_by_field_name('condition')
                        condition = text(condition_node)
                        if condition.startswith('( ') and condition.endswith(' )'): condition = condition[2:-2]
                        identity = loc(child)
                        events['conditions'].append({'control_kind': 'if_statement', 'expression': condition, 'expression_node': expression_fact(condition_node),
                                                     'source_span': identity, 'guards': guards, **metadata(condition_node)})
                        events['conditions'][-1]['condition_id'] = child.start_byte
                        consequence = child.child_by_field_name('consequence')
                        alternative = child.child_by_field_name('alternative')
                        then_guards = guards.extend({'condition': condition, 'branch': 'then', 'condition_id': child.start_byte, 'source_span': identity}) if consequence else None
                        else_guards = guards.extend({'condition': condition, 'branch': 'else', 'condition_id': child.start_byte, 'source_span': identity}) if alternative else None
                        if alternative: work.append(('visit', alternative, else_guards))
                        if consequence: work.append(('visit', consequence, then_guards))
                        work.append(('visit', condition_node, guards))
                        continue
                    if child.type in ('for_statement', 'while_statement', 'do_statement', 'switch_statement'):
                        control = child.child_by_field_name('condition')
                        if control:
                            events['control_expressions'].append({
                                'expression': text(control), 'expression_node': expression_fact(control),
                                'control_kind': child.type, 'source_span': loc(control),
                                'guards': guards, **metadata(control)})
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
                    work.extend(('visit', nested, guards) for nested in reversed(child.named_children))
            visit(body)
            def flow(root):
                """Structured control flow of ``root`` as a preorder node table.

                Node 0 is ``root``. ``children``, ``then``, ``else`` and ``body`` hold positions of
                other nodes in the table, so the table nests no deeper than one node."""
                table = []
                # Each entry is (syntax node, parent node, slot); a None syntax node in an ``else``
                # slot is the fallthrough of an if statement without an else clause.
                work = [(root, None, None)]
                while work:
                    child, parent, slot = work.pop()
                    position = len(table)
                    pending = []
                    if child is None and slot == 'else':
                        entry = {'kind': 'fallthrough'}
                    elif child.type in ('compound_statement', 'else_clause'):
                        entry = {'kind': 'block', 'children': []}
                        pending = [(nested, entry, 'children') for nested in child.named_children if nested.type != 'comment']
                    elif child.type == 'if_statement':
                        condition = text(child.child_by_field_name('condition'))
                        if condition.startswith('( ') and condition.endswith(' )'):
                            condition = condition[2:-2]
                        entry = {'kind': 'if', 'condition': condition, 'byte_start': child.start_byte,
                                 'condition_start': child.child_by_field_name('condition').start_byte,
                                 'condition_end': child.child_by_field_name('condition').end_byte,
                                 'then': None, 'else': None}
                        pending = [(child.child_by_field_name('consequence'), entry, 'then'),
                                   (child.child_by_field_name('alternative'), entry, 'else')]
                    elif child.type == 'return_statement':
                        entry = {'kind': 'return', 'byte_start': child.start_byte, 'byte_end': child.end_byte, 'loops': metadata(child)['loops']}
                    elif (child.type == 'expression_statement' and len(child.named_children) == 1
                            and child.named_children[0].type == 'call_expression'
                            and typedef_cast(child.named_children[0]) is None):
                        entry = {'kind': 'call', 'byte_start': child.named_children[0].start_byte,
                                 'byte_end': child.named_children[0].end_byte}
                    elif child.type in ('for_statement', 'while_statement', 'do_statement'):
                        entry = {'kind': 'loop', 'loop_kind': child.type, 'byte_start': child.start_byte,
                                 'initializer_expression': text(child.child_by_field_name('initializer')) if child.child_by_field_name('initializer') else None,
                                 'condition_expression': text(child.child_by_field_name('condition')) if child.child_by_field_name('condition') else None,
                                 'update_expression': text(child.child_by_field_name('update')) if child.child_by_field_name('update') else None,
                                 'body': None}
                        pending = [(child.child_by_field_name('body'), entry, 'body')]
                    elif child.type in ('continue_statement', 'break_statement'):
                        entry = {'kind': child.type.removesuffix('_statement'), 'byte_start': child.start_byte}
                    elif child.type == 'goto_statement':
                        entry = {'kind': 'goto', 'target': text(child.child_by_field_name('label')), 'byte_start': child.start_byte}
                    elif child.type == 'labeled_statement':
                        entry = {'kind': 'label', 'name': text(child.child_by_field_name('label')), 'byte_start': child.start_byte,
                                 'children': []}
                        pending = [(nested, entry, 'children') for nested in child.named_children if nested.type != 'statement_identifier']
                    else:
                        entry = {'kind': 'fallthrough', 'byte_start': child.start_byte, 'byte_end': child.end_byte}
                    table.append(entry)
                    if slot == 'children':
                        parent['children'].append(position)
                    elif slot is not None:
                        parent[slot] = position
                    work.extend(reversed(pending))
                return table
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
                                  'declaration_prefix_expression': original_prefix,
                                  'kind': 'old-style-identifier-list' if legacy_headers else 'parsed-C-declaration',
                                  'parameter_names': params, 'source_evidence': legacy_headers,
                                  'declarations': parameter_declarations,
                                  'declaration_binding_resolution': 'resolved' if parameter_declarations_resolved else 'unresolved',
                                  'unresolved': [] if parameter_declarations_resolved else ['original identifier list and parsed declaration-list declarators do not form a complete unique binding'],
                                  'abi_resolution': 'unassessed'}})
    errors = [{'source_span': loc(n), 'reason': 'tree-sitter ' + n.type, 'operational': True} for n in tree_index.errors()]
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
