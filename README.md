# Telegram Mini App for Home Assistant

A small Telegram Mini App served by Home Assistant itself, so no extra server is needed. It opens from your Telegram bot's menu button and shows three screens:

- **Energy**: grid status and outage duration, battery SOC and time left, PV, load per phase, today's totals (Deye / Sunsynk sensors).
- **Heating**: heating zones (current/target temperature, Off / Manual / Schedule), system pressure, flow and outdoor temperature, heat-pump on/off.
- **AC**: power, mode, target temperature and fan speed for each air conditioner.

## Security

- Every API call carries Telegram `initData`, which is checked with HMAC-SHA256 against the token of your existing **Telegram bot** integration.
- Only users in the bot's allowed **private** chats, plus any extra IDs you list, get access.
- Only entities listed in the layout can be read. Only the zone/AC climates and the heat-pump switch can be changed, and every value is validated against the entity's own limits and modes.
- A forged signature counts as a failed login, so HA's IP ban applies. An expired session is answered with 403, so family members with an old session are never banned.
- Actions are rate-limited to 30 per minute per user.

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
