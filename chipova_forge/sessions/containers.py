"""Session containers: one long-lived container per simulated board, and the app's line to it.

A session container runs the simulation image with the session agent
(:mod:`chipova_forge.sessions.agent.session_agent`) as its command:

- as user 1000, with **no network** (Renode's monitor, the UART and the agent's socket are all
  inside it), all Linux capabilities dropped, no new privileges, and limits on CPUs, memory and
  processes;
- its inputs are copied in before it starts (``put_archive``): ``/work/session`` (the board: the
  platform files, the chip library, the firmware, ``session.json``, ``session.resc``) and
  ``/work/agent`` (the agent), read-only to it; ``/work/run`` and ``/work/out`` to write in.
  Nothing of the host is mounted, so the same code works when the app itself runs in a
  container;
- labelled ``forge.session=<id>`` and ``forge.project=<id>``: a restarted backend finds its
  sessions again and reconnects (:func:`running`).

The app talks to the agent through :class:`Channel`: a ``docker exec -i`` of ``ctl.py``, one
JSON object per line each way, kept open while the session lives. It needs no port, no network
and no shared folder: only the Docker socket the app already has.
"""

from __future__ import annotations

import io
import itertools
import json
import logging
import struct
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from docker.errors import APIError, DockerException, NotFound

from chipova_forge.jobs import docker_runner
from chipova_forge.jobs.docker_runner import DockerUnavailable, client

logger = logging.getLogger(__name__)

LABEL_SESSION = "forge.session"
LABEL_PROJECT = "forge.project"
USER = "1000:1000"
AGENT_DIR = Path(__file__).resolve().parent / "agent"


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    cpus: float = 2.0
    memory_mb: int = 1536
    pids: int = 512


def container_name(session_id: str) -> str:
    return f"forge-{session_id}"


