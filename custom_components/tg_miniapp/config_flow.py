"""Config flow for the Telegram Mini App."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .const import (
    CONF_EXTRA_USER_IDS,
    CONF_LAYOUT,
    CONF_MENU_TEXT,
    CONF_PUBLIC_URL,
    DEFAULT_MENU_TEXT,
    DOMAIN,
    TELEGRAM_DOMAIN,
    LayoutError,
    parse_layout,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PUBLIC_URL, default=defaults.get(CONF_PUBLIC_URL, "https://")): str,
            vol.Optional(
                CONF_MENU_TEXT, default=defaults.get(CONF_MENU_TEXT, DEFAULT_MENU_TEXT)
            ): str,
            vol.Optional(
                CONF_EXTRA_USER_IDS, default=defaults.get(CONF_EXTRA_USER_IDS, "")
            ): str,
            vol.Optional(
                CONF_LAYOUT, default=defaults.get(CONF_LAYOUT, "")
            ): TextSelector(TextSelectorConfig(multiline=True)),
        }
    )


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    url = user_input[CONF_PUBLIC_URL].strip()
    if not url.startswith("https://") or len(url) < 12:
        errors[CONF_PUBLIC_URL] = "https_required"
    for raw in user_input.get(CONF_EXTRA_USER_IDS, "").replace(";", ",").split(","):
        if raw.strip() and not raw.strip().isdigit():
            errors[CONF_EXTRA_USER_IDS] = "bad_ids"
    raw = (user_input.get(CONF_LAYOUT) or "").strip()
    if raw:
        try:
            parse_layout(raw)
        except LayoutError:
            errors[CONF_LAYOUT] = "bad_layout"
    return errors


class TgMiniAppConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if not self.hass.config_entries.async_entries(TELEGRAM_DOMAIN):
            return self.async_abort(reason="no_telegram_bot")

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                user_input[CONF_PUBLIC_URL] = user_input[CONF_PUBLIC_URL].strip().rstrip("/")
                return self.async_create_entry(
                    title="Telegram Mini App", data={}, options=user_input
                )
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TgMiniAppOptionsFlow()


class TgMiniAppOptionsFlow(OptionsFlow):
    """Change URL, button text or extra users later."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                user_input[CONF_PUBLIC_URL] = user_input[CONF_PUBLIC_URL].strip().rstrip("/")
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or dict(self.config_entry.options)),
            errors=errors,
        )
