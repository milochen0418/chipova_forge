"""Sessions: a project's board running in the simulation (Renode, the chip's RTL in Verilator, the
firmware), shared by the people on the project, the device API, the SDK and AI agents.

Starting one builds what is missing (the chip library, the firmware; an identical earlier build
is reused), assembles the board (the platform's files, Forge's components, board.repl,
session.resc, session.json, the built files) and starts its container
(:mod:`chipova_forge.sessions.containers`). Its agent's events arrive over the channel and are
kept here (the latest few thousand), pushed to the project's page and to API followers, and,
when it stops, its whole event log, Renode's log and the chip's waveform are copied into
``<project>/sessions/<id>/`` as evidence.

States (blueprint 10): CREATED -> STARTING -> READY -> RUNNING, and PAUSED, FAILED, STOPPING,
STOPPED, EXPIRED. A session nobody touched for ``IDLE_MINUTES`` expires, as does one older than
``MAX_HOURS``. A restarted backend reconnects to the sessions whose containers still run.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from chipova_forge import ids, paths
from chipova_forge.db import db, loads
from chipova_forge.domain import roles
from chipova_forge.gateway import device as device_mod
from chipova_forge.jobs import builds, docker_runner
from chipova_forge.services import audit, projects
from chipova_forge.services.actors import SYSTEM, Actor
from chipova_forge.sessions import containers
from chipova_forge.system import board as board_mod
from chipova_forge.system import catalog

logger = logging.getLogger(__name__)

IMAGE = builds.SIM_IMAGE
MAX_PER_PROJECT = 3
MAX_TOTAL = 8
IDLE_MINUTES = 30
MAX_HOURS = 4
KEEP_EVENTS = 4000
ACTIVE = ("CREATED", "STARTING", "READY", "RUNNING", "PAUSED")

# Told (project id, session id) when a session's state or events change.
listeners: list[Callable[[str, str], None]] = []
# Told (project id, session id, the reply's seq, its values) after each answered device command.
reply_listeners: list[Callable[[str, str, int, dict[str, Any]], None]] = []


class SessionError(RuntimeError):
    pass


class Live:
    """What the app keeps of a running session."""

    def __init__(self, session_id: str, project_id: str) -> None:
        self.session_id = session_id
        self.project_id = project_id
        self.channel: containers.Channel | None = None
        self.events: collections.deque = collections.deque(maxlen=KEEP_EVENTS)
        # What the panels show (UART lines, bus transactions, LEDs), kept apart: the analog side's
        # many events would otherwise push them out of ``events``.
        self.panel: collections.deque = collections.deque(maxlen=1200)
        self.state = "CREATED"
        self.error = ""
        self.status: dict[str, Any] = {}
        self.leds: dict[str, bool] = {}
        self.sensors: dict[str, dict[str, Any]] = {}
        self.vt = 0.0
        self.last_seq = 0
        self.lock = threading.Lock()  # one device command at a time (their replies share the UART)
        self.cond = threading.Condition()
        self.subscribers: set[asyncio.Queue] = set()
        self.profile: device_mod.Profile | None = None
        self.profile_error = ""
        self.board: board_mod.Board | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        # The analog side (lock-step with the board): its last values and a few minutes of them.
        self.analog: dict[str, Any] = {}
        self.analog_history: collections.deque = collections.deque(maxlen=1500)


_live: dict[str, Live] = {}


def _notify(project_id: str, session_id: str) -> None:
    for fn in list(listeners):
        try:
            fn(project_id, session_id)
        except Exception:  # noqa: BLE001
            logger.exception("A session listener failed")


# ------------------------------------------------------------------ records


def get(session_id: str) -> dict[str, Any] | None:
    if not ids.is_prefixed(session_id, "ses"):
        return None
    row = db().one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    return {**dict(row), "summary": loads(row["summary"], {})} if row else None


def of_project(project_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db().query("SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))
    return [{**dict(r), "summary": loads(r["summary"], {})} for r in rows]


def active_of(project_id: str) -> dict[str, Any] | None:
    for s in of_project(project_id, 10):
        if s["status"] in ACTIVE:
            return s
    return None


def live(session_id: str) -> Live | None:
    return _live.get(session_id)


def _set(session_id: str, **fields: Any) -> None:
    db().update("sessions", {"id": session_id}, fields)


# ------------------------------------------------------------------ assembling the board


def assemble(project_id: str, chip_build: dict[str, Any], fw_build: dict[str, Any]) -> tuple[dict[str, bytes], board_mod.Board]:
    """The files of the session's /work/session."""
    c = projects.contract(project_id)
    b = board_mod.from_yaml(projects.read(project_id, "system.yaml"))
    regmap = builds.register_map(project_id)
    files: dict[str, bytes] = {}
    pdir = catalog.platform_dir(b.platform)
    for name in (catalog.platform(b.platform).get("renode") or {}).get("files", []):
        files[f"platform/{name}"] = (pdir / name).read_bytes()
    repl, sources = board_mod.board_repl(b, c)
    for src in sources:
        files[f"components/{src}"] = catalog.component_file(src).read_bytes()
    files["board.repl"] = repl.encode()
    files["session.resc"] = board_mod.session_resc(b, c, sources).encode()
    files["session.json"] = json.dumps(board_mod.session_json(b, c, regmap), indent=1).encode()
    files["libVchip.so"] = builds.artifact_path(chip_build, "libVchip.so").read_bytes()
    files["firmware.elf"] = builds.artifact_path(fw_build, "firmware.elf").read_bytes()
    return files, b


