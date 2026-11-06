"""smoke_home: each product template makes a working product, and every section opens.

A guest makes the smart temperature controller (the smart sensor chip on an STM32F4): the twelve
steps show; the chip's ports and SPI interface, its seven registers (in sync with its RTL), the
board's diagram and pins, the firmware in the editor, the two tests and the release checks are
there (the PCB not run yet). Then the chip bring-up board with the Heartbeat (a chip with no bus): it sits in Forge's
register harness. The home page lists both."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from common import finish, launch, log, new_page, new_product, open_home, shot, view, wait_for  # noqa: E402


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = launch(pw)
        page = new_page(browser, errors, "Guest")
        open_home(page)
        shot(page, "01_home")
        pid = new_product(page, "Smoke: sensor")
        log("made", pid)
        assert page.locator("#steps [data-step]").count() == 12
        shot(page, "02_overview")

        view(page, "chip")
        page.wait_for_selector("#ports [data-port]")
        ports = {page.locator("#ports [data-port]").nth(i).get_attribute("data-port") for i in range(page.locator("#ports [data-port]").count())}
        assert {"spi_sclk", "spi_mosi", "spi_miso", "spi_cs_n", "irq", "alarm_led"} <= ports, ports
        assert "spi" in page.inner_text("#chip-interface").lower()
        shot(page, "03_chip")

        view(page, "registers")
        wait_for(lambda: page.locator("#register-map [data-register]").count() == 7, "seven registers")
        page.click("#build-registers")
        wait_for(lambda: "is what the systemrdl generates" in page.inner_text("#reg-sync").lower(), "the CSR RTL is what the SystemRDL generates", 60)
        shot(page, "04_registers")

        view(page, "board")
        page.wait_for_selector("#board-diagram svg")
        assert "PA4" in page.inner_text("#pin-use") and "PB1" in page.inner_text("#pin-use")
        wait_for(lambda: page.inner_text("#pcb-line").startswith("Not built yet: 15 parts"), "the PCB panel says what it would lay out")
        shot(page, "05_board")

        view(page, "firmware")
        page.wait_for_selector("#editor .monaco-editor", timeout=90000)
        wait_for(lambda: page.inner_text("#open-path").strip() == "firmware/main.c", "firmware/main.c is open")
        shot(page, "06_firmware")

        view(page, "tests")
        wait_for(lambda: page.locator("#tests [data-test]").count() == 2, "two tests")
        view(page, "release")
        page.wait_for_selector("#checks [data-check='chip'][data-status='PASS']")
        page.wait_for_selector("#checks [data-check='pcb'][data-status='NOT_RUN']")
        shot(page, "07_release")
        for v in ("lab", "dashboard", "agents", "people"):
            view(page, v)
            shot(page, f"08_{v}")
        assert page.locator("#members [data-member]").count() == 1

        bid = new_product(page, "Smoke: bring-up", "chip_bringup", "heartbeat")
        log("made", bid)
        view(page, "chip")
        wait_for(lambda: "none" in page.inner_text("#chip-interface").lower(), "the Heartbeat has no bus")
        view(page, "board")
        page.wait_for_selector("#board-diagram svg")
        assert "register harness" in page.inner_html("#board-diagram")
        shot(page, "09_bringup_board")

        open_home(page)
        page.wait_for_selector(f"[data-project='{pid}']")
        page.wait_for_selector(f"[data-project='{bid}']")
        shot(page, "10_home_lists_both")
        browser.close()
    finish(errors)


if __name__ == "__main__":
    main()
