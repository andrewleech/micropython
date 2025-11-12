# iperf3_lwip Module Implementation Plan

## Current Status

**Commit**: `718d68537f` - TCP send/receive implementation complete but device fails to boot

**Issue**: Device not enumerating on USB after flashing firmware with iperf3_lwip module enabled

**Reference**: Existing `ciperf` module works and achieves 489 Mbits/sec using identical lwIP patterns

## Code Review - Boot Failure Analysis

### Potential Issues Identified

#### 1. Module Registration Conflict
**Location**: `iperf3_lwip.c:430`
```c
MP_REGISTER_MODULE(MP_QSTR_iperf3_lwip, iperf3_lwip_user_cmodule);
```

**Analysis**: Both `ciperf` and `iperf3_lwip` may be registered simultaneously
- Check `ports/stm32/boards/OPENMV_N6/mpconfigboard.mk`
- Verify `USER_C_MODULES` path includes both
- May cause module table overflow or initialization conflict

**Test**: Disable ciperf in build, rebuild with only iperf3_lwip

#### 2. Static State Structure Initialization
**Location**: `iperf3_lwip.c:45`
```c
static iperf3_state_t iperf3_state = {0};
```

**Analysis**: Structure size ~40 bytes, contains 2 pointers + integers
- Not large enough to cause BSS issues alone
- But combined with ciperf's static state could be problematic
- STM32N6 may have memory constraints during early boot

**Comparison with ciperf**:
- ciperf has similar structure at line 51: `static ciperf_state_t ciperf_state = {0};`
- If both are present, that's ~80 bytes in BSS

**Test**: Make state structure non-static (heap-allocate on first use)

#### 3. lwIP Callback Registration During Init
**Analysis**: No constructor functions detected, callbacks only set during function calls
- This is correct - callbacks registered lazily
- Not a boot-time issue

#### 4. Float Operations in Printf
**Location**: Lines 293, 296, 399
```c
mp_printf(&mp_plat_print, "%.2f MB in %.2f sec = %.2f Mbits/sec\n",
          (double)mbytes, (double)elapsed_sec, (double)mbits_per_sec);
```

**Analysis**: Float formatting in printf requires FPU support
- STM32N6 has FPU, should be enabled
- But if FPU not initialized before module registration, could crash
- However, these printfs only execute during function calls, not at module load

**Test**: Not likely boot issue, but can remove float printf as precaution

#### 5. Missing QSTR Definitions
**Analysis**: Module uses `MP_QSTR_iperf3_lwip` which must be generated
- Build system auto-generates from `MP_REGISTER_MODULE`
- If QSTR generation fails, module registration fails
- May cause silent boot failure

**Test**: Check build output for QSTR generation errors

### Most Likely Root Cause

**Hypothesis**: Multiple user C modules with static state structures exceeding BSS budget during C startup

**Evidence**:
1. ciperf module works alone (489 Mbits/sec achieved)
2. Boot failure only occurs with iperf3_lwip added
3. Both modules have ~40 byte static state structures
4. Original ciperf boot failure was fixed by heap-allocating 16KB buffer
5. No constructors or init-time code that would crash

**Solution**: Eliminate all static state, heap-allocate everything on first use

## Bisection Plan

### Phase 0: Minimal Module (Boot Test Only)

**Goal**: Verify module registration works without any functionality

**Implementation**:
```c
#include "py/runtime.h"

#if MICROPY_PY_LWIP

STATIC mp_obj_t iperf3_lwip_test(void) {
    return mp_const_none;
}
STATIC MP_DEFINE_CONST_FUN_OBJ_0(iperf3_lwip_test_obj, iperf3_lwip_test);

static const mp_rom_map_elem_t iperf3_lwip_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_iperf3_lwip) },
    { MP_ROM_QSTR(MP_QSTR_test), MP_ROM_PTR(&iperf3_lwip_test_obj) },
};
STATIC MP_DEFINE_CONST_DICT(iperf3_lwip_module_globals, iperf3_lwip_module_globals_table);

const mp_obj_module_t iperf3_lwip_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&iperf3_lwip_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_iperf3_lwip, iperf3_lwip_user_cmodule);

#endif
```

**Test**:
```python
import iperf3_lwip
iperf3_lwip.test()  # Should return None
```

