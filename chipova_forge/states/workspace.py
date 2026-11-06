"""One tab on a project (/p/<id>): which section it shows, the file open in its editor, and what
it asks of the project (builds, the simulation, the device, tests, releases, proposals).

What everybody sees change lives in the shared room (:mod:`.room`); this state holds the tab's
own things and calls the services, checking access through them (never trusting the page to
hide a button).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
import time
from pathlib import Path
from typing import Any

import reflex as rx

from chipova_forge import ids
from chipova_forge.contract import contract as hdc
from chipova_forge.cosim import vcd, wave_svg
from chipova_forge.domain import roles
from chipova_forge.gateway import api as api_mod
from chipova_forge.gateway import device as device_mod
from chipova_forge.jobs import builds
from chipova_forge.services import audit, downloads, presence, projects, proposals, releases, systemtests
from chipova_forge.services.actors import Actor
from chipova_forge.sessions import service
from chipova_forge.states.room import EVERYTHING, ProjectRoom, last_evidence_session, note, publish, push_later
from chipova_forge.states.ui_state import UiState
from chipova_forge.system import board as board_mod
from chipova_forge.system import catalog, diagram

VIEWS = ("overview", "chip", "registers", "board", "firmware", "lab", "dashboard", "tests", "release", "agents", "people")


@dataclasses.dataclass
class FileRow:
    path: str = ""
    version: int = 0
    size: int = 0
    by: str = ""
    editable: bool = True


@dataclasses.dataclass
class PortRow:
    port: str = ""
    direction: str = ""
    width: int = 1
    role: str = ""
    pins: str = ""


@dataclasses.dataclass
class PinRow:
    pin: int = 0
    signal: str = ""
    kind: str = ""
    pad: str = ""


@dataclasses.dataclass
class IssueRow:
    level: str = ""
    where: str = ""
    message: str = ""
    fix: str = ""


@dataclasses.dataclass
class RegRow:
    name: str = ""
    offset: str = ""
    access: str = ""
    reset: str = ""
    title: str = ""
    fields: str = ""
    desc: str = ""


@dataclasses.dataclass
class ProblemRow:
    file: str = ""
    line: int = 0
    severity: str = ""
    message: str = ""


@dataclasses.dataclass
class PinUseRow:
    pin: str = ""
    use: str = ""


@dataclasses.dataclass
class CommandRow:
    name: str = ""
    title: str = ""
    args: str = ""


@dataclasses.dataclass
class StepResultRow:
    n: int = 0
    text: str = ""
    status: str = ""
    detail: str = ""
    vt: str = ""
    seconds: str = ""


@dataclasses.dataclass
class CheckRow:
    name: str = ""
    title: str = ""
    status: str = ""
    detail: str = ""


@dataclasses.dataclass
class MemberRow:
    user: str = ""
    role: str = ""
    added_by: str = ""
    me: bool = False


@dataclasses.dataclass
class AuditRow:
    at: float = 0.0
    who: str = ""
    kind: str = ""
    action: str = ""
    target: str = ""
    decision: str = ""
    detail: str = ""


@dataclasses.dataclass
class LinkRow:
    name: str = ""
    url: str = ""
    size: str = ""


@dataclasses.dataclass
class DiffRow:
    path: str = ""
    old: str = ""
    new: str = ""
    added: int = 0
    removed: int = 0


@dataclasses.dataclass
class DiffLineRow:
    """One line of a proposal's diff: file (its path), hunk (@@ where), add, del or ctx."""

    kind: str = "ctx"
    text: str = ""
    old_no: str = ""
    new_no: str = ""


def diff_lines(path: str, old: str, new: str) -> tuple[list[DiffLineRow], int, int]:
    """The changed lines of one file with three lines around each change, numbered."""
    import difflib  # noqa: PLC0415
    import re  # noqa: PLC0415

    out = [DiffLineRow(kind="file", text=path)]
    added = removed = 0
    a, b = old.splitlines(), new.splitlines()
    o = n = 0
    for line in difflib.unified_diff(a, b, lineterm="", n=3):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
            o, n = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            out.append(DiffLineRow(kind="hunk", text=line))
        elif line.startswith("+"):
            out.append(DiffLineRow(kind="add", text=line[1:], new_no=str(n)))
            n += 1
            added += 1
        elif line.startswith("-"):
            out.append(DiffLineRow(kind="del", text=line[1:], old_no=str(o)))
            o += 1
            removed += 1
        else:
            out.append(DiffLineRow(kind="ctx", text=line[1:], old_no=str(o), new_no=str(n)))
            o += 1
            n += 1
    if len(out) == 1:
        out.append(DiffLineRow(kind="hunk", text="(no change)"))
    return out, added, removed


def _size(n: int) -> str:
    return f"{n} B" if n < 2048 else (f"{n / 1024:.1f} KB" if n < 2 << 20 else f"{n / 1048576:.1f} MB")


LANGS = {".c": "c", ".h": "c", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".sv": "systemverilog", ".v": "verilog",
         ".rdl": "plaintext", ".toml": "ini", ".csv": "plaintext"}


def _lang(path: str) -> str:
    return LANGS.get(Path(path).suffix, "plaintext")


BUILD_NAMES = {"registers": "Registers", "chip": "Chip", "firmware": "Firmware", "pcb": "Schematic & PCB"}
PCB_PICTURES = (("top", "board-top.svg"), ("bottom", "board-bottom.svg"), ("schematic", "schematic.svg"))
PCB_FILES = ("gerbers.zip", "kicad.zip", "bom.csv", "positions.csv", "schematic.pdf")


