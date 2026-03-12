# test sys.settrace activating/deactivating mid-function (dual-VM branch-point switch)

import sys

try:
    sys.settrace
except AttributeError:
    print("SKIP")
    raise SystemExit


# --- helpers ---

events = []


def make_trace(tag):
    def trace(frame, event, arg):
        name = frame.f_code.co_name
        if not name.startswith("_") and name != "make_trace":
            events.append((tag, name, event))
        return trace

    return trace


def print_events(label):
    print(label)
    for ev in events:
        print(ev)
    events.clear()


# --- test 1: enable mid-loop, verify line events fire in same function ---


def test_enable_midloop():
    total = 0
    for i in range(6):
        if i == 3:
            sys.settrace(make_trace("T1"))
        total += i
    sys.settrace(None)
    return total


result = test_enable_midloop()
print(result)
print_events("test_enable_midloop")


# --- test 2: disable mid-loop (tracing->standard switch) ---


def test_disable_midloop():
    total = 0
    for i in range(6):
        if i == 3:
            sys.settrace(None)
        total += i
    return total


sys.settrace(make_trace("T2"))
result = test_disable_midloop()
# settrace(None) was called inside, but ensure it's off
sys.settrace(None)
print(result)
print_events("test_disable_midloop")


# --- test 3: nested call gets traced after mid-function enable ---


def helper_add(x):
    return x + 10


def test_nested_after_branch():
    total = 0
    for i in range(3):
        if i == 1:
            sys.settrace(make_trace("T3"))
        total += helper_add(i)
    sys.settrace(None)
    return total


result = test_nested_after_branch()
print(result)
print_events("test_nested_after_branch")


# --- test 4: toggle on/off/on using loop iterations as branch points ---


def test_toggle():
    total = 0
    for i in range(9):
        if i == 2:
            sys.settrace(make_trace("T4"))
        elif i == 5:
            sys.settrace(None)
        elif i == 7:
            sys.settrace(make_trace("T4"))
        total += i
    sys.settrace(None)
    return total


result = test_toggle()
print(result)
print_events("test_toggle")


# --- test 5: settrace active through exception handling ---


def test_exception():
    total = 0
    for i in range(3):
        if i == 1:
            sys.settrace(make_trace("T5"))
        total += i
    try:
        raise ValueError("test")
    except ValueError:
        total += 100
    sys.settrace(None)
    return total


result = test_exception()
print(result)
print_events("test_exception")


# --- test 6: return value integrity through multiple switches ---


def test_return_value():
    values = []
    for i in range(6):
        if i == 2:
            sys.settrace(make_trace("T6"))
        elif i == 4:
            sys.settrace(None)
        values.append(i * i)
    sys.settrace(None)
    return values


result = test_return_value()
print(result)
print_events("test_return_value")


# --- test 7: generator mid-iteration VM switch ---


def gen_counter(n):
    for i in range(n):
        yield i * 10


def test_generator_midswitch():
    g = gen_counter(5)
    results = []
    results.append(next(g))  # no tracing
    results.append(next(g))  # no tracing
    sys.settrace(make_trace("T7"))
    results.append(next(g))  # tracing starts
    results.append(next(g))
    results.append(next(g))
    sys.settrace(None)
    return results


result = test_generator_midswitch()
print(result)
print_events("test_generator_midswitch")


# --- test 8: trace callback disables itself ---


def self_disabling_trace(frame, event, arg):
    name = frame.f_code.co_name
    if not name.startswith("_") and name != "make_trace":
        events.append(("T8", name, event))
        if event == "line" and name == "test_self_disable":
            sys.settrace(None)
    return self_disabling_trace


def test_self_disable():
    total = 0
    for i in range(4):
        total += i
    return total


sys.settrace(self_disabling_trace)
result = test_self_disable()
sys.settrace(None)
print(result)
print_events("test_self_disable")
