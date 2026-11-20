# Telegram Mini App for Home Assistant

A small Telegram Mini App served by Home Assistant itself, so no extra server is needed. It opens from your Telegram bot's menu button and shows three screens:

- **Energy**: grid status and outage duration, battery SOC and time left, PV, load per phase, today's totals (Deye / Sunsynk sensors).
- **Heating**: outdoor temperature, pressure, flow and hot-water temperature; quick toggles for away mode (until cancelled or 1/3/7 days), all heating zones (back to each zone's last mode, otherwise Schedule) and the heat pump; heating zones (current/target temperature, Off / Manual / Schedule), domestic hot water (temperature and operation mode), heat-pump heating and hot-water temperatures.

While away mode is on, the heating zones and hot water cannot be changed (the API answers 409). When away mode ends, whoever turned it off, the zones and hot water are re-read from the cloud and shown as syncing for a few seconds. The ↻ button next to the tabs asks HA to poll every entity of the layout (at most once per 20 seconds, to respect cloud API quotas).
- **AC**: power, mode, target temperature and fan speed for each air conditioner.

During a grid outage the Heating and AC screens show a "no grid" banner with the battery level. Before turning on the heat pump or an air conditioner, the app shows how much load it adds, the current battery level and roughly how long the battery will last. Set each device's draw with `"power_w"` in the layout (defaults: heat pump 2000 W, AC 1000 W).

Hot water and away mode are optional parts of `heating` in the layout:

```json
"dhw": {"water_heater": "water_heater.xata_domestic_hot_water_0"},
"away": {"switch": "switch.xata_away_mode"}
```

Heat-pump temperatures shown on its card: `"temp_heating"` and `"temp_dhw"` in `heating.heatpump` (fallback: `flow` and `temperature`). Away mode for a number of days uses `mypyllant.set_holiday`; "until cancelled" turns the away switch on.

Cloud integrations such as myVAILLANT poll the cloud rarely, so HA reports a change late. After a command the app shows the requested value with "⏳ застосовується" and asks HA to refresh the entity after 15, 60 and 150 seconds. If HA has not confirmed the change within 4 minutes, the app shows the real state again.

## Security

- Every API call carries Telegram `initData`, which is checked with HMAC-SHA256 against the token of your existing **Telegram bot** integration.
- Only users in the bot's allowed **private** chats, plus any extra IDs you list, get access.
- Only entities listed in the layout can be read. Only the zone/AC climates and the heat-pump switch can be changed, and every value is validated against the entity's own limits and modes.
- A forged signature is answered with 401 and counts as a failed login in HA (a "Login attempt failed" notification; an IP ban only if `login_attempts_threshold` is set in `http:`). Behind a Cloudflare tunnel set `use_x_forwarded_for` and `trusted_proxies`, otherwise every request has the tunnel's IP and a ban would lock out all remote access. An expired session is answered with 403, so family members with an old session are never banned.
- When the integration is disabled or removed, the page and the API answer 404 until HA restarts.
- Actions are rate-limited to 30 per minute per user.
- The page is served with a strict Content-Security-Policy: it can only run its own code and the Telegram SDK and only talk to its own API. Error details from HA stay in the log and are not sent to the client.

## Install

1. In HACS, go to ⋮ → Custom repositories, add this repository's URL and choose type **Integration**. Then download **Telegram Mini App** and restart HA.
2. Go to Settings → Devices & services → Add integration → **Telegram Mini App**.
   - **Public URL**: the HTTPS address of HA, for example a Cloudflare tunnel hostname. The app opens at `<url>/tgapp`.
   - **Layout**: paste your layout JSON. See `EXAMPLE_LAYOUT` in `custom_components/tg_miniapp/const.py` for the format. Leaving it empty uses the example.
3. The integration sets the bot's menu button to open the app. In Telegram, open the bot and tap the button.

To limit what the tunnel exposes, set the Cloudflare public hostname path to `^/(tgapp|api/tgapp)(/.*)?$`.

## Develop

```
pip install pytest-homeassistant-custom-component
pytest
```
