"""The project list (/)."""

from __future__ import annotations

import reflex as rx

from chipova_forge.components.common import BTN_GHOST, BTN_OUTLINE, BTN_PRIMARY, INPUT, LABEL, PANEL, ago, empty, logo, status_badge
from chipova_forge.states.home_state import HomeState, OptionRow, ProjectRow, ToolRow, ToolsState
from chipova_forge.states.ui_state import UiState

FLOW = [
    ("cpu", "Chip", "A Chipova snapshot: RTL, pins, SystemRDL registers"),
    ("file-check-2", "Contract", "hardware.yaml: pins, interface, registers, limits"),
    ("circuit-board", "Board", "MCU, chip, sensors, LEDs, console"),
    ("binary", "Firmware", "C for the MCU, with the generated headers"),
    ("activity", "Simulation", "Renode runs the firmware, Verilator the chip's RTL"),
    ("plug-zap", "Device API", "REST, events, the Python SDK"),
    ("layout-dashboard", "App", "a dashboard on the device API"),
    ("badge-check", "Tests & release", "end to end, with evidence"),
]


def flow_strip() -> rx.Component:
    items = []
    for i, (icon, title, hint) in enumerate(FLOW):
        if i:
            items.append(rx.icon("chevron-right", class_name="h-4 w-4 text-slate-700 shrink-0"))
        items.append(rx.el.div(
            rx.icon(icon, class_name="h-4 w-4 text-amber-400"),
            rx.el.span(title, class_name="text-xs font-semibold text-slate-200"),
            title=hint,
            class_name="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-slate-900 border border-slate-800 shrink-0",
        ))
    return rx.el.div(*items, class_name="flex items-center gap-1.5 overflow-x-auto pb-1")


def project_card(p: ProjectRow) -> rx.Component:
    return rx.el.a(
        rx.el.div(
            rx.el.div(
                rx.el.span(p.title, class_name="font-semibold text-slate-100 truncate"),
                rx.cond(p.simulation != "", status_badge(p.simulation, small=True)),
                class_name="flex items-center justify-between gap-2",
            ),
            rx.el.div(
                rx.icon("cpu", class_name="h-3.5 w-3.5 text-teal-400"),
                rx.el.span(p.chip, class_name="font-mono"),
                rx.el.span("·", class_name="text-slate-600"),
                rx.el.span(p.interface.upper()),
                class_name="flex items-center gap-1.5 text-xs text-slate-400",
            ),
            rx.el.div(
                rx.el.span(p.owner),
                rx.el.span("·", class_name="text-slate-600"),
                ago(p.updated),
                class_name="flex items-center gap-1.5 text-[11px] text-slate-500",
            ),
            class_name="flex flex-col gap-1.5",
        ),
        href="/p/" + p.id,
        custom_attrs={"data-project": p.id},
        class_name=f"{PANEL} p-4 hover:border-amber-500/60 transition-colors block",
    )


def option_radio(o: OptionRow, value, on_change, name: str) -> rx.Component:  # noqa: ANN001
    return rx.el.label(
        rx.el.input(type="radio", name=name, value=o.id, checked=value == o.id, on_change=lambda _: on_change(o.id), class_name="mt-1 accent-amber-500"),
        rx.el.div(
            rx.el.span(o.title, class_name="text-sm font-semibold text-slate-100"),
            rx.el.span(o.description, class_name="text-xs text-slate-400"),
            class_name="flex flex-col gap-0.5",
        ),
        custom_attrs={"data-option": o.id},
        class_name="flex gap-2.5 p-2.5 rounded-lg border border-slate-800 hover:border-slate-600 cursor-pointer",
    )


def new_project_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("New product"),
            rx.dialog.description("A product template, built around a chip from Chipova.", size="2"),
            rx.el.div(
                rx.el.span("Title", class_name=LABEL),
                rx.el.input(value=HomeState.new_title, on_change=HomeState.set_new_title, placeholder="(the template's title)", id="new-title", class_name=f"{INPUT} w-full"),
                rx.el.span("Template", class_name=f"{LABEL} mt-2"),
                rx.foreach(HomeState.templates, lambda o: option_radio(o, HomeState.new_template, HomeState.set_new_template, "template")),
                rx.el.span("The chip", class_name=f"{LABEL} mt-2"),
                rx.el.div(
                    *[rx.el.button(label, on_click=HomeState.set_new_source(key), id=f"source-{key}",
                                   class_name=rx.cond(HomeState.new_source == key, f"{BTN_PRIMARY}", f"{BTN_OUTLINE}"))
                      for key, label in (("example", "Example chip"), ("upload", "Chipova release (.zip)"), ("collection", "From Chipova (collection)"))],
                    class_name="flex gap-2 flex-wrap",
                ),
                rx.match(
                    HomeState.new_source,
                    ("upload", rx.el.div(
                        rx.upload(
                            rx.el.div(
                                rx.icon("upload", class_name="h-5 w-5 text-slate-400"),
                                rx.el.span(rx.cond(HomeState.upload_name != "", HomeState.upload_name, "Drop the zip of a release from Chipova's Release page"), class_name="text-sm text-slate-300"),
                                class_name="flex flex-col items-center gap-1 py-5",
                            ),
                            id="chip-zip",
                            accept={"application/zip": [".zip"]},
                            max_files=1,
                            on_drop=HomeState.take_upload(rx.upload_files(upload_id="chip-zip")),
                            class_name="border border-dashed border-slate-600 rounded-lg cursor-pointer hover:border-amber-500",
                        ),
                        rx.el.p("Forge checks every file against the release's snapshot before it takes the chip.", class_name="text-xs text-slate-500 mt-1"),
                    )),
                    ("collection", rx.cond(
                        HomeState.collection.length() > 0,
                        rx.el.div(rx.foreach(HomeState.collection, lambda o: option_radio(o, HomeState.new_ref, HomeState.set_new_ref, "ref")), class_name="flex flex-col gap-2"),
                        rx.el.p("No chips in the platform's chips collection yet: publish one from Chipova's Release page, or use an example or a zip.", class_name="text-xs text-slate-500"),
                    )),
                    rx.el.div(rx.foreach(HomeState.chips, lambda o: option_radio(o, HomeState.new_chip, HomeState.set_new_chip, "chip")), class_name="flex flex-col gap-2"),
                ),
                rx.cond(HomeState.error != "", rx.el.p(HomeState.error, id="new-error", class_name="text-sm text-rose-300 bg-rose-950/40 border border-rose-900 rounded-md p-2")),
                class_name="flex flex-col gap-2 mt-3 max-h-[60vh] overflow-y-auto pr-1",
            ),
            rx.el.div(
                rx.dialog.close(rx.button("Cancel", variant="soft", color_scheme="gray")),
                rx.button("Make it", on_click=HomeState.create, color_scheme="amber", id="create-project", loading=HomeState.creating),
                class_name="flex justify-end gap-2 mt-4",
            ),
            max_width="620px",
        ),
        open=HomeState.new_open,
        on_open_change=HomeState.set_new_open,
    )


