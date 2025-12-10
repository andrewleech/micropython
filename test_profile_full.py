"""Comprehensive IRQ profiling with zero prescaler for maximum resolution.

NUCLEO_H563ZI @ 250MHz:
- TIM2 runs at 250MHz (prescaler=0)
- 1 tick = 4ns resolution
- IRQ_PERIOD = 250000 (1ms between IRQs)
"""

import micropython
from pyb import Timer
import pyb
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


def test_with_profiling(name, callback):
    global idx, latencies
    idx = 0
    for i in range(NUM_SAMPLES):
        latencies[i] = 0
    gc.collect()

    # Enable profiling
    pyb.irq_profile_enable(True)

    tim = Timer(TIMER_NUM, prescaler=PRESCALER, period=IRQ_PERIOD, callback=callback)

    # Wait for first IRQ to stabilize
    while idx < 5:
        pass

    # Capture one profiled IRQ
    while idx < 6:
        pass

    # Get profile data
    profile = pyb.irq_profile_get()

    # Continue collecting latency samples
    while idx < NUM_SAMPLES:
        pass

    tim.callback(None)
    tim.deinit()

    # Disable profiling
    pyb.irq_profile_enable(False)

    # Calculate statistics on latency samples (skip first 5 warmup)
    valid = [x for x in latencies[5:] if x > 0 and x < IRQ_PERIOD]
    if valid:
        avg = sum(valid) // len(valid)
        mn = min(valid)
        mx = max(valid)
        avg_ns = avg * 4  # 4ns per tick @ 250MHz
        mn_ns = mn * 4
        mx_ns = mx * 4

        print(f"\n{name}:")
        print(f"  Latency: min={mn} avg={avg} max={mx} ticks")
        print(f"           min={mn_ns}ns avg={avg_ns}ns max={mx_ns}ns")
        print(f"  Valid samples: {len(valid)}/{len(latencies[5:])}")
        print(f"  First 10 (after warmup): {latencies[5:15]}")

        # Print profile data (all 24 points)
        print("\n  Profile capture points (ticks @ 4ns/tick):")
        profile_labels = [
            "P0:  timer_handle_irq_channel entry",
            "P1:  mp_irq_dispatch entry",
            "P2:  after sched_lock + gc_lock",
            "P3:  after nlr_push",
            "P4:  before handler call/resume",
            "P5:  after handler returns",
            "P6:  mp_irq_dispatch exit",
            "P7:  handler entry (fun_bc_call/gen_resume)",
            "P8:  after state setup (INIT_CODESTATE/send_value)",
            "P9:  bytecode/native entry",
            "P10: bound_meth_call entry OR vm dispatch ready",
            "P11: after alloca/args setup OR viper entry",
            "P12: before call OR before viper native",
            "P13: mp_call_function_n_kw entry",
            "P14: after mp_obj_get_type",
            "P15: before slot call",
            "P16: (unused)",
            "P17: (unused)",
            "P18: (unused)",
            "P19: fun_builtin_var_call entry",
            "P20: after arg check",
            "P21: pyb_timer_counter entry",
            "P22: (unused)",
            "P23: (unused)",
        ]

        for i, (label, value) in enumerate(zip(profile_labels, profile)):
            if value > 0:  # Only print non-zero values
                value_ns = value * 4
                print(f"    {label:50s} {value:6d} ticks ({value_ns:6d}ns)")

        # Calculate phase deltas
        print("\n  Phase breakdown (ticks @ 4ns/tick):")
        phases = [
            ("P0→P1: timer.c → mpirq.c entry", profile[1] - profile[0] if profile[1] > 0 else 0),
            ("P1→P2: sched_lock + gc_lock", profile[2] - profile[1] if profile[2] > 0 else 0),
            ("P2→P3: nlr_push", profile[3] - profile[2] if profile[3] > 0 else 0),
            ("P3→P4: type check → call/resume", profile[4] - profile[3] if profile[4] > 0 else 0),
            ("P4→P7: to handler entry", profile[7] - profile[4] if profile[7] > 0 else 0),
            ("P7→P8: state setup", profile[8] - profile[7] if profile[8] > 0 else 0),
            ("P8→P9: to VM/native entry", profile[9] - profile[8] if profile[9] > 0 else 0),
            ("P9→P10: dispatch setup", profile[10] - profile[9] if profile[10] > 0 else 0),
            ("P10→Px: to Python code", avg - profile[10] if profile[10] > 0 else 0),
        ]

        for label, delta in phases:
            if delta > 0:
                delta_ns = delta * 4
                print(f"    {label:40s} {delta:6d} ticks ({delta_ns:6d}ns)")

        total_dispatch = profile[10] if profile[10] > 0 else 0
        total_ns = total_dispatch * 4
        print(f"\n  Total P0→P10 (dispatch to VM ready): {total_dispatch} ticks ({total_ns}ns)")
        print(f"  Total P0→Px (ISR to Python):         {avg} ticks ({avg_ns}ns)")

        return avg
    else:
        print(f"{name}: NO VALID SAMPLES")
        return -1


print("=" * 80)
print("IRQ PROFILING WITH ZERO PRESCALER")
print("=" * 80)
print(f"Timer: TIM{TIMER_NUM}")
print(f"Prescaler: {PRESCALER} (250MHz)")
print("Resolution: 4ns per tick")
print(f"IRQ Period: {IRQ_PERIOD} ticks ({IRQ_PERIOD * 4}ns = {IRQ_PERIOD * 4 / 1000}µs)")
print(f"Samples: {NUM_SAMPLES}")
print("=" * 80)

print("\n### Bytecode Function ###")
test_with_profiling("func bytecode", func_cb)

print("\n\n### Bytecode Generator ###")
test_with_profiling("gen bytecode", gen_cb)

print("\n\n### Native Generator ###")
test_with_profiling("gen @native", native_gen_cb)

print("\n" + "=" * 80)
print("PROFILING COMPLETE")
print("=" * 80)
