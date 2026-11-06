"""Forge's two tool images, built in the app when they aren't on this computer yet (an App Store
install doesn't bring them): the Dockerfiles in ``infra/dockerfiles``, built through the Docker
API, on the platform's network with its download cache when there is one.

- ``chipova-forge/sim:1``: Renode, Verilator, Arm GCC, ngspice (builds, simulations, tests)
- ``chipova-forge/board:1``: KiCad, Freerouting (the schematic and PCB)
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from pathlib import Path

from chipova_forge.jobs import docker_runner

logger = logging.getLogger(__name__)

DOCKERFILES = Path(__file__).resolve().parents[2] / "infra" / "dockerfiles"
TOOLS = {
    "sim": {"image": "chipova-forge/sim:1", "folder": "forge-sim", "title": "Simulation tools",
            "what": "Renode, Verilator, Arm GCC, ngspice: builds, simulations, tests", "size": "about 2.3 GB"},
    "board": {"image": "chipova-forge/board:1", "folder": "forge-board", "title": "Board tools",
              "what": "KiCad and Freerouting: the schematic and the PCB", "size": "about 1.7 GB"},
}
CACHE = "http://pkg-cache:3142"

# tool -> {"status": "installing" | "done" | "error", "line": str, "started": float}
progress: dict[str, dict] = {}
_lock = threading.Lock()


def installed(key: str) -> bool:
    try:
        return docker_runner.image_info(TOOLS[key]["image"]) is not None
    except Exception:  # noqa: BLE001 - no Docker: not installed
        return False


def missing() -> list[str]:
    return [k for k in TOOLS if not installed(k)]


def _network() -> str | None:
    """The platform's network (where the download cache is), when Forge runs on the platform."""
    if not os.path.exists("/.dockerenv"):
        return None
    try:
        own = docker_runner.client().containers.get(socket.gethostname())
        names = list(own.attrs["NetworkSettings"]["Networks"])
        return next((n for n in names if "ddns" in n), names[0] if names else None)
    except Exception:  # noqa: BLE001
        return None


def _cache_running() -> bool:
    try:
        return docker_runner.client().containers.get("pkg-cache").status == "running"
    except Exception:  # noqa: BLE001
        return False


def _build(key: str) -> None:
    spec = TOOLS[key]
    try:
        net = _network()
        args = {"PKG_CACHE": CACHE} if net and _cache_running() else {}
        for event in docker_runner.client().api.build(path=str(DOCKERFILES / spec["folder"]), tag=spec["image"], rm=True, decode=True,
                                                      buildargs=args, network_mode=net if args else None, pull=True):
            if "error" in event:
                raise RuntimeError(str(event["error"]).strip())
            line = str(event.get("stream", "")).strip()
            if line:
                progress[key]["line"] = line[:160]
        if not installed(key):
            raise RuntimeError("the build ended but the image isn't there")
        progress[key] = {"status": "done", "line": "Installed", "started": progress[key]["started"]}
    except Exception as e:  # noqa: BLE001 - shown on the home page
        logger.exception("Installing %s failed", spec["image"])
        progress[key] = {"status": "error", "line": str(e)[:300], "started": progress.get(key, {}).get("started", time.time())}


def install(keys: list[str] | None = None) -> None:
    """Builds the missing images, one after another, in a thread."""
    keys = [k for k in (keys or list(TOOLS)) if k in TOOLS and not installed(k)]
    with _lock:
        keys = [k for k in keys if progress.get(k, {}).get("status") != "installing"]
        for k in keys:
            progress[k] = {"status": "installing", "line": "Waiting…" if k != keys[0] else "Starting…", "started": time.time()}
    if not keys:
        return

    def work() -> None:
        for k in keys:
            progress[k].update(line="Starting…", started=time.time())
            _build(k)

    threading.Thread(target=work, daemon=True, name="forge-tools").start()


def busy() -> bool:
    return any(p.get("status") == "installing" for p in progress.values())
