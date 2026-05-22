#!/usr/bin/env bash
# Build the rp2 mboot bootloader for every main rp2 board that opts into mboot
# (USE_MBOOT=1 in its mpconfigboard.cmake).  Usage:
#   ports/rp2/mboot/scripts/build_all.sh [extra make args]
# (invoke from anywhere; the script resolves its own paths).

set -e
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mboot_dir="$(cd "${script_dir}/.." && pwd)"
boards_dir="$(cd "${mboot_dir}/../boards" && pwd)"

cd "${mboot_dir}"
for board_dir in "${boards_dir}"/*/; do
    board=$(basename "${board_dir%/}")
    if ! grep -q "set(USE_MBOOT 1)" "${board_dir}mpconfigboard.cmake" 2>/dev/null; then
        continue
    fi
    echo "================================================================"
    echo "Building ${board}"
    echo "================================================================"
    make BOARD="${board}" "$@"
done
