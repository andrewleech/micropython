# fmt: off
# This file is vendored byte-for-byte between repos with different ruff
# line-length settings; `ruff format` must leave it alone everywhere so a
# routine reformat in either repo can't silently break the copies apart.
"""Single parameterised boot script for MicroPython debugpy sessions.

Usage: mpy_launch_debugpy.py [target_module] [target_method] [port] [dap_device]

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

On a board `_detect_dap_stream()` finds a dedicated DAP CDC interface on, or
when `dap_device` names a path this runtime can open directly, the session
runs over that stream instead of TCP (`host`/`port` in the handshake become
`"serial"`/`0`, and the `port` argument is unused). `caps["serial_dap"]`
reports which of the two actually happened, not a board guess.
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


def _detect_dap_stream(device=None):
    """Return an open reader/writer stream for a dedicated DAP CDC interface, if any.

    `device`, when given, is a device path this runtime can open directly.
    Only the unix port has one to give: a real board's DAP interface is a
    runtime object (e.g. rp2's second `machine.USBDevice` CDC), not a path
    the host can name, so board-specific second-CDC detection still has no
    implementation here and `device` stays unused there. With no `device`,
    this always returns `None` and `_run()` falls back to
    `debugpy.listen()` over TCP. `caps["serial_dap"]` is derived from which
    channel `_run()` actually picked (see `debugpy.get_capabilities()`),
    never guessed here, so the two can't disagree.
    """
    if device is not None:
        try:
            return open(device, "r+b")
        except OSError as er:
            raise OSError(f"dap_device {device!r} could not be opened: {er}")
    return None


def _parse_args():
    import debugpy

    args = sys.argv[1:]
    target_module = args[0] if len(args) > 0 else "target"
    target_method = args[1] if len(args) > 1 else "main"
    port = int(args[2]) if len(args) > 2 else debugpy.DEFAULT_PORT
    dap_device = args[3] if len(args) > 3 else None
    if len(args) > 4:
        raise ValueError(
            "Too many arguments. Usage: mpy_launch_debugpy.py "
            "[target_module] [target_method] [port] [dap_device]"
        )
    return target_module, target_method, port, dap_device


def _run():
    import debugpy


    print(_banner)
    print("MicroPython VS Code Debugging")
    print("Usage: mpy_launch_debugpy.py [target_module] [target_method] [port] [dap_device]")
    print("==================================")

    target_module, target_method, port, dap_device = _parse_args()
    print(f"Target module: {target_module}")
    print(f"Target method: {target_method}")
    print("==================================")

    if not hasattr(sys, "settrace"):
        print(
            "sys.settrace is not available. You need a firmware compiled with debugging features."
        )
        return

    stream = _detect_dap_stream(dap_device)
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
