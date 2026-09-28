"""Guided setup for Evac Relay.

The flow walks a household through each piece in order: location, zones,
county alert sign-up, the email account Home Assistant watches, the scanner
stream, and NWS. Twilio is an advanced step offered at the end.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
import voluptuous as vol

from .classifier import PROFILES, invalid_zones, parse_zones
from .const import (
    CONF_ACK_DISCLAIMER,
    CONF_COUNTY_REGISTERED,
    CONF_IMAP_ENTRIES,
    CONF_IMAP_SENDERS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NWS_ENABLED,
    CONF_NWS_TRIGGERS_ALARM,
    CONF_PROFILE,
    CONF_PUBLIC_URL,
    CONF_SCANNER_URL,
    CONF_TWILIO_ALLOWED_FROM,
    CONF_TWILIO_AUTH_TOKEN,
    CONF_TWILIO_ENABLED,
    CONF_WEBHOOK_ID,
    CONF_ZONES,
    COUNTY_LINKS,
    DEFAULT_PROFILE,
    DOMAIN,
)

TEXT = selector.TextSelector()
URL = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))
SECRET = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
MULTILINE = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
BOOL = selector.BooleanSelector()
COORD = selector.NumberSelector(selector.NumberSelectorConfig(min=-180, max=180, step="any", mode="box"))
PROFILE = selector.SelectSelector(selector.SelectSelectorConfig(options=sorted(PROFILES), translation_key="profile"))


def _imap_selector(hass: HomeAssistant) -> selector.SelectSelector:
    options = [
        selector.SelectOptionDict(value=e.entry_id, label=e.title) for e in hass.config_entries.async_entries("imap")
    ]
    return selector.SelectSelector(selector.SelectSelectorConfig(options=options, multiple=True))


def _links(profile: str) -> dict[str, str]:
    return COUNTY_LINKS.get(profile, COUNTY_LINKS["generic"])


# ---------------------------------------------------------------- validation


def _check_zones(data: dict[str, Any]) -> dict[str, str]:
    if not data.get(CONF_ZONES):
        return {}
    zones = parse_zones(data[CONF_ZONES])
    if not zones or invalid_zones(zones):
        return {CONF_ZONES: "invalid_zones"}
    return {}


def _check_url(data: dict[str, Any], key: str, https_only: bool) -> dict[str, str]:
    url = (data.get(key) or "").strip()
    if not url:
        return {}
    if https_only and not url.startswith("https://"):
        return {key: "https_required"}
    if not url.startswith(("https://", "http://")):
        return {key: "invalid_url"}
    if _is_listen_page(url):
        return {key: "page_not_stream"}
    return {}


def _is_listen_page(url: str) -> bool:
    """A scanner directory's web page (with an embedded player) rather than the stream itself."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host.endswith("broadcastify.com") and parts.path.startswith("/listen/")


def _check_twilio(data: dict[str, Any]) -> dict[str, str]:
    if data.get(CONF_TWILIO_ENABLED) and not data.get(CONF_TWILIO_AUTH_TOKEN):
        return {CONF_TWILIO_AUTH_TOKEN: "twilio_token_required"}
    return _check_url(data, CONF_PUBLIC_URL, https_only=True)


def _check_sources(data: dict[str, Any]) -> dict[str, str]:
    if not (data.get(CONF_IMAP_ENTRIES) or data.get(CONF_TWILIO_ENABLED) or data.get(CONF_NWS_ENABLED)):
        return {"base": "no_sources"}
    return {}


# ---------------------------------------------------------------- setup wizard


class EvacRelayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Step-by-step setup for one monitored home."""

    VERSION = 1

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Welcome, disclaimer, home location, county."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_ACK_DISCLAIMER):
                errors[CONF_ACK_DISCLAIMER] = "disclaimer_required"
            else:
                self.data.update(user_input)
                return await self.async_step_zones()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Home"): TEXT,
                vol.Required(CONF_PROFILE, default=DEFAULT_PROFILE): PROFILE,
                vol.Required(CONF_LATITUDE, default=self.hass.config.latitude): COORD,
                vol.Required(CONF_LONGITUDE, default=self.hass.config.longitude): COORD,
                vol.Required(CONF_ACK_DISCLAIMER, default=False): BOOL,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Find and enter evacuation zones."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_zones(user_input)
            if not errors:
                self.data.update(user_input)
                return await self.async_step_county_alerts()
        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema({vol.Optional(CONF_ZONES, default=""): MULTILINE}),
            description_placeholders=_links(self.data[CONF_PROFILE]),
            errors=errors,
        )

    async def async_step_county_alerts(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Sign up for the county's alert system with an email Home Assistant can read."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_email()
        return self.async_show_form(
            step_id="county_alerts",
            data_schema=vol.Schema({vol.Required(CONF_COUNTY_REGISTERED, default=False): BOOL}),
            description_placeholders=_links(self.data[CONF_PROFILE]),
        )

    async def async_step_email(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the IMAP account(s) that receive county alert email."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_scanner()
        has_imap = bool(self.hass.config_entries.async_entries("imap"))
        return self.async_show_form(
            step_id="email",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_IMAP_ENTRIES, default=[]): _imap_selector(self.hass),
                    vol.Optional(CONF_IMAP_SENDERS, default=""): TEXT,
                }
            ),
            description_placeholders={
                "imap_status": "found" if has_imap else "none yet",
                **_links(self.data[CONF_PROFILE]),
            },
        )

    async def async_step_scanner(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Scanner stream to play during an alarm."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_url(user_input, CONF_SCANNER_URL, https_only=False)
            if not errors:
                self.data.update(user_input)
                return await self.async_step_nws()
        return self.async_show_form(
            step_id="scanner",
            data_schema=vol.Schema({vol.Optional(CONF_SCANNER_URL, default=""): URL}),
            description_placeholders=_links(self.data[CONF_PROFILE]),
            errors=errors,
        )

    async def async_step_nws(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """National Weather Service backup source."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_sources({**self.data, **user_input})
            if not errors:
                self.data.update(user_input)
                return await self.async_step_finish()
        return self.async_show_form(
            step_id="nws",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NWS_ENABLED, default=True): BOOL,
                    vol.Required(CONF_NWS_TRIGGERS_ALARM, default=False): BOOL,
                }
            ),
            errors=errors,
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer advanced setup or finish."""
        return self.async_show_menu(step_id="finish", menu_options=["create", "twilio"])

    async def async_step_twilio(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Advanced: a Twilio number registered with the county for texts and robocalls."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_twilio({**user_input, CONF_TWILIO_ENABLED: True})
            if not errors:
                self.data.update(user_input)
                self.data[CONF_TWILIO_ENABLED] = True
                return await self.async_step_create()
        return self.async_show_form(
            step_id="twilio",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TWILIO_AUTH_TOKEN): SECRET,
                    vol.Optional(CONF_TWILIO_ALLOWED_FROM, default=""): TEXT,
                    vol.Optional(CONF_PUBLIC_URL, default=""): TEXT,
                }
            ),
            errors=errors,
        )

    async def async_step_create(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the entry."""
        self.data.setdefault(CONF_TWILIO_ENABLED, False)
        return self.async_create_entry(
            title=self.data[CONF_NAME],
            data={**self.data, CONF_WEBHOOK_ID: webhook.async_generate_id()},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EvacRelayOptionsFlow()


# ---------------------------------------------------------------- options


class EvacRelayOptionsFlow(OptionsFlow):
    """Change any part of the setup later, grouped the same way as the wizard.

    Fields use suggested values rather than defaults so a cleared field really
    clears. The Twilio auth token is never sent back to the browser; leaving it
    empty keeps the stored token.
    """

    def __init__(self) -> None:
        self.updates: dict[str, Any] = {}

    @property
    def current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options, **self.updates}

    def _suggest(self, key: str, fallback: Any = None) -> dict[str, Any]:
        return {"suggested_value": self.current.get(key, fallback)}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["alerts", "scanner", "twilio", "save"])

    async def async_step_alerts(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {CONF_ZONES: "", CONF_IMAP_ENTRIES: [], CONF_IMAP_SENDERS: "", **user_input}
            errors = _check_zones(user_input) or _check_sources({**self.current, **user_input})
            if not errors:
                self.updates.update(user_input)
                return await self.async_step_init()
        existing = {e.entry_id for e in self.hass.config_entries.async_entries("imap")}
        imap_now = [i for i in self.current.get(CONF_IMAP_ENTRIES, []) if i in existing]
        c = self.current
        schema = vol.Schema(
            {
                vol.Optional(CONF_ZONES, description=self._suggest(CONF_ZONES, "")): MULTILINE,
                vol.Required(CONF_PROFILE, default=c.get(CONF_PROFILE, DEFAULT_PROFILE)): PROFILE,
                vol.Required(CONF_COUNTY_REGISTERED, default=c.get(CONF_COUNTY_REGISTERED, False)): BOOL,
                vol.Optional(CONF_IMAP_ENTRIES, description={"suggested_value": imap_now}): _imap_selector(self.hass),
                vol.Optional(CONF_IMAP_SENDERS, description=self._suggest(CONF_IMAP_SENDERS, "")): TEXT,
                vol.Required(CONF_NWS_ENABLED, default=c.get(CONF_NWS_ENABLED, True)): BOOL,
                vol.Required(CONF_NWS_TRIGGERS_ALARM, default=c.get(CONF_NWS_TRIGGERS_ALARM, False)): BOOL,
                vol.Required(CONF_LATITUDE, default=c.get(CONF_LATITUDE, self.hass.config.latitude)): COORD,
                vol.Required(CONF_LONGITUDE, default=c.get(CONF_LONGITUDE, self.hass.config.longitude)): COORD,
            }
        )
        return self.async_show_form(step_id="alerts", data_schema=schema, errors=errors)

    async def async_step_scanner(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {CONF_SCANNER_URL: "", **user_input}
            errors = _check_url(user_input, CONF_SCANNER_URL, https_only=False)
            if not errors:
                self.updates.update(user_input)
                return await self.async_step_init()
        schema = vol.Schema({vol.Optional(CONF_SCANNER_URL, description=self._suggest(CONF_SCANNER_URL, "")): URL})
        return self.async_show_form(step_id="scanner", data_schema=schema, errors=errors)

    async def async_step_twilio(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {CONF_TWILIO_ALLOWED_FROM: "", CONF_PUBLIC_URL: "", **user_input}
            if not user_input.get(CONF_TWILIO_AUTH_TOKEN):
                user_input[CONF_TWILIO_AUTH_TOKEN] = self.current.get(CONF_TWILIO_AUTH_TOKEN, "")
            errors = _check_twilio(user_input) or _check_sources({**self.current, **user_input})
            if not errors:
                self.updates.update(user_input)
                return await self.async_step_init()
        c = self.current
        schema = vol.Schema(
            {
                vol.Required(CONF_TWILIO_ENABLED, default=c.get(CONF_TWILIO_ENABLED, False)): BOOL,
                vol.Optional(CONF_TWILIO_AUTH_TOKEN): SECRET,
                vol.Optional(CONF_TWILIO_ALLOWED_FROM, description=self._suggest(CONF_TWILIO_ALLOWED_FROM, "")): TEXT,
                vol.Optional(CONF_PUBLIC_URL, description=self._suggest(CONF_PUBLIC_URL, "")): TEXT,
            }
        )
        return self.async_show_form(step_id="twilio", data_schema=schema, errors=errors)

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **self.updates})
