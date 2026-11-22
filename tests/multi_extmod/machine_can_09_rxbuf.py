from machine import CAN
import gc
import micropython
import time
import sys

# The receiver is an STM32 with rxbuf support. Both nodes need CAN transceivers.
# A single exact-match filter routes the capacity test frames to one FIFO.
ID = 0x345
BITRATE = 500_000
SPECIAL_FRAMES = (
    (0x12345678, b"ext", CAN.FLAG_EXT_ID),
    (ID, b"\x11\x22\x33\x44\x55\x66\x77\x88", 0),
    (ID, b"request", CAN.FLAG_RTR),
)
irq_count = 0


def rx_irq(can):
    global irq_count
    irq_count += 1


def wait_pending(can, count):
    start = time.ticks_ms()
    while True:
        pending = can.get_counters()[6]
        assert pending <= count
        if pending == count:
            return
        assert time.ticks_diff(time.ticks_ms(), start) < 2000
        time.sleep_ms(1)


def wait_irq(count):
    start = time.ticks_ms()
    while irq_count < count:
        assert time.ticks_diff(time.ticks_ms(), start) < 2000
        time.sleep_ms(1)
    # Give any unwanted extra callback time to run.
    time.sleep_ms(10)
    assert irq_count == count


def configure(can, capacity):
    can.init(BITRATE, rxbuf=capacity)
    can.set_filters([(ID, 0x7FF, 0)])
    can.irq(rx_irq, trigger=CAN.IRQ_RX, hard=False)


def receive_frame(can, value):
    result = can.recv()
    assert result is not None
    can_id, data, flags, errors = result
    assert can_id == ID and bytes(data) == bytes([value % 256]) and flags == 0
    assert not errors & CAN.RECV_ERR_OVERRUN


def request_frame(can, value, pending):
    multitest.broadcast("send {}".format(value))
    multitest.wait("sent {}".format(value))
    wait_pending(can, pending)
    # The receiver has observed this frame before the sender submits another.
    multitest.broadcast("received {}".format(value))


def overflow_depth(can, capacity, value):
    configure(can, capacity)
    # Measure the first lost frame without draining the single hardware FIFO.
    for count in range(1, 129):
        multitest.broadcast("send {}".format(value))
        multitest.wait("sent {}".format(value))
        value += 1
        time.sleep_ms(20)
        counters = can.get_counters()
        multitest.broadcast("received {}".format(value - 1))
        if counters[7]:
            first = can.recv()
            assert first is not None and first[3] & CAN.RECV_ERR_OVERRUN
            while True:
                frame = can.recv()
                if frame is None:
                    break
                assert not frame[3] & CAN.RECV_ERR_OVERRUN
            wait_pending(can, 0)
            time.sleep_ms(20)
            return count - 1, value
        # Some ports report a pending flag, not a frame count.
        assert counters[6] > 0
    assert False, "receive FIFO depth exceeds test limit"


