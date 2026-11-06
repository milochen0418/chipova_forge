"""Who is acting: a person (signed in or a guest), an AI agent working for someone, or Forge
itself."""

from __future__ import annotations

import dataclasses
import hashlib


@dataclasses.dataclass(frozen=True)
class Actor:
    user: str  # an e-mail, guest-<key>, or agent:<name>
    name: str
    kind: str = "human"  # human | agent | system
    email: str = ""
    # For an agent: the person it works for (its access is theirs, minus what agents never do).
    on_behalf_of: str = ""

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"

    @property
    def is_guest(self) -> bool:
        return self.user.startswith("guest-")


SYSTEM = Actor("system", "Chipova Forge", "system")


def guest(key: str) -> Actor:
    """A guest, known by the key their browser keeps. The key itself stays secret (it is what
    makes them this guest); others only ever see a one-way hash of it."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    number = int(digest[:8], 16) % 9000 + 1000
    return Actor(f"guest-{digest[:16]}", f"Guest {number}")


def agent(name: str, on_behalf_of: str = "") -> Actor:
    name = (name or "AI").strip()[:60]
    return Actor(f"agent:{name}", f"AI · {name}", "agent", on_behalf_of=on_behalf_of)
