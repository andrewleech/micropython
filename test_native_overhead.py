"""Test to understand native function overhead."""

import micropython
from pyb import Timer
import time

TIMER_NUM = 2
PRESCALER = 0
IRQ_PERIOD = 250000

# Test 1: Measure just the counter read from different contexts
print("=" * 60)
print("Testing t.counter() call overhead from different contexts")
print("=" * 60)

tim = Timer(TIMER_NUM, prescaler=PRESCALER, period=IRQ_PERIOD)

# From bytecode
start = tim.counter()
for _ in range(1000):
    _ = tim.counter()
bytecode_overhead = (tim.counter() - start) / 1000
print("\nBytecode loop (1000 iterations):")
print(f"  Per-call overhead: {bytecode_overhead:.1f} ticks ({bytecode_overhead * 4:.0f}ns)")


# From native
@micropython.native
def test_native():
    start = tim.counter()
    for _ in range(1000):
        _ = tim.counter()
    return (tim.counter() - start) / 1000


native_overhead = test_native()
print("\nNative loop (1000 iterations):")
print(f"  Per-call overhead: {native_overhead:.1f} ticks ({native_overhead * 4:.0f}ns)")

print(
    f"\nDifference: {native_overhead - bytecode_overhead:+.1f} ticks ({(native_overhead - bytecode_overhead) * 4:+.0f}ns)"
)
if native_overhead > bytecode_overhead:
    print(f"  Native is SLOWER by {((native_overhead / bytecode_overhead - 1) * 100):.1f}%")
else:
    print(f"  Native is faster by {((1 - native_overhead / bytecode_overhead) * 100):.1f}%")

tim.deinit()

# Test 2: Check if the issue is global variable access
print("\n" + "=" * 60)
print("Testing global variable access overhead")
print("=" * 60)

global_var = 0


def bytecode_global_test():
    global global_var
    for _ in range(1000):
        global_var += 1


@micropython.native
def native_global_test():
    global global_var
    for _ in range(1000):
        global_var += 1


tim2 = Timer(TIMER_NUM, prescaler=PRESCALER, period=IRQ_PERIOD)

start = tim2.counter()
bytecode_global_test()
bytecode_time = tim2.counter() - start

start = tim2.counter()
native_global_test()
native_time = tim2.counter() - start

print(f"\nBytecode global access: {bytecode_time} ticks ({bytecode_time * 4}ns)")
print(f"Native global access:   {native_time} ticks ({native_time * 4}ns)")
print(
    f"Difference: {native_time - bytecode_time:+d} ticks ({(native_time - bytecode_time) * 4:+d}ns)"
)

tim2.deinit()

print("\n" + "=" * 60)
print("Hypothesis: Native functions may have overhead from:")
print("  1. Method call dispatch (t.counter())")
print("  2. Global variable access")
print("  3. Calling convention")
print("=" * 60)
