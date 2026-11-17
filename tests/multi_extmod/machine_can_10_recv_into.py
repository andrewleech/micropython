import gc
import micropython
import struct
import sys
import time
from machine import CAN

# Instance 0 needs CAN.RX_RECORD_SIZE support. Both nodes need transceivers.
BITRATE = 500_000
STD_ID = 0x345
EXT_ID = 0x12345678
MESSAGES = (
    (STD_ID, b"\x01\x00\xff", 0),
    (EXT_ID, b"\x10\x20\x30\x40\x50", CAN.FLAG_EXT_ID),
    (STD_ID, b"a", 0),
    (STD_ID, b"bc", 0),
    (STD_ID, b"clean", 0),
    (STD_ID, b"fresh", 0),
    (STD_ID, b"clear", 0),
    (STD_ID, b"valid", 0),
    (STD_ID, b"old", 0),
    (STD_ID, b"new", 0),
    (STD_ID, b"rxbuf", 0),
    (STD_ID, b"restart", 0),
    (STD_ID, b"after", 0),
    (STD_ID, b"\x11\x22\x33\x44\x55\x66\x77\x88", 0),
    (STD_ID, b"nonzero!", CAN.FLAG_RTR),
    (STD_ID, b"fill", 0),
    (STD_ID, b"lost", 0),
)
irq_count = 0


def rx_irq(can):
    global irq_count
    irq_count += 1


def wait_pending(can, minimum=1):
    start = time.ticks_ms()
    while can.get_counters()[6] < minimum:
        assert time.ticks_diff(time.ticks_ms(), start) < 2000
        time.sleep_ms(1)


def wait_irq(count):
    start = time.ticks_ms()
    while irq_count < count:
        assert time.ticks_diff(time.ticks_ms(), start) < 2000
        time.sleep_ms(1)
    time.sleep_ms(20)
    assert irq_count == count


def request(index):
    multitest.broadcast("send {}".format(index))
    multitest.wait("sent {}".format(index))


def received(index):
    multitest.broadcast("received {}".format(index))


def expected_record(index, size, errors=0):
    can_id, payload, flags = MESSAGES[index]
    if flags & CAN.FLAG_RTR:
        payload = bytes(len(payload))
    return (
        struct.pack("<IHBB", can_id, flags, len(payload), errors)
        + payload
        + bytes(size - 8 - len(payload))
    )


def check_record(buffer, offset, index, errors=0):
    size = CAN.RX_RECORD_SIZE
    assert bytes(buffer[offset : offset + size]) == expected_record(index, size, errors)


def drain_one_into(can, index, errors=0):
    record = bytearray(CAN.RX_RECORD_SIZE + 3)
    record[-3:] = b"xyz"
    assert can.recv_into(record) == CAN.RX_RECORD_SIZE
    check_record(record, 0, index, errors)
    assert record[-3:] == b"xyz"


