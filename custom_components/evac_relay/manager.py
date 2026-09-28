"""Alert state machine shared by every source and entity."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import hashlib
import logging
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .classifier import PROFILES, Classification, Level, Profile, classify, normalize, parse_zones
from .const import (
    DEDUPE_WINDOW_S,
    DOMAIN,
    EVENT_ALARM,
    EVENT_CLEARED,
    EVENT_MESSAGE,
    SIGNAL_UPDATE,
    TEST_EXPIRE_S,
)

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
MAX_NWS_IDS = 200


@dataclass
class AlarmState:
    """Persisted alarm state; survives restarts so an active order is not lost."""

    level: str = Level.NONE.key
    active: bool = False
    acknowledged: bool = False
    test: bool = False
    # The message and source that raised the current alarm.
    alarm_message: str | None = None
    alarm_source: str | None = None
    activated_at: str | None = None
    # The latest message with evacuation language for any zone.
    last_message: str | None = None
    last_source: str | None = None
    last_level_seen: str | None = None
    last_received_at: str | None = None
    nws_seen: list[str] = field(default_factory=list)
    imap_seen: list[str] = field(default_factory=list)
    test_expires_at: str | None = None
    twilio_url_notified: str | None = None


class EvacManager:
    """Owns classification, escalation rules, persistence, and event emission."""

    def __init__(self, hass: HomeAssistant, entry_id: str, name: str, zones: str, profile: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.name = name
        self.zones = parse_zones(zones)
        self.profile: Profile = PROFILES.get(profile, PROFILES["generic"])
        self.state = AlarmState()
        self.nws_last_poll: datetime | None = None
        self.scanner_url: str | None = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._seen: dict[str, float] = {}
        self._test_timer: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------ persistence

    async def async_load(self) -> None:
        """Restore persisted state."""
        if data := await self._store.async_load():
            known = AlarmState.__dataclass_fields__
            self.state = AlarmState(**{k: v for k, v in data.items() if k in known})
        # A test survives a restart only until its original expiry.
        if self.state.active and self.state.test:
            expires = dt_util.parse_datetime(self.state.test_expires_at or "")
            remaining = (expires - dt_util.utcnow()).total_seconds() if expires else 0
            if remaining <= 0:
                self.async_end_test()
            else:
                self._test_timer = async_call_later(self.hass, remaining, self._expire_test)

    async def async_flush(self) -> None:
        """Write pending state immediately (called on unload)."""
        self._cancel_test_timer()
        await self._store.async_save(asdict(self.state))

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(lambda: asdict(self.state), 1)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry_id))

    @callback
    def push_update(self) -> None:
        """Refresh entities without persisting (diagnostic values only)."""
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry_id))

    # ------------------------------------------------------------ helpers

    @property
    def real_level(self) -> Level:
        """Level of the active non-test alarm, or NONE."""
        if self.state.active and not self.state.test:
            return Level.from_key(self.state.level)
        return Level.NONE

    @callback
    def nws_seen(self, alert_id: str) -> bool:
        """Record an NWS alert id; True if it was already handled (persists across restarts)."""
        if alert_id in self.state.nws_seen:
            return True
        self.state.nws_seen = [*self.state.nws_seen, alert_id][-MAX_NWS_IDS:]
        return False

    @callback
    def imap_seen(self, message_key: str) -> bool:
        """Record a handled email; True if it was already handled (persists across restarts)."""
        if message_key in self.state.imap_seen:
            return True
        self.state.imap_seen = [*self.state.imap_seen, message_key][-MAX_NWS_IDS:]
        self._store.async_delay_save(lambda: asdict(self.state), 1)
        return False

    @callback
    def set_twilio_url_notified(self, url: str) -> bool:
        """Remember the Twilio URL shown to the user; True if it changed."""
        if self.state.twilio_url_notified == url:
            return False
        self.state.twilio_url_notified = url
        self._store.async_delay_save(lambda: asdict(self.state), 1)
        return True

    def _dedupe_key(self, source: str, text: str) -> str:
        return hashlib.sha256(f"{source}|{normalize(text)}".encode()).hexdigest()[:24]

    def _recently_seen(self, key: str) -> bool:
        now = time.monotonic()
        self._seen = {k: t for k, t in self._seen.items() if now - t < DEDUPE_WINDOW_S}
        return key in self._seen

    def _fire_alarm_event(self) -> None:
        self.hass.bus.async_fire(
            EVENT_ALARM,
            {
                "entry_id": self.entry_id,
                "level": self.state.level,
                "test": self.state.test,
                "source": self.state.alarm_source,
                "activated_at": self.state.activated_at,
            },
        )

    # ------------------------------------------------------------ ingest

    @callback
    def async_ingest(
        self,
        source: str,
        text: str,
        meta: dict[str, Any] | None = None,
        *,
        forced_level: Level | None = None,
        assume_zone_hit: bool = False,
        address_targeted: bool = False,
        may_trigger: bool = True,
    ) -> Classification | None:
        """Classify one message from any source and apply escalation rules."""
        if not text or not text.strip():
            return None

        result = classify(text, self.zones, self.profile, address_targeted=address_targeted)
        if forced_level is not None or assume_zone_hit:
            result = Classification(
                forced_level if forced_level is not None else result.level,
                result.clearing,
                result.zone_hit or assume_zone_hit,
                result.matched_zones,
            )

        current = self.real_level
        would_alarm = result.actionable and may_trigger and result.level >= current
        key = self._dedupe_key(source, text)
        # A duplicate is only ignored if it can't change anything: an alert that would
        # raise or re-raise the alarm (for example after it was cleared) always goes through.
        if self._recently_seen(key) and not (would_alarm and result.level > current):
            decision = "duplicate"
        elif would_alarm:
            decision = "alarm"
        elif result.actionable and may_trigger and result.level < current:
            decision = "ignored_lower_level"
        elif result.level > Level.NONE and result.clearing:
            decision = "clearing"
        elif result.level > Level.NONE and not result.zone_hit:
            decision = "other_zone"
        elif result.level > Level.NONE:
            decision = "advisory"
        else:
            decision = "no_evacuation_language"

        if may_trigger and decision != "duplicate":
            self._seen[key] = time.monotonic()

        now = dt_util.utcnow().isoformat()
        clean = text.strip()[:2000]
        if result.level > Level.NONE and decision != "duplicate":
            self.state.last_message = clean
            self.state.last_source = source
            self.state.last_level_seen = result.level.key
            self.state.last_received_at = now

        escalated = False
        if decision == "alarm":
            escalated = result.level > current
            self._cancel_test_timer()
            self.state.level = result.level.key
            self.state.active = True
            self.state.test = False
            self.state.test_expires_at = None
            self.state.alarm_message = clean
            self.state.alarm_source = source
            if escalated:
                self.state.acknowledged = False
                self.state.activated_at = now

        self.hass.bus.async_fire(
            EVENT_MESSAGE,
            {
                "entry_id": self.entry_id,
                "source": source,
                "decision": decision,
                "level": result.level.key,
                "clearing": result.clearing,
                "zone_hit": result.zone_hit,
                "matched_zones": list(result.matched_zones),
                "text": clean[:1000],
                "meta": meta or {},
            },
        )
        _LOGGER.info("Evac message from %s: level=%s decision=%s", source, result.level.key, decision)
        if escalated:
            self._fire_alarm_event()
        self._changed()
        return result

    # ------------------------------------------------------------ controls

    @callback
    def async_acknowledge(self) -> None:
        """Stop the repeating alarm; the level stays set."""
        if self.state.active and not self.state.acknowledged:
            self.state.acknowledged = True
            self._changed()

    @callback
    def async_clear(self) -> None:
        """Return to normal. Only a person should do this."""
        self._cancel_test_timer()
        was_active, was_test = self.state.active, self.state.test
        self.state.level = Level.NONE.key
        self.state.active = False
        self.state.acknowledged = False
        self.state.test = False
        self.state.activated_at = None
        self.state.alarm_message = None
        self.state.alarm_source = None
        self.state.test_expires_at = None
        if was_active:
            self.hass.bus.async_fire(EVENT_CLEARED, {"entry_id": self.entry_id, "test": was_test})
        self._changed()

    @callback
    def async_test(self, level: Level) -> None:
        """Raise a clearly labeled test alarm. Refused while a real alarm is active."""
        if self.real_level > Level.NONE:
            raise ServiceValidationError("A real evacuation alarm is active; tests are disabled until it is cleared")
        self._cancel_test_timer()
        self.state.level = level.key
        self.state.active = True
        self.state.acknowledged = False
        self.state.test = True
        self.state.alarm_source = "test"
        self.state.alarm_message = f"Test of the {self.name} evacuation relay."
        now = dt_util.utcnow()
        self.state.activated_at = now.isoformat()
        self.state.test_expires_at = (now + timedelta(seconds=TEST_EXPIRE_S)).isoformat()
        self._test_timer = async_call_later(self.hass, TEST_EXPIRE_S, self._expire_test)
        self._fire_alarm_event()
        self._changed()

    @callback
    def async_end_test(self) -> None:
        """End a test alarm. Does nothing if a real alarm has replaced it."""
        if self.state.active and self.state.test:
            self.async_clear()

    @callback
    def _expire_test(self, _now: Any) -> None:
        self._test_timer = None
        self.async_end_test()

    def _cancel_test_timer(self) -> None:
        if self._test_timer:
            self._test_timer()
            self._test_timer = None
