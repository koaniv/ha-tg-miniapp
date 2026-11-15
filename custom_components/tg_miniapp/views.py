"""HTTP endpoints of the Telegram Mini App."""

from __future__ import annotations

from collections import defaultdict, deque
import logging
import math
from pathlib import Path
import time
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .auth import InitDataError, validate_init_data
from .const import (
    ACTIONS_PER_MINUTE,
    API_ACTION_URL,
    API_STATE_URL,
    CONF_EXTRA_USER_IDS,
    DOMAIN,
    EXPOSED_ATTRS,
    INIT_DATA_HEADER,
    INIT_DATA_MAX_AGE,
    PAGE_URL,
    TELEGRAM_DOMAIN,
    climate_entities,
    readable_entities,
    switch_entities,
)

_LOGGER = logging.getLogger(__name__)

WWW = Path(__file__).parent / "www"

# Telegram Web opens Mini Apps in an iframe. A CSP frame-ancestors header
# overrides the X-Frame-Options: SAMEORIGIN that HA adds to every response.
# The rest of the policy limits the page to its own inline code, the Telegram
# SDK and its own API, so injected markup cannot load or send anything.
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'unsafe-inline' https://telegram.org; "
        "style-src 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
    ),
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}
API_HEADERS = {"Cache-Control": "no-store"}


def _allowed_user_ids(hass: HomeAssistant) -> set[int]:
    """Private chat IDs allowed in the telegram_bot integration + extra IDs."""
    ids: set[int] = set()
    for entry in hass.config_entries.async_entries(TELEGRAM_DOMAIN):
        for sub in entry.subentries.values():
            try:
                chat_id = int(sub.data.get("chat_id"))
            except (TypeError, ValueError):
                continue
            if chat_id > 0:  # negative IDs are groups/channels, not people
                ids.add(chat_id)
    for entry in hass.config_entries.async_entries(DOMAIN):
        for raw in str(entry.options.get(CONF_EXTRA_USER_IDS, "")).replace(";", ",").split(","):
            raw = raw.strip()
            if raw.lstrip("-").isdigit() and int(raw) > 0:
                ids.add(int(raw))
    return ids


def _bot_tokens(hass: HomeAssistant) -> list[str]:
    return [
        entry.data[CONF_API_KEY]
        for entry in hass.config_entries.async_entries(TELEGRAM_DOMAIN)
        if entry.data.get(CONF_API_KEY)
    ]


def _error(code: str, status: int) -> web.Response:
    return web.json_response({"code": code}, status=status, headers=API_HEADERS)


def authenticate(hass: HomeAssistant, request: web.Request) -> dict | web.Response:
    """Return the Telegram user dict, or a ready error response.

    A forged signature raises HTTPUnauthorized, so HA's IP-ban counts it as a
    failed login. Expired or missing initData is answered without raising,
    so a family member with an old session is never banned.
    """
    init_data = request.headers.get(INIT_DATA_HEADER, "")
    if not init_data:
        return _error("no_init_data", 403)
    reasons: list[str] = []
    for token in _bot_tokens(hass):
        try:
            user = validate_init_data(init_data, token, INIT_DATA_MAX_AGE)
            break
        except InitDataError as err:
            reasons.append(str(err))
    else:
        if "expired" in reasons:
            return _error("expired", 403)
        _LOGGER.warning("Mini App: bad initData from %s (%s)", request.remote, reasons)
        raise web.HTTPUnauthorized(headers=API_HEADERS)
    if user["id"] not in _allowed_user_ids(hass):
        _LOGGER.warning(
            "Mini App: rejected Telegram user %s (%s)", user["id"], user.get("username")
        )
        return _error("forbidden", 403)
    return user


def _layout(hass: HomeAssistant) -> dict:
    return hass.data[DOMAIN]["layout"]


def _state_dict(hass: HomeAssistant, entity_id: str) -> dict | None:
    st = hass.states.get(entity_id)
    if st is None:
        return None
    return {
        "s": st.state,
        "a": {k: st.attributes[k] for k in EXPOSED_ATTRS if k in st.attributes},
        "lc": st.last_changed.isoformat(),
    }


