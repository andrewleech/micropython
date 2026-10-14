"""Selected link operands and allocated object/ELF ownership."""
from pathlib import Path
import re
import shlex
import subprocess

from .compiler import executable, expand_arguments, read_recorded, recorded_inputs, reconcile_inputs
from .bindings import materialize, split_args, strip_outer, row_pair, coalesce_definitions
from .semantics import demand_semantics, frontend_unresolved


def symbols(path, nm, directory):
    argv = [nm, '-a', str(path)]
    proc = subprocess.run(argv, cwd=directory, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f'cannot inspect selected link symbols: {shlex.join(argv)}\n{proc.stderr}')
    rows = []
    for line in proc.stdout.splitlines():
        match = re.match(r'\s*(?:([0-9a-fA-F]+)\s+)?([A-Za-z?])\s+(\S+)$', line)
        if match:
            rows.append({'address': int(match[1], 16) if match[1] else None, 'kind': match[2], 'name': match[3]})
    return rows, {'argv': argv, 'exit': proc.returncode, 'stderr': proc.stderr}


def object_sections(path, objdump, directory):
    argv = [objdump, '-t', str(path)]
    proc = subprocess.run(argv, cwd=directory, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f'cannot inspect selected object sections: {shlex.join(argv)}\n{proc.stderr}')
    rows = []
    for line in proc.stdout.splitlines():
        match = re.match(r'^([0-9a-fA-F]+)\s+(.+?)\s+(\S+)\s+([0-9a-fA-F]+)\s+(\S+)\s*$', line)
        if match and match[3] != '*UND*':
            rows.append({'offset': int(match[1], 16), 'section': match[3], 'size': int(match[4], 16), 'name': match[5]})
    return rows, {'argv': argv, 'exit': proc.returncode, 'stderr': proc.stderr}


def _path(text, directory):
    return str((Path(directory) / text).resolve())


def _map_contributions(allocated, directory, *, first_line=1, filesystem_operands=()):
    """Distinguish GNU map object inputs from its reserved generated stub producer."""
    generated_label = 'linker stubs'
    physical_operands = {_path(str(path), directory) for path in filesystem_operands}
    generated_path = _path(generated_label, directory)
    loaded, allocations, generated = [], [], []
    pending = None

    def generated_record(rows, section=None, address=None, size=None):
        if generated_path in physical_operands:
            raise ValueError('GNU map generated producer "linker stubs" conflicts with an explicit filesystem input')
        return {'kind': 'linker-generated', 'producer': generated_label,
                'map_rows': rows, 'section': section, 'address': address, 'size': size,
                'ownership': {'status': 'unassessed', 'reason': 'linker-generated contribution has no selected compiled-TU object owner'}}

    owner_pattern = r'(.+?\.(?:o|a)(?:\([^)]*\))?|linker stubs)'
    for number, line in enumerate(allocated.splitlines(), first_line):
        row = {'line': number, 'text': line}
        if line.startswith('LOAD '):
            owner = line[5:].strip()
            if owner == generated_label:
                generated.append(generated_record([row]))
            else:
                loaded.append(owner)
        single = re.fullmatch(r'\s+(\.[^\s]+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+' + owner_pattern + r'\s*', line)
        if single:
            section, address, size, owner = single.groups()
            rows = [row]
        else:
            name = re.fullmatch(r'\s+(\.[^\s]+)\s*', line)
            if name:
                pending = (name[1], row)
                continue
            continuation = re.fullmatch(r'\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+' + owner_pattern + r'\s*', line)
            if not continuation or not pending:
                pending = None
                continue
            section, previous_row = pending
            address, size, owner = continuation.groups()
            rows = [previous_row, row]
        pending = None
        address, size = int(address, 16), int(size, 16)
        if owner == generated_label:
            generated.append(generated_record(rows, section, address, size))
        elif address and size:
            allocations.append({'section': section, 'address': address, 'size': size,
                                'object': _path(owner, directory)})
    return loaded, allocations, generated


