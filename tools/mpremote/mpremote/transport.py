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

import ast, os, sys
from collections import namedtuple


class TransportError(Exception):
    pass


listdir_result = namedtuple("dir_result", ["name", "st_mode", "st_ino", "st_size"])


# Takes a Transport error (containing the text of an OSError traceback) and
# raises it as the corresponding OSError-derived exception.
def _convert_filesystem_error(e, info):
    if len(e.args) >= 3:
        if b"OSError" in e.args[2] and b"ENOENT" in e.args[2]:
            return FileNotFoundError(info)
        if b"OSError" in e.args[2] and b"EISDIR" in e.args[2]:
            return IsADirectoryError(info)
        if b"OSError" in e.args[2] and b"EEXIST" in e.args[2]:
            return FileExistsError(info)
    return e


class Transport:
    def fs_listdir(self, src=""):
        buf = bytearray()

        def repr_consumer(b):
            buf.extend(b.replace(b"\x04", b""))

        cmd = "import os\nfor f in os.ilistdir(%s):\n" " print(repr(f), end=',')" % (
            ("'%s'" % src) if src else ""
        )
        try:
            buf.extend(b"[")
            self.exec(cmd, data_consumer=repr_consumer)
            buf.extend(b"]")
        except TransportError as e:
            raise _convert_filesystem_error(e, src) from None

        return [
            listdir_result(*f) if len(f) == 4 else listdir_result(*(f + (0,)))
            for f in ast.literal_eval(buf.decode())
        ]

    def fs_stat(self, src):
        try:
            self.exec("import os")
            return os.stat_result(self.eval("os.stat(%s)" % (("'%s'" % src))))
        except TransportError as e:
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
        def stdout_write_bytes(b):
            b = b.replace(b"\x04", b"")
            sys.stdout.buffer.write(b)
            sys.stdout.buffer.flush()

        cmd = (
            "with open('%s') as f:\n while 1:\n"
            "  b=f.read(%u)\n  if not b:break\n  print(b,end='')" % (src, chunk_size)
        )
        try:
            self.exec(cmd, data_consumer=stdout_write_bytes)
        except TransportError as e:
            raise _convert_filesystem_error(e, src) from None

    def fs_readfile(self, src, chunk_size=256, progress_callback=None):
        if progress_callback:
            src_size = self.fs_stat(src).st_size

        contents = bytearray()

        try:
            self.exec("f=open('%s','rb')\nr=f.read" % src)
            while True:
                chunk = self.eval("r({})".format(chunk_size))
                if not chunk:
                    break
                contents.extend(chunk)
                if progress_callback:
                    progress_callback(len(contents), src_size)
            self.exec("f.close()")
        except TransportError as e:
            raise _convert_filesystem_error(e, src) from None

        return contents

    def fs_writefile(self, dest, data, chunk_size=256, progress_callback=None):
        if progress_callback:
            src_size = len(data)
            written = 0

        try:
            self.exec("f=open('%s','wb')\nw=f.write" % dest)
            while data:
                chunk = data[:chunk_size]
                self.exec("w(" + repr(chunk) + ")")
                written += len(chunk)
                data = data[len(chunk) :]
                if progress_callback:
                    progress_callback(written, src_size)
            self.exec("f.close()")
        except TransportError as e:
            raise _convert_filesystem_error(e, dest) from None

    def fs_mkdir(self, path):
        try:
            self.exec("import os\nos.mkdir('%s')" % path)
        except TransportError as e:
            raise _convert_filesystem_error(e, path) from None

    def fs_rmdir(self, path):
        try:
            self.exec("import os\nos.rmdir('%s')" % path)
        except TransportError as e:
            raise _convert_filesystem_error(e, path) from None

    def fs_rmfile(self, path):
        try:
            self.exec("import os\nos.remove('%s')" % path)
        except TransportError as e:
            raise _convert_filesystem_error(e, path) from None

    def fs_touchfile(self, path):
        try:
            self.exec("f=open('%s','a')\nf.close()" % path)
        except TransportError as e:
            raise _convert_filesystem_error(e, path) from None
