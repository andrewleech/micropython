"""Run on the selected firmware to record actual API presence, not host typing."""

import json
import micropython
import sys


report = {
    "implementation": repr(sys.implementation),
    "platform": sys.platform,
    "version": sys.version,
    "capabilities": {
        "int.bit_count": hasattr(int, "bit_count"),
        "int.is_integer": hasattr(int, "is_integer"),
        "memoryview.cast": hasattr(memoryview, "cast"),
        "str.casefold": hasattr(str, "casefold"),
        "str.format_map": hasattr(str, "format_map"),
        "str.isascii": hasattr(str, "isascii"),
        "str.removeprefix": hasattr(str, "removeprefix"),
        "micropython.const": hasattr(micropython, "const"),
    },
}
try:
    execfile
except NameError:
    report["capabilities"]["execfile"] = False
else:
    report["capabilities"]["execfile"] = True
print(json.dumps(report))
