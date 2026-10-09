from pathlib import Path

import pytest

from conftest import write
from mpy_analysis.native_sbom import ExtractorError, selected_libraries


@pytest.mark.parametrize(
    "directory,metadata,text,version",
    [
        (
            "lwip",
            "src/include/lwip/init.h",
            "#define LWIP_VERSION_MAJOR 2\n#define LWIP_VERSION_MINOR 1\n#define LWIP_VERSION_REVISION 3\n",
            "2.1.3",
        ),
        (
            "mbedtls",
            "include/mbedtls/build_info.h",
            '#define MBEDTLS_VERSION_STRING "3.6.4"\n',
            "3.6.4",
        ),
        (
            "tinyusb",
            "src/tusb_option.h",
            "#define TUSB_VERSION_MAJOR 0\n#define TUSB_VERSION_MINOR 18\n#define TUSB_VERSION_REVISION 0\n",
            "0.18.0",
        ),
        (
            "pico-sdk",
            "pico_sdk_version.cmake",
            'set(PICO_SDK_VERSION_MAJOR 2)\nset(PICO_SDK_VERSION_MINOR "1")\nset(PICO_SDK_VERSION_REVISION 0)\nset(PICO_SDK_VERSION_PRE_RELEASE_ID dev)\n',
            "2.1.0-dev",
        ),
        ("libffi", "configure.ac", "AC_INIT([libffi], [3.4.4], [bugs@example.org])\n", "3.4.4"),
        (
            "protobuf-c",
            "configure.ac",
            "AC_INIT([protobuf-c],\n  [1.4.1],\n  [bugs@example.org])\n",
            "1.4.1",
        ),
        (
            "libmetal",
            "VERSION",
            "VERSION_MAJOR = 1\nVERSION_MINOR = 5\nVERSION_PATCH = 0\n",
            "1.5.0",
        ),
        (
            "open-amp",
            "VERSION",
            "VERSION_MAJOR = 1\nVERSION_MINOR = 1\nVERSION_PATCH = 0\n",
            "1.1.0",
        ),
        ("libhydrogen", "library.properties", "name=Hydrogen\nversion=1.0.0\n", "1.0.0"),
        (
            "cyw43-driver",
            "src/cyw43.h",
            "#define CYW43_VERSION_MAJOR (1U)\n#define CYW43_VERSION_MINOR 0\n#define CYW43_VERSION_MICRO 4\n",
            "1.0.4",
        ),
        ("fsp", "ra/fsp/inc/fsp_version.h", '#define FSP_VERSION_STRING ("5.8.0")\n', "5.8.0"),
        ("nxp_driver", "sdk/version.txt", "2.15.000\n", "2.15.000"),
        (
            "alif_ensemble-cmsis-dfp",
            "AlifSemiconductor.Ensemble.pdsc",
            '<releases><release version="1.3.0" date="2026-01-01"/><release version="1.2.0"/></releases>',
            "1.3.0",
        ),
        ("berkeley-db-1.xx", "changelog", "1.84 -> 1.85\nolder notes\n", "1.85"),
        (
            "oofatfs",
            "ff.h",
            "/* FatFs - Generic FAT Filesystem module R0.15 */\n#define FF_DEFINED 80286\n",
            "R0.15-rev80286",
        ),
        (
            "libm_dbl",
            "README",
            "These files are copied from the musl library,\nv1.1.16, and adapted.\n",
            "1.1.16",
        ),
    ],
)
def test_selected_library_versions_have_metadata_evidence(
    tmp_path, directory, metadata, text, version
):
    source = write(tmp_path, f"lib/{directory}/selected.c", "void selected(void) {}\n")
    write(tmp_path, f"lib/{directory}/{metadata}", text)
    records = selected_libraries(tmp_path, [source])
    assert len(records) == 1
    record = records[0]
    assert record["version"] == version
    assert record["version_source"] == "in-tree"
    assert record["version_evidence"] == f"lib/{directory}/{metadata}"
    assert record["directory"] == str(tmp_path / "lib" / directory)
    assert record["sources"] == [str(source)]
    assert "gaps" not in record
    if directory not in {"oofatfs", "libm_dbl"}:
        assert "upstream" not in record


