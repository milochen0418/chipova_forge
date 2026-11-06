"""A project's room: what everybody on /p/<id> sees change live, pushed to all of them.

``ProjectRoom`` is a Reflex ``SharedState``: every tab on a project links its state to the
project's id. It carries what the pages draw: the steps of the flow and where each stands, the
builds, the running simulation (its state, LEDs, sensors, the console's lines, the chip's
register transactions), the device's dashboard values, the tests, the AI's proposals, the
releases, the people here and the recent activity. Anything that changes these calls
:func:`push_later`, which gathers the changes of a moment into one push per project.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import time
from typing import Any

import reflex as rx
from reflex_ddns_auth import rooms

from chipova_forge import ids
from chipova_forge.contract import contract as hdc
from chipova_forge.jobs import builds
from chipova_forge.services import presence, projects, proposals, releases, systemtests
from chipova_forge.sessions import service
from chipova_forge.cosim import analog_svg
from chipova_forge.system import board as board_mod
from chipova_forge.system import catalog

logger = logging.getLogger(__name__)

PUSH_DELAY = 0.25
ACTIVITY = 60
UART_ROWS = 80
BUS_ROWS = 80


@dataclasses.dataclass
class StepRow:
    n: int = 0
    title: str = ""
    status: str = ""  # DONE | ATTENTION | TODO | RUNNING | FAILED | LATER
    detail: str = ""
    view: str = ""


@dataclasses.dataclass
class BuildRow:
    kind: str = ""
    id: str = ""
    status: str = ""
    by: str = ""
    at: float = 0.0
    seconds: float = 0.0
    summary: str = ""
    error: str = ""


@dataclasses.dataclass
class LedRow:
    name: str = ""
    on: bool = False
    color: str = ""  # tailwind colour name


@dataclasses.dataclass
class SensorRow:
    sensor: str = ""
    setting: str = ""
    title: str = ""
    value: float = 0.0
    unit: str = ""
    min: float = 0.0
    max: float = 0.0
    step: float = 0.5
    driven: bool = False  # the analog side sets it


@dataclasses.dataclass
class AnalogRow:
    """An output, input or drive of the board's analog side."""

    name: str = ""
    title: str = ""
    value: float = 0.0
    unit: str = ""
    min: float = 0.0
    max: float = 0.0
    step: float = 0.1
    on: bool = False


@dataclasses.dataclass
class UartRow:
    seq: int = 0
    vt: str = ""
    dir: str = ""  # in (to the device) | out (from it)
    line: str = ""
    event: str = ""


@dataclasses.dataclass
class BusRow:
    seq: int = 0
    vt: str = ""
    op: str = ""
    register: str = ""
    offset: str = ""
    value: str = ""
    via: str = ""


@dataclasses.dataclass
class TileRow:
    name: str = ""
    value: str = ""
    unit: str = ""
    alert: bool = False


@dataclasses.dataclass
class TestRow:
    path: str = ""
    name: str = ""
    status: str = ""
    run: str = ""
    at: float = 0.0
    passed: int = 0
    total: int = 0
    error: str = ""


@dataclasses.dataclass
class ProposalRow:
    id: str = ""
    title: str = ""
    agent: str = ""
    status: str = ""
    files: str = ""
    rationale: str = ""
    decided_by: str = ""
    reason: str = ""
    at: float = 0.0


@dataclasses.dataclass
class ReleaseRow:
    id: str = ""
    title: str = ""
    status: str = ""
    by: str = ""
    by_user: str = ""
    at: float = 0.0
    passed: int = 0
    total: int = 0
    size: int = 0


@dataclasses.dataclass
class ActivityRow:
    at: float = 0.0
    who: str = ""
    what: str = ""
    kind: str = ""


_activity: dict[str, collections.deque] = {}
# Reflex's registration of the app (set by the app module): pushes started outside Reflex's own
# handlers (session events, build workers, AI tool calls) run in a context that lacks it.
registration = None
_pending: dict[str, set[str]] = {}
_handles: dict[str, asyncio.TimerHandle] = {}
_tasks: set[asyncio.Task] = set()
# The device's last known values, per project (from status replies and events).
_device_values: dict[str, dict[str, Any]] = {}


def note(project_id: str, who: str, what: str, kind: str = "edit") -> None:
    log = _activity.setdefault(project_id, collections.deque(maxlen=ACTIVITY))
    log.appendleft(ActivityRow(at=time.time(), who=who, what=what, kind=kind))
    push_later(project_id, "activity")


