"""In the board image: the KiCad files of the routed board.

    python3 -m forge_pcb.run design.json board.ses OUTDIR
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import kicad, ses
from .stitch import stitch
from .model import Design


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: python3 -m forge_pcb.run design.json board.ses OUTDIR", file=sys.stderr)
        return 2
    d = Design.from_json(Path(argv[1]).read_text())
    if argv[2] != "-":
        d.tracks, d.vias = ses.read(Path(argv[2]).read_text(), d)
    stitching = stitch(d)
    d.vias += stitching
    out = Path(argv[3])
    out.mkdir(parents=True, exist_ok=True)
    (out / "board.kicad_pcb").write_text(kicad.write_pcb(d))
    (out / "board.kicad_sch").write_text(kicad.write_sch(d))
    (out / "board.kicad_pro").write_text(kicad.write_pro(d))
    segments = sum(len(t.points) - 1 for t in d.tracks)
    print(f"KiCad files: {len(d.parts)} parts, {len(d.nets())} nets, {segments} track segments, {len(d.vias)} vias, "
          f"{d.width} x {d.height} mm")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
