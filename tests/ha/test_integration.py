"""End-to-end tests of the integration against a real Home Assistant core."""

from __future__ import annotations

from datetime import timedelta
import hashlib
from pathlib import Path
from unittest.mock import patch

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.aiohttp import MockRequest
from homeassistant.util.yaml import dump, load_yaml
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
import voluptuous as vol

from custom_components.evac_relay import BLUEPRINT_SRC, twilio_sig
from custom_components.evac_relay.const import DOMAIN, EVENT_ALARM, EVENT_MESSAGE, NWS_API

TOKEN = "tok_123"
PUBLIC = "https://hooks.example.test/evac"
BASE = {
    "name": "Home",
    "zones": "LAK-E123",
    "profile": "lake_county_ca",
    "imap_entries": ["imap_entry_1"],
    "imap_senders": "",
    "nws_enabled": False,
    "nws_triggers_alarm": False,
    "latitude": 38.99,
    "longitude": -122.84,
    "twilio_enabled": True,
    "twilio_auth_token": TOKEN,
    "twilio_allowed_from": "",
    "public_webhook_url": PUBLIC,
    "county_alerts_registered": True,
    "webhook_id": "evac_test_hook",
}
LEVEL = "sensor.evac_relay_home_evacuation_level"


async def _setup(hass: HomeAssistant, **overrides) -> MockConfigEntry:
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, title="Home", data={**BASE, **overrides})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    return entry


def _states(hass):
    return (
        hass.states.get(LEVEL),
        hass.states.get("binary_sensor.evac_relay_home_alarm_active"),
        hass.states.get("binary_sensor.evac_relay_home_alarm_acknowledged"),
    )


async def _ingest(hass, text, **kw):
    await hass.services.async_call(DOMAIN, "ingest", {"text": text, "can_trigger": True, **kw}, blocking=True)
    await hass.async_block_till_done()


def _email(subject, *, initial=True, entry_id="imap_entry_1", age=timedelta(0), sender="alerts@county.example"):
    return {
        "entry_id": entry_id,
        "sender": sender,
        "subject": subject,
        "text": "Details follow.",
        "date": dt_util.utcnow() - age,
        "initial": initial,
        "uid": "1",
    }


# ---------------------------------------------------------------- email


async def test_new_email_order_raises_alarm(hass: HomeAssistant) -> None:
    await _setup(hass)
    alarms = []
    hass.bus.async_listen(EVENT_ALARM, alarms.append)
    # Home Assistant marks the first report of a new message initial=True.
    hass.bus.async_fire("imap_content", _email("EVACUATION ORDER LAK-E123"))
    await hass.async_block_till_done()
    level, active, ack = _states(hass)
    assert (level.state, active.state, ack.state) == ("order", "on", "off")
    assert level.attributes["message"].startswith("EVACUATION ORDER LAK-E123")
    assert alarms
    assert alarms[0].data["level"] == "order"


async def test_address_targeted_email_without_zone_mention(hass: HomeAssistant) -> None:
    await _setup(hass)
    hass.bus.async_fire("imap_content", _email("EVACUATION ORDER for the area north of Hwy 20. Leave now."))
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "order"


async def test_email_filters(hass: HomeAssistant) -> None:
    await _setup(hass, imap_senders="everbridge")
    for data in (
        _email("EVACUATION ORDER LAK-E123", initial=False),  # re-report of the same last message
        _email("EVACUATION ORDER LAK-E123", entry_id="other"),
        _email("EVACUATION ORDER LAK-E123", age=timedelta(hours=5)),
        _email("EVACUATION ORDER LAK-E123", sender="spoof@example.com"),
    ):
        hass.bus.async_fire("imap_content", data)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"
    ok = _email("EVACUATION ORDER LAK-E123", sender="alerts@everbridge.net")
    hass.bus.async_fire("imap_content", ok)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "order"


# ---------------------------------------------------------------- rules


async def test_other_zone_and_clearing_do_not_alarm(hass: HomeAssistant) -> None:
    await _setup(hass)
    events = []
    hass.bus.async_listen(EVENT_MESSAGE, events.append)
    await _ingest(hass, "EVACUATION ORDER for zone LAK-E999")
    await _ingest(hass, "Evacuation order lifted for LAK-E123")
    assert _states(hass)[0].state == "none"
    assert sorted(e.data["decision"] for e in events) == ["clearing", "other_zone"]


async def test_ingest_defaults_to_non_triggering(hass: HomeAssistant) -> None:
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "ingest", {"text": "EVACUATION ORDER LAK-E123"}, blocking=True)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"
    # A non-triggering copy never blocks the real alert that follows.
    await _ingest(hass, "EVACUATION ORDER LAK-E123")
    assert _states(hass)[0].state == "order"


