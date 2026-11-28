# Test that class keyword arguments are passed to the metaclass.


class Meta(type):
    def __new__(meta, name, bases, dct, **kw):
        if hasattr(type, "__new__"):
            cls = type.__new__(meta, name, bases, dct)
        else:
            cls = type(name, bases, dct)
        cls.options = kw
        return cls

    def __init__(cls, name, bases, dct, **kw):
        pass


class Probe(metaclass=Meta, probe=1):
    pass


if Probe.options != {"probe": 1}:
    print("SKIP")
    raise SystemExit


class Verbose(Meta):
    def __init__(cls, name, bases, dct, **kw):
        print("Verbose.__init__", name, sorted(kw.items()))


class A(metaclass=Verbose, size=3, label="a"):
    pass


# an inherited metaclass also gets the keywords
class B(A, size=4):
    pass


class C(A):
    pass


print(sorted(A.options.items()), B.options, C.options)
