"""Releases: one package with everything the product is, and the evidence that it works in the
simulation (blueprint 12, 15).

A release is made from the project as it is now. Its checks are each computed from what Forge
has, never assumed:

    chip        the chip's files still hash to the Chipova snapshot they were imported as
    contract    hardware.yaml has no errors
    board       system.yaml has no errors
    registers   the chip's CSR RTL is what its SystemRDL generates (or Forge's harness)
    builds      the chip library and the firmware built from the current files
    tests       every tests/*.yaml passed, last run on those same builds
    pcb         the board's ERC/DRC (not in this version: NOT_CONFIGURED)

The package (a zip) holds the chip's snapshot, the project's files, the builds' outputs and
manifests, the register header and Python map, each test's last run with its session's events
and the chip's waveform, the checks, and RELEASE_MANIFEST.json with every file's SHA-256. A
release whose checks all pass is READY; a person who isn't the one who made it (never an AI)
approves it. Passing these checks says the digital design works in simulation; it is not a
foundry signoff, nor a qualification of real hardware (README.txt in the package says what is
not covered).
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from chipova_forge import ids, paths
from chipova_forge.contract import chip_package, contract as hdc
from chipova_forge.db import db, loads
from chipova_forge.domain import roles
from chipova_forge.jobs import builds
from chipova_forge.services import audit, projects, systemtests
from chipova_forge.services.actors import Actor
from chipova_forge.system import board as board_mod
from chipova_forge.system import catalog

MAX_EVIDENCE_BYTES = 40 << 20

DISCLAIMER = """\
Chipova Forge release package.

What it shows: the chip's RTL (the Chipova snapshot named in RELEASE_MANIFEST.json), the firmware
and the board description work together in simulation: Renode models the MCU and its
peripherals, Verilator runs the chip's own RTL (at a slower clock than the real one), the
sensors are behavioural models, and the system tests listed passed on exactly the builds in
this package.

