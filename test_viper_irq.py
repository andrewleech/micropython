"""Test IRQ callback latency with various counter read methods."""

import micropython
from micropython import const
from pyb import Timer
import gc
import array
import uctypes
from machine import mem32

TIMER_NUM = 2
IRQ_PERIOD = 10000
NUM_SAMPLES = const(100)

# TIM2 base address (STM32H5) + CNT offset
TIM2_CNT_ADDR = const(0x40000024)

latencies = [0] * NUM_SAMPLES
idx = 0

# Array-based storage for direct viper callback (viper can't access Python lists)
viper_lat_arr = array.array("I", [0] * NUM_SAMPLES)
viper_idx_arr = array.array("I", [0])
VIPER_LAT_ADDR = uctypes.addressof(viper_lat_arr)
VIPER_IDX_ADDR = uctypes.addressof(viper_idx_arr)


# Standard bytecode function - uses t.counter()
def func_cb(t):
    global idx, latencies
    count = t.counter()
    if idx < NUM_SAMPLES:
        latencies[idx] = count
        idx += 1


# Bytecode function with direct mem32 read
def func_mem32_cb(t):
    global idx, latencies
    count = mem32[TIM2_CNT_ADDR]
    if idx < NUM_SAMPLES:
        latencies[idx] = count
        idx += 1


# Bytecode generator - uses t.counter()
def gen_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    while True:
        t = yield
        count = t.counter()
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx


# Native generator - uses t.counter()
@micropython.native
def native_gen_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    while True:
        t = yield
        count = t.counter()
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx


# Bytecode generator with mem32
def gen_mem32_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    addr = TIM2_CNT_ADDR
    while True:
        yield  # t not needed - we read counter directly
        count = mem32[addr]
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx


# Viper counter read helper
@micropython.viper
def viper_read_counter() -> int:
    p = ptr32(TIM2_CNT_ADDR)  # noqa: F821 - viper builtin
    return p[0]


# Bytecode wrapper for viper
def viper_cb(t):
    global idx, latencies
    count = viper_read_counter()
    if idx < NUM_SAMPLES:
        latencies[idx] = count
        idx += 1


# Generator with viper counter read
def gen_viper_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    while True:
        yield  # t not needed - we read counter directly
        count = viper_read_counter()
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx


# Native generator with mem32 - fastest approach
@micropython.native
def native_gen_mem32_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    addr = TIM2_CNT_ADDR
    while True:
        yield  # t not needed
        count = mem32[addr]
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx


# Direct viper callback - uses array storage (viper can't access Python lists)
@micropython.viper
def viper_direct_cb(t):
    # Read TIM2->CNT directly
    cnt_ptr = ptr32(TIM2_CNT_ADDR)  # noqa: F821
    count: int = cnt_ptr[0]

    # Access index via ptr32
    idx_ptr = ptr32(VIPER_IDX_ADDR)  # noqa: F821
    i: int = idx_ptr[0]

    if i < NUM_SAMPLES:
        lat_ptr = ptr32(VIPER_LAT_ADDR)  # noqa: F821
        lat_ptr[i] = count
        idx_ptr[0] = i + 1


def test_viper_direct(name, callback):
    """Special test for direct viper callback using array storage."""
    global viper_lat_arr, viper_idx_arr
    viper_idx_arr[0] = 0
    for i in range(NUM_SAMPLES):
        viper_lat_arr[i] = 0
    gc.collect()

    tim = Timer(TIMER_NUM, prescaler=24, period=IRQ_PERIOD, callback=callback)
    while viper_idx_arr[0] < NUM_SAMPLES:
        pass
    tim.callback(None)
    tim.deinit()

    valid = [x for x in viper_lat_arr if x > 0 and x < IRQ_PERIOD]
    if valid:
        avg = sum(valid) // len(valid)
        mn = min(valid)
        mx = max(valid)
        print(f"{name}: min={mn} avg={avg} max={mx} ticks")
        print(f"  First 10: {list(viper_lat_arr[:10])}")
        return avg
    print(f"{name}: NO VALID SAMPLES")
    return -1


def test_callback(name, callback):
    global idx, latencies
    idx = 0
    for i in range(NUM_SAMPLES):
        latencies[i] = 0
    gc.collect()

    tim = Timer(TIMER_NUM, prescaler=24, period=IRQ_PERIOD, callback=callback)
    while idx < NUM_SAMPLES:
        pass
    tim.callback(None)
    tim.deinit()

    valid = [x for x in latencies if x > 0 and x < IRQ_PERIOD]
    if valid:
        avg = sum(valid) // len(valid)
        mn = min(valid)
        mx = max(valid)
        print(f"{name}: min={mn} avg={avg} max={mx} ticks")
        print(f"  First 10: {latencies[:10]}")
        return avg
    print(f"{name}: NO VALID SAMPLES")
    return -1


print("IRQ Latency Test - Counter Read Methods")
print("=" * 50)
print(f"Timer: TIM{TIMER_NUM}, Period: {IRQ_PERIOD} ticks @ 10MHz")
print("Each tick = 100ns")
print()

print("=== Functions ===")
test_callback("func t.counter()", func_cb)
test_callback("func mem32[]", func_mem32_cb)
test_callback("func viper_read()", viper_cb)
print()

print("=== Generators ===")
test_callback("gen t.counter()", gen_cb)
test_callback("gen @native t.counter()", native_gen_cb)
test_callback("gen mem32[]", gen_mem32_cb)
test_callback("gen @native mem32[]", native_gen_mem32_cb)
test_callback("gen viper_read()", gen_viper_cb)
print()

print("=== Direct Viper ===")
test_viper_direct("viper direct ptr32", viper_direct_cb)
