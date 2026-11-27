# A minimal Enum that needs only the core metaclass support: a metaclass
# __init__, metaclass inheritance and object.__new__.  auto() values are
# numbered in creation order, so this does not need ordered class members.

if not hasattr(object, "__new__"):
    print("SKIP")
    raise SystemExit


class auto:
    _count = 0

    def __init__(self):
        auto._count += 1
        self._seq = auto._count


class EnumMeta(type):
    def __init__(cls, name, bases, dct):
        cls._members = {}
        items = [(k, v) for k, v in dct.items() if k[0] != "_" and not callable(v)]
        autos = sorted((v._seq, k) for k, v in items if isinstance(v, auto))
        values = dict(items)
        for i, (_, k) in enumerate(autos):
            values[k] = i + 1
        for k, v in values.items():
            m = object.__new__(cls)
            m.name, m.value = k, v
            setattr(cls, k, cls._members.setdefault(v, m))


class Enum(metaclass=EnumMeta):
    def __new__(cls, value):
        try:
            return cls._members[value]
        except KeyError:
            raise ValueError(value)

    def __repr__(self):
        return "<%s.%s: %r>" % (type(self).__name__, self.name, self.value)


class Color(Enum):
    RED = 1
    GREEN = 2

    def hot(self):
        return self is Color.RED


class Shape(Enum):
    SQUARE = auto()
    CIRCLE = auto()
    TRIANGLE = auto()


print(Color.RED, Color.RED.name, Color.RED.value)
print(Color(2), Color(1) is Color.RED, Color.RED.hot(), Color.GREEN.hot())
print(isinstance(Color.GREEN, Color), type(Color.GREEN) is Color)
print(Shape.SQUARE, Shape.CIRCLE, Shape.TRIANGLE, Shape(3) is Shape.TRIANGLE)
try:
    Color(3)
except ValueError as e:
    print("ValueError", e)
