"""Fact objects: the extraction result of one translation unit, written to one file by its worker.

A fact object is the contract between the per-unit stage and the link stage. Its index section is
what the link stage holds for every unit: the unit's identity, definitions, registrations, errors,
contracts and dependency identities, and for every function its identity fields, a call index (each
call's callee declaration and source span) and its syntax barriers. A worker that computes the unit
semantics of the unit's functions also leaves their frontend-unresolved reasons and helper lookups in
the index; otherwise the semantics and those two fields are ``Unloaded`` until the link stage demands
the function's unit semantics (``semantics.demand_semantics``). The evidence section is everything
else: per function the complete record (events, flow, syntax subtrees, semantics, the parameter
source contract), and per unit the frontend tables, the complete input record and the complete
coverage record. The link stage loads evidence per function, on demand, through a ``FactStore``.

An index record names each field it leaves in the evidence with an ``Unloaded`` value, so reading
such a field from the index fails instead of seeing a default.

File layout: ``_MAGIC``, the sections, the section table and the table's offset as an unsigned
64-bit little-endian integer. Every section and the table is a pickle (protocol 5), zlib compressed
when the fact object's compression level is above 0. Section 0 is the index (the unit index and its
node table), 1 the shared node table, 2 the unit evidence, and 3 onwards one per function in the
order of the index's functions. The bytes of a unit's fact object depend on what ran before it in
the same worker: a compiler receipt replayed from the worker's cache holds its own copies of strings
a fresh run shares with the unit (its directory), which pickle writes out again instead of as
back-references. The decoded content does not depend on it.

Containers (dicts, lists, tuples and ChainViews) reachable from more than one section are stored
once. Those reachable from the index are pickled in it, and its node table lists the ones evidence
refers to; those reachable from two or more evidence sections and not from the index are pickled
once in the shared node table. Every other section refers to both tables by memo position: it is
pickled with the pickler's memo preset to the node table followed by the shared node table, and
loaded by an unpickler that first reads a primer, a pickle storing the table entry ``k`` (given by
persistent id) at memo position ``k`` for every entry. A loaded function therefore shares every such
object with the index and with other loaded evidence exactly as the worker's facts did. (Assigning
the unpickler's memo does not set the count from which protocol 4 memoization numbers new entries,
so the memo is filled by reading a primer instead.)"""
import gc
import hashlib
import io
import pickle
import struct
import zlib
from pathlib import Path

from .chains import ChainView

_MAGIC = b'MPYFACT1'
_PROTOCOL = 5
_INDEX, _SHARED, _UNIT = 0, 1, 2
_FUNCTIONS = 3
# The zlib level of fact objects written by extraction, chosen on measured write CPU against size.
COMPRESSION = 1

# Function fields the index keeps as they are, besides the derived call index and evidence position.
FUNCTION_INDEX = ('id', 'name', 'linkage', 'source_span', 'declaration_identity', 'translation_unit',
                  'parameters', 'handler_attributes', 'semantic_helper_lookups')
# Unit fields the index keeps whole; ``input`` and ``coverage`` keep the fields named below, and
# every other unit field is evidence.
UNIT_INDEX = ('translation_unit', 'source', 'definitions', 'registrations', 'errors', 'contracts')
INPUT_INDEX = ('object', 'dependencies', 'compiler', 'compiler_sha256', 'compiler_expanded_sha256')
COVERAGE_EVIDENCE = ('project_headers', 'unsupported_definitions')


class Unloaded:
    """The value an index record holds for a field kept only in the evidence section.

    Using it as a value raises: truth, length, iteration, membership, item and attribute access,
    ``==``/``!=``, hashing, JSON encoding and copying. ``repr``/``str``, ``isinstance``, ``is`` and
    pickling succeed. Every field gets its own instance, so comparing two distinct records or
    containers that hold one raises too; only a container compared with itself, or with another
    holding the same instance, compares equal without asking it, as container comparison checks
    identity first."""
    __slots__ = ('field',)

    def __init__(self, field):
        self.field = field

    def _missing(self, *args, **kwargs):
        raise RuntimeError(f'fact field {self.field!r} is evidence; load it from the fact object')

    __getitem__ = get = __iter__ = __len__ = __bool__ = __contains__ = __eq__ = _missing
    keys = items = values = _missing
    __hash__ = None

    def __getattr__(self, name):
        self._missing()

    def __reduce__(self):
        return Unloaded, (self.field,)

    def __repr__(self):
        return f'Unloaded({self.field!r})'


