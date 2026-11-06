"""The carrier module: its netlist, footprints, placement, the autorouter's files and the KiCad
files, made in-process (the routing and KiCad's checks run in the board image: test_docker)."""

from __future__ import annotations

import json

import pytest

from chipova_forge import paths
from chipova_forge.contract import chip_package, contract
from chipova_forge.pcb import dsn, footprints, kicad, netlist, place, ses, stitch
from chipova_forge.pcb.model import Design, courtyard, overlaps, pad_at
from chipova_forge.system import board


def _design(chip: str = "smart_sensor", product: str = "smart_sensor", system: str | None = None) -> tuple[Design | None, list]:
    c, _, _ = contract.generate(chip_package.from_example(chip))
    assert c is not None
    text = system or (paths.templates_dir() / f"products/{product}/system.yaml").read_text()
    return netlist.build(c, board.from_yaml(text), "Test board")


def test_footprints_follow_the_package():
    q = footprints.qfn(16, 3.0, 3.0, 0.5, (1.6, 1.6))
    assert q.name == "QFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm_ThermalVias"
    first = {p.number: p for p in q.pads if p.number != "17"}
    assert (first["1"].x, first["1"].y) == (-1.475, -0.75) and (first["5"].x, first["5"].y) == (-0.75, 1.475)
    assert (first["9"].x, first["9"].y) == (1.475, 0.75) and (first["13"].x, first["13"].y) == (0.75, -1.475)
    ep = [p for p in q.pads if p.number == "17"]
    assert len(ep) == 6 and sum(p.kind == "thru_hole" for p in ep) == 4 and all(p.solid for p in ep)
    s = footprints.soic()
    assert [(p.number, p.x) for p in s.pads][:1] == [("1", -2.475)] and s.pad("8").y == -1.905
    with pytest.raises(ValueError):
        footprints.qfn(18, 3, 3, 0.5)


def test_the_smart_sensor_module():
    d, issues = _design()
    assert d is not None and not [m for lvl, m in issues if lvl == "error"]
    refs = {p.ref: p for p in d.parts}
    assert set(refs) == {"U1", "J1", "U2", "X1", "R1", "R2", "R3", "R4", "C1", "C2", "C3", "C4", "C5", "C6", "D1"}
    u1 = refs["U1"]
    assert u1.nets["1"] == "SPI_SCK" and u1.nets["9"] == "IRQ" and u1.nets["17"] == "GND" and u1.nets["8"] == ""
    assert [p.name for p in refs["J1"].pins] == ["+3V3/3V", "GND/GND", "SPI_SCK/PA5", "SPI_MOSI/PA7", "SPI_MISO/PA6", "SPI_CS_N/PA4",
                                                 "IRQ/PB1", "I2C1_SCL/PB6", "I2C1_SDA/PB7"]
    sensor = refs["U2"]
    assert sensor.value == "TMP1075" and {sensor.nets[n] for n in ("5", "6", "7")} == {"GND"}  # address 0x48
    assert refs["X1"].value == "25 MHz" and refs["X1"].mpn == "ECS-3225MV-250-BN-TR"
    assert refs["D1"].nets == {"1": "GND", "2": "ALARM_LED_A"}
    assert d.nets()[:2] == ["GND", "+3V3"]


def test_an_address_pin_follows_the_address():
    text = (paths.templates_dir() / "products/smart_sensor/system.yaml").read_text().replace("address: 0x48", "address: 0x4B")
    d, _ = _design(system=text)
    assert d is not None
    u2 = d.part("U2")
    assert (u2.nets["7"], u2.nets["6"], u2.nets["5"]) == ("+3V3", "+3V3", "GND")  # A0, A1, A2


def test_what_isnt_laid_out_says_why():
    text = (paths.templates_dir() / "products/smart_sensor/system.yaml").read_text().replace("part: tmp1075", "part: tmp108")
    d, issues = _design(system=text)
    assert d is not None and any("tmp108" in m and lvl == "error" for lvl, m in issues)
    c, _, _ = contract.generate(chip_package.from_example("smart_sensor"))
    assert c is not None
    c.interface.protocol = "apb3"
    d, issues = netlist.build(c, board.from_yaml((paths.templates_dir() / "products/smart_sensor/system.yaml").read_text()), "x")
    assert d is None and "APB3" in issues[0][1]


