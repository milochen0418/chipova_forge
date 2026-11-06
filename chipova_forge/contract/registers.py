"""The chip's registers: one SystemRDL description, everything else made from it (PeakRDL).

From the chip's ``registers/*.rdl``:

- the register map Forge shows and the device API uses (names, offsets, fields, access,
  reset values, descriptions);
- the CSR block in SystemVerilog (PeakRDL-regblock, APB3), compared with the copy in the chip's
  RTL: a chip whose registers no longer match its description is flagged before anything is
  built on it;
- the firmware's C header (PeakRDL-cheader);
- the SDK's Python register access layer (PeakRDL-python, as a zip).

Hardware and software never write register addresses by hand: they all come from the same file.
PeakRDL runs in-process (pure Python); what it generates belongs to the chip's owner.
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any

# PeakRDL-regblock's CPU interface that matches what Forge's co-simulation drives.
CPUIF = "apb3-flat"


class RegistersError(ValueError):
    pass


@dataclasses.dataclass
class Field:
    name: str
    lsb: int
    msb: int
    sw: str  # r | w | rw
    hw: str
    reset: int | None
    desc: str
    onwrite: str = ""

    @property
    def mask(self) -> int:
        return ((1 << (self.msb - self.lsb + 1)) - 1) << self.lsb


@dataclasses.dataclass
class Register:
    name: str
    offset: int
    width: int
    title: str
    desc: str
    fields: list[Field]

    @property
    def index(self) -> int:
        return self.offset // 4

    @property
    def access(self) -> str:
        sws = {f.sw for f in self.fields}
        if sws <= {"r"}:
            return "r"
        if sws <= {"w"}:
            return "w"
        return "rw"


@dataclasses.dataclass
class RegisterMap:
    name: str
    title: str
    desc: str
    size: int
    registers: list[Register]

    def by_name(self, name: str) -> Register | None:
        return next((r for r in self.registers if r.name.lower() == (name or "").lower()), None)

    def by_offset(self, offset: int) -> Register | None:
        return next((r for r in self.registers if r.offset == offset), None)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _compile(rdl_text: str, filename: str):  # noqa: ANN202
    from systemrdl import RDLCompileError, RDLCompiler  # noqa: PLC0415

    from peakrdl_python.compiler_udp import PythonHideUDP, PythonInstNameUDP  # noqa: PLC0415
    from peakrdl_regblock.udps import ALL_UDPS  # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="forge-rdl-")
    path = Path(tmp) / Path(filename).name
    path.write_text(rdl_text)
    rdlc = RDLCompiler()
    # The properties the generators understand (as their command lines register them).
    for udp in [*ALL_UDPS, PythonHideUDP, PythonInstNameUDP]:
        rdlc.register_udp(udp, soft=True)
    try:
        rdlc.compile_file(str(path))
        return rdlc.elaborate(), tmp
    except RDLCompileError as e:
        raise RegistersError(f"{filename} doesn't compile: {e}") from e


def _sw(f) -> str:  # noqa: ANN001
    r, w = f.is_sw_readable, f.is_sw_writable
    return "rw" if r and w else ("r" if r else "w")


def _hw(f) -> str:  # noqa: ANN001
    try:
        return str(f.get_property("hw")).rsplit(".", 1)[-1]
    except Exception:  # noqa: BLE001
        return ""


def read_map(rdl_text: str, filename: str = "registers.rdl") -> RegisterMap:
    root, _ = _compile(rdl_text, filename)
    top = root.top
    regs = []
    for reg in top.registers():
        fields = []
        for f in reg.fields():
            reset = f.get_property("reset")
            onwrite = f.get_property("onwrite")
            fields.append(Field(
                name=f.inst_name, lsb=f.lsb, msb=f.msb, sw=_sw(f), hw=_hw(f),
                reset=int(reset) if isinstance(reset, int) else None,
                desc=str(f.get_property("desc") or ""),
                onwrite=str(onwrite).rsplit(".", 1)[-1] if onwrite else "",
            ))
        regs.append(Register(
            name=reg.inst_name, offset=int(reg.address_offset), width=int(reg.get_property("regwidth")),
            title=str(reg.get_property("name") or reg.inst_name), desc=str(reg.get_property("desc") or ""), fields=fields,
        ))
    return RegisterMap(name=top.inst_name, title=str(top.get_property("name") or top.inst_name),
                       desc=str(top.get_property("desc") or ""), size=int(top.size), registers=regs)


@dataclasses.dataclass
class Outputs:
    regblock: dict[str, str]  # file name -> SystemVerilog
    c_header: str
    c_header_name: str
    python_zip: bytes
    module: str


def generate(rdl_text: str, filename: str = "registers.rdl") -> Outputs:
    from peakrdl_cheader.exporter import CHeaderExporter  # noqa: PLC0415
    from peakrdl_python.exporter import PythonExporter  # noqa: PLC0415
    from peakrdl_regblock import RegblockExporter  # noqa: PLC0415
    from peakrdl_regblock.cpuif.apb3 import APB3_Cpuif_flattened  # noqa: PLC0415

    root, tmp = _compile(rdl_text, filename)
    out = Path(tmp) / "out"
    (out / "regblock").mkdir(parents=True)
    RegblockExporter().export(root, str(out / "regblock"), cpuif_cls=APB3_Cpuif_flattened)
    regblock = {p.name: p.read_text() for p in sorted((out / "regblock").iterdir()) if p.is_file()}
    name = root.top.inst_name
    header_path = out / f"{name}.h"
    CHeaderExporter().export(root, str(header_path))
    py_dir = out / "python"
    py_dir.mkdir()
    PythonExporter().export(root.top, str(py_dir))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(py_dir.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(py_dir).as_posix())
    return Outputs(regblock=regblock, c_header=header_path.read_text(), c_header_name=header_path.name,
                   python_zip=buf.getvalue(), module=name)


@dataclasses.dataclass
class SyncCheck:
    status: str  # in_sync | differs | no_copy | no_registers
    detail: str
    generated_sha: str = ""
    chip_sha: str = ""
    diff: str = ""


def _norm(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.replace("\r\n", "\n").strip().splitlines()]


def check_sync(outputs: Outputs, chip_files: dict[str, bytes], csr_rtl: str | None) -> SyncCheck:
    """Whether the chip's CSR RTL is what its SystemRDL generates now."""
    gen = outputs.regblock.get(f"{outputs.module}.sv", "")
    gen_sha = hashlib.sha256(gen.encode()).hexdigest()
    if not csr_rtl or csr_rtl not in chip_files:
        return SyncCheck("no_copy", f"the chip's RTL has no module {outputs.module}: its registers come from somewhere else", gen_sha)
    chip_text = chip_files[csr_rtl].decode("utf-8", errors="replace")
    chip_sha = hashlib.sha256(chip_text.encode()).hexdigest()
    if _norm(chip_text) == _norm(gen):
        return SyncCheck("in_sync", f"{csr_rtl} is what the SystemRDL generates", gen_sha, chip_sha)
    diff = "\n".join(list(difflib.unified_diff(_norm(chip_text), _norm(gen), csr_rtl, "generated", lineterm="", n=1))[:60])
    return SyncCheck("differs", f"{csr_rtl} differs from what the SystemRDL generates: regenerate it in Chipova", gen_sha, chip_sha, diff)
