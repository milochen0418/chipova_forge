"""The builds, a live simulation, the system tests and a release, in real containers (the
chipova-forge/sim image): about a minute. Skipped without Docker or the image."""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile

import pytest

from chipova_forge.contract import chip_package
from chipova_forge.gateway import device
from chipova_forge.jobs import builds, docker_runner
from chipova_forge.services import projects, releases, systemtests
from chipova_forge.services.actors import Actor
from chipova_forge.sessions import service

pytestmark = pytest.mark.docker

REVIEWER = Actor("rev@example.com", "Rev", email="rev@example.com")


@pytest.fixture()
async def queue():
    await builds.start()
    yield
    await service.stop_all()
    await builds.stop()


def _failed(run: dict) -> list:
    return [run.get("error"), *[(s["text"], s["detail"]) for s in run.get("steps", []) if s["status"] != "PASS"]]


async def test_the_smart_sensor_from_its_builds_to_a_release(queue, me):
    pid = projects.create("Sensor", "smart_sensor", chip_package.from_example("smart_sensor"), me)["id"]
    regs = await builds.request(pid, "registers", me, wait=True)
    assert regs["status"] == builds.SUCCEEDED and regs["summary"]["sync"]["status"] == "in_sync"
    chip = await builds.request(pid, "chip", me, wait=True)
    assert chip["status"] == builds.SUCCEEDED, builds.log_text(chip)[-3000:]
    fw = await builds.request(pid, "firmware", me, wait=True)
    assert fw["status"] == builds.SUCCEEDED, builds.log_text(fw)[-3000:]
    assert {"firmware.elf", "firmware.bin"} <= {a["name"] for a in builds.artifacts(fw["id"])}
    again = await builds.request(pid, "firmware", me, wait=True)
    assert again.get("reused") and again["id"] == fw["id"]

    s = await service.start(pid, me)
    sid = s["id"]
    lv = service.live(sid)
    assert lv is not None
    try:
        assert await asyncio.to_thread(service.wait_state, lv, ("RUNNING",), 30), lv.error
        assert await asyncio.to_thread(service.wait_for, lv, 0, lambda e: e.get("device_event") == "ready", 30)
        st = await asyncio.to_thread(service.device_command, sid, me, "get_status")
        assert st["result"]["temperature"] == pytest.approx(25.0, abs=0.1) and st["result"]["alarm"] is False
        seq = lv.last_seq
        await asyncio.to_thread(service.set_environment, sid, me, "temperature", "celsius", 40)
        assert await asyncio.to_thread(service.wait_for, lv, seq, lambda e: e.get("device_event") == "alarm", 10)
        assert (await asyncio.to_thread(service.request, sid, me, "read_reg", offset=0x18))["value"] == 1  # COUNT
        with pytest.raises(device.DeviceError):
            await asyncio.to_thread(service.device_command, sid, me, "set_threshold", {"celsius": 500})
    finally:
        await service.stop(sid, me)
    evidence = {e["name"] for e in (service.get(sid)["summary"] or {}).get("evidence", [])}
    assert "events.jsonl" in evidence

    for path in ("tests/alarm.yaml", "tests/protocol.yaml"):
        run = await systemtests.run(pid, path, me)
        assert run["status"] == "PASSED", _failed(run)
    if docker_runner.image_info(builds.BOARD_IMAGE) is not None:
        assert (await builds.request(pid, "pcb", me, wait=True))["status"] == builds.SUCCEEDED
    cs, _ = releases.checks(pid)
    assert releases.ready(cs), [c for c in cs if c["status"] not in ("PASS", "NOT_CONFIGURED")]
    rel = releases.create(pid, "v1", me)
    assert rel["status"] == "READY"
    names = zipfile.ZipFile(releases.package_path(rel)).namelist()
    assert {"builds/firmware/firmware.elf", "builds/chip/libVchip.so", "tests/alarm/result.json", "RELEASE_MANIFEST.json"} <= set(names)
    if docker_runner.image_info(builds.BOARD_IMAGE) is not None:
        assert {"builds/pcb/gerbers.zip", "builds/pcb/kicad/board.kicad_pcb", "builds/pcb/bom.csv"} <= set(names)
    assert any(n.startswith("tests/alarm/session/") for n in names)
    projects.set_member(pid, REVIEWER.user, "reviewer", me)
    with pytest.raises(releases.ReleaseError, match="someone other"):
        releases.approve(rel["id"], me)
    assert releases.approve(rel["id"], REVIEWER)["status"] == "APPROVED"


async def test_a_chip_without_a_bus_runs_in_the_harness(queue, me):
    pid = projects.create("Bring-up", "chip_bringup", chip_package.from_example("heartbeat"), me)["id"]
    run = await systemtests.run(pid, "tests/bringup.yaml", me)
    assert run["status"] == "PASSED", _failed(run)


async def test_the_module_is_laid_out_routed_and_checked(queue, me):
    if docker_runner.image_info(builds.BOARD_IMAGE) is None:
        pytest.skip(f"the board image {builds.BOARD_IMAGE} isn't installed")
    pid = projects.create("Board", "smart_sensor", chip_package.from_example("smart_sensor"), me)["id"]
    b = await builds.request(pid, "pcb", me, wait=True)
    assert b["status"] == builds.SUCCEEDED, builds.log_text(b)[-3000:]
    ps = b["summary"]["pcb"]
    assert (ps["erc"]["errors"], ps["drc"]["errors"], ps["drc"]["unconnected"], ps["drc"]["parity"]) == (0, 0, 0, 0), ps
    names = {a["name"] for a in builds.artifacts(b["id"])}
    assert {"gerbers.zip", "kicad.zip", "bom.csv", "positions.csv", "board-top.svg", "board-bottom.svg", "schematic.svg",
            "kicad/board.kicad_pcb", "kicad/board.kicad_sch", "kicad/board.kicad_pro"} <= names
    gerbers = zipfile.ZipFile(builds.artifact_path(b, "gerbers.zip")).namelist()
    assert {"board-F_Cu.gtl", "board-B_Cu.gbl", "board-F_Mask.gts", "board-Edge_Cuts.gm1", "board-job.gbrjob"} <= set(gerbers)
    assert any(n.endswith(".drl") for n in gerbers)
    bom = list(csv.DictReader(io.StringIO(builds.artifact_path(b, "bom.csv").read_text())))
    assert any(r["MPN"] == "TMP1075DR" for r in bom) and any(r["Value"] == "smart_sensor" for r in bom)
    again = await builds.request(pid, "pcb", me, wait=True)
    assert again.get("reused") and again["id"] == b["id"]
    bring = projects.create("Bring-up board", "chip_bringup", chip_package.from_example("heartbeat"), me)["id"]
    hb = await builds.request(bring, "pcb", me, wait=True)
    assert hb["status"] == builds.SUCCEEDED and hb["summary"]["pcb"]["drc"]["errors"] == 0


async def test_the_fan_closes_the_loop_through_the_analog_side(queue, me):
    pid = projects.create("Fan", "fan_controller", chip_package.from_example("smart_sensor"), me)["id"]
    run = await systemtests.run(pid, "tests/fan_control.yaml", me)
    assert run["status"] == "PASSED", _failed(run)
    s = service.get(run["session_id"])
    evidence = {e["name"] for e in (s["summary"] or {}).get("evidence", [])}
    assert "events.jsonl" in evidence