def unloaded(field):
    """A new ``Unloaded`` value for ``field``."""
    return Unloaded(field)


def _function_index(function, position):
    record = {key: function[key] for key in FUNCTION_INDEX}
    events = function['events']
    record['call_index'] = [{'callee': call.get('callee'), 'source_span': call['source_span']}
                            for call in events['calls']]
    record['semantic_unassessed'] = events.get('semantic_unassessed', [])
    semantics = function['semantics']
    record['frontend_unresolved'] = (unloaded('frontend_unresolved') if isinstance(semantics, Unloaded)
                                     else semantics.get('frontend_unresolved', []))
    record['evidence'] = position
    for key in function:
        if key not in record:
            record[key] = unloaded(key)
    return record


def unit_index(facts):
    """The unit index of ``facts`` and its unit evidence."""
    index = {key: facts[key] for key in UNIT_INDEX if key in facts}
    index['functions'] = [_function_index(function, position) for position, function in enumerate(facts['functions'])]
    index['input'] = {key: value if key in INPUT_INDEX else unloaded(key) for key, value in facts['input'].items()}
    index['coverage'] = {key: unloaded(key) if key in COVERAGE_EVIDENCE else value
                         for key, value in facts['coverage'].items()}
    for key in facts:
        if key not in index:
            index[key] = unloaded(key)
    evidence = {key: value for key, value in facts.items() if key not in UNIT_INDEX and key != 'functions'}
    return index, evidence


_TRAVERSED = frozenset((dict, list, tuple, ChainView))
_PERMITTED = frozenset((str, int, float, bool, type(None), bytes, object, Unloaded, type(ChainView))) | _TRAVERSED


def _reachable(root, stop):
    """The containers reachable from ``root`` without passing through one in ``stop`` (both by id),
    and the ones of ``stop`` that are reached."""
    reached, met = {}, {}
    frontier = [root]
    if id(root) in stop:
        met[id(root)] = root
        frontier = []
    else:
        reached[id(root)] = root
    while frontier:
        referents = gc.get_referents(*frontier)
        kinds = set(map(type, referents))
        if not kinds <= _PERMITTED or type(ChainView) in kinds and any(
                type(item) is type(ChainView) and item is not ChainView for item in referents):
            raise TypeError('fact objects hold only plain data, lists, dicts, tuples and ChainViews, not '
                            + ', '.join(sorted(kind.__name__ for kind in kinds - _PERMITTED) or ['a class']))
        frontier = []
        for identity, item in {id(item): item for item in referents if type(item) in _TRAVERSED}.items():
            if identity in reached:
                continue
            if identity in stop:
                met[identity] = item
                continue
            reached[identity] = item
            frontier.append(item)
    return reached, met


def _layout(index, sections):
    """The node table and the shared node table for ``index`` and the evidence ``sections``."""
    in_index, _ = _reachable(index, {})
    referenced, first, shared = {}, {}, {}
    for section, root in enumerate(sections):
        reached, met = _reachable(root, in_index)
        referenced.update(met)
        for identity, item in reached.items():
            if first.setdefault(identity, section) != section:
                shared[identity] = item
    return list(referenced.values()), list(shared.values())


def _dump(value, memo, level):
    stream = io.BytesIO()
    pickler = pickle.Pickler(stream, protocol=_PROTOCOL)
    if memo:
        pickler.memo = memo
    pickler.dump(value)
    data = stream.getvalue()
    return zlib.compress(data, level) if level else data


