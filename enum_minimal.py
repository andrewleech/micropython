"""
Minimal Enum implementation for MicroPython
Compatible with CPython's enum module (basic features only)

Size: ~250 lines, ~6 KB flash
Features: Basic Enum, IntEnum, iteration, value lookup, aliases
"""


class EnumMeta(type):
    """Metaclass for Enum"""

    def __new__(mcs, name, bases, namespace):
        # Extract enum members (non-callable, non-dunder attributes)
        member_names = []
        member_values = {}

        # Identify members (keep them in namespace for now)
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
                # Recursively check if int is in the MRO
                def has_int_in_mro(klass):
                    if klass is int:
                        return True
                    for base in getattr(klass, "__bases__", ()):
                        if has_int_in_mro(base):
                            return True
                    return False

                has_int_base = has_int_in_mro(cls)
                has_custom_new = "__new__" in cls.__dict__

                if has_int_base or has_custom_new:
                    # Use the class's __new__ method (e.g., for IntEnum)
                    member = cls.__new__(cls, member_value)
                    if not hasattr(member, "_value_"):
                        member._value_ = member_value
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
    Supports all integer operations.
    """

    def __new__(cls, value):
        """Create integer enum member"""
        # MicroPython limitation: int.__new__ is not accessible
        # Create enum member using object.__new__ and add int-like behavior via methods
        obj = object.__new__(cls)
        obj._value_ = value
        return obj

    # Integer operation methods to make IntEnum behave like int
    def __int__(self):
        return self._value_

    def __index__(self):
        return self._value_

    def __eq__(self, other):
        if type(other) is type(self):
            return self is other
        return self._value_ == other

    def __ne__(self, other):
        return not self.__eq__(other)

    def __lt__(self, other):
        return self._value_ < int(other)

    def __le__(self, other):
        return self._value_ <= int(other)

    def __gt__(self, other):
        return self._value_ > int(other)

    def __ge__(self, other):
        return self._value_ >= int(other)

    def __add__(self, other):
        return self._value_ + int(other)

    def __radd__(self, other):
        return int(other) + self._value_

    def __sub__(self, other):
        return self._value_ - int(other)

    def __rsub__(self, other):
        return int(other) - self._value_

    def __mul__(self, other):
        return self._value_ * int(other)

    def __rmul__(self, other):
        return int(other) * self._value_

    def __floordiv__(self, other):
        return self._value_ // int(other)

    def __rfloordiv__(self, other):
        return int(other) // self._value_

    def __mod__(self, other):
        return self._value_ % int(other)

    def __rmod__(self, other):
        return int(other) % self._value_

    def __and__(self, other):
        return self._value_ & int(other)

    def __rand__(self, other):
        return int(other) & self._value_

    def __or__(self, other):
        return self._value_ | int(other)

    def __ror__(self, other):
        return int(other) | self._value_

    def __xor__(self, other):
        return self._value_ ^ int(other)

    def __rxor__(self, other):
        return int(other) ^ self._value_


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


__all__ = ["Enum", "IntEnum", "unique", "EnumMeta"]
