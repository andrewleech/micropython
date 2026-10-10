"""Replay selected compiler records without replacing their build arguments."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import shlex
import shutil
import subprocess

from .frontend import canonical


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_input(path):
    """Capture the bytes and physical identity of one consumed input."""
    path = Path(path).absolute()
    resolved = path.resolve(strict=True)
    before = path.stat()
    content = path.read_bytes()
    after = path.stat()
    def stamp(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if stamp(before) != stamp(after) or path.resolve(strict=True) != resolved:
        raise RuntimeError(f'input changed while being read: {path}')
    return content, {'path': str(path), 'resolved': str(resolved),
                     'sha256': hashlib.sha256(content).hexdigest(), '_stat': stamp(after)}


def capture_inputs(paths):
    result = {}
    for path in sorted({str(Path(path).absolute()) for path in paths}):
        _, record = read_input(path)
        result[path] = record
    return result


def reconcile_inputs(groups):
    """Reject different content or physical ownership for the same input."""
    result, owners, physical = {}, {}, {}
    for owner, records in groups:
        for path, record in records.items():
            existing = result.get(path)
            same_target = physical.get(record['resolved'])
            if existing and existing != record:
                raise RuntimeError(f'conflicting consumed input snapshots for {path}: {owners[path]} and {owner}')
            if same_target and any(same_target[field] != record[field] for field in ('sha256', '_stat')):
                raise RuntimeError(f'conflicting consumed content snapshots for {record["resolved"]}: {owner}')
            result[path] = record
            owners[path] = owner
            physical[record['resolved']] = record
    return result


def verify_inputs(records):
    """Require every consumed input to retain its captured content and identity."""
    for path, expected in sorted(records.items()):
        _, current = read_input(path)
        if current != expected:
            raise RuntimeError(f'consumed input changed during collection: {path}')


def public_inputs(records):
    return {path: {field: value for field, value in record.items() if field != '_stat'}
            for path, record in sorted(records.items())}


def authority(root):
    path = Path(root).resolve() / 'tools/mpy_analysis/mpy_analysis/compdb.py'
    if not path.is_file():
        raise ValueError(f'MicroPython root lacks compiler database authority: {path}')
    spec = importlib.util.spec_from_file_location('_mpy_api_compdb_authority', path)
    module = importlib.util.module_from_spec(spec)
    content, snapshot = read_input(path)
    exec(compile(content, str(path), 'exec'), module.__dict__)
    module.__mpy_api_input__ = snapshot
    return module


def expand_arguments(argv, directory, stack=(), *, snapshots=None):
    """Expand compiler/linker response operands, preserving their content identity."""
    if snapshots is None:
        snapshots = {}
    expanded, inputs = [], {}
    for operand in argv:
        if operand.startswith('@'):
            path = (Path(directory) / operand[1:]).absolute()
            resolved = path.resolve(strict=True)
            if resolved in stack:
                raise ValueError(f'recursive compiler/linker response file: {path}')
            content, snapshot = read_input(path)
            nested, identities = expand_arguments(shlex.split(content.decode()), directory, (*stack, resolved), snapshots=snapshots)
            expanded.extend(nested)
            inputs.update(identities)
            if snapshots is not None:
                merged = reconcile_inputs([('previous response operands', snapshots), ('response operand ' + str(path), {str(path): snapshot})])
                snapshots.clear()
                snapshots.update(merged)
            if str(path) in inputs and inputs[str(path)] != snapshot['sha256']:
                raise RuntimeError(f'conflicting response file content: {path}')
            inputs[str(path)] = snapshot['sha256']
        else:
            expanded.append(operand)
    return expanded, inputs


def clean_flags(record):
    args = record['expanded_arguments'][1:] if 'expanded_arguments' in record else expand_arguments(record['arguments'][1:], record['directory'])[0]
    result, index = [], 0
    physical = Path(record['physical'])
    paired = {'-o', '-MF', '-MT', '-MQ', '-MJ'}
    dropped = {'-c', '-MD', '-MMD', '-MP', '-M', '-MM', '-MG', '-E', '-S'}
    while index < len(args):
        argument = args[index]
        if argument in paired:
            if index + 1 == len(args):
                raise ValueError(f'missing value for {argument}: {physical}')
            index += 2
            continue
        if argument in dropped or any(argument.startswith(flag) and argument != flag for flag in paired):
            index += 1
            continue
        if not argument.startswith('-') and (Path(record['directory']) / argument).resolve() == physical:
            index += 1
            continue
        result.append(argument)
        index += 1
    return result


def executable(name, directory):
    candidate = str((Path(directory) / name).resolve()) if '/' in name else shutil.which(name)
    if not candidate or not Path(candidate).is_file():
        raise ValueError(f'selected compiler/tool executable is unavailable: {name}')
    return candidate


def invoke(record, extras, source=None):
    argv = [record.get('compiler') or record.get('expanded_arguments', record['arguments'])[0], *clean_flags(record), *extras,
            record['file'] if source is None else '-']
    result = subprocess.run(argv, input=source, cwd=record['directory'], capture_output=True, text=True)
    receipt = {'argv': argv, 'directory': record['directory'], 'exit': result.returncode,
               'stderr': result.stderr, 'stdout_sha256': hashlib.sha256(result.stdout.encode()).hexdigest()}
    if source is not None:
        receipt['stdin'] = source
    if result.returncode:
        raise RuntimeError(f'compiler replay failed for {record["physical"]}: {shlex.join(argv)}\n{result.stderr}')
    return result.stdout, receipt


def unmark(text, directory):
    rows, origins, source, line = [], [], '<unknown>', 0
    for row in text.splitlines():
        marker = re.match(r'\s*#\s+(\d+)\s+"([^"]+)"', row)
        if marker:
            line = int(marker[1])
            name = marker[2]
            source = name if name.startswith('<') else str((Path(directory) / name).resolve())
            continue
        rows.append(row if not row.lstrip().startswith('#') else '')
        origins.append((source, line))
        line += 1
    return ('\n'.join(rows) + '\n' if rows else ''), origins


def compiler_contracts(record):
    samples = {'none': ('mp_obj_t', 'mp_const_none'), 'true': ('mp_obj_t', 'mp_const_true'),
               'false': ('mp_obj_t', 'mp_const_false'), 'rom_none': ('mp_rom_obj_t', 'MP_ROM_NONE'),
               'rom_true': ('mp_rom_obj_t', 'MP_ROM_TRUE'), 'rom_false': ('mp_rom_obj_t', 'MP_ROM_FALSE'),
               'rom_reference': ('mp_rom_obj_t', 'MP_ROM_PTR(&mp_type_type)'),
               'obj_reference': ('mp_obj_t', 'MP_OBJ_FROM_PTR(&mp_type_type)'),
               'null': ('mp_obj_t', 'MP_OBJ_NULL'), 'sentinel': ('mp_obj_t', 'MP_OBJ_SENTINEL'),
               'rom_qstr': ('mp_rom_obj_t', 'MP_ROM_QSTR(MP_QSTR___name__)'),
               'rom_int': ('mp_rom_obj_t', 'MP_ROM_INT(7)'),
               'buffer_read': ('unsigned', 'MP_BUFFER_READ'), 'buffer_write': ('unsigned', 'MP_BUFFER_WRITE')}
    samples.update({name: ('unsigned', expression) for name, expression in
                    {'module_getattr': 'MICROPY_MODULE_GETATTR',
                     'module_delegation': 'MICROPY_MODULE_ATTR_DELEGATION',
                     'instance_type_flag': 'MP_TYPE_FLAG_INSTANCE_TYPE',
                     'binds_self_flag': 'MP_TYPE_FLAG_BINDS_SELF',
                     'builtin_fun_flag': 'MP_TYPE_FLAG_BUILTIN_FUN',
                     'iterator_mask': 'MP_TYPE_FLAG_ITER_IS_ITERNEXT | MP_TYPE_FLAG_ITER_IS_CUSTOM | MP_TYPE_FLAG_ITER_IS_STREAM'}.items()})
    samples.update({name: ('unsigned', expression) for name, expression in
                    {'arg_required': 'MP_ARG_REQUIRED', 'arg_kw_only': 'MP_ARG_KW_ONLY',
                     'arg_kind_mask': 'MP_ARG_KIND_MASK', 'arg_bool': 'MP_ARG_BOOL',
                     'arg_int': 'MP_ARG_INT', 'arg_obj': 'MP_ARG_OBJ'}.items()})
    # Expansion requires no semantic compilation: unknown macros stay visibly unknown.
    physical_source = (Path(record['directory']) / record['file']).absolute()
    content, snapshot = read_input(physical_source)
    if record['dependencies'].get(snapshot['resolved']) != snapshot['sha256']:
        raise RuntimeError(f'constant probe source differs from selected dependency: {physical_source}')
    record['input_snapshots'] = reconcile_inputs([
        ('selected compiler inputs', record['input_snapshots']), ('constant probe source', {snapshot['path']: snapshot})])
    source = '#line 1 ' + json.dumps(record['file']) + '\n' + content.decode() + '\n' + '\n'.join(
        'static const ' + typ + ' mpy_api_contract_' + name + ' = ' + expression + ';'
        for name, (typ, expression) in samples.items()) + '\n'
    # Quote lookup retains the selected main source directory while include depth stays zero.
    probe_record = dict(record)
    if '-I-' not in record['expanded_arguments']:
        probe_record['expanded_arguments'] = [record['expanded_arguments'][0], '-iquote', str(physical_source.parent),
                                              *record['expanded_arguments'][1:]]
    raw, receipt = invoke(probe_record, ['-E', '-x', 'c'], source)
    validate_include_markers(raw, record)
    values = {}
    for name in samples:
        matched = re.search(r'\bmpy_api_contract_' + name + r'\s*=\s*([^;]+);', raw)
        if matched:
            value = canonical(matched[1])
            if not re.search(r'\b(?:MP_ROM_(?:NONE|TRUE|FALSE|QSTR|INT|PTR)|MP_OBJ_(?:FROM_PTR|NULL|SENTINEL)|mp_const_(?:none|true|false))\b', value):
                values[name] = value
    return values, receipt


def prepare(record, compdb):
    physical = record['physical']
    selected = {**record, 'arguments': compdb.arguments_of(record)}
    response_snapshots = {}
    expanded, response_inputs = expand_arguments(selected['arguments'], selected['directory'], snapshots=response_snapshots)
    selected['expanded_arguments'] = record.get('_expanded_arguments', expanded)
    selected['response_inputs'] = response_inputs
    source_snapshots = capture_inputs([compdb.entry_file(record)])
    selected['compiler'] = executable(selected['expanded_arguments'][0], selected['directory'])
    compiler_snapshots = capture_inputs([selected['compiler']])
    selected['compiler_sha256'] = next(iter(compiler_snapshots.values()))['sha256']
    selected['input_snapshots'] = reconcile_inputs([
        ('selected source', {record['_source_snapshot']['path']: record['_source_snapshot']}),
        ('selected response operands', record.get('_response_snapshots', {})),
        ('current source', source_snapshots), ('current response operands', response_snapshots),
        ('selected compiler', compiler_snapshots)])
    all_text, all_receipt = invoke(selected, ['-M'])
    project_text, project_receipt = invoke(selected, ['-MM'])
    paths = compdb.first_rule(all_text)
    project_paths = compdb.first_rule(project_text)
    if paths is None or project_paths is None:
        raise RuntimeError(f'selected compiler emitted no dependency rule: {physical}')
    dependency_snapshots = capture_inputs(Path(record['directory']) / path for path in set(paths) | set(project_paths))
    selected['input_snapshots'] = reconcile_inputs([
        ('compiler/source/response inputs', selected['input_snapshots']), ('compiler dependencies', dependency_snapshots)])
    # The discovery pass identifies paths; the stable pass selects them under captured input content.
    stable_all, stable_all_receipt = invoke(selected, ['-M'])
    stable_project, stable_project_receipt = invoke(selected, ['-MM'])
    if set(compdb.first_rule(stable_all) or []) != set(paths) or set(compdb.first_rule(stable_project) or []) != set(project_paths):
        raise RuntimeError(f'compiler dependency selection changed during collection: {physical}')
    verify_inputs(selected['input_snapshots'])
    selected['dependencies'] = {snapshot['resolved']: snapshot['sha256'] for snapshot in dependency_snapshots.values()}
    source_snapshot = next(iter(source_snapshots.values()))
    selected['dependencies'][source_snapshot['resolved']] = source_snapshot['sha256']
    selected['project_dependencies'] = sorted({str((Path(record['directory']) / path).resolve()) for path in project_paths})
    selected['receipts'] = [all_receipt, project_receipt, stable_all_receipt, stable_project_receipt]
    target, target_receipt = invoke(selected, ['-dumpmachine'])
    version, version_receipt = invoke(selected, ['--version'])
    selected['compiler_target'] = target.strip()
    selected['compiler_version'] = version.strip()
    selected['receipts'].extend([target_receipt, version_receipt])
    return selected


def validate_include_markers(text, record):
    for line in text.splitlines():
        marker = re.fullmatch(r'\s*#\s+\d+\s+"([^"]+)"(?:\s+([\d ]+))?\s*', line)
        if not marker or marker[1].startswith('<') or '1' not in (marker[2] or '').split():
            continue
        path = str((Path(record['directory']) / marker[1]).resolve())
        if path not in record['dependencies']:
            raise RuntimeError(f'compiler expansion consumes an uncaptured include dependency: {path}')


def validate_dependencies(record, compdb):
    for option, expected in (('-M', set(record['dependencies'])), ('-MM', set(record['project_dependencies']))):
        text, receipt = invoke(record, [option])
        paths = compdb.first_rule(text)
        if paths is None or {str((Path(record['directory']) / path).resolve()) for path in paths} != expected:
            raise RuntimeError(f'compiler dependency selection changed after expansion: {record["physical"]}')
        record['receipts'].append(receipt)
    verify_inputs(record['input_snapshots'])


def qstr_strings(dependencies, snapshots=None):
    """Read actual generated pool strings, not an encoded-name heuristic."""
    result = {}
    for path in sorted(dependencies):
        if Path(path).name != 'qstrdefs.generated.h':
            continue
        content, snapshot = read_input(path)
        if snapshot['sha256'] != dependencies[path]:
            raise RuntimeError(f'qstr pool differs from compiler dependency snapshot: {path}')
        if snapshots is not None:
            merged = reconcile_inputs([('consumed compiler inputs', snapshots), ('qstr pool', {snapshot['path']: snapshot})])
            snapshots.clear()
            snapshots.update(merged)
        for line_number, line in enumerate(content.decode().splitlines(), 1):
            match = re.fullmatch(r'QDEF[01]\(MP_QSTR_(\w*)\s*,\s*[^,]+,\s*[^,]+,\s*("(?:\\.|[^"\\])*")\s*\)', line)
            if not match:
                continue
            literal = match[2][1:-1]
            data, index = bytearray(), 0
            try:
                while index < len(literal):
                    char = literal[index]
                    index += 1
                    if char != '\\':
                        data.extend(char.encode('utf-8'))
                        continue
                    escape = literal[index]
                    index += 1
                    simple = {'n': 10, 'r': 13, 't': 9, 'b': 8, 'f': 12, 'v': 11, 'a': 7,
                              '\\': 92, '"': 34, "'": 39, '?': 63}
                    if escape in simple:
                        data.append(simple[escape])
                    elif escape == 'x':
                        number = re.match(r'[0-9a-fA-F]+', literal[index:])
                        if not number:
                            raise ValueError('missing hexadecimal escape')
                        data.append(int(number[0], 16))
                        index += len(number[0])
                    elif escape in '01234567':
                        tail = re.match(r'[0-7]{0,2}', literal[index:])[0]
                        data.append(int(escape + tail, 8))
                        index += len(tail)
                    else:
                        raise ValueError('unsupported C string escape')
                value = data.decode('utf-8')
            except (ValueError, UnicodeError, IndexError):
                result[match[1]] = {'status': 'unresolved', 'source': {'source': path, 'start_line': line_number,
                                                                       'end_line': line_number},
                                    'reason': 'generated qstr string encoding unassessed', 'expression': match[2]}
                continue
            result[match[1]] = {'status': 'resolved', 'value': value,
                                'source': {'source': path, 'start_line': line_number, 'end_line': line_number}}
    return result
