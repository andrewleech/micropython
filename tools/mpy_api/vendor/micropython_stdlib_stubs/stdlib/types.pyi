
# pytype crashes if types.MappingProxyType inherits from collections.abc.Mapping instead of typing.Mapping
from typing import Any
from typing import Mapping
from typing import TypeVar
from typing import final
_KT = TypeVar("_KT")

# Note, all classes "defined" here require special handling.

_VT_co = TypeVar("_VT_co", covariant=True)

# Make sure this class definition stays roughly in line with `builtins.function`
@final
class FunctionType:
    ...

@final
class CodeType:
    ...

@final
class MappingProxyType(Mapping[_KT, _VT_co]):
    ...

class ModuleType:
    ...

@final
class MethodType:
    ...

@final
class BuiltinFunctionType:
    ...

@final
class WrapperDescriptorType:
    ...

@final
class MethodWrapperType:
    ...

@final
class MethodDescriptorType:
    ...

@final
class TracebackType:
    ...

def get_original_bases(cls: type, /) -> tuple[Any, ...]: ...

class GenericAlias:
    ...

@final
class NoneType:
    ...

@final
class EllipsisType: ...

@final
class UnionType:
    ...

