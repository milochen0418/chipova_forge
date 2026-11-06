"""Small pieces every page uses (from Chipova's): the logo, status badges, times, empty states."""

from __future__ import annotations

import reflex as rx

BTN = "inline-flex items-center gap-1.5 px-2.5 h-8 rounded-md text-xs font-semibold transition-colors whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed"
BTN_PRIMARY = f"{BTN} bg-amber-600 hover:bg-amber-500 text-white shadow"
BTN_GHOST = f"{BTN} text-slate-300 hover:bg-slate-800 hover:text-white"
BTN_OUTLINE = f"{BTN} border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-200"
BTN_AI = f"{BTN} bg-violet-600 hover:bg-violet-500 text-white shadow"
BTN_DANGER = f"{BTN} bg-rose-700 hover:bg-rose-600 text-white"
PANEL = "bg-slate-900/70 border border-slate-800 rounded-xl"
INPUT = "h-9 px-3 rounded-md bg-slate-950 border border-slate-700 text-sm text-slate-100 outline-none focus:border-amber-500 placeholder:text-slate-600"
LABEL = "text-[11px] font-semibold uppercase tracking-wider text-slate-500"

STATUS_STYLE = {
    "SUCCEEDED": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "Succeeded"),
    "FAILED": ("bg-rose-500/15 text-rose-300 border-rose-500/30", "Failed"),
    "RUNNING": ("bg-sky-500/15 text-sky-300 border-sky-500/30 animate-pulse", "Running"),
    "QUEUED": ("bg-slate-500/15 text-slate-300 border-slate-500/30", "Queued"),
    "CREATED": ("bg-slate-500/15 text-slate-300 border-slate-500/30", "Created"),
    "WAITING": ("bg-amber-500/15 text-amber-300 border-amber-500/30", "Waiting for approval"),
    "CANCELED": ("bg-slate-600/20 text-slate-400 border-slate-600/40", "Canceled"),
    "TIMED_OUT": ("bg-orange-500/15 text-orange-300 border-orange-500/30", "Timed out"),
    "PROPOSED": ("bg-violet-500/15 text-violet-300 border-violet-500/30", "Proposed"),
    "APPROVED": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "Approved"),
    "REJECTED": ("bg-rose-500/15 text-rose-300 border-rose-500/30", "Rejected"),
    "EXPIRED": ("bg-slate-600/20 text-slate-400 border-slate-600/40", "Expired"),
    "PENDING": ("bg-slate-600/20 text-slate-400 border-slate-600/40", "Pending"),
    "SKIPPED": ("bg-slate-600/20 text-slate-500 border-slate-600/40", "Skipped"),
    # Signoff
    "PASS": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "PASS"),
    "FAIL": ("bg-rose-500/15 text-rose-300 border-rose-500/30", "FAIL"),
    "WAIVED": ("bg-amber-500/15 text-amber-300 border-amber-500/30", "WAIVED"),
    "BLOCKED": ("bg-rose-900/30 text-rose-300 border-rose-700/40", "BLOCKED"),
    "NOT_RUN": ("bg-slate-600/20 text-slate-300 border-slate-600/40", "NOT RUN"),
    "NOT_CONFIGURED": ("bg-slate-700/30 text-slate-400 border-slate-600/40", "NOT CONFIGURED"),
    # Tool images
    "INSTALLED": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "Installed"),
    "MISSING": ("bg-amber-500/15 text-amber-300 border-amber-500/30", "Not installed"),
    "INSTALLING": ("bg-sky-500/15 text-sky-300 border-sky-500/30", "Installing"),
    # Releases
    "DRAFT": ("bg-slate-600/20 text-slate-300 border-slate-600/40", "Draft"),
    "IN_REVIEW": ("bg-sky-500/15 text-sky-300 border-sky-500/30", "In review"),
    "READY_RELEASE": ("bg-teal-500/15 text-teal-300 border-teal-500/30", "Ready"),
    "FOUNDRY_READY": ("bg-teal-500/15 text-teal-300 border-teal-500/30", "Foundry ready"),
    # Forge: steps, sessions, tests, releases
    "DONE": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "Done"),
    "ATTENTION": ("bg-amber-500/15 text-amber-300 border-amber-500/30", "Needs attention"),
    "TODO": ("bg-slate-600/20 text-slate-300 border-slate-600/40", "To do"),
    "LATER": ("bg-slate-700/30 text-slate-500 border-slate-600/40", "Later version"),
    "NONE": ("bg-slate-700/30 text-slate-500 border-slate-600/40", "Not built"),
    "STARTING": ("bg-sky-500/15 text-sky-300 border-sky-500/30 animate-pulse", "Starting"),
    "READY": ("bg-sky-500/15 text-sky-300 border-sky-500/30", "Ready"),
    "PAUSED": ("bg-amber-500/15 text-amber-300 border-amber-500/30", "Paused"),
    "STOPPING": ("bg-slate-500/15 text-slate-300 border-slate-500/30 animate-pulse", "Stopping"),
    "STOPPED": ("bg-slate-600/20 text-slate-400 border-slate-600/40", "Stopped"),
    "PASSED": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "Passed"),
    "ACCEPTED": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "Accepted"),
    "RUNNING_TEST": ("bg-sky-500/15 text-sky-300 border-sky-500/30 animate-pulse", "Running"),
    # Board jobs
    "SENT": ("bg-sky-500/15 text-sky-300 border-sky-500/30", "Sent to the helper"),
    "PROGRAMMING": ("bg-sky-500/15 text-sky-300 border-sky-500/30 animate-pulse", "Programming"),
    "DONE": ("bg-emerald-500/15 text-emerald-300 border-emerald-500/30", "Done"),
}


