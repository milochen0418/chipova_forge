"""Chipova Forge: from a finished chip to a working virtual product.

Pages: ``/`` lists the products (:mod:`.components.home`); ``/p/<id>`` is a product, used
together by everyone who opens it: its chip and contract, registers, board, firmware, the
simulation (Renode running the firmware, Verilator the chip's own RTL), the device's app, the
tests and the release. AI agents work through the AI Hub (:mod:`.agents.ai_tools`); scripts and
the SDK through the REST API (:mod:`.gateway.api`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import reflex as rx
from reflex_ddns_auth.intent import install_intent_host

from chipova_forge import ids
from chipova_forge.agents import ai_tools
from chipova_forge.components import editor
from chipova_forge.components.editor import editor_libraries
from chipova_forge.components.home import home_page
from chipova_forge.components.sections_a import board_view, chip_view, firmware_view, overview_view, registers_view
from chipova_forge.components.sections_b import agents_view, dashboard_view, lab_view, people_view, release_view, tests_view
from chipova_forge.components.workspace import workspace
from chipova_forge.gateway import api
from chipova_forge.jobs import builds
from chipova_forge.services import downloads, presence, proposals, systemtests
from chipova_forge.sessions import service
from chipova_forge.states import room as room_state
from chipova_forge.states.home_state import HomeState
from chipova_forge.states.room import note, push_later
from chipova_forge.states.workspace import WorkspaceState

logger = logging.getLogger(__name__)

SWEEP_EVERY = 30.0


def project_page() -> rx.Component:
    return rx.fragment(editor_libraries(), workspace({
        "overview": overview_view,
        "chip": chip_view,
        "registers": registers_view,
        "board": board_view,
        "firmware": firmware_view,
        "lab": lab_view,
        "dashboard": dashboard_view,
        "tests": tests_view,
        "release": release_view,
        "agents": agents_view,
        "people": people_view,
    }))


async def _sweep() -> None:
    """Now and then: ends idle and old simulations, expires old proposals, forgets gone tabs."""
    while True:
        await asyncio.sleep(SWEEP_EVERY)
        try:
            await service.sweep()
            proposals.expire_old()
        except Exception:  # noqa: BLE001
            logger.exception("The sweep failed")


@contextlib.asynccontextmanager
async def lifespan():
    """Starts the build queue and reconnects to the simulations a restart left running (their
    containers go on without the app)."""
    await builds.start()
    try:
        await service.recover()
    except Exception:  # noqa: BLE001
        logger.exception("Couldn't look for running simulations")
    sweeper = asyncio.create_task(_sweep())
    try:
        yield
    finally:
        sweeper.cancel()
        await service.stop_all()
        await builds.stop()


def _ai_activity(project_id: str, actor, what: str) -> None:  # noqa: ANN001
    """The AI is in the project while it works: among its people, and in its activity."""
    if not ids.is_project(project_id):
        return
    presence.join(project_id, "ai-" + str(abs(hash(actor.user)) % 10**8), actor.user, actor.user, actor.name)
    note(project_id, actor.name, what, "ai")
    push_later(project_id, "people")


app = rx.App(
    head_components=[rx.el.script(src="/forge/forge.js", defer=True)],
)
app.add_page(home_page, route="/", title="Chipova Forge", on_load=[HomeState.refresh, WorkspaceState.leave_project])
app.add_page(project_page, route="/p/[project_id]", title="Chipova Forge", on_load=WorkspaceState.on_load_project)
# Dialogs of other apps (the AI Hub's assistant) on every page.
install_intent_host(app)
app.register_lifespan_task(lifespan)
try:
    from reflex_base.registry import RegistrationContext

    room_state.registration = RegistrationContext.get()
except (ImportError, LookupError):
    pass

# What changes reaches the rooms of its project.
builds.listeners.append(lambda pid, bid: push_later(pid, "builds"))
service.listeners.append(lambda pid, sid: push_later(pid, "session"))
systemtests.listeners.append(lambda pid, rid: push_later(pid, "tests"))
proposals.listeners.append(lambda pid, xid: push_later(pid, "proposals"))
ai_tools.activity = _ai_activity

api.register_routes(app)
downloads.register_routes(app)
editor.register_routes(app)
ai_tools.register(app)
