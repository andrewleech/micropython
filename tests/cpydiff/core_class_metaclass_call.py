"""
categories: Core,Classes
description: A metaclass ``__call__`` method is not used when a class is called to create an instance.
cause: The type of all MicroPython classes is ``type``, so calling a class always uses ``type.__call__``.
workaround: Define ``__new__`` on the class, or on a common base class, instead.
"""


class Meta(type):
    def __call__(cls, *args):
        print("Meta.__call__", cls.__name__)
        return super().__call__(*args)


class A(metaclass=Meta):
    pass


print(type(A()) is A)
