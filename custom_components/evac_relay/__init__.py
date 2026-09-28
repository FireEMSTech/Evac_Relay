"""Evac Relay: relay county evacuation alerts into Home Assistant."""

from __future__ import annotations

import filecmp
import hashlib
import logging
from pathlib import Path
import shutil
from typing import Any

from homeassistant.components import persistent_notification, webhook
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.yaml import load_yaml
import voluptuous as vol

from .classifier import Level
from .const import (
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
    MEDIA_URL_PATH,
)
from .manager import EvacManager
from .sources import TwilioWebhook, async_setup_imap, async_setup_nws

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
BLUEPRINT_SRC = Path(__file__).parent / "blueprints" / "evac_relay_response.yaml"
BLUEPRINT_STORE_VERSION = 1
ISSUE_KEYS = (
    "no_email_source",
    "imap_entry_missing",
    "county_not_registered",
    "twilio_url_unavailable",
    "twilio_signature",
    "twilio_error",
)

type EvacConfigEntry = ConfigEntry[EvacManager]

ATTR_ENTRY = "config_entry_id"
SERVICE_BASE = vol.Schema({vol.Optional(ATTR_ENTRY): cv.string})
SERVICE_TEST = SERVICE_BASE.extend({vol.Optional("level", default="warning"): vol.In(["warning", "shelter", "order"])})
SERVICE_INGEST = SERVICE_BASE.extend(
    {
        vol.Required("text"): cv.string,
        vol.Optional("source", default="manual"): cv.string,
        vol.Optional("can_trigger", default=False): cv.boolean,
    }
)
SERVICE_DISPATCH = vol.Schema(
    {
        vol.Required("calls"): vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required("action"): cv.string,
                        vol.Optional("target", default=dict): dict,
                        vol.Optional("data", default=dict): dict,
                    }
                )
            ],
        )
    }
)


def merged(entry: ConfigEntry) -> dict[str, Any]:
    """Options override the values captured at setup."""
    return {**entry.data, **entry.options}


# ---------------------------------------------------------------- domain setup


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register bundled media and domain services once."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(MEDIA_URL_PATH, str(Path(__file__).parent / "media"), False)]
    )

    def managers(call: ServiceCall) -> list[EvacManager]:
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
            and (ATTR_ENTRY not in call.data or e.entry_id == call.data[ATTR_ENTRY])
        ]
        if not entries:
            raise ServiceValidationError("No loaded Evac Relay entry matches")
        return [e.runtime_data for e in entries]

    @callback
    def acknowledge(call: ServiceCall) -> None:
        for m in managers(call):
            m.async_acknowledge()

    @callback
    def clear(call: ServiceCall) -> None:
        for m in managers(call):
            m.async_clear()

    @callback
    def test(call: ServiceCall) -> None:
        for m in managers(call):
            m.async_test(Level.from_key(call.data["level"]))

    @callback
    def end_test(call: ServiceCall) -> None:
        for m in managers(call):
            m.async_end_test()

    @callback
    def ingest(call: ServiceCall) -> None:
        for m in managers(call):
            m.async_ingest(call.data["source"], call.data["text"], {}, may_trigger=call.data["can_trigger"])

    async def dispatch(call: ServiceCall) -> None:
        """Fire each call independently so one missing or failing target never stops the others."""
        for item in call.data["calls"]:
            domain, _, service = item["action"].partition(".")
            if not service or not hass.services.has_service(domain, service):
                _LOGGER.warning("Evac Relay skipped %s: action not found", item["action"])
                continue
            try:
                await hass.services.async_call(
                    domain, service, item["data"], target=item["target"] or None, blocking=False
                )
            except Exception:
                _LOGGER.exception("Evac Relay action %s failed", item["action"])

    # Anyone in the household may acknowledge; everything that can silence or fake an alarm is admin-only.
    hass.services.async_register(DOMAIN, "acknowledge", acknowledge, SERVICE_BASE)
    async_register_admin_service(hass, DOMAIN, "clear", clear, SERVICE_BASE)
    async_register_admin_service(hass, DOMAIN, "test", test, SERVICE_TEST)
    async_register_admin_service(hass, DOMAIN, "end_test", end_test, SERVICE_BASE)
    async_register_admin_service(hass, DOMAIN, "ingest", ingest, SERVICE_INGEST)
    async_register_admin_service(hass, DOMAIN, "dispatch", dispatch, SERVICE_DISPATCH)
    return True


# ---------------------------------------------------------------- entry setup


