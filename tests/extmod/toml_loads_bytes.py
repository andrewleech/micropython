# test loading from bytes and bytearray (introduced in Python 3.6)

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

print(toml.loads(b'key = "value"'))
print(toml.loads(bytearray(b'key = 123')))
