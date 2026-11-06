"""Chipova Forge's records in one SQLite file (``<home>/forge.db``, see :mod:`chipova_forge.paths`).

One connection per process, shared by the event loop and worker threads under a lock; every
query is short (the big things, files and artifacts, live as files). The schema is created on
first use; ``SCHEMA`` counts its versions.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from typing import Any, Iterator

from chipova_forge import paths

SCHEMA = 1

_TABLES = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS projects(
    id TEXT PRIMARY KEY, title TEXT NOT NULL, template TEXT DEFAULT '', owner TEXT DEFAULT '',
    owner_name TEXT DEFAULT '', link_access TEXT NOT NULL, created_at REAL, updated_at REAL,
    deleted INTEGER DEFAULT 0, chip TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS memberships(
    project_id TEXT, user TEXT, role TEXT NOT NULL, added_by TEXT DEFAULT '', added_at REAL,
    PRIMARY KEY(project_id, user));
CREATE TABLE IF NOT EXISTS files(
    project_id TEXT, path TEXT, sha256 TEXT DEFAULT '', size INTEGER DEFAULT 0, version INTEGER DEFAULT 1,
    updated_at REAL, updated_by TEXT DEFAULT '', updated_by_name TEXT DEFAULT '',
    PRIMARY KEY(project_id, path));
CREATE TABLE IF NOT EXISTS builds(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    inputs_sha TEXT DEFAULT '', requested_by TEXT DEFAULT '', requested_by_name TEXT DEFAULT '',
    requested_via TEXT DEFAULT 'ui', created_at REAL, started_at REAL DEFAULT 0, ended_at REAL DEFAULT 0,
    image TEXT DEFAULT '', image_id TEXT DEFAULT '', exit_code INTEGER, error TEXT DEFAULT '',
    summary TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS builds_project ON builds(project_id, created_at);
CREATE TABLE IF NOT EXISTS artifacts(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner TEXT NOT NULL, kind TEXT NOT NULL,
    name TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, created_at REAL);
CREATE INDEX IF NOT EXISTS artifacts_owner ON artifacts(owner);
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL, container TEXT DEFAULT '',
    chip_build TEXT DEFAULT '', firmware_build TEXT DEFAULT '', config_sha TEXT DEFAULT '',
    started_by TEXT DEFAULT '', started_by_name TEXT DEFAULT '', started_via TEXT DEFAULT 'ui',
    created_at REAL, ready_at REAL DEFAULT 0, ended_at REAL DEFAULT 0, expires_at REAL DEFAULT 0,
    last_active REAL DEFAULT 0, last_seq INTEGER DEFAULT 0, error TEXT DEFAULT '', summary TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS sessions_project ON sessions(project_id, created_at);
CREATE TABLE IF NOT EXISTS test_runs(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scenario TEXT NOT NULL, status TEXT NOT NULL,
    session_id TEXT DEFAULT '', requested_by TEXT DEFAULT '', requested_by_name TEXT DEFAULT '',
    requested_via TEXT DEFAULT 'ui', created_at REAL, ended_at REAL DEFAULT 0, steps TEXT DEFAULT '[]',
    error TEXT DEFAULT '', inputs TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS test_runs_project ON test_runs(project_id, created_at);
CREATE TABLE IF NOT EXISTS releases(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT DEFAULT '', status TEXT NOT NULL,
    created_by TEXT DEFAULT '', created_by_name TEXT DEFAULT '', created_at REAL,
    checks TEXT DEFAULT '[]', manifest_sha TEXT DEFAULT '', package TEXT DEFAULT '', size INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS proposals(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, agent TEXT NOT NULL, agent_name TEXT DEFAULT '',
    title TEXT DEFAULT '', rationale TEXT DEFAULT '', files TEXT DEFAULT '[]', status TEXT NOT NULL,
    decided_by TEXT DEFAULT '', decided_by_name TEXT DEFAULT '', decided_at REAL DEFAULT 0,
    reason TEXT DEFAULT '', created_at REAL);
CREATE INDEX IF NOT EXISTS proposals_project ON proposals(project_id, created_at);
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, project_id TEXT DEFAULT '',
    actor TEXT DEFAULT '', actor_name TEXT DEFAULT '', actor_kind TEXT DEFAULT 'human', action TEXT NOT NULL,
    target TEXT DEFAULT '', args TEXT DEFAULT '{}', decision TEXT DEFAULT 'ok', detail TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS audit_project ON audit(project_id, ts);
"""

# Columns added after a table was first made: (table, column, declaration).
_ADDED_COLUMNS: list[tuple[str, str, str]] = []


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(_TABLES)
            for table, column, decl in _ADDED_COLUMNS:
                have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if column not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA),))

    def query(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def execute(self, sql: str, args: tuple | list = ()) -> int:
        """Runs one statement; returns the rows it changed (or the new row id of an insert)."""
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def insert(self, table: str, row: dict[str, Any]) -> None:
        cols = list(row)
        self.execute(
            f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
            [_value(row[c]) for c in cols],
        )

    def update(self, table: str, key: dict[str, Any], fields: dict[str, Any]) -> int:
        if not fields:
            return 0
        sets = ", ".join(f"{c} = ?" for c in fields)
        where = " AND ".join(f"{c} = ?" for c in key)
        return self.execute(
            f"UPDATE {table} SET {sets} WHERE {where}",
            [_value(v) for v in fields.values()] + [_value(v) for v in key.values()],
        )

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def _value(v: Any) -> Any:
    """What SQLite stores for a field: JSON for lists and dicts, the value of enums."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    if hasattr(v, "value") and not isinstance(v, (int, float)):
        return v.value
    return v


_db: Database | None = None
_db_lock = threading.Lock()


def db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                path = paths.db_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                _db = Database(str(path))
    return _db


def reset() -> None:
    """Forgets the open database (tests point FORGE_HOME elsewhere and start again)."""
    global _db
    with _db_lock:
        if _db is not None:
            _db.close()
        _db = None


def loads(value: str | None, default: Any) -> Any:
    """A JSON column, read back (``default`` for empty or broken values)."""
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default
