"""The chip's ports, read from its RTL (pyslang: SystemVerilog, elaborated like a compiler does).

Forge never guesses a chip's interface from file names: it elaborates the RTL files the chip's
``chipova.toml`` lists, finds the top module, and reads each port's direction and width. The
compiler's errors come back too (a chip that doesn't elaborate can't be simulated).
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Port:
    name: str
    direction: str  # in | out | inout
    width: int

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Elaboration:
    ports: list[Port]
    errors: list[str]
    warnings: list[str]
    tops: list[str]


_DIRECTIONS = {"In": "in", "Out": "out", "InOut": "inout", "Ref": "inout"}


def elaborate(sources: dict[str, str], top: str) -> Elaboration:
    """Elaborates ``sources`` (file name -> text, in order) with ``top`` as the top module."""
    from pyslang import ast, syntax  # noqa: PLC0415 - a large native module, loaded when used

    options = ast.CompilationOptions()
    try:
        options.topModules = {top} if top else set()
    except (AttributeError, TypeError):
        pass
    bag = ast.Bag([options]) if hasattr(ast, "Bag") else None
    comp = ast.Compilation(bag) if bag is not None else ast.Compilation()
    for name, text in sources.items():
        comp.addSyntaxTree(syntax.SyntaxTree.fromText(text, name))
    errors, warnings = [], []
    try:
        diags = comp.getAllDiagnostics()
        engine_text = _format(comp, diags)
        for d, text in zip(diags, engine_text):
            (errors if d.isError() else warnings).append(text)
    except Exception as e:  # noqa: BLE001
        errors.append(f"the compiler failed: {e}")
    root = comp.getRoot()
    tops = [i.name for i in root.topInstances]
    ports: list[Port] = []
    inst = next((i for i in root.topInstances if i.name == top), None)
    if inst is None and top:
        errors.append(f"there is no top module {top!r} in the RTL (found: {', '.join(tops) or 'none'})")
    elif inst is not None:
        for p in inst.body.portList:
            direction = _DIRECTIONS.get(str(p.direction).rsplit(".", 1)[-1], "inout")
            try:
                width = int(p.type.bitWidth)
            except (AttributeError, TypeError, ValueError):
                width = 1
            ports.append(Port(str(p.name), direction, max(1, width)))
    return Elaboration(ports=ports, errors=errors, warnings=warnings, tops=tops)


def _format(comp, diags) -> list[str]:  # noqa: ANN001
    """Each diagnostic as one line: file:line: message."""
    try:
        from pyslang import DiagnosticEngine  # noqa: PLC0415

        engine = DiagnosticEngine(comp.sourceManager)
        out = []
        for d in diags:
            text = DiagnosticEngine.reportAll(comp.sourceManager, [d]).strip()
            out.append(" ".join(text.splitlines()[:1]))
        del engine
        return out
    except Exception:  # noqa: BLE001
        return [str(d.code) for d in diags]
