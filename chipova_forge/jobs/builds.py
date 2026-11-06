"""Builds: what a simulation (and a release) is made of, each from exactly known inputs.

    registers   the chip's SystemRDL -> CSR RTL, C header, Python register map (in-process,
                PeakRDL), and whether the chip's CSR RTL still matches
    chip        the chip's RTL (+ the register harness for a chip with no bus) -> libVchip.so,
                the Renode co-simulation library (Verilator, in the simulation image)
    firmware    the project's firmware/*.c + the platform's start-up code + Forge's chip driver +
                the generated board.h and register header -> firmware.elf (Arm GCC)

Every build gathers its inputs first (files, and the text Forge generates for it), hashes them
together with the image it runs in, and reuses an earlier successful build with the same hash
instead of building again. What runs in the container is a fixed script per kind with the
inputs' names in it, never a command from outside. Each build folder keeps:

    inputs.tar      the exact inputs
    manifest.json   inputs and outputs with their SHA-256, the image and its id, the tools'
                    versions, the command, the result
    build.log       the tools' output
    out/            the outputs
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import shlex
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from chipova_forge import ids, paths
from chipova_forge.contract import contract as hdc
from chipova_forge.contract import registers as regs_mod
from chipova_forge.cosim import harness, wrapper
from chipova_forge.db import db, loads
from chipova_forge.domain import roles
from chipova_forge.jobs import docker_runner
from chipova_forge.pcb import dsn as pcb_dsn
from chipova_forge.pcb import netlist as pcb_netlist
from chipova_forge.pcb import place as pcb_place
from chipova_forge.services import audit, projects
from chipova_forge.services.actors import Actor
from chipova_forge.system import board as board_mod
from chipova_forge.system import catalog

logger = logging.getLogger(__name__)

SIM_IMAGE = "chipova-forge/sim:1"
BOARD_IMAGE = "chipova-forge/board:1"
KINDS = ("registers", "chip", "firmware", "pcb")
# How each image is made (from the repository's root), for the message when one is missing.
IMAGE_BUILD = {SIM_IMAGE: "docker build -t chipova-forge/sim:1 infra/dockerfiles/forge-sim",
               BOARD_IMAGE: "docker build -t chipova-forge/board:1 infra/dockerfiles/forge-board"}
# Bump when what a kind generates changes, so old builds aren't reused.
GENERATOR_VERSION = {"registers": 1, "chip": 3, "firmware": 2, "pcb": 1}
MAX_PARALLEL = 2

QUEUED, RUNNING, SUCCEEDED, FAILED = "QUEUED", "RUNNING", "SUCCEEDED", "FAILED"

listeners: list[Callable[[str, str], None]] = []  # (project id, build id)


class BuildError(ValueError):
    """The project can't be built as it is (the message says what to fix)."""


@dataclass
class Plan:
    kind: str
    files: dict[str, bytes]  # path in /work/src -> bytes
    script: str
    image: str = SIM_IMAGE
    meta: dict[str, Any] = field(default_factory=dict)
    in_process: bool = False


# ------------------------------------------------------------------ records


def get(build_id: str) -> dict[str, Any] | None:
    if not ids.is_prefixed(build_id, "bld"):
        return None
    row = db().one("SELECT * FROM builds WHERE id = ?", (build_id,))
    return _row(row) if row else None


def _row(row) -> dict[str, Any]:  # noqa: ANN001
    return {**dict(row), "summary": loads(row["summary"], {})}