**Expected**: Device boots, module imports successfully

**If fails**: Module registration itself is broken - check QSTR generation or module table size

**If succeeds**: Proceed to Phase 1

### Phase 1: Add lwIP Includes Only

**Add**:
```c
#include "lwip/tcp.h"
#include "lwip/ip_addr.h"
#include "lwip/err.h"
```

**Test**: Boot and import

**If fails**: lwIP header conflict or missing dependency

**If succeeds**: Proceed to Phase 2

### Phase 2: Add Static State Structure

**Add**:
```c
typedef struct {
    struct tcp_pcb *pcb;
    uint32_t start_time_ms;
    uint64_t bytes_transferred;
    bool is_running;
} iperf3_state_t;

static iperf3_state_t iperf3_state = {0};
```

**Test**: Boot and import

**If fails**: Static structure initialization is the issue - convert to heap allocation

**If succeeds**: Proceed to Phase 3

### Phase 3: Add Buffer Pointer and Init Function

**Add**:
```c
static uint8_t *iperf3_tx_buffer = NULL;

static void iperf3_init_buffers(void) {
    if (iperf3_tx_buffer == NULL) {
        iperf3_tx_buffer = m_malloc(16384);
    }
}
```

**Test**: Boot and import, call init function

**If fails**: Heap allocation during module load is problematic

**If succeeds**: Proceed to Phase 4

### Phase 4: Add Callbacks (No PCB Creation)

**Add**: All callback functions (tcp_sent_cb, tcp_recv_cb, tcp_err_cb, etc.)

**Test**: Boot and import

**If fails**: Callback function signatures or lwIP API mismatch

**If succeeds**: Proceed to Phase 5

### Phase 5: Add Client Function (Complete)

**Add**: Full `client()` function with tcp_new(), tcp_connect(), etc.

**Test**: Boot, import, run client test

**Expected**: 400-600 Mbits/sec throughput

### Phase 6: Add Server Function (Complete)

**Add**: Full `server()` function

**Test**: Boot, import, run server test

**Expected**: 400-600 Mbits/sec throughput

## Alternative: Heap-Allocated State Structure

If Phase 2 fails, implement state as heap-allocated:

```c
typedef struct {
    struct tcp_pcb *pcb;
    uint32_t start_time_ms;
    uint64_t bytes_transferred;
    bool is_running;
} iperf3_state_t;

static iperf3_state_t *iperf3_state = NULL;

static void iperf3_init_state(void) {
    if (iperf3_state == NULL) {
        iperf3_state = m_malloc(sizeof(iperf3_state_t));
        if (iperf3_state == NULL) {
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("Failed to allocate state"));
        }
        memset(iperf3_state, 0, sizeof(iperf3_state_t));
    }
}
```

Update all references: `iperf3_state.field` → `iperf3_state->field`

## Full Implementation Roadmap

### Phase A: Core TCP Send (Client Mode)

**Status**: Implemented at commit `718d68537f`, needs boot fix

**Functions**:
- `iperf3_lwip.client(host, port=5201, duration=10)`

**Implementation**:
1. Parse arguments (host IP, port, duration)
2. Create TCP PCB with `tcp_new()`
3. Set callbacks: `tcp_sent_cb`, `tcp_err_cb`, `tcp_connected_cb`
4. Call `tcp_connect()`
5. Wait for connection with timeout
6. Run test loop with `MICROPY_EVENT_POLL_HOOK`
7. Print results and cleanup

**Testing**:
```bash
# On PC:
iperf3 -s

# On device:
import iperf3_lwip
iperf3_lwip.client('192.168.0.8', 5201, 10)
```

**Expected**: 400-600 Mbits/sec

### Phase B: Core TCP Receive (Server Mode)

**Status**: Implemented at commit `718d68537f`, needs boot fix

**Functions**:
- `iperf3_lwip.server(port=5201, duration=10)`

**Implementation**:
1. Parse arguments (port, duration)
2. Create TCP PCB with `tcp_new()`
3. Bind with `tcp_bind()`
4. Listen with `tcp_listen()`
5. Set accept callback: `tcp_accept_cb`
6. Wait for connection
7. In accept callback: set `tcp_recv_cb`
8. Run test loop
9. Print results and cleanup

