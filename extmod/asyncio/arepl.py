# MIT license; Copyright (c) 2022 Jim Mussared

import micropython
from micropython import const
import sys
import asyncio


CHAR_CTRL_A = const(1)
CHAR_CTRL_B = const(2)
CHAR_CTRL_C = const(3)
CHAR_CTRL_D = const(4)
CHAR_CTRL_E = const(5)


def _import_name(code):
    """If code is an import statement, return the name to globalize, else None."""
    if code.startswith("import "):
        parts = code.split()
        if len(parts) >= 4 and parts[2] == "as":
            return parts[3]
        return parts[1] if len(parts) >= 2 else None
    if code.startswith("from "):
        parts = code.split()
        try:
            idx = parts.index("import") + 1
        except ValueError:
            return None
        if idx < len(parts):
            if len(parts) > idx + 2 and parts[idx + 1] == "as":
                return parts[idx + 2]
            return parts[idx]
    return None


def _is_identifier(s):
    """Check if s is a valid Python identifier (MicroPython-compatible)."""
    if not s:
        return False
    for i, c in enumerate(s):
        if c == "_" or c.isalpha():
            continue
        if i > 0 and c.isdigit():
            continue
        return False
    return True


def _is_global_assign(code):
    """Check if code starts with 'name = ...' (not ==). Returns name or None."""
    eq = code.find("=")
    if eq > 0 and code[eq + 1 : eq + 2] != "=":
        name = code[:eq].rstrip()
        if _is_identifier(name):
            return name
    return None


def _has_assignment(code):
    """Check if code contains any assignment (= but not ==, !=, <=, >=)."""
    i = 0
    while i < len(code):
        if code[i] == "=" and (i == 0 or code[i - 1] not in "!<>=") and code[i + 1 : i + 2] != "=":
            return True
        i += 1
    return False


async def execute(code, g, s):
    if not code.strip():
        return

    try:
        if "await " in code:
            # Execute the code snippet in an async context.
            name = _import_name(code)
            if name is not None:
                code = "global {}\n    {}".format(name, code)
            else:
                name = _is_global_assign(code)
                if name is not None:
                    code = "global {}\n    {}".format(name, code)
                elif not _has_assignment(code):
                    code = "return {}".format(code)

            code = """
import asyncio
async def __code():
    {}

__exec_task = asyncio.create_task(__code())
""".format(code)

            async def kbd_intr_task(exec_task, s):
                while True:
                    if ord(await s.read(1)) == CHAR_CTRL_C:
                        exec_task.cancel()
                        return

            l = {"__exec_task": None}
            exec(compile(code, "<stdin>", "exec"), g, l)
            exec_task = l["__exec_task"]

            # Concurrently wait for either Ctrl-C from the stream or task
            # completion.
            intr_task = asyncio.create_task(kbd_intr_task(exec_task, s))

            try:
                try:
                    return await exec_task
                except asyncio.CancelledError:
                    pass
            finally:
                intr_task.cancel()
                try:
                    await intr_task
                except asyncio.CancelledError:
                    pass
        else:
            # Execute code snippet directly.
            try:
                try:
                    micropython.kbd_intr(3)
                    try:
                        return eval(compile(code, "<stdin>", "eval"), g)
                    except SyntaxError:
                        # Maybe an assignment, try with exec.
                        return exec(compile(code, "<stdin>", "exec"), g)
                except KeyboardInterrupt:
                    pass
            finally:
                micropython.kbd_intr(-1)

    except Exception as err:
        sys.print_exception(err, sys.stdout)


async def _readline_char(s):
    """Read one character from the async stream, return its ordinal or -1 on EOF."""
    try:
        b = await s.read(1)
    except UnicodeError:
        # Garbage byte on stdin (e.g. USB CDC reconnect); skip it.
        return -2
    if not b:
        return -1
    return ord(b[0])


async def _readline_native(s, ps1, ps2):
    """Read a line using native C readline with continuation handling.

    Returns (line, ret) where ret is the control code that terminated input,
    or (None, None) if EOF was reached.
    """
    micropython.repl_readline_init(ps1, ps2)
    while True:
        c = await _readline_char(s)
        if c == -1:
            return None, None
        if c == -2:
            continue  # skip garbage byte
        result = micropython.repl_readline_process_char(c)
        if result != -1:
            return result  # (line, ret) tuple


async def _paste_mode(s, g, _stdio_raw):
    """Handle paste mode input. Returns when paste is complete or cancelled."""
    sys.stdout.write("\r\npaste mode; Ctrl-C to cancel, Ctrl-D to finish\r\n=== ")
    parts = []
    while True:
        c = await _readline_char(s)
        if c < 0:
            return
        if c == CHAR_CTRL_C:
            # Caller's raw mode state is preserved — no mode switch needed.
            sys.stdout.write("\r\n")
            return
        elif c == CHAR_CTRL_D:
            sys.stdout.write("\r\n")
            _stdio_raw(False)
            result = await execute("".join(parts), g, s)
            if result is not None:
                sys.stdout.write(repr(result))
                sys.stdout.write("\n")
            _stdio_raw(True)
            return
        else:
            parts.append(chr(c))
            if c == 0x0D or c == 0x0A:
                sys.stdout.write("\r\n=== ")
            else:
                sys.stdout.write(chr(c))