What it does not show, and must be checked elsewhere before anything is manufactured:
- the chip's foundry signoff (physical verification, timing at corners, yield, packaging, ESD);
- the PCB's signal and power integrity, EMI/EMC, thermal behaviour and safety approvals;
- real sensors' non-linearity, noise, drift and part-to-part variation;
- precise timing and real-time performance (each simulator keeps its own model of time);
- anything the models listed under "model_limits" don't cover.
"""


listeners: list = []  # (project id, release id): a release was made or approved


def _notify(project_id: str, release_id: str) -> None:
    for fn in list(listeners):
        try:
            fn(project_id, release_id)
        except Exception:  # noqa: BLE001
            pass


class ReleaseError(RuntimeError):
    pass


def get(release_id: str) -> dict[str, Any] | None:
    if not ids.is_prefixed(release_id, "rel"):
        return None
    row = db().one("SELECT * FROM releases WHERE id = ?", (release_id,))
    return {**dict(row), "checks": loads(row["checks"], [])} if row else None


def of_project(project_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db().query("SELECT * FROM releases WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))
    return [{**dict(r), "checks": loads(r["checks"], [])} for r in rows]


def package_path(r: dict[str, Any]) -> Path:
    return paths.release_dir(r["project_id"], r["id"]) / "package.zip"


def _check(name: str, title: str, status: str, detail: str, evidence: str = "") -> dict[str, Any]:
    return {"name": name, "title": title, "status": status, "detail": detail, "evidence": evidence}


def checks(project_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The release checks of the project as it is now, and what they were computed from."""
    out: list[dict[str, Any]] = []
    used: dict[str, Any] = {}
    try:
        chip = projects.chip(project_id)
        used["snapshot"] = chip.snapshot
        out.append(_check("chip", "Chip snapshot", "PASS", f"{len(chip.files)} files hash to {chip.snapshot[:19]}…", chip.source.get("ref", "")))
    except chip_package.ChipImportError as e:
        out.append(_check("chip", "Chip snapshot", "FAIL", str(e)))
        return out, used
    try:
        c = projects.contract(project_id)
        errs = [i for i in hdc.check(c) if i.level == "error"]
        out.append(_check("contract", "Hardware contract", "FAIL" if errs else "PASS",
                          "; ".join(i.message for i in errs) or f"{c.interface.protocol} interface, {len(c.pins)} pins"))
    except Exception as e:  # noqa: BLE001
        out.append(_check("contract", "Hardware contract", "FAIL", str(e)))
        return out, used
    try:
        b = board_mod.from_yaml(projects.read(project_id, "system.yaml"))
        errs = [i for i in board_mod.check(b, c) if i.level == "error"]
        out.append(_check("board", "Board description", "FAIL" if errs else "PASS",
                          "; ".join(f"{i.where}: {i.message}" for i in errs) or f"{catalog.platform(b.platform)['title']}, {len(b.sensors)} sensors"))
    except Exception as e:  # noqa: BLE001
        out.append(_check("board", "Board description", "FAIL", str(e)))
    current: dict[str, str] = {}
    pcb_reason = ""
    for kind in builds.KINDS:
        try:
            p = builds.plan(project_id, kind)
            image_id = "" if p.in_process else builds._image_id(p.image)  # noqa: SLF001
            current[kind] = builds.inputs_sha(p, image_id)
        except builds.BuildError as e:
            current[kind] = ""
            if kind == "pcb":
                pcb_reason = str(e)  # a board Forge doesn't lay out (yet): said below
            else:
                out.append(_check(f"build_{kind}", f"Build: {kind}", "FAIL", str(e)))
    regs = None
    if current.get("registers"):
        regs = db().one("SELECT id, summary FROM builds WHERE project_id = ? AND kind = 'registers' AND inputs_sha = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
                        (project_id, current["registers"], builds.SUCCEEDED))
        if regs is None:
            out.append(_check("registers", "Registers", "NOT_RUN", "build the registers of the current files"))
        else:
            sync = (loads(regs["summary"], {}).get("sync") or {})
            ok = sync.get("status") in ("in_sync", "harness")
            out.append(_check("registers", "Registers", "PASS" if ok else "FAIL", sync.get("detail", ""), regs["id"]))
            used["registers_build"] = regs["id"]
    for kind in ("chip", "firmware"):
        if not current.get(kind):
            continue
        row = db().one("SELECT id FROM builds WHERE project_id = ? AND kind = ? AND inputs_sha = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
                       (project_id, kind, current[kind], builds.SUCCEEDED))
        if row is None:
            out.append(_check(f"build_{kind}", f"Build: {kind}", "NOT_RUN", f"build the {kind} from the current files"))
        else:
            out.append(_check(f"build_{kind}", f"Build: {kind}", "PASS", "built from the current files", row["id"]))
            used[f"{kind}_build"] = row["id"]
    tests = projects.test_files(project_id)
    latest = systemtests.latest_by_scenario(project_id)
    if not tests:
        out.append(_check("tests", "System tests", "NOT_CONFIGURED", "the project has no tests/*.yaml"))
    for path, text in tests.items():
        r = latest.get(path)
        sha = hashlib.sha256(text.encode()).hexdigest()
        name = f"test:{path}"
        if r is None:
            out.append(_check(name, f"Test {path}", "NOT_RUN", "never run"))
            continue
        inp = r.get("inputs") or {}
        same = inp.get("test_sha256") == sha and inp.get("chip_build") == used.get("chip_build") and inp.get("firmware_build") == used.get("firmware_build")
        if r["status"] != "PASSED":
            out.append(_check(name, f"Test {path}", "FAIL", r.get("error") or "a step failed", r["id"]))
        elif not same:
            out.append(_check(name, f"Test {path}", "NOT_RUN", "it passed on other files or builds: run it again", r["id"]))
        else:
            out.append(_check(name, f"Test {path}", "PASS", f"{len(r['steps'])} steps", r["id"]))
            used.setdefault("tests", []).append(r["id"])
    title = "Schematic & PCB (KiCad ERC, DRC, parity)"
    if pcb_reason:
        out.append(_check("pcb", title, "NOT_CONFIGURED", pcb_reason))
    elif current.get("pcb"):
        row = db().one("SELECT id, summary FROM builds WHERE project_id = ? AND kind = 'pcb' AND inputs_sha = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
                       (project_id, current["pcb"], builds.SUCCEEDED))
        if row is None:
            out.append(_check("pcb", title, "NOT_RUN", "build the schematic and PCB from the current files (Board page)"))
        else:
            s = (loads(row["summary"], {}).get("pcb") or {})
            erc, drc = s.get("erc") or {}, s.get("drc") or {}
            bad = {"ERC errors": erc.get("errors", 1), "DRC errors": drc.get("errors", 1), "unconnected": drc.get("unconnected", 1),
                   "differences from the schematic": drc.get("parity", 1)}
            if any(bad.values()):
                out.append(_check("pcb", title, "FAIL", ", ".join(f"{v} {k}" for k, v in bad.items() if v), row["id"]))
            else:
                warn = erc.get("warnings", 0) + drc.get("warnings", 0)
                out.append(_check("pcb", title, "PASS", f"ERC and DRC clean, the board matches the schematic ({warn} warnings)", row["id"]))
                used["pcb_build"] = row["id"]
    return out, used


