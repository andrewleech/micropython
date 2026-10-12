"""Bounded source-qualified runtime helper and argument-table contracts."""
import re
from copy import deepcopy
from functools import cached_property
from bisect import bisect_right
from collections.abc import Mapping
from heapq import merge
from .frontend import constant
from .bindings import call_parts, constant_value, errors_at, intersect, strip_outer

CONSTRUCTORS = {'mp_obj_new_bool': 'bool', 'mp_obj_new_int': 'int', 'mp_obj_new_int_from_uint': 'int', 'mp_obj_new_int_from_ll': 'int', 'mp_obj_new_int_from_ull': 'int', 'mp_obj_new_bytes': 'bytes', 'mp_obj_new_bytes_from_vstr': 'bytes', 'mp_obj_new_str': 'str', 'mp_obj_new_str_from_utf8_vstr': 'str'}
CONSTRUCTORS.update({'mp_obj_new_list': 'list', 'mp_obj_new_float': 'float'})
CONSTRUCTORS.update({'mp_obj_new_bytearray': 'bytearray', 'mp_obj_new_bytearray_by_ref': 'bytearray'})
CONSTRUCTORS['mp_obj_new_str_from_vstr'] = 'str'
INPUT_HELPERS = {'mp_obj_is_true': 'object/truth-tested', 'mp_obj_get_int': 'integer-conversion/helper-boundary', 'mp_get_buffer_raise': 'buffer-capability', 'mp_obj_get_array_fixed_n': 'tuple-or-list/exact-length', 'mp_obj_str_get_str': 'str-or-bytes', 'mp_module_generic_attr': 'registered-module-key-table'}
INPUT_HELPERS.update({'mp_obj_str_get_data': 'str-or-bytes', 'check_is_str_or_bytes': 'str-or-bytes',
                      'str_check_arg_type': 'same-native-string-family'})

_INPUT_DEPTH = 16


class _QueryFunction(Mapping):
    """Read-only function evidence with analysis-lifetime indexes and memo tables."""

    def __init__(self, function):
        self.function = function
        events = function['events']
        self.variables = {}
        self.writes = {}
        self.identifier_writes = {}
        self.indirect_writes = []
        self.locals = {}
        self.binding_mentions = {}
        self.parameter_roots = {}
        self.parameter_refs = {}
        self.address_names = {}
        self.alias_sources = {}
        self.root_sources = {}
        self.identities = {}
        self.binding_bits = {}
        self.reachable = {}
        for variable in events['variables']:
            self.variables.setdefault(variable['name'], []).append(variable)
        for ordinal, write in enumerate(events['writes']):
            self.writes.setdefault(write.get('target'), []).append(write)
            target = _unparen(write.get('target_node'))
            if target.get('kind') == 'identifier':
                name = target.get('expression')
                self.identifier_writes.setdefault(name, []).append((ordinal, write))
            else:
                self.indirect_writes.append((ordinal, write))
        intervals = []
        for call in events['calls']:
            if call.get('byte_start') is not None and call.get('byte_end') is not None:
                intervals.append((call['byte_start'], call['byte_end']))
        intervals.sort()
        self.call_starts = []
        self.call_ends = []
        end = -1
        for start, stop in intervals:
            self.call_starts.append(start)
            end = max(end, stop)
            self.call_ends.append(end)

    def __getitem__(self, key):
        return self.function[key]

    def __iter__(self):
        return iter(self.function)

    def __len__(self):
        return len(self.function)

    def identity(self, evidence):
        identity = id(evidence)
        # Synthetic use records must stay alive while their identity is a memo key.
        self.identities[identity] = evidence
        return identity

    def parameter_ref(self, index):
        """One shared c-parameter reference per parameter index."""
        ref = self.parameter_refs.get(index)
        if ref is None:
            ref = self.parameter_refs[index] = {'kind': 'c-parameter', 'scope': self.function['id'], 'parameter': index}
        return ref

    @cached_property
    def parse_calls(self):
        """Qualified mp_arg_parse_all calls; the filter does not depend on any consumer."""
        return [call for call in self.function['events']['calls']
                if call_parts(call['expression'])[0] == 'mp_arg_parse_all' and qualified_template(call)]

    @cached_property
    def calls_by_expression(self):
        index = {}
        for call in self.function['events']['calls']:
            index.setdefault(call['expression'], []).append(call)
        return index

    def call_end(self, point):
        index = bisect_right(self.call_starts, point) - 1
        return self.call_ends[index] if index >= 0 and self.call_ends[index] > point else -1

    def writes_for_binding(self, name, declaration, start=-1, end=None, use=None):
        candidates = merge(self.identifier_writes.get(name, ()), self.indirect_writes, key=lambda item: item[0])
        for _, write in candidates:
            if (not write.get('value_node') or write.get('byte_start', -1) < start
                    or end is not None and write.get('byte_start', -1) >= end
                    or use is not None and _exclusive(write, use)):
                continue
            target = _unparen(write.get('target_node'))
            if target.get('kind') == 'identifier':
                local = _local(self, name, write)
                affected = (local.get('declaration_id') if local else None) == declaration
            else:
                affected = _input_binding_mention(self, target, write, name, declaration)
            if affected:
                yield write

    def alias_origins(self, name, local, use):
        key = name, local.get('declaration_id'), self.identity(use)
        if key not in self.alias_sources:
            point = _point(use)
            origins = [(local.get('initializer_node') or {}, local)]
            if point is not None:
                origins.extend((write['value_node'], write) for write in self.writes.get(name, ())
                               if write.get('value_node')
                               and local.get('byte_start', -1) <= write.get('byte_start', -1) < point
                               and not _exclusive(write, use)
                               and (_local(self, name, write) or {}).get('declaration_id') == local.get('declaration_id'))
            self.alias_sources[key] = origins
        return self.alias_sources[key]

    def root_origins(self, name, local, use):
        declaration = local.get('declaration_id') if local else None
        key = name, declaration, self.identity(use)
        if key not in self.root_sources:
            point = _point(use)
            origins = [(local.get('initializer_node') or {}, local)] if local else []
            if point is not None:
                end = max(point or -1, use.get('byte_end', point) or -1, self.call_end(point))
                start = local.get('byte_start', -1) if local else -1
                origins.extend((write['value_node'], write)
                               for write in self.writes_for_binding(name, declaration, start, end, use))
            self.root_sources[key] = origins
        return self.root_sources[key]

    def binding_bit(self, identity):
        if identity not in self.binding_bits:
            self.binding_bits[identity] = 1 << len(self.binding_bits)
        return self.binding_bits[identity]

    def reachable_bindings(self, node, use, remaining, alias):
        node = _unparen(node)
        if not node or remaining <= 0:
            return 0
        key = self.identity(node), self.identity(use), remaining, alias
        if key not in self.reachable:
            bindings = 0
            if node.get('kind') == 'identifier':
                name = node.get('expression')
                local = _local(self, name, use)
                bindings = self.binding_bit((name, local.get('declaration_id') if local else None))
                if remaining > 1:
                    origins = (self.alias_origins(name, local, use) if local else ()) if alias else self.root_origins(name, local, use)
                    for origin, event in origins:
                        bindings |= self.reachable_bindings(origin, event, remaining - 1, alias)
            elif remaining > 1:
                for child in _input_children(node, alias):
                    bindings |= self.reachable_bindings(child, use, remaining - 1, alias)
            self.reachable[key] = bindings
        return self.reachable[key]

    def seen_key(self, node, use, seen, depth, alias):
        if not seen:
            return 0
        reachable = self.reachable_bindings(node, use, _INPUT_DEPTH - depth, alias)
        blocked = 0
        for identity in seen:
            blocked |= self.binding_bit(identity)
        # Only bindings reachable before the cutoff can observe path-local blocking.
        return reachable & blocked


def _query_function(function):
    return function if isinstance(function, _QueryFunction) else _QueryFunction(function)


def _input_children(node, alias):
    fields = {
        'call_expression': ('arguments',), 'cast_expression': ('value',),
        'field_expression': ('argument',),
        'subscript_expression': ('argument',) if alias else ('argument', 'index'),
    }.get(node.get('kind'))
    if fields:
        for field in fields:
            yield node.get(field)
    else:
        for value in node.values():
            if isinstance(value, dict):
                yield value
    for value in node.values():
        if isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    yield child


def qualified_template(call):
    callee = call.get('callee') if call else None
    return bool(callee and callee['kind'] == 'FUNCTION_DECL' and callee['source_span']['source'] in call.get('qualified_template_sources', []))


def _qualified_error_exit(call):
    helper = call_parts(call['expression'])[0] if call else None
    return helper in {'mp_raise_ValueError', 'mp_raise_TypeError', 'mp_raise_OverflowError', 'mp_raise_OSError',
                      'mp_raise_msg', 'mp_raise_msg_varg', 'mp_raise_NotImplementedError', 'nlr_raise'} and qualified_template(call)


def event_call(function, expression, location=None):
    if function['events'].get('semantic_unassessed'):
        return None
    calls = (function.calls_by_expression.get(expression, ()) if isinstance(function, _QueryFunction)
             else [c for c in function['events']['calls'] if c['expression'] == expression])
    candidates = [c for c in calls if
                  (location is None or location.get('byte_start') is not None and
                   location['byte_start'] <= c.get('byte_start', -1) < location.get('byte_end', location['byte_start'] + 1)
                   or location.get('byte_start') is None and intersect(c['source_span'], location))]
    if not candidates:
        return None
    callee = candidates[0].get('callee')
    return candidates[0] if all(c.get('callee') == callee for c in candidates) else None


def _point(location):
    return location.get('byte_start') if location else None


def _containing_call_end(function, point):
    if isinstance(function, _QueryFunction):
        end = function.call_end(point)
        return end if end > point else None
    return max((call['byte_end'] for call in function['events']['calls']
                if call.get('byte_start', point + 1) <= point < call.get('byte_end', point)), default=None)


def _unparen(node):
    while node and node.get('kind') == 'parenthesized_expression':
        node = next(iter(node.get('children', [])), {})
    return node or {}


def _mentions(node, name):
    if not node:
        return False
    if node.get('kind') == 'identifier' and node.get('expression') == name:
        return True
    for value in node.values():
        if isinstance(value, dict):
            if _mentions(value, name):
                return True
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _mentions(item, name):
                    return True
    return False


def _address_mentions(node, name):
    if not node:
        return False
    if node.get('kind') == 'pointer_expression' and node.get('expression', '').startswith('& ') and _mentions(node, name):
        return True
    for value in node.values():
        if isinstance(value, dict):
            if _address_mentions(value, name):
                return True
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _address_mentions(item, name):
                    return True
    return False


def _collect_address_names(node, address_names):
    """Identifiers under node; those under an address-of expression are added to address_names."""
    mentioned = {node.get('expression')} if node.get('kind') == 'identifier' else set()
    for value in node.values():
        if isinstance(value, dict):
            mentioned |= _collect_address_names(value, address_names)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    mentioned |= _collect_address_names(item, address_names)
    if node.get('kind') == 'pointer_expression' and node.get('expression', '').startswith('& '):
        address_names |= mentioned
    return mentioned


def _address_mentioned(function, node, name):
    """_address_mentions with the per-node name set cached on a query function."""
    if not node:
        return False
    if not isinstance(function, _QueryFunction):
        return _address_mentions(node, name)
    entry = function.address_names.get(id(node))
    if entry is None:
        names = set()
        _collect_address_names(node, names)
        # The entry holds the node so its id stays unique for the analysis lifetime.
        entry = function.address_names[id(node)] = node, frozenset(names)
    return name in entry[1]


def _operand(expression, node=None):
    node = _unparen(node)
    if node:
        if node.get('kind') == 'identifier':
            return node['expression'], False
        if node.get('kind') == 'pointer_expression' and node.get('expression', '').startswith('& '):
            argument = _unparen(node.get('argument'))
            if argument.get('kind') == 'identifier':
                return argument['expression'], True
        return None, False
    expression = strip_outer(expression or '')
    address = expression.startswith('& ')
    name = strip_outer(expression[2:] if address else expression)
    return (name, address) if re.fullmatch(r'\w+', name) else (None, address)


def _call_operand_nodes(call):
    return (call.get('expression_node') or {}).get('arguments', {}).get('children', [])

def _call_arguments(call):
    node = call.get('expression_node') or {}
    if node.get('arguments') is not None:
        return [argument.get('expression', '') for argument in _call_operand_nodes(call)]
    return call_parts(call['expression'])[1]


def _applicable(definition, use):
    """A reaching definition must dominate the use in the bounded branch model."""
    return all(guard in use.get('guards', []) for guard in definition.get('guards', []))


def _exclusive(left, right):
    right_guards = right.get('guards', [])
    if not right_guards:
        return False
    for a in left.get('guards', []):
        if a.get('condition_id') is None:
            continue
        for b in right_guards:
            if a.get('condition_id') == b.get('condition_id') and a['condition'] == b['condition'] and a['branch'] != b['branch']:
                return True
    return False

def event_applicability(function, event, specialization=None, assumed_guards=()):
    """Prove execution on every successful exit in a bounded branch context."""
    events = function['events']
    reasons = []
    if events.get('semantic_unassessed') or events.get('control_flow_unresolved') or not events.get('flow'):
        reasons.append('successful-path applicability requires assessed structured branch/block flow')
    if event.get('loops'):
        reasons.append('loop-contained event has no guaranteed execution certificate')
    if event.get('conditional_evaluation'):
        reasons.append('conditionally evaluated operand has no guaranteed execution certificate')
    if event.get('byte_start') is None or event.get('byte_end') is None:
        reasons.append('event lacks a structured source interval')
    result = {'resolution': 'unresolved', 'kind': 'successful-path-dominance', 'unresolved': reasons}
    if assumed_guards:
        result['assumed_guards'] = deepcopy(assumed_guards)
    if reasons:
        return result
    environment = dict(function['constants'])
    environment.update(specialization or {})
    assumptions = {guard.get('condition_id'): guard['branch'] == 'then' for guard in assumed_guards}
    calls = {call.get('byte_start'): call for call in events['calls']}
    successes = set()
    def contains(start, end):
        return (start is not None and end is not None
                and start <= event['byte_start'] < event['byte_end'] <= end)
    def visit(node, states, in_loop=False):
        kind = node.get('kind')
        if kind == 'block':
            for child in node.get('children', []):
                if not states:
                    break
                states = visit(child, states, in_loop)
            return states
        if kind == 'if':
            if not in_loop and contains(node.get('condition_start'), node.get('condition_end')):
                states = {True}
            value = _constant_at(function, node['condition'], node, environment)
            assumed = assumptions.get(node.get('byte_start'))
            if assumed is not None:
                if value is not None and bool(value) != assumed:
                    return set()
                value = assumed
            branches = [node['then'] if value else node['else']] if value is not None else [node['then'], node['else']]
            return set().union(*(visit(branch, states, in_loop) for branch in branches))
        if kind == 'loop':
            # Both zero-trip fallthrough and body exits remain possible; no event
            # in an unassessed loop gains a guaranteed-execution certificate.
            visit(node['body'], states, True)
            return states
        if not in_loop and contains(node.get('byte_start'), node.get('byte_end')):
            states = {True}
        if kind == 'return':
            successes.update(states)
            return set()
        if kind == 'call':
            call = calls.get(node.get('byte_start'))
            if _qualified_error_exit(call):
                return set()
        return states
    successes.update(visit(events['flow'], {False}))
    if successes == {True}:
        result['resolution'] = 'bounded-resolved'
    else:
        result['unresolved'].append('event does not dominate every applicable successful exit')
    return result


def _composed_applicability(child, caller):
    return {'resolution': 'bounded-resolved' if child.get('resolution') == caller.get('resolution') == 'bounded-resolved' else 'unresolved',
            'kind': 'successful-path-dominance',
            'unresolved': child.get('unresolved', []) + caller.get('unresolved', [])}


def _local(function, name, use):
    point = _point(use)
    if point is None:
        return None
    key = name, point
    if isinstance(function, _QueryFunction) and key in function.locals:
        return function.locals[key]
    variables = function.variables.get(name, ()) if isinstance(function, _QueryFunction) else function['events']['variables']
    local = None
    rank = None
    for variable in variables:
        if (variable['name'] == name and variable.get('byte_start', point + 1) < point
                and variable.get('scope_start', point + 1) <= point < variable.get('scope_end', -1)):
            candidate_rank = variable.get('scope_start', 0), variable['byte_start']
            if rank is None or candidate_rank > rank:
                local, rank = variable, candidate_rank
    if isinstance(function, _QueryFunction):
        function.locals[key] = local
    return local


_C_INTEGER_TYPES = {
    'char', 'signed char', 'unsigned char', 'short', 'short int', 'signed short',
    'signed short int', 'unsigned short', 'unsigned short int', 'int', 'signed',
    'signed int', 'unsigned', 'unsigned int', 'long', 'long int', 'signed long',
    'signed long int', 'unsigned long', 'unsigned long int', 'long long',
    'long long int', 'signed long long', 'signed long long int',
    'unsigned long long', 'unsigned long long int',
}


def _scalar_formal(function, name):
    """Recognize declared C integer storage, not a Python accepted domain."""
    source = function.get('parameter_source_contract', {})
    declarations = [declaration for declaration in source.get('declarations', []) if declaration['name'] == name]
    return (source.get('declaration_binding_resolution') == 'resolved' and len(declarations) == 1
            and declarations[0]['declarator_expression'] == name
            and declarations[0]['type_expression'] in _C_INTEGER_TYPES)


def _scalar_read(node):
    node = _unparen(node)
    kind = node.get('kind')
    if kind in ('identifier', 'number_literal', 'char_literal'):
        return True
    if kind == 'binary_expression' and node.get('operator') in {
            '+', '-', '*', '/', '%', '<<', '>>', '&', '|', '^', '==', '!=', '<', '<=', '>', '>=', '&&', '||'}:
        return _scalar_read(node.get('left')) and _scalar_read(node.get('right'))
    if kind == 'unary_expression' and node.get('operator') in ('+', '-', '~', '!'):
        return _scalar_read(node.get('argument'))
    if kind == 'cast_expression' and node.get('type', {}).get('expression') in _C_INTEGER_TYPES:
        return _scalar_read(node.get('value'))
    return False

def _constant_at(function, expression, use, environment=None):
    """Evaluate only lexically visible constants or separately supplied bindings."""
    point = _point(use)
    supplied = environment is not None
    source_environment = function['constants'] if environment is None else environment
    visible = {}
    for name in set(re.findall(r'\b[A-Za-z_]\w*\b', expression or '')):
        fact = source_environment.get(name)
        if fact is None or _local(function, name, use):
            continue
        if name in function['parameters']:
            if not supplied or fact == function['constants'].get(name):
                continue
            reference = source_reference(function, name, use)
            original = {'kind': 'c-parameter', 'scope': function['id'],
                        'parameter': function['parameters'].index(name)}
            if reference != original and not (_scalar_formal(function, name)
                                              and _unchanged(function, name, use, scalar_reads=True)):
                continue
        if isinstance(fact, dict) and 'visible_start' in fact:
            if point is None or not fact['visible_start'] <= point or not fact['scope_start'] <= point < fact['scope_end']:
                continue
        visible[name] = fact
    return constant(expression, visible)


def lexical_constant(function, expression, use, specialization=None):
    """Evaluate source constants with source-qualified unchanged formal bindings."""
    environment = dict(function['constants'])
    for name, value in (specialization or {}).items():
        if name not in function['parameters']:
            continue
        environment[name] = value
    return _constant_at(function, expression, use, environment)


def _automatic_definition(definition):
    specifiers = definition.get('declaration_specifiers')
    return (isinstance(specifiers, str) and bool(specifiers)
            and not {'static', 'extern', '_Thread_local', 'thread_local', '__thread'}.intersection(specifiers.split()))


def _local_definition(function, name, use):
    local = _local(function, name, use)
    if not local or local.get('initializer_node') or local.get('initializer'):
        return local
    assignments = [write for write in function['events']['writes']
                   if write.get('target') == name and write.get('operator') == '='
                   and local['byte_start'] < write.get('byte_start', -1) < use.get('byte_start', -1)
                   and (_local(function, name, write) or {}).get('declaration_id') == local.get('declaration_id')]
    if len(assignments) != 1 or assignments[0].get('loops') or not _applicable(assignments[0], use):
        return local
    assignment = assignments[0]
    return {**local, 'initializer_node': assignment.get('value_node'),
            'byte_start': assignment['byte_end'], 'source_span': assignment['source_span'],
            'guards': assignment.get('guards', []), 'definition_use': assignment}


