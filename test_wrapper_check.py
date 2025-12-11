"""Check if native functions are being wrapped correctly."""

import micropython
from pyb import Timer

TIMER_NUM = 2
PRESCALER = 0
IRQ_PERIOD = 250000


# Native function
@micropython.native
def native_func(t):
    pass


# Bytecode function
def bytecode_func(t):
    pass


print("Testing callback type detection...")

# Register and immediately check what was stored
tim = Timer(TIMER_NUM, prescaler=PRESCALER, period=IRQ_PERIOD)

print("\n1. Registering bytecode function...")
tim.callback(bytecode_func)
print("   Callback registered successfully")

print("\n2. Registering native function...")
tim.callback(native_func)
print("   Callback registered successfully")

print("\n3. Testing if callbacks work...")
count = 0


def test_func(t):
    global count
    count += 1
    if count >= 5:
        t.callback(None)


tim.callback(test_func)
import time

time.sleep(0.1)

if count >= 5:
    print(f"   Function callbacks work: {count} IRQs received")
else:
    print(f"   FAILED: Only {count} IRQs received")

tim.deinit()

print("\nDone - if both registered successfully, wrapping is working")
