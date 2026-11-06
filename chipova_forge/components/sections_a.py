"""The project page's first sections: Overview, Chip, Registers, Board, Firmware (and the pieces
they share: the editor panel, issue lists, the build bar)."""

from __future__ import annotations

import reflex as rx

from chipova_forge.components.common import BTN_GHOST, BTN_OUTLINE, BTN_PRIMARY, INPUT, LABEL, PANEL, ago, empty, status_badge
from chipova_forge.components.editor import code_editor
from chipova_forge.components.links import backend_url
from chipova_forge.states.room import ActivityRow, BuildRow, ProjectRoom, StepRow
from chipova_forge.states.workspace import FileRow, IssueRow, LinkRow, PinRow, PinUseRow, PortRow, ProblemRow, RegRow, WorkspaceState

TH = "text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500 px-3 py-2 border-b border-slate-800"
TD = "px-3 py-1.5 border-b border-slate-900 text-sm align-top"


def page(title: str, sub: str, *children: rx.Component, actions: rx.Component | None = None) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.div(
                rx.el.h2(title, class_name="text-lg font-bold text-slate-100 tracking-tight"),
                rx.el.p(sub, class_name="text-sm text-slate-400"),
                class_name="flex flex-col gap-0.5",
            ),
            actions or rx.fragment(),
            class_name="flex justify-between items-start gap-4 flex-wrap",
        ),
        *children,
        class_name="flex flex-col gap-4 p-5 max-w-[1400px]",
    )


def panel(title: str, *children: rx.Component, right: rx.Component | None = None, id: str = "") -> rx.Component:  # noqa: A002
    return rx.el.section(
        rx.el.div(rx.el.span(title, class_name=LABEL), right or rx.fragment(), class_name="flex justify-between items-center gap-2 px-4 pt-3"),
        rx.el.div(*children, class_name="p-4 pt-2"),
        id=id or None,
        class_name=PANEL,
    )


def issue_row(i: IssueRow) -> rx.Component:
    return rx.el.div(
        rx.match(i.level,
                 ("error", rx.icon("circle-x", class_name="h-4 w-4 text-rose-400 shrink-0 mt-0.5")),
                 ("warning", rx.icon("triangle-alert", class_name="h-4 w-4 text-amber-400 shrink-0 mt-0.5")),
                 rx.icon("info", class_name="h-4 w-4 text-sky-400 shrink-0 mt-0.5")),
        rx.el.div(
            rx.el.span(i.where, class_name="font-mono text-xs text-slate-500"),
            rx.el.span(i.message, class_name="text-sm text-slate-200"),
            rx.cond(i.fix != "", rx.el.span(i.fix, class_name="text-xs text-slate-400")),
            class_name="flex flex-col",
        ),
        custom_attrs={"data-issue": i.level},
        class_name="flex gap-2 py-1.5",
    )


def issues(rows, ok_text: str) -> rx.Component:  # noqa: ANN001
    return rx.cond(
        rows.length() > 0,
        rx.el.div(rx.foreach(rows, issue_row), class_name="flex flex-col divide-y divide-slate-800"),
        rx.el.div(rx.icon("circle-check", class_name="h-4 w-4 text-emerald-400"), rx.el.span(ok_text, class_name="text-sm text-slate-300"), class_name="flex items-center gap-2"),
    )


def file_button(f: FileRow) -> rx.Component:
    return rx.el.button(
        rx.icon(rx.cond(f.path.endswith(".yaml"), "file-cog", "file-code-2"), class_name="h-3.5 w-3.5 shrink-0"),
        rx.el.span(f.path, class_name="truncate"),
        on_click=WorkspaceState.open_file(f.path),
        custom_attrs={"data-file": f.path},
        class_name=rx.cond(WorkspaceState.open_path == f.path,
                           "flex items-center gap-2 w-full px-2 py-1 rounded text-left text-xs font-mono bg-amber-500/15 text-amber-200",
                           "flex items-center gap-2 w-full px-2 py-1 rounded text-left text-xs font-mono text-slate-300 hover:bg-slate-800"),
    )