def _unchanged(function, name, use, definition=None, allow_calls=(), scalar_reads=False):
    if definition and not _automatic_definition(definition):
        return False
    point = _point(use)
    start = definition.get('byte_start', -1) if definition else -1
    end = (use or {}).get('byte_end', point)
    if point is not None:
        # C call arguments are unordered; a sibling expression may change the binding.
        call_end = _containing_call_end(function, point)
        end = max(end or point, call_end) if call_end is not None else end or point
    for variable in function['events']['variables']:
        if point is not None and not start <= variable.get('byte_start', -1) < point or _exclusive(variable, use or {}):
            continue
        visible = _local(function, name, variable)
        if visible and (definition is None or visible.get('declaration_id') != definition.get('declaration_id')):
            continue
        initializer = variable.get('initializer_node') or variable.get('initializer') or {}
        if _address_mentioned(function, initializer, name) or not initializer.get('kind') and re.search(r'& ' + re.escape(name) + r'\b', initializer.get('expression', '')):
            return False
    for write in function['events']['writes']:
        before = point is None or write.get('byte_start', -1) < end
        if _exclusive(write, use or {}):
            continue
        visible = _local(function, name, write)
        if visible and (definition is None or visible.get('declaration_id') != definition.get('declaration_id')):
            continue
        if before and write.get('byte_start', 0) >= start and (re.search(r'\b' + re.escape(name) + r'\b', write.get('target', '')) or _address_mentioned(function, write.get('value_node'), name)):
            return False
    for call in function['events']['calls']:
        if point is not None and call.get('byte_start', -1) < start:
            continue
        if _exclusive(call, use or {}):
            continue
        visible = _local(function, name, call)
        if visible and (definition is None or visible.get('declaration_id') != definition.get('declaration_id')):
            continue
        if point is not None and call.get('byte_end', call.get('byte_start', -1)) > point:
            if call.get('byte_start', -1) >= end:
                continue
            if any(_address_mentioned(function, node, name) for node in _call_operand_nodes(call)):
                return False
            nested = any(parent.get('byte_start', -1) < call.get('byte_start', -1)
                         and call.get('byte_end', end + 1) <= parent.get('byte_end', -1)
                         for parent in function['events']['calls'])
            if not nested:
                continue
        helper = call_parts(call['expression'])[0]
        arguments = _call_arguments(call)
        nodes = _call_operand_nodes(call)
        permitted = qualified_template(call) and helper in set(CONSTRUCTORS) | set(INPUT_HELPERS) | set(allow_calls) | {'mp_obj_get_type', 'mp_obj_new_tuple', 'mp_obj_new_str_of_type', 'mp_obj_new_str_type_from_vstr'}
        for index, argument in enumerate(arguments):
            node = nodes[index] if index < len(nodes) else None
            operand, address = _operand(argument, node)
            mentioned = operand == name or _mentions(node, name)
            if not mentioned:
                continue
            if address or _address_mentioned(function, node, name):
                return False
            if scalar_reads and _scalar_read(node):
                continue
            if operand != name:
                subscript = _unparen(node)
                if permitted and subscript.get('kind') == 'subscript_expression' and _operand('', subscript.get('argument'))[0] == name:
                    continue
                return False
            if not permitted:
                return False
    return True

def _storage_unchanged(function, name, use, definition=None, after=None, protected_fields=None):
    """Follow scoped storage aliases before rejecting writes and escaping addresses."""
    if definition and not _automatic_definition(definition):
        return False
    start = definition.get('byte_start', -1) if definition else -1
    after = start if after is None else after
    end = _point(use)
    if end is None:
        return False
    consumer = _unparen(use.get('expression_node') or use.get('initializer_node') or use)
    if definition is None:
        # Sibling arguments may write through an alias before the item is read.
        end = max(end, use.get('byte_end') or end)
        end = max([end] + [call['byte_end'] for call in function['events']['calls']
                          if call.get('byte_start', end + 1) <= _point(use) < call.get('byte_end', _point(use))])
    aliases = {(name, definition.get('declaration_id') if definition else None)}
    def binding(identifier, event):
        local = _local(function, identifier, event)
        return identifier, local.get('declaration_id') if local else None
    def mentioned(node, event):
        return [identifier for identifier, declaration in aliases
                if _mentions(node, identifier) and binding(identifier, event) == (identifier, declaration)]
    for variable in sorted(function['events']['variables'], key=lambda event: event.get('byte_start', -1)):
        point = variable.get('byte_start', -1)
        if not start <= point < end or point == _point(use) or _exclusive(variable, use):
            continue
        node = _unparen(variable.get('initializer_node'))
        roots = mentioned(node, variable)
        if not roots:
            continue
        if point >= after and any(_address_mentioned(function, node, root) for root in roots):
            return False
        while node.get('kind') == 'cast_expression':
            node = _unparen(node.get('value'))
        operand, _ = _operand('', node)
        if operand in roots:
            aliases.add((variable['name'], variable.get('declaration_id')))
        elif not (protected_fields is None and node.get('kind') == 'field_expression'):
            return False
    for write in function['events']['writes']:
        if not start <= write.get('byte_start', -1) < end or _exclusive(write, use):
            continue
        if mentioned(write.get('value_node'), write):
            return False
        if write.get('byte_start', -1) < after:
            continue
        target = _unparen(write.get('target_node'))
        roots = mentioned(target, write)
        if not roots:
            continue
        if protected_fields is None:
            return False
        if target.get('kind') != 'field_expression' or any(
                re.search(r'\b' + re.escape(field) + r'\b', target.get('expression', ''))
                for field in protected_fields):
            return False
    for call in function['events']['calls']:
        if not after <= call.get('byte_start', -1) or call.get('byte_end', call.get('byte_start', -1)) > end or _exclusive(call, use):
            continue
        if (definition is None and consumer.get('byte_start') is not None
                and call.get('byte_start', end + 1) <= consumer['byte_start']
                and consumer.get('byte_end', end + 1) <= call.get('byte_end', -1)):
            continue
        helper = call_parts(call['expression'])[0]
        for node in _call_operand_nodes(call):
            roots = mentioned(node, call)
            if not roots:
                continue
            argument = _unparen(node)
            field = _unparen(argument.get('argument'))
            if (protected_fields is not None and argument.get('kind') == 'pointer_expression'
                    and argument.get('operator') == '&' and field.get('kind') == 'field_expression'
                    and field.get('field', {}).get('expression') == 'mpz'
                    and _operand('', field.get('argument'))[0] in roots
                    and qualified_template(call) and call['callee']['source_span']['source'].endswith('/py/mpz.h')):
                continue
            if (protected_fields is None and argument.get('kind') in ('field_expression', 'subscript_expression')
                    and not any(_address_mentioned(function, argument, root) for root in roots)
                    and qualified_template(call) and helper in set(CONSTRUCTORS) | set(INPUT_HELPERS)):
                continue
            return False
    return True


def source_reference(function, expression, use=None, seen=()):
    use = use or {}
    expression = strip_outer(expression or '')
    if function['events'].get('semantic_unassessed') or expression in seen:
        return None
    local = _local_definition(function, expression, use)
    if local:
        if local.get('loops') or not _applicable(local, use) or not _unchanged(function, expression, use, local):
            return None
        initializer = local.get('initializer_node') or local.get('initializer') or {}
        origin = source_reference(function, initializer.get('expression'), local.get('definition_use', local), (*seen, expression))
        return {**origin, 'alias_evidence': origin.get('alias_evidence', []) + [
            {'declaration_id': local.get('declaration_id'), 'source_span': local['source_span']}]} if origin else None
    if expression in function['parameters'] and _unchanged(function, expression, use):
        return {'kind': 'c-parameter', 'scope': function['id'], 'parameter': function['parameters'].index(expression)}
    vector = re.fullmatch(r'(\w+) \[ (.*?) \]', expression)
    if vector:
        index = _constant_at(function, vector[2], use)
        parameter = source_reference(function, vector[1], use, seen)
        if (parameter and parameter['kind'] == 'c-parameter' and isinstance(index, int)
                and not isinstance(index, bool) and index >= 0
                and _storage_unchanged(function, function['parameters'][parameter['parameter']], use)):
            return {'kind': 'vector-item', 'scope': function['id'], 'vector_parameter': parameter['parameter'], 'index': index}
    field = re.fullmatch(r'(\w+) \[ (.*?) \] \. (u_obj|u_int|u_bool)', expression)
    if field:
        index = _constant_at(function, field[2], use)
        parsers = [call for call in function['events']['calls']
                   if call_parts(call['expression'])[0] == 'mp_arg_parse_all' and qualified_template(call)
                   and len(call_parts(call['expression'])[1]) == 6
                   and call_parts(call['expression'])[1][-1] == field[1]
                   and not call.get('loops') and _applicable(call, use)
                   and (not use or call.get('byte_start', 0) < use.get('byte_start', -1))]
        carrier = _local(function, field[1], use)
        if (len(parsers) == 1 and carrier and carrier.get('type_expression') == 'mp_arg_val_t'
                and _local(function, field[1], parsers[0]) == carrier and isinstance(index, int)):
            parser = parsers[0]
            args = call_parts(parser['expression'])[1]
            tables = [v for v in function['events']['variables'] if v['name'] == args[-2] and v.get('type_expression') == 'mp_arg_t']
            rows = list(argument_table_rows(tables[0], function, function['constants'], {})) if len(tables) == 1 else []
            row = next((r for r in rows if r['table_entry_index'] == index), None)
            count_expression = re.sub(r'sizeof \( ' + re.escape(args[-2]) + r' \) / sizeof \( \( ' + re.escape(args[-2]) + r' \) \[ 0 \] \)', str(len(rows)), args[3])
            count = _constant_at(function, count_expression, parser)
            if (row and row['row_resolution'] == 'resolved' and row['table_count_resolution'] == 'resolved'
                    and count == len(rows) and _storage_unchanged(
                        function, field[1], use, carrier, after=parser.get('byte_end', parser['byte_start']))):
                return {'kind': 'parser-field', 'scope': function['id'], 'table_id': row['table_id'],
                        'table_entry_index': index, 'storage_field': field[3]}
    return None


def direct_parameter(function, expression, use=None):
    ref = source_reference(function, expression, use)
    return ref['parameter'] if ref and ref['kind'] == 'c-parameter' else None


def _type_selector(function, expression, use, seen=()):
    if expression in seen:
        return None
    local = _local_definition(function, expression, use or {})
    if local and not local.get('loops') and _unchanged(function, expression, use, local):
        initializer = local.get('initializer_node') or local.get('initializer') or {}
        return _type_selector(function, initializer.get('expression', ''), local.get('definition_use', local), (*seen, expression))
    helper, values = call_parts(expression)
    if helper == 'mp_obj_get_type' and len(values) == 1 and qualified_template(event_call(function, expression, (use or {}).get('source_span'))):
        return source_reference(function, values[0], use)
    return None


def return_contract(expression, function, contracts, location=None, use=None, seen=()):
    use = use or next((event for event in function['events']['returns'] if event['expression'] == expression and (location is None or intersect(event['source_span'], location))), {})
    if not expression:
        return {'kind': 'void-C-return', 'reason': 'bare C return does not denote Python None', 'evidence': expression}
    value = constant_value(expression, contracts)
    if value['kind'] in ('None', 'bool', 'int', 'str'):
        return value
    for key, marker, kind in (('rom_int', '7', 'int'), ('rom_qstr', 'MP_QSTR___name__', 'str')):
        template = contracts.get(key, '')
        if template and template.count(marker) == 1:
            matched = re.fullmatch(re.escape(template).replace(re.escape(marker), r'(.+)'), expression)
            if matched:
                return {'kind': kind, 'tagged_value_expression': matched[1], 'evidence': expression,
                        'authority': 'actual compiler-expanded tagged object constructor contract'}
    reference = source_reference(function, expression, use)
    if reference and (reference['kind'] != 'parser-field' or reference.get('storage_field') == 'u_obj'):
        value = {'kind': 'argument', 'source_ref': reference, 'evidence': expression, 'scope': reference['scope']}
        if reference['kind'] == 'c-parameter':
            value['argument'] = reference['parameter']
        return value
    name, arguments = call_parts(expression)
    call = event_call(function, expression, location)
    if name in CONSTRUCTORS and qualified_template(call):
        return {'kind': CONSTRUCTORS[name], 'evidence': expression, 'callee': call['callee']}
    if name in ('mp_obj_new_str_type_from_vstr', 'mp_obj_new_str_of_type') and arguments and qualified_template(call):
        reference = _type_selector(function, arguments[0], use)
        if reference:
            return {'kind': 'same-native-family', 'source_ref': reference, 'builder': name,
                    'families': ['str', 'bytes', 'bytearray'], 'identity': False, 'subtype_preservation': False,
                    'evidence': expression, 'scope': function['id'], 'callee': call['callee']}
    if name == 'mp_obj_new_tuple' and len(arguments) == 2 and qualified_template(call):
        node = (call.get('expression_node') or {}).get('arguments', {})
        nodes = node.get('children', [])
        items = nodes[1] if len(nodes) == 2 else {}
        if items.get('kind') == 'compound_literal_expression':
            items = next((child for child in items.get('children', []) if child.get('kind') == 'initializer_list'), {})
        operand, address = _operand(arguments[1], nodes[1] if len(nodes) == 2 else None)
        local = _local(function, operand, use) if operand and not address else None
        if local and _unchanged(function, operand, use, local):
            items = local.get('initializer_node') or {}
        count = _constant_at(function, arguments[0], use)
        children = items.get('children', []) if items.get('kind') == 'initializer_list' else []
        extent = count
        if local:
            dimensions = local.get('array_dimensions', [])
            extent = _constant_at(function, dimensions[0]['expression'], local) if len(dimensions) == 1 and dimensions[0]['expression'] else len(children) if len(dimensions) == 1 else None
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0 and count == len(children) == extent:
            item_guards = local.get('guards', []) if local else use.get('guards', [])
            elements = [return_contract(item['expression'], function, contracts,
                                        {'byte_start': item['byte_start'], 'byte_end': item['byte_end']},
                                        {**item, 'byte_end': items.get('byte_end', item['byte_end']), 'guards': item_guards}, seen)
                        for item in children]
            return {'kind': 'tuple', 'length': count, 'length_expression': arguments[0], 'elements': elements,
                    'cardinality_authority': 'constructor count equals structured initializer and declaration extent',
                    'evidence': expression, 'callee': call['callee']}
        return {'kind': 'unresolved', 'reason': 'tuple constructor count/initializer cardinality/accessible extent mismatch or unknown', 'evidence': expression}
    local = _local_definition(function, expression, use)
    if local and not _automatic_definition(local):
        return {'kind': 'unresolved', 'reason': 'nonautomatic local storage has no qualified current-call value definition', 'evidence': expression}
    if local and not local.get('loops') and expression not in seen and _applicable(local, use):
        initializer = local.get('initializer_node') or local.get('initializer') or {}
        if _unchanged(function, expression, use, local, ('mp_obj_list_append',)):
            value = return_contract(initializer.get('expression'), function, contracts, local['source_span'], local.get('definition_use', local), (*seen, expression))
            if value['kind'] == 'list':
                if _point(use) is None:
                    return {'kind': 'unresolved', 'reason': 'list mutation accounting requires ordered use metadata', 'evidence': expression}
                root_id = value.get('list_allocation_declaration', {}).get('declaration_id', local.get('declaration_id'))
                root_variable = next((variable for variable in function['events']['variables'] if variable.get('declaration_id') == root_id), None)
                root = _local_definition(function, root_variable['name'], use) if root_variable else None
                if not root or root.get('declaration_id') != root_id:
                    return {'kind': 'unresolved', 'reason': 'list allocation root declaration is hidden or unavailable at the return use', 'evidence': expression}
                aliases = {(root['name'], root.get('declaration_id')): root}
                value['list_allocation_declaration'] = {'declaration_id': root.get('declaration_id'), 'source_span': root['source_span']}
                candidates = sorted(function['events']['variables'], key=lambda variable: variable.get('byte_start', -1))
                for variable in candidates:
                    if variable.get('byte_start', -1) <= root.get('byte_start', -1) or variable.get('byte_start', -1) >= _point(use) or _exclusive(variable, use):
                        continue
                    initializer_node = variable.get('initializer_node') or variable.get('initializer') or {}
                    referenced = [name for (name, declaration), definition in aliases.items()
                                  if _mentions(initializer_node, name)
                                  and (_local(function, name, variable) or {}).get('declaration_id') == declaration]
                    if not referenced:
                        continue
                    operand, address = _operand(initializer_node.get('expression', ''), initializer_node)
                    operand_key = (operand, (_local(function, operand, variable) or {}).get('declaration_id'))
                    if operand_key not in aliases or address or variable.get('loops') or not _applicable(variable, use):
                        return {'kind': 'unresolved', 'reason': 'list alias aggregate/derived/loop storage or non-dominating definition is unsupported', 'evidence': expression}
                    aliases[(variable['name'], variable.get('declaration_id'))] = variable
                if any(not _unchanged(function, name, use, definition, ('mp_obj_list_append',))
                       for (name, declaration), definition in aliases.items()):
                    return {'kind': 'unresolved', 'reason': 'list allocation alias mutation/address/unknown-call escape', 'evidence': expression}
                for write in function['events']['writes']:
                    if write.get('byte_start', -1) >= _point(use) or _exclusive(write, use):
                        continue
                    if any(_mentions(write.get('value_node'), name)
                           and (_local(function, name, write) or {}).get('declaration_id') == declaration
                           for (name, declaration), definition in aliases.items()):
                        return {'kind': 'unresolved', 'reason': 'list alias assignment/storage is outside the bounded declaration-alias model', 'evidence': expression}
                appends = []
                for append in function['events']['calls']:
                    helper, operands = call_parts(append['expression'])
                    nodes = _call_operand_nodes(append)
                    operand, address = _operand(operands[0], nodes[0] if nodes else None) if operands else (None, False)
                    operand_key = (operand, (_local(function, operand, append) or {}).get('declaration_id'))
                    if helper != 'mp_obj_list_append' or len(operands) != 2 or operand_key not in aliases or address or _exclusive(append, use):
                        continue
                    definition = aliases[operand_key]
                    if not definition.get('byte_start', -1) <= append.get('byte_start', -1) < _point(use):
                        continue
                    appends.append(append)
                allocation_name, allocation_arguments = call_parts(value['evidence'])
                empty = allocation_name == 'mp_obj_new_list' and len(allocation_arguments) == 2 and _constant_at(function, allocation_arguments[0], root.get('definition_use', root)) == 0
                if empty and all(qualified_template(c) for c in appends):
                    value['elements'] = [return_contract(call_parts(c['expression'])[1][1], function, contracts, c['source_span'], c, seen) for c in appends]
                    value['builder_authority'] = 'empty local list allocation and complete lexical declaration-alias qualified append sites'
                    value['element_resolution'] = 'unresolved' if any(element['kind'] == 'unresolved' for element in value['elements']) else 'bounded-resolved'
                    value['element_unresolved'] = sorted({element.get('reason', 'element relation unresolved') for element in value['elements'] if element['kind'] == 'unresolved'})
                else:
                    value['element_resolution'] = 'unresolved'
                    value['element_unresolved'] = ['initial list contents or append qualification is outside the bounded empty-list builder model']
            value['local_definition'] = {'declaration_id': local.get('declaration_id'), 'source_span': local['source_span']}
            return value
    if name and call and not qualified_template(call):
        return {'kind': 'unresolved', 'reason': 'callee is not a qualified runtime helper template', 'evidence': expression, 'callee': call.get('callee')}
    return {'kind': 'unresolved', 'reason': 'unsupported return/helper expression or alias mutation/escape/scope barrier', 'evidence': expression}


def remap_return(contract, arguments, outer, callee, use=None):
    mapped = deepcopy(contract)
    ref = contract.get('source_ref')
    if ref and ref.get('scope') == callee['id'] and ref['kind'] in ('c-parameter', 'vector-item'):
        index = ref.get('parameter', ref.get('vector_parameter'))
        actual = arguments[index] if index < len(arguments) else None
        target = source_reference(outer, actual, use)
        if target and ref['kind'] == 'vector-item' and target['kind'] == 'c-parameter':
            target = {'kind': 'vector-item', 'scope': outer['id'], 'vector_parameter': target['parameter'], 'index': ref['index']}
        if target:
            mapped.update(source_ref=target, scope=outer['id'], callee_relation=contract)
            mapped.pop('argument', None)
            if target['kind'] == 'c-parameter' and mapped['kind'] == 'argument':
                mapped['argument'] = target['parameter']
        else:
            mapped.update(kind='unresolved', reason='helper source reference forwarding is not qualified')
    if contract.get('elements'):
        mapped['elements'] = [remap_return(e, arguments, outer, callee, use) for e in contract['elements']]
    return mapped


def constraint_binding(expression, function, use=None):
    reference = source_reference(function, expression, use)
    if reference:
        return {**reference, 'source_ref': reference, 'expression': expression}
    return {'kind': 'callee-local-or-derived-expression', 'scope': function['id'], 'expression': expression,
            'unresolved': 'not a proven Python parameter reference'}


def argument_table_rows(variable, function, environment, contracts):
    """Retain every initializer ordinal, including unsupported field syntax."""
    initializers = variable.get('initializer_rows', [])
    dimensions = variable.get('array_dimensions', [])
    declared_count = _constant_at(function, dimensions[0]['expression'], variable, environment) if len(dimensions) == 1 and dimensions[0]['expression'] else None
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
        if _constant_at(function, flags, variable, environment) is None:
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
                numeric_default = _constant_at(function, expression, variable, environment)
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


