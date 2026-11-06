"""release_review: the board, tests, a release, and someone else approving it.

Two signed-in people. The owner makes the product and lays out its board from the Board page:
the schematic and the PCB come back routed and clean (KiCad's ERC and DRC, the board matching the
schematic), with pictures and Gerbers. Then both system tests from the Tests page (each in a fresh
simulation); they pass, step by step. The release checks then pass and the owner
makes a READY release, which they can't approve themselves. On the People page the owner makes
the second person a reviewer and closes the link to members only: an outsider is refused, on the
page and on the REST API. The reviewer approves the release, and its package downloads with its
manifest and the tests' evidence."""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from common import BASE_URL, api, finish, launch, log, new_page, new_product, open_product, shot, view, wait_for, wait_status  # noqa: E402

OWNER = "owner@example.com"
REVIEWER = "reviewer@example.com"
OUTSIDER = "outsider@example.com"


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = launch(pw)
        owner = new_page(browser, errors, "Owner", email=OWNER)
        pid = new_product(owner, "Release: thermostat")
        log("made", pid)

        view(owner, "board")
        owner.wait_for_selector("#build-pcb:not([disabled])", timeout=30000)
        owner.click("#build-pcb")
        t0 = time.time()
        wait_for(lambda: "DRC 0 errors" in owner.inner_text("#pcb-line"), "the PCB is laid out and checked", 240)
        line = owner.inner_text("#pcb-line")
        log(f"PCB after {time.time() - t0:.1f} s:", line)
        assert "ERC 0 errors" in line and "0 unconnected" in line and "0 differences from the schematic" in line, line
        owner.wait_for_selector("#pcb-picture")
        wait_for(lambda: owner.evaluate("() => { const i = document.getElementById('pcb-picture'); return i && i.complete && i.naturalWidth > 0; }"),
                 "the board's picture loads", 20)
        shot(owner, "00_board_top")
        owner.click("[data-pcb-view='schematic']")
        wait_for(lambda: owner.evaluate("() => { const i = document.getElementById('pcb-picture'); return i && i.complete && i.naturalWidth > 0 && i.src.includes('schematic'); }"),
                 "the schematic's picture loads", 20)
        shot(owner, "00_schematic")
        assert owner.locator("#pcb-downloads [data-download='gerbers.zip']").count() == 1

        view(owner, "tests")
        for test in ("tests/alarm.yaml", "tests/protocol.yaml"):
            wait_for(lambda: owner.locator(f"[data-run-test='{test}']").is_enabled(), "Run is enabled", 60)
            owner.click(f"[data-run-test='{test}']")
            t0 = time.time()
            st = wait_status(owner, f"#tests [data-test='{test}']", ("PASSED", "FAILED"), 240)
            log(test, st, f"{time.time() - t0:.1f} s")
            assert st == "PASSED", owner.inner_text("#test-steps")
            assert owner.locator("#test-steps [data-step-result][data-status='PASS']").count() >= 5
        shot(owner, "01_tests_passed")

        view(owner, "release")
        try:
            wait_for(lambda: owner.locator("#checks [data-check]:not([data-status='PASS']):not([data-status='NOT_CONFIGURED'])").count() == 0
                     and owner.locator("#checks [data-check]").count() >= 8, "the checks pass", 60)
        except AssertionError:
            raise AssertionError("checks: " + owner.inner_text("#checks")) from None
        owner.click("#make-release")
        wait_status(owner, "#releases [data-release]", ("READY", "DRAFT"), 120)
        assert owner.get_attribute("#releases [data-release]", "data-status") == "READY", owner.inner_text("#checks")
        rid = owner.get_attribute("#releases [data-release]", "data-release")
        assert owner.locator(f"[data-approve='{rid}']").count() == 0, "the maker of a release can't approve it"
        shot(owner, "02_release_ready")

        view(owner, "people")
        owner.fill("#member-email", REVIEWER)
        owner.select_option("#member-role", "reviewer")
        owner.click("#add-member")
        owner.wait_for_selector(f"#members [data-member='{REVIEWER}']")
        owner.select_option("#link-access", "none")
        time.sleep(1)
        shot(owner, "03_people")

        outsider = new_page(browser, errors, "Outsider", email=OUTSIDER)
        outsider.goto(f"{BASE_URL}/p/{pid}", wait_until="domcontentloaded")
        outsider.wait_for_selector("text=You may not see this product", timeout=60000)
        from chipova_forge.gateway.api import make_token  # noqa: PLC0415 - the server's own signing
        from chipova_forge.services.actors import Actor  # noqa: PLC0415

        code, body = api("GET", f"/api/projects/{pid}", make_token(Actor(OUTSIDER, "Outsider", email=OUTSIDER)))
        assert code == 403, (code, body)
        shot(outsider, "04_outsider_refused")

        view(owner, "release")
        reviewer = new_page(browser, errors, "Reviewer", email=REVIEWER)
        open_product(reviewer, pid, "release")
        reviewer.wait_for_selector(f"[data-approve='{rid}']", timeout=30000)
        reviewer.click(f"[data-approve='{rid}']")
        wait_status(reviewer, f"#releases [data-release='{rid}']", ("APPROVED",), 30)
        wait_status(owner, f"#releases [data-release='{rid}']", ("APPROVED",), 30)
        shot(reviewer, "05_approved")

        href = reviewer.get_attribute(f"[data-package='{rid}']", "href") or ""
        got = reviewer.request.get(href if href.startswith("http") else BASE_URL + href)
        assert got.ok, (got.status, href)
        z = zipfile.ZipFile(io.BytesIO(got.body()))
        manifest = json.loads(z.read("RELEASE_MANIFEST.json"))
        assert manifest["release"]["id"] == rid and "tests/alarm/result.json" in z.namelist()
        assert "builds/firmware/firmware.elf" in z.namelist() and "builds/pcb/gerbers.zip" in z.namelist()
        log("package:", len(z.namelist()), "files")
        browser.close()
    finish(errors)


if __name__ == "__main__":
    main()
