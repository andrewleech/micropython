"""IRQ latency measurement with high-resolution timer (profiling disabled).

This test measures the same metric as the original tests but with 4ns resolution
to validate that disabling profiling gives identical results to the low-resolution
tests documented in PR_DESCRIPTION_DRAFT.md.

NUCLEO_H563ZI @ 250MHz:
- TIM2 runs at 250MHz (prescaler=0)
- 1 tick = 4ns resolution
- IRQ_PERIOD = 250000 (1ms between IRQs)

Expected results (from PR_DESCRIPTION_DRAFT.md with prescaler=24, 100ns/tick):
- Bytecode function: ~2800ns (28 ticks @ 100ns)
- Bytecode generator: ~2700ns (27 ticks @ 100ns)
- Native generator: ~2500ns (25 ticks @ 100ns)

With 4ns resolution, we expect:
- Bytecode function: ~2800ns (700 ticks @ 4ns)
- Bytecode generator: ~2700ns (675 ticks @ 4ns)
- Native generator: ~2500ns (625 ticks @ 4ns)
"""

import micropython
from pyb import Timer
import gc

TIMER_NUM = 2
PRESCALER = 0  # Zero prescaler = 250MHz counter (4ns per tick)
IRQ_PERIOD = 250000  # 250000 ticks @ 250MHz = 1ms
NUM_SAMPLES = 100

latencies = [0] * NUM_SAMPLES
idx = 0


# Bytecode function callback
def func_cb(t):
    global idx, latencies
    count = t.counter()  # Capture FIRST
    if idx < NUM_SAMPLES:
        latencies[idx] = count
        idx += 1


# Bytecode generator callback
def gen_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    while True:
        t = yield
        count = t.counter()  # Capture FIRST after yield resumes
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx


# Native function callback
@micropython.native
def native_func_cb(t):
    global idx, latencies
    count = t.counter()  # Capture FIRST
    if idx < NUM_SAMPLES:
        latencies[idx] = count
        idx += 1


# Native generator callback
@micropython.native
def native_gen_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    while True:
        t = yield
        count = t.counter()  # Capture FIRST after yield resumes
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx


def test_callback(name, callback):
    global idx, latencies
    idx = 0
    for i in range(NUM_SAMPLES):
        latencies[i] = 0
    gc.collect()

    tim = Timer(TIMER_NUM, prescaler=PRESCALER, period=IRQ_PERIOD, callback=callback)
    while idx < NUM_SAMPLES:
        pass
    tim.callback(None)
    tim.deinit()

    # Skip first 5 samples for warmup, then calculate stats
    valid = [x for x in latencies[5:] if x > 0 and x < IRQ_PERIOD]
    if valid:
        avg = sum(valid) // len(valid)
        mn = min(valid)
        mx = max(valid)

        # Convert to nanoseconds (4ns per tick)
        avg_ns = avg * 4
        mn_ns = mn * 4
        mx_ns = mx * 4

        # Also show equivalent 100ns-resolution values for comparison
        avg_100ns = avg_ns / 100
        mn_100ns = mn_ns / 100
        mx_100ns = mx_ns / 100

        print(f"\n{name}:")
        print(f"  High-res (4ns/tick):  min={mn:4d} avg={avg:4d} max={mx:4d} ticks")
        print(f"                        min={mn_ns:5d}ns avg={avg_ns:5d}ns max={mx_ns:5d}ns")
        print(
            f"  Equivalent @ 100ns:   min={mn_100ns:4.1f} avg={avg_100ns:4.1f} max={mx_100ns:4.1f} ticks"
        )
        print(f"  Valid samples: {len(valid)}/{len(latencies[5:])}")
        print(f"  First 10 (after warmup): {latencies[5:15]}")
        print(f"  Raw data for analysis: min={mn} avg={avg} max={mx} (ticks @ 4ns)")

        return avg_ns
    else:
        print(f"{name}: NO VALID SAMPLES")
        return -1


print("=" * 80)
print("IRQ LATENCY MEASUREMENT - HIGH RESOLUTION (PROFILING DISABLED)")
print("=" * 80)
print(f"Timer: TIM{TIMER_NUM}")
print(f"Prescaler: {PRESCALER} (250MHz)")
print("Resolution: 4ns per tick")
print(f"IRQ Period: {IRQ_PERIOD} ticks ({IRQ_PERIOD * 4}ns = {IRQ_PERIOD * 4 / 1000}µs)")
print(f"Samples: {NUM_SAMPLES} (first 5 skipped for warmup)")
print()
print("Expected results (from PR_DESCRIPTION_DRAFT.md @ 100ns resolution):")
print("  Bytecode function:  ~2800ns (28 ticks)")
print("  Bytecode generator: ~2700ns (27 ticks)")
print("  Native generator:   ~2500ns (25 ticks)")
print()
print("With 4ns resolution, expecting:")
print("  Bytecode function:  ~2800ns (700 ticks)")
print("  Bytecode generator: ~2700ns (675 ticks)")
print("  Native generator:   ~2500ns (625 ticks)")
print("=" * 80)

# Run tests
func_result = test_callback("Bytecode Function", func_cb)
gen_result = test_callback("Bytecode Generator", gen_cb)
native_func_result = test_callback("Native Function", native_func_cb)
native_gen_result = test_callback("Native Generator", native_gen_cb)

# Summary comparison
print("\n" + "=" * 80)
print("SUMMARY - Comparison to PR_DESCRIPTION_DRAFT.md expectations")
print("=" * 80)

if func_result > 0 and gen_result > 0 and native_gen_result > 0:
    print(f"\nBytecode Function:  {func_result}ns")
    print("  Expected: ~2800ns")
    print(f"  Difference: {func_result - 2800:+d}ns ({(func_result - 2800) * 100 / 2800:+.1f}%)")

    print(f"\nBytecode Generator: {gen_result}ns")
    print("  Expected: ~2700ns")
    print(f"  Difference: {gen_result - 2700:+d}ns ({(gen_result - 2700) * 100 / 2700:+.1f}%)")
    print(
        f"  vs Function: {gen_result - func_result:+d}ns ({(gen_result - func_result) * 100 / func_result:+.1f}%)"
    )

    print(f"\nNative Generator:   {native_gen_result}ns")
    print("  Expected: ~2500ns")
    print(
        f"  Difference: {native_gen_result - 2500:+d}ns ({(native_gen_result - 2500) * 100 / 2500:+.1f}%)"
    )
    print(
        f"  vs Function: {native_gen_result - func_result:+d}ns ({(native_gen_result - func_result) * 100 / func_result:+.1f}%)"
    )

    if native_func_result > 0:
        print(f"\nNative Function:    {native_func_result}ns")
        print(
            f"  vs Bytecode Func: {native_func_result - func_result:+d}ns ({(native_func_result - func_result) * 100 / func_result:+.1f}%)"
        )

print("\n" + "=" * 80)
print("Validation: Results should match PR expectations within ~5-10%")
print("(Small differences expected due to measurement methodology)")
print("=" * 80)
