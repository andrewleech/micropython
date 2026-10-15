from pathlib import Path
from xml.etree import ElementTree

import pytest
from mpy_analysis import license_text

DATA = Path(license_text.__file__).parent / "data" / "licenses"
NS = "{http://www.spdx.org/license}"
LWIP = """Copyright (c) 2001, 2002 Swedish Institute of Computer Science.
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. The name of the author may not be used to endorse or promote products
   derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR IMPLIED
WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT
SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY
OF SUCH DAMAGE.
"""


def default_text(identifier: str) -> str:
    """The licence body with every alternative at its default wording."""

    def walk(element):
        parts = [element.text or ""]
        for child in element:
            if child.tag.removeprefix(NS) not in ("copyrightText", "titleText", "bullet"):
                parts.append(walk(child))
            parts.append(child.tail or "")
        return (
            "\n".join(parts) if element.tag.removeprefix(NS) in ("p", "item") else "".join(parts)
        )

    root = ElementTree.parse(DATA / f"{identifier}.xml").getroot()
    return walk(root.find(f"{NS}license").find(f"{NS}text"))


@pytest.mark.parametrize("path", sorted(DATA.glob("*.xml")), ids=lambda path: path.stem)
def test_every_shipped_template_identifies_its_own_text(path):
    text = default_text(path.stem)
    assert license_text.identify(text) == path.stem
    assert license_text.identify("Copyright (c) 2020 A. N. Other\n\n" + text) == path.stem
    assert license_text.identify(text.replace("\n", "\r\n") + "\n\n") == path.stem


def test_holder_wording_and_clause_three_forms_are_identified():
    assert license_text.identify(LWIP) == "BSD-3-Clause"
    university = LWIP.replace(
        "The name of the author may not",
        "Neither the name of the University nor the names of its contributors may",
    ).replace("THE AUTHOR", "THE REGENTS AND CONTRIBUTORS")
    assert license_text.identify(university) == "BSD-3-Clause"


def test_title_and_copyright_lines_may_precede_the_body():
    mit = default_text("MIT")
    assert license_text.identify("The MIT License (MIT)\n\nCopyright (c) 2001 A\n" + mit) == "MIT"


def test_clause_count_selects_the_licence():
    clause3 = "3. The name of the author may not be used to endorse or promote products\n   derived from this software without specific prior written permission.\n"
    assert license_text.identify(LWIP.replace(clause3, "")) == "BSD-2-Clause"
    advertising = (
        "4. All advertising materials mentioning features or use of this software must "
        "display the following acknowledgement: This product includes software developed "
        "by the organization.\n"
    )
    assert license_text.identify(LWIP.replace(clause3, advertising + clause3)) != "BSD-3-Clause"


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace("must retain", "may retain"),
        lambda t: t.replace("may not be used", "may be used"),
        lambda t: t.replace("without specific prior written permission", "without notice"),
        lambda t: t + "\nPlus extra terms.\n",
        lambda t: "Use is limited to non-commercial purposes.\n\n" + t,
        lambda t: t.replace("SHALL THE AUTHOR BE", "SHALL THE AUTHOR NOT BE"),
        lambda t: t.replace("PROVIDED BY THE AUTHOR", "PROVIDED BY THE AUTHOR ONLY"),
    ],
)
def test_changed_added_or_missing_terms_are_not_identified(edit):
    assert license_text.identify(edit(LWIP)) is None


def test_text_that_only_quotes_a_licence_is_not_identified():
    assert license_text.identify("All rights reserved.") is None
    assert (
        license_text.identify("See the license text in README for the BSD terms.\n" + LWIP) is None
    )


def test_licence_section_running_to_the_end_of_a_readme_is_identified():
    body = LWIP.split("\n\n", 1)[1]
    readme = "project\n=======\n\nCopyright (c) 2002 X\n\nLicense\n-------\n\nUsed under the terms of\nBSD license:\n\n"
    assert license_text.identify(readme + body) == "BSD-3-Clause"
    assert license_text.identify(readme + body.replace("must retain", "may retain")) is None
    assert license_text.identify(readme + body + "\nOther\n-----\nmore text\n") is None