def remember_device_values(project_id: str, values: dict[str, Any]) -> None:
    _device_values.setdefault(project_id, {}).update(values)
    push_later(project_id, "session")


LED_COLORS = {"green": "emerald", "orange": "amber", "red": "rose", "blue": "sky"}


def _fmt_vt(v: Any) -> str:
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return ""


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.3g}"
    return str(v)


class ProjectRoom(rx.SharedState):
    project_id: str = ""
    title: str = ""
    template: str = ""
    owner_name: str = ""
    gone: bool = False
    chip_name: str = ""
    chip_top: str = ""
    chip_snapshot: str = ""
    chip_source: str = ""
    interface: str = ""
    platform: str = ""
    contract_errors: int = 0
    board_errors: int = 0
    steps: list[StepRow] = []
    builds: list[BuildRow] = []
    # The simulation
    session_id: str = ""
    session_state: str = ""
    session_error: str = ""
    session_vt: str = ""
    session_by: str = ""
    # The last ended simulation that left evidence (each tab signs its own download links).
    evidence_session: str = ""
    # The latest finished PCB build (each tab signs its own links to its pictures and files).
    pcb_build: str = ""
    leds: list[LedRow] = []
    sensors: list[SensorRow] = []
    # The analog side (ngspice in step with the board)
    analog_title: str = ""
    analog_outputs: list[AnalogRow] = []
    analog_inputs: list[AnalogRow] = []
    analog_drives: list[AnalogRow] = []
    analog_svg: str = ""
    uart: list[UartRow] = []
    bus: list[BusRow] = []
    events_total: int = 0
    # The device (dashboard)
    tiles: list[TileRow] = []
    alert: bool = False
    tests: list[TestRow] = []
    proposals: list[ProposalRow] = []
    releases: list[ReleaseRow] = []
    activity: list[ActivityRow] = []
    peers: list[dict[str, str]] = []

    @rx.var
    def people(self) -> list[dict[str, str]]:
        out, seen = [], set()
        for p in self.peers:
            user = p.get("user") or p.get("id")
            if user in seen:
                continue
            seen.add(user)
            name = p.get("name", "") or "?"
            out.append({"id": p.get("id", ""), "user": user, "name": name, "initials": presence.initials(name, user),
                        "color": p.get("color", ""), "avatar": p.get("avatar", "")})
        return out

    @rx.var
    def running(self) -> bool:
        return self.session_state in ("STARTING", "READY", "RUNNING", "PAUSED")

    @rx.var
    def pending_proposals(self) -> int:
        return sum(1 for p in self.proposals if p.status == "PROPOSED")


