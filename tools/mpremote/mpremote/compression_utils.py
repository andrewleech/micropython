#
# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2024 Andrew Leech
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import zlib

# Minimum file size to attempt compression (bytes).
# Below this threshold, decompression setup overhead isn't worth it.
MIN_COMPRESS_SIZE = 128

# Minimum compression ratio to use compressed transfer.
# If compression doesn't achieve at least this ratio, send uncompressed.
MIN_COMPRESS_RATIO = 0.95


def compress_data(data, wbits=10):
    """Compress data using zlib (deflate) compression.

    Uses ZLIB format (deflate with header and checksum) which is compatible
    with MicroPython's deflate.DeflateIO(..., deflate.ZLIB).

    Args:
        data: Bytes to compress
        wbits: Window size (default 10 = 1024 bytes)
               Range: 8-15 (256 bytes to 32KB)
               Smaller = less RAM on device, slightly worse compression

    Returns:
        Compressed bytes in ZLIB format
    """
    compressor = zlib.compressobj(wbits=wbits)
    return compressor.compress(data) + compressor.flush()
