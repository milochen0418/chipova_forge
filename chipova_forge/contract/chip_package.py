"""A chip from Chipova: the files of one frozen snapshot, and where they came from.

Forge never edits a chip: it takes the files of one Chipova snapshot as they are, checks that
they are that snapshot, and keeps them read-only in the project's ``chip/`` folder with
``CHIP_LOCK.json`` (the source, the snapshot id and each file's SHA-256).

A snapshot's id is Chipova's revision id: ``sha256:`` + the SHA-256 of its source manifest (each
file's path and SHA-256, sorted, as compact JSON), so the same files always give the same id and
Forge can recompute it from the files alone (:func:`snapshot_id`).

Where a chip can come from:

- a Chipova release package (the zip Chipova's Release page makes): ``source/<path>`` files and
  ``RELEASE_MANIFEST.json``, whose ``revision.files`` lists each file's SHA-256
  (:func:`from_release_zip`);
- an item of the ``chips`` collection Chipova publishes into (the same files, unzipped);
- one of Forge's example chips (``templates/chips/<name>``), as Chipova would publish it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from chipova_forge import ids, paths

LOCK_NAME = "CHIP_LOCK.json"
MAX_FILES = 2000
MAX_BYTES = 64 << 20
# What a chip package may hold: sources and descriptions, not build outputs.
_SKIP_DIRS = {".git", "runs", "build", "out", "__pycache__"}


class ChipImportError(ValueError):
    """The files are not a chip Forge can take (the message says why)."""


@dataclasses.dataclass
class ChipPackage:
    files: dict[str, bytes]
    source: dict[str, Any]
    snapshot: str

    @property
    def manifest(self) -> list[dict]:
        return manifest(self.files)

    def text(self, path: str) -> str:
        return self.files[path].decode("utf-8", errors="replace")

    def settings(self) -> dict[str, Any]:
        """The chip's ``chipova.toml`` (top module, RTL files, clock, ...)."""
        if "chipova.toml" not in self.files:
            return {}
        try:
            return tomllib.loads(self.text("chipova.toml"))
        except tomllib.TOMLDecodeError as e:
            raise ChipImportError(f"chipova.toml doesn't read: {e}") from e

    @property
    def name(self) -> str:
        return str(self.source.get("name") or self.settings().get("design", {}).get("top", "chip"))