def collect_ownership(records, link):
    if not isinstance(link, dict):
        raise ValueError('selected link authority must be an object')
    for field in ('directory', 'elf', 'map'):
        value = link.get(field)
        if not isinstance(value, (str, Path)) or not str(value).strip() or '\x00' in str(value):
            raise ValueError(f'selected link {field} must be a nonempty path without NUL')
    if 'argv' not in link:
        raise ValueError('selected link authority requires its actual argv')
    if link.get('nm') is not None and (not isinstance(link['nm'], str) or not link['nm'].strip() or '\x00' in link['nm']):
        raise ValueError('selected nm must be a nonempty executable name/path without NUL')
    directory = Path(link['directory']).resolve()
    if not directory.is_dir():
        raise ValueError(f'link working directory does not exist: {directory}')
    elf, map_path = Path(link['elf']).resolve(), Path(link['map']).resolve()
    for path in (elf, map_path):
        if not path.is_file():
            raise ValueError(f'selected link artifact does not exist: {path}')
    argv = link['argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or '\x00' in a for a in argv):
        raise ValueError('selected link argv must be a nonempty string list without NUL')
    response_snapshots = {}
    expanded, response_inputs = expand_arguments(argv, directory, snapshots=response_snapshots)
    output = None
    for index, argument in enumerate(expanded):
        if argument == '-o' and index + 1 < len(expanded): output = expanded[index + 1]
        elif argument.startswith('-o') and len(argument) > 2: output = argument[2:]
    if output is None or _path(output, directory) != str(elf):
        raise ValueError('selected link command output does not match the supplied ELF')
    linker_arguments = []
    index = 1
    while index < len(expanded):
        argument = expanded[index]
        if argument.startswith('-Wl,'):
            linker_arguments.extend(argument[4:].split(','))
        elif argument == '-Xlinker' and index + 1 < len(expanded):
            index += 1
            linker_arguments.append(expanded[index])
        else:
            linker_arguments.append(argument)
        index += 1
    linker_arguments, linker_responses = expand_arguments(linker_arguments, directory, snapshots=response_snapshots)
    for path, value in linker_responses.items():
        if path in response_inputs and response_inputs[path] != value:
            raise RuntimeError(f'conflicting selected linker response content: {path}')
        response_inputs[path] = value
    selected_map = None
    for index, argument in enumerate(linker_arguments):
        if argument in ('-Map', '--Map') and index + 1 < len(linker_arguments):
            selected_map = linker_arguments[index + 1]
        elif argument.startswith(('-Map=', '--Map=')):
            selected_map = argument.split('=', 1)[1]
    if selected_map is None or _path(selected_map, directory) != str(map_path):
        raise ValueError('selected link command map output does not match the supplied linker map')
    linker = executable(expanded[0], directory)
    nm_name = link.get('nm')
    if not nm_name:
        name = Path(expanded[0]).name
        match = re.match(r'(.*?)(?:gcc|g\+\+|cc|clang|clang\+\+|ld)(?:-\d+)?$', name)
        nm_name = (match[1] if match else '') + 'nm'
    nm = executable(nm_name, directory)
    objdump_name = str(Path(nm).with_name(Path(nm).name.replace('nm', 'objdump')))
    objdump = executable(objdump_name, directory)
    map_content, map_snapshot = read_recorded(link['map'])
    input_snapshots = reconcile_inputs([
        ('selected link response operands', response_snapshots),
        ('selected map', {map_snapshot['path']: map_snapshot}),
        ('selected ELF and tools', recorded_inputs([link['elf'], linker, nm, objdump]))])
    map_text = map_content.decode()
    if 'Linker script and memory map' not in map_text:
        raise ValueError(f'selected linker map lacks allocated memory map: {map_path}')
    before_map, allocated = map_text.split('Linker script and memory map', 1)
    object_operands = [arg for arg in linker_arguments if not arg.startswith('-') and arg.endswith(('.o', '.a'))]
    operands = {_path(arg, directory) for arg in object_operands}
    script_paths = []
    for index, argument in enumerate(linker_arguments):
        script = None
        if argument in ('-T', '--script') and index + 1 < len(linker_arguments):
            script = linker_arguments[index + 1]
        elif argument.startswith('--script='):
            script = argument.split('=', 1)[1]
        elif argument.startswith('-T') and not argument.startswith(('-Ttext', '-Tdata', '-Tbss')):
            script = argument[2:]
        if script:
            script_paths.append(script)
    explicit_paths = [*object_operands, *script_paths, *(record['object'] for record in records)]
    explicit_paths.extend(arg for arg in linker_arguments if not arg.startswith('-')
                          and _path(arg, directory) not in (str(elf), str(map_path)))
    loaded_paths, allocations, generated_contributions = _map_contributions(
        allocated, directory, first_line=before_map.count('\n') + 1, filesystem_operands=explicit_paths)
    loaded = {_path(path, directory) for path in loaded_paths}
    selected_objects = [record['object'] for record in records
                        if record['object'] in operands or record['object'] in loaded
                        or any(block['object'] == record['object'] for block in allocations)]
    linked_inputs = recorded_inputs([*(Path(directory) / path for path in [*loaded_paths, *object_operands, *script_paths]),
                                     *selected_objects])
    input_snapshots = reconcile_inputs([
        ('selected link artifacts/tools/response operands', input_snapshots), ('selected linked operands', linked_inputs)])
    link_inputs = {snapshot['resolved']: snapshot['sha256'] for snapshot in linked_inputs.values()}
    by_physical = {snapshot['resolved']: snapshot for snapshot in input_snapshots.values()}
    elf_symbols, receipt = symbols(elf, nm, directory)
    if not any(symbol['kind'] != 'U' for symbol in elf_symbols):
        raise ValueError(f'selected ELF lacks symbols needed for allocated ownership; supply its unstripped linked image: {elf}')
    if not allocations:
        raise ValueError(f'selected map has no supported allocated object contributions: {map_path}')
    allocated_elf_symbols = {(s['name'], s['address']) for s in elf_symbols if s['kind'] != 'U'}
    objects, receipts = [], [receipt]
    for record in records:
        obj = str(Path(record['object']).resolve())
        blocks = [a for a in allocations if a['object'] == obj]
        if obj not in operands and obj not in loaded and not blocks:
            continue
        rows, receipt = symbols(obj, nm, directory)
        receipts.append(receipt)
        sections, receipt = object_sections(obj, objdump, directory)
        receipts.append(receipt)
        allocated_symbols = []
        for symbol in sections:
            for block in blocks:
                if symbol['section'] == block['section'] and symbol['offset'] < block['size']:
                    address = block['address'] + symbol['offset']
                    if (symbol['name'], address) in allocated_elf_symbols:
                        allocated_symbols.append({**symbol, 'address': address, 'authority': 'allocated-object-section-and-ELF-address'})
        objects.append({'object': {'path': obj, 'sha256': by_physical[obj]['sha256']}, 'translation_unit': record['physical'],
                        'symbols': rows, 'allocated_symbols': allocated_symbols, 'allocations': blocks})
    known = {o['object']['path'] for o in objects}
    return {'objects': objects, 'elf_symbols': elf_symbols, 'allocations': allocations,
            'unassessed_objects': sorted({a['object'] for a in allocations} - known),
            'generated_contributions': generated_contributions,
            'input_snapshots': input_snapshots,
            'selected_link_authority': {'elf': str(elf), 'elf_sha256': by_physical[str(elf)]['sha256'], 'map': str(map_path),
                                        'map_sha256': map_snapshot['sha256'], 'argv': argv, 'expanded_argv': expanded,
                                        'directory': str(directory), 'response_inputs': response_inputs,
                                        'linker_arguments': linker_arguments, 'link_inputs': link_inputs,
                                        'generated_contributions': generated_contributions,
                                        'linker': linker, 'linker_sha256': by_physical[str(Path(linker).resolve())]['sha256'],
                                        'nm': nm, 'nm_sha256': by_physical[str(Path(nm).resolve())]['sha256'],
                                        'objdump': objdump, 'objdump_sha256': by_physical[str(Path(objdump).resolve())]['sha256']},
            'receipts': receipts}


def join(inputs, ownership, store):
    """Join the unit indexes ``inputs`` to the selected link ownership; the unit semantics of the
    functions the join reads (bound wrapper functions, delegation providers) are demanded from
    ``store`` (``semantics.demand_semantics``)."""
    selected = {o['translation_unit']: o for o in ownership['objects']}
    definitions = [(f, d) for f in inputs for d in coalesce_definitions(f['definitions'])]
    strong = {}
    for obj in ownership['objects']:
        for symbol in obj['symbols']:
            if symbol['kind'].isupper() and symbol['kind'] not in ('U', 'W', 'V'):
                strong.setdefault(symbol['name'], []).append(obj)

    def present(facts, definition):
        owner = selected.get(facts['translation_unit'])
        return bool(owner and any(s['name'] == definition['name'] for s in owner['allocated_symbols']))

    def resolve(name, from_tu):
        local = [(f, d) for f, d in definitions if f['translation_unit'] == from_tu and d['name'] == name]
        if len(local) == 1 and present(*local[0]):
            return local[0], 'same-translation-unit-allocated-object'
        if len(local) > 1:
            return None, 'ambiguous-translation-unit-definition'
        candidates = [(f, d) for f, d in definitions if d['name'] == name and d['linkage'] == 'external' and present(f, d)]
        owners = strong.get(name, [])
        if len(owners) == 1:
            matched = [(f, d) for f, d in candidates if f['translation_unit'] == owners[0]['translation_unit']]
            if len(matched) == 1:
                return matched[0], 'unique-selected-allocated-external'
        return None, 'missing-ambiguous-or-unallocated-owner'

    def resolve_function(name, from_tu):
        local = [(facts, fn) for facts in inputs for fn in facts['functions']
                 if facts['translation_unit'] == from_tu and fn['name'] == name and present(facts, fn)]
        if len(local) == 1:
            return local[0], 'same-translation-unit-allocated-function'
        if len(local) > 1:
            return None, 'ambiguous-local-function'
        owners = strong.get(name, [])
        candidates = [(facts, fn) for facts in inputs for fn in facts['functions']
                      if fn['name'] == name and fn['linkage'] == 'external' and present(facts, fn)
                      and len(owners) == 1 and facts['translation_unit'] == owners[0]['translation_unit']]
        return (candidates[0], 'unique-selected-allocated-external-function') if len(candidates) == 1 else (None, 'missing-ambiguous-or-unallocated-function')

    result = materialize(inputs, resolve, selected, present, resolve_function,
                         lambda function: frontend_unresolved(store, function))
    result['unassessed_containers'] = [{'identity': d['id'], 'source': d['source_span'], 'name': d['name'],
                                        'reason': 'container has no qualified allocated object/ELF ownership'}
                                       for f, d in definitions if d['kind'] in ('type', 'module') and not present(f, d)]
    import_rows, delegation_rows = [], []
    for facts, table in definitions:
        if not present(facts, table):
            continue
        if table['kind'] == 'map-table' and table['name'] in ('mp_builtin_module_table', 'mp_builtin_extensible_module_table'):
            for index, entry in enumerate(table.get('map_entries', [])):
                pair = row_pair(entry, facts['contracts'])
                if not pair:
                    continue
                targets = re.findall(r'& (\w+)', pair[1])
                target, status = resolve(targets[-1], facts['translation_unit']) if targets else (None, 'unsupported-module-pointer')
                import_rows.append({'name': pair[0], 'table_id': table['id'], 'entry_index': index, 'source': entry['source_span'],
                                    'table_kind': 'extensible' if 'extensible' in table['name'] else 'ordinary',
                                    'module': target[1]['id'] if target else None, 'ownership': status})
        elif table['kind'] == 'module-delegation-table':
            for index, entry in enumerate(table.get('map_entries', [])):
                fields = split_args(strip_outer(entry['expression'], '{', '}'))
                if len(fields) != 2:
                    continue
                names = re.findall(r'& (\w+)', fields[0])
                handlers = re.findall(r'\b[A-Za-z_]\w*\b', fields[1])
                target, status = resolve(names[-1], facts['translation_unit']) if names else (None, 'unsupported-module-pointer')
                delegation_rows.append({'module': target[1]['id'] if target else None, 'ownership': status,
                                        'handler': handlers[-1] if handlers else None, 'table_id': table['id'],
                                        'table_translation_unit': facts['translation_unit'], 'source': entry['source_span'], 'entry_index': index})
    for container in result['containers']:
        policy = container['attribute_policy']
        rows = [r for r in import_rows if r['module'] == container['id']]
        container['import_policy']['selected_table_evidence'] = rows
        container['import_policy']['registration_selection'] = 'selected-compiler-table-and-allocated-owner' if rows else 'unresolved-registration-selection'
        if any(r['table_kind'] == 'extensible' for r in rows):
            container['import_policy']['kind'] = 'extensible-contributor-gap'
            policy['unresolved'].append('extensible import contributors unassessed')
        for row in [r for r in delegation_rows if r['module'] == container['id']]:
            owners = strong.get(row['handler'], [])
            functions = [(f, fn) for f in inputs for fn in f['functions'] if fn['name'] == row['handler'] and len(owners) == 1 and f['translation_unit'] == owners[0]['translation_unit']]
            dispatch = selected[row['table_translation_unit']]
            referenced = any(s['name'] == row['handler'] and s['kind'] == 'U' for s in dispatch['symbols'])
            row['status'] = 'selected-dispatch-and-handler' if referenced and len(functions) == 1 and any(s['name'] == row['handler'] for s in owners[0]['allocated_symbols']) else 'unresolved-dispatch-selection'
            row['qualified_key_tables'] = []
            if len(functions) == 1:
                provider_facts, provider = functions[0]
                row['handler_function'] = provider['id']
                for helper in demand_semantics(store, provider)['semantics']['inputs']:
                    if helper['helper'] != 'mp_module_generic_attr':
                        continue
                    for argument in helper['arguments']:
                        candidate, status = resolve(argument, provider_facts['translation_unit'])
                        if candidate and candidate[1]['kind'] == 'qstr-key-table':
                            row['qualified_key_tables'].append({'definition': candidate[1]['id'], 'keys': candidate[1]['keys'],
                                                               'source': candidate[1]['source_span'], 'ownership': status})
            policy.setdefault('delegation_evidence', []).append(row)
            policy['kind'] = 'selected-registered-delegation' if row['status'] == 'selected-dispatch-and-handler' else 'registered-delegation-selection-unassessed'
            policy['provider'] = row.get('handler_function', row['handler'])
            policy['unknown_attribute_return'] = 'Any'
            policy['unresolved'].append('delegated attribute read/write/result contracts and mutable value types unassessed')
        policy['unresolved'] = sorted(set(policy['unresolved']))
    result['import_rows'] = import_rows
    return result, resolve, present