def ready(cs: list[dict[str, Any]]) -> bool:
    return all(c["status"] in ("PASS", "NOT_CONFIGURED") for c in cs) and any(c["name"].startswith("test:") and c["status"] == "PASS" for c in cs)


def create(project_id: str, title: str, actor: Actor) -> dict[str, Any]:
    projects.require(project_id, actor, roles.REQUEST_RELEASE)
    cs, used = checks(project_id)
    rid = ids.new_id("rel")
    status = "READY" if ready(cs) else "DRAFT"
    now = time.time()
    db().insert("releases", {"id": rid, "project_id": project_id, "title": (title or "").strip()[:120] or f"Release {time.strftime('%Y-%m-%d %H:%M')}",
                             "status": status, "created_by": actor.user, "created_by_name": actor.name, "created_at": now, "checks": cs})
    r = get(rid)
    assert r is not None
    sha, size, manifest_sha = _package(r, cs, used, actor)
    db().update("releases", {"id": rid}, {"package": sha, "size": size, "manifest_sha": manifest_sha})
    audit.record("release.create", actor, project_id, rid, {"status": status, "sha256": sha})
    _notify(project_id, rid)
    return get(rid)  # type: ignore[return-value]


def approve(release_id: str, actor: Actor) -> dict[str, Any]:
    r = get(release_id)
    if r is None:
        raise projects.NotFound("no such release")
    projects.require(r["project_id"], actor, roles.APPROVE_RELEASE, release_id)
    if r["status"] != "READY":
        raise ReleaseError("only a READY release (every check passed) can be approved")
    if r["created_by"] == actor.user:
        raise ReleaseError("someone other than the person who made it approves a release")
    db().update("releases", {"id": release_id}, {"status": "APPROVED"})
    audit.record("release.approve", actor, r["project_id"], release_id)
    _notify(r["project_id"], release_id)
    return get(release_id)  # type: ignore[return-value]


def _package(r: dict[str, Any], cs: list[dict[str, Any]], used: dict[str, Any], actor: Actor) -> tuple[str, int, str]:
    pid = r["project_id"]
    files: dict[str, bytes] = {}
    work = paths.work_dir(pid)
    for f in sorted(work.rglob("*")):
        rel = f.relative_to(work).as_posix()
        if f.is_file() and not any(part.startswith(".") for part in rel.split("/")):
            files[f"project/{rel}"] = f.read_bytes()
    for key in ("registers_build", "chip_build", "firmware_build", "pcb_build"):
        bid = used.get(key)
        b = builds.get(bid) if bid else None
        if b is None:
            continue
        bdir = paths.build_dir(pid, bid)
        for name in ("manifest.json", "build.log"):
            if (bdir / name).is_file():
                files[f"builds/{b['kind']}/{name}"] = (bdir / name).read_bytes()
        for a in builds.artifacts(bid):
            f = builds.artifact_path(b, a["name"])
            if f.is_file():
                files[f"builds/{b['kind']}/{a['name']}"] = f.read_bytes()
    budget = MAX_EVIDENCE_BYTES
    for tid in used.get("tests", []):
        t = systemtests.get(tid)
        if t is None:
            continue
        base = f"tests/{t['scenario'].removeprefix('tests/').removesuffix('.yaml')}"
        files[f"{base}/result.json"] = json.dumps(t, indent=1, default=str).encode()
        sdir = paths.session_dir(pid, t["session_id"]) if t.get("session_id") else None
        if sdir and sdir.is_dir():
            for f in sorted(sdir.iterdir()):
                if f.is_file() and f.stat().st_size <= budget:
                    budget -= f.stat().st_size
                    files[f"{base}/session/{f.name}"] = f.read_bytes()
    limits = {k: v.get("model_limits", "") for k, v in catalog.components().items()}
    manifest = {
        "schema": "chipova-forge.release/1",
        "release": {"id": r["id"], "title": r["title"], "status": r["status"], "created_by": r["created_by"], "created_at": r["created_at"]},
        "project": {k: v for k, v in projects.must(pid).items() if k in ("id", "title", "template", "chip")},
        "checks": cs,
        "used": used,
        "model_limits": limits,
        "files": [{"path": p, "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)} for p, d in sorted(files.items())],
        "built_at": time.time(),
        "built_by": actor.user,
    }
    body = json.dumps(manifest, indent=1, default=str).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p, d in sorted(files.items()):
            z.writestr(p, d)
        z.writestr("RELEASE_MANIFEST.json", body)
        z.writestr("README.txt", DISCLAIMER)
    data = buf.getvalue()
    out = package_path(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data), hashlib.sha256(body).hexdigest()
