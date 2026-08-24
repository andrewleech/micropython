#!/usr/bin/env python3
#
# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2023 Jim Mussared
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

import ast, binascii, errno, hashlib, os, re, sys
from collections import namedtuple
from .compression_utils import (
    compress_chunk,
    test_compression_ratio,
    MIN_COMPRESS_SIZE,
    MIN_COMPRESS_RATIO,
    DEFLATE_CHUNK_SIZE,
    DEFLATE_WBITS,
    BASE64_CHUNK_SIZE,
)

from .mp_errno import MP_ERRNO_TABLE

# Buffer for accumulating bytes that may be split UTF-8 sequences
_stdout_buffer = b""


def stdout_write_bytes(b: bytes):
    """Write bytes to stdout, handling UTF-8 encoding and buffering incomplete sequences.

    Strips CTRL-D (0x04) bytes and buffers incomplete UTF-8 sequences across calls.
    Uses binary mode with sequence validation when available, otherwise decodes
    with replacement characters for invalid sequences.
    """
    global _stdout_buffer
    b = b.replace(b"\x04", b"")
    if not b:
        return

    _stdout_buffer += b

    if hasattr(sys.stdout, "buffer"):
        # Try to decode to find complete UTF-8 sequences
        # Write only complete sequences, keep incomplete trailing bytes buffered
        try:
            # Try to decode the entire buffer
            _stdout_buffer.decode("utf-8")
            # Success - all bytes form valid UTF-8, write them all
            sys.stdout.buffer.write(_stdout_buffer)
            sys.stdout.buffer.flush()
            _stdout_buffer = b""
        except UnicodeDecodeError as e:
            # Check if error is at the end (incomplete sequence) vs middle (invalid)
            if e.start == 0 and e.reason == "unexpected end of data":
                # Starts with incomplete sequence - keep buffering
                pass
            elif e.start > 0:
                # Write valid prefix, keep incomplete/invalid suffix
                valid_bytes = _stdout_buffer[: e.start]
                sys.stdout.buffer.write(valid_bytes)
                sys.stdout.buffer.flush()
                _stdout_buffer = _stdout_buffer[e.start :]
                # If remaining is just incomplete trailing bytes, keep them
                # If it's invalid, write with replacement
                if len(_stdout_buffer) > 4:  # Max UTF-8 sequence is 4 bytes
                    sys.stdout.buffer.write(_stdout_buffer[:1])
                    _stdout_buffer = _stdout_buffer[1:]
            else:
                # Invalid sequence at start - write one byte and continue
                sys.stdout.buffer.write(_stdout_buffer[:1])
                sys.stdout.buffer.flush()
                _stdout_buffer = _stdout_buffer[1:]
    else:
        text = _stdout_buffer.decode(sys.stdout.encoding, "replace")
        sys.stdout.write(text)
        _stdout_buffer = b""


class TransportError(Exception):
    pass


class TransportExecError(TransportError):
    def __init__(self, status_code, error_output):
        self.status_code = status_code
        self.error_output = error_output
        super().__init__(error_output)


listdir_result = namedtuple("dir_result", ["name", "st_mode", "st_ino", "st_size"])


def _quote_path(path: str) -> str:
    """
    Properly escape a path string for use in Python code sent to the REPL.
    Uses repr() to handle all special characters including quotes, backslashes, and Unicode characters.
    """
    return repr(path)


# Takes a Transport error (containing the text of an OSError traceback) and
# raises it as the corresponding OSError-derived exception.
def _convert_filesystem_error(e, info):
    if "OSError" in e.error_output:
        for code, estr in [
            *errno.errorcode.items(),
            (errno.EOPNOTSUPP, "EOPNOTSUPP"),
        ]:
            if estr in e.error_output:
                return OSError(code, info)

        # Some targets don't render OSError with the name of the errno, so in these
        # cases support an explicit mapping of errnos to known numeric codes.
        error_lines = e.error_output.splitlines()
        match = re.match(r"OSError: (\d+)$", error_lines[-1])
        if match:
            value = int(match.group(1), 10)
            if value in MP_ERRNO_TABLE:
                return OSError(MP_ERRNO_TABLE[value], info)

    return e


