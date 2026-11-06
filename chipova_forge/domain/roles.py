"""Who may do what in a project.

    viewer    see everything, download artifacts and evidence
    editor    a viewer who also works: edits the project's files, builds, starts and stops
              sessions, operates the virtual board (commands, sensors), runs tests, decides
              on AI proposals, asks for a release
    reviewer  a viewer who also approves releases
    owner     everything, also the project's settings, members and deleting it

An AI agent acts with the access of the person it works for, but never edits files itself (it
proposes; people decide), never decides on proposals, approves releases or manages the project.
It may build, simulate, operate the virtual board and run tests: nothing of that leaves the
simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    OWNER = "owner"


class LinkAccess(str, Enum):
    NONE = "none"
    VIEWER = "viewer"
    EDITOR = "editor"


VIEW = "view"
DOWNLOAD = "download"
EDIT = "edit"
BUILD = "build"
SIMULATE = "simulate"
OPERATE = "operate"
TEST = "test"
DECIDE_PROPOSAL = "decide_proposal"
PROPOSE = "propose"
REQUEST_RELEASE = "request_release"
APPROVE_RELEASE = "approve_release"
MANAGE = "manage"

_VIEWER = {VIEW, DOWNLOAD}
_EDITOR = _VIEWER | {EDIT, BUILD, SIMULATE, OPERATE, TEST, DECIDE_PROPOSAL, PROPOSE, REQUEST_RELEASE}
_REVIEWER = _VIEWER | {APPROVE_RELEASE}
_OWNER = _EDITOR | _REVIEWER | {MANAGE}

ALLOWED: dict[Role, set[str]] = {
    Role.VIEWER: _VIEWER,
    Role.EDITOR: _EDITOR,
    Role.REVIEWER: _REVIEWER,
    Role.OWNER: _OWNER,
}

NEVER_FOR_AGENTS = {EDIT, DECIDE_PROPOSAL, APPROVE_RELEASE, MANAGE}

_RANK = {Role.VIEWER: 0, Role.REVIEWER: 1, Role.EDITOR: 2, Role.OWNER: 3}

WORDS = {
    VIEW: "see it",
    DOWNLOAD: "download from it",
    EDIT: "edit its files",
    BUILD: "build",
    SIMULATE: "start or stop simulations",
    OPERATE: "operate the virtual board",
    TEST: "run tests",
    DECIDE_PROPOSAL: "accept or reject AI proposals",
    PROPOSE: "propose changes",
    REQUEST_RELEASE: "ask for a release",
    APPROVE_RELEASE: "approve releases",
    MANAGE: "change its settings and members",
}


@dataclass(frozen=True)
class Access:
    """What one person (or the AI working for them) may do in one project."""

    roles: frozenset[Role] = frozenset()
    agent: bool = False

    def can(self, action: str) -> bool:
        if self.agent and action in NEVER_FOR_AGENTS:
            return False
        return any(action in ALLOWED[r] for r in self.roles)

    @property
    def role(self) -> Role | None:
        return max(self.roles, key=lambda r: _RANK[r]) if self.roles else None

    @property
    def label(self) -> str:
        if Role.OWNER in self.roles:
            return "Owner"
        names = [r.value.capitalize() for r in sorted(self.roles, key=lambda r: -_RANK[r])]
        return " + ".join(names) if names else "No access"


def link_role(link: LinkAccess) -> Role | None:
    return {LinkAccess.VIEWER: Role.VIEWER, LinkAccess.EDITOR: Role.EDITOR}.get(link)


def access(member: Role | None, link: LinkAccess, agent: bool = False) -> Access:
    roles = {r for r in (member, link_role(link)) if r is not None}
    return Access(frozenset(roles), agent)