def of_project(project_id: str, kind: str = "", limit: int = 30) -> list[dict[str, Any]]:
    if kind:
        rows = db().query("SELECT * FROM builds WHERE project_id = ? AND kind = ? ORDER BY created_at DESC LIMIT ?", (project_id, kind, limit))
    else:
        rows = db().query("SELECT * FROM builds WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))
    return [_row(r) for r in rows]


def latest(project_id: str, kind: str, status: str = "") -> dict[str, Any] | None:
    sql = "SELECT * FROM builds WHERE project_id = ? AND kind = ?"
    args: list[Any] = [project_id, kind]
    if status:
        sql += " AND status = ?"
        args.append(status)
    row = db().one(sql + " ORDER BY created_at DESC LIMIT 1", args)
    return _row(row) if row else None


def out_dir(build: dict[str, Any]) -> Path:
    return paths.build_dir(build["project_id"], build["id"]) / "out"


def artifacts(build_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in db().query("SELECT * FROM artifacts WHERE owner = ? ORDER BY name", (build_id,))]


def artifact_path(build: dict[str, Any], name: str) -> Path:
    name = ids.clean_path(name)
    return out_dir(build) / name


def manifest(build: dict[str, Any]) -> dict[str, Any]:
    f = paths.build_dir(build["project_id"], build["id"]) / "manifest.json"
    return json.loads(f.read_text()) if f.is_file() else {}


def log_text(build: dict[str, Any], limit: int = 200_000) -> str:
    f = paths.build_dir(build["project_id"], build["id"]) / "build.log"
    if not f.is_file():
        return ""
    data = f.read_bytes()
    return data[-limit:].decode("utf-8", errors="replace")


# ------------------------------------------------------------------ what a build is made of


def _registers_plan(project_id: str) -> Plan:
    c = projects.contract(project_id)
    chip = projects.chip(project_id)
    if c.interface.protocol == "none":
        rdl, name = harness.render_rdl(c), "forge_harness.rdl"
    elif c.design.registers:
        rdl, name = chip.text(c.design.registers), c.design.registers
    else:
        raise BuildError("the chip has no SystemRDL description (registers/*.rdl)")
    files = {name: rdl.encode()}
    if c.design.csr_rtl and c.interface.protocol != "none":
        files[f"chip/{c.design.csr_rtl}"] = chip.files[c.design.csr_rtl]
    return Plan("registers", files, "", in_process=True, meta={"rdl": name, "csr_rtl": c.design.csr_rtl or "", "harness": c.interface.protocol == "none"})


def _register_map(project_id: str) -> regs_mod.RegisterMap | None:
    c = projects.contract(project_id)
    try:
        if c.interface.protocol == "none":
            return regs_mod.read_map(harness.render_rdl(c), "forge_harness.rdl")
        if c.design.registers:
            return regs_mod.read_map(projects.chip(project_id).text(c.design.registers), c.design.registers)
    except regs_mod.RegistersError:
        return None
    return None


def register_map(project_id: str) -> regs_mod.RegisterMap | None:
    """The project's register map (cached by the chip's snapshot and the contract)."""
    try:
        key = (projects.contract(project_id).model_dump_json(), project_id)
    except Exception:  # noqa: BLE001
        return None
    hit = _map_cache.get(project_id)
    if hit and hit[0] == key:
        return hit[1]
    m = _register_map(project_id)
    _map_cache[project_id] = (key, m)
    return m


_map_cache: dict[str, tuple[Any, Any]] = {}


def _chip_plan(project_id: str) -> Plan:
    c = projects.contract(project_id)
    issues = [i for i in hdc.check(c) if i.level == "error"]
    if issues:
        raise BuildError("hardware.yaml: " + "; ".join(i.message for i in issues))
    chip = projects.chip(project_id)
    files: dict[str, bytes] = {}
    for p in c.design.rtl_sources:
        if p not in chip.files:
            raise BuildError(f"the chip has no {p}")
        files[p] = chip.files[p]
    sources = tuple(c.design.rtl_sources)
    if c.interface.protocol == "none":
        rdl = harness.render_rdl(c)
        out = regs_mod.generate(rdl, "forge_harness.rdl")
        for name, text in out.regblock.items():
            files[f"forge/{name}"] = text.encode()
        files["forge/forge_harness.sv"] = harness.render_verilog(c).encode()
        sources = (f"forge/{harness.HARNESS_REGS}_pkg.sv", f"forge/{harness.HARNESS_REGS}.sv", *sources, "forge/forge_harness.sv")
        spec = board_mod.cosim_spec(c, sources, top=harness.HARNESS_TOP, spec_ports=harness.contract_ports())
    else:
        spec = board_mod.cosim_spec(c, sources)
    files["sim_main.cpp"] = wrapper.render_sim_main(spec).encode()
    files["CMakeLists.txt"] = wrapper.render_cmake(spec).encode()
    script = """#!/bin/bash
set -o pipefail
echo "== $(verilator --version)"
echo "== $(cmake --version | head -1)"
mkdir -p /work/tmp/build
cmake -S /work/src -B /work/tmp/build -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -n 20 || exit 1
cmake --build /work/tmp/build -j 4 2>&1
status=$?
if [ $status -eq 0 ]; then cp /work/tmp/build/libVchip.so /work/out/libVchip.so; fi
exit $status
"""
    return Plan("chip", files, script, meta={"top": spec.top, "interface": c.interface.protocol,
                                              "frequency_hz": spec.frequency_hz, "outputs": [p.name for p in spec.outputs]})


def _firmware_plan(project_id: str) -> Plan:
    c = projects.contract(project_id)
    b = board_mod.from_yaml(projects.read(project_id, "system.yaml"))
    errors = [i for i in board_mod.check(b, c) if i.level == "error"]
    if errors:
        raise BuildError("system.yaml: " + "; ".join(f"{i.where}: {i.message}" for i in errors))
    plat = catalog.platform(b.platform)
    pdir = catalog.platform_dir(b.platform)
    fw = plat["firmware"]
    files: dict[str, bytes] = {}
    for name in [*fw.get("sources", []), *fw.get("headers", []), fw["linker_script"]]:
        files[name] = (pdir / name).read_bytes()
    proto = "apb3" if c.interface.protocol in ("apb3", "none") else c.interface.protocol
    driver = fw["chip_drivers"][proto]
    files[driver] = (pdir / driver).read_bytes()
    regmap = register_map(project_id)
    header_name = ""
    if c.interface.protocol == "none":
        out = regs_mod.generate(harness.render_rdl(c), "forge_harness.rdl")
        header_name = out.c_header_name
        files[header_name] = out.c_header.encode()
    elif c.design.registers:
        out = regs_mod.generate(projects.chip(project_id).text(c.design.registers), c.design.registers)
        header_name = out.c_header_name
        files[header_name] = out.c_header.encode()
    h = board_mod.board_h(b, c, regmap)
    if c.interface.protocol == "none":
        h = h.replace("#endif\n", "#define BOARD_CHIP_HARNESS 1  /* the chip sits in Forge's register harness */\n\n#endif\n")
    files["board.h"] = h.encode()
    user = projects.firmware_sources(project_id)
    if not any(p.endswith(".c") for p in user):
        raise BuildError("the firmware has no .c file (firmware/main.c)")
    files.update(user)
    c_files = [*fw.get("sources", []), driver, *[p for p in sorted(user) if p.endswith(".c")]]
    for p in c_files:
        if not re.match(r"^[A-Za-z0-9_/\-]+\.c$", p):
            raise BuildError(f"not a source file name: {p}")
    cflags = " ".join(shlex.quote(f) for f in fw["cflags"])
    srcs = " ".join(shlex.quote(p) for p in c_files)
    script = f"""#!/bin/bash
set -o pipefail
cd /work/src
echo "== $(arm-none-eabi-gcc --version | head -1)"
arm-none-eabi-gcc {cflags} -Os -g -std=gnu11 -Wall -Wextra -ffreestanding -ffunction-sections -fdata-sections \\
  -nostartfiles -nostdlib -Wl,--gc-sections -Wl,-Map=/work/out/firmware.map -T {shlex.quote(fw['linker_script'])} \\
  -I. -Ifirmware {srcs} -o /work/out/firmware.elf -lgcc 2>&1
status=$?
if [ $status -eq 0 ]; then
  arm-none-eabi-objcopy -O binary /work/out/firmware.elf /work/out/firmware.bin
  arm-none-eabi-size -A /work/out/firmware.elf > /work/out/size.txt
  arm-none-eabi-size /work/out/firmware.elf
fi
exit $status
"""
    return Plan("firmware", files, script, meta={"platform": b.platform, "header": header_name, "sources": c_files,
                                                  "driver": driver})


# The board image runs these modules of chipova_forge.pcb as forge_pcb (standard library only).
PCB_MODULES = ("__init__", "model", "ses", "kicad", "stitch", "run", "fill", "report")

PCB_SCRIPT = r"""#!/bin/bash
set -o pipefail
cd /work/src
export PYTHONPATH=/work/src
echo "== $(kicad-cli version | sed 's/^/kicad-cli /')"
echo "== freerouting 2.5.0 ($(java -version 2>&1 | head -1))"
mkdir -p /work/tmp/fab /work/tmp/sch /work/out/kicad
echo "-- routing (Freerouting)"
timeout 600 freerouting -de board.dsn -do /work/tmp/board.ses --router.autorouter.max_passes=60 -mt 2 > /work/tmp/router.log 2>&1
grep -E "Auto-routing stage completed|could not be routed|^  Net '|^    - " /work/tmp/router.log | sed 's/^.*INFO *\[[^]]*\] //' | head -40
if [ ! -s /work/tmp/board.ses ]; then echo "error: the autorouter wrote no session file"; tail -n 20 /work/tmp/router.log; exit 1; fi
cp /work/tmp/board.ses /work/out/board.ses
echo "-- KiCad files"
python3 -m forge_pcb.run design.json /work/tmp/board.ses /work/out/kicad || exit 1
python3 -m forge_pcb.fill /work/out/kicad/board.kicad_pcb || exit 1
cd /work/out/kicad
echo "-- checks (KiCad ERC and DRC with schematic parity)"
kicad-cli sch erc --format json --severity-all -o /work/out/erc.json board.kicad_sch > /work/tmp/erc.log 2>&1 || { cat /work/tmp/erc.log; exit 1; }
kicad-cli pcb drc --format json --severity-all --schematic-parity -o /work/out/drc.json board.kicad_pcb > /work/tmp/drc.log 2>&1 || { cat /work/tmp/drc.log; exit 1; }
echo "-- fabrication outputs"
kicad-cli pcb export gerbers -o /work/tmp/fab/ -l F.Cu,B.Cu,F.Paste,F.Silkscreen,F.Mask,B.Mask,Edge.Cuts board.kicad_pcb > /work/tmp/fab.log 2>&1 || { cat /work/tmp/fab.log; exit 1; }
kicad-cli pcb export drill -o /work/tmp/fab/ board.kicad_pcb >> /work/tmp/fab.log 2>&1 || { cat /work/tmp/fab.log; exit 1; }
kicad-cli pcb export pos --format csv --units mm --side front -o /work/out/positions.csv board.kicad_pcb >> /work/tmp/fab.log 2>&1
kicad-cli sch export bom --fields 'Reference,Value,Footprint,Manufacturer,MPN,${QUANTITY}' --labels 'Refs,Value,Footprint,Manufacturer,MPN,Qty' \
  --group-by 'Value,Footprint,MPN' -o /work/out/bom.csv board.kicad_sch >> /work/tmp/fab.log 2>&1
kicad-cli pcb export svg --mode-single --page-size-mode 2 --exclude-drawing-sheet -l Edge.Cuts,F.Cu,F.Silkscreen,F.Fab -o /work/out/board-top.svg board.kicad_pcb >> /work/tmp/fab.log 2>&1
kicad-cli pcb export svg --mode-single --page-size-mode 2 --exclude-drawing-sheet --mirror -l Edge.Cuts,B.Cu -o /work/out/board-bottom.svg board.kicad_pcb >> /work/tmp/fab.log 2>&1
kicad-cli sch export svg -o /work/tmp/sch board.kicad_sch >> /work/tmp/fab.log 2>&1 && cp /work/tmp/sch/board.svg /work/out/schematic.svg
kicad-cli sch export pdf -o /work/out/schematic.pdf board.kicad_sch >> /work/tmp/fab.log 2>&1
cd /work/tmp/fab && python3 -c "
import pathlib, zipfile
with zipfile.ZipFile('/work/out/gerbers.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for f in sorted(pathlib.Path('.').iterdir()):
        z.write(f, f.name)
with zipfile.ZipFile('/work/out/kicad.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for f in sorted(pathlib.Path('/work/out/kicad').iterdir()):
        z.write(f, 'board/' + f.name)
print('gerbers.zip:', len(list(pathlib.Path('.').iterdir())), 'files')
"
cd /work/src && python3 -m forge_pcb.report /work/out
"""


def _pcb_plan(project_id: str) -> Plan:
    c = projects.contract(project_id)
    b = board_mod.from_yaml(projects.read(project_id, "system.yaml"))
    errors = [i for i in board_mod.check(b, c) if i.level == "error"]
    if errors:
        raise BuildError("system.yaml: " + "; ".join(f"{i.where}: {i.message}" for i in errors))
    d, issues = pcb_netlist.build(c, b, projects.must(project_id)["title"])
    if d is None:
        raise BuildError("; ".join(m for level, m in issues if level == "error") or "the board can't be laid out")
    try:
        pcb_place.place(d)
    except ValueError as e:
        raise BuildError(f"placement: {e}") from e
    src = Path(pcb_dsn.__file__).parent
    files = {"design.json": d.to_json().encode(), "board.dsn": pcb_dsn.write(d).encode()}
    for name in PCB_MODULES:
        files[f"forge_pcb/{name}.py"] = (src / f"{name}.py").read_bytes()
    return Plan("pcb", files, PCB_SCRIPT, image=BOARD_IMAGE,
                meta={"width": d.width, "height": d.height, "parts": len(d.parts), "nets": len(d.nets()),
                      "issues": [{"level": lvl, "message": m} for lvl, m in issues], "notes": d.notes})


PLANNERS = {"registers": _registers_plan, "chip": _chip_plan, "firmware": _firmware_plan, "pcb": _pcb_plan}


def plan(project_id: str, kind: str) -> Plan:
    if kind not in PLANNERS:
        raise BuildError(f"there is no build {kind!r} (there are: {', '.join(KINDS)})")
    try:
        return PLANNERS[kind](project_id)
    except BuildError:
        raise
    except (projects.NotFound, ValueError, yaml.YAMLError) as e:
        raise BuildError(str(e)) from e


def inputs_sha(p: Plan, image_id: str) -> str:
    h = hashlib.sha256()
    h.update(f"{p.kind}|{GENERATOR_VERSION[p.kind]}|{image_id}|{p.script}\n".encode())
    for path in sorted(p.files):
        h.update(f"{path}\0{hashlib.sha256(p.files[path]).hexdigest()}\n".encode())
    return h.hexdigest()


# ------------------------------------------------------------------ running


_queue: asyncio.Queue | None = None
_workers: list[asyncio.Task] = []
_inflight: dict[str, asyncio.Future] = {}
_lock = threading.Lock()


def _notify(project_id: str, build_id: str) -> None:
    for fn in list(listeners):
        try:
            fn(project_id, build_id)
        except Exception:  # noqa: BLE001
            logger.exception("A build listener failed")


async def start() -> None:
    global _queue
    if _queue is not None:
        return
    _queue = asyncio.Queue()
    for _ in range(MAX_PARALLEL):
        _workers.append(asyncio.create_task(_worker()))
    # Builds a stopped backend left queued or running can't be followed: they failed.
    for r in db().query("SELECT id, project_id FROM builds WHERE status IN (?, ?)", (QUEUED, RUNNING)):
        db().update("builds", {"id": r["id"]}, {"status": FAILED, "error": "the app restarted during the build: build again", "ended_at": time.time()})


async def stop() -> None:
    global _queue
    for t in _workers:
        t.cancel()
    _workers.clear()
    _queue = None


async def _worker() -> None:
    assert _queue is not None
    while True:
        build_id, p, fut = await _queue.get()
        try:
            await asyncio.to_thread(_run, build_id, p)
        except Exception as e:  # noqa: BLE001
            logger.exception("Build %s failed", build_id)
            db().update("builds", {"id": build_id}, {"status": FAILED, "error": f"{type(e).__name__}: {e}", "ended_at": time.time()})
        finally:
            b = get(build_id)
            if not fut.done():
                fut.set_result(b)
            if b:
                _notify(b["project_id"], build_id)
            _inflight.pop(build_id, None)


def _image_id(image: str) -> str:
    info = docker_runner.image_info(image)
    return info["id"] if info else ""


async def request(project_id: str, kind: str, actor: Actor, via: str = "ui", wait: bool = False) -> dict[str, Any]:
    """Builds ``kind`` for the project (or reuses an identical earlier build)."""
    projects.require(project_id, actor, roles.BUILD, kind)
    p = await asyncio.to_thread(plan, project_id, kind)
    image_id = "" if p.in_process else await asyncio.to_thread(_image_id, p.image)
    if not p.in_process and not image_id:
        if not await asyncio.to_thread(docker_runner.available):
            raise BuildError("Docker isn't reachable: builds run in containers")
        raise BuildError(f"Forge's tools ({p.image}) aren't installed on this computer yet: press Install on the home page "
                         f"(or {IMAGE_BUILD.get(p.image, 'docker build it')})")
    sha = inputs_sha(p, image_id)
    row = db().one("SELECT * FROM builds WHERE project_id = ? AND kind = ? AND inputs_sha = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
                   (project_id, kind, sha, SUCCEEDED))
    if row is not None:
        audit.record("build.reuse", actor, project_id, row["id"], {"kind": kind})
        return {**_row(row), "reused": True}
    running = db().one("SELECT * FROM builds WHERE project_id = ? AND kind = ? AND inputs_sha = ? AND status IN (?, ?) ORDER BY created_at DESC LIMIT 1",
                       (project_id, kind, sha, QUEUED, RUNNING))
    if running is not None:
        fut = _inflight.get(running["id"])
        if wait and fut is not None:
            return await asyncio.shield(fut)
        return _row(running)
    build_id = ids.new_id("bld")
    now = time.time()
    db().insert("builds", {
        "id": build_id, "project_id": project_id, "kind": kind, "status": QUEUED, "inputs_sha": sha,
        "requested_by": actor.user, "requested_by_name": actor.name, "requested_via": via, "created_at": now,
        "image": "" if p.in_process else p.image, "image_id": image_id, "summary": {"meta": p.meta},
    })
    audit.record("build.request", actor, project_id, build_id, {"kind": kind, "via": via})
    _notify(project_id, build_id)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _inflight[build_id] = fut
    if _queue is None:
        await start()
    assert _queue is not None
    await _queue.put((build_id, p, fut))
    if wait:
        return await asyncio.shield(fut)
    return get(build_id)  # type: ignore[return-value]


def _src_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, data in sorted(files.items()):
            info = tarfile.TarInfo("src/" + path)
            info.size = len(data)
            info.mode = 0o444
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


_PROBLEM = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<sev>error|warning|fatal error|%Error|%Warning)[:\s-]*(?P<msg>.*)$", re.I)


