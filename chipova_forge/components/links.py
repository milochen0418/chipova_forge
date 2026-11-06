"""Addresses on the app's backend, as the browser must use them.

Downloads are served by the backend (``/_forge/...``).
Behind re-ddns that is the page's own host; in a local run it is another port. Reflex knows
which (``getBackendURL``), so links are built in the browser from a path, the same way Reflex
builds its upload URLs (which also works in a production build, unlike code in ``call_script``).
"""

from __future__ import annotations

import reflex as rx
from reflex.constants import Dirs
from reflex.utils.imports import ImportVar
from reflex.vars.base import Var, VarData

_IMPORTS = VarData(
    imports={
        f"$/{Dirs.STATE_PATH}": "getBackendURL",
        "$/env.json": ImportVar(tag="env", is_default=True),
    }
)


def backend_url(path: Var | str) -> Var:
    """The full URL of ``path`` (``/_forge/...``) on the backend."""
    p = Var.create(path)
    return Var(
        _js_expr=f"(new URL({p}, getBackendURL(env.UPLOAD))).href",
        _var_data=VarData.merge(_IMPORTS, p._get_all_var_data()),
    ).to(str)


def backend_base() -> Var:
    """Any URL on the backend, for scripts to resolve paths against (``data-backend``)."""
    return rx.get_upload_url("")
