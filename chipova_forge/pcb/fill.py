"""In the board image: fills the board's copper pours with KiCad's own filler (its pcbnew
module), so the checks and the Gerbers see the ground the pours carry.

    python3 -m forge_pcb.fill board.kicad_pcb
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    import pcbnew  # KiCad's module (the board image's KiCad)

    board = pcbnew.LoadBoard(argv[1])
    zones = board.Zones()
    pcbnew.ZONE_FILLER(board).Fill(zones)
    board.Save(argv[1])
    print(f"filled {len(zones)} copper pours")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