def problems(log: str) -> list[dict[str, Any]]:
    out = []
    for line in log.splitlines():
        m = _PROBLEM.match(line.strip())
        if m:
            sev = "error" if "rror" in m.group("sev") else "warning"
            f = m.group("file").removeprefix("/work/src/")
            out.append({"file": f, "line": int(m.group("line")), "severity": sev, "message": m.group("msg").strip()[:300]})
    return out[:200]


def _run(build_id: str, p: Plan) -> None:
    b = get(build_id)
    assert b is not None
    bdir = paths.build_dir(b["project_id"], build_id)
    out = bdir / "out"
    out.mkdir(parents=True, exist_ok=True)
    src_tar = _src_tar(p.files)
    (bdir / "inputs.tar").write_bytes(src_tar)
    started = time.time()
    db().update("builds", {"id": build_id}, {"status": RUNNING, "started_at": started})
    _notify(b["project_id"], build_id)
    summary: dict[str, Any] = {"meta": p.meta}
    versions: dict[str, str] = {}
    if p.in_process:
        code, log = _run_registers(p, out, summary)
    else:
        container = docker_runner.create(build_id, b["project_id"], p.image, docker_runner.Limits(cpus=4, memory_mb=3072, timeout=900),
                                         docker_runner.workspace_tar(src_tar, {"job/run.sh": p.script.encode()}, {}))
        lines: list[str] = []
        stop = threading.Event()
        try:
            container.start()
            follower = threading.Thread(target=docker_runner.follow, args=(container, lines.append, stop), daemon=True)
            follower.start()
            code = docker_runner.wait(container, 900)
            if code is None:
                docker_runner.stop(container)
                code = -1
                lines.append("== the build took more than 15 minutes and was stopped")
            follower.join(timeout=5)
            docker_runner.fetch_out(container, out)
        finally:
            stop.set()
            docker_runner.remove(container)
        log = "\n".join(lines)
        for line in lines:
            if line.startswith("== "):
                versions[line[3:].split(" ", 1)[0].lower()] = line[3:].strip()
        summary["problems"] = problems(log)
        summary["errors"] = sum(1 for x in summary["problems"] if x["severity"] == "error")
        summary["warnings"] = sum(1 for x in summary["problems"] if x["severity"] == "warning")
        if p.kind == "firmware" and code == 0:
            summary["size"] = _size(log)
        if p.kind == "pcb" and (out / "pcb_summary.json").is_file():
            try:
                summary["pcb"] = json.loads((out / "pcb_summary.json").read_text())
            except ValueError:
                pass
    (bdir / "build.log").write_text(log)
    status = SUCCEEDED if code == 0 else FAILED
    outputs = []
    db().execute("DELETE FROM artifacts WHERE owner = ?", (build_id,))
    for f in sorted(out.rglob("*")):
        if f.is_file():
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            rel = f.relative_to(out).as_posix()
            outputs.append({"name": rel, "sha256": sha, "size": len(data)})
            db().insert("artifacts", {"id": ids.new_id("art"), "project_id": b["project_id"], "owner": build_id, "kind": p.kind,
                                      "name": rel, "sha256": sha, "size": len(data), "created_at": time.time()})
    m = {
        "schema": "chipova-forge.build/1",
        "build": build_id,
        "project": b["project_id"],
        "kind": p.kind,
        "inputs_sha": b["inputs_sha"],
        "generator_version": GENERATOR_VERSION[p.kind],
        "image": p.image if not p.in_process else "",
        "image_id": b["image_id"],
        "tools": versions,
        "command": p.script if not p.in_process else "PeakRDL (in-process): regblock apb3-flat, c-header, python",
        "inputs": [{"path": k, "sha256": hashlib.sha256(v).hexdigest(), "size": len(v)} for k, v in sorted(p.files.items())],
        "outputs": outputs,
        "exit_code": code,
        "started_at": started,
        "ended_at": time.time(),
        "requested_by": b["requested_by"],
    }
    (bdir / "manifest.json").write_text(json.dumps(m, indent=1))
    error = ""
    if status == FAILED:
        errs = [x for x in summary.get("problems", []) if x["severity"] == "error"]
        error = errs[0]["message"] if errs else (summary.get("error") or log.strip().splitlines()[-1:] or ["failed"])[0]
    db().update("builds", {"id": build_id}, {"status": status, "ended_at": time.time(), "exit_code": code,
                                              "summary": summary, "error": str(error)[:500]})


