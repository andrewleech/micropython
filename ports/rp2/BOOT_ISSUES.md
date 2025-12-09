# RP2 FreeRTOS Boot Issues

## Current Status: BROKEN

The FreeRTOS-enabled build for RP2040 (Pico W) currently fails to boot.

## Problem Description

### Root Cause
Two initialization functions called during early boot rely on `busy_wait_us()`, which requires timer hardware that doesn't function properly before the FreeRTOS scheduler starts:

1. **`aon_timer_start()`** - RTC initialization (main.c:141)
   - Calls `aon_timer_set_time()` → `busy_wait_us()`

2. **`lwip_init()`** - Network stack initialization (main.c:168)
   - Calls `rosc_random_u32()` → `mp_hal_delay_us_fast()` → `busy_wait_us()`

### Attempted Fix (commit 8faeb8b704)
Moved both initializations from `main()` to `rp2_main_loop()` to run after FreeRTOS scheduler starts.

**Result**: Complete boot failure - device enters bootloader mode ("RP2 Boot") instead of running MicroPython.

### Behavior Without Fix (commit e8b576daeb)
- Firmware attempts to boot
- USB enumeration attempts occur but fail
- Error: "device descriptor read/64, error -32"
- Error: "Device not responding to setup address"

## Test Results

### With deferred RTC/LWIP init (commit 8faeb8b704)
```
Firmware size: 884816 bytes
Flash: Success via pyocd
Boot: FAILED - enters bootloader
USB: No MicroPython device appears
Power cycle: No change
```

### Without deferred init (commit e8b576daeb)
```
Firmware size: [not recorded]
Flash: Success via pyocd
Boot: Partial - firmware runs but hangs
USB: Enumeration attempts but fails with descriptor errors
Debug: PC stuck at 0x000020e0 (RAM address, likely in service task)
```

## Investigation Needed

### High Priority
1. **Why does deferred initialization cause boot failure?**
   - Need GDB session to trace execution from `main()` through scheduler start
   - Check for stack corruption, memory issues
   - Verify FreeRTOS SMP configuration
   - Examine heap/stack boundary conditions

2. **Alternative timer initialization approaches**
   - Can `aon_timer_start()` be rewritten to avoid `busy_wait_us()`?
   - Is there a FreeRTOS-compatible delay mechanism we can use?
   - Can we initialize RTC later in boot sequence?

3. **LWIP initialization alternatives**
   - Can `rosc_random_u32()` use a different delay mechanism?
   - Can we seed random number generator differently?
   - Is LWIP init truly required at this point?

### Questions
- Why was USB working "a couple of changes ago"? Need to identify that commit
- Is the service task stack size (2048 bytes) adequate?
- Are there other calls to `busy_wait_us()` we haven't identified?

## Files Modified
- `ports/rp2/main.c` - RTC and LWIP initialization moved (BROKEN state)
- `ports/rp2/pendsv.c` - Service task stack size increased to 2048 bytes

## Debug Setup
```bash
# Build
cd ports/rp2
make clean
make BOARD=RPI_PICO_W MICROPY_PY_THREAD=1

# Flash
pyocd load --probe 0501083219160908 --target rp2040 build-RPI_PICO_W/firmware.hex
pyocd reset --probe 0501083219160908 --target rp2040

# Debug
openocd -f interface/cmsis-dap.cfg -f target/rp2040.cfg \
  -c "adapter serial 0501083219160908" &
arm-none-eabi-gdb build-RPI_PICO_W/firmware.elf \
  -ex "target extended-remote localhost:3333"
```

## Next Steps

1. Revert to commit e8b576daeb (partial boot, USB enumeration failures)
2. Use GDB to trace execution and identify where firmware gets stuck
3. Examine timer hardware initialization sequence
4. Consider alternative approaches:
   - Lazy RTC initialization (defer until first use)
   - Replace `busy_wait_us()` calls with FreeRTOS delays
   - Initialize only critical subsystems before scheduler
5. Review FreeRTOS SMP port requirements for RP2040

## References
- Commit e8b576daeb: "ports/rp2: Add FreeRTOS scheduler debugging and fixes."
- Commit 8faeb8b704: "ports/rp2: Attempt to fix RTC/LWIP init timing for FreeRTOS." (BROKEN)
- Debug logs in /tmp/openocd*.log
- CMSIS-DAP probe: serial 0501083219160908
- Target device: Pico W serial e6614c311b7e6f35
