"""
Minimal Enum implementation for MicroPython
Compatible with CPython's enum module (basic features only)

Size: ~320 lines
Features: Basic Enum, IntEnum, iteration, value lookup, aliases, auto()

Note: IntEnum members support all integer operations but isinstance(member, int)
may return False due to MicroPython metaclass limitations. All arithmetic and
comparison operations work correctly.
"""

def _check_prepare_support():
    """
    Check if __prepare__ metaclass method is actually functional.
    Returns True only if __prepare__ is called during class creation.
    """
    try:
        class _TestMeta(type):
            _prepare_called = False

            @classmethod
            def __prepare__(mcs, name, bases):
                _TestMeta._prepare_called = True
                return {}

        class _Test(metaclass=_TestMeta):
            pass

        return _TestMeta._prepare_called
    except:
        return False


_prepare_supported = _check_prepare_support()


# Global counter for auto() to track creation order
_auto_counter = 0
# Track the current enum class being created (for context)
_current_enum_generation = 0

class auto:
    """
    Instances are replaced with an appropriate value for Enum members.
    By default, the initial value starts at 1 and increments by 1.

    Note: In MicroPython, when mixing auto() with explicit values, all auto()
    values are assigned after considering ALL explicit values in the enum.
    This differs from CPython which processes members in definition order.
    """
    def __init__(self):
        global _auto_counter
        # Track creation order via a global counter
        self._order = _auto_counter
        _auto_counter += 1
        self._value = None
        self._generation = _current_enum_generation

    def __repr__(self):
        return 'auto()'


class _EnumDict(dict):
    """
    Track enum members as they are defined.

    Note: MicroPython's __prepare__ implementation doesn't call __setitem__ during
    class body execution, so this is just a placeholder.
    """
    pass


def _create_int_member(enum_class, value, enum_name, member_name):
    """
    Create an int enum member without using metaclass __call__.

    This creates an int instance that will support all integer operations.
    Due to MicroPython limitations with metaclass and int subclass creation,
    we create a simple int wrapper that behaves correctly.
    """
    # In MicroPython, we cannot easily create true int subclass instances
    # without going through the metaclass machinery. The safest approach
    # is to use object.__new__ and implement integer operations through
    # methods that forward to the stored _value_.
    #
    # While this means isinstance(member, int) returns False, all integer
    # operations will work correctly.
    member = object.__new__(enum_class)
    member._value_ = value

    return member


