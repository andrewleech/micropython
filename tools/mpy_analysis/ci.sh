#!/bin/bash
# Builds a port, derives link evidence from the build, generates the SPDX and CycloneDX
# SBOMs and checks them. Usage: tools/mpy_analysis/ci.sh <function> [<function> ...]
#
# The mpy_build_inputs and mpy_analysis packages and spdx-tools must be installed, and the
# libraries the port uses must be checked out (git submodule update --init --recursive).
# The SPDX output must pass the reference validator, the summary must report no gaps and the
# firmware-complete policy must be satisfied.

set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
OUT=${SBOM_OUT:-/tmp/sbom}
IDF_DIR=${IDF_DIR:-$ROOT/esp-idf}

# sbom_run <name>: uses the arrays ROOTS and OWNERS (name=value), COMPDBS (paths), IMAGES
# (--image values) and SCOPE_ARGS (extra mpy-build-scope arguments).
function sbom_run {
    local out=$OUT/$1 item
    local roots=() source_roots=() owners=() databases=() images=()
    for item in "${ROOTS[@]}"; do roots+=(--root "$item"); source_roots+=(--source-root "$item"); done
    for item in "${OWNERS[@]}"; do owners+=(--owner "$item"); done
    for item in "${COMPDBS[@]}"; do databases+=(--compile-database "$item"); done
    for item in "${IMAGES[@]}"; do images+=(--image "$item"); done
    mkdir -p "$out"
    sast-link-evidence "${databases[@]}" "${images[@]}" "${source_roots[@]}" \
        --output "$out/evidence.json" --selected-database "$out/selected.json"
    mpy-build-scope --micropython-root . "${roots[@]}" "${owners[@]}" "${SCOPE_ARGS[@]}" \
        --compile-database "$out/selected.json" --output "$out/scope.json"
    sast-sbom --scope "$out/scope.json" --micropython-root . \
        --link-evidence "$out/evidence.json" --output-dir "$out/spdx"
    sast-sbom --scope "$out/scope.json" --micropython-root . \
        --link-evidence "$out/evidence.json" --output-dir "$out/cdx" --format cyclonedx
    pyspdxtools -i "$out/spdx/sbom.spdx.json"
    python3 - "$out/spdx/sbom-summary.json" <<'EOF'
import json
import sys

gaps = [(g["kind"], g.get("boundary"), g.get("field")) for g in json.load(open(sys.argv[1]))["gaps"]]
assert not gaps, gaps
EOF
    sast-sbom-policy --summary "$out/spdx/sbom-summary.json" \
        --link-evidence "$out/evidence.json" --profile firmware-complete
}

function sbom_unix {
    make -C mpy-cross -j"$(nproc)"
    # compiledb reads the commands of a build that has not run yet.
    (cd ports/unix && python3 -m compiledb -n -S -o "$OUT/unix-compile.json" make && make -j"$(nproc)")
    local build=$ROOT/ports/unix/build-standard
    ROOTS=(micropython="$ROOT" mbedtls="$ROOT/lib/mbedtls" micropython-lib="$ROOT/lib/micropython-lib"
        berkeley-db-1.xx="$ROOT/lib/berkeley-db-1.xx")
    OWNERS=(micropython=application mbedtls=external micropython-lib=external berkeley-db-1.xx=external)
    COMPDBS=("$OUT/unix-compile.json")
    IMAGES=("unix:application:$build/micropython:$build/micropython.map")
    SCOPE_ARGS=(--manifest ports/unix/variants/standard/manifest.py
        --var MPY_DIR="$ROOT" --var MPY_LIB_DIR="$ROOT/lib/micropython-lib"
        --var PORT_DIR="$ROOT/ports/unix" --var BOARD_DIR="$ROOT/ports/unix/variants/standard"
        --target x86_64-linux-gnu --variant standard --port unix)
    sbom_run unix
}

function sbom_stm32 {
    local board=NUCLEO_H563ZI lib
    make -C mpy-cross -j"$(nproc)"
    (cd ports/stm32 && python3 -m compiledb -n -S -o "$OUT/stm32-app.json" make BOARD=$board \
        && make -j"$(nproc)" BOARD=$board)
    (cd ports/stm32/mboot && python3 -m compiledb -n -S -o "$OUT/stm32-mboot.json" make BOARD=$board \
        && make -j"$(nproc)" BOARD=$board)
    local app=$ROOT/ports/stm32/build-$board mboot=$ROOT/ports/stm32/mboot/build-$board
    ROOTS=(micropython="$ROOT") OWNERS=(micropython=runtime)
    for lib in lwip mbedtls CMSIS_6 micropython-lib stm32lib tinyusb; do
        ROOTS+=("$lib=$ROOT/lib/$lib")
        OWNERS+=("$lib=dependency")
    done
    COMPDBS=("$OUT/stm32-app.json" "$OUT/stm32-mboot.json")
    IMAGES=("application:application:$app/firmware.elf:$app/firmware.map"
        "mboot:bootloader:$mboot/firmware.elf:$mboot/firmware.map")
    SCOPE_ARGS=(--manifest ports/stm32/boards/manifest.py
        --var MPY_DIR="$ROOT" --var MPY_LIB_DIR="$ROOT/lib/micropython-lib"
        --var PORT_DIR="$ROOT/ports/stm32" --var BOARD_DIR="$ROOT/ports/stm32/boards/$board"
        --target arm-none-eabi --variant $board --port stm32)
    sbom_run stm32
}

# The ESP32 build needs the ESP-IDF environment (see ci_esp32_idf_setup in tools/ci.sh); the
# analysis does not, and runs with the Python the tools were installed into.
function sbom_esp32_build {
    # shellcheck disable=SC1091
    source "$IDF_DIR/export.sh"
    make -C mpy-cross -j"$(nproc)"
    make -C ports/esp32 submodules
    make -C ports/esp32 -j"$(nproc)" BOARD=ESP32_GENERIC
    # The build refreshes the dependency lock file, which is not part of the analysed state.
    git restore ports/esp32/lockfiles
}

function sbom_esp32 {
    local build=$ROOT/ports/esp32/build-ESP32_GENERIC
    ROOTS=(micropython="$ROOT" idf="$IDF_DIR") OWNERS=(micropython=runtime idf=dependency)
    COMPDBS=("$build/compile_commands.json" "$build/bootloader/compile_commands.json")
    IMAGES=("app:firmware-image:$build/micropython.elf:$build/micropython.map"
        "bootloader:bootloader-image:$build/bootloader/bootloader.elf:$build/bootloader/bootloader.map")
    SCOPE_ARGS=(--target xtensa-esp32-elf --variant ESP32_GENERIC --port esp32)
    sbom_run esp32
}

mkdir -p "$OUT"
for target in "$@"; do
    "$target"
done