def editor_panel(height: str = "62vh") -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.div(
                rx.icon("file-pen", class_name="h-4 w-4 text-slate-400"),
                rx.el.span(WorkspaceState.open_path, id="open-path", class_name="font-mono text-xs text-slate-200"),
                rx.cond(WorkspaceState.editor_readonly, rx.el.span("read-only", class_name="text-[10px] uppercase tracking-wider text-slate-500")),
                rx.cond(WorkspaceState.editor_dirty, rx.el.span("● unsaved", id="unsaved", class_name="text-[11px] text-amber-400")),
                class_name="flex items-center gap-2 min-w-0",
            ),
            rx.el.div(
                rx.el.button(rx.icon("rotate-ccw", class_name="h-3.5 w-3.5"), "Reload", on_click=WorkspaceState.reload_file, class_name=BTN_GHOST),
                rx.el.button(rx.icon("save", class_name="h-3.5 w-3.5"), "Save", on_click=WorkspaceState.save_file, id="save-file",
                             disabled=WorkspaceState.editor_readonly | ~WorkspaceState.editor_dirty, class_name=BTN_PRIMARY),
                class_name="flex items-center gap-2",
            ),
            class_name="flex justify-between items-center px-3 h-10 border-b border-slate-800",
        ),
        rx.cond(
            WorkspaceState.open_path != "",
            rx.el.div(
                code_editor(WorkspaceState.editor_key, WorkspaceState.editor_initial, WorkspaceState.editor_lang, WorkspaceState.editor_readonly,
                            WorkspaceState.set_editor_text),
                id="editor",
                style={"height": height},
            ),
            empty("file-code-2", "No file open"),
        ),
        class_name=f"{PANEL} overflow-hidden",
    )


def build_row(b: BuildRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.span(rx.match(b.kind, ("pcb", "PCB"), ("registers", "Registers"), ("chip", "Chip"), ("firmware", "Firmware"), b.kind),
                       class_name="font-semibold text-slate-100 w-20"),
            status_badge(b.status, small=True),
            rx.el.span(b.summary, class_name="text-xs text-slate-400 truncate"),
            class_name="flex items-center gap-2 min-w-0",
        ),
        rx.el.div(
            rx.cond(b.at > 0, rx.el.span(ago(b.at), class_name="text-[11px] text-slate-500")),
            rx.el.button(rx.icon("scroll-text", class_name="h-3.5 w-3.5"), "Log", on_click=WorkspaceState.show_log(b.kind), class_name=BTN_GHOST),
            rx.el.button(rx.icon("hammer", class_name="h-3.5 w-3.5"), "Build", on_click=WorkspaceState.request_build(b.kind),
                         disabled=~WorkspaceState.can_build | (WorkspaceState.busy != ""), custom_attrs={"data-build": b.kind}, class_name=BTN_OUTLINE),
            class_name="flex items-center gap-2 shrink-0",
        ),
        custom_attrs={"data-build-row": b.kind, "data-status": b.status},
        class_name="flex justify-between items-center gap-3 py-2",
    )


def problem_row(p: ProblemRow) -> rx.Component:
    return rx.el.button(
        rx.el.span(p.severity, class_name=rx.cond(p.severity == "error", "text-rose-400 w-14 shrink-0", "text-amber-400 w-14 shrink-0")),
        rx.el.span(p.file + ":" + p.line.to(str), class_name="font-mono text-slate-300 shrink-0"),
        rx.el.span(p.message, class_name="text-slate-200 text-left"),
        on_click=WorkspaceState.jump_to_problem(p.file, p.line),
        class_name="flex gap-2 text-xs w-full py-1 hover:bg-slate-800/60 rounded px-1",
    )