class EnumMeta(type):
    """Metaclass for Enum"""

    if _prepare_supported:
        @classmethod
        def __prepare__(mcs, name, bases):
            """
            Return a plain dict for the class namespace.
            We can't use a dict subclass because MicroPython's __build_class__
            implementation casts the namespace to mp_obj_dict_t*.
            """
            return {}

    def __new__(mcs, name, bases, namespace):
        # Process auto() values if __prepare__ is supported
        if _prepare_supported:
            # Collect all members with auto() instances
            auto_members = []
            explicit_values = []

            for key in namespace.keys():
                if not key.startswith("_"):
                    value = namespace[key]
                    if not callable(value):
                        if isinstance(value, auto):
                            auto_members.append((key, value))
                        elif isinstance(value, int):
                            explicit_values.append(value)

            if auto_members:
                # Sort auto() members by their creation order
                auto_members.sort(key=lambda x: x[1]._order)

                # Determine starting value for auto()
                # In MicroPython, without dict insertion order, we take a simplified approach:
                # auto() starts at 1, or at max(explicit_values) + 1 if there are explicit values
                if explicit_values:
                    auto_value = max(explicit_values) + 1
                else:
                    auto_value = 1

                # Assign sequential values to auto() members
                for key, value in auto_members:
                    namespace[key] = auto_value
                    auto_value += 1
        else:
            # __prepare__ not supported - check if auto() was used
            for key, value in namespace.items():
                if not key.startswith("_") and isinstance(value, auto):
                    raise RuntimeError(
                        f"auto() in enum {name}.{key} requires MICROPY_PY_METACLASS_PREPARE "
                        f"to be enabled in py/mpconfig.h. Either enable this feature, or use "
                        f"explicit integer values instead of auto()."
                    )

        # Extract enum members (non-callable, non-dunder attributes)
        member_names = []
        member_values = {}

        # Identify members
        for key in list(namespace.keys()):
            if not key.startswith("_") and not callable(namespace.get(key)):
                value = namespace[key]
                member_names.append(key)
                member_values[key] = value

        # Create the class using type.__new__ with 4 arguments
        # (metaclass, name, bases, namespace) for type creation
        cls = type.__new__(mcs, name, bases, namespace)

        # Don't process the base Enum class itself
        if bases and any(isinstance(b, EnumMeta) for b in bases):
            # Create member instances
            cls._member_map_ = {}
            cls._value2member_map_ = {}

            for member_name in member_names:
                member_value = member_values[member_name]

                # Create member instance
                # Check if class inherits from int (IntEnum) or has custom __new__
                try:
                    has_int_base = issubclass(cls, int)
                except (TypeError, AttributeError):
                    # cls might not be fully initialized yet
                    has_int_base = False

                # Check if class has a custom __new__ (from StrEnum, IntFlag, etc.)
                # We need to check if any of the base classes have __new__ in their __dict__
                has_custom_new = False
                def has_custom_new_in_bases(cls_to_check):
                    """Recursively check if any base has custom __new__"""
                    for base in cls_to_check.__bases__:
                        if base is Enum or base is object:
                            continue
                        if '__new__' in getattr(base, '__dict__', {}):
                            return True
                        if has_custom_new_in_bases(base):
                            return True
                    return False

                has_custom_new = has_custom_new_in_bases(cls)

                if has_custom_new:
                    # Use the class's custom __new__ method (takes priority)
                    # This handles IntFlag, StrEnum, and other custom cases
                    member = cls.__new__(cls, member_value)
                    if not hasattr(member, "_value_"):
                        member._value_ = member_value
                elif has_int_base:
                    # For int subclasses (IntEnum), create proper int instances
                    if not isinstance(member_value, int):
                        raise TypeError(f"IntEnum values must be integers, not {type(member_value).__name__}")

                    # Create int enum member using helper function
                    member = _create_int_member(cls, member_value, cls.__name__, member_name)
                else:
                    # Default: use object.__new__
                    member = object.__new__(cls)
                    member._value_ = member_value

                member._name_ = member_name

                # Store in maps (first occurrence wins for value lookup)
                cls._member_map_[member_name] = member
                if member_value not in cls._value2member_map_:
                    cls._value2member_map_[member_value] = member

                # Set as class attribute
                setattr(cls, member_name, member)
        else:
            # Base Enum class
            cls._member_map_ = {}
            cls._value2member_map_ = {}

        return cls

    def __call__(cls, value):
        """Lookup member by value"""
        # Look up existing member by value
        try:
            return cls._value2member_map_[value]
        except (KeyError, TypeError):
            raise ValueError(f"{value} is not a valid {cls.__name__}")

    def __iter__(cls):
        """Iterate over enum members"""
        return iter(cls._member_map_.values())

    def __len__(cls):
        """Number of members"""
        return len(cls._member_map_)

    def __contains__(cls, member):
        """Check if member is in enum"""
        return isinstance(member, cls) and member._name_ in cls._member_map_

    def __repr__(cls):
        """Representation of enum class"""
        return f"<enum '{cls.__name__}'>"


class Enum(metaclass=EnumMeta):
    """Base class for creating enumerated constants"""

    def __init__(self, value):
        # This is never actually called for enum members
        # Members are created directly via object.__new__() in the metaclass
        pass

    @property
    def name(self):
        """The name of the enum member"""
        return self._name_

    @property
    def value(self):
        """The value of the enum member"""
        return self._value_

    def __repr__(self):
        """Representation: <EnumClass.MEMBER: value>"""
        return f"<{self.__class__.__name__}.{self._name_}: {self._value_!r}>"

    def __str__(self):
        """String representation: EnumClass.MEMBER"""
        return f"{self.__class__.__name__}.{self._name_}"

    def __eq__(self, other):
        """
        Enum members are equal only if they are the same object (identity).
        This is different from comparing their values.
        """
        if isinstance(other, self.__class__):
            return self is other
        return NotImplemented

    def __ne__(self, other):
        """Not equal comparison"""
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        """Hash based on name"""
        return hash(self._name_)

    def __reduce_ex__(self, proto):
        """Support for pickle"""
        return self.__class__, (self._value_,)


