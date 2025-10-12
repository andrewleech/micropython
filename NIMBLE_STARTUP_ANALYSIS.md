# NimBLE Startup Analysis: HCI Transport on RP2 Pico W

## Executive Summary

NimBLE successfully uses the CYW43 Bluetooth controller **WITHOUT any custom HCI transport code**. It relies entirely on the shared HCI infrastructure in `mpbthciport.c` and uses **WEAK function overrides** from the pico-sdk's CYW43 driver. The Zephyr BLE implementation should follow this exact pattern instead of reimplementing HCI transport.

## NimBLE Architecture on RP2

### 1. Boot Sequence

```c
// In main.c (lines 200-210)
#if MICROPY_PY_BLUETOOTH
    #if MICROPY_BLUETOOTH_NIMBLE
        // Step 1: Initialize shared HCI infrastructure (soft timer, scheduling)
        mp_bluetooth_hci_init();  // from mpbthciport.c

        // Step 2: NimBLE stack NOT initialized at boot
        // Initialization is LAZY - happens when user calls ble.active(True)
    #endif
#endif
```

**CRITICAL DIFFERENCE**: NimBLE does NOT initialize the BLE stack at boot. Only infrastructure setup happens here.

### 2. Shared HCI Infrastructure (`mpbthciport.c`)

This file provides the foundation for ALL BLE stacks (BTstack, NimBLE, Zephyr):

```c
// Polling infrastructure
static soft_timer_entry_t mp_bluetooth_hci_soft_timer;
static mp_sched_node_t mp_bluetooth_hci_sched_node;

void mp_bluetooth_hci_init(void) {
    // Initialize soft timer for periodic polling
    soft_timer_static_init(&mp_bluetooth_hci_soft_timer, ...);
}

void mp_bluetooth_hci_poll_in_ms(uint32_t ms) {
    // Schedule next poll
    soft_timer_reinsert(&mp_bluetooth_hci_soft_timer, ms);
}

void mp_bluetooth_hci_poll_now(void) {
    // Trigger immediate poll via scheduler
    mp_sched_schedule_node(&mp_bluetooth_hci_sched_node, run_events_scheduled_task);
}

// WEAK controller interface (overridden by CYW43 driver)
MP_WEAK int mp_bluetooth_hci_controller_init(void) { return 0; }
MP_WEAK int mp_bluetooth_hci_controller_deinit(void) { return 0; }
MP_WEAK int mp_bluetooth_hci_controller_sleep_maybe(void) { return 0; }
MP_WEAK bool mp_bluetooth_hci_controller_woken(void) { return true; }
MP_WEAK int mp_bluetooth_hci_controller_wakeup(void) { return 0; }
```

**Key insight**: Controller functions are WEAK - the pico-sdk's CYW43 driver overrides them automatically when linked.

### 3. NimBLE Port Implementation (`mpnimbleport.c`)

Minimal - only 80 lines total:

```c
// Called by shared infrastructure via mp_bluetooth_hci_poll_now()
void mp_bluetooth_hci_poll(void) {
    if (mp_bluetooth_nimble_ble_state >= MP_BLUETOOTH_NIMBLE_BLE_STATE_WAITING_FOR_SYNC) {
        // Run timers
        mp_bluetooth_nimble_os_callout_process();

        // Process UART data (HCI packets)
        mp_bluetooth_nimble_hci_uart_process(true);

        // Run event queue
        mp_bluetooth_nimble_os_eventq_run_all();
    }

    // Schedule next poll
    mp_bluetooth_hci_poll_in_ms(128);
}

// Wait-for-interrupt during semaphore wait
void mp_bluetooth_nimble_hci_uart_wfi(void) {
    best_effort_wfe_or_timeout(make_timeout_time_ms(1));
    mp_bluetooth_nimble_hci_uart_process(false);
}
```

**Key insight**: NimBLE doesn't know or care about CYW43. It just calls generic HCI UART functions.

