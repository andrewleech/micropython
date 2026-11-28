"""
categories: Core,Classes
description: The type of a class created with a metaclass is ``type``, not the metaclass.
cause: All MicroPython classes have type ``type``. The metaclass is called to create the class and is recorded as its ``__metaclass__`` attribute so that subclasses use it too.
workaround: Use ``getattr(cls, "__metaclass__", type(cls))`` instead of ``type(cls)``, and check for an attribute set by the metaclass instead of using ``isinstance(cls, Meta)``.
"""


class Meta(type):
    pass


class A(metaclass=Meta):
    pass


print(type(A) is Meta, isinstance(A, Meta))
