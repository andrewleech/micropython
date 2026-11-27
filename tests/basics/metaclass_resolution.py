# Test that a class uses the most derived metaclass of all its bases.


class Probe(type):
    def __init__(cls, name, bases, dct):
        cls.probed = name


class P(metaclass=Probe):
    pass


class Plain:
    pass


class Q(Plain, P):
    pass


if Q.probed != "Q":
    print("SKIP")
    raise SystemExit


class Meta(type):
    def __init__(cls, name, bases, dct):
        print("Meta.__init__", name)


class SubMeta(Meta):
    def __init__(cls, name, bases, dct):
        print("SubMeta.__init__", name)


class OtherMeta(type):
    pass


class A(metaclass=Meta):
    pass


class S(metaclass=SubMeta):
    pass


class O(metaclass=OtherMeta):
    pass


# metaclass of a base other than the first
class B(Plain, A):
    pass


# the most derived metaclass wins, whichever base it comes from
class C(A, S):
    pass


class D(S, A):
    pass


# a subclass keeps the metaclass that was chosen for its base
class E(C):
    pass


# an explicit metaclass is replaced by a more derived one from a base
class F(S, metaclass=Meta):
    pass


# unrelated metaclasses conflict
try:

    class G(A, metaclass=OtherMeta):
        pass

except TypeError:
    print("TypeError")

try:

    class H(A, O):
        pass

except TypeError:
    print("TypeError")


# a metaclass that is a function is not inherited
def function_meta(name, bases, dct):
    print("function_meta", name)
    return type(name, bases, dct)


class I(metaclass=function_meta):
    pass


class J(I):
    pass


class K(I, A):
    pass