def status_badge(status: rx.Var | str, small: bool = False) -> rx.Component:
    """A coloured badge for any status (runs, steps, proposals, signoff, releases)."""
    size = "text-[10px] px-1.5 py-0" if small else "text-[11px] px-2 py-0.5"
    cases = [
        (k, rx.el.span(label, class_name=f"inline-flex items-center rounded border font-semibold whitespace-nowrap {size} {style}", custom_attrs={"data-status": k}))
        for k, (style, label) in STATUS_STYLE.items()
    ]
    return rx.match(
        status,
        *cases,
        rx.el.span(status, class_name=f"inline-flex items-center rounded border border-slate-700 text-slate-300 font-semibold {size}"),
    )


def logo() -> rx.Component:
    return rx.el.a(
        rx.el.div(
            rx.icon("hammer", class_name="h-5 w-5 text-amber-400"),
            rx.el.span("Chipova", class_name="text-[15px] font-bold tracking-tight text-slate-100"),
            rx.el.span("Forge", class_name="text-[15px] font-bold tracking-tight text-amber-400"),
            class_name="flex items-center gap-1.5",
        ),
        href="/",
        title="All projects",
    )


def ago(ts: rx.Var, prefix: str = "") -> rx.Component:
    """A time as "5 min ago", kept current in the browser (assets/forge/forge.js)."""
    return rx.el.span(prefix, custom_attrs={"data-ago": ts.to(str)}, class_name="whitespace-nowrap")


def empty(icon: str, text: str, hint: str | rx.Component = "") -> rx.Component:
    return rx.el.div(
        rx.icon(icon, class_name="h-8 w-8 text-slate-600"),
        rx.el.p(text, class_name="text-sm text-slate-400 font-medium"),
        rx.fragment() if isinstance(hint, str) and not hint else rx.el.div(hint, class_name="text-xs text-slate-500 text-center max-w-sm"),
        class_name="flex flex-col items-center justify-center gap-2 py-10 px-4",
    )


def kv(label: rx.Var | str, value: rx.Var | str) -> rx.Component:
    return rx.el.div(
        rx.el.span(label, class_name="text-[11px] text-slate-500"),
        rx.el.span(value, class_name="text-sm font-semibold text-slate-100 font-mono break-all"),
        class_name="flex flex-col gap-0.5 min-w-0",
    )


def section_title(text: str, sub: str | rx.Var = "") -> rx.Component:
    return rx.el.div(
        rx.el.h2(text, class_name="text-lg font-bold text-slate-100 tracking-tight"),
        rx.fragment() if isinstance(sub, str) and not sub else rx.el.p(sub, class_name="text-sm text-slate-400"),
        class_name="flex flex-col gap-0.5",
    )
