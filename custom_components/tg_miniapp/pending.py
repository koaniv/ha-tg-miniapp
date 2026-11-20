"""Pending (not yet confirmed) results of Mini App actions.

Cloud integrations such as myVAILLANT poll the vendor cloud rarely (every
30 minutes is common) and the cloud itself reports a change with a delay. The
device does what was asked, but HA keeps the old state for a while, so the app
would show the old value. After a successful action we remember what was
requested, show it as "pending" until HA reports it or PENDING_TTL runs out,
and ask HA to refresh the entity a few times in between.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, PENDING_TTL, REFRESH_DELAYS, REFRESH_MERGE_SECONDS, SYNC_SECONDS

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
        # Shared refresh ticks: [(monotonic fire time, cancel)]. Each tick polls
        # every still-pending entity in one update_entity call, so a cloud
        # integration is asked once per tick, not once per entity.
        self._ticks: list[tuple[float, object]] = []

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
        self._items[entity_id] = {"s": state, "a": attrs, "until": time.monotonic() + PENDING_TTL}
        st = self.hass.states.get(entity_id)
        if st is not None and self.matches(entity_id, st.state, dict(st.attributes)):
            self.drop(entity_id)
            return
        now = time.monotonic()
        for delay in REFRESH_DELAYS:
            fire = now + delay
            if not any(abs(t - fire) < REFRESH_MERGE_SECONDS for t, _ in self._ticks):
                self._schedule_tick(fire, delay)

    def _schedule_tick(self, fire: float, delay: float) -> None:
        holder: list = []

        @callback
        def _tick(_now) -> None:
            self._ticks[:] = [t for t in self._ticks if t is not holder[0]]
            self._refresh_pending()

        entry = (fire, async_call_later(self.hass, delay, HassJob(_tick, cancel_on_shutdown=True)))
        holder.append(entry)
        self._ticks.append(entry)

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
        self._items.pop(entity_id, None)

    def clear(self) -> None:
        self._items.clear()
        for _, cancel in self._ticks:
            cancel()
        self._ticks.clear()

    def __contains__(self, entity_id: str) -> bool:
        return entity_id in self._items

    def expected_state(self, entity_id: str) -> str | None:
        """Requested state of an entity that HA has not confirmed yet."""
        item = self._items.get(entity_id)
        if item is None or time.monotonic() > item["until"]:
            return None
        return item["s"]

    def _refresh_pending(self) -> None:
        """Poll the entities that are still pending, in one call."""
        now = time.monotonic()
        ids = []
        for entity_id, item in list(self._items.items()):
            st = self.hass.states.get(entity_id)
            if now > item["until"] or (
                st is not None and self.matches(entity_id, st.state, dict(st.attributes))
            ):
                self.drop(entity_id)
            else:
                ids.append(entity_id)
        if ids:
            self.hass.async_create_task(async_update_entities(self.hass, ids), eager_start=True)



async def async_update_entities(
    hass: HomeAssistant, entity_ids, timeout: float | None = None
) -> bool:
    """Ask HA to poll the given entities now (homeassistant.update_entity)."""
    ids = sorted(e for e in entity_ids if hass.states.get(e) is not None)
    if not ids or not hass.services.has_service("homeassistant", "update_entity"):
        return False
    try:
        async with asyncio.timeout(timeout):
            await hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": ids}, blocking=True
            )
    except Exception as err:  # noqa: BLE001 - a failed refresh is not fatal
        _LOGGER.debug("Mini App: refresh failed: %s", err)
        return False
    return True


def start_sync(hass: HomeAssistant, entity_ids) -> None:
    """Away mode ended: re-read the zones and hot water, show them as syncing."""
    data = hass.data[DOMAIN]
    data["sync_until"] = time.monotonic() + SYNC_SECONDS
    ids = list(entity_ids)
    # The away switch has just been read from the cloud, so the zones were too.
    # One more poll a little later picks up what the cloud changes after a
    # holiday ends. A previous sync's poll is replaced, not added to.
    if (old := data.pop("sync_unsub", None)) is not None:
        old()

    @callback
    def _again(_now) -> None:
        data.pop("sync_unsub", None)
        hass.async_create_task(async_update_entities(hass, ids), eager_start=True)

    data["sync_unsub"] = async_call_later(
        hass, SYNC_SECONDS // 2, HassJob(_again, cancel_on_shutdown=True)
    )


def sync_remaining(hass: HomeAssistant) -> int:
    left = hass.data.get(DOMAIN, {}).get("sync_until", 0) - time.monotonic()
    return max(0, math.ceil(left))
