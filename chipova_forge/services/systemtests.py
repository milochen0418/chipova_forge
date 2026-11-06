"""System tests (``tests/*.yaml``): the whole product checked end to end on the virtual board.

Each run starts a fresh session (the chip's RTL, the firmware, the sensors), waits for the
device to say it is ready, then runs the steps in order and stops at the first that fails:

    - sensor: {temperature: {celsius: 40}}           the board's environment
    - command: {set_threshold: {celsius: 35}}         a device command (device.yaml)
      expect: {threshold: 35}                         ... and what its answer must hold
    - wait: 0.5                                       seconds
    - expect_event: {event: alarm, within: 2, fields: {temperature: 40}}
    - expect_led: {alarm: true, within: 1}            true | false | any (it changed)
    - expect_register: {COUNT: 1}                     read through the chip's bus or SPI
    - uart: {send: "FROBNICATE", expect: "ERR unknown command", within: 1}
    - record: {}                                      the chip's waveform from here on

A value to expect is a number (equal, with a 1e-6 tolerance), true/false, a text, or
``{min: .., max: ..}``. Every step keeps the event numbers it covered, so its evidence (UART
lines, bus transactions, LED changes, the chip's waveform) can be shown next to it; the session's
whole event log is kept with the run.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import yaml

from chipova_forge import ids
from chipova_forge.db import db, loads
from chipova_forge.domain import roles
from chipova_forge.gateway import device as device_mod
from chipova_forge.jobs import builds
from chipova_forge.services import audit, projects
from chipova_forge.services.actors import Actor
from chipova_forge.sessions import service

STEP_KINDS = ("sensor", "command", "wait", "expect_event", "expect_led", "expect_register", "uart", "record", "analog", "expect_analog")
READY_TIMEOUT = 30.0


class TestError(ValueError):
    pass


def get(run_id: str) -> dict[str, Any] | None:
    if not ids.is_prefixed(run_id, "tst"):
        return None
    row = db().one("SELECT * FROM test_runs WHERE id = ?", (run_id,))
    return _row(row) if row else None


def _row(row) -> dict[str, Any]:  # noqa: ANN001
    return {**dict(row), "steps": loads(row["steps"], []), "inputs": loads(row["inputs"], {})}


def of_project(project_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = db().query("SELECT * FROM test_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))
    return [_row(r) for r in rows]


def latest_by_scenario(project_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in of_project(project_id, 200):
        out.setdefault(r["scenario"], r)
    return out


def load(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise TestError(f"the test doesn't read: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not data["steps"]:
        raise TestError("a test has a name and a list of steps")
    for i, st in enumerate(data["steps"], 1):
        if not isinstance(st, dict):
            raise TestError(f"step {i} isn't a mapping")
        kinds = [k for k in st if k in STEP_KINDS]
        if len(kinds) != 1:
            raise TestError(f"step {i}: one of {', '.join(STEP_KINDS)}")
        extra = set(st) - {kinds[0], "expect"}
        if extra or ("expect" in st and kinds[0] != "command"):
            raise TestError(f"step {i}: unknown {', '.join(sorted(extra or {'expect'}))}")
    return data


def _matches(want: Any, got: Any) -> bool:
    if isinstance(want, dict):
        if got is None or isinstance(got, (str, bool)):
            return False
        lo, hi = want.get("min"), want.get("max")
        return (lo is None or got >= lo) and (hi is None or got <= hi)
    if isinstance(want, bool) or isinstance(got, bool):
        return bool(want) == bool(got) and type(got) is bool
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(float(want) - float(got)) <= 1e-6 * max(1.0, abs(float(want)))
    return str(want) == str(got)


def _check_fields(want: dict[str, Any], got: dict[str, Any]) -> list[str]:
    bad = []
    for k, w in (want or {}).items():
        if k not in got:
            bad.append(f"{k} is missing")
        elif not _matches(w, got[k]):
            bad.append(f"{k} = {got[k]!r}, expected {w!r}")
    return bad


def _describe(kind: str, body: Any, st: dict[str, Any]) -> str:
    if kind == "sensor":
        return "set " + ", ".join(f"{s}.{k} = {v}" for s, kv in body.items() for k, v in kv.items())
    if kind == "command":
        name, args = next(iter(body.items()))
        text = f"{name}(" + ", ".join(f"{k}={v}" for k, v in (args or {}).items()) + ")"
        if st.get("expect"):
            text += " -> " + ", ".join(f"{k}={v}" for k, v in st["expect"].items())
        return text
    if kind == "wait":
        return f"wait {body} s"
    if kind == "expect_event":
        return f"event {body.get('event')} within {body.get('within', 2)} s"
    if kind == "expect_led":
        return "LED " + ", ".join(f"{k} {'changes' if v == 'any' else ('on' if v else 'off')}" for k, v in body.items() if k != "within")
    if kind == "expect_register":
        return ", ".join(f"{k} = {v}" for k, v in body.items())
    if kind == "uart":
        return f"send {body.get('send')!r}, expect {body.get('expect')!r}"
    if kind == "record":
        return "record the chip's waveform"
    if kind == "analog":
        return "set the analog side: " + ", ".join(f"{k} = {v}" for k, v in body.items())
    if kind == "expect_analog":
        return "analog " + ", ".join(f"{k} {v}" for k, v in body.items() if k != "within") + f" within {body.get('within', 5)} s"
    return kind


def _step(sid: str, actor: Actor, kind: str, body: Any, st: dict[str, Any], since: int) -> tuple[bool, str, dict[str, Any]]:
    """Runs one step (in a thread). Returns (passed, detail, extra). ``since``: the event number
    the step before this one started at (what it caused may already have happened)."""
    lv = service.live(sid)
    if lv is None:
        return False, "the session ended", {}
    if kind == "sensor":
        for sensor, settings in body.items():
            for setting, value in settings.items():
                service.set_environment(sid, actor, sensor, setting, value)
        return True, "", {}
    if kind == "wait":
        time.sleep(min(30.0, float(body)))
        return True, "", {}
    if kind == "command":
        name, args = next(iter(body.items()))
        try:
            out = service.device_command(sid, actor, name, args or {})
        except device_mod.DeviceError as e:
            return False, str(e), {}
        bad = _check_fields(st.get("expect") or {}, out["result"])
        return (not bad), "; ".join(bad) or out["reply"], {"reply": out["reply"], "result": out["result"]}
    if kind == "expect_event":
        name = body.get("event")
        within = float(body.get("within", 2))
        e = service.wait_for(lv, since, lambda ev: ev.get("device_event") == name, within)
        if e is None:
            return False, f"no {name} event in {within:g} s", {}
        bad = _check_fields(body.get("fields") or {}, e.get("fields") or {})
        return (not bad), "; ".join(bad) or e.get("line", ""), {"event": e.get("line", ""), "vt": e.get("vt"), "matched_seq": int(e.get("seq", 0))}
    if kind == "expect_led":
        within = float(body.get("within", 1))
        start = since
        bad = []
        for led, want in body.items():
            if led == "within":
                continue
            if want == "any":
                e = service.wait_for(lv, start, lambda ev, led=led: ev.get("kind") == "led" and ev.get("led") == led, within)
                if e is None:
                    bad.append(f"{led} didn't change")
                continue
            end = time.time() + within
            while time.time() < end and lv.leds.get(led) != bool(want):
                time.sleep(0.05)
            if lv.leds.get(led) != bool(want):
                bad.append(f"{led} is {'on' if lv.leds.get(led) else 'off'}")
        return (not bad), "; ".join(bad), {"leds": dict(lv.leds)}
    if kind == "expect_register":
        regmap = builds.register_map(lv.project_id)
        bad, got = [], {}
        for reg, want in body.items():
            r = regmap.by_name(reg) if regmap else None
            if r is None:
                bad.append(f"the chip has no register {reg}")
                continue
            v = service.request(sid, actor, "read_reg", offset=r.offset, audit_it=False)["value"]
            got[reg] = v
            if not _matches(want, v):
                bad.append(f"{reg} = {v} (0x{v:x}), expected {want}")
        return (not bad), "; ".join(bad) or ", ".join(f"{k}={v}" for k, v in got.items()), {"registers": got}
    if kind == "uart":
        send, want, within = str(body.get("send", "")), str(body.get("expect", "")), float(body.get("within", 1))
        uart = lv.profile.uart if lv.profile else "console"
        start = _seq_at(lv)
        service.request(sid, actor, "uart_send", uart=uart, text=send, audit_it=False)
        e = service.wait_for(lv, start, lambda ev: ev.get("kind") == "uart" and want in str(ev.get("line", "")), within)
        return (e is not None), (e or {}).get("line", "") or f"no line with {want!r} in {within:g} s", {}
    if kind == "record":
        service.request(sid, actor, "record", audit_it=False)
        return True, "", {}
    if kind == "analog":
        for name, value in body.items():
            service.set_environment(sid, actor, "analog", name, value)
        return True, "", {}
    if kind == "expect_analog":
        # The analog side's outputs (and drives, 0/1) meet the conditions within the time given.
        within = float(body.get("within", 5))
        want = {k: v for k, v in body.items() if k != "within"}
        end = time.time() + within
        bad: list[str] = []
        while True:
            a = lv.analog or {}
            got = {**(a.get("values") or {}), **(a.get("drives") or {})}
            bad = _check_fields(want, got)
            if not bad or time.time() >= end or lv.state not in service.ACTIVE:
                break
            time.sleep(0.1)
        return (not bad), "; ".join(bad) or ", ".join(f"{k}={got.get(k)}" for k in want), {"analog": got, "vt": lv.vt}
    return False, f"unknown step {kind}", {}


def _seq_at(lv: service.Live) -> int:
    return lv.last_seq


async def run(project_id: str, path: str, actor: Actor, via: str = "ui") -> dict[str, Any]:
    projects.require(project_id, actor, roles.TEST, path)
    path = ids.clean_path(path)
    if not path.startswith("tests/") or not path.endswith(".yaml"):
        raise TestError("tests are tests/<name>.yaml")
    text = projects.read(project_id, path)
    spec = load(text)
    rid = ids.new_id("tst")
    now = time.time()
    db().insert("test_runs", {"id": rid, "project_id": project_id, "scenario": path, "status": "RUNNING",
                              "requested_by": actor.user, "requested_by_name": actor.name, "requested_via": via,
                              "created_at": now, "inputs": {"test_sha256": hashlib.sha256(text.encode()).hexdigest(), "name": spec.get("name", path)}})
    audit.record("test.run", actor, project_id, rid, {"test": path, "via": via})
    _notify(project_id, rid)
    steps: list[dict[str, Any]] = []
    sid = ""
    status, error = "FAILED", ""
    try:
        s = await service.start(project_id, actor, via="test")
        sid = s["id"]
        db().update("test_runs", {"id": rid}, {"session_id": sid, "inputs": {
            "test_sha256": hashlib.sha256(text.encode()).hexdigest(), "name": spec.get("name", path),
            "chip_build": s["chip_build"], "firmware_build": s["firmware_build"], "session_config": s["config_sha"]}})
        lv = service.live(sid)
        assert lv is not None
        if lv.profile is None:
            raise TestError(lv.profile_error or "the project has no device protocol")
        if not await asyncio.to_thread(service.wait_state, lv, ("RUNNING",), READY_TIMEOUT):
            raise TestError(f"the board didn't start running in {READY_TIMEOUT:g} s ({lv.state.lower()}: {lv.error})")
        ready_event = "ready" if "ready" in lv.profile.events else ""
        if ready_event:
            e = await asyncio.to_thread(service.wait_for, lv, 0, lambda ev: ev.get("device_event") == ready_event, READY_TIMEOUT)
            if e is None:
                raise TestError(f"the device didn't say it was ready in {READY_TIMEOUT:g} s (state {lv.state}: {lv.error})")
        failed = False
        prev_seq = lv.last_seq
        for i, st in enumerate(spec["steps"], 1):
            kind = next(k for k in st if k in STEP_KINDS)
            body = st[kind]
            row: dict[str, Any] = {"n": i, "kind": kind, "text": _describe(kind, body, st), "status": "SKIPPED", "detail": ""}
            if failed:
                steps.append(row)
                continue
            seq0, t0 = lv.last_seq, time.time()
            try:
                ok, detail, extra = await asyncio.to_thread(_step, sid, actor, kind, body, st, prev_seq if kind.startswith("expect") else seq0)
            except (service.SessionError, device_mod.DeviceError, ValueError) as e:
                ok, detail, extra = False, str(e), {}
            row.update({"status": "PASS" if ok else "FAIL", "detail": detail, "seq_from": seq0 + 1, "seq_to": lv.last_seq,
                        "vt": round(lv.vt, 6), "seconds": round(time.time() - t0, 3), **extra})
            steps.append(row)
            if not kind.startswith("expect"):
                prev_seq = seq0
            elif kind == "expect_event" and ok and extra.get("matched_seq"):
                # Expected events come in order: the next one is looked for after this one.
                prev_seq = int(extra["matched_seq"])
            db().update("test_runs", {"id": rid}, {"steps": steps})
            _notify(project_id, rid)
            if not ok:
                failed = True
        status = "FAILED" if failed else "PASSED"
    except (service.SessionError, TestError, projects.NotFound, builds.BuildError) as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        if sid:
            try:
                await service.stop(sid, actor, f"the test {path} ended")
            except Exception:  # noqa: BLE001
                pass
    db().update("test_runs", {"id": rid}, {"status": status, "steps": steps, "error": error[:1000], "ended_at": time.time()})
    audit.record("test.result", actor, project_id, rid, {"test": path, "status": status, "error": error[:200]})
    _notify(project_id, rid)
    return get(rid)  # type: ignore[return-value]


listeners: list = []


def _notify(project_id: str, run_id: str) -> None:
    for fn in list(listeners):
        try:
            fn(project_id, run_id)
        except Exception:  # noqa: BLE001
            pass
