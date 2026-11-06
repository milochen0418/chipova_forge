"""Projects: their records, their files, and who may do what in them.

A Forge project is one product: the chip it is built around (imported, read-only, in
``work/chip``), its contract (``hardware.yaml``), its board (``system.yaml``), its device
protocol (``device.yaml``), its firmware (``firmware/``), its system tests (``tests/``).
Files are plain files in the project's work folder, each with a version number in the database:
a save names the version it was based on, and a save over someone else's newer save is refused
(Conflict) instead of silently overwriting it.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any

import yaml

from chipova_forge import ids, paths
from chipova_forge.contract import chip_package, contract as hdc
from chipova_forge.contract.chip_package import ChipPackage
from chipova_forge.db import db, loads
from chipova_forge.domain import roles
from chipova_forge.domain.roles import LinkAccess, Role
from chipova_forge.services import audit
from chipova_forge.services.actors import Actor

MAX_FILE_BYTES = 512 * 1024
# The files a person edits (the chip and generated outputs aren't among them).
EDITABLE = ("hardware.yaml", "system.yaml", "device.yaml", "firmware/", "tests/", "README.md")


class Denied(PermissionError):
    """The person (or AI) may not do this; the message says why, for them to read."""


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    """Someone else changed it meanwhile."""


# ------------------------------------------------------------------ records


def get(project_id: str) -> dict[str, Any] | None:
    if not ids.is_project(project_id):
        return None
    row = db().one("SELECT * FROM projects WHERE id = ? AND deleted = 0", (project_id,))
    if row is None:
        return None
    return {**dict(row), "chip": loads(row["chip"], {})}


def must(project_id: str) -> dict[str, Any]:
    p = get(project_id)
    if p is None:
        raise NotFound(f"there is no project {project_id!r}")
    return p


def member_role(project_id: str, user: str) -> Role | None:
    row = db().one("SELECT role FROM memberships WHERE project_id = ? AND user = ?", (project_id, user))
    return Role(row["role"]) if row else None


def access_of(project: dict[str, Any], actor: Actor) -> roles.Access:
    member = member_role(project["id"], actor.user) if actor.user else None
    if member is None and actor.is_agent and actor.on_behalf_of:
        member = member_role(project["id"], actor.on_behalf_of)
    return roles.access(member, LinkAccess(project["link_access"]), agent=actor.is_agent)


def require(project_id: str, actor: Actor, action: str, target: str = "") -> tuple[dict[str, Any], roles.Access]:
    p = get(project_id)
    if p is None:
        raise NotFound(f"there is no project {project_id!r}")
    a = access_of(p, actor)
    if not a.can(action):
        audit.denied(action, actor, project_id, target, f"{a.label} may not {action}")
        if not a.roles:
            raise Denied("the person this AI works for has no access to this project" if actor.is_agent else "you have no access to this project")
        who = "An AI agent" if actor.is_agent and action in roles.NEVER_FOR_AGENTS else a.label
        raise Denied(f"{who} may not {roles.WORDS.get(action, action)} in this project")
    return p, a


def visible_to(user: str) -> list[dict[str, Any]]:
    rows = db().query(
        "SELECT p.* FROM projects p WHERE p.deleted = 0 AND (p.link_access != 'none' OR EXISTS ("
        "SELECT 1 FROM memberships m WHERE m.project_id = p.id AND m.user = ?)) ORDER BY p.updated_at DESC",
        (user,),
    )
    return [{**dict(r), "chip": loads(r["chip"], {})} for r in rows]


# ------------------------------------------------------------------ people


_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


def members(project_id: str) -> list[dict[str, Any]]:
    rows = db().query("SELECT user, role, added_by, added_at FROM memberships WHERE project_id = ? ORDER BY added_at", (project_id,))
    return [dict(r) for r in rows]


def set_link_access(project_id: str, link: str, actor: Actor) -> None:
    """Who the project's link lets in: nobody but its members, viewers, or editors."""
    require(project_id, actor, roles.MANAGE)
    try:
        la = LinkAccess(link)
    except ValueError:
        raise ValueError("the link lets in: none, viewer or editor") from None
    db().execute("UPDATE projects SET link_access = ?, updated_at = ? WHERE id = ?", (la.value, time.time(), project_id))
    audit.record("project.link", actor, project_id, project_id, {"link_access": la.value})


