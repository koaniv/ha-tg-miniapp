"""Constants and the default screen layout for the Telegram Mini App."""

from __future__ import annotations

import copy
import json

DOMAIN = "tg_miniapp"
TELEGRAM_DOMAIN = "telegram_bot"

CONF_PUBLIC_URL = "public_url"
CONF_MENU_TEXT = "menu_text"
CONF_EXTRA_USER_IDS = "extra_user_ids"

DEFAULT_MENU_TEXT = "🏠 Дім"

PAGE_URL = "/tgapp"
API_STATE_URL = "/api/tgapp/state"
API_ACTION_URL = "/api/tgapp/action"

INIT_DATA_HEADER = "X-Telegram-Init-Data"
SWITCH_DOMAINS = ("switch.", "input_boolean.")
INIT_DATA_MAX_AGE = 24 * 3600  # seconds a Mini App session stays valid
ACTIONS_PER_MINUTE = 30

# Cloud integrations (e.g. myVAILLANT) poll rarely and report a change late.
# After an action the app shows the requested value as "pending" for up to
# PENDING_TTL seconds and asks HA to refresh the entity at REFRESH_DELAYS.
PENDING_TTL = 240
REFRESH_DELAYS = (20, 90)
# Refresh ticks closer than this are merged into one update_entity call.
REFRESH_MERGE_SECONDS = 15
# After away mode ends, the zones and hot water are re-read from the cloud and
# shown as "syncing" (locked) for up to SYNC_SECONDS.
SYNC_SECONDS = 30
# "Refresh all" asks every integration to poll; cloud APIs have quotas.
REFRESH_COOLDOWN = 60
HOLIDAY_MAX_DAYS = 30
# "Until cancelled" away mode: same length as the myVAILLANT away switch uses.
HOLIDAY_DEFAULT_DAYS = 365

# ---------------------------------------------------------------------------
# Layout: what the Mini App shows. It is stored as JSON in the integration
# options (Settings -> Devices & services -> Telegram Mini App -> Configure),
# so the repository holds no home-specific data. Only entities listed in the
# layout can be read; only zone/AC climates and the heat-pump switch can be
# changed.
# ---------------------------------------------------------------------------
CONF_LAYOUT = "layout"

# Estimated extra load (W) shown before a device is turned on during an outage.
# Override per device with "power_w" in the layout.
DEFAULT_HEATPUMP_POWER_W = 2000
DEFAULT_AC_POWER_W = 1000

EXAMPLE_LAYOUT: dict = {
    "energy": {
        "grid_connected": "binary_sensor.deye_grid_connected",
        "grid_outage_start": "input_datetime.grid_outage_start",
        "grid_voltage": [
            "sensor.deye_grid_l1_voltage",
            "sensor.deye_grid_l2_voltage",
            "sensor.deye_grid_l3_voltage",
        ],
        "battery_soc": "sensor.deye_battery_soc",
        "battery_power": "sensor.deye_battery_power",
        "battery_voltage": "sensor.deye_battery_voltage",
        "battery_temperature": "sensor.deye_battery_temperature",
        "pv_power": "sensor.deye_pv_power",
        "load_power": "sensor.deye_load_power",
        "load_phase": [
            "sensor.deye_load_l1_power",
            "sensor.deye_load_l2_power",
            "sensor.deye_load_l3_power",
        ],
        "day_pv": "sensor.deye_day_pv_energy",
        "day_load": "sensor.deye_day_load_energy",
        "day_import": "sensor.deye_day_grid_import",
        "day_export": "sensor.deye_day_grid_export",
        "inverter_state": "sensor.deye_overall_state",
        "inverter_temp": "sensor.deye_radiator_temperature",
        "battery_capacity_kwh": 10,
        "battery_min_soc": 20,
        "phase_limit_w": 4000,
    },
    "heating": {
        "outdoor": "sensor.outdoor_temperature",
        "pressure": "sensor.system_water_pressure",
        "flow": "sensor.system_flow_temperature",
        "zones": [
            {
                "name": "Living room",
                "icon": "🛋",
                "climate": "climate.zone_living_room",
                "humidity": "sensor.zone_living_room_humidity",
                "special": "sensor.zone_living_room_special_function",
            }
        ],
        "heatpump": {
            "switch": "switch.heatpump",
            "temperature": "sensor.heatpump_temperature",
            "flow": "sensor.heatpump_flow_temperature",
            "power_w": DEFAULT_HEATPUMP_POWER_W,
            # optional: shown as "heating" / "hot water" temperatures
            "temp_heating": "sensor.heatpump_flow_temperature",
            "temp_dhw": "sensor.heatpump_temperature",
        },
        # Domestic hot water (water_heater.*), optional.
        "dhw": {"water_heater": "water_heater.domestic_hot_water_0"},
        # Away / holiday mode switch, optional.
        "away": {"switch": "switch.away_mode"},
    },
    "ac": [
        {"name": "Bedroom", "icon": "🛏", "climate": "climate.bedroom_ac", "power_w": 1000},
    ],
}


