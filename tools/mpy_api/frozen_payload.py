"""Bounded compiler-expanded frozen name and source-byte extraction."""
import hashlib
from pathlib import Path
import re
from typing import Callable

from .frontend import canonical


_NAMES = 'mp_frozen_names'
_CONTENT = 'mp_frozen_str_content'
_SIZES = 'mp_frozen_str_sizes'
_ESCAPES = {'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9,
            'v': 11, '\\': 92, '"': 34, "'": 39, '?': 63}


def _body(expression: str) -> str:
    text = expression.strip().removesuffix(';').strip()
    if text.startswith('{') and text.endswith('}'):
        text = text[1:-1].strip()
        if text.endswith(','):
            text = text[:-1].rstrip()
    return text


def _strings(expression: str) -> bytes:
    """Decode only adjacent ordinary C byte string literals, including their final NUL."""
    text = _body(expression)
    result = bytearray()
    index = 0
    count = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text[index] != '"':
            raise ValueError('initializer is not adjacent ordinary C byte string literals')
        count += 1
        index += 1
        while index < len(text) and text[index] != '"':
            char = text[index]
            index += 1
            if char != '\\':
                if not 32 <= ord(char) <= 126:
                    raise ValueError('literal execution character encoding is not qualified ASCII')
                result.append(ord(char))
                continue
            if index == len(text):
                raise ValueError('incomplete C escape')
            escape = text[index]
            index += 1
            if escape in _ESCAPES:
                result.append(_ESCAPES[escape])
            elif escape in '01234567':
                digits = escape
                while index < len(text) and len(digits) < 3 and text[index] in '01234567':
                    digits += text[index]
                    index += 1
                value = int(digits, 8)
                if value > 255:
                    raise ValueError('octal escape exceeds byte range')
                result.append(value)
            elif escape == 'x':
                start = index
                while index < len(text) and text[index] in '0123456789abcdefABCDEF':
                    index += 1
                if start == index:
                    raise ValueError('empty C hexadecimal escape')
                value = int(text[start:index], 16)
                if value > 255:
                    raise ValueError('hexadecimal escape exceeds byte range')
                result.append(value)
            else:
                raise ValueError('unsupported C escape: ' + escape)
        if index == len(text):
            raise ValueError('unterminated C string literal')
        index += 1
    if not count:
        raise ValueError('empty string initializer')
    return bytes(result) + b'\0'


def _sizes(expression: str) -> list[int]:
    text = expression.strip().removesuffix(';').strip()
    if not (text.startswith('{') and text.endswith('}')):
        raise ValueError('size initializer is not a direct integer array')
    fields = text[1:-1].split(',')
    if fields[-1].strip() == '':
        fields.pop()
    values = []
    for field in fields:
        token = field.strip()
        match = re.fullmatch(r'(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)([uU](?:[lL]{1,2})?|[lL]{1,2}[uU]?)?', token)
        if not match:
            raise ValueError('size initializer contains a nonliteral integer')
        literal = match[1]
        base = 16 if literal.lower().startswith('0x') else 8 if literal.startswith('0') else 10
        value = int(literal, base)
        if value > 0xffffffff:
            raise ValueError('size exceeds uint32_t range')
        values.append(value)
    if not values or values[-1] != 0 or 0 in values[:-1]:
        raise ValueError('size table requires positive lengths followed by one zero terminator')
    return values[:-1]


def _array(definition: dict, c_type: str) -> None:
    declaration = definition.get('declaration_expression', '')
    prefix = declaration.split('=', 1)[0]
    pattern = (r'\s*(?:static\s+)?const\s+' + re.escape(c_type) + r'\s+'
               + re.escape(definition['name']) + r'\s*\[\s*\]\s*')
    if not re.fullmatch(pattern, prefix):
        raise ValueError('exact unsized const ' + c_type + ' array declaration is unqualified')
    if definition.get('errors'):
        raise ValueError('frontend errors overlap frozen definition')


def _source(definition: dict) -> dict:
    return {'identity': definition.get('id'), 'source_span': definition.get('source_span'),
            'authority': 'selected compiler-expanded initializer'}


