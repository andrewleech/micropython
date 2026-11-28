.. _metaclasses:

Metaclasses
===========

MicroPython supports a subset of Python metaclasses, enough to write
class-creation hooks such as registries and a simple ``Enum``.

Core support
------------

These work at all ROM levels:

- ``class A(metaclass=Meta)``, where ``Meta`` is a subclass of ``type`` or
  any callable taking ``(name, bases, dct)``.
- A metaclass ``__new__`` and/or ``__init__``.  A metaclass ``__new__``
  creates the class by calling ``type(name, bases, dct)``.
- Subclasses inherit the metaclass of their first base class.
- ``super()`` in metaclass methods, such as
  ``super().__init__(name, bases, dct)``.

A minimal ``Enum`` using only the core support (``object.__new__`` needs the
core features ROM level or above)::

    class EnumMeta(type):
        def __init__(cls, name, bases, dct):
            cls._members = {}
            for k, v in list(dct.items()):
                if k[0] != "_" and not callable(v):
                    m = object.__new__(cls)
                    m.name, m.value = k, v
                    setattr(cls, k, cls._members.setdefault(v, m))

    class Enum(metaclass=EnumMeta):
        def __new__(cls, value):
            try:
                return cls._members[value]
            except KeyError:
                raise ValueError(value)

    class Color(Enum):
        RED = 1
        GREEN = 2

    print(Color.RED.name, Color(2) is Color.GREEN)

Class members are given to the metaclass in no particular order, so this
``Enum`` cannot rely on definition order, for example to pick the canonical
name of an alias.

Differences to CPython
----------------------

- The type of every class is ``type``.  ``type(cls)`` does not return the
  metaclass and ``isinstance(cls, Meta)`` is ``False``.
- The metaclass is recorded on the class as ``__metaclass__``, so that
  subclasses can find it.  It is visible in the class namespace given to
  the metaclass and as a class attribute.  A class created by calling the
  metaclass directly, instead of with a ``class`` statement, does not record
  it.
- ``__prepare__`` is not called; the class namespace is always a ``dict``.
- A metaclass ``__call__`` is not used when a class is called to create an
  instance.
- Only the first base class is used to find the metaclass.
- ``type.__new__`` is not provided, so a metaclass ``__new__`` cannot call
  ``super().__new__(meta, name, bases, dct)``.
- Keyword arguments in a class definition, other than ``metaclass``, are
  ignored.
- Methods and properties defined on a metaclass cannot be used from its
  classes, and metaclass special methods such as ``__len__`` and
  ``__iter__`` are not used for ``len(cls)`` or iteration over a class.