def _steps(pid: str, c: hdc.Contract | None, b: board_mod.Board | None, regs: dict | None, bmap: dict[str, dict], sess: dict | None,
           tests: dict[str, dict], rels: list[dict], c_issues: list, b_issues: list) -> list[StepRow]:
    """The blueprint's twelve steps, each from what Forge has."""
    def st(n, title, status, detail, view):  # noqa: ANN001, ANN202
        return StepRow(n=n, title=title, status=status, detail=detail, view=view)

    out = []
    snap = c.chip.snapshot[:19] + "…" if c else ""
    out.append(st(1, "Import the chip", "DONE" if c else "FAILED", f"{c.chip.name if c else '?'} · snapshot {snap}", "chip"))
    errs = [i for i in c_issues if i.level == "error"]
    out.append(st(2, "Hardware contract", "ATTENTION" if errs else "DONE", f"{len(errs)} errors" if errs else f"{c.interface.protocol if c else ''} · {len(c.pins) if c else 0} pins", "chip"))
    berrs = [i for i in b_issues if i.level == "error"]
    out.append(st(3, "System architecture", "ATTENTION" if berrs else "DONE", f"{len(berrs)} errors" if berrs else (f"{b.platform} · {len(b.sensors)} sensors" if b else ""), "board"))
    pcb = bmap.get("pcb")
    if pcb is None:
        out.append(st(4, "Circuit schematic", "TODO", "build the schematic and PCB (Board page)", "board"))
        out.append(st(5, "PCB", "TODO", "laid out, routed and checked by KiCad", "board"))
    elif pcb["status"] in ("QUEUED", "RUNNING"):
        out.append(st(4, "Circuit schematic", "RUNNING", pcb["status"].lower(), "board"))
        out.append(st(5, "PCB", "RUNNING", pcb["status"].lower(), "board"))
    elif pcb["status"] == "FAILED":
        out.append(st(4, "Circuit schematic", "FAILED", pcb.get("error") or "failed", "board"))
        out.append(st(5, "PCB", "FAILED", pcb.get("error") or "failed", "board"))
    else:
        s = (pcb.get("summary") or {})
        ps, meta = s.get("pcb") or {}, s.get("meta") or {}
        erc, drc = ps.get("erc") or {}, ps.get("drc") or {}
        e_err = erc.get("errors", 0)
        out.append(st(4, "Circuit schematic", "ATTENTION" if e_err else "DONE",
                      f"ERC: {e_err} errors" if e_err else f"{meta.get('parts', 0)} parts, {meta.get('nets', 0)} nets · ERC clean", "board"))
        bad = drc.get("errors", 0) + drc.get("unconnected", 0) + drc.get("parity", 0)
        out.append(st(5, "PCB", "ATTENTION" if bad else "DONE",
                      (f"DRC: {drc.get('errors', 0)} errors, {drc.get('unconnected', 0)} unconnected, {drc.get('parity', 0)} parity" if bad
                       else f"{meta.get('width', 0)} × {meta.get('height', 0)} mm, 2 layers · DRC clean · Gerbers"), "board"))
    if b is not None and b.analog is not None:
        out.append(st(6, "Electrical simulation", "DONE", f"ngspice ({b.analog.model}) in step with the board, every {b.analog.step_ms} ms", "lab"))
    else:
        out.append(st(6, "Electrical simulation", "NONE", "no analog side in system.yaml: the sensors read what you set", "board"))

    def build_status(kind: str) -> tuple[str, str]:
        x = bmap.get(kind)
        if x is None:
            return "TODO", "not built yet"
        if x["status"] in ("QUEUED", "RUNNING"):
            return "RUNNING", x["status"].lower()
        if x["status"] == "FAILED":
            return "FAILED", x.get("error") or "failed"
        s = x.get("summary") or {}
        if kind == "firmware" and s.get("size"):
            return "DONE", f"{s['size'].get('text', 0)} bytes of code"
        if kind == "registers":
            sync = (s.get("sync") or {}).get("status", "")
            return ("ATTENTION" if sync == "differs" else "DONE"), f"{s.get('registers', 0)} registers · {sync.replace('_', ' ')}"
        return "DONE", "built"

    out.append(st(7, "Firmware", *build_status("firmware"), "firmware"))
    out.append(st(8, "Virtual chip (Verilator)", *build_status("chip"), "registers"))
    if sess and sess["status"] in service.ACTIVE:
        out.append(st(9, "Co-simulation (Renode × Verilator)", "RUNNING", sess["status"].lower(), "lab"))
        out.append(st(10, "Device gateway", "DONE", f"simulation {sess['id']}", "lab"))
    elif sess:
        out.append(st(9, "Co-simulation (Renode × Verilator)", "DONE" if sess["status"] in ("STOPPED", "EXPIRED") else "FAILED",
                      f"last: {sess['status'].lower()}", "lab"))
        out.append(st(10, "Device gateway", "TODO", "start a simulation", "lab"))
    else:
        out.append(st(9, "Co-simulation (Renode × Verilator)", "TODO", "start a simulation", "lab"))
        out.append(st(10, "Device gateway", "TODO", "start a simulation", "lab"))
    out.append(st(11, "App (dashboard)", "DONE" if sess and sess["status"] in service.ACTIVE else "TODO", "the device's dashboard", "dashboard"))
    passed = sum(1 for r in tests.values() if r["status"] == "PASSED")
    failed = sum(1 for r in tests.values() if r["status"] == "FAILED")
    total = len(projects.test_files(pid))
    rel = rels[0] if rels else None
    tstat = "FAILED" if failed else ("DONE" if total and passed == total else "TODO")
    detail = f"{passed}/{total} tests passed" + (f" · release {rel['status'].lower()}" if rel else "")
    out.append(st(12, "E2E verification & release", tstat, detail, "tests"))
    return out


