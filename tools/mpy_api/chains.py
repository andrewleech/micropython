"""Read-only sequences over parent-linked tables, so nested paths share their prefixes."""
from collections.abc import Sequence


class ChainView(Sequence):
    """The items on the path from a table root to ``position``, outermost first.

    ``parents[p]`` is the position enclosing entry ``p`` (None at a root) and ``items[p]`` the value
    the path yields for it. Views over one table share it, so the paths of nested branches cost one
    table entry each instead of a list each, and pickling a view stores the flat table once.
    Table entries are only ever appended, never changed, which makes every view immutable.
    """
    __slots__ = ('parents', 'items', 'position', 'length')

    def __init__(self, parents, items, position, length):
        self.parents, self.items, self.position, self.length = parents, items, position, length

    def extend(self, item):
        """A new view one entry deeper, appending ``item`` to the table under this view's last entry."""
        parents = self.parents
        parents.append(self.position)
        self.items.append(item)
        return ChainView(parents, self.items, len(parents) - 1, self.length + 1)

    def over(self, items):
        """The same path yielding ``items[p]`` instead, for a list aligned with this table."""
        return ChainView(self.parents, items, self.position, self.length)

    def positions(self):
        """Table positions on the path, outermost first."""
        path = [None] * self.length
        position, parents = self.position, self.parents
        for index in range(self.length - 1, -1, -1):
            path[index] = position
            position = parents[position]
        return path

    def __len__(self):
        return self.length

    def __iter__(self):
        items = self.items
        return iter([items[position] for position in self.positions()])

    def __reversed__(self):
        items = self.items
        return iter([items[position] for position in reversed(self.positions())])

    def __getitem__(self, index):
        items = self.items
        if isinstance(index, slice):
            return [items[position] for position in self.positions()[index]]
        return items[self.positions()[index]]

    def __contains__(self, value):
        return any(item is value or item == value for item in self)

    def __eq__(self, other):
        if isinstance(other, ChainView):
            if (other.parents is self.parents and other.items is self.items
                    and other.position == self.position):
                return True
            return self.length == other.length and list(self) == list(other)
        if isinstance(other, list):
            return list(self) == other
        return NotImplemented

    __hash__ = None

    def __add__(self, other):
        if isinstance(other, (list, ChainView)):
            return list(self) + list(other)
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, list):
            return other + list(self)
        return NotImplemented

    def __repr__(self):
        return repr(list(self))

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return ChainView, (self.parents, self.items, self.position, self.length)


def chain_root(parents, items):
    """The empty path over a table."""
    return ChainView(parents, items, None, 0)
