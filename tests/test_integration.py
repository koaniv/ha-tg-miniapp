"""End-to-end tests of the Mini App endpoints inside a real HA test instance."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

TOKEN = "123456:TEST-token"
ALLOWED = 111
STRANGER = 222
HP = "switch.heatpump"
AC = "climate.living_room_ac"
ZONE = "climate.zone_living_room"
LAYOUT = {
    "energy": {"battery_soc": "sensor.deye_battery_soc", "pv_power": "sensor.deye_pv_power",
               "battery_capacity_kwh": 15},
    "heating": {"zones": [{"name": "Z", "icon": "x", "climate": ZONE}],
                "heatpump": {"switch": HP}},
    "ac": [{"name": "Hall", "icon": "y", "climate": AC}],
}


def init_data(user_id=ALLOWED, auth_date=None, token=TOKEN):
    fields = {
        "user": json.dumps({"id": user_id, "first_name": "A"}, separators=(",", ":")),
        "auth_date": str(int(auth_date or time.time())),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def setup(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(
        f"https://api.telegram.org/bot{TOKEN}/setChatMenuButton", json={"ok": True}
    )
    tg = MockConfigEntry(
        domain="telegram_bot",
        data={"api_key": TOKEN, "platform": "polling"},
        subentries_data=[
            ConfigSubentryData(
                data={"chat_id": ALLOWED}, subentry_type="allowed_chat_ids",
                title="me", unique_id=str(ALLOWED),
            ),
            ConfigSubentryData(
                data={"chat_id": -100500}, subentry_type="allowed_chat_ids",
                title="group", unique_id="-100500",
            ),
        ],
    )
    tg.add_to_hass(hass)
    hass.config.components.add("telegram_bot")  # pretend it is loaded

    hass.states.async_set("sensor.deye_battery_soc", "85", {"unit_of_measurement": "%"})
    hass.states.async_set(HP, "off")
    hass.states.async_set(
        AC, "off",
        {"hvac_modes": ["off", "heat", "cool"], "fan_modes": ["auto", "high"],
         "min_temp": 16, "max_temp": 32, "target_temp_step": 1, "temperature": 24,
         "current_temperature": 22, "secret_attr": "x"},
    )
    hass.states.async_set(
        ZONE, "heat_cool",
        {"hvac_modes": ["heat_cool", "off", "auto"], "min_temp": 7, "max_temp": 35,
         "target_temp_step": 0.5, "temperature": 22},
    )
    hass.states.async_set("light.not_in_layout", "on")

    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain="tg_miniapp", data={},
        options={"public_url": "https://app.example.com", "menu_text": "Дім",
                 "extra_user_ids": "333", "layout": json.dumps(LAYOUT)},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_page_served_without_auth(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    r = await client.get("/tgapp")
    assert r.status == 200
    assert "frame-ancestors" in r.headers["Content-Security-Policy"]
    assert "telegram-web-app.js" in await r.text()


async def test_menu_button_set(hass, setup, aioclient_mock):
    calls = [c for c in aioclient_mock.mock_calls if "setChatMenuButton" in str(c[1])]
    assert calls
    assert calls[0][2]["menu_button"]["web_app"]["url"] == "https://app.example.com/tgapp"


async def test_state_requires_init_data(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    r = await client.get("/api/tgapp/state")
    assert r.status == 403 and (await r.json())["code"] == "no_init_data"


async def test_state_ok_and_filtered(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    r = await client.get("/api/tgapp/state", headers={"X-Telegram-Init-Data": init_data()})
    assert r.status == 200
    body = await r.json()
    assert body["states"]["sensor.deye_battery_soc"]["s"] == "85"
    assert "light.not_in_layout" not in body["states"]
    assert "secret_attr" not in body["states"][AC]["a"]
    assert body["states"]["sensor.deye_pv_power"] is None  # missing entity -> null


async def test_extra_user_allowed(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    r = await client.get("/api/tgapp/state", headers={"X-Telegram-Init-Data": init_data(333)})
    assert r.status == 200


async def test_stranger_and_group_forbidden(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    for uid in (STRANGER, 100500):
        r = await client.get("/api/tgapp/state", headers={"X-Telegram-Init-Data": init_data(uid)})
        assert r.status == 403 and (await r.json())["code"] == "forbidden"


async def test_expired_is_403_not_401(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    old = init_data(auth_date=time.time() - 3 * 86400)
    r = await client.get("/api/tgapp/state", headers={"X-Telegram-Init-Data": old})
    assert r.status == 403 and (await r.json())["code"] == "expired"


async def test_forged_is_401(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    r = await client.get(
        "/api/tgapp/state", headers={"X-Telegram-Init-Data": init_data(token="9:forged")}
    )
    assert r.status == 401


async def test_actions(hass, setup, hass_client_no_auth):
    temp_calls = async_mock_service(hass, "climate", "set_temperature")
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    fan_calls = async_mock_service(hass, "climate", "set_fan_mode")
    sw_calls = async_mock_service(hass, "switch", "turn_on")
    client = await hass_client_no_auth()
    h = {"X-Telegram-Init-Data": init_data()}

    async def post(body):
        return await client.post("/api/tgapp/action", headers=h, json=body)

    r = await post({"action": "set_temperature", "entity_id": ZONE, "temperature": 22.3})
    assert r.status == 200 and temp_calls[-1].data["temperature"] == 22.5
    r = await post({"action": "set_temperature", "entity_id": AC, "temperature": 99})
    assert r.status == 200 and temp_calls[-1].data["temperature"] == 32  # clamped
    r = await post({"action": "set_hvac_mode", "entity_id": AC, "hvac_mode": "heat"})
    assert r.status == 200 and mode_calls[-1].data["hvac_mode"] == "heat"
    r = await post({"action": "set_hvac_mode", "entity_id": AC, "hvac_mode": "dry"})
    assert r.status == 400  # mode not supported by entity
    r = await post({"action": "set_fan_mode", "entity_id": AC, "fan_mode": "high"})
    assert r.status == 200 and fan_calls[-1].data["fan_mode"] == "high"
    r = await post({"action": "switch_on", "entity_id": HP})
    assert r.status == 200 and len(sw_calls) == 1
    # whitelist
    r = await post({"action": "switch_on", "entity_id": "light.not_in_layout"})
    assert r.status == 400
    r = await post({"action": "set_hvac_mode", "entity_id": HP, "hvac_mode": "off"})
    assert r.status == 400
    r = await post({"action": "set_temperature", "entity_id": AC, "temperature": "nan"})
    assert r.status == 400


async def test_rate_limit(hass, setup, hass_client_no_auth):
    async_mock_service(hass, "climate", "set_hvac_mode")
    client = await hass_client_no_auth()
    h = {"X-Telegram-Init-Data": init_data()}
    codes = []
    for _ in range(32):
        r = await client.post(
            "/api/tgapp/action", headers=h,
            json={"action": "set_hvac_mode", "entity_id": AC, "hvac_mode": "heat"},
        )
        codes.append(r.status)
    assert codes.count(429) == 2


async def test_config_flow(hass):
    MockConfigEntry(domain="telegram_bot", data={"api_key": TOKEN, "platform": "polling"}).add_to_hass(hass)
    hass.config.components.add("telegram_bot")
    r = await hass.config_entries.flow.async_init("tg_miniapp", context={"source": "user"})
    assert r["type"] == "form"
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"public_url": "http://bad", "menu_text": "x", "extra_user_ids": ""}
    )
    assert r["errors"]["public_url"] == "https_required"
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"public_url": "https://a.example.com", "menu_text": "x",
                       "extra_user_ids": "", "layout": '{"ac": [{"climate": "light.x"}]}'}
    )
    assert r["errors"]["layout"] == "bad_layout"
    with pytest.MonkeyPatch.context() as mp:
        async def _noop(*a, **k):
            return True
        mp.setattr("custom_components.tg_miniapp.async_setup_entry", _noop)
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"],
            {"public_url": "https://app.example.com/", "menu_text": "Дім", "extra_user_ids": "1, 2", "layout": ""},
        )
    assert r["type"] == "create_entry"
    assert r["options"]["public_url"] == "https://app.example.com"


async def test_layout_name_not_treated_as_entity(hass, setup, hass_client_no_auth):
    client = await hass_client_no_auth()
    r = await client.get("/api/tgapp/state", headers={"X-Telegram-Init-Data": init_data()})
    body = await r.json()
    assert set(body["states"]) == {
        "sensor.deye_battery_soc", "sensor.deye_pv_power", ZONE, HP, AC}
