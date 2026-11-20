"""Pending (not yet confirmed) results of Mini App actions.

Cloud integrations such as myVAILLANT poll the vendor cloud rarely (every
30 minutes is common) and the cloud itself reports a change with a delay. The
device does what was asked, but HA keeps the old state for a while, so the app
would show the old value. After a successful action we remember what was
requested, show it as "pending" until HA reports it or PENDING_TTL runs out,
and ask HA to refresh the entity a few times in between.
"""

from __future__ import annotations

import logging
import time

from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import PENDING_TTL, REFRESH_DELAYS

_LOGGER = logging.getLogger(__name__)


def expected_result(domain: str, service: str, data: dict) -> tuple[str | None, dict]:
    """What the entity should report once the service call has taken effect."""
    if service in ("turn_on", "turn_off"):
        return ("on" if service == "turn_on" else "off"), {}
    if service == "set_hvac_mode":
        return data["hvac_mode"], {}
    if service == "set_operation_mode":
        return data["operation_mode"], {"operation_mode": data["operation_mode"]}
    if service == "set_temperature":
        return None, {"temperature": data["temperature"]}
    if service == "set_fan_mode":
        return None, {"fan_mode": data["fan_mode"]}
    return None, {}


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.05
    return a == b


class PendingStore:
    """entity_id -> requested result that HA has not reported yet."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._items: dict[str, dict] = {}

    def matches(self, entity_id: str, state: str, attrs: dict) -> bool:
        item = self._items[entity_id]
        if item["s"] is not None and state != item["s"]:
            return False
        return all(_same(attrs.get(k), v) for k, v in item["a"].items())

    def add(self, entity_id: str, state: str | None, attrs: dict) -> None:
        """Remember a requested result unless HA already reports it."""
        self.drop(entity_id)
        if state is None and not attrs:
            return
        self._items[entity_id] = {"s": state, "a": attrs, "until": time.monotonic() + PENDING_TTL,
                                  "cancel": []}
        st = self.hass.states.get(entity_id)
        if st is not None and self.matches(entity_id, st.state, dict(st.attributes)):
            self.drop(entity_id)
            return
        @callback
        def _fire(_now, eid: str = entity_id) -> None:
            self._refresh(eid)

        job = HassJob(_fire, cancel_on_shutdown=True)
        self._items[entity_id]["cancel"] = [
            async_call_later(self.hass, delay, job) for delay in REFRESH_DELAYS
        ]

    def overlay(self, entity_id: str, out: dict) -> dict:
        """Apply a pending result to a state dict ({"s", "a", ...}) and mark it."""
        item = self._items.get(entity_id)
        if item is None:
            return out
        if time.monotonic() > item["until"] or self.matches(entity_id, out["s"], out["a"]):
            self.drop(entity_id)
            return out
        if item["s"] is not None:
            out["s"] = item["s"]
        out["a"] = {**out["a"], **item["a"]}
        out["p"] = 1
        return out

    def drop(self, entity_id: str) -> None:
        item = self._items.pop(entity_id, None)
        for cancel in (item or {}).get("cancel", []):
            cancel()

    def clear(self) -> None:
        for entity_id in list(self._items):
            self.drop(entity_id)

    def __contains__(self, entity_id: str) -> bool:
        return entity_id in self._items

    def _refresh(self, entity_id: str) -> None:
        st = self.hass.states.get(entity_id)
        if entity_id not in self._items:
            return
        if st is not None and self.matches(entity_id, st.state, dict(st.attributes)):
            self.drop(entity_id)
            return
        if not self.hass.services.has_service("homeassistant", "update_entity"):
            return
        self.hass.async_create_task(self._async_update(entity_id), eager_start=True)

    async def _async_update(self, entity_id: str) -> None:
        try:
            await self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - a failed refresh is not fatal
            _LOGGER.debug("Mini App: refresh of %s failed: %s", entity_id, err)

