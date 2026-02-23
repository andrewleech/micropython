# Test fixture for coverage trials.

MODULE_VAR = 42


def branching(x):
    if x > 0:
        return "positive"
    elif x == 0:
        return "zero"
    else:
        return "negative"


def with_nested():
    result = []

    def inner():
        result.append("inner called")

    inner()
    return result


class MyClass:
    def __init__(self, value):
        self.value = value

    def method_a(self):
        return self.value * 2

    def method_b(self):
        return self.value + 1


def run():
    # Exercise some but not all paths
    branching(1)  # takes "positive" branch only
    obj = MyClass(10)
    obj.method_a()  # called
    # obj.method_b() is NOT called
    # with_nested() is NOT called
    return True
