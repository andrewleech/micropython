
from typing import Dict
from typing import Generic
from typing import overload
from typing_extensions import TypeVar

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import MutableMapping
from collections.abc import MutableSequence

_T = TypeVar("_T")
_KT = TypeVar("_KT")
_VT = TypeVar("_VT")

class deque(MutableSequence[_T]):
    @overload
    def __init__(self, iterable: Iterable[_T], maxlen: int | None = None) -> None: ...
    def append(self, x: _T, /) -> None:  # type: ignore
        ...
    def pop(self) -> _T:  # type: ignore
        ...
    def __contains__(self, key: object, /) -> bool: ...

class Counter(dict[_T, int], Generic[_T]):
    ...

class OrderedDict(Dict[_KT, _VT], Generic[_KT, _VT]):  # type: ignore
    ...

class defaultdict(dict[_KT, _VT]):  # type: ignore
    @overload
    def __init__(
        self: defaultdict[str, _VT],  # pyright: ignore[reportInvalidTypeVarUse]  #11780
        default_factory: Callable[[], _VT] | None,
        /,
        **kwargs: _VT,  # type: ignore
    ) -> None: ...

class ChainMap(MutableMapping[_KT, _VT]):  # type: ignore
    ...
