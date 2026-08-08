"""Project-level named debug targets (`mpdebug.toml`) for `mpremote debug`.

Maps a short name to a transport kind, connect string, and firmware/program
defaults, replacing hardcoded IPs and per-transport invocations with one file
discovered like `.git` - nearest `mpdebug.toml` from the current directory
upward:

    [target.pico]
    kind = "serial"
    device = "/dev/serial/by-id/usb-MicroPython_..."
    requires = ["settrace", "save_names"]

`kind` is one of "unix", "serial", "network". `device` is a connect string as
accepted by `mpremote connect` (required for "serial"; optional for
"network", where it names the control-plane device used for the pre-IP
handshake - the debug endpoint itself is never written here, the device
reports its own). `firmware` names a firmware.toml variant id (or "system"
for whatever binary is already on PATH/flashed); satisfying it is the
caller's job, not this module's. `program` is a "module[:method]" default.
`requires` lists capability names, checked here against the fixed vocabulary
the MPDBG-READY handshake can report, so a typo is caught before any device
is touched; matching them against what a specific device actually probes
happens later, once `do_debug` has a handshake to check it against.

`source` names a host directory that `do_debug` mounts at the device's
remote-fs mount point before running the debugged program, so the program
executes from a live view of the host filesystem rather than whatever copy
already sits on the device. A relative path is resolved against the
directory holding this `mpdebug.toml`; the value stored on `Target` is
always an absolute, symlink-resolved path, checked for existence when
`do_debug` actually uses it rather than here, so one target's stale source
doesn't break every other target in the file. Its absence means the module
already lives on the device's own filesystem - the hardware-in-the-loop
targets that run `/flash/target.py` want exactly this - so nothing is
mounted and `do_debug` behaves as it did before this key existed. Valid only
on a "serial" or "network" target: a "unix" target already runs the program
straight from the host filesystem, so there is nothing to mount. A
`--source PATH` command-line option overrides this key without needing an
`mpdebug.toml` entry at all.

Unknown keys in a target table are ignored, not rejected, so a front-end can
store its own metadata (icons, ordering, ...) alongside these.

A bare name that isn't a target in the file, but looks like a connect string
(a path, an ``id:``/``port:`` prefix, or one of ``auto``/``list``/``unix``),
falls back to the pre-existing literal handling instead of erroring - so a
project with an `mpdebug.toml` doesn't break `mpremote debug /dev/ttyACM0`.
"""

import os
import sys

CONFIG_FILENAME = "mpdebug.toml"


def _command_error(msg):
    # Imported lazily: commands.py imports this module at load time, so a
    # top-level "from .commands import CommandError" here would be circular.
    from .commands import CommandError

    raise CommandError(msg)


# The caps keys the MPDBG-READY handshake reports (see debugpy's
# probe_capabilities()); keep in step with the probe. A target's `requires`
# is checked against this tuple.
KNOWN_CAPABILITIES = ("settrace", "save_names", "set_local", "f_back")

_KINDS = ("unix", "serial", "network")


# Names that resolve_target falls back to literal connect-string handling
# for, when they aren't a target name in the file: mpremote's own
# "auto"/"list" device strings, "unix", the "a0"/"u0"/"c0"-style port
# shorthands, and the "id:"/"port:" connect-string prefixes.
class Target:
    def __init__(
        self,
        name,
        kind,
        device=None,
        firmware=None,
        program=None,
        requires=(),
        source=None,
    ):
        self.name = name
        self.kind = kind
        self.device = device
        self.firmware = firmware
        self.program = program
        self.requires = requires
        # Absolute, symlink-resolved host directory `do_debug` mounts at the
        # device's remote-fs mount point before running the program. None
        # means the program's module already lives on the device's own
        # filesystem, so `do_debug` mounts nothing.
        self.source = source


def find_config(start_dir=None):
    """Search start_dir (default cwd) upward for mpdebug.toml; None if absent.

    Stops above the home directory, so a config outside $HOME is never
    picked up, and at a directory holding `.git` (a project root) - as
    either a directory or a `gitdir:` file, so worktrees and submodule
    checkouts are recognised too.
    """
    d = os.path.abspath(start_dir or os.getcwd())
    home = os.path.abspath(os.path.expanduser("~"))
    while True:
        candidate = os.path.join(d, CONFIG_FILENAME)
        if os.path.isfile(candidate):
            return candidate
        if os.path.exists(os.path.join(d, ".git")) or d == home:
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _toml_module():
    # mpremote declares no toml dependency and a pre-3.11 floor; try the
    # stdlib module first, fall back to the third-party backport, and only
    # fail once neither is importable.
    try:
        import tomllib

        return tomllib
    except ImportError:
        pass
    try:
        import tomli

        return tomli
    except ImportError:
        _command_error(
            f"reading {CONFIG_FILENAME} needs a TOML parser: Python >= 3.11 "
            "(stdlib tomllib) or 'pip install tomli'"
        )


