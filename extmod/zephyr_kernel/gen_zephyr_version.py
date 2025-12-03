#!/usr/bin/env python3
"""
Generate Zephyr version.h header from VERSION file

This script reads Zephyr's VERSION file and generates a version.h header
with version macros, similar to what Zephyr's CMake build system generates.
"""

import argparse
import re
import sys


def parse_version_file(version_file):
    """Parse Zephyr VERSION file and extract version components."""
    version = {}

    with open(version_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Parse KEY = VALUE format
            match = re.match(r"(\w+)\s*=\s*(.*)", line)
            if match:
                key, value = match.groups()
                # Remove quotes if present
                value = value.strip().strip('"')
                version[key] = value

    return version


def generate_version_header(version, output_file):
    """Generate version.h header file from version dict."""

    major = version.get("VERSION_MAJOR", "0")
    minor = version.get("VERSION_MINOR", "0")
    patchlevel = version.get("PATCHLEVEL", "0")
    tweak = version.get("VERSION_TWEAK", "0")
    extraversion = version.get("EXTRAVERSION", "")

    # Calculate KERNELVERSION (32-bit: major.minor.patchlevel)
    # Format: (major << 24) | (minor << 16) | (patchlevel << 8)
    kernel_version = (int(major) << 24) | (int(minor) << 16) | (int(patchlevel) << 8)

    # Build version string
    version_string = f"{major}.{minor}.{patchlevel}"
    if extraversion:
        version_string += f"-{extraversion}"

    # Generate header content
    header = f"""#ifndef _KERNEL_VERSION_H_
#define _KERNEL_VERSION_H_

/*
 * Auto-generated Zephyr version header
 * Generated from: VERSION file
 */

#define ZEPHYR_VERSION_CODE {kernel_version}
#define ZEPHYR_VERSION(a,b,c) (((a) << 16) + ((b) << 8) + (c))

#define KERNELVERSION                   0x{kernel_version:08x}
#define KERNEL_VERSION_NUMBER           0x{kernel_version:08x}
#define KERNEL_VERSION_MAJOR            {major}
#define KERNEL_VERSION_MINOR            {minor}
#define KERNEL_PATCHLEVEL               {patchlevel}
#define KERNEL_VERSION_TWEAK            {tweak}
#define KERNEL_VERSION_STRING           "{version_string}"
#define KERNEL_VERSION_EXTENDED_STRING  "{version_string}"
#define KERNEL_VERSION_TWEAK_STRING     "{tweak}"

#define BUILD_VERSION 0x{kernel_version:08x}

#endif /* _KERNEL_VERSION_H_ */
"""

    with open(output_file, "w") as f:
        f.write(header)

    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("-i", "--input", required=True, help="Input VERSION file")

    parser.add_argument("-o", "--output", required=True, help="Output version.h file")

    args = parser.parse_args()

    try:
        version = parse_version_file(args.input)
        return generate_version_header(version, args.output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