def instance0():
    try:
        probe = CAN(1, BITRATE, rxbuf=1)
    except TypeError:
        multitest.skip()
    probe.deinit()
    multitest.next()

    # A request larger than the available heap forces allocation failure.
    gc.collect()
    try:
        CAN(1, BITRATE, rxbuf=gc.mem_free())
        assert False
    except MemoryError:
        print("fresh allocation failure")

    can = CAN(1, BITRATE, rxbuf=1)
    can.set_filters([(ID, 0x7FF, 0)])
    can.irq(rx_irq, trigger=CAN.IRQ_RX, hard=False)
    value = 0

    depth, value = overflow_depth(can, 0, value)
    for capacity in (1, 2, 3, 5):
        actual, value = overflow_depth(can, capacity, value)
        assert actual == depth + capacity
    print("ring capacity")
    # Reinitialisation discards a pending overrun and queued frames.
    configure(can, 1)
    for count in range(128):
        multitest.broadcast("send {}".format(value))
        multitest.wait("sent {}".format(value))
        time.sleep_ms(20)
        multitest.broadcast("received {}".format(value))
        value += 1
        if can.get_counters()[7]:
            break
    else:
        assert False, "hardware receive overflow was not reported"
    configure(can, 3)
    request_frame(can, value, 1)
    result = can.recv()
    assert result is not None and result[3] == 0
    assert bytes(result[1]) == bytes([value % 256])
    wait_pending(can, 0)
    value += 1
    print("reinit clears pending overrun")
    configure(can, 1)

    for capacity in (1, 2, 3, 5):
        if capacity != 1:
            configure(can, capacity)
        for cycle in range(4):
            base = irq_count
            # One extra frame is queued after the software ring fills.
            for n in range(capacity + 1):
                request_frame(can, value + n, n + 1)
                wait_irq(base + 1)
            for n in range(capacity + 1):
                receive_frame(can, value + n)
                wait_pending(can, capacity - n)
            assert can.recv() is None
            wait_irq(base + 1)
            assert can.get_counters()[7] == 0
            value += capacity + 1
            # Shift the start index before the next fill.
            request_frame(can, value, 1)
            wait_irq(base + 2)
            receive_frame(can, value)
            wait_pending(can, 0)
            wait_irq(base + 2)
            value += 1
        print("fill drain wrap", capacity)

        # Leave one frame in hardware, so its receive interrupt is masked.
        base = irq_count
        for n in range(capacity + 1):
            request_frame(can, value + n, n + 1)
            wait_irq(base + 1)
        retained_value = value + capacity
        value += capacity + 1
        can.restart()
        time.sleep_ms(20)
        pending = can.get_counters()[6]
        assert pending <= 1
        if pending:
            # Some controllers retain the hardware FIFO across restart.
            # Its interrupt must be rearmed before recv can drain the ring.
            wait_irq(base + 2)
            receive_frame(can, retained_value)
            wait_pending(can, 0)
            base += 1
        assert can.recv() is None
        request_frame(can, value, 1)
        wait_irq(base + 2)
        receive_frame(can, value)
        wait_pending(can, 0)
        value += 1
        print("restart", capacity)

    # A buffered controller must receive without installing a Python IRQ.
    can.deinit()
    can.init(BITRATE, rxbuf=3)
    can.set_filters([(ID, 0x7FF, 0)])
    request_frame(can, value, 1)
    time.sleep_ms(20)
    receive_frame(can, value)
    wait_pending(can, 0)
    value += 1
    print("poll without callback")

    # A returned frame owns its data, while a supplied result is reused.
    request_frame(can, value, 1)
    first = can.recv()
    assert first is not None
    request_frame(can, value + 1, 1)
    result = [0, memoryview(bytearray(64)), 0, 0]
    micropython.heap_lock()
    try:
        reused = can.recv(result)
    finally:
        micropython.heap_unlock()
    assert reused is result
    assert (result[0], bytes(result[1]), result[2], result[3]) == (
        ID,
        bytes([(value + 1) % 256]),
        0,
        0,
    )
    gc.collect()
    assert (first[0], bytes(first[1]), first[2], first[3]) == (ID, bytes([value % 256]), 0, 0)
    wait_pending(can, 0)
    value += 2
    print("receive data ownership")

    # Pre-teardown validation must leave the old controller functional.
    request_frame(can, value, 1)
    for invalid in (-1, 0x7FFFFFFF):
        try:
            can.init(BITRATE, rxbuf=invalid)
            assert False
        except ValueError:
            pass
    # The frame queued before the invalid calls remains available.
    receive_frame(can, value)
    wait_pending(can, 0)
    value += 1
    print("invalid reinit preserves receiver")

    # A failure after teardown leaves an uninitialised, reusable object.
    gc.collect()
    try:
        can.init(BITRATE, rxbuf=gc.mem_free())
        assert False
    except MemoryError:
        pass
    try:
        can.recv()
        assert False
    except OSError:
        pass
    can.deinit()
    can.deinit()
    configure(can, 3)
    request_frame(can, value, 1)
    receive_frame(can, value)
    wait_pending(can, 0)
    print("allocation failure reinit")

    can.set_filters(None)
    for index, (can_id, payload, flags) in enumerate(SPECIAL_FRAMES):
        request_frame(can, -index - 1, 1)
        assert can.recv(result) is result
        expected_data = bytes(len(payload)) if flags & CAN.FLAG_RTR else payload
        assert (result[0], bytes(result[1]), result[2], result[3]) == (
            can_id,
            expected_data,
            flags,
            0,
        )
        wait_pending(can, 0)
    print("extended full payload and RTR")
    multitest.broadcast("done")


def instance1():
    can = CAN(1, BITRATE)
    multitest.next()

    # Only one frame is submitted at a time. The receiver acknowledges each
    # observation before the next send, so payload order is also wire order.
    while True:
        command = sys.stdin.readline().rstrip()
        if command == "BROADCAST done":
            break
        if not command.startswith("BROADCAST send "):
            continue
        value = int(command[15:])
        start = time.ticks_ms()
        if value < 0:
            can_id, payload, flags = SPECIAL_FRAMES[-value - 1]
        else:
            can_id, payload, flags = ID, bytes([value % 256]), 0
        while can.send(can_id, payload, flags) is None:
            assert time.ticks_diff(time.ticks_ms(), start) < 2000
            time.sleep_ms(1)
        while can.get_counters()[5]:
            assert time.ticks_diff(time.ticks_ms(), start) < 2000
            time.sleep_ms(1)
        multitest.broadcast("sent {}".format(value))
        multitest.wait("received {}".format(value))
    print("sent OK")
