import micropython
import time

_BAD_LIMIT = const(1, 2)


@micropython.viper
def bad_pointer(buffer):
    address = ptr8(buffer)
    address[0] = "bad"


def bad_timing():
    time.ticks_diff("bad", 1)


def unavailable_core():
    return (1).bit_count(), memoryview(b"x").cast("B"), "text".casefold()
