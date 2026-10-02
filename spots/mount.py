"""Celestron NexStar mounts, driven through the hand controller's USB port.

Speaks Celestron's NexStar serial protocol (9600 baud, 8N1). The hand
controller's USB port is a Prolific PL2303 serial adapter. Motors are moved
with the pass-through fixed-rate slew commands, at the hand controller's
speeds 1-9; speed 0 stops an axis.

A move only lasts while the page keeps asking for it. Each request carries
a deadline, and a watchdog stops any axis whose deadline passes, so a
dropped WiFi link can't leave the mount slewing.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

BAUD = 9600
# Celestron allows up to 3.5 s for a reply in the worst case.
REPLY_TIMEOUT_S = 3.5
PROLIFIC_VID = 0x067B

# Motor controller device ids, and the fixed-rate slew directions.
AZM, ALT = 16, 17
POSITIVE, NEGATIVE = 36, 37
TRACKING_OFF = 0

AXIS_NAMES = {AZM: "azimuth", ALT: "altitude"}

MODELS = {
    1: "NexStar GPS", 3: "NexStar i-Series", 4: "NexStar i-Series SE", 5: "CGE",
    6: "Advanced GT", 7: "NexStar SLT", 9: "CPC", 10: "NexStar GT",
    11: "NexStar 4/5 SE", 12: "NexStar 6/8 SE",
}

# The hand controller's nine speeds on the SE mounts, from Celestron's manual.
SPEEDS = {
    1: "0.5× sidereal", 2: "1× sidereal", 3: "4× sidereal", 4: "8× sidereal",
    5: "16× sidereal", 6: "64× sidereal", 7: "1°/s", 8: "2°/s", 9: "4°/s",
}

# On-screen direction -> axis and slew direction, before any reversal.
DIRECTIONS = {
    "up": (ALT, POSITIVE), "down": (ALT, NEGATIVE),
    "right": (AZM, POSITIVE), "left": (AZM, NEGATIVE),
}


class MountError(RuntimeError):
    """The hand controller couldn't be reached, or refused a command."""


class MountNotConnected(MountError):
    """A move was asked for with no mount connected."""


def slew_command(axis: int, direction: int, speed: int) -> bytes:
    """Celestron's fixed-rate pass-through slew. Speed 0 stops the axis."""
    return bytes([ord("P"), 2, axis, direction, speed, 0, 0, 0])


def _serial_module():
    try:
        import serial
        import serial.tools.list_ports  # noqa: F401  (loads the submodule)
    except ImportError as exc:
        raise MountError("The pyserial package isn't installed -- run "
                         "'pip install -r requirements.txt'") from exc
    return serial


def candidate_ports(serial_module=None) -> list[str]:
    """USB serial ports, Prolific adapters first. Built-in UARTs are left
    out, since they may carry a console or another device."""
    serial = serial_module or _serial_module()
    ports = [p for p in serial.tools.list_ports.comports() if p.vid is not None]
    ports.sort(key=lambda p: p.vid != PROLIFIC_VID)
    return [p.device for p in ports]


class NexStar:
    """One open hand controller. Commands are serialised, since the
    protocol is strictly one reply per command."""

    def __init__(self, link, port: str = ""):
        self._link = link
        self.port = port
        self._lock = threading.Lock()

    @classmethod
    def open(cls, port: str, serial_module=None) -> "NexStar":
        serial = serial_module or _serial_module()
        try:
            link = serial.Serial(port, BAUD, bytesize=8, parity="N", stopbits=1,
                                 timeout=REPLY_TIMEOUT_S, write_timeout=REPLY_TIMEOUT_S)
        except Exception as exc:
            hint = (" -- add this user to the 'dialout' group"
                    if "ermission" in str(exc) else "")
            raise MountError(f"Could not open {port} ({exc}){hint}") from exc
        return cls(link, port)

    def command(self, payload: bytes) -> bytes:
        """Sends one command and returns its reply without the closing '#'."""
        with self._lock:
            try:
                # Anything left over belongs to an earlier, timed-out command.
                self._link.reset_input_buffer()
                self._link.write(payload)
                reply = self._link.read_until(b"#")
            except Exception as exc:
                raise MountError(f"Lost the hand controller ({exc})") from exc
        if not reply.endswith(b"#"):
            raise MountError("No reply from the hand controller")
        return reply[:-1]

    def echo(self) -> bool:
        try:
            return self.command(b"Kx") == b"x"
        except MountError:
            return False

    def version(self) -> str:
        reply = self.command(b"V")
        return f"{reply[0]}.{reply[1]}" if len(reply) == 2 else ""

    def model(self) -> str:
        reply = self.command(b"m")
        if len(reply) != 1:
            return ""
        return MODELS.get(reply[0], f"model {reply[0]}")

    def slew(self, axis: int, direction: int, speed: int) -> None:
        reply = self.command(slew_command(axis, direction, speed))
        # A pass-through command that got no answer from its device comes
        # back with an extra byte before the '#'.
        if reply:
            raise MountError(f"The {AXIS_NAMES[axis]} motor didn't answer -- is the "
                             "hand controller plugged into the mount?")

    def stop(self, axis: int) -> None:
        self.slew(axis, POSITIVE, 0)

    def set_tracking(self, mode: int) -> None:
        if self.command(b"T" + bytes([mode])):
            raise MountError("The hand controller refused to change tracking")

    def close(self) -> None:
        try:
            self._link.close()
        except Exception:
            pass