def semantics(function, functions, errors, contracts, specialization=None, depth=0, linked_functions=None):
    events = function['events']
    result = {'function_id': function['id'], 'parameters': function['parameters'], 'inputs': [], 'argument_tables': [], 'parser_calls': [], 'returns': [], 'helper_propagation': [], 'destination_writes': [], 'branch_selection': [], 'input_unresolved': [], 'return_unresolved': [], 'frontend_unresolved': [], 'input_coverage': 'bounded helper constraints, not a complete accepted callable domain'}
    lifted_uses = []
    composed_calls = set()
    barriers = events.get('semantic_unassessed', [])
    if barriers:
        reasons = [barrier['reason'] for barrier in barriers]
        result.update(constructor_calls=[], syntax_semantic_barriers=barriers,
                      input_unresolved=reasons, return_unresolved=reasons,
                      frontend_unresolved=reasons,
                      input_resolution='unresolved', return_resolution='unresolved', input_uses=[],
                      successful_path_coverage={'resolution': 'unresolved', 'unresolved': reasons})
        return result
    function = _query_function(function)
    result['return_unresolved'].extend(events.get('control_flow_unresolved', []))
    result['constructor_calls'] = [{'kind': CONSTRUCTORS[call_parts(call['expression'])[0]], 'expression': call['expression'], 'callee': call['callee'], 'source_span': call['source_span'], 'guards': call.get('guards', [])} for call in events['calls'] if call_parts(call['expression'])[0] in CONSTRUCTORS and qualified_template(call)]
    shadowed = set(function['parameters']) | {v['name'] for v in events['variables']}
    environment = {name: value for name, value in function['constants'].items() if name not in shadowed}
    for name, value in (specialization or {}).items():
        if name in function['parameters']:
            environment[name] = value
    # A failed constant evaluation keeps both supported paths; it does not erase
    # a structurally closed union.
    condition_values = {}
    def condition_value(expression, point):
        key = expression, point
        if key not in condition_values:
            condition_values[key] = _constant_at(function, expression, {'byte_start': point}, environment)
        return condition_values[key]
    def selected(event):
        for guard in event.get('guards', []):
            value = condition_value(guard['condition'], guard.get('condition_id', event.get('byte_start')))
            if value is not None and bool(value) != (guard['branch'] == 'then'): return False
        return True
    flow_closed, reachable, error_exits = _flow_paths(function, environment)
    result['error_only_exits'] = error_exits
    result['successful_path_coverage'] = {
        'resolution': 'bounded-resolved' if flow_closed else 'unresolved',
        'authority': 'structured branch/block closure with qualified nonreturn error exits',
        'reachable_return_points': sorted(reachable),
        'unresolved': [] if flow_closed else ['successful fallthrough/control-flow closure not established']}
    for variable in events['variables']:
        if variable.get('type_expression') != 'mp_arg_t': continue
        result['argument_tables'].extend(argument_table_rows(variable, function, environment, contracts))
    for call in events['calls']:
        if not selected(call): continue
        helper, args = call_parts(call['expression'])
        if helper in INPUT_HELPERS and qualified_template(call):
            contract = INPUT_HELPERS[helper]
            domain_authority = input_helper_authority(call, function, functions, contracts, linked_functions)
            if helper == 'mp_get_buffer_raise' and len(args) >= 3:
                mode = _constant_operand(args[2], function, call, environment)
                read_mode, write_mode = constant(contracts.get('buffer_read', ''), {}), constant(contracts.get('buffer_write', ''), {})
                contract = 'readable-buffer-capability' if mode == read_mode and read_mode is not None else 'writable-buffer-capability' if mode == write_mode and write_mode is not None else 'buffer-mode-unresolved'
                if contract == 'buffer-mode-unresolved':
                    domain_authority = {**domain_authority, 'native_operation_resolution': domain_authority['resolution'],
                                        'resolution': 'unresolved', 'unresolved': domain_authority.get('unresolved', []) +
                                        ['selected requested buffer mode is not one unchanged qualified read/write flag']}
            result['inputs'].append({'helper': helper, 'arguments': args, 'contract': contract,
                                     'bindings': [constraint_binding(a, function, call) for a in args],
                                     'domain_authority': domain_authority,
                                     'successful_path_applicability': event_applicability(function, call, specialization),
                                     'guards': call.get('guards', []), 'callee': call['callee'],
                                     'source_span': call['source_span'], 'scope': function['id'],
                                     'byte_start': call.get('byte_start'), 'byte_end': call.get('byte_end')})
            if helper == 'mp_obj_get_array_fixed_n' and len(args) == 3:
                result['inputs'][-1]['extent'] = {'expression': args[1], 'value': _constant_operand(args[1], function, call, environment),
                                                'authority': 'bounded source count operand; helper count guard is independently qualified by domain_authority'}
        if helper == 'mp_arg_parse_all' and qualified_template(call):
            result['parser_calls'].append({'arguments': args, 'argument_nodes': (call.get('expression_node') or {}).get('arguments', {}).get('children', []),
                                           'constant_bindings': dict(specialization or {}),
                                           'source_span': call['source_span'], 'guards': call.get('guards', []), 'scope': function['id'], 'callee': call['callee'],
                                           'byte_start': call.get('byte_start'), 'byte_end': call.get('byte_end'),
                                           'loops': call.get('loops', []), 'conditional_evaluation': call.get('conditional_evaluation', []),
                                           'successful_path_applicability': event_applicability(function, call, specialization)})
    for write in events['writes']:
        if not selected(write): continue
        assigned = re.match(r'dest \[ (\d+) \] = (.*)$', write['expression'])
        if assigned:
            result['destination_writes'].append({'destination_index': int(assigned[1]), 'expression': assigned[2], 'value_contract': return_contract(assigned[2], function, contracts, write['source_span']), 'source_span': write['source_span'], 'guards': write.get('guards', []), 'scope': function['id'],
                                               'byte_start': write.get('byte_start'), 'byte_end': write.get('byte_end'),
                                               'loops': write.get('loops', []), 'conditional_evaluation': write.get('conditional_evaluation', [])})
    for returned in events['returns']:
        result['branch_selection'].append({'source_span': returned['source_span'], 'selected': selected(returned), 'guards': returned.get('guards', []), 'evaluated_conditions': [condition_value(g['condition'], g.get('condition_id', returned.get('byte_start'))) for g in returned.get('guards', [])]})
        if not selected(returned) or flow_closed is not None and returned.get('byte_start') not in reachable:
            continue
        helper, args = call_parts(returned['expression'])
        contract = return_contract(returned['expression'], function, contracts, returned['source_span'], returned)
        contract = _qualify_builder(contract, function, functions, linked_functions, contracts)
        if contract['kind'] == 'unresolved' and depth < 4:
            allocated = _allocated_result(function, returned, functions, linked_functions, contracts, depth)
            if allocated:
                contract = allocated
        call = event_call(function, returned['expression'], returned['source_span'])
        call_use = returned
        local_result = _local_definition(function, returned['expression'], returned)
        if not call and local_result and _applicable(local_result, returned) and _unchanged(function, local_result['name'], returned, local_result):
            initializer = local_result.get('initializer_node') or {}
            helper, args = call_parts(initializer.get('expression', ''))
            call = event_call(function, initializer.get('expression'), local_result['source_span'])
            call_use = local_result.get('definition_use', local_result)
        candidates = _selected_helpers(call, helper, functions, linked_functions, function)
        if contract['kind'] == 'unresolved' and call and len(candidates) == 1 and depth < 4:
            callee, helper_authority = candidates[0]
            constant_bindings = {p: value for p, arg in zip(callee['parameters'], args) if (value := _constant_operand(arg, function, call_use, environment)) is not None}
            child_contracts = helper_authority.get('contracts', contracts)
            child = semantics(callee, functions, errors, child_contracts, constant_bindings, depth + 1, linked_functions)
            bindings = {}
            for parameter, argument in zip(callee['parameters'], args):
                ref = source_reference(function, argument, call_use)
                bindings[parameter] = {'expression': argument,
                                       'direct_outer_parameter': ref['parameter'] if ref and ref['kind'] == 'c-parameter' else None,
                                       'source_ref': ref, 'constant': _constant_operand(argument, function, call_use, environment)}
            composed_calls.add(call.get('byte_start'))
            for child_use in child.get('input_uses', []):
                mapped_ref = remap_return({'kind': 'argument', 'source_ref': child_use['source_ref']}, args, function, callee, call_use)
                if mapped_ref['kind'] != 'unresolved':
                    lifted_uses.append({**child_use, 'source_ref': mapped_ref['source_ref'],
                                       'guards': returned.get('guards', []) + child_use.get('guards', []),
                                       'via_helper': callee['id']})
            constant_sources = {name: evidence for name, evidence in function['constants'].items() if any(re.search(r'\b' + re.escape(name) + r'\b', arg) for arg in args)}
            result['helper_propagation'].append({'helper': callee['id'], 'arguments': args, 'parameter_bindings': bindings,
                                               'constant_bindings': constant_bindings, 'constant_sources': constant_sources,
                                               'authority': helper_authority,
                                               'facts': {key: child[key] for key in ('function_id', 'return_resolution', 'return_unresolved', 'successful_path_coverage')}})
            for child_return in child['returns']:
                mapped = remap_return(child_return, args, function, callee, call_use)
                mapped['guards'] = returned.get('guards', []) + child_return.get('guards', [])
                result['returns'].append(mapped)
                if mapped['kind'] in ('unresolved', 'void-C-return'): result['return_unresolved'].append(mapped.get('reason', 'propagated return unresolved'))
            result['return_unresolved'].extend(child['return_unresolved'])
            result['frontend_unresolved'].extend(child['frontend_unresolved'])
            if child['argument_tables'] or child['parser_calls'] or child['inputs']:
                forwarded = len(bindings) == len(callee['parameters']) == len(args) and all(binding['direct_outer_parameter'] is not None or binding['constant'] is not None for binding in bindings.values())
                if forwarded:
                    if child['argument_tables']:
                        result['argument_tables'].extend({**row, 'via_helper': callee['id'], 'caller_parameter_bindings': bindings} for row in child['argument_tables'])
                    caller_applicability = event_applicability(function, call, specialization)
                    result['parser_calls'].extend({
                        **row, 'via_helper': callee['id'], 'caller_parameter_bindings': bindings,
                        'successful_path_applicability': _composed_applicability(
                            row.get('successful_path_applicability', {}), caller_applicability)}
                        for row in child['parser_calls'])
                    for row in child['inputs']:
                        lifted = {**row, 'via_helper': callee['id'], 'caller_parameter_bindings': bindings,
                                  'guards': returned.get('guards', []) + row.get('guards', []), 'bindings': [],
                                  'successful_path_applicability': _composed_applicability(
                                      row.get('successful_path_applicability', {}), caller_applicability)}
                        for relation in row.get('bindings', []):
                            ref = relation.get('source_ref')
                            mapped = remap_return({'kind': 'argument', 'source_ref': ref}, args, function, callee, call_use) if ref else None
                            lifted['bindings'].append({**relation, 'source_ref': mapped.get('source_ref')} if mapped and mapped['kind'] != 'unresolved' else relation)
                        result['inputs'].append(lifted)
                else: result['input_unresolved'].append('helper constraints/table not lifted: operands are not direct parameters or proven constants')
        else:
            contract['source_span'] = returned['source_span']; contract['guards'] = returned.get('guards', [])
            result['returns'].append(contract)
            if contract['kind'] in ('unresolved', 'void-C-return'): result['return_unresolved'].append(contract.get('reason', 'return unresolved'))
            if contract['kind'] == 'tuple' and any(e['kind'] in ('unresolved', 'void-C-return') for e in contract.get('elements', [])): result['return_unresolved'].append('tuple element relation unresolved')
    loop_returns = [returned for returned in events['returns'] if returned.get('loops')
                    and selected(returned) and returned.get('byte_start') in reachable]
    if loop_returns:
        family = [contract for contract in result['returns'] if contract.get('kind') == 'same-native-family']
        same_reference = family and all(contract.get('kind') in ('argument', 'same-native-family')
                                       and _reference_key(contract.get('source_ref')) == _reference_key(family[0]['source_ref'])
                                       for contract in result['returns'])
        typed_integer = all(contract.get('kind') == 'int' for contract in result['returns']) and any(
            contract.get('allocation_authority') for contract in result['returns'])
        if same_reference or typed_integer:
            result['loop_result_certificate'] = {
                'resolution': 'bounded-resolved', 'kind': 'native-family-exit-invariant' if same_reference else 'selected-typed-integer-exit-invariant',
                'authority': 'all reachable successful exits independently derive one native result family',
                'loops': sorted({loop for returned in loop_returns for loop in returned['loops']})}
        else:
            result['return_unresolved'].append('loop-contained return lacks selected native builder/allocation exit invariant')
    if not result['returns']: result['return_unresolved'].append('no applicable return contract extracted')
    if flow_closed is False or flow_closed is None and events['returns'] and not any(not r.get('guards') for r in events['returns']):
        result['return_unresolved'].append('conditional-only return coverage/fallthrough unassessed')
    if errors_at({'errors': errors}, function['source_span']): result['frontend_unresolved'].append('frontend error overlaps function region')
    converted_calls = {(fact.get('byte_start'), fact.get('byte_end')) for fact in result['inputs']
                       if fact.get('scope') == function['id'] and not fact.get('via_helper')
                       and fact.get('helper') in ('mp_obj_is_true', 'mp_obj_get_int')
                       and isinstance(fact.get('byte_start'), int) and isinstance(fact.get('byte_end'), int)
                       and fact['byte_start'] < fact['byte_end']
                       and fact.get('domain_authority', {}).get('resolution') in ('resolved', 'bounded-resolved')}
    result['input_uses'] = _input_uses(function, selected, composed_calls, converted_calls, lifted_uses)
    result['input_unresolved'].append('complete accepted callable input domain unassessed')
    for axis in ('input_unresolved', 'return_unresolved', 'frontend_unresolved'):
        result[axis] = sorted(set(result[axis]))
    result['input_resolution'] = 'unresolved'
    result['return_resolution'] = 'unresolved' if result['return_unresolved'] or result['frontend_unresolved'] else 'bounded-resolved'
    result['status'] = 'unresolved-input-domain' if result['return_resolution'] == 'bounded-resolved' else 'unresolved'
    result['unresolved'] = sorted(set(result['input_unresolved'] + result['return_unresolved'] + result['frontend_unresolved']))
    return result


def _constant_operand(expression, function, use, environment, seen=()):
    value = _constant_at(function, expression, use, environment)
    if value is not None:
        return value
    local = _local_definition(function, expression, use)
    if local and expression not in seen and not local.get('loops') and _applicable(local, use) and _unchanged(function, expression, use, local):
        initializer = local.get('initializer_node') or local.get('initializer') or {}
        return _constant_operand(initializer.get('expression', ''), function, local.get('definition_use', local), environment, (*seen, expression))
    return None


def _selected_helpers(call, name, functions, linked_functions, caller=None):
    if not call or (call.get('callee') or {}).get('kind') != 'FUNCTION_DECL':
        return []
    local = [(definition, {'resolution': 'bounded-resolved', 'kind': 'same-TU-declaration-definition', 'declaration_identity': call['callee']})
             for definition in functions.get(name, []) if definition['declaration_identity'] == call['callee']]
    linked = [(row['function'], row['authority']) for row in (linked_functions or {}).get(name, [])
              if row.get('declaration_identity') == call['callee']
              and row.get('authority', {}).get('resolution') in ('resolved', 'bounded-resolved')]
    translation_unit = (caller or {}).get('translation_unit')
    if translation_unit:
        same_tu = [(definition, authority) for definition, authority in linked
                   if authority.get('translation_unit') == translation_unit]
        linked = same_tu or [(definition, authority) for definition, authority in linked
                            if definition.get('linkage') == 'external' or not authority.get('translation_unit')]
    candidates = {}
    for definition, authority in (linked if linked_functions is not None else local):
        candidates[definition['id']] = (definition, authority)
    return list(candidates.values())


def _reference_key(reference):
    if not reference:
        return None
    fields = {'c-parameter': ('kind', 'scope', 'parameter'),
              'vector-item': ('kind', 'scope', 'vector_parameter', 'index'),
              'parser-field': ('kind', 'scope', 'table_id', 'table_entry_index', 'storage_field')}.get(reference.get('kind'))
    return tuple(reference.get(field) for field in fields) if fields else None


def _parser_object_uses(function, node, use):
    """Map possible output-storage consumers to original object-field inputs."""
    function = _query_function(function)
    for parser in function.parse_calls:
        if (parser.get('byte_end', parser.get('byte_start', -1)) > _point(use)
                or parser.get('loops') or _exclusive(parser, use)):
            continue
        arguments, nodes = _call_arguments(parser), _call_operand_nodes(parser)
        if len(arguments) != 6 or len(nodes) != 6:
            continue
        carrier_name, carrier_address = _operand(arguments[5], nodes[5])
        table_name, table_address = _operand(arguments[4], nodes[4])
        carrier, table = _local(function, carrier_name, parser), _local(function, table_name, parser)
        if (not carrier or carrier_address or carrier.get('type_expression') != 'mp_arg_val_t'
                or not table or table_address or table.get('type_expression') != 'mp_arg_t'):
            continue
        rows = list(argument_table_rows(table, function, function['constants'], {}))
        count_expression = re.sub(r'sizeof \( ' + re.escape(table_name) + r' \) / sizeof \( \( '
                                  + re.escape(table_name) + r' \) \[ 0 \] \)', str(len(rows)), arguments[3])
        if (_constant_at(function, count_expression, parser) != len(rows)
                or any(row['row_resolution'] != 'resolved' or row['table_count_resolution'] != 'resolved' for row in rows)):
            continue
        whole, unknown = 'whole-carrier', 'unknown-offset'
        def shifted_origins(base, offset):
            result = set()
            for origin in base:
                if origin == unknown:
                    result.add(unknown)
                else:
                    index = offset if origin == whole else origin + offset
                    result.add(index if index >= 0 else unknown)
            return result
        def carrier_pointer(expression, event, seen=(), depth=0):
            expression = _unparen(expression)
            if not expression or depth >= 24:
                return False
            if expression.get('kind') == 'identifier':
                local = _local(function, expression['expression'], event)
                if local and local.get('declaration_id') == carrier.get('declaration_id'):
                    return True
                if (not local or local.get('declaration_id') in seen or local.get('type_expression') != 'mp_arg_val_t'
                        or not re.fullmatch(r'\* (?:const )?' + re.escape(local['name']), local.get('declarator_expression', ''))):
                    return False
                seen = (*seen, local.get('declaration_id'))
                sources = [(local.get('initializer_node'), local)] if local.get('initializer_node') else []
                sources.extend((write.get('value_node'), write) for write in function['events']['writes']
                               if write.get('target') == local['name'] and write.get('byte_start', -1) < _point(event)
                               and not _exclusive(write, event)
                               and (_local(function, local['name'], write) or {}).get('declaration_id') == local.get('declaration_id'))
                return bool(sources) and all(point.get('operator', '=') == '=' and carrier_pointer(
                    source, point, seen, depth + 1) for source, point in sources)
            if expression.get('kind') == 'cast_expression':
                return (expression.get('type', {}).get('expression') == 'mp_arg_val_t *'
                        and carrier_pointer(expression.get('value'), event, seen, depth + 1))
            if expression.get('kind') == 'pointer_expression' and expression.get('operator') == '&':
                target = _unparen(expression.get('argument'))
                return target.get('kind') == 'subscript_expression' and carrier_pointer(target.get('argument'), event, seen, depth + 1)
            if expression.get('kind') == 'binary_expression':
                return (expression.get('operator') in ('+', '-') and
                        (carrier_pointer(expression.get('left'), event, seen, depth + 1)
                         or expression.get('operator') == '+' and carrier_pointer(expression.get('right'), event, seen, depth + 1)))
            return False
        def origins(expression, event, seen=(), depth=0):
            expression = _unparen(expression)
            if not expression:
                return set()
            if depth >= 24:
                return {unknown}
            if expression.get('kind') == 'identifier':
                name = expression['expression']
                local = _local(function, name, event)
                if name == carrier_name and local and local.get('declaration_id') == carrier.get('declaration_id'):
                    return {whole}
                if not local or local.get('declaration_id') in seen:
                    return set()
                seen = (*seen, local.get('declaration_id'))
                result = origins(local.get('initializer_node'), local, seen, depth + 1)
                for write in function['events']['writes']:
                    if (write.get('target') == name and write.get('byte_start', -1) < _point(event)
                            and not _exclusive(write, event)
                            and (_local(function, name, write) or {}).get('declaration_id') == local.get('declaration_id')):
                        incoming = origins(write.get('value_node'), write, seen, depth + 1)
                        if write.get('operator') != '=' and (result or incoming):
                            result.add(unknown)
                        result.update(incoming)
                return result
            if expression.get('kind') == 'subscript_expression':
                base = origins(expression.get('argument'), event, seen, depth + 1)
                index = _constant_at(function, (expression.get('index') or {}).get('expression', ''), event)
                if not base:
                    return set()
                if not carrier_pointer(expression.get('argument'), event):
                    return base
                if not isinstance(index, int) or isinstance(index, bool):
                    return {unknown}
                return shifted_origins(base, index)
            if expression.get('kind') == 'field_expression':
                result = origins(expression.get('argument'), event, seen, depth + 1)
                return {0 if origin == whole else origin for origin in result} if expression.get('operator') == '->' else result
            if expression.get('kind') == 'cast_expression':
                base = origins(expression.get('value'), event, seen, depth + 1)
                if carrier_pointer(expression.get('value'), event) and not carrier_pointer(expression, event):
                    return {unknown} if base else set()
                return base
            if expression.get('kind') in ('pointer_expression', 'unary_expression'):
                return origins(expression.get('argument'), event, seen, depth + 1)
            if expression.get('kind') == 'binary_expression':
                left = origins(expression.get('left'), event, seen, depth + 1)
                right = origins(expression.get('right'), event, seen, depth + 1)
                if not left and not right:
                    return set()
                left_pointer = carrier_pointer(expression.get('left'), event)
                right_pointer = carrier_pointer(expression.get('right'), event)
                if not left_pointer and not right_pointer:
                    return {unknown if origin == whole else origin for origin in left | right}
                operator = expression.get('operator')
                if left_pointer and left and not right_pointer and operator in ('+', '-'):
                    base = left
                    offset = _constant_at(function, (expression.get('right') or {}).get('expression', ''), event)
                    if operator == '-' and isinstance(offset, int):
                        offset = -offset
                elif right_pointer and right and not left_pointer and operator == '+':
                    base = right
                    offset = _constant_at(function, (expression.get('left') or {}).get('expression', ''), event)
                else:
                    return {unknown}
                return shifted_origins(base, offset) if isinstance(offset, int) and not isinstance(offset, bool) else {unknown}
            result = set()
            for value in expression.values():
                if isinstance(value, dict):
                    result.update(origins(value, event, seen, depth + 1))
                elif isinstance(value, list):
                    for child in value:
                        if isinstance(child, dict):
                            result.update(origins(child, event, seen, depth + 1))
            return {unknown if origin == whole else origin for origin in result}
        affected = origins(node, use)
        for row in rows:
            if whole in affected or unknown in affected or row['table_entry_index'] in affected:
                yield {'kind': 'parser-field', 'scope': function['id'], 'table_id': row['table_id'],
                       'table_entry_index': row['table_entry_index'], 'storage_field': 'u_obj'}