def test_placement_keeps_parts_apart_and_on_the_board():
    for chip, product in (("smart_sensor", "smart_sensor"), ("heartbeat", "chip_bringup")):
        d, _ = _design(chip, product)
        assert d is not None
        place.place(d)
        boxes = [courtyard(p) for p in d.parts]
        for i, a in enumerate(boxes):
            assert 0 <= a[0] and 0 <= a[1] and a[2] <= d.width and a[3] <= d.height, d.parts[i].ref
            for b in boxes[i + 1:]:
                assert not overlaps(a, b, 0.1)
        # Each small part sits by the pad it serves.
        for p in d.parts:
            if p.near:
                ref, _, num = p.near.partition(":")
                anchor = d.part(ref)
                ax, ay = pad_at(anchor, anchor.footprint.pad(num))
                assert abs(p.x - ax) + abs(p.y - ay) < 16, p.ref


def test_the_router_files_and_the_session_back():
    d, _ = _design()
    assert d is not None
    place.place(d)
    text = dsn.write(d)
    tree = ses.parse(text)
    assert tree[0][0] == "pcb"
    assert '(pin "Round[A]Pad_600_um" "17@1"' in text and "U1-17@4" in text
    assert '(class power "GND" "+3V3"' in text and "(control (via_at_smd off))" in text
    session = ('(session board.ses (routes (resolution um 10) (parser) (network_out '
               '(net "SPI_SCK" (wire (path F.Cu 2500 100000 -50000 200000 -50000)) (via "Via[0-1]_600:300_um" 200000 -50000)))))')
    tracks, vias = ses.read(session, d)
    assert tracks[0].points == [[10.0, 5.0], [20.0, 5.0]] and tracks[0].width == 0.25 and tracks[0].layer == "F.Cu"
    assert (vias[0].x, vias[0].y, vias[0].net) == (20.0, 5.0, "SPI_SCK")


def test_the_kicad_files():
    d, _ = _design()
    assert d is not None
    place.place(d)
    d.tracks, d.vias = [], []
    pcb, sch, pro = kicad.write_pcb(d), kicad.write_sch(d), json.loads(kicad.write_pro(d))
    for text in (pcb, sch):
        ses.parse(text)  # balanced, quoted right
    assert '(footprint "Forge:QFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm_ThermalVias"' in pcb
    assert '(net 1 "GND")' in pcb and '"unconnected-(U1-NC-Pad8)"' in pcb and "(zone_connect 2)" in pcb
    assert pcb.count("(zone (net 1)") == 2
    assert sch.count('(global_label "SPI_SCK"') == 2 and '(lib_id "power:PWR_FLAG")' in sch
    assert '(property "Footprint" "Forge:SOIC-8_3.9x4.9mm_P1.27mm"' in sch
    assert pro["net_settings"]["netclass_patterns"] == [{"netclass": "Power", "pattern": "GND"}, {"netclass": "Power", "pattern": "+3V3"}]
    # The same inputs make the same files (stable ids), so identical builds are reused.
    assert kicad.write_pcb(d) == pcb


def test_stitching_vias_keep_clear():
    d, _ = _design()
    assert d is not None
    place.place(d)
    vias = stitch.stitch(d)
    assert vias and all(v.net == "GND" for v in vias)
    for v in vias:
        for p in d.parts:
            for pad in p.footprint.pads:
                x, y = pad_at(p, pad)
                assert abs(v.x - x) > 0.6 or abs(v.y - y) > 0.6


def test_the_fan_module_carries_its_driver():
    d, issues = _design(product="fan_controller")
    assert d is not None and not [m for lvl, m in issues if lvl == "error"]
    refs = {p.ref: p for p in d.parts}
    q = next(p for p in d.parts if p.ref.startswith("Q"))
    assert q.value == "AO3400A" and q.nets == {"1": "FAN_G", "2": "GND", "3": "FAN_SW"}
    gate = next(p for p in d.parts if p.ref.startswith("R") and p.nets.get("2") == "FAN_G")
    assert gate.nets["1"] == "ALARM_LED"
    assert refs["J2"].nets == {"1": "+5V", "2": "FAN_SW"} and "+5V" in d.power_nets
    assert [p.name for p in refs["J1"].pins][2] == "+5V/5V"
