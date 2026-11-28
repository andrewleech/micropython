# Test that a metaclass is given the class members in definition order.


def public(dct):
    return [k for k in dct if not k.startswith("_")]


class Record(type):
    def __init__(cls, name, bases, dct):
        cls.order = public(dct)


class Probe(metaclass=Record):
    m = 0
    l = 0
    k = 0
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


if Probe.order != list("mlkjihgfedcba"):
    print("SKIP")
    raise SystemExit


class auto:
    pass


# Values that follow on from the member defined before them.
class Counted(type):
    def __init__(cls, name, bases, dct):
        last = 0
        for k in public(dct):
            v = dct[k]
            if callable(v):
                continue
            if isinstance(v, auto):
                v = last + 1
                setattr(cls, k, v)
            last = v


class E(metaclass=Counted):
    ZULU = auto()
    YANKEE = 10
    XRAY = auto()
    WHISKEY = auto()

    def total(self):
        return self.ZULU + self.YANKEE + self.XRAY + self.WHISKEY


print(E.ZULU, E.YANKEE, E.XRAY, E.WHISKEY, E().total())


# A subclass inherits the metaclass, so its members are ordered too.
class F(E):
    VICTOR = 5
    UNIFORM = auto()


print(F.VICTOR, F.UNIFORM, F.ZULU)


# Class attributes can still be added and removed.
F.extra = 1
del F.extra
print(hasattr(F, "extra"), F().total())