def publish(room: ProjectRoom, pid: str, what: set[str]) -> None:
    """Shows the project as it is now to everyone on it (only what changed)."""
    p = projects.get(pid)
    if p is None:
        if not room.gone:
            room.gone = True
        return
    c = b = None
    c_issues: list = []
    b_issues: list = []
    try:
        c = projects.contract(pid)
        c_issues = hdc.check(c)
    except Exception:  # noqa: BLE001
        pass
    try:
        b = board_mod.from_yaml(projects.read(pid, "system.yaml"))
        b_issues = board_mod.check(b, c)
    except Exception:  # noqa: BLE001
        pass
    if "project" in what:
        vals = {
            "title": p["title"], "template": p["template"], "owner_name": p["owner_name"],
            "chip_name": c.chip.name if c else "", "chip_top": c.chip.top_module if c else "",
            "chip_snapshot": c.chip.snapshot if c else "", "chip_source": str((p.get("chip") or {}).get("source", {}).get("ref", "")),
            "interface": c.interface.protocol if c else "", "platform": catalog.platform(b.platform)["title"] if b else "",
            "contract_errors": sum(1 for i in c_issues if i.level == "error"),
            "board_errors": sum(1 for i in b_issues if i.level == "error"),
        }
        for k, v in vals.items():
            if getattr(room, k) != v:
                setattr(room, k, v)
    bmap = {k: x for k in builds.KINDS if (x := builds.latest(pid, k)) is not None}
    sess = service.active_of(pid) or next(iter(service.of_project(pid, 1)), None)
    tests = systemtests.latest_by_scenario(pid)
    rels = releases.of_project(pid, 10)
    if what & {"project", "builds", "session", "tests", "releases"}:
        rows = _steps(pid, c, b, None, bmap, sess, tests, rels, c_issues, b_issues)
        if list(room.steps) != rows:
            room.steps = rows
    if "builds" in what:
        rows = []
        for k in builds.KINDS:
            x = bmap.get(k)
            if x is None:
                rows.append(BuildRow(kind=k, status="NONE"))
                continue
            s = x.get("summary") or {}
            text = ""
            if k == "firmware" and s.get("size"):
                text = f"{s['size'].get('text', 0)} B code, {s['size'].get('bss', 0)} B RAM"
            elif k == "registers":
                text = f"{s.get('registers', 0)} registers, {(s.get('sync') or {}).get('status', '')}"
            elif k == "chip":
                meta = s.get("meta") or {}
                text = f"{meta.get('top', '')} at {int(meta.get('frequency_hz', 0)) // 1000} kHz"
            elif k == "pcb":
                meta, ps = s.get("meta") or {}, s.get("pcb") or {}
                drc = ps.get("drc") or {}
                text = (f"{meta.get('width', 0)} × {meta.get('height', 0)} mm · DRC {drc.get('errors', '?')} errors, "
                        f"{drc.get('unconnected', '?')} unconnected · ERC {(ps.get('erc') or {}).get('errors', '?')} errors")
            secs = (x["ended_at"] - x["started_at"]) if x["ended_at"] and x["started_at"] else 0.0
            rows.append(BuildRow(kind=k, id=x["id"], status=x["status"], by=x["requested_by_name"], at=x["created_at"],
                                 seconds=round(secs, 1), summary=text, error=x["error"]))
        if list(room.builds) != rows:
            room.builds = rows
        done = builds.latest(pid, "pcb", builds.SUCCEEDED)
        pcb_id = done["id"] if done else ""
        if room.pcb_build != pcb_id:
            room.pcb_build = pcb_id
    if "session" in what:
        _publish_session(room, pid, sess, b)
    if "tests" in what:
        rows = []
        for path, text in projects.test_files(pid).items():
            r = tests.get(path)
            try:
                name = str(systemtests.load(text).get("name", path))
            except systemtests.TestError:
                name = path
            if r is None:
                rows.append(TestRow(path=path, name=name, status="NOT_RUN"))
            else:
                rows.append(TestRow(path=path, name=name, status=r["status"], run=r["id"], at=r["created_at"],
                                    passed=sum(1 for s in r["steps"] if s["status"] == "PASS"), total=len(r["steps"]), error=r["error"]))
        if list(room.tests) != rows:
            room.tests = rows
    if "proposals" in what:
        rows = [ProposalRow(id=x["id"], title=x["title"], agent=x["agent_name"], status=x["status"], files=", ".join(f["path"] for f in x["files"]),
                            rationale=x["rationale"], decided_by=x["decided_by_name"], reason=x["reason"], at=x["created_at"])
                for x in proposals.of_project(pid, 30)]
        if list(room.proposals) != rows:
            room.proposals = rows
    if "releases" in what:
        rows = [ReleaseRow(id=r["id"], title=r["title"], status=r["status"], by=r["created_by_name"], by_user=r["created_by"], at=r["created_at"],
                           passed=sum(1 for x in r["checks"] if x["status"] == "PASS"), total=len(r["checks"]), size=r["size"]) for r in rels]
        if list(room.releases) != rows:
            room.releases = rows
    if "activity" in what:
        rows = list(_activity.get(pid, ()))
        if list(room.activity) != rows:
            room.activity = rows
    if "people" in what:
        peers = presence.tabs(pid)
        if list(room.peers) != peers:
            room.peers = peers