async def async_setup_entry(hass: HomeAssistant, entry: EvacConfigEntry) -> bool:
    """Set up one monitored home. Sources are independent: one failing never blocks the others."""
    conf = merged(entry)
    manager = EvacManager(
        hass,
        entry.entry_id,
        conf.get(CONF_NAME, "Home"),
        conf.get(CONF_ZONES, ""),
        conf.get(CONF_PROFILE, DEFAULT_PROFILE),
    )
    await manager.async_load()
    manager.scanner_url = (conf.get(CONF_SCANNER_URL) or "").strip() or None
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if imap_ids := conf.get(CONF_IMAP_ENTRIES):
        senders = (conf.get(CONF_IMAP_SENDERS) or "").split(",")
        entry.async_on_unload(async_setup_imap(hass, manager, imap_ids, senders))

    if conf.get(CONF_NWS_ENABLED, True):
        entry.async_on_unload(
            async_setup_nws(
                hass,
                entry,
                manager,
                conf.get(CONF_LATITUDE, hass.config.latitude),
                conf.get(CONF_LONGITUDE, hass.config.longitude),
                conf.get(CONF_NWS_TRIGGERS_ALARM, False),
            )
        )

    if conf.get(CONF_TWILIO_ENABLED) and conf.get(CONF_TWILIO_AUTH_TOKEN):
        _setup_twilio(hass, entry, manager, conf)
    else:
        ir.async_delete_issue(hass, DOMAIN, f"twilio_url_unavailable_{entry.entry_id}")

    _sync_repairs(hass, entry, conf, manager.name)

    @callback
    def _entry_changed(change: Any, changed: ConfigEntry) -> None:
        if changed.domain == "imap":
            _sync_repairs(hass, entry, conf, manager.name)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _entry_changed))
    entry.async_create_background_task(hass, _install_blueprint(hass), f"{DOMAIN}_blueprint")
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _setup_twilio(hass: HomeAssistant, entry: EvacConfigEntry, manager: EvacManager, conf: dict[str, Any]) -> None:
    """Register the webhook first, then resolve its public URL; retry when the cloud connects."""
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    issue_id = f"twilio_url_unavailable_{entry.entry_id}"
    resolved: dict[str, str | None] = {"url": None}

    async def resolve() -> str | None:
        if resolved["url"]:
            return resolved["url"]
        url = await _resolve_public_url(hass, webhook_id, conf)
        if url:
            resolved["url"] = url
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            _notify_twilio_urls(hass, entry, manager, url)
        return url

    handler = TwilioWebhook(
        hass,
        manager,
        conf[CONF_TWILIO_AUTH_TOKEN],
        [n for n in (conf.get(CONF_TWILIO_ALLOWED_FROM) or "").split(",") if n.strip()],
        resolve,
    )
    webhook.async_register(
        hass, DOMAIN, f"Evac Relay {manager.name}", webhook_id, handler.handle, allowed_methods=["POST"]
    )
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))

    async def first_resolve() -> None:
        if not await resolve():
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="twilio_url_unavailable",
                translation_placeholders={"name": manager.name},
            )

    entry.async_create_background_task(hass, first_resolve(), f"{DOMAIN}_twilio_url")

    if "cloud" in hass.config.components:
        from homeassistant.components import cloud  # noqa: PLC0415

        @callback
        def _cloud_changed(state: Any) -> None:
            if not resolved["url"]:
                entry.async_create_background_task(hass, first_resolve(), f"{DOMAIN}_twilio_url_retry")

        entry.async_on_unload(cloud.async_listen_connection_change(hass, _cloud_changed))


async def _resolve_public_url(hass: HomeAssistant, webhook_id: str, conf: dict[str, Any]) -> str | None:
    """Override, then Nabu Casa cloudhook, then a public external URL. None if nothing works yet."""
    if override := (conf.get(CONF_PUBLIC_URL) or "").strip():
        return override
    if "cloud" in hass.config.components:
        from homeassistant.components import cloud  # noqa: PLC0415

        if cloud.async_active_subscription(hass):
            try:
                return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
            except (cloud.CloudNotAvailable, ValueError) as err:
                _LOGGER.info("Nabu Casa cloudhook not available yet: %s", err)
                return None
    try:
        return webhook.async_generate_url(hass, webhook_id, allow_internal=False, allow_ip=False)
    except NoURLAvailableError:
        return None