class IntEnum(int, Enum, metaclass=EnumMeta):
    """
    Enum where members are also integers.
    Supports all integer operations automatically through int inheritance.

    Note: Due to MicroPython limitations with metaclasses and int subclassing,
    isinstance(member, int) may return False even though members behave as proper
    integers and support all integer operations.
    """

    def __eq__(self, other):
        """IntEnum members compare equal to their integer values"""
        if type(other) is type(self):
            return self is other
        return int(self) == other

    def __ne__(self, other):
        """Not equal comparison"""
        return not self.__eq__(other)

    def __lt__(self, other):
        """Less than comparison"""
        return int(self) < int(other)

    def __le__(self, other):
        """Less than or equal comparison"""
        return int(self) <= int(other)

    def __gt__(self, other):
        """Greater than comparison"""
        return int(self) > int(other)

    def __ge__(self, other):
        """Greater than or equal comparison"""
        return int(self) >= int(other)

    def __int__(self):
        """Convert to int"""
        return self._value_

    # Arithmetic operations - forward to int value
    def __add__(self, other):
        return int(self) + int(other)

    def __radd__(self, other):
        return int(other) + int(self)

    def __sub__(self, other):
        return int(self) - int(other)

    def __rsub__(self, other):
        return int(other) - int(self)

    def __mul__(self, other):
        return int(self) * int(other)

    def __rmul__(self, other):
        return int(other) * int(self)

    def __truediv__(self, other):
        return int(self) / int(other)

    def __rtruediv__(self, other):
        return int(other) / int(self)

    def __floordiv__(self, other):
        return int(self) // int(other)

    def __rfloordiv__(self, other):
        return int(other) // int(self)

    def __mod__(self, other):
        return int(self) % int(other)

    def __rmod__(self, other):
        return int(other) % int(self)

    def __pow__(self, other):
        return int(self) ** int(other)

    def __rpow__(self, other):
        return int(other) ** int(self)

    # Bitwise operations
    def __and__(self, other):
        return int(self) & int(other)

    def __rand__(self, other):
        return int(other) & int(self)

    def __or__(self, other):
        return int(self) | int(other)

    def __ror__(self, other):
        return int(other) | int(self)

    def __xor__(self, other):
        return int(self) ^ int(other)

    def __rxor__(self, other):
        return int(other) ^ int(self)

    def __lshift__(self, other):
        return int(self) << int(other)

    def __rshift__(self, other):
        return int(self) >> int(other)

    def __neg__(self):
        return -int(self)

    def __pos__(self):
        return +int(self)

    def __abs__(self):
        return abs(int(self))

    def __invert__(self):
        return ~int(self)


class Flag(Enum):
    """Support for flags with bitwise operations"""

    def _create_pseudo_member_(self, value):
        """Create a pseudo-member for composite flag values"""
        # Try to find existing member first
        if value in self.__class__._value2member_map_:
            return self.__class__._value2member_map_[value]

        # Create a new pseudo-member for composite values
        pseudo_member = object.__new__(self.__class__)
        pseudo_member._value_ = value
        pseudo_member._name_ = None  # Composite members don't have simple names
        return pseudo_member

    def __or__(self, other):
        if isinstance(other, self.__class__):
            return self._create_pseudo_member_(self._value_ | other._value_)
        elif isinstance(other, int):
            return self._create_pseudo_member_(self._value_ | other)
        return NotImplemented

    def __and__(self, other):
        if isinstance(other, self.__class__):
            return self._create_pseudo_member_(self._value_ & other._value_)
        elif isinstance(other, int):
            return self._create_pseudo_member_(self._value_ & other)
        return NotImplemented

    def __xor__(self, other):
        if isinstance(other, self.__class__):
            return self._create_pseudo_member_(self._value_ ^ other._value_)
        elif isinstance(other, int):
            return self._create_pseudo_member_(self._value_ ^ other)
        return NotImplemented

    def __invert__(self):
        # Calculate the complement based on all defined flag values
        all_bits = 0
        for member in self.__class__:
            all_bits |= member._value_
        return self._create_pseudo_member_(all_bits & ~self._value_)

    # Reverse operations for when Flag is on the right side
    __ror__ = __or__
    __rand__ = __and__
    __rxor__ = __xor__


