# Enum Extensions Implementation Plan

## Overview

This document outlines the implementation plan for optional enum features beyond the core Enum and IntEnum classes. These features are designed to be conditionally compiled based on ROM size levels, allowing ports to balance functionality against code size constraints.

## Feature Summary

| Feature | Description | Size Estimate | ROM Level | CPython Version |
|---------|-------------|---------------|-----------|-----------------|
| Flag/IntFlag | Bitwise flag operations | ~200 bytes | EXTRA_FEATURES | 3.6+ |
| StrEnum | String-valued enums | ~50 bytes | EXTRA_FEATURES | 3.11+ |
| auto() | Automatic value generation | ~150 bytes | FULL_FEATURES | 3.6+ |
| @unique | Enforce no duplicate values | ~80 bytes | EXTRA_FEATURES | 3.4+ |

**Total size impact**: ~480 bytes for all optional features

## Conditional Compilation Flags

### Configuration in py/mpconfig.h

```c
// Whether to support Flag and IntFlag enums for bitwise operations
// This enables combining enum members with | & ^ operators
// Requires MICROPY_PY_METACLASS_OPS
#ifndef MICROPY_PY_ENUM_FLAG
#define MICROPY_PY_ENUM_FLAG (MICROPY_CONFIG_ROM_LEVEL_AT_LEAST_EXTRA_FEATURES)
#endif

// Whether to support StrEnum for string-valued enums (Python 3.11+)
// Minimal overhead, just a thin wrapper around Enum
#ifndef MICROPY_PY_ENUM_STRENUM
#define MICROPY_PY_ENUM_STRENUM (MICROPY_CONFIG_ROM_LEVEL_AT_LEAST_EXTRA_FEATURES)
#endif

// Whether to support auto() for automatic value generation
// Requires __prepare__ support for OrderedDict tracking
#ifndef MICROPY_PY_ENUM_AUTO
#define MICROPY_PY_ENUM_AUTO (MICROPY_CONFIG_ROM_LEVEL_AT_LEAST_FULL_FEATURES)
#endif

// Whether to support @unique decorator for enforcing unique values
// Simple decorator, minimal overhead
#ifndef MICROPY_PY_ENUM_UNIQUE
#define MICROPY_PY_ENUM_UNIQUE (MICROPY_CONFIG_ROM_LEVEL_AT_LEAST_EXTRA_FEATURES)
#endif
```

### Dependencies

- **Flag/IntFlag**: Requires `MICROPY_PY_METACLASS_OPS` (already implemented)
- **auto()**: Requires `__prepare__` metaclass support (needs implementation)
- **StrEnum**: No special dependencies
- **@unique**: No special dependencies

## Feature 1: Flag and IntFlag

### CPython Compatibility

```python
from enum import Flag, IntFlag, auto

class Permission(Flag):
    READ = 1
    WRITE = 2
    EXECUTE = 4

# Bitwise operations
Permission.READ | Permission.WRITE  # Permission.READ|WRITE (value: 3)
Permission.READ & Permission.WRITE  # <Permission: 0>
~Permission.READ                    # Permission.WRITE|EXECUTE (value: 6)

class Status(IntFlag):
    READY = 1
    WAITING = 2
    DONE = 4

# IntFlag works with integers
Status.READY | 8  # Status.READY|8 (value: 9)
```

### Implementation Details

**In enum.py (conditionally compiled):**