**Testing**:
```bash
# On device:
import iperf3_lwip
iperf3_lwip.server(5201, 10)

# On PC:
iperf3 -c 192.168.0.212
```

**Expected**: 400-600 Mbits/sec

### Phase C: UDP Support

**Status**: Not started

**Functions**:
- `iperf3_lwip.udp_client(host, port=5201, duration=10, bandwidth=100000000)`
- `iperf3_lwip.udp_server(port=5201, duration=10)`

**UDP Packet Format** (iperf3 compatible):
```c
typedef struct __attribute__((packed)) {
    uint32_t sec;      // Timestamp seconds (network byte order)
    uint32_t usec;     // Timestamp microseconds (network byte order)
    uint32_t seq;      // Sequence number (network byte order)
    uint8_t data[];    // Payload data
} iperf3_udp_packet_t;
```

**UDP Client Implementation**:
1. Create UDP PCB with `udp_new()`
2. Connect to server address
3. Allocate pbuf with 12-byte header + data
4. Fill header: `sec`, `usec`, `seq` in network byte order
5. Send with `udp_send()`
6. Rate limit with `mp_hal_delay_us()` based on bandwidth parameter
7. Track packets sent
8. Cleanup and report results

**UDP Server Implementation**:
1. Create UDP PCB with `udp_new()`
2. Bind to port
3. Set receive callback: `udp_recv_cb`
4. In callback:
   - Parse 12-byte header
   - Extract `sec`, `usec`, `seq` (convert from network byte order)
   - Track packets received
   - Calculate jitter using RFC 1889 algorithm
   - Detect packet loss from sequence gaps
5. After duration expires, report:
   - Total bytes received
   - Packets received/lost
   - Packet loss percentage
   - Average jitter

**Jitter Calculation** (RFC 1889):
```c
static float jitter = 0.0f;
static uint32_t prev_transit = 0;

// In UDP receive callback:
uint32_t arrival_time_us = (mp_hal_ticks_ms() * 1000);
uint32_t sent_time_us = (packet->sec * 1000000) + packet->usec;
uint32_t transit = arrival_time_us - sent_time_us;

if (prev_transit != 0) {
    int32_t delta = (int32_t)(transit - prev_transit);
    if (delta < 0) delta = -delta;
    jitter += (delta - jitter) / 16.0f;
}
prev_transit = transit;
```

**Testing**:
```bash
# UDP client test:
iperf3 -s -u
import iperf3_lwip
iperf3_lwip.udp_client('192.168.0.8', 5201, 10, 100000000)

# UDP server test:
import iperf3_lwip
iperf3_lwip.udp_server(5201, 10)
iperf3 -c 192.168.0.212 -u -b 100M
```

**Expected**: 300-500 Mbits/sec (UDP typically lower than TCP)

### Phase D: Buffer Size Configuration

**Status**: Not started

**API Enhancement**:
```python
iperf3_lwip.client(host, port, duration, buffer_size=16384)
iperf3_lwip.server(port, duration, buffer_size=16384)
```

**Implementation**:
1. Add `buffer_size` parameter to functions (default 16384)
2. Validate range: 1024 - 65536 bytes
3. Allocate buffer dynamically: `m_malloc(buffer_size)`
4. Free buffer after test: `m_free(buffer)`
5. Test with multiple sizes: 1KB, 4KB, 8KB, 16KB, 32KB, 64KB

**Testing**:
```bash
for size in 1024 4096 8192 16384 32768 65536; do
    echo "Testing buffer size: $size"
    mpremote exec "import iperf3_lwip; iperf3_lwip.client('192.168.0.8', 5201, 5, $size)"
done
```

**Expected**: Optimal throughput at 16KB-32KB for Gigabit Ethernet

### Phase E: Python iperf3 Integration

**Status**: Not started

**Goal**: Modify existing Python iperf3.py to use C acceleration for data transfer

**Architecture**:
```
Python iperf3.py              C iperf3_lwip
-----------------             ------------------
Control protocol   -------->  [no change]
JSON handshake     -------->  [no change]
Cookie exchange    -------->  [no change]
TEST_START         -------->  [no change]

Data transfer      -------->  iperf3_lwip.client/server() <<<< C ACCELERATION
(Replaces Python              (Runs entire test in C)
 poll() loop)

Results JSON       <--------  Receive bytes/duration from C
EXCHANGE_RESULTS   <--------  Format into iperf3 JSON
```

