from typing_extensions import ParamSpec
from collections.abc import Callable
from typing import Any
from typing import Literal
from typing import TypeVar

import _typeshed
from _typeshed import SupportsWrite
from typing_extensions import Concatenate
from typing_extensions import deprecated
_T = TypeVar("_T")
_R_co = TypeVar("_R_co", covariant=True)
_P = ParamSpec("_P")

_FuncT = TypeVar("_FuncT", bound=Callable[..., Any])

# These definitions have special processing in mypy
class ABCMeta(type):
    __abstractmethods__: frozenset[str]
    def __new__(
        mcls: type[_typeshed.Self], name: str, bases: tuple[type, ...], namespace: dict[str, Any], /, **kwargs: Any
    ) -> _typeshed.Self: ...

    def __instancecheck__(cls: ABCMeta, instance: Any) -> bool: ...
    def __subclasscheck__(cls: ABCMeta, subclass: type) -> bool: ...
    def _dump_registry(cls: ABCMeta, file: SupportsWrite[str] | None = None) -> None: ...
    def register(cls: ABCMeta, subclass: type[_T]) -> type[_T]: ...

def abstractmethod(funcobj: _FuncT) -> _FuncT: ...
@deprecated("Use 'classmethod' with 'abstractmethod' instead")
class abstractclassmethod(classmethod[_T, _P, _R_co]):
    __isabstractmethod__: Literal[True]
    def __init__(self, callable: Callable[Concatenate[type[_T], _P], _R_co]) -> None: ...

@deprecated("Use 'staticmethod' with 'abstractmethod' instead")
class abstractstaticmethod(staticmethod[_P, _R_co]):
    __isabstractmethod__: Literal[True]
    def __init__(self, callable: Callable[_P, _R_co]) -> None: ...

@deprecated("Use 'property' with 'abstractmethod' instead")
class abstractproperty(property):
    __isabstractmethod__: Literal[True]

class ABC(metaclass=ABCMeta):
    __slots__ = ()