def log_box() -> rx.Component:
    return rx.cond(
        WorkspaceState.log_kind != "",
        rx.el.div(
            rx.el.div(rx.el.span("Output of the ", WorkspaceState.log_kind, " build", class_name=LABEL),
                      rx.el.button(rx.icon("x", class_name="h-3.5 w-3.5"), on_click=WorkspaceState.show_log(WorkspaceState.log_kind), class_name=BTN_GHOST),
                      class_name="flex justify-between items-center"),
            rx.cond(WorkspaceState.problems.length() > 0, rx.el.div(rx.foreach(WorkspaceState.problems, problem_row), class_name="flex flex-col mb-2")),
            rx.el.pre(WorkspaceState.log_text, id="build-log", custom_attrs={"data-autoscroll": "1"},
                      class_name="text-[11px] leading-snug text-slate-300 bg-black/40 rounded p-3 max-h-80 overflow-auto whitespace-pre-wrap"),
            class_name=f"{PANEL} p-3 flex flex-col gap-2",
        ),
    )


def download_link(l: LinkRow) -> rx.Component:
    return rx.el.a(
        rx.icon("download", class_name="h-3.5 w-3.5"),
        rx.el.span(l.name, class_name="font-mono"),
        rx.el.span(l.size, class_name="text-slate-500"),
        href=backend_url(l.url),
        download=True,
        custom_attrs={"data-download": l.name},
        class_name="inline-flex items-center gap-1.5 text-xs text-sky-300 hover:text-sky-200 px-2 py-1 rounded border border-slate-800 bg-slate-950",
    )


# ------------------------------------------------------------------ Overview


def step_card(s: StepRow) -> rx.Component:
    return rx.el.button(
        rx.el.div(
            rx.el.span(s.n, class_name="text-xs font-mono text-slate-500"),
            status_badge(s.status, small=True),
            class_name="flex justify-between items-center",
        ),
        rx.el.span(s.title, class_name="text-sm font-semibold text-slate-100 text-left"),
        rx.el.span(s.detail, class_name="text-xs text-slate-400 text-left line-clamp-2"),
        on_click=WorkspaceState.set_view(s.view),
        custom_attrs={"data-step": s.n.to(str), "data-status": s.status},
        class_name=f"{PANEL} p-3 flex flex-col gap-1.5 hover:border-amber-500/50 transition-colors",
    )


def activity_row(a: ActivityRow) -> rx.Component:
    return rx.el.div(
        rx.el.span(a.who, class_name="font-semibold text-slate-200"),
        rx.el.span(a.what, class_name="text-slate-400"),
        rx.el.span(ago(a.at), class_name="ml-auto text-slate-600 shrink-0"),
        class_name="flex gap-1.5 text-xs py-1",
    )


def overview_view() -> rx.Component:
    return page(
        "Overview", "The flow from the chip to a released product: where each of its twelve steps stands.",
        rx.el.div(rx.foreach(ProjectRoom.steps, step_card), id="steps", class_name="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3"),
        rx.el.div(
            panel("Builds", rx.el.div(rx.foreach(ProjectRoom.builds, build_row), class_name="flex flex-col divide-y divide-slate-800")),
            panel("What's happening", rx.cond(ProjectRoom.activity.length() > 0,
                                               rx.el.div(rx.foreach(ProjectRoom.activity, activity_row), class_name="flex flex-col max-h-64 overflow-y-auto"),
                                               rx.el.p("Nothing yet.", class_name="text-sm text-slate-500"))),
            class_name="grid grid-cols-1 lg:grid-cols-2 gap-3",
        ),
        log_box(),
        actions=rx.el.div(
            rx.el.button(rx.icon("play", class_name="h-4 w-4"), "Start the simulation", on_click=WorkspaceState.start_session,
                         disabled=~WorkspaceState.can_simulate | ProjectRoom.running | (WorkspaceState.busy != ""), id="overview-start", class_name=BTN_PRIMARY),
            rx.el.button(rx.icon("flask-conical", class_name="h-4 w-4"), "Tests", on_click=WorkspaceState.set_view("tests"), class_name=BTN_OUTLINE),
            class_name="flex gap-2",
        ),
    )