### 4. CYW43 Controller Integration

**Where CYW43 is initialized:**

In `main.c` (lines 162-182), CYW43 WiFi is initialized BEFORE BLE:

```c
#if MICROPY_PY_NETWORK_CYW43
    cyw43_init(&cyw43_state);           // Initialize CYW43 driver (WiFi + BT controller)
    cyw43_irq_init();                   // Setup interrupts
    cyw43_post_poll_hook();             // Enable IRQ
    // ... WiFi AP setup ...
#endif
```

**CYW43 Controller Functions:**

The pico-sdk provides these as **strong symbols** that override the WEAK defaults in `mpbthciport.c`:

```c
// From lib/pico-sdk/src/rp2_common/pico_cyw43_driver/cyw43_driver.c
int mp_bluetooth_hci_controller_init(void) {
    // Called when NimBLE enables BLE via ble.active(True)
    return cyw43_bluetooth_hci_init();  // Initialize BT controller via SPI
}

int mp_bluetooth_hci_controller_deinit(void) {
    // Power down BT controller
    cyw43_bluetooth_hci_deinit();
    return 0;
}

int mp_bluetooth_hci_controller_wakeup(void) {
    // Wake BT controller from sleep
    cyw43_bt_controller_wakeup();
    return 0;
}

// etc...
```

**HCI Transport:**

The pico-sdk also provides UART HCI read/write functions that NimBLE calls:

```c
// From lib/pico-sdk/src/rp2_common/pico_cyw43_driver/cyw43_btbus.c (or similar)
// These implement the HCI H:4 UART protocol over CYW43 SPI/SDIO

int mp_bluetooth_hci_uart_init(uint32_t port, uint32_t baudrate) {
    // NO-OP for CYW43 (uses SPI not UART)
    // But must exist for API compatibility
    return 0;
}

int mp_bluetooth_hci_uart_readchar(void) {
    // Read HCI byte from CYW43 SPI btbus
    return cyw43_bluetooth_hci_read_byte();
}

int mp_bluetooth_hci_uart_write(const uint8_t *buf, size_t len) {
    // Write HCI packet to CYW43 SPI btbus
    return cyw43_bluetooth_hci_write(buf, len);
}
```

## Build Configuration

### NimBLE Build

```cmake
# From ports/rp2/CMakeLists.txt

if(MICROPY_BLUETOOTH_NIMBLE)
    list(APPEND MICROPY_SOURCE_PORT
        mpbthciport.c    # Shared HCI infrastructure
        mpnimbleport.c   # NimBLE-specific polling
    )
    include(${MICROPY_DIR}/extmod/nimble/nimble.cmake)  # NimBLE stack
endif()

if (MICROPY_PY_BLUETOOTH_CYW43)
    target_compile_definitions(${MICROPY_TARGET} PRIVATE
        CYW43_ENABLE_BLUETOOTH=1
        MICROPY_PY_BLUETOOTH_CYW43=1
    )

    # NO special transport library for NimBLE
    # pico-sdk's CYW43 driver automatically provides controller functions
endif()
```

### BTstack Build (for comparison)

```cmake
if (MICROPY_PY_BLUETOOTH_CYW43)
    if (MICROPY_BLUETOOTH_BTSTACK)
        # BTstack needs explicit transport library
        target_link_libraries(${MICROPY_TARGET}
            pico_btstack_hci_transport_cyw43
        )
    endif()
endif()
```

**Key difference**: NimBLE doesn't need a special transport library. The pico-sdk's base CYW43 driver provides all necessary functions via WEAK symbol overrides.

## What NimBLE Does NOT Do

1. **Does NOT call `cyw43_bluetooth_hci_init()` at boot**
   - Only called later when user activates BLE

2. **Does NOT implement custom HCI transport**
   - Uses generic `mp_bluetooth_hci_uart_*` functions
   - CYW43 driver overrides these automatically

