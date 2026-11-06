"""AI proposals: an AI agent never edits a project's files; it proposes whole new texts of some
files, with why, and a person accepts (the files are saved as theirs) or rejects them (with a
reason the AI reads). Chipova's rule, the same here.

A proposal remembers each file's version when it was made; accepting one whose file someone
changed meanwhile is refused (Conflict), so a proposal never overwrites newer work.
"""

from __future__ import annotations

import time
from typing import Any

from chipova_forge import ids
from chipova_forge.db import db, loads
from chipova_forge.domain import roles
from chipova_forge.services import audit, projects
from chipova_forge.services.actors import Actor

MAX_FILES = 12
EXPIRE_DAYS = 14

listeners: list = []


def _notify(project_id: str, proposal_id: str) -> None:
    for fn in list(listeners):
        try:
            fn(project_id, proposal_id)
        except Exception:  # noqa: BLE001
            pass


def get(proposal_id: str) -> dict[str, Any] | None:
    if not ids.is_prefixed(proposal_id, "prp"):
        return None
    row = db().one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
    return {**dict(row), "files": loads(row["files"], [])} if row else None


def of_project(project_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = db().query("SELECT * FROM proposals WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))
    return [{**dict(r), "files": loads(r["files"], [])} for r in rows]


def propose(project_id: str, actor: Actor, title: str, changes: list[dict[str, Any]], rationale: str = "") -> dict[str, Any]:
    projects.require(project_id, actor, roles.PROPOSE)
    if not changes or len(changes) > MAX_FILES:
        raise ValueError(f"propose 1 to {MAX_FILES} files")
    files = []
    for ch in changes:
        path = ids.clean_path(str(ch.get("path", "")))
        if not projects.editable(path):
            raise ValueError(f"{path} can't be changed here (the chip is read-only; generated files come from builds)")
        text = ch.get("content", ch.get("text"))
        if not isinstance(text, str):
            raise ValueError(f"{path}: give the whole new text as content")
        if len(text.encode()) > projects.MAX_FILE_BYTES:
            raise ValueError(f"{path} is too large")
        info = projects.file_info(project_id, path)
        old = ""
        if info is not None:
            try:
                old = projects.read(project_id, path)
            except projects.NotFound:
                old = ""
        files.append({"path": path, "text": text, "base_version": info["version"] if info else 0, "old": old})
    pid = ids.new_id("prp")
    db().insert("proposals", {"id": pid, "project_id": project_id, "agent": actor.user, "agent_name": actor.name,
                              "title": (title or "").strip()[:160] or "A change", "rationale": (rationale or "")[:4000],
                              "files": files, "status": "PROPOSED", "created_at": time.time()})
    audit.record("proposal.create", actor, project_id, pid, {"title": title, "files": [f["path"] for f in files]})
    _notify(project_id, pid)
    return get(pid)  # type: ignore[return-value]


def decide(proposal_id: str, actor: Actor, accept: bool, reason: str = "") -> dict[str, Any]:
    p = get(proposal_id)
    if p is None:
        raise projects.NotFound("there is no such proposal")
    projects.require(p["project_id"], actor, roles.DECIDE_PROPOSAL, proposal_id)
    if p["status"] != "PROPOSED":
        raise projects.Conflict(f"it was already {p['status'].lower()}")
    if accept:
        for f in p["files"]:
            info = projects.file_info(p["project_id"], f["path"])
            now_version = info["version"] if info else 0
            if now_version != f["base_version"]:
                raise projects.Conflict(f"{f['path']} changed since the proposal was made: ask the AI for a new one")
        for f in p["files"]:
            projects.write(p["project_id"], f["path"], f["text"], actor)
    db().update("proposals", {"id": proposal_id}, {"status": "ACCEPTED" if accept else "REJECTED", "decided_by": actor.user,
                                                    "decided_by_name": actor.name, "decided_at": time.time(), "reason": (reason or "")[:1000]})
    audit.record("proposal.accept" if accept else "proposal.reject", actor, p["project_id"], proposal_id, {"reason": reason})
    _notify(p["project_id"], proposal_id)
    return get(proposal_id)  # type: ignore[return-value]


def expire_old() -> None:
    db().execute("UPDATE proposals SET status = 'EXPIRED' WHERE status = 'PROPOSED' AND created_at < ?", (time.time() - EXPIRE_DAYS * 86400,))