async def test_no_downgrade_ack_and_clear(hass: HomeAssistant) -> None:
    await _setup(hass)
    await _ingest(hass, "EVACUATION ORDER LAK-E123")
    await _ingest(hass, "Evacuation Warning LAK-E123")
    assert _states(hass)[0].state == "order"
    await hass.services.async_call(DOMAIN, "acknowledge", {}, blocking=True)
    assert _states(hass)[2].state == "on"
    await hass.services.async_call(DOMAIN, "clear", {}, blocking=True)
    level, active, ack = _states(hass)
    assert (level.state, active.state, ack.state) == ("none", "off", "off")


async def test_upgrade_resets_acknowledgement(hass: HomeAssistant) -> None:
    await _setup(hass)
    await _ingest(hass, "Evacuation Warning LAK-E123")
    await hass.services.async_call(DOMAIN, "acknowledge", {}, blocking=True)
    await _ingest(hass, "EVACUATION ORDER LAK-E123 now")
    level, _, ack = _states(hass)
    assert level.state == "order"
    assert ack.state == "off"


async def test_duplicates(hass: HomeAssistant) -> None:
    await _setup(hass)
    events = []
    hass.bus.async_listen(EVENT_MESSAGE, events.append)
    for _ in range(3):
        await _ingest(hass, "EVACUATION ORDER LAK-E123")
    assert [e.data["decision"] for e in events] == ["alarm", "duplicate", "duplicate"]
    # The same alert re-sent after someone cleared the alarm must raise it again.
    await hass.services.async_call(DOMAIN, "clear", {}, blocking=True)
    await _ingest(hass, "EVACUATION ORDER LAK-E123")
    assert _states(hass)[0].state == "order"


async def test_other_zone_message_does_not_replace_alarm_text(hass: HomeAssistant) -> None:
    await _setup(hass)
    await _ingest(hass, "EVACUATION ORDER LAK-E123")
    await _ingest(hass, "Evacuation order lifted for LAK-E999")
    assert _states(hass)[0].attributes["message"] == "EVACUATION ORDER LAK-E123"


# ---------------------------------------------------------------- tests vs real alarms


async def test_real_alert_replaces_test(hass: HomeAssistant) -> None:
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "test", {"level": "warning"}, blocking=True)
    await _ingest(hass, "Evacuation Warning LAK-E123")
    level = _states(hass)[0]
    assert (level.state, level.attributes["test"]) == ("warning", False)
    # end_test must not clear the real alarm that replaced the test.
    await hass.services.async_call(DOMAIN, "end_test", {}, blocking=True)
    assert _states(hass)[0].state == "warning"


async def test_test_refused_during_real_alarm(hass: HomeAssistant) -> None:
    await _setup(hass)
    await _ingest(hass, "EVACUATION ORDER LAK-E123")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "test", {"level": "order"}, blocking=True)
    assert _states(hass)[0].attributes["test"] is False


async def test_test_expires_on_its_own(hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "test", {"level": "order"}, blocking=True)
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"


# ---------------------------------------------------------------- Twilio


def _signed(url: str, params: dict[str, str]) -> dict[str, str]:
    return {"X-Twilio-Signature": twilio_sig.compute_signature(TOKEN, url, params)}


async def test_twilio_sms_signed_and_unsigned(hass: HomeAssistant, hass_client_no_auth) -> None:
    entry = await _setup(hass)
    client = await hass_client_no_auth()
    path = f"/api/webhook/{BASE['webhook_id']}?kind=sms"
    params = {"From": "+17075550100", "Body": "EVACUATION ORDER LAK-E123"}

    for _ in range(3):
        bad = await client.post(path, data=params, headers={"X-Twilio-Signature": "nope"})
        assert bad.status == 403
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"twilio_signature_{entry.entry_id}")
    assert _states(hass)[0].state == "none"

    good = await client.post(path, data=params, headers=_signed(f"{PUBLIC}?kind=sms", params))
    assert good.status == 200
    assert "<Response/>" in await good.text()
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "order"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"twilio_signature_{entry.entry_id}") is None


async def test_twilio_allowed_from(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass, twilio_allowed_from="+1 (707) 555-0199")
    client = await hass_client_no_auth()
    params = {"From": "+17075550100", "Body": "EVACUATION ORDER LAK-E123"}
    resp = await client.post(
        f"/api/webhook/{BASE['webhook_id']}?kind=sms", data=params, headers=_signed(f"{PUBLIC}?kind=sms", params)
    )
    assert resp.status == 200
    assert _states(hass)[0].state == "none"


async def test_twilio_override_url_with_query(hass: HomeAssistant, hass_client_no_auth) -> None:
    base = "https://proxy.example.test/evac?t=1&x=a"
    await _setup(hass, public_webhook_url=base)
    client = await hass_client_no_auth()
    call = {"From": "+17075550100", "CallSid": "CA9"}
    resp = await client.post(
        f"/api/webhook/{BASE['webhook_id']}?kind=voice", data=call, headers=_signed(f"{base}&kind=voice", call)
    )
    body = await resp.text()
    assert resp.status == 200
    assert 'transcribeCallback="https://proxy.example.test/evac?t=1&amp;x=a&amp;kind=transcription"' in body


async def test_twilio_voice_transcription_without_zone_id(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    hook = f"/api/webhook/{BASE['webhook_id']}"
    call = {"From": "+17075550100", "CallSid": "CA1"}
    resp = await client.post(f"{hook}?kind=voice", data=call, headers=_signed(f"{PUBLIC}?kind=voice", call))
    assert "<Record" in await resp.text()

    tx = {
        "CallSid": "CA1",
        "TranscriptionStatus": "completed",
        "TranscriptionText": "This is an evacuation warning for zone L A K E one two three. Prepare to leave.",
        "RecordingUrl": "https://api.twilio.com/rec/RE1",
    }
    resp = await client.post(f"{hook}?kind=transcription", data=tx, headers=_signed(f"{PUBLIC}?kind=transcription", tx))
    assert resp.status == 200
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "warning"


async def test_twilio_failed_transcription_notifies(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    events = []
    hass.bus.async_listen(EVENT_MESSAGE, events.append)
    client = await hass_client_no_auth()
    tx = {"CallSid": "CA2", "TranscriptionStatus": "failed", "RecordingUrl": "https://api.twilio.com/rec/RE2"}
    resp = await client.post(
        f"/api/webhook/{BASE['webhook_id']}?kind=transcription",
        data=tx,
        headers=_signed(f"{PUBLIC}?kind=transcription", tx),
    )
    assert resp.status == 200
    await hass.async_block_till_done()
    assert events[0].data["decision"] == "transcription_failed"
    assert events[0].data["meta"]["recording_url"].endswith("RE2")


async def test_twilio_url_failure_does_not_break_other_sources(hass: HomeAssistant) -> None:
    with patch("custom_components.evac_relay._resolve_public_url", return_value=None):
        entry = await _setup(hass, public_webhook_url="")
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"twilio_url_unavailable_{entry.entry_id}")
    hass.bus.async_fire("imap_content", _email("EVACUATION ORDER LAK-E123"))
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "order"


# ---------------------------------------------------------------- NWS


async def test_nws_advisory_does_not_alarm_by_default(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(
        NWS_API,
        json={
            "features": [
                {"properties": {"id": "urn:1", "event": "Evacuation Immediate", "headline": "x", "description": "y"}}
            ]
        },
    )
    events = []
    hass.bus.async_listen(EVENT_MESSAGE, events.append)
    await _setup(hass, nws_enabled=True)
    await hass.async_block_till_done()
    assert events
    assert events[0].data["decision"] == "advisory"
    assert _states(hass)[0].state == "none"
    assert hass.states.get("sensor.evac_relay_home_nws_last_poll").state not in ("unknown", "unavailable")


async def test_nws_cem_never_triggers(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(
        NWS_API,
        json={"features": [{"properties": {"id": "urn:3", "event": "Civil Emergency Message", "headline": "Boil"}}]},
    )
    await _setup(hass, nws_enabled=True, nws_triggers_alarm=True)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"


async def test_nws_alert_not_replayed_after_reload(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(
        NWS_API,
        json={"features": [{"properties": {"id": "urn:2", "event": "Evacuation Immediate", "headline": "x"}}]},
    )
    entry = await _setup(hass, nws_enabled=True, nws_triggers_alarm=True)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "order"
    await hass.services.async_call(DOMAIN, "clear", {}, blocking=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"


# ---------------------------------------------------------------- lifecycle


async def test_state_survives_reload(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await _ingest(hass, "EVACUATION ORDER LAK-E123")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "order"


async def test_bundled_media_served(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    for name in ("order", "warning", "shelter", "test"):
        resp = await client.get(f"/evac_relay_media/evac_{name}.mp4")
        assert resp.status == 200


async def test_admin_only_services(hass: HomeAssistant, hass_admin_user) -> None:
    await _setup(hass)
    hass_admin_user.groups = []
    ctx = Context(user_id=hass_admin_user.id)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "clear", {}, blocking=True, context=ctx)
    await hass.services.async_call(DOMAIN, "acknowledge", {}, blocking=True, context=ctx)


async def test_repairs_and_blueprint_install(hass: HomeAssistant) -> None:
    entry = await _setup(hass, imap_entries=[], county_alerts_registered=False)
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, f"no_email_source_{entry.entry_id}")
    assert reg.async_get_issue(DOMAIN, f"county_not_registered_{entry.entry_id}")
    bp = Path(hass.config.path("blueprints/automation/evac_relay/evac_relay_response.yaml"))
    assert await hass.async_add_executor_job(bp.exists)

    hass.config_entries.async_update_entry(
        entry, options={"imap_entries": ["imap_entry_1"], "county_alerts_registered": True}
    )
    await hass.async_block_till_done()
    assert reg.async_get_issue(DOMAIN, f"no_email_source_{entry.entry_id}") is None
    assert reg.async_get_issue(DOMAIN, f"county_not_registered_{entry.entry_id}") is None
    # The selected IMAP entry doesn't exist in this test instance.
    assert reg.async_get_issue(DOMAIN, f"imap_entry_missing_{entry.entry_id}")


async def test_blueprint_unmodified_copy_is_updated(hass: HomeAssistant) -> None:
    """An installed copy that Evac Relay wrote (hash remembered) is replaced by a newer bundled one."""
    entry = await _setup(hass)
    bp = Path(hass.config.path("blueprints/automation/evac_relay/evac_relay_response.yaml"))
    bundled = await hass.async_add_executor_job(BLUEPRINT_SRC.read_bytes)
    old = b"blueprint:\n  name: older Evac Relay blueprint\n  domain: automation\n"
    await hass.async_add_executor_job(bp.write_bytes, old)
    await Store(hass, 1, f"{DOMAIN}.blueprint").async_save({"installed_sha256": hashlib.sha256(old).hexdigest()})

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await hass.async_add_executor_job(bp.read_bytes) == bundled
    assert ir.async_get(hass).async_get_issue(DOMAIN, "blueprint_outdated") is None


async def test_blueprint_reserialized_copy_counts_as_current(hass: HomeAssistant) -> None:
    """A copy saved through Home Assistant's blueprint API has different bytes but the same YAML."""
    entry = await _setup(hass)
    bp = Path(hass.config.path("blueprints/automation/evac_relay/evac_relay_response.yaml"))
    reserialized = dump(load_yaml(str(BLUEPRINT_SRC))).encode()
    assert reserialized != await hass.async_add_executor_job(BLUEPRINT_SRC.read_bytes)
    await hass.async_add_executor_job(bp.write_bytes, reserialized)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await hass.async_add_executor_job(bp.read_bytes) == reserialized
    assert ir.async_get(hass).async_get_issue(DOMAIN, "blueprint_outdated") is None
    # It is now known as an Evac Relay copy, so a later bundled change replaces it.
    store = await Store(hass, 1, f"{DOMAIN}.blueprint").async_load()
    assert store["installed_sha256"] == hashlib.sha256(reserialized).hexdigest()


async def test_blueprint_edited_copy_is_kept(hass: HomeAssistant) -> None:
    """A copy the user edited is left alone and flagged in Repairs."""
    entry = await _setup(hass)
    bp = Path(hass.config.path("blueprints/automation/evac_relay/evac_relay_response.yaml"))
    original = await hass.async_add_executor_job(bp.read_bytes)
    edited = original.replace(b"default: 0.8", b"default: 0.5")
    assert edited != original
    await hass.async_add_executor_job(bp.write_bytes, edited)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await hass.async_add_executor_job(bp.read_bytes) == edited
    assert ir.async_get(hass).async_get_issue(DOMAIN, "blueprint_outdated")


# ---------------------------------------------------------------- setup wizard and options


async def test_wizard_basic_path(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(NWS_API, json={"features": []})
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    fid = flow["flow_id"]
    user = {"name": "Home", "profile": "lake_county_ca", "latitude": 38.99, "longitude": -122.84}
    r = await hass.config_entries.flow.async_configure(fid, {**user, "acknowledge_disclaimer": False})
    assert r["errors"] == {"acknowledge_disclaimer": "disclaimer_required"}
    r = await hass.config_entries.flow.async_configure(fid, {**user, "acknowledge_disclaimer": True})
    assert r["step_id"] == "zones"
    assert "protect.genasys.com" in r["description_placeholders"]["zone_url"]
    r = await hass.config_entries.flow.async_configure(fid, {"zones": "LAK-E123"})
    assert r["step_id"] == "county_alerts"
    r = await hass.config_entries.flow.async_configure(fid, {"county_alerts_registered": True})
    assert r["step_id"] == "email"
    r = await hass.config_entries.flow.async_configure(fid, {"imap_entries": [], "imap_senders": ""})
    assert r["step_id"] == "scanner"
    r = await hass.config_entries.flow.async_configure(fid, {"scanner_url": "not a url"})
    assert r["errors"] == {"scanner_url": "invalid_url"}
    r = await hass.config_entries.flow.async_configure(fid, {"scanner_url": "https://stream.example/feed"})
    assert r["step_id"] == "nws"
    r = await hass.config_entries.flow.async_configure(fid, {"nws_enabled": False, "nws_triggers_alarm": False})
    assert r["errors"] == {"base": "no_sources"}
    r = await hass.config_entries.flow.async_configure(fid, {"nws_enabled": True, "nws_triggers_alarm": False})
    assert r["type"] == "menu"
    r = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "create"})
    assert r["type"] == "create_entry"
    assert r["data"]["zones"] == "LAK-E123"
    assert r["data"]["twilio_enabled"] is False
    assert r["data"]["webhook_id"]


async def test_wizard_twilio_path(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(NWS_API, json={"features": []})
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    fid = flow["flow_id"]
    await hass.config_entries.flow.async_configure(
        fid, {"name": "Home", "profile": "generic", "latitude": 1, "longitude": 1, "acknowledge_disclaimer": True}
    )
    await hass.config_entries.flow.async_configure(fid, {"zones": ""})
    await hass.config_entries.flow.async_configure(fid, {"county_alerts_registered": False})
    await hass.config_entries.flow.async_configure(fid, {"imap_entries": []})
    await hass.config_entries.flow.async_configure(fid, {"scanner_url": ""})
    await hass.config_entries.flow.async_configure(fid, {"nws_enabled": True, "nws_triggers_alarm": False})
    r = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "twilio"})
    assert r["step_id"] == "twilio"
    r = await hass.config_entries.flow.async_configure(
        fid, {"twilio_auth_token": "t", "public_webhook_url": "http://x"}
    )
    assert r["errors"] == {"public_webhook_url": "https_required"}
    r = await hass.config_entries.flow.async_configure(fid, {"twilio_auth_token": "t"})
    assert r["type"] == "create_entry"
    assert r["data"]["twilio_enabled"] is True


async def test_options_clear_fields_and_keep_token(hass: HomeAssistant) -> None:
    entry = await _setup(hass, imap_senders="everbridge", scanner_url="https://s.example/a")
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == "menu"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "scanner"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "twilio"})
    token_key = next(k for k in r["data_schema"].schema if str(k) == "twilio_auth_token")
    assert token_key.default is vol.UNDEFINED
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"twilio_enabled": True})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "save"})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["scanner_url"] == ""
    assert entry.options["twilio_auth_token"] == TOKEN


