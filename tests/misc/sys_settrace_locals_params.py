# test sys.settrace() frame.f_locals exposes function parameters under
# their real source names, alongside body locals (regression guard for a
# bug where parameters were filtered out of f_locals)

import sys

try:
    sys.settrace
except AttributeError:
    print("SKIP")
    raise SystemExit


def target(a, b, c):
    x = a + b + c
    return x


def trace(frame, event, arg):
    # "x" is only bound once its assignment statement has executed, i.e.
    # from the "line" event for "return x" onwards, so keying on it proves
    # f_locals is tracking the frame live and not just showing a snapshot
    # taken at function entry. Restrict to the "line" event so the check
    # runs exactly once (the same frame/locals also reach the following
    # "return" event).
    if event == "line" and frame.f_code.co_name == "target" and "x" in frame.f_locals:
        print(sorted(frame.f_locals.keys()))
        sys.settrace(None)
    return trace


sys.settrace(trace)
target(1, 2, 3)
sys.settrace(None)
