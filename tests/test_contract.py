"""The chip's import, its contract, its registers, the board, the device protocol, the harness:
everything Forge makes from files, without containers."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from chipova_forge.contract import chip_package, contract, registers
from chipova_forge.contract.ports import elaborate
from chipova_forge.cosim import harness, wrapper
from chipova_forge.gateway import device
from chipova_forge.system import board, catalog


def _release_zip(pkg: chip_package.ChipPackage, tamper: str = "") -> bytes:
    """A release package as Chipova's Release page makes it."""
    files = dict(pkg.files)
    entries = chip_package.manifest(files)
    rel = {"schema": "chipova.release-manifest/1", "project": {"id": "abcdef0123", "title": "Smart sensor"},
           "release": {"id": "rel-0123456789", "status": "APPROVED"}, "gate": {"passed": True, "missing": []},
           "revision": {"id": pkg.snapshot, "files": entries}}
    if tamper:
        files[tamper] = files[tamper] + b"\n// changed"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, d in files.items():
            z.writestr(f"source/{p}", d)
        z.writestr("RELEASE_MANIFEST.json", json.dumps(rel))
    return buf.getvalue()


def test_snapshot_id_is_chipovas_revision_id():
    pkg = chip_package.from_example("smart_sensor")
    canonical = json.dumps([[e["path"], e["sha256"]] for e in chip_package.manifest(pkg.files)], separators=(",", ":"))
    assert pkg.snapshot == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    assert chip_package.from_example("smart_sensor").snapshot == pkg.snapshot