async def test_twilio_via_nabu_casa_mock_request(hass: HomeAssistant) -> None:
    """Cloudhooks deliver a MockRequest without rel_url."""
    await _setup(hass)
    params = {"From": "+17075550100", "Body": "EVACUATION ORDER LAK-E123"}
    body = "&".join(f"{k}={v.replace('+', '%2B').replace(' ', '+')}" for k, v in params.items())
    request = MockRequest(
        content=body.encode(),
        mock_source="cloud",
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **_signed(f"{PUBLIC}?kind=sms", params),
        },
        query_string="kind=sms",
    )
    resp = await webhook.async_handle_webhook(hass, BASE["webhook_id"], request)
    assert resp.status == 200
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "order"


async def test_email_not_replayed_after_restart(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    email = _email("EVACUATION ORDER LAK-E123")
    hass.bus.async_fire("imap_content", email)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "clear", {}, blocking=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire("imap_content", email)  # HA re-reports the newest message on start
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"


async def test_display_name_does_not_pass_sender_filter(hass: HomeAssistant) -> None:
    await _setup(hass, imap_senders="everbridge")
    spoof = {
        **_email("EVACUATION ORDER LAK-E123", sender="x@evil.example"),
        "headers": {"From": "everbridge <x@evil.example>"},
    }
    hass.bus.async_fire("imap_content", spoof)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"


async def test_test_expiry_survives_reload(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "test", {"level": "warning"}, blocking=True)
    await hass.services.async_call(DOMAIN, "acknowledge", {}, blocking=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _states(hass)[0].state == "none"