def _input_binding_mention(function, node, use, name, declaration, seen=(), depth=0):
    """Follow possible scoped lvalue aliases without claiming a definite store."""
    node = _unparen(node)
    if not node:
        return False
    if depth >= _INPUT_DEPTH:
        return True
    function = _query_function(function)
    local, identity = None, None
    if node.get('kind') == 'identifier':
        identifier = node.get('expression')
        local = _local(function, identifier, use)
        identity = identifier, local.get('declaration_id') if local else None
        if identity == (name, declaration):
            return True
        if not local or identity in seen:
            return False
    key = (function.identity(node), function.identity(use), name, declaration,
           function.seen_key(node, use, seen, depth, True), depth)
    if key not in function.binding_mentions:
        function.binding_mentions[key] = _input_binding_mention_query(
            function, node, use, name, declaration, seen, depth, local, identity)
    return function.binding_mentions[key]


def _input_binding_mention_query(function, node, use, name, declaration, seen, depth, local, identity):
    if node.get('kind') == 'identifier':
        origins = function.alias_origins(node.get('expression'), local, use)
        return any(_input_binding_mention(function, origin, event, name, declaration, (*seen, identity), depth + 1)
                   for origin, event in origins)
    return any(_input_binding_mention(function, child, use, name, declaration, seen, depth + 1)
               for child in _input_children(node, True))


def _input_parameter_roots(function, node, use, seen=(), depth=0):
    """Parameter indices whose values may reach node, in first-occurrence order without repeats.

    Roots are keyed by parameter index alone, so every result is an immutable tuple
    bounded by the parameter count and is shared by memo hits instead of copied.
    """
    node = _unparen(node)
    if not node:
        return ()
    if depth >= _INPUT_DEPTH:
        return tuple(range(len(function['parameters'])))
    function = _query_function(function)
    local = None
    if node.get('kind') == 'identifier':
        name = node.get('expression')
        local = _local(function, name, use)
        if (name, local.get('declaration_id') if local else None) in seen:
            return (function['parameters'].index(name),) if not local and name in function['parameters'] else ()
    key = function.identity(node), function.identity(use), function.seen_key(node, use, seen, depth, False), depth
    roots = function.parameter_roots.get(key)
    if roots is None:
        roots = function.parameter_roots[key] = _input_parameter_roots_query(function, node, use, seen, depth, local)
    return roots


def _input_parameter_roots_query(function, node, use, seen, depth, local):
    roots = []
    found = set()
    total = len(function['parameters'])
    if node.get('kind') == 'identifier':
        name = node.get('expression')
        if not local and name in function['parameters']:
            roots.append(function['parameters'].index(name))
            found.add(roots[0])
        inner = (*seen, (name, local.get('declaration_id') if local else None))
        sources = ((origin, event, inner) for origin, event in function.root_origins(name, local, use))
    else:
        sources = ((child, use, seen) for child in _input_children(node, False))
    for source, event, path in sources:
        # Once every parameter is present no later source can add a root or change the order.
        if len(found) == total:
            break
        for index in _input_parameter_roots(function, source, event, path, depth + 1):
            if index not in found:
                found.add(index)
                roots.append(index)
    return tuple(roots)


def _private_input_slot(function, name, use, permitted_pointer=None):
    local = _local(function, name, use)
    if not local and name not in function['parameters']:
        return False
    if local and not _automatic_definition(local):
        return False
    identity = local.get('declaration_id') if local else None
    start, end = local.get('byte_start', -1) if local else -1, _point(use)
    if end is None:
        return False
    array = bool(local and local.get('array_dimensions'))
    for event in (function['events']['variables'] + function['events']['writes'] + function['events']['calls']):
        if not start <= event.get('byte_start', -1) < end or _exclusive(event, use):
            continue
        if permitted_pointer and event.get('declaration_id') == permitted_pointer.get('declaration_id'):
            continue
        owner = _local(function, name, event)
        same_owner = (owner.get('declaration_id') if owner else None) == identity
        nodes = (_call_operand_nodes(event) if 'callee' in event else
                 [event.get('initializer_node') or event.get('value_node') or {}])
        if same_owner and any(_address_mentioned(function, node, name) or array and _mentions(node, name) for node in nodes):
            return False
        if permitted_pointer:
            pointer = _local(function, permitted_pointer['name'], event)
            if (pointer and pointer.get('declaration_id') == permitted_pointer.get('declaration_id')
                    and any(_mentions(node, permitted_pointer['name']) for node in nodes)):
                return False
    return True


def _private_input_lvalue(function, node, use):
    """Recognise only unescaped C slots, owned arrays and direct local addresses."""
    node = _unparen(node)
    if node.get('kind') == 'identifier':
        return _private_input_slot(function, node.get('expression'), use)
    if node.get('kind') == 'field_expression' and node.get('operator') == '.':
        return _private_input_lvalue(function, node.get('argument'), use)
    if node.get('kind') == 'subscript_expression':
        base = _unparen(node.get('argument'))
        local = _local(function, base.get('expression'), use) if base.get('kind') == 'identifier' else None
        dimensions = local.get('array_dimensions', []) if local else []
        index = _constant_at(function, (node.get('index') or {}).get('expression', ''), use)
        extent = _constant_at(function, dimensions[0]['expression'], local) if len(dimensions) == 1 else None
        return (isinstance(index, int) and isinstance(extent, int) and 0 <= index < extent
                and _private_input_slot(function, base['expression'], use))
    if (node.get('kind') == 'pointer_expression' and node.get('operator') == '*'
            or node.get('kind') == 'field_expression' and node.get('operator') == '->'):
        pointer = _unparen(node.get('argument'))
        local = _local(function, pointer.get('expression'), use) if pointer.get('kind') == 'identifier' else None
        initial = _unparen(local.get('initializer_node')) if local else {}
        owner = _unparen(initial.get('argument'))
        before_store = {**use, 'byte_end': use.get('byte_start')}
        if (not local or initial.get('kind') != 'pointer_expression' or initial.get('operator') != '&'
                or owner.get('kind') != 'identifier'
                or (_local(function, owner.get('expression'), local) or {}).get('declaration_id') !=
                   (_local(function, owner.get('expression'), use) or {}).get('declaration_id')
                or not _unchanged(function, pointer['expression'], before_store, local)):
            return False
        return _private_input_slot(function, owner['expression'], use, local)
    return False


class _UseTable:
    """Input-use rows as an ordered set keyed by full value.

    Consumers treat uses as facts (membership, any-unresolved, sorted reason sets), so a
    row repeated by overlapping event views of one statement carries no further
    information. Rows keep first-occurrence order. Candidates are bucketed on cheap scalar
    fields and confirmed by full value equality, so nested guard chains are never hashed.
    """

    def __init__(self, rows=()):
        self.rows = []
        self.buckets = {}
        for row in rows:
            self.add(row)

    def add(self, row):
        reference = row.get('source_ref') or {}
        span = row.get('source_span') or {}
        key = (row.get('helper'), row.get('operand'), row.get('resolution'), row.get('reason'),
               row.get('byte_start'), row.get('byte_end'), row.get('via_helper'),
               _reference_key(reference) or reference.get('kind'),
               span.get('start_line'), span.get('end_line'), len(row.get('guards') or ()))
        bucket = self.buckets.setdefault(key, [])
        if row not in bucket:
            bucket.append(row)
            self.rows.append(row)


