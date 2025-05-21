# Test the simplest possible TOML usage

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

# One key-value pair
toml_str = 'key = "value"'

try:
    data = toml.loads(toml_str)
    print("Parsing succeeded")

    # Try to access the parsed data
    if isinstance(data, dict):
        print("data is a dict")
        print("keys:", list(data.keys()))

        if "key" in data:
            value = data["key"]
            print("value:", value)
            print("type:", type(value).__name__)
    else:
        print("data is not a dict, it's:", type(data).__name__)

except Exception as e:
    import sys

    # Print the full error and traceback for debugging
    sys.print_exception(e)
    print("Error type:", type(e).__name__)