def _loader(inputs: list[dict], present: Callable, store) -> dict:
    candidates = [(facts, function) for facts in inputs for function in facts.get('functions', [])
                  if function.get('name') == 'mp_find_frozen_module' and present(facts, function)]
    if len(candidates) != 1:
        raise ValueError('selected allocated frozen import loader ownership is missing or ambiguous')
    facts, function = candidates[0]
    function = store.function(function)
    if function.get('events', {}).get('semantic_unassessed'):
        raise ValueError('selected frozen loader body has unassessed GNU expression semantics')
    path = function.get('source_span', {}).get('source')
    expected = facts.get('input', {}).get('dependencies', {}).get(path)
    if not path or not expected:
        raise ValueError('frozen loader has no selected dependency identity')
    text = function.get('body_expression', '')
    if not text:
        raise ValueError('frozen loader has no selected compiler-expanded function body')
    # The selected body, including feature-selected branches, bounds this family.
    required = ('const char *name = mp_frozen_names;', 'size_t entry_len = strlen(name);',
                'for (size_t i = 0; *name != 0; i++)',
                'entry_len >= len && memcmp(str, name, len) == 0',
                'entry_len == len', "name[len] == '/'", 'name += entry_len + 1;',
                'return MP_IMPORT_STAT_FILE;', 'return MP_IMPORT_STAT_DIR;')
    if not all(canonical(fragment) in text for fragment in required):
        raise ValueError('selected compiler-expanded frozen name lookup contract is unsupported')
    str_required = ('size_t num_str = 0;',
                    'for (const uint32_t *s = mp_frozen_str_sizes; *s != 0; ++s) { ++num_str; }',
                    'if (i < num_str)', '*frozen_type = MP_FROZEN_STR;',
                    'for (size_t j = 0; j < i; ++j) { offset += mp_frozen_str_sizes[j] + 1; }',
                    'size_t content_len = mp_frozen_str_sizes[i];',
                    'const char *content = &mp_frozen_str_content[offset];')
    str_resolved = all(canonical(fragment) in text for fragment in str_required)
    mpy_required = ('size_t num_str = 0;',
                    'if (i >= num_str) { *frozen_type = MP_FROZEN_MPY; '
                    '*data = (void *)mp_frozen_mpy_content[i - num_str]; }')
    mpy_resolved = all(canonical(fragment) in text for fragment in mpy_required)
    return {'source': path, 'sha256': expected, 'identity': function.get('id'),
            'translation_unit': facts['translation_unit'],
            'body_authority': 'selected compiler-expanded allocated function',
            'body_expression': text, 'body_expression_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'path_rules': 'exact NUL-separated file paths; slash-separated directory prefixes',
            'str_alignment': {'status': 'resolved' if str_resolved else 'unresolved',
                              'contract': 'STR entries precede MPY entries; compiled count traverses sizes to zero; offsets sum size + 1',
                              'reason': None if str_resolved else 'selected loader lacks the qualified STR count/branch/alignment relations'},
            'mpy_alignment': {'status': 'resolved' if mpy_resolved else 'unresolved',
                              'contract': 'bounded selected MPY branch assigns type and content[i - num_str]',
                              'reason': None if mpy_resolved else 'selected loader lacks the qualified MPY branch/type/content-index relations'}}


def _str_gap(result: dict, reason: str) -> dict:
    result.update(status='unresolved', availability={'status': 'unresolved'},
                  names_resolution='unresolved',
                  str_content_qualification={'status': 'unresolved', 'availability': {'status': 'unresolved'}},
                  mpy_content_qualification={'status': 'unresolved', 'reason': 'STR prefix partition is unqualified'})
    result['gaps'].append(reason)
    return result


