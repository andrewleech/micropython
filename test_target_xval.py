MODULE_CONST = 99


class ContextManager(object):
    def __init__(self, value):
        self.value = value
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        return False


def simple_branching(x):
    if x < 0:
        return "negative"
    elif x == 0:
        return "zero"
    else:
        return "positive"


def for_loop_with_break(items):
    result = []
    for item in items:
        if item == 99:
            break
        result.append(item)
    else:
        result.append("completed")
    return result


def while_loop(n):
    count = 0
    total = 0
    while count < n:
        total = total + count
        count = count + 1
    return total


def try_except_finally(should_raise):
    result = "start"
    try:
        if should_raise:
            _ = 1 / 0
        result = "try_success"
    except ZeroDivisionError:
        result = "caught_error"
    finally:
        result = result + "_finalized"
    return result


def nested_closure(x):
    def inner(y):
        return x + y

    return inner(10)


class MyClass(object):
    def __init__(self, value):
        self.value = value
        self.computed = False

    def compute(self):
        self.computed = True
        return self.value * 2

    def check_value(self):
        if self.value > 50:
            return "large"
        else:
            return "small"


def ternary_expression(x):
    return "even" if x % 2 == 0 else "odd"


def multiple_returns(code):
    if code == 1:
        return "first"
    if code == 2:
        return "second"
    if code == 3:
        return "third"
    return "default"


def with_statement():
    ctx = ContextManager(42)
    with ctx:
        value = ctx.value
    return value


def run_partial():
    results = []

    results.append(simple_branching(-5))
    results.append(simple_branching(0))

    results.append(for_loop_with_break([1, 2, 3]))

    results.append(while_loop(3))

    results.append(try_except_finally(False))

    results.append(nested_closure(5))

    obj = MyClass(30)
    results.append(obj.check_value())

    results.append(ternary_expression(4))

    results.append(multiple_returns(1))

    results.append(with_statement())

    return results


def run_full():
    results = []

    results.append(simple_branching(-5))
    results.append(simple_branching(0))
    results.append(simple_branching(10))

    results.append(for_loop_with_break([1, 2, 3]))
    results.append(for_loop_with_break([99, 1, 2]))

    results.append(while_loop(0))
    results.append(while_loop(5))

    results.append(try_except_finally(False))
    results.append(try_except_finally(True))

    results.append(nested_closure(5))
    results.append(nested_closure(20))

    obj1 = MyClass(30)
    results.append(obj1.check_value())
    results.append(obj1.compute())

    obj2 = MyClass(100)
    results.append(obj2.check_value())
    results.append(obj2.compute())

    results.append(ternary_expression(4))
    results.append(ternary_expression(5))

    results.append(multiple_returns(1))
    results.append(multiple_returns(2))
    results.append(multiple_returns(3))
    results.append(multiple_returns(99))

    results.append(with_statement())

    return results
