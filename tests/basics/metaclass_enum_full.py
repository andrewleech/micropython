# An Enum using the extra metaclass features: ordered class members (for
# auto() after explicit values and for aliases), type.__new__ and metaclass
# methods (for iteration, len, `in`, lookup by name and __members__).

try:
    from collections import OrderedDict
except ImportError:
    print("SKIP")
    raise SystemExit

if not hasattr(type, "__new__"):
    print("SKIP")
    raise SystemExit


class Record(type):
    def __init__(cls, name, bases, dct):
        cls.order = [k for k in dct if not k.startswith("_")]

    def __len__(cls):
        return len(cls.order)


class Probe(metaclass=Record):
    j = 0
    i = 0
    h = 0
    g = 0
    f = 0
    e = 0
    d = 0
    c = 0
    b = 0
    a = 0


try:
    ok = len(Probe) == 10 and Probe.order == list("jihgfedcba")
except TypeError:
    ok = False
if not ok:
    print("SKIP")
    raise SystemExit


class auto:
    pass


class EnumMeta(type):
    def __new__(meta, name, bases, dct):
        cls = super().__new__(meta, name, bases, dct)
        cls._by_name = OrderedDict()
        cls._by_value = OrderedDict()
        last = 0
        for k, v in list(dct.items()):
            if k[0] == "_" or callable(v):
                continue
            if isinstance(v, auto):
                v = last + 1
            last = v
            m = cls._by_value.get(v)
            if m is None:
                m = object.__new__(cls)
                m.name, m.value = k, v
                cls._by_value[v] = m
            cls._by_name[k] = m
            setattr(cls, k, m)
        return cls

    def __iter__(cls):
        return iter(list(cls._by_value.values()))

    def __len__(cls):
        return len(cls._by_value)

    def __getitem__(cls, name):
        return cls._by_name[name]

    @property
    def __members__(cls):
        return cls._by_name


class Enum(metaclass=EnumMeta):
    def __new__(cls, value):
        if isinstance(value, cls):
            return value
        try:
            return cls._by_value[value]
        except KeyError:
            raise ValueError("%r is not a valid %s" % (value, cls.__name__))

    def __repr__(self):
        return "<%s.%s: %r>" % (type(self).__name__, self.name, self.value)

    def __str__(self):
        return "%s.%s" % (type(self).__name__, self.name)


class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3
    CRIMSON = 1

    def hot(self):
        return self is Color.RED


class Status(Enum):
    IDLE = auto()
    BUSY = auto()
    ERROR = 10
    FATAL = auto()


print(Color.RED, repr(Color.GREEN), Color.BLUE.name, Color.BLUE.value)
print(Color.CRIMSON is Color.RED, Color.CRIMSON.name)
print(list(Color), len(Color), list(Color.__members__))
print(Color.RED in Color, Status.IDLE in Color, Color["GREEN"], Color["CRIMSON"])
print(Color(3), Color(Color.RED), Color.RED.hot(), Color.BLUE.hot())
print(list(Status), [m.value for m in Status])
print(isinstance(Color.RED, Color), type(Status.FATAL) is Status)
print({Color.RED: "stop", Color.GREEN: "go"}[Color.CRIMSON])
try:
    Color(4)
except ValueError as e:
    print("ValueError", e)
try:
    Color["PINK"]
except KeyError:
    print("KeyError")