def _size(log: str) -> dict[str, int]:
    m = re.search(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[0-9a-f]+\s+\S*firmware\.elf", log, re.M)
    if not m:
        return {}
    return {"text": int(m.group(1)), "data": int(m.group(2)), "bss": int(m.group(3))}


def _run_registers(p: Plan, out: Path, summary: dict[str, Any]) -> tuple[int, str]:
    rdl_name = p.meta["rdl"]
    rdl = p.files[rdl_name].decode()
    try:
        regmap = regs_mod.read_map(rdl, rdl_name)
        gen = regs_mod.generate(rdl, rdl_name)
    except regs_mod.RegistersError as e:
        summary["error"] = str(e)
        return 1, str(e)
    for name, text in gen.regblock.items():
        (out / "rtl").mkdir(exist_ok=True)
        (out / "rtl" / name).write_text(text)
    (out / "c").mkdir(exist_ok=True)
    (out / "c" / gen.c_header_name).write_text(gen.c_header)
    (out / "python").mkdir(exist_ok=True)
    (out / "python" / f"{gen.module}.zip").write_bytes(gen.python_zip)
    (out / "register_map.json").write_text(json.dumps(regmap.to_dict(), indent=1))
    chip_files = {k[len("chip/"):]: v for k, v in p.files.items() if k.startswith("chip/")}
    sync = regs_mod.check_sync(gen, chip_files, p.meta.get("csr_rtl") or None) if not p.meta.get("harness") else \
        regs_mod.SyncCheck("harness", "Forge's register harness (the chip has no registers of its own)")
    summary.update({"registers": len(regmap.registers), "size": regmap.size, "module": gen.module,
                    "sync": {"status": sync.status, "detail": sync.detail, "diff": sync.diff[:4000]}})
    log = f"{rdl_name}: {len(regmap.registers)} registers, {regmap.size} bytes\nCSR RTL: {sync.status}: {sync.detail}\n"
    return 0, log
