# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2022 Jim Mussared
# Copyright (c) 2026 Andrew Leech
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

import micropython
from micropython import const
import sys
import asyncio
import select


_CHAR_CTRL_C = const(3)
_PYEXEC_FORCED_EXIT = const(0x100)
_PYEXEC_RAW_ACTIVE = const(0x400)

# Cap on how many already-buffered bytes task() will drain ahead in one go
# (see `pending` below), so a sustained burst can't shut out other tasks
# indefinitely.
_MAX_READAHEAD = const(256)

# Raw terminal mode: no echo/line-buffering, escape sequences delivered raw.
# Absent on ports without MICROPY_PY_MICROPYTHON_STDIO_RAW (e.g. non-unix).
_stdio_raw = getattr(micropython, "stdio_mode_raw", lambda _: None)


async def _async_exec(coro, s, pending):
    """Run a coroutine compiled from a top-level-await REPL line on the loop.

    The C REPL compiles a line containing top-level await into a coroutine (it
    runs every other line itself) and hands it here to run on the event loop.
    Print the repr of a non-None result, and cancel on Ctrl-C from the stream.
    Bytes read while watching for Ctrl-C are type-ahead for the next line; they
    are collected in `pending` for the caller to replay, rather than dropped.
    """
    try:
        exec_task = asyncio.create_task(coro)

        async def kbd_intr_task(exec_task, s, pending):
            while True:
                b = await s.read(1)
                if not b:
                    return
                if ord(b[0]) == _CHAR_CTRL_C:
                    exec_task.cancel()
                    return
                pending.append(ord(b[0]))

        intr_task = asyncio.create_task(kbd_intr_task(exec_task, s, pending))
        try:
            try:
                result = await exec_task
                if result is not None:
                    sys.stdout.write(repr(result))
                    sys.stdout.write("\r\n")
            except asyncio.CancelledError:
                pass
        finally:
            intr_task.cancel()
            try:
                await intr_task
            except asyncio.CancelledError:
                pass

    except Exception as err:
        sys.print_exception(err, sys.stdout)


# REPL task. Drives the native C event-driven REPL: all line editing, paste,
# raw REPL, raw-paste and synchronous execution happen in C; only complete lines
# containing top-level `await` are compiled to a coroutine and handed back here
# to run on the event loop.
#
# stop_loop_on_exit: on Ctrl-D stop the event loop (default), so a boot REPL
#   soft-resets; set False to just end this task and leave the loop and any
#   other tasks running.
# persistent: if True, Ctrl-D re-prompts instead of exiting, keeping the console
#   up for the life of the program (it still ends if stdin closes).
async def task(stop_loop_on_exit=True, persistent=False):
    # Deliver Ctrl-C as a byte to the REPL rather than raising KeyboardInterrupt.
    micropython.kbd_intr(-1)
    _stdio_raw(True)
    s = asyncio.StreamReader(sys.stdin)
    # Registered once for the task's life. asyncio.StreamReader.read() re-arms
    # its own poll registration on every single-byte read, which is fine for
    # interactive typing but costly for a pasted block (one register/poll/
    # unregister cycle per byte); this poller instead lets the drain below
    # check readiness directly, so a burst only pays that cost once.
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    # Bytes queued ahead of the next repl_event() call: either read-ahead
    # drained below, or type-ahead collected by _async_exec while a coroutine
    # ran. Both are appended in arrival order and popped from the front, so
    # the two sources interleave correctly without needing to be told apart.
    pending = []
    try:
        micropython.repl_event_init()
        while True:
            if pending:
                c = pending.pop(0)
            else:
                try:
                    b = await s.read(1)
                except UnicodeError:
                    continue  # garbage byte on stdin (e.g. USB CDC reconnect)
                if not b:
                    return
                pending.append(ord(b[0]))
                # Drain whatever else is already available in one go, instead
                # of paying the register/poll/unregister cycle again for
                # every remaining byte of the same burst.
                while len(pending) < _MAX_READAHEAD and next(poller.ipoll(0), None) is not None:
                    try:
                        ch = sys.stdin.read(1)
                    except UnicodeError:
                        continue
                    if not ch:
                        break
                    pending.append(ord(ch[0]))
                c = pending.pop(0)
            r = micropython.repl_event(c)
            # In raw mode feed input synchronously (no await/yield) so a
            # concurrent task's stdout can't corrupt a raw/raw-paste transfer.
            # Bytes already read ahead into `pending` (e.g. the rest of a
            # raw-paste burst that arrived together with the byte that
            # triggered raw mode) must be drained first, in order, before
            # reading fresh ones from stdin.
            while isinstance(r, int) and r & _PYEXEC_RAW_ACTIVE and not r & _PYEXEC_FORCED_EXIT:
                if pending:
                    c = pending.pop(0)
                else:
                    try:
                        ch = sys.stdin.read(1)
                    except UnicodeError:
                        continue
                    if not ch:
                        return
                    c = ord(ch[0])
                r = micropython.repl_event(c)
            if not isinstance(r, int):
                # C deferred a top-level-await line as a coroutine; run it here.
                # Anything still in pending (read ahead but not yet dispatched)
                # stays at the front, ahead of type-ahead _async_exec collects
                # while the coroutine runs.
                await _async_exec(r, s, pending)
                micropython.repl_event_resume()
            elif r & _PYEXEC_FORCED_EXIT:
                # Ctrl-D.
                if persistent:
                    # Keep the console alive: re-prompt instead of exiting.
                    micropython.repl_event_resume()
                    continue
                if stop_loop_on_exit:
                    # Boot REPL: stop the loop so the caller soft-resets.
                    asyncio.get_event_loop().stop()
                return
            if pending:
                # More bytes already buffered: yield once per character, same
                # as if each had come from its own await, so other tasks stay
                # interleaved at the same granularity during a fast paste.
                await asyncio.sleep_ms(0)
    finally:
        poller.unregister(sys.stdin)
        _stdio_raw(False)
        micropython.kbd_intr(3)


async def breakpoint():
    """Drop into a blocking interactive REPL from async code; Ctrl-D resumes.

    Uses the native breakpoint REPL (micropython.repl()), which saves and
    restores readline state so it nests safely inside a running arepl session.
    """
    try:
        micropython.repl()
    finally:
        # micropython.repl() restores original terminal mode on exit; the
        # surrounding asyncio REPL needs raw mode again.
        _stdio_raw(True)