**Modifications to lib/micropython-lib/python-ecosys/iperf3/iperf3.py**:

At top of file:
```python
try:
    import iperf3_lwip
    LWIP_ACCEL = True
except ImportError:
    LWIP_ACCEL = False
```

Replace lines 263-290 in `server_once()`:
```python
if LWIP_ACCEL and param.get("tcp", False) and not param.get("reverse", False):
    # Fast C path for TCP receive
    stats.start()
    iperf3_lwip.server(5201, param["time"])
    stats.stop()
else:
    # Original Python loop (fallback)
    stats = Stats(param)
    stats.start()
    running = True
    # ... existing code ...
```

Similar modifications for client mode in `client()` function

**Benefits**:
- Full iperf3 protocol compatibility maintained
- 5-10x performance improvement on data transfer
- Pure Python fallback on platforms without lwIP
- Existing JSON/statistics code reused

**Testing**:
```bash
# Test all iperf3 modes:
iperf3 -c 192.168.0.212              # TCP upload
iperf3 -c 192.168.0.212 -R           # TCP download (reverse)
iperf3 -c 192.168.0.212 -u           # UDP upload
iperf3 -c 192.168.0.212 -u -R        # UDP download

# Test Python iperf3 as client:
import iperf3
iperf3.client('192.168.0.8')         # Uses C acceleration automatically
iperf3.client('192.168.0.8', udp=True)
```

**Expected**: Same throughput as direct C module calls, full iperf3 compatibility

## Implementation Guidelines

### Memory Management
- **NO static arrays >1KB**: Use heap allocation with `m_malloc()`
- **Check all malloc returns**: Raise `mp_type_MemoryError` if NULL
- **Free resources in cleanup**: Always call `m_free()` and `tcp_close()`
- **Consider state structure heap allocation**: If static state causes boot issues

### Error Handling
- **Validate all parameters**: Check ranges, NULL pointers, IP address format
- **Set timeouts**: Connection timeout (5 sec), test duration enforcement
- **Use `mp_raise_*` for errors**: Don't return error codes
- **Clean up on error**: Close PCBs, free buffers before raising exceptions

### lwIP Integration
- **TCP_NODELAY**: Always disable Nagle's algorithm for throughput tests
- **TCP_WRITE_FLAG_MORE**: Use for segment coalescing
- **TCP_WRITE_FLAG_COPY**: Allow immediate buffer reuse
- **tcp_output()**: Force immediate transmission after tcp_write()
- **tcp_recved()**: Call in receive callback to update TCP window
- **TCP_PRIO_MAX**: Set maximum priority for test connections

### Python Integration
- **MICROPY_EVENT_POLL_HOOK**: Use in all wait loops for Ctrl-C support
- **mp_hal_delay_ms()**: Use for delays, not busy-wait
- **mp_printf(&mp_plat_print, ...)**: For user-visible output
- **Dictionary returns**: Use for structured results (bytes, duration, etc.)

### Build Configuration
- **Optimization**: Use `-Os` (not `-O3`) to reduce code size
- **No aggressive flags**: Avoid `-funroll-loops`, `-finline-functions`
- **Format code**: Run `tools/codeformat.py` before committing
- **Sign commits**: Always use `git commit -s`

## Testing Strategy

### Unit Testing
1. **Module import**: `import iperf3_lwip` succeeds
2. **Function exists**: `hasattr(iperf3_lwip, 'client')` is True
3. **Parameter validation**: Invalid IP raises ValueError
4. **Connection timeout**: Unreachable host times out after 5 seconds
5. **Zero-duration test**: Returns immediately with 0 bytes

### Integration Testing
1. **TCP client → iperf3 server**: Verify throughput ≥ 400 Mbits/sec
2. **iperf3 client → TCP server**: Verify throughput ≥ 400 Mbits/sec
3. **UDP client → iperf3 server**: Verify jitter/loss statistics
4. **iperf3 client → UDP server**: Verify packet counting
5. **Multiple sequential tests**: No memory leaks or resource exhaustion

