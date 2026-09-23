# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
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
"""Single parameterised boot script for MicroPython debugpy sessions.

Usage: mpy_launch_debugpy.py [target_module] [target_method] [port] [dap_stream] [loop]

Every argument is positional, so one that is not given but is followed by one
that is has to be passed as the empty string; an empty `port` or `dap_stream`
reads the same as leaving it off the end.

The bind address is probed at runtime rather than passed in: boards with a
`network` module report their own address, everything else binds all
interfaces. No device IP is hardcoded by the caller; the port, if given, is
supplied by the caller (0 is rejected by `debugpy.listen()` on every current
MicroPython port, since none implements `socket.getsockname()`). The actual
bound endpoint plus the probed firmware capabilities are reported in a
single machine-readable handshake line on stdout, printed as soon as the
socket is bound and before any client has attached:

    MPDBG-READY {"host": "...", "port": ..., "caps": {...}}

Tooling parses that one line rather than any of the human-readable banner
text around it. `wait_for_client()` (not a fixed sleep) blocks until the DAP
client has finished configuring breakpoints, so breakpoints set before then
are already applied by the time the target starts running.

`dap_stream`, when given, moves the DAP channel off TCP and onto a byte
stream: `"repl"` shares the stream this script was launched over, and
anything else is a path this runtime can open. Either way `host`/`port` in
the handshake become `"serial"`/`0` and the `port` argument goes unused. A
caller that asks for a stream and does not get one is told so: this never
falls back to TCP behind the caller's back, because the caller has a bridge
waiting on the stream and nothing listening on a port. `caps["repl_dap"]`
reports whether this run split the REPL stream - a property of the session
rather than of the build, and the channel a board with one UART and no
network has. It is the only one that changes what the REPL itself can do
while a session is live (see the `debug` section of the mpremote docs).

`loop`, when the literal `"loop"`, keeps the process and the DAP session alive
across re-runs of the target: the DAP `restart` request is advertised and
honoured, and each restart evicts whatever the target imported from
`sys.modules` and imports it again, so a source edit takes effect with no
upload and no reset. Each re-run announces itself with

    MPDBG-RESTART {"iteration": N, "evicted": [...]}

which is deliberately not another MPDBG-READY: the endpoint has not changed.
That line goes to the client's debug console as well as to stdout, because a
mounted serial session's host never sees anything the device prints.
"""

import gc
import json
import sys


def _detect_host():
    """Return the address debugpy should bind to on this runtime.

    A board that has an address of its own reports it, so tooling never has
    to guess or hardcode a device IP. USB networking comes first: its peer is
    the host at the other end of the cable, the one driving this session, so
    its address is always reachable from there where a WLAN one may not be.
    Then cheapest first: a wired `LAN` is up without anything having to
    associate, while constructing a `WLAN` starts the wifi driver on some
    ports. Only an interface that is already active is asked, since bringing
    one up is the caller's business and not a side effect of launching a
    debug session.

    Everything else - the unix port with no `network` module, a board whose
    interfaces are all down, any error while probing - falls back to binding
    all interfaces, so a probe failure never aborts the launch. The host
    side treats that as "no address" rather than something to connect to.
    """
    try:
        import network
    except ImportError:
        return "0.0.0.0"

    makers = []
    if hasattr(network, "USBD_NCM"):
        makers.append(network.USBD_NCM)
    if hasattr(network, "LAN"):
        makers.append(network.LAN)
    if hasattr(network, "WLAN"):
        makers.append(lambda: network.WLAN(network.STA_IF))

    for make in makers:
        try:
            nic = make()
            if not nic.active():
                continue
            try:
                addr = nic.ipconfig("addr4")[0]
            except (AttributeError, ValueError, OSError):
                # Firmware predating ipconfig().
                addr = nic.ifconfig()[0]
        except Exception:
            continue
        if addr and addr != "0.0.0.0":
            return addr
    return "0.0.0.0"


# The dupterm slot the REPL occupies on the ports that put it in one. stm32
# fixes it at 1 (`pyb_usb_vcp_init0`), and no other port currently has enough
# slots for it to be anything else; a port that arrives with a different
# arrangement has to be taught this rather than discovering it, since reading
# every slot to find the busy one would displace whichever came first.
_REPL_DUPTERM_SLOT = 1

