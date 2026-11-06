"""What an AI may do in Chipova Forge, offered to the AI Hub (reflex_ddns_auth.tools): the
blueprint's agent tools (12), as structured calls only — no shell, no Renode monitor.

The AI is one more member of the team with fewer rights: it reads everything, builds, starts and
operates simulations, injects the board's environment, runs tests and makes release packages; it
never edits files (it proposes, a person accepts or rejects), never decides on proposals and
never approves releases. Every call is in the project's audit log, refusals too. A "room" is a
project's id, as in /p/<id>. ``ctx.user`` is the person the AI works for.
"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any, Callable

import reflex as rx
from reflex_ddns_auth.tools import ToolContext, ask_first, tool

from chipova_forge import ids
from chipova_forge.agents import outputs
from chipova_forge.contract import contract as hdc
from chipova_forge.cosim import vcd
from chipova_forge.domain import roles
from chipova_forge.gateway import device as device_mod
from chipova_forge.jobs import builds
from chipova_forge.services import audit, projects, proposals, releases, systemtests
from chipova_forge.services.actors import Actor, agent
from chipova_forge.sessions import service
from chipova_forge.system import board as board_mod

ROOM = "The project's id, as in https://chipova-forge.reflex-ddns.com/p/<id> (forge.list_projects lists them)."
WAIT = 45.0

# Told (project id, the AI's actor, what it did) so the people on the project see it working.
activity: Callable[[str, Actor, str], None] | None = None

GUIDE = """\
Chipova Forge turns a finished chip (from Chipova: its RTL, pins, SystemRDL registers) into a
working virtual product: a board with an MCU and sensors, firmware, a simulation where the
firmware runs on Renode and the chip's real RTL runs in Verilator, a device API, and tests.

