"""Source segments of frozen sources come from lines split once per file and equal ``ast.get_source_segment``."""
import ast
from pathlib import Path

import pytest

from mpy_api.frozen import _source_segments

TOOLS = Path(__file__).resolve().parents[2]

FIXTURES = [
    "x = 1\ny = x + 2\n",
    "def f(a, b=2, *c, d, **e):\n    return (a +\n            b)\n",
    "name = 'caf\u00e9'\nvalue = name.upper() + '\u4e2d\u6587'.lower()\n",
    "a = 1\r\nb = (a +\r\n     2)\r\nc = f'{a}' f'{b}'\r\n",
    "a = (1 +\r2)\rb = a\r",
    "a = 1\r\r\nb = 2\n\rc = 3",
    "x = 1\x0c\ny = (2 +\x0c\n     3)\n",
    "class C:\n\tdef m(self):\n\t\treturn self.\\\n\t\t\tattr\n",
    "f(a, key=value, *args, **kw)\nobj.attr.deeper[0].call(x)\n",
    "last = 1",
    "",
]


def check(text):
    tree = ast.parse(text)
    segment = _source_segments(text)
    nodes = list(ast.walk(tree))
    for node in nodes:
        assert segment(node) == ast.get_source_segment(text, node), ast.dump(node)
    return len(nodes)


@pytest.mark.parametrize("text", FIXTURES)
def test_segments_equal_get_source_segment_on_fixtures(text):
    check(text)


def test_nodes_without_a_location_have_no_segment():
    segment = _source_segments("x = 1\n")
    assert segment(ast.Load()) is None and segment(ast.Name(id="x", ctx=ast.Load())) is None
    assert segment(ast.parse("x = 1\n")) is None


def test_segments_equal_get_source_segment_on_python_files():
    # ``ast.get_source_segment`` splits the whole text at every call, so only small files are compared.
    files = [path for path in sorted(TOOLS.glob("mpy_api/*.py")) + sorted(TOOLS.glob("*.py")) if path.stat().st_size < 9000]
    assert len(files) > 5
    total = 0
    for path in files:
        total += check(path.read_text(encoding="utf-8"))
    assert total > 3000
