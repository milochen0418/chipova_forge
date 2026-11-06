"""One job container: made, filled, started, followed, emptied and removed (docker-py).

(From Chipova's runner.) Forge talks to Docker through its socket: /var/run/docker.sock, which
the App Store catalog mounts into the app's container, or Docker Desktop's on a Mac. A job
container:

- runs one image (the simulation image, the board image), as user 1000 (not root), with no network, all Linux
  capabilities dropped, no new privileges, and limits on CPUs, memory and processes;
- gets its whole workspace copied in before it starts (``put_archive``): the snapshot's files
  under /work/src (owned by root, read-only to the job), inputs from other runs under /work/in
  (read-only too), the generated scripts under /work/job, and /work/out, /work/home, /work/tmp
  to write in. Nothing of the host is mounted, so the same code works when the app itself runs
  in a container;
- is followed through its output, and emptied with ``get_archive`` of /work/out when it ends
  (paths checked, sizes capped), then removed.

Only containers labelled ``forge.job`` are ever touched (sessions: ``forge.session``, see
:mod:`chipova_forge.sessions.manager`).
"""

from __future__ import annotations

import calendar
import io
import os
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

LABEL_RUN = "forge.job"
LABEL_PROJECT = "forge.project"
JOB_USER = "1000:1000"
# What a run may bring back: all its files together, and how many.
MAX_OUT_BYTES = int(os.environ.get("FORGE_MAX_OUTPUT_MB", "1024")) * 1024 * 1024
MAX_OUT_FILES = 20000


class DockerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    cpus: float = 2.0
    memory_mb: int = 2048
    pids: int = 1024
    timeout: int = 600
    # Pin the run to that many CPUs too: tools that size their parallelism by the CPUs they see
    # (nproc) would otherwise start one process per host CPU within the run's CPU and memory limits.
    pin_cpus: bool = False


_client: docker.DockerClient | None = None
_client_lock = threading.Lock()


def client() -> docker.DockerClient:
    global _client
    with _client_lock:
        if _client is None:
            try:
                c = docker.from_env(timeout=120)
                c.ping()
            except DockerException as e:
                raise DockerUnavailable(f"Docker is not reachable: {e}") from e
            _client = c
        return _client


def available() -> bool:
    try:
        client()
        return True
    except DockerUnavailable:
        return False


def container_name(run_id: str) -> str:
    return f"forge-{run_id}"


def image_info(ref: str) -> dict | None:
    """{"id", "digests", "created", "size"} of a local image, None when it isn't here."""
    try:
        img = client().images.get(ref)
    except (ImageNotFound, APIError):
        return None
    attrs = img.attrs
    return {
        "id": img.id,
        "digests": attrs.get("RepoDigests") or [],
        "created": attrs.get("Created", ""),
        "size": attrs.get("Size", 0),
        "arch": attrs.get("Architecture", ""),
    }


# ------------------------------------------------------------ the workspace tar


def _add(tar: tarfile.TarFile, name: str, data: bytes | None, mode: int, uid: int = 0) -> None:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = uid
    info.mtime = int(time.time())
    if data is None:
        info.type = tarfile.DIRTYPE
        info.mode = mode
        tar.addfile(info)
    else:
        info.size = len(data)
        info.mode = mode
        tar.addfile(info, io.BytesIO(data))


def workspace_tar(src_tar: bytes, job: dict[str, bytes], inputs: dict[str, Path]) -> bytes:
    """The workspace a job starts with: ``src_tar`` (a ``git archive`` with prefix ``src/``)
    under /work/src, read-only; ``job`` files (runner.py, job.json, scripts) under /work;
    ``inputs`` (files of other runs) under /work/in, read-only."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Root-owned and open to all, so the job (user 1000) can work in them (put_archive
        # doesn't keep owners).
        _add(tar, "work", None, 0o755)
        for d in ("work/out", "work/home", "work/tmp"):
            _add(tar, d, None, 0o1777)
        _add(tar, "work/src", None, 0o555)
        with tarfile.open(fileobj=io.BytesIO(src_tar), mode="r:") as src:
            for m in src.getmembers():
                name = m.name.lstrip("./")
                if not name.startswith("src/") or ".." in name.split("/"):
                    continue
                if m.isdir():
                    _add(tar, "work/" + name.rstrip("/"), None, 0o555)
                elif m.isfile():
                    data = src.extractfile(m).read()  # type: ignore[union-attr]
                    _add(tar, "work/" + name, data, 0o444)
        dirs = set()
        for rel, data in sorted(job.items()):
            parts = rel.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "work/" + "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    _add(tar, d, None, 0o555)
            _add(tar, "work/" + rel, data, 0o555 if rel.endswith((".sh", ".py")) else 0o444)
        if inputs:
            _add(tar, "work/in", None, 0o555)
            for rel, path in sorted(inputs.items()):
                parts = rel.split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    d = "work/in/" + "/".join(parts[:i])
                    if d not in dirs:
                        dirs.add(d)
                        _add(tar, d, None, 0o555)
                _add(tar, "work/in/" + rel, path.read_bytes(), 0o444)
    return buf.getvalue()


# ------------------------------------------------------------ the container


def _cpuset(c: docker.DockerClient, cpus: float) -> str:
    """The host's last ``cpus`` CPUs, as Docker's --cpuset-cpus wants them."""
    total = int(c.info().get("NCPU") or 1)
    n = max(1, min(total, int(cpus)))
    return f"{total - n}-{total - 1}"


def create(run_id: str, project_id: str, image: str, limits: Limits, workspace: bytes, env: dict[str, str] | None = None):
    """Makes the job's container with its workspace in it (not started yet)."""
    c = client()
    name = container_name(run_id)
    try:
        old = c.containers.get(name)
        old.remove(force=True)
    except NotFound:
        pass
    container = c.containers.create(
        image,
        command=["bash", "/work/job/run.sh"],
        name=name,
        user=JOB_USER,
        working_dir="/work",
        environment=env or {},
        labels={LABEL_RUN: run_id, LABEL_PROJECT: project_id},
        network_mode="none",
        # With no network the container's own name doesn't resolve; Java tools (Freerouting)
        # look it up at start.
        hostname="localhost",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        nano_cpus=int(limits.cpus * 1e9),
        cpuset_cpus=_cpuset(c, limits.cpus) if limits.pin_cpus else None,
        mem_limit=f"{limits.memory_mb}m",
        memswap_limit=f"{limits.memory_mb}m",
        pids_limit=limits.pids,
        detach=True,
        tty=False,
        stdin_open=False,
    )
    if not container.put_archive("/", workspace):
        container.remove(force=True)
        raise RuntimeError("cannot copy the workspace into the job's container")
    return container