```python
#if MICROPY_PY_ENUM_FLAG

class FlagMeta(EnumMeta):
    """Metaclass for Flag enums"""

    def __new__(mcs, name, bases, namespace):
        cls = EnumMeta.__new__(mcs, name, bases, namespace)

        # Generate combination members for all possible flag combinations
        if bases and any(isinstance(b, FlagMeta) for b in bases):
            cls._generate_combinations()

        return cls

    def _generate_combinations(cls):
        """Create pseudo-members for flag combinations"""
        # This is optional optimization for CPython compatibility
        # Can be simplified for MicroPython
        pass


class Flag(Enum, metaclass=FlagMeta):
    """Base class for flag enums (bitwise operations)"""

    def __or__(self, other):
        if isinstance(other, self.__class__):
            return self.__class__(self._value_ | other._value_)
        return NotImplemented

    def __and__(self, other):
        if isinstance(other, self.__class__):
            return self.__class__(self._value_ & other._value_)
        return NotImplemented

    def __xor__(self, other):
        if isinstance(other, self.__class__):
            return self.__class__(self._value_ ^ other._value_)
        return NotImplemented

    def __invert__(self):
        # Calculate all possible values
        all_bits = 0
        for member in self.__class__:
            all_bits |= member._value_
        return self.__class__(all_bits & ~self._value_)

    def __repr__(self):
        # Show combined flags as FLAG1|FLAG2
        if self._value_ == 0:
            return f"<{self.__class__.__name__}: 0>"

        # Find which flags are set
        flags = []
        for member in self.__class__._member_map_.values():
            if member._value_ and (self._value_ & member._value_) == member._value_:
                flags.append(member._name_)

        if flags:
            return f"<{self.__class__.__name__}.{('|'.join(flags))}: {self._value_}>"
        return f"<{self.__class__.__name__}: {self._value_}>"


class IntFlag(int, Flag, metaclass=FlagMeta):
    """Flag enum where members are also integers"""

    def __new__(cls, value):
        obj = object.__new__(cls)
        obj._value_ = value
        return obj

    def __or__(self, other):
        if isinstance(other, self.__class__):
            return self.__class__(self._value_ | other._value_)
        if isinstance(other, int):
            return self.__class__(self._value_ | other)
        return NotImplemented

    def __and__(self, other):
        if isinstance(other, self.__class__):
            return self.__class__(self._value_ & other._value_)
        if isinstance(other, int):
            return self.__class__(self._value_ & other)
        return NotImplemented

    # Include __ror__, __rand__ for right-side operations
    __ror__ = __or__
    __rand__ = __and__

#endif  // MICROPY_PY_ENUM_FLAG
```

### Size Impact

- **Core implementation**: ~180 bytes (Flag/IntFlag classes, operators)
- **Metaclass support**: ~20 bytes (FlagMeta)
- **Total**: ~200 bytes

### Testing

```python
# test_enum_flag.py
from enum import Flag, IntFlag

class Color(Flag):
    RED = 1
    GREEN = 2
    BLUE = 4

# Test bitwise OR
assert (Color.RED | Color.GREEN)._value_ == 3

# Test bitwise AND
assert (Color.RED & Color.GREEN)._value_ == 0
assert (Color.RED & Color.RED) is Color.RED

# Test invert
inverted = ~Color.RED
assert inverted._value_ == 6  # GREEN | BLUE

# Test IntFlag with integers
class Status(IntFlag):
    READY = 1
    DONE = 2

assert (Status.READY | 4)._value_ == 5
```

## Feature 2: StrEnum

### CPython Compatibility

```python
from enum import StrEnum

class Color(StrEnum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"

# String operations
Color.RED.upper()     # "RED"
Color.RED + "_color"  # "red_color"
Color.RED == "red"    # True
```

### Implementation Details

**In enum.py (conditionally compiled):**

```python
#if MICROPY_PY_ENUM_STRENUM

class StrEnum(str, Enum):
    """Enum where members are also strings"""

    def __new__(cls, value):
        if not isinstance(value, str):
            raise TypeError(f"StrEnum values must be strings, not {type(value).__name__}")
        obj = str.__new__(cls, value)
        obj._value_ = value
        return obj

    def __str__(self):
        return self._value_

    # Inherit all string methods from str base class
    # No need to override arithmetic/comparison operators

#endif  // MICROPY_PY_ENUM_STRENUM
```

### Size Impact

- **Core implementation**: ~50 bytes
- Very lightweight, just a thin wrapper

### Testing

```python
# test_enum_strenum.py
from enum import StrEnum

class Color(StrEnum):
    RED = "red"
    GREEN = "green"

# Test string operations
assert Color.RED.upper() == "RED"
assert Color.RED + "_color" == "red_color"
assert Color.RED == "red"
assert str(Color.RED) == "red"

# Test enum properties still work
assert Color.RED.name == "RED"
assert Color("red") is Color.RED
```

## Feature 3: auto()

### CPython Compatibility

```python
from enum import Enum, auto

class Color(Enum):
    RED = auto()    # 1
    GREEN = auto()  # 2
    BLUE = auto()   # 3

class Status(Enum):
    PENDING = auto()  # 1
    ACTIVE = 10
    DONE = auto()     # 11 (continues from last value)
```