class WorkspaceState(rx.State):
    project_id: str = ""
    missing: bool = False
    denied: bool = False
    role: str = ""
    my_user: str = ""
    can_edit: bool = False
    can_build: bool = False
    can_simulate: bool = False
    can_operate: bool = False
    can_test: bool = False
    can_decide: bool = False
    can_release: bool = False
    can_approve: bool = False
    can_manage: bool = False
    view: str = "overview"
    busy: str = ""
    # Files and the editor
    files: list[FileRow] = []
    chip_files: list[str] = []
    open_path: str = ""
    editor_key: str = ""
    editor_initial: str = ""
    editor_text: str = ""
    editor_version: int = 0
    editor_lang: str = "c"
    editor_readonly: bool = False
    new_file_open: bool = False
    new_file_path: str = ""
    # The chip
    ports: list[PortRow] = []
    pinmap: list[PinRow] = []
    contract_issues: list[IssueRow] = []
    board_issues: list[IssueRow] = []
    package_text: str = ""
    electrical_text: str = ""
    # Registers
    registers: list[RegRow] = []
    reg_sync: str = ""
    reg_sync_detail: str = ""
    reg_build: str = ""
    reg_downloads: list[LinkRow] = []
    fw_downloads: list[LinkRow] = []
    evidence: list[LinkRow] = []
    release_links: dict[str, str] = {}
    # The board
    board_svg: str = ""
    pin_use: list[PinUseRow] = []
    # Its schematic and PCB (the latest pcb build)
    pcb_build_id: str = ""
    pcb_status: str = ""
    pcb_line: str = ""
    pcb_view: str = "top"
    pcb_pictures: dict[str, str] = {}
    pcb_downloads: list[LinkRow] = []
    pcb_checks: list[IssueRow] = []
    pcb_notes: list[str] = []
    # A build's output
    log_kind: str = ""
    log_text: str = ""
    problems: list[ProblemRow] = []
    # The lab
    uart_input: str = ""
    slider_values: dict[str, float] = {}
    reg_name: str = ""
    reg_value_input: str = ""
    reg_result: str = ""
    wave_svg: str = ""
    wave_info: str = ""
    # The dashboard
    commands: list[CommandRow] = []
    dash_message: str = ""
    dash_control_value: dict[str, float] = {}
    sdk_snippet: str = ""
    # Tests
    test_run_id: str = ""
    test_title: str = ""
    test_status: str = ""
    test_error: str = ""
    test_steps: list[StepResultRow] = []
    # Release
    checks: list[CheckRow] = []
    # Agents and the API
    audit_rows: list[AuditRow] = []
    api_token: str = ""
    proposal_id: str = ""
    proposal_diff: list[DiffRow] = []
    proposal_lines: list[DiffLineRow] = []
    proposal_title: str = ""
    proposal_why: str = ""
    reject_reason: str = ""
    # People
    link_access: str = "editor"
    members: list[MemberRow] = []
    member_email: str = ""
    member_role: str = "reviewer"
    _ui_me: dict[str, str] = {}

    # -------------------------------------------------------------- who and where

    async def _actor(self) -> Actor:
        ui = await self.get_state(UiState)
        return ui.actor()

    @rx.event
    async def on_load_project(self):
        pid = str(self.router._page.params.get("project_id", "") or "")
        ui = await self.get_state(UiState)
        actor = await ui._identify()
        self._ui_me = dict(ui.me)
        view = str(self.router._page.params.get("view", "") or "")
        project = projects.get(pid)
        room = await self.get_state(ProjectRoom)
        if project is None:
            self.missing, self.denied = True, False
            return await self._leave(room)
        access = projects.access_of(project, actor)
        if not access.can(roles.VIEW):
            audit.denied(roles.VIEW, actor, pid, pid, "opened a project without access")
            self.missing, self.denied = False, True
            return await self._leave(room)
        changed = self.project_id != pid
        self.missing = self.denied = False
        self.project_id = pid
        self.role = access.label
        self.my_user = actor.user
        self.can_edit = access.can(roles.EDIT)
        self.can_build = access.can(roles.BUILD)
        self.can_simulate = access.can(roles.SIMULATE)
        self.can_operate = access.can(roles.OPERATE)
        self.can_test = access.can(roles.TEST)
        self.can_decide = access.can(roles.DECIDE_PROPOSAL)
        self.can_release = access.can(roles.REQUEST_RELEASE)
        self.can_approve = access.can(roles.APPROVE_RELEASE)
        self.can_manage = access.can(roles.MANAGE)
        if changed:
            self.view = view if view in VIEWS else "overview"
            self.open_path = ""
            self.api_token = ""
        elif view in VIEWS:
            self.view = view
        linked = await room._link_to(pid)
        linked.project_id = pid
        linked.gone = False
        publish(linked, pid, EVERYTHING)
        self._load_view(pid)
        return [WorkspaceState.join_room, rx.call_script(f"document.title = {json.dumps(project['title'] + ' · Chipova Forge')}")]

    async def _leave(self, room: ProjectRoom):
        left = presence.leave(self.router.session.client_token)
        if left:
            push_later(left, "people")
        self.project_id = ""
        try:
            return await room._unlink()
        except Exception:  # noqa: BLE001
            return None

    @rx.event
    async def leave_project(self):
        self.missing = self.denied = False
        return await self._leave(await self.get_state(ProjectRoom))

    @rx.event
    async def join_room(self):
        ui = await self.get_state(UiState)
        pid = self.project_id
        if not pid:
            return
        me = ui.me
        left, color = presence.join(pid, ui.router.session.client_token, me["peer"], me["user"], me["name"], me.get("avatar", ""))
        ui._set_color(color)
        presence.prune(pid)
        room = await self.get_state(ProjectRoom)
        publish(room, pid, {"people"})
        note(pid, me["name"], "opened the project", "people")
        if left:
            push_later(left, "people")

    @rx.event
    def set_view(self, view: str):
        if view in VIEWS:
            self.view = view
            self._load_view(self.project_id)

    def _load_view(self, pid: str) -> None:
        if not pid:
            return
        v = self.view
        if v in ("overview", "chip", "board", "registers", "firmware", "tests"):
            self._load_files(pid)
        if v in ("chip", "overview"):
            self._load_chip(pid)
        if v in ("registers", "lab"):
            # The Lab's register probe lists them too.
            self._load_registers(pid)
        if v == "board":
            self._load_board(pid)
            self._load_pcb(pid)
        if v == "firmware":
            fb = builds.latest(pid, "firmware", builds.SUCCEEDED)
            self.fw_downloads = self._links(pid, fb) if fb else []
        if v == "lab":
            self._load_evidence(pid)
        if v == "release":
            self._load_release_links(pid)
        if v == "firmware" and not self.open_path.startswith("firmware/"):
            fw = [f.path for f in self.files if f.path.startswith("firmware/") and f.path.endswith(".c")]
            self._open(pid, fw[0] if fw else "system.yaml")
        if v == "board" and not self.open_path:
            self._open(pid, "system.yaml")
        if v in ("lab", "dashboard"):
            self._load_device(pid)
        if v == "release":
            self._load_checks(pid)
        if v == "agents":
            self._load_audit(pid)
        if v == "people":
            self._load_people(pid)

    # -------------------------------------------------------------- files

    def _load_files(self, pid: str) -> None:
        rows = []
        for f in projects.files(pid):
            rows.append(FileRow(path=f["path"], version=f["version"], size=f["size"], by=f["updated_by_name"], editable=projects.editable(f["path"])))
        self.files = rows
        try:
            self.chip_files = sorted(p for p in projects.chip(pid).files)
        except Exception:  # noqa: BLE001
            self.chip_files = []

    def _open(self, pid: str, path: str) -> None:
        try:
            text = projects.read(pid, path)
        except (projects.NotFound, ValueError):
            return
        info = projects.file_info(pid, path)
        self.open_path = path
        self.editor_initial = text
        self.editor_text = text
        self.editor_version = info["version"] if info else 0
        self.editor_lang = _lang(path)
        self.editor_readonly = not (self.can_edit and projects.editable(path))
        self.editor_key = f"{path}@{self.editor_version}@{time.time():.3f}"

    @rx.event
    def open_file(self, path: str):
        self._open(self.project_id, path)

    @rx.event
    def set_editor_text(self, text: str):
        self.editor_text = text

    @rx.var
    def editor_dirty(self) -> bool:
        return self.editor_text != self.editor_initial

    @rx.event
    async def save_file(self):
        actor = await self._actor()
        try:
            v = projects.write(self.project_id, self.open_path, self.editor_text, actor, self.editor_version)
        except (projects.Denied, projects.Conflict, ValueError) as e:
            return rx.toast.error(str(e))
        self.editor_version = v
        self.editor_initial = self.editor_text
        note(self.project_id, actor.name, f"saved {self.open_path}", "edit")
        push_later(self.project_id, "project", "builds", "tests")
        self._load_files(self.project_id)
        if self.open_path in ("hardware.yaml", "system.yaml"):
            self._load_chip(self.project_id)
            self._load_board(self.project_id)
        return rx.toast.success(f"Saved {self.open_path} (version {v})")

    @rx.event
    def reload_file(self):
        self._open(self.project_id, self.open_path)

    @rx.event
    def set_new_file_open(self, value: bool):
        self.new_file_open = value
        if value:
            self.new_file_path = "firmware/" if self.view == "firmware" else ("tests/" if self.view == "tests" else "")

    @rx.event
    def set_new_file_path(self, value: str):
        self.new_file_path = value

    @rx.event
    async def create_file(self):
        actor = await self._actor()
        path = self.new_file_path.strip()
        try:
            path = ids.clean_path(path)
            if projects.file_info(self.project_id, path) is not None:
                raise ValueError(f"{path} exists already")
            starter = ""
            if path.endswith(".yaml") and path.startswith("tests/"):
                starter = "name: A new test\nsteps:\n  - command: {ping: {}}\n"
            elif path.endswith(".h"):
                guard = Path(path).stem.upper() + "_H"
                starter = f"#ifndef {guard}\n#define {guard}\n\n#endif\n"
            projects.write(self.project_id, path, starter, actor, None)
        except (projects.Denied, ValueError) as e:
            return rx.toast.error(str(e))
        self.new_file_open = False
        self._load_files(self.project_id)
        self._open(self.project_id, path)
        push_later(self.project_id, "tests")
        return rx.toast.success(f"Made {path}")

    # -------------------------------------------------------------- the chip, registers, board

    def _load_chip(self, pid: str) -> None:
        try:
            c = projects.contract(pid)
        except Exception as e:  # noqa: BLE001
            self.contract_issues = [IssueRow(level="error", where="hardware.yaml", message=str(e))]
            return
        pins_of: dict[str, list[str]] = {}
        if c.package:
            for e in c.package.pinmap:
                if e.signal:
                    pins_of.setdefault(e.signal.split("[")[0], []).append(str(e.pin))
            self.pinmap = [PinRow(pin=e.pin, signal=e.signal, kind=e.kind, pad=e.pad) for e in c.package.pinmap]
            self.package_text = f"{c.package.type} · {c.package.pins} pins · {' × '.join(f'{x:g}' for x in c.package.body_mm)} mm · pitch {c.package.pitch_mm:g} mm"
        else:
            self.pinmap, self.package_text = [], "no package plan"
        self.ports = [PortRow(port=p.port, direction=p.direction, width=p.width, role=p.role, pins=", ".join(pins_of.get(p.port, []))) for p in c.pins]
        self.contract_issues = [IssueRow(**i.model_dump()) for i in hdc.check(c)]
        e = c.electrical
        self.electrical_text = f"VDD {e.vdd:g} V ({e.vdd_min:g}–{e.vdd_max:g}) · I/O {e.io_voltage:g} V" + (f" · clock ≤ {e.max_clock_hz / 1e6:g} MHz" if e.max_clock_hz else "") + f" · from {e.source}"

    def _load_registers(self, pid: str) -> None:
        m = builds.register_map(pid)
        rows = []
        if m is not None:
            for r in m.registers:
                reset = 0
                have_reset = False
                for f in r.fields:
                    if f.reset is not None:
                        reset |= f.reset << f.lsb
                        have_reset = True
                fields = ", ".join(f"{f.name}[{f.msb}:{f.lsb}] {f.sw}{(' ' + f.onwrite) if f.onwrite else ''}" if f.msb != f.lsb else f"{f.name}[{f.lsb}] {f.sw}{(' ' + f.onwrite) if f.onwrite else ''}" for f in r.fields)
                rows.append(RegRow(name=r.name, offset=f"0x{r.offset:02x}", access=r.access, reset=f"0x{reset:x}" if have_reset else "–",
                                   title=r.title, fields=fields, desc=r.desc or " ".join(f.desc for f in r.fields if f.desc)))
        self.registers = rows
        b = builds.latest(pid, "registers", builds.SUCCEEDED)
        if b is not None:
            sync = (b.get("summary") or {}).get("sync") or {}
            self.reg_sync, self.reg_sync_detail, self.reg_build = sync.get("status", ""), sync.get("detail", ""), b["id"]
            self.reg_downloads = self._links(pid, b)
        else:
            self.reg_sync, self.reg_sync_detail, self.reg_build = "", "", ""
            self.reg_downloads = []

    def _links(self, pid: str, b: dict[str, Any]) -> list[LinkRow]:
        actor = self._actor_now()
        return [LinkRow(name=a["name"], url=downloads.link(pid, "build", b["id"], a["name"], actor), size=_size(a["size"]))
                for a in builds.artifacts(b["id"])]

    def _load_evidence(self, pid: str) -> None:
        """Download links (signed for this tab's person) to the last ended simulation's evidence."""
        sid = last_evidence_session(pid)
        s = service.get(sid) if sid else None
        actor = self._actor_now()
        self.evidence = ([LinkRow(name=e["name"], url=downloads.link(pid, "session", sid, e["name"], actor), size=_size(e["size"]))
                          for e in (s.get("summary") or {}).get("evidence", [])] if s else [])

    @rx.event
    def load_evidence(self):
        if self.project_id:
            self._load_evidence(self.project_id)

    def _load_release_links(self, pid: str) -> None:
        actor = self._actor_now()
        self.release_links = {r["id"]: downloads.link(pid, "release", r["id"], "package.zip", actor) for r in releases.of_project(pid, 20)}

    @rx.event
    def load_release_links(self):
        if self.project_id:
            self._load_release_links(self.project_id)
            self._load_checks(self.project_id)

    def _actor_now(self) -> Actor:
        """This tab's person as last identified (UiState keeps it): for links made in sync code."""
        me = self._ui_me
        return Actor(me.get("user", ""), me.get("name", ""), email=me.get("email", ""))

    def _load_pcb(self, pid: str) -> None:
        """The latest schematic and PCB build: its pictures, checks and files (links signed for
        this tab's person)."""
        b = builds.latest(pid, "pcb")
        self.pcb_pictures, self.pcb_downloads, self.pcb_checks, self.pcb_notes = {}, [], [], []
        if b is None:
            self.pcb_build_id, self.pcb_status = "", ""
            try:
                p = builds.plan(pid, "pcb")
                m = p.meta
                self.pcb_line = f"Not built yet: {m['parts']} parts and {m['nets']} nets on a {m['width']} × {m['height']} mm module."
                self.pcb_checks = [IssueRow(level=i["level"], where="netlist", message=i["message"]) for i in m.get("issues", [])]
            except builds.BuildError as e:
                self.pcb_status, self.pcb_line = "NOT_CONFIGURED", str(e)
            return
        self.pcb_build_id, self.pcb_status = b["id"], b["status"]
        s = b.get("summary") or {}
        meta, ps = s.get("meta") or {}, s.get("pcb") or {}
        erc, drc = ps.get("erc") or {}, ps.get("drc") or {}
        if b["status"] == builds.SUCCEEDED:
            self.pcb_line = (f"{meta.get('width', 0)} × {meta.get('height', 0)} mm, 2 layers · {meta.get('parts', 0)} parts · {meta.get('nets', 0)} nets"
                             f" · ERC {erc.get('errors', '?')} errors, {erc.get('warnings', '?')} warnings"
                             f" · DRC {drc.get('errors', '?')} errors, {drc.get('warnings', '?')} warnings, {drc.get('unconnected', '?')} unconnected,"
                             f" {drc.get('parity', '?')} differences from the schematic")
        else:
            self.pcb_line = b.get("error") or b["status"].lower()
        actor = self._actor_now()
        names = {a["name"]: a for a in builds.artifacts(b["id"])}
        self.pcb_pictures = {k: downloads.link(pid, "build", b["id"], f, actor) for k, f in PCB_PICTURES if f in names}
        if self.pcb_view not in self.pcb_pictures and self.pcb_pictures:
            self.pcb_view = next(iter(self.pcb_pictures))
        self.pcb_downloads = [LinkRow(name=f, url=downloads.link(pid, "build", b["id"], f, actor), size=_size(names[f]["size"]))
                              for f in PCB_FILES if f in names]
        rows = [IssueRow(level=i["level"], where="netlist", message=i["message"]) for i in meta.get("issues", [])]
        for where, vs in (("ERC", erc.get("violations", [])), ("DRC", drc.get("violations", [])),
                          ("unconnected", drc.get("unconnected_items", [])), ("parity", drc.get("parity_items", []))):
            for v in vs:
                level = "error" if v.get("severity") == "error" or where in ("unconnected", "parity") else "warning"
                rows.append(IssueRow(level=level, where=f"{where}: {v.get('type', '')}", message=v.get("description", ""),
                                     fix="; ".join(v.get("items", []))))
        self.pcb_checks = rows
        self.pcb_notes = list(meta.get("notes", []))

    @rx.event
    def load_pcb(self):
        if self.project_id:
            self._load_pcb(self.project_id)

    @rx.event
    def set_pcb_view(self, view: str):
        self.pcb_view = view

    def _load_board(self, pid: str) -> None:
        try:
            c = projects.contract(pid)
            b = board_mod.from_yaml(projects.read(pid, "system.yaml"))
        except Exception as e:  # noqa: BLE001
            self.board_issues = [IssueRow(level="error", where="system.yaml", message=str(e))]
            self.board_svg = ""
            return
        self.board_issues = [IssueRow(**i.model_dump()) for i in board_mod.check(b, c)]
        try:
            self.board_svg = diagram.render(b, c)
        except Exception as e:  # noqa: BLE001
            self.board_svg = ""
            self.board_issues = [*self.board_issues, IssueRow(level="warning", where="diagram", message=str(e))]
        p = catalog.platform(b.platform)
        use: list[PinUseRow] = []
        uart = p["uarts"].get(b.console, {})
        if uart:
            use += [PinUseRow(pin=uart["tx"], use=f"{b.console} TX (console)"), PinUseRow(pin=uart["rx"], use=f"{b.console} RX (console)")]
        if c.interface.protocol == "spi" and b.chip.interface in p.get("spi", {}):
            spi = p["spi"][b.chip.interface]
            use += [PinUseRow(pin=spi["sck"], use=f"{b.chip.interface} SCK → chip {c.interface.ports.get('sclk', '')}"),
                    PinUseRow(pin=spi["mosi"], use=f"{b.chip.interface} MOSI → chip {c.interface.ports.get('mosi', '')}"),
                    PinUseRow(pin=spi["miso"], use=f"{b.chip.interface} MISO ← chip {c.interface.ports.get('miso', '')}"),
                    PinUseRow(pin=b.chip.chip_select, use=f"GPIO → chip {c.interface.ports.get('cs_n', '')}")]
        for chip_pin, mcu_pin in b.chip.irq.items():
            use.append(PinUseRow(pin=mcu_pin, use=f"EXTI ← chip {chip_pin}"))
        for s in b.sensors:
            bus = p.get("i2c", {}).get(s.bus, {})
            use += [PinUseRow(pin=bus.get("scl", ""), use=f"{s.bus} SCL → {s.name}"), PinUseRow(pin=bus.get("sda", ""), use=f"{s.bus} SDA ↔ {s.name}")]
        for name, led in b.leds.items():
            use.append(PinUseRow(pin=p["leds"][led]["pin"], use=f"{led} LED ({name})"))
        self.pin_use = sorted(use, key=lambda r: (r.pin[:2], int(r.pin[2:]) if r.pin[2:].isdigit() else 0))

    # -------------------------------------------------------------- builds

    @rx.event(background=True)
    async def request_build(self, kind: str):
        async with self:
            pid = self.project_id
            actor = await self._actor()
            self.busy = f"build:{kind}"
        try:
            b = await builds.request(pid, kind, actor, "ui", wait=True)
            name = BUILD_NAMES.get(kind, kind.capitalize())
            msg = (rx.toast.success(f"{name}: {'up to date (reused)' if b.get('reused') else 'built'}")
                   if b["status"] == builds.SUCCEEDED else rx.toast.error(f"{name} failed: {b.get('error') or 'see the log'}"))
            note(pid, actor.name, f"built the {kind} ({b['status'].lower()})", "build")
        except (builds.BuildError, projects.Denied) as e:
            msg = rx.toast.error(str(e))
        async with self:
            self.busy = ""
            if self.view == "registers":
                self._load_registers(pid)
            if self.view == "board" and kind == "pcb":
                self._load_pcb(pid)
            if self.log_kind == kind:
                self._load_log(pid, kind)
        push_later(pid, "builds")
        return msg

    def _load_log(self, pid: str, kind: str) -> None:
        b = builds.latest(pid, kind)
        self.log_kind = kind
        if b is None:
            self.log_text, self.problems = "Not built yet.", []
            return
        self.log_text = builds.log_text(b, 60_000) or (b.get("error") or "")
        self.problems = [ProblemRow(**p) for p in (b.get("summary") or {}).get("problems", [])]

    @rx.event
    def show_log(self, kind: str):
        if self.log_kind == kind and self.log_text:
            self.log_kind, self.log_text, self.problems = "", "", []
            return
        self._load_log(self.project_id, kind)

    @rx.event
    def jump_to_problem(self, file: str, line: int):
        if file.startswith("firmware/"):
            self._open(self.project_id, file)
            return rx.call_script(f"window.__forgeReveal && window.__forgeReveal({int(line)})")
        return None

    # -------------------------------------------------------------- the simulation

    @rx.event(background=True)
    async def start_session(self):
        async with self:
            pid = self.project_id
            actor = await self._actor()
            self.busy = "session"
        try:
            s = await service.start(pid, actor, "ui")
            note(pid, actor.name, "started the simulation", "session")
            msg = rx.toast.success(f"Simulation {s['id']} is starting")
        except (service.SessionError, projects.Denied, builds.BuildError) as e:
            msg = rx.toast.error(str(e))
        async with self:
            self.busy = ""
            self._load_device(pid)
        push_later(pid, "session", "builds")
        return msg

    @rx.event(background=True)
    async def stop_session(self):
        async with self:
            pid = self.project_id
            actor = await self._actor()
            self.busy = "session"
        room_sid = ""
        s = service.active_of(pid)
        if s is not None:
            room_sid = s["id"]
        try:
            if room_sid:
                await service.stop(room_sid, actor, f"stopped by {actor.name}")
                note(pid, actor.name, "stopped the simulation", "session")
            msg = rx.toast.success("Stopped: its events, logs and waveform are kept")
        except (service.SessionError, projects.Denied) as e:
            msg = rx.toast.error(str(e))
        async with self:
            self.busy = ""
        push_later(pid, "session")
        return msg

    async def _op(self, op: str, **args: Any) -> dict[str, Any] | None:
        actor = await self._actor()
        s = service.active_of(self.project_id)
        if s is None:
            raise service.SessionError("no simulation runs: start one")
        return await asyncio.to_thread(service.request, s["id"], actor, op, **args)

    @rx.event
    async def control(self, op: str):
        if op not in ("pause", "start", "reset"):
            return None
        try:
            await self._op(op)
        except (service.SessionError, projects.Denied) as e:
            return rx.toast.error(str(e))
        actor = await self._actor()
        note(self.project_id, actor.name, {"pause": "paused", "start": "resumed", "reset": "reset"}[op] + " the board", "session")
        push_later(self.project_id, "session")
        return None

    @rx.event
    def set_uart_input(self, value: str):
        self.uart_input = value

    @rx.event
    async def send_uart(self):
        text = self.uart_input.strip()
        if not text:
            return None
        try:
            uart = "console"
            await self._op("uart_send", uart=uart, text=text)
        except (service.SessionError, projects.Denied) as e:
            return rx.toast.error(str(e))
        self.uart_input = ""
        return None

    @rx.event
    async def uart_key(self, key: str):
        if key == "Enter":
            return await self.send_uart()
        return None

    @rx.event
    async def set_sensor(self, sensor: str, setting: str, value: str):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return rx.toast.error("a number")
        actor = await self._actor()
        s = service.active_of(self.project_id)
        if s is None:
            return rx.toast.error("no simulation runs: start one")
        try:
            await asyncio.to_thread(service.set_environment, s["id"], actor, sensor, setting, v)
        except (service.SessionError, projects.Denied) as e:
            return rx.toast.error(str(e))
        note(self.project_id, actor.name, f"set {sensor} to {v:g}", "session")
        push_later(self.project_id, "session")
        return None

    @rx.event
    def set_slider(self, key: str, value: str):
        try:
            self.slider_values = {**self.slider_values, key: float(value)}
        except (TypeError, ValueError):
            pass

    @rx.event
    async def commit_slider(self, sensor: str, setting: str):
        key = f"{sensor}.{setting}"
        if key not in self.slider_values:
            return None
        return await self.set_sensor(sensor, setting, str(self.slider_values[key]))

    @rx.event
    def set_reg_name(self, value: str):
        self.reg_name = value

    @rx.event
    def set_reg_value_input(self, value: str):
        self.reg_value_input = value

    @rx.event
    async def read_register(self):
        m = builds.register_map(self.project_id)
        r = m.by_name(self.reg_name) if m else None
        if r is None:
            return rx.toast.error("pick a register")
        try:
            out = await self._op("read_reg", offset=r.offset)
        except (service.SessionError, projects.Denied) as e:
            return rx.toast.error(str(e))
        v = int(out["value"]) if out else 0
        fields = ", ".join(f"{f.name}={(v & f.mask) >> f.lsb}" for f in r.fields)
        self.reg_result = f"{r.name} = 0x{v:08x} ({v}) · {fields}"
        return None

    @rx.event
    async def write_register(self):
        m = builds.register_map(self.project_id)
        r = m.by_name(self.reg_name) if m else None
        if r is None:
            return rx.toast.error("pick a register")
        try:
            v = int(self.reg_value_input.strip(), 0)
            await self._op("write_reg", offset=r.offset, value=v)
        except ValueError:
            return rx.toast.error("a value: decimal or 0x hexadecimal")
        except (service.SessionError, projects.Denied) as e:
            return rx.toast.error(str(e))
        self.reg_result = f"wrote 0x{v:08x} to {r.name}"
        return None

    @rx.event
    async def record_wave(self):
        try:
            await self._op("record")
        except (service.SessionError, projects.Denied) as e:
            return rx.toast.error(str(e))
        return rx.toast.success("Recording the chip's signals for the next 10,000 clocks")

    @rx.event(background=True)
    async def show_wave(self):
        async with self:
            pid = self.project_id
            actor = await self._actor()
        s = service.active_of(pid) or next(iter(service.of_project(pid, 1)), None)
        if s is None:
            return rx.toast.error("no simulation yet")
        try:
            data = await asyncio.to_thread(service.waveform, s["id"], actor)
            f = Path(tempfile.mkstemp(suffix=".vcd")[1])
            f.write_bytes(data)
            try:
                w = await asyncio.to_thread(vcd.parse, f)
            finally:
                f.unlink(missing_ok=True)
        except (service.SessionError, projects.NotFound, projects.Denied, OSError) as e:
            return rx.toast.error(str(e))
        if not w.signals or w.end == 0:
            return rx.toast.error("nothing recorded yet: record first")
        tops = [x.name for x in w.signals if x.name.count(".") == 1]
        inner = [x.name for x in w.signals if x.name.count(".") == 2 and any(k in x.name for k in ("psel", "penable", "pwrite", "paddr", "pwdata", "prdata", "above", "count", "edges"))]
        names = (tops + inner)[:16]
        span = min(w.end, 2_000_000_000)  # the last 2 ms (ps)
        t0, t1 = wave_svg.last_window(w, span)
        svg = wave_svg.render(w, names, t0, t1)
        async with self:
            self.wave_svg = svg
            self.wave_info = f"{len(w.signals)} signals recorded; the last {span / 1e9:.2f} ms shown (simulation {s['id']})"
        return None

    # -------------------------------------------------------------- the device (dashboard)

    def _load_device(self, pid: str) -> None:
        try:
            prof = device_mod.load(projects.read(pid, "device.yaml"))
        except (projects.NotFound, device_mod.DeviceError):
            self.commands = []
            return
        rows = []
        for n, c in prof.commands.items():
            rows.append(CommandRow(name=n, title=c.title, args=", ".join(f"{a.name} ({a.type}{f', {a.min}..{a.max}' if a.min is not None else ''})" for a in c.args)))
        self.commands = rows
        dash = prof.dashboard or {}
        for ctl in dash.get("controls", []):
            if ctl.get("arg") and ctl["command"] not in self.dash_control_value:
                self.dash_control_value = {**self.dash_control_value, ctl["command"]: float(ctl.get("min", 0) + (ctl.get("max", 0) - ctl.get("min", 0)) / 2)}
        status = dash.get("status", "")
        s = service.active_of(pid)
        token_hint = "<your token>"
        self.sdk_snippet = (
            "from chipova_forge_sdk import Device\n\n"
            f"device = Device(\"{s['id'] if s else '<simulation id>'}\", url=\"https://chipova-forge.reflex-ddns.com\", token=\"{token_hint}\")\n"
            + (f"print(device.{status}())\n" if status else "")
            + "".join(f"device.{c['command']}({c['arg']}=35)\n" for c in dash.get("controls", []) if c.get("arg"))[:200]
            + "\n# On a real board, the same calls over its serial port:\n"
            "real = Device.serial(\"/dev/cu.usbmodem1101\", profile=\"device.yaml\")\n"
        )

    @rx.event
    def set_control_value(self, command: str, value: str):
        try:
            self.dash_control_value = {**self.dash_control_value, command: float(value)}
        except (TypeError, ValueError):
            pass

    @rx.event
    async def device_command(self, command: str, arg: str = ""):
        actor = await self._actor()
        s = service.active_of(self.project_id)
        if s is None:
            return rx.toast.error("no simulation runs: start one")
        args: dict[str, Any] = {}
        if arg:
            args[arg] = self.dash_control_value.get(command, 0.0)
        try:
            out = await asyncio.to_thread(service.device_command, s["id"], actor, command, args)
        except (service.SessionError, device_mod.DeviceError, projects.Denied) as e:
            self.dash_message = str(e)
            return rx.toast.error(str(e))
        self.dash_message = f"{command} → {out['reply']}"
        note(self.project_id, actor.name, f"sent {command}", "device")
        return WorkspaceState.refresh_status

    @rx.event
    async def refresh_status(self):
        actor = await self._actor()
        s = service.active_of(self.project_id)
        if s is None:
            return None
        try:
            prof = device_mod.load(projects.read(self.project_id, "device.yaml"))
            status = (prof.dashboard or {}).get("status", "")
            if status:
                await asyncio.to_thread(service.device_command, s["id"], actor, status, {})
        except (service.SessionError, device_mod.DeviceError, projects.Denied, projects.NotFound) as e:
            self.dash_message = str(e)
        return None

    # -------------------------------------------------------------- tests

    @rx.event(background=True)
    async def run_test(self, path: str):
        async with self:
            pid = self.project_id
            actor = await self._actor()
            self.busy = f"test:{path}"
        try:
            r = await systemtests.run(pid, path, actor, "ui")
            note(pid, actor.name, f"ran {path}: {r['status'].lower()}", "test")
            msg = rx.toast.success(f"{path}: PASSED") if r["status"] == "PASSED" else rx.toast.error(f"{path}: {r['status']} {r['error']}")
            async with self:
                self._show_test(r)
        except (systemtests.TestError, projects.Denied, projects.NotFound, ValueError) as e:
            msg = rx.toast.error(str(e))
        async with self:
            self.busy = ""
        push_later(pid, "tests", "session")
        return msg

    def _show_test(self, r: dict[str, Any]) -> None:
        self.test_run_id = r["id"]
        self.test_title = f"{(r.get('inputs') or {}).get('name', r['scenario'])} · {r['scenario']}"
        self.test_status = r["status"]
        self.test_error = r["error"]
        self.test_steps = [StepResultRow(n=s["n"], text=s["text"], status=s["status"], detail=str(s.get("detail", "")),
                                         vt=f"{float(s['vt']):.3f}" if s.get("vt") is not None else "", seconds=f"{s.get('seconds', '')}") for s in r["steps"]]

    @rx.event
    def show_test(self, run_id: str):
        r = systemtests.get(run_id)
        if r is not None and r["project_id"] == self.project_id:
            self._show_test(r)

    # -------------------------------------------------------------- releases

    def _load_checks(self, pid: str) -> None:
        cs, _ = releases.checks(pid)
        self.checks = [CheckRow(name=c["name"], title=c["title"], status=c["status"], detail=c["detail"]) for c in cs]

    @rx.event(background=True)
    async def make_release(self):
        async with self:
            pid = self.project_id
            actor = await self._actor()
            self.busy = "release"
        try:
            r = await asyncio.to_thread(releases.create, pid, "", actor)
            msg = rx.toast.success(f"Release {r['id']}: {r['status']}")
            note(pid, actor.name, f"made the release {r['id']} ({r['status'].lower()})", "release")
        except (releases.ReleaseError, projects.Denied) as e:
            msg = rx.toast.error(str(e))
        async with self:
            self.busy = ""
            self._load_checks(pid)
        push_later(pid, "releases")
        return msg

    @rx.event
    async def approve_release(self, release_id: str):
        actor = await self._actor()
        try:
            releases.approve(release_id, actor)
        except (releases.ReleaseError, projects.Denied, projects.NotFound) as e:
            return rx.toast.error(str(e))
        note(self.project_id, actor.name, f"approved the release {release_id}", "release")
        push_later(self.project_id, "releases")
        return rx.toast.success("Approved")

    # -------------------------------------------------------------- AI proposals, audit, API

    def _load_audit(self, pid: str) -> None:
        self.audit_rows = [AuditRow(at=e["ts"], who=e["actor_name"], kind=e["actor_kind"], action=e["action"], target=e["target"],
                                    decision=e["decision"], detail=e["detail"][:200]) for e in audit.entries(pid, 120)]

    @rx.event
    def show_proposal(self, proposal_id: str):
        p = proposals.get(proposal_id)
        if p is None or p["project_id"] != self.project_id:
            return
        self.proposal_id = proposal_id
        rows, lines = [], []
        for f in p["files"]:
            ls, added, removed = diff_lines(f["path"], f.get("old", ""), f["text"])
            rows.append(DiffRow(path=f["path"], old=f.get("old", ""), new=f["text"], added=added, removed=removed))
            lines += ls
        self.proposal_diff, self.proposal_lines = rows, lines
        self.proposal_title, self.proposal_why = p["title"], p.get("rationale", "")
        self.reject_reason = ""

    @rx.event
    def close_proposal(self):
        self.proposal_id = ""
        self.proposal_diff, self.proposal_lines = [], []

    @rx.event
    def set_reject_reason(self, value: str):
        self.reject_reason = value

    @rx.event
    async def decide_proposal(self, accept: bool):
        actor = await self._actor()
        try:
            proposals.decide(self.proposal_id, actor, accept, self.reject_reason)
        except (projects.Denied, projects.Conflict, projects.NotFound) as e:
            return rx.toast.error(str(e))
        note(self.project_id, actor.name, ("accepted" if accept else "rejected") + " an AI proposal", "ai")
        self.proposal_id, self.proposal_diff, self.proposal_lines = "", [], []
        self._load_files(self.project_id)
        if self.open_path:
            self._open(self.project_id, self.open_path)
        push_later(self.project_id, "proposals", "project", "builds", "tests")
        return rx.toast.success("Accepted: the files are saved" if accept else "Rejected: the AI reads your reason")

    @rx.event
    async def make_api_token(self):
        actor = await self._actor()
        if not actor.user or actor.is_guest:
            return rx.toast.error("Sign in to make an API token")
        self.api_token = api_mod.make_token(actor)
        audit.record("api.token", actor, self.project_id, "", {"days": api_mod.TOKEN_DAYS})
        return None

    # -------------------------------------------------------------- people

    def _load_people(self, pid: str) -> None:
        p = projects.get(pid)
        self.link_access = p["link_access"] if p else "none"
        me = self._ui_me.get("user", "")
        self.members = [MemberRow(user=m["user"], role=m["role"], added_by=m["added_by"], me=m["user"] == me) for m in projects.members(pid)]

    @rx.event
    def set_member_email(self, value: str):
        self.member_email = value

    @rx.event
    def set_member_role(self, value: str):
        self.member_role = value

    async def _manage(self, fn, what: str):  # noqa: ANN001
        actor = await self._actor()
        try:
            fn(actor)
        except (projects.Denied, projects.NotFound, ValueError) as e:
            return rx.toast.error(str(e))
        note(self.project_id, actor.name, what, "people")
        self._load_people(self.project_id)
        push_later(self.project_id, "project", "people")
        return None

    @rx.event
    async def set_link(self, value: str):
        pid = self.project_id
        return await self._manage(lambda a: projects.set_link_access(pid, value, a), f"let the link in as {value}")

    @rx.event
    async def add_member(self):
        pid, email, role = self.project_id, self.member_email.strip().lower(), self.member_role
        out = await self._manage(lambda a: projects.set_member(pid, email, role, a), f"made {email} {role}")
        if out is None:
            self.member_email = ""
        return out

    @rx.event
    async def change_member(self, user: str, role: str):
        pid = self.project_id
        return await self._manage(lambda a: projects.set_member(pid, user, role, a), f"made {user} {role or 'no longer a member'}")

    @rx.event
    def refresh_audit(self):
        self._load_audit(self.project_id)
