"""Where Chipova Forge keeps its data.

``FORGE_HOME`` moves it; the default is ``~/Library/Application Support/chipova-forge`` on macOS
and ``~/.local/share/chipova-forge`` on Linux (on re_ddns that folder is the volume
``smart-app-chipova-forge-data``). It lies outside the project folder, so writing it never triggers a
Reflex hot reload.

    <home>/forge.db                          projects, builds, sessions, tests, releases, audit
    <home>/secret                            signs download links and API tokens
    <home>/projects/<project>/work/          the project's files (chip/, hardware.yaml,
                                             system.yaml, device.yaml, firmware/, tests/)
    <home>/projects/<project>/builds/<id>/   one build: inputs.tar, manifest.json, log, outputs
    <home>/projects/<project>/sessions/<id>/ a session's evidence once it ends (events, logs, VCD)
    <home>/projects/<project>/releases/<id>/ release packages
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def _default_home() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "chipova-forge"


def home() -> Path:
    return Path(os.environ.get("FORGE_HOME") or _default_home())


def db_file() -> Path:
    return home() / "forge.db"


def project_dir(project_id: str) -> Path:
    return home() / "projects" / project_id


def work_dir(project_id: str) -> Path:
    return project_dir(project_id) / "work"


def build_dir(project_id: str, build_id: str) -> Path:
    return project_dir(project_id) / "builds" / build_id


def session_dir(project_id: str, session_id: str) -> Path:
    return project_dir(project_id) / "sessions" / session_id


def release_dir(project_id: str, release_id: str) -> Path:
    return project_dir(project_id) / "releases" / release_id


def secret() -> bytes:
    """This installation's signing key (made on first use, kept in the data folder)."""
    path = home() / "secret"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        tmp = path.with_name(f"secret.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(key)
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, path)  # the first writer wins; a second one reads the first's key
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
        return path.read_bytes()


def repo_root() -> Path:
    """The app's source folder (templates, infra)."""
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    return repo_root() / "templates"