def test_pico_version_ignores_commented_prerelease(tmp_path):
    source = write(tmp_path, "lib/pico-sdk/src/selected.c")
    write(
        tmp_path,
        "lib/pico-sdk/pico_sdk_version.cmake",
        "# set(PICO_SDK_VERSION_MAJOR 99)\n"
        "set(PICO_SDK_VERSION_MAJOR 2)\n"
        "set(PICO_SDK_VERSION_MINOR 1)\n"
        "set(PICO_SDK_VERSION_REVISION 1)\n"
        "# set(PICO_SDK_VERSION_PRE_RELEASE_ID wrong)\n",
    )
    assert selected_libraries(tmp_path, [source])[0]["version"] == "2.1.1"


@pytest.mark.parametrize("family", [1, 2])
def test_littlefs_emits_only_selected_version(tmp_path, family):
    source = write(tmp_path, f"lib/littlefs/lfs{family}.c")
    utility = write(tmp_path, f"lib/littlefs/lfs{family}_util.c")
    write(
        tmp_path, f"lib/littlefs/lfs{family}.h", f"#define LFS{family}_VERSION 0x000{family}0007\n"
    )
    # The other generation exists but has broken metadata. It is not selected.
    write(tmp_path, f"lib/littlefs/lfs{3 - family}.h", "broken metadata\n")
    records = selected_libraries(tmp_path, [source, utility])
    assert len(records) == 1
    assert records[0]["name"] == f"littlefs-lfs{family}"
    assert records[0]["version"] == f"{family}.7"
    assert records[0]["sources"] == sorted([str(source), str(utility)])


def test_both_littlefs_generations_keep_source_ownership_separate(tmp_path):
    sources = []
    for family, packed in [(1, "0x00010007"), (2, "0x0002000b")]:
        sources.append(write(tmp_path, f"lib/littlefs/lfs{family}_util.c"))
        write(
            tmp_path, f"lib/littlefs/lfs{family}.h", f"#define LFS{family}_VERSION ({packed}U)\n"
        )
    records = selected_libraries(tmp_path, sources)
    assert [record["version"] for record in records] == ["1.7", "2.11"]
    assert [record["sources"] for record in records] == [[str(source)] for source in sources]


def test_littlefs_does_not_classify_similar_or_nested_names(tmp_path):
    sources = [
        write(tmp_path, "lib/littlefs/lfs12.c"),
        write(tmp_path, "lib/littlefs/vendor/lfs1.c"),
        write(tmp_path, "lib/littlefs/README.md"),
    ]
    assert selected_libraries(tmp_path, sources) == []


def stm32_metadata(tmp_path, family, major, minor, patch):
    return write(
        tmp_path,
        f"lib/stm32lib/CMSIS/{family}/Include/{family.lower()}.h",
        f"#define __{family}_CMSIS_VERSION_MAIN (0x{major:02x}U)\n"
        f"#define __{family}_CMSIS_VERSION_SUB1 (0x{minor:02x}U)\n"
        f"#define __{family}_CMSIS_VERSION_SUB2 (0x{patch:02x}U)\n",
    )


def test_stm32_only_observed_families_include_hal_and_cmsis_sources(tmp_path):
    hal = write(tmp_path, "lib/stm32lib/STM32F4xx_HAL_Driver/Src/stm32f4xx_hal.c")
    cmsis = write(tmp_path, "lib/stm32lib/CMSIS/STM32F4xx/Source/Templates/system_stm32f4xx.c")
    stm32_metadata(tmp_path, "STM32F4xx", 2, 6, 10)
    write(tmp_path, "lib/stm32lib/CMSIS/STM32H7xx/Include/stm32h7xx.h", "invalid\n")
    records = selected_libraries(tmp_path, [hal, cmsis])
    assert len(records) == 1
    record = records[0]
    assert record["name"] == "stm32lib-STM32F4xx"
    assert record["version"] == "2.6.10"
    assert record["version_kind"] == "cmsis-device"
    assert record["version_evidence"] == "lib/stm32lib/CMSIS/STM32F4xx/Include/stm32f4xx.h"
    assert record["directory"] == str(tmp_path / "lib/stm32lib")
    assert record["sources"] == sorted([str(hal), str(cmsis)])