def _input_uses(function, selected, composed_calls=(), converted_calls=(), lifted=()):
    function = _query_function(function)
    table = _UseTable(lifted)
    uses = table.add
    for call in function['events']['calls']:
        if not selected(call):
            continue
        helper = call_parts(call['expression'])[0]
        arguments = _call_arguments(call)
        nodes = _call_operand_nodes(call)
        for index, expression in enumerate(arguments):
            node = nodes[index] if index < len(nodes) else None
            reference = source_reference(function, expression, call)
            operand, address = _operand(expression, node)
            if not reference and operand:
                reference = source_reference(function, operand, call)
            known = not address and (call.get('byte_start') in composed_calls or qualified_template(call) and (helper in INPUT_HELPERS or helper in CONSTRUCTORS or helper in {'mp_obj_get_type', 'mp_obj_new_tuple', 'mp_obj_new_str_of_type', 'mp_obj_new_str_type_from_vstr'}))
            converted = (known and CONSTRUCTORS.get(helper) in ('bool', 'int')
                         and _unparen(node).get('kind') == 'call_expression'
                         and (_unparen(node).get('byte_start'), _unparen(node).get('byte_end')) in converted_calls)
            if reference:
                uses({'source_ref': reference, 'helper': helper, 'operand': index, 'guards': call.get('guards', []),
                      'resolution': 'bounded-resolved' if known else 'unresolved',
                      'source_span': call['source_span'], 'callee': call.get('callee'),
                      'byte_start': call.get('byte_start'), 'byte_end': call.get('byte_end')})
            elif not converted:
                for root in _input_parameter_roots(function, node, call):
                    uses({'source_ref': function.parameter_ref(root), 'helper': helper, 'operand': index,
                          'guards': call.get('guards', []),
                          'resolution': 'unresolved', 'source_span': call['source_span'], 'callee': call.get('callee'),
                          'byte_start': call.get('byte_start'), 'byte_end': call.get('byte_end'),
                          'reason': 'input consumer retains conservative parameter provenance after def/use barriers'})
            if not converted and (not reference or not known or address):
                for field in _parser_object_uses(function, node, call):
                    uses({'source_ref': field, 'helper': helper, 'operand': index, 'guards': call.get('guards', []),
                          'resolution': 'unresolved', 'source_span': call['source_span'], 'callee': call.get('callee'),
                          'byte_start': call.get('byte_start'), 'byte_end': call.get('byte_end'),
                          'reason': 'unassessed parser output-storage consumer can constrain an original object field'})
    def inspect(node, event):
        if not node:
            return
        if node.get('kind') in ('subscript_expression', 'field_expression') and source_reference(function, node.get('expression'), event):
            return
        if node.get('kind') in ('binary_expression', 'conditional_expression', 'cast_expression', 'pointer_expression', 'unary_expression', 'update_expression', 'subscript_expression', 'field_expression', 'comma_expression'):
            def references(child):
                ref = source_reference(function, child.get('expression'), event) if child.get('kind') != 'field_identifier' else None
                if ref:
                    yield ref
                elif child.get('kind') == 'identifier':
                    for root in _input_parameter_roots(function, child, event):
                        yield function.parameter_ref(root)
                for value in child.values():
                    if isinstance(value, dict):
                        yield from references(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                yield from references(item)
            for reference in {_reference_key(ref): ref for ref in references(node)}.values():
                uses({'source_ref': reference, 'helper': None, 'guards': event.get('guards', []),
                      'resolution': 'unresolved', 'source_span': event.get('source_span'),
                      'reason': 'non-call derived operation requires an accepted-domain proof'})
            return
        for value in node.values():
            if isinstance(value, dict):
                inspect(value, event)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        inspect(item, event)
    for event in (function['events']['returns'] + function['events']['writes'] + function['events']['conditions']
                  + function['events']['calls'] + function['events']['variables'] + function['events'].get('expressions', [])
                  + function['events'].get('control_expressions', [])):
        if selected(event):
            inspect(event.get('expression_node') or event.get('value_node') or event.get('initializer_node'), event)
            if event.get('control_kind') and _unparen(event.get('expression_node')).get('kind') == 'identifier':
                for root in _input_parameter_roots(function, event['expression_node'], event):
                    uses({'source_ref': function.parameter_ref(root), 'helper': None, 'guards': event.get('guards', []),
                          'resolution': 'unresolved', 'source_span': event.get('source_span'),
                          'reason': 'native controlling-expression test requires an accepted-domain proof'})
            if event.get('target_node'):
                inspect(event['target_node'], {**event, 'byte_end': event.get('byte_start')})
                if not _private_input_lvalue(function, event['target_node'], event):
                    reference = source_reference(function, (event.get('value_node') or {}).get('expression'), event)
                    roots = ([reference] if reference else
                             [function.parameter_ref(root) for root in _input_parameter_roots(function, event.get('value_node'), event)])
                    roots.extend(_parser_object_uses(function, event.get('value_node'), event))
                    for origin in {_reference_key(root): root for root in roots}.values():
                        uses({'source_ref': origin, 'helper': None, 'guards': event.get('guards', []),
                              'resolution': 'unresolved', 'source_span': event.get('source_span'),
                              'byte_start': event.get('byte_start'), 'byte_end': event.get('byte_end'),
                              'reason': 'input value escapes through unqualified storage'})
    return table.rows


def _certified_parser_consumer(use, conversion):
    consumer = conversion.get('consumer_authority') or {}
    protocol = conversion.get('helper_contract_authority', {}).get('parser_protocol') or {}
    compact = consumer.get('helper_contract_authority') or {}
    reference = use.get('source_ref') or {}
    return (conversion.get('kind') == 'object-carrier'
            and conversion.get('resolution') in ('resolved', 'bounded-resolved')
            and conversion.get('helper_contract_authority', {}).get('resolution') in ('resolved', 'bounded-resolved')
            and consumer.get('resolution') == 'bounded-resolved'
            and protocol.get('resolution') == compact.get('resolution') == 'bounded-resolved'
            and protocol.get('kind') == compact.get('kind') == 'selected-indexed-parser-protocol'
            and bool(protocol.get('parser_identity'))
            and protocol.get('parser_identity') == compact.get('parser_identity') == conversion.get('parser_identity')
            and bool(protocol.get('source')) and protocol.get('source') == compact.get('source')
            and use.get('helper') == (consumer.get('callee') or {}).get('name') == 'mp_arg_parse_all'
            and (consumer.get('callee') or {}).get('kind') == 'FUNCTION_DECL'
            and reference.get('kind') == 'c-parameter'
            and reference.get('scope') == consumer.get('parser_scope')
            and reference.get('parameter') == consumer.get('vector_parameter')
            and use.get('operand') == consumer.get('operand') == 1
            and isinstance(consumer.get('byte_start'), int) and isinstance(consumer.get('byte_end'), int)
            and consumer['byte_start'] < consumer['byte_end']
            and all(use.get(key) == consumer.get(key)
                    for key in ('callee', 'source_span', 'byte_start', 'byte_end', 'guards')))


def bind_public_contracts(summary, public_bindings):
    """Compose source facts with qualified inventory bindings without mutation."""
    result = deepcopy(summary)
    parameters = deepcopy(public_bindings.get('parameters', []))
    receiver = deepcopy(public_bindings.get('receiver'))
    if receiver and 'binding_authority' not in receiver:
        receiver['binding_authority'] = deepcopy(receiver.get('authority', {}))
    all_bindings = parameters + ([receiver] if receiver else [])
    by_reference = {}
    name_counts = {}
    for binding in all_bindings:
        name_counts[binding.get('name')] = name_counts.get(binding.get('name'), 0) + 1
    for binding in all_bindings:
        authority = binding.get('binding_authority', {})
        if authority.get('resolution') not in ('resolved', 'bounded-resolved'):
            continue
        refs = binding.get('source_refs', [binding.get('source_ref')])
        for reference in refs:
            key = _reference_key(reference)
            if key is not None:
                by_reference.setdefault(key, []).append(binding)

    def lookup(reference):
        matches = by_reference.get(_reference_key(reference), [])
        return matches[0] if len(matches) == 1 and name_counts.get(matches[0].get('name')) == 1 else None

    def compose(contract):
        copied = deepcopy(contract)
        binding = lookup(contract.get('source_ref'))
        if contract.get('kind') == 'argument':
            if binding:
                copied['exported_argument'] = binding['name']
                copied['public_binding_authority'] = binding['binding_authority']
                native = binding.get('native_type')
                if isinstance(native, dict):
                    native = native.get('name', native.get('kind'))
                if binding is receiver:
                    copied.pop('exported_argument', None)
                    if native in ('str', 'bytes', 'bytearray') and binding.get('nominal_authority_resolution') in ('resolved', 'bounded-resolved'):
                        copied.update(kind=native, native_identity_relation=deepcopy(contract))
                    else:
                        copied['selected_receiver_binding'] = deepcopy(binding)
                        copied['representation_resolution'] = 'unresolved'
                        copied['representation_unresolved'] = ['native owner occurrence does not establish complete unbound receiver acceptance']
                if not binding.get('required', False):
                    copied['optional_identity'] = True
                    if binding.get('default_contract') is not None:
                        copied['default_contract'] = binding['default_contract']
            else:
                copied.update(kind='unresolved', reason='return source reference lacks unique qualified public binding')
        elif contract.get('kind') == 'same-native-family':
            native = binding.get('native_type') if binding else None
            if isinstance(native, dict):
                native = native.get('name', native.get('kind'))
            target = contract.get('native_family_mapping', {}).get(native)
            if target in ('str', 'bytes', 'bytearray') and contract.get('family_resolution') == 'bounded-resolved' and binding and binding.get('nominal_authority_resolution') in ('resolved', 'bounded-resolved'):
                copied.update(kind=target, native_family_relation=deepcopy(contract),
                              public_binding_authority=binding['binding_authority'])
            else:
                copied['representation_resolution'] = 'unresolved'
                copied['representation_unresolved'] = ['selected native builder family has no qualified receiver-domain nominal specialization']
                if binding:
                    copied['selected_receiver_binding'] = deepcopy(binding)
        if 'elements' in contract:
            copied['elements'] = [compose(element) for element in contract['elements']]
        return copied

    result['returns'] = [compose(contract) for contract in summary.get('returns', [])]
    public = []
    domain_kinds = {'object/truth-tested': 'object', 'integer-conversion/helper-boundary': 'integer-conversion-capability',
                    'readable-buffer-capability': 'readable-buffer-capability',
                    'writable-buffer-capability': 'writable-buffer-capability',
                    'str-or-bytes': 'str-or-bytes', 'tuple-or-list/exact-length': 'tuple-or-list/exact-length'}
    for binding in parameters:
        refs = {_reference_key(reference) for reference in binding.get('source_refs', [binding.get('source_ref')])}
        refs.discard(None)
        constraints = []
        for fact in summary.get('inputs', []):
            relation = fact.get('bindings', [{}])[0] if fact.get('bindings') else {}
            if _reference_key(relation.get('source_ref', relation)) in refs:
                constraints.append({'kind': domain_kinds.get(fact['contract'], fact['contract']),
                                    'guards': deepcopy(fact.get('guards', [])),
                                    'domain_authority': deepcopy(fact.get('domain_authority', {})),
                                    'successful_path_applicability': deepcopy(fact.get('successful_path_applicability', {})),
                                    'operands': deepcopy(fact.get('arguments', [])),
                                    'resolution': fact.get('domain_authority', {}).get('resolution', 'unresolved'),
                                    'source_ref': deepcopy(relation.get('source_ref', relation)),
                                    'evidence': {'callee': fact.get('callee'), 'source_span': fact.get('source_span')},
                                    'applicability': 'supplied-input'})
                if fact.get('extent') is not None:
                    constraints[-1]['extent'] = deepcopy(fact['extent'])
        vector_carriers = {(reference.get('scope'), reference.get('vector_parameter'))
                           for reference in binding.get('source_refs', []) if reference.get('kind') == 'vector-item'}
        uses = [use for use in summary.get('input_uses', [])
                if _reference_key(use.get('source_ref')) in refs
                or use.get('resolution') == 'unresolved' and use.get('source_ref', {}).get('kind') == 'c-parameter'
                and (use['source_ref'].get('scope'), use['source_ref'].get('parameter')) in vector_carriers]
        authority = binding.get('binding_authority', {})
        qualified = authority.get('resolution') in ('resolved', 'bounded-resolved') and not summary.get('frontend_unresolved') and name_counts.get(binding.get('name')) == 1
        conversion = binding.get('parser_conversion') or {}
        conversion_kind = conversion.get('kind') if conversion.get('resolution') in ('resolved', 'bounded-resolved') and conversion.get('helper_contract_authority', {}).get('resolution') in ('resolved', 'bounded-resolved') else None
        carrier = conversion_kind == 'object-carrier'
        if carrier:
            uses = [use for use in uses if not _certified_parser_consumer(use, conversion)]
            conversion_kind = 'object'
        kinds = {constraint['kind'] for constraint in constraints
                 if constraint.get('successful_path_applicability', {}).get('resolution') == 'bounded-resolved'
                 and constraint.get('domain_authority', {}).get('resolution') in ('resolved', 'bounded-resolved')}
        narrow = {constraint['kind'] for constraint in constraints if constraint['kind'] != 'object'}
        reasons = []
        if conversion_kind in ('object', 'integer-conversion-capability'):
            kinds.add(conversion_kind)
            constraints.append({'kind': conversion_kind, 'guards': [], 'applicability': 'supplied-input',
                                'domain_authority': deepcopy(conversion.get('helper_contract_authority', {})),
                                'resolution': 'bounded-resolved', 'evidence': deepcopy(conversion.get('authority', conversion))})
        unknown_uses = any(use['resolution'] == 'unresolved' for use in uses)
        if 'object' in kinds and len(kinds - {'object'}) == 1 and narrow <= kinds:
            kinds.discard('object')
        if qualified and not carrier and conversion_kind in ('object', 'integer-conversion-capability'):
            # Parser conversions consume the supplied object before downstream
            # operations on the converted C scalar.
            domain = {'kind': conversion_kind}
        elif qualified and len(kinds) == 1 and not unknown_uses and narrow <= kinds:
            domain = {'kind': next(iter(kinds))}
        elif qualified and not constraints and not unknown_uses and any(
                contract.get('kind') == 'argument' and contract.get('exported_argument') == binding['name']
                for contract in result['returns']) and summary.get('return_resolution') == 'bounded-resolved':
            domain = {'kind': 'object'}
        else:
            domain = None
            reasons.append('whole accepted domain not established from complete bounded uses')
            reasons.extend(reason for constraint in constraints for reason in constraint.get('domain_authority', {}).get('unresolved', []))
            reasons.extend(reason for constraint in constraints
                           for reason in constraint.get('successful_path_applicability', {}).get('unresolved', []))
            reasons.extend(use.get('reason', 'unknown input consumer prevents whole-domain qualification') for use in uses if use.get('resolution') == 'unresolved')
            reasons = sorted(set(reasons))
        record = {'name': binding['name'], 'accepted_domain': {
            'resolution': 'bounded-resolved' if domain else 'unresolved', 'contract': domain,
            'reasons': reasons, 'authority': [deepcopy(authority)] + [deepcopy(constraint.get('domain_authority', {})) for constraint in constraints] if domain else []},
            'constraints': constraints, 'binding_authority': deepcopy(authority)}
        if binding.get('result_storage') is not None:
            record['result_storage'] = deepcopy(binding['result_storage'])
        if domain and domain['kind'] in ('integer-conversion-capability', 'readable-buffer-capability', 'writable-buffer-capability', 'tuple-or-list/exact-length'):
            record['accepted_domain'].update(representation_resolution='unresolved',
                                            representation_unresolved=['target native capability has no qualified checker representation'])
        public.append(record)
    result['public_parameters'] = public
    unresolved = list(result.get('return_unresolved', []))
    for contract in result['returns']:
        if contract['kind'] in ('unresolved', 'void-C-return'):
            unresolved.append(contract.get('reason', 'public return relation unresolved'))
        if contract['kind'] == 'tuple' and any(element['kind'] in ('unresolved', 'void-C-return') for element in contract.get('elements', [])):
            unresolved.append('public tuple element relation unresolved')
    result['return_unresolved'] = sorted(set(unresolved))
    if unresolved:
        result['return_resolution'] = 'unresolved'
    return result




def _allocated_result(function, returned, functions, linked_functions, contracts, depth):
    """Require a selected typed allocation before interpreting pointer wrapping."""
    expression = returned['expression']
    template = contracts.get('obj_reference', '')
    pointer = None
    if '& mp_type_type' in template:
        pattern = re.escape(template).replace(re.escape('& mp_type_type'), r'([A-Za-z_]\w*)')
        matched = re.fullmatch(pattern, expression)
        pointer = matched[1] if matched else None
    if expression in {variable['name'] for variable in function['events']['variables']}:
        pointer = expression
    if not pointer:
        return None
    local = _local(function, pointer, returned)
    if not local or local.get('loops') or not _applicable(local, returned):
        return None
    if not _storage_unchanged(function, pointer, returned, local, protected_fields=('base', 'type')):
        return None
    initializer = local.get('initializer_node') or {}
    node = initializer
    while node.get('kind') in ('cast_expression', 'parenthesized_expression'):
        node = node.get('value') or next(iter(node.get('children', [])), {})
    helper, arguments = call_parts(node.get('expression', ''))
    call = event_call(function, node.get('expression'), local['source_span'])
    evidence = None
    if helper == 'mp_obj_malloc_helper' and len(arguments) == 2 and qualified_template(call) and arguments[1] == '& mp_type_int':
        allocator_authority = _allocation_helper_authority(call, function, functions, linked_functions)
        if allocator_authority.get('resolution') != 'bounded-resolved':
            return None
        if local.get('type_expression') != 'mp_obj_int_t' or not re.fullmatch(r'sizeof \( mp_obj_int_t \)', arguments[0]):
            return None
        evidence = {'callee': call['callee'], 'allocation_expression': initializer['expression'],
                    'type_expression': arguments[1], 'source_span': local['source_span'], 'allocator': allocator_authority}
    elif depth < 3:
        candidates = _selected_helpers(call, helper, functions, linked_functions, function)
        if len(candidates) == 1:
            callee, authority = candidates[0]
            child = semantics(callee, functions, [], authority.get('contracts', contracts), depth=depth + 1, linked_functions=linked_functions)
            if child.get('return_resolution') == 'bounded-resolved' and child['returns'] and all(
                    result.get('kind') == 'allocated-native-pointer' and result.get('native_type') == 'int'
                    for result in child['returns']):
                evidence = {'helper': callee['id'], 'authority': authority,
                            'allocation': [result['allocation_authority'] for result in child['returns']]}
    if not evidence:
        return None
    is_wrapped = expression != pointer
    return {'kind': 'int' if is_wrapped else 'allocated-native-pointer', 'native_type': 'int',
            'allocation_authority': evidence, 'evidence': expression,
            'pointer_declaration_id': local['declaration_id'],
            'object_conversion_authority': 'actual compiler-expanded MP_OBJ_FROM_PTR contract' if is_wrapped else None}


def _guard_node(function, guard):
    conditions = [condition for condition in function['events']['conditions']
                  if condition.get('condition_id') == guard.get('condition_id')
                  and condition['expression'] == guard['condition']]
    return _unparen(conditions[0].get('expression_node')) if len(conditions) == 1 else {}


def _flag_guard(function, guard, base, contract):
    node = _guard_node(function, guard)
    left = _unparen(node.get('left'))
    target = _unparen(left.get('argument'))
    value = _constant_at(function, contract, {'byte_start': guard.get('condition_id')})
    return (node.get('kind') == 'binary_expression' and node.get('operator') == '&'
            and left.get('kind') == 'field_expression' and left.get('operator') == '->'
            and left.get('field', {}).get('expression') == 'flags'
            and target.get('kind') == 'identifier' and target.get('expression') == base
            and isinstance(value, int) and value > 0
            and _constant_at(function, _unparen(node.get('right')).get('expression', ''),
                             {'byte_start': guard.get('condition_id')}) == value)


def _literal_message(node):
    node = _unparen(node)
    return (node.get('kind') == 'string_literal'
            or node.get('kind') == 'concatenated_string' and bool(node.get('children'))
            and all(child.get('kind') == 'string_literal' for child in node['children']))


def _terminal_literal_message(call, failure, definitions):
    """A source-selected literal codec is confined to a terminal error operand."""
    name, _ = call_parts(call['expression'])
    row = definitions.get(name) or {}
    function = row.get('function') or {}
    operands = _call_operand_nodes(call)
    error_name, _ = call_parts(failure['expression'])
    error_operands = _call_operand_nodes(failure)
    index = 1 if error_name in ('mp_raise_msg', 'mp_raise_msg_varg') else 0
    if (name != 'MP_COMPRESSED_ROM_TEXT' or row.get('authority', {}).get('resolution') not in ('resolved', 'bounded-resolved')
            or not function.get('source_span', {}).get('source', '').endswith('/py/misc.h')
            or function.get('parameters') != ['msg'] or call.get('callee') != function.get('declaration_identity')
            or (call.get('callee') or {}).get('kind') != 'FUNCTION_DECL'
            or len(operands) != 1 or not _literal_message(operands[0])
            or len(error_operands) <= index or call.get('guards') != failure.get('guards') or call.get('loops')):
        return None
    message = _unparen(error_operands[index])
    while message.get('kind') == 'cast_expression' and message.get('type', {}).get('expression') == 'mp_rom_error_text_t':
        message = _unparen(message.get('value'))
    if (message.get('kind') != 'call_expression' or message.get('expression') != call['expression']
            or message.get('byte_start') != call.get('byte_start') or message.get('byte_end') != call.get('byte_end')):
        return None
    return {'resolution': 'bounded-resolved', 'kind': 'literal-terminal-error-operand',
            'function_id': function['id'], 'callee': deepcopy(call['callee']),
            'source_span': deepcopy(call['source_span']), 'authority': deepcopy(row['authority'])}


def _source_body(definitions, name, call, suffix):
    row = definitions.get(name) or {}
    function = row.get('function') or {}
    authority = row.get('authority') or {}
    header = authority.get('header_declaration_identity', function.get('declaration_identity'))
    if (authority.get('resolution') not in ('resolved', 'bounded-resolved')
            or not function.get('source_span', {}).get('source', '').endswith(suffix)
            or not call or call.get('callee') != header
            or function.get('events', {}).get('semantic_unassessed')):
        return None
    return function


def _protocol_shape(node):
    """Compare bounded source expressions without erasing casts or operands."""
    node = _unparen(node)
    kind = node.get('kind')
    if kind == 'number_literal':
        return ('number', constant(node.get('expression', ''), {}))
    if kind == 'identifier':
        return ('identifier', node.get('expression'))
    if kind == 'binary_expression':
        return (node.get('operator'), _protocol_shape(node.get('left')), _protocol_shape(node.get('right')))
    if kind in ('pointer_expression', 'unary_expression', 'update_expression'):
        return (node.get('operator'), _protocol_shape(node.get('argument')))
    if kind == 'cast_expression':
        return ('cast', node.get('type', {}).get('expression'), _protocol_shape(node.get('value')))
    if kind == 'field_expression':
        return (node.get('operator'), _protocol_shape(node.get('argument')), node.get('field', {}).get('expression'))
    if kind == 'subscript_expression':
        return ('index', _protocol_shape(node.get('argument')), _protocol_shape(node.get('index')))
    if kind == 'sizeof_expression':
        return ('sizeof', node.get('expression'))
    return ('unsupported', kind, node.get('expression'))


def _tag_object_authority(definitions, call, role='mp_obj_is_obj'):
    function = _source_body(definitions, role, call, '/py/obj.h')
    if not function or len(function.get('parameters', [])) != 1:
        return None
    events = function['events']
    if (events.get('writes') or events.get('calls') or events.get('variables') or events.get('expressions')
            or events.get('conditions') or events.get('control_flow_unresolved')
            or any(node.get('kind') not in ('block', 'fallthrough', 'return') for node in _protocol_flow(events.get('flow', {})))
            or len(events.get('returns', [])) != 1):
        return None
    returned = events['returns'][0]
    shape = _protocol_shape(returned.get('expression_node'))
    operand = shape[1][1] if len(shape) == 3 and shape[0] == '==' and shape[1][0] == '&' else ()
    if (returned.get('guards') or returned.get('loops') or shape[2:] != (('number', 0),)
            or shape[1][2:] != (('number', 3),) or len(operand) != 3 or operand[0] != 'cast'
            or operand[1] not in ('mp_int_t', 'mp_uint_t', 'uintptr_t')
            or operand[2] != ('identifier', function['parameters'][0])):
        return None
    return {'resolution': 'bounded-resolved', 'kind': 'selected-A-object-tag-predicate',
            'mask': 3, 'object_tag': 0, 'function_id': function['id'],
            'source_span': function['source_span'], 'authority': deepcopy(definitions[role]['authority'])}


def _configured_tagged_word(expression):
    """Decode only closed low-bit words from selected compiler object contracts."""
    expression = strip_outer(expression or '')
    cast = re.fullmatch(r'\( mp_obj_t \) (.+)', expression)
    if cast:
        expression = strip_outer(cast[1])
    tokens = re.findall(r'0[xX][0-9a-fA-F]+|\d+|<<|[^\s]', expression)
    if (not tokens or len(tokens) > 32 or any(token not in ('(', ')', '<<', '|')
            and not re.fullmatch(r'0[xX][0-9a-fA-F]+|\d+', token) for token in tokens)):
        return None
    if any(int(token, 16 if token.lower().startswith('0x') else 10) > 31
           for token in tokens if re.fullmatch(r'0[xX][0-9a-fA-F]+|\d+', token)):
        return None
    value = constant(expression, {})
    return value if isinstance(value, int) and 0 <= value <= 31 else None


def _configured_encoding(expression, payload, shift, marker):
    def binary(expression, operator):
        tokens = strip_outer(expression).split()
        depth, positions = 0, []
        for index, token in enumerate(tokens):
            if token == '(':
                depth += 1
            elif token == ')':
                depth -= 1
            elif token == operator and depth == 0:
                positions.append(index)
            if depth < 0:
                return None
        if depth or len(positions) != 1:
            return None
        index = positions[0]
        return ' '.join(tokens[:index]), ' '.join(tokens[index + 1:])
    cast = re.fullmatch(r'\( mp_obj_t \) (.+)', strip_outer(expression or ''))
    combined = binary(cast[1], '|') if cast else None
    shifted = binary(combined[0], '<<') if combined else None
    if (not combined or not shifted or strip_outer(combined[1]) != marker
            or strip_outer(shifted[1]) != shift):
        return False
    cast = re.fullmatch(r'\( mp_uint_t \) (.+)', strip_outer(shifted[0]))
    return bool(cast and strip_outer(cast[1]) == payload)


def _native_getter_authority(definitions, call, contracts):
    function = _source_body(definitions, 'mp_obj_get_type', call, '/py/obj.c')
    if not function or function.get('parameters') != ['o_in']:
        return None
    events = function['events']
    returned = events.get('returns', [])
    if (events.get('writes') or events.get('expressions') or events.get('control_flow_unresolved') or len(returned) != 2
            or any(node.get('kind') not in ('block', 'fallthrough', 'if', 'return') for node in _protocol_flow(events.get('flow', {})))
            or len(events.get('conditions', [])) != 1
            or len(events.get('calls', [])) != 1 or len(events.get('variables', [])) != 2):
        return None
    predicate = events['calls'][0]
    tag = _tag_object_authority(definitions, predicate)
    if not tag or call_parts(predicate['expression']) != ('mp_obj_is_obj', ['o_in']):
        return None
    heap, immediate = returned
    if (heap.get('loops') or immediate.get('loops') or len(immediate.get('guards', [])) != 1
            or immediate['guards'][0]['branch'] != 'else' or
            [(guard['condition_id'], guard['branch']) for guard in heap.get('guards', [])] !=
            [(immediate['guards'][0]['condition_id'], 'then')]):
        return None
    condition = _guard_node(function, immediate['guards'][0])
    if (condition.get('kind') != 'call_expression' or condition.get('expression') != predicate['expression']
            or condition.get('byte_start') != predicate.get('byte_start')
            or condition.get('byte_end') != predicate.get('byte_end')):
        return None
    pointer = _local(function, 'o', heap)
    table = _local(function, 'types', immediate)
    if (not pointer or not table or pointer.get('type_expression') != 'mp_obj_base_t'
            or _protocol_shape(pointer.get('initializer_node')) != ('cast', 'void *', ('identifier', 'o_in'))
            or _protocol_shape(heap.get('expression_node')) != ('->', ('identifier', 'o'), 'type')
            or table.get('declaration_specifiers') != 'static const mp_obj_type_t'
            or table.get('declarator_expression') != '* const types [ ]'
            or _protocol_shape(immediate.get('expression_node')) !=
               ('index', ('identifier', 'types'), ('&', ('cast', 'uintptr_t', ('identifier', 'o_in')), ('number', 15)))):
        return None
    fields = _unparen(table.get('initializer_node')).get('children', [])
    if len(fields) != 16:
        return None
    expected = [None if index % 4 == 0 else 'mp_type_int' if index & 1
                else 'mp_type_str' if index & 7 == 2 else 'mp_type_NoneType' if index == 6
                else 'mp_type_bool' for index in range(16)]
    for field, name in zip(fields, expected):
        if name is None:
            if _protocol_shape(field) != ('cast', 'void *', ('number', 0)) and _protocol_shape(field) != ('number', 0):
                return None
        elif _protocol_shape(field) != ('&', ('identifier', name)):
            return None
    for name, tag_name in (('none', 'mp_type_NoneType'), ('false', 'mp_type_bool'), ('true', 'mp_type_bool')):
        value = _configured_tagged_word(contracts.get(name, ''))
        if not isinstance(value, int) or expected[value & 15] != tag_name:
            return None
    for name, payload, shift, marker in (('rom_int', '7', '1', '1'), ('rom_qstr', 'MP_QSTR___name__', '3', '2')):
        if not _configured_encoding(contracts.get(name, ''), payload, shift, marker):
            return None
    return {'resolution': 'bounded-resolved', 'kind': 'selected-A-native-type-projection',
            'function_id': function['id'], 'source_span': function['source_span'],
            'heap_projection': 'unchanged object base.type', 'immediate_type_table': expected,
            'object_tag_authority': tag, 'authority': deepcopy(definitions['mp_obj_get_type']['authority'])}


def _protocol_flow(node):
    yield node
    for child in node.get('children', []):
        yield from _protocol_flow(child)
    for key in ('then', 'else', 'body'):
        if node.get(key):
            yield from _protocol_flow(node[key])


def _protocol_scoped_uses(function, local, node):
    if not isinstance(node, dict):
        return True
    if node.get('kind') == 'identifier' and node.get('expression') in local:
        binding = _local(function, node['expression'], node)
        if not binding or binding.get('declaration_id') != local[node['expression']].get('declaration_id'):
            return False
    return all(_protocol_scoped_uses(function, local, value) if isinstance(value, dict) else
               all(_protocol_scoped_uses(function, local, child) for child in value if isinstance(child, dict))
               if isinstance(value, list) else True for value in node.values())


def _gc_region_authority(function):
    """Selected free-block reservation under normal GC-area state invariants."""
    events = function['events']
    if function.get('parameters') != ['n_bytes', 'alloc_flags'] or events.get('semantic_unassessed'):
        return None
    variables = events.get('variables', [])
    names = [variable['name'] for variable in variables]
    expected_names = {'has_finaliser', 'n_blocks', 'area', 'i', 'end_block', 'start_block', 'n_free', 'collected', 'a', 'bl', 'ret_ptr'}
    if len(names) != len(set(names)) or set(names) != expected_names:
        return None
    local = {variable['name']: variable for variable in variables}
    if not all(_automatic_definition(variable) for variable in local.values()):
        return None
    for category in ('calls', 'conditions', 'expressions', 'returns', 'variables', 'writes'):
        for event in events.get(category, []):
            for key in ('expression_node', 'initializer_node', 'target_node', 'value_node'):
                if not _protocol_scoped_uses(function, local, event.get(key)):
                    return None
    ident = lambda name: ('identifier', name)
    num = lambda value: ('number', value)
    area_field = lambda field: ('->', ident('area'), field)
    memory = lambda field: ('.', ('.', ident('mp_state_ctx'), 'mem'), field)
    block_count = _protocol_shape(local['n_blocks'].get('initializer_node'))
    if len(block_count) != 3 or block_count[0] != '/':
        return None
    block_size = block_count[2]
    if block_count[1] != ('&', ('-', ('+', ident('n_bytes'), block_size), num(1)), ('~', ('-', block_size, num(1)))):
        return None
    if not (block_size == ('*', num(4), ('sizeof', 'sizeof ( mp_uint_t )'))
            or block_size[0] == 'number' and isinstance(block_size[1], int)
            and block_size[1] > 0 and block_size[1] & (block_size[1] - 1) == 0):
        return None
    if (_protocol_shape(local['a'].get('initializer_node')) != ('index', area_field('gc_alloc_table_start'), ident('i'))
            or local['area'].get('type_expression') != 'mp_state_mem_area_t'
            or local['area'].get('declarator_expression') != '* area'
            or _protocol_shape(local['ret_ptr'].get('initializer_node')) !=
               ('cast', 'void *', ('+', area_field('gc_pool_start'), ('*', ident('start_block'), block_size)))):
        return None
    flows = list(_protocol_flow(events.get('flow', {})))
    labels = [node for node in flows if node.get('kind') == 'label']
    jumps = [node for node in flows if node.get('kind') == 'goto']
    scans = [node for node in flows if node.get('kind') == 'loop'
             and node.get('condition_expression') == 'i < area -> gc_alloc_table_byte_len']
    if (len(labels) != 1 or labels[0].get('name') != 'found' or len(jumps) != 4
            or len(scans) != 1 or any(node.get('kind') in ('break', 'continue') for node in flows)):
        return None
    label = labels[0]
    scan = scans[0]
    if (scan.get('initializer_expression') != 'i = area -> gc_last_free_atb_index'
            or scan.get('update_expression') != 'i ++'
            or any(jump.get('target') != 'found' or jump['byte_start'] >= label['byte_start'] for jump in jumps)):
        return None
    free_checks = [node for node in _protocol_flow(scan['body']) if node.get('kind') == 'if'
                   and node.get('condition') != '++ n_free >= n_blocks']
    if len(free_checks) != 4:
        return None
    writes = events.get('writes', [])
    matched = set()
    for ordinal, check in enumerate(free_checks):
        guard = {'condition': check['condition'], 'condition_id': check['byte_start']}
        if _protocol_shape(_guard_node(function, guard)) != ('==', ('&', ident('a'), num(3 << (2 * ordinal))), num(0)):
            return None
        children = list(_protocol_flow(check.get('then', {})))
        inner = [node for node in children if node.get('kind') == 'if']
        goto = [node for node in children if node.get('kind') == 'goto']
        if len(inner) != 1 or inner[0].get('condition') != '++ n_free >= n_blocks' or len(goto) != 1:
            return None
        successful = list(_protocol_flow(inner[0].get('then', {})))
        if (any(node.get('kind') not in ('block', 'fallthrough', 'goto') for node in successful)
                or [node['byte_start'] for node in successful if node.get('kind') == 'goto'] != [goto[0]['byte_start']]
                or any(node.get('kind') == 'goto' for node in _protocol_flow(inner[0].get('else', {})))):
            return None
        branch_writes = [write for write in writes if any(
            entry['condition_id'] == check['byte_start'] for entry in write.get('guards', []))]
        if len(branch_writes) != 3:
            return None
        increment, selected, reset = branch_writes
        if (increment.get('target') != 'n_free' or increment.get('expression') != '++ n_free'
                or selected.get('target') != 'i'
                or _protocol_shape(selected.get('value_node')) != ('+', ('*', ident('i'), num(4)), num(ordinal))
                or selected.get('operator') != '=' or selected['byte_start'] >= goto[0]['byte_start']
                or reset.get('target') != 'n_free' or reset.get('operator') != '='
                or _protocol_shape(reset.get('value_node')) != num(0)
                or [(entry['condition_id'], entry['branch']) for entry in increment.get('guards', [])][-1:] != [(check['byte_start'], 'then')]
                or [(entry['condition_id'], entry['branch']) for entry in selected.get('guards', [])][-2:] !=
                   [(check['byte_start'], 'then'), (inner[0]['byte_start'], 'then')]
                or [(entry['condition_id'], entry['branch']) for entry in reset.get('guards', [])][-1:] != [(check['byte_start'], 'else')]):
            return None
        matched.update(write['byte_start'] for write in branch_writes)
    marks = [write for write in writes if _unparen(write.get('target_node')).get('kind') == 'subscript_expression'
             and _protocol_shape(_unparen(write.get('target_node')).get('argument')) == area_field('gc_alloc_table_start')]
    if len(marks) != 2:
        return None
    head, tail = marks
    tail_loops = [node for node in flows if node.get('kind') == 'loop'
                  and node.get('initializer_expression') == 'size_t bl = start_block + 1 ;'
                  and node.get('condition_expression') == 'bl <= end_block' and node.get('update_expression') == 'bl ++']
    if len(tail_loops) != 1:
        return None
    for mark, index, state in ((head, 'start_block', 1), (tail, 'bl', 2)):
        if (mark.get('operator', '').replace(' ', '') != '|='
                or _protocol_shape(mark.get('target_node')) !=
                   ('index', area_field('gc_alloc_table_start'), ('/', ident(index), num(4)))
                or _protocol_shape(mark.get('value_node')) !=
                   ('<<', num(state), ('*', num(2), ('&', ident(index), ('-', num(4), num(1)))))
                or mark['byte_start'] <= label['byte_start'] or mark['byte_start'] >= local['ret_ptr']['byte_start']):
            return None
        loops = [node for node in flows if node.get('kind') == 'loop' and node['byte_start'] in mark.get('loops', [])]
        if (any(node.get('loop_kind') != 'do_statement' or constant(node.get('condition_expression', ''), {}) != 0
                for node in loops if node['byte_start'] != tail_loops[0]['byte_start'])
                or (index == 'bl') != (tail_loops[0]['byte_start'] in mark.get('loops', []))):
            return None
        matched.add(mark['byte_start'])
    boundaries = {'end_block': ident('i'), 'start_block': ('+', ('-', ident('i'), ident('n_free')), num(1))}
    for name, value in boundaries.items():
        selected = [write for write in writes if write.get('target') == name]
        if (len(selected) != 1 or selected[0].get('operator') != '='
                or _protocol_shape(selected[0].get('value_node')) != value
                or not label['byte_start'] <= selected[0]['byte_start'] < head['byte_start']
                or selected[0].get('guards', []) != head.get('guards', []) or selected[0].get('loops')):
            return None
        matched.add(selected[0]['byte_start'])
    scan_loops = local['a'].get('loops', [])
    if len(scan_loops) != 3 or scan_loops[0] != scan['byte_start']:
        return None
    area_loop, outer_loop = scan_loops[1:]
    loop_nodes = {node['byte_start']: node for node in flows if node.get('kind') == 'loop'}
    if (area_loop not in loop_nodes or outer_loop not in loop_nodes
            or loop_nodes[area_loop].get('condition_expression') != 'area != ( ( void * ) 0 )'
            or any(loop_nodes[outer_loop].get(key) for key in
                   ('condition_expression', 'initializer_expression', 'update_expression'))):
        return None
    update_roles = set()
    for write in writes:
        if write['byte_start'] in matched:
            continue
        target = _protocol_shape(write.get('target_node'))
        value = _protocol_shape(write.get('value_node'))
        operator = write.get('operator', '').replace(' ', '')
        before = write['byte_start'] < label['byte_start']
        scope = write.get('scope_id')
        role = None
        if before and target == ident('area') and operator == '=':
            if value == ('&', memory('area')) and write.get('loops') == [outer_loop] and write['byte_start'] < area_loop:
                role = 'select-area'
            elif value == ('cast', 'void *', num(0)) and scope == area_loop:
                role = 'next-area'
        elif before and target == ident('i'):
            if scope == area_loop and operator == '=' and value == num(0):
                role = 'next-area-index'
            elif scope == scan['byte_start'] and operator == '=' and value == area_field('gc_last_free_atb_index'):
                role = 'scan-initial-index'
            elif scope == scan['byte_start'] and write['expression'] == 'i ++':
                role = 'scan-next-index'
        elif before and target == ident('n_free') and operator == '=' and value == num(0):
            if write.get('loops') == scan_loops[1:] and write['byte_start'] < scan['byte_start']:
                role = 'initial-free-run'
        elif target == ident('bl') and write['expression'] == 'bl ++' and scope == tail_loops[0]['byte_start']:
            role = 'next-tail-block'
        if role:
            if role in update_roles:
                return None
            update_roles.add(role)
            continue
        if before and target == ident('collected') and operator == '=' and value == num(1):
            continue
        if target == area_field('gc_last_free_atb_index') and operator == '=' and value == ('/', ('+', ident('i'), num(1)), num(4)):
            continue
        if target == area_field('gc_last_used_block') and operator == '=' and write.get('value_node', {}).get('kind') == 'parenthesized_expression':
            maximum = _unparen(write['value_node'])
            if (maximum.get('kind') == 'conditional_expression'
                    and _protocol_shape(maximum.get('condition')) == ('>', area_field('gc_last_used_block'), ident('end_block'))
                    and _protocol_shape(maximum.get('consequence')) == area_field('gc_last_used_block')
                    and _protocol_shape(maximum.get('alternative')) == ident('end_block')):
                continue
        if target == memory('gc_alloc_amount') and operator == '+=' and value == ident('n_blocks'):
            continue
        if any(entry.get('condition') == 'has_finaliser' and entry.get('branch') == 'then' for entry in write.get('guards', [])):
            continue
        return None
    if update_roles != {'select-area', 'next-area', 'next-area-index', 'scan-initial-index',
                        'scan-next-index', 'initial-free-run', 'next-tail-block'}:
        return None
    # These guards are inherited from the two error-only entry exits.
    common = head.get('guards', [])
    if any(mark.get('guards', []) != common for mark in marks) or not 1 <= len(common) <= 2:
        return None
    if (any(entry['branch'] != 'else' for entry in common)
            or _protocol_shape(_guard_node(function, common[0])) != ('==', ident('n_blocks'), num(0))):
        return None
    if len(common) == 2:
        locked = _guard_node(function, common[1])
        left = _unparen(locked.get('left'))
        if (locked.get('kind') != 'binary_expression' or locked.get('operator') != '>'
                or _protocol_shape(locked.get('right')) != num(0)
                or left.get('kind') != 'field_expression' or left.get('field', {}).get('expression') != 'gc_lock_depth'):
            return None
    returns = events.get('returns', [])
    if (not returns or returns[-1].get('expression') != 'ret_ptr'
            or returns[-1].get('guards', []) != common or returns[-1].get('loops')
            or any(_protocol_shape(returned.get('expression_node')) not in
                   (num(0), ('cast', 'void *', num(0))) or returned['byte_start'] >= label['byte_start']
                   for returned in returns[:-1])):
        return None
    finaliser = _unparen(local['has_finaliser'].get('initializer_node'))
    if (finaliser.get('kind') != 'binary_expression' or finaliser.get('operator') != '&'
            or _protocol_shape(finaliser.get('left')) != ident('alloc_flags')
            or not isinstance(_constant_at(function, _unparen(finaliser.get('right')).get('expression', ''), local['has_finaliser']), int)):
        return None
    call_roles = []
    for call in events.get('calls', []):
        name, _ = call_parts(call['expression'])
        operands = [_protocol_shape(node) for node in _call_operand_nodes(call)]
        callee = call.get('callee') or {}
        source = callee.get('source_span', {}).get('source', '')
        disabled = any(entry.get('condition') == 'has_finaliser' and entry.get('branch') == 'then' for entry in call.get('guards', []))
        if callee.get('kind') != 'FUNCTION_DECL':
            return None
        if disabled:
            continue
        if name == 'memset' and source.endswith('/string.h') and call['byte_start'] > local['ret_ptr']['byte_start']:
            whole = ('*', ('+', ('-', ident('end_block'), ident('start_block')), num(1)), block_size)
            start = ('cast', 'byte *', ident('ret_ptr'))
            if operands not in ([start, num(0), whole], [('+', start, ident('n_bytes')), num(0), ('-', whole, ident('n_bytes'))]):
                return None
            call_roles.append('clear-reserved-region')
        elif name in ('mp_thread_recursive_mutex_lock', 'mp_thread_recursive_mutex_unlock') and source.endswith(('/py/mpthread.h', '/ports/unix/mpthreadport.h')):
            expected = [('&', memory('gc_mutex'))] + ([num(1)] if name.endswith('_lock') else [])
            if operands != expected:
                return None
            call_roles.append('allocator-state-mutex')
        elif call['byte_start'] < label['byte_start'] and name in ('gc_collect', 'mp_thread_get_state') and not operands and source.endswith(('/py/gc.h', '/py/mpthread.h', '/ports/unix/mpthreadport.h')):
            call_roles.append('normal-allocator-state-service')
        else:
            return None
    return {'resolution': 'bounded-resolved', 'kind': 'selected-gc-free-block-reservation',
            'function_id': function['id'], 'source_span': function['source_span'],
            'free_bitmap_masks': [3 << (2 * ordinal) for ordinal in range(4)],
            'head_mark_source_span': head['source_span'], 'tail_mark_source_span': tail['source_span'],
            'pool_return_source_span': local['ret_ptr']['source_span'], 'call_roles': call_roles,
            'invariants': ['GC-area allocation bitmap corresponds to disjoint pool blocks',
                           'normal GC collection and mutex state invariants'],
            'limitations': ['not a proof of collection liveness, thread safety, or general memory safety']}


def _allocator_failure_authority(definitions, call):
    function = _source_body(definitions, 'm_malloc_fail', call, '/py/runtime.c')
    if (not function or function.get('parameters') != ['num_bytes']
            or function.get('parameter_source_contract', {}).get('return_type_expression') != 'void'):
        return None
    events = function['events']
    if (events.get('control_flow_unresolved') or events.get('returns') or events.get('variables')
            or events.get('writes') or any(
                _protocol_shape(event.get('expression_node')) != ('cast', 'void', ('number', 0))
                for event in events.get('expressions', []))):
        return None
    failures, predicates, messages = [], [], []
    for candidate in events.get('calls', []):
        name, _ = call_parts(candidate['expression'])
        operands = _call_operand_nodes(candidate)
        if name in ('mp_raise_msg', 'mp_raise_msg_varg'):
            if (not qualified_template(candidate) or candidate.get('loops')
                    or len(operands) != (3 if name == 'mp_raise_msg_varg' else 2)
                    or _protocol_shape(operands[0]) != ('&', ('identifier', 'mp_type_MemoryError'))
                    or name == 'mp_raise_msg_varg' and _protocol_shape(operands[2]) != ('identifier', 'num_bytes')):
                return None
            message = _unparen(operands[1])
            while message.get('kind') == 'cast_expression' and message.get('type', {}).get('expression') == 'mp_rom_error_text_t':
                message = _unparen(message.get('value'))
            if not _literal_message(message) and message.get('kind') != 'call_expression':
                return None
            failures.append(candidate)
        elif name == 'gc_is_locked':
            declaration = candidate.get('callee') or {}
            if (operands or candidate.get('guards') or candidate.get('loops')
                    or declaration.get('kind') != 'FUNCTION_DECL' or declaration.get('name') != name
                    or declaration.get('source_span', {}).get('source') !=
                       function['source_span']['source'].rsplit('/', 1)[0] + '/gc.h'):
                return None
            predicates.append(candidate)
        else:
            messages.append(candidate)
    closed, reachable, exits = _flow_paths(function, function.get('constants', {}))
    if not closed or reachable or not failures or len(exits) != len(failures) or len(predicates) > 1:
        return None
    conditions = events.get('conditions', [])
    if len(conditions) != len(predicates):
        return None
    if predicates:
        condition = _unparen(conditions[0].get('expression_node'))
        if (condition.get('kind') != 'call_expression' or condition.get('expression') != predicates[0]['expression']
                or condition.get('byte_start') != predicates[0].get('byte_start')
                or condition.get('byte_end') != predicates[0].get('byte_end')):
            return None
    message_authorities = []
    for message in messages:
        owners = [failure for failure in failures
                  if failure['byte_start'] < message['byte_start'] < message['byte_end'] < failure['byte_end']]
        authority = _terminal_literal_message(message, owners[0], definitions) if len(owners) == 1 else None
        if not authority:
            return None
        message_authorities.append(authority)
    for failure in failures:
        payload = _unparen(_call_operand_nodes(failure)[1])
        while payload.get('kind') == 'cast_expression' and payload.get('type', {}).get('expression') == 'mp_rom_error_text_t':
            payload = _unparen(payload.get('value'))
        if not _literal_message(payload) and not any(
                payload.get('byte_start') == message.get('byte_start') and payload.get('byte_end') == message.get('byte_end')
                for message in messages):
            return None
    return {'resolution': 'bounded-resolved', 'kind': 'selected-terminal-allocation-failure',
            'function_id': function['id'], 'source_span': function['source_span'],
            'authority': deepcopy(definitions['m_malloc_fail']['authority']),
            'terminal_errors': exits, 'terminal_message_authority': message_authorities}


def _fresh_allocator_authority(definitions, allocation_call):
    function = _source_body(definitions, 'm_malloc', allocation_call, '/py/malloc.c')
    if not function or function.get('parameters') != ['num_bytes']:
        return None
    events = function['events']
    if events.get('control_flow_unresolved') or len(events.get('returns', [])) != 1 or len(events.get('variables', [])) != 1:
        return None
    returned = events['returns'][0]
    pointer = events['variables'][0]
    if (returned.get('expression') != 'ptr' or returned.get('guards') or returned.get('loops')
            or pointer.get('name') != 'ptr' or pointer.get('declarator_expression') != '* ptr'
            or pointer.get('type_expression') != 'void' or pointer.get('guards') or pointer.get('loops')):
        return None
    calls = events.get('calls', [])
    allocated = [call for call in calls if call_parts(call['expression'])[0] == 'gc_alloc']
    if (len(allocated) != 1 or pointer.get('initializer_node', {}).get('expression') != allocated[0]['expression']
            or [_protocol_shape(node) for node in _call_operand_nodes(allocated[0])] != [('identifier', 'num_bytes'), ('number', 0)]
            or allocated[0].get('guards') or allocated[0].get('loops')):
        return None
    failure_authorities = []
    for call in calls:
        if call is allocated[0]:
            continue
        guard = call.get('guards', [])
        failure = _allocator_failure_authority(definitions, call)
        if (call_parts(call['expression']) != ('m_malloc_fail', ['num_bytes'])
                or not failure or len(guard) != 1 or guard[0]['branch'] != 'then'
                or _protocol_shape(_guard_node(function, guard[0])) !=
                   ('&&', ('==', ('identifier', 'ptr'), ('cast', 'void *', ('number', 0))),
                    ('!=', ('identifier', 'num_bytes'), ('number', 0)))):
            return None
        failure_authorities.append(failure)
    if len(failure_authorities) != 1:
        return None
    for write in events.get('writes', []):
        target = _unparen(write.get('target_node'))
        if (target.get('kind') != 'field_expression' or target.get('operator') != '.'
                or _protocol_shape(target.get('argument')) != ('.', ('identifier', 'mp_state_ctx'), 'mem')
                or target.get('field', {}).get('expression') not in ('total_bytes_allocated', 'current_bytes_allocated', 'peak_bytes_allocated')):
            return None
    gc = _source_body(definitions, 'gc_alloc', allocated[0], '/py/gc.c')
    reservation = _gc_region_authority(gc) if gc else None
    if not reservation:
        return None
    return {'resolution': 'bounded-resolved', 'kind': 'selected-size-forwarding-gc-allocation',
            'function_id': function['id'], 'source_span': function['source_span'],
            'authority': deepcopy(definitions['m_malloc']['authority']),
            'gc_authority': deepcopy(definitions['gc_alloc']['authority']), 'reservation': reservation,
            'failure_authority': failure_authorities}


def checked_receiver_authority(definitions):
    """Certify the selected checked-function dispatch, not a containing class."""
    names = ('checked_fun_call', 'mp_convert_member_lookup', 'mp_obj_new_checked_fun')
    rows = [definitions.get(name) for name in names]
    unresolved = {'resolution': 'unresolved',
                  'unresolved': ['selected checked native receiver dispatch is absent or outside the bounded source template']}
    if any(not row or row.get('authority', {}).get('resolution') not in ('resolved', 'bounded-resolved')
           or not row['function']['source_span']['source'].endswith('/py/runtime.c')
           or row['function']['events'].get('semantic_unassessed')
           or row['function']['events'].get('control_flow_unresolved') for row in rows):
        return unresolved
    checked, dispatch, allocation = [row['function'] for row in rows]
    if checked['parameters'] != ['self_in', 'n_args', 'n_kw', 'args'] or dispatch['parameters'] != ['self', 'type', 'member', 'dest'] or allocation['parameters'] != ['type', 'fun']:
        return unresolved
    checked_type = definitions.get('mp_type_checked_fun') or {}
    type_definition = checked_type.get('definition') or {}
    call_slot = checked_type.get('call_slot_authority') or {}
    slots = [slot for slot in type_definition.get('slots', []) if slot.get('slot') == 'call']
    if (checked_type.get('authority', {}).get('resolution') not in ('resolved', 'bounded-resolved')
            or type_definition.get('name') != 'mp_type_checked_fun' or type_definition.get('kind') != 'type'
            or not type_definition.get('source_span', {}).get('source', '').endswith('/py/runtime.c')
            or len(slots) != 1 or call_slot.get('resolution') not in ('resolved', 'bounded-resolved')
            or slots[0].get('expression') != call_slot.get('expression')
            or call_slot.get('function_id') != checked['id']
            or call_slot.get('declaration_identity') != checked['declaration_identity']):
        unresolved['unresolved'].append('allocated checked-function type call slot is not connected to the certified call body')
        return unresolved
    loader_row = definitions.get('mp_load_method_maybe') or {}
    loader = loader_row.get('function') or {}
    if (loader_row.get('authority', {}).get('resolution') not in ('resolved', 'bounded-resolved')
            or not loader.get('source_span', {}).get('source', '').endswith('/py/runtime.c')
            or loader.get('parameters') != ['obj', 'attr', 'dest']):
        return unresolved
    connected_calls = [call for call in loader.get('events', {}).get('calls', [])
                       if call_parts(call['expression']) == ('mp_convert_member_lookup', ['obj', 'type', 'elem -> value', 'dest'])]
    if len(connected_calls) != 1:
        return unresolved
    connected_call = connected_calls[0]
    if (connected_call.get('callee') or {}).get('kind') != 'FUNCTION_DECL':
        return unresolved
    dispatch_connection = loader_row.get('call_to_definition_authority') or {}
    if connected_call.get('callee') != dispatch['declaration_identity'] and not (
            dispatch_connection.get('resolution') in ('resolved', 'bounded-resolved')
            and dispatch_connection.get('function_id') == dispatch['id']
            and dispatch_connection.get('declaration_identity') == connected_call.get('callee')):
        return unresolved
    selector = [variable for variable in checked['events']['variables']
                if variable['name'] == 'arg0_type'
                and (variable.get('initializer_node') or {}).get('expression') == 'mp_obj_get_type ( args [ 0 ] )']
    get_type = event_call(checked, 'mp_obj_get_type ( args [ 0 ] )')
    failures = [call for call in checked['events']['calls']
                if call_parts(call['expression'])[0] in ('mp_raise_TypeError', 'mp_raise_msg_varg')
                and qualified_template(call)
                and any(guard['condition'] == 'arg0_type != self -> type' and guard['branch'] == 'then' for guard in call.get('guards', []))
                and any(guard['condition'] == 'n_args > 0' and guard['branch'] == 'then' for guard in call.get('guards', []))]
    forwarded = [returned for returned in checked['events']['returns']
                 if returned['expression'] == 'mp_call_function_n_kw ( self -> fun , n_args , n_kw , args )']
    wrapped = [write for write in dispatch['events']['writes']
               if write.get('target') == 'dest [ 0 ]'
               and (write.get('value_node') or {}).get('expression') == 'mp_obj_new_checked_fun ( type , member )']
    if len(selector) != 1 or not qualified_template(get_type) or len(failures) != 1 or len(forwarded) != 1 or len(checked['events']['returns']) != 1 or len(wrapped) != 1:
        return unresolved
    getter_witness = _native_getter_authority(definitions, get_type, rows[0]['authority'].get('contracts', {}))
    if not getter_witness:
        unresolved['unresolved'].append('selected native type getter or object-tag body is outside the qualified A-tag projection')
        return unresolved
    if ([(guard['condition'], guard['branch']) for guard in selector[0].get('guards', [])] != [('n_args > 0', 'then')]
            or [(guard['condition'], guard['branch']) for guard in failures[0].get('guards', [])] !=
               [('n_args > 0', 'then'), ('arg0_type != self -> type', 'then')]
            or forwarded[0].get('guards') or selector[0].get('loops') or failures[0].get('loops')):
        unresolved['unresolved'].append('receiver mismatch failure is conditional beyond the complete positive-argument/type-mismatch guards')
        return unresolved
    message_authorities = []
    for call in checked['events']['calls']:
        if call_parts(call['expression'])[0] in ('mp_obj_get_type', 'mp_raise_TypeError', 'mp_raise_msg_varg', 'mp_call_function_n_kw'):
            continue
        message = _terminal_literal_message(call, failures[0], definitions)
        if message is None:
            return unresolved
        message_authorities.append(message)
    if checked['events']['writes']:
        return unresolved
    if call_parts(failures[0]['expression'])[0] == 'mp_raise_msg_varg' and call_parts(failures[0]['expression'])[1][0] != '& mp_type_TypeError':
        return unresolved
    if failures[0].get('byte_start', -1) >= forwarded[0].get('byte_start', -1):
        return unresolved
    guards = wrapped[0].get('guards', [])
    contracts = rows[1]['authority'].get('contracts', {})
    null = contracts.get('null')
    if (not null or len(guards) != 5 or [guard['branch'] for guard in guards] != ['then', 'then', 'then', 'else', 'then']
            or guards[0]['condition'] != 'mp_obj_is_obj ( member )'
            or not _flag_guard(dispatch, guards[1], 'm_type', contracts.get('binds_self_flag', ''))
            or not _flag_guard(dispatch, guards[2], 'm_type', contracts.get('builtin_fun_flag', ''))
            or guards[4]['condition'] != 'self == ' + null + ' && type != & mp_type_object'
            or wrapped[0].get('loops')):
        return unresolved
    expected_guards = [(guard['condition'], guard['branch']) for guard in guards]
    object_test = event_call(dispatch, 'mp_obj_is_obj ( member )')
    instance_inline = _flag_guard(dispatch, guards[3], 'type', contracts.get('instance_type_flag', ''))
    if not instance_inline:
        unresolved['unresolved'].append('instance-type dispatch is not the configured inline source flag projection')
        return unresolved
    member_type = _local(dispatch, 'm_type', wrapped[0])
    if not qualified_template(object_test) or not member_type:
        return unresolved
    object_witness = _tag_object_authority(definitions, object_test, 'dispatch_mp_obj_is_obj')
    if not object_witness:
        unresolved['unresolved'].append('dispatch object predicate is not connected to the qualified tag body')
        return unresolved
    if ([(guard['condition'], guard['branch']) for guard in member_type.get('guards', [])] != expected_guards[:1]
            or member_type.get('loops')):
        return unresolved
    selector_node = _unparen(member_type.get('initializer_node'))
    if selector_node.get('kind') != 'field_expression' or selector_node.get('field', {}).get('expression') != 'type':
        return unresolved
    pointer_node, pointer_casts = _unparen(selector_node.get('argument')), []
    while pointer_node.get('kind') == 'cast_expression':
        pointer_casts.append(pointer_node.get('type', {}).get('expression'))
        pointer_node = _unparen(pointer_node.get('value'))
    if (pointer_casts != ['mp_obj_base_t *', 'void *'] or pointer_node.get('expression') != 'member'
            or rows[1]['authority'].get('contracts', {}).get('obj_reference') != '( ( mp_obj_t ) ( & mp_type_type ) )'
            or not _unchanged(dispatch, 'm_type', wrapped[0], member_type)
            or any(_local(dispatch, name, wrapped[0]) or not _unchanged(
                dispatch, name, wrapped[0], allow_calls=('mp_obj_is_obj', 'mp_obj_is_instance_type'))
                for name in ('self', 'type', 'member'))):
        return unresolved
    dispatch_call = event_call(dispatch, 'mp_obj_new_checked_fun ( type , member )')
    if not dispatch_call or dispatch_call.get('callee') != allocation['declaration_identity']:
        return unresolved
    assignments = {write['target']: (write.get('value_node') or {}).get('expression')
                   for write in allocation['events']['writes']}
    if assignments != {'o -> type': 'type', 'o -> fun': 'fun'}:
        return unresolved
    if len(allocation['events']['writes']) != 2 or len(allocation['events']['calls']) != 1 or any(write.get('guards') for write in allocation['events']['writes']):
        return unresolved
    allocated = [variable for variable in allocation['events']['variables'] if variable['name'] == 'o' and variable.get('type_expression') == 'mp_obj_checked_fun_t']
    if len(allocated) != 1:
        return unresolved
    node = allocated[0].get('initializer_node') or {}
    while node.get('kind') in ('cast_expression', 'parenthesized_expression'):
        node = node.get('value') or next(iter(node.get('children', [])), {})
    if node.get('expression') != 'mp_obj_malloc_helper ( sizeof ( mp_obj_checked_fun_t ) , & mp_type_checked_fun )':
        return unresolved
    malloc = event_call(allocation, node['expression'])
    if not qualified_template(malloc):
        return unresolved
    allocator = definitions.get('mp_obj_malloc_helper')
    if not allocator or malloc.get('callee') != allocator['function']['declaration_identity']:
        return unresolved
    allocator_graph = {'mp_obj_malloc_helper': [{'function': allocator['function'],
                                               'declaration_identity': malloc['callee'],
                                               'authority': allocator['authority']}]}
    allocator_witness = _allocation_helper_authority(malloc, allocation, {}, allocator_graph)
    if allocator_witness.get('resolution') != 'bounded-resolved':
        return unresolved
    allocator_calls = allocator['function']['events'].get('calls', [])
    fresh = _fresh_allocator_authority(definitions, allocator_calls[0]) if len(allocator_calls) == 1 else None
    if not fresh:
        unresolved['unresolved'].append('selected allocator does not qualify size forwarding, free-block reservation, or pool return')
        return unresolved
    template = rows[2]['authority'].get('contracts', {}).get('obj_reference', '')
    expected_return = template.replace('& mp_type_type', 'o')
    if len(allocation['events']['returns']) != 1 or allocation['events']['returns'][0]['expression'] != expected_return:
        return unresolved
    return {'resolution': 'bounded-resolved', 'kind': 'checked-native-receiver',
            'required_receiver_count': 1, 'excluded_native_type': 'mp_type_object',
            'predicate': 'actual native type of args[0] equals allocated checked function type',
            'allocator_authority': allocator_witness,
            'native_getter_authority': getter_witness, 'object_predicate_authority': object_witness,
            'fresh_region_authority': fresh,
            'dispatch_guard_authority': {'guards': deepcopy(guards), 'configured_flags': {
                name: contracts[name] for name in ('binds_self_flag', 'builtin_fun_flag', 'instance_type_flag') if name in contracts},
                'instance_predicate': 'compiled-source-flag-projection' if instance_inline else 'selected-source-call'},
            'terminal_message_authority': message_authorities,
            'checked_type_connection': {'type_source_span': type_definition['source_span'], 'slot': deepcopy(slots[0]), 'authority': deepcopy(call_slot)},
            'lookup_connection': {'function_id': loader['id'], 'source_span': connected_call['source_span'],
                                  'callee': connected_call['callee'], 'authority': deepcopy(dispatch_connection)},
            'functions': [{'function_id': row['function']['id'], 'source_span': row['function']['source_span'],
                           'authority': row['authority']} for row in rows],
            'check_source_span': failures[0]['source_span'], 'wrapper_source_span': wrapped[0]['source_span'],
            'unresolved': []}


def _flow_paths(function, environment):
    events = function['events']
    calls_by_point = {call.get('byte_start'): call for call in events['calls']}
    reachable, error_exits = set(), []
    def close(node):
        kind = node.get('kind')
        if kind == 'return':
            reachable.add(node['byte_start'])
            return True
        if kind == 'call':
            call = calls_by_point.get(node['byte_start'])
            if _qualified_error_exit(call):
                error_exits.append({'callee': call['callee'], 'source_span': call['source_span'], 'guards': call.get('guards', [])})
                return True
            return False
        if kind == 'block':
            return any(close(child) for child in node.get('children', []))
        if kind == 'if':
            value = _constant_at(function, node['condition'], node, environment)
            if value is not None:
                return close(node['then'] if value else node['else'])
            then = close(node['then'])
            otherwise = close(node['else'])
            return then and otherwise
        if kind == 'loop':
            close(node['body'])
        return False
    return (close(events['flow']) if events.get('flow') else None), reachable, error_exits


def _small_integer_decode(node, parameter, contracts):
    node = _unparen(node)
    if node.get('kind') != 'binary_expression' or node.get('operator') != '>>':
        return False
    shift = constant(_unparen(node.get('right')).get('expression', ''), {})
    left = _unparen(node.get('left'))
    if (not isinstance(shift, int) or not 0 < shift < 8 or left.get('kind') != 'cast_expression'
            or left.get('type', {}).get('expression') != 'mp_int_t'
            or _unparen(left.get('value')).get('expression') != parameter):
        return False
    template = r'\( \( mp_obj_t \) \( \( \( \( mp_uint_t \) \( 7 \) \) << ' + str(shift) + r' \) \| (\d+) \) \)'
    tagged = re.fullmatch(template, contracts.get('rom_int', ''))
    return bool(tagged and 0 < int(tagged[1]) < 1 << shift)


def _checked_integer_body(body, functions, contracts, linked_functions):
    """Qualify selected untagging or checked digit conversion, never an origin alone."""
    events = body['events']
    unresolved = {'resolution': 'unresolved', 'unresolved': ['selected NONE/MPZ checked integer body/operation is outside the bounded source proof']}
    if len(body['parameters']) != 1 or events.get('semantic_unassessed') or events.get('control_flow_unresolved'):
        return unresolved
    parameter = body['parameters'][0]
    returns = events['returns']
    if body['source_span']['source'].endswith('/py/objint.c'):
        if (len(returns) != 1 or events['calls'] or events['writes'] or events['variables'] or events['conditions']
                or returns[0].get('guards') or not _small_integer_decode(returns[0].get('expression_node'), parameter, contracts)):
            return unresolved
        return {'resolution': 'bounded-resolved', 'kind': 'compiler-tagged-small-integer-decoding',
                'source_span': body['source_span'], 'compiler_constructor_contract': contracts['rom_int']}
    if not body['source_span']['source'].endswith('/py/objint_mpz.c') or parameter != 'self_in' or events['writes']:
        return unresolved
    locals_by_name = {variable['name']: variable for variable in events['variables']}
    self_node = _unparen(locals_by_name.get('self', {}).get('initializer_node'))
    if (set(locals_by_name) != {'self', 'value'} or locals_by_name['self'].get('type_expression') != 'mp_obj_int_t'
            or contracts.get('obj_reference') != '( ( mp_obj_t ) ( & mp_type_type ) )'
            or self_node.get('kind') != 'cast_expression' or self_node.get('type', {}).get('expression') != 'void *'
            or _unparen(self_node.get('value')).get('expression') != parameter
            or [(guard['condition'], guard['branch']) for guard in locals_by_name['self'].get('guards', [])] != [('mp_obj_is_small_int ( self_in )', 'else')]):
        return unresolved
    small = [returned for returned in returns if _small_integer_decode(returned.get('expression_node'), parameter, contracts)]
    values = [returned for returned in returns if returned['expression'] == 'value']
    operations = [call for call in events['calls'] if call_parts(call['expression']) == ('mpz_as_int_checked', ['& self -> mpz', '& value'])]
    overflow = [call for call in events['calls'] if qualified_template(call) and
                (call_parts(call['expression'])[0] == 'mp_raise_OverflowError' or
                 call_parts(call['expression'])[0] == 'mp_raise_msg' and call_parts(call['expression'])[1][:1] == ['& mp_type_OverflowError'])]
    if len(returns) != 2 or len(small) != 1 or len(values) != 1 or len(operations) != 1 or len(overflow) != 1:
        return unresolved
    if ([(guard['condition'], guard['branch']) for guard in small[0].get('guards', [])] != [('mp_obj_is_small_int ( self_in )', 'then')]
            or [(guard['condition'], guard['branch']) for guard in values[0].get('guards', [])] !=
               [('mp_obj_is_small_int ( self_in )', 'else'), (operations[0]['expression'], 'then')]
            or [(guard['condition'], guard['branch']) for guard in overflow[0].get('guards', [])] !=
               [('mp_obj_is_small_int ( self_in )', 'else'), (operations[0]['expression'], 'else')]):
        return unresolved
    if any(call_parts(call['expression'])[0] not in ('mp_obj_is_small_int', 'mpz_as_int_checked', 'mp_raise_OverflowError', 'mp_raise_msg')
           or not qualified_template(call) for call in events['calls']):
        return unresolved
    candidates = _selected_helpers(operations[0], 'mpz_as_int_checked', functions, linked_functions, body)
    if len(candidates) != 1:
        unresolved['unresolved'].append('native checked MPZ operation lacks independently selected definition authority')
        return unresolved
    operation, authority = candidates[0]
    native = operation['events']
    if (not operation['source_span']['source'].endswith('/py/mpz.c') or operation['parameters'] != ['i', 'value']
            or native['calls'] or native.get('semantic_unassessed') or native.get('control_flow_unresolved')):
        return unresolved
    initializers = {variable['name']: (variable.get('initializer_node') or {}).get('expression') for variable in native['variables']}
    if initializers != {'val': '0', 'd': 'i -> dig + i -> len'} or not all(_automatic_definition(variable) for variable in native['variables']):
        return unresolved
    writes = native['writes']
    if len(writes) != 4 or [write.get('target') for write in writes] != ['d', 'val', 'val', '* value']:
        return unresolved
    fold = _unparen(writes[1].get('value_node'))
    shifted = _unparen(fold.get('left'))
    digit_bits = _constant_at(operation, _unparen(shifted.get('right')).get('expression', ''), writes[1])
    if (writes[0].get('operator') != '--' or fold.get('operator') != '|' or shifted.get('operator') != '<<'
            or _unparen(shifted.get('left')).get('expression') != 'val'
            or _unparen(fold.get('right')).get('expression') != '* d'
            or not isinstance(digit_bits, int) or not 4 <= digit_bits <= 32
            or (writes[2].get('value_node') or {}).get('expression') != '- val'
            or (writes[3].get('value_node') or {}).get('expression') != 'val' or writes[3].get('guards') or writes[3].get('loops')):
        return unresolved
    def loop_nodes(node):
        if node.get('kind') == 'loop':
            yield node
        for value in node.values():
            if isinstance(value, dict):
                yield from loop_nodes(value)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, dict):
                        yield from loop_nodes(child)
    loops = list(loop_nodes(native.get('flow', {})))
    if (len(loops) != 1 or loops[0].get('loop_kind') != 'while_statement'
            or strip_outer(loops[0].get('condition_expression', '')) != 'd > i -> dig'
            or any(write.get('loops') != [loops[0]['byte_start']] for write in writes[:2])
            or [(guard['condition'], guard['branch']) for guard in writes[2].get('guards', [])] != [('i -> neg != 0', 'then')]
            or writes[2].get('loops')):
        return unresolved
    failures = [returned for returned in native['returns'] if _constant_at(operation, returned['expression'], returned) == 0]
    successes = [returned for returned in native['returns'] if _constant_at(operation, returned['expression'], returned) == 1]
    if len(failures) != 1 or len(successes) != 1 or successes[0].get('guards') or successes[0].get('loops') or writes[3]['byte_start'] >= successes[0]['byte_start']:
        return unresolved
    conditions = native['conditions']
    bounds = [condition for condition in conditions if _unparen(condition.get('expression_node')).get('operator') == '>']
    if (len(bounds) != 1 or _unparen(bounds[0]['expression_node']).get('left', {}).get('expression') != 'val'
            or failures[0].get('loops') != [loops[0]['byte_start']]
            or len(failures[0].get('guards', [])) != 1
            or failures[0]['guards'][0].get('condition_id') != bounds[0].get('condition_id')
            or failures[0]['guards'][0]['condition'] != bounds[0]['expression']
            or failures[0]['guards'][0]['branch'] != 'then'):
        return unresolved
    for write in writes[:2]:
        guards = write.get('guards', [])
        if (len(guards) != 1 or guards[0].get('condition_id') != bounds[0].get('condition_id')
                or guards[0]['condition'] != bounds[0]['expression'] or guards[0]['branch'] != 'else'):
            return unresolved
    bound = _unparen(_unparen(bounds[0]['expression_node']).get('right'))
    if bound.get('operator') != '>>' or _constant_at(operation, _unparen(bound.get('right')).get('expression', ''), bounds[0]) != digit_bits or _unparen(bound.get('left')).get('operator') != '~':
        return unresolved
    high_bit = _unparen(_unparen(bound.get('left')).get('argument'))
    high_one = _unparen(high_bit.get('left'))
    bit_index = _unparen(high_bit.get('right'))
    word_bits = _unparen(bit_index.get('left'))
    word_bytes = _unparen(word_bits.get('left'))
    if (high_bit.get('operator') != '<<' or high_one.get('kind') != 'cast_expression'
            or high_one.get('type', {}).get('expression') != 'mp_uint_t'
            or constant(_unparen(high_one.get('value')).get('expression', ''), {}) != 1
            or bit_index.get('operator') != '-' or constant(_unparen(bit_index.get('right')).get('expression', ''), {}) != 1
            or word_bits.get('operator') != '*' or constant(_unparen(word_bits.get('right')).get('expression', ''), {}) != 8
            or word_bytes.get('expression') not in ('sizeof ( mp_obj_t )', 'sizeof ( mp_uint_t )')):
        unresolved['unresolved'].append('native MPZ overflow bound does not derive the source object/native word high bit')
        return unresolved
    return {'resolution': 'bounded-resolved', 'kind': 'selected-checked-MPZ-digit-conversion',
            'source_span': body['source_span'], 'operation': {'function_id': operation['id'], 'source_span': operation['source_span'],
            'authority': authority, 'overflow_condition': bounds[0]['expression'], 'digit_bits': digit_bits}}