# ------------------------------------------------------------------ Chip


def port_row(p: PortRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(p.port, class_name=f"{TD} font-mono text-slate-100"),
        rx.el.td(p.direction, class_name=f"{TD} text-slate-400"),
        rx.el.td(p.width, class_name=f"{TD} text-slate-400"),
        rx.el.td(rx.el.span(p.role, class_name="px-1.5 py-0.5 rounded bg-slate-800 text-xs text-slate-200"), class_name=TD),
        rx.el.td(p.pins, class_name=f"{TD} font-mono text-slate-400"),
        custom_attrs={"data-port": p.port},
    )


def pin_row(p: PinRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(p.pin, class_name=f"{TD} font-mono text-slate-400"),
        rx.el.td(p.signal, class_name=f"{TD} font-mono text-slate-100"),
        rx.el.td(p.kind, class_name=f"{TD} text-slate-400"),
        rx.el.td(p.pad, class_name=f"{TD} font-mono text-slate-500"),
    )


def chip_view() -> rx.Component:
    return page(
        "The chip", "A frozen Chipova snapshot (read-only here) and the hardware contract Forge made from it.",
        rx.el.div(
            panel("Snapshot",
                  rx.el.div(
                      rx.el.div(rx.el.span("Name", class_name="text-[11px] text-slate-500"), rx.el.span(ProjectRoom.chip_name, class_name="font-semibold text-slate-100"), class_name="flex flex-col"),
                      rx.el.div(rx.el.span("Top module", class_name="text-[11px] text-slate-500"), rx.el.span(ProjectRoom.chip_top, class_name="font-mono text-slate-100"), class_name="flex flex-col"),
                      rx.el.div(rx.el.span("Interface", class_name="text-[11px] text-slate-500"), rx.el.span(ProjectRoom.interface.upper(), id="chip-interface", class_name="font-semibold text-teal-300"), class_name="flex flex-col"),
                      rx.el.div(rx.el.span("From", class_name="text-[11px] text-slate-500"), rx.el.span(ProjectRoom.chip_source, class_name="font-mono text-slate-300 break-all"), class_name="flex flex-col"),
                      rx.el.div(rx.el.span("Snapshot (Chipova revision id)", class_name="text-[11px] text-slate-500"), rx.el.span(ProjectRoom.chip_snapshot, id="chip-snapshot", class_name="font-mono text-xs text-slate-300 break-all"), class_name="flex flex-col col-span-2"),
                      rx.el.div(rx.el.span("Package", class_name="text-[11px] text-slate-500"), rx.el.span(WorkspaceState.package_text, class_name="text-slate-300"), class_name="flex flex-col"),
                      rx.el.div(rx.el.span("Electrical", class_name="text-[11px] text-slate-500"), rx.el.span(WorkspaceState.electrical_text, class_name="text-slate-300"), class_name="flex flex-col"),
                      class_name="grid grid-cols-2 gap-3",
                  )),
            panel("Contract checks", issues(WorkspaceState.contract_issues, "The contract holds together: every port has a role and a pin, the interface is complete."), id="contract-issues"),
            class_name="grid grid-cols-1 lg:grid-cols-2 gap-3",
        ),
        rx.el.div(
            panel("Ports (from the RTL, elaborated)",
                  rx.el.table(rx.el.thead(rx.el.tr(*[rx.el.th(h, class_name=TH) for h in ("Port", "Dir", "Bits", "Role", "Pins")])),
                              rx.el.tbody(rx.foreach(WorkspaceState.ports, port_row)), id="ports", class_name="w-full")),
            panel("Package pins (package/pins.csv)",
                  rx.el.div(rx.el.table(rx.el.thead(rx.el.tr(*[rx.el.th(h, class_name=TH) for h in ("Pin", "Signal", "Kind", "Pad")])),
                                        rx.el.tbody(rx.foreach(WorkspaceState.pinmap, pin_row)), class_name="w-full"), class_name="max-h-96 overflow-y-auto")),
            class_name="grid grid-cols-1 lg:grid-cols-2 gap-3",
        ),
        rx.el.div(
            panel("Files",
                  rx.el.div(
                      file_button(FileRow(path="hardware.yaml")),
                      rx.el.span("chip/ (read-only)", class_name=f"{LABEL} mt-2"),
                      rx.foreach(WorkspaceState.chip_files, lambda p: rx.el.button(
                          rx.icon("lock", class_name="h-3 w-3 shrink-0 text-slate-600"), rx.el.span(p, class_name="truncate"),
                          on_click=WorkspaceState.open_file("chip/" + p),
                          class_name=rx.cond(WorkspaceState.open_path == "chip/" + p,
                                             "flex items-center gap-2 w-full px-2 py-1 rounded text-left text-xs font-mono bg-amber-500/15 text-amber-200",
                                             "flex items-center gap-2 w-full px-2 py-1 rounded text-left text-xs font-mono text-slate-400 hover:bg-slate-800"))),
                      class_name="flex flex-col gap-0.5",
                  )),
            editor_panel("52vh"),
            class_name="grid grid-cols-1 lg:grid-cols-[18rem_1fr] gap-3",
        ),
    )


