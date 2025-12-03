# zlib compatibility shim for MicroPython.
# Provides crc32 using binascii.crc32.

from binascii import crc32

__all__ = ['crc32']