def input_helper_authority(call, function, functions, contracts, linked_functions=None):
    """Qualify bounded supplied-object domains against selected helper bodies."""
    helper = call_parts(call['expression'])[0]
    unresolved = {'resolution': 'unresolved', 'kind': INPUT_HELPERS.get(helper),
                  'unresolved': ['selected runtime helper body does not establish the bounded accepted domain']}
    witnesses = []
    unresolved['functions'] = witnesses
    def selected(target_call, caller):
        name = call_parts(target_call['expression'])[0]
        candidates = _selected_helpers(target_call, name, functions, linked_functions, caller)
        if len(candidates) != 1:
            return None
        body, authority = candidates[0]
        if body['events'].get('semantic_unassessed') or body['events'].get('control_flow_unresolved'):
            return None
        witnesses.append({'function_id': body['id'], 'source_span': body['source_span'], 'authority': authority})
        return body
    body = selected(call, function)
    if not body:
        return unresolved
    def calls(body, name):
        return [event for event in body['events']['calls'] if call_parts(event['expression'])[0] == name]
    def native_calls(body, slot, arguments):
        return [event for event in body['events']['calls']
                if slot in (event.get('expression_node') or {}).get('function', {}).get('expression', '')
                and [node['expression'] for node in (event.get('expression_node') or {}).get('arguments', {}).get('children', [])][:len(arguments)] == arguments]
    if helper == 'mp_obj_is_true':
        if not body['source_span']['source'].endswith('/py/obj.c') or body['parameters'] != ['arg'] or body['events']['writes']:
            return unresolved
        if any(variable['name'] == 'MP_UNARY_OP_BOOL' for variable in body['events']['variables']):
            return unresolved
        closed, reachable, _ = _flow_paths(body, body['constants'])
        expected = {'0', '1', 'result == ' + contracts.get('true', ''),
                    'len != ' + contracts.get('rom_int', '').replace('7', '0')}
        if not closed or any(returned['expression'] not in expected for returned in body['events']['returns'] if returned.get('byte_start') in reachable):
            return unresolved
        predicates = {condition['expression'] for condition in body['events']['conditions']}
        if not all('arg == ' + contracts.get(name, '') in predicates for name in ('false', 'true', 'none')):
            return unresolved
        boolean_slots = native_calls(body, 'slot_index_unary_op', ['MP_UNARY_OP_BOOL', 'arg'])
        lengths = calls(body, 'mp_obj_len_maybe')
        fallback = [returned for returned in body['events']['returns'] if returned['expression'] == '1'
                    and any(guard['condition'] == 'len != ' + contracts.get('null', '') and guard['branch'] == 'else' for guard in returned.get('guards', []))]
        if len(boolean_slots) != 1 or len(lengths) != 1 or not qualified_template(lengths[0]) or call_parts(lengths[0]['expression'])[1] != ['arg'] or not fallback:
            return unresolved
        kind = 'object'
    elif helper == 'mp_obj_get_int':
        if not body['source_span']['source'].endswith('/py/obj.c') or body['parameters'] != ['arg']:
            return unresolved
        if any(re.search(r'\barg\b', write.get('target', '')) for write in body['events']['writes']):
            return unresolved
        maybe_calls = calls(body, 'mp_obj_get_int_maybe')
        if len(maybe_calls) != 1 or call_parts(maybe_calls[0]['expression'])[1] != ['arg', '& val']:
            return unresolved
        maybe = selected(maybe_calls[0], body)
        if not maybe or not maybe['source_span']['source'].endswith('/py/obj.c') or maybe['parameters'] != ['arg', 'value']:
            return unresolved
        if any(variable['name'] == 'MP_UNARY_OP_INT_MAYBE' for variable in maybe['events']['variables']):
            return unresolved
        argument_writes = [write for write in maybe['events']['writes'] if re.search(r'\barg\b', write.get('target', ''))]
        operations = calls(maybe, 'mp_unary_op')
        checked = calls(maybe, 'mp_obj_int_get_checked')
        if len(operations) != 1 or not qualified_template(operations[0]) or call_parts(operations[0]['expression'])[1] != ['MP_UNARY_OP_INT_MAYBE', '( mp_obj_t ) arg'] or len(checked) != 2:
            return unresolved
        if len(argument_writes) > 1 or any(write.get('target') != 'arg' or write.get('operator') != '='
                                        or (write.get('value_node') or {}).get('expression') != operations[0]['expression']
                                        for write in argument_writes):
            return unresolved
        if any(call_parts(event['expression'])[0] not in ('mp_obj_get_type', 'mp_obj_is_obj', 'mp_obj_is_small_int', 'mp_unary_op', 'mp_obj_int_get_checked')
               or not qualified_template(event) for event in maybe['events']['calls']):
            return unresolved
        false_returns = [returned for returned in maybe['events']['returns']
                         if _constant_at(maybe, returned['expression'], returned) == 0]
        if (len(false_returns) != 1 or not any(guard['condition'] == 'arg != ' + contracts.get('null', '')
                                              and guard['branch'] == 'else' for guard in false_returns[0].get('guards', []))
                or any(_constant_at(maybe, returned['expression'], returned) not in (0, 1) for returned in maybe['events']['returns'])):
            return unresolved
        integer_body = selected(checked[0], maybe)
        if not integer_body or not integer_body['source_span']['source'].endswith(('/py/objint.c', '/py/objint_mpz.c')):
            unresolved['unresolved'].append('selected integer implementation is outside qualified NONE/MPZ body scope')
            return unresolved
        second_integer_body = selected(checked[1], maybe)
        if not second_integer_body or second_integer_body['id'] != integer_body['id']:
            return unresolved
        backend = _checked_integer_body(integer_body, functions, witnesses[-1]['authority'].get('contracts', contracts), linked_functions)
        if backend['resolution'] != 'bounded-resolved':
            unresolved['unresolved'].extend(backend['unresolved'])
            return unresolved
        witnesses[-1]['body_contract'] = backend
        if not any('& mp_type_int' in condition['expression'] for condition in maybe['events']['conditions']):
            return unresolved
        errors = calls(body, 'mp_raise_TypeError_int_conversion')
        if len(errors) != 1 or len(body['events']['returns']) != 1 or body['events']['returns'][0]['expression'] != 'val':
            return unresolved
        if not any(guard['condition'] == '! ' + maybe_calls[0]['expression'] and guard['branch'] == 'then'
                   for guard in errors[0].get('guards', [])):
            return unresolved
        error_body = selected(errors[0], body)
        if not error_body:
            return unresolved
        error_closed, error_returns, exits = _flow_paths(error_body, error_body['constants'])
        if not error_closed or error_returns or not exits:
            return unresolved
        kind = 'integer-conversion-capability'
    elif helper == 'mp_get_buffer_raise':
        if not body['source_span']['source'].endswith('/py/obj.h') or body['parameters'] != ['obj', 'bufinfo', 'flags']:
            return unresolved
        if body['events']['writes'] or len(body['events']['calls']) != 1:
            return unresolved
        buffer_calls = calls(body, 'mp_get_buffer')
        if len(buffer_calls) != 1:
            return unresolved
        operands = call_parts(buffer_calls[0]['expression'])[1]
        if len(operands) != 3 or operands[:2] != ['obj', 'bufinfo'] or not operands[2].startswith('flags | '):
            return unresolved
        raise_flag = _constant_at(body, operands[2][len('flags | '):], buffer_calls[0])
        buffer = selected(buffer_calls[0], body)
        if not buffer or not buffer['source_span']['source'].endswith('/py/obj.c') or buffer['parameters'] != ['obj', 'bufinfo', 'flags']:
            return unresolved
        slots = native_calls(buffer, 'slot_index_buffer', ['obj', 'bufinfo'])
        if len(slots) != 1:
            return unresolved
        modes = (slots[0].get('expression_node') or {}).get('arguments', {}).get('children', [])
        mode = modes[2]['expression'] if len(modes) == 3 else ''
        read, write = constant(contracts.get('buffer_read', ''), {}), constant(contracts.get('buffer_write', ''), {})
        if not mode.startswith('flags & ') or read is None or write is None or _constant_at(buffer, mode[len('flags & '):], slots[0]) != read | write:
            return unresolved
        errors = calls(buffer, 'mp_raise_TypeError')
        guarded_raise = any(any(guard['branch'] == 'then' and guard['condition'].startswith('flags & ')
                                and _constant_at(buffer, guard['condition'][len('flags & '):], {'byte_start': guard.get('condition_id')}) == raise_flag
                                for guard in error.get('guards', [])) and qualified_template(error) for error in errors)
        success = any(slots[0]['expression'] + ' == 0' in condition['expression'] for condition in buffer['events']['conditions'])
        successful_returns = [returned for returned in buffer['events']['returns']
                              if _constant_at(buffer, returned['expression'], returned) == 1]
        if (raise_flag is None or raise_flag == 0 or read == 0 or write == 0 or read & write
                or raise_flag & (read | write) or not guarded_raise or not success or not successful_returns
                or any(not any(guard['branch'] == 'then' and slots[0]['expression'] + ' == 0' in guard['condition']
                               for guard in returned.get('guards', [])) for returned in successful_returns)
                or any(_constant_at(buffer, returned['expression'], returned) not in (0, 1)
                       for returned in buffer['events']['returns'])):
            return unresolved
        kind = 'native-buffer-capability'
    elif helper == 'mp_obj_get_array_fixed_n':
        if not body['source_span']['source'].endswith('/py/obj.c') or body['parameters'] != ['o', 'len', 'items'] or body['events']['writes'] or body['events']['returns']:
            return unresolved
        arrays = calls(body, 'mp_obj_get_array')
        if len(arrays) != 1 or call_parts(arrays[0]['expression'])[1] != ['o', '& seq_len', 'items']:
            return unresolved
        array = selected(arrays[0], body)
        if not array or not array['source_span']['source'].endswith('/py/obj.c') or array['parameters'] != ['o', 'len', 'items'] or array['events']['writes'] or array['events']['returns']:
            return unresolved
        if len(body['events']['calls']) != 2:
            return unresolved
        body_contracts = witnesses[-1]['authority'].get('contracts', contracts)
        def type_selector(condition):
            def unwrap(node):
                while node and node.get('kind') == 'parenthesized_expression':
                    node = next(iter(node.get('children', [])), {})
                return node or {}
            def conjuncts(node):
                node = unwrap(node)
                if node.get('kind') == 'binary_expression' and node.get('operator') == '&&':
                    return conjuncts(node.get('left')) + conjuncts(node.get('right'))
                return [node]
            leaves = conjuncts(condition.get('expression_node'))
            comparisons = [node for node in leaves if node.get('kind') == 'binary_expression' and node.get('operator') == '==']
            native_tests = []
            for node in comparisons:
                left, right = unwrap(node.get('left')), unwrap(node.get('right'))
                if right.get('kind') != 'pointer_expression' or right.get('expression') not in ('& mp_type_tuple', '& mp_type_list'):
                    continue
                selected_call = event_call(array, left.get('expression', ''))
                getter = selected_call and qualified_template(selected_call) and call_parts(left.get('expression', '')) == ('mp_obj_get_type', ['o'])
                if not getter:
                    if body_contracts.get('obj_reference') != '( ( mp_obj_t ) ( & mp_type_type ) )' or left.get('kind') != 'field_expression' or left.get('field', {}).get('expression') != 'type':
                        continue
                    base, casts = unwrap(left.get('argument')), []
                    while base.get('kind') == 'cast_expression':
                        casts.append(base.get('type', {}).get('expression'))
                        base = unwrap(base.get('value'))
                    if base.get('expression') != 'o' or casts != ['mp_obj_base_t *', 'void *']:
                        continue
                    object_calls = [event_call(array, leaf.get('expression', '')) for leaf in leaves if call_parts(leaf.get('expression', '')) == ('mp_obj_is_obj', ['o'])]
                    if len(object_calls) != 1 or not qualified_template(object_calls[0]):
                        continue
                if any(variable['name'] == right['argument']['expression'] for variable in array['events']['variables']):
                    continue
                native_tests.append((node, right['argument']['expression'].removeprefix('mp_type_')))
            if len(native_tests) != 1:
                return None
            selected_node, native = native_tests[0]
            if any(leaf is not selected_node and re.search(r'\bo\b', leaf.get('expression', ''))
                   and call_parts(leaf.get('expression', '')) != ('mp_obj_is_obj', ['o']) for leaf in leaves):
                return None
            return native
        selectors = {type_selector(condition): condition for condition in array['events']['conditions']}
        if set(selectors) != {'tuple', 'list'}:
            unresolved['unresolved'].append('configured tuple/list type predicate has unsupported assertion/cast/tag projection')
            return unresolved
        for native in ('tuple', 'list'):
            getter = calls(array, 'mp_obj_' + native + '_get')
            if len(getter) != 1 or not qualified_template(getter[0]) or call_parts(getter[0]['expression'])[1] != ['o', 'len', 'items']:
                return unresolved
            if not any(guard.get('condition_id') == selectors[native].get('condition_id') and guard['branch'] == 'then' for guard in getter[0].get('guards', [])):
                return unresolved
        allowed_calls = {'mp_obj_get_type', 'mp_obj_is_obj', 'mp_obj_tuple_get', 'mp_obj_list_get', 'mp_obj_get_type_str',
                         'mp_raise_TypeError', 'mp_raise_msg', 'mp_raise_msg_varg'}
        if any(call_parts(event['expression'])[0] not in allowed_calls or not qualified_template(event) for event in array['events']['calls']):
            return unresolved
        def errors_for(target, native):
            return [event for event in target['events']['calls'] if qualified_template(event) and
                    (call_parts(event['expression'])[0] == 'mp_raise_' + native
                     or call_parts(event['expression'])[0] in ('mp_raise_msg', 'mp_raise_msg_varg')
                     and call_parts(event['expression'])[1][:1] == ['& mp_type_' + native])]
        length_errors, type_errors = errors_for(body, 'ValueError'), errors_for(array, 'TypeError')
        if len(length_errors) != 1 or not any(guard['condition'] == 'seq_len != len' and guard['branch'] == 'then' for guard in length_errors[0].get('guards', [])):
            return unresolved
        if len(type_errors) != 1 or not all(any(guard.get('condition_id') == condition.get('condition_id') and guard['branch'] == 'else' for guard in type_errors[0].get('guards', [])) for condition in selectors.values()):
            return unresolved
        kind = 'tuple-or-list/exact-length'
    else:
        return unresolved
    return {'resolution': 'bounded-resolved', 'kind': kind, 'functions': witnesses,
            'authority': 'selected compiler-expanded native helper operation and successful/error branches',
            'unresolved': [], **({'range_failure': True, 'conversion_operation': 'MP_UNARY_OP_INT_MAYBE'} if kind == 'integer-conversion-capability' else {})}