# ------------------------------------------------------------------ Registers


def reg_row(r: RegRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(r.offset, class_name=f"{TD} font-mono text-slate-400"),
        rx.el.td(rx.el.div(rx.el.span(r.name, class_name="font-mono font-semibold text-slate-100"), rx.el.span(r.title, class_name="text-xs text-slate-500"), class_name="flex flex-col"), class_name=TD),
        rx.el.td(r.access, class_name=f"{TD} text-slate-300"),
        rx.el.td(r.reset, class_name=f"{TD} font-mono text-slate-300"),
        rx.el.td(r.fields, class_name=f"{TD} font-mono text-xs text-slate-300"),
        rx.el.td(r.desc, class_name=f"{TD} text-xs text-slate-400"),
        custom_attrs={"data-register": r.name},
    )


def registers_view() -> rx.Component:
    return page(
        "Registers", "One SystemRDL description: the chip's CSR RTL, the firmware's C header and the SDK's Python map all come from it (PeakRDL).",
        panel("CSR RTL",
              rx.el.div(
                  rx.cond(WorkspaceState.reg_sync != "", status_badge(rx.match(WorkspaceState.reg_sync, ("in_sync", "PASS"), ("harness", "DONE"), ("differs", "FAIL"), "NOT_RUN")),
                          status_badge("NOT_RUN")),
                  rx.el.span(rx.cond(WorkspaceState.reg_sync_detail != "", WorkspaceState.reg_sync_detail, "Build the registers to check the chip's CSR RTL against its SystemRDL."),
                             id="reg-sync", class_name="text-sm text-slate-300"),
                  class_name="flex items-center gap-3",
              ),
              rx.cond(WorkspaceState.reg_downloads.length() > 0, rx.el.div(rx.foreach(WorkspaceState.reg_downloads, download_link), class_name="flex flex-wrap gap-2 mt-3")),
              right=rx.el.button(rx.icon("hammer", class_name="h-3.5 w-3.5"), "Build the registers", on_click=WorkspaceState.request_build("registers"),
                                 disabled=~WorkspaceState.can_build | (WorkspaceState.busy != ""), id="build-registers", class_name=BTN_OUTLINE)),
        panel("Register map",
              rx.cond(WorkspaceState.registers.length() > 0,
                      rx.el.table(rx.el.thead(rx.el.tr(*[rx.el.th(h, class_name=TH) for h in ("Offset", "Register", "Access", "Reset", "Fields", "")])),
                                  rx.el.tbody(rx.foreach(WorkspaceState.registers, reg_row)), id="register-map", class_name="w-full"),
                      empty("table-properties", "No register map", "The chip has no SystemRDL description."))),
        log_box(),
    )


# ------------------------------------------------------------------ Board