A project ("room") has: chip/ (read-only, a Chipova snapshot), hardware.yaml (the chip's contract:
its pins, interface, registers), system.yaml (the board), device.yaml (the device's commands, one
UART line each), firmware/*.c, tests/*.yaml.

- forge.get_project shows where each step stands. forge.read_file reads the project's files;
  the chip's own sources (chip/...) are Chipova's design, and reading one asks the person first.
- forge.read_output reads what a build made: for a PCB build "netlist" (every part, its value and
  each pin's net: what a part does), "bom.csv", "positions.csv", "erc.json", "drc.json"; any
  build's "log". Others ask the person first. When the person declines, work without it.
- You never edit files: forge.propose_change proposes changes; a person accepts or rejects
  (forge.proposal_status). A new or short file: its whole new text. A small change in a long file
  (firmware/main.c): edits, each {"old": lines that are in the file now, copied exactly, "new":
  what replaces them}; read the file first.
- forge.build builds 'registers', 'chip' (the RTL as a simulation library), 'firmware' or 'pcb'
  (the carrier module's schematic and two-layer PCB: placed, routed by Freerouting, checked by
  KiCad's ERC and DRC against the schematic, with Gerbers and a BOM); errors come back with
  file:line. An identical earlier build is reused. forge.get_build shows a PCB's checks.
- forge.start_simulation runs the board (it builds what's missing). Then: forge.device_command
  (the device's commands, as in device.yaml), forge.set_environment (e.g. temperature celsius;
  on a board with an analog side, sensor 'analog' sets its inputs, e.g. load or ambient: ngspice
  then drives the sensors, in step with the board),
  forge.read_register (the chip's registers by name), forge.read_events (UART lines, register
  transactions, LEDs, with virtual time), forge.record_waveform + forge.read_waveform (the chip's
  signals), forge.stop_simulation.
- forge.run_test runs a tests/*.yaml in a fresh simulation and tells you each step's result.
- Say only what the tools showed. A simulation passing is not a hardware qualification.
"""


def _actor(ctx: ToolContext) -> Actor:
    return agent("AI Hub", on_behalf_of=ctx.user or "")


def _project(room: str, actor: Actor, action: str = roles.VIEW) -> dict[str, Any]:
    room = (room or "").strip()
    if not ids.is_project(room):
        raise ValueError(f"there is no project {room!r}: forge.list_projects lists them")
    p, _ = projects.require(room, actor, action)
    return p


def _show(room: str, actor: Actor, what: str) -> None:
    if activity is not None:
        try:
            activity(room, actor, what)
        except Exception:  # noqa: BLE001
            pass


def _session(room: str, actor: Actor) -> dict[str, Any]:
    _project(room, actor)
    s = service.active_of(room)
    if s is None:
        raise ValueError("no simulation runs in this project: forge.start_simulation starts one")
    return s


# ------------------------------------------------------------------ reading


async def guide(ctx: ToolContext) -> dict:
    """Read this first: how Chipova Forge works for an AI, and what it may do."""
    return {"guide": GUIDE}


async def list_projects(ctx: ToolContext) -> dict:
    """The projects this AI may see, the most recently changed first."""
    out = []
    for p in projects.visible_to(_actor(ctx).on_behalf_of)[:100]:
        out.append({"room": p["id"], "title": p["title"], "template": p["template"], "chip": p["chip"].get("name", ""),
                    "interface": p["chip"].get("interface", "")})
    return {"projects": out}


async def get_project(ctx: ToolContext, room: str) -> dict:
    """A project's state, step by step: the chip, the contract's issues, the board's, the builds,
    the simulation, the tests, the AI's open proposals."""
    actor = _actor(ctx)
    p = _project(room, actor)
    c = projects.contract(p["id"])
    b = board_mod.from_yaml(projects.read(p["id"], "system.yaml"))
    sim = service.active_of(p["id"])
    tests = systemtests.latest_by_scenario(p["id"])
    return {
        "room": p["id"], "title": p["title"],
        "chip": {"name": c.chip.name, "top": c.chip.top_module, "snapshot": c.chip.snapshot, "interface": c.interface.protocol,
                 "pins": [{"port": x.port, "role": x.role, "width": x.width} for x in c.pins]},
        "contract_issues": [i.model_dump() for i in hdc.check(c)],
        "board": {"platform": b.platform, "interface": b.chip.interface, "sensors": [s.name for s in b.sensors], "issues": [i.model_dump() for i in board_mod.check(b, c)]},
        "files": [f["path"] for f in projects.files(p["id"])],
        "builds": {k: _build_brief(builds.latest(p["id"], k)) for k in builds.KINDS},
        "simulation": {"id": sim["id"], "state": sim["status"]} if sim else None,
        "tests": {path: {"status": r["status"], "run": r["id"]} for path, r in tests.items()},
        "open_proposals": [{"proposal": x["id"], "title": x["title"]} for x in proposals.of_project(p["id"]) if x["status"] == "PROPOSED"],
    }


def _build_brief(b: dict[str, Any] | None) -> dict[str, Any] | None:
    if b is None:
        return None
    s = b.get("summary") or {}
    out = {"build": b["id"], "kind": b["kind"], "status": b["status"], "error": b["error"],
           "problems": (s.get("problems") or [])[:20], "size": s.get("size"), "sync": (s.get("sync") or {}).get("status")}
    if b["kind"] == "pcb":
        ps, meta = s.get("pcb") or {}, s.get("meta") or {}
        erc, drc = ps.get("erc") or {}, ps.get("drc") or {}
        out["pcb"] = {"size_mm": [meta.get("width"), meta.get("height")], "parts": meta.get("parts"), "nets": meta.get("nets"),
                      "erc": {k: erc.get(k) for k in ("errors", "warnings")},
                      "drc": {k: drc.get(k) for k in ("errors", "warnings", "unconnected", "parity")},
                      "findings": [f"{v.get('severity')}: {v.get('description')}" for v in
                                   (erc.get("violations", []) + drc.get("violations", []) + drc.get("unconnected_items", []) + drc.get("parity_items", []))][:20],
                      "notes": meta.get("notes", [])}
    return out


async def read_file(ctx: ToolContext, room: str, path: str) -> dict:
    """A file of the project (hardware.yaml, system.yaml, device.yaml, firmware/..., tests/...). The
    chip's own sources (chip/...) are Chipova's design: reading one asks the person first."""
    actor = _actor(ctx)
    p = _project(room, actor)
    if ids.clean_path(path).startswith("chip/"):
        ask_first(ctx, f"read the chip's source {ids.clean_path(path)}",
                  "The chip comes from Chipova; its own design files aren't open to the AI unless you allow it. "
                  "With a cloud model, the file leaves this computer.")
    text = projects.read(p["id"], path)
    info = projects.file_info(p["id"], ids.clean_path(path))
    _show(p["id"], actor, f"reads {path}")
    return {"path": ids.clean_path(path), "text": text[:200_000], "version": info["version"] if info else 0}


async def get_registers(ctx: ToolContext, room: str) -> dict:
    """The chip's register map (from its SystemRDL): names, offsets, fields, access, reset values."""
    p = _project(room, _actor(ctx))
    m = await asyncio.to_thread(builds.register_map, p["id"])
    if m is None:
        raise ValueError("the chip has no register map")
    return m.to_dict()


# ------------------------------------------------------------------ proposing


async def propose_change(ctx: ToolContext, room: str, title: str, changes: list[dict] | dict | str, rationale: str = "") -> dict:
    """Proposes changes to some files for people to accept or reject (firmware/*.c|h, system.yaml,
    device.yaml, hardware.yaml, tests/*.yaml). Each change is {"path": ..., "content": the whole new
    text} or, for a small change in a long file, {"path": ..., "edits": [{"old": lines in the file
    now, copied exactly, "new": what replaces them}]}."""
    actor = _actor(ctx)
    p = _project(room, actor, roles.PROPOSE)
    if isinstance(changes, str):   # some models send the list as JSON text, often with raw line breaks in its strings
        try:
            changes = json.loads(changes, strict=False)
        except ValueError:
            raise ValueError('changes is a list: [{"path": ..., "content": ...}] or [{"path": ..., "edits": [...]}]') from None
    if isinstance(changes, dict):
        changes = [changes]
    x = proposals.propose(p["id"], actor, title, changes, rationale)
    _show(p["id"], actor, f"proposed “{x['title']}”")
    return {"proposal": x["id"], "files": [f["path"] for f in x["files"]],
            "next": "People see it on the project's page and accept or reject it; forge.proposal_status tells you which."}


async def proposal_status(ctx: ToolContext, room: str, proposal: str) -> dict:
    """What people decided about a proposal (and why, when they rejected it)."""
    p = _project(room, _actor(ctx))
    x = proposals.get(proposal)
    if x is None or x["project_id"] != p["id"]:
        raise ValueError(f"there is no proposal {proposal!r} in this project")
    return {"proposal": x["id"], "title": x["title"], "status": x["status"], "decided_by": x["decided_by_name"], "reason": x["reason"]}


# ------------------------------------------------------------------ building


async def build(ctx: ToolContext, room: str, kind: str) -> dict:
    """Builds 'registers', 'chip', 'firmware' or 'pcb' (waits up to 45 s). Errors come with file:line."""
    actor = _actor(ctx)
    p = _project(room, actor, roles.BUILD)
    _show(p["id"], actor, f"builds the {kind}")
    task = asyncio.ensure_future(builds.request(p["id"], kind, actor, "agent", wait=True))
    try:
        b = await asyncio.wait_for(asyncio.shield(task), WAIT)
    except asyncio.TimeoutError:
        return {"status": "RUNNING", "next": "forge.get_build tells you when it ends"}
    return {**(_build_brief(b) or {}), "reused": bool(b.get("reused"))}


async def get_build(ctx: ToolContext, room: str, build: str, lines: int = 80) -> dict:
    """A build's state, problems and the last lines of its output."""
    p = _project(room, _actor(ctx))
    b = builds.get(build)
    if b is None or b["project_id"] != p["id"]:
        raise ValueError(f"there is no build {build!r} in this project")
    tail = builds.log_text(b).splitlines()[-max(10, min(int(lines or 80), 1000)):]
    return {**(_build_brief(b) or {}), "log": "\n".join(tail), "outputs": outputs.names(b),
            "next": "forge.read_output reads one of the outputs"}


async def read_output(ctx: ToolContext, room: str, build: str, file: str) -> dict:
    """One of a build's outputs, as text: a PCB build's "netlist" (every part, its value and each
    pin's net), "bom.csv", "positions.csv", "erc.json", "drc.json"; any build's "log"; build is
    a build's id or a kind ("pcb", "firmware", "registers", "chip") for its latest good build.
    Outputs that aren't open to the AI ask the person first."""
    actor = _actor(ctx)
    p = _project(room, actor)
    b = builds.get(build) if build.startswith("bld-") else builds.latest(p["id"], build, builds.SUCCEEDED)
    if b is None or b["project_id"] != p["id"]:
        raise ValueError(f"there is no build {build!r} in this project: give a build's id, or pcb, firmware, registers or chip")
    name = ids.clean_path(file) if file not in ("log", "netlist") else file
    if not outputs.is_open(b["kind"], name):
        ask_first(ctx, f"read {name}, an output of the {b['kind']} build {b['id']}",
                  "It isn't one of the outputs open to the AI (those it can read are what the product's page shows).")
    text = await asyncio.to_thread(outputs.read, b, name)
    _show(p["id"], actor, f"reads {name} of the {b['kind']} build")
    return {"build": b["id"], "kind": b["kind"], "file": name, "text": text}


# ------------------------------------------------------------------ the simulation


async def start_simulation(ctx: ToolContext, room: str) -> dict:
    """Runs the project's board (building what is missing): waits until it runs (up to 45 s)."""
    actor = _actor(ctx)
    p = _project(room, actor, roles.SIMULATE)
    s = service.active_of(p["id"])
    if s is None:
        _show(p["id"], actor, "starts the simulation")
        s = await service.start(p["id"], actor, via="agent")
    lv = service.live(s["id"])
    if lv is not None:
        await asyncio.to_thread(service.wait_state, lv, ("RUNNING", "PAUSED"), WAIT)
    return await simulation_status(ctx, room)


async def simulation_status(ctx: ToolContext, room: str) -> dict:
    """The running simulation: its state, virtual time, LEDs, sensors and the device's latest events."""
    actor = _actor(ctx)
    s = _session(room, actor)
    lv = service.live(s["id"])
    if lv is None:
        return {"simulation": s["id"], "state": s["status"]}
    with lv.cond:
        recent = [e for e in lv.events if e.get("kind") in ("uart", "state")][-15:]
    out = {"simulation": s["id"], "state": lv.state, "error": lv.error, "virtual_time_s": round(lv.vt, 6), "leds": dict(lv.leds),
           "sensors": dict(lv.sensors), "recent": [{"seq": e["seq"], "kind": e["kind"], "line": e.get("line", e.get("state", "")),
                                                   "event": e.get("device_event", ""), "vt": e.get("vt")} for e in recent],
           "device_commands": list(lv.profile.commands) if lv.profile else []}
    if lv.board is not None and lv.board.analog is not None:
        out["analog"] = {"model": lv.board.analog.model, "step_ms": lv.board.analog.step_ms, **(lv.analog or {}),
                         "note": "ngspice runs this circuit in step with the board; set its inputs with forge.set_environment(sensor='analog')"}
    return out


async def device_command(ctx: ToolContext, room: str, command: str, args: dict | None = None) -> dict:
    """Sends one of the device's commands (device.yaml) and returns its answer."""
    actor = _actor(ctx)
    s = _session(room, actor)
    _show(room, actor, f"sends {command}")
    try:
        return await asyncio.to_thread(service.device_command, s["id"], actor, command, args or {})
    except device_mod.DeviceError as e:
        return {"refused": str(e)}


async def set_environment(ctx: ToolContext, room: str, sensor: str, setting: str, value: float) -> dict:
    """Sets a sensor of the virtual board (e.g. sensor 'temperature', setting 'celsius', 40), or,
    with sensor 'analog', an input of the board's analog side (e.g. setting 'load', 0 watts)."""
    actor = _actor(ctx)
    s = _session(room, actor)
    _show(room, actor, f"sets {sensor} {setting} to {value}")
    await asyncio.to_thread(service.set_environment, s["id"], actor, sensor, setting, value)
    return {"sensor": sensor, setting: value}


async def read_register(ctx: ToolContext, room: str, register: str) -> dict:
    """Reads one of the chip's registers by name (through its SPI or bus, as a probe would)."""
    actor = _actor(ctx)
    s = _session(room, actor)
    m = builds.register_map(room)
    r = m.by_name(register) if m else None
    if r is None:
        raise ValueError(f"the chip has no register {register!r} (forge.get_registers lists them)")
    out = await asyncio.to_thread(service.request, s["id"], actor, "read_reg", offset=r.offset)
    v = int(out["value"])
    fields = {f.name: (v & f.mask) >> f.lsb for f in r.fields}
    return {"register": r.name, "offset": r.offset, "value": v, "hex": f"0x{v:08x}", "fields": fields}


async def read_events(ctx: ToolContext, room: str, after: int = 0, kinds: list[str] | None = None, limit: int = 100) -> dict:
    """The simulation's events after a number: UART lines, the chip's register transactions (bus),
    LEDs, sensors, states; each with its virtual time."""
    actor = _actor(ctx)
    s = _session(room, actor)
    evs = service.events_after(s["id"], int(after or 0), 2000)
    if kinds:
        evs = [e for e in evs if e.get("kind") in kinds]
    evs = evs[: max(1, min(int(limit or 100), 500))]
    return {"events": [{k: v for k, v in e.items() if k != "wall"} for e in evs], "last": evs[-1]["seq"] if evs else after}


async def record_waveform(ctx: ToolContext, room: str) -> dict:
    """Records the chip's signals for the next moments (10k chip clocks): read them with forge.read_waveform."""
    actor = _actor(ctx)
    s = _session(room, actor)
    await asyncio.to_thread(service.request, s["id"], actor, "record")
    return {"recording": True}


async def read_waveform(ctx: ToolContext, room: str, signals: list[str] | None = None) -> dict:
    """How the chip's signals changed in what was recorded (time in ps, value; buses in hex)."""
    actor = _actor(ctx)
    s = _session(room, actor)
    data = await asyncio.to_thread(service.waveform, s["id"], actor)
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    f = Path(tempfile.mkstemp(suffix=".vcd")[1])
    f.write_bytes(data)
    try:
        w = await asyncio.to_thread(vcd.parse, f)
    finally:
        f.unlink(missing_ok=True)
    names = signals or [x.name for x in w.signals if x.name.count(".") <= 1][:20]
    return {"timescale": w.timescale, "end": w.end, "signals": vcd.summary(w, names)}


async def stop_simulation(ctx: ToolContext, room: str) -> dict:
    """Stops the running simulation (its events, logs and waveform are kept as evidence)."""
    actor = _actor(ctx)
    s = _session(room, actor)
    _show(room, actor, "stops the simulation")
    s = await service.stop(s["id"], actor, "stopped by an AI")
    return {"simulation": s["id"], "state": s["status"], "evidence": [e["name"] for e in (s.get("summary") or {}).get("evidence", [])]}


# ------------------------------------------------------------------ tests and releases


async def run_test(ctx: ToolContext, room: str, test: str) -> dict:
    """Runs a tests/*.yaml in a fresh simulation; each step's result (waits up to 45 s)."""
    actor = _actor(ctx)
    p = _project(room, actor, roles.TEST)
    _show(p["id"], actor, f"runs {test}")
    task = asyncio.ensure_future(systemtests.run(p["id"], test, actor, "agent"))
    try:
        r = await asyncio.wait_for(asyncio.shield(task), WAIT)
    except asyncio.TimeoutError:
        return {"status": "RUNNING", "next": "forge.test_result with the test's path tells you when it ends"}
    return _test_brief(r)


def _test_brief(r: dict[str, Any]) -> dict[str, Any]:
    return {"run": r["id"], "test": r["scenario"], "status": r["status"], "error": r["error"],
            "steps": [{"n": s["n"], "step": s["text"], "status": s["status"], "detail": s.get("detail", "")} for s in r["steps"]]}


async def test_result(ctx: ToolContext, room: str, test: str) -> dict:
    """The last run of a test (tests/<name>.yaml)."""
    p = _project(room, _actor(ctx))
    r = systemtests.latest_by_scenario(p["id"]).get(ids.clean_path(test))
    if r is None:
        raise ValueError(f"{test} never ran")
    return _test_brief(r)


async def release_checks(ctx: ToolContext, room: str) -> dict:
    """What a release would say now: each check (chip, contract, board, registers, builds, tests)."""
    p = _project(room, _actor(ctx))
    cs, _ = await asyncio.to_thread(releases.checks, p["id"])
    return {"ready": releases.ready(cs), "checks": cs}


async def export_release(ctx: ToolContext, room: str, title: str = "") -> dict:
    """Makes a release package (people approve it, never an AI)."""
    actor = _actor(ctx)
    p = _project(room, actor, roles.REQUEST_RELEASE)
    r = await asyncio.to_thread(releases.create, p["id"], title, actor)
    _show(p["id"], actor, f"made the release {r['id']}")
    return {"release": r["id"], "status": r["status"], "checks": r["checks"], "package_sha256": r["package"]}


def _audited(name: str, fn, writes: bool = False):  # noqa: ANN001, ANN202
    """Every call leaves a line in the audit log (refusals too)."""

    @functools.wraps(fn)
    async def wrapper(ctx: ToolContext, *args, **kwargs):  # noqa: ANN002, ANN003
        room = str(kwargs.get("room", ""))
        actor = _actor(ctx)
        try:
            result = await fn(ctx, *args, **kwargs)
        except projects.Denied:
            raise
        except Exception as e:
            audit.record(f"tool.{name}", actor, room if ids.is_project(room) else "", name, {k: v for k, v in kwargs.items() if k != "changes"},
                         decision="denied" if isinstance(e, (ValueError, projects.NotFound)) else "error", detail=f"{type(e).__name__}: {e}")
            raise
        audit.record(f"tool.{name}", actor, room if ids.is_project(room) else "", name, {k: v for k, v in kwargs.items() if k != "changes"})
        return result

    return wrapper


def register(app: rx.App) -> None:
    """Offers Forge's tools on ``app``'s backend."""
    t = lambda name, fn, title, kind="read", params=None: tool(app, name, title=title, kind=kind, params=params or {})(_audited(name, fn))  # noqa: E731
    t("forge.guide", guide, "How Chipova Forge works for an AI")
    t("forge.list_projects", list_projects, "List the projects")
    t("forge.get_project", get_project, "Look at a project, step by step", params={"room": ROOM})
    t("forge.read_file", read_file, "Read a file", params={"room": ROOM, "path": "The file's path, e.g. firmware/main.c, system.yaml, chip/rtl/..."})
    t("forge.get_registers", get_registers, "The chip's register map", params={"room": ROOM})
    t("forge.propose_change", propose_change, "Propose a change", "write", params={
        "room": ROOM, "title": "What the change does, in a few words.",
        "changes": "[{\"path\": ..., \"content\": the whole new text}] or, for a small change in a long file, "
                   "[{\"path\": ..., \"edits\": [{\"old\": lines in the file now, copied exactly, \"new\": what replaces them}]}]",
        "rationale": "Why, and how to check it."})
    t("forge.proposal_status", proposal_status, "What people decided", params={"room": ROOM, "proposal": "The proposal's id (prp-…)."})
    t("forge.read_output", read_output, "Read a build's output", params={
        "room": ROOM, "build": "A build's id (bld-…), or pcb, firmware, registers or chip for the latest good one.",
        "file": "netlist, bom.csv, positions.csv, erc.json, drc.json, log, … (forge.get_build lists them)."})
    t("forge.build", build, "Build", "write", params={"room": ROOM, "kind": "registers, chip, firmware or pcb."})
    t("forge.get_build", get_build, "Look at a build", params={"room": ROOM, "build": "The build's id (bld-…).", "lines": "How many of its output's last lines."})
    t("forge.start_simulation", start_simulation, "Start the simulation", "write", params={"room": ROOM})
    t("forge.simulation_status", simulation_status, "The simulation's state", params={"room": ROOM})
    t("forge.device_command", device_command, "Send a device command", "write", params={
        "room": ROOM, "command": "A command of device.yaml (forge.simulation_status lists them).", "args": "Its arguments, e.g. {\"celsius\": 35}."})
    t("forge.set_environment", set_environment, "Set a sensor's value", "write", params={
        "room": ROOM, "sensor": "The sensor's name in system.yaml.", "setting": "e.g. celsius.", "value": "The value in that setting's unit."})
    t("forge.read_register", read_register, "Read a chip register", "write", params={"room": ROOM, "register": "The register's name (forge.get_registers)."})
    t("forge.read_events", read_events, "Read the simulation's events", params={
        "room": ROOM, "after": "Only events after this number.", "kinds": "Only these kinds (uart, bus, led, sensor, state, pin).", "limit": "At most this many."})
    t("forge.record_waveform", record_waveform, "Record the chip's signals", "write", params={"room": ROOM})
    t("forge.read_waveform", read_waveform, "Read the chip's signals", params={"room": ROOM, "signals": "Signal names (default: the top's)."})
    t("forge.stop_simulation", stop_simulation, "Stop the simulation", "write", params={"room": ROOM})
    t("forge.run_test", run_test, "Run a system test", "write", params={"room": ROOM, "test": "tests/<name>.yaml"})
    t("forge.test_result", test_result, "A test's last result", params={"room": ROOM, "test": "tests/<name>.yaml"})
    t("forge.release_checks", release_checks, "What a release would say", params={"room": ROOM})
    t("forge.export_release", export_release, "Make a release package", "write", params={"room": ROOM, "title": "A title for it."})
