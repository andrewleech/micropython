# Net_Buf Allocation Crash Fix

## Problem

Firmware crashed with HardFault at address `0x00000010` when attempting BLE initialization on RP2 Pico W. The crash occurred in `lib/zephyr/lib/net_buf/buf.c:234` when trying to call the function pointer `pool->alloc->cb->alloc`.

### Stack Trace
```
#0  HardFault_Handler
#1  <signal handler called>
#2  0x00000010 in ?? ()  ← Invalid function pointer
#3  data_alloc (buf.c:234) at pool->alloc->cb->alloc()
#4  net_buf_alloc_len()
#7  bt_hci_cmd_alloc()  ← First HCI command buffer allocation
#11 bt_init()
```

### Register State at Crash
```
r3 = 0x10  (invalid function pointer, should be ~0x1004bd45)
```

## Root Cause

**Type mismatch** between what `net_buf_pool_registry.c` provided and what Zephyr expected:

### The Problem

1. **Zephyr's design**: Uses `STRUCT_SECTION_ITERABLE` macro to place `net_buf_pool` structures in named linker sections (e.g., `._net_buf_pool.static.*`). Runtime code uses `TYPE_SECTION_START(net_buf_pool)` and `TYPE_SECTION_END(net_buf_pool)` to iterate over pools as a contiguous array.

2. **MicroPython build flags**: Uses `-fdata-sections` to split data objects into individual sections for size optimization. This prevents Zephyr's section collection mechanism from working.

3. **Attempted workaround**: `net_buf_pool_registry.c` defined:
   ```c
   struct net_buf_pool **_net_buf_pool_list_start = registered_pools;
   ```
   But Zephyr expected:
   ```c
   extern struct net_buf_pool _net_buf_pool_list_start[];
   ```

4. **Type mismatch consequences**:
   - Registry provided: `struct net_buf_pool **` (pointer to array of pointers)
   - Zephyr expected: `struct net_buf_pool[]` (array of structures)
   - When Zephyr indexed `_net_buf_pool_list_start[id]`, it read garbage memory
   - Dereferencing `pool->alloc->cb->alloc` chain through garbage pointers produced `0x10`

### Why This Caused HardFault at 0x10

When `net_buf_pool_get(id)` calculated a pool address by indexing into what it thought was an array of structures, it was actually reading bytes from a pointer value. The value `0x10` (16 decimal) likely came from:
- Part of a RAM address (e.g., reading 2 bytes from `0x2001xxxx` pointer)
- An offset into an unrelated structure
- Uninitialized or corrupted memory

## Solution

### Changes Made

**1. Disable `-fdata-sections` for Zephyr BLE sources (`extmod/zephyr_ble/zephyr_ble.cmake`)**

```cmake
# CRITICAL FIX: Disable -fdata-sections for Zephyr sources using STRUCT_SECTION_ITERABLE
set_source_files_properties(
    ${ZEPHYR_LIB_DIR}/lib/net_buf/buf.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/hci_core.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/buf.c
    PROPERTIES COMPILE_FLAGS "-fno-data-sections"
)
```

This allows `NET_BUF_POOL_FIXED_DEFINE` macro to place pools in the correct section.

**2. Add linker section symbols (`ports/rp2/memmap_mp_rp2040.ld`)**

```ld
/* Zephyr BLE net_buf pools - must be in copied data section */
_net_buf_pool_list_start = .;
KEEP(*(SORT(._net_buf_pool.static.*)))
_net_buf_pool_list_end = .;
. = ALIGN(4);
```

This collects all pools into a contiguous array and provides the symbols Zephyr expects.

**3. Remove conflicting definitions (`extmod/zephyr_ble/net_buf_pool_registry.c`)**

Removed the wrong-type definitions of `_net_buf_pool_list_start` and `_net_buf_pool_list_end` since the linker script now provides them correctly.

## Verification

### Before Fix
```
Creating BLE object...
BLE_NEW: enter, bluetooth=   ← Crashed here
```

No checkpoints printed. Device in ARM M lockup state. GDB showed HardFault at 0x10.

### After Fix
```
Creating BLE object...
BLE_NEW: enter, bluetooth=0
BLE_NEW: allocating object
BLE_NEW: object at 20016f30
BLE_NEW: returning 20016f30
Activating BLE...
CHECKPOINT 1: bt_init() entered
CHECKPOINT 2: Calling hci_init()...
[FIFO] k_fifo_put(200014ec, 20014fa8)
[FIFO] k_fifo_get(200014ec, timeout=0)
```

✅ Checkpoints printing
✅ Net_buf allocation working
✅ No HardFault
⏸️ Hangs in hci_init() (different issue - work queue processing)

## Technical Details

### Zephyr's STRUCT_SECTION_ITERABLE Pattern

```c
// In header - declares pool and places it in named section
NET_BUF_POOL_FIXED_DEFINE(hci_cmd_pool, 2, 72, 0, NULL);

// Expands to:
static STRUCT_SECTION_ITERABLE(net_buf_pool, hci_cmd_pool) = { ... };

// Which expands to:
__attribute__((section("._net_buf_pool.static.hci_cmd_pool")))
static struct net_buf_pool hci_cmd_pool = { ... };
```

At runtime:
```c
struct net_buf_pool *net_buf_pool_get(int id) {
    return &TYPE_SECTION_START(net_buf_pool)[id];
}

// TYPE_SECTION_START expands to:
extern struct net_buf_pool _net_buf_pool_list_start[];
return &_net_buf_pool_list_start[id];
```

The linker collects all `._net_buf_pool.static.*` sections into a contiguous array and defines the start/end symbols.

### Why -fdata-sections Broke This

With `-fdata-sections`:
- GCC places `hci_cmd_pool` in `.data.hci_cmd_pool` (NOT the custom section)
- Linker script's `*(._net_buf_pool.static.*)` matches nothing
- `_net_buf_pool_list_start` and `_net_buf_pool_list_end` are equal (empty array)
- Runtime indexing reads garbage

Without `-fdata-sections`:
- GCC places `hci_cmd_pool` in `._net_buf_pool.static.hci_cmd_pool` as intended
- Linker collects pools into contiguous array
- Symbols point to valid array
- Runtime indexing works correctly

## Impact

- **Binary size**: Slightly larger (pools can't be individually optimized out)
- **Safety**: CRITICAL - prevents crash on first BLE operation
- **Performance**: No impact (section iteration is initialization-time only)

## Related Issues

- Issue in session: RP2 Pico W hang during BLE init (work queue processing - separate issue)
- This fix is prerequisite for any BLE functionality on RP2 with Zephyr stack

## References

- Zephyr docs: https://docs.zephyrproject.org/latest/kernel/iterable_sections/index.html
- GCC `-fdata-sections`: https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html
- Code review: Agent acc08c9 analysis (principal-code-reviewer)

---

**Commit**: [To be added when committed]
**Date**: 2025-12-22
**Author**: Claude Code (with user guidance)
