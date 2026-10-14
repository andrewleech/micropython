"""Replay selected compiler records without replacing their build arguments."""
from pathlib import Path
import os
import hashlib
import importlib.util
import re
import shlex
import shutil
import stat
import subprocess

from .frontend import canonical


# Symlinks and directory layout under dependency and probe paths are treated as fixed for the
# duration of one run, so ``resolve_path`` resolves each (directory, name) once per process.
# Input snapshots do not rely on that assumption: read_input caches the resolution only for a
# path none of whose components is a symlink, and resolves any other path before and after
# reading it; the final verification resolves every path again. File replacement or
# modification is detected by the (device, inode, size, mtime, ctime) stamp and the content hash.
_RESOLVED = {}
_LINK_FREE = {}
_LINKED = set()
# Consumed inputs, keyed by absolute path as spelled, each captured once per process on its first
# use (``recorded``). A compiler run whose dependencies were all captured before it started is
# bracketed by those captures and the final verification (``verify_inputs``), so the content it
# read is the recorded content.
_INPUTS = {}
# The ``__has_include`` probes in each recorded input that has any: ``(probes, unmodelled)``, the
# literal probes as ``(next, angled, name)`` and None, or a reason a use is not a literal probe. A
# probe is a filesystem query that no include directive or dependency rule reports.
_PROBES = {}
# The ``#pragma push_macro`` directives in each recorded input that has any, as {line: [macro names]}.
_PRAGMAS = {}
# What each probed path held when this process first observed it (``_found``), and the identity
# of each include search directory when first observed (``_directory``). As with inputs, a compiler
# run whose probed paths and search directories were all observed before it started is bracketed
# by those observations and the final verification.
_OBSERVED = {}
_OBSERVED_DIRECTORIES = {}
# Include selection constraints this process has already returned (``check_selection``).
_CONSTRAINTS = set()
# The include search order the last pass 1 with the same compiler, flags and directory reported,
# used to observe the paths ``__has_include`` probes test before the next such pass 1 starts.
_SEARCH_ORDERS = {}
_MARKER = re.compile(r'\s*#\s+(\d+)\s+"([^"]+)"')
_LINEMARKER = re.compile(r'# (\d+) "([^"\\]*)"((?: \d)*)')
_COMMAND_LINE_MARKER = re.compile(r'^# \d+ "<command-line>"(?: \d)*\n', re.M)
# ``-dI`` writes an executed include directive as ``#include "name"`` (or ``<name>``,
# ``include_next``, ``import``), after the whitespace and comments that precede it on its line.
_DUMPED_INCLUDE = re.compile(r'((?:.*\*/)?[ \t]*)#(include|include_next|import) (?:<([^>]*)>|"([^"]*)")')
# ``__has_include`` as a probe: a call with a literal operand, a call with any other operand, or
# neither (a definedness test, or the name in a comment). A ``#define`` of the name is no probe.
# Comments and string or character literals, so comments can be blanked with lines kept.
_COMMENT_OR_LITERAL = re.compile(rb'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
_HAS_INCLUDE = re.compile(rb'__has_include\w*')
# Text before ``__has_include`` on its line that makes it no probe: its own definition, an
# ``#undef``, or a definedness test.
_NOT_A_PROBE = re.compile(rb'#[ \t]*(?:define|undef|ifdef|ifndef)[ \t]+$|\bdefined[ \t]*\(?[ \t]*$')
_LITERAL_PROBE = re.compile(rb'[ \t]*\([ \t]*(?:"([^"\n]*)"|<([^>\n]*)>)[ \t]*\)')
_PUSH_MACRO_DIRECTIVE = re.compile(rb'^[ \t]*#[ \t]*pragma[ \t]+push_macro[ \t]*\([ \t]*"([^"\n]*)"[ \t]*\)', re.M)
# A ``#define`` or ``#undef`` starting a row of pass 1 output, after any comments on the row.
_DEFINITION_ROW = re.compile(r'(?:[ \t]*/\*.*?\*/)*[ \t]*#[ \t]*(define|undef)[ \t]+(\w+)')
# Outside a comment, what can open one: comment openers, and literals that hide them.
_COMMENT_STATE = re.compile(r'/\*|//|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
# Macros the preprocessor computes rather than stores; ``-fdirectives-only`` output defines none.
_DYNAMIC_MACROS = frozenset({
    '__FILE__', '__LINE__', '__BASE_FILE__', '__FILE_NAME__', '__INCLUDE_LEVEL__', '__COUNTER__', '__DATE__',
    '__TIME__', '__TIMESTAMP__', '_Pragma', '__has_include', '__has_include_next', '__has_attribute',
    '__has_cpp_attribute', '__has_c_attribute', '__has_builtin', '__has_feature', '__has_extension'})
# Builtin macros whose expansion names the file being read: in pass 2 that is stdin, not the source.
_STDIN_DEPENDENT = ('__BASE_FILE__', '__TIMESTAMP__')
# What pass 2 poisons, so that any use of one fails pass 2 and the unit is expanded by plain -E:
# the builtins naming the file being read, written or pasted, and the pragma names a ``_Pragma``
# operand that changes preprocessor state must contain. Pass 1 runs no ``_Pragma``, so where
# such an operand executes pass 1 may have entered, defined or selected differently from plain
# -E; pass 2 runs every ``_Pragma`` that plain -E would, however its operand was spelled.
_PASS_TWO_POISON = (*_STDIN_DEPENDENT, 'once', 'push_macro', 'pop_macro', 'system_header')
_POISONED = re.compile(r'attempt to use poisoned "(\w+)"')
_CONTRACT_PROBE = re.compile(r'\bmpy_api_contract_(\w+)\s*=\s*([^;]+);')
_CONTRACT_UNRESOLVED = re.compile(r'\b(?:MP_ROM_(?:NONE|TRUE|FALSE|QSTR|INT|PTR)|MP_OBJ_(?:FROM_PTR|NULL|SENTINEL)|mp_const_(?:none|true|false))\b')
_DEPENDENCY_TARGET = 'mpy-api-dependencies'
# Pass 1 is the only compiler run that resolves includes. Its output keeps directives and the
# position of every executed include (-fdirectives-only, -dI); its stderr carries the include
# search order (-v) and the dependency rule (-MD to stderr).
_PASS_ONE = ['-E', '-fdirectives-only', '-dI', '-v', '-MD', '-MF', '/dev/stderr', '-MT', _DEPENDENCY_TARGET]
# Pass 2 expands pass 1's output from stdin and reads no other input.
# Pass 2 diagnostics are not the build's (the build compiled with its own); the poisoning pass 2
# adds warns, so warnings stay warnings here.
_PASS_TWO = ['-E', '-fpreprocessed', '-fdirectives-only', '-Wno-error', '-x', 'c']
_PASS_TWO_PREFIX = '_Pragma("GCC poison ' + ' '.join(_PASS_TWO_POISON) + '")\n'
# The expansion in one run, for a unit pass 2 cannot reproduce; with the dependency rule on stderr.
_PLAIN = ['-E', '-MD', '-MF', '/dev/stderr', '-MT', _DEPENDENCY_TARGET]
_PROBE_FILE = '<mpy-api-contract-probe>'
# Options whose include lookups the stat replay does not model.
_UNMODELLED_OPTIONS = ('-include', '--include', '-imacros', '--imacros')
_UNMODELLED_FLAGS = ('-I-', '-remap', '--remap')
_CONTRACT_SAMPLES = {
    'none': ('mp_obj_t', 'mp_const_none'), 'true': ('mp_obj_t', 'mp_const_true'),
    'false': ('mp_obj_t', 'mp_const_false'), 'rom_none': ('mp_rom_obj_t', 'MP_ROM_NONE'),
    'rom_true': ('mp_rom_obj_t', 'MP_ROM_TRUE'), 'rom_false': ('mp_rom_obj_t', 'MP_ROM_FALSE'),
    'rom_reference': ('mp_rom_obj_t', 'MP_ROM_PTR(&mp_type_type)'),
    'obj_reference': ('mp_obj_t', 'MP_OBJ_FROM_PTR(&mp_type_type)'),
    'null': ('mp_obj_t', 'MP_OBJ_NULL'), 'sentinel': ('mp_obj_t', 'MP_OBJ_SENTINEL'),
    'rom_qstr': ('mp_rom_obj_t', 'MP_ROM_QSTR(MP_QSTR___name__)'),
    'rom_int': ('mp_rom_obj_t', 'MP_ROM_INT(7)'),
    'buffer_read': ('unsigned', 'MP_BUFFER_READ'), 'buffer_write': ('unsigned', 'MP_BUFFER_WRITE'),
    **{name: ('unsigned', expression) for name, expression in {
        'module_getattr': 'MICROPY_MODULE_GETATTR',
        'module_delegation': 'MICROPY_MODULE_ATTR_DELEGATION',
        'instance_type_flag': 'MP_TYPE_FLAG_INSTANCE_TYPE',
        'binds_self_flag': 'MP_TYPE_FLAG_BINDS_SELF',
        'builtin_fun_flag': 'MP_TYPE_FLAG_BUILTIN_FUN',
        'iterator_mask': 'MP_TYPE_FLAG_ITER_IS_ITERNEXT | MP_TYPE_FLAG_ITER_IS_CUSTOM | MP_TYPE_FLAG_ITER_IS_STREAM',
        'arg_required': 'MP_ARG_REQUIRED', 'arg_kw_only': 'MP_ARG_KW_ONLY',
        'arg_kind_mask': 'MP_ARG_KIND_MASK', 'arg_bool': 'MP_ARG_BOOL',
        'arg_int': 'MP_ARG_INT', 'arg_obj': 'MP_ARG_OBJ'}.items()}}


def resolve_path(directory, name):
    """Resolve ``name`` relative to ``directory`` through symlinks, memoized per process."""
    key = (str(directory), name)
    result = _RESOLVED.get(key)
    if result is None:
        result = _RESOLVED[key] = str((Path(directory) / name).resolve())
    return result


def clear_resolution_cache():
    _RESOLVED.clear()
    _LINK_FREE.clear()
    _LINKED.clear()


def clear_input_table():
    _INPUTS.clear()
    _PROBES.clear()
    _PRAGMAS.clear()
    _OBSERVED.clear()
    _OBSERVED_DIRECTORIES.clear()
    _CONSTRAINTS.clear()
    _SEARCH_ORDERS.clear()


def _has_symlink(absolute):
    """True when any named component of the absolute path ``absolute`` is a symlink."""
    prefix = ''
    for part in absolute.split('/')[1:]:
        prefix += '/' + part
        if part not in ('', '.', '..') and os.path.islink(prefix):
            return True
    return False


def _stamp(status):
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def read_input(path):
    """Capture the bytes and physical identity of one consumed input."""
    path = Path(path).absolute()
    key = str(path)
    resolved = _LINK_FREE.get(key)
    linked = resolved is None
    if linked:
        resolved = str(path.resolve(strict=True))
        if key not in _LINKED:
            if _has_symlink(key):
                _LINKED.add(key)
            else:
                _LINK_FREE[key] = resolved
                linked = False
    before = path.stat()
    content = path.read_bytes()
    after = path.stat()
    if _stamp(before) != _stamp(after) or (linked and str(path.resolve(strict=True)) != resolved):
        raise RuntimeError(f'input changed while being read: {path}')
    return content, {'path': key, 'resolved': resolved,
                     'sha256': hashlib.sha256(content).hexdigest(), '_stat': _stamp(after)}


def strip_comments(data):
    """``data`` with every comment blanked to spaces, its line breaks and literals kept."""
    def blank(match):
        text = match[0]
        return text if text[:1] in b'"\'' else re.sub(rb'[^\n]', b' ', text)
    return _COMMENT_OR_LITERAL.sub(blank, data)


def probe_scan(text, label):
    """The ``__has_include`` probes in comment-blanked ``text``: ``(probes, unmodelled)`` as in
    ``_PROBES``, with ``label`` naming where the text came from in a reason."""
    probes, unmodelled = [], None
    for match in _HAS_INCLUDE.finditer(text):
        if _NOT_A_PROBE.search(text, text.rfind(b'\n', 0, match.start()) + 1, match.start()):
            continue
        literal = _LITERAL_PROBE.match(text, match.end())
        if match[0] not in (b'__has_include', b'__has_include_next'):
            unmodelled = unmodelled or match[0].decode() + ' in ' + label
        elif literal is None:
            unmodelled = unmodelled or '__has_include without a literal operand in ' + label
        else:
            angled = literal[2] is not None
            probes.append((match[0] == b'__has_include_next', angled,
                           (literal[2] if angled else literal[1]).decode('utf-8', 'surrogateescape')))
    return probes, unmodelled


def _record(key, content, snapshot):
    _INPUTS[key] = snapshot
    if b'__has_include' not in content and b'push_macro' not in content:
        return snapshot
    text = strip_comments(content)
    if b'__has_include' in text:
        probes, unmodelled = probe_scan(text, key)
        if probes or unmodelled:
            _PROBES[key] = (probes, unmodelled)
    pushes = {}
    for directive in _PUSH_MACRO_DIRECTIVE.finditer(text):
        pushes.setdefault(text.count(b'\n', 0, directive.start()) + 1, []).append(
            directive[1].decode('utf-8', 'surrogateescape'))
    if pushes:
        _PRAGMAS[key] = pushes
    return snapshot


def recorded(path):
    """The snapshot of the consumed input ``path``, captured on its first use in this process."""
    key = str(Path(path).absolute())
    snapshot = _INPUTS.get(key)
    if snapshot is None:
        snapshot = _record(key, *read_input(key))
    return snapshot


def recorded_inputs(paths):
    return {path: recorded(path) for path in sorted({str(Path(path).absolute()) for path in paths})}


def read_recorded(path):
    """The bytes of the consumed input ``path``, which must equal its recorded snapshot."""
    key = str(Path(path).absolute())
    content, current = read_input(key)
    snapshot = _INPUTS.get(key)
    if snapshot is None:
        snapshot = _record(key, content, current)
    elif current != snapshot:
        raise RuntimeError(f'consumed input changed during collection: {key}')
    return content, snapshot


class InputTable:
    """Consumed input snapshots of several owners, reconciled as each owner's are added."""

    def __init__(self):
        self.records, self._owners, self._physical = {}, {}, {}

    def add(self, owner, records):
        """Reject different content or physical ownership for an input already added."""
        for path, record in records.items():
            existing = self.records.get(path)
            same_target = self._physical.get(record['resolved'])
            if existing and existing != record:
                raise RuntimeError(f'conflicting consumed input snapshots for {path}: {self._owners[path]} and {owner}')
            if same_target and any(same_target[field] != record[field] for field in ('sha256', '_stat')):
                raise RuntimeError(f'conflicting consumed content snapshots for {record["resolved"]}: {owner}')
            self.records[path] = record
            self._owners[path] = owner
            self._physical[record['resolved']] = record


def reconcile_inputs(groups):
    """Reject different content or physical ownership for the same input."""
    table = InputTable()
    for owner, records in groups:
        table.add(owner, records)
    return table.records


def verify_inputs(records):
    """Require every consumed input to retain its captured content and identity.

    Every path is resolved and stat'ed again; each distinct physical file is read and hashed once."""
    current = {}
    for path, expected in sorted(records.items()):
        try:
            resolved = str(Path(path).resolve(strict=True))
            stamp = _stamp(os.stat(path))
        except OSError as exc:
            raise RuntimeError(f'consumed input changed during collection: {path}') from exc
        if resolved != expected['resolved'] or stamp != expected['_stat']:
            raise RuntimeError(f'consumed input changed during collection: {path}')
        if resolved not in current:
            current[resolved] = read_input(resolved)[1]
        if any(current[resolved][field] != expected[field] for field in ('sha256', '_stat')):
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
    content, snapshot = read_recorded(path)
    exec(compile(content, str(path), 'exec'), module.__dict__)
    module.__mpy_api_input__ = snapshot
    return module


def expand_arguments(argv, directory, *, snapshots=None):
    """Expand compiler/linker response operands, preserving their content identity."""
    if snapshots is None:
        snapshots = {}
    # One frame per operand list being expanded: its remaining operands, its expanded operands,
    # its response identities, the resolved response files enclosing it, and the response
    # operand ``(path, snapshot)`` it expands within the frame below (None for ``argv``).
    frames = [(iter(argv), [], {}, (), None)]
    while True:
        operands, expanded, inputs, stack, response = frames[-1]
        for operand in operands:
            if operand.startswith('@'):
                path = (Path(directory) / operand[1:]).absolute()
                resolved = path.resolve(strict=True)
                if resolved in stack:
                    raise ValueError(f'recursive compiler/linker response file: {path}')
                content, snapshot = read_recorded(path)
                frames.append((iter(shlex.split(content.decode())), [], {}, (*stack, resolved), (path, snapshot)))
                break
            expanded.append(operand)
        else:
            frames.pop()
            if response is None:
                return expanded, inputs
            path, snapshot = response
            _, outer_expanded, outer_inputs, _, _ = frames[-1]
            outer_expanded.extend(expanded)
            outer_inputs.update(inputs)
            merged = reconcile_inputs([('previous response operands', snapshots), ('response operand ' + str(path), {str(path): snapshot})])
            snapshots.clear()
            snapshots.update(merged)
            if str(path) in outer_inputs and outer_inputs[str(path)] != snapshot['sha256']:
                raise RuntimeError(f'conflicting response file content: {path}')
            outer_inputs[str(path)] = snapshot['sha256']


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
        if not argument.startswith('-') and resolve_path(record['directory'], argument) == str(physical):
            index += 1
            continue
        result.append(argument)
        index += 1
    return result


def executable(name, directory):
    candidate = resolve_path(directory, name) if '/' in name else shutil.which(name)
    if not candidate or not Path(candidate).is_file():
        raise ValueError(f'selected compiler/tool executable is unavailable: {name}')
    return candidate


# Recorded results of compiler identity queries (-dumpmachine, --version), keyed by the flags and
# working directory they ran with. These queries do not read the source operand, so one run
# serves every translation unit with the same flags. Dependency selection and expansion are
# never replayed.
_REPLAYS = {}


def clear_replay_cache():
    _REPLAYS.clear()


def invoke(record, extras, source=None, *, reuse=False, fail=True):
    """Run the selected compiler; ``reuse`` returns the recorded result of an identical earlier run.

    Only for queries that do not read the source operand (the reuse key then omits it). With
    ``fail`` false a failed run is returned with its exit status in the receipt."""
    argv = [record.get('compiler') or record.get('expanded_arguments', record['arguments'])[0], *clean_flags(record), *extras,
            record['file'] if source is None else '-']
    key = None
    if reuse and source is None:
        key = (tuple(argv[:-1] if extras in (['-dumpmachine'], ['--version']) else argv), record['directory'])
        replay = _REPLAYS.get(key)
        if replay is not None:
            stdout, receipt = replay
            return stdout, {**receipt, 'argv': argv}
    result = subprocess.run(argv, input=source, cwd=record['directory'], capture_output=True, text=True)
    receipt = {'argv': argv, 'directory': record['directory'], 'exit': result.returncode,
               'stderr': result.stderr, 'stdout_sha256': hashlib.sha256(result.stdout.encode()).hexdigest()}
    if source is not None:
        receipt['stdin_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    if result.returncode and fail:
        raise RuntimeError(f'compiler replay failed for {record["physical"]}: {shlex.join(argv)}\n{result.stderr}')
    if key is not None and not result.returncode:
        _REPLAYS[key] = (result.stdout, receipt)
    return result.stdout, receipt


def unmark(text, directory, entries=None):
    """Rows of ``text`` without linemarkers or directives, and the (source, line) of each row.

    ``entries``, when given, receives the file name of every entering linemarker (flag 1)."""
    rows, origins, source, line = [], [], '<unknown>', 0
    for row in text.splitlines():
        if '#' not in row:
            rows.append(row)
            origins.append((source, line))
            line += 1
            continue
        marker = _MARKER.match(row)
        if marker:
            line = int(marker[1])
            name = marker[2]
            source = name if name.startswith('<') else resolve_path(directory, name)
            if entries is not None and '1' in row[marker.end():].split():
                entries.append(name)
            continue
        rows.append(row if not row.lstrip().startswith('#') else '')
        origins.append((source, line))
        line += 1
    return ('\n'.join(rows) + '\n' if rows else ''), origins


def _dependency_pass(record, compdb):
    """Run pass 1; return its output, receipt and dependency rule prerequisites."""
    text, receipt = invoke(record, _PASS_ONE)
    stderr = receipt['stderr']
    start = ('\n' + stderr).find('\n' + _DEPENDENCY_TARGET + ':')
    paths = compdb.first_rule(stderr[start:]) if start >= 0 else None
    if paths is None:
        raise RuntimeError(f'selected compiler emitted no dependency rule: {record["physical"]}')
    return text, receipt, paths


def search_order(stderr):
    """The quote chain, bracket chain and omitted directories pass 1 reported, or None.

    Omitted are the directories -v reports ignoring (nonexistent or duplicate) and those the
    compiler warns are not a directory; any other report of one is not modelled."""
    lines = stderr.split('\n')
    try:
        quote = lines.index('#include "..." search starts here:')
        bracket = lines.index('#include <...> search starts here:', quote)
        end = lines.index('End of search list.', bracket)
    except ValueError:
        return None
    listed = lines[quote + 1:bracket] + lines[bracket + 1:end]
    if any(not line.startswith(' ') or line.endswith(' (framework directory)') for line in listed):
        return None
    omitted = [(entry, kind) for kind, entry in
               re.findall(r'^ignoring (nonexistent|duplicate) directory "([^"]*)"$', stderr, re.M)]
    files = re.findall(r'^[^\n:]*: warning: (.*): not a directory$', stderr, re.M)
    if len(files) != stderr.count('not a directory'):
        return None
    omitted.extend((entry, 'not a directory') for entry in files)
    return [line[1:] for line in lines[quote + 1:bracket]], [line[1:] for line in lines[bracket + 1:end]], omitted


def scan_dependency_pass(text, main):
    """Read pass 1's output: the pass-2 input, the files entered and every executed include lookup.

    ``-dI`` rows are removed from the pass-2 input, which would otherwise include the named file
    again. An include the compiler entered is followed by its entering linemarker (flag 1) with
    only linemarkers between. A row continuing the previous one (after a backslash) is no
    directive. A comment row of the same form as a ``-dI`` row is removed as well and read as an
    include that entered no file: the expansion's tokens are unchanged, and the stat replay finds
    that lookup unmodelled or holds its candidates as it does for any include.

    Each lookup is ``[including file, index of the lookup that entered it or None, directive,
    angled, name, entered file or None]``. ``entered`` maps each entered file name to whether its
    first entry is in a system header; ``mixed`` holds the names first entered in a system header
    and entered again outside one; ``order`` lists every entry in order."""
    rows = text.split('\n')
    lookups, entered, mixed, order, unmodelled = [], {}, set(), [], []
    stack, pending = [], None
    for index, row in enumerate(rows):
        if '#' not in row:
            pending = None
            continue
        if row.startswith('# '):
            marker = _LINEMARKER.fullmatch(row)
            if marker is None:
                if row[2:3].isdigit():
                    unmodelled.append('unparsed linemarker ' + row)
                pending = None
                continue
            name, flags = marker[2], marker[3].split()
            if '1' in flags:
                if pending is not None:
                    lookups[pending][5] = name
                    entry = pending
                elif stack and stack[-1][0] == '<command-line>' and name.endswith('/stdc-predef.h'):
                    # The implicit preinclude, looked up as <stdc-predef.h>.
                    lookups.append(['<command-line>', None, 'include', True, 'stdc-predef.h', name])
                    entry = len(lookups) - 1
                else:
                    unmodelled.append('file entered without an include directive: ' + name)
                    entry = None
                system = '3' in flags
                if name not in entered:
                    entered[name] = system
                elif entered[name] and not system:
                    mixed.add(name)
                order.append(name)
                stack.append((name, entry))
                pending = None
            elif '2' in flags:
                if len(stack) < 2 or stack[-2][0] != name:
                    unmodelled.append('return to a file not including the current one: ' + name)
                    stack = [(name, None)]
                else:
                    stack.pop()
                pending = None
            elif not stack or stack[-1][0] != name:
                if len(stack) <= 1 and name in (main, '<built-in>', '<command-line>'):
                    stack = [(name, None)]
                else:
                    unmodelled.append('presumed file name changed to ' + name)
                    stack = [*stack[:-1], (name, stack[-1][1] if stack else None)]
            continue
        if ('#include' in row or '#import' in row) and not (index and rows[index - 1].endswith('\\')):
            dumped = _DUMPED_INCLUDE.fullmatch(row)
            if dumped:
                rows[index] = dumped[1]
                if not stack:
                    unmodelled.append('include outside any file')
                    stack = [('', None)]
                angled = dumped[3] is not None
                lookups.append([stack[-1][0], stack[-1][1], dumped[2], angled, dumped[3] if angled else dumped[4], None])
                pending = len(lookups) - 1
                continue
        pending = None
    return {'source': '\n'.join(rows), 'lookups': lookups, 'entered': entered, 'mixed': mixed, 'order': order,
            'unmodelled': unmodelled}


def project_dependencies(directory, main, scan):
    """The -MM set as far as pass 1 decides it, and the files it leaves undecided.

    gcc lists a name under -MM when one of its file entries is first stacked outside a system
    header. A name's first entry is the first stacking of an entry, so a name first entered outside
    a system header is listed and a name only entered in system headers is not. A name first
    entered in a system header and entered again outside one is listed only if that later entry is
    the first stacking of another file entry (another lookup name or start directory), which the
    output does not show: such a file is undecided unless another name of it is listed."""
    members = {resolve_path(directory, main)} | {
        resolve_path(directory, name) for name, system in scan['entered'].items() if not system}
    undecided = {resolve_path(directory, name) for name in scan['mixed']} - members
    return sorted(members), sorted(undecided)


def project_set(record, compdb, scan, receipts):
    """The -MM set: derived from pass 1, or from a -MM run when pass 1 leaves files undecided.

    The -MM run must agree with every file pass 1 decides."""
    members, undecided = project_dependencies(record['directory'], record['file'], scan)
    if not undecided:
        return members
    text, receipt = invoke(record, ['-MM'])
    receipts.append(receipt)
    paths = compdb.first_rule(text)
    listed = None if paths is None else {resolve_path(record['directory'], path) for path in paths}
    if listed is None or not set(members) <= listed or not listed <= set(members) | set(undecided):
        raise RuntimeError(f'compiler dependency selection changed during collection: {record["physical"]}')
    return sorted(listed)


def prepare(record, compdb):
    """Capture the selected inputs and run pass 1, from which dependencies and expansion come."""
    physical, directory = record['physical'], record['directory']
    selected = {**record, 'arguments': compdb.arguments_of(record)}
    response_snapshots = record['_response_snapshots']
    if '_expanded_arguments' in record:
        selected['expanded_arguments'] = record['_expanded_arguments']
        selected['response_inputs'] = record['_response_inputs']
    else:
        response_snapshots = dict(response_snapshots)
        selected['expanded_arguments'], selected['response_inputs'] = expand_arguments(
            selected['arguments'], directory, snapshots=response_snapshots)
    selected['compiler'] = executable(selected['expanded_arguments'][0], directory)
    tool = recorded(selected['compiler'])
    selected['compiler_sha256'] = tool['sha256']
    source = recorded(compdb.entry_file(record))
    snapshots = reconcile_inputs([
        ('selected source', {record['_source_snapshot']['path']: record['_source_snapshot']}),
        ('selected response operands', response_snapshots),
        ('current source', {source['path']: source}), ('selected compiler', {tool['path']: tool})])
    order_key = (selected['compiler'], tuple(clean_flags(selected)), directory)
    previous = _SEARCH_ORDERS.get(order_key)
    if previous is not None:
        for path in search_directories(directory, previous):
            observe_directory(path)
        predicted = probe_paths(directory, [scan for scan in _PROBES.values() if not scan[1]], _INPUTS, previous)
        for path in predicted:
            observe(path)
    text, receipt, paths = _dependency_pass(selected, compdb)
    consumed = {str(Path(directory) / path) for path in paths}
    order = _SEARCH_ORDERS[order_key] = search_order(receipt['stderr'])
    known = consumed <= _INPUTS.keys() and set(search_directories(directory, order)) <= _OBSERVED_DIRECTORIES.keys()
    recorded_inputs(consumed)
    for path in search_directories(directory, order):
        observe_directory(path)
    scans = [_PROBES[key] for key in sorted(consumed & _PROBES.keys())]
    command_line = probe_scan(strip_comments(command_line_definitions(text).encode('utf-8', 'surrogateescape')),
                              '<command-line>')
    if command_line[0] or command_line[1]:
        scans.append(command_line)
    probes = probe_paths(directory, scans, consumed, order)
    if not isinstance(probes, str):
        known = known and probes <= _OBSERVED.keys()
        for path in probes:
            observe(path)
    if not known:
        # This run read inputs, searched directories or probed paths before they were captured or
        # observed: they are now, and pass 1 runs again, so the expansion used comes from a run that
        # started after every capture of what it read. Receipts record the runs the facts come
        # from: this one.
        text, receipt, again = _dependency_pass(selected, compdb)
        if set(again) != set(paths) or search_order(receipt['stderr']) != order:
            raise RuntimeError(f'compiler dependency selection changed during collection: {physical}')
    dependency_snapshots = recorded_inputs(consumed)
    selected['input_snapshots'] = reconcile_inputs([
        ('compiler/source/response inputs', snapshots), ('compiler dependencies', dependency_snapshots)])
    selected['dependencies'] = {snapshot['resolved']: snapshot['sha256'] for snapshot in dependency_snapshots.values()}
    selected['dependencies'][source['resolved']] = source['sha256']
    scan = scan_dependency_pass(text, record['file'])
    entered = {resolve_path(directory, name) for name in scan['entered']} | {resolve_path(directory, record['file'])}
    if entered != set(selected['dependencies']):
        raise RuntimeError(f'compiler dependency rule disagrees with the files its expansion entered: {physical}')
    selected['receipts'] = [receipt]
    selected['project_dependencies'] = project_set(selected, compdb, scan, selected['receipts'])
    selected['_pass_one'] = text
    selected['_scan'] = scan
    selected['_search_order'] = order
    selected['_probes'] = probes
    selected['_consumed'] = consumed
    target, target_receipt = invoke(selected, ['-dumpmachine'], reuse=True)
    version, version_receipt = invoke(selected, ['--version'], reuse=True)
    selected['compiler_target'] = target.strip()
    selected['compiler_version'] = version.strip()
    selected['receipts'].extend([target_receipt, version_receipt])
    return selected


def divergence(record):
    """Why pass 2 cannot reproduce plain -E for this unit, or None.

    Pass 2 receives pass 1's output, in which a macro definition stands as written but some
    preprocessor state does not:

    - ``__BASE_FILE__`` and ``__TIMESTAMP__`` name the file being read, which in pass 2 is stdin.
      Any occurrence in pass 1's output routes the unit.
    - ``#pragma pop_macro`` reappears in pass 1's output as ``#undef``, which equals the pop only
      when the macro was undefined at its push. The ``#define`` and ``#undef`` directives of the
      output are replayed in order, and a push of a macro defined at that point (or computed by
      the preprocessor) routes the unit.

    Pass 1 runs no ``_Pragma``. One whose operand changes preprocessor state (``once``,
    ``push_macro``, ``pop_macro``, ``system_header``) uses a name pass 2 poisons
    (``_PASS_TWO_POISON``), and pass 2 runs every ``_Pragma`` plain -E would: before the first such
    ``_Pragma`` pass 1 and plain -E agree, so it reaches pass 2, fails it, and ``expand`` routes
    the unit. ``GCC poison`` cannot diverge in a unit the build compiled, since using a poisoned
    name fails plain -E as well. ``__COUNTER__`` and ``-traditional`` fail pass 1 when they would
    diverge (gcc rejects ``__COUNTER__`` in a directive under ``-fdirectives-only``, and
    ``-fdirectives-only`` with ``-traditional``). Every other preprocessor construct expands in
    pass 2 as in plain -E: ``__FILE__``, ``__FILE_NAME__``, ``__LINE__`` and ``__INCLUDE_LEVEL__``
    follow the linemarkers, and directives and pragmas other than these act in pass 1 or only on
    diagnostics."""
    text = record['_pass_one']
    for name in _STDIN_DEPENDENT:
        if name in text:
            return name + ' in the expanded text'
    pushes = {resolve_path('/', key): _PRAGMAS[key] for key in sorted(record['_consumed'] & _PRAGMAS.keys())}
    if not pushes:
        return None
    defined, source, line = set(_DYNAMIC_MACROS), None, 0
    commented = continued = False
    for row in text.splitlines():
        if '#' in row:
            # Linemarkers are the compiler's own rows, outside any comment.
            marker = _MARKER.match(row)
            if marker:
                line = int(marker[1])
                source = marker[2] if marker[2].startswith('<') else resolve_path(record['directory'], marker[2])
                commented = continued = False
                continue
        if '#' in row and not commented and not continued:
            definition = _DEFINITION_ROW.match(row)
            if definition:
                (defined.add if definition[1] == 'define' else defined.discard)(definition[2])
        position = 0
        while commented or '/' in row[position:]:
            if commented:
                end = row.find('*/', position)
                if end < 0:
                    break
                commented, position = False, end + 2
                continue
            token = _COMMENT_STATE.search(row, position)
            if token is None or token[0] == '//':
                break
            commented, position = token[0] == '/*', token.end()
        continued = row.endswith('\\')
        names = pushes.get(source)
        if names is not None:
            for name in names.get(line, ()):
                if name in defined:
                    return f'#pragma push_macro("{name}") of a defined macro in {source}'
        line += 1
    return None


def expand(record, compdb):
    """The expansion of the translation unit, with the contract values read after it.

    Normally pass 2 expands pass 1's output from stdin, with the contract probe after the
    translation unit: the probe declarations see the macros defined at the end of the unit, and a
    probe whose expansion keeps an unresolved name is omitted. Where pass 2 cannot reproduce plain
    -E (``divergence``), the unit is expanded by plain -E instead, which must enter the files of
    pass 1 in the same order and report the same dependency rule; its contract values are then
    unassessed. Returns the expansion, its rows and origins, the contract values, the receipts and
    how the expansion was made."""
    scan, physical = record['_scan'], record['physical']
    reason = divergence(record)
    receipts = []
    if reason is None:
        source = _PASS_TWO_PREFIX + scan['source'] + ('' if scan['source'].endswith('\n') else '\n')
        source += '# 1 "' + _PROBE_FILE + '"\n' + ''.join(
            'static const ' + typ + ' mpy_api_contract_' + name + ' = ' + expression + ';\n'
            for name, (typ, expression) in _CONTRACT_SAMPLES.items())
        output, receipt = invoke(record, _PASS_TWO, source, fail=False)
        receipts.append(receipt)
        if receipt['exit']:
            poisoned = _POISONED.search(receipt['stderr'])
            if poisoned is None:
                raise RuntimeError(f'compiler replay failed for {physical}: {shlex.join(receipt["argv"])}\n{receipt["stderr"]}')
            reason = f'pass 2 used {poisoned[1]}'
    if reason is None:
        first = scan['source'][:scan['source'].find('\n') + 1]
        start = output.find(first)
        if not first or start < 0 or any(row not in ('', '# 1 "<stdin>"') for row in output[:start].split('\n')[:-1]):
            raise RuntimeError(f'compiler expansion lost its first linemarker: {physical}')
        split = output.rfind('\n# 1 "' + _PROBE_FILE + '"')
        if split < start:
            raise RuntimeError(f'compiler expansion lost the contract probe: {physical}')
        expanded = output[start:split + 1]
        found = {}
        for matched in _CONTRACT_PROBE.finditer(output, split):
            found.setdefault(matched[1], matched[2])
        contracts = {}
        for name in _CONTRACT_SAMPLES:
            if name in found:
                value = canonical(found[name])
                if not _CONTRACT_UNRESOLVED.search(value):
                    contracts[name] = value
        method = {'method': 'pass 1 output expanded from stdin'}
    else:
        expanded, receipt = invoke(record, _PLAIN)
        receipts.append(receipt)
        rule = receipt['stderr']
        begin = ('\n' + rule).find('\n' + _DEPENDENCY_TARGET + ':')
        paths = compdb.first_rule(rule[begin:]) if begin >= 0 else None
        if paths is None or {str(Path(record['directory']) / path) for path in paths} != record['_consumed']:
            raise RuntimeError(f'compiler dependency selection changed during collection: {physical}')
        contracts = {}
        method = {'method': 'plain -E', 'reason': reason,
                  'contracts': 'unassessed: the contract probe expands only from pass 1 output'}
    entries = []
    code, origins = unmark(expanded, record['directory'], entries)
    if entries != scan['order']:
        raise RuntimeError(f'compiler expansion entered files outside its dependency pass: {physical}')
    return expanded, code, origins, contracts, receipts, method


def _join(directory, name):
    """The path the compiler forms for ``name`` in the search directory ``directory``."""
    return directory + '/' + name if directory and not directory.endswith('/') else directory + name


def _found(path):
    """The resolved file an include lookup finds at ``path``, or None where it passes over it.

    The compiler passes over a path that does not exist and a directory; any other failure to
    open a path stops the lookup there, so it is reported as a file no resolution equals."""
    try:
        status = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return ''
    return None if stat.S_ISDIR(status.st_mode) else os.path.realpath(path)


def _directory(path):
    """The (device, inode) of the directory at ``path``, or None if it is not a directory."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino) if stat.S_ISDIR(status.st_mode) else None


def observe(path):
    """What ``path`` held when this process first observed it, as ``_found`` reports it."""
    if path not in _OBSERVED:
        _OBSERVED[path] = _found(path)
    return _OBSERVED[path]


def observe_directory(path):
    """The identity of the directory at ``path`` when this process first observed it."""
    if path not in _OBSERVED_DIRECTORIES:
        _OBSERVED_DIRECTORIES[path] = _directory(path)
    return _OBSERVED_DIRECTORIES[path]


def search_directories(directory, order):
    """The paths of every directory of the include search order, kept or left out."""
    return [] if order is None else [os.path.join(directory, entry) for entry in
                                     [*order[0], *order[1], *(entry for entry, _ in order[2])]]


def command_line_definitions(text):
    """The rows pass 1 wrote under a ``<command-line>`` linemarker: the definitions of the -D and
    -U options, whether given directly or in a response file."""
    limit = text.find('"<command-line>" 2\n')
    rows = []
    for marker in _COMMAND_LINE_MARKER.finditer(text, 0, len(text) if limit < 0 else limit):
        end = text.find('\n# ', marker.end() - 1)
        rows.append(text[marker.end():len(text) if end < 0 else end + 1])
    return ''.join(rows)


def probe_paths(directory, scans, consumed, order):
    """Every path the ``__has_include`` probes of ``scans`` (each ``(probes, unmodelled)`` as in
    ``_PROBES``) can test, or why they are not modelled.

    The compiler evaluates a probe in the file whose conditional uses it, which can be any file of
    the unit when the probe is in a macro (a command-line definition included). Each literal probe
    is taken to test its name in the directory of every file in ``consumed``, every directory of
    the search order and every directory left out of it, whether or not its directive was
    evaluated."""
    for _, unmodelled in scans:
        if unmodelled:
            return unmodelled
    if scans and order is None:
        return 'include search order not reported'
    paths = set()
    if not scans:
        return paths
    searched = [entry + '/' for entry in search_directories(directory, order)]
    including = sorted({key[:key.rfind('/') + 1] for key in consumed})
    for probes, _ in scans:
        for _, angled, name in probes:
            if os.path.isabs(name):
                paths.add(name)
                continue
            paths.update(entry + name for entry in searched)
            if not angled:
                paths.update(entry + name for entry in including)
    return paths


def check_selection(record):
    """Replay pass 1's include lookups by stat; return the new constraints and None, or a reason.

    Each lookup is replayed in the include search order pass 1 reported: for a quoted name the
    including file's directory, then the quote chain, then the bracket chain; for an angled name
    the bracket chain; for #include_next the chain after the entry where the including file was
    found; for an absolute name the name alone. The first candidate found must be the file the
    compiler entered or, for an include that entered nothing (a guarded or once-only file), a file
    entered earlier. Then the candidates before it are absent. Where no implicit preinclude
    (<stdc-predef.h>) was entered, none exists in the bracket chain.

    The search order itself is fixed by the identity of each directory it names, observed before
    pass 1 ran: every chain directory is a distinct directory, a directory left out as nonexistent
    or not a directory is no directory, and one left out as a duplicate is the same directory
    (device and inode) as a chain directory. The compiler builds the same order from the same
    identities, so each file is found through the same entry, with the same spelling and system
    flag. Every path a ``__has_include`` probe can test holds what it held when first observed.
    Those facts are the constraints the final verification checks again (``verify_selection``)."""
    directory, scan, order = record['directory'], record['_scan'], record['_search_order']
    flags = clean_flags(record)
    if any(flag in _UNMODELLED_FLAGS or flag.startswith(_UNMODELLED_OPTIONS) for flag in flags):
        return None, 'command-line include option'
    if '-w' in flags:
        return None, 'warnings suppressed (-w), so include directories that are not directories go unreported'
    if isinstance(record['_probes'], str):
        return None, record['_probes']
    if scan['unmodelled']:
        return None, scan['unmodelled'][0]
    if order is None:
        return None, 'include search order not reported'
    quote, bracket, omitted = order
    chain = quote + bracket
    observed = {}
    constraints = []
    identities = set()
    for entry in chain:
        path = os.path.join(directory, entry)
        identity = observe_directory(path)
        if identity is None or identity in identities:
            return None, f'include directory {entry} was not a distinct directory'
        identities.add(identity)
        constraints.append(('directory', path, identity))
    for entry, kind in omitted:
        path = os.path.join(directory, entry)
        identity = observe_directory(path)
        if (identity in identities) if kind == 'duplicate' else (identity is None):
            constraints.append(('directory', path, identity))
        else:
            return None, f'include directory {entry} left out as {kind} was not one'

    def found(path):
        if path not in observed:
            observed[path] = _found(path)
        return observed[path]

    stacked = {resolve_path(directory, record['file'])}
    where_found = {}
    lookups = scan['lookups']
    if not any(lookup[0] == '<command-line>' for lookup in lookups):
        # No implicit preinclude was entered; none may appear in the bracket chain.
        lookups = [*lookups, ['<command-line>', None, 'include', True, 'stdc-predef.h', False]]
    for index, (includer, parent, directive, angled, name, entered) in enumerate(lookups):
        if os.path.isabs(name):
            candidates = [(name, 'absolute')]
        else:
            start = where_found.get(parent) if directive == 'include_next' and parent is not None else None
            if start is None or start == 'absolute':
                candidates = [] if angled else [(_join(includer[:includer.rfind('/') + 1], name), 'includer')]
                start = len(quote) if angled else 0
            else:
                candidates, start = [], 0 if start == 'includer' else start + 1
            candidates.extend((_join(entry, name), position) for position, entry in enumerate(chain[start:], start))
        absent, selected = [], None
        for path, position in candidates:
            path = os.path.join(directory, path)
            resolved = found(path)
            if resolved is None:
                absent.append(path)
            else:
                selected = (path, position, resolved)
                break
        if entered is False:
            if selected is not None:
                return None, 'an implicit preinclude candidate exists'
            constraints.extend(('absent', path, None) for path in absent)
            continue
        if selected is None:
            return None, f'no candidate exists for #{directive} {name} in {includer}'
        path, position, resolved = selected
        if entered is not None:
            if resolved != resolve_path(directory, entered):
                return None, f'#{directive} {name} in {includer} now finds {path}'
            where_found[index] = position
            stacked.add(resolved)
        elif resolved not in stacked:
            return None, f'#{directive} {name} in {includer} finds {path}, which was not entered'
        constraints.extend(('absent', path, None) for path in absent)
        constraints.append(('is', path, resolved))
    constraints.extend(('probe', path, _OBSERVED[path]) for path in sorted(record['_probes']))
    fresh = [constraint for constraint in dict.fromkeys(constraints) if constraint not in _CONSTRAINTS]
    _CONSTRAINTS.update(fresh)
    return fresh, None


def validate_selection(record, compdb):
    """Show pass 1's include selection still holds after expansion.

    The stat replay (``check_selection``) is the check; where it cannot model a lookup, pass 1 runs
    again and must select the same dependencies and the same non-system dependencies."""
    constraints, reason = check_selection(record)
    if reason is None:
        record['selection_check'] = {'method': 'include search order replayed by stat'}
        record['_selection'] = constraints
        return
    text, receipt, paths = _dependency_pass(record, compdb)
    record['receipts'].append(receipt)
    scan = scan_dependency_pass(text, record['file'])
    if ({resolve_path(record['directory'], path) for path in paths} != set(record['dependencies'])
            or project_set(record, compdb, scan, record['receipts']) != record['project_dependencies']):
        raise RuntimeError(f'compiler dependency selection changed after expansion: {record["physical"]}')
    record['selection_check'] = {'method': 'compiler dependency pass repeated', 'reason': reason}
    record['_selection'] = []


def merge_selection(groups, selection=None):
    """Join include selection constraints from every translation unit into one table, ``selection``
    when given."""
    if selection is None:
        selection = {'absent': set(), 'is': {}, 'probe': {}, 'directory': {}}
    for constraints in groups:
        for kind, path, resolved in constraints:
            if kind == 'absent':
                selection['absent'].add(path)
            elif selection[kind].setdefault(path, resolved) != resolved:
                raise RuntimeError(f'compiler include selection changed during collection: {path}')
    return selection


def verify_selection(selection):
    """Require every include lookup to still find what pass 1 found: one stat per path."""
    for path in sorted(selection['absent']):
        if _found(path) is not None:
            raise RuntimeError(f'compiler include selection changed during collection: {path}')
    for path, resolved in sorted(selection['is'].items()):
        if _found(path) != resolved:
            raise RuntimeError(f'compiler include selection changed during collection: {path}')
    for path, identity in sorted(selection['directory'].items()):
        if _directory(path) != identity:
            raise RuntimeError(f'compiler include search directory changed during collection: {path}')
    for path, held in sorted(selection['probe'].items()):
        if _found(path) != held:
            raise RuntimeError(f'compiler include probe changed during collection: {path}')


def verify_collection(verification):
    """The final check before publication: every consumed input, then every include lookup."""
    verify_inputs(verification['inputs'])
    verify_selection(verification['selection'])


def qstr_strings(dependencies, snapshots=None):
    """Read actual generated pool strings, not an encoded-name heuristic."""
    result = {}
    for path in sorted(dependencies):
        if Path(path).name != 'qstrdefs.generated.h':
            continue
        content, snapshot = read_recorded(path)
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