def pin_use_row(r: PinUseRow) -> rx.Component:
    return rx.el.tr(rx.el.td(r.pin, class_name=f"{TD} font-mono text-amber-300"), rx.el.td(r.use, class_name=f"{TD} text-slate-300"))


PCB_TABS = (("top", "Top"), ("bottom", "Bottom (mirrored)"), ("schematic", "Schematic"))


def pcb_panel() -> rx.Component:
    """The module Forge lays out from system.yaml: its schematic and two-layer PCB (KiCad files),
    routed by Freerouting, checked by KiCad (ERC, DRC with schematic parity), with Gerbers."""
    tab = lambda key, label: rx.el.button(  # noqa: E731
        label, on_click=WorkspaceState.set_pcb_view(key), custom_attrs={"data-pcb-view": key},
        class_name=rx.cond(WorkspaceState.pcb_view == key, "px-3 h-7 rounded-md bg-amber-500/15 text-amber-300 text-xs font-semibold",
                           "px-3 h-7 rounded-md text-slate-400 hover:text-slate-200 text-xs"))
    return panel(
        "Schematic & PCB",
        # Re-mounted (links signed again for this tab) whenever someone builds a newer PCB.
        rx.el.div(rx.el.div(on_mount=WorkspaceState.load_pcb), key=ProjectRoom.pcb_build, class_name="hidden"),
        rx.el.div(
            rx.cond(WorkspaceState.pcb_status != "", status_badge(WorkspaceState.pcb_status), status_badge("NOT_RUN")),
            rx.el.span(WorkspaceState.pcb_line, id="pcb-line", class_name="text-sm text-slate-300"),
            class_name="flex items-center gap-3 flex-wrap",
        ),
        rx.el.p("A carrier module for the chip: its decoupling, clock and reset, the sensors with their pull-ups, the chip's LEDs, and a header to the MCU board. "
                "Forge places it and writes the KiCad files; Freerouting routes it; KiCad checks it (ERC, DRC, the board against the schematic) and makes the Gerbers.",
                class_name="text-xs text-slate-500 mt-1"),
        rx.cond(
            WorkspaceState.pcb_pictures.length() > 0,
            rx.el.div(
                rx.el.div(*[tab(k, label) for k, label in PCB_TABS], class_name="flex gap-1"),
                rx.el.div(
                    rx.el.img(src=backend_url(WorkspaceState.pcb_pictures[WorkspaceState.pcb_view]), id="pcb-picture",
                              alt="The board", class_name="w-full max-h-[60vh] object-contain"),
                    class_name=rx.cond(WorkspaceState.pcb_view == "schematic", "rounded-lg bg-[#f5f4ef] p-2", "rounded-lg bg-[#0b1020] p-2"),
                ),
                rx.el.div(rx.foreach(WorkspaceState.pcb_downloads, download_link), id="pcb-downloads", class_name="flex flex-wrap gap-2"),
                class_name="flex flex-col gap-2 mt-3",
            ),
        ),
        rx.cond(WorkspaceState.pcb_checks.length() > 0,
                rx.el.div(issues(WorkspaceState.pcb_checks, ""), id="pcb-checks", class_name="mt-3 max-h-64 overflow-y-auto")),
        rx.cond(WorkspaceState.pcb_notes.length() > 0,
                rx.el.ul(rx.foreach(WorkspaceState.pcb_notes, lambda n: rx.el.li(n, class_name="font-mono text-[11px] text-slate-400")),
                         id="pcb-notes", class_name="mt-3 flex flex-col gap-0.5")),
        right=rx.el.div(
            rx.el.button(rx.icon("scroll-text", class_name="h-3.5 w-3.5"), "Log", on_click=WorkspaceState.show_log("pcb"), class_name=BTN_GHOST),
            rx.el.button(rx.icon("cpu", class_name="h-3.5 w-3.5"), "Lay out & check", on_click=WorkspaceState.request_build("pcb"),
                         disabled=~WorkspaceState.can_build | (WorkspaceState.busy != "") | (WorkspaceState.pcb_status == "NOT_CONFIGURED"),
                         custom_attrs={"data-build": "pcb"}, id="build-pcb", class_name=BTN_PRIMARY),
            class_name="flex items-center gap-2"),
        id="pcb",
    )