class PageView(HomeAssistantView):
    """Serves the single-page Mini App."""

    url = PAGE_URL
    extra_urls = [PAGE_URL + "/"]
    name = "tg_miniapp:page"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        html = await hass.async_add_executor_job(
            (WWW / "index.html").read_text, "utf-8"
        )
        return web.Response(
            text=html, content_type="text/html", charset="utf-8", headers=PAGE_HEADERS
        )


class StateView(HomeAssistantView):
    """Returns the layout and the current state of every listed entity."""

    url = API_STATE_URL
    name = "tg_miniapp:state"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        user = authenticate(hass, request)
        if isinstance(user, web.Response):
            return user
        return self.json(
            {
                "user": {"id": user["id"], "first_name": user.get("first_name", "")},
                "layout": (layout := _layout(hass)),
                "states": {e: _state_dict(hass, e) for e in sorted(readable_entities(layout))},
                "ts": time.time(),
            },
            headers=API_HEADERS,
        )


class ActionView(HomeAssistantView):
    """Runs one whitelisted action."""

    url = API_ACTION_URL
    name = "tg_miniapp:action"
    requires_auth = False

    def __init__(self) -> None:
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def _rate_ok(self, uid: int) -> bool:
        now = time.monotonic()
        q = self._hits[uid]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= ACTIONS_PER_MINUTE:
            return False
        q.append(now)
        return True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        user = authenticate(hass, request)
        if isinstance(user, web.Response):
            return user
        if not self._rate_ok(user["id"]):
            return self.json_message("too many actions", 429, headers=API_HEADERS)
        try:
            body: dict[str, Any] = await request.json()
        except ValueError:
            return self.json_message("bad json", 400, headers=API_HEADERS)

        try:
            domain, service, data = _build_call(hass, body)
        except ValueError as err:
            return self.json_message(str(err), 400, headers=API_HEADERS)

        _LOGGER.info(
            "Mini App: %s (%s) -> %s.%s %s",
            user.get("first_name"), user["id"], domain, service, data,
        )
        try:
            await hass.services.async_call(
                domain, service, data, blocking=True, context=None
            )
        except (HomeAssistantError, ValueError) as err:
            # Details go to the log, not to the client.
            _LOGGER.warning("Mini App: %s.%s failed: %s", domain, service, err)
            return self.json_message("action failed", 502, headers=API_HEADERS)
        return self.json(
            {"ok": True, "state": _state_dict(hass, data["entity_id"])},
            headers=API_HEADERS,
        )


def _build_call(hass: HomeAssistant, body: dict) -> tuple[str, str, dict]:
    """Validate a request body against the whitelist and build a service call."""
    layout = _layout(hass)
    action = body.get("action")
    entity_id = body.get("entity_id")
    if not isinstance(entity_id, str):
        raise ValueError("entity_id required")
    st = hass.states.get(entity_id)
    if st is None:
        raise ValueError("unknown entity")

    if action in ("switch_on", "switch_off"):
        if entity_id not in switch_entities(layout):
            raise ValueError("entity not allowed")
        return "switch", "turn_on" if action == "switch_on" else "turn_off", {
            "entity_id": entity_id
        }

    if entity_id not in climate_entities(layout):
        raise ValueError("entity not allowed")

    if action == "set_temperature":
        try:
            temp = float(body.get("temperature"))
        except (TypeError, ValueError) as err:
            raise ValueError("temperature required") from err
        if not math.isfinite(temp):
            raise ValueError("bad temperature")
        lo = float(st.attributes.get("min_temp", 7))
        hi = float(st.attributes.get("max_temp", 35))
        step = float(st.attributes.get("target_temp_step") or 0.5)
        temp = min(max(round(temp / step) * step, lo), hi)
        return "climate", "set_temperature", {"entity_id": entity_id, "temperature": temp}

    if action == "set_hvac_mode":
        mode = body.get("hvac_mode")
        if mode not in (st.attributes.get("hvac_modes") or []):
            raise ValueError("mode not supported")
        return "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": mode}

    if action == "set_fan_mode":
        mode = body.get("fan_mode")
        if mode not in (st.attributes.get("fan_modes") or []):
            raise ValueError("fan mode not supported")
        return "climate", "set_fan_mode", {"entity_id": entity_id, "fan_mode": mode}

    raise ValueError("unknown action")