def last_evidence_session(pid: str) -> str:
    """The newest ended simulation of the project that left evidence."""
    for s in service.of_project(pid, 10):
        if s["status"] not in service.ACTIVE and (s.get("summary") or {}).get("evidence"):
            return s["id"]
    return ""


def _publish_analog(room: ProjectRoom, pid: str, lv: Any, b: board_mod.Board | None) -> None:
    """The analog side's values, its inputs (sliders) and its chart."""
    title, outs, ins, drives, svg = "", [], [], [], ""
    if b is not None and b.analog is not None:
        try:
            m = catalog.analog_model(b.analog.model)
        except catalog.CatalogError:
            m = None
        if m is not None:
            title = str(m.get("title", b.analog.model))
            a = lv.analog if lv is not None else {}
            values, inputs, dstate = a.get("values") or {}, a.get("inputs") or {}, a.get("drives") or {}
            for name, o in (m.get("outputs") or {}).items():
                outs.append(AnalogRow(name=name, title=o.get("title", name), value=round(float(values.get(name, 0.0)), 2), unit=o.get("unit", "")))
            for name, i in (m.get("inputs") or {}).items():
                v = inputs.get(name, b.analog.inputs.get(name, i.get("default", 0)))
                ins.append(AnalogRow(name=name, title=i.get("title", name), value=round(float(v), 2), unit=i.get("unit", ""),
                                     min=float(i["min"]), max=float(i["max"]), step=0.1 if float(i["max"]) - float(i["min"]) <= 10 else 0.5))
            for name, d in (m.get("drives") or {}).items():
                if name in b.analog.drives:
                    drives.append(AnalogRow(name=name, title=f"{d.get('title', name)} ← chip {b.analog.drives[name]}", on=bool(dstate.get(name))))
            if lv is not None and outs:
                first = outs[0]
                dv = _device_values.get(pid, {})
                marks = {}
                if isinstance(dv.get("threshold"), (int, float)):
                    marks["threshold"] = float(dv["threshold"])
                    if isinstance(dv.get("hysteresis"), (int, float)):
                        marks["− hysteresis"] = round(float(dv["threshold"]) - float(dv["hysteresis"]), 2)
                svg = analog_svg.render(list(lv.analog_history), first.name, first.unit, next(iter(b.analog.drives), ""), marks=marks)
    vals = {"analog_title": title, "analog_svg": svg}
    for k, v in vals.items():
        if getattr(room, k) != v:
            setattr(room, k, v)
    for k, v in (("analog_outputs", outs), ("analog_inputs", ins), ("analog_drives", drives)):
        if list(getattr(room, k)) != v:
            setattr(room, k, v)


