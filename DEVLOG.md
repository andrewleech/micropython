# MicroPython Ordered Dict Development Log

## Background

This work continues from two stalled PRs attempting to make MicroPython dicts ordered by default (matching CPython 3.7+ behavior):

- **PR #5323** (andrewleech): Added `MICROPY_PY_MAP_ORDERED` compile-time option to force all dicts to use brute-force linear search ordering
- **PR #6173** (dpgeorge): Implemented CPython/PyPy-style compact dict with separate hash table and dense entry array

PR #5323 was rejected due to O(n) lookup performance degradation on large dicts. PR #6173 was started as a better approach but was never completed.

---

## PR #6173 Analysis

### Implementation Overview

Uses the same algorithm as CPython 3.6+/PyPy:

```
Memory layout:
map->table → [key/value pairs (dense)][hash table indices (sparse)]
```

- **Dense array**: Key/value pairs stored in insertion order
- **Sparse hash table**: Contains indices into dense array (uint8_t for <255 elements, uint16_t for <65535)

### Identified Issues

1. **O(n) `len()`**: `mp_map_num_filled_slots()` scans entire array to count non-deleted entries
2. **No compaction**: Deleted entries become tombstones forever; repeated delete+insert grows dict unbounded
3. **Size limit**: Only supports dicts up to 65535 elements
4. **Missing compile-time toggle**: No option to select between implementations

### Comparison with CPython/PyPy

| Aspect | CPython | PyPy | PR #6173 |
|--------|---------|------|----------|
| Index sizing | 1/2/4/8 bytes | Similar | 1/2 bytes only |
| Sentinel values | DKIX_EMPTY=-1, DKIX_DUMMY=-2 | Similar | 0 for empty, SENTINEL in key |
| Deletion | Marks index as DUMMY | Same + compaction | Marks key as SENTINEL |
| `len()` tracking | Separate counter (ma_used) | Same | O(n) scan |
| Compaction | On resize/rehash | Explicit packing | Never |

---

## Fix: O(1) len() Implementation

### Changes Made (2026-01-11)

Added `filled` field to `mp_map_t` to track actual element count separately from `used` (insertion index).

#### py/obj.h

```c
typedef struct _mp_map_t {
    size_t all_keys_are_qstrs : 1;
    size_t is_fixed : 1;
    size_t is_ordered : 1;
    size_t used : (8 * sizeof(size_t) - 3); // next insertion index
    size_t alloc;
    size_t filled;  // NEW: number of non-deleted entries
    mp_map_elem_t *table;
} mp_map_t;
```

- Updated `MP_DEFINE_CONST_MAP` and `MP_DEFINE_CONST_DICT` macros
- Removed `mp_map_num_filled_slots()` declaration

#### py/map.c

- Removed `mp_map_num_filled_slots()` function
- Updated all initialization functions to set `filled`
- Updated `mp_map_lookup()`:
  - Increment `filled` on insert (both ordered and hash table paths)
  - Decrement `filled` on delete (both paths)
- Updated `mp_map_rehash()` to reset `filled = 0`

#### py/objdict.c

- `dict_unary_op`: Use `map->filled` for `len()` and `bool()`
- `dict_binary_op`: Use `filled` for equality size check
- `dict_popitem`: Use `filled` for empty check; handle ordered vs hash map deletion correctly
- `mp_obj_dict_len`: Return `filled`

#### Other Files

Updated to use `filled` instead of `used` where element count is needed:
- `py/modthread.c`
- `py/runtime.c`
- `ports/stm32/pin.c`
- `ports/nrf/modules/machine/pin.c`
- `ports/nrf/modules/ubluepy/ubluepy_scan_entry.c`
- `ports/cc3200/mods/pybpin.c`
- `ports/cc3200/mods/pybsleep.c`

### Memory Impact

+1 `size_t` per `mp_map_t`:
- 32-bit: +4 bytes per map
- 64-bit: +8 bytes per map

### Test Results

```
basics/dict*.py:        19/19 passed
basics/ordereddict*.py:  2/2 passed
stress/dict*.py:         3/3 passed
All basics:            483/484 passed (1 unrelated failure)
```

Manual verification confirms `len()` returns correct count after deletions.

---

## Fix: >65535 Element Support

### Changes Made (2026-01-12)

Added uint32_t hash table indices for dicts larger than 65535 elements.

#### py/mpconfig.h

```c
// Whether to support dicts with >65535 elements (requires uint32_t hash indices)
#ifndef MICROPY_PY_MAP_LARGE
#define MICROPY_PY_MAP_LARGE (1)
#endif
```

#### py/map.c

Updated macros to support three index sizes:

```c
#define MP_MAP_IS_UINT8(alloc) ((alloc) < 255)
#define MP_MAP_IS_UINT16(alloc) ((alloc) < 65535)
#if MICROPY_PY_MAP_LARGE
#define MP_MAP_INDEX_SIZE(alloc) (MP_MAP_IS_UINT8(alloc) ? 1 : (MP_MAP_IS_UINT16(alloc) ? 2 : 4))
#else
#define MP_MAP_INDEX_SIZE(alloc) (MP_MAP_IS_UINT8(alloc) ? 1 : 2)
#endif
```

Updated `mp_map_hash_table_get()` and `mp_map_hash_table_put()` functions with conditional uint32_t paths wrapped in `#if MICROPY_PY_MAP_LARGE`.

Changed `mp_map_hash_table_put()` value parameter from `uint16_t` to `size_t`.

### Memory Impact

- No impact for dicts <255 elements (uint8_t indices)
- No impact for dicts <65535 elements (uint16_t indices)
- +2 bytes per slot for dicts ≥65535 elements (uint32_t vs uint16_t)

