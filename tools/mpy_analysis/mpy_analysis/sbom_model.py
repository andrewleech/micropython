"""Format-neutral SBOM model shared by the CycloneDX and SPDX writers.

The builders in ``sbom`` and ``generic_inventory`` produce a ``Model``; ``sbom_cyclonedx`` and
``sbom_spdx`` are independent writers over it. Neither writer reads the other's output, and
nothing in the model is shaped by either format.

A ``Model`` is a set of components keyed by ``ref`` plus the relationships between them:

* ``parent`` nests a component under another (the files of a library); ``depends_on`` is the
  dependency graph between components.
* ``dynamic_links`` records the sonames a component needs at run time. They are not components:
  the providing package is not resolved, so the model holds only the names, in the order the
  binary records them.
* ``properties`` is an ordered mapping of evidence names to string values. Names carry no
  format prefix; each writer applies its own namespace.
* Hashes map a lowercase algorithm name (``sha1``, ``sha256``, ``sha3-256``, ``blake2b-256``,
  ...) to lowercase hexadecimal digest content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

APPLICATION = "application"
FIRMWARE = "firmware"
LIBRARY = "library"
FILE = "file"


def property_values(values: dict[str, Any]) -> dict[str, str]:
    """Return the non-null values by sorted name, serialising non-strings as sorted JSON."""
    return {
        key: value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        for key, value in sorted(values.items())
        if value is not None
    }


@dataclass
class Contact:
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    # Fields only the CycloneDX writer carries, copied verbatim.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Party:
    """A supplier organisation."""

    name: str | None = None
    urls: list[str] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    # Fields only the CycloneDX writer carries, copied verbatim.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalReference:
    type: str
    url: str
    comment: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class Component:
    kind: str
    name: str
    ref: str = ""
    version: str | None = None
    purl: str | None = None
    cpe: str | None = None
    group: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)
    supplier: Party | None = None
    author: str | None = None
    publisher: str | None = None
    description: str | None = None
    copyright: str | None = None
    # SPDX licence expression; None when no licence is known.
    license: str | None = None
    # Retained licence texts by licence name.
    license_texts: dict[str, str] = field(default_factory=dict)
    external_references: list[ExternalReference] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    parent: str | None = None
    comment: str | None = None


@dataclass
class Model:
    root: str
    name: str
    components: dict[str, Component]
    depends_on: dict[str, set[str]] = field(default_factory=dict)
    dynamic_links: dict[str, list[str]] = field(default_factory=dict)

    def check(self) -> None:
        """Raise ValueError unless every reference in the model resolves."""
        if self.root not in self.components:
            raise ValueError(f"root component is not in the model: {self.root}")
        for ref, component in self.components.items():
            if component.ref != ref:
                raise ValueError(f"component {component.name} is keyed by {ref}, not {component.ref}")
            if component.parent is not None and component.parent not in self.components:
                raise ValueError(f"component {ref} has unknown parent: {component.parent}")
        for ref, targets in self.depends_on.items():
            if ref not in self.components:
                raise ValueError(f"dependency from unknown component: {ref}")
            for target in targets:
                if target not in self.components:
                    raise ValueError(f"dependency on unknown component: {target}")
        for ref, sonames in self.dynamic_links.items():
            if ref not in self.components:
                raise ValueError(f"dynamic links on unknown component: {ref}")
            if not isinstance(sonames, list) or not all(
                isinstance(item, str) and item for item in sonames
            ):
                raise ValueError(f"dynamic links on {ref} must be nonempty sonames")

    def nested(self) -> dict[str, list[Component]]:
        """Components grouped by the component they are nested under, in model order."""
        groups: dict[str, list[Component]] = {}
        for item in self.components.values():
            if item.parent is not None:
                groups.setdefault(item.parent, []).append(item)
        return groups