### Implementation Details

This is the most complex feature as it requires `__prepare__` support.

**In enum.py (conditionally compiled):**

```python
#if MICROPY_PY_ENUM_AUTO

class _AutoValue:
    """Sentinel for automatic value generation"""
    _counter = 0

    def __init__(self):
        _AutoValue._counter += 1
        self.value = _AutoValue._counter


def auto():
    """Generate automatic values for enum members"""
    return _AutoValue()


class EnumMeta(type):
    """Enhanced metaclass with auto() support"""

    @classmethod
    def __prepare__(mcs, name, bases):
        """Return an ordered namespace for tracking member order"""
        # Need to track insertion order for auto()
        return {}  # In MicroPython, dicts are already ordered

    def __new__(mcs, name, bases, namespace):
        # Process auto() values
        auto_counter = 0
        for key, value in list(namespace.items()):
            if isinstance(value, _AutoValue):
                auto_counter += 1
                namespace[key] = auto_counter
            elif not key.startswith('_') and not callable(value):
                # Update auto counter based on manual values
                if isinstance(value, int) and value > auto_counter:
                    auto_counter = value

        # Reset auto counter for next enum
        _AutoValue._counter = 0

        # Continue with normal enum creation
        return type.__new__(mcs, name, bases, namespace)

#endif  // MICROPY_PY_ENUM_AUTO
```

### C Support Required

**In py/objtype.c:**

```c
#if MICROPY_PY_ENUM_AUTO

// Add __prepare__ support to type_call
// This allows metaclasses to return a custom namespace dict
// for class creation (needed for auto() to track insertion order)

static mp_obj_t type_call(mp_obj_t self_in, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    // ... existing code ...

    // Check if metaclass has __prepare__
    mp_obj_t prepare[2] = {MP_OBJ_NULL};
    mp_load_method_maybe(metaclass, MP_QSTR___prepare__, prepare);

    if (prepare[0] != MP_OBJ_NULL) {
        // Call __prepare__(name, bases) to get namespace
        mp_obj_t namespace = mp_call_method_n_kw(2, 0, prepare, name, bases);
        // Use this namespace instead of empty dict
    }

    // ... continue with normal type creation ...
}

#endif  // MICROPY_PY_ENUM_AUTO
```

### Size Impact

- **Python code**: ~100 bytes (auto() function, _AutoValue class)
- **C code**: ~50 bytes (__prepare__ support in objtype.c)
- **Total**: ~150 bytes

### Testing

```python
# test_enum_auto.py
from enum import Enum, auto

class Color(Enum):
    RED = auto()
    GREEN = auto()
    BLUE = auto()

assert Color.RED.value == 1
assert Color.GREEN.value == 2
assert Color.BLUE.value == 3

# Test with mixed auto/manual values
class Status(Enum):
    PENDING = auto()  # 1
    ACTIVE = 10
    DONE = auto()     # 11

assert Status.PENDING.value == 1
assert Status.ACTIVE.value == 10
assert Status.DONE.value == 11
```

## Feature 4: @unique Decorator

### CPython Compatibility

```python
from enum import Enum, unique

@unique
class Color(Enum):
    RED = 1
    GREEN = 2
    CRIMSON = 1  # ValueError: duplicate values found in Color: RED/CRIMSON
```

### Implementation Details

**In enum.py (conditionally compiled):**

```python
#if MICROPY_PY_ENUM_UNIQUE

def unique(enumeration):
    """
    Class decorator that ensures only one name is bound to each value.
    Raises ValueError if any aliases are found.
    """
    seen = {}
    duplicates = []

    for name, member in enumeration._member_map_.items():
        value = member._value_
        if value in seen:
            duplicates.append((seen[value], name))
        else:
            seen[value] = name

    if duplicates:
        alias_details = ', '.join(f"{first}/{second}" for first, second in duplicates)
        raise ValueError(f"duplicate values found in {enumeration.__name__}: {alias_details}")

    return enumeration

#endif  // MICROPY_PY_ENUM_UNIQUE
```

### Size Impact

- **Implementation**: ~80 bytes
- Simple decorator function, minimal overhead

### Testing

