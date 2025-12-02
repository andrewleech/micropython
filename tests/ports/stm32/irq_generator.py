# Test generator-based IRQ handlers for timers.
# This test requires hardware and must be run on an STM32 board.
#
# Note: Uses global counters instead of list.append() to avoid
# memory allocation in hard IRQ context.

import pyb
import time

# Test 1: Generator function with argument (auto-instantiated)
print("Test 1: Generator function with arg")
count1 = 0


def handler_with_arg(tim):
    global count1
    buf = bytearray(4)
    while count1 < 5:
        t = yield
        buf[count1 % 4] = count1
        count1 += 1


tim1 = pyb.Timer(4, freq=1000)
tim1.callback(handler_with_arg)
time.sleep_ms(20)
tim1.callback(None)
print("count1:", count1)
print("PASS" if count1 == 5 else "FAIL")

# Test 2: Generator instance (manually instantiated)
print("\nTest 2: Generator instance")
count2 = 0


def handler_no_arg():
    global count2
    while count2 < 3:
        tim = yield
        count2 += 1


tim2 = pyb.Timer(7, freq=1000)
tim2.callback(handler_no_arg())
time.sleep_ms(20)
tim2.callback(None)
print("count2:", count2)
print("PASS" if count2 == 3 else "FAIL")

# Test 3: Generator that exits (should auto-disable callback)
print("\nTest 3: Generator exit")
count3 = 0


def handler_exits(tim):
    global count3
    for i in range(3):
        yield
        count3 += 1
    # Generator returns here, should disable callback


tim3 = pyb.Timer(8, freq=1000)
tim3.callback(handler_exits)
time.sleep_ms(20)
# Callback should have been disabled after generator returned
print("count3:", count3)
print("PASS" if count3 == 3 else "FAIL")

# Test 4: Regular function callback still works
print("\nTest 4: Regular function callback")
count4 = 0


def regular_handler(tim):
    global count4
    count4 += 1


tim4 = pyb.Timer(2, freq=1000)
tim4.callback(regular_handler)
time.sleep_ms(10)
tim4.callback(None)
print("count4:", count4)
print("PASS" if count4 >= 5 else "FAIL")

print("\nAll tests completed")