def _mpy_payload(result: dict, inputs: list[dict], facts: dict, present: Callable) -> dict:
    """Qualify direct compiled module pointers without interpreting MPY contents."""
    remaining = result['names'][len(result['str_sources']):]
    candidates = [(owner, item) for owner in inputs for item in owner.get('definitions', [])
                  if item.get('name') == 'mp_frozen_mpy_content' and item.get('value_expression') is not None]
    qualification = {'status': 'not-selected' if not remaining else 'unresolved',
                     'remaining_names': remaining, 'mpy_semantics': 'not-decoded',
                     'table_evidence': [
                         {**_source(item), 'translation_unit': owner['translation_unit'],
                          'declaration_expression': item.get('declaration_expression'),
                          'value_expression': item.get('value_expression'),
                          'initializer_rows': item.get('initializer_rows'),
                          'allocated': bool(present(owner, item))}
                         for owner, item in candidates]}
    result['mpy_content_qualification'] = qualification
    if not remaining:
        qualification['reason'] = 'no names beyond the qualified STR prefix; a null sentinel is not a module'
        result.update(status='resolved', names_resolution='resolved',
                      availability={'status': 'known-present' if result['names'] else 'not-selected',
                                    'authority': 'qualified allocated names, loader, and STR byte slices'})
        return result
    try:
        alignment = result['loader_source']['mpy_alignment']
        if alignment['status'] != 'resolved':
            raise ValueError(alignment['reason'])
        if len(candidates) != 1:
            raise ValueError('MPY content table initialized ownership is missing or ambiguous')
        owner, table = candidates[0]
        if owner['translation_unit'] != facts['translation_unit'] or not present(owner, table):
            raise ValueError('MPY content table is not allocated in the frozen names translation unit')
        prefix = canonical(table.get('declaration_expression', '').split('=', 1)[0])
        expected = canonical('const mp_frozen_module_t *const mp_frozen_mpy_content[]')
        if prefix not in (expected, 'static ' + expected) or table.get('errors'):
            raise ValueError('MPY content requires an exact unsized const mp_frozen_module_t *const array')
        dimensions = table.get('array_dimensions')
        rows = table.get('initializer_rows')
        if not isinstance(dimensions, list) or len(dimensions) != 1 or dimensions[0].get('expression'):
            raise ValueError('MPY content array inferred extent is unqualified')
        if not isinstance(rows, list) or len(rows) != len(remaining):
            raise ValueError('MPY ordinary initializer row count differs from remaining frozen names')
        sources = []
        for index, (path, row) in enumerate(zip(remaining, rows)):
            pointer = re.fullmatch(r'&\s*([A-Za-z_]\w*)', row.get('expression', '').strip())
            if row.get('syntax_kind') != 'pointer_expression' or row.get('initializer_ordinal') != index or not pointer:
                raise ValueError('MPY row is not an ordinary direct non-null object address at its original ordinal')
            pointees = [item for item in facts.get('definitions', [])
                        if item.get('name') == pointer[1] and item.get('value_expression') is not None]
            if len(pointees) != 1 or not present(facts, pointees[0]) or pointees[0].get('errors'):
                raise ValueError('MPY row pointee initialized allocation in the same translation unit is unqualified')
            sources.append({'target_path': path, 'pointer_expression': row['expression'],
                            'source': {**_source(table), 'initializer_row': row,
                                       'pointee': _source(pointees[0]),
                                       'pointee_declaration_expression': pointees[0].get('declaration_expression'),
                                       'translation_unit': facts['translation_unit'],
                                       'allocation_authority': 'qualified allocated table and pointee in names translation unit',
                                       'loader_source': result['loader_source']}})
    except ValueError as exc:
        qualification['reason'] = str(exc)
        result.update(status='unresolved', names_resolution='unresolved', availability={'status': 'unresolved'})
        result['gaps'].append('MPY payload: ' + str(exc))
        return result
    result['mpy_sources'] = sources
    qualification.update(status='resolved', row_count=len(rows),
                         extent_authority='independently traversed ordinary unsized-array initializer rows',
                         availability={'status': 'known-present'})
    result.update(status='resolved', names_resolution='resolved',
                  availability={'status': 'known-present',
                                'authority': 'qualified allocated names, loader, STR bytes and MPY pointer dataset'})
    return result