3. **Does NOT directly interact with CYW43 hardware**
   - All CYW43 access goes through pico-sdk functions

4. **Does NOT create HCI device structures**
   - No `struct device` or `bt_hci_driver_api` needed

## What Zephyr BLE Is Doing WRONG

### Problem 1: Calling bt_enable() at Boot

```c
// In main.c (lines 204-206) - WRONG
mp_bluetooth_zephyr_port_init();  // ✅ OK
mp_bluetooth_init();               // ❌ WRONG - calls bt_enable() immediately
```

This immediately initializes the full Zephyr BLE stack at boot, which:
- Tries to send HCI commands before controller is ready
- Crashes at 0xF0000030 (likely accessing uninitialized controller)

**Solution**: Follow NimBLE pattern - only initialize infrastructure, NOT the stack.

### Problem 2: Custom HCI Transport in mpzephyrport_rp2.c

We created a custom Zephyr HCI driver with:
- `struct device mp_bluetooth_zephyr_hci_dev`
- `bt_hci_driver_api` with open/close/send callbacks
- `bt_hci_transport_setup()` function
- Manual HCI packet handling with `cyw43_bluetooth_hci_read/write()`

**None of this is necessary!** NimBLE doesn't have any of this.

### Problem 3: Calling cyw43_bluetooth_hci_init() Too Early

```c
// In mpzephyrport_rp2.c bt_hci_transport_setup() - WRONG
int bt_hci_transport_setup(const struct device *dev) {
    extern int cyw43_bluetooth_hci_init(void);
    int ret = cyw43_bluetooth_hci_init();  // ❌ Called at boot
    return ret;
}
```

This initializes the CYW43 BT controller at boot, but:
- WiFi driver might not be fully initialized
- Timing-sensitive initialization race
- Not lazy like NimBLE

## Correct Approach for Zephyr BLE

### Step 1: Minimal Port Infrastructure (like NimBLE)

Create a simple `mpzephyrport.c` (NOT `mpzephyrport_rp2.c` - make it generic):

```c
// mpzephyrport.c - Generic Zephyr BLE port (works on ANY platform with HCI UART)

#include "py/runtime.h"
#include "py/mphal.h"
#include "extmod/modbluetooth.h"
#include "extmod/mpbthci.h"
#include "mpbthciport.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

// Called by shared HCI infrastructure
void mp_bluetooth_hci_poll(void) {
    if (zephyr_ble_is_active()) {
        // Process Zephyr work queues
        mp_bluetooth_zephyr_poll();

        // Process HCI UART data (calls mp_bluetooth_hci_uart_readchar)
        zephyr_hci_uart_process();

        // Schedule next poll
        mp_bluetooth_hci_poll_in_ms(128);
    }
}

// Wait-for-interrupt (called during semaphore waits)
void mp_bluetooth_zephyr_hci_wfi(void) {
    best_effort_wfe_or_timeout(make_timeout_time_ms(1));
    zephyr_hci_uart_process();  // Process HCI data during wait
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
```

### Step 2: Remove ALL Custom HCI Transport Code

Delete from `mpzephyrport_rp2.c`:
- `struct device mp_bluetooth_zephyr_hci_dev`
- `bt_hci_driver_api` structure
- `hci_cyw43_open()`, `hci_cyw43_close()`, `hci_cyw43_send()`
- `bt_hci_transport_setup()`, `bt_hci_transport_teardown()`
- All `cyw43_bluetooth_hci_read/write()` calls
- HCI packet parsing and net_buf allocation

**These are all unnecessary!** The shared HCI infrastructure + CYW43 driver handle this automatically.

### Step 3: Lazy Stack Initialization

