#!/usr/bin/env python3
"""Chipova Forge's session agent: runs one simulated board inside a session container.

Standard library only (the container's Debian Python 3). The agent

- starts Renode headless, with its monitor on a local port, and builds the board with
  ``/work/session/session.resc`` (made by the app: the platform, the chip as a co-simulated
  library, the sensors, the firmware);
- follows what the board does into one numbered event log, ``/work/out/events.jsonl``: UART
  lines and LED changes (written by ``hooks.py`` inside Renode, with virtual time), the chip's
  bus transactions and pin changes (written by the chip library), the session's own states;
- answers requests on a Unix socket, ``/work/run/agent.sock``: one JSON object per line in, one
  per line out (``{"id": .., "ok": true, ...}``), and, after ``follow``, events as they happen
  (``{"event": {...}}``).

A board with an analog side (``session.json``'s ``analog``) runs in lock-step: Renode runs one
step of virtual time (``emulation RunFor``), then the agent reads the chip pins that switch the
circuit, ngspice (``analog.py``) solves the circuit on to the same time, and the circuit's
outputs go to the board's sensors; at each step both sides start from the same moment with the
other's values of that moment.

Nothing from outside reaches Renode's monitor: requests are named operations whose arguments
are checked against ``session.json`` (which UART, which sensor property, which register
window), and the agent writes the monitor commands itself.

Files: /work/session (read-only inputs), /work/run (sockets, Renode's config), /work/out (what
the app copies out: events, logs, waveforms).
"""

from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

SESSION = Path(os.environ.get("FORGE_SESSION_DIR", "/work/session"))
RUN = Path(os.environ.get("FORGE_RUN_DIR", "/work/run"))
OUT = Path(os.environ.get("FORGE_OUT_DIR", "/work/out"))
SOCKET_PATH = RUN / "agent.sock"
MONITOR_PORT = 4321

STATES = ("CREATED", "STARTING", "READY", "RUNNING", "PAUSED", "FAILED", "STOPPING", "STOPPED")


def now() -> float:
    return round(time.time(), 3)


class AgentError(Exception):
    """A request the agent refuses or can't carry out; its message goes back to the app."""


# ------------------------------------------------------------------ Renode's monitor


class Monitor:
    """A client of Renode's monitor on a local TCP port (telnet framing, --plain output)."""

    _ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
    _PROMPT = re.compile(r"^\((?:monitor|[A-Za-z0-9_\- .]{1,64})\) ?$")

    def __init__(self, port: int, timeout: float = 90.0) -> None:
        end = time.time() + timeout
        last: Exception | None = None
        while time.time() < end:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError as e:
                last = e
                time.sleep(0.25)
        else:
            raise AgentError(f"Renode's monitor didn't open: {last}")
        self.sock.settimeout(0.5)
        self.lock = threading.Lock()
        self.pending = b""
        self._read_until_prompt(timeout)

    def _clean(self, data: bytes) -> bytes:
        out = bytearray()
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0xFF and i + 1 < len(data):  # telnet: IAC ...
                cmd = data[i + 1]
                if cmd == 0xFA:  # sub-negotiation up to IAC SE
                    end = data.find(b"\xff\xf0", i + 2)
                    i = len(data) if end < 0 else end + 2
                    continue
                if cmd in (0xFB, 0xFC, 0xFD, 0xFE):
                    i += 3
                    continue
                i += 2
                continue
            out.append(b)
            i += 1
        return self._ANSI.sub(b"", bytes(out))

    def _read_until_prompt(self, timeout: float) -> list[str]:
        end = time.time() + timeout
        buf = self.pending
        while True:
            text = self._clean(buf).decode("utf-8", errors="replace").replace("\r", "")
            lines = text.split("\n")
            if lines and self._PROMPT.match(lines[-1].strip()):
                self.pending = b""
                return lines[:-1]
            if time.time() > end:
                raise AgentError("Renode's monitor didn't answer in time")
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise AgentError("Renode's monitor closed")
            buf += chunk

    def command(self, cmd: str, timeout: float = 30.0) -> str:
        """Runs one monitor command; its output (the echo and the prompt left out)."""
        if "\n" in cmd or "\r" in cmd:
            raise AgentError("one command per line")
        with self.lock:
            self.sock.sendall(cmd.encode() + b"\n")
            lines = self._read_until_prompt(timeout)
        out = [ln for ln in lines if ln.strip() and ln.strip() != cmd.strip()]
        return "\n".join(out).strip()