Embedded systems with limited RAM are unlikely to need dicts this large anyway.

### Test Results

All 24 dict tests passed.

---

## Fix: Compaction on Delete

### Problem

Hash table maps use tombstones (`MP_OBJ_SENTINEL`) for deletion. Tombstones are only cleared during rehash, which triggers when the table is full during insertion. This causes unbounded memory growth with repeated delete+insert cycles.

### Options Considered

| Option | Approach | Code Size | Delete Perf |
|--------|----------|-----------|-------------|
| A | Threshold-triggered rehash | +50-100 B | O(1) amortized |
| B | Immediate memmove | +150-200 B | O(n) always |
| C | CPython-style fill tracking | +100-150 B | O(1) amortized |
| D | Lazy in-place compaction | +200-250 B | O(1) amortized |

### Choice: Option A (Threshold-Triggered Rehash)

Rationale:
- Smallest code size increase
- Reuses existing rehash mechanism (already skips tombstones)
- Embedded systems rarely do heavy delete workloads
- Tombstone count already available: `used - filled`

### Changes Made (2026-01-13)

After deletion, check if tombstones exceed 50% of live entries. If so, trigger compaction.

#### py/map.c

Added `mp_map_compact()` function that sizes based on `filled` count (not `alloc`), allowing the table to shrink when many entries are deleted.

```c
void mp_map_compact(mp_map_t *map) {
    size_t new_alloc = get_hash_alloc_greater_or_equal_to(map->filled + 1);
    // ... rehash to new_alloc, skipping tombstones
}
```

Updated deletion path to call `mp_map_compact()` instead of `mp_map_rehash()`, and skip when dict becomes empty:

```c
// In mp_map_lookup() after deletion:
if (map->filled > 0 && map->used - map->filled > map->filled / 2) {
    mp_map_compact(map);
}
```

#### py/objdict.c

Added compaction trigger to `popitem()` for non-ordered maps (ordered maps don't use tombstones).

#### py/obj.h

Exposed `mp_map_compact()` for use by objdict.c.

### Memory Impact

Can now shrink allocation when entries are deleted, not just remove tombstones.

### Tests Added

- `tests/basics/dict_compact_order.py` - Verify insertion order preserved after compaction
- `tests/basics/dict_compact_threshold.py` - Test threshold boundary and deletion patterns
- `tests/basics/dict_compact_empty.py` - Empty dict edge cases
- `tests/stress/dict_compact.py` - Stress test for memory growth from repeated add/delete

---

## Fix: Tail Tombstone Reclamation for Heap-Locked Adds

### Problem

Module-level exception handlers fail when heap is locked and prior deletions have created tombstones:
- Python 3 semantics: `except X as e:` deletes `e` after the block ends
- Each deletion creates a tombstone in the dense array
- When `used >= alloc`, adding requires allocation, which fails when heap locked

### Solution (2026-01-14)

Added tail tombstone reclamation with two changes:

#### py/mpconfig.h

```c
// Whether to reclaim tail tombstones when adding to a full dict.
#ifndef MICROPY_PY_MAP_REUSE_TAIL_TOMBSTONE
#define MICROPY_PY_MAP_REUSE_TAIL_TOMBSTONE (1)
#endif
```

#### py/map.c

1. **Stale hash entry detection**: When probing hash table, treat entries pointing beyond `used` as empty:
```c
if (idx != 0 && idx <= map->used) {
    slot = &map->table[idx - 1];
}
```

2. **Tail tombstone reclamation**: When hash probe wraps around (table full), check if last slot is tombstone:
```c
#if MICROPY_PY_MAP_REUSE_TAIL_TOMBSTONE
// If tail slot is tombstone, reclaim it (common in exception handling).
// Decrementing used makes stale hash entry point beyond used, treated as empty.
if (map->used > 0 && map->table[map->used - 1].key == MP_OBJ_SENTINEL) {
    map->used--;
    start_pos = pos = hash % map->alloc;
    continue;
}
#endif
```

### How It Works

In exception handling, the tombstone IS the last entry:
1. `except X as e:` adds `e` at position `used`, increments `used`
2. Block ends, `e` is deleted → tombstone at position `used-1`
3. Next heap-locked add: detect tail tombstone, decrement `used`
4. Hash entry now points beyond `used`, treated as empty
5. New entry added at reclaimed position

### Code Size

~50 bytes with feature enabled, can be disabled via `MICROPY_PY_MAP_REUSE_TAIL_TOMBSTONE=0`.

### Test Results

```
tests/micropython/heapalloc_exc_compressed.py: pass
tests/basics/dict*.py: 22/22 passed
tests/basics/ordereddict*.py: 2/2 passed
tests/stress/dict*.py: 4/4 passed
```

---

## Remaining Work

### Still TODO from PR #6173

1. **Compile-time toggle**: Add `MICROPY_PY_MAP_ORDERED` option to select implementation
2. **OrderedDict simplification**: Once regular dicts are ordered, simplify/remove separate OrderedDict code
3. **Performance profiling**: Benchmark against original implementation

---

## References

- [PyPy compact dict blog post (2015)](https://pypy.org/posts/2015/01/faster-more-memory-efficient-and-more-4096950404745375390.html)
- [CPython compact dict commit](https://github.com/python/cpython/commit/742da040db28e1284615e88874d5c952da80344e)
- [CPython dict internals documentation](https://github.com/zpoint/CPython-Internals/blob/master/BasicObject/dict/dict.md)
- [Python 3.6 dict ordering announcement](https://mail.python.org/pipermail/python-dev/2016-September/146327.html)