def test_stm32_selected_families_keep_versions_separate(tmp_path):
    sources = []
    for family, minor in [("STM32F4xx", 6), ("STM32H7xx", 3)]:
        sources.append(write(tmp_path, f"lib/stm32lib/{family}_HAL_Driver/Src/hal.c"))
        stm32_metadata(tmp_path, family, 2, minor, 0)
    records = selected_libraries(tmp_path, sources)
    assert [record["version"] for record in records] == ["2.6.0", "2.3.0"]
    assert [record["sources"] for record in records] == [[str(source)] for source in sources]


def test_shared_stm32_sources_do_not_select_any_family(tmp_path):
    source = write(tmp_path, "lib/stm32lib/CMSIS/Core/Include/core_cm4.h")
    record = selected_libraries(tmp_path, [source])[0]
    assert record["name"] == "stm32lib"
    assert record["version"] is None
    assert record["version_source"] == "unknown"


def test_asf4_only_observed_family_is_extracted(tmp_path):
    source = write(tmp_path, "lib/asf4/samd21/hal/src/hal_gpio.c")
    write(
        tmp_path,
        "lib/asf4/samd21/include/component-version.h",
        '#define COMPONENT_VERSION_STRING "1.2"\n#define BUILD_NUMBER 104\n',
    )
    write(tmp_path, "lib/asf4/samd51/include/component-version.h", "broken\n")
    records = selected_libraries(tmp_path, [source])
    assert len(records) == 1
    assert records[0]["name"] == "asf4-samd21"
    assert records[0]["version"] == "1.2+build.104"
    assert records[0]["directory"] == str(tmp_path / "lib/asf4/samd21")
    assert records[0]["sources"] == [str(source)]
    assert "upstream" not in records[0]


def test_asf4_multiple_selected_families_keep_versions_separate(tmp_path):
    sources = []
    for family, version, build in [("samd21", "1.2", 104), ("same51", "1.1", 88)]:
        sources.append(write(tmp_path, f"lib/asf4/{family}/hal/src/hal_gpio.c"))
        write(
            tmp_path,
            f"lib/asf4/{family}/include/component-version.h",
            f'#define COMPONENT_VERSION_STRING "{version}"\n#define BUILD_NUMBER {build}\n',
        )
    records = selected_libraries(tmp_path, sources)
    assert [record["version"] for record in records] == ["1.2+build.104", "1.1+build.88"]
    assert [record["sources"] for record in records] == [[str(source)] for source in sources]


def test_asf4_shared_sources_have_unknown_version(tmp_path):
    source = write(tmp_path, "lib/asf4/common/helper.c")
    record = selected_libraries(tmp_path, [source])[0]
    assert record["name"] == "asf4"
    assert record["version"] is None


@pytest.mark.parametrize(
    "vendor", ["uzlib", "crypto-algorithms", "libm", "re1.5", "axtls", "wiznet5k"]
)
def test_known_unversioned_vendors_are_not_given_synthetic_releases(tmp_path, vendor):
    source = write(tmp_path, f"lib/{vendor}/selected.c")
    record = selected_libraries(tmp_path, [source])[0]
    assert record["version"] is None
    assert record["version_source"] == "unknown"
    assert record["gaps"]
    assert "version_evidence" not in record
    if vendor in {"axtls", "wiznet5k"}:
        assert "upstream" not in record
    else:
        assert record["upstream"].startswith("https://")


def test_nimble_release_notes_are_only_a_lower_bound(tmp_path):
    source = write(tmp_path, "lib/mynewt-nimble/nimble/host/src/ble_hs.c")
    write(
        tmp_path,
        "lib/mynewt-nimble/RELEASE_NOTES.md",
        "# Apache NimBLE v1.8.0\n\n# Apache NimBLE v1.7.0\n",
    )
    write(tmp_path, "lib/mynewt-nimble/version.yml", "repo.version: 0.0.0\n")
    record = selected_libraries(tmp_path, [source])[0]
    assert record["version"] is None
    assert record["version_source"] == "declared-lower-bound"
    assert record["version_lower_bound"] == "1.8.0"
    assert record["version_evidence"] == "lib/mynewt-nimble/RELEASE_NOTES.md"
    assert record["gaps"]
    assert "upstream" not in record