def instance0():
    if not hasattr(CAN, "RX_RECORD_SIZE"):
        multitest.skip()
    size = CAN.RX_RECORD_SIZE
    assert size >= 16
    can = CAN(1, BITRATE, rxbuf=4)
    can.set_filters(None)
    can.irq(rx_irq, trigger=CAN.IRQ_RX, hard=False)
    multitest.next()

    assert can.recv_into(bytearray(size)) == 0
    assert can.recv_into(bytearray(2 * size + 1)) == 0

    # A small or read-only destination must leave the oldest frame queued.
    request(0)
    wait_pending(can)
    wait_irq(1)
    try:
        can.recv_into(bytearray(size - 1))
        assert False
    except ValueError:
        pass
    try:
        can.recv_into(bytes(size))
        assert False
    except (TypeError, ValueError):
        pass
    assert can.get_counters()[6] >= 1
    received(0)
    drain_one_into(can, 0)
    assert can.recv_into(bytearray(size)) == 0
    print("empty and invalid destinations")

    # recv() and recv_into() consume one FIFO in order, without duplicates.
    request(1)
    wait_pending(can)
    received(1)
    request(2)
    wait_pending(can)
    received(2)
    request(3)
    time.sleep_ms(20)
    received(3)
    # Some controllers expose only a pending flag, not a frame count.
    # The sender handshake plus this settle interval gives the batch time to
    # reach the software queue without relying on a hardware FIFO depth.
    frame = can.recv()
    assert frame is not None
    assert (frame[0], bytes(frame[1]), frame[2], frame[3]) == (
        EXT_ID,
        MESSAGES[1][1],
        CAN.FLAG_EXT_ID,
        0,
    )
    batch = bytearray(2 * size + 5)
    batch[-5:] = b"spare"
    assert can.recv_into(batch) == 2 * size
    check_record(batch, 0, 2)
    check_record(batch, size, 3)
    assert batch[-5:] == b"spare"
    assert can.recv() is None
    assert can.recv_into(batch) == 0
    print("mixed FIFO and batch records")

    # Repeatedly cross the ring boundary with destination sizes that are not
    # multiples of the record size. Only complete records are returned.
    for index in (0, 1, 2, 3, 0):
        request(index)
        wait_pending(can)
        received(index)
        capacity = size + (index % 3) + 1
        target = bytearray(capacity)
        target[-1] = 0xA5
        assert can.recv_into(target) == size
        check_record(target, 0, index)
        assert target[-1] == 0xA5
    print("wrap and spare capacity")

    # No receive ring is an API error, not a hardware-frame read.
    can.deinit()
    can.init(BITRATE)
    can.set_filters(None)
    request(7)
    wait_pending(can)
    start = time.ticks_ms()
    try:
        can.recv_into(bytearray(size))
        assert False
    except RuntimeError:
        pass
    while True:
        frame = can.recv()
        if frame is not None:
            break
        assert time.ticks_diff(time.ticks_ms(), start) < 2000
        time.sleep_ms(1)
    assert (frame[0], bytes(frame[1]), frame[2], frame[3]) == (STD_ID, MESSAGES[7][1], 0, 0)
    received(7)
    can.init(BITRATE, rxbuf=4)
    can.set_filters(None)
    can.irq(rx_irq, trigger=CAN.IRQ_RX, hard=False)
    print("no ring and reinitialisation")
    # Invalid reinitialisation must preserve the old queue and its frame.
    request(6)
    wait_pending(can)
    received(6)
    try:
        can.init(BITRATE, rxbuf=-1)
        assert False
    except ValueError:
        pass
    drain_one_into(can, 6)
    print("invalid reinit preserves queue")

    # Restart discards unread software records; the next frame is readable.
    request(7)
    wait_pending(can)
    received(7)
    request(8)
    wait_pending(can)
    received(8)
    can.restart()
    assert can.recv() is None
    assert can.recv_into(bytearray(size)) == 0
    request(9)
    wait_pending(can)
    received(9)
    drain_one_into(can, 9)
    print("restart clears queue")

    for index in (13, 14):
        request(index)
        wait_pending(can)
        received(index)
    # Some controllers expose only a pending flag, not a frame count.
    time.sleep_ms(20)
    target = bytearray(2 * size)
    assert can.recv_into(target) == 2 * size
    check_record(target, 0, 13)
    check_record(target, size, 14)
    print("payload padding and RTR")

    # Empty-to-nonempty notification is rearmed when either API drains.
    base = irq_count
    request(0)
    wait_pending(can)
    wait_irq(base + 1)
    received(0)
    assert can.recv() is not None
    request(2)
    wait_pending(can)
    wait_irq(base + 2)
    received(2)
    drain_one_into(can, 2)
    print("IRQ rearm for both consumers")

    # Fill the software and hardware receive queues until hardware reports
    # overflow. The pending error is consumed by recv(), not repeated by the
    # following recv_into() call.
    can.init(BITRATE, rxbuf=1)
    can.set_filters(None)
    can.irq(None)
    overruns = can.get_counters()[7]
    for count in range(32):
        index = 15 + count % 2
        request(index)
        time.sleep_ms(20)
        received(index)
        if can.get_counters()[7] > overruns:
            break
    else:
        assert False, "hardware receive overflow was not reported"

    saw_overrun = False
    start = time.ticks_ms()
    while not saw_overrun:
        frame = can.recv()
        if frame is not None:
            saw_overrun = bool(frame[3] & CAN.RECV_ERR_OVERRUN)
        else:
            assert time.ticks_diff(time.ticks_ms(), start) < 2000
            time.sleep_ms(5)
    request(4)
    wait_pending(can)
    received(4)
    record = bytearray(size)
    assert can.recv_into(record) == size
    assert not record[7] & CAN.RECV_ERR_OVERRUN
    print("overflow error consumed once")

    # Reinitialisation clears a pending hardware overrun before the first
    # frame enters the new receive ring.
    overruns = can.get_counters()[7]
    for count in range(32):
        index = 15 + count % 2
        request(index)
        time.sleep_ms(20)
        received(index)
        if can.get_counters()[7] > overruns:
            break
    else:
        assert False, "hardware receive overflow was not reported"
    can.init(BITRATE, rxbuf=4)
    can.set_filters(None)
    request(5)
    wait_pending(can)
    received(5)
    record = bytearray(size)
    assert can.recv_into(record) == size
    check_record(record, 0, 5)
    print("reinit clears pending overrun")

    # Bulk consumption must not allocate after the destination exists.
    can.init(BITRATE, rxbuf=4)
    can.set_filters(None)
    request(10)
    wait_pending(can)
    received(10)
    target = bytearray(size)
    micropython.heap_lock()
    count = can.recv_into(target)
    micropython.heap_unlock()
    assert count == size
    check_record(target, 0, 10)
    print("heap-locked bulk read")

    # A failed allocation after teardown leaves the object deinitialised and
    # reusable. Deinit remains safe when called more than once.
    can.deinit()
    can.deinit()
    gc.collect()
    try:
        can.init(BITRATE, rxbuf=gc.mem_free())
        assert False
    except MemoryError:
        pass
    try:
        can.recv_into(bytearray(size))
        assert False
    except OSError:
        pass
    can.init(BITRATE, rxbuf=3)
    can.set_filters(None)
    request(8)
    wait_pending(can)
    received(8)
    drain_one_into(can, 8)
    can.deinit()
    print("deinit and allocation failure recovery")
    multitest.broadcast("done")


def instance1():
    can = CAN(1, BITRATE)
    multitest.next()
    while True:
        command = sys.stdin.readline().rstrip()
        if command == "BROADCAST done":
            break
        if not command.startswith("BROADCAST send "):
            continue
        index = int(command[15:])
        can_id, payload, flags = MESSAGES[index]
        start = time.ticks_ms()
        while can.send(can_id, payload, flags) is None:
            assert time.ticks_diff(time.ticks_ms(), start) < 2000
            time.sleep_ms(1)
        while can.get_counters()[5]:
            assert time.ticks_diff(time.ticks_ms(), start) < 2000
            time.sleep_ms(1)
        multitest.broadcast("sent {}".format(index))
        multitest.wait("received {}".format(index))
    can.deinit()
    print("sent OK")
