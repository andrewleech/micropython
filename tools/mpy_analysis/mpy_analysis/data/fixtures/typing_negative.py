import time
import micropython


def compatible(value: int) -> str:
    return str(value)


def valid_delay() -> int:
    time.sleep_ms(1)
    return micropython.const(2) + time.ticks_diff(time.ticks_ms(), time.ticks_ms())
