"""What Forge's boards are made of: MCU platforms and virtual components (templates/platforms,
templates/components). Read once, from the app's own files."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

from chipova_forge import ids, paths

_PIN = re.compile(r"^P([A-I])(\d{1,2})$")


class CatalogError(ValueError):
    pass


def platform_dir(platform_id: str) -> Path:
    if not ids.is_name(platform_id):
        raise CatalogError(f"there is no platform {platform_id!r}")
    d = paths.templates_dir() / "platforms" / platform_id
    if not (d / "platform.yaml").is_file():
        raise CatalogError(f"there is no platform {platform_id!r}")
    return d


@functools.lru_cache(maxsize=16)
def platform(platform_id: str) -> dict[str, Any]:
    return yaml.safe_load((platform_dir(platform_id) / "platform.yaml").read_text())


def platforms() -> dict[str, dict[str, Any]]:
    out = {}
    for d in sorted((paths.templates_dir() / "platforms").iterdir()):
        if (d / "platform.yaml").is_file():
            out[d.name] = platform(d.name)
    return out


@functools.lru_cache(maxsize=1)
def components() -> dict[str, dict[str, Any]]:
    return yaml.safe_load((paths.templates_dir() / "components" / "components.yaml").read_text()) or {}


def component(part: str) -> dict[str, Any]:
    c = components().get(part)
    if c is None:
        raise CatalogError(f"there is no part {part!r} (there are: {', '.join(components())})")
    return c


def component_file(name: str) -> Path:
    return paths.templates_dir() / "components" / name


def parse_pin(pin: str) -> tuple[str, int]:
    """'PB1' -> ('B', 1)."""
    m = _PIN.match((pin or "").strip().upper())
    if not m or int(m.group(2)) > 15:
        raise CatalogError(f"{pin!r} isn't a pin (PA0 .. PI15)")
    return m.group(1), int(m.group(2))


@functools.lru_cache(maxsize=16)
def renode_pin_lists(platform_id: str) -> dict[str, dict[int, list[str]]]:
    """The platform's GPIO pin -> connection lists from its Renode fixes file (what each pin is
    wired to before Forge adds the board's own), by port: {"gpioPortA": {0: [...], ...}}."""
    p = platform(platform_id)
    fixes = (p.get("renode") or {}).get("fixes")
    out: dict[str, dict[int, list[str]]] = {}
    if not fixes:
        return out
    port = None
    for line in (platform_dir(platform_id) / fixes).read_text().splitlines():
        m = re.match(r"^(gpioPort[A-I]):\s*$", line)
        if m:
            port = m.group(1)
            out[port] = {}
            continue
        m = re.match(r"^\s+(\d+)\s*->\s*(.+)$", line)
        if port and m:
            out[port][int(m.group(1))] = [t.strip() for t in m.group(2).split("|")]
        elif line.strip() and not line.startswith((" ", "/")):
            port = None
    return out


# ------------------------------------------------------------------ analog models


def analog_models() -> dict[str, dict[str, Any]]:
    """Forge's analog models (templates/analog/*.yaml): {name: model}."""
    out = {}
    root = paths.templates_dir() / "analog"
    if root.is_dir():
        for f in sorted(root.glob("*.yaml")):
            out[f.stem] = yaml.safe_load(f.read_text()) or {}
    return out


def analog_model(name: str) -> dict[str, Any]:
    models = analog_models()
    if name not in models:
        raise CatalogError(f"there is no analog model {name!r} (there are: {', '.join(models) or 'none'})")
    return models[name]
