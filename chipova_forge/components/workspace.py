"""A project's page (/p/<id>): the top bar, the rail of the flow's sections, the section on
screen."""

from __future__ import annotations

from typing import Callable

import reflex as rx
from reflex_ddns_auth.intent import Intent

from chipova_forge.components.common import BTN_AI, BTN_GHOST, BTN_OUTLINE, LABEL, empty, logo, status_badge
from chipova_forge.states.room import ProjectRoom
from chipova_forge.states.ui_state import UiState
from chipova_forge.states.workspace import WorkspaceState

SECTIONS = [
    ("overview", "list-checks", "Overview", "The flow's twelve steps and where each stands"),
    ("chip", "cpu", "Chip", "The chip from Chipova and its hardware contract"),
    ("registers", "table-properties", "Registers", "SystemRDL: CSR RTL, C header, Python map"),
    ("board", "circuit-board", "Board", "The MCU, the chip, the sensors: system.yaml"),
    ("firmware", "binary", "Firmware", "The MCU's firmware: edit and build"),
    ("lab", "activity", "Lab", "The simulation: console, sensors, registers, waveform"),
    ("dashboard", "layout-dashboard", "App", "The product's dashboard on the device API"),
    ("tests", "flask-conical", "Tests", "End-to-end tests on the virtual board"),
    ("release", "package", "Release", "The package and its evidence"),
    ("agents", "bot", "AI & API", "AI proposals, the audit log, API tokens, the SDK"),
    ("people", "users", "People", "Who may see, change and approve this product"),
]


def ai_button() -> rx.Component:
    """Opens the AI Hub's assistant on this project (the ai.assist intent)."""
    return rx.el.button(
        rx.icon("sparkles", class_name="w-3.5 h-3.5"),
        rx.el.span("AI"),
        rx.cond(
            ProjectRoom.pending_proposals > 0,
            rx.el.span(ProjectRoom.pending_proposals, class_name="ml-0.5 min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-white text-violet-700 text-[10px] font-bold flex items-center justify-center"),
        ),
        on_click=Intent.start(None, "ai.assist", keep_alive=True, single=True, label="AI assistant", in_app="chipova-forge", room=WorkspaceState.project_id),
        title="Ask the AI Hub's assistant to work on this product (it proposes; you decide)",
        id="ai-button",
        class_name=BTN_AI,
    )


def people() -> rx.Component:
    return rx.el.div(
        rx.foreach(
            ProjectRoom.people,
            lambda p: rx.el.div(
                p["initials"],
                title=p["name"],
                style={"background": p["color"]},
                class_name="h-7 w-7 -ml-1.5 first:ml-0 rounded-full ring-2 ring-slate-950 text-[10px] font-bold text-white flex items-center justify-center bg-slate-600",
            ),
        ),
        id="people",
        class_name="flex items-center",
    )


def top_bar() -> rx.Component:
    return rx.el.header(
        rx.el.div(
            logo(),
            rx.el.div(class_name="w-px h-6 bg-slate-800"),
            rx.el.div(
                rx.el.span(ProjectRoom.title, id="project-title", class_name="text-sm font-semibold text-slate-100 truncate max-w-[18rem]"),
                rx.el.span(WorkspaceState.role, class_name="text-[10px] text-slate-500 uppercase tracking-wider"),
                class_name="flex flex-col leading-tight min-w-0",
            ),
            rx.el.div(
                rx.icon("cpu", class_name="h-3.5 w-3.5 text-teal-400"),
                rx.el.span(ProjectRoom.chip_name, class_name="font-mono"),
                rx.el.span(ProjectRoom.interface.upper(), class_name="text-slate-500"),
                class_name="hidden md:flex items-center gap-1.5 text-xs text-slate-300 px-2 h-7 rounded-md border border-slate-800",
            ),
            class_name="flex items-center gap-3 min-w-0",
        ),
        rx.el.div(
            rx.cond(ProjectRoom.session_state != "", rx.el.button(
                rx.icon("activity", class_name="w-3.5 h-3.5"), status_badge(ProjectRoom.session_state, small=True),
                on_click=WorkspaceState.set_view("lab"), title="The simulation", id="session-chip", class_name=BTN_OUTLINE)),
            ai_button(),
            people(),
            rx.el.a(rx.icon("house", class_name="w-4 h-4"), href="/", title="All products", class_name=BTN_GHOST),
            class_name="flex items-center gap-2",
        ),
        class_name="flex justify-between items-center px-3 bg-slate-950 border-b border-slate-800 h-12 gap-3 shrink-0",
    )


def rail_button(view: str, icon: str, title: str, hint: str) -> rx.Component:
    badge = rx.fragment()
    if view == "agents":
        badge = rx.cond(ProjectRoom.pending_proposals > 0, rx.el.span(class_name="absolute top-1.5 right-2 h-2 w-2 rounded-full bg-violet-400"))
    if view == "lab":
        badge = rx.cond(ProjectRoom.running, rx.el.span(class_name="absolute top-1.5 right-2 h-2 w-2 rounded-full bg-emerald-400 animate-pulse"))
    return rx.el.button(
        rx.icon(icon, class_name="h-5 w-5"),
        rx.el.span(title, class_name="text-[9px] font-medium"),
        badge,
        on_click=WorkspaceState.set_view(view),
        title=f"{title}: {hint}",
        custom_attrs={"data-view": view},
        class_name=rx.cond(
            WorkspaceState.view == view,
            "relative flex flex-col items-center gap-0.5 w-16 py-2 rounded-lg bg-amber-500/15 text-amber-300",
            "relative flex flex-col items-center gap-0.5 w-16 py-2 rounded-lg text-slate-500 hover:text-slate-200 hover:bg-slate-800/60",
        ),
    )


def rail() -> rx.Component:
    return rx.el.nav(
        *[rail_button(*s) for s in SECTIONS],
        class_name="flex flex-col items-center gap-1 py-2 px-1 bg-slate-950 border-r border-slate-800 shrink-0 overflow-y-auto",
    )


def workspace(sections: dict[str, Callable[[], rx.Component]]) -> rx.Component:
    body = rx.match(WorkspaceState.view, *[(k, v()) for k, v in sections.items()], sections["overview"]())
    return rx.cond(
        WorkspaceState.missing | ProjectRoom.gone,
        rx.el.div(empty("search-x", "There is no such product", rx.el.a("All products", href="/", class_name="text-amber-400")), class_name="min-h-screen bg-slate-950"),
        rx.cond(
            WorkspaceState.denied,
            rx.el.div(empty("lock", "You may not see this product", "Ask its owner for access."), class_name="min-h-screen bg-slate-950"),
            rx.el.div(
                top_bar(),
                rx.el.div(
                    rail(),
                    rx.el.main(body, id="section", custom_attrs={"data-section": WorkspaceState.view}, class_name="flex-1 min-w-0 overflow-y-auto"),
                    class_name="flex flex-1 min-h-0",
                ),
                class_name="h-screen flex flex-col bg-slate-950 text-slate-200",
            ),
        ),
    )


def label(text: str) -> rx.Component:
    return rx.el.span(text, class_name=LABEL)


def avatar_note() -> rx.Component:
    me = UiState.me
    return rx.el.span(me["name"])