# Holds the one `ReplMux` for the length of a run that split the REPL stream,
# empty otherwise. Two things read it: the handshake, for `caps["repl_dap"]`,
# and the release at exit, which has to put the REPL back.
_repl_mux = []


def _repl_dap_stream():
    """Split the REPL's own stream and return the DAP half.

    The channel a board with one UART and no network has. What makes it
    possible is that on some ports the runtime's console is a Python object in
    a `dupterm` slot, so replacing it with a framing wrapper puts DAP on the
    same wire and leaves program output on it too, marked apart. Where the
    slot is empty the runtime writes to its console directly and no Python
    object can intercept it, so this refuses rather than handing back a stream
    that would carry nothing: rp2 and esp32 build one slot and the REPL is not
    in it, and the unix port has no `dupterm` at all.

    The REPL is displaced for the length of the session. On stm32, installing
    anything in the slot detaches the interface from the REPL
    (`usb_vcp_attach_to_repl(vcp, false)`), which stops the interrupt
    character being scanned, so Ctrl-C reaches the target as data instead of
    raising `KeyboardInterrupt`. The mpremote docs state that trade-off.

    Which is why the stream must be able to say when the host has let go. On
    every other channel a session that waits forever costs nothing the user
    cannot walk away from; on this one it holds the console the board is
    reached by, and Ctrl-C cannot end it. A stream with no `isconnected` is
    refused here rather than taken and never given back.
    """
    import os

    from debugpy.common import repl_mux

    mux = repl_mux.ReplMux()
    try:
        previous = os.dupterm(mux.console, _REPL_DUPTERM_SLOT)
    except (AttributeError, ValueError, OSError) as er:
        raise OSError(f"this runtime cannot share the REPL stream: {er}")
    if previous is None:
        # An empty slot is not the REPL, and installing into it would have
        # diverted nothing; put it back the way it was found.
        os.dupterm(None, _REPL_DUPTERM_SLOT)
        raise OSError(f"no REPL stream in dupterm slot {_REPL_DUPTERM_SLOT} to share")
    if getattr(previous, "isconnected", None) is None:
        os.dupterm(previous, _REPL_DUPTERM_SLOT)
        raise OSError("the REPL stream cannot report the host letting go of it")
    # Registered before it is attached: from the moment the wrapper is in the
    # slot, the release path has to know about it. A failure in between would
    # otherwise leave the board framing its own console with nothing able to
    # put it back, and on this channel there is no second way in.
    _repl_mux.append(mux)
    try:
        mux.attach(previous)
    except Exception:
        _repl_mux.pop()
        os.dupterm(previous, _REPL_DUPTERM_SLOT)
        raise
    return mux.dap


def _release_repl_stream():
    """Put the REPL back, if this run took it. Safe to call when it did not.

    Runs on every exit path, including a failed one: a board left with the
    framing wrapper in the slot answers a plain REPL with escaped bytes, and
    nothing short of a reset would clear it.
    """
    import os

    while _repl_mux:
        mux = _repl_mux.pop()
        try:
            port = mux.detach()
            os.dupterm(port, _REPL_DUPTERM_SLOT)
        except Exception:
            pass


def _detect_dap_stream(spec=None):
    """Return an open reader/writer stream for the DAP channel, or None for TCP.

    `spec` is the caller's choice of channel: `None` for TCP, `"repl"` for
    a share of the stream this script was launched over, or a path this
    runtime can open directly (what the unix port has instead of a USB
    interface). Failing to produce the requested stream raises rather than
    returning None, so the caller never gets a TCP endpoint it has no client
    for.

    `caps["repl_dap"]` is derived from which channel `_run()` actually picked
    (see `debugpy.get_capabilities()`), never guessed here, so the two cannot
    disagree.
    """
    if spec is None:
        return None
    if spec == "repl":
        return _repl_dap_stream()
    try:
        return open(spec, "r+b")
    except OSError as er:
        raise OSError(f"dap_stream {spec!r} could not be opened: {er}")


