"""The project page's other sections: Lab (the simulation), App (the device's dashboard), Tests,
Release, AI & API, People."""

from __future__ import annotations

import reflex as rx

from chipova_forge.components.common import BTN_GHOST, BTN_OUTLINE, BTN_PRIMARY, BTN_DANGER, INPUT, LABEL, PANEL, ago, empty, status_badge
from chipova_forge.components.links import backend_url
from chipova_forge.components.sections_a import TD, TH, download_link, editor_panel, file_button, new_file_dialog, page, panel
from chipova_forge.states.room import AnalogRow, BusRow, LedRow, ProjectRoom, ProposalRow, ReleaseRow, SensorRow, TestRow, TileRow, UartRow
from chipova_forge.states.workspace import AuditRow, CheckRow, CommandRow, DiffLineRow, DiffRow, MemberRow, StepResultRow, WorkspaceState

LED_ON = {"emerald": "bg-emerald-400 shadow-[0_0_14px_rgba(52,211,153,0.9)]", "amber": "bg-amber-400 shadow-[0_0_14px_rgba(251,191,36,0.9)]",
          "rose": "bg-rose-500 shadow-[0_0_14px_rgba(244,63,94,0.9)]", "sky": "bg-sky-400 shadow-[0_0_14px_rgba(56,189,248,0.9)]"}


# ------------------------------------------------------------------ Lab


def session_bar() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.cond(ProjectRoom.session_state != "", status_badge(ProjectRoom.session_state), status_badge("NOT_RUN")),
            rx.cond(ProjectRoom.session_id != "", rx.el.span(ProjectRoom.session_id, id="session-id", class_name="font-mono text-xs text-slate-400")),
            rx.cond(ProjectRoom.running, rx.el.span("t = ", ProjectRoom.session_vt, " s", id="virtual-time", class_name="font-mono text-xs text-slate-300")),
            rx.cond(ProjectRoom.session_by != "", rx.el.span("started by ", ProjectRoom.session_by, class_name="text-xs text-slate-500")),
            class_name="flex items-center gap-3 flex-wrap",
        ),
        rx.el.div(
            rx.cond(
                ProjectRoom.running,
                rx.fragment(
                    rx.cond(ProjectRoom.session_state == "PAUSED",
                            rx.el.button(rx.icon("play", class_name="h-3.5 w-3.5"), "Resume", on_click=WorkspaceState.control("start"), disabled=~WorkspaceState.can_simulate, class_name=BTN_OUTLINE),
                            rx.el.button(rx.icon("pause", class_name="h-3.5 w-3.5"), "Pause", on_click=WorkspaceState.control("pause"), disabled=~WorkspaceState.can_simulate, id="pause", class_name=BTN_OUTLINE)),
                    rx.el.button(rx.icon("rotate-ccw", class_name="h-3.5 w-3.5"), "Reset", on_click=WorkspaceState.control("reset"), disabled=~WorkspaceState.can_simulate, class_name=BTN_OUTLINE),
                    rx.el.button(rx.icon("square", class_name="h-3.5 w-3.5"), "Stop", on_click=WorkspaceState.stop_session, disabled=~WorkspaceState.can_simulate, id="stop-session", class_name=BTN_DANGER),
                ),
                rx.el.button(rx.icon("play", class_name="h-4 w-4"), "Start the simulation", on_click=WorkspaceState.start_session,
                             disabled=~WorkspaceState.can_simulate | (WorkspaceState.busy != ""), id="start-session", class_name=BTN_PRIMARY),
            ),
            class_name="flex items-center gap-2",
        ),
        class_name=f"{PANEL} flex justify-between items-center gap-3 px-4 py-3 flex-wrap",
    )


def led(l: LedRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(class_name=rx.cond(l.on, rx.match(l.color, *[(k, f"h-5 w-5 rounded-full {v}") for k, v in LED_ON.items()], "h-5 w-5 rounded-full bg-emerald-400"),
                                     "h-5 w-5 rounded-full bg-slate-700 border border-slate-600")),
        rx.el.span(l.name, class_name="text-[11px] font-mono text-slate-400"),
        custom_attrs={"data-led": l.name, "data-on": rx.cond(l.on, "1", "0")},
        class_name="flex flex-col items-center gap-1 w-20",
    )


