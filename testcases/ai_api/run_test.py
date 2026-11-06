"""ai_api: the AI Hub's tools, the REST API and the Python SDK.

An AI working for a signed-in person (through the AI Hub's tool endpoint) reads the project,
builds the firmware, starts the simulation, warms the sensor and reads the chip's COUNT
register. It can't edit a file: it proposes. The person rejects a first proposal with a reason
(the AI reads it) and accepts a second one (the file is the person's, in the editor). The AI's
release is a package it can't approve. Every call, refusals too, is in the audit log. Then the
SDK, with the person's API token from the page, drives the same simulation: status, threshold,
events, a register, and a refusal."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk"))

from playwright.sync_api import sync_playwright  # noqa: E402

from common import BACKEND_URL, ToolRefused, finish, launch, log, new_page, new_product, shot, tool, view, wait_for  # noqa: E402

OWNER = "owner@example.com"


def refused(name: str, user: str = OWNER, **args) -> str:
    try:
        out = tool(name, user, **args)
    except ToolRefused as e:
        return str(e)
    except urllib.error.HTTPError as e:
        return f"{e.code} {e.read().decode(errors='replace')}"
    raise AssertionError(f"{name} wasn't refused: {out}")


def main() -> None:
    from chipova_forge_sdk import Device, DeviceError  # noqa: PLC0415

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = launch(pw)
        page = new_page(browser, errors, "Owner", email=OWNER)
        pid = new_product(page, "AI: thermostat")
        log("made", pid)

        listed = tool("forge.list_projects", OWNER)
        assert pid in json.dumps(listed), listed
        main_c = tool("forge.read_file", OWNER, room=pid, path="firmware/main.c")
        assert "smart_sensor" in json.dumps(main_c)
        regs = tool("forge.get_registers", OWNER, room=pid)
        assert "COUNT" in json.dumps(regs)
        fw = tool("forge.build", OWNER, room=pid, kind="firmware")
        log("firmware build:", fw.get("status"))
        assert fw.get("status") == "SUCCEEDED", fw
        st = tool("forge.start_simulation", OWNER, room=pid)
        assert st.get("state") == "RUNNING", st
        sim = st["simulation"]
        tool("forge.set_environment", OWNER, room=pid, sensor="temperature", setting="celsius", value=40)
        wait_for(lambda: "ALARM" in json.dumps(tool("forge.read_events", OWNER, room=pid, kinds=["uart"])), "the AI sees ALARM", 15)
        count = tool("forge.read_register", OWNER, room=pid, register="COUNT")
        assert count.get("value") == 1, count
        log("the AI ran the board; COUNT =", count.get("value"))

        # It proposes; a person decides.
        readme = tool("forge.read_file", OWNER, room=pid, path="README.md")
        text = readme["text"]
        first = tool("forge.propose_change", OWNER, room=pid, title="Shorter README", changes=[{"path": "README.md", "content": "# Thermostat\n"}],
                     rationale="It is shorter.")
        view(page, "agents")
        page.wait_for_selector(f"[data-review='{first['proposal']}']")
        page.click(f"[data-review='{first['proposal']}']")
        page.fill("#reject-reason", "Keep the steps in it")
        page.click("#reject-proposal")
        wait_for(lambda: tool("forge.proposal_status", OWNER, room=pid, proposal=first["proposal"])["status"] == "REJECTED", "rejected", 15)
        assert tool("forge.proposal_status", OWNER, room=pid, proposal=first["proposal"])["reason"] == "Keep the steps in it"
        second = tool("forge.propose_change", OWNER, room=pid, title="Add a note", changes=[{"path": "README.md", "content": text + "\nTested by the AI.\n"}])
        page.wait_for_selector(f"[data-review='{second['proposal']}']")
        page.click(f"[data-review='{second['proposal']}']")
        page.click("#accept-proposal")
        wait_for(lambda: tool("forge.proposal_status", OWNER, room=pid, proposal=second["proposal"])["status"] == "ACCEPTED", "accepted", 15)
        assert "Tested by the AI." in json.dumps(tool("forge.read_file", OWNER, room=pid, path="README.md"))
        shot(page, "01_proposals")

        # What it may not do, and for whom.
        log("refused:", refused("forge.read_file", room=pid, path="../../forge.db")[:100])
        log("refused:", refused("forge.get_project", room="0123456789")[:100])
        rel = tool("forge.export_release", OWNER, room=pid, title="AI's release")
        assert rel["status"] in ("DRAFT", "READY"), rel
        def audited(action: str) -> bool:
            page.click("#refresh-audit")
            return page.locator(f"#audit td:has-text('{action}')").count() > 0

        wait_for(lambda: audited("tool.forge.export_release"), "the audit log shows the AI's calls", 15)
        shot(page, "02_audit")

        # The SDK, with the person's token from the page.
        page.click("#make-token")
        page.wait_for_selector("#api-token")
        token = page.inner_text("#api-token").strip()
        dev = Device(sim, url=BACKEND_URL, token=token)
        status = dev.get_status()
        assert status.temperature == 40.0 and status.alarm in (True, False), dict(status)
        seen: list = []

        def listen() -> None:
            for ev in dev.events(after=0):
                seen.append(ev.name)
                if ev.name == "normal":
                    return

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        dev.set_threshold(celsius=45)
        t.join(timeout=15)
        assert "normal" in seen, seen
        assert dev.register("COUNT") == 1
        try:
            dev.set_threshold(celsius=500)
            raise AssertionError("500 °C wasn't refused")
        except DeviceError as e:
            log("SDK refusal:", e)
        tool("forge.stop_simulation", OWNER, room=pid)

        # Members only: someone else's AI gets nothing; the owner's still works.
        view(page, "people")
        page.select_option("#link-access", "none")
        wait_for(lambda: "stranger" not in refused("forge.get_project", user="stranger@example.com", room=pid), "the stranger's AI is refused", 15)
        log("refused:", refused("forge.get_project", user="stranger@example.com", room=pid)[:100])
        assert pid not in json.dumps(tool("forge.list_projects", "stranger@example.com"))
        assert tool("forge.get_project", OWNER, room=pid)
        browser.close()
    finish(errors)


if __name__ == "__main__":
    main()
