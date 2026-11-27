# Test that subclasses inherit the metaclass of their first base.


def make_class(meta, name, bases, dct):
    if hasattr(type, "__new__"):
        # CPython
        return type.__new__(meta, name, bases, dct)
    else:
        # MicroPython: type has no __new__ attribute.
        return type(name, bases, dct)


class Meta(type):
    def __new__(meta, name, bases, dct):
        print("Meta.__new__", name, len(bases))
        return make_class(meta, name, bases, dct)

    def __init__(cls, name, bases, dct):
        print("Meta.__init__", name)


class OtherMeta(Meta):
    def __new__(meta, name, bases, dct):
        print("OtherMeta.__new__", name)
        return make_class(meta, name, bases, dct)


class A(metaclass=Meta):
    pass


# direct and indirect subclasses use the inherited metaclass
class B(A):
    pass


class C(B):
    pass


# an explicit metaclass takes precedence
class D(A, metaclass=OtherMeta):
    pass


# the metaclass is taken from the first base
class E:
    pass


class F(A, E):
    pass


print(issubclass(C, A), issubclass(D, A), issubclass(F, E))
