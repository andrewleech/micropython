import sys
import json
from collections import OrderedDict as OD

def tracer(frame, event, arg):
    try:
        # print(repr(frame)   )
        # print(dir(frame))
        # for name in dir(frame):
        #         print(f"{name} = {getattr(frame, name)}")
        # # print(f"{json.dumps(frame)}")
        # print(f"{frame.f_globals=}")
        print(f"( {frame.f_lineno} , {frame.f_lasti} ) ", end="")
        # sort frame.f_locals by key , and add all k,v pairs to an OrderedDict
        f_locals = OD(sorted(frame.f_locals.items()))
        print(f"{f_locals=}")
        print( type(f_locals[list(f_locals)[0]]))

    except Exception as e:
        pass

    return tracer

granny = "granny"

def test():
    foo = "hello debugger"
    _under = "this is an under variable"
    __dunder = "this is a double under variable"
    bar = granny
    a = 100
    b = 2200
    c = 33333
    l = [1, 2, 3]
    d = {"a": 1, "b": 2, "c": 3}
    tuple1 = (1, 2, 3)
    tuple2 = (a, b, c,a)
    print(foo)
    print(a+b+c)
    long_name = a+ b + c
    print(long_name)

sys.settrace(tracer)
test()
sys.settrace(None)
