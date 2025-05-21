# Test the toml module with a simple TOML document

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

# Simple TOML document with basic types
toml_str = """
# This is a simple TOML document
title = "TOML Test"
boolean = true
integer = 42
string = "hello"

[section]
key = "value"
"""

# Parse the TOML document
data = toml.loads(toml_str)

# Verify the parsed data
print(data["title"])
print(data["boolean"])
print(data["integer"])
print(data["string"])
print(data["section"]["key"])

# Test with bytearray input
data2 = toml.loads(bytearray(toml_str.encode()))
print(data2["title"] == data["title"])