def write(path, facts, level=COMPRESSION):
    """Write the fact object of ``facts`` to ``path``.

    Returns the unit index, its node table and the object's location: its path, size, sha256, zlib
    level and section table (offset, length and sha256 of every section)."""
    index, evidence = unit_index(facts)
    sections = [evidence, *facts['functions']]
    nodes, shared = _layout(index, sections)
    memo = {id(node): (position, node) for position, node in enumerate(nodes)}
    blobs = [_dump((index, nodes), None, level), _dump(shared, memo, level)]
    memo.update((id(node), (len(nodes) + position, node)) for position, node in enumerate(shared))
    blobs.extend(_dump(section, memo, level) for section in sections)
    digest = hashlib.sha256()
    table, offset = [], len(_MAGIC)
    with open(path, 'xb') as stream:
        stream.write(_MAGIC)
        digest.update(_MAGIC)
        for blob in blobs:
            stream.write(blob)
            digest.update(blob)
            table.append((offset, len(blob), hashlib.sha256(blob).hexdigest()))
            offset += len(blob)
        trailer = _dump({'compression': level, 'sections': table}, None, level) + struct.pack('<Q', offset)
        stream.write(trailer)
        digest.update(trailer)
    location = {'path': str(path), 'bytes': offset + len(trailer), 'sha256': digest.hexdigest(),
                'compression': level, 'sections': table}
    return index, nodes, location


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _primer(count):
    """A pickle that stores persistent id ``k`` at memo position ``k`` for every ``k`` below ``count``."""
    return (b'\x80\x05' + b''.join(b'J' + struct.pack('<i', position) + b'Q\x940' for position in range(count))
            + b'N.')


class _Section(pickle.Unpickler):
    """Loads one section after its unit's primer, which presets the memo to ``table``."""

    def __init__(self, primer, data, table):
        super().__init__(io.BytesIO(primer + data))
        self._table = table

    def persistent_load(self, position):
        return self._table[position]

    def section(self):
        self.load()
        return self.load()


class _Unit:
    __slots__ = ('location', 'nodes', 'table', 'primer', 'evidence', 'index', 'records')

    def __init__(self, location, nodes, index):
        self.location, self.nodes, self.evidence, self.index = location, nodes, None, index
        self.table = self.primer = self.records = None