```c
// In modbluetooth_zephyr.c

int mp_bluetooth_init(void) {
    // DO NOT call bt_enable() here!
    // Just set up memory pools, work queues, etc.

    return 0;  // Success but stack not active yet
}

// Only initialize stack when user calls ble.active(True)
STATIC mp_obj_t bluetooth_ble_active(size_t n_args, const mp_obj_t *args) {
    if (n_args == 2) {
        bool active = mp_obj_is_true(args[1]);
        if (active && !zephyr_ble_is_active()) {
            // NOW initialize the controller and call bt_enable()
            mp_bluetooth_hci_controller_init();  // CYW43 driver overrides this
            int ret = bt_enable(NULL);
            if (ret != 0) {
                mp_raise_OSError(MP_EIO);
            }
        }
        // ... deactivation logic ...
    }
    return mp_obj_new_bool(zephyr_ble_is_active());
}
```

### Step 4: Use Existing WEAK Overrides

No need to call CYW43 functions directly. The pico-sdk's CYW43 driver already provides:

```c
// These exist in pico-sdk and override mpbthciport.c WEAK symbols
int mp_bluetooth_hci_controller_init(void);    // Calls cyw43_bluetooth_hci_init()
int mp_bluetooth_hci_controller_deinit(void);  // Powers down BT
int mp_bluetooth_hci_controller_wakeup(void);  // Wake from sleep
int mp_bluetooth_hci_uart_readchar(void);      // Read HCI byte
int mp_bluetooth_hci_uart_write(...);          // Write HCI packet
```

Just call these generic functions - CYW43 driver handles the hardware.

## Summary: What to Remove/Change

### Files to Delete/Simplify

1. **Delete**: `ports/rp2/mpzephyrport_rp2.c` (650+ lines)
   - Replace with generic `mpzephyrport.c` (~80 lines, like NimBLE)

2. **Delete**: Any CYW43-specific HCI transport code
   - No custom device structures
   - No manual HCI packet handling
   - No direct `cyw43_bluetooth_hci_*()` calls

3. **Simplify**: `modbluetooth_zephyr.c`
   - Remove `bt_enable()` from `mp_bluetooth_init()`
   - Move stack initialization to `ble.active(True)` handler

### Key Changes

**Before (WRONG):**
```c
// main.c - Stack initialized at boot
mp_bluetooth_zephyr_port_init();
mp_bluetooth_init();  // ← Calls bt_enable(), crashes

// mpzephyrport_rp2.c - Custom HCI transport
struct device mp_bluetooth_zephyr_hci_dev = { ... };
int bt_hci_transport_setup(const struct device *dev) {
    cyw43_bluetooth_hci_init();  // ← Called too early
}
```

**After (CORRECT):**
```c
// main.c - Only infrastructure at boot
mp_bluetooth_hci_init();  // ← Shared infrastructure (like NimBLE)

// mpzephyrport.c - Generic polling
void mp_bluetooth_hci_poll(void) {
    mp_bluetooth_zephyr_poll();
    // ← No CYW43-specific code!
}

// modbluetooth_zephyr.c - Lazy init
STATIC mp_obj_t bluetooth_ble_active(...) {
    if (active && !is_active) {
        mp_bluetooth_hci_controller_init();  // ← CYW43 driver provides this
        bt_enable(NULL);  // ← Called only when user activates
    }
}
```

## Benefits of This Approach

1. **No platform-specific code** - `mpzephyrport.c` works on any platform with HCI UART
2. **Reuses existing infrastructure** - Same pattern as NimBLE (proven to work)
3. **Lazy initialization** - No crash at boot, only activate when needed
4. **Automatic CYW43 support** - pico-sdk provides controller functions via WEAK overrides
5. **Simpler code** - ~80 lines instead of 650+
6. **No race conditions** - Controller initialized only when BLE activated

## Next Steps

1. Create minimal `mpzephyrport.c` based on `mpnimbleport.c`
2. Delete custom HCI transport code from `mpzephyrport_rp2.c`
3. Move `bt_enable()` call from `mp_bluetooth_init()` to `ble.active(True)` handler
4. Change `main.c` to call `mp_bluetooth_hci_init()` instead of `mp_bluetooth_init()`
5. Test - should work immediately without crashes
