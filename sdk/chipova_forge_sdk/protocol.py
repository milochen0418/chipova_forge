"""The device protocol (``device.yaml``), as Chipova Forge reads it (a copy of
chipova_forge/gateway/device.py, so the SDK needs nothing of the app): how apps talk to the
product, the same on the virtual
board and on a real one.

A command is one line out and one line back over the device's console (the UART the board's
system.yaml names): ``send`` is the line, with ``{arg}`` replaced by the argument's value times
its ``scale`` as a whole number; ``reply`` is the first word of the answer, whose ``key=value``
pairs are the result (each read by its field: ``scale``, ``type`` number|int|bool|text,
``as`` another name). A line starting with ``ERR`` is a refusal. Lines the device sends by
itself are events, recognised by their first word (``match``).

This module only turns commands into lines and lines into results: the gateway sends them to a
session, the SDK's serial transport to a real port (the same file).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_ARG = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_KV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class DeviceError(ValueError):
    pass


@dataclass
class Arg:
    name: str
    type: str = "number"  # number | int | bool
    min: float | None = None
    max: float | None = None
    scale: float = 1.0
    title: str = ""


@dataclass
class FieldSpec:
    key: str
    type: str = "number"
    scale: float = 1.0
    unit: str = ""
    name: str = ""  # the name in the result ("as"), else the key


@dataclass
class Command:
    name: str
    send: str
    reply: str
    title: str = ""
    args: list[Arg] = field(default_factory=list)
    fields: list[FieldSpec] = field(default_factory=list)


@dataclass
class EventSpec:
    name: str
    match: str
    title: str = ""
    fields: list[FieldSpec] = field(default_factory=list)


@dataclass
class Profile:
    name: str
    title: str
    uart: str
    commands: dict[str, Command]
    events: dict[str, EventSpec]
    dashboard: dict[str, Any]

    def describe(self) -> dict[str, Any]:
        """What an app or an AI needs to know to use the device."""
        return {
            "name": self.name,
            "title": self.title,
            "commands": {
                n: {"title": c.title, "args": {a.name: {"type": a.type, "min": a.min, "max": a.max} for a in c.args},
                    "returns": {f.name or f.key: {"type": f.type, "unit": f.unit} for f in c.fields}}
                for n, c in self.commands.items()
            },
            "events": {n: {"title": e.title, "fields": {f.name or f.key: {"type": f.type, "unit": f.unit} for f in e.fields}}
                       for n, e in self.events.items()},
        }


def _fields(spec: Any) -> list[FieldSpec]:
    out = []
    for key, f in (spec or {}).items():
        f = f or {}
        out.append(FieldSpec(key=str(key), type=str(f.get("type", "number")), scale=float(f.get("scale", 1.0)),
                             unit=str(f.get("unit", "")), name=str(f.get("as", "")) or str(key)))
    return out


def load(text: str) -> Profile:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise DeviceError(f"device.yaml doesn't read: {e}") from e
    if not isinstance(data, dict):
        raise DeviceError("device.yaml isn't a mapping")
    commands = {}
    for name, c in (data.get("commands") or {}).items():
        if not _NAME.match(str(name)) or not isinstance(c, dict):
            raise DeviceError(f"command {name!r}: names are lowercase letters, digits and _")
        send = str(c.get("send", ""))
        if not send or "\n" in send or '"' in send or "\\" in send:
            raise DeviceError(f"command {name}: send is one line without quotes or backslashes")
        args = []
        for an, a in (c.get("args") or {}).items():
            a = a or {}
            args.append(Arg(name=str(an), type=str(a.get("type", "number")), min=a.get("min"), max=a.get("max"),
                            scale=float(a.get("scale", 1.0)), title=str(a.get("title", ""))))
        unknown = set(_ARG.findall(send)) - {a.name for a in args}
        if unknown:
            raise DeviceError(f"command {name}: send uses {', '.join(sorted(unknown))}, which aren't arguments")
        commands[str(name)] = Command(name=str(name), send=send, reply=str(c.get("reply", "OK")), title=str(c.get("title", "")),
                                      args=args, fields=_fields(c.get("fields")))
    events = {}
    for name, e in (data.get("events") or {}).items():
        e = e or {}
        events[str(name)] = EventSpec(name=str(name), match=str(e.get("match", str(name).upper())), title=str(e.get("title", "")),
                                      fields=_fields(e.get("fields")))
    return Profile(name=str(data.get("name", "device")), title=str(data.get("title", "")), uart=str(data.get("uart", "console")),
                   commands=commands, events=events, dashboard=dict(data.get("dashboard") or {}))


def encode(p: Profile, command: str, args: dict[str, Any] | None = None) -> str:
    """The line for a command, its arguments checked."""
    c = p.commands.get(command)
    if c is None:
        raise DeviceError(f"the device has no command {command!r} (it has: {', '.join(p.commands)})")
    args = dict(args or {})
    values: dict[str, str] = {}
    for a in c.args:
        if a.name not in args:
            raise DeviceError(f"{command} needs {a.name}")
        v = args.pop(a.name)
        if a.type == "bool":
            if isinstance(v, str):
                v = v.strip().lower() in ("1", "true", "on", "yes")
            values[a.name] = "1" if v else "0"
            continue
        try:
            num = float(v)
        except (TypeError, ValueError) as e:
            raise DeviceError(f"{a.name} is a number") from e
        if not math.isfinite(num):
            raise DeviceError(f"{a.name} is a number")
        if a.min is not None and num < float(a.min) or a.max is not None and num > float(a.max):
            raise DeviceError(f"{a.name} is {a.min} .. {a.max}")
        if a.type == "int" and num != int(num):
            raise DeviceError(f"{a.name} is a whole number")
        values[a.name] = str(int(round(num * a.scale)))
    if args:
        raise DeviceError(f"{command} takes no {', '.join(sorted(args))}")
    return _ARG.sub(lambda m: values[m.group(1)], c.send)


def _value(f: FieldSpec, raw: str) -> Any:
    if f.type == "text":
        return raw
    if f.type == "bool":
        return raw.lower() in ("1", "true", "on", "yes")
    try:
        num = int(raw, 0) if f.type == "int" else float(raw)
    except ValueError:
        return raw
    if f.type == "int":
        return num
    v = num * f.scale
    return round(v, 6) if f.scale != 1.0 else v


def parse_fields(specs: list[FieldSpec], line: str) -> dict[str, Any]:
    pairs = dict(_KV.findall(line))
    out: dict[str, Any] = {}
    known = {f.key: f for f in specs}
    for k, raw in pairs.items():
        f = known.get(k)
        if f is None:
            try:
                out[k] = int(raw, 0)
            except ValueError:
                out[k] = raw
            continue
        out[f.name or f.key] = _value(f, raw)
    return out


def reply_of(p: Profile, command: str, line: str) -> tuple[str, dict[str, Any] | None]:
    """How a line answers a command: ("ok", result), ("error", {"message"}), or ("other", None)."""
    c = p.commands[command]
    word = line.split(" ", 1)[0] if line else ""
    if word == "ERR" or word == "ERROR":
        return "error", {"message": line[len(word):].strip()}
    if word == c.reply:
        return "ok", parse_fields(c.fields, line)
    return "other", None


def event_of(p: Profile, line: str) -> tuple[str, dict[str, Any]] | None:
    """The event a line the device sent by itself is, if any."""
    word = line.split(" ", 1)[0] if line else ""
    for name, e in p.events.items():
        if word == e.match:
            return name, parse_fields(e.fields, line)
    return None
