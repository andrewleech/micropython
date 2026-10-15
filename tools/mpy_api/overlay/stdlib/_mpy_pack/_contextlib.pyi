# The two context manager protocols of the CPython contextlib module, which the pack does not ship.
# Derived from typeshed's stdlib/contextlib.pyi (Apache License 2.0, see LICENSE_typeshed).
import abc
from types import TracebackType
from typing import Protocol, runtime_checkable
from typing_extensions import TypeVar

_T_co = TypeVar("_T_co", covariant=True)
_ExitT_co = TypeVar("_ExitT_co", covariant=True, bound=bool | None, default=bool | None)

@runtime_checkable
class AbstractContextManager(abc.ABC, Protocol[_T_co, _ExitT_co]):
    def __enter__(self) -> _T_co: ...
    @abc.abstractmethod
    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None, /
    ) -> _ExitT_co: ...

@runtime_checkable
class AbstractAsyncContextManager(abc.ABC, Protocol[_T_co, _ExitT_co]):
    async def __aenter__(self) -> _T_co: ...
    @abc.abstractmethod
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None, /
    ) -> _ExitT_co: ...