def _publish_session(room: ProjectRoom, pid: str, sess: dict | None, b: board_mod.Board | None) -> None:
    lv = service.live(sess["id"]) if sess else None
    state = (lv.state if lv else sess["status"]) if sess else ""
    vals: dict[str, Any] = {
        "session_id": sess["id"] if sess else "",
        "session_state": state,
        "session_error": (lv.error if lv else sess.get("error", "")) if sess else "",
        "session_vt": _fmt_vt(lv.vt) if lv else "",
        "session_by": sess["started_by_name"] if sess else "",
        "evidence_session": last_evidence_session(pid),
    }
    for k, v in vals.items():
        if getattr(room, k) != v:
            setattr(room, k, v)
    leds, sensors, uart, bus = [], [], [], []
    total = 0
    if lv is not None and b is not None:
        plat = catalog.platform(b.platform)
        for name, on in sorted(lv.leds.items()):
            color = ""
            if name in b.leds:
                color = LED_COLORS.get(b.leds[name], "emerald")
            elif name.startswith("chip_"):
                color = "amber"
            leds.append(LedRow(name=name, on=bool(on), color=color or "emerald"))
        del plat
        for s in b.sensors:
            comp = catalog.component(s.part)
            for key, spec in (comp.get("settings") or {}).items():
                raw = (lv.sensors.get(s.name) or {}).get(spec["property"])
                value = (float(raw) / spec.get("scale", 1)) if raw is not None else float(s.initial.get(key, spec.get("default", 0)))
                driven = b.analog is not None and s.name in b.analog.sensors
                sensors.append(SensorRow(sensor=s.name, setting=key, title=f"{s.name} ({comp['title']})", value=round(value, 3),
                                         unit=spec.get("unit", ""), min=float(spec["min"]), max=float(spec["max"]), step=0.25, driven=driven))
        with lv.cond:
            evs = list(lv.events)
        total = lv.last_seq
        for e in evs:
            k = e.get("kind")
            if k == "uart":
                uart.append(UartRow(seq=int(e["seq"]), vt=_fmt_vt(e.get("vt")), dir="out", line=str(e.get("line", "")), event=str(e.get("device_event", ""))))
            elif k == "uart_in":
                uart.append(UartRow(seq=int(e["seq"]), vt=_fmt_vt(e.get("vt")), dir="in", line=str(e.get("line", ""))))
            elif k == "bus":
                bus.append(BusRow(seq=int(e["seq"]), vt=_fmt_vt(e.get("vt")), op="write" if e.get("op") == "w" else "read",
                                  register=str(e.get("register") or ""), offset=f"0x{int(e.get('offset', 0)):02x}",
                                  value=f"0x{int(e.get('value', 0)):08x} ({int(e.get('value', 0))})", via=str(e.get("via", ""))))
        uart, bus = uart[-UART_ROWS:], bus[-BUS_ROWS:]
        # The device's values: its last status reply or event.
        prof = lv.profile
        if prof is not None:
            for e in evs:
                if e.get("kind") == "uart" and e.get("fields") and e.get("device_event") in ("alarm", "normal"):
                    _device_values.setdefault(pid, {}).update(e["fields"])
    if list(room.leds) != leds:
        room.leds = leds
    if list(room.sensors) != sensors:
        room.sensors = sensors
    _publish_analog(room, pid, lv, b)
    if list(room.uart) != uart:
        room.uart = uart
    if list(room.bus) != bus:
        room.bus = bus
    if room.events_total != total:
        room.events_total = total
    tiles, alert = [], False
    prof = lv.profile if lv is not None else None
    if prof is not None:
        values = _device_values.get(pid, {})
        dash = prof.dashboard or {}
        units = {}
        for cmd in prof.commands.values():
            for f in cmd.fields:
                units[f.name or f.key] = f.unit
        alert_key = str(dash.get("alert", ""))
        for name in dash.get("show", []):
            v = values.get(name)
            tiles.append(TileRow(name=name, value=_fmt_value(v) if v is not None else "–", unit=units.get(name, ""), alert=name == alert_key and bool(v)))
        alert = bool(values.get(alert_key)) if alert_key else False
    if list(room.tiles) != tiles:
        room.tiles = tiles
    if room.alert != alert:
        room.alert = alert


EVERYTHING = {"project", "builds", "session", "tests", "proposals", "releases", "activity", "people"}


def push_later(project_id: str, *what: str) -> None:
    """``publish`` from outside the tabs' own events, gathered over PUSH_DELAY."""
    if not ids.is_project(project_id):
        return
    _pending.setdefault(project_id, set()).update(what or EVERYTHING)
    if project_id in _handles:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _handles[project_id] = loop.call_later(PUSH_DELAY, _start_push, project_id)


def _start_push(project_id: str) -> None:
    _handles.pop(project_id, None)
    what = _pending.pop(project_id, set())
    task = asyncio.create_task(push(project_id, what))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def push(project_id: str, what: set[str]) -> None:
    if registration is not None:
        from reflex_base.registry import RegistrationContext  # noqa: PLC0415

        RegistrationContext.set(registration)
    try:
        async with rooms.change(ProjectRoom, project_id) as room:
            publish(room, project_id, what)
    except Exception as e:  # noqa: BLE001
        logger.warning("Cannot show project %s to its people: %s", project_id, e)
