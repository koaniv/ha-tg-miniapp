"""Heating tab: away lock, all-zones toggle, away duration, refresh, sync after away."""

from datetime import timedelta
import json

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from tests.test_integration import ALLOWED, TOKEN, init_data

Z1 = "climate.xata_zone_first"
Z2 = "climate.xata_zone_second"
AC = "climate.hall_ac"
WH = "water_heater.dhw"
AWAY = "switch.away"
MODES = ["off", "heat_cool", "auto"]
LAYOUT = {
    "energy": {"battery_soc": "sensor.soc"},
    "heating": {
        "zones": [{"name": "A", "climate": Z1}, {"name": "B", "climate": Z2}],
        "dhw": {"water_heater": WH},
        "away": {"switch": AWAY},
        "heatpump": {"switch": "switch.hp"},
    },
    "ac": [{"name": "Hall", "climate": AC}],
}


def zone(hass, eid, mode, temp=21):
    hass.states.async_set(eid, mode, {"hvac_modes": MODES, "min_temp": 7, "max_temp": 35,
                                      "target_temp_step": 0.5, "temperature": temp})


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def client(hass, hass_client_no_auth, aioclient_mock):
    aioclient_mock.post(f"https://api.telegram.org/bot{TOKEN}/setChatMenuButton", json={"ok": True})
    MockConfigEntry(
        domain="telegram_bot", data={"api_key": TOKEN, "platform": "polling"},
        subentries_data=[ConfigSubentryData(data={"chat_id": ALLOWED}, subentry_type="allowed_chat_ids",
                                            title="me", unique_id=str(ALLOWED))],
    ).add_to_hass(hass)
    hass.config.components.add("telegram_bot")
    zone(hass, Z1, "auto")
    zone(hass, Z2, "heat_cool")
    hass.states.async_set(AC, "off", {"hvac_modes": ["off", "heat", "cool"], "min_temp": 16, "max_temp": 30,
                                      "temperature": 22})
    hass.states.async_set(WH, "Manual", {"operation_list": ["Manual", "Off"], "operation_mode": "Manual",
                                         "min_temp": 35, "max_temp": 70, "temperature": 50})
    hass.states.async_set(AWAY, "off", {})
    hass.states.async_set("switch.hp", "off", {})
    hass.states.async_set("sensor.soc", "50", {})
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain="tg_miniapp", data={}, options={
        "public_url": "https://app.example.com", "layout": json.dumps(LAYOUT)})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = await hass_client_no_auth()
    h = {"X-Telegram-Init-Data": init_data()}

    class C:
        async def post(self, body):
            r = await c.post("/api/tgapp/action", headers=h, json=body)
            return r.status, (await r.json())

        async def state(self):
            return await (await c.get("/api/tgapp/state", headers=h)).json()

    return C()


async def test_zones_off_then_on_restores_last_modes(hass, client):
    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    status, body = await client.post({"action": "zones_off"})
    assert status == 200
    assert {(c.data["entity_id"], c.data["hvac_mode"]) for c in calls} == {(Z1, "off"), (Z2, "off")}
    assert set(body["states"]) == {Z1, Z2} and body["states"][Z1]["p"] == 1
    zone(hass, Z1, "off")
    zone(hass, Z2, "off")
    calls.clear()
    status, _ = await client.post({"action": "zones_on"})
    assert status == 200
    assert {(c.data["entity_id"], c.data["hvac_mode"]) for c in calls} == {(Z1, "auto"), (Z2, "heat_cool")}


async def test_zones_on_without_history_uses_schedule(hass, client):
    zone(hass, Z1, "off")
    zone(hass, Z2, "off")
    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    status, _ = await client.post({"action": "zones_on"})
    assert status == 200 and {c.data["hvac_mode"] for c in calls} == {"auto"}
    # everything already on: nothing to do
    zone(hass, Z1, "auto")
    zone(hass, Z2, "auto")
    status, _ = await client.post({"action": "zones_on"})
    assert status == 400