### Performance Benchmarking
```python
# Test suite for buffer size optimization
import iperf3_lwip
import time

for buffer_size in [1024, 2048, 4096, 8192, 16384, 32768, 65536]:
    print(f"\nBuffer size: {buffer_size} bytes")
    for trial in range(3):
        start = time.ticks_ms()
        iperf3_lwip.client('192.168.0.8', 5201, 10, buffer_size)
        duration = time.ticks_diff(time.ticks_ms(), start)
        print(f"  Trial {trial+1}: {duration}ms")
```

### Stress Testing
```python
# Test stability over many iterations
import iperf3_lwip
import gc

for i in range(100):
    print(f"Test {i+1}/100")
    iperf3_lwip.client('192.168.0.8', 5201, 5)
    gc.collect()
    print(f"Free memory: {gc.mem_free()}")
    # Should not decrease over time (no leaks)
```

## Expected Performance Targets

| Test Mode | Current Python | With C Module | Improvement |
|-----------|---------------|---------------|-------------|
| TCP TX    | ~80 Mbits/sec | 500-600 Mbits/sec | 6-7x |
| TCP RX    | ~60 Mbits/sec | 400-500 Mbits/sec | 7-8x |
| UDP TX    | ~50 Mbits/sec | 400-500 Mbits/sec | 8-10x |
| UDP RX    | ~40 Mbits/sec | 300-400 Mbits/sec | 7-10x |

**Reference**: ciperf module achieves 489 Mbits/sec TCP TX on same hardware

## Commit Strategy

### Commit 1: Minimal Module (Phase 0)
```
examples/usercmodule/iperf3_lwip: Add minimal module skeleton.

Minimal module with single test() function to verify registration works.
No static state, no lwIP dependencies, no functionality.

Tested on OpenMV N6: boots successfully, module imports.
```

### Commit 2: Add Static State (Phase 2)
```
examples/usercmodule/iperf3_lwip: Add static state structure.

Added iperf3_state_t structure (40 bytes) with static initialization.

Tested on OpenMV N6: [boots successfully / BOOT FAILURE].

[If boot failure]: Next commit will convert to heap allocation.
```

### Commit 3: Full TCP Implementation
```
examples/usercmodule/iperf3_lwip: Add TCP client and server functions.

Implemented direct lwIP PCB API for high-performance TCP testing:
- client(host, port, duration): Connect and send data
- server(port, duration): Listen and receive data
- Heap-allocated 16KB buffer (lazy initialization)
- Zero-copy transmission with TCP_WRITE_FLAG_MORE
- TCP_NODELAY for maximum throughput

Tested on OpenMV N6 (STM32N657 @ 800MHz):
- TCP TX: XXX Mbits/sec (iperf3 -s on PC)
- TCP RX: XXX Mbits/sec (iperf3 -c 192.168.0.212 from PC)
```

### Commit 4: Add UDP Support
```
examples/usercmodule/iperf3_lwip: Add UDP client and server functions.

Implemented UDP with iperf3-compatible 12-byte headers:
- udp_client(): Send with sequence numbers and timestamps
- udp_server(): Receive with jitter and loss tracking
- RFC 1889 jitter calculation
- Sequence gap detection for packet loss

Tested on OpenMV N6:
- UDP TX: XXX Mbits/sec, X.XX ms jitter, X.X% loss
- UDP RX: XXX Mbits/sec, X.XX ms jitter, X.X% loss
```

### Commit 5: Python Integration
```
lib/iperf3: Add optional C acceleration via iperf3_lwip.

Modified Python iperf3 to use C module for data transfer when available:
- Control protocol remains in Python (JSON, cookies, states)
- Data transfer uses iperf3_lwip C module (5-10x faster)
- Automatic fallback to pure Python if C module unavailable

Maintains full iperf3 compatibility with significant performance gain.
```

## Next Steps

1. **Execute Phase 0**: Create minimal module, test boot
2. **If boot successful**: Proceed through phases 1-6 iteratively
3. **If boot fails at Phase 2**: Implement heap-allocated state structure
4. **After TCP working**: Add UDP support (Phase C)
5. **After UDP working**: Integrate with Python iperf3 (Phase E)
6. **Final validation**: Test all modes against official iperf3 client/server

## Notes

- Boot failure is critical blocker - must resolve before proceeding
- Bisection approach minimizes risk of introducing new boot issues
- Each phase independently tested before moving to next
- Reference implementation (ciperf) proves approach is sound
- Static state structure is most likely culprit based on previous experience
