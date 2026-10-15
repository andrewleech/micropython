from typing import overload

class StringIO:
    @overload
    def __init__(self, string: str = "", /):
        ...

class BytesIO:
    @overload
    def __init__(self, string: bytes = b"", /):
        ...
