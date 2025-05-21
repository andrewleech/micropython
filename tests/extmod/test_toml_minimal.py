# Test the minimal TOML module functionality

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

# Very basic TOML document
toml_str = 'title = "Simple Test"'

# Try to parse it
try:
    data = toml.loads(toml_str)
    print("Parsing succeeded")

    # Check if any data was returned
    if "title" in data:
        print("Title:", data["title"])
    else:
        print("No title found")

    # Print the keys we got
    for key in data:
        print("Key:", key)
except Exception as e:
    print("Error:", type(e).__name__, "-", e)
