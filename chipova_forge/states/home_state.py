"""The project list (/): the projects you may see, and making one: a product template, built
around a chip from Forge's examples, a Chipova release package (zip) or the platform's ``chips``
collection (what Chipova publishes)."""

from __future__ import annotations

import dataclasses
import logging

import reflex as rx

from chipova_forge.contract import chip_package
from chipova_forge.services import presence, projects
from chipova_forge.sessions import service
from chipova_forge.states.ui_state import UiState

logger = logging.getLogger(__name__)
MAX_UPLOAD = 64 << 20


@dataclasses.dataclass
class ProjectRow:
    id: str = ""
    title: str = ""
    template: str = ""
    chip: str = ""
    interface: str = ""
    owner: str = ""
    updated: float = 0.0
    simulation: str = ""
    people: int = 0


@dataclasses.dataclass
class OptionRow:
    id: str = ""
    title: str = ""
    description: str = ""


def _collection_items() -> list[OptionRow]:
    """Chips Chipova published into the platform's ``chips`` collection (none off the platform)."""
    try:
        from reflex_ddns_auth.collections import Collection  # noqa: PLC0415

        out = []
        for it in Collection("chips").items(kind="chip.release"):
            meta = (it.manifest or {}).get("meta") or {}
            out.append(OptionRow(id=str(it.ref), title=str(it.title or meta.get("title") or it.ref),
                                 description=f"{meta.get('top', '')} · {str(meta.get('snapshot', ''))[:19]}…"))
        return out
    except Exception:  # noqa: BLE001 - no collections here
        return []


class HomeState(rx.State):
    projects: list[ProjectRow] = []
    templates: list[OptionRow] = []
    chips: list[OptionRow] = []
    collection: list[OptionRow] = []
    new_open: bool = False
    new_title: str = ""
    new_template: str = "smart_sensor"
    new_source: str = "example"  # example | upload | collection
    new_chip: str = ""
    new_ref: str = ""
    upload_name: str = ""
    error: str = ""
    creating: bool = False
    _upload: bytes = b""

    @rx.event
    async def refresh(self):
        ui = await self.get_state(UiState)
        actor = await ui._identify()
        watching = presence.watching() if hasattr(presence, "watching") else {}
        rows = []
        for p in projects.visible_to(actor.user):
            s = service.active_of(p["id"])
            rows.append(ProjectRow(id=p["id"], title=p["title"], template=p["template"], chip=(p.get("chip") or {}).get("name", ""),
                                   interface=(p.get("chip") or {}).get("interface", ""), owner=p["owner_name"], updated=p["updated_at"],
                                   simulation=s["status"] if s else "", people=len(watching.get(p["id"], [])) if isinstance(watching, dict) else 0))
        self.projects = rows
        self.templates = [OptionRow(id=k, title=str(v.get("title", k)), description=str(v.get("description", "")))
                          for k, v in projects.product_templates().items()]
        self.chips = [OptionRow(id=k, title=v["title"], description=v["description"]) for k, v in chip_package.example_chips().items()]
        self.collection = _collection_items()

    @rx.event
    def set_new_open(self, value: bool):
        self.new_open = value
        self.error = ""
        if value:
            tpl = projects.product_templates().get(self.new_template, {})
            self.new_chip = str(tpl.get("chip", "")) or (self.chips[0].id if self.chips else "")

    @rx.event
    def set_new_title(self, value: str):
        self.new_title = value

    @rx.event
    def set_new_template(self, value: str):
        self.new_template = value
        tpl = projects.product_templates().get(value, {})
        if self.new_source == "example":
            self.new_chip = str(tpl.get("chip", self.new_chip))

    @rx.event
    def set_new_source(self, value: str):
        self.new_source = value
        self.error = ""

    @rx.event
    def set_new_chip(self, value: str):
        self.new_chip = value

    @rx.event
    def set_new_ref(self, value: str):
        self.new_ref = value

    @rx.event
    async def take_upload(self, files: list[rx.UploadFile]):
        if not files:
            return
        f = files[0]
        data = bytearray()
        while chunk := await f.read(1024 * 1024):
            data += chunk
            if len(data) > MAX_UPLOAD:
                self.error = "the zip is larger than 64 MB"
                return
        self._upload = bytes(data)
        self.upload_name = f.filename or "release.zip"
        self.error = ""

    @rx.event
    async def create(self):
        ui = await self.get_state(UiState)
        actor = await ui._identify()
        self.error = ""
        try:
            if self.new_source == "upload":
                if not self._upload:
                    raise chip_package.ChipImportError("choose a Chipova release package (.zip) first")
                chip = chip_package.from_release_zip(self._upload, self.upload_name)
            elif self.new_source == "collection":
                from reflex_ddns_auth.collections import Collection  # noqa: PLC0415

                path = Collection("chips").resolve(self.new_ref)
                chip = chip_package.from_collection_dir(path, self.new_ref)
            else:
                chip = chip_package.from_example(self.new_chip)
            p = projects.create(self.new_title, self.new_template, chip, actor)
        except (chip_package.ChipImportError, ValueError, projects.Denied) as e:
            self.error = str(e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception("Couldn't make a project")
            self.error = f"{type(e).__name__}: {e}"
            return None
        self.new_open = False
        self.new_title = ""
        self._upload, self.upload_name = b"", ""
        return rx.redirect(f"/p/{p['id']}")
