"""Semantics the link stage computes before the code that reads them, in forked processes.

The link stage reads the unit semantics of every bound wrapper function (``semantics.demand_semantics``)
and the linked semantics of the ones whose helper lookups find a linked helper
(``semantics.linked_semantics_result``). Each is a function of the fact objects and the link state alone.
Processes forked once that state is complete compute them as the link stage would, and the link stage
installs the results. The list a caller passes need not be exactly what is read later: what is not read
only costs the processes' time, and what is missing is computed where it is read.

A function whose computation raises in a process, or whose result cannot be returned, is left to the
code that reads it, which computes it again and raises what it raises, at the point it would have.
A result is the value the serial computation returns, so nothing depends on which process computed it
or in what order."""
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

from .facts import Unloaded
from .semantics import computed_unit_semantics, install_unit_semantics, linked_semantics_result

# What the forked processes read: the arguments of the current batch, set before they fork.
_WORK = None


def _unit_task(position):
    store, functions = _WORK
    try:
        return computed_unit_semantics(store, functions[position])
    except Exception:
        return None


def _linked_task(position):
    store, linked_functions, items = _WORK
    facts, function = items[position]
    try:
        return linked_semantics_result(store, facts, function, linked_functions)[0]
    except Exception:
        return None


def _run(task, work, costs, jobs):
    """``task(position)`` for each position of ``costs``, in at most ``jobs`` forked processes that
    start the costliest positions first; the result of each position, or None where its task failed."""
    global _WORK
    _WORK = work
    results = [None] * len(costs)
    try:
        with ProcessPoolExecutor(max_workers=min(jobs, len(costs)),
                                 mp_context=multiprocessing.get_context('fork')) as pool:
            futures = {position: pool.submit(task, position)
                       for position in sorted(range(len(costs)), key=lambda position: (-costs[position], position))}
            for position, future in futures.items():
                try:
                    results[position] = future.result()
                except Exception:
                    pass
    finally:
        _WORK = None
    return results


def _size(function):
    """A cost hint for the function: the lines its source span covers."""
    span = function['source_span']
    return span['end_line'] - span['start_line'] + 1


def prefetch_unit_semantics(store, functions, jobs):
    """Compute the unit semantics of those of the index records ``functions`` that have none yet, in
    up to ``jobs`` forked processes, and install them in ``store``."""
    pending, seen = [], set()
    for function in functions:
        record = store.index_record(function)
        if id(record) not in seen and isinstance(record['frontend_unresolved'], Unloaded):
            seen.add(id(record))
            pending.append(record)
    if jobs < 2 or not pending:
        return
    results = _run(_unit_task, (store, pending), [_size(record) for record in pending], jobs)
    for record, result in zip(pending, results):
        if result is not None:
            install_unit_semantics(store, record, *result)


def prefetch_linked_semantics(store, linked_functions, items, jobs):
    """The linked semantics, in up to ``jobs`` forked processes, of the ``(unit index, index record)``
    pairs ``items``, as a dict by function id; a function whose computation failed is absent."""
    if jobs < 2 or not items:
        return {}
    results = _run(_linked_task, (store, linked_functions, items), [_size(function) for _, function in items], jobs)
    return {function['id']: result for (_, function), result in zip(items, results) if result is not None}