def sensor_control(s: SensorRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.span(s.title, class_name="text-xs text-slate-400"),
            rx.el.span(s.value.to(str), " ", s.unit, custom_attrs={"data-sensor-value": s.sensor}, class_name="text-lg font-bold text-slate-100 font-mono whitespace-nowrap shrink-0"),
            class_name="flex justify-between items-baseline gap-3",
        ),
        rx.cond(
            s.driven,
            rx.el.p(rx.icon("waves", class_name="h-3.5 w-3.5 inline mr-1"), "Set by the analog side (ngspice), each step: change its inputs below.",
                    class_name="text-xs text-sky-300"),
            rx.fragment(
                rx.el.input(type="range", min=s.min, max=s.max, step=s.step, default_value=s.value.to(str), key=s.sensor + "-" + s.setting + "-" + s.value.to(str),
                            on_change=lambda v: WorkspaceState.set_slider(s.sensor + "." + s.setting, v),
                            on_mouse_up=WorkspaceState.commit_slider(s.sensor, s.setting),
                            on_key_up=WorkspaceState.commit_slider(s.sensor, s.setting),
                            disabled=~WorkspaceState.can_operate | ~ProjectRoom.running,
                            custom_attrs={"data-sensor": s.sensor}, class_name="w-full accent-amber-500"),
                rx.el.div(
                    *[rx.el.button(f"{v}", on_click=WorkspaceState.set_sensor(s.sensor, s.setting, str(v)), disabled=~WorkspaceState.can_operate | ~ProjectRoom.running,
                                   custom_attrs={"data-preset": f"{v}"}, class_name="px-2 h-6 rounded bg-slate-800 hover:bg-slate-700 text-xs text-slate-200")
                      for v in (20, 25, 30, 35, 40, 45)],
                    class_name="flex gap-1 flex-wrap",
                ),
            ),
        ),
        class_name="flex flex-col gap-2",
    )


def analog_input(i: AnalogRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.span(i.title, class_name="text-xs text-slate-400"),
            rx.el.span(i.value.to(str), " ", i.unit, custom_attrs={"data-analog-input": i.name}, class_name="text-sm font-bold text-slate-100 font-mono whitespace-nowrap shrink-0"),
            class_name="flex justify-between items-baseline gap-3",
        ),
        # Keyed by the value too: when someone else (or the AI) changes it, the slider moves there.
        rx.el.input(type="range", min=i.min, max=i.max, step=i.step, default_value=i.value.to(str), key="analog-" + i.name + "-" + i.value.to(str),
                    on_change=lambda v: WorkspaceState.set_slider("analog." + i.name, v),
                    on_mouse_up=WorkspaceState.commit_slider("analog", i.name),
                    on_key_up=WorkspaceState.commit_slider("analog", i.name),
                    disabled=~WorkspaceState.can_operate | ~ProjectRoom.running,
                    custom_attrs={"data-analog-slider": i.name}, class_name="w-full accent-sky-500"),
        class_name="flex flex-col gap-1",
    )


def analog_panel() -> rx.Component:
    """The board's analog side: ngspice solving its circuit in step with the board."""
    return panel(
        "Analog side (ngspice, in step with the board)",
        rx.el.p(ProjectRoom.analog_title, class_name="text-sm text-slate-200 font-semibold"),
        rx.el.div(
            rx.foreach(ProjectRoom.analog_outputs, lambda o: rx.el.div(
                rx.el.span(o.title, class_name="text-[11px] text-slate-500"),
                rx.el.span(o.value.to(str), " ", o.unit, custom_attrs={"data-analog-output": o.name}, class_name="text-2xl font-bold text-pink-300 font-mono"),
                class_name="flex flex-col")),
            rx.foreach(ProjectRoom.analog_drives, lambda d: rx.el.div(
                rx.el.span(d.title, class_name="text-[11px] text-slate-500"),
                rx.el.span(rx.cond(d.on, "ON", "off"), custom_attrs={"data-analog-drive": d.name, "data-on": rx.cond(d.on, "1", "0")},
                           class_name=rx.cond(d.on, "text-lg font-bold text-sky-300", "text-lg font-bold text-slate-500")),
                class_name="flex flex-col")),
            class_name="flex gap-8 flex-wrap",
        ),
        rx.cond(ProjectRoom.analog_svg != "", rx.html(ProjectRoom.analog_svg, id="analog-chart", class_name="mt-2"),
                rx.el.p("Start the simulation: the chart follows the circuit.", class_name="text-xs text-slate-500 mt-2")),
        rx.el.div(rx.foreach(ProjectRoom.analog_inputs, analog_input), class_name="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3"),
        id="analog",
    )


def uart_line(u: UartRow) -> rx.Component:
    return rx.el.div(
        rx.el.span(u.vt, class_name="text-slate-600 w-16 shrink-0 text-right"),
        rx.el.span(rx.cond(u.dir == "in", "→", "←"), class_name=rx.cond(u.dir == "in", "text-sky-400 w-4 shrink-0", "text-emerald-400 w-4 shrink-0")),
        rx.el.span(u.line, class_name=rx.cond(u.event != "", rx.match(u.event, ("alarm", "text-rose-300 font-semibold"), ("normal", "text-emerald-300"), "text-slate-100"), "text-slate-200")),
        custom_attrs={"data-uart-line": u.line},
        class_name="flex gap-2 font-mono text-xs leading-5",
    )


def bus_row(b: BusRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(b.vt, class_name=f"{TD} font-mono text-xs text-slate-500"),
        rx.el.td(rx.el.span(b.op, class_name=rx.cond(b.op == "write", "text-amber-300", "text-sky-300")), class_name=f"{TD} text-xs"),
        rx.el.td(b.register, class_name=f"{TD} font-mono text-xs text-slate-100"),
        rx.el.td(b.value, class_name=f"{TD} font-mono text-xs text-slate-300"),
        rx.el.td(b.via, class_name=f"{TD} text-xs text-slate-500"),
    )