def _qualify_builder(contract, function, functions, linked_functions, contracts):
    if 'elements' in contract:
        contract['elements'] = [_qualify_builder(element, function, functions, linked_functions, contracts)
                                for element in contract['elements']]
        if contract.get('kind') == 'list':
            unresolved = [reason for element in contract['elements'] for reason in
                          (element.get('family_unresolved', []) if element.get('family_resolution') == 'unresolved'
                           else [element.get('reason', 'element relation unresolved')] if element.get('kind') == 'unresolved' else [])]
            if unresolved:
                contract['element_resolution'] = 'unresolved'
                contract['element_unresolved'] = sorted(set(contract.get('element_unresolved', []) + unresolved))
    if contract.get('kind') != 'same-native-family':
        return contract
    contract['family_resolution'] = 'unresolved'
    contract['family_unresolved'] = ['selected configured builder successful exits are not qualified']
    call = event_call(function, contract['evidence'])
    candidates = _selected_helpers(call, contract['builder'], functions, linked_functions, function)
    if len(candidates) != 1:
        return contract
    body, authority = candidates[0]
    if not body['source_span']['source'].endswith('/py/objstr.c') or body['parameters'] not in (['type', 'data', 'len'], ['type', 'vstr']):
        return contract
    if body['events'].get('semantic_unassessed') or body['events'].get('control_flow_unresolved'):
        return contract
    if (any(variable['name'] == 'type' for variable in body['events']['variables'])
            or any(re.search(r'\btype\b', write.get('target', '')) for write in body['events']['writes'])
            or any(_address_mentions(variable.get('initializer_node'), 'type') for variable in body['events']['variables'])
            or any(_address_mentions(node, 'type') or _mentions(node, 'type') and
                   call_parts(event['expression'])[0] not in ('mp_obj_malloc_helper', 'mp_obj_new_str', 'mp_obj_new_bytes', 'mp_obj_new_bytearray')
                   for event in body['events']['calls'] for node in _call_operand_nodes(event))):
        contract['family_unresolved'].append('native builder selector is shadowed, written, addressed or consumed by an unassessed helper')
        return contract
    body_contracts = authority.get('contracts', contracts)
    closed, reachable, _ = _flow_paths(body, body['constants'])
    if not closed:
        return contract
    mapping, allocation, result_paths = {}, [], []
    for returned in body['events']['returns']:
        if returned.get('byte_start') not in reachable:
            continue
        value = return_contract(returned['expression'], body, body_contracts, returned['source_span'], returned)
        if value['kind'] in ('str', 'bytes', 'bytearray'):
            result_paths.append((returned.get('guards', []), value['kind']))
            continue
        template = body_contracts.get('obj_reference', '')
        if not template or returned['expression'] != template.replace('& mp_type_type', 'o'):
            return contract
        local = _local(body, 'o', returned)
        if not local or local.get('type_expression') != 'mp_obj_str_t':
            return contract
        node = local.get('initializer_node') or {}
        while node.get('kind') in ('cast_expression', 'parenthesized_expression'):
            node = node.get('value') or next(iter(node.get('children', [])), {})
        malloc = event_call(body, node.get('expression'), local['source_span'])
        if node.get('expression') != 'mp_obj_malloc_helper ( sizeof ( mp_obj_str_t ) , type )' or not qualified_template(malloc):
            return contract
        allocator_witness = _allocation_helper_authority(malloc, body, functions, linked_functions)
        if allocator_witness.get('resolution') != 'bounded-resolved':
            return contract
        if not _storage_unchanged(body, 'o', returned, local, protected_fields=('base', 'type')):
            return contract
        result_paths.append((returned.get('guards', []), 'same-native-type'))
        allocation.append({'source_span': local['source_span'], 'callee': malloc['callee'],
                           'allocation_expression': local['initializer_node']['expression'], 'type_parameter': 0,
                           'allocator_authority': allocator_witness})
    for native in ('str', 'bytes', 'bytearray'):
        possible = set()
        for guards, result in result_paths:
            eligible = True
            for guard in guards:
                selector = re.fullmatch(r'type (==|!=) & mp_type_(str|bytes|bytearray)', guard['condition'])
                if selector:
                    matches = native == selector[2]
                    selected = matches if selector[1] == '==' else not matches
                    if selected != (guard['branch'] == 'then'):
                        eligible = False
                        break
            if eligible:
                possible.add(native if result == 'same-native-type' else result)
        if len(possible) != 1:
            contract['family_unresolved'].append('native selector has incomplete or conflicting possible successful result families')
            return contract
        mapping[native] = possible.pop()
    if mapping['str'] != 'str':
        return contract
    contract.update(family_resolution='bounded-resolved', family_unresolved=[],
                    native_family_mapping=mapping,
                    builder_authority={'function_id': body['id'], 'source_span': body['source_span'],
                                       'authority': authority, 'typed_allocations': allocation,
                                       'successful_return_points': sorted(reachable)})
    return contract


