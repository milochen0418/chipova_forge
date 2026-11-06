"""analog_loop: the enclosure fan controller, firmware, chip and physics in one loop.

A guest makes the fan controller (the smart sensor chip; its alarm pin switches a fan). The
Overview says the board has an analog side. In the Lab the simulation starts with ngspice in
step with the board: the enclosure warms past 30 °C, the console says ALARM, the fan comes on
(the chart shades it), it cools, NORMAL, the fan goes off, and again. The temperature slider is
replaced by the analog side's inputs; with the load at 0 W the enclosure cools and the fan stays
off. The system test passes, and the module laid out from the Board page carries the fan's
MOSFET and connector, clean in KiCad's checks."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from common import finish, launch, log, new_page, new_product, shot, view, wait_for, wait_status  # noqa: E402


def drive_on(page) -> str:  # noqa: ANN001
    loc = page.locator("[data-analog-drive='fan']")
    return loc.get_attribute("data-on") if loc.count() else ""


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = launch(pw)
        page = new_page(browser, errors, "Guest")
        pid = new_product(page, "Analog: fan controller", "fan_controller", "smart_sensor")
        log("made", pid)
        wait_for(lambda: "ngspice (thermal_fan)" in page.inner_text("#steps [data-step='6']"), "step 6 names the analog side")
        assert page.get_attribute("#steps [data-step='6']", "data-status") == "DONE"

        view(page, "lab")
        page.click("#start-session")
        t0 = time.time()
        page.wait_for_selector("#analog [data-analog-output='temperature']", timeout=180000)
        page.wait_for_selector("[data-uart-line^='READY']", timeout=60000)
        log(f"running after {time.time() - t0:.1f} s")
        assert page.locator("[data-sensor='temperature']").count() == 0, "the analog side drives the sensor: no slider"
        page.wait_for_selector("[data-uart-line^='ALARM']", timeout=60000)
        wait_for(lambda: drive_on(page) == "1", "the fan comes on", 10)
        page.wait_for_selector("[data-uart-line^='NORMAL']", timeout=60000)
        wait_for(lambda: drive_on(page) == "0", "the fan goes off", 10)
        wait_for(lambda: page.locator("[data-uart-line^='ALARM']").count() >= 2, "a second alarm: the loop holds", 60)
        page.wait_for_selector("#analog-chart svg")
        temp = float(page.inner_text("[data-analog-output='temperature']").split()[0])
        log("temperature in the loop:", temp)
        assert 28.5 <= temp <= 31.0, temp
        page.locator("#analog").scroll_into_view_if_needed()
        shot(page, "01_loop")

        slider = page.locator("[data-analog-slider='load']")
        slider.fill("0")
        slider.dispatch_event("mouseup")
        wait_for(lambda: page.inner_text("[data-analog-input='load']").startswith("0"), "the load is 0 W", 15)
        wait_for(lambda: float(page.inner_text("[data-analog-output='temperature']").split()[0]) < 29.4 and drive_on(page) == "0",
                 "the enclosure cools with the fan off", 60)
        shot(page, "02_cooled")
        page.click("#stop-session")

        view(page, "tests")
        page.click("[data-run-test='tests/fan_control.yaml']")
        st = wait_status(page, "#tests [data-test='tests/fan_control.yaml']", ("PASSED", "FAILED"), 300)
        assert st == "PASSED", page.inner_text("#test-steps")
        shot(page, "03_test")

        view(page, "board")
        page.click("#build-pcb")
        wait_for(lambda: "DRC 0 errors" in page.inner_text("#pcb-line"), "the module is laid out and checked", 300)
        line = page.inner_text("#pcb-line")
        assert "ERC 0 errors" in line and "0 unconnected" in line and "0 differences" in line, line
        assert any("J1 pin 3: +5V" in n for n in page.locator("#pcb-notes li").all_inner_texts())
        page.locator("#pcb").scroll_into_view_if_needed()
        shot(page, "04_module")
        browser.close()
    finish(errors)


if __name__ == "__main__":
    main()
