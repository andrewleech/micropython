from machine import CAN
import time

# Regression test for arrival-order delivery. Standard and extended id
# frames are received in hardware through independent FIFOs; a receiver
# that drains one FIFO to empty before looking at the other reorders
# frames whenever both id widths are on the bus at once. Filter
# configuration must route both id widths to the same FIFO for order to
# hold under load.
#
# Sent back to back with no inter-frame delay, unlike
# machine_can_03_rx_filters.py's 5ms gap: that gap gives each frame time
# to be drained before the next arrives, which cannot catch this class of
# bug regardless of filter configuration, since only one frame is ever in
# flight at a time.

can = CAN(1, 500_000)

# Interleaved std/ext ids, arrival order is exactly this list, and correct
# reception means the receiver reports them back in this order.
MESSAGES = [
    (0x101, 0),
    (0x1FFFFF01, CAN.FLAG_EXT_ID),
    (0x102, 0),
    (0x1FFFFF02, CAN.FLAG_EXT_ID),
    (0x103, 0),
    (0x1FFFFF03, CAN.FLAG_EXT_ID),
    (0x104, 0),
    (0x105, 0),
    (0x1FFFFF04, CAN.FLAG_EXT_ID),
    (0x1FFFFF05, CAN.FLAG_EXT_ID),
]


def instance0():
    can.set_filters([(0, 0, 0), (0, 0, CAN.FLAG_EXT_ID)])
    multitest.next()
    multitest.wait("sent")

    # Drain everything the sender put on the bus and report it in the order
    # it was received. A correct implementation reproduces MESSAGES exactly;
    # a FIFO-grouping bug reports every standard id first, then every
    # extended id, regardless of the order they were sent in.
    received = []
    deadline = time.ticks_add(time.ticks_ms(), 1000)
    while len(received) < len(MESSAGES) and time.ticks_diff(deadline, time.ticks_ms()) > 0:
        r = can.recv()
        if r is not None:
            can_id, _data, flags, _errors = r
            received.append((can_id, flags & CAN.FLAG_EXT_ID))

    for can_id, is_ext in received:
        print("recv", hex(can_id), "ext" if is_ext else "std")
    print("count", len(received))
    print("in order", received == MESSAGES)


def instance1():
    multitest.next()

    for can_id, flags in MESSAGES:
        r = can.send(can_id, b"", flags)
        if r is None:
            print("Failed to send:", hex(can_id))
        # Deliberately no sleep: the point of this test is frames still in
        # flight together, which is what a 5ms-apart send cannot produce.

    multitest.broadcast("sent")
