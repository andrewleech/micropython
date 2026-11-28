"""
categories: Core,Classes
description: A metaclass ``__prepare__`` method is not called.
cause: MicroPython always runs the class body with a ``dict`` as its namespace.
workaround: Process the class namespace in the metaclass ``__new__`` or ``__init__`` instead.
"""


class Meta(type):
    @classmethod
    def __prepare__(meta, name, bases):
        print("__prepare__", name)
        return {}


class A(metaclass=Meta):
    pass
