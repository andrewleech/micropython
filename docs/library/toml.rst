:mod:`toml` -- TOML encoding and decoding
=========================================

.. module:: toml
   :synopsis: TOML parsing

|see_cpython_module| :mod:`tomllib`.

This module allows conversion between Python objects and the TOML
(Tom's Obvious Minimal Language) data format.

Unlike JSON, the TOML format is particularly suited for configuration files.
TOML is designed to be easy to read due to obvious semantics, and is simpler
than other formats like YAML.

Currently, this module only supports reading/parsing TOML data, not writing it.
Future versions may add serialization support.

Functions
---------

.. function:: load(stream)

   Parse the given *stream*, interpreting it as a TOML document and
   deserializing the data to a Python object. The resulting object is
   returned.

   Parsing continues until end-of-file is encountered.
   A :exc:`ValueError` is raised if the data in *stream* is not correctly formed.

.. function:: loads(str)

   Parse the TOML *str* and return an object. Raises :exc:`ValueError` if the
   string is not correctly formed.

Data Types
---------

TOML data types are converted to Python types as follows:

* TOML strings → Python ``str``
* TOML integers → Python ``int``
* TOML floats → Python ``float``
* TOML booleans → Python ``bool``
* TOML dates → Python ``tuple`` (year, month, day)
* TOML times → Python ``tuple`` (hour, minute, second, microsecond)
* TOML datetimes → Python ``tuple`` (year, month, day, hour, minute, second, microsecond, timezone_offset_in_minutes)
* TOML arrays → Python ``list``
* TOML tables → Python ``dict``

Example
-------

Example TOML document::

    # This is a TOML document.

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

Parsing this document in MicroPython::

    import toml
    
    data = toml.loads(toml_string)  # toml_string contains the TOML document above
    
    print(data["title"])  # Outputs: TOML Example
    print(data["owner"]["name"])  # Outputs: Tom Preston-Werner
    print(data["database"]["ports"])  # Outputs: [8000, 8001, 8002]
    print(data["servers"]["alpha"]["ip"])  # Outputs: 10.0.0.1