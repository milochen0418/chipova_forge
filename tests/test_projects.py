"""Projects, their files, who may do what, AI proposals, API tokens, signed downloads and the
release checks: Forge's bookkeeping, without containers."""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest

from chipova_forge.contract import chip_package
from chipova_forge.domain import roles
from chipova_forge.domain.roles import LinkAccess, Role
from chipova_forge.gateway import api
from chipova_forge.jobs import builds
from chipova_forge.services import actors, audit, downloads, projects, proposals, releases, systemtests
from chipova_forge.services.actors import Actor

OTHER = Actor("other@example.com", "Other", email="other@example.com")


@pytest.fixture()
def sensor(me):
    return projects.create("My sensor", "smart_sensor", chip_package.from_example("smart_sensor"), me)


def test_a_new_product(sensor, me):
    pid = sensor["id"]
    names = {f["path"] for f in projects.files(pid)}
    assert {"hardware.yaml", "system.yaml", "device.yaml", "firmware/main.c", "tests/alarm.yaml", "tests/protocol.yaml"} <= names
    assert projects.contract(pid).interface.protocol == "spi"
    assert projects.chip(pid).snapshot == chip_package.from_example("smart_sensor").snapshot
    assert sensor["chip"]["interface"] == "spi" and projects.member_role(pid, me.user) == Role.OWNER
    assert [e["action"] for e in audit.entries(pid)] == ["project.create"]


def test_a_template_can_slow_a_chip_down(me):
    p = projects.create("", "chip_bringup", chip_package.from_example("heartbeat"), me)
    assert projects.contract(p["id"]).simulation.frequency_hz == 1000
    assert p["title"] == projects.product_templates()["chip_bringup"]["title"]


def test_saving_files(sensor, me):
    pid = sensor["id"]
    v = projects.file_info(pid, "firmware/main.c")["version"]
    assert projects.write(pid, "firmware/main.c", "int main(void) { return 0; }\n", me, base_version=v) == v + 1
    with pytest.raises(projects.Conflict, match="meanwhile"):
        projects.write(pid, "firmware/main.c", "// stale\n", OTHER, base_version=v)
    for path in ("chip/rtl/smart_sensor_top.sv", "build/firmware.elf"):
        with pytest.raises(projects.Denied):
            projects.write(pid, path, "x", me)
    for bad in ("firmware/a b.c", "firmware/x.py"):
        with pytest.raises(ValueError):
            projects.write(pid, bad, "x", me)
    with pytest.raises(ValueError):
        projects.write(pid, "README.md", "x" * (projects.MAX_FILE_BYTES + 1), me)
    with pytest.raises(ValueError):
        projects.read(pid, "../../forge.db")
    projects.write(pid, "firmware/extra.c", "void extra(void) {}\n", me)
    assert "firmware/extra.c" in projects.firmware_sources(pid)
    projects.delete_file(pid, "firmware/extra.c", me)
    assert "firmware/extra.c" not in projects.firmware_sources(pid)
    with pytest.raises(projects.Denied):
        projects.delete_file(pid, "system.yaml", me)


def test_who_may_do_what(sensor, me):
    pid = sensor["id"]
    guest = actors.guest("a-secret-browser-key")
    assert guest.user.startswith("guest-") and "a-secret" not in guest.user
    # Anyone with the link edits (the default) ...
    assert projects.require(pid, guest, roles.EDIT)[1].label == "Editor"
    # ... until the owner makes the link view-only.
    from chipova_forge.db import db

    db().execute("UPDATE projects SET link_access = ? WHERE id = ?", (LinkAccess.VIEWER.value, pid))
    with pytest.raises(projects.Denied, match="Viewer may not edit its files"):
        projects.write(pid, "README.md", "hi", guest)
    db().execute("UPDATE projects SET link_access = ? WHERE id = ?", (LinkAccess.NONE.value, pid))
    with pytest.raises(projects.Denied):
        projects.require(pid, guest, roles.VIEW)
    assert pid not in [p["id"] for p in projects.visible_to(guest.user)]
    assert pid in [p["id"] for p in projects.visible_to(me.user)]
    # The AI works with its person's access, minus what agents never do.
    ai = actors.agent("AI", on_behalf_of=me.user)
    _, a = projects.require(pid, ai, roles.BUILD)
    for action in roles.NEVER_FOR_AGENTS:
        assert not a.can(action)
    with pytest.raises(projects.Denied, match="An AI agent may not edit"):
        projects.write(pid, "README.md", "hi", ai)
    with pytest.raises(projects.Denied):
        projects.require(pid, actors.agent("AI", on_behalf_of="nobody@example.com"), roles.VIEW)
    with pytest.raises(projects.Denied):
        projects.create("x", "smart_sensor", chip_package.from_example("smart_sensor"), ai)
    assert any(e["decision"] == "denied" and e["action"] == roles.EDIT for e in audit.entries(pid))


def test_ai_proposals(sensor, me):
    pid = sensor["id"]
    ai = actors.agent("AI", on_behalf_of=me.user)
    old = projects.read(pid, "README.md")
    p = proposals.propose(pid, ai, "Say hello", [{"path": "README.md", "content": old + "\nHello\n"}], "a friendlier README")
    assert p["status"] == "PROPOSED" and p["files"][0]["old"] == old
    with pytest.raises(projects.Denied):
        proposals.decide(p["id"], ai, True)
    done = proposals.decide(p["id"], me, True)
    assert done["status"] == "ACCEPTED" and projects.read(pid, "README.md").endswith("Hello\n")
    assert projects.file_info(pid, "README.md")["updated_by"] == me.user
    with pytest.raises(projects.Conflict, match="already accepted"):
        proposals.decide(p["id"], me, False)
    # A proposal never overwrites newer work.
    q = proposals.propose(pid, ai, "Again", [{"path": "README.md", "content": "replaced"}])
    projects.write(pid, "README.md", "a person's newer text", me)
    with pytest.raises(projects.Conflict, match="changed since"):
        proposals.decide(q["id"], me, True)
    assert proposals.decide(q["id"], me, False, "no thanks")["reason"] == "no thanks"
    for bad in ([], [{"path": "chip/rtl/x.sv", "content": ""}], [{"path": "README.md"}]):
        with pytest.raises(ValueError):
            proposals.propose(pid, ai, "bad", bad)


def test_api_tokens(me):
    t = api.make_token(me)
    assert t.startswith("cfg1.") and api.read_token(t) == me
    head, body, sig = t.split(".")
    assert api.read_token(f"{head}.{body}.{sig[:-2]}AA") is None
    assert api.read_token(api.make_token(me, days=-1)) is None
    ai = api.read_token(api.make_token(actors.agent("Bot", on_behalf_of=me.user)))
    assert ai is not None and ai.is_agent and ai.on_behalf_of == me.user
    assert api.read_token("nonsense") is None


def test_download_links_are_signed(sensor, me):
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route

    r = releases.create(sensor["id"], "draft", me)
    client = TestClient(Starlette(routes=[Route(downloads.ROUTE, downloads.serve)]))
    url = downloads.link(sensor["id"], "release", r["id"], "package.zip", me)
    got = client.get(url)
    assert got.status_code == 200 and zipfile.ZipFile(io.BytesIO(got.content)).read("README.txt")
    assert client.get(url.replace("u=owner", "u=other")).status_code == 403
    assert client.get(url.replace(r["id"], "rel-0000000000")).status_code == 403


