import time


def incompatible(value: int) -> str:
    result: str = value
    return result


def invalid_delay() -> None:
    time.sleep_ms("one")