def lab_view() -> rx.Component:
    return page(
        "Lab", "The board in simulation: the firmware on Renode's STM32F407, the chip's own RTL in Verilator, the sensors' models. Everyone here sees the same board.",
        session_bar(),
        rx.cond(ProjectRoom.session_error != "", rx.el.p(ProjectRoom.session_error, id="session-error", class_name="text-sm text-rose-300 bg-rose-950/40 border border-rose-900 rounded-md p-3")),
        rx.cond(ProjectRoom.analog_title != "", analog_panel()),
        rx.el.div(
            rx.el.div(
                panel("LEDs", rx.cond(ProjectRoom.leds.length() > 0, rx.el.div(rx.foreach(ProjectRoom.leds, led), id="leds", class_name="flex flex-wrap gap-3"),
                                      rx.el.p("Start the simulation to see the board.", class_name="text-sm text-slate-500"))),
                panel("Environment", rx.cond(ProjectRoom.sensors.length() > 0, rx.el.div(rx.foreach(ProjectRoom.sensors, sensor_control), class_name="flex flex-col gap-4"),
                                             rx.el.p("No sensors on this board, or no simulation.", class_name="text-sm text-slate-500"))),
                panel("Chip registers (probe)",
                      rx.el.div(
                          rx.el.select(rx.el.option("register…", value=""), rx.foreach(WorkspaceState.registers, lambda r: rx.el.option(r.name, value=r.name)),
                                       value=WorkspaceState.reg_name, on_change=WorkspaceState.set_reg_name, id="reg-select", class_name=f"{INPUT} w-full"),
                          rx.el.div(
                              rx.el.button("Read", on_click=WorkspaceState.read_register, disabled=~WorkspaceState.can_operate | ~ProjectRoom.running, id="reg-read", class_name=BTN_OUTLINE),
                              rx.el.input(value=WorkspaceState.reg_value_input, on_change=WorkspaceState.set_reg_value_input, placeholder="0x…", class_name=f"{INPUT} w-28 font-mono"),
                              rx.el.button("Write", on_click=WorkspaceState.write_register, disabled=~WorkspaceState.can_operate | ~ProjectRoom.running, class_name=BTN_OUTLINE),
                              class_name="flex gap-2",
                          ),
                          rx.cond(WorkspaceState.reg_result != "", rx.el.p(WorkspaceState.reg_result, id="reg-result", class_name="text-xs font-mono text-emerald-300 break-all")),
                          rx.el.p("Reads the chip as a probe would, through its own interface, between the MCU's frames.", class_name="text-[11px] text-slate-500"),
                          class_name="flex flex-col gap-2",
                      )),
                class_name="flex flex-col gap-3",
            ),
            rx.el.div(
                panel("Console (the device's UART)",
                      rx.el.div(rx.foreach(ProjectRoom.uart, uart_line), id="console", custom_attrs={"data-autoscroll": "1"},
                                class_name="h-72 overflow-y-auto bg-black/50 rounded p-2"),
                      rx.el.div(
                          rx.el.input(value=WorkspaceState.uart_input, on_change=WorkspaceState.set_uart_input, on_key_down=WorkspaceState.uart_key,
                                      placeholder="A line to the device, e.g. GET STATUS", id="uart-input", disabled=~ProjectRoom.running,
                                      class_name=f"{INPUT} flex-1 font-mono"),
                          rx.el.button(rx.icon("send", class_name="h-3.5 w-3.5"), "Send", on_click=WorkspaceState.send_uart, disabled=~WorkspaceState.can_operate | ~ProjectRoom.running,
                                       id="uart-send", class_name=BTN_OUTLINE),
                          class_name="flex gap-2 mt-2",
                      )),
                panel("The chip's register transactions",
                      rx.el.div(rx.el.table(rx.el.thead(rx.el.tr(*[rx.el.th(h, class_name=TH) for h in ("t (s)", "", "Register", "Value", "Via")])),
                                            rx.el.tbody(rx.foreach(ProjectRoom.bus, bus_row)), class_name="w-full"),
                                id="bus", custom_attrs={"data-autoscroll": "1"}, class_name="h-64 overflow-y-auto"),
                      right=rx.el.span(ProjectRoom.events_total.to(str), " events", class_name="text-[11px] text-slate-500")),
                class_name="flex flex-col gap-3",
            ),
            class_name="grid grid-cols-1 xl:grid-cols-[22rem_1fr] gap-3",
        ),
        panel("The chip's signals (waveform)",
              rx.el.div(
                  rx.el.button(rx.icon("circle-dot", class_name="h-3.5 w-3.5"), "Record", on_click=WorkspaceState.record_wave,
                               disabled=~WorkspaceState.can_operate | ~ProjectRoom.running, id="record-wave", class_name=BTN_OUTLINE),
                  rx.el.button(rx.icon("chart-line", class_name="h-3.5 w-3.5"), "Show", on_click=WorkspaceState.show_wave, id="show-wave", class_name=BTN_OUTLINE),
                  rx.el.span(WorkspaceState.wave_info, class_name="text-xs text-slate-500"),
                  class_name="flex items-center gap-2 mb-2",
              ),
              rx.cond(WorkspaceState.wave_svg != "", rx.html(WorkspaceState.wave_svg, id="waveform"),
                      rx.el.p("Record the chip's signals for a moment, then show them: every bus transaction is recorded too.", class_name="text-sm text-slate-500"))),
        # Re-mounted (so its links are signed again for this tab) whenever another simulation ends.
        # (The key goes on a wrapper: on the element itself Reflex puts it inside the component
        # that runs on_mount, which then never re-mounts.)
        rx.el.div(rx.el.div(on_mount=WorkspaceState.load_evidence), key=ProjectRoom.evidence_session, class_name="hidden"),
        rx.cond(WorkspaceState.evidence.length() > 0,
                panel("The last simulation's evidence", rx.el.div(rx.foreach(WorkspaceState.evidence, download_link), class_name="flex flex-wrap gap-2"))),
    )


