"""
categories: Core,Classes
description: A class created with a metaclass has a ``__metaclass__`` attribute, which is also in the namespace given to the metaclass.
cause: The type of all MicroPython classes is ``type``, so the metaclass is recorded on the class for its subclasses to find.
workaround: Skip names that start with an underscore when processing the class namespace.
"""


class Meta(type):
    def __init__(cls, name, bases, dct):
        print("__metaclass__" in dct)


class A(metaclass=Meta):
    pass


print(hasattr(A, "__metaclass__"))