def _allocation_helper_authority(call, function, functions, linked_functions):
    candidates = _selected_helpers(call, 'mp_obj_malloc_helper', functions, linked_functions, function)
    unresolved = {'resolution': 'unresolved', 'unresolved': ['selected allocator type-storage body not qualified']}
    if len(candidates) != 1:
        return unresolved
    body, authority = candidates[0]
    events = body['events']
    if not body['source_span']['source'].endswith('/py/obj.c') or body['parameters'] != ['num_bytes', 'type'] or events.get('semantic_unassessed') or events.get('control_flow_unresolved'):
        return unresolved
    base = [variable for variable in events['variables'] if variable['name'] == 'base' and variable.get('type_expression') == 'mp_obj_base_t']
    if len(base) != 1 or len(events['writes']) != 1 or len(events['returns']) != 1:
        return unresolved
    write, returned = events['writes'][0], events['returns'][0]
    if write.get('target') != 'base -> type' or (write.get('value_node') or {}).get('expression') != 'type' or returned['expression'] != 'base' or write['byte_start'] >= returned['byte_start']:
        return unresolved
    closed, reachable, _ = _flow_paths(body, body['constants'])
    if write.get('guards') or returned.get('guards') or base[0].get('guards') or write.get('loops') or returned.get('loops') or base[0].get('loops') or not closed or returned.get('byte_start') not in reachable:
        return unresolved
    node = base[0].get('initializer_node') or {}
    while node.get('kind') in ('cast_expression', 'parenthesized_expression'):
        node = node.get('value') or next(iter(node.get('children', [])), {})
    malloc = event_call(body, node.get('expression'))
    if node.get('expression') != 'm_malloc ( num_bytes )' or not malloc or (malloc.get('callee') or {}).get('kind') != 'FUNCTION_DECL' or not malloc['callee']['source_span']['source'].endswith('/py/misc.h') or len(events['calls']) != 1:
        return unresolved
    return {'resolution': 'bounded-resolved', 'function_id': body['id'], 'source_span': body['source_span'],
            'authority': authority, 'type_storage_write': write['source_span'],
            'allocation_callee': malloc['callee'], 'unresolved': []}