@pytest.mark.parametrize(
    "selected,metadata,text",
    [
        ("lib/lwip/src/core/init.c", "lib/lwip/src/include/lwip/init.h", None),
        (
            "lib/lwip/src/core/init.c",
            "lib/lwip/src/include/lwip/init.h",
            "#define LWIP_VERSION_MAJOR INVALID\n#define LWIP_VERSION_MINOR 1\n#define LWIP_VERSION_REVISION 0\n",
        ),
        ("lib/littlefs/lfs2.c", "lib/littlefs/lfs2.h", "#define LFS2_VERSION invalid\n"),
        ("lib/nxp_driver/sdk/source.c", "lib/nxp_driver/sdk/version.txt", "\n"),
        (
            "lib/stm32lib/STM32F4xx_HAL_Driver/Src/hal.c",
            "lib/stm32lib/CMSIS/STM32F4xx/Include/stm32f4xx.h",
            "missing macros\n",
        ),
        (
            "lib/asf4/samd21/hal/src/hal.c",
            "lib/asf4/samd21/include/component-version.h",
            '#define COMPONENT_VERSION_STRING "1.2"\n',
        ),
        (
            "lib/mynewt-nimble/nimble/host/src/ble.c",
            "lib/mynewt-nimble/RELEASE_NOTES.md",
            "no release headings\n",
        ),
    ],
)
def test_broken_selected_extractors_fail_hard(tmp_path, selected, metadata, text):
    source = write(tmp_path, selected)
    if text is not None:
        write(tmp_path, metadata, text)
    with pytest.raises(ExtractorError, match=metadata):
        selected_libraries(tmp_path, [source])


def test_outside_unknown_and_unselected_trees_are_not_catalogued(tmp_path):
    root = tmp_path / "mp"
    selected = [
        write(root, "py/runtime.c"),
        write(root, "lib/unknown-vendor/source.c"),
        write(root, "lib/mbedtls_errors/source.c"),
        write(root, "lib/uzlib-copy/source.c"),
        write(tmp_path, "other/lib/lwip/source.c"),
    ]
    write(root, "lib/lwip/src/core/init.c")
    write(root, "lib/asf4/samd21/include/component-version.h", "broken\n")
    assert selected_libraries(root, selected) == []
    assert selected_libraries(root, []) == []


def test_symlink_escape_is_not_micro_python_library_selection(tmp_path):
    root = tmp_path / "mp"
    outside = write(tmp_path, "external/lib/uzlib/selected.c")
    link = root / "lib/uzlib/selected.c"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert selected_libraries(root, [link]) == []


def test_version_metadata_cannot_escape_micro_python_root(tmp_path):
    root = tmp_path / "mp"
    source = write(root, "lib/nxp_driver/sdk/source.c")
    outside = write(tmp_path, "version.txt", "2.0\n")
    (root / "lib/nxp_driver/sdk/version.txt").symlink_to(outside)
    with pytest.raises(ExtractorError, match="escapes MicroPython root"):
        selected_libraries(root, [source])


def test_nested_repository_is_left_for_repository_fallback(tmp_path):
    source = write(tmp_path, "lib/pico-sdk/lib/tinyusb/src/tusb.c")
    write(tmp_path, "lib/pico-sdk/lib/tinyusb/.git", "gitdir: /unused/fixture\n")
    # No pico SDK metadata is needed for a source owned by its nested checkout.
    assert selected_libraries(tmp_path, [source]) == []


def test_relative_paths_and_symlink_aliases_keep_deduplicated_physical_sources(tmp_path):
    source = write(tmp_path, "lib/uzlib/tinflate.c")
    alias = tmp_path / "alias.c"
    alias.symlink_to(source)
    record = selected_libraries(tmp_path, [source, Path("lib/uzlib/tinflate.c"), alias, source])[0]
    assert record["sources"] == [str(source)]
    assert record["directory"] == str(tmp_path / "lib/uzlib")