def test_release_zip_is_checked_file_by_file():
    pkg = chip_package.from_example("smart_sensor")
    got = chip_package.from_release_zip(_release_zip(pkg), "r.zip")
    assert got.snapshot == pkg.snapshot
    assert got.source["chipova_project"] == "abcdef0123"
    with pytest.raises(chip_package.ChipImportError, match="changed rtl/smart_sensor_top.sv"):
        chip_package.from_release_zip(_release_zip(pkg, tamper="rtl/smart_sensor_top.sv"))
    with pytest.raises(chip_package.ChipImportError, match="RELEASE_MANIFEST"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("source/chipova.toml", "")
        chip_package.from_release_zip(buf.getvalue())


def test_a_changed_chip_is_noticed(tmp_path):
    pkg = chip_package.from_example("heartbeat")
    chip_package.write(pkg, tmp_path / "chip")
    assert chip_package.load(tmp_path / "chip").snapshot == pkg.snapshot
    (tmp_path / "chip" / "rtl" / "heartbeat.v").write_text("module heartbeat; endmodule\n")
    with pytest.raises(chip_package.ChipImportError, match="changed since"):
        chip_package.load(tmp_path / "chip")


def test_ports_come_from_the_elaborated_rtl():
    e = elaborate({"a.sv": "module m #(parameter W = 4) (input logic clk, input logic [W-1:0] d, output logic q); endmodule"}, "m")
    assert [(p.name, p.direction, p.width) for p in e.ports] == [("clk", "in", 1), ("d", "in", 4), ("q", "out", 1)]
    assert elaborate({"a.v": "module m(input a); assign b = c; endmodule"}, "m").errors


def test_the_smart_sensor_contract():
    c, issues, _ = contract.generate(chip_package.from_example("smart_sensor"))
    assert c is not None and not [i for i in issues if i.level == "error"]
    assert c.interface.protocol == "spi"
    assert c.interface.ports == {"sclk": "spi_sclk", "mosi": "spi_mosi", "miso": "spi_miso", "cs_n": "spi_cs_n"}
    assert c.reset.port == "rst_n" and c.reset.active_low
    assert [p.port for p in c.irqs] == ["irq"] and [p.port for p in c.outputs] == ["alarm_led"]
    assert c.design.csr_module == "smart_sensor_regs" and c.design.csr_rtl == "rtl/smart_sensor_regs.sv"
    assert c.package and c.package.type == "QFN-16"
    assert contract.from_yaml(contract.to_yaml(c)) == c


def test_a_chip_without_a_bus_gets_a_harness():
    c, issues, _ = contract.generate(chip_package.from_example("heartbeat"))
    assert c is not None and c.interface.protocol == "none"
    assert any("harness" in i.message for i in issues)
    rdl = harness.render_rdl(c)
    m = registers.read_map(rdl, "forge_harness.rdl")
    assert [r.name for r in m.registers] == ["ID", "CONTROL", "OUTPUTS", "EDGES_0"]
    out = registers.generate(rdl, "forge_harness.rdl")
    srcs = {k: v for k, v in out.regblock.items()}
    srcs["heartbeat.v"] = chip_package.from_example("heartbeat").text("rtl/heartbeat.v")
    srcs["forge_harness.sv"] = harness.render_verilog(c)
    order = ["forge_harness_regs_pkg.sv", "forge_harness_regs.sv", "heartbeat.v", "forge_harness.sv"]
    e = elaborate({k: srcs[k] for k in order}, "forge_harness")
    assert not e.errors, e.errors
    assert {p.name for p in e.ports} >= {"psel", "pready", "prdata", "out"}


def test_contract_checks_find_what_doesnt_fit():
    c, _, _ = contract.generate(chip_package.from_example("smart_sensor"))
    assert c is not None and c.package is not None
    c.package.pinmap = [e for e in c.package.pinmap if e.signal != "irq"]
    msgs = [i.message for i in contract.check(c)]
    assert "irq has no pin" in msgs
    c.interface.ports["sclk"] = "nope"
    assert any("isn't a port" in m for m in [i.message for i in contract.check(c)])


def test_registers_are_generated_and_checked_against_the_chip():
    pkg = chip_package.from_example("smart_sensor")
    rdl = pkg.text("registers/smart_sensor.rdl")
    m = registers.read_map(rdl, "smart_sensor.rdl")
    status = m.by_name("STATUS")
    assert status and status.offset == 0x10 and [f.name for f in status.fields] == ["ABOVE", "ALARM"]
    assert m.by_name("THRESHOLD").fields[0].reset == 3000  # type: ignore[union-attr]
    out = registers.generate(rdl, "smart_sensor.rdl")
    assert "SMART_SENSOR_REGS__STATUS__ALARM_bm 0x2" in out.c_header
    assert registers.check_sync(out, pkg.files, "rtl/smart_sensor_regs.sv").status == "in_sync"
    files = dict(pkg.files)
    files["rtl/smart_sensor_regs.sv"] = files["rtl/smart_sensor_regs.sv"].replace(b"16'hbb8", b"16'hbb9")
    s = registers.check_sync(out, files, "rtl/smart_sensor_regs.sv")
    assert s.status == "differs" and "16'hbb8" in s.diff
    with pytest.raises(registers.RegistersError):
        registers.read_map("addrmap x { reg { field {} f; } r; ", "bad.rdl")


def _board(text: str | None = None) -> board.Board:
    from chipova_forge import paths

    return board.from_yaml(text or (paths.templates_dir() / "products/smart_sensor/system.yaml").read_text())


def test_the_board_and_what_it_makes():
    c, _, _ = contract.generate(chip_package.from_example("smart_sensor"))
    assert c is not None
    b = _board()
    assert board.check(b, c) == []
    h = board.board_h(b, c, None)
    for macro in ("BOARD_CHIP_SPI SPI1_BASE", "BOARD_CHIP_CS_PIN 4u", "BOARD_CHIP_IRQn 7u", "BOARD_CHIP_IRQHandler EXTI1_IRQHandler",
                  "BOARD_SENSOR_ADDR 0x48u", "BOARD_LED_ALARM_PIN 14u", "BOARD_SAMPLE_MS 100u"):
        assert f"#define {macro}" in h
    repl, sources = board.board_repl(b, c)
    assert "chip: CoSimulated.ForgeSpiChip @ spi1" in repl and "4 -> exti@4 | chip@0" in repl
    assert "0 -> gpioPortB@1" in repl and "1 -> chip_alarm_led@0" in repl
    assert sources == ["ForgeTMP108.cs", "ForgeSpiChip.cs"]
    js = board.session_json(b, c, None)
    assert js["chip"]["access"] == "spi" and js["initial"]["sensors"]["temperature"]["MilliCelsius"] == 25000


def test_board_checks_catch_clashes():
    c, _, _ = contract.generate(chip_package.from_example("smart_sensor"))
    bad = _board(_board_text().replace("chip_select: PA4", "chip_select: PA2").replace("irq: PB1", "irq: PB9"))
    msgs = [i.message for i in board.check(bad, c)]
    assert "PA2 is already usart2 TX" in msgs
    assert any("EXTI0-4" in m for m in msgs)
    assert any("no part" in i.message for i in board.check(_board(_board_text().replace("part: tmp1075", "part: nope")), c))


def _board_text() -> str:
    from chipova_forge import paths

    return (paths.templates_dir() / "products/smart_sensor/system.yaml").read_text()


def test_the_platform_keeps_gpio_to_exti_wired():
    lists = catalog.renode_pin_lists("stm32f4_discovery")
    assert "exti@1" in lists["gpioPortB"][1] and "timer3@03" in lists["gpioPortB"][1]
    assert lists["gpioPortD"][14][-1] == "ledRed@0"


def test_the_cosim_wrapper_for_spi_and_apb3():
    c, _, _ = contract.generate(chip_package.from_example("smart_sensor"))
    spec = board.cosim_spec(c, tuple(c.design.rtl_sources))
    cpp = wrapper.render_sim_main(spec)
    assert "spiTransfer" in cpp and "ClockOnly" in cpp and "top->spi_sclk = 1" in cpp
    assert "communicationChannel->sendMain(Protocol(ok, 0, 0))" in cpp
    assert "RESET_ACTIVE_LOW" in wrapper.render_cmake(spec)
    hb, _, _ = contract.generate(chip_package.from_example("heartbeat"))
    hspec = board.cosim_spec(hb, ("a.sv",), top="forge_harness", spec_ports=harness.contract_ports())
    assert hspec.clock == "clk" and hspec.reset == "rst_n" and "APB3* bus" in wrapper.render_sim_main(hspec)
    with pytest.raises(ValueError):
        wrapper.render_sim_main(wrapper.CosimSpec(top="x; rm", sources=("a.sv",), clock="clk", reset="r", reset_active_low=True, spi=spec.spi))


def test_the_device_protocol():
    from chipova_forge import paths

    p = device.load((paths.templates_dir() / "products/smart_sensor/device.yaml").read_text())
    assert device.encode(p, "set_threshold", {"celsius": 35}) == "SET THRESHOLD 3500"
    assert device.encode(p, "set_threshold", {"celsius": -5.25}) == "SET THRESHOLD -525"
    assert device.encode(p, "enable", {"enabled": False}) == "ENABLE 0"
    how, r = device.reply_of(p, "get_status", "STATUS temp=4000 threshold=3500 above=1 alarm=0 count=1 enabled=1 hyst=50")
    assert how == "ok" and r == {"temperature": 40.0, "threshold": 35.0, "above": True, "alarm": False, "alarms": 1, "enabled": True, "hysteresis": 0.5}
    assert device.reply_of(p, "set_threshold", "ERR bad value") == ("error", {"message": "bad value"})
    assert device.event_of(p, "ALARM temp=4000 threshold=3500 above=1 alarm=1 count=1 enabled=1 hyst=50")[0] == "alarm"  # type: ignore[index]
    for bad in ({"celsius": 300}, {}, {"celsius": 30, "x": 1}, {"celsius": "hot"}):
        with pytest.raises(device.DeviceError):
            device.encode(p, "set_threshold", bad)
    with pytest.raises(device.DeviceError, match="no command"):
        device.encode(p, "nope", {})
    with pytest.raises(device.DeviceError, match="quotes"):
        device.load('commands: {x: {send: "A \\" B"}}')