# ------------------------------------------------------------------ App (dashboard)


def tile(t: TileRow) -> rx.Component:
    return rx.el.div(
        rx.el.span(t.name, class_name="text-[11px] uppercase tracking-wider text-slate-500"),
        rx.el.div(rx.el.span(t.value, class_name="text-2xl font-bold font-mono"), rx.el.span(t.unit, class_name="text-sm text-slate-400"), class_name="flex items-baseline gap-1"),
        custom_attrs={"data-tile": t.name},
        class_name=rx.cond(t.alert, "rounded-xl p-4 border border-rose-500/60 bg-rose-950/40 text-rose-100 flex flex-col gap-1",
                           "rounded-xl p-4 border border-slate-800 bg-slate-900/70 text-slate-100 flex flex-col gap-1"),
    )


def command_row(c: CommandRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(c.name, class_name=f"{TD} font-mono text-slate-100"),
        rx.el.td(c.title, class_name=f"{TD} text-xs text-slate-400"),
        rx.el.td(c.args, class_name=f"{TD} text-xs font-mono text-slate-500"),
        rx.el.td(rx.cond(c.args == "", rx.el.button("Send", on_click=WorkspaceState.device_command(c.name, ""), disabled=~WorkspaceState.can_operate | ~ProjectRoom.running,
                                                    custom_attrs={"data-command": c.name}, class_name=BTN_GHOST)), class_name=TD),
    )


def dashboard_view() -> rx.Component:
    return page(
        "The product's app", "A dashboard built only on the device API: the same commands an app sends to the real product over its serial port. Backend: the virtual board.",
        rx.cond(~ProjectRoom.running,
                rx.el.div(rx.el.span("The device is off: start the simulation."),
                          rx.el.button(rx.icon("play", class_name="h-4 w-4"), "Start", on_click=WorkspaceState.start_session, disabled=~WorkspaceState.can_simulate, class_name=BTN_PRIMARY),
                          class_name=f"{PANEL} flex justify-between items-center p-4 text-sm text-slate-300")),
        # There all the time the device runs, only its colour changes: a banner that came and went
        # would move the page under the user's pointer.
        rx.cond(ProjectRoom.running,
                rx.cond(ProjectRoom.alert,
                        rx.el.div(rx.icon("siren", class_name="h-5 w-5"), rx.el.span("ALARM: above the threshold", class_name="font-bold"),
                                  id="alarm-banner", class_name="flex items-center gap-2 p-3 rounded-xl border border-rose-500 bg-rose-600/90 text-white animate-pulse"),
                        rx.el.div(rx.icon("circle-check", class_name="h-5 w-5"), rx.el.span("No alarm", class_name="font-bold"),
                                  id="no-alarm", class_name="flex items-center gap-2 p-3 rounded-xl border border-slate-800 bg-slate-900/70 text-emerald-300"))),
        rx.el.div(rx.foreach(ProjectRoom.tiles, tile), id="tiles", class_name="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3"),
        rx.el.div(
            panel("Controls",
                  rx.el.div(
                      rx.el.div(
                          rx.el.span("Alarm threshold (°C)", class_name="text-sm text-slate-300"),
                          rx.el.div(
                              rx.el.input(type="number", step="0.5", value=WorkspaceState.dash_control_value["set_threshold"].to(str),
                                          on_change=lambda v: WorkspaceState.set_control_value("set_threshold", v), id="threshold-input", class_name=f"{INPUT} w-28 font-mono"),
                              rx.el.button("Apply", on_click=WorkspaceState.device_command("set_threshold", "celsius"), disabled=~WorkspaceState.can_operate | ~ProjectRoom.running,
                                           id="apply-threshold", class_name=BTN_PRIMARY),
                              rx.el.button("Clear the alarm", on_click=WorkspaceState.device_command("clear_alarm", ""), disabled=~WorkspaceState.can_operate | ~ProjectRoom.running, class_name=BTN_OUTLINE),
                              rx.el.button(rx.icon("refresh-cw", class_name="h-3.5 w-3.5"), "Refresh", on_click=WorkspaceState.refresh_status, disabled=~ProjectRoom.running, id="refresh-status", class_name=BTN_GHOST),
                              class_name="flex gap-2 items-center flex-wrap",
                          ),
                          class_name="flex flex-col gap-2",
                      ),
                      rx.cond(WorkspaceState.dash_message != "", rx.el.p(WorkspaceState.dash_message, id="dash-message", class_name="text-xs font-mono text-slate-400")),
                      rx.el.div(rx.el.span("All commands (device.yaml)", class_name=LABEL),
                                rx.el.table(rx.el.tbody(rx.foreach(WorkspaceState.commands, command_row)), class_name="w-full"),
                                class_name="flex flex-col gap-1 mt-2"),
                      class_name="flex flex-col gap-3",
                  )),
            rx.el.div(
                panel("The virtual environment", rx.cond(ProjectRoom.sensors.length() > 0, rx.el.div(rx.foreach(ProjectRoom.sensors, sensor_control), class_name="flex flex-col gap-3"),
                                                          rx.el.p("No simulation.", class_name="text-sm text-slate-500")),
                      right=rx.el.span("only on the virtual board", class_name="text-[10px] text-slate-500")),
                panel("The same from Python (SDK)",
                      rx.el.pre(WorkspaceState.sdk_snippet, class_name="text-xs font-mono text-slate-300 bg-black/40 rounded p-3 whitespace-pre-wrap"),
                      rx.el.p("Make a token on the AI & API page. sdk/ in the repository has the SDK; with backend=serial it talks to a real board.", class_name="text-[11px] text-slate-500 mt-2")),
                class_name="flex flex-col gap-3",
            ),
            class_name="grid grid-cols-1 xl:grid-cols-2 gap-3",
        ),
    )


