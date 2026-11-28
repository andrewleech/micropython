# Test that methods and properties of a metaclass can be used from its classes.


try:
    property
except NameError:
    print("SKIP")
    raise SystemExit


class Registry(type):
    def __init__(cls, name, bases, dct):
        cls._items = sorted(k for k in dct if not k.startswith("_"))

    def __len__(cls):
        return len(cls._items)

    def __iter__(cls):
        return iter(cls._items)

    def __getitem__(cls, key):
        return getattr(cls, key)

    def describe(cls):
        return cls.__name__ + ":" + ",".join(cls._items)

    @property
    def count(cls):
        return len(cls._items)


class Probe(metaclass=Registry):
    pass


try:
    len(Probe)
except TypeError:
    print("SKIP")
    raise SystemExit


class A(metaclass=Registry):
    x = 1
    y = 2


class Empty(metaclass=Registry):
    pass


class B(A):
    z = 3


print(len(A), bool(A), len(Empty), bool(Empty), len(B))
print(list(A), sorted(B), "x" in A, "q" in A, A["y"], B["x"])
print(A.describe(), B.describe(), A.count, B.count)

# instances do not see metaclass methods
print(hasattr(A(), "describe"), hasattr(A(), "count"))

# classes are still hashable and compare by identity
d = {A: "a", B: "b"}
print(d[A], d[B], A == A, A == B, A != B)


# classes without a metaclass, and native types, are unchanged
class Plain:
    pass


print(bool(Plain), hasattr(Plain, "describe"), hash(int) == hash(int))
for f in (len, iter, lambda c: c["x"]):
    for cls in (Plain, int):
        try:
            f(cls)
        except TypeError:
            print("TypeError")