def board_view() -> rx.Component:
    return page(
        "The board", "The MCU platform, how the chip is wired to it, the sensors and the console: system.yaml. The firmware's board.h and the simulation's board are made from it.",
        rx.el.div(
            panel("Board", rx.cond(WorkspaceState.board_svg != "", rx.html(WorkspaceState.board_svg, id="board-diagram"), empty("circuit-board", "No picture", "system.yaml doesn't read."))),
            rx.el.div(
                panel("Checks", issues(WorkspaceState.board_issues, "The board holds together: no pin is used twice, every part has its bus."), id="board-issues"),
                panel("MCU pins in use", rx.el.table(rx.el.tbody(rx.foreach(WorkspaceState.pin_use, pin_use_row)), id="pin-use", class_name="w-full")),
                class_name="flex flex-col gap-3",
            ),
            class_name="grid grid-cols-1 xl:grid-cols-[1fr_24rem] gap-3",
        ),
        pcb_panel(),
        rx.el.div(
            panel("Files", rx.el.div(
                file_button(FileRow(path="system.yaml")), file_button(FileRow(path="hardware.yaml")), file_button(FileRow(path="device.yaml")),
                class_name="flex flex-col gap-0.5")),
            editor_panel("48vh"),
            class_name="grid grid-cols-1 lg:grid-cols-[18rem_1fr] gap-3",
        ),
    )


# ------------------------------------------------------------------ Firmware


def firmware_view() -> rx.Component:
    return page(
        "Firmware", "The MCU's firmware: C, compiled with Arm GCC together with the platform's start-up code, Forge's chip driver, board.h (from system.yaml) and the register header (from the SystemRDL).",
        rx.el.div(
            rx.el.div(
                panel("Files",
                      rx.el.div(
                          rx.foreach(WorkspaceState.files, lambda f: rx.cond(f.path.startswith("firmware/"), file_button(f))),
                          rx.el.button(rx.icon("plus", class_name="h-3.5 w-3.5"), "New file", on_click=WorkspaceState.set_new_file_open(True), disabled=~WorkspaceState.can_edit, class_name=f"{BTN_GHOST} mt-1"),
                          class_name="flex flex-col gap-0.5",
                      )),
                panel("Build",
                      rx.el.div(rx.foreach(ProjectRoom.builds, lambda b: rx.cond(b.kind == "firmware", build_row(b))), class_name="flex flex-col"),
                      rx.cond(WorkspaceState.fw_downloads.length() > 0, rx.el.div(rx.foreach(WorkspaceState.fw_downloads, download_link), class_name="flex flex-wrap gap-2 mt-2"))),
                class_name="flex flex-col gap-3",
            ),
            editor_panel("64vh"),
            class_name="grid grid-cols-1 lg:grid-cols-[20rem_1fr] gap-3",
        ),
        log_box(),
        new_file_dialog(),
    )


def new_file_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("New file"),
            rx.dialog.description("firmware/<name>.c or .h, or tests/<name>.yaml", size="2"),
            rx.el.input(value=WorkspaceState.new_file_path, on_change=WorkspaceState.set_new_file_path, id="new-file-path", auto_focus=True, class_name=f"{INPUT} w-full mt-3 font-mono"),
            rx.el.div(
                rx.dialog.close(rx.button("Cancel", variant="soft", color_scheme="gray")),
                rx.button("Create", on_click=WorkspaceState.create_file, color_scheme="amber", id="create-file"),
                class_name="flex justify-end gap-2 mt-4",
            ),
            max_width="460px",
        ),
        open=WorkspaceState.new_file_open,
        on_open_change=WorkspaceState.set_new_file_open,
    )