# ------------------------------------------------------------------ Tests


def test_row(t: TestRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.span(t.name, class_name="text-sm font-semibold text-slate-100"),
            rx.el.span(t.path, class_name="text-xs font-mono text-slate-500"),
            class_name="flex flex-col min-w-0",
        ),
        rx.el.div(
            status_badge(t.status, small=True),
            rx.cond(t.total > 0, rx.el.span(t.passed.to(str), "/", t.total.to(str), class_name="text-xs text-slate-400")),
            rx.cond(t.run != "", rx.el.button("Steps", on_click=WorkspaceState.show_test(t.run), class_name=BTN_GHOST)),
            rx.el.button(rx.icon("pen", class_name="h-3.5 w-3.5"), on_click=WorkspaceState.open_file(t.path), title="Edit", class_name=BTN_GHOST),
            rx.el.button(rx.icon("play", class_name="h-3.5 w-3.5"), "Run", on_click=WorkspaceState.run_test(t.path),
                         disabled=~WorkspaceState.can_test | (WorkspaceState.busy != ""), custom_attrs={"data-run-test": t.path}, class_name=BTN_OUTLINE),
            class_name="flex items-center gap-2 shrink-0",
        ),
        custom_attrs={"data-test": t.path, "data-status": t.status},
        class_name="flex justify-between items-center gap-3 py-2",
    )


def step_result(s: StepResultRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(s.n, class_name=f"{TD} text-slate-500 font-mono"),
        rx.el.td(status_badge(s.status, small=True), class_name=TD),
        rx.el.td(s.text, class_name=f"{TD} text-slate-200"),
        rx.el.td(s.detail, class_name=f"{TD} font-mono text-xs text-slate-400 break-all"),
        rx.el.td(s.vt, class_name=f"{TD} font-mono text-xs text-slate-500"),
        custom_attrs={"data-step-result": s.n.to(str), "data-status": s.status},
    )


def tests_view() -> rx.Component:
    return page(
        "Tests", "Each test runs in a fresh simulation: the steps drive the board's environment and the device's commands, and check its answers, events, LEDs and the chip's registers.",
        rx.el.div(
            panel("Tests (tests/*.yaml)",
                  rx.el.div(rx.foreach(ProjectRoom.tests, test_row), id="tests", class_name="flex flex-col divide-y divide-slate-800"),
                  rx.el.button(rx.icon("plus", class_name="h-3.5 w-3.5"), "New test", on_click=WorkspaceState.set_new_file_open(True), disabled=~WorkspaceState.can_edit, class_name=f"{BTN_GHOST} mt-2")),
            panel("Result",
                  rx.cond(WorkspaceState.test_run_id != "",
                          rx.el.div(
                              rx.el.div(status_badge(WorkspaceState.test_status), rx.el.span(WorkspaceState.test_title, class_name="text-sm text-slate-300"),
                                        rx.el.span(WorkspaceState.test_run_id, class_name="font-mono text-xs text-slate-500"), class_name="flex items-center gap-2 flex-wrap"),
                              rx.cond(WorkspaceState.test_error != "", rx.el.p(WorkspaceState.test_error, class_name="text-sm text-rose-300")),
                              rx.el.table(rx.el.thead(rx.el.tr(*[rx.el.th(h, class_name=TH) for h in ("#", "", "Step", "What happened", "t (s)")])),
                                          rx.el.tbody(rx.foreach(WorkspaceState.test_steps, step_result)), id="test-steps", class_name="w-full"),
                              class_name="flex flex-col gap-2",
                          ),
                          rx.el.p("Run a test, or pick one's steps.", class_name="text-sm text-slate-500"))),
            class_name="grid grid-cols-1 xl:grid-cols-2 gap-3",
        ),
        rx.cond(WorkspaceState.open_path.startswith("tests/"), editor_panel("40vh")),
        new_file_dialog(),
    )


