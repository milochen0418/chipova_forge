"""Who this tab is: the signed-in account (the platform's, through reflex_ddns_auth), else a
guest. A guest's key is kept in the browser's local storage, so the same browser stays the same
guest (and the owner of the projects it made); other guests can't see the key, only the name.
"""

from __future__ import annotations

import secrets

import reflex as rx

from chipova_forge.services import actors, presence
from chipova_forge.services.actors import Actor

try:
    from reflex_ddns_auth import AuthState
except ImportError:  # runs without sign-in: everyone is a guest
    AuthState = None


# Client token -> the guest key that tab had (see UiState._guest).
_tab_keys: dict[str, str] = {}


def _valid(key: str) -> bool:
    return len(key or "") == 12 and all(c in "0123456789abcdef" for c in key)


class UiState(rx.State):
    # Who this tab is: {"peer", "user", "name", "initials", "email", "avatar", "color"}.
    me: dict[str, str] = {}
    signed_in: bool = False
    guest_key: str = rx.LocalStorage("", name="forge_guest", sync=False)
    _peer: str = ""

    async def _identify(self) -> Actor:
        """Who this tab is, worked out again (sign-in may have changed meanwhile)."""
        email = name = avatar = ""
        if AuthState is not None:
            try:
                auth = await self.get_state(AuthState)
                if not auth._auth_loaded:
                    await auth.load_auth()
                if auth.is_logged_in:
                    email, name, avatar = auth.user_email, auth.user_name or auth.user_email, auth.user_avatar
            except Exception:  # noqa: BLE001 — a guest then
                pass
        if not self._peer:
            self._peer = secrets.token_hex(6)
        if email:
            actor = Actor(email.lower(), name, email=email.lower())
        else:
            actor = actors.guest(self._guest())
            # A Relack guest is signed in with a nickname but no e-mail: keep the nickname.
            if name:
                actor = Actor(actor.user, name)
        me = {
            "peer": self._peer,
            "user": actor.user,
            "name": actor.name,
            "initials": presence.initials(actor.name, actor.user),
            "email": actor.email,
            "avatar": avatar,
            "color": (self.me or {}).get("color", ""),
        }
        if me != self.me:
            self.me = me
        self.signed_in = bool(email)
        return actor

    def _guest(self) -> str:
        """This browser's guest key: the one it keeps; else the one this tab had (leaving a
        project's room rehydrates the tab, which empties the stored value for a moment); else a
        new one."""
        token = self.router.session.client_token
        key = self.guest_key if _valid(self.guest_key) else _tab_keys.get(token, "")
        if not _valid(key):
            key = secrets.token_hex(6)
        _tab_keys[token] = key
        if self.guest_key != key:
            self.guest_key = key
        return key

    def actor(self) -> Actor:
        """This tab's person, as last identified."""
        me = self.me or {}
        return Actor(me.get("user", ""), me.get("name", ""), email=me.get("email", ""))

    def _set_color(self, color: str) -> None:
        if self.me.get("color") != color:
            self.me = {**self.me, "color": color}

    @rx.event
    async def identify(self):
        await self._identify()
