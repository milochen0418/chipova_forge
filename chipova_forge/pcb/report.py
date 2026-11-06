"""In the board image: what KiCad's checks found (erc.json, drc.json), as pcb_summary.json, and
a few lines for the build's log.

    python3 -m forge_pcb.report OUTDIR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _brief(v: dict[str, Any]) -> dict[str, Any]:
    return {"type": v.get("type", ""), "severity": v.get("severity", ""), "description": v.get("description", ""),
            "items": [i.get("description", "") for i in v.get("items", [])][:3]}


def _count(vs: list[dict[str, Any]], severity: str) -> int:
    return sum(1 for v in vs if v.get("severity") == severity)


def summarise(out: Path) -> dict[str, Any]:
    s: dict[str, Any] = {}
    erc_f, drc_f = out / "erc.json", out / "drc.json"
    if erc_f.is_file():
        data = json.loads(erc_f.read_text())
        vs = [v for sheet in data.get("sheets", []) for v in sheet.get("violations", [])]
        s["erc"] = {"errors": _count(vs, "error"), "warnings": _count(vs, "warning"), "violations": [_brief(v) for v in vs][:60]}
    if drc_f.is_file():
        data = json.loads(drc_f.read_text())
        vs = data.get("violations", [])
        unconnected = data.get("unconnected_items", [])
        parity = data.get("schematic_parity", [])
        s["drc"] = {"errors": _count(vs, "error"), "warnings": _count(vs, "warning"), "unconnected": len(unconnected),
                    "parity": len(parity), "violations": [_brief(v) for v in vs][:60],
                    "unconnected_items": [_brief(v) for v in unconnected][:30], "parity_items": [_brief(v) for v in parity][:30]}
    s["outputs"] = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    return s


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    s = summarise(out)
    (out / "pcb_summary.json").write_text(json.dumps(s, indent=1))
    if "erc" in s:
        print(f"ERC: {s['erc']['errors']} errors, {s['erc']['warnings']} warnings")
        for v in s["erc"]["violations"][:10]:
            print(f"  {v['severity']}: {v['description']} {'; '.join(v['items'])}")
    if "drc" in s:
        d = s["drc"]
        print(f"DRC: {d['errors']} errors, {d['warnings']} warnings, {d['unconnected']} unconnected, {d['parity']} differences from the schematic")
        for v in (d["violations"] + d["unconnected_items"] + d["parity_items"])[:15]:
            print(f"  {v['severity']}: {v['description']} {'; '.join(v['items'])}")
    return 0 if "drc" in s and "erc" in s else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
