"""Who is on which project: one entry per browser tab, in this process's memory.

As on a Codoc in CAD model or a Cellora notebook: a tab joins when it opens /p/<id>, pings
every few seconds, and leaves when it goes back to the project list, closes (a beacon) or stops
pinging for IDLE seconds. A tab is known by its Reflex client token, which is never shown to the
others; they see its ``peer`` (a random id), the person's name, picture and colour. Several tabs
of one person share a colour and count once. An AI working in a project is in it too.

Plain Python without Reflex.
"""

from __future__ import annotations

import dataclasses
import threading
import time

# The colours people get in a project, in this order: bright enough for a caret on the dark
# editor, and readable behind a name tag.
COLORS = ["#F97316", "#22C55E", "#A855F7", "#EF4444", "#EC4899", "#06B6D4", "#EAB308", "#3B82F6", "#14B8A6", "#F43F5E"]
# A tab that hasn't pinged for this long has left (it pings every 5 s; hidden tabs are throttled).
IDLE = 30.0


@dataclasses.dataclass
class Tab:
    peer: str  # what the others know the tab by
    user: str  # the person: an e-mail, guest-NNNN, or ai:<name>
    name: str
    avatar: str = ""
    color: str = ""
    last_seen: float = 0.0


_lock = threading.Lock()
# Project id -> client token -> tab, in the order they came.
_rooms: dict[str, dict[str, Tab]] = {}


def _color_for(tabs: dict[str, Tab], user: str) -> str:
    """The person's colour in the project: their other tabs' one, else a free one."""
    used: dict[str, str] = {}
    for t in tabs.values():
        if t.color:
            used.setdefault(t.user, t.color)
    if user in used:
        return used[user]
    taken = set(used.values())
    return next((c for c in COLORS if c not in taken), COLORS[len(used) % len(COLORS)])


def join(room: str, token: str, peer: str, user: str, name: str, avatar: str = "", now: float | None = None) -> tuple[str, str]:
    """Puts a tab in a project, or finds it there again: ``(the project it left or "", its colour)``."""
    now = time.time() if now is None else now
    with _lock:
        left = ""
        for other in [r for r, tabs in _rooms.items() if r != room and token in tabs]:
            _drop(other, token)
            left = other
        tabs = _rooms.setdefault(room, {})
        tab = tabs.get(token)
        if tab is None:
            tab = tabs[token] = Tab(peer, user, name, avatar)
        elif tab.user != user:
            tab.color = ""  # signed in or out: another person
        tab.peer, tab.user, tab.name, tab.avatar = peer, user, name, avatar
        if not tab.color:
            tab.color = _color_for({t: v for t, v in tabs.items() if t != token}, user)
        tab.last_seen = now
        return left, tab.color


def _drop(room: str, token: str) -> bool:
    tabs = _rooms.get(room)
    if not tabs or tabs.pop(token, None) is None:
        return False
    if not tabs:
        del _rooms[room]
    return True


def touch(room: str, token: str, now: float | None = None) -> bool:
    """The tab is still there; False when it isn't in the project (any more)."""
    with _lock:
        tab = _rooms.get(room, {}).get(token)
        if tab is None:
            return False
        tab.last_seen = time.time() if now is None else now
        return True


def leave(token: str) -> str:
    """Takes a tab out of its project; returns that project ("" when it was in none)."""
    with _lock:
        for room in [r for r, tabs in _rooms.items() if token in tabs]:
            _drop(room, token)
            return room
    return ""


def prune(room: str, idle: float = IDLE, now: float | None = None) -> list[str]:
    """Drops the tabs not seen for ``idle`` seconds; returns their tokens."""
    now = time.time() if now is None else now
    with _lock:
        gone = [t for t, tab in _rooms.get(room, {}).items() if now - tab.last_seen > idle]
        for token in gone:
            _drop(room, token)
        return gone


def tabs(room: str) -> list[dict[str, str]]:
    """One entry per tab in the project: {"id", "user", "name", "avatar", "color"}."""
    with _lock:
        return [
            {"id": t.peer, "user": t.user, "name": t.name, "avatar": t.avatar, "color": t.color}
            for t in _rooms.get(room, {}).values()
        ]


def users_on(room: str) -> list[str]:
    """The people (not AIs) in the project."""
    with _lock:
        return sorted({t.user for t in _rooms.get(room, {}).values() if t.user and not t.user.startswith("ai:")})


def watching() -> dict[str, list[dict[str, str]]]:
    """The people in each project, one entry per person: {project: [{"user", "name", "avatar", "color"}]}."""
    out: dict[str, list[dict[str, str]]] = {}
    with _lock:
        for room, tabs_ in _rooms.items():
            people: dict[str, dict[str, str]] = {}
            for t in tabs_.values():
                people.setdefault(t.user or t.peer, {"user": t.user, "name": t.name, "avatar": t.avatar, "color": t.color})
            if people:
                out[room] = list(people.values())
    return out


def initials(name: str, user: str = "") -> str:
    """What a person's circle shows without a picture."""
    if user.startswith("ai:"):
        return "AI"
    return "".join(w[0] for w in (name or "?").split()[:2]).upper() or "?"