def set_member(project_id: str, user: str, role: str, actor: Actor) -> None:
    """Gives a person (by e-mail) a role in the project, or takes it away (role ``""``). A
    project keeps at least one owner."""
    require(project_id, actor, roles.MANAGE)
    user = (user or "").strip().lower()
    if not _EMAIL.match(user):
        raise ValueError("a member is someone's e-mail address")
    if role:
        try:
            Role(role)
        except ValueError:
            raise ValueError("a role is viewer, reviewer, editor or owner") from None
    if member_role(project_id, user) == Role.OWNER and role != Role.OWNER.value:
        row = db().one("SELECT COUNT(*) AS n FROM memberships WHERE project_id = ? AND role = ?", (project_id, Role.OWNER.value))
        if row is not None and row["n"] <= 1:
            raise ValueError("a project keeps at least one owner")
    if role:
        db().execute(
            "INSERT INTO memberships(project_id, user, role, added_by, added_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, user) DO UPDATE SET role = excluded.role",
            (project_id, user, role, actor.user, time.time()),
        )
    else:
        db().execute("DELETE FROM memberships WHERE project_id = ? AND user = ?", (project_id, user))
    audit.record("member.set", actor, project_id, user, {"role": role or "removed"})


def touch(project_id: str) -> None:
    db().execute("UPDATE projects SET updated_at = ? WHERE id = ?", (time.time(), project_id))


# ------------------------------------------------------------------ templates


def product_templates() -> dict[str, dict[str, Any]]:
    """Forge's product templates: {id: product.yaml}."""
    out = {}
    root = paths.templates_dir() / "products"
    for d in sorted(root.iterdir()):
        f = d / "product.yaml"
        if f.is_file():
            out[d.name] = yaml.safe_load(f.read_text()) or {}
    return out


def _template_files(template_id: str) -> dict[str, bytes]:
    if not ids.is_name(template_id) or template_id not in product_templates():
        raise ValueError(f"there is no product template {template_id!r}")
    root = paths.templates_dir() / "products" / template_id
    files = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and rel != "product.yaml" and "__pycache__" not in rel:
            files[rel] = p.read_bytes()
    return files


# ------------------------------------------------------------------ creating


def create(title: str, template_id: str, chip: ChipPackage, actor: Actor) -> dict[str, Any]:
    """A new project from a product template, built around ``chip``, owned by ``actor``."""
    if actor.is_agent:
        raise Denied("An AI agent may not make projects")
    tpl = product_templates().get(template_id)
    if tpl is None:
        raise ValueError(f"there is no product template {template_id!r}")
    contract, issues, _ = hdc.generate(chip)
    if contract is None:
        errors = "; ".join(i.message for i in issues if i.level == "error")
        raise chip_package.ChipImportError(f"Forge can't use this chip: {errors}")
    sim = (tpl.get("simulation_for_chips") or {}).get(chip.source.get("name", "")) or {}
    if sim.get("frequency_hz"):
        contract.simulation.frequency_hz = int(sim["frequency_hz"])
    now = time.time()
    pid = ids.new_project_id()
    project = {
        "id": pid,
        "title": (title or "").strip()[:120] or str(tpl.get("title", template_id)),
        "template": template_id,
        "owner": actor.user,
        "owner_name": actor.name,
        "link_access": LinkAccess.EDITOR.value,
        "created_at": now,
        "updated_at": now,
        "chip": {"name": contract.chip.name, "top": contract.chip.top_module, "snapshot": chip.snapshot,
                 "source": chip.source, "interface": contract.interface.protocol},
    }
    work = paths.work_dir(pid)
    work.mkdir(parents=True, exist_ok=True)
    chip_package.write(chip, work / "chip")
    files = _template_files(template_id)
    files["hardware.yaml"] = hdc.to_yaml(contract).encode()
    for rel, data in files.items():
        f = work / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    db().insert("projects", project)
    db().insert("memberships", {"project_id": pid, "user": actor.user, "role": Role.OWNER.value, "added_by": actor.user, "added_at": now})
    for rel, data in files.items():
        _record_file(pid, rel, data, actor)
    audit.record("project.create", actor, pid, pid, {"title": project["title"], "template": template_id,
                                                      "chip": chip.source.get("ref", ""), "snapshot": chip.snapshot})
    return project


def delete(project_id: str, actor: Actor) -> None:
    require(project_id, actor, roles.MANAGE)
    db().execute("UPDATE projects SET deleted = 1, updated_at = ? WHERE id = ?", (time.time(), project_id))
    audit.record("project.delete", actor, project_id, project_id)


def purge(project_id: str) -> None:
    """Removes a deleted project's folder (the audit log stays)."""
    p = db().one("SELECT deleted FROM projects WHERE id = ?", (project_id,))
    if p is not None and p["deleted"] and ids.is_project(project_id):
        shutil.rmtree(paths.project_dir(project_id), ignore_errors=True)


# ------------------------------------------------------------------ files


