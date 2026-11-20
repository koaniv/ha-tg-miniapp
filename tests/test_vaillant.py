"""DHW (water_heater), away mode and pending results of slow cloud devices."""

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

WH = "water_heater.xata_domestic_hot_water_0"
AWAY = "switch.xata_away_mode"
ZONE = "climate.xata_zone_first"
LAYOUT = {
    "heating": {
        "zones": [{"name": "Z", "climate": ZONE}],
        "dhw": {"water_heater": WH},
        "away": {"switch": AWAY},
    },
}


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
    hass.states.async_set(WH, "Off", {
        "operation_list": ["Manual", "Time Controlled", "Off", "Cylinder Boost"],
        "operation_mode": "Off", "min_temp": 35, "max_temp": 70, "temperature": 50,
        "current_temperature": 32.6, "time_program_dhw": {"monday": []}})
    hass.states.async_set(AWAY, "off", {"holiday_end_date_time": None})
    hass.states.async_set(ZONE, "off", {"hvac_modes": ["off", "heat_cool", "auto"],
                                        "min_temp": 7, "max_temp": 35, "temperature": 21})
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
            return (await (await c.get("/api/tgapp/state", headers=h)).json())

    return C()


async def test_dhw_controls(hass, client):
    mode = async_mock_service(hass, "water_heater", "set_operation_mode")
    temp = async_mock_service(hass, "water_heater", "set_temperature")
    status, _ = await client.post({"action": "set_operation_mode", "entity_id": WH, "operation_mode": "Manual"})
    assert status == 200 and mode[-1].data["operation_mode"] == "Manual"
    status, _ = await client.post({"action": "set_operation_mode", "entity_id": WH, "operation_mode": "Turbo"})
    assert status == 400
    status, _ = await client.post({"action": "set_temperature", "entity_id": WH, "temperature": 52.4})
    assert status == 200 and temp[-1].data["temperature"] == 52  # step 1
    status, _ = await client.post({"action": "set_temperature", "entity_id": WH, "temperature": 90})
    assert status == 200 and temp[-1].data["temperature"] == 70  # clamped
    # climate-only actions are not allowed on the water heater
    status, _ = await client.post({"action": "set_hvac_mode", "entity_id": WH, "hvac_mode": "off"})
    assert status == 400


async def test_dhw_state_exposed_without_time_program(hass, client):
    body = await client.state()
    a = body["states"][WH]["a"]
    assert a["operation_mode"] == "Off" and "Manual" in a["operation_list"]
    assert "time_program_dhw" not in a


async def test_away_switch(hass, client):
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    assert (await client.post({"action": "switch_on", "entity_id": AWAY}))[0] == 200
    assert (await client.post({"action": "switch_off", "entity_id": AWAY}))[0] == 200
    assert len(on) == 1 and len(off) == 1


async def test_pending_until_confirmed(hass, client):
    """Cloud device: HA keeps the old state after the call; the app shows the request."""
    async_mock_service(hass, "climate", "set_hvac_mode")
    status, body = await client.post({"action": "set_hvac_mode", "entity_id": ZONE, "hvac_mode": "heat_cool"})
    assert status == 200 and body["state"]["s"] == "heat_cool" and body["state"]["p"] == 1
    st = (await client.state())["states"][ZONE]
    assert st["s"] == "heat_cool" and st["p"] == 1
    hass.states.async_set(ZONE, "heat_cool", {"hvac_modes": ["off", "heat_cool", "auto"], "temperature": 21})
    st = (await client.state())["states"][ZONE]
    assert st["s"] == "heat_cool" and "p" not in st


async def test_pending_refreshes_entity_then_expires(hass, client, monkeypatch):
    refresh = async_mock_service(hass, "homeassistant", "update_entity")
    async_mock_service(hass, "water_heater", "set_operation_mode")
    await client.post({"action": "set_operation_mode", "entity_id": WH, "operation_mode": "Manual"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=16))
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in refresh] == [WH]
    st = (await client.state())["states"][WH]
    assert st["s"] == "Manual" and st["a"]["operation_mode"] == "Manual" and st["p"] == 1
    # never confirmed: after the TTL the real state is shown again
    import custom_components.tg_miniapp.pending as pending
    real = pending.time.monotonic
    monkeypatch.setattr(pending.time, "monotonic", lambda: real() + 10_000)
    st = (await client.state())["states"][WH]
    assert st["s"] == "Off" and "p" not in st


async def test_no_pending_when_state_already_matches(hass, client):
    async def instant(call):
        hass.states.async_set(AWAY, "on", {})
    hass.services.async_register("switch", "turn_on", instant)
    status, body = await client.post({"action": "switch_on", "entity_id": AWAY})
    assert status == 200 and body["state"]["s"] == "on" and "p" not in body["state"]


async def test_unload_clears_pending(hass, client):
    async_mock_service(hass, "climate", "set_hvac_mode")
    await client.post({"action": "set_hvac_mode", "entity_id": ZONE, "hvac_mode": "auto"})
    store = hass.data["tg_miniapp"]["pending"]
    assert ZONE in store
    entry = hass.config_entries.async_entries("tg_miniapp")[0]
    await hass.config_entries.async_unload(entry.entry_id)
    assert ZONE not in store


def test_layout_validation():
    from custom_components.tg_miniapp.const import LayoutError, parse_layout, switch_entities, water_heater_entities
    ok = parse_layout(LAYOUT)
    assert water_heater_entities(ok) == {WH} and switch_entities(ok) == {AWAY}
    for bad in ({"heating": {"dhw": {"water_heater": "climate.x"}}},
                {"heating": {"away": {"switch": "sensor.x"}}},
                {"heating": {"dhw": "water_heater.x"}}):
        with pytest.raises(LayoutError):
            parse_layout(bad)