def collect_frozen_payload(inputs: list[dict], present: Callable, store) -> dict:
    """Collect allocated compiled names; unknown ownership never grants availability.

    ``present(facts, definition)`` must prove qualified allocated object, symbol,
    and address ownership. Inputs are selected translation-unit indexes; the loader body is read
    from ``store``.
    """
    result = {'status': 'not-selected', 'names': [], 'names_resolution': 'not-selected', 'names_source': None,
              'translation_unit': None, 'availability': {'status': 'not-selected'},
              'str_sources': [], 'mpy_sources': [],
              'mpy_content_qualification': {'status': 'not-selected'},
              'gaps': []}
    names = [(facts, definition) for facts in inputs for definition in facts.get('definitions', [])
             if definition.get('name') == _NAMES and definition.get('value_expression') is not None]
    if not names:
        selected_frozen = any(Path(facts.get('source', '')).name == 'frozen_content.c'
                              or any(d.get('name') in (_NAMES, _SIZES, _CONTENT, 'mp_frozen_mpy_content')
                                     for d in facts.get('definitions', []))
                              for facts in inputs)
        if selected_frozen:
            result.update(status='unresolved', names_resolution='unresolved', availability={'status': 'unresolved'})
            result['mpy_content_qualification'] = {'status': 'unresolved', 'reason': 'names and STR prefix are not qualified'}
            result['gaps'].append('selected frozen inputs have no initialized names definition')
        return result
    result.update(status='unresolved', names_resolution='unresolved', availability={'status': 'unresolved'})
    result['mpy_content_qualification'] = {'status': 'unresolved', 'reason': 'names and STR prefix are not qualified'}
    owned = [(facts, definition) for facts, definition in names if present(facts, definition)]
    # Even an unqualified competing definition prevents a unique ownership claim.
    if len(names) != 1 or len(owned) != 1:
        result['gaps'].append('frozen names definition ownership is missing or ambiguous')
        result['names_source'] = [_source(definition) for _, definition in names]
        return result
    facts, definition = owned[0]
    result['translation_unit'] = facts['translation_unit']
    result['names_source'] = _source(definition)
    try:
        _array(definition, 'char')
        data = _strings(definition['value_expression'])
        fields = data.split(b'\0')
        end = fields.index(b'')
        if any(fields[end:]):
            raise ValueError('nonempty frozen name follows loader terminator')
        paths = [field.decode('utf-8') for field in fields[:end]]
        if len(set(paths)) != len(paths):
            raise ValueError('duplicate frozen target paths')
        if any(path.startswith('/') or any(part in ('', '.', '..') for part in path.split('/'))
               or '\\' in path for path in paths):
            raise ValueError('frozen target path is not a bounded relative import path')
        loader = _loader(inputs, present, store)
    except (ValueError, UnicodeError) as exc:
        result['gaps'].append(str(exc))
        return result
    result.update(status='unresolved', names_resolution='unresolved', names=paths,
                  availability={'status': 'unresolved'},
                  loader_source=loader)
    tables = {}
    for name, c_type in ((_SIZES, 'uint32_t'), (_CONTENT, 'char')):
        candidates = [(owner, item) for owner in inputs for item in owner.get('definitions', [])
                      if item.get('name') == name and item.get('value_expression') is not None]
        if not candidates:
            tables[name] = None
            continue
        if len(candidates) != 1:
            return _str_gap(result, name + ' ownership is ambiguous')
        owner, item = candidates[0]
        if owner['translation_unit'] != facts['translation_unit'] or not present(owner, item):
            return _str_gap(result, name + ' is not qualified in the selected frozen names translation unit')
        try:
            _array(item, c_type)
            tables[name] = item
        except ValueError as exc:
            return _str_gap(result, name + ': ' + str(exc))
    if tables[_SIZES] is None and tables[_CONTENT] is None:
        if loader['str_alignment']['status'] == 'resolved':
            return _str_gap(result, 'selected STR loader has no allocated sizes/content tables')
        result['str_content_qualification'] = {'status': 'not-selected', 'availability': {'status': 'not-selected'},
                                               'mpy_semantics': 'not-decoded'}
        return _mpy_payload(result, inputs, facts, present)
    if tables[_SIZES] is None or tables[_CONTENT] is None:
        return _str_gap(result, 'STR sizes/content pairing is incomplete')
    if loader['str_alignment']['status'] != 'resolved':
        return _str_gap(result, loader['str_alignment']['reason'])
    try:
        sizes = _sizes(tables[_SIZES]['value_expression'])
        content = _strings(tables[_CONTENT]['value_expression'])
        if len(sizes) > len(paths):
            raise ValueError('STR size count exceeds frozen names count')
        offset = 0
        sources = []
        for path, size in zip(paths, sizes):
            if offset + size >= len(content) or content[offset + size] != 0:
                raise ValueError('STR size/content slice lacks its exact NUL boundary')
            payload = content[offset:offset + size]
            sources.append({'target_path': path, 'sha256': hashlib.sha256(payload).hexdigest(),
                            'bytes_hex': payload.hex(), 'source': {
                                **_source(tables[_CONTENT]), 'offset': offset, 'bytes': size,
                                'sizes_source': _source(tables[_SIZES]), 'loader_source': loader}})
            offset += size + 1
        if not content[offset:] or any(content[offset:]):
            raise ValueError('STR content has unmatched nonzero bytes or lacks final terminator')
    except ValueError as exc:
        return _str_gap(result, str(exc))
    result['str_sources'] = sources
    result['str_content_qualification'] = {
        'status': 'resolved', 'availability': {'status': 'known-present' if sources else 'not-selected'},
        'authority': 'exact compiler-expanded byte slices, not manifest file inference',
        'source_comparison': 'compare these bytes with the exact source bytes consumed for AST declarations',
        'mpy_semantics': 'not-decoded'}
    return _mpy_payload(result, inputs, facts, present)