def raw_paste(s, window=512):
    sys.stdout.write("R\x01")  # supported
    sys.stdout.write(bytearray([window & 0xFF, window >> 8, 0x01]).decode())
    eof = False
    buff = bytearray(window)
    chunks = []
    while not eof:
        idx = 0
        while idx < window:
            try:
                b = s.read(1)
            except UnicodeError:
                continue  # skip garbage byte, retry same index
            c = ord(b)
            if c == CHAR_CTRL_C or c == CHAR_CTRL_D:
                sys.stdout.write(chr(CHAR_CTRL_D))
                if c == CHAR_CTRL_C:
                    raise KeyboardInterrupt
                chunks.append(bytes(buff[:idx]))
                eof = True
                break
            buff[idx] = c
            idx += 1

        if not eof:
            chunks.append(bytes(buff))
            sys.stdout.write("\x01")  # indicate window available to host

    return b"".join(chunks)


def raw_repl(s, g: dict):
    """
    This function is blocking to prevent other
    async tasks from writing to the stdio stream and
    breaking the raw repl session.
    """
    heading = "raw REPL; CTRL-B to exit\n"
    sys.stdout.write(heading)

    while True:
        parts = []
        sys.stdout.write(">")
        while True:
            try:
                b = s.read(1)
            except UnicodeError:
                continue  # skip garbage byte
            c = ord(b)
            if c == CHAR_CTRL_A:
                rline = "".join(parts)
                parts = []

                if len(rline) == 2 and ord(rline[0]) == CHAR_CTRL_E:
                    if rline[1] == "A":
                        parts.append(raw_paste(s))
                        break
                else:
                    # reset raw REPL
                    sys.stdout.write(heading)
                    sys.stdout.write(">")
                continue
            elif c == CHAR_CTRL_B:
                # exit raw REPL
                sys.stdout.write("\n")
                return
            elif c == CHAR_CTRL_C:
                # clear line
                parts = []
            elif c == CHAR_CTRL_D:
                # entry finished
                # indicate reception of command
                sys.stdout.write("OK")
                break
            else:
                # let through any other raw 8-bit value
                parts.append(b)

        line = "".join(parts)
        if len(line) == 0:
            # Trigger soft reset matching C REPL behaviour.
            raise SystemExit

        try:
            result = exec(compile(line, "<stdin>", "exec"), g)
            if result is not None:
                sys.stdout.write(repr(result))
            sys.stdout.write(chr(CHAR_CTRL_D))
        except Exception as ex:
            print(line)
            sys.stdout.write(chr(CHAR_CTRL_D))
            sys.print_exception(ex, sys.stdout)
        sys.stdout.write(chr(CHAR_CTRL_D))


async def _repl_loop(s, g, _stdio_raw, stop_loop_on_exit, prompt=">>> "):
    """Shared input loop for task() and breakpoint()."""
    while True:
        ps1 = getattr(sys, "ps1", prompt)
        ps2 = getattr(sys, "ps2", "... ")
        try:
            line, ret = await _readline_native(s, ps1, ps2)
        except UnicodeError:
            sys.stdout.write("\r\n")
            continue

        if ret is None:
            return

        if ret == CHAR_CTRL_A:
            _stdio_raw(False)
            raw_repl(sys.stdin, g)
            _stdio_raw(True)
            continue

        if ret == CHAR_CTRL_B or ret == CHAR_CTRL_C:
            continue

        if ret == CHAR_CTRL_D:
            sys.stdout.write("\r\n")
            if stop_loop_on_exit:
                asyncio.get_event_loop().stop()
            return

        if ret == CHAR_CTRL_E:
            await _paste_mode(s, g, _stdio_raw)
            continue

        # ret == 0: line complete
        if not line:
            continue

        _stdio_raw(False)
        result = await execute(line, g, s)
        if result is not None:
            sys.stdout.write(repr(result))
            sys.stdout.write("\n")
        _stdio_raw(True)


# REPL task. Invoke this with an optional mutable globals dict.
async def task(g=None, prompt=">>> "):
    if g is None:
        g = __import__("__main__").__dict__
    try:
        micropython.kbd_intr(-1)
        s = asyncio.StreamReader(sys.stdin)
        _stdio_raw = getattr(micropython, "stdio_mode_raw", lambda _: None)
        _stdio_raw(True)
        await _repl_loop(s, g, _stdio_raw, stop_loop_on_exit=True, prompt=prompt)
    finally:
        _stdio_raw(False)
        micropython.kbd_intr(3)


if hasattr(micropython, "repl_readline_push"):

    async def breakpoint(g=None, prompt=">>> "):
        """Async REPL breakpoint: await this to drop into an interactive REPL.

        Ctrl-D exits back to the caller. Uses readline push/pop so it can be
        called from within a running arepl task() session.
        """
        if g is None:
            g = __import__("__main__").__dict__

        micropython.repl_readline_push()
        _stdio_raw = getattr(micropython, "stdio_mode_raw", lambda _: None)
        s = asyncio.StreamReader(sys.stdin)

        try:
            _stdio_raw(True)
            micropython.kbd_intr(-1)
            await _repl_loop(s, g, _stdio_raw, stop_loop_on_exit=False, prompt=prompt)
        finally:
            _stdio_raw(False)
            micropython.kbd_intr(3)
            micropython.repl_readline_pop()
