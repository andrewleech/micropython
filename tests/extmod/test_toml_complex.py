# Test TOML module with complex data structures

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

# TOML document with nested structures
toml_str = """
# This is a TOML document with various types and structures

title = "TOML Complex Test"
integer = 42
float = 3.14159
boolean = true
string = "hello world"

# Array examples
integers = [1, 2, 3, 4, 5]
floats = [1.1, 2.2, 3.3]
strings = ["one", "two", "three"]
mixed = [1, 2.2, "three", true]
nested_array = [[1, 2], [3, 4, 5]]

# Table example
[database]
enabled = true
ports = [8000, 8001, 8002]
max_connections = 5000

# Nested tables
[servers]

[servers.alpha]
ip = "10.0.0.1"
role = "frontend"

[servers.beta]
ip = "10.0.0.2"
role = "backend"
load = 0.75
"""

try:
    data = toml.loads(toml_str)
    print("Parsing succeeded")

    # Check top-level values
    if "title" in data:
        print("title:", data["title"])
    if "integer" in data:
        print("integer:", data["integer"])
    if "float" in data:
        print("float:", data["float"])
    if "boolean" in data:
        print("boolean:", data["boolean"])

    # Check arrays
    if "integers" in data:
        print("integers length:", len(data["integers"]))
        print("integers[0]:", data["integers"][0])

    if "nested_array" in data:
        print("nested_array[0][1]:", data["nested_array"][0][1])

    # Check nested tables
    if "database" in data:
        if "enabled" in data["database"]:
            print("database.enabled:", data["database"]["enabled"])
        if "ports" in data["database"]:
            print("database.ports length:", len(data["database"]["ports"]))

    if "servers" in data:
        if "alpha" in data["servers"]:
            if "ip" in data["servers"]["alpha"]:
                print("servers.alpha.ip:", data["servers"]["alpha"]["ip"])

        if "beta" in data["servers"]:
            if "load" in data["servers"]["beta"]:
                print("servers.beta.load:", data["servers"]["beta"]["load"])

except Exception as e:
    print("Error:", type(e).__name__, "-", e)