# ------------------------------------------------------------------ the agent


class Agent:
    def __init__(self) -> None:
        self.config = json.loads((SESSION / "session.json").read_text())
        self.state = "CREATED"
        self.error = ""
        self.renode: subprocess.Popen | None = None
        self.monitor: Monitor | None = None
        self.seq = 0
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.leds: dict[str, bool] = {}
        self.sensors: dict[str, dict] = {}
        self.pins: dict[str, int] = {}
        self.vt = 0.0
        self.started_at = time.time()
        self.events_file = open(OUT / "events.jsonl", "a", encoding="utf-8")
        self.stop_requested = threading.Event()
        # SPI chips: the bytes of the frame in progress, and how many frames Forge itself makes
        # (register probes) are still to come.
        self.frame: list[tuple[int, int]] | None = None
        self.probes = 0
        self.spi_bytes = 0
        # The analog side (lock-step with Renode), when the board has one.
        self.analog = self.config.get("analog") or None
        self.spice = None
        self.stepping = threading.Event()
        self.step_lock = threading.Lock()
        self.analog_values: dict[str, float] = {}
        self.analog_drives: dict[str, int] = {}
        self.analog_inputs: dict[str, float] = {k: float(v["value"]) for k, v in (self.analog or {}).get("inputs", {}).items()}
        self.analog_steps = 0
        self._analog_event_vt = -1.0

    # -------------------------------------------------------------- events

    def emit(self, kind: str, **fields) -> dict:
        with self.cond:
            self.seq += 1
            event = {"seq": self.seq, "wall": now(), "kind": kind, **fields}
            if "vt" in event:
                self.vt = max(self.vt, float(event["vt"]))
            self.events.append(event)
            if len(self.events) > 20000:
                del self.events[:5000]
            self.events_file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.events_file.flush()
            self.cond.notify_all()
        return event

    def set_state(self, state: str, why: str = "") -> None:
        if state != self.state:
            self.state = state
            self.emit("state", state=state, detail=why)

    def events_after(self, seq: int, limit: int = 500) -> list[dict]:
        with self.cond:
            if self.events and self.events[0]["seq"] > seq + 1:
                # Older ones fell out of memory: read them back from the log.
                found = []
                with open(OUT / "events.jsonl", encoding="utf-8") as fh:
                    for line in fh:
                        e = json.loads(line)
                        if e["seq"] > seq:
                            found.append(e)
                            if len(found) >= limit:
                                break
                return found
            return [e for e in self.events if e["seq"] > seq][:limit]

    # -------------------------------------------------------------- following the board

    def _follow_file(self, path: Path, handle) -> None:
        """Calls ``handle`` with each JSON line appended to ``path`` (which may not exist yet)."""
        pos = 0
        partial = ""
        while not self.stop_requested.is_set():
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    data = fh.read()
                    pos = fh.tell()
            except FileNotFoundError:
                data = ""
            if data:
                partial += data
                *lines, partial = partial.split("\n")
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        handle(json.loads(line))
                    except (ValueError, KeyError, TypeError):
                        pass
            else:
                time.sleep(0.05)

    def _from_renode(self, e: dict) -> None:
        src = e.get("src")
        if src == "uart":
            self.emit("uart", uart=e.get("name", ""), vt=e.get("vt", 0.0), line=str(e.get("line", ""))[:400])
        elif src == "led":
            name, on = str(e.get("name", "")), bool(e.get("on"))
            if self.leds.get(name) != on:
                self.leds[name] = on
                self.emit("led", led=name, on=on, vt=e.get("vt", 0.0))

    def _register_name(self, offset: int) -> str:
        return self.config.get("chip", {}).get("registers", {}).get(str(offset), "")

    def _from_chip(self, e: dict) -> None:
        kind = e.get("kind")
        chip = self.config.get("chip") or {}
        if kind == "bus":
            off = int(e.get("addr", 0))
            self.emit("bus", op=e.get("op", ""), offset=off, register=self._register_name(off),
                      value=int(e.get("value", 0)), vt=e.get("t", 0.0), via="bus")
        elif kind == "spi":
            self.spi_bytes += 1
            if self.frame is not None:
                self.frame.append((int(e.get("tx", 0)), int(e.get("rx", 0))))
        elif kind == "pin" and e.get("input") and e.get("pin") == "chip_select":
            if int(e.get("value", 1)) == 0:
                self.frame = []
            else:
                frame, self.frame = self.frame, None
                if frame:
                    self._decode_frame(frame, float(e.get("t", 0.0)), chip.get("framing", ""))
        elif kind == "pin":
            name, value = str(e.get("pin", "")), int(e.get("value", 0))
            self.pins[name] = value
            self.emit("pin", pin=name, value=value, vt=e.get("t", 0.0))
        elif kind == "trace":
            self.emit("trace", detail=str(e.get("detail", "")), vt=e.get("t", 0.0))

    def _decode_frame(self, frame: list[tuple[int, int]], t: float, framing: str) -> None:
        """One SPI frame as a register access (Forge's SPI-to-APB bridge: command + 4 bytes)."""
        via = "spi"
        if self.probes > 0:
            self.probes -= 1
            via = "probe"
        if framing != "forge-spi-apb" or len(frame) < 5:
            self.emit("spi", bytes_out=[b[0] for b in frame], bytes_in=[b[1] for b in frame], vt=t, via=via)
            return
        cmd = frame[0][0]
        write = bool(cmd & 0x80)
        data = [b[0] if write else b[1] for b in frame[1:5]]
        value = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        off = (cmd & 0x7F) * 4
        self.emit("bus", op="w" if write else "r", offset=off, register=self._register_name(off),
                  value=value, vt=t, via=via)

    def _watch_renode(self) -> None:
        assert self.renode is not None
        code = self.renode.wait()
        if not self.stop_requested.is_set():
            self.error = f"Renode stopped by itself (exit code {code}); see renode.log"
            self.set_state("FAILED", self.error)

    # -------------------------------------------------------------- start

    def start(self) -> None:
        self.set_state("STARTING")
        cfg = RUN / "renode.config"
        log = open(OUT / "renode.log", "ab")
        env = dict(os.environ)
        env.setdefault("HOME", str(RUN))
        env["FORGE_EVENTS"] = str(OUT / "chip-events.jsonl")
        env["FORGE_VCD"] = str(OUT / "chip.vcd")
        cmd = ["renode", "--disable-gui", "--plain", "-P", str(MONITOR_PORT), "--config", str(cfg),
               "--pid-file", str(RUN / "renode.pid")]
        self.renode = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       cwd=str(RUN), env=env)
        threading.Thread(target=self._watch_renode, daemon=True).start()
        threading.Thread(target=self._follow_file, args=(OUT / "renode-events.jsonl", self._from_renode), daemon=True).start()
        threading.Thread(target=self._follow_file, args=(OUT / "chip-events.jsonl", self._from_chip), daemon=True).start()
        try:
            self.monitor = Monitor(MONITOR_PORT)
            out = self.monitor.command(f"include @{SESSION / 'session.resc'}", timeout=180)
            if "error" in out.lower() or "exception" in out.lower():
                raise AgentError("the board didn't build: " + out[-1500:])
            self.set_state("READY")
            driven = {(s["sensor"], s["property"]) for s in (self.analog or {}).get("sensors", [])}
            for name, value in self.config.get("initial", {}).get("sensors", {}).items():
                for prop, v in value.items():
                    if (name, prop) not in driven:
                        self.set_sensor(name, prop, v)
            if self.analog:
                self._start_analog()
            if self.config.get("autostart", True):
                self.run()
        except AgentError as e:
            self.error = str(e)
            self.set_state("FAILED", self.error)
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.set_state("FAILED", self.error)

    # -------------------------------------------------------------- operations

    def _need_monitor(self) -> Monitor:
        if self.monitor is None or self.state in ("FAILED", "STOPPING", "STOPPED", "CREATED", "STARTING"):
            raise AgentError(f"the board isn't ready ({self.state.lower()})")
        return self.monitor

    def run(self) -> None:
        m = self._need_monitor()
        if self.analog:
            self.stepping.set()
        else:
            m.command("start")
        self.set_state("RUNNING")

    def pause(self) -> None:
        m = self._need_monitor()
        if self.analog:
            self.stepping.clear()
            with self.step_lock:
                pass
        else:
            m.command("pause")
        self.read_time()
        self.set_state("PAUSED")

    def run_for(self, seconds: float) -> float:
        if not 0 < seconds <= 60:
            raise AgentError("run for between 0 and 60 seconds of board time")
        m = self._need_monitor()
        if self.analog:
            self.stepping.clear()
            self.set_state("RUNNING", f"for {seconds:g} s")
            end = self.read_time() + seconds
            while self.vt < end - 1e-9 and self.state == "RUNNING":
                self._step(min(self.analog["step"], end - self.vt))
            self.set_state("PAUSED")
            return self.vt
        if self.state == "RUNNING":
            m.command("pause")
        self.set_state("RUNNING", f"for {seconds:g} s")
        m.command(f'emulation RunFor "{seconds:.6f}"', timeout=600)
        vt = self.read_time()
        self.set_state("PAUSED")
        return vt

    # -------------------------------------------------------------- the analog side

    def _start_analog(self) -> None:
        import analog  # noqa: PLC0415 - beside this file in /work/agent

        a = self.analog
        sources = {v["source"]: float(self.analog_inputs[k]) for k, v in a["inputs"].items()}
        sources.update({d["source"]: float(d["off"]) for d in a["drives"].values()})
        self.spice = analog.Ngspice(a["netlist"], sources)
        self.spice.advance(1e-6)  # the first time point: the circuit at the start, to read
        self._exchange(0.0)
        self.emit("note", detail=f"analog side: {a['title']} (ngspice), in step with the board every {a['step'] * 1000:g} ms", vt=self.vt)
        threading.Thread(target=self._analog_loop, daemon=True).start()

    def _exchange(self, vt: float) -> None:
        """Both sides at the same moment: the chip pins go to the circuit, the circuit's outputs
        to the sensors."""
        a = self.analog
        m = self._need_monitor()
        changed = False
        for name, d in a["drives"].items():
            on = "true" in m.command(f"{d['path']} State").lower()
            self.spice.set(d["source"], d["on"] if on else d["off"])
            if self.analog_drives.get(name) != int(on):
                self.analog_drives[name] = int(on)
                changed = True
        for name, o in a["outputs"].items():
            self.analog_values[name] = self.spice.value(o["node"])
        for s in a["sensors"]:
            v = int(round(self.analog_values[s["output"]] * s["scale"]))
            if self.sensors.get(s["sensor"], {}).get(s["property"]) != v:
                path = self.config["sensors"][s["sensor"]]["path"]
                m.command(f"{path} {s['property']} {v}")
                self.sensors.setdefault(s["sensor"], {})[s["property"]] = v
        if changed or vt - self._analog_event_vt >= 0.2 or vt == 0.0:
            self._analog_event_vt = vt
            self.emit("analog", vt=vt, values={k: round(v, 4) for k, v in self.analog_values.items()}, drives=dict(self.analog_drives),
                      inputs=dict(self.analog_inputs), sensors={s["sensor"]: dict(self.sensors.get(s["sensor"], {})) for s in a["sensors"]})

    def _step(self, seconds: float) -> None:
        """One step of both sides from the same moment: Renode runs, ngspice solves on to where
        Renode stopped, then they exchange values."""
        m = self._need_monitor()
        with self.step_lock:
            m.command(f'emulation RunFor "{seconds:.6f}"', timeout=120)
            vt = self.read_time()
            self.spice.advance(vt)
            self.analog_steps += 1
            self._exchange(vt)

    def _analog_loop(self) -> None:
        while not self.stop_requested.is_set():
            if not self.stepping.wait(0.2):
                continue
            try:
                self._step(self.analog["step"])
            except Exception as e:  # noqa: BLE001
                if self.stop_requested.is_set():
                    return
                self.stepping.clear()
                self.error = f"the analog side stopped: {type(e).__name__}: {e}"
                self.set_state("FAILED", self.error)
                return

    def set_analog(self, name: str, value) -> None:
        a = self.analog
        if not a:
            raise AgentError("this board has no analog side")
        spec = a["inputs"].get(name)
        if spec is None:
            raise AgentError(f"the analog side has no input {name!r} (it has {', '.join(a['inputs'])})")
        try:
            v = float(value)
        except (TypeError, ValueError) as e:
            raise AgentError(f"{name} takes a number") from e
        if not spec["min"] <= v <= spec["max"]:
            raise AgentError(f"{name} is {spec['min']}..{spec['max']} {spec.get('unit', '')}")
        self.analog_inputs[name] = v
        self.spice.set(spec["source"], v)
        self.emit("analog_input", input=name, value=v, vt=self.vt)

    def read_time(self) -> float:
        out = self._need_monitor().command("forge_time")
        try:
            self.vt = float(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            pass
        return self.vt

    def reset(self) -> None:
        m = self._need_monitor()
        m.command("machine Reset")
        self.emit("note", detail="board reset (the MCU restarts its firmware)")

    def _uart(self, name: str) -> str:
        uarts = self.config.get("uarts", {})
        if name not in uarts:
            raise AgentError(f"there is no UART {name!r} (there is {', '.join(uarts) or 'none'})")
        return uarts[name]

    def uart_send(self, name: str, text: str) -> None:
        path = self._uart(name)
        text = str(text)
        if len(text) > 200 or not all(32 <= ord(c) < 127 and c not in '"\\' for c in text):
            raise AgentError("send printable text without quotes or backslashes, at most 200 characters")
        self._need_monitor().command(f'forge_uart_send "{path}" "{text}"')
        self.emit("uart_in", uart=name, line=text, vt=self.vt)

    def _window(self, offset: int) -> None:
        chip = self.config.get("chip") or {}
        size = int(chip.get("size", 0))
        if not chip or not 0 <= offset < size or offset % 4:
            raise AgentError(f"registers are 32-bit words at offsets 0..{size - 4:#x} of the chip")

    def _probe(self, command: str) -> str:
        """A register access by Forge itself on an SPI chip: waits for the MCU to finish the frame
        it may be in the middle of."""
        m = self._need_monitor()
        for _ in range(50):
            self.probes += 1
            out = m.command(command)
            if "middle of an SPI frame" not in out:
                return out
            self.probes -= 1
            if self.state == "RUNNING":
                time.sleep(0.01)
            else:
                m.command('emulation RunFor "0.0002"')
        raise AgentError("the MCU kept the chip busy: try again")

    def read_reg(self, offset: int) -> int:
        chip = self.config.get("chip") or {}
        offset = int(offset)
        self._window(offset)
        if chip.get("access") == "spi":
            out = self._probe(f"{chip['path']} DebugReadRegister {offset // 4}")
        else:
            out = self._need_monitor().command(f"sysbus ReadDoubleWord {int(chip['base']) + offset:#x}")
        m = re.search(r"0x[0-9A-Fa-f]+|\b\d+\b", out)
        if not m or "rror" in out:
            raise AgentError("the read failed: " + out[-300:])
        return int(m.group(0), 0)

    def write_reg(self, offset: int, value: int) -> None:
        chip = self.config.get("chip") or {}
        offset = int(offset)
        self._window(offset)
        value = int(value)
        if not 0 <= value <= 0xFFFFFFFF:
            raise AgentError("a register takes 0..0xFFFFFFFF")
        if chip.get("access") == "spi":
            out = self._probe(f"{chip['path']} DebugWriteRegister {offset // 4} {value:#x}")
        else:
            out = self._need_monitor().command(f"sysbus WriteDoubleWord {int(chip['base']) + offset:#x} {value:#x}")
        if out and "rror" in out:
            raise AgentError("the write failed: " + out[-300:])

    def set_sensor(self, name: str, prop: str, value) -> None:
        sensors = self.config.get("sensors", {})
        if name not in sensors:
            raise AgentError(f"there is no sensor {name!r}")
        if any(s["sensor"] == name and s["property"] == prop for s in (self.analog or {}).get("sensors", [])):
            raise AgentError(f"the analog side ({self.analog['title']}) sets {name}: change its inputs "
                             f"({', '.join(self.analog['inputs'])}) instead")
        props = sensors[name].get("properties", {})
        if prop not in props:
            raise AgentError(f"sensor {name!r} has no setting {prop!r} (it has {', '.join(props)})")
        spec = props[prop]
        try:
            v = int(value)
        except (TypeError, ValueError) as e:
            raise AgentError(f"{prop} takes a whole number") from e
        lo, hi = spec.get("min", -2**31), spec.get("max", 2**31 - 1)
        if not lo <= v <= hi:
            raise AgentError(f"{prop} takes {lo}..{hi}")
        out = self._need_monitor().command(f"{sensors[name]['path']} {prop} {v}")
        if out and "error" in out.lower():
            raise AgentError(out[-300:])
        self.sensors.setdefault(name, {})[prop] = v
        self.emit("sensor", sensor=name, property=prop, value=v, vt=self.vt)

    def set_pin(self, name: str, value: int) -> None:
        chip = self.config.get("chip") or {}
        inputs = chip.get("inputs", {})
        if name not in inputs:
            raise AgentError(f"the chip has no input pin {name!r} driven by the board (it has {', '.join(inputs) or 'none'})")
        on = "true" if int(value) else "false"
        self._need_monitor().command(f"{chip['path']} OnGPIO {int(inputs[name])} {on}")
        self.emit("pin_in", pin=name, value=1 if int(value) else 0, vt=self.vt)

    def record(self) -> None:
        chip = self.config.get("chip") or {}
        if not chip:
            raise AgentError("this board has no chip to record")
        self._need_monitor().command(f"{chip['path']} OnGPIO {int(chip.get('record_gpio', 100))} true")
        self.emit("note", detail="recording the chip's signals", vt=self.vt)

    def status(self) -> dict:
        return {
            "state": self.state,
            "error": self.error,
            "vt": self.vt,
            "seq": self.seq,
            "leds": dict(self.leds),
            "sensors": {k: dict(v) for k, v in self.sensors.items()},
            "pins": dict(self.pins),
            "spi_bytes": self.spi_bytes,
            "uptime": round(time.time() - self.started_at, 1),
            "analog": ({"model": self.analog["model"], "values": dict(self.analog_values), "drives": dict(self.analog_drives),
                        "inputs": dict(self.analog_inputs), "steps": self.analog_steps} if self.analog else None),
        }

    def stop(self) -> None:
        if self.state in ("STOPPING", "STOPPED"):
            return
        self.set_state("STOPPING")
        self.stop_requested.set()
        self.stepping.clear()
        try:
            if self.monitor is not None:
                try:
                    self.monitor.command("pause", timeout=10)
                except AgentError:
                    pass
                try:
                    self.monitor.sock.sendall(b"quit\n")
                except OSError:
                    pass
            if self.renode is not None:
                try:
                    self.renode.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.renode.kill()
        finally:
            self.set_state("STOPPED")

    # -------------------------------------------------------------- requests

    def handle(self, req: dict) -> dict:
        op = req.get("op")
        if op == "hello":
            return {"state": self.state, "seq": self.seq, "error": self.error}
        if op == "status":
            if self.state in ("RUNNING", "PAUSED", "READY"):
                try:
                    self.read_time()
                except AgentError:
                    pass
            return self.status()
        if op == "start":
            self.run()
            return self.status()
        if op == "pause":
            self.pause()
            return self.status()
        if op == "run_for":
            return {"vt": self.run_for(float(req.get("seconds", 0)))}
        if op == "reset":
            self.reset()
            return self.status()
        if op == "uart_send":
            self.uart_send(str(req.get("uart", "")), str(req.get("text", "")))
            return {}
        if op == "read_reg":
            return {"value": self.read_reg(int(req.get("offset", -1)))}
        if op == "write_reg":
            self.write_reg(int(req.get("offset", -1)), int(req.get("value", 0)))
            return {}
        if op == "set_sensor":
            self.set_sensor(str(req.get("sensor", "")), str(req.get("property", "")), req.get("value"))
            return {}
        if op == "set_analog":
            self.set_analog(str(req.get("input", "")), req.get("value"))
            return {}
        if op == "set_pin":
            self.set_pin(str(req.get("pin", "")), int(req.get("value", 0)))
            return {}
        if op == "record":
            self.record()
            return {}
        if op == "events":
            return {"events": self.events_after(int(req.get("after", 0)), int(req.get("limit", 500)))}
        if op == "stop":
            threading.Thread(target=self.stop, daemon=True).start()
            return {"state": "STOPPING"}
        raise AgentError(f"unknown request {op!r}")


AGENT: Agent | None = None


class Handler(socketserver.StreamRequestHandler):
    def _send(self, obj: dict) -> None:
        with self.wlock:
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

    def _follow(self, after: int) -> None:
        assert AGENT is not None
        seq = after
        try:
            while not AGENT.stop_requested.is_set() or seq < AGENT.seq:
                with AGENT.cond:
                    if AGENT.seq <= seq:
                        AGENT.cond.wait(timeout=1.0)
                for e in AGENT.events_after(seq):
                    self._send({"event": e})
                    seq = e["seq"]
        except (OSError, ValueError):
            return

    def handle(self) -> None:
        assert AGENT is not None
        self.wlock = threading.Lock()
        follower: threading.Thread | None = None
        for raw in self.rfile:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                rid = req.get("id")
            except ValueError:
                self._send({"ok": False, "error": "not JSON"})
                continue
            if req.get("op") == "follow":
                if follower is None:
                    follower = threading.Thread(target=self._follow, args=(int(req.get("after", 0)),), daemon=True)
                    follower.start()
                self._send({"id": rid, "ok": True})
                continue
            try:
                result = AGENT.handle(req)
                self._send({"id": rid, "ok": True, **result})
            except AgentError as e:
                self._send({"id": rid, "ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main() -> int:
    global AGENT
    for d in (RUN, OUT):
        d.mkdir(parents=True, exist_ok=True)
    AGENT = Agent()
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = Server(str(SOCKET_PATH), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    AGENT.start()
    try:
        while AGENT.state not in ("STOPPED",):
            time.sleep(0.5)
            if AGENT.state == "FAILED" and AGENT.stop_requested.is_set():
                break
    except KeyboardInterrupt:
        AGENT.stop()
    # Let followers read the last events, then end (the container ends with it).
    time.sleep(1.0)
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
