# Test TOML module with float values

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

# TOML document with float values
toml_str = """
pi = 3.14159
neg_pi = -3.14159
large = 1e6
small = 1e-6
"""

try:
    data = toml.loads(toml_str)
    print("Parsing succeeded")

    # Print the float values
    for key in ["pi", "neg_pi", "large", "small"]:
        if key in data:
            print(key + ":", data[key])
            print("type:", type(data[key]).__name__)

except Exception as e:
    print("Error:", type(e).__name__, "-", e)
