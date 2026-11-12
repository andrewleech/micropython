from collections.abc import Iterable
from collections.abc import Sequence
from typing import Any
from typing import Generic
from typing import MutableSequence
from typing import overload
from typing_extensions import TypeVar

_T = TypeVar("_T", int, float, str)

class array(MutableSequence[_T], Generic[_T]):
    @overload
    def __init__(self, typecode: str, initializer: Iterable[_T], /) -> None: ...
    def append(self, val: Any, /) -> None:
        ...
    def extend(self, iterable: Sequence[Any], /) -> None:
        ...

    def __len__(self) -> int:
        ...
    @overload
    def __getitem__(self, index: int) -> _T:
        ...
