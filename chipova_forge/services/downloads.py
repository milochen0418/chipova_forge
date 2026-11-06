"""Download links made for one person: ``/_forge/dl/<project>/<kind>/<id>/<name>?u=..&x=..&s=..``.

The page can't send an API token with a plain link, and a guest has no sign-in cookie, so a link
to a build's output, a session's evidence or a release package is signed by Forge for the person
who may download it (``x`` is when it stops working). The route checks the signature, then the
person's access again, then serves the file.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote, urlencode

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from chipova_forge import ids, paths
from chipova_forge.domain import roles
from chipova_forge.jobs import builds
from chipova_forge.services import audit, projects, releases
from chipova_forge.services.actors import Actor
from chipova_forge.sessions import service

ROUTE = "/_forge/dl/{project_id}/{kind}/{owner}/{name:path}"
TTL = 6 * 3600
KINDS = ("build", "session", "release")


def _sig(project_id: str, kind: str, owner: str, name: str, user: str, until: int) -> str:
    msg = f"dl|{project_id}|{kind}|{owner}|{name}|{user}|{until}".encode()
    return hmac.new(paths.secret(), msg, hashlib.sha256).hexdigest()[:32]


def link(project_id: str, kind: str, owner: str, name: str, actor: Actor) -> str:
    until = int(time.time()) + TTL
    q = urlencode({"u": actor.user, "x": until, "s": _sig(project_id, kind, owner, name, actor.user, until)})
    return f"/_forge/dl/{project_id}/{kind}/{owner}/{quote(name)}?{q}"


def _file(project_id: str, kind: str, owner: str, name: str):  # noqa: ANN202
    if kind == "build":
        b = builds.get(owner)
        if b is None or b["project_id"] != project_id:
            return None
        return builds.artifact_path(b, name)
    if kind == "session":
        s = service.get(owner)
        if s is None or s["project_id"] != project_id:
            return None
        return service.evidence_file(project_id, owner, name)
    if kind == "release":
        r = releases.get(owner)
        if r is None or r["project_id"] != project_id:
            return None
        return releases.package_path(r)
    return None


async def serve(request: Request) -> Response:
    pid = request.path_params.get("project_id", "")
    kind = request.path_params.get("kind", "")
    owner = request.path_params.get("owner", "")
    name = request.path_params.get("name", "")
    user = request.query_params.get("u", "")
    try:
        until = int(request.query_params.get("x", "0"))
    except ValueError:
        until = 0
    if not ids.is_project(pid) or kind not in KINDS or until < time.time():
        return JSONResponse({"error": "this link has expired: open the page again for a new one"}, status_code=403)
    if not hmac.compare_digest(request.query_params.get("s", ""), _sig(pid, kind, owner, name, user, until)):
        return JSONResponse({"error": "not a link Forge made"}, status_code=403)
    actor = Actor(user, user, email=user if "@" in user else "")
    try:
        projects.require(pid, actor, roles.DOWNLOAD, owner)
        f = _file(pid, kind, owner, name)
    except (projects.Denied, projects.NotFound, ValueError):
        return JSONResponse({"error": "you may not download this"}, status_code=403)
    if f is None or not f.is_file():
        return JSONResponse({"error": "there is no such file"}, status_code=404)
    audit.record("download", actor, pid, f"{kind}/{owner}/{name}")
    return FileResponse(f, filename=f.name if kind != "release" else f"{owner}.zip")


def register_routes(app) -> None:  # noqa: ANN001
    api = app._api
    if not any(getattr(r, "path", None) == ROUTE for r in api.routes):
        api.add_route(ROUTE, serve, methods=["GET"])
