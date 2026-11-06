"""Chipova Forge's Python SDK: talk to a product's device, simulated or real, with the same calls.

    from chipova_forge_sdk import Device

    # A device running in a Forge simulation (its id is the simulation's id):
    device = Device("ses-36fcf268bd", url="https://chipova-forge.reflex-ddns.com", token="cfg1....")
    device.set_threshold(celsius=35)
    status = device.get_status()
    print(status.temperature, status.alarm)
    device.environment("temperature", celsius=40)      # the virtual board only
    for event in device.events():                       # ALARM, NORMAL, ... as they happen
        print(event.name, event.fields)

    # The same product on a real board, over its serial port (pyserial), from its device.yaml:
    real = Device.serial("/dev/cu.usbmodem1101", profile="device.yaml")
    real.set_threshold(celsius=35)

Commands are the ones in the product's device.yaml, as methods (keyword arguments are the
command's arguments); a command's answer comes back as a ``Result`` whose attributes are its
fields, in their units (°C, not hundredths).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from chipova_forge_sdk import protocol

__all__ = ["Device", "Result", "Event", "DeviceError"]
__version__ = "0.1.0"


class DeviceError(RuntimeError):
    """The device refused a command, or couldn't be reached."""


class Result(dict):
    """A command's answer: a dict whose keys are also attributes (status.temperature)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


@dataclass
class Event:
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    line: str = ""
    vt: float | None = None
    seq: int = 0


class _Http:
    def __init__(self, url: str, token: str, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", "")
            except ValueError:
                msg = ""
            raise DeviceError(msg or f"HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise DeviceError(f"cannot reach {self.url}: {e.reason}") from e

    def stream(self, path: str) -> Iterator[dict]:
        req = urllib.request.Request(self.url + path, headers={"Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=None) as r:
            data = ""
            for raw in r:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if line.startswith("data: "):
                    data += line[6:]
                elif not line and data:
                    yield json.loads(data)
                    data = ""


class Device:
    """A product's device. ``Device(id, url=, token=)`` is one in a Forge simulation;
    ``Device.serial(port, profile=)`` a real one."""

    def __init__(self, device_id: str = "", url: str = "", token: str = "", *, backend: str = "virtual",
                 profile: protocol.Profile | None = None, transport: Any = None) -> None:
        self.backend = backend
        self.device_id = device_id
        self._http = _Http(url, token) if backend == "virtual" else None
        self._transport = transport
        if profile is None and self._http is not None:
            info = self._http.call("GET", f"/api/simulations/{device_id}/devices")
            dev = (info.get("devices") or [{}])[0]
            self._describe = dev.get("profile", {})
            self.profile = None
        else:
            self.profile = profile
            self._describe = profile.describe() if profile else {}

    # ---------------------------------------------------------------- real boards

    @classmethod
    def serial(cls, port: str, profile: str | Path = "device.yaml", baud: int = 115200, timeout: float = 2.0) -> "Device":
        """The product on a real board, over its serial port (needs pyserial)."""
        try:
            import serial  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as e:
            raise DeviceError("a real board needs pyserial: pip install pyserial") from e
        prof = protocol.load(Path(profile).read_text())
        return cls(backend="serial", profile=prof, transport=_SerialLines(serial.Serial(port, baud, timeout=timeout)))

    # ---------------------------------------------------------------- commands

    @property
    def commands(self) -> list[str]:
        return list((self._describe.get("commands") or {}).keys())

    def command(self, name: str, **args: Any) -> Result:
        if self.backend == "virtual":
            assert self._http is not None
            out = self._http.call("POST", f"/api/devices/{self.device_id}/commands", {"command": name, "args": args})
            return Result(out.get("result") or {})
        assert self.profile is not None and self._transport is not None
        line = protocol.encode(self.profile, name, args)
        self._transport.write_line(line)
        end = time.time() + 5.0
        while time.time() < end:
            got = self._transport.read_line()
            if got is None:
                continue
            how, result = protocol.reply_of(self.profile, name, got)
            if how == "ok":
                return Result(result or {})
            if how == "error":
                raise DeviceError(f"the device refused {name}: {(result or {}).get('message', '')}")
        raise DeviceError(f"the device didn't answer {name}")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.commands:
            return lambda **args: self.command(name, **args)
        raise AttributeError(f"the device has no command {name!r} (it has: {', '.join(self.commands)})")

    # ---------------------------------------------------------------- the virtual board

    def environment(self, sensor: str, **settings: float) -> None:
        """Sets the virtual board's environment, e.g. environment("temperature", celsius=40), or the
        inputs of its analog side, e.g. environment("analog", load=0)."""
        if self.backend != "virtual":
            raise DeviceError("only the virtual board's environment can be set")
        assert self._http is not None
        for setting, value in settings.items():
            self._http.call("POST", f"/api/simulations/{self.device_id}/environment", {"sensor": sensor, "setting": setting, "value": value})

    def register(self, name: str) -> int:
        """Reads one of the chip's registers (the virtual board only: a probe on its interface)."""
        assert self._http is not None
        return int(self._http.call("GET", f"/api/simulations/{self.device_id}/registers/{name}")["value"])

    def state(self) -> dict[str, Any]:
        assert self._http is not None
        return self._http.call("GET", f"/api/devices/{self.device_id}/state")

    def events(self, after: int = 0) -> Iterator[Event]:
        """The device's events as they happen (the virtual board: server-sent events)."""
        if self.backend == "virtual":
            assert self._http is not None
            for e in self._http.stream(f"/api/simulations/{self.device_id}/events?after={after}"):
                if e.get("device_event"):
                    yield Event(e["device_event"], e.get("fields") or {}, e.get("line", ""), e.get("vt"), int(e.get("seq", 0)))
            return
        assert self.profile is not None and self._transport is not None
        while True:
            line = self._transport.read_line()
            if line is None:
                continue
            ev = protocol.event_of(self.profile, line)
            if ev is not None:
                yield Event(ev[0], ev[1], line)


class _SerialLines:
    def __init__(self, port: Any) -> None:
        self.port = port

    def write_line(self, line: str) -> None:
        self.port.write((line + "\r\n").encode())

    def read_line(self) -> str | None:
        raw = self.port.readline()
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace").strip() or None