class FactStore:
    """The fact objects of one run, read by the link stage.

    Holds the index, location and node table of every unit. ``function`` and ``unit_evidence`` load
    and keep what composition reads, so every reader of one function or unit gets the same objects;
    the ``peek`` variants give the kept value when there is one and otherwise load without keeping.
    Every section read is checked against the sha256 its worker recorded, and ``verify`` checks
    every whole file."""

    def __init__(self, directory, compression=COMPRESSION):
        self.directory = Path(directory)
        self.compression = compression
        self._units = {}
        self._functions = {}
        # Sections loaded by kind, bytes read, and how composition obtained each selected function's
        # linked semantics (``inventory._normal_surface``); ``unit_semantics_demanded`` counts the
        # unit semantics the link stage computed itself (``semantics.demand_semantics``), and the
        # ``*_prefetched`` counts those of them, and the linked runs, that processes forked by the link
        # stage computed before they were read (``prefetch``).
        self.counts = {'function': 0, 'unit': 0, 'shared': 0, 'bytes': 0, 'unit_semantics_demanded': 0,
                       'unit_semantics_reused': 0, 'linked_semantics_runs': 0,
                       'unit_semantics_prefetched': 0, 'linked_semantics_prefetched': 0}
        # The index records whose unit semantics were prefetched and not yet read.
        self.prefetched = set()

    def path(self, ordinal):
        """The path of the fact object for the unit at ``ordinal`` in record order."""
        return self.directory / f'{ordinal:06d}.facts'

    def add(self, index, nodes, location):
        identity = index['translation_unit']
        if identity in self._units:
            raise RuntimeError(f'repeated fact object for translation unit {identity}')
        self._units[identity] = _Unit(location, nodes, index)

    def unit_index(self, translation_unit):
        """The index of the unit ``translation_unit``."""
        return self._units[translation_unit].index

    def index_record(self, function):
        """The index record of the function ``function`` (an index record or a complete record) of
        its unit."""
        unit = self._units[function['translation_unit']]
        if unit.records is None:
            unit.records = {}
            for record in unit.index['functions']:
                if unit.records.setdefault(record['id'], record) is not record:
                    raise RuntimeError(f'repeated function identity {record["id"]}')
        return unit.records[function['id']]

    def _read(self, unit, section):
        offset, length, expected = unit.location['sections'][section]
        with open(unit.location['path'], 'rb') as stream:
            stream.seek(offset)
            data = stream.read(length)
        if len(data) != length or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f'fact object changed during generation: {unit.location["path"]}')
        self.counts['bytes'] += length
        return zlib.decompress(data) if unit.location['compression'] else data

    def _load(self, unit, section):
        if unit.table is None:
            self.counts['shared'] += 1
            shared = _Section(_primer(len(unit.nodes)), self._read(unit, _SHARED), unit.nodes).section()
            unit.table = [*unit.nodes, *shared]
            unit.primer = _primer(len(unit.table))
        return _Section(unit.primer, self._read(unit, section), unit.table).section()

    def function(self, record):
        """The complete record of the index function ``record``, kept for the run."""
        key = (record['translation_unit'], record['evidence'])
        function = self._functions.get(key)
        if function is None:
            self.counts['function'] += 1
            function = self._functions[key] = self._load(self._units[record['translation_unit']],
                                                          _FUNCTIONS + record['evidence'])
        return function

    def peek_function(self, record):
        """The complete record of ``record``: the kept one, else loaded without keeping it."""
        function = self._functions.get((record['translation_unit'], record['evidence']))
        if function is not None:
            return function
        self.counts['function'] += 1
        return self._load(self._units[record['translation_unit']], _FUNCTIONS + record['evidence'])

    def unit_evidence(self, facts):
        """The unit evidence (frontend, input, coverage) of the unit index ``facts``, kept for the run."""
        unit = self._units[facts['translation_unit']]
        if unit.evidence is None:
            self.counts['unit'] += 1
            unit.evidence = self._load(unit, _UNIT)
        return unit.evidence

    def peek_unit_evidence(self, facts):
        """The unit evidence of ``facts``: the kept one, else loaded without keeping it."""
        unit = self._units[facts['translation_unit']]
        if unit.evidence is not None:
            return unit.evidence
        self.counts['unit'] += 1
        return self._load(unit, _UNIT)

    def frontend(self, facts):
        """The frontend tables of the unit index ``facts``; empty for a unit without them."""
        return self.unit_evidence(facts).get('frontend', {})

    def verify(self):
        """Require every fact object to hold what its worker wrote."""
        for unit in self._units.values():
            if file_sha256(unit.location['path']) != unit.location['sha256']:
                raise RuntimeError(f'fact object changed during generation: {unit.location["path"]}')

    def report(self):
        """Sizes and load counts of the run's fact objects."""
        return {'units': len(self._units), 'bytes': sum(unit.location['bytes'] for unit in self._units.values()),
                'compression': self.compression,
                'counts': {**self.counts, 'unit_semantics_prefetch_unread': len(self.prefetched)}}


class UnitFunctions:
    """The functions of one unit by name, as ``semantics`` looks up same-unit helpers: each lookup
    gives the complete records of the unit's functions of that name, loaded from the store."""
    __slots__ = ('store', 'by_name')

    def __init__(self, store, records):
        self.store = store
        self.by_name = {}
        for record in records:
            self.by_name.setdefault(record['name'], []).append(record)

    def get(self, name, default):
        records = self.by_name.get(name)
        return [self.store.function(record) for record in records] if records else default


class LinkedFunctions(dict):
    """Linked function rows by name; each row's ``function`` is an index record, and ``function``
    gives its complete record from the run's fact objects."""

    def __init__(self, store):
        super().__init__()
        self.store = store

    def function(self, record):
        return self.store.function(record)


class SemanticsReference:
    """The unit semantics of an index function, where a raw binding records it.

    Composition reads only the function it names; the inventory document writes the semantics loaded
    from the fact object in its place. It is not JSON, so it cannot be published unresolved."""
    __slots__ = ('function',)

    def __init__(self, function):
        self.function = function