class LayoutError(ValueError):
    """Layout JSON is not usable."""


def parse_layout(raw: str | dict | None) -> dict:
    """Parse and sanity-check a layout (JSON text or dict)."""
    if raw is None or raw == "":
        return copy.deepcopy(EXAMPLE_LAYOUT)
    try:
        layout = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
    except ValueError as err:
        raise LayoutError(f"invalid JSON: {err}") from err
    if not isinstance(layout, dict):
        raise LayoutError("layout must be a JSON object")
    energy = layout.setdefault("energy", {})
    heating = layout.setdefault("heating", {})
    layout.setdefault("ac", [])
    heating.setdefault("zones", [])
    heating.setdefault("heatpump", {})
    heating.setdefault("dhw", {})
    heating.setdefault("away", {})
    if not isinstance(energy, dict) or not isinstance(heating, dict):
        raise LayoutError("energy and heating must be objects")
    if not isinstance(layout["ac"], list) or not isinstance(heating["zones"], list):
        raise LayoutError("ac and heating.zones must be lists")
    for key in ("heatpump", "dhw", "away"):
        if not isinstance(heating[key], dict):
            raise LayoutError(f"heating.{key} must be an object")
    wh = heating["dhw"].get("water_heater")
    if wh is not None and not str(wh).startswith("water_heater."):
        raise LayoutError("heating.dhw.water_heater must be a water_heater.* entity")
    away = heating["away"].get("switch")
    if away is not None and not str(away).startswith(SWITCH_DOMAINS):
        raise LayoutError("heating.away.switch must be a switch.* entity")
    for key in ("grid_voltage", "load_phase"):
        energy.setdefault(key, [])
    energy.setdefault("battery_capacity_kwh", 10)
    energy.setdefault("battery_min_soc", 20)
    energy.setdefault("phase_limit_w", 4000)
    for item in [*heating["zones"], *layout["ac"]]:
        if not isinstance(item, dict) or not str(item.get("climate", "")).startswith("climate."):
            raise LayoutError("every zone/ac item needs a climate.* entity")
    sw = heating["heatpump"].get("switch")
    if sw is not None and not str(sw).startswith(SWITCH_DOMAINS):
        raise LayoutError("heatpump.switch must be a switch.* entity")
    if not isinstance(energy.get("phase_limit_w"), (int, float)) or energy["phase_limit_w"] <= 0:
        raise LayoutError("energy.phase_limit_w must be a positive number")
    heating["heatpump"].setdefault("power_w", DEFAULT_HEATPUMP_POWER_W)
    for item in layout["ac"]:
        item.setdefault("power_w", DEFAULT_AC_POWER_W)
    for item in [heating["heatpump"], *layout["ac"]]:
        pw = item["power_w"]
        if isinstance(pw, bool) or not isinstance(pw, (int, float)) or not 0 <= pw <= 20000:
            raise LayoutError("power_w must be a number of watts (0-20000)")
    return layout


def _walk(node) -> list[str]:
    if isinstance(node, str):
        head = node.split(".", 1)[0]
        return [node] if "." in node and head.isidentifier() and " " not in node else []
    if isinstance(node, dict):
        return [e for k, v in node.items() if k not in ("name", "icon") for e in _walk(v)]
    if isinstance(node, list):
        return [e for v in node for e in _walk(v)]
    return []


def readable_entities(layout: dict) -> frozenset[str]:
    return frozenset(_walk(layout))


def climate_entities(layout: dict) -> frozenset[str]:
    return frozenset(
        [z["climate"] for z in layout["heating"]["zones"]]
        + [a["climate"] for a in layout["ac"]]
    )


def switch_entities(layout: dict) -> frozenset[str]:
    heating = layout["heating"]
    found = [heating["heatpump"].get("switch"), heating.get("away", {}).get("switch")]
    return frozenset(sw for sw in found if sw and str(sw).startswith(SWITCH_DOMAINS))


def zone_entities(layout: dict) -> frozenset[str]:
    """Heating-zone climates (locked while away mode is on); ACs are not included."""
    return frozenset(z["climate"] for z in layout["heating"]["zones"])


def away_switch(layout: dict) -> str | None:
    sw = layout["heating"].get("away", {}).get("switch")
    return sw if sw and str(sw).startswith(SWITCH_DOMAINS) else None


def water_heater_entities(layout: dict) -> frozenset[str]:
    wh = layout["heating"].get("dhw", {}).get("water_heater")
    return frozenset([wh] if wh and str(wh).startswith("water_heater.") else [])


# Attributes passed to the frontend (everything else is dropped).
EXPOSED_ATTRS = (
    "friendly_name",
    "unit_of_measurement",
    "current_temperature",
    "temperature",
    "target_temp_step",
    "min_temp",
    "max_temp",
    "hvac_modes",
    "hvac_action",
    "fan_mode",
    "fan_modes",
    "preset_mode",
    "operation_list",
    "operation_mode",
    "holiday_end_date_time",
)