def _record_file(project_id: str, path: str, data: bytes, actor: Actor) -> int:
    sha = hashlib.sha256(data).hexdigest()
    row = db().one("SELECT version FROM files WHERE project_id = ? AND path = ?", (project_id, path))
    version = (row["version"] + 1) if row else 1
    db().execute(
        "INSERT INTO files(project_id, path, sha256, size, version, updated_at, updated_by, updated_by_name) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, path) DO UPDATE SET sha256 = excluded.sha256, "
        "size = excluded.size, version = excluded.version, updated_at = excluded.updated_at, "
        "updated_by = excluded.updated_by, updated_by_name = excluded.updated_by_name",
        (project_id, path, sha, len(data), version, time.time(), actor.user, actor.name),
    )
    return version


def editable(path: str) -> bool:
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in EDITABLE)


def files(project_id: str) -> list[dict[str, Any]]:
    rows = db().query("SELECT * FROM files WHERE project_id = ? ORDER BY path", (project_id,))
    return [dict(r) for r in rows]


def file_info(project_id: str, path: str) -> dict[str, Any] | None:
    row = db().one("SELECT * FROM files WHERE project_id = ? AND path = ?", (project_id, path))
    return dict(row) if row else None


def read(project_id: str, path: str) -> str:
    path = ids.clean_path(path)
    f = paths.work_dir(project_id) / path
    if not f.is_file():
        raise NotFound(f"there is no file {path}")
    return f.read_text(encoding="utf-8", errors="replace")


def read_bytes(project_id: str, path: str) -> bytes:
    path = ids.clean_path(path)
    f = paths.work_dir(project_id) / path
    if not f.is_file():
        raise NotFound(f"there is no file {path}")
    return f.read_bytes()


def write(project_id: str, path: str, text: str, actor: Actor, base_version: int | None = None) -> int:
    """Saves a file (a person's edit, or an accepted AI proposal); returns its new version."""
    require(project_id, actor, roles.EDIT, path)
    path = ids.clean_path(path)
    if not editable(path):
        raise Denied(f"{path} isn't a file people edit here (the chip is read-only; generated files are made by builds)")
    data = text.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"a file is at most {MAX_FILE_BYTES // 1024} KB")
    if path.startswith("firmware/") and not _firmware_name_ok(path):
        raise ValueError("firmware files are firmware/<name>.c or .h (letters, digits, _ and -)")
    info = file_info(project_id, path)
    if base_version is not None and info is not None and info["version"] != base_version:
        raise Conflict(f"{path} was saved by {info['updated_by_name'] or 'someone'} meanwhile (version {info['version']}): reload it and edit again")
    f = paths.work_dir(project_id) / path
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(f)
    version = _record_file(project_id, path, data, actor)
    touch(project_id)
    audit.record("file.save", actor, project_id, path, {"version": version, "bytes": len(data)})
    return version


def delete_file(project_id: str, path: str, actor: Actor) -> None:
    require(project_id, actor, roles.EDIT, path)
    path = ids.clean_path(path)
    if not (path.startswith("firmware/") or path.startswith("tests/")):
        raise Denied("only firmware and test files can be deleted")
    f = paths.work_dir(project_id) / path
    if f.is_file():
        f.unlink()
    db().execute("DELETE FROM files WHERE project_id = ? AND path = ?", (project_id, path))
    audit.record("file.delete", actor, project_id, path)


def _firmware_name_ok(path: str) -> bool:
    import re  # noqa: PLC0415

    return re.match(r"^firmware/[A-Za-z0-9_\-]{1,48}\.(c|h)$", path) is not None


# ------------------------------------------------------------------ what the files say


def chip(project_id: str) -> ChipPackage:
    return chip_package.load(paths.work_dir(project_id) / "chip")


def contract(project_id: str) -> hdc.Contract:
    return hdc.from_yaml(read(project_id, "hardware.yaml"))


def firmware_sources(project_id: str) -> dict[str, bytes]:
    root = paths.work_dir(project_id) / "firmware"
    out = {}
    if root.is_dir():
        for f in sorted(root.iterdir()):
            rel = f"firmware/{f.name}"
            if f.is_file() and _firmware_name_ok(rel):
                out[rel] = f.read_bytes()
    return out


def test_files(project_id: str) -> dict[str, str]:
    root = paths.work_dir(project_id) / "tests"
    out = {}
    if root.is_dir():
        for f in sorted(root.glob("*.yaml")):
            out[f"tests/{f.name}"] = f.read_text()
    return out


def work_file(project_id: str, path: str) -> Path:
    return paths.work_dir(project_id) / ids.clean_path(path)