def _load_targets(path):
    toml = _toml_module()
    try:
        with open(path, "rb") as f:
            data = toml.load(f)
    except OSError as er:
        _command_error(f"{path}: {er}")
    except Exception as er:
        _command_error(f"{path}: invalid TOML: {er}")

    raw = data.get("target", {})
    if not isinstance(raw, dict):
        _command_error(f"{path}: 'target' must be a table of [target.<name>] entries")

    targets = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            _command_error(f"{path}: target '{name}' must be a table")

        kind = spec.get("kind")
        if kind not in _KINDS:
            if kind is None and any(isinstance(v, dict) for v in spec.values()):
                _command_error(
                    f"{path}: target '{name}' has a nested table (e.g. "
                    f"[target.{name}.<sub>]); a target entry must be a flat table"
                )
            _command_error(
                f"{path}: target '{name}' has kind {kind!r}, expected one of {', '.join(_KINDS)}"
            )

        requires = spec.get("requires", [])
        if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
            _command_error(f"{path}: target '{name}' requires must be a list of capability names")
        unknown = [r for r in requires if r not in KNOWN_CAPABILITIES]
        if unknown:
            _command_error(
                f"{path}: target '{name}' requires unknown capability "
                f"{', '.join(repr(u) for u in unknown)}; the probe only reports "
                f"{', '.join(KNOWN_CAPABILITIES)}"
            )

            value = spec.get(key)
            if value is not None and not isinstance(value, str):
                _command_error(f"{path}: target '{name}' {key} must be a string")

        device = spec.get("device")
        if kind == "serial" and not device:
            _command_error(f"{path}: target '{name}' is kind 'serial' but has no 'device'")
        if device == "":
            # Distinct from an absent device, which means "let mpremote choose".
            _command_error(f"{path}: target '{name}' has an empty 'device'")


        source = spec.get("source")
        if source == "":
            _command_error(f"{path}: target '{name}' has an empty 'source'")
        if source is not None:
            if kind == "unix":
                _command_error(
                    f"{path}: target '{name}' is kind 'unix' and has a 'source'; a "
                    "unix target already runs the program straight from the host "
                    "filesystem, there is nothing to mount"
                )
            if not os.path.isabs(source):
                source = os.path.join(os.path.dirname(path), source)
            source = os.path.realpath(source)
            # Existence is checked at the point of use (do_debug), not here:
            # an isdir check at load time would fail every target in the
            # file the moment any one of them names a source root that
            # doesn't (yet) exist, not just the one actually being run.

        targets[name] = Target(
            name=name,
            kind=kind,
            device=device,
            firmware=spec.get("firmware"),
            program=spec.get("program"),
            requires=requires,
            source=source,
        )
    return targets


def warn_if_tty_device(device, label):
    """Warn on stderr if `device` is a /dev/tty* path (renumbers on replug).

    Called by `do_debug` against the connect string it actually ends up
    using, whether that came from a resolved target or a literal argument,
    so the warning fires the same way regardless of where the string came
    from.
    """
    if isinstance(device, str) and device.startswith("/dev/tty"):
        print(
            f"warning: {label} uses {device!r}; /dev/tty* nodes can "
            "renumber on replug, prefer a /dev/serial/by-id/... path",
            file=sys.stderr,
        )


def resolve_target(name, start_dir=None):
    """Resolve `name` to a Target, or None if `name` isn't one (try it as a literal).

    `name=None` picks the sole target when the file defines exactly one.
    Any `name` that is not a configured target is handled as a connect
    string, which is what `do_connect` accepts - there is no attempt to tell
    the two apart by shape, since a connect string can be a path, an
    `id:`/`port:` selector, a shortcut, or a bare device name like `COM4`.
    A typo'd target name therefore reaches the transport; `target_hint()`
    supplies the "did you mean" text for that case.
    """
    path = find_config(start_dir)
    if path is None:
        if name is None:
            _command_error(
                f"no target given and no {CONFIG_FILENAME} found; pass a connect string "
                "or add a [target.<name>] table"
            )
        return None

    targets = _load_targets(path)
    if not targets:
        _command_error(f"{path} defines no [target.<name>] entries")

    if name is None:
        if len(targets) == 1:
            return next(iter(targets.values()))
        _command_error(
            "no target given and {} defines several; choose one of: {}".format(
                path, ", ".join(sorted(targets))
            )
        )

    if name not in targets:
        return None
    return targets[name]


def target_hint(name, start_dir=None):
    """Text naming the configured targets, for when `name` failed as a device.

    Empty when there is no config or `name` is one of its targets, so a
    caller can append this to a transport error unconditionally.
    """
    path = find_config(start_dir)
    if path is None:
        return ""
    try:
        targets = _load_targets(path)
    except Exception:
        return ""
    if not targets or name in targets:
        return ""
    return " ({} defines targets: {})".format(path, ", ".join(sorted(targets)))