```python
# test_enum_unique.py
from enum import Enum, unique

# Should work fine
@unique
class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3

# Should raise ValueError
try:
    @unique
    class Status(Enum):
        PENDING = 1
        WAITING = 1  # Duplicate!
    assert False, "Should have raised ValueError"
except ValueError as e:
    assert "duplicate values" in str(e)
```

## Implementation Strategy

### Phase 1: Flag/IntFlag (Highest Priority)

1. Add `MICROPY_PY_ENUM_FLAG` to mpconfig.h
2. Implement Flag and IntFlag classes in enum.py
3. Add conditional compilation guards
4. Write comprehensive tests
5. Measure size impact on STM32
6. Update documentation

**Estimated effort**: 4-6 hours

### Phase 2: StrEnum (Low Complexity)

1. Add `MICROPY_PY_ENUM_STRENUM` to mpconfig.h
2. Implement StrEnum class in enum.py
3. Write tests
4. Measure size impact
5. Update documentation

**Estimated effort**: 2-3 hours

### Phase 3: @unique Decorator (Low Complexity)

1. Add `MICROPY_PY_ENUM_UNIQUE` to mpconfig.h
2. Implement unique() decorator in enum.py
3. Write tests
4. Update documentation

**Estimated effort**: 2-3 hours

### Phase 4: auto() (Highest Complexity)

1. Add `MICROPY_PY_ENUM_AUTO` to mpconfig.h
2. Implement __prepare__ support in py/objtype.c
3. Implement auto() in enum.py with _AutoValue
4. Write comprehensive tests
5. Measure size impact
6. Update documentation

**Estimated effort**: 8-12 hours (due to C changes)

## Size Budget Analysis

### Baseline

Current implementation: 370,812 bytes (STM32 PYBV10)

### With All Extensions

| Configuration | Size | Change | % Change |
|---------------|------|--------|----------|
| Baseline + enum.py | 370,812 | - | - |
| + Flag/IntFlag | 371,012 | +200 | +0.054% |
| + StrEnum | 371,062 | +250 | +0.068% |
| + @unique | 371,142 | +330 | +0.089% |
| + auto() | 371,292 | +480 | +0.130% |

**Total overhead**: 480 bytes (0.13% increase over baseline)

### Recommendations by ROM Level

| ROM Level | Recommended Features | Total Size |
|-----------|---------------------|------------|
| CORE_FEATURES | Enum, IntEnum only | +2,536 bytes |
| EXTRA_FEATURES | + Flag, StrEnum, @unique | +2,866 bytes |
| FULL_FEATURES | + auto() | +3,016 bytes |

## Testing Plan

### Unit Tests

- `test_enum_flag.py`: Flag/IntFlag bitwise operations
- `test_enum_strenum.py`: String enum operations
- `test_enum_unique.py`: Duplicate detection
- `test_enum_auto.py`: Automatic value generation
- `test_enum_combined.py`: Features working together

### CPython Compatibility Tests

Run all tests on both CPython 3.11+ and MicroPython to verify identical behavior.

### Size Regression Tests

Build STM32 firmware with each feature enabled/disabled and verify size impact matches estimates.

## Documentation Updates

### Files to Update

1. **CPYTHON_COMPATIBILITY.md**: Add sections for each new feature
2. **SIZE_REPORT.md**: Update with new size measurements
3. **enum.py docstrings**: Document all new classes and functions
4. **README or USAGE.md**: Add examples for each feature

## Backwards Compatibility

All features are additive and behind conditional compilation flags. Existing code using Enum/IntEnum will continue to work unchanged. Ports can selectively enable features based on ROM constraints.

## Future Enhancements (Out of Scope)

The following CPython features are not planned for implementation due to complexity or size constraints:

- `_missing_()` hook - Custom handling for missing values
- `_ignore_` attribute - Excluding members from the enum
- Functional API - `Status = Enum('Status', 'READY DONE')` syntax
- Complex auto() customization via `_generate_next_value_`
- Multi-bit flag aliases (e.g., `READ_WRITE = READ | WRITE`)

## Summary

This implementation plan provides a clear path to add optional enum features while maintaining:

1. **CPython compatibility** for all supported features
2. **Minimal size impact** (~480 bytes total)
3. **Flexible configuration** via ROM level flags
4. **Clean separation** of features for maintenance

The phased approach allows implementing high-value, low-complexity features first (Flag, StrEnum, @unique) while deferring the more complex auto() feature until C support is ready.
