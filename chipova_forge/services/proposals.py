"""AI proposals: an AI agent never edits a project's files; it proposes whole new texts of some
files, with why, and a person accepts (the files are saved as theirs) or rejects them (with a
reason the AI reads). Chipova's rule, the same here.

A proposal remembers each file's version when it was made; accepting one whose file someone
changed meanwhile is refused (Conflict), so a proposal never overwrites newer work.
"""

from __future__ import annotations

import json
import time
from typing import Any

from chipova_forge import ids
from chipova_forge.db import db, loads
from chipova_forge.domain import roles
from chipova_forge.services import audit, projects
from chipova_forge.services.actors import Actor

MAX_FILES = 12
MAX_EDITS = 20
# What models call the two halves of an edit (coding tools taught them several names).
_OLD = ("old", "old_string", "old_text", "oldText", "oldString", "search", "find", "from", "before", "original")
_NEW = ("new", "new_string", "new_text", "newText", "newString", "replace", "replacement", "to", "after")


def _text(v: Any) -> Any:
    """Lines given as a list are the lines of one text."""
    return "\n".join(v) if isinstance(v, list) and all(isinstance(x, str) for x in v) else v


def _edit_pair(e: Any) -> tuple[Any, Any, str]:
    """An edit's old and new text, whatever the model called them; else what it sent instead."""
    if isinstance(e, (list, tuple)) and len(e) == 2:
        return _text(e[0]), _text(e[1]), ""
    if not isinstance(e, dict):
        return None, None, f"an edit is {{\"old\": ..., \"new\": ...}}, not {type(e).__name__}"
    old = _text(next((e[k] for k in _OLD if k in e), None))
    new = _text(next((e[k] for k in _NEW if k in e), None))
    return old, new, "" if old is not None and new is not None else f"it has {', '.join(map(str, e)) or 'nothing'}: use old and new"


def apply_edits(text: str, edits: Any, path: str) -> str:
    """A small change to a long file, as edits: each {"old": text that is in the file now, once,
    "new": what replaces it}. A model that copied the lines with other indentation or spaces at
    their ends still finds them: the lines are compared without those, and the new lines take the
    file's indentation."""
    if isinstance(edits, str):
        try:
            edits = json.loads(edits, strict=False)
        except ValueError:
            raise ValueError(f'{path}: edits are a list, [{{"old": ..., "new": ...}}], not a text') from None
    if isinstance(edits, dict):
        edits = [edits]
    if not isinstance(edits, list) or not edits or len(edits) > MAX_EDITS:
        raise ValueError(f'{path}: give 1 to {MAX_EDITS} edits, as [{{"old": ..., "new": ...}}]')
    for i, e in enumerate(edits, 1):
        old, new, why = _edit_pair(e)
        if not why and isinstance(old, str) and not old.strip():
            why = "old is empty: to add lines, take the line before them as old, and that line followed by the new ones as new"
        elif not why and not isinstance(old, str):
            why = f"old is {type(old).__name__}, not text"
        elif not why and not isinstance(new, str):
            why = f"new is {type(new).__name__}, not text"
        if why:
            raise ValueError(f"{path}, edit {i}: give the text to replace as old, and what replaces it as new ({why})")
        n = text.count(old)
        if n == 1:
            pos = text.find(old)
            prefix = text[text.rfind("\n", 0, pos) + 1:pos]
            if prefix and not prefix.strip() and "\n" in new:
                # Copied from after the line's indentation: its new lines get that indentation too.
                first, *rest = new.split("\n")
                new = "\n".join([first] + [prefix + ln if ln.strip() and not ln.startswith(prefix) else ln for ln in rest])
            text = text[:pos] + new + text[pos + len(old):]
            continue
        if n > 1:
            raise ValueError(f"{path}, edit {i}: that text is there {n} times: take a line before or after it too, so it's there once")
        lines = text.split("\n")
        want = [ln.strip() for ln in old.strip("\n").split("\n")]
        hits = [k for k in range(len(lines) - len(want) + 1) if [ln.strip() for ln in lines[k:k + len(want)]] == want]
        if len(hits) != 1:
            raise ValueError(f"{path}, edit {i}: " + ("that text isn't in the file: read it with forge.read_file and copy its lines exactly" if not hits
                                                    else f"those lines are there {len(hits)} times: take a line before or after them too"))
        k = hits[0]
        indent = lines[k][: len(lines[k]) - len(lines[k].lstrip())]
        new_lines = new.strip("\n").split("\n") if new.strip() else []
        base = new_lines[0][: len(new_lines[0]) - len(new_lines[0].lstrip())] if new_lines else ""
        lines[k:k + len(want)] = [indent + (ln[len(base):] if ln.startswith(base) else ln.lstrip()) for ln in new_lines]
        text = "\n".join(lines)
    return text
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
        info = projects.file_info(project_id, path)
        old = ""
        if info is not None:
            try:
                old = projects.read(project_id, path)
            except projects.NotFound:
                old = ""
        text = ch.get("content", ch.get("text"))
        if text is None and "edits" not in ch and any(k in ch for k in _OLD):
            ch = {**ch, "edits": [ch]}   # one edit given right in the change
        if text is None and "edits" in ch:
            if info is None:
                raise ValueError(f"{path} isn't there yet: give its whole text as content")
            text = apply_edits(old, ch["edits"], path)
            if text == old:
                raise ValueError(f"{path}: the edits change nothing")
        if not isinstance(text, str):
            raise ValueError(f"{path}: give the whole new text as content, or edits")
        if len(text.encode()) > projects.MAX_FILE_BYTES:
            raise ValueError(f"{path} is too large")
        files.append({"path": path, "text": text, "base_version": info["version"] if info else 0, "old": old})
    pid = ids.new_id("prp")
    db().insert("proposals", {"id": pid, "project_id": project_id, "agent": actor.user, "agent_name": actor.name,
                              "title": (title or "").strip()[:160] or "A change", "rationale": (rationale or "")[:4000],
                              "files": files, "status": "PROPOSED", "created_at": time.time()})
    audit.record("proposal.create", actor, project_id, pid, {"title": title, "files": [f["path"] for f in files]})
    # The same AI's older proposals for these files, still waiting, are replaced by this one: nobody
    # should accept an outdated copy.
    paths = {f["path"] for f in files}
    for old_p in db().query("SELECT id, files FROM proposals WHERE project_id = ? AND agent = ? AND status = 'PROPOSED' AND id != ?",
                            (project_id, actor.user, pid)):
        if paths & {f["path"] for f in loads(old_p["files"], [])}:
            db().update("proposals", {"id": old_p["id"]}, {"status": "SUPERSEDED", "reason": f"replaced by {pid}"})
            audit.record("proposal.superseded", actor, project_id, old_p["id"], {"by": pid})
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
