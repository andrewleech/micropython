#!/usr/bin/env bash
set -euo pipefail

port=$1
build=$2
make_cmd=$3
shift 3
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$build/.mpy-analysis
rm -f "$build/sbom.spdx.json" "$build/sbom.json" "$build/sbom-summary.json"
mkdir -p "$work"

run_make() {
    local directory=$1
    shift
    "$make_cmd" -C "$directory" "$@"
}

capture_make() {
    local directory=$1 output=$2
    shift 2
    python3 -m compiledb -n -S -f -o "$output" make -c "$make_cmd" -C "$directory" "$@"
}

case "$port" in
    unix)
        variant=$1
        variant_dir=$2
        manifest=$3
        mpy_lib_dir=$4
        prog=$5
        target=$6
        capture_make "$ROOT/ports/unix" "$work/compile_commands.json" all
        run_make "$ROOT/ports/unix" all
        roots=(--root "micropython=$ROOT" --root "mbedtls=$ROOT/lib/mbedtls" \
            --root "micropython-lib=$mpy_lib_dir" --root "berkeley-db-1.xx=$ROOT/lib/berkeley-db-1.xx")
        source_roots=(--source-root "micropython=$ROOT" --source-root "mbedtls=$ROOT/lib/mbedtls" \
            --source-root "micropython-lib=$mpy_lib_dir" --source-root "berkeley-db-1.xx=$ROOT/lib/berkeley-db-1.xx")
        owners=(--owner micropython=application --owner mbedtls=external \
            --owner micropython-lib=external --owner berkeley-db-1.xx=external)
        compdbs=(--compile-database "$work/compile_commands.json")
        images=(--image "unix:application:$build/$prog:$build/$prog.map")
        scope=(--manifest "$manifest" --var "MPY_DIR=$ROOT" --var "MPY_LIB_DIR=$mpy_lib_dir" \
            --var "PORT_DIR=$ROOT/ports/unix" --var "BOARD_DIR=$variant_dir" \
            --target "$target" --variant "$variant" --port unix)
        ;;
    stm32)
        board=$1
        board_dir=$2
        manifest=$3
        board_variant=$4
        mboot_build=$build/mboot
        capture_make "$ROOT/ports/stm32" "$work/stm32-compile_commands.json" all
        run_make "$ROOT/ports/stm32" all
        capture_make "$ROOT/ports/stm32/mboot" "$work/mboot-compile_commands.json" "BUILD=$mboot_build" all
        run_make "$ROOT/ports/stm32/mboot" "BUILD=$mboot_build" all
        roots=(--root "micropython=$ROOT")
        source_roots=(--source-root "micropython=$ROOT")
        owners=(--owner micropython=runtime)
        for lib in lwip mbedtls CMSIS_6 micropython-lib stm32lib tinyusb; do
            roots+=(--root "$lib=$ROOT/lib/$lib")
            source_roots+=(--source-root "$lib=$ROOT/lib/$lib")
            owners+=(--owner "$lib=dependency")
        done
        compdbs=(--compile-database "$work/stm32-compile_commands.json" \
            --compile-database "$work/mboot-compile_commands.json")
        images=(--image "application:application:$build/firmware.elf:$build/firmware.map" \
            --image "mboot:bootloader:$mboot_build/firmware.elf:$mboot_build/firmware.map")
        variant=$board
        if [[ -n $board_variant ]]; then
            variant=$board-$board_variant
        fi
        scope=(--manifest "$manifest" --var "MPY_DIR=$ROOT" \
            --var "MPY_LIB_DIR=$ROOT/lib/micropython-lib" --var "PORT_DIR=$ROOT/ports/stm32" \
            --var "BOARD_DIR=$board_dir" --target arm-none-eabi --variant "$variant" --port stm32)
        ;;
    esp32)
        board=$1
        board_variant=$2
        idf_dir=${3:-$ROOT/esp-idf}
        source "$idf_dir/export.sh"
        run_make "$ROOT/ports/esp32" all
        if [[ ${MPY_SBOM_RESTORE_LOCKFILES:-0} == 1 ]]; then
            git -C "$ROOT" restore -- ports/esp32/lockfiles
        fi
        cp "$build/compile_commands.json" "$work/compile_commands.json"
        cp "$build/bootloader/compile_commands.json" "$work/bootloader-compile_commands.json"
        roots=(--root "micropython=$ROOT" --root "idf=$idf_dir")
        source_roots=(--source-root "micropython=$ROOT" --source-root "idf=$idf_dir")
        owners=(--owner micropython=runtime --owner idf=dependency)
        compdbs=(--compile-database "$work/compile_commands.json" \
            --compile-database "$work/bootloader-compile_commands.json")
        images=(--image "app:firmware-image:$build/micropython.elf:$build/micropython.map" \
            --image "bootloader:bootloader-image:$build/bootloader/bootloader.elf:$build/bootloader/bootloader.map")
        variant=$board
        if [[ -n $board_variant ]]; then
            variant=$board-$board_variant
        fi
        scope=(--target xtensa-esp32-elf --variant "$variant" --port esp32)
        ;;
    *)
        echo "Unknown SBOM port: $port" >&2
        exit 2
        ;;
esac

sast-link-evidence "${compdbs[@]}" "${images[@]}" "${source_roots[@]}" \
    --output "$work/evidence.json" --selected-database "$work/selected.json"
mpy-build-scope --micropython-root "$ROOT" "${roots[@]}" "${owners[@]}" "${scope[@]}" \
    --compile-database "$work/selected.json" --output "$work/scope.json"
sast-sbom --scope "$work/scope.json" --micropython-root "$ROOT" \
    --link-evidence "$work/evidence.json" --output-dir "$work/spdx"
sast-sbom --scope "$work/scope.json" --micropython-root "$ROOT" \
    --link-evidence "$work/evidence.json" --output-dir "$work/cdx" --format cyclonedx
pyspdxtools -i "$work/spdx/sbom.spdx.json"
cp "$work/spdx/sbom.spdx.json" "$build/sbom.spdx.json"
cp "$work/cdx/sbom.json" "$build/sbom.json"
cp "$work/spdx/sbom-summary.json" "$build/sbom-summary.json"
python3 - "$build/sbom-summary.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    summary = json.load(source)
gaps = [(gap["kind"], gap.get("boundary"), gap.get("field")) for gap in summary["gaps"]]
print(f"SBOM report: {summary['component_count']} components; {summary['coverage_status']}")
print("SPDX: sbom.spdx.json; CycloneDX: sbom.json; summary: sbom-summary.json")
if gaps:
    print("SBOM gaps:")
    for kind, boundary, field in gaps:
        print(f"  {kind}: {boundary or '-'}{'.' + field if field else ''}")
PY
