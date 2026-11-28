# Test type.__new__ and super().__new__ in a metaclass.

if not hasattr(type, "__new__"):
    print("SKIP")
    raise SystemExit


class Meta(type):
    def __new__(meta, name, bases, dct):
        print("Meta.__new__", name)
        cls = super().__new__(meta, name, bases, dct)
        cls.made_by = meta.__name__
        return cls

    def __init__(cls, name, bases, dct):
        print("Meta.__init__", name)
        super().__init__(name, bases, dct)


class A(metaclass=Meta):
    x = 1


class B(A):
    pass


print(A.x, A.made_by, B.made_by, type(A()) is A, type(B()) is B, issubclass(B, A))


# a metaclass without __new__ inherits type.__new__
class InitOnly(type):
    def __init__(cls, name, bases, dct):
        print("InitOnly.__init__", name)


class C(metaclass=InitOnly):
    pass


print(type(C()) is C)

# called directly
D = type.__new__(Meta, "D", (A,), {"y": 2})
print(D.__name__, D.x, D.y, issubclass(D, A))
try:
    type.__new__(type, 1)
except TypeError:
    print("TypeError")
try:
    type.__new__(type, "E", ())
except TypeError:
    print("TypeError")