def _add(tar: tarfile.TarFile, name: str, data: bytes | None, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = 0
    info.mtime = int(time.time())
    info.mode = mode
    if data is None:
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
    else:
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def workspace_tar(session_files: dict[str, bytes]) -> bytes:
    """``/work`` as a session starts: the session's files (read-only), the agent, and the
    folders it writes in."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        _add(tar, "work", None, 0o755)
        for d in ("work/run", "work/out", "work/home"):
            _add(tar, d, None, 0o1777)
        _add(tar, "work/agent", None, 0o555)
        for f in sorted(AGENT_DIR.glob("*.py")):
            _add(tar, f"work/agent/{f.name}", f.read_bytes(), 0o555)
        _add(tar, "work/session", None, 0o555)
        dirs: set[str] = set()
        for rel, data in sorted(session_files.items()):
            parts = rel.split("/")
            if not rel or rel.startswith("/") or ".." in parts:
                raise SessionError(f"not a session file path: {rel!r}")
            for i in range(1, len(parts)):
                d = "work/session/" + "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    _add(tar, d, None, 0o555)
            mode = 0o555 if rel.endswith((".so", ".py", ".sh")) else 0o444
            _add(tar, "work/session/" + rel, data, mode)
    return buf.getvalue()


def start(session_id: str, project_id: str, image: str, session_files: dict[str, bytes], limits: Limits = Limits()):
    """Makes and starts the session's container."""
    c = client()
    name = container_name(session_id)
    try:
        c.containers.get(name).remove(force=True)
    except NotFound:
        pass
    container = c.containers.create(
        image,
        command=["python3", "/work/agent/session_agent.py"],
        name=name,
        user=USER,
        working_dir="/work",
        environment={"HOME": "/work/home", "PYTHONDONTWRITEBYTECODE": "1"},
        labels={LABEL_SESSION: session_id, LABEL_PROJECT: project_id},
        network_mode="none",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        nano_cpus=int(limits.cpus * 1e9),
        mem_limit=f"{limits.memory_mb}m",
        memswap_limit=f"{limits.memory_mb}m",
        pids_limit=limits.pids,
        detach=True,
        tty=False,
        stdin_open=False,
    )
    if not container.put_archive("/", workspace_tar(session_files)):
        container.remove(force=True)
        raise SessionError("cannot copy the board into the session's container")
    container.start()
    return container


def find(session_id: str):
    try:
        return client().containers.get(container_name(session_id))
    except (NotFound, APIError, DockerUnavailable):
        return None


def running() -> dict[str, Any]:
    """The session containers that run now, by session id."""
    try:
        found = client().containers.list(filters={"label": LABEL_SESSION})
    except (DockerUnavailable, DockerException):
        return {}
    return {c.labels.get(LABEL_SESSION, ""): c for c in found if c.labels.get(LABEL_SESSION)}


def all_containers() -> dict[str, Any]:
    """Every session container, running or not, by session id."""
    try:
        found = client().containers.list(all=True, filters={"label": LABEL_SESSION})
    except (DockerUnavailable, DockerException):
        return {}
    return {c.labels.get(LABEL_SESSION, ""): c for c in found if c.labels.get(LABEL_SESSION)}


def remove(session_id: str) -> None:
    c = find(session_id)
    if c is not None:
        docker_runner.remove(c)


def logs(session_id: str, tail: int = 200) -> str:
    c = find(session_id)
    if c is None:
        return ""
    try:
        return c.logs(stdout=True, stderr=True, tail=tail).decode("utf-8", errors="replace")
    except (APIError, DockerException):
        return ""


def fetch_out(container, dest: Path) -> tuple[int, int]:
    """Copies the session's /work/out (events, logs, waveforms) into ``dest``."""
    stream, _ = container.get_archive("/work/out")
    reader = docker_runner._StreamReader(stream)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    files = total = 0
    with tarfile.open(fileobj=reader, mode="r|") as tar:
        for m in tar:
            parts = m.name.split("/")
            if parts and parts[0] == "out":
                parts = parts[1:]
            if not parts or any(p in ("", ".", "..") for p in parts) or not m.isfile():
                continue
            target = (root / "/".join(parts)).resolve()
            if root not in target.parents:
                continue
            files += 1
            total += m.size
            if total > docker_runner.MAX_OUT_BYTES:
                raise SessionError("the session wrote too much to copy out")
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            with open(target, "wb") as fh:
                while chunk := src.read(1 << 20):  # type: ignore[union-attr]
                    fh.write(chunk)
    return files, total


def read_file(container, path: str, limit: int = 64 << 20) -> bytes:
    """One file of the container (e.g. /work/out/chip.vcd)."""
    stream, stat = container.get_archive(path)
    if int(stat.get("size", 0)) > limit:
        raise SessionError(f"{path} is larger than {limit >> 20} MB")
    reader = docker_runner._StreamReader(stream)
    with tarfile.open(fileobj=reader, mode="r|") as tar:
        for m in tar:
            if m.isfile():
                return tar.extractfile(m).read()  # type: ignore[union-attr]
    raise SessionError(f"{path} isn't a file")


# ------------------------------------------------------------------ the line to the agent


class Channel:
    """``docker exec -i <session> python3 /work/agent/ctl.py``: requests and their answers, and
    the agent's events after ``follow``, as JSON lines over the exec's socket."""

    def __init__(self, session_id: str, on_event: Callable[[dict], None] | None = None) -> None:
        self.session_id = session_id
        self.on_event = on_event
        self._ids = itertools.count(1)
        self._waiting: dict[int, tuple[threading.Event, list]] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._sock = None
        self._raw = None
        self.closed = threading.Event()

    def open(self, timeout: float = 30.0) -> None:
        container = find(self.session_id)
        if container is None:
            raise SessionError("the session's container is gone")
        api = client().api
        end = time.time() + timeout
        while True:
            try:
                exec_id = api.exec_create(container.id, ["python3", "/work/agent/ctl.py"], stdin=True, stdout=True, stderr=False, tty=False, user=USER)
                sock = api.exec_start(exec_id, socket=True)
                break
            except APIError as e:
                if time.time() > end:
                    raise SessionError(f"cannot reach the session's agent: {e}") from e
                time.sleep(0.5)
        self._sock = sock
        self._raw = getattr(sock, "_sock", sock)
        self.closed.clear()
        threading.Thread(target=self._read_loop, name=f"forge-channel-{self.session_id}", daemon=True).start()

    def _read_loop(self) -> None:
        raw = self._raw
        buf = b""
        pending = b""
        try:
            while True:
                chunk = raw.recv(65536)
                if not chunk:
                    break
                buf += chunk
                # docker's multiplexed stream: [type, 0, 0, 0, size (4 bytes, big-endian)] + data
                while len(buf) >= 8:
                    size = struct.unpack(">I", buf[4:8])[0]
                    if len(buf) < 8 + size:
                        break
                    kind, data, buf = buf[0], buf[8 : 8 + size], buf[8 + size :]
                    if kind != 1:
                        continue
                    pending += data
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        self._dispatch(line)
        except OSError:
            pass
        finally:
            self.closed.set()
            with self._lock:
                for ev, box in self._waiting.values():
                    box.append({"ok": False, "error": "the line to the session closed"})
                    ev.set()
                self._waiting.clear()

    def _dispatch(self, line: bytes) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            logger.warning("Session %s sent a line that isn't JSON: %r", self.session_id, line[:200])
            return
        if "event" in msg:
            if self.on_event is not None:
                try:
                    self.on_event(msg["event"])
                except Exception:  # noqa: BLE001
                    logger.exception("An event handler failed")
            return
        rid = msg.get("id")
        with self._lock:
            waiter = self._waiting.pop(rid, None)
        if waiter is not None:
            waiter[1].append(msg)
            waiter[0].set()

    def request(self, op: str, timeout: float = 30.0, **args) -> dict:
        """Sends one request and waits for its answer: ``{"ok": True, ...}`` or raises."""
        if self._raw is None or self.closed.is_set():
            raise SessionError("the line to the session is closed")
        rid = next(self._ids)
        ev, box = threading.Event(), []
        with self._lock:
            self._waiting[rid] = (ev, box)
        data = (json.dumps({"id": rid, "op": op, **args}) + "\n").encode()
        try:
            with self._send_lock:
                self._raw.sendall(data)
        except OSError as e:
            with self._lock:
                self._waiting.pop(rid, None)
            raise SessionError(f"cannot write to the session: {e}") from e
        if not ev.wait(timeout):
            with self._lock:
                self._waiting.pop(rid, None)
            raise SessionError(f"the session didn't answer {op!r} in {timeout:g} s")
        msg = box[0]
        if not msg.get("ok"):
            raise SessionError(str(msg.get("error") or "the session refused"))
        return msg

    def close(self) -> None:
        raw = self._raw
        self._raw = None
        if raw is not None:
            try:
                raw.shutdown(2)
            except OSError:
                pass
            try:
                raw.close()
            except OSError:
                pass