def manifest(files: dict[str, bytes]) -> list[dict]:
    """Each file's path, SHA-256 and size, sorted by path (as Chipova lists a snapshot)."""
    return [{"path": p, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for p, b in sorted(files.items())]


def snapshot_id(files: dict[str, bytes]) -> str:
    entries = manifest(files)
    canonical = json.dumps([[e["path"], e["sha256"]] for e in entries], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _check_files(files: dict[str, bytes]) -> dict[str, bytes]:
    if not files:
        raise ChipImportError("there are no files")
    if len(files) > MAX_FILES or sum(len(b) for b in files.values()) > MAX_BYTES:
        raise ChipImportError(f"a chip is at most {MAX_FILES} files and {MAX_BYTES >> 20} MB")
    out = {}
    for p, b in files.items():
        try:
            clean = ids.clean_path(p)
        except ValueError as e:
            raise ChipImportError(str(e)) from e
        out[clean] = b
    if "chipova.toml" not in out:
        raise ChipImportError("there is no chipova.toml (the chip's settings: top module, RTL files)")
    return out


def from_files(files: dict[str, bytes], source: dict[str, Any]) -> ChipPackage:
    files = _check_files(files)
    return ChipPackage(files=files, source=dict(source), snapshot=snapshot_id(files))


def read_dir(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel == LOCK_NAME or any(part in _SKIP_DIRS or part.startswith(".") for part in rel.split("/")):
            continue
        files[rel] = p.read_bytes()
    return files


def example_chips() -> dict[str, dict[str, Any]]:
    """Forge's example chips: {name: {"title", "description"}}."""
    out = {}
    for d in sorted((paths.templates_dir() / "chips").iterdir()):
        if (d / "chipova.toml").is_file():
            info: dict[str, Any] = {"title": d.name, "description": ""}
            readme = d / "README.md"
            if readme.is_file():
                lines = [ln.strip() for ln in readme.read_text().splitlines() if ln.strip()]
                if lines:
                    info["title"] = lines[0].lstrip("# ")
                    info["description"] = lines[1] if len(lines) > 1 else ""
            out[d.name] = info
    return out


def from_example(name: str) -> ChipPackage:
    if not ids.is_name(name):
        raise ChipImportError(f"there is no example chip {name!r}")
    root = paths.templates_dir() / "chips" / name
    if not (root / "chipova.toml").is_file():
        raise ChipImportError(f"there is no example chip {name!r}")
    files = read_dir(root)
    return from_files(files, {"kind": "example", "name": name, "ref": f"example:{name}"})


def from_release_zip(data: bytes, filename: str = "") -> ChipPackage:
    """A Chipova release package: its ``source/`` files, checked against its manifest."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ChipImportError("this isn't a zip file") from e
    with z:
        names = z.namelist()
        if "RELEASE_MANIFEST.json" not in names:
            raise ChipImportError("this zip has no RELEASE_MANIFEST.json: make it on Chipova's Release page")
        try:
            rel = json.loads(z.read("RELEASE_MANIFEST.json"))
        except ValueError as e:
            raise ChipImportError("RELEASE_MANIFEST.json doesn't read") from e
        files: dict[str, bytes] = {}
        total = 0
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith("source/"):
                continue
            total += info.file_size
            if total > MAX_BYTES:
                raise ChipImportError(f"the sources are larger than {MAX_BYTES >> 20} MB")
            files[info.filename[len("source/"):]] = z.read(info)
    return _verified(files, rel, {"kind": "release", "filename": filename})


def _verified(files: dict[str, bytes], rel: dict[str, Any], source: dict[str, Any]) -> ChipPackage:
    revision = rel.get("revision") or {}
    listed = {e["path"]: e["sha256"] for e in revision.get("files") or [] if "path" in e and "sha256" in e}
    if not listed:
        raise ChipImportError("the release manifest lists no files of a snapshot")
    have = {e["path"]: e["sha256"] for e in manifest(files)}
    missing = sorted(set(listed) - set(have))
    changed = sorted(p for p in set(listed) & set(have) if listed[p] != have[p])
    extra = sorted(set(have) - set(listed))
    if missing or changed or extra:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing[:5]))
        if changed:
            parts.append("changed " + ", ".join(changed[:5]))
        if extra:
            parts.append("not in the snapshot " + ", ".join(extra[:5]))
        raise ChipImportError("the files don't match the release's snapshot: " + "; ".join(parts))
    pkg = from_files(files, source)
    want = str(revision.get("id") or "")
    if want and want != pkg.snapshot:
        raise ChipImportError(f"the files hash to {pkg.snapshot[:19]}…, but the release says {want[:19]}…")
    project = rel.get("project") or {}
    release = rel.get("release") or {}
    pkg.source.update({
        "name": project.get("title") or pkg.settings().get("design", {}).get("top", "chip"),
        "chipova_project": project.get("id", ""),
        "chipova_release": release.get("id", ""),
        "release_status": release.get("status", ""),
        "gate": rel.get("gate") or {},
        "ref": f"chipova:{project.get('id', '')}/{release.get('id', '')}",
    })
    return pkg


def from_collection_dir(root: Path, ref: str) -> ChipPackage:
    """An item of the ``chips`` collection: ``RELEASE_MANIFEST.json`` and ``source/``."""
    mpath = root / "RELEASE_MANIFEST.json"
    if not mpath.is_file():
        raise ChipImportError("the item has no RELEASE_MANIFEST.json")
    rel = json.loads(mpath.read_text())
    files = read_dir(root / "source")
    return _verified(files, rel, {"kind": "collection", "ref": ref})


def write(pkg: ChipPackage, dest: Path) -> None:
    """Puts the chip's files and its lock into ``dest`` (the project's ``chip/``)."""
    dest.mkdir(parents=True, exist_ok=True)
    for p, b in pkg.files.items():
        f = dest / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)
    lock = {"snapshot": pkg.snapshot, "source": pkg.source, "files": pkg.manifest}
    (dest / LOCK_NAME).write_text(json.dumps(lock, indent=1, ensure_ascii=False))


def load(dest: Path) -> ChipPackage:
    """The chip a project has, checked against its lock (ChipImportError if it was changed)."""
    lock_file = dest / LOCK_NAME
    if not lock_file.is_file():
        raise ChipImportError("the project has no chip yet")
    lock = json.loads(lock_file.read_text())
    files = read_dir(dest)
    pkg = ChipPackage(files=files, source=lock.get("source", {}), snapshot=snapshot_id(files))
    if pkg.snapshot != lock.get("snapshot"):
        raise ChipImportError("the chip's files changed since they were imported: import it again")
    return pkg
