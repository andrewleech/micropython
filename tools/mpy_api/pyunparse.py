"""Python source text for an AST node, equal to ``ast.unparse`` without recursion.

The running interpreter's own unparser renders every node, so the text follows
that interpreter's version. ``_Unparser`` changes only how a node reaches its
children: ``traverse`` writes a placeholder for the child and queues it, and
``visit`` renders the queued nodes from a worklist, each into its own part list,
before joining the parts in place of the placeholders. A queued node renders
with the unparser state its parent had when reaching it (indentation, the
``except*`` and interactive contexts, the type-ignore comments and whether any
output precedes it); precedences persist on the unparser. Every node therefore
renders as it would inline, and nesting depth never reaches the call stack.

The unparser renders an f-string replacement field with a fresh unparser whose
text the caller inspects at once, so those fields cannot be queued. ``unparse``
renders them first, innermost first, and a fresh unparser asked for one of them
returns the stored text.
"""

from __future__ import annotations

import ast
import sys
from contextvars import ContextVar

try:
    from _ast_unparse import Unparser as _Base
except ImportError:
    _Base = ast._Unparser

_Precedence = sys.modules[_Base.__module__]._Precedence
# Constructor arguments the unparser passes to the fresh unparser of a replacement field; before
# Python 3.12 it renders the field's expression avoiding backslashes.
_FIELD_ARGUMENTS = {"_avoid_backslashes": True} if sys.version_info < (3, 12) else {}
# Unparser attributes, besides the type-ignore comments, that a node's rendering reads from its context.
_STATE = tuple(name for name in ("_indent", "_in_try_star", "_in_interactive") if hasattr(_Base(), name))
# Text of each replacement field value of the f-strings in the tree being unparsed, by node id.
_FIELD_TEXTS = ContextVar("_FIELD_TEXTS", default=None)
_END = object()


def unparse(node):
    """Return ``ast.unparse(node)``."""
    values = [item.value for root in (node if isinstance(node, list) else (node,))
              for item in ast.walk(root) if isinstance(item, ast.FormattedValue)]
    if not values:
        return _Unparser().visit(node)
    texts = {}
    token = _FIELD_TEXTS.set(texts)
    try:
        # ast.walk is breadth first, so in reverse the fields inside a field value come before it.
        for value in reversed(values):
            unparser = _Unparser(**_FIELD_ARGUMENTS)
            unparser.set_precedence(_Precedence.TEST.next(), value)
            texts[id(value)] = unparser.visit(value)
        return _Unparser().visit(node)
    finally:
        _FIELD_TEXTS.reset(token)


class _Queued:
    """A node reached by ``traverse``, the parts it renders to and the context it renders in."""
    __slots__ = ("node", "parts", "state", "type_ignores", "preceded")

    def __init__(self, node, state, type_ignores, preceded):
        self.node, self.parts, self.state = node, [], state
        self.type_ignores, self.preceded = type_ignores, preceded


class _Unparser(_Base):
    def __init__(self, **arguments):
        super().__init__(**arguments)
        self._queue = None
        # The part list of the node being rendered when output precedes that node, else None.
        self._preceded = None
        self._live_ignores = self._ignores_copy = None

    def visit(self, node):
        texts = _FIELD_TEXTS.get()
        if texts is not None and id(node) in texts:
            return texts[id(node)]
        outer_queue, outer_preceded = self._queue, self._preceded
        queue = self._queue = []
        self._preceded = None
        try:
            # The root renders as in ``ast._Unparser.visit``; a nested ``visit`` from
            # ``NodeVisitor.generic_visit`` may replace the output list, as there.
            self._source = []
            if isinstance(node, list):
                self.traverse(node)
            else:
                ast.NodeVisitor.visit(self, node)
            root = self._source
            # Render depth first, a node's children in source order.
            while queue:
                queued = queue.pop()
                mark = len(queue)
                self._source = queued.parts
                for name, value in zip(_STATE, queued.state):
                    setattr(self, name, value)
                self._type_ignores = queued.type_ignores
                self._preceded = queued.parts if queued.preceded else None
                ast.NodeVisitor.visit(self, queued.node)
                queue[mark:] = reversed(queue[mark:])
            parts = []
            pending = [iter(root)]
            while pending:
                part = next(pending[-1], _END)
                if part is _END:
                    pending.pop()
                elif type(part) is _Queued:
                    pending.append(iter(part.parts))
                else:
                    parts.append(part)
            text = "".join(parts)
            self._source = [text]
            return text
        finally:
            self._queue, self._preceded = outer_queue, outer_preceded

    def traverse(self, node):
        # AST list fields hold nodes, never lists.
        for item in node if isinstance(node, list) else (node,):
            if isinstance(item, ast.arguments):
                # A lambda tests whether its arguments wrote anything; their own children are queued.
                ast.NodeVisitor.visit(self, item)
            else:
                queued = _Queued(item, tuple(getattr(self, name) for name in _STATE), self._frozen_ignores(),
                                 bool(self._source) or self._source is self._preceded)
                self._source.append(queued)
                self._queue.append(queued)

    def _frozen_ignores(self):
        """The type-ignore comments in effect, as a mapping the module visit will not clear."""
        ignores = self._type_ignores
        if not ignores or ignores is self._ignores_copy:
            return ignores
        if ignores is not self._live_ignores:
            self._live_ignores, self._ignores_copy = ignores, dict(ignores)
        return self._ignores_copy

    def maybe_newline(self):
        if self._source or self._source is self._preceded:
            self.write("\n")

    def maybe_semicolon(self):
        if self._source or self._source is self._preceded:
            self.write("; ")
