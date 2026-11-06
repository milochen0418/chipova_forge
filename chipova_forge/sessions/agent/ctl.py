#!/usr/bin/env python3
"""The app's line to a session's agent: runs in the session container (``docker exec -i``) and
passes JSON lines between its stdin/stdout and the agent's Unix socket, both ways, until either
side closes. One request as arguments (``ctl.py '{"op": "status"}'``) prints its answer and
ends."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

SOCKET_PATH = os.environ.get("FORGE_AGENT_SOCKET", "/work/run/agent.sock")


def connect(timeout: float = 30.0) -> socket.socket:
    end = time.time() + timeout
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCKET_PATH)
            return s
        except OSError:
            if time.time() > end:
                raise
            time.sleep(0.2)


def main() -> int:
    try:
        sock = connect()
    except OSError as e:
        sys.stdout.write('{"ok": false, "error": "the session agent is not answering: %s"}\n' % str(e).replace('"', "'"))
        sys.stdout.flush()
        return 1
    if len(sys.argv) > 1:
        sock.sendall(sys.argv[1].encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        return 0

    def to_agent() -> None:
        for line in sys.stdin.buffer:
            try:
                sock.sendall(line)
            except OSError:
                break
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=to_agent, daemon=True).start()
    out = sys.stdout.buffer
    while True:
        try:
            chunk = sock.recv(65536)
        except OSError:
            break
        if not chunk:
            break
        out.write(chunk)
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
