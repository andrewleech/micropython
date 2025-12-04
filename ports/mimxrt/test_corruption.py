# Test to determine if corruption is on write or read
import os
from machine import SDCard

sdcard = SDCard(1)
os.chdir("/sdcard")

size = 57190  # NOT aligned to 512 - same size that showed corruption
block_count = 20

# Create known pattern
data = bytearray(size)
for i in range(size):
    data[i] = i & 0xFF

print(f"Test size: {size} bytes x {block_count} blocks = {size * block_count} bytes")

# Write
print("\n=== WRITE ===")
f = open("corruption_test.bin", "wb")
for i in range(block_count):
    f.write(data)
f.close()
print("Write complete")


def find_first_error(f, size, block_count):
    """Find first error offset, return (offset, got, expected) or None"""
    for block in range(block_count):
        rd = f.read(size)
        for i in range(size):
            if rd[i] != (i & 0xFF):
                return (block * size + i, rd[i], i & 0xFF)
    return None


# First read
print("\n=== READ 1 ===")
f = open("corruption_test.bin", "rb")
err1 = find_first_error(f, size, block_count)
f.close()
if err1:
    print(f"  First error at 0x{err1[0]:x}: got {err1[1]}, expected {err1[2]}")
else:
    print("  No errors")

# Second read
print("\n=== READ 2 ===")
f = open("corruption_test.bin", "rb")
err2 = find_first_error(f, size, block_count)
f.close()
if err2:
    print(f"  First error at 0x{err2[0]:x}: got {err2[1]}, expected {err2[2]}")
else:
    print("  No errors")

# Compare
if err1 and err2:
    if err1[0] == err2[0]:
        print("\n=> SAME first error location - likely WRITE corruption")
    else:
        print("\n=> DIFFERENT first error locations - likely READ corruption")
        print(f"   Read 1: 0x{err1[0]:x}, Read 2: 0x{err2[0]:x}")
elif not err1 and not err2:
    print("\n=> No errors detected!")
else:
    print(f"\n=> Inconsistent results")
