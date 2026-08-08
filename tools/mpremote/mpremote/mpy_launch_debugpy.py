# fmt: off
# This file is vendored byte-for-byte between repos with different ruff
# line-length settings; `ruff format` must leave it alone everywhere so a
# routine reformat in either repo can't silently break the copies apart.
"""Single parameterised boot script for MicroPython debugpy sessions.

Usage: mpy_launch_debugpy.py [target_module] [target_method] [port] [dap_stream]

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
stream: `"board"` selects the board's own dedicated DAP interface, anything
else is a path this runtime can open. Either way `host`/`port` in the
handshake become `"serial"`/`0` and the `port` argument goes unused.
`caps["serial_dap"]` reports which channel was actually taken, not a board
guess. A caller that asks for a stream and does not get one is told so: this
never falls back to TCP behind the caller's back, because the caller has a
bridge waiting on the stream and nothing listening on a port.
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
    False here on every run that goes on to succeed.
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


def _detect_dap_stream(spec=None):
    """Return an open reader/writer stream for the DAP channel, or None for TCP.

    `spec` is the caller's choice of channel: `None` for TCP, `"board"` for
    the board's dedicated DAP interface, or a path this runtime can open
    directly (what the unix port has instead of a USB interface). Failing to
    produce the requested stream raises rather than returning None, so the
    caller never gets a TCP endpoint it has no client for.

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
    try:
        return open(spec, "r+b")
    except OSError as er:
        raise OSError(f"dap_stream {spec!r} could not be opened: {er}")


def _parse_args():
    import debugpy

    args = sys.argv[1:]
    target_module = args[0] if len(args) > 0 else "target"
    target_method = args[1] if len(args) > 1 else "main"
    port = int(args[2]) if len(args) > 2 else debugpy.DEFAULT_PORT
    dap_stream = args[3] if len(args) > 3 else None
    if len(args) > 4:
        raise ValueError(
            "Too many arguments. Usage: mpy_launch_debugpy.py "
            "[target_module] [target_method] [port] [dap_stream]"
        )
    return target_module, target_method, port, dap_stream


def _run():
    import debugpy

    print(_banner)
    print("MicroPython VS Code Debugging")
    print("Usage: mpy_launch_debugpy.py [target_module] [target_method] [port] [dap_stream]")
    print("==================================")

    target_module, target_method, port, dap_stream = _parse_args()
    print(f"Target module: {target_module}")
    print(f"Target method: {target_method}")
    print("==================================")

    if not hasattr(sys, "settrace"):
        print(
            "sys.settrace is not available. You need a firmware compiled with debugging features."
        )
        return

    stream = _detect_dap_stream(dap_stream)
    if stream is not None:
        debugpy.listen_stream(stream)
        actual_host, actual_port = "serial", 0
    else:
        host = _detect_host()
        actual_host, actual_port = debugpy.listen(host=host, port=port)
    print(f"Debug server listening on {actual_host}:{actual_port}")

    caps = debugpy.get_capabilities()
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

    debugpy.debug_this_thread()

    # Imported only once a client is configured: the module's top-level code
    # runs on import, and it should run under the debugger with the client's
    # breakpoints already in place, not before the session exists.
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

    print("Target completed successfully!")
    if result is None:
        print("No result returned from target method")
    else:
        print("Result type:", type(result))
        print("Result:", result)


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
# fmt: on