async def test_mode_seen_in_state_is_remembered(hass, client):
    zone(hass, Z1, "heat_cool")
    await client.state()  # the app has seen Z1 in manual mode
    zone(hass, Z1, "off")
    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await client.post({"action": "zones_on"})
    assert (Z1, "heat_cool") in {(c.data["entity_id"], c.data["hvac_mode"]) for c in calls}


async def test_away_locks_vaillant_but_not_ac_or_heat_pump(hass, client):
    hass.states.async_set(AWAY, "on", {})
    async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "water_heater", "set_operation_mode")
    sw = async_mock_service(hass, "switch", "turn_on")
    for body in ({"action": "set_hvac_mode", "entity_id": Z1, "hvac_mode": "off"},
                 {"action": "set_temperature", "entity_id": Z2, "temperature": 22},
                 {"action": "set_operation_mode", "entity_id": WH, "operation_mode": "Off"},
                 {"action": "zones_off"}):
        status, resp = await client.post(body)
        assert status == 409 and resp["code"] == "away_mode", body
    assert (await client.post({"action": "set_hvac_mode", "entity_id": AC, "hvac_mode": "heat"}))[0] == 200
    assert (await client.post({"action": "switch_on", "entity_id": "switch.hp"}))[0] == 200
    assert len(sw) == 1


async def test_away_on_until_cancelled_and_for_days(hass, client):
    sw = async_mock_service(hass, "switch", "turn_on")
    hol = async_mock_service(hass, "mypyllant", "set_holiday")
    status, body = await client.post({"action": "away_on", "days": None})
    assert status == 200 and len(sw) == 1 and body["states"][AWAY]["s"] == "on" and body["states"][AWAY]["p"] == 1
    status, body = await client.post({"action": "away_on", "days": 3})
    assert status == 200
    assert hol[-1].data["duration_hours"] == 72 and hol[-1].data["entity_id"] in (Z1, Z2)
    assert body["states"][AWAY]["p"] == 1
    for bad in (0, 31, "3", True, 1.5):
        assert (await client.post({"action": "away_on", "days": bad}))[0] == 400


async def test_away_days_need_mypyllant(hass, client):
    async_mock_service(hass, "switch", "turn_on")
    assert (await client.post({"action": "away_on", "days": 1}))[0] == 400


async def test_refresh_all_with_cooldown(hass, client):
    upd = async_mock_service(hass, "homeassistant", "update_entity")
    status, body = await client.post({"action": "refresh"})
    assert status == 200 and "skipped" not in body
    ids = upd[-1].data["entity_id"]
    assert Z1 in ids and WH in ids and "sensor.soc" in ids
    status, body = await client.post({"action": "refresh"})
    assert status == 200 and body["skipped"] is True and len(upd) == 1


async def test_away_off_syncs_and_locks_briefly(hass, client):
    upd = async_mock_service(hass, "homeassistant", "update_entity")
    hass.states.async_set(AWAY, "on", {})
    await hass.async_block_till_done()
    hass.states.async_set(AWAY, "off", {})  # e.g. turned off by the grid automation
    await hass.async_block_till_done()
    assert upd and set(upd[-1].data["entity_id"]) == {Z1, Z2, WH}
    body = await client.state()
    assert body["sync"] > 0
    status, resp = await client.post({"action": "set_hvac_mode", "entity_id": Z1, "hvac_mode": "off"})
    assert status == 409 and resp["code"] == "syncing"
    # second refresh half-way, then the lock is gone
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=16))
    await hass.async_block_till_done()
    assert len(upd) == 2
    import custom_components.tg_miniapp.pending as pending
    real = pending.time.monotonic
    pending.time.monotonic = lambda: real() + 60
    try:
        assert (await client.state())["sync"] == 0
    finally:
        pending.time.monotonic = real


def test_example_layout_has_heat_pump_temperatures():
    from custom_components.tg_miniapp.const import EXAMPLE_LAYOUT, parse_layout, zone_entities
    hp = EXAMPLE_LAYOUT["heating"]["heatpump"]
    assert hp["temp_heating"].startswith("sensor.") and hp["temp_dhw"].startswith("sensor.")
    assert zone_entities(parse_layout(LAYOUT)) == {Z1, Z2}