def identity_chip() -> rx.Component:
    me = UiState.me
    return rx.el.div(
        rx.el.div(me["initials"], class_name="h-7 w-7 rounded-full bg-slate-700 text-[11px] font-bold text-white flex items-center justify-center"),
        rx.el.span(me["name"], class_name="text-xs text-slate-300 hidden sm:inline"),
        class_name="flex items-center gap-2",
    )


def tool_row(r: ToolRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.span(r.title, class_name="text-sm font-semibold text-slate-100"),
            rx.el.span(r.what, " · ", r.size, class_name="text-xs text-slate-400"),
            rx.cond(r.line != "", rx.el.span(r.line, class_name="text-[11px] font-mono text-slate-500 truncate")),
            class_name="flex flex-col min-w-0",
        ),
        status_badge(r.status, small=True),
        custom_attrs={"data-tool": r.key, "data-status": r.status},
        class_name="flex justify-between items-center gap-3 py-2",
    )


def tools_panel() -> rx.Component:
    """Shown until Forge's two tool images are on this computer (an App Store install doesn't
    bring them): Install builds them here."""
    return rx.cond(
        ToolsState.tools_missing,
        rx.el.section(
            rx.el.div(
                rx.el.div(
                    rx.el.h2("Install Forge's tools first", class_name="text-base font-bold text-amber-200"),
                    rx.el.p("Builds, simulations and the PCB run in two Docker images. Forge builds them on this computer once, "
                            "in the background: a few minutes with the platform's download cache.", class_name="text-sm text-slate-300"),
                    class_name="flex flex-col gap-1",
                ),
                rx.el.button(rx.icon("download", class_name="h-4 w-4"), "Install", on_click=ToolsState.install_tools, id="install-tools",
                             disabled=ToolsState.tools_busy, class_name=BTN_PRIMARY),
                class_name="flex justify-between items-start gap-4",
            ),
            rx.el.div(rx.foreach(ToolsState.tools, tool_row), class_name="flex flex-col divide-y divide-slate-800 mt-2"),
            id="tools",
            class_name="rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 mt-6",
        ),
    )


def home_page() -> rx.Component:
    return rx.el.div(
        rx.el.header(
            logo(),
            rx.el.div(
                rx.el.a(rx.icon("cpu", class_name="h-3.5 w-3.5"), "Chipova", href="https://chipova.reflex-ddns.com", target="_blank", class_name=BTN_GHOST),
                identity_chip(),
                class_name="flex items-center gap-3",
            ),
            class_name="flex justify-between items-center px-5 h-14 border-b border-slate-800 bg-slate-950",
        ),
        rx.el.main(
            rx.el.div(
                rx.el.div(
                    rx.el.h1("From a finished chip to a working product", class_name="text-2xl font-bold text-slate-100 tracking-tight"),
                    rx.el.p("Take a chip designed in Chipova, put it on a board with an MCU and sensors, write its firmware, run it all in simulation — the chip's real RTL included — drive it through a device API and an app, and release it with the evidence.", class_name="text-sm text-slate-400 max-w-3xl"),
                    class_name="flex flex-col gap-2",
                ),
                flow_strip(),
                tools_panel(),
                class_name="flex flex-col gap-4",
            ),
            rx.el.div(
                rx.el.h2("Products", class_name="text-lg font-bold text-slate-100"),
                rx.el.button(rx.icon("plus", class_name="h-4 w-4"), "New product", on_click=HomeState.set_new_open(True), id="new-project", class_name=BTN_PRIMARY),
                class_name="flex justify-between items-center mt-8",
            ),
            rx.cond(
                HomeState.projects.length() > 0,
                rx.el.div(rx.foreach(HomeState.projects, project_card), id="project-list", class_name="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 mt-3"),
                rx.el.div(empty("hammer", "No products yet", "Make one from a template: the smart temperature controller shows the whole flow."), class_name=f"{PANEL} mt-3"),
            ),
            class_name="max-w-6xl mx-auto px-5 py-8",
        ),
        new_project_dialog(),
        class_name="min-h-screen bg-slate-950 text-slate-200",
    )