# ------------------------------------------------------------------ starting and stopping


async def start(project_id: str, actor: Actor, via: str = "ui") -> dict[str, Any]:
    projects.require(project_id, actor, roles.SIMULATE)
    if active_of(project_id) is not None and sum(1 for s in of_project(project_id, 20) if s["status"] in ACTIVE) >= MAX_PER_PROJECT:
        raise SessionError(f"the project already has {MAX_PER_PROJECT} sessions running: stop one first")
    if sum(1 for lv in _live.values() if lv.state in ACTIVE) >= MAX_TOTAL:
        raise SessionError(f"{MAX_TOTAL} sessions run on this computer already: try again later")
    # The registers' check too (in-process, a fraction of a second; a mismatch doesn't stop the
    # simulation, it shows on the Registers page and in the release checks).
    try:
        await builds.request(project_id, "registers", actor, via, wait=True)
    except builds.BuildError:
        pass
    chip_build = await builds.request(project_id, "chip", actor, via, wait=True)
    if chip_build["status"] != builds.SUCCEEDED:
        raise SessionError(f"the chip didn't build: {chip_build.get('error') or 'see its log'}")
    fw_build = await builds.request(project_id, "firmware", actor, via, wait=True)
    if fw_build["status"] != builds.SUCCEEDED:
        raise SessionError(f"the firmware didn't build: {fw_build.get('error') or 'see its log'}")
    files, board = await asyncio.to_thread(assemble, project_id, chip_build, fw_build)
    sid = ids.new_id("ses")
    now = time.time()
    import hashlib  # noqa: PLC0415

    cfg_sha = hashlib.sha256(b"".join(hashlib.sha256(files[k]).digest() for k in sorted(files))).hexdigest()
    db().insert("sessions", {
        "id": sid, "project_id": project_id, "status": "CREATED", "chip_build": chip_build["id"], "firmware_build": fw_build["id"],
        "config_sha": cfg_sha, "started_by": actor.user, "started_by_name": actor.name, "started_via": via,
        "created_at": now, "expires_at": now + MAX_HOURS * 3600, "last_active": now,
        "summary": {"files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}},
    })
    audit.record("session.start", actor, project_id, sid, {"chip_build": chip_build["id"], "firmware_build": fw_build["id"], "via": via})
    lv = Live(sid, project_id)
    lv.board = board
    lv.loop = asyncio.get_running_loop()
    try:
        lv.profile = device_mod.load(projects.read(project_id, "device.yaml"))
    except projects.NotFound:
        lv.profile, lv.profile_error = None, "the project has no device.yaml (its device protocol)"
    except device_mod.DeviceError as e:
        lv.profile, lv.profile_error = None, str(e)
    _live[sid] = lv
    try:
        await asyncio.to_thread(containers.start, sid, project_id, IMAGE, files)
        _set(sid, status="STARTING", container=containers.container_name(sid))
        lv.state = "STARTING"
        await asyncio.to_thread(_connect, lv, 0)
    except (containers.SessionError, docker_runner.DockerUnavailable, Exception) as e:  # noqa: BLE001
        lv.state = "FAILED"
        lv.error = str(e)
        _set(sid, status="FAILED", error=str(e)[:500], ended_at=time.time())
        await asyncio.to_thread(containers.remove, sid)
        _live.pop(sid, None)
        raise SessionError(f"the session didn't start: {e}") from e
    _notify(project_id, sid)
    return get(sid)  # type: ignore[return-value]


def _connect(lv: Live, after: int) -> None:
    ch = containers.Channel(lv.session_id, lambda e: _on_event(lv, e))
    ch.open()
    lv.channel = ch
    ch.request("follow", after=after)
    st = ch.request("status")
    _apply_status(lv, st)


def _apply_status(lv: Live, st: dict[str, Any]) -> None:
    lv.status = {k: v for k, v in st.items() if k not in ("id", "ok")}
    lv.vt = float(st.get("vt", lv.vt) or 0.0)
    lv.leds.update(st.get("leds") or {})
    if st.get("analog"):
        lv.analog = {k: st["analog"].get(k) or {} for k in ("values", "drives", "inputs")}
    if st.get("state") and st["state"] != lv.state:
        lv.state = st["state"]
        _set(lv.session_id, status=lv.state)


def _on_event(lv: Live, e: dict[str, Any]) -> None:
    """An event from the session's agent (on the channel's thread)."""
    kind = e.get("kind")
    if kind == "uart" and lv.profile is not None:
        ev = device_mod.event_of(lv.profile, str(e.get("line", "")))
        if ev is not None:
            e = {**e, "device_event": ev[0], "fields": ev[1]}
    with lv.cond:
        lv.events.append(e)
        if kind in ("uart", "uart_in", "bus", "led"):
            lv.panel.append(e)
        lv.last_seq = max(lv.last_seq, int(e.get("seq", 0)))
        if "vt" in e:
            try:
                lv.vt = max(lv.vt, float(e["vt"]))
            except (TypeError, ValueError):
                pass
        if kind == "led":
            lv.leds[str(e.get("led"))] = bool(e.get("on"))
        elif kind == "sensor":
            lv.sensors.setdefault(str(e.get("sensor")), {})[str(e.get("property"))] = e.get("value")
        elif kind == "analog":
            lv.analog = {"values": e.get("values") or {}, "drives": e.get("drives") or {}, "inputs": e.get("inputs") or {}}
            lv.analog_history.append((float(e.get("vt", 0.0)), dict(e.get("values") or {}), dict(e.get("drives") or {})))
            for name, props in (e.get("sensors") or {}).items():
                lv.sensors.setdefault(str(name), {}).update(props)
        elif kind == "analog_input":
            lv.analog.setdefault("inputs", {})[str(e.get("input"))] = e.get("value")
        elif kind == "state":
            lv.state = str(e.get("state"))
            if lv.state == "FAILED":
                lv.error = str(e.get("detail", ""))
        lv.cond.notify_all()
    if kind == "state":
        fields: dict[str, Any] = {"status": lv.state}
        if lv.state == "READY":
            fields["ready_at"] = time.time()
        if lv.state == "FAILED":
            fields["error"] = lv.error[:500]
        _set(lv.session_id, **fields)
    loop = lv.loop
    for q in list(lv.subscribers):
        if loop is not None:
            loop.call_soon_threadsafe(_put, q, e)
    if loop is not None:
        loop.call_soon_threadsafe(_notify, lv.project_id, lv.session_id)


def _put(q: asyncio.Queue, e: dict) -> None:
    try:
        q.put_nowait(e)
    except asyncio.QueueFull:
        pass


async def stop(session_id: str, actor: Actor, reason: str = "stopped", status: str = "STOPPED") -> dict[str, Any]:
    s = get(session_id)
    if s is None:
        raise projects.NotFound(f"there is no session {session_id!r}")
    if actor is not SYSTEM:
        projects.require(s["project_id"], actor, roles.SIMULATE, session_id)
    if s["status"] not in ACTIVE and s["status"] != "FAILED":
        return s
    lv = _live.get(session_id)
    _set(session_id, status="STOPPING")
    if lv is not None:
        lv.state = "STOPPING"
        _notify(lv.project_id, session_id)
    await asyncio.to_thread(_stop_container, session_id, lv)
    final = "FAILED" if s["status"] == "FAILED" else status
    summary = dict(s.get("summary") or {})
    summary["evidence"] = _evidence_list(s["project_id"], session_id)
    summary["stopped_because"] = reason
    _set(session_id, status=final, ended_at=time.time(), summary=summary, last_seq=lv.last_seq if lv else s["last_seq"])
    if lv is not None:
        lv.state = final
        if lv.channel is not None:
            lv.channel.close()
        _notify(lv.project_id, session_id)
        _live.pop(session_id, None)
    audit.record("session.stop", actor, s["project_id"], session_id, {"reason": reason, "status": final})
    return get(session_id)  # type: ignore[return-value]


def _stop_container(session_id: str, lv: Live | None) -> None:
    c = containers.find(session_id)
    if c is None:
        return
    if lv is not None and lv.channel is not None and not lv.channel.closed.is_set():
        try:
            lv.channel.request("stop", timeout=10)
        except containers.SessionError:
            pass
    docker_runner.wait(c, 25)
    try:
        dest = paths.session_dir(lv.project_id if lv else c.labels.get(containers.LABEL_PROJECT, ""), session_id)
        containers.fetch_out(c, dest)
    except Exception:  # noqa: BLE001
        logger.exception("Couldn't copy session %s's evidence", session_id)
    containers.remove(session_id)


def _evidence_list(project_id: str, session_id: str) -> list[dict[str, Any]]:
    import hashlib  # noqa: PLC0415

    d = paths.session_dir(project_id, session_id)
    out = []
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.is_file():
                out.append({"name": f.name, "size": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
    return out


def evidence_file(project_id: str, session_id: str, name: str) -> Path:
    if not ids.is_prefixed(session_id, "ses"):
        raise projects.NotFound("no such session")
    return paths.session_dir(project_id, session_id) / ids.clean_path(name)


# ------------------------------------------------------------------ operating the board


def _live_or_fail(session_id: str) -> Live:
    lv = _live.get(session_id)
    if lv is None or lv.channel is None:
        s = get(session_id)
        state = s["status"].lower() if s else "gone"
        raise SessionError(f"the session isn't running ({state})")
    return lv


def _touch(lv: Live) -> None:
    db().execute("UPDATE sessions SET last_active = ? WHERE id = ?", (time.time(), lv.session_id))


OPS_NEED = {
    "status": roles.VIEW,
    "events": roles.VIEW,
    "start": roles.SIMULATE,
    "pause": roles.SIMULATE,
    "run_for": roles.SIMULATE,
    "reset": roles.SIMULATE,
    "uart_send": roles.OPERATE,
    "set_sensor": roles.OPERATE,
    "set_analog": roles.OPERATE,
    "set_pin": roles.OPERATE,
    "read_reg": roles.OPERATE,
    "write_reg": roles.OPERATE,
    "record": roles.OPERATE,
}


def request(session_id: str, actor: Actor, op: str, timeout: float = 30.0, audit_it: bool = True, **args: Any) -> dict[str, Any]:
    """One operation on the running board (blocking: call it in a thread)."""
    if op not in OPS_NEED:
        raise SessionError(f"unknown operation {op!r}")
    lv = _live_or_fail(session_id)
    projects.require(lv.project_id, actor, OPS_NEED[op], session_id)
    assert lv.channel is not None
    try:
        out = lv.channel.request(op, timeout=timeout, **args)
    except containers.SessionError as e:
        raise SessionError(str(e)) from e
    if op == "status":
        _apply_status(lv, out)
    if OPS_NEED[op] != roles.VIEW:
        _touch(lv)
        if audit_it:
            audit.record(f"session.{op}", actor, lv.project_id, session_id, args)
    return {k: v for k, v in out.items() if k not in ("id", "ok")}


def set_environment(session_id: str, actor: Actor, sensor: str, setting: str, value: float) -> dict[str, Any]:
    """A sensor's setting in the part's own units (celsius), as the board's environment; or, with
    sensor ``analog``, an input of the board's analog side (its ambient, its load...)."""
    lv = _live_or_fail(session_id)
    b = lv.board
    if sensor == "analog":
        if b is None or b.analog is None:
            raise SessionError("this board has no analog side")
        inputs = catalog.analog_model(b.analog.model).get("inputs") or {}
        if setting not in inputs:
            raise SessionError(f"the analog side has no input {setting!r} (it has {', '.join(inputs)})")
        return request(session_id, actor, "set_analog", input=setting, value=float(value))
    part = next((s for s in (b.sensors if b else []) if s.name == sensor), None)
    if part is None:
        raise SessionError(f"the board has no sensor {sensor!r}")
    st = (catalog.component(part.part).get("settings") or {}).get(setting)
    if st is None:
        raise SessionError(f"a {part.part} has no setting {setting!r}")
    v = float(value)
    if not st["min"] <= v <= st["max"]:
        raise SessionError(f"{setting} is {st['min']}..{st['max']} {st.get('unit', '')}")
    return request(session_id, actor, "set_sensor", sensor=sensor, property=st["property"], value=int(round(v * st.get("scale", 1))))


def events_after(session_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    lv = _live.get(session_id)
    if lv is not None:
        with lv.cond:
            evs = [e for e in lv.events if int(e.get("seq", 0)) > after]
        if evs and int(evs[0].get("seq", 0)) <= after + 1 or not evs:
            return evs[:limit]
    # From the evidence (a stopped session) or the agent's log.
    s = get(session_id)
    if s is None:
        return []
    f = paths.session_dir(s["project_id"], session_id) / "events.jsonl"
    out = []
    if f.is_file():
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if int(e.get("seq", 0)) > after:
                    out.append(e)
                    if len(out) >= limit:
                        break
    return out


def wait_state(lv: Live, states: tuple[str, ...], timeout: float) -> bool:
    """Waits until the session is in one of ``states`` (False on timeout or failure)."""
    end = time.time() + timeout
    with lv.cond:
        while lv.state not in states:
            left = end - time.time()
            if left <= 0 or lv.state in ("FAILED", "STOPPED", "STOPPING", "EXPIRED"):
                return lv.state in states
            lv.cond.wait(min(left, 0.5))
    return True


def wait_for(lv: Live, after_seq: int, match: Callable[[dict], bool], timeout: float) -> dict[str, Any] | None:
    """The first event after ``after_seq`` that ``match`` accepts, waiting up to ``timeout`` s."""
    end = time.time() + timeout
    with lv.cond:
        while True:
            for e in lv.events:
                if int(e.get("seq", 0)) > after_seq and match(e):
                    return e
            left = end - time.time()
            if left <= 0 or lv.state in ("FAILED", "STOPPED", "STOPPING", "EXPIRED"):
                return None
            lv.cond.wait(min(left, 0.5))


def device_command(session_id: str, actor: Actor, command: str, args: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """A command of the device protocol: the line out over the console, the reply back."""
    lv = _live_or_fail(session_id)
    projects.require(lv.project_id, actor, roles.OPERATE, session_id)
    if lv.profile is None:
        raise SessionError(lv.profile_error or "the project has no device protocol")
    line = device_mod.encode(lv.profile, command, args)
    with lv.lock:
        start_seq = lv.last_seq
        assert lv.channel is not None
        try:
            lv.channel.request("uart_send", uart=lv.profile.uart, text=line)
        except containers.SessionError as e:
            raise SessionError(str(e)) from e

        def is_reply(e: dict) -> bool:
            if e.get("kind") != "uart" or e.get("uart") != lv.profile.uart:
                return False
            how, _ = device_mod.reply_of(lv.profile, command, str(e.get("line", "")))
            return how in ("ok", "error")

        e = wait_for(lv, start_seq, is_reply, timeout)
    _touch(lv)
    audit.record("device.command", actor, lv.project_id, session_id, {"command": command, "args": args or {}, "line": line})
    if e is None:
        raise SessionError(f"the device didn't answer {command} in {timeout:g} s (sent {line!r})")
    how, result = device_mod.reply_of(lv.profile, command, str(e.get("line", "")))
    if how == "error":
        raise device_mod.DeviceError(f"the device refused {command}: {result.get('message') if result else ''}")
    for fn in list(reply_listeners):
        try:
            fn(lv.project_id, session_id, int(e.get("seq", 0)), result or {})
        except Exception:  # noqa: BLE001
            logger.exception("A device reply listener failed")
    return {"command": command, "sent": line, "reply": e.get("line", ""), "result": result or {}, "vt": e.get("vt"), "seq": e.get("seq")}


async def subscribe(session_id: str) -> asyncio.Queue:
    lv = _live_or_fail(session_id)
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    lv.subscribers.add(q)
    return q


def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    lv = _live.get(session_id)
    if lv is not None:
        lv.subscribers.discard(q)


def waveform(session_id: str, actor: Actor) -> bytes:
    """The chip's waveform so far (VCD): from the running container, or the evidence."""
    s = get(session_id)
    if s is None:
        raise projects.NotFound("no such session")
    projects.require(s["project_id"], actor, roles.DOWNLOAD, session_id)
    c = containers.find(session_id)
    if c is not None and s["status"] in ACTIVE:
        return containers.read_file(c, "/work/out/chip.vcd")
    f = paths.session_dir(s["project_id"], session_id) / "chip.vcd"
    if not f.is_file():
        raise projects.NotFound("this session has no waveform")
    return f.read_bytes()


# ------------------------------------------------------------------ keeping them in order


async def recover() -> None:
    """At start: reconnect to the sessions whose containers still run; the others ended."""
    found = await asyncio.to_thread(containers.running)
    loop = asyncio.get_running_loop()
    for row in db().query("SELECT * FROM sessions WHERE status IN ('CREATED','STARTING','READY','RUNNING','PAUSED','STOPPING')"):
        sid, pid = row["id"], row["project_id"]
        if sid in found and row["status"] != "STOPPING":
            lv = Live(sid, pid)
            lv.loop = loop
            lv.state = row["status"]
            try:
                lv.board = board_mod.from_yaml(projects.read(pid, "system.yaml"))
                lv.profile = device_mod.load(projects.read(pid, "device.yaml"))
            except Exception:  # noqa: BLE001
                pass
            _live[sid] = lv
            try:
                await asyncio.to_thread(_connect, lv, 0)
                logger.info("Reconnected to session %s", sid)
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning("Couldn't reconnect to session %s: %s", sid, e)
                _live.pop(sid, None)
        await asyncio.to_thread(_stop_container, sid, None)
        _set(sid, status="STOPPED" if row["status"] == "STOPPING" else "FAILED", ended_at=time.time(),
             error="" if row["status"] == "STOPPING" else "the app restarted and the session's container was gone")
    # Containers of sessions the database doesn't know (or knows ended): removed.
    for sid, c in found.items():
        if sid not in _live:
            await asyncio.to_thread(docker_runner.remove, c)


async def sweep() -> None:
    """Ends idle and old sessions."""
    now = time.time()
    for sid, lv in list(_live.items()):
        s = get(sid)
        if s is None:
            continue
        if s["status"] in ACTIVE and (now - (s["last_active"] or s["created_at"]) > IDLE_MINUTES * 60 or now > (s["expires_at"] or now + 1)):
            try:
                await stop(sid, SYSTEM, "nobody used it for a while" if now <= (s["expires_at"] or now + 1) else "it ran its maximum time", "EXPIRED")
            except Exception:  # noqa: BLE001
                logger.exception("Couldn't end session %s", sid)
        elif lv.channel is not None and lv.channel.closed.is_set() and lv.state in ACTIVE:
            # The line closed under a running session: reconnect.
            try:
                await asyncio.to_thread(_connect, lv, lv.last_seq)
            except Exception:  # noqa: BLE001
                c = await asyncio.to_thread(containers.find, sid)
                if c is None or docker_runner.state(c).get("Running") is False:
                    lv.state = "FAILED"
                    _set(sid, status="FAILED", error="the session's container stopped", ended_at=time.time())
                    _live.pop(sid, None)
                    _notify(lv.project_id, sid)


async def stop_all() -> None:
    """At shutdown: nothing to do; the containers go on and a restarted backend reconnects."""
    for lv in list(_live.values()):
        if lv.channel is not None:
            lv.channel.close()
