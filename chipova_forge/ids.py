"""Ids of the things Chipova Forge keeps, and checks that an id from outside (a URL, an AI's
call, the API) is one of them before it is used in a path or a query."""

from __future__ import annotations

import re
import secrets

# A project's id is also the token of its shared room (which can't hold "_") and a folder name.
_PROJECT = re.compile(r"^[a-f0-9]{10}$")
_PREFIXED = re.compile(r"^(bld|ses|tst|rel|art|prp)-[a-f0-9]{10}$")
# A path in a project: folders and names of letters, digits, "_", "-", "." (no "..", no "/" at
# either end), at most 8 levels.
_PATH_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def new_project_id() -> str:
    return secrets.token_hex(5)


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5)}"


def is_project(value: str) -> bool:
    return bool(value) and _PROJECT.match(value) is not None


def is_prefixed(value: str, prefix: str) -> bool:
    m = _PREFIXED.match(value or "")
    return m is not None and m.group(1) == prefix


def is_name(value: str) -> bool:
    """A name in a description (a sensor, a pin, a command): a C/Verilog-style identifier."""
    return bool(value) and _NAME.match(value) is not None


def clean_path(path: str) -> str:
    """A project file path, checked: ValueError for anything that could leave the project
    (``..``, absolute paths, odd characters)."""
    path = (path or "").strip().replace("\\", "/")
    parts = path.split("/")
    if not path or path.startswith("/") or len(parts) > 8:
        raise ValueError(f"not a project file path: {path!r}")
    for part in parts:
        if not _PATH_PART.match(part) or part in {".", ".."} or ".." in part:
            raise ValueError(f"not a project file path: {path!r} (use letters, digits, _ - . and /)")
    return "/".join(parts)
