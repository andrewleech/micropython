"""Identify a licence file by matching it against the SPDX licence templates.

``data/licenses/<id>.xml`` are the SPDX license-list-XML sources. Their text is a
template: ``<alt match=...>`` marks the variable parts (holder nouns, the wording of
the non-endorsement clause, ...), ``<optional>`` the parts that may be absent. The
template is compiled to one regular expression and the whole text of a licence file
has to match it, so a missing, added or changed clause is not identified. Only
copyright lines and the licence title may precede the body, and a template that ends
in optional sections (the Apache-2.0 appendix) accepts any text after the body.
A text that matches no template, or more than one, is not identified.

The licence set is the XML files in the data directory; adding one needs no code.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from xml.etree import ElementTree

_DATA = Path(__file__).parent / "data" / "licenses"
_NS = "{http://www.spdx.org/license}"
_DROPPED = {"copyrightText", "titleText", "bullet"}
_SLOT_LIMIT = 300
# An open-ended slot holds names, not obligations, so it may not contain a word that
# negates or restricts ("THE AUTHOR NOT BE LIABLE").
_RESTRICTING = r"\b(?:not|no|never|except|unless|only|without|non)\b"
_SLOT_CHAR = rf"(?:(?!{_RESTRICTING}).)"
_BLANKS = re.compile(r"(?:\\s[*+])+")
_COPYRIGHT_LINE = r"[^\n]*(?:copyright|\(c\)|\u00a9|all rights reserved)[^\n]*(?:\n|$)"
# Text that may precede the body: copyright lines, a "License" heading with its
# underline, and one sentence ending in a colon that names a licence ("... under
# the terms of BSD license:") without restricting words.
_LEAD = "|".join(
    [
        _COPYRIGHT_LINE,
        r"[-=~*#_]{3,}[ ]*(?:\n|$)",
        r"licen[cs]es?[ :]*(?:\n|$)",
        rf"(?=[^:]*licen[cs]e)(?:(?!{_RESTRICTING})[^:]){{1,200}}?:[ ]*(?:\n|$)",
        r"\s",
    ]
)
_BULLET = re.compile(r"^[ ]*(?:\(?\d{1,2}[.)]|\(?[a-z][.)]|[-*\u2022])[ ]+", re.MULTILINE)


def _fold(text: str) -> str:
    """Lower case, one kind of quote and dash, no "(s)" plural marker, blank runs collapsed."""
    text = text.lower().replace("``", '"').replace("''", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"(?<=\w)\(s\)", "", text)
    text = re.sub(r"[ \t\r\f\v\u00a0]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n"))


def _literal(text: str) -> str:
    """Template text as a pattern: whitespace is required between two words inside it only."""
    words = _fold(text).split()
    if not words:
        return r"\s*" if text else ""
    body = re.escape(words[0])
    for before, after in zip(words, words[1:]):
        separator = r"\s+" if before[-1].isalnum() and after[0].isalnum() else r"\s*"
        body += separator + re.escape(after)
    return r"\s*" + body + r"\s*"


def _bounded(match: str) -> str:
    """An ``alt`` expression with spaces as any whitespace and open-ended slots restricted."""
    match = _fold(match).replace(" ", r"\s+")
    match = match.replace(".*", f"{_SLOT_CHAR}{{0,{_SLOT_LIMIT}}}?")
    match = match.replace(".+", f"{_SLOT_CHAR}{{1,{_SLOT_LIMIT}}}?")
    return match


def _pattern(element: ElementTree.Element, skip: tuple[ElementTree.Element, ...] = ()) -> str:
    parts = [_literal(element.text or "")]
    for child in element:
        tag = child.tag.removeprefix(_NS)
        if child in skip or tag in _DROPPED:
            inner = ""
        elif tag == "alt":
            inner = "(?:" + _bounded(child.get("match", "")) + ")"
        elif tag == "optional":
            inner = f"(?:{_pattern(child)})?"
        else:
            inner = r"\s*" + _pattern(child) + r"\s*"
        parts.append(inner)
        parts.append(_literal(child.tail or ""))
    return "".join(parts)


def _compile(path: Path) -> re.Pattern[str]:
    root = ElementTree.parse(path).getroot()
    license_ = root.find(f"{_NS}license")
    text = license_.find(f"{_NS}text")
    children = list(text)
    trailing = []
    while children and children[-1].tag.removeprefix(_NS) == "optional":
        trailing.append(children.pop())
    title = text.find(f"{_NS}titleText")
    title_pattern = _pattern(title) if title is not None else ""
    names = [re.escape(word) for word in _fold(license_.get("name", "")).split()]
    heading = "|".join(filter(None, [title_pattern, r"\s+".join(names)]))
    body = _BLANKS.sub(
        lambda run: r"\s+" if "+" in run.group() else r"\s*", _pattern(text, skip=tuple(trailing))
    )
    tail = r"(?:\s.*)?" if trailing else ""
    return re.compile(
        rf"(?:{_LEAD})*(?:(?:the\s+)?(?:{heading})(?:\s*\([^)\n]*\))?\s*)?"
        rf"(?:{_LEAD})*{body}{tail}\s*",
        re.DOTALL,
    )


@cache
def _templates() -> dict[str, re.Pattern[str]]:
    return {path.stem: _compile(path) for path in sorted(_DATA.glob("*.xml"))}


def _whole(text: str) -> str | None:
    folded = _BULLET.sub("", _fold(text).strip())
    found = [name for name, pattern in _templates().items() if pattern.fullmatch(folded)]
    return found[0] if len(found) == 1 else None


def identify(text: str) -> str | None:
    """The SPDX identifier of the one template ``text`` matches in full, if any.

    Failing that, a "License" heading (underlined, or marked with ``#``) that runs to
    the end of the file is matched on its own, as in a README whose last section holds
    the licence terms. Exactly one such heading may identify a licence.
    """
    found = _whole(text)
    if found:
        return found
    sections = {
        _whole(text[match.end() :])
        for match in re.finditer(
            r"^(?:#+[ ]*)?licen[cs]es?[ ]*\n(?:[-=~]{3,}[ ]*\n)?", text, re.M | re.I
        )
    } - {None}
    return sections.pop() if len(sections) == 1 else None