# ------------------------------------------------------------------ Release


def check_row(c: CheckRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(status_badge(c.status, small=True), class_name=TD),
        rx.el.td(c.title, class_name=f"{TD} text-slate-100"),
        rx.el.td(c.detail, class_name=f"{TD} text-xs text-slate-400"),
        custom_attrs={"data-check": c.name, "data-status": c.status},
    )


def release_row(r: ReleaseRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(rx.el.span(r.title, class_name="text-sm font-semibold text-slate-100"),
                  rx.el.span(r.id, " · ", r.by, " · ", r.passed.to(str), "/", r.total.to(str), " checks", class_name="text-xs text-slate-500"), class_name="flex flex-col"),
        rx.el.div(
            status_badge(r.status, small=True),
            rx.el.a(rx.icon("download", class_name="h-3.5 w-3.5"), "Package", href=backend_url(WorkspaceState.release_links[r.id]), download=True,
                    custom_attrs={"data-package": r.id}, class_name=BTN_GHOST),
            rx.cond((r.status == "READY") & WorkspaceState.can_approve & (r.by_user != WorkspaceState.my_user),
                    rx.el.button("Approve", on_click=WorkspaceState.approve_release(r.id), custom_attrs={"data-approve": r.id}, class_name=BTN_OUTLINE)),
            class_name="flex items-center gap-2",
        ),
        custom_attrs={"data-release": r.id, "data-status": r.status},
        class_name="flex justify-between items-center gap-3 py-2",
    )


def release_view() -> rx.Component:
    return page(
        "Release", "One package: the chip's snapshot, the board, the firmware, the builds, each test's evidence and the checks, every file with its SHA-256.",
        # Re-mounted when a release appears (the AI's too): its download link is signed for this tab.
        rx.el.div(rx.el.div(on_mount=WorkspaceState.load_release_links), key=ProjectRoom.releases.length().to(str), class_name="hidden"),
        panel("What a release would say now",
              rx.el.table(rx.el.tbody(rx.foreach(WorkspaceState.checks, check_row)), id="checks", class_name="w-full"),
              rx.el.p("Passing means the digital design works in simulation. It is not a foundry signoff or a hardware qualification: the package's README says what isn't covered.",
                      class_name="text-xs text-slate-500 mt-2"),
              right=rx.el.button(rx.icon("package", class_name="h-3.5 w-3.5"), "Make a release", on_click=WorkspaceState.make_release,
                                 disabled=~WorkspaceState.can_release | (WorkspaceState.busy != ""), id="make-release", class_name=BTN_PRIMARY)),
        panel("Releases", rx.cond(ProjectRoom.releases.length() > 0, rx.el.div(rx.foreach(ProjectRoom.releases, release_row), id="releases", class_name="flex flex-col divide-y divide-slate-800"),
                                  rx.el.p("None yet.", class_name="text-sm text-slate-500"))),
    )


# ------------------------------------------------------------------ AI & API


def proposal_row(p: ProposalRow) -> rx.Component:
    return rx.el.div(
        rx.el.div(rx.el.span(p.title, class_name="text-sm font-semibold text-slate-100"),
                  rx.el.span(p.agent, " · ", p.files, class_name="text-xs text-slate-500 font-mono"),
                  rx.cond(p.reason != "", rx.el.span("why rejected: ", p.reason, class_name="text-xs text-rose-300")), class_name="flex flex-col min-w-0"),
        rx.el.div(status_badge(p.status, small=True),
                  rx.cond(p.status == "PROPOSED", rx.el.button("Review", on_click=WorkspaceState.show_proposal(p.id), custom_attrs={"data-review": p.id}, class_name=BTN_OUTLINE)),
                  class_name="flex items-center gap-2 shrink-0"),
        class_name="flex justify-between items-center gap-3 py-2",
    )


