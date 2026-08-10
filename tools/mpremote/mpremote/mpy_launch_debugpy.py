# fmt: off
# This file is vendored byte-for-byte between repos with different ruff
# line-length settings; `ruff format` must leave it alone everywhere so a
# routine reformat in either repo can't silently break the copies apart.
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
stream: `"board"` selects the board's own dedicated DAP interface, `"repl"`
shares the stream this script was launched over, and anything else is a path
this runtime can open. Either way `host`/`port` in the handshake become
`"serial"`/`0` and the `port` argument goes unused. `caps["serial_dap"]`
reports which channel was actually taken, not a board guess. A caller that
asks for a stream and does not get one is told so: this never falls back to
TCP behind the caller's back, because the caller has a bridge waiting on the
stream and nothing listening on a port. `caps["second_cdc"]` is the other
half of that answer and is a build property rather than a session one:
whether this firmware has a second CDC interface for `"board"` to select at
all. `caps["repl_dap"]` is a session property again: whether this run split
the REPL stream, which is the channel a board with one UART and no network
has and the only one that changes what the REPL itself can do while a
session is live (see `docs/debugging.md`).

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

import json
import sys

_banner = r"""
 _____  _______ ______ _______ _______ ______ ___ ___
|     \|    ___|   __ \   |   |     __|   __ \   |   |
|  --  |    ___|   __ <   |   |    |  |    __/\     /
|_____/|_______|______/_______|_______|___|    |___|
"""


def _detect_host():
    """Return the address debugpy should bind to on this runtime.

    Boards with a `network` module report their own DHCP/WiFi address so
    tooling never has to guess or hardcode a device IP. The unix port has no
    `network` module, and a board with `network` but no configured/connected
    interface has no address to report either - both cases, and any error
    while probing, fall back to binding all interfaces so a network probe
    failure never aborts the launch.
    """
    try:
        import network

        wlan = network.WLAN(network.STA_IF)
        addr = wlan.ipconfig("addr4")[0]
    except Exception:
        return "0.0.0.0"

    if not addr or addr == "0.0.0.0":
        return "0.0.0.0"
    return addr


def _board_dap_stream():
    """Return this board's dedicated DAP interface, or None if it has none.

    stm32 is the only port with an implementation: `pyb.USB_VCP(1)` is the
    second CDC interface, and `pyb.USB_VCP(0)` is the one carrying the REPL
    this script was launched over. The constructor is not the test for
    whether that interface exists - it answers from
    `MICROPY_HW_USB_CDC_NUM`, which is a build-time maximum. Which
    interfaces are really enumerated is decided at boot by `usb_mode`, whose
    name carries the count (`VCP+MSC` for one, `2xVCP+MSC` for two), so that
    is what this reads.

    `isconnected()` is deliberately not the test either: the host opens the
    interface only after reading the handshake that this feeds, so it is
    False here on every run that goes on to succeed. It is what
    `_dap_stream_liveness` reads later, once there is a session to lose.
    """
    try:
        import pyb
    except ImportError:
        return None  # no other port implements a dedicated DAP interface yet
    try:
        mode = pyb.usb_mode()
    except AttributeError:
        return None  # a pyb without USB at all
    if not mode or "xVCP" not in mode:
        return None  # at most one CDC interface, and the REPL is on it
    try:
        return pyb.USB_VCP(1)
    except ValueError:
        return None  # firmware built with a single CDC; usb_mode lied


def _probe_second_cdc():
    """True when this build has a second CDC interface DAP could run over.

    The build's maximum, which is a different question from either of the
    two next to it and can answer differently on the same run:
    `pyb.USB_VCP(id)` constructs for every `id` below
    `MICROPY_HW_USB_CDC_NUM`, a compile-time constant, so this stays True on
    a board booted `VCP+MSC` with a single interface enumerated, and it is
    True before any host has opened the interface. `_board_dap_stream()`
    asks the narrower question - is there an interface here on this boot -
    and reads `usb_mode` for it; `isconnected()` asks the narrowest, whether
    a host is holding it. The three are meant to be able to disagree.

    Only stm32 defines the flag, so every other port reports False. That is
    the right answer for the current firmware variants - rp2 and esp32 build
    TinyUSB with `CFG_TUD_CDC (1)`, and the unix port has no USB device
    peripheral - but it is False by ignorance rather than by measurement, so
    a port that gains a second CDC has to be taught to this probe before a
    manifest may claim it.

    The expected failures are `ImportError` (no `pyb`), `AttributeError` (a
    `pyb` with no USB) and `ValueError` (a single-CDC build), but anything at
    all reads as False, for the same reason `_detect_host()` swallows its own
    probe: a capability report is not worth aborting a launch over.
    """
    try:
        import pyb

        pyb.USB_VCP(1)
    except Exception:
        return False
    return True


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
    raising `KeyboardInterrupt`. `docs/debugging.md` states that trade-off.
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
    mux.attach(previous)
    _repl_mux.append(mux)
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

    `spec` is the caller's choice of channel: `None` for TCP, `"board"` for
    the board's dedicated DAP interface, `"repl"` for a share of the stream
    this script was launched over, or a path this runtime can open directly
    (what the unix port has instead of a USB interface). Failing to produce
    the requested stream raises rather than returning None, so the caller
    never gets a TCP endpoint it has no client for.

    `caps["serial_dap"]` is derived from which channel `_run()` actually
    picked (see `debugpy.get_capabilities()`), never guessed here, so the two
    cannot disagree.
    """
    if spec is None:
        return None
    if spec == "board":
        stream = _board_dap_stream()
        if stream is None:
            raise OSError("no dedicated DAP interface on this board")
        return stream
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
    import debugpy

    print(_banner)
    print("MicroPython VS Code Debugging")
    print(
        "Usage: mpy_launch_debugpy.py [target_module] [target_method] [port] [dap_stream] [loop]"
    )
    print("==================================")

    target_module, target_method, port, dap_stream, loop = _parse_args()
    print(f"Target module: {target_module}")
    print(f"Target method: {target_method}")
    print("==================================")

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
    # session's own dict, and the second CDC is not the debug server's to
    # report - it is USB topology, which only the boot script can see.
    caps = debugpy.get_capabilities().copy()
    caps["second_cdc"] = _probe_second_cdc()
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
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Last, and after the prints above, so anything they said still goes
        # out through the framing the host is still reading.
        _release_repl_stream()
# fmt: on
