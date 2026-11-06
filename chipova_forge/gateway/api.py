"""Chipova Forge's REST API: the Virtual Device Gateway, and the rest of the project for scripts,
CI and the Python SDK (blueprint 10).

Every endpoint is a thin layer over the services the pages use, with the same access rules and
audit log. Who is calling: ``Authorization: Bearer <token>`` (a personal token made on a
project's API page; an agent's token makes its calls an agent's), or the platform's sign-in
cookie.

    GET  /api/projects                              POST /api/projects
    GET  /api/projects/{id}                         GET  /api/projects/{id}/contract
    GET  /api/projects/{id}/registers               GET|PUT /api/projects/{id}/files/{path}
    POST /api/projects/{id}/builds                  GET  /api/builds/{id}
    GET  /api/builds/{id}/artifacts/{name}
    POST /api/simulations                           GET  /api/simulations/{id}
    POST /api/simulations/{id}/pause|resume|reset|stop|run_for
    GET  /api/simulations/{id}/devices              GET  /api/simulations/{id}/events  (SSE)
    GET  /api/simulations/{id}/waveform             POST /api/simulations/{id}/environment
    GET|POST /api/simulations/{id}/registers/{name or offset}
    POST /api/devices/{id}/commands                 GET  /api/devices/{id}/state
    POST /api/projects/{id}/tests                   GET  /api/tests/{id}
    POST /api/projects/{id}/releases                GET  /api/releases/{id}   GET /api/releases/{id}/package

A device's id is its session's id (one device per board in this version).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from chipova_forge import ids, paths
from chipova_forge.contract import chip_package, contract as hdc
from chipova_forge.domain import roles
from chipova_forge.gateway import device as device_mod
from chipova_forge.jobs import builds
from chipova_forge.services import projects, releases, systemtests
from chipova_forge.services.actors import Actor, agent
from chipova_forge.sessions import service
from chipova_forge.system import board as board_mod

TOKEN_DAYS = 30


# ------------------------------------------------------------------ tokens


def make_token(actor: Actor, days: int = TOKEN_DAYS) -> str:
    """A personal API token: calls made with it are this person's (or this agent's)."""
    payload = json.dumps({"u": actor.user, "n": actor.name, "k": actor.kind, "o": actor.on_behalf_of,
                          "x": int(time.time()) + days * 86400}, separators=(",", ":"))
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(paths.secret(), b"api|" + body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"cfg1.{body}.{sig}"


def read_token(token: str) -> Actor | None:
    try:
        prefix, body, sig = token.split(".")
    except ValueError:
        return None
    want = base64.urlsafe_b64encode(hmac.new(paths.secret(), b"api|" + body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    if prefix != "cfg1" or not hmac.compare_digest(sig, want):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return None
    if float(data.get("x", 0)) < time.time():
        return None
    if data.get("k") == "agent":
        return agent(str(data.get("u", "")).removeprefix("agent:"), on_behalf_of=str(data.get("o", "")))
    user = str(data.get("u", ""))
    return Actor(user, str(data.get("n", "")), email=user if "@" in user else "")


def _cookie_actor(request: Request) -> Actor | None:
    secret = os.environ.get("DDNS_AUTH_SECRET", "")
    token = request.cookies.get(os.environ.get("DDNS_AUTH_COOKIE", "ddns_auth"), "")
    if not secret or not token:
        return None
    try:
        import jwt  # noqa: PLC0415

        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:  # noqa: BLE001
        return None
    email = str(claims.get("email", "")).lower()
    return Actor(email, str(claims.get("name", "") or email), email=email) if email else None


def actor_of(request: Request) -> Actor | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return read_token(token.strip())
    return _cookie_actor(request)


# ------------------------------------------------------------------ schemas


class CreateProject(BaseModel):
    title: str = ""
    template: str = "smart_sensor"
    chip: str = ""  # an example chip; "" = the template's own


class SaveFile(BaseModel):
    text: str
    base_version: int | None = None


class BuildIn(BaseModel):
    kind: str
    wait: bool = False


class StartSimulation(BaseModel):
    project_id: str


class RunFor(BaseModel):
    seconds: float


class Environment(BaseModel):
    sensor: str
    setting: str
    value: float


class RegisterWrite(BaseModel):
    value: int


class CommandIn(BaseModel):
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 5.0


class TestIn(BaseModel):
    test: str
    wait: bool = True


class ReleaseIn(BaseModel):
    title: str = ""


# ------------------------------------------------------------------ the wrapper


def endpoint(fn: Callable[..., Awaitable[Any]], body: type[BaseModel] | None = None):
    async def handle(request: Request) -> Response:
        actor = actor_of(request)
        if actor is None:
            return JSONResponse({"error": "sign in, or send Authorization: Bearer <token> (made on the project's API page)"}, status_code=401)
        try:
            args = []
            if body is not None:
                raw = await request.body()
                args.append(body.model_validate_json(raw or b"{}"))
            result = await fn(request, actor, *args)
        except ValidationError as e:
            return JSONResponse({"error": "invalid request", "detail": json.loads(e.json())}, status_code=400)
        except projects.Denied as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        except projects.NotFound as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except projects.Conflict as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except (service.SessionError, builds.BuildError, systemtests.TestError, releases.ReleaseError) as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except (ValueError, device_mod.DeviceError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if isinstance(result, Response):
            return result
        return Response(json.dumps(result, default=str), media_type="application/json")

    return handle


def _pid(request: Request) -> str:
    return request.path_params.get("project_id", "")


def _session(request: Request, actor: Actor, action: str = roles.VIEW) -> dict[str, Any]:
    sid = request.path_params.get("session_id", "")
    s = service.get(sid)
    if s is None:
        raise projects.NotFound(f"there is no simulation {sid!r}")
    projects.require(s["project_id"], actor, action, sid)
    return s


# ------------------------------------------------------------------ projects


async def list_projects(request: Request, actor: Actor):
    return {"projects": [{k: p[k] for k in ("id", "title", "template", "chip", "updated_at")} for p in projects.visible_to(actor.user)]}


async def create_project(request: Request, actor: Actor, body: CreateProject):
    tpl = projects.product_templates().get(body.template)
    if tpl is None:
        raise ValueError(f"there is no product template {body.template!r}")
    chip = chip_package.from_example(body.chip or str(tpl.get("chip")))
    return await asyncio.to_thread(projects.create, body.title, body.template, chip, actor)


async def get_project(request: Request, actor: Actor):
    p, a = projects.require(_pid(request), actor, roles.VIEW)
    active = service.active_of(p["id"])
    return {"project": p, "role": a.label, "files": projects.files(p["id"]),
            "builds": {k: builds.latest(p["id"], k) for k in builds.KINDS},
            "simulation": active, "tests": systemtests.latest_by_scenario(p["id"])}


async def get_contract(request: Request, actor: Actor):
    projects.require(_pid(request), actor, roles.VIEW)
    c = projects.contract(_pid(request))
    b = board_mod.from_yaml(projects.read(_pid(request), "system.yaml"))
    return {"contract": c.model_dump(mode="json"), "issues": [i.model_dump() for i in hdc.check(c)],
            "board_issues": [i.model_dump() for i in board_mod.check(b, c)]}


async def get_registers(request: Request, actor: Actor):
    projects.require(_pid(request), actor, roles.VIEW)
    m = await asyncio.to_thread(builds.register_map, _pid(request))
    if m is None:
        raise projects.NotFound("the chip has no register map")
    return m.to_dict()


async def get_file(request: Request, actor: Actor):
    projects.require(_pid(request), actor, roles.VIEW)
    path = ids.clean_path(request.path_params.get("path", ""))
    info = projects.file_info(_pid(request), path)
    return {"path": path, "text": projects.read(_pid(request), path), "version": info["version"] if info else 0}


async def put_file(request: Request, actor: Actor, body: SaveFile):
    path = ids.clean_path(request.path_params.get("path", ""))
    v = await asyncio.to_thread(projects.write, _pid(request), path, body.text, actor, body.base_version)
    return {"path": path, "version": v}


# ------------------------------------------------------------------ builds


async def post_build(request: Request, actor: Actor, body: BuildIn):
    via = "agent" if actor.is_agent else "api"
    return await builds.request(_pid(request), body.kind, actor, via, wait=body.wait)


async def get_build(request: Request, actor: Actor):
    b = builds.get(request.path_params.get("build_id", ""))
    if b is None:
        raise projects.NotFound("there is no such build")
    projects.require(b["project_id"], actor, roles.VIEW)
    return {"build": b, "artifacts": builds.artifacts(b["id"]), "manifest": builds.manifest(b), "log_tail": builds.log_text(b, 8000)}


async def get_artifact(request: Request, actor: Actor):
    b = builds.get(request.path_params.get("build_id", ""))
    if b is None:
        raise projects.NotFound("there is no such build")
    projects.require(b["project_id"], actor, roles.DOWNLOAD)
    f = builds.artifact_path(b, request.path_params.get("name", ""))
    if not f.is_file():
        raise projects.NotFound("there is no such file")
    return Response(f.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{f.name}"'})


# ------------------------------------------------------------------ simulations (sessions)


async def start_simulation(request: Request, actor: Actor, body: StartSimulation):
    return await service.start(body.project_id, actor, via="agent" if actor.is_agent else "api")


async def get_simulation(request: Request, actor: Actor):
    s = _session(request, actor)
    lv = service.live(s["id"])
    st: dict[str, Any] = {}
    if lv is not None:
        try:
            st = await asyncio.to_thread(service.request, s["id"], actor, "status")
        except service.SessionError:
            st = {}
    return {"simulation": s, "state": (lv.state if lv else s["status"]), "status": st}


def _control(op: str):
    async def handler(request: Request, actor: Actor):
        s = _session(request, actor)
        if op == "stop":
            return await service.stop(s["id"], actor, "stopped through the API")
        name = {"resume": "start"}.get(op, op)
        return await asyncio.to_thread(service.request, s["id"], actor, name)
    return handler


async def run_for(request: Request, actor: Actor, body: RunFor):
    s = _session(request, actor)
    return await asyncio.to_thread(service.request, s["id"], actor, "run_for", 120.0, True, seconds=body.seconds)


async def get_devices(request: Request, actor: Actor):
    s = _session(request, actor)
    lv = service.live(s["id"])
    if lv is None or lv.profile is None:
        raise service.SessionError("the simulation isn't running" if lv is None else (lv.profile_error or "no device protocol"))
    return {"devices": [{"id": s["id"], "name": lv.profile.name, "profile": lv.profile.describe(),
                         "uart": lv.profile.uart, "backend": "virtual"}]}


async def get_events(request: Request, actor: Actor):
    s = _session(request, actor)
    try:
        after = int(request.query_params.get("after", "0") or 0)
    except ValueError:
        after = 0
    if request.query_params.get("format") == "json" or s["status"] not in service.ACTIVE:
        return {"events": service.events_after(s["id"], after, 1000)}
    q = await service.subscribe(s["id"])

    async def stream():
        try:
            last = after
            for e in service.events_after(s["id"], after, 2000):
                last = max(last, int(e.get("seq", 0)))
                yield f"id: {e.get('seq')}\nevent: {e.get('kind')}\ndata: {json.dumps(e)}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    e = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if int(e.get("seq", 0)) <= last:
                    continue
                last = int(e.get("seq", 0))
                yield f"id: {e.get('seq')}\nevent: {e.get('kind')}\ndata: {json.dumps(e)}\n\n"
                if e.get("kind") == "state" and e.get("state") in ("STOPPED", "FAILED", "EXPIRED"):
                    return
        finally:
            service.unsubscribe(s["id"], q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def get_waveform(request: Request, actor: Actor):
    s = _session(request, actor, roles.DOWNLOAD)
    data = await asyncio.to_thread(service.waveform, s["id"], actor)
    return Response(data, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{s["id"]}-chip.vcd"'})


async def post_environment(request: Request, actor: Actor, body: Environment):
    s = _session(request, actor)
    return await asyncio.to_thread(service.set_environment, s["id"], actor, body.sensor, body.setting, body.value)


def _register_offset(project_id: str, ref: str) -> tuple[int, str]:
    m = builds.register_map(project_id)
    r = m.by_name(ref) if m else None
    if r is not None:
        return r.offset, r.name
    try:
        off = int(ref, 0)
    except ValueError as e:
        raise projects.NotFound(f"the chip has no register {ref!r}") from e
    name = (m.by_offset(off).name if m and m.by_offset(off) else "")
    return off, name


async def get_register(request: Request, actor: Actor):
    s = _session(request, actor)
    off, name = _register_offset(s["project_id"], request.path_params.get("ref", ""))
    out = await asyncio.to_thread(service.request, s["id"], actor, "read_reg", offset=off)
    return {"register": name, "offset": off, "value": out["value"]}


async def post_register(request: Request, actor: Actor, body: RegisterWrite):
    s = _session(request, actor)
    off, name = _register_offset(s["project_id"], request.path_params.get("ref", ""))
    await asyncio.to_thread(service.request, s["id"], actor, "write_reg", offset=off, value=body.value)
    return {"register": name, "offset": off, "value": body.value}


async def post_command(request: Request, actor: Actor, body: CommandIn):
    s = _session(request, actor)
    return await asyncio.to_thread(service.device_command, s["id"], actor, body.command, body.args, min(30.0, body.timeout))


async def get_device_state(request: Request, actor: Actor):
    s = _session(request, actor)
    lv = service.live(s["id"])
    if lv is None:
        return {"state": s["status"], "leds": {}, "sensors": {}, "events": []}
    with lv.cond:
        recent = [e for e in lv.events if e.get("device_event")][-20:]
    return {"state": lv.state, "vt": lv.vt, "leds": dict(lv.leds), "sensors": dict(lv.sensors),
            "events": [{"event": e["device_event"], "fields": e.get("fields", {}), "vt": e.get("vt"), "seq": e.get("seq")} for e in recent]}


# ------------------------------------------------------------------ tests and releases


async def post_test(request: Request, actor: Actor, body: TestIn):
    via = "agent" if actor.is_agent else "api"
    if body.wait:
        return await systemtests.run(_pid(request), body.test, actor, via)
    task = asyncio.create_task(systemtests.run(_pid(request), body.test, actor, via))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"started": True}


_background: set[asyncio.Task] = set()


async def get_test(request: Request, actor: Actor):
    t = systemtests.get(request.path_params.get("test_id", ""))
    if t is None:
        raise projects.NotFound("there is no such test run")
    projects.require(t["project_id"], actor, roles.VIEW)
    return t


async def post_release(request: Request, actor: Actor, body: ReleaseIn):
    return await asyncio.to_thread(releases.create, _pid(request), body.title, actor)


async def get_release(request: Request, actor: Actor):
    r = releases.get(request.path_params.get("release_id", ""))
    if r is None:
        raise projects.NotFound("there is no such release")
    projects.require(r["project_id"], actor, roles.VIEW)
    return r


async def get_release_package(request: Request, actor: Actor):
    r = releases.get(request.path_params.get("release_id", ""))
    if r is None:
        raise projects.NotFound("there is no such release")
    projects.require(r["project_id"], actor, roles.DOWNLOAD)
    f = releases.package_path(r)
    if not f.is_file():
        raise projects.NotFound("the release has no package yet")
    return Response(f.read_bytes(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{r["id"]}.zip"'})


ROUTES = [
    ("/api/projects", "GET", endpoint(list_projects)),
    ("/api/projects", "POST", endpoint(create_project, CreateProject)),
    ("/api/projects/{project_id}", "GET", endpoint(get_project)),
    ("/api/projects/{project_id}/contract", "GET", endpoint(get_contract)),
    ("/api/projects/{project_id}/registers", "GET", endpoint(get_registers)),
    ("/api/projects/{project_id}/files/{path:path}", "GET", endpoint(get_file)),
    ("/api/projects/{project_id}/files/{path:path}", "PUT", endpoint(put_file, SaveFile)),
    ("/api/projects/{project_id}/builds", "POST", endpoint(post_build, BuildIn)),
    ("/api/builds/{build_id}", "GET", endpoint(get_build)),
    ("/api/builds/{build_id}/artifacts/{name:path}", "GET", endpoint(get_artifact)),
    ("/api/simulations", "POST", endpoint(start_simulation, StartSimulation)),
    ("/api/simulations/{session_id}", "GET", endpoint(get_simulation)),
    ("/api/simulations/{session_id}/pause", "POST", endpoint(_control("pause"))),
    ("/api/simulations/{session_id}/resume", "POST", endpoint(_control("resume"))),
    ("/api/simulations/{session_id}/reset", "POST", endpoint(_control("reset"))),
    ("/api/simulations/{session_id}/stop", "POST", endpoint(_control("stop"))),
    ("/api/simulations/{session_id}/run_for", "POST", endpoint(run_for, RunFor)),
    ("/api/simulations/{session_id}/devices", "GET", endpoint(get_devices)),
    ("/api/simulations/{session_id}/events", "GET", endpoint(get_events)),
    ("/api/simulations/{session_id}/waveform", "GET", endpoint(get_waveform)),
    ("/api/simulations/{session_id}/environment", "POST", endpoint(post_environment, Environment)),
    ("/api/simulations/{session_id}/registers/{ref}", "GET", endpoint(get_register)),
    ("/api/simulations/{session_id}/registers/{ref}", "POST", endpoint(post_register, RegisterWrite)),
    ("/api/devices/{session_id}/commands", "POST", endpoint(post_command, CommandIn)),
    ("/api/devices/{session_id}/state", "GET", endpoint(get_device_state)),
    ("/api/projects/{project_id}/tests", "POST", endpoint(post_test, TestIn)),
    ("/api/tests/{test_id}", "GET", endpoint(get_test)),
    ("/api/projects/{project_id}/releases", "POST", endpoint(post_release, ReleaseIn)),
    ("/api/releases/{release_id}", "GET", endpoint(get_release)),
    ("/api/releases/{release_id}/package", "GET", endpoint(get_release_package)),
]


def register_routes(app) -> None:  # noqa: ANN001 - Reflex App
    api = app._api
    have = {(getattr(r, "path", None), m) for r in api.routes for m in (getattr(r, "methods", None) or [])}
    for path, method, handler in ROUTES:
        if (path, method) not in have:
            api.add_route(path, handler, methods=[method])