def diff_line(d: DiffLineRow) -> rx.Component:
    """One line of the diff: the changed lines in colour, a few around them, the file and where."""
    num = "w-10 shrink-0 text-right pr-2 select-none text-slate-600"
    return rx.match(
        d.kind,
        ("file", rx.el.div(rx.icon("file-code-2", class_name="h-3.5 w-3.5"), d.text, class_name="flex items-center gap-1.5 px-2 py-1.5 mt-2 first:mt-0 text-amber-300 font-semibold bg-slate-900 rounded-t")),
        ("hunk", rx.el.div(d.text, class_name="px-2 py-0.5 text-sky-400/80 bg-sky-950/30")),
        ("add", rx.el.div(rx.el.span(d.old_no, class_name=num), rx.el.span(d.new_no, class_name=num), rx.el.span("+", class_name="w-4 shrink-0 text-emerald-400"),
                          rx.el.span(d.text, class_name="whitespace-pre-wrap break-all"), custom_attrs={"data-diff": "add"},
                          class_name="flex bg-emerald-500/15 text-emerald-100")),
        ("del", rx.el.div(rx.el.span(d.old_no, class_name=num), rx.el.span(d.new_no, class_name=num), rx.el.span("−", class_name="w-4 shrink-0 text-rose-400"),
                          rx.el.span(d.text, class_name="whitespace-pre-wrap break-all line-through decoration-rose-400/50"), custom_attrs={"data-diff": "del"},
                          class_name="flex bg-rose-500/15 text-rose-100")),
        rx.el.div(rx.el.span(d.old_no, class_name=num), rx.el.span(d.new_no, class_name=num), rx.el.span(" ", class_name="w-4 shrink-0"),
                  rx.el.span(d.text, class_name="whitespace-pre-wrap break-all"), class_name="flex text-slate-400"),
    )


def diff_count(d: DiffRow) -> rx.Component:
    return rx.el.span(d.path, " ", rx.el.span("+", d.added.to(str), class_name="text-emerald-400"), " ",
                      rx.el.span("−", d.removed.to(str), class_name="text-rose-400"), class_name="font-mono text-xs text-slate-300")


def proposal_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("The AI's proposal"),
            rx.el.p(WorkspaceState.proposal_title, id="proposal-title", class_name="text-base font-semibold text-slate-100"),
            rx.cond(WorkspaceState.proposal_why != "", rx.el.p(rx.el.span("Why: ", class_name="text-slate-500"), WorkspaceState.proposal_why,
                                                               class_name="text-sm text-slate-300 mt-1")),
            rx.el.div(rx.foreach(WorkspaceState.proposal_diff, diff_count), class_name="flex gap-4 flex-wrap mt-2"),
            rx.el.div(rx.foreach(WorkspaceState.proposal_lines, diff_line), id="proposal-diff",
                      class_name="flex flex-col mt-2 max-h-[52vh] overflow-y-auto rounded border border-slate-800 bg-black/40 font-mono text-[12px] leading-5"),
            rx.dialog.description("Only what changes, with a few lines around it. Accept saves the files as yours; reject tells the AI why.", size="2", class_name="mt-2"),
            # Uncontrolled (a controlled textarea breaks Chinese input methods); a new proposal gets a new box.
            rx.el.textarea(default_value="", key=WorkspaceState.proposal_id, on_change=WorkspaceState.set_reject_reason, rows="2",
                           placeholder="Why (if you reject it)", id="reject-reason",
                           class_name="w-full mt-3 p-2 rounded-md bg-slate-950 border border-slate-700 text-sm text-slate-100"),
            rx.el.div(
                rx.button("Close", variant="soft", color_scheme="gray", on_click=WorkspaceState.close_proposal),
                rx.button("Reject", color_scheme="red", on_click=WorkspaceState.decide_proposal(False), disabled=~WorkspaceState.can_decide, id="reject-proposal"),
                rx.button("Accept", color_scheme="green", on_click=WorkspaceState.decide_proposal(True), disabled=~WorkspaceState.can_decide, id="accept-proposal"),
                class_name="flex justify-end gap-2 mt-3",
            ),
            max_width="1000px",
        ),
        open=WorkspaceState.proposal_id != "",
    )


def audit_row(a: AuditRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(ago(a.at), class_name=f"{TD} text-xs text-slate-500 whitespace-nowrap"),
        rx.el.td(a.who, class_name=f"{TD} text-xs " + "text-slate-200"),
        rx.el.td(a.action, class_name=f"{TD} font-mono text-xs text-slate-300"),
        rx.el.td(a.target, class_name=f"{TD} font-mono text-xs text-slate-500"),
        rx.el.td(rx.cond(a.decision == "ok", rx.el.span("ok", class_name="text-emerald-400"), rx.el.span(a.decision, class_name="text-rose-400")), class_name=f"{TD} text-xs"),
        rx.el.td(a.detail, class_name=f"{TD} text-xs text-slate-500"),
    )