def _dap_stream_liveness(stream):
    """How `stream` reports that the host has gone, or None if it cannot.

    A USB CDC interface has no EOF: an idle one and one nobody is holding
    both read as no bytes, so a session whose host disappears - the editor
    killed, the terminal closed - would leave the target stopped at its
    breakpoint until the board is power-cycled. `isconnected()` is that
    interface's DTR line, which the host raises when it opens the port and
    the kernel drops when the last opener goes away, so it is the signal a
    CDC stream has instead of EOF. A stream that does reach EOF - the file
    the unix port opens - has no `isconnected` and needs none.
    """
    return getattr(stream, "isconnected", None)


def _parse_args():
    import debugpy

    args = sys.argv[1:]
    target_module = args[0] if len(args) > 0 else "target"
    target_method = args[1] if len(args) > 1 else "main"
    # Empty reads as "not given" for both of these, which is how a caller
    # passes over one of them to reach a later positional argument.
    port = int(args[2]) if len(args) > 2 and args[2] else debugpy.DEFAULT_PORT
    dap_stream = args[3] if len(args) > 3 else None
    # Anything other than the literal "loop" is refused rather than quietly
    # read as false.
    loop = args[4] if len(args) > 4 else ""
    if loop not in ("", "loop"):
        raise ValueError(f"loop argument must be 'loop' or empty, not {loop!r}")
    if len(args) > 5:
        raise ValueError(
            "Too many arguments. Usage: mpy_launch_debugpy.py "
            "[target_module] [target_method] [port] [dap_stream] [loop]"
        )
    return target_module, target_method, port, dap_stream or None, loop == "loop"


def _evict_target_modules(baseline):
    """Drop from sys.modules everything the target's imports added.

    The eviction set is defined by what the target pulled in, not by a list of
    names to spare, so the debugger cannot be evicted out from under itself:
    every module debugpy needs was imported before `baseline` was taken. It also
    means a submodule of the target that changed is re-read along with it, which
    a target-module-only eviction would miss.

    Returns the evicted names, sorted, for the restart marker.
    """
    evicted = []
    for name in list(sys.modules):
        if name not in baseline:
            del sys.modules[name]
            evicted.append(name)
    evicted.sort()
    # Eviction drops the last reference to whole modules, and the re-import
    # about to happen needs the heap they held. On a small board collecting
    # here is the difference between a restart that works and MemoryError.
    gc.collect()
    return evicted


def _report(line, debugpy):
    """Print a marker line to stdout and show it in the client's debug console.

    Both, because neither reaches everyone on its own: a mounted serial session
    discards everything the device prints, and a caller reading stdout may have
    no DAP client of its own. The text is identical on both channels, so there
    is one format to parse rather than two.
    """
    print(line)
    debugpy.console(line + "\n")


def _tracing_survived_unwind():
    """True if sys.settrace still calls back after the unwind that just ran.

    A firmware whose profiling hook leaves its recursion guard set when a trace
    callback raises never invokes another callback, while sys.settrace() and
    sys.gettrace() go on reporting success - so installing one and watching is
    the only way to tell. Silence about that would mean every run after the
    first restart binds no breakpoints at all, with nothing to see.

    Only safe to call after an unwind: on an affected firmware tracing is
    already gone by then, and on a sound one this leaves nothing behind.
    """
    calls = []

    def _count(frame, event, arg):
        calls.append(event)
        return _count

    def _probe():
        return 1

    sys.settrace(_count)
    try:
        _probe()
    finally:
        sys.settrace(None)
    return bool(calls)


