try:
    from io import StringIO
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

print(toml.load(StringIO('key = "value"')))
print(toml.load(StringIO('[table]\nkey = 123')))
print(toml.load(StringIO('array = [1, 2, 3]')))
print(toml.load(StringIO('bool = true')))
print(toml.load(StringIO('date = 1979-05-27')))
