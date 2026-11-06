"""What an AI may read of a build's outputs, and how: the files a person sees or downloads on the
product's page are open to it; any other text file asks the person first
(``reflex_ddns_auth.tools.ask_first``); a binary file is pointed at what to read instead.

A PCB build's ``netlist`` isn't a file: it is written here from the design the build laid out (its
``design.json``), part by part (value, what it is, each pin's net) and net by net, which is what
answers "what does R3 do?" better than the KiCad files."""

from __future__ import annotations

import fnmatch
import tarfile
from typing import Any

from chipova_forge import ids
from chipova_forge.jobs import builds
from chipova_forge.pcb import model as pcb_model

MAX_CHARS = 60_000
# Open to the AI without asking: what the product's page shows or offers for download.
OPEN = {
    "pcb": ["netlist", "bom.csv", "positions.csv", "erc.json", "drc.json", "pcb_summary.json"],
    "firmware": ["firmware.map", "size.txt"],
    "registers": ["register_map.json", "c/*.h"],
    "chip": [],
}
BINARY = (".zip", ".pdf", ".elf", ".bin", ".so", ".tar", ".png", ".jpg", ".svg")
INSTEAD = {"pcb": "netlist (the parts and how they connect), bom.csv (the parts list) or positions.csv",
           "firmware": "firmware.map or size.txt", "registers": "register_map.json or c/*.h", "chip": "log"}


def is_open(kind: str, name: str) -> bool:
    return name == "log" or any(fnmatch.fnmatch(name, pattern) for pattern in OPEN.get(kind, []))


def names(build: dict[str, Any]) -> list[str]:
    """What there is to read: the build's log, its outputs, and a PCB build's netlist."""
    out = ["log"] + (["netlist"] if build["kind"] == "pcb" else [])
    return out + [a["name"] for a in builds.artifacts(build["id"])]


def netlist(build: dict[str, Any]) -> str:
    """The parts and nets of the design a PCB build laid out."""
    tar_path = builds.out_dir(build).parent / "inputs.tar"
    with tarfile.open(tar_path) as tf:
        member = next((m for m in tf.getmembers() if m.name.endswith("design.json")), None)
        if member is None:
            raise ValueError("this build kept no design")
        d = pcb_model.Design.from_json(tf.extractfile(member).read().decode())
    return netlist_of(d)


def netlist_of(d: pcb_model.Design) -> str:
    lines = [f"Board: {d.title or ''}, {d.width:g} × {d.height:g} mm, {len(d.parts)} parts.", "", "Parts (ref, value, what it is, its pins' nets):"]
    for p in sorted(d.parts, key=lambda p: (p.ref.rstrip("0123456789"), int("".join(ch for ch in p.ref if ch.isdigit()) or 0))):
        names_of = {pin.number: pin.name for pin in p.pins}
        pins = ", ".join(f"{n}{'' if names_of.get(n, '~') in ('~', '') else ' ' + names_of[n]} → {net or '(not connected)'}"
                         for n, net in sorted(p.nets.items(), key=lambda kv: (len(kv[0]), kv[0])))
        lines.append(f"- {p.ref}: {p.value}" + (f" ({p.description})" if p.description else "") + f"; pins: {pins}")
    lines += ["", "Nets (what each connects):"]
    members: dict[str, list[str]] = {}
    for p in d.parts:
        for n, net in p.nets.items():
            if net:
                members.setdefault(net, []).append(f"{p.ref}.{n}")
    for net in sorted(members):
        lines.append(f"- {net}: {', '.join(sorted(members[net]))}")
    return "\n".join(lines)


def read(build: dict[str, Any], name: str) -> str:
    """The text of one of the build's outputs (the caller asks the person first when it isn't open)."""
    if name == "log":
        text = builds.log_text(build)
    elif name == "netlist" and build["kind"] == "pcb":
        text = netlist(build)
    else:
        clean = ids.clean_path(name)
        if clean not in names(build):
            raise ValueError(f"{build['id']} has no output {name!r}: it has {', '.join(names(build))}")
        if clean.lower().endswith(BINARY):
            raise ValueError(f"{clean} isn't text: read {INSTEAD.get(build['kind'], 'log')} instead")
        path = builds.artifact_path(build, clean)
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{clean} isn't text: read {INSTEAD.get(build['kind'], 'log')} instead") from None
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n… ({len(text) - MAX_CHARS:,} more characters)"
    return text
