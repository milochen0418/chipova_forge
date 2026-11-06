"""lab_flow: the simulation, seen by two people at once.

One guest starts the smart temperature controller's simulation (the firmware on a virtual
STM32F4, the chip's own RTL in Verilator); a second guest on the same product sees the same
board live. Warming the sensor to 40 °C makes the chip raise its alarm: the console says ALARM
and the red LED lights, for both. The chip's COUNT register, read through its SPI, is 1. The
chip's waveform is recorded and drawn. On the App page the threshold goes to 42 °C and the tiles
follow. Stopping the simulation leaves its evidence to download."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from common import BASE_URL, finish, launch, log, new_page, new_product, open_product, shot, view, wait_for  # noqa: E402


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = launch(pw)
        a = new_page(browser, errors, "A")
        b = new_page(browser, errors, "B")
        pid = new_product(a, "Lab: thermostat")
        open_product(b, pid, "lab")
        b.wait_for_selector("#section[data-section='lab']")
        view(a, "lab")
        a.click("#start-session")
        t0 = time.time()
        a.wait_for_selector("#leds [data-led]", timeout=180000)
        a.wait_for_selector("[data-uart-line^='READY']", timeout=60000)
        log(f"running after {time.time() - t0:.1f} s")
        b.wait_for_selector("[data-uart-line^='READY']", timeout=20000)
        shot(a, "01_running")

        a.click("[data-sensor-value='temperature'] >> xpath=../.. >> [data-preset='40']")
        a.wait_for_selector("[data-uart-line^='ALARM']", timeout=20000)
        a.wait_for_selector("[data-led='alarm'][data-on='1']", timeout=10000)
        b.wait_for_selector("[data-uart-line^='ALARM']", timeout=10000)
        b.wait_for_selector("[data-led='alarm'][data-on='1']", timeout=10000)
        log("both see the alarm")
        shot(a, "02_alarm_a")
        shot(b, "02_alarm_b")

        a.select_option("#reg-select", "COUNT")
        a.click("#reg-read")
        wait_for(lambda: a.inner_text("#reg-result").startswith("COUNT = 0x00000001"), "COUNT reads 1", 15)

        a.fill("#uart-input", "GET STATUS")
        a.press("#uart-input", "Enter")
        a.wait_for_selector("[data-uart-line^='STATUS temp=4000']", timeout=10000)

        a.click("#record-wave")
        time.sleep(1.5)
        a.click("#show-wave")
        a.wait_for_selector("#waveform svg", timeout=30000)
        shot(a, "03_waveform")

        view(a, "dashboard")
        a.wait_for_selector("#tiles [data-tile='temperature']", timeout=15000)
        a.fill("[data-control-input='set_threshold']", "42")
        a.click("[data-control-apply='set_threshold']")
        wait_for(lambda: "42" in a.inner_text("#tiles [data-tile='threshold']"), "the threshold tile says 42", 15)
        wait_for(lambda: a.locator("#alarm-banner").count() == 0, "40 °C under 42 °C is no alarm", 15)
        shot(a, "04_dashboard")

        view(a, "lab")
        a.click("#stop-session")
        wait_for(lambda: a.locator("[data-download='events.jsonl']").count() > 0, "the evidence to download", 60)
        wait_for(lambda: b.locator("[data-download='events.jsonl']").count() > 0, "the other person gets the evidence too", 20)
        href = a.get_attribute("[data-download='events.jsonl']", "href") or ""
        got = a.request.get(href if href.startswith("http") else BASE_URL + href)
        assert got.ok and b'"kind"' in got.body(), (got.status, href)
        shot(a, "05_stopped")
        browser.close()
    finish(errors)


if __name__ == "__main__":
    main()
