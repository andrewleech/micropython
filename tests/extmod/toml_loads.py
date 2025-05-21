try:
    import toml
except ImportError:
    print("SKIP")
    raise SystemExit


def my_print(o):
    if isinstance(o, dict):
        print("sorted dict", sorted(o.items()))
    else:
        print(o)


# Simple values
my_print(toml.loads('key = "value"'))
my_print(toml.loads('key = 123'))
my_print(toml.loads('key = 123.456'))
my_print(toml.loads('key = true'))
my_print(toml.loads('key = false'))
my_print(toml.loads('key = 1979-05-27'))  # date
my_print(toml.loads('key = 07:32:00'))  # time
my_print(toml.loads('key = 1979-05-27T07:32:00'))  # datetime
my_print(toml.loads('key = 1979-05-27T07:32:00-08:00'))  # datetime with timezone

# Arrays
my_print(toml.loads('key = []'))
my_print(toml.loads('key = [1, 2, 3]'))
my_print(toml.loads('key = ["a", "b", "c"]'))
my_print(toml.loads('key = [1, "a", true]'))
my_print(toml.loads('key = [[1, 2], ["a", "b"]]'))  # array of arrays

# Tables
my_print(toml.loads('[table]'))
my_print(toml.loads('[table]\nkey = "value"'))
my_print(toml.loads('[table]\nkey1 = "value1"\nkey2 = 123'))

# Nested tables
my_print(toml.loads('[table.subtable]\nkey = "value"'))
my_print(toml.loads('[server]\nhost = "localhost"\n\n[server.database]\nport = 5432'))

# Array of tables
my_print(
    toml.loads(
        '[[products]]\nname = "Hammer"\nprice = 9.99\n\n[[products]]\nname = "Nail"\nprice = 0.10'
    )
)

# Whitespace handling
my_print(toml.loads('  key  =  "value"  '))
my_print(toml.loads('key = [ 1 , 2 , 3 ]'))

# Comments
my_print(toml.loads('key = "value" # This is a comment'))
my_print(toml.loads('# Full line comment\nkey = "value"'))

# Complex example
complex_toml = '''
# This is a full example

title = "TOML Example"

[owner]
name = "Tom Preston-Werner"
dob = 1979-05-27T07:32:00-08:00

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
'''

my_print(toml.loads(complex_toml))

# Error cases
try:
    toml.loads("")
except ValueError:
    print("ValueError: empty document")

try:
    toml.loads('key = "unterminated string')
except ValueError:
    print("ValueError: unterminated string")

try:
    toml.loads('key = 2019-02-30')  # Invalid date
except ValueError:
    print("ValueError: invalid date")

try:
    toml.loads('[table]\nkey = 1\n[table]')  # Duplicate table
except ValueError:
    print("ValueError: duplicate table")

try:
    toml.loads('key = .')  # Invalid value
except ValueError:
    print("ValueError: invalid value")
