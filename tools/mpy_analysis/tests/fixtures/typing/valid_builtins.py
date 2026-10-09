import micropython
import time

_LIMIT = const(4)
_IMPORTED_LIMIT = micropython.const(8)


@micropython.viper
def fill(buffer):
    address = ptr8(buffer)
    address[0] = 42
    return int(address[0])


def timing() -> int:
    start = time.ticks_ms()
    return time.ticks_diff(time.ticks_add(start, 1), start)


def valid_core() -> tuple[str, int]:
    return " payload ".strip(), len(bytes([1, 2, 3]))
