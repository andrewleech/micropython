# Test basic TOML functionality

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

# Simple TOML document
toml_str = """
title = "Basic Test"
integer = 42
string = "hello"
boolean = true
"""

# Parse the TOML
result = toml.loads(toml_str)

# Print the keys we received
print("Keys:", list(result.keys()))

# Check if we have the expected keys
if "title" in result:
    print("title:", result["title"])

if "integer" in result:
    print("integer:", result["integer"])

if "string" in result:
    print("string:", result["string"])

if "boolean" in result:
    print("boolean:", result["boolean"])
