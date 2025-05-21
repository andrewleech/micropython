# Test the toml module

try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit

# Basic types
toml_str = """
# This is a TOML document

title = "TOML Example"

[owner]
name = "Tom Preston-Werner"

[database]
enabled = true
ports = [ 8000, 8001, 8002 ]
data = [ ["delta", "phi"], [3.14] ]
temp_targets = { cpu = 79.5, case = 72.0 }

[servers]

[servers.alpha]
ip = "10.0.0.1"
role = "frontend"

[servers.beta]
ip = "10.0.0.2"
role = "backend"
"""

# Test loads() function
data = toml.loads(toml_str)

# Verify the parsed data
print(data["title"])
print(data["owner"]["name"])
print(data["database"]["enabled"])
print(len(data["database"]["ports"]))
print(data["database"]["ports"][0])
print(data["database"]["data"][0][0])
print(data["database"]["data"][1][0])
print(type(data["database"]["temp_targets"]["cpu"]))
print(data["database"]["temp_targets"]["cpu"])
print(data["servers"]["alpha"]["ip"])
print(data["servers"]["beta"]["role"])

# Test with bytearray input
data2 = toml.loads(bytearray(toml_str.encode()))
print(data2["title"] == data["title"])
