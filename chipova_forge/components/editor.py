"""The code editor (Monaco), served by Forge itself, never from a CDN, so editing works on a
network without internet (Chipova's way: the npm package Reflex installs, served by the backend
under ``/_forge/monaco/<version>/vs``; the page points Monaco's loader there before the first
editor mounts)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import reflex as rx
import reflex_monaco  # noqa: F401
from reflex_monaco.monaco import MonacoEditor
from reflex.constants import Dirs
from reflex.utils.imports import ImportVar
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

logger = logging.getLogger(__name__)

MONACO_VERSION = "0.55.1"
MONACO_PATH = "/_forge/monaco/{version}/{path:path}"
_TYPES = {".js": "text/javascript", ".css": "text/css", ".ttf": "font/ttf", ".json": "application/json", ".map": "application/json"}


class EditorLibraries(rx.Fragment):
    """Renders nothing: its module code points Monaco's loader at Forge's own copy."""

    lib_dependencies: list[str] = [f"monaco-editor@{MONACO_VERSION}"]

    def add_imports(self):  # noqa: ANN201
        return {
            "@monaco-editor/react": ImportVar(tag="loader"),
            f"$/{Dirs.STATE_PATH}": "getBackendURL",
            "$/env.json": ImportVar(tag="env", is_default=True),
        }

    def add_custom_code(self) -> list[str]:
        return [
            "if (typeof window !== 'undefined') {"
            f" loader.config({{ paths: {{ vs: new URL('/_forge/monaco/{MONACO_VERSION}/vs', getBackendURL(env.UPLOAD)).href }} }});"
            " loader.init().then((m) => { window.monaco = m; }).catch(() => {});"
            " }"
        ]


editor_libraries = EditorLibraries.create


def _root() -> Path:
    from reflex.utils.prerequisites import get_web_dir  # noqa: PLC0415

    return Path(get_web_dir()) / "node_modules" / "monaco-editor" / "min"


async def monaco_file(request: Request) -> Response:
    root = _root().resolve()
    target = (root / request.path_params.get("path", "")).resolve()
    if root not in target.parents or not target.is_file():
        return JSONResponse({"error": "no such file"}, status_code=404)
    return FileResponse(target, media_type=_TYPES.get(target.suffix, "application/octet-stream"),
                        headers={"Cache-Control": "public, max-age=31536000, immutable", "Access-Control-Allow-Origin": "*"})


def register_routes(app) -> None:  # noqa: ANN001 - Reflex App
    api = app._api
    if not any(getattr(r, "path", None) == MONACO_PATH for r in api.routes):
        api.add_route(MONACO_PATH, monaco_file, methods=["GET"])


class ForgeMonaco(MonacoEditor):
    """Monaco with its options (read-only, minimap, font size)."""

    options: rx.Var[dict]


def code_editor(key: rx.Var, text: rx.Var, language: rx.Var, readonly: rx.Var, on_change) -> rx.Component:  # noqa: ANN001
    """Monaco, uncontrolled: it starts from ``text`` (again whenever ``key`` changes) and reports
    each change."""
    return ForgeMonaco.create(
        key=key,
        default_value=text,
        language=language,
        theme="vs-dark",
        height="100%",
        width="100%",
        on_change=on_change,
        options={"readOnly": readonly, "minimap": {"enabled": False}, "fontSize": 13, "scrollBeyondLastLine": False, "automaticLayout": True},
    )
