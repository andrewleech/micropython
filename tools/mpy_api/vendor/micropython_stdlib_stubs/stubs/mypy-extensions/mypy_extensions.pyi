# These stubs are forked from typeshed, since we use some definitions that only make
# sense in the context of mypy/mypyc (in particular, native int types such as i64).

# Native int types such as i64 are magical and support implicit
# coercions to/from int using special logic in mypy. We generally only
# include operations here for which we have specialized primitives.