def agents_view() -> rx.Component:
    return page(
        "AI & API", "AI agents work here through the AI Hub's forge.* tools: they read, build, simulate and test; they propose file changes and people decide. Scripts use the REST API and the Python SDK.",
        rx.el.div(
            panel("AI proposals", rx.cond(ProjectRoom.proposals.length() > 0, rx.el.div(rx.foreach(ProjectRoom.proposals, proposal_row), class_name="flex flex-col divide-y divide-slate-800"),
                                          rx.el.p("None yet. Press AI at the top to ask the AI Hub's assistant.", class_name="text-sm text-slate-500"))),
            panel("API token",
                  rx.el.p("A token for the REST API and the SDK: calls made with it are yours (30 days).", class_name="text-sm text-slate-400"),
                  rx.el.button(rx.icon("key-round", class_name="h-3.5 w-3.5"), "Make a token", on_click=WorkspaceState.make_api_token, id="make-token", class_name=f"{BTN_OUTLINE} mt-2"),
                  rx.cond(WorkspaceState.api_token != "", rx.el.pre(WorkspaceState.api_token, id="api-token", class_name="text-[11px] font-mono text-amber-200 bg-black/40 rounded p-2 mt-2 break-all whitespace-pre-wrap"))),
            class_name="grid grid-cols-1 xl:grid-cols-2 gap-3",
        ),
        panel("Audit log", rx.el.div(rx.el.table(rx.el.tbody(rx.foreach(WorkspaceState.audit_rows, audit_row)), id="audit", class_name="w-full"), class_name="max-h-[50vh] overflow-y-auto"),
              right=rx.el.button(rx.icon("refresh-cw", class_name="h-3.5 w-3.5"), on_click=WorkspaceState.refresh_audit, id="refresh-audit", class_name=BTN_GHOST)),
        proposal_dialog(),
    )


# ------------------------------------------------------------------ People

ROLE_HELP = [
    ("viewer", "Viewer", "sees everything, downloads"),
    ("reviewer", "Reviewer", "sees everything, approves releases"),
    ("editor", "Editor", "edits, builds, simulates, tests, decides AI proposals, asks for releases"),
    ("owner", "Owner", "all of it, and this page"),
]


def member_row(m: MemberRow) -> rx.Component:
    return rx.el.tr(
        rx.el.td(m.user, rx.cond(m.me, rx.el.span(" (you)", class_name="text-slate-500")), class_name=f"{TD} text-sm text-slate-200", custom_attrs={"data-member": m.user}),
        rx.el.td(
            rx.el.select(*[rx.el.option(label, value=v) for v, label, _ in ROLE_HELP], value=m.role,
                         on_change=lambda v: WorkspaceState.change_member(m.user, v), disabled=~WorkspaceState.can_manage,
                         class_name=f"{INPUT} w-32 py-1"),
            class_name=TD),
        rx.el.td(m.added_by, class_name=f"{TD} text-xs text-slate-500"),
        rx.el.td(rx.el.button(rx.icon("user-minus", class_name="h-3.5 w-3.5"), on_click=WorkspaceState.change_member(m.user, ""),
                              disabled=~WorkspaceState.can_manage, title="Take away", class_name=BTN_GHOST), class_name=TD),
    )


def people_view() -> rx.Component:
    return page(
        "People", "Who may see, change and approve this product. The link is how most people come in; members get a role of their own (by the e-mail they sign in with). Someone other than the person who made a release approves it: an owner or a reviewer.",
        rx.el.div(
            panel("The link",
                  rx.el.p("Anyone who opens this product's link and isn't a member:", class_name="text-sm text-slate-400"),
                  rx.el.select(rx.el.option("can't see it (members only)", value="none"), rx.el.option("sees it (viewer)", value="viewer"),
                               rx.el.option("edits it (editor)", value="editor"), value=WorkspaceState.link_access,
                               on_change=WorkspaceState.set_link, disabled=~WorkspaceState.can_manage, id="link-access", class_name=f"{INPUT} mt-2 w-64"),
                  rx.el.ul(*[rx.el.li(rx.el.span(label, class_name="text-slate-200 font-medium"), " ", help_, class_name="text-xs text-slate-400")
                             for _, label, help_ in ROLE_HELP], class_name="mt-3 flex flex-col gap-1")),
            panel("Add a member",
                  rx.el.div(
                      rx.el.input(placeholder="name@example.com", value=WorkspaceState.member_email, on_change=WorkspaceState.set_member_email,
                                  id="member-email", class_name=f"{INPUT} flex-1 min-w-0"),
                      rx.el.select(*[rx.el.option(label, value=v) for v, label, _ in ROLE_HELP], value=WorkspaceState.member_role,
                                   on_change=WorkspaceState.set_member_role, id="member-role", class_name=f"{INPUT} w-32"),
                      rx.el.button(rx.icon("user-plus", class_name="h-3.5 w-3.5"), "Add", on_click=WorkspaceState.add_member,
                                   disabled=~WorkspaceState.can_manage | (WorkspaceState.member_email == ""), id="add-member", class_name=BTN_PRIMARY),
                      class_name="flex items-center gap-2"),
                  rx.cond(~WorkspaceState.can_manage, rx.el.p("Only an owner changes who may do what.", class_name="text-xs text-slate-500 mt-2"))),
            class_name="grid grid-cols-1 xl:grid-cols-2 gap-3",
        ),
        panel("Members", rx.el.table(
            rx.el.thead(rx.el.tr(rx.el.th("Person", class_name=TH), rx.el.th("Role", class_name=TH), rx.el.th("Added by", class_name=TH), rx.el.th("", class_name=TH))),
            rx.el.tbody(rx.foreach(WorkspaceState.members, member_row)), id="members", class_name="w-full")),
    )
