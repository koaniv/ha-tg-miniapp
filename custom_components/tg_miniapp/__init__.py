"""Telegram Mini App for Home Assistant.

Serves a small web app at /tgapp that opens from the Telegram bot's menu
button. Every API call is authenticated with the Telegram initData signature
(bot token from the telegram_bot integration) and the user must be one of the
bot's allowed private chats.
"""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_LAYOUT,
    CONF_MENU_TEXT,
    CONF_PUBLIC_URL,
    DEFAULT_MENU_TEXT,
    DOMAIN,
    PAGE_URL,
    TELEGRAM_DOMAIN,
    LayoutError,
    away_switch,
    parse_layout,
    water_heater_entities,
    zone_entities,
)
from .pending import PendingStore, start_sync
from .views import ActionView, PageView, StateView

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register the views and point the bot's menu button at the Mini App."""
    data = hass.data.setdefault(DOMAIN, {})
    try:
        data["layout"] = parse_layout(entry.options.get(CONF_LAYOUT))
    except LayoutError as err:
        _LOGGER.error("Mini App: layout ignored (%s), using the example layout", err)
        data["layout"] = parse_layout(None)
    if not data.get("views_registered"):
        hass.http.register_view(PageView())
        hass.http.register_view(StateView())
        hass.http.register_view(ActionView())
        data["views_registered"] = True  # aiohttp routes cannot be removed
    data["active"] = True
    if "pending" not in data:
        data["pending"] = PendingStore(hass)

    entry.async_on_unload(entry.add_update_listener(_async_reload))

    layout = data["layout"]
    if away := away_switch(layout):
        vaillant = zone_entities(layout) | water_heater_entities(layout)

        @callback
        def _away_changed(event: Event[EventStateChangedData]) -> None:
            old, new = event.data["old_state"], event.data["new_state"]
            if old is not None and new is not None and old.state == "on" and new.state == "off":
                start_sync(hass, vaillant)

        entry.async_on_unload(async_track_state_change_event(hass, [away], _away_changed))
    # Background task: Telegram may be unreachable (e.g. no internet during a
    # power outage) and that must not delay Home Assistant startup.
    entry.async_create_background_task(
        hass, _async_set_menu_button(hass, entry), "tg_miniapp_menu_button"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Switch the endpoints off. aiohttp routes stay, but answer 404."""
    data = hass.data.get(DOMAIN, {})
    data["active"] = False
    pending = data.get("pending")
    if pending is not None:
        pending.clear()
    if (unsub := data.pop("sync_unsub", None)) is not None:
        unsub()
    data.pop("sync_until", None)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_set_menu_button(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set the default menu button of every telegram_bot to open the Mini App."""
    base = str(entry.options.get(CONF_PUBLIC_URL, entry.data.get(CONF_PUBLIC_URL, "")))
    text = str(
        entry.options.get(CONF_MENU_TEXT, entry.data.get(CONF_MENU_TEXT, DEFAULT_MENU_TEXT))
    )
    if not base.startswith("https://"):
        _LOGGER.warning("Mini App: public URL must be https, menu button not set")
        return
    url = base.rstrip("/") + PAGE_URL
    session = async_get_clientsession(hass)
    for tg_entry in hass.config_entries.async_entries(TELEGRAM_DOMAIN):
        token = tg_entry.data.get(CONF_API_KEY)
        if not token:
            continue
        payload = {"menu_button": {"type": "web_app", "text": text, "web_app": {"url": url}}}
        try:
            async with session.post(
                f"https://api.telegram.org/bot{token}/setChatMenuButton",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - network errors must not break setup
            _LOGGER.warning("Mini App: could not set menu button: %s", type(err).__name__)
            continue
        if not isinstance(body, dict):
            _LOGGER.warning("Mini App: unexpected Telegram reply to setChatMenuButton")
        elif body.get("ok"):
            _LOGGER.info("Mini App: menu button '%s' -> %s", text, url)
        else:
            _LOGGER.warning("Mini App: Telegram refused menu button: %s", body.get("description"))