class MountController:
    """The connected mount, the moves in progress and the watchdog that ends
    them. Safe to call from the web server's threads."""

    # A move stops this long after the page last asked for it.
    HOLD_S = 1.0
    _TICK_S = 0.1

    def __init__(self, settings, on_motion: Callable[[bool], None] | None = None,
                 opener: Callable[[str], NexStar] | None = None,
                 ports: Callable[[], list[str]] | None = None,
                 clock: Callable[[], float] = time.monotonic, watchdog: bool = True):
        self._settings = settings
        self._on_motion = on_motion or (lambda moving: None)
        self._opener = opener or NexStar.open
        self._ports = ports or candidate_ports
        self._clock = clock
        self._use_watchdog = watchdog

        self._lock = threading.RLock()
        self._mount: NexStar | None = None
        self._model = ""
        self._version = ""
        self._error = ""
        self._connecting = False
        # axis -> (screen direction, slew direction, speed, deadline)
        self._moves: dict[int, tuple[str, int, int, float]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- connection ---------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._mount is not None

    def connect(self) -> dict:
        """Finds and opens the hand controller, stops both motors and turns
        tracking off. Raises MountError saying what went wrong."""
        with self._lock:
            self._connecting = True
            try:
                self._close_link()
                self._mount = self._find()
                self._model, self._version = self._identify(self._mount)
                for axis in (AZM, ALT):
                    self._mount.stop(axis)
                # Tracking follows the sky, which walks the view off a target
                # on the ground.
                self._mount.set_tracking(TRACKING_OFF)
                self._error = ""
                logger.info("Mount connected on %s: %s %s", self._mount.port,
                            self._model, self._version)
            except MountError as exc:
                self._close_link()
                self._error = str(exc)
                raise
            finally:
                self._connecting = False
        if self._use_watchdog:
            self._start_watchdog()
        return self.status()

    def connect_in_background(self) -> None:
        """Connects without holding up the caller; failures land in status()."""
        def run():
            try:
                self.connect()
            except MountError as exc:
                logger.warning("Mount not connected: %s", exc)
        self._connecting = True
        threading.Thread(target=run, daemon=True).start()

    def _find(self) -> NexStar:
        configured = (self._settings.mount.port or "").strip()
        ports = [configured] if configured else self._ports()
        if not ports:
            raise MountError("No USB serial device found -- plug the hand controller's "
                             "USB port into the Pi, with the mount switched on")
        problems = []
        for port in ports:
            try:
                mount = self._opener(port)
            except MountError as exc:
                problems.append(str(exc))
                continue
            if mount.echo():
                return mount
            mount.close()
            problems.append(f"{port} didn't answer")
        raise MountError("No NexStar hand controller answered (" + "; ".join(problems)
                         + "). Check the mount is switched on and past its start-up screens.")

    @staticmethod
    def _identify(mount: NexStar) -> tuple[str, str]:
        # Only for show, and older hand controllers lack the model query.
        try:
            model = mount.model()
        except MountError:
            model = ""
        try:
            version = mount.version()
        except MountError:
            version = ""
        return model, version

    def apply_settings(self) -> None:
        """Connects or disconnects to match a just-saved Mount setting."""
        enabled = self._settings.mount.enabled
        if enabled and not self.connected and not self._connecting:
            self.connect_in_background()
        elif not enabled and self.connected:
            self.close()

    # ---- moving -------------------------------------------------------

    def _axis_for(self, direction: str) -> tuple[int, int]:
        axis, sign = DIRECTIONS[direction]
        mount = self._settings.mount
        reverse = mount.reverse_up_down if axis == ALT else mount.reverse_left_right
        if reverse:
            sign = NEGATIVE if sign == POSITIVE else POSITIVE
        return axis, sign

    def hold(self, direction: str, speed: int) -> dict:
        """Starts or continues a move. Must be repeated within HOLD_S."""
        if direction not in DIRECTIONS:
            raise ValueError(f"Unknown direction {direction!r}")
        if not 1 <= int(speed) <= 9:
            raise ValueError("Speed must be 1 to 9")
        speed = int(speed)
        with self._lock:
            if self._mount is None:
                raise MountNotConnected("The mount isn't connected")
            axis, sign = self._axis_for(direction)
            deadline = self._clock() + self.HOLD_S
            current = self._moves.get(axis)
            if current is None or current[1:3] != (sign, speed):
                self._send(lambda m: m.slew(axis, sign, speed))
            was_still = not self._moves
            self._moves[axis] = (direction, sign, speed, deadline)
            if was_still:
                self._on_motion(True)
        return self.status()

    def release(self, direction: str) -> dict:
        """Stops the axis `direction` moves, if it is moving that way."""
        if direction not in DIRECTIONS:
            raise ValueError(f"Unknown direction {direction!r}")
        with self._lock:
            axis, _ = DIRECTIONS[direction]
            current = self._moves.get(axis)
            if current is not None and current[0] == direction:
                self._halt(axis)
        return self.status()

    def stop_all(self) -> dict:
        """Stops both motors, whatever this side believes they are doing."""
        with self._lock:
            for axis in (AZM, ALT):
                if self._mount is None:
                    break
                self._halt(axis)
        return self.status()

    def _halt(self, axis: int) -> None:
        moving = bool(self._moves)
        self._moves.pop(axis, None)
        if moving and not self._moves:
            self._on_motion(False)
        self._send(lambda m: m.stop(axis))

    def _send(self, action) -> None:
        """Runs a command, dropping the connection if the link has failed."""
        try:
            action(self._mount)
        except MountError as exc:
            self._drop(str(exc))
            raise

    def _drop(self, error: str) -> None:
        moving = bool(self._moves)
        self._moves.clear()
        self._close_link()
        self._error = error
        logger.warning("Mount disconnected: %s", error)
        if moving:
            self._on_motion(False)

    # ---- the watchdog -------------------------------------------------

    def tick(self) -> None:
        """Stops any axis the page has stopped asking for."""
        with self._lock:
            now = self._clock()
            for axis, (direction, _, _, deadline) in list(self._moves.items()):
                if now >= deadline and self._mount is not None:
                    logger.warning("No word from the page for %.1f s, stopping the %s motor",
                                   self.HOLD_S, AXIS_NAMES[axis])
                    try:
                        self._halt(axis)
                    except MountError:
                        pass

    def _start_watchdog(self) -> None:
        if self._thread is not None and self._thread.is_alive() and not self._stop.is_set():
            return
        if self._thread is not None:
            # One told to stop by close() may not have noticed yet.
            self._thread.join(timeout=1.0)
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self) -> None:
        while not self._stop.wait(self._TICK_S):
            self.tick()

    # ---- reporting and shutdown ----------------------------------------

    def status(self) -> dict:
        with self._lock:
            moving = {AXIS_NAMES[axis]: move[0] for axis, move in self._moves.items()}
            return {
                "enabled": bool(self._settings.mount.enabled),
                "connected": self._mount is not None,
                "connecting": self._connecting,
                "port": self._mount.port if self._mount is not None else "",
                "model": self._model if self._mount is not None else "",
                "version": self._version if self._mount is not None else "",
                "error": self._error,
                "moving": moving,
                "speeds": SPEEDS,
                "hold_s": self.HOLD_S,
            }

    def _close_link(self) -> None:
        mount, self._mount = self._mount, None
        if mount is not None:
            mount.close()

    def close(self) -> None:
        """Stops both motors and lets go of the hand controller."""
        self._stop.set()
        with self._lock:
            if self._mount is not None:
                for axis in (AZM, ALT):
                    try:
                        self._mount.stop(axis)
                    except MountError:
                        break
            moving = bool(self._moves)
            self._moves.clear()
            self._close_link()
            if moving:
                self._on_motion(False)
