"""The autorouter's session file (Specctra SES): the copper it laid, net by net, as wires (a
layer, a width, a path) and vias. Read back into Forge's design, y negated again."""

from __future__ import annotations

import re

from .model import Design, Track, Via

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def parse(text: str) -> list:
    """An S-expression as nested lists of strings."""
    stack: list[list] = [[]]
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip():
                raise ValueError(f"unexpected text at {pos}: {text[pos:pos + 30]!r}")
            break
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            stack[-1].append(m.group(3))
        elif m.group(4) is not None:
            stack[-1].append(m.group(4))
            if m.group(4) == "string_quote":
                # Specctra's (string_quote ") names the quote character itself, unquoted.
                while pos < len(text) and text[pos].isspace():
                    pos += 1
                stack[-1].append(text[pos])
                pos += 1
    if len(stack) != 1:
        raise ValueError("unbalanced parentheses")
    return stack[0]


def _find(node: list, name: str) -> list | None:
    for x in node:
        if isinstance(x, list) and x and x[0] == name:
            return x
    return None


def _all(node: list, name: str) -> list[list]:
    return [x for x in node if isinstance(x, list) and x and x[0] == name]


def read(text: str, d: Design) -> tuple[list[Track], list[Via]]:
    """The tracks and vias of a session file, in Forge's millimetres."""
    tree = parse(text)
    ses = tree[0] if tree and isinstance(tree[0], list) else tree
    routes = _find(ses, "routes")
    if routes is None:
        raise ValueError("the session has no routes")
    res = _find(routes, "resolution")
    unit, per = (res[1], float(res[2])) if res else ("um", 1.0)
    scale = {"um": 0.001, "mm": 1.0, "mil": 0.0254, "inch": 25.4}[unit] / per

    def mm(v: str) -> float:
        return round(float(v) * scale, 4)

    tracks: list[Track] = []
    vias: list[Via] = []
    net_out = _find(routes, "network_out") or []
    for net in _all(net_out, "net"):
        name = net[1]
        for wire in _all(net, "wire"):
            path = _find(wire, "path")
            if path is None:
                continue
            layer, width = path[1], mm(path[2])
            coords = [c for c in path[3:] if isinstance(c, str)]
            pts = [[mm(coords[i]), -mm(coords[i + 1])] for i in range(0, len(coords) - 1, 2)]
            if len(pts) >= 2:
                tracks.append(Track(name, layer, width, pts))
        for via in _all(net, "via"):
            vias.append(Via(name, mm(via[2]), -mm(via[3]), d.rules.via, d.rules.via_drill))
    return tracks, vias