def _run():
    # Before `import debugpy`: a firmware built without MICROPY_PY_SYS_SETTRACE
    # cannot be debugged at all, and debugpy's own import fails on it with a
    # message about its internals rather than about the firmware. Checking
    # first is what makes this message reachable.
    if not hasattr(sys, "settrace"):
        print(
            "sys.settrace is not available. You need a firmware compiled with "
            "MICROPY_PY_SYS_SETTRACE."
        )
        return

    import debugpy

    target_module, target_method, port, dap_stream, loop = _parse_args()
    print(f"Target module: {target_module}")
    print(f"Target method: {target_method}")

    if not hasattr(sys, "settrace"):
        print(
            "sys.settrace is not available. You need a firmware compiled with debugging features."
        )
        return

    if loop:
        # Before wait_for_client(), which is where `initialize` is answered.
        debugpy.enable_restart()

    stream = _detect_dap_stream(dap_stream)
    if stream is not None:
        debugpy.listen_stream(stream, is_connected=_dap_stream_liveness(stream))
        actual_host, actual_port = "serial", 0
    else:
        host = _detect_host()
        actual_host, actual_port = debugpy.listen(host=host, port=port)
    print(f"Debug server listening on {actual_host}:{actual_port}")

    # `.copy()` first: with a session live `get_capabilities()` hands back the
    # session's own dict, and which channel this run took is not the debug
    # server's to report - only the boot script knows it split the REPL.
    caps = debugpy.get_capabilities().copy()
    caps["repl_dap"] = bool(_repl_mux)
    # Exactly one MPDBG-READY line, valid JSON, nothing else on this line.
    print("MPDBG-READY " + json.dumps({"host": actual_host, "port": actual_port, "caps": caps}))

    print("Waiting for the client to finish configuring (configurationDone)...")
    if not debugpy.wait_for_client():
        print(
            "[DAP] No client finished configuring (timed out or disconnected) - "
            "not running the target under a dead debug session."
        )
        debugpy.disconnect()
        return

    # Everything the debugger needs is imported by now, so nothing captured here
    # is ever a candidate for eviction on a restart.
    baseline_modules = set(sys.modules)

    iteration = 0
    while True:
        iteration += 1
        if iteration > 1:
            evicted = _evict_target_modules(baseline_modules)
            # A restart is NOT another MPDBG-READY: the endpoint has not
            # changed and a caller that re-parsed one would think a second
            # session had started.
            _report(
                "MPDBG-RESTART " + json.dumps({"iteration": iteration, "evicted": evicted}),
                debugpy,
            )
            if not _tracing_survived_unwind():
                _report(
                    "MPDBG-DEGRADED sys.settrace stopped calling back after the unwind; "
                    "this run has no breakpoints. The firmware needs the fix that clears "
                    "the profiling recursion guard when a trace callback raises.",
                    debugpy,
                )

        # Traced from here to the end of the run and no further: the loop's own
        # code must not be traced, or a restart handled while it waits below
        # would unwind the loop itself. The unwind also drops the trace
        # function (sys.settrace semantics), so this re-arms it each iteration.
        debugpy.debug_this_thread()
        restarting = False
        try:
            # Imported only once a client is configured: the module's top-level
            # code runs on import, and it should run under the debugger with the
            # client's breakpoints already in place, not before the session
            # exists. On a later iteration the eviction above is what makes this
            # re-read the file rather than hand back the cached module.
            try:
                target = __import__(target_module, None, None, ("*",))
            except ImportError as e:
                print(f"Error importing target module '{target_module}': {e}")
                return

            method = getattr(target, target_method, None)
            if method is None:
                print(f"Method '{target_method}' not found in module '{target_module}'")
                return

            result = method()
        except debugpy.RestartRequest:
            # The target was unwound part-way through on purpose; it has no
            # result, and the next iteration is already asked for.
            print("Target restarting")
            restarting = True
        finally:
            sys.settrace(None)

        if not restarting:
            print("Target completed successfully!")
            if result is None:
                print("No result returned from target method")
            else:
                print("Result type:", type(result))
                print("Result:", result)

        if not loop:
            return
        if not restarting and not debugpy.wait_for_restart():
            # No client left to restart for. Ending here rather than looping
            # keeps a session whose client went away from spinning forever.
            print("Debug client gone; not waiting for another restart")
            return


# Guarded so importing this module does not run device boot code: it ships as
# a resource inside the mpremote package, where a package walk or autodoc pass
# would otherwise execute it on the host. Both real invocations - `micropython
# mpy_launch_debugpy.py ...` and the raw-REPL exec mpremote performs - run it
# as __main__.
if __name__ == "__main__":
    try:
        _run()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Last, and after the prints above, so anything they said still goes
        # out through the framing the host is still reading.
        _release_repl_stream()
