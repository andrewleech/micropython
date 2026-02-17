# Test asyncio.arepl code-analysis helper functions.
try:
    from asyncio.arepl import _import_name, _is_global_assign, _has_assignment
except ImportError:
    print("SKIP")
    raise SystemExit

# _import_name: "import foo"
print(_import_name("import os"))
print(_import_name("import os as myos"))
print(_import_name("from os import path"))
print(_import_name("from os import path as p"))
print(_import_name("x = 1"))
print(_import_name("print('hello')"))

# _is_global_assign: "name = ..."
print(_is_global_assign("x = 1"))
print(_is_global_assign("my_var = [1, 2]"))
print(_is_global_assign("x == 1"))
print(_is_global_assign("print('hello')"))
print(_is_global_assign("1 + 2"))

# _has_assignment: any = not part of ==, !=, <=, >=
print(_has_assignment("x = 1"))
print(_has_assignment("x == 1"))
print(_has_assignment("x != 1"))
print(_has_assignment("x <= 1"))
print(_has_assignment("x >= 1"))
print(_has_assignment("x += 1"))
print(_has_assignment("1 + 2"))
print(_has_assignment("a, b = 1, 2"))

print("helpers_ok")