@callback
def _notify_twilio_urls(hass: HomeAssistant, entry: ConfigEntry, manager: EvacManager, url: str) -> None:
    if not manager.set_twilio_url_notified(url):
        return
    persistent_notification.async_create(
        hass,
        "Set these in your Twilio phone number configuration (HTTP POST):\n\n"
        f"- Messaging webhook: `{url}?kind=sms`\n"
        f"- Voice webhook: `{url}?kind=voice`\n\n"
        "Treat these URLs as secrets.",
        title=f"Evac Relay {manager.name}: Twilio webhook URLs",
        notification_id=f"{DOMAIN}_twilio_{entry.entry_id}",
    )


async def _install_blueprint(hass: HomeAssistant) -> None:
    """Copy the bundled blueprint into /config and keep it current.

    The hash of the copy Evac Relay wrote is remembered, so a newer bundled
    version replaces an installed copy the user never touched (existing
    automations keep their inputs). An edited copy is never overwritten; a
    Repairs issue explains how to take the update instead.
    """
    dest = Path(hass.config.path("blueprints", "automation", DOMAIN, BLUEPRINT_SRC.name))
    store: Store[dict[str, str]] = Store(hass, BLUEPRINT_STORE_VERSION, f"{DOMAIN}.blueprint")
    remembered = ((await store.async_load()) or {}).get("installed_sha256")

    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _same_blueprint(a: Path, b: Path) -> bool:
        """Byte-equal, or the same YAML: Home Assistant re-serializes blueprints saved through its API."""
        if filecmp.cmp(a, b, shallow=False):
            return True
        try:
            return load_yaml(str(a)) == load_yaml(str(b))
        except HomeAssistantError:
            return False

    def _sync() -> tuple[str, str | None]:
        if dest.exists():
            if _same_blueprint(BLUEPRINT_SRC, dest):
                return "current", _sha(dest)
            if _sha(dest) != remembered:
                return "outdated", None
            status = "updated"
        else:
            status = "installed"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BLUEPRINT_SRC, dest)
        return status, _sha(dest)

    status, installed = await hass.async_add_executor_job(_sync)
    if installed and installed != remembered:
        await store.async_save({"installed_sha256": installed})
    if status == "updated":
        _LOGGER.info("Replaced the unmodified Evac Relay blueprint with the bundled version")
        if hass.services.has_service("automation", "reload"):
            await hass.services.async_call("automation", "reload", blocking=True)
    if status == "installed":
        persistent_notification.async_create(
            hass,
            "Last step: turn alerts into alarms. Go to **Settings > Automations & scenes > Blueprints**, "
            "open **Evac Relay: house alarm**, and choose your speakers, TVs, phones, and scanner speakers. "
            "Then press **Test alarm** on the Evac Relay device.",
            title="Evac Relay: create the alarm automation",
            notification_id=f"{DOMAIN}_blueprint",
        )
    if status == "outdated":
        ir.async_create_issue(
            hass,
            DOMAIN,
            "blueprint_outdated",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="blueprint_outdated",
            translation_placeholders={"path": str(dest)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "blueprint_outdated")


@callback
def _sync_repairs(hass: HomeAssistant, entry: ConfigEntry, conf: dict[str, Any], name: str) -> None:
    """Keep setup gaps visible under Settings > Repairs until they are fixed."""
    links = COUNTY_LINKS.get(conf.get(CONF_PROFILE, DEFAULT_PROFILE), COUNTY_LINKS["generic"])
    selected = conf.get(CONF_IMAP_ENTRIES) or []
    existing = {e.entry_id for e in hass.config_entries.async_entries("imap")}
    checks = {
        "no_email_source": not selected,
        "imap_entry_missing": any(i not in existing for i in selected),
        "county_not_registered": not conf.get(CONF_COUNTY_REGISTERED),
    }
    for key, broken in checks.items():
        issue_id = f"{key}_{entry.entry_id}"
        if broken:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=key,
                translation_placeholders={"name": name, **links},
                learn_more_url=links["alert_signup_url"] if key == "county_not_registered" else None,
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _async_reload(hass: HomeAssistant, entry: EvacConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EvacConfigEntry) -> bool:
    """Unload an entry, persisting alarm state first."""
    await entry.runtime_data.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: EvacConfigEntry) -> None:
    """Delete the Nabu Casa cloudhook and repair issues when the entry is removed."""
    for key in ISSUE_KEYS:
        ir.async_delete_issue(hass, DOMAIN, f"{key}_{entry.entry_id}")
    if "cloud" in hass.config.components:
        from homeassistant.components import cloud  # noqa: PLC0415

        try:
            await cloud.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])
        except (cloud.CloudNotAvailable, ValueError):
            _LOGGER.debug("No cloudhook to delete")