def find(run_id: str):
    """The run's container, None when it is gone."""
    try:
        return client().containers.get(container_name(run_id))
    except (NotFound, APIError):
        return None


def follow(container, on_line: Callable[[str], None], stop: threading.Event) -> None:
    """Calls ``on_line`` with each line of the container's output (all of it from the start),
    until it ends or ``stop`` is set; blocking, for a thread."""
    pending = b""
    try:
        for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=True):
            if stop.is_set():
                return
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                on_line(line.decode("utf-8", errors="replace"))
    except (APIError, DockerException, OSError, ValueError):
        pass
    if pending:
        on_line(pending.decode("utf-8", errors="replace"))


def wait(container, timeout: float) -> int | None:
    """The container's exit code, or None when it still runs after ``timeout`` seconds."""
    try:
        result = container.wait(timeout=timeout)
        return int(result.get("StatusCode", -1))
    except Exception:  # noqa: BLE001 — requests' ReadTimeout and friends
        return None


def state(container) -> dict:
    try:
        container.reload()
        return container.attrs.get("State") or {}
    except (NotFound, APIError):
        return {}


def stop(container) -> None:
    try:
        container.kill()
    except (NotFound, APIError):
        pass


def remove(container) -> None:
    try:
        container.remove(force=True)
    except (NotFound, APIError):
        pass


def remove_stale_probes(older_than: float = 600) -> int:
    """Removes version-probe containers a backend stopped in the middle of a probe left behind
    (made, never started). Only old ones: another Forge on this Docker may be probing now."""
    try:
        found = client().containers.list(all=True, filters={"label": f"{LABEL_RUN}=probe"})
    except (DockerUnavailable, DockerException):
        return 0
    removed = 0
    for c in found:
        try:
            created = calendar.timegm(time.strptime(c.attrs["Created"][:19], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            continue
        if time.time() - created > older_than:
            remove(c)
            removed += 1
    return removed


def fetch_out(container, dest: Path) -> tuple[int, int]:
    """Copies the container's /work/out into ``dest``; returns (files, bytes). Paths that would
    leave ``dest``, links and special files are skipped; too much stops it (RuntimeError)."""
    stream, _ = container.get_archive("/work/out")
    reader = _StreamReader(stream)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    files = total = 0
    with tarfile.open(fileobj=reader, mode="r|") as tar:
        for m in tar:
            parts = m.name.split("/")
            if parts and parts[0] == "out":
                parts = parts[1:]
            if not parts or any(p in ("", ".", "..") for p in parts):
                continue
            target = (root / "/".join(parts)).resolve()
            if root not in target.parents:
                continue
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not m.isfile():
                continue  # links, devices: never
            files += 1
            total += m.size
            if files > MAX_OUT_FILES or total > MAX_OUT_BYTES:
                raise RuntimeError(f"the run wrote more than {MAX_OUT_FILES} files or {MAX_OUT_BYTES // 2**20} MB")
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            with open(target, "wb") as fh:
                while chunk := src.read(1 << 20):  # type: ignore[union-attr]
                    fh.write(chunk)
    return files, total


class _StreamReader(io.RawIOBase):
    """A file-like view of docker-py's chunk iterator (for tarfile's stream mode)."""

    def __init__(self, chunks: Iterator[bytes]):
        self._chunks = iter(chunks)
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # noqa: ANN001
        while not self._buf:
            try:
                self._buf = next(self._chunks)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def run_quick(image: str, argv: list[str], timeout: float = 60) -> tuple[int, str]:
    """Runs a short command in a throwaway container (no network): (exit code, output). For
    reading tool versions."""
    c = client()
    container = c.containers.create(image, command=argv, user=JOB_USER, network_mode="none", detach=True, labels={LABEL_RUN: "probe"})
    try:
        container.start()
        code = wait(container, timeout)
        out = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        return (code if code is not None else -1), out
    finally:
        remove(container)
