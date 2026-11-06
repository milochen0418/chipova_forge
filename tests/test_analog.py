"""The board's analog side, in-process: the model, the board's checks, what the session agent
gets, the system test steps and the chart. (ngspice itself runs in the session container:
test_docker.)"""

from __future__ import annotations

import pytest

from chipova_forge import paths
from chipova_forge.contract import chip_package, contract
from chipova_forge.cosim import analog_svg
from chipova_forge.services import systemtests
from chipova_forge.system import board, catalog


def _fan_board(edit=lambda t: t) -> board.Board:  # noqa: ANN001
    return board.from_yaml(edit((paths.templates_dir() / "products/fan_controller/system.yaml").read_text()))


def _contract():
    c, _, _ = contract.generate(chip_package.from_example("smart_sensor"))
    assert c is not None
    return c


def test_the_model_and_its_netlist():
    m = catalog.analog_model("thermal_fan")
    assert set(m["inputs"]) == {"ambient", "load"} and set(m["drives"]) == {"fan"} and m["outputs"]["temperature"]["node"] == "temp"
    lines = board.analog_netlist(_fan_board())
    assert "Rcase temp amb 20" in lines and "Ccase temp 0 2 ic=28" in lines and ".tran 5m 1e7 0 5m uic" in lines
    assert "Vfan fan 0 dc 0 external" in lines
    with pytest.raises(catalog.CatalogError):
        catalog.analog_model("nope")


def test_the_board_checks_its_analog_side():
    c = _contract()
    assert board.check(_fan_board(), c) == []
    msgs = [i.message for i in board.check(_fan_board(lambda t: t.replace("fan: alarm_led", "fan: irq")), c)]
    assert any("one of chip.leds" in m for m in msgs)
    msgs = [i.message for i in board.check(_fan_board(lambda t: t.replace("load: 1.5", "load: 50")), c)]
    assert any("load is 0..5" in m for m in msgs)
    msgs = [i.message for i in board.check(_fan_board(lambda t: t.replace("model: thermal_fan", "model: nope")), c)]
    assert any("no analog model" in m for m in msgs)


def test_what_the_session_agent_gets():
    a = board.session_json(_fan_board(), _contract(), None)["analog"]
    assert a["step"] == 0.05 and a["drives"]["fan"]["path"] == "sysbus.gpioPortD.chip_alarm_led"
    assert a["sensors"] == [{"sensor": "temperature", "output": "temperature", "property": "MilliCelsius", "scale": 1000}]
    assert a["inputs"]["load"]["value"] == 1.5 and a["inputs"]["ambient"]["source"] == "vamb"
    smart = board.from_yaml((paths.templates_dir() / "products/smart_sensor/system.yaml").read_text())
    assert board.session_json(smart, _contract(), None)["analog"] is None


def test_the_fan_test_reads():
    spec = systemtests.load((paths.templates_dir() / "products/fan_controller/tests/fan_control.yaml").read_text())
    kinds = [next(k for k in st if k in systemtests.STEP_KINDS) for st in spec["steps"]]
    assert "analog" in kinds and "expect_analog" in kinds


def test_the_chart():
    hist = [(i * 0.1, {"temperature": 29.5 + (i % 10) * 0.06}, {"fan": int(i % 10 > 5)}) for i in range(80)]
    svg = analog_svg.render(hist, "temperature", "°C", "fan", marks={"threshold": 30.0})
    assert svg.startswith("<svg") and "threshold 30" in svg and svg.count('fill="#38bdf8"') >= 3
    assert analog_svg.render(hist[:1], "temperature", "°C") == ""
