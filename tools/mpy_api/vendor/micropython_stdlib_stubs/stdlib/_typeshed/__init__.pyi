# Utility types for typeshed
#
# See the README.md file in this directory for more information.

from collections.abc import Iterable
from typing import Any
from typing import Protocol
from typing import SupportsFloat
from typing import SupportsIndex
from typing import SupportsInt
from typing import TypeVar
from typing_extensions import Buffer
from typing_extensions import TypeAlias

_KT = TypeVar("_KT")
_VT_co = TypeVar("_VT_co", covariant=True)
_T_co = TypeVar("_T_co", covariant=True)
_T_contra = TypeVar("_T_contra", contravariant=True)

# Alternative to `typing_extensions.Self`, exclusively for use with `__new__`
# in metaclasses:
#     def __new__(cls: type[Self], ...) -> Self: ...
# In other cases, use `typing_extensions.Self`.
Self = TypeVar("Self")  # noqa: Y001

# Marker for return types that include None, but where forcing the user to
# check for None can be detrimental. Sometimes called "the Any trick". See
# CONTRIBUTING.md for more information.
MaybeNone: TypeAlias = Any  # stable

# stable
class IdentityFunction(Protocol):
    def __call__(self, x: _T, /) -> _T: ...

# stable
class SupportsNext(Protocol[_T_co]):
    def __next__(self) -> _T_co: ...

# Comparison protocols

class SupportsDunderLT(Protocol[_T_contra]):
    def __lt__(self, other: _T_contra, /) -> bool: ...

class SupportsDunderGT(Protocol[_T_contra]):
    def __gt__(self, other: _T_contra, /) -> bool: ...

SupportsRichComparison: TypeAlias = SupportsDunderLT[Any] | SupportsDunderGT[Any]
SupportsRichComparisonT = TypeVar("SupportsRichComparisonT", bound=SupportsRichComparison)  # noqa: Y001

# Dunder protocols

class SupportsLenAndGetItem(Protocol[_T_co]):
    def __len__(self) -> int: ...
    def __getitem__(self, k: int, /) -> _T_co: ...

class SupportsTrunc(Protocol):
    def __trunc__(self) -> int: ...

# Mapping-like protocols

# stable
class SupportsKeysAndGetItem(Protocol[_KT, _VT_co]):
    def keys(self) -> Iterable[_KT]: ...
    def __getitem__(self, key: _KT, /) -> _VT_co: ...

# stable
class SupportsWrite(Protocol[_T_contra]):
    def write(self, s: _T_contra, /) -> object: ...

# Anything that implements the read-write buffer interface.
WriteableBuffer: TypeAlias = Buffer
# Same as WriteableBuffer, but also includes read-only buffer types (like bytes).
ReadableBuffer: TypeAlias = Buffer  # stable

# stable
from types import NoneType as NoneType
_T = TypeVar("_T")

# Anything that can be passed to the int/float constructors
ConvertibleToInt: TypeAlias = str | ReadableBuffer | SupportsInt | SupportsIndex | SupportsTrunc
ConvertibleToFloat: TypeAlias = str | ReadableBuffer | SupportsFloat | SupportsIndex

