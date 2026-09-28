"""The bundled blueprint loads in Home Assistant and drives the alarm correctly."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.evac_relay.const import DOMAIN

LEVEL = "sensor.evac_relay_home_evacuation_level"
DATA = {
    "name": "Home",
    "zones": "LAK-E123",
    "profile": "lake_county_ca",
    "imap_entries": [],
    "nws_enabled": False,
    "twilio_enabled": False,
    "scanner_url": "https://stream.example/feed",
    "county_alerts_registered": True,
    "webhook_id": "wh",
}


async def _setup(hass: HomeAssistant, phones: str = "notify.mobile_app_phone"):
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, title="Home", data=DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    calls = {
        "notify": async_mock_service(hass, "notify", "mobile_app_phone"),
        "overlay": async_mock_service(hass, "notify", "living_room_tv"),
        "play": async_mock_service(hass, "media_player", "play_media"),
        "stop": async_mock_service(hass, "media_player", "media_stop"),
        "volume": async_mock_service(hass, "media_player", "volume_set"),
        "announce": async_mock_service(hass, "assist_satellite", "announce"),
        "remote": async_mock_service(hass, "remote", "turn_on"),
    }
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "evac_relay/evac_relay_response.yaml",
                    "input": {
                        "level_sensor": LEVEL,
                        "phones": phones,
                        "speakers": ["media_player.kitchen"],
                        "tts_entity": "tts.piper",
                        "satellites": ["assist_satellite.voice_pe"],
                        "tv_remotes": ["remote.apple_tv"],
                        "tv_players": ["media_player.apple_tv"],
                        "tv_overlays": "notify.living_room_tv",
                        "scanner_players": ["media_player.den"],
                        "ha_lan_url": "http://192.168.1.10:8123/",
                    },
                }
            }
        },
    )
    await hass.async_block_till_done()
    return entry, calls


async def _spin(hass: HomeAssistant) -> None:
    """Let the running alarm loop progress without waiting for it to finish.

    hass.async_block_till_done() would wait for the whole alarm, which by
    design runs until acknowledged.
    """
    for _ in range(80):
        await asyncio.sleep(0)


async def _tick(hass: HomeAssistant, seconds: int) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await _spin(hass)


async def _alert(hass, text):
    await hass.services.async_call(DOMAIN, "ingest", {"text": text, "can_trigger": True}, blocking=True)
    await _spin(hass)


def _played(calls):
    return [c.data["media"]["media_content_id"] for c in calls["play"]]


async def test_order_runs_full_alarm_until_acknowledged(hass: HomeAssistant) -> None:
    entry, calls = await _setup(hass)
    await _alert(hass, "EVACUATION ORDER for LAK-E123")
    assert hass.states.get(LEVEL).state == "order"

    phone = calls["notify"][0].data
    assert phone["title"] == "EVACUATION ORDER"
    assert phone["data"]["push"]["interruption-level"] == "critical"
    assert phone["data"]["actions"][0]["action"] == f"EVAC_RELAY_ACK_{entry.entry_id}"
    assert calls["remote"]
    assert calls["volume"][0].data["volume_level"] == 0.8

    await _tick(hass, 5)
    played = _played(calls)
    assert "https://stream.example/feed" in played
    assert "http://192.168.1.10:8123/evac_relay_media/evac_order.mp4" in played
    assert any(p.startswith("media-source://tts/tts.piper?message=Evacuation") for p in played)
    assert calls["announce"]
    assert calls["overlay"]
    first_round = len(calls["announce"])

    await _tick(hass, 70)
    assert len(calls["announce"]) > first_round

    await hass.services.async_call(DOMAIN, "acknowledge", {}, blocking=True)
    await _spin(hass)
    after_ack = len(calls["announce"])
    await _tick(hass, 200)
    assert len(calls["announce"]) == after_ack


async def test_missing_phone_service_does_not_stop_alarm(hass: HomeAssistant) -> None:
    _, calls = await _setup(hass, phones="notify.mobile_app_retired_phone\nnotify.mobile_app_phone")
    await _alert(hass, "EVACUATION ORDER for LAK-E123")
    await _tick(hass, 5)
    assert calls["notify"]
    assert calls["announce"]
    assert calls["play"]


async def test_phone_acknowledge_button_is_per_home(hass: HomeAssistant) -> None:
    entry, _ = await _setup(hass)
    await _alert(hass, "EVACUATION WARNING LAK-E123")
    hass.bus.async_fire("mobile_app_notification_action", {"action": "EVAC_RELAY_ACK_someotherhome"})
    await _spin(hass)
    assert hass.states.get(LEVEL).attributes["acknowledged"] is False
    hass.bus.async_fire("mobile_app_notification_action", {"action": f"EVAC_RELAY_ACK_{entry.entry_id}"})
    await hass.async_block_till_done()
    assert hass.states.get(LEVEL).attributes["acknowledged"] is True
    assert hass.states.get(LEVEL).state == "warning"


async def test_reload_does_not_end_alarm(hass: HomeAssistant) -> None:
    entry, calls = await _setup(hass)
    await _alert(hass, "EVACUATION ORDER for LAK-E123")
    await _tick(hass, 5)
    before = len(calls["announce"])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await _spin(hass)
    await _tick(hass, 70)
    assert len(calls["announce"]) > before


async def test_alarm_resumes_after_restart(hass: HomeAssistant) -> None:
    hass.set_state(CoreState.not_running)
    _, calls = await _setup(hass)
    manager = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    manager.state.active = True
    manager.state.level = "order"
    manager.push_update()
    await _spin(hass)
    await hass.async_start()
    await _tick(hass, 5)
    assert calls["notify"]
    assert calls["announce"]


async def test_clear_stops_scanner(hass: HomeAssistant) -> None:
    _, calls = await _setup(hass)
    await _alert(hass, "EVACUATION ORDER for LAK-E123")
    await _tick(hass, 5)
    await hass.services.async_call(DOMAIN, "clear", {}, blocking=True)
    await hass.async_block_till_done()
    assert calls["stop"][0].data["entity_id"] == ["media_player.den"]


async def test_test_alarm_runs_once_and_ends(hass: HomeAssistant) -> None:
    _, calls = await _setup(hass)
    await hass.services.async_call(DOMAIN, "test", {"level": "order"}, blocking=True)
    await _spin(hass)
    assert calls["notify"][0].data["title"] == "TEST EVACUATION ORDER"
    await _tick(hass, 5)
    played = _played(calls)
    assert "https://stream.example/feed" not in played
    assert "http://192.168.1.10:8123/evac_relay_media/evac_test.mp4" in played
    await _tick(hass, 70)
    assert hass.states.get(LEVEL).state == "none"


async def test_real_alert_during_test_takes_over(hass: HomeAssistant) -> None:
    _, calls = await _setup(hass)
    await hass.services.async_call(DOMAIN, "test", {"level": "warning"}, blocking=True)
    await _spin(hass)
    await _alert(hass, "EVACUATION WARNING LAK-E123")
    await _tick(hass, 5)
    assert calls["notify"][-1].data["title"] == "EVACUATION WARNING"
    await _tick(hass, 700)
    # The test's cleanup never clears the real alarm.
    assert hass.states.get(LEVEL).state == "warning"


async def test_ending_a_test_does_not_stop_scanner_speakers(hass: HomeAssistant) -> None:
    _, calls = await _setup(hass)
    await hass.services.async_call(DOMAIN, "test", {"level": "warning"}, blocking=True)
    await _spin(hass)
    await hass.services.async_call(DOMAIN, "end_test", {}, blocking=True)
    await hass.async_block_till_done()
    assert not calls["stop"]
