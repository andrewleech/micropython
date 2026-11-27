# Test a minimal Enum built on metaclass support, using `class Color(Enum):`.

try:
    object.__new__
except AttributeError:
    print("SKIP")
    raise SystemExit


class EnumMeta(type):
    def __new__(meta, name, bases, dct):
        members = [(k, v) for k, v in dct.items() if not k.startswith("_") and not callable(v)]
        if hasattr(type, "__new__"):
            cls = type.__new__(meta, name, bases, dct)
        else:
            cls = type(name, bases, dct)
        cls._value2member = {}
        for key, value in members:
            member = cls._value2member.get(value)
            if member is None:
                member = object.__new__(cls)
                member.name = key
                member.value = value
                cls._value2member[value] = member
            setattr(cls, key, member)
        return cls


class Enum(metaclass=EnumMeta):
    def __new__(cls, value):
        try:
            return cls._value2member[value]
        except KeyError:
            raise ValueError(value)


class Color(Enum):
    RED = 1
    GREEN = 2
    CRIMSON = 1

    def describe(self):
        return "%s=%d" % (self.name, self.value)


print(Color.GREEN.name, Color.GREEN.value, Color.GREEN.describe())
print(Color.CRIMSON is Color.RED, Color.RED.value)
print(Color(2) is Color.GREEN)
print(type(Color.RED) is Color, isinstance(Color.RED, Enum))
print(len(Color._value2member))
try:
    Color(3)
except ValueError as er:
    print("ValueError", er)


# enums are independent of each other
class Size(Enum):
    SMALL = 1


print(Size(1) is Size.SMALL, Size.SMALL is not Color.RED)