def test_release_checks_and_packages(sensor, me):
    pid = sensor["id"]
    cs, used = releases.checks(pid)
    status = {c["name"]: c["status"] for c in cs}
    assert status["chip"] == status["contract"] == status["board"] == "PASS"
    assert status["registers"] == "NOT_RUN" and status["test:tests/alarm.yaml"] == "NOT_RUN"
    assert status["pcb"] == "NOT_RUN" and not releases.ready(cs)
    r = releases.create(pid, "", me)
    assert r["status"] == "DRAFT" and r["size"] > 0
    z = zipfile.ZipFile(releases.package_path(r))
    m = json.loads(z.read("RELEASE_MANIFEST.json"))
    assert "project/firmware/main.c" in z.namelist() and m["used"]["snapshot"] == used["snapshot"]
    with pytest.raises(projects.Denied, match="Editor may not approve"):
        releases.approve(r["id"], OTHER)
    with pytest.raises(releases.ReleaseError, match="READY"):
        releases.approve(r["id"], me)


def test_system_test_files_are_checked(sensor):
    for text in projects.test_files(sensor["id"]).values():
        assert systemtests.load(text)["steps"]
    for bad in ("steps: []", "name: x\nsteps:\n  - wait: 1\n    expect: {a: 1}", "steps:\n  - fly: 1", ": :"):
        with pytest.raises(systemtests.TestError):
            systemtests.load(bad)
    assert systemtests._matches({"min": 30, "max": 40}, 35.5) and not systemtests._matches({"min": 30}, True)  # noqa: SLF001
    assert systemtests._matches(True, True) and not systemtests._matches(True, 1)  # noqa: SLF001


def test_build_plans(sensor, me):
    pid = sensor["id"]
    regs = builds.plan(pid, "registers")
    assert regs.in_process
    chip = builds.plan(pid, "chip")
    assert "sim_main.cpp" in chip.files and "CMakeLists.txt" in chip.files and "rtl/smart_sensor_top.sv" in chip.files
    fw = builds.plan(pid, "firmware")
    assert {"firmware/main.c", "board.h", "smart_sensor_regs.h", "forge_chip_spi.c", "startup.c"} <= set(fw.files)
    before = builds.inputs_sha(fw, "img")
    projects.write(pid, "firmware/main.c", projects.read(pid, "firmware/main.c") + "\n", me)
    assert builds.inputs_sha(builds.plan(pid, "firmware"), "img") != before
    assert builds.inputs_sha(builds.plan(pid, "chip"), "img") == builds.inputs_sha(chip, "img")
    with pytest.raises(builds.BuildError):
        builds.plan(pid, "pcb-nope")
    log = "/work/src/firmware/main.c:12:5: error: 'x' undeclared\n%Warning-WIDTH: rtl/top.sv:3:1: hmm\nfine\n"
    assert builds.problems(log)[0] == {"file": "firmware/main.c", "line": 12, "severity": "error", "message": "'x' undeclared"}


def test_members_and_the_link(sensor, me):
    pid = sensor["id"]
    projects.set_member(pid, "Reviewer@Example.com", "reviewer", me)
    assert projects.member_role(pid, "reviewer@example.com") == Role.REVIEWER
    rev = Actor("reviewer@example.com", "Rev", email="reviewer@example.com")
    _, a = projects.require(pid, rev, roles.APPROVE_RELEASE)
    assert a.label == "Editor + Reviewer"
    with pytest.raises(projects.Denied):
        projects.set_member(pid, "x@example.com", "owner", rev)
    with pytest.raises(ValueError, match="at least one owner"):
        projects.set_member(pid, me.user, "editor", me)
    with pytest.raises(ValueError):
        projects.set_member(pid, "not-an-email", "viewer", me)
    with pytest.raises(ValueError):
        projects.set_member(pid, "x@example.com", "boss", me)
    projects.set_link_access(pid, "none", me)
    _, a = projects.require(pid, rev, roles.VIEW)
    assert a.label == "Reviewer" and not a.can(roles.EDIT)
    with pytest.raises(projects.Denied):
        projects.require(pid, OTHER, roles.VIEW)
    projects.set_member(pid, "reviewer@example.com", "", me)
    with pytest.raises(projects.Denied):
        projects.require(pid, rev, roles.VIEW)
    with pytest.raises(ValueError):
        projects.set_link_access(pid, "everyone", me)
    assert [m["user"] for m in projects.members(pid)] == [me.user]