class Transport:
    def fs_listdir(self, src=""):
        buf = bytearray()

        def repr_consumer(b):
            buf.extend(b.replace(b"\x04", b""))

        cmd = "import os\nfor f in os.ilistdir(%s):\n print(repr(f), end=',')" % (
            _quote_path(src) if src else ""
        )
        try:
            buf.extend(b"[")
            self.exec(cmd, data_consumer=repr_consumer)
            buf.extend(b"]")
        except TransportExecError as e:
            raise _convert_filesystem_error(e, src) from None

        return [
            listdir_result(*f) if len(f) == 4 else listdir_result(*(f + (0,)))
            for f in ast.literal_eval(buf.decode())
        ]

    def fs_stat(self, src):
        try:
            self.exec("import os")
            return os.stat_result(self.eval("tuple(os.stat(%s))" % _quote_path(src)))
        except TransportExecError as e:
            raise _convert_filesystem_error(e, src) from None

    def fs_exists(self, src):
        try:
            self.fs_stat(src)
            return True
        except OSError:
            return False

    def fs_isdir(self, src):
        try:
            mode = self.fs_stat(src).st_mode
            return (mode & 0x4000) != 0
        except OSError:
            # Match CPython, a non-existent path is not a directory.
            return False

    def fs_printfile(self, src, chunk_size=256):
        cmd = (
            "with open(%s) as f:\n while 1:\n"
            "  b=f.read(%u)\n  if not b:break\n  print(b,end='')" % (_quote_path(src), chunk_size)
        )
        try:
            self.exec(cmd, data_consumer=stdout_write_bytes)
        except TransportExecError as e:
            raise _convert_filesystem_error(e, src) from None

    def fs_readfile(self, src, chunk_size=256, progress_callback=None, verify_hash=False):
        if progress_callback:
            src_size = self.fs_stat(src).st_size

        contents = bytearray()

        try:
            if verify_hash:
                # Initialize hash on device along with file read
                self.exec(
                    "import hashlib\nh=hashlib.sha256()\nf=open(%s,'rb')\nr=f.read\nu=h.update"
                    % _quote_path(src)
                )
            else:
                self.exec("f=open(%s,'rb')\nr=f.read" % _quote_path(src))

            while True:
                chunk = self.eval("r({})".format(chunk_size))
                if not chunk:
                    break
                if verify_hash:
                    # Update hash with chunk
                    self.exec("u(" + repr(chunk) + ")")
                contents.extend(chunk)
                if progress_callback:
                    progress_callback(len(contents), src_size)
            self.exec("f.close()")

            if verify_hash:
                # Get final hash from device
                remote_hash = self.eval("h.digest()")
                # Calculate local hash
                local_hash = hashlib.sha256(contents).digest()
                if remote_hash != local_hash:
                    raise TransportError("file transfer verification failed for '%s'" % src)
        except TransportExecError as e:
            raise _convert_filesystem_error(e, src) from None

        return contents

    def detect_encoding_capabilities(self):
        """Detect available encoding methods on device. Cached after first call."""
        if hasattr(self, "_fs_encoding_caps"):
            return self._fs_encoding_caps

        # Two separate eval() calls so that a missing deflate module (which
        # raises ImportError inside the dict expression) doesn't prevent
        # base64/bytesio from being detected.
        try:
            caps = self.eval(
                "{"
                "'bytesio':hasattr(__import__('io'),'BytesIO'),"
                "'base64':hasattr(__import__('binascii'),'a2b_base64'),"
                "'fromhex':hasattr(bytes,'fromhex'),"
                "}"
            )
        except TransportExecError:
            caps = {}

        try:
            has_deflate = self.eval("hasattr(__import__('deflate'),'DeflateIO')")
        except TransportExecError:
            has_deflate = False

        self._fs_encoding_caps = {
            "deflate": has_deflate and caps.get("bytesio") and caps.get("base64"),
            "base64": caps.get("base64", False),
            "fromhex": caps.get("fromhex", False),
        }

        return self._fs_encoding_caps

    def _choose_encoding_for_data(self, data):
        """Choose best encoding based on device capabilities and data compressibility.

        Three-tier fallback:
        1. deflate+base64 - if device has deflate/io/binascii and data compresses well
        2. base64 - if device has binascii.a2b_base64
        3. repr - universal fallback

        Returns: (encoding, ratio) where encoding is 'deflate', 'base64', or 'repr',
            and ratio is the compression ratio (float) for deflate, or None otherwise.
        """
        caps = self.detect_encoding_capabilities()

        if caps.get("deflate") and len(data) > MIN_COMPRESS_SIZE:
            ratio = test_compression_ratio(data)
            if ratio < MIN_COMPRESS_RATIO:
                return "deflate", ratio

        if caps.get("base64"):
            return "base64", None

        return "repr", None

    _VALID_ENCODINGS = ("deflate", "base64", "repr")

    def fs_writefile(
        self,
        dest,
        data,
        chunk_size=None,
        progress_callback=None,
        encoding=None,
        verify_hash=False,
    ):
        """Write data to a file on the device.

        Automatically selects the best encoding based on device capabilities and
        data compressibility, with dynamic chunk sizing per encoding method.

        Args:
            dest: Destination path on device
            data: Bytes to write
            chunk_size: Chunk size in bytes, or None for encoding-appropriate default.
            progress_callback: Optional callback(written, total)
            encoding: Force encoding: 'deflate', 'base64', 'repr', or None (auto-select)
            verify_hash: Compare a device-side digest against the source. Independent
                of `encoding`: the digest is taken over each decoded chunk, so it
                verifies what was written rather than what was sent, whichever
                encoding carried it.
        """
        if progress_callback:
            src_size = len(data)
            written = 0

        # Auto-select encoding based on data compressibility
        if encoding is None:
            encoding, _ratio = self._choose_encoding_for_data(data)

        if encoding not in self._VALID_ENCODINGS:
            raise ValueError(
                "encoding must be one of %s, got %r" % (self._VALID_ENCODINGS, encoding)
            )

        # Dynamic chunk sizing: larger chunks = fewer exec() round trips.
        # Only auto-size when caller didn't specify.
        if chunk_size is None:
            if encoding == "deflate":
                chunk_size = DEFLATE_CHUNK_SIZE
            elif encoding == "base64":
                chunk_size = BASE64_CHUNK_SIZE
            else:
                chunk_size = 256

        try:
            if verify_hash:
                # Taken before the loop, which consumes `data`.
                source_hash = hashlib.sha256(data).digest()

            # Setup imports and file handle on device. The two features are
            # orthogonal here: the encoding decides which decoder is imported,
            # `verify_hash` adds the digest, and the write loop below binds both.
            setup = []
            if encoding == "deflate":
                setup += [
                    "from binascii import a2b_base64",
                    "from io import BytesIO",
                    "from deflate import DeflateIO,RAW",
                ]
            elif encoding == "base64":
                setup.append("from binascii import a2b_base64")
            if verify_hash:
                setup += ["import hashlib", "h=hashlib.sha256()"]
            setup += ["f=open(%s,'wb')" % _quote_path(dest), "w=f.write"]
            if verify_hash:
                setup.append("u=h.update")
            self.exec("\n".join(setup))

            while data:
                chunk = data[:chunk_size]
                # Each branch yields an expression for the *decoded* bytes, so
                # the digest below covers the file's contents, not the wire form.
                if encoding == "deflate":
                    compressed = compress_chunk(chunk)
                    b64 = binascii.b2a_base64(compressed).strip()
                    decoded = "DeflateIO(BytesIO(a2b_base64(%s)),RAW,%d).read()" % (
                        b64,
                        DEFLATE_WBITS,
                    )
                elif encoding == "base64":
                    b64 = binascii.b2a_base64(chunk).strip()
                    decoded = "a2b_base64(%s)" % b64
                else:
                    decoded = repr(chunk)
                if verify_hash:
                    self.exec("d=" + decoded + "\nw(d)\nu(d)")
                else:
                    self.exec("w(" + decoded + ")")
                data = data[len(chunk) :]
                if progress_callback:
                    written += len(chunk)
                    progress_callback(written, src_size)

            self.exec("f.close()")

            if verify_hash:
                remote_hash = self.eval("h.digest()")
                if remote_hash != source_hash:
                    raise TransportError("file transfer verification failed for '%s'" % dest)
        except TransportExecError as e:
            raise _convert_filesystem_error(e, dest) from None

    def fs_mkdir(self, path):
        try:
            self.exec("import os\nos.mkdir(%s)" % _quote_path(path))
        except TransportExecError as e:
            raise _convert_filesystem_error(e, path) from None

    def fs_ensure_path_exists(self, path):
        # mkdir every directory in dirname(path) that doesn't already exist.
        # Equivalent to os.makedirs(os.path.dirname(path)) run on the device.
        split = path.split("/")

        # Handle paths starting with "/".
        if not split[0]:
            split.pop(0)
            split[0] = "/" + split[0]

        prefix = ""
        for i in range(len(split) - 1):
            prefix += split[i]
            if not self.fs_exists(prefix):
                self.fs_mkdir(prefix)
            prefix += "/"

    def fs_rmdir(self, path):
        try:
            self.exec("import os\nos.rmdir(%s)" % _quote_path(path))
        except TransportExecError as e:
            raise _convert_filesystem_error(e, path) from None

    def fs_rmfile(self, path):
        try:
            self.exec("import os\nos.remove(%s)" % _quote_path(path))
        except TransportExecError as e:
            raise _convert_filesystem_error(e, path) from None

    def fs_touchfile(self, path):
        try:
            self.exec("f=open(%s,'a')\nf.close()" % _quote_path(path))
        except TransportExecError as e:
            raise _convert_filesystem_error(e, path) from None

    def fs_hashfile(self, path, algo, chunk_size=256):
        try:
            self.exec("import hashlib\nh = hashlib.{algo}()".format(algo=algo))
        except TransportExecError:
            # hashlib (or hashlib.{algo}) not available on device. Do the hash locally.
            data = self.fs_readfile(path, chunk_size=chunk_size)
            return getattr(hashlib, algo)(data).digest()
        try:
            self.exec(
                "buf = memoryview(bytearray({chunk_size}))\nwith open({path}, 'rb') as f:\n while True:\n  n = f.readinto(buf)\n  if n == 0:\n   break\n  h.update(buf if n == {chunk_size} else buf[:n])\n".format(
                    chunk_size=chunk_size, path=_quote_path(path)
                )
            )
            return self.eval("h.digest()")
        except TransportExecError as e:
            raise _convert_filesystem_error(e, path) from None
