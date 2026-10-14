from pathlib import Path

from mpy_analysis import license_text

DATA = Path(license_text.__file__).parent / "data" / "licenses"


def test_exact_texts_are_identified_regardless_of_layout_and_copyright_notice():
    apache = (DATA / "Apache-2.0.txt").read_text()
    assert license_text.identify(apache) == "Apache-2.0"
    assert (
        license_text.identify(apache.replace("\n", "\r\n") + "\nCopyright 2020 Someone\n")
        == "Apache-2.0"
    )
    mit = "The MIT License\n\nCopyright (c) 2001 A. N. Other\n\n" + (DATA / "MIT.txt").read_text()
    assert license_text.identify(mit) == "MIT"


def test_modified_or_unknown_text_is_not_identified():
    apache = (DATA / "Apache-2.0.txt").read_text()
    assert license_text.identify(apache.replace("Licensor", "Owner", 1)) is None
    assert license_text.identify((DATA / "MIT.txt").read_text() + "\nPlus extra terms.\n") is None
    assert license_text.identify("All rights reserved.") is None