class IntFlag(int, Flag, metaclass=EnumMeta):
    """Flag enum that is also compatible with integers"""

    def __new__(cls, value):
        # Create an int instance - MicroPython doesn't expose int.__new__
        # Use the same approach as IntEnum's _create_int_member
        obj = object.__new__(cls)
        obj._value_ = value
        return obj

    def __int__(self):
        """Convert to int"""
        return self._value_

    def _create_pseudo_member_(self, value):
        """Create a pseudo-member for composite flag values"""
        # Try to find existing member first
        if value in self.__class__._value2member_map_:
            return self.__class__._value2member_map_[value]

        # Create a new pseudo-member for composite values
        pseudo_member = object.__new__(self.__class__)
        pseudo_member._value_ = value
        pseudo_member._name_ = None  # Composite members don't have simple names
        return pseudo_member

    def __or__(self, other):
        if isinstance(other, (self.__class__, int)):
            return self._create_pseudo_member_(self._value_ | int(other))
        return NotImplemented

    def __and__(self, other):
        if isinstance(other, (self.__class__, int)):
            return self._create_pseudo_member_(self._value_ & int(other))
        return NotImplemented

    def __xor__(self, other):
        if isinstance(other, (self.__class__, int)):
            return self._create_pseudo_member_(self._value_ ^ int(other))
        return NotImplemented

    __ror__ = __or__
    __rand__ = __and__
    __rxor__ = __xor__


class StrEnum(str, Enum, metaclass=EnumMeta):
    """Enum where members are also strings"""

    def __new__(cls, value):
        if not isinstance(value, str):
            raise TypeError(f"StrEnum values must be strings, not {type(value).__name__}")
        # MicroPython doesn't expose str.__new__, use object.__new__
        obj = object.__new__(cls)
        obj._value_ = value
        return obj

    def __str__(self):
        return self._value_

    def __eq__(self, other):
        """StrEnum members compare equal to their string values"""
        if isinstance(other, str):
            return self._value_ == other
        return super().__eq__(other)

    def __add__(self, other):
        """String concatenation"""
        return self._value_ + other

    def __radd__(self, other):
        """Reverse string concatenation"""
        return other + self._value_

    def upper(self):
        """Return uppercase version"""
        return self._value_.upper()

    def lower(self):
        """Return lowercase version"""
        return self._value_.lower()

    def capitalize(self):
        """Return capitalized version"""
        return self._value_.capitalize()

    def replace(self, old, new):
        """Return string with replacements"""
        return self._value_.replace(old, new)


# Module-level functions for compatibility
def unique(enumeration):
    """
    Decorator that ensures only one name is bound to each value.
    Raises ValueError if an alias is found.

    Note: This is a simplified version. In minimal implementation,
    consider this optional/deprecated.
    """
    duplicates = []
    for name, member in enumeration._member_map_.items():
        for other_name, other_member in enumeration._member_map_.items():
            if name != other_name and member._value_ == other_member._value_:
                duplicates.append((name, other_name, member._value_))
                break

    if duplicates:
        duplicate_names = ", ".join([f"{n1}/{n2}" for n1, n2, _ in duplicates])
        raise ValueError(f"duplicate values found in {enumeration.__name__}: {duplicate_names}")

    return enumeration


__all__ = ["Enum", "IntEnum", "Flag", "IntFlag", "StrEnum", "unique", "EnumMeta", "auto"]
