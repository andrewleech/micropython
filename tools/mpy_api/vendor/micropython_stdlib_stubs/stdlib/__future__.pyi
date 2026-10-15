from typing_extensions import TypeAlias

_VersionInfo: TypeAlias = tuple[int, int, int, str, int]

class _Feature:
    def __init__(self, optionalRelease: _VersionInfo, mandatoryRelease: _VersionInfo | None, compiler_flag: int) -> None: ...
    def getOptionalRelease(self) -> _VersionInfo: ...
    def getMandatoryRelease(self) -> _VersionInfo | None: ...
    compiler_flag: int

annotations: _Feature

