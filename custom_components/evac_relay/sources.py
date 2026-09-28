"""Alert sources: IMAP email, Twilio SMS/voice webhook, and NWS polling."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import quoteattr

from aiohttp import ClientError, web
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import twilio_sig
from .classifier import Level
from .const import (
    DOMAIN,
    EVENT_MESSAGE,
    MAX_MESSAGE_AGE_S,
    NWS_ADVISORY_ONLY,
    NWS_API,
    NWS_EVENT_LEVELS,
    NWS_POLL_S,
    NWS_USER_AGENT,
    TWIML_EMPTY,
    TWIML_RECORD,
)

if TYPE_CHECKING:
    from .manager import EvacManager

_LOGGER = logging.getLogger(__name__)
BAD_SIGNATURE_ISSUE_AFTER = 3


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


# ---------------------------------------------------------------- IMAP


@callback
def async_setup_imap(
    hass: HomeAssistant,
    manager: EvacManager,
    imap_entry_ids: list[str],
    senders: list[str],
) -> Callable[[], None]:
    """Listen for imap_content events from the selected IMAP config entries.

    Home Assistant sets `initial` True the first time it reports a message and
    False when it re-reports the same last message, so re-reports are skipped
    and everything else is processed. Dedupe in the manager covers the rest.
    """
    wanted = set(imap_entry_ids)
    sender_filters = [s.strip().lower() for s in senders if s.strip()]

    @callback
    def _handle(event: Event) -> None:
        data = event.data
        if data.get("entry_id") not in wanted or data.get("initial") is False:
            return
        # Match the sender address only: display names are chosen by whoever sends the mail.
        sender = str(data.get("sender", "")).lower()
        if sender_filters and not any(f in sender for f in sender_filters):
            _LOGGER.debug("Email from %s does not match the sender filter", sender)
            return
        headers = data.get("headers") or {}
        auth = str(headers.get("Authentication-Results", "")).lower() if isinstance(headers, dict) else ""
        if "dmarc=fail" in auth:
            _LOGGER.warning("Ignoring email from %s that failed DMARC", sender)
            return
        sent = data.get("date")
        if isinstance(sent, datetime):
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=dt_util.UTC)
            if (dt_util.utcnow() - sent).total_seconds() > MAX_MESSAGE_AGE_S:
                _LOGGER.info("Ignoring stale email from %s sent %s", sender, sent)
                return
        # Home Assistant re-reports the newest message after every restart; remember what was handled.
        message_key = f"{data.get('entry_id')}|{data.get('uid')}|{data.get('subject')}|{sent}"
        if manager.imap_seen(message_key):
            return
        text = f"{data.get('subject', '')}\n{data.get('text', '')}"
        manager.async_ingest(
            "email",
            text,
            {"sender": data.get("sender"), "subject": data.get("subject"), "uid": data.get("uid")},
            address_targeted=True,
        )

    return hass.bus.async_listen("imap_content", _handle)


# ---------------------------------------------------------------- Twilio


class TwilioWebhook:
    """Validates and routes Twilio SMS, voice, and transcription callbacks."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: EvacManager,
        auth_token: str,
        allowed_from: list[str],
        public_url: Callable[[], Awaitable[str | None]],
    ) -> None:
        self.hass = hass
        self.manager = manager
        self.auth_token = auth_token
        self.allowed = {_digits(n) for n in allowed_from if _digits(n)}
        self.public_url = public_url
        self.bad_signatures = 0

    @property
    def _issue_id(self) -> str:
        return f"twilio_signature_{self.manager.entry_id}"

    def _xml(self, body: str, status: int = 200) -> web.Response:
        return web.Response(text=body, status=status, content_type="text/xml")

    def _reject(self) -> web.Response:
        self.bad_signatures += 1
        _LOGGER.warning(
            "Rejected a Twilio request with an invalid signature (%s in a row). The webhook URL "
            "configured in Twilio must match the URL Evac Relay reports",
            self.bad_signatures,
        )
        if self.bad_signatures >= BAD_SIGNATURE_ISSUE_AFTER:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="twilio_signature",
                translation_placeholders={"name": self.manager.name},
            )
        return web.Response(status=403)

    async def handle(self, hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        """Entry point registered with the HA webhook component.

        Nabu Casa cloudhooks deliver a MockRequest without `rel_url`, so the raw
        query string is read defensively. Any unexpected error raises a repair
        instead of silently returning 200 to Twilio.
        """
        try:
            return await self._handle(hass, request)
        except Exception:
            _LOGGER.exception("Evac Relay could not process a Twilio request")
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"twilio_error_{self.manager.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="twilio_error",
                translation_placeholders={"name": self.manager.name},
            )
            return web.Response(status=500)

    async def _handle(self, hass: HomeAssistant, request: web.Request) -> web.Response:
        base = await self.public_url()
        form = await request.post()
        params: dict[str, list[str]] = {k: [str(v) for v in form.getall(k)] for k in set(form.keys())}
        rel_url = getattr(request, "rel_url", None)
        raw_query = rel_url.raw_query_string if rel_url is not None else (request.query_string or "")
        candidates = twilio_sig.signed_url_candidates(base, raw_query) if base else []
        signature = request.headers.get("X-Twilio-Signature")
        if not any(twilio_sig.is_valid(self.auth_token, url, params, signature) for url in candidates):
            return self._reject()
        if self.bad_signatures:
            self.bad_signatures = 0
            ir.async_delete_issue(hass, DOMAIN, self._issue_id)

        def first(key: str) -> str:
            return params.get(key, [""])[0]

        kind = request.query.get("kind", "")
        sender = first("From")
        if kind in ("sms", "voice") and self.allowed and _digits(sender) not in self.allowed:
            _LOGGER.info("Ignoring Twilio %s from non-allowed sender %s", kind, sender)
            return self._xml(TWIML_EMPTY)

        if kind == "sms":
            self.manager.async_ingest("twilio_sms", first("Body"), {"from": sender}, address_targeted=True)
            return self._xml(TWIML_EMPTY)

        if kind == "voice":
            callback_url = twilio_sig.join_query(base, "kind=transcription")
            return self._xml(TWIML_RECORD.format(callback=quoteattr(callback_url)))

        if kind == "transcription":
            status = first("TranscriptionStatus")
            text = first("TranscriptionText")
            meta = {"recording_url": first("RecordingUrl"), "call_sid": first("CallSid")}
            if status == "completed" and text:
                # A robocall to our registered number is ours; transcripts rarely spell zone IDs.
                self.manager.async_ingest("twilio_voice", text, meta, assume_zone_hit=True)
            else:
                persistent_notification.async_create(
                    hass,
                    "A call reached your alert number but could not be transcribed. "
                    f"Listen to the recording: {meta['recording_url']}",
                    title="Evac Relay: untranscribed alert call",
                    notification_id=f"evac_relay_call_{meta['call_sid']}",
                )
                hass.bus.async_fire(
                    EVENT_MESSAGE,
                    {
                        "entry_id": self.manager.entry_id,
                        "source": "twilio_voice",
                        "decision": "transcription_failed",
                        "level": "none",
                        "clearing": False,
                        "zone_hit": False,
                        "matched_zones": [],
                        "text": "",
                        "meta": meta,
                    },
                )
            return self._xml(TWIML_EMPTY)

        return web.Response(status=400)


# ---------------------------------------------------------------- NWS


@callback
def async_setup_nws(
    hass: HomeAssistant,
    entry: ConfigEntry,
    manager: EvacManager,
    latitude: float,
    longitude: float,
    may_trigger: bool,
) -> Callable[[], None]:
    """Poll api.weather.gov for evacuation-type alerts covering this point."""
    session = async_get_clientsession(hass)
    params = {"point": f"{latitude:.4f},{longitude:.4f}", "status": "actual"}
    headers = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}

    async def _poll(_now: Any = None) -> None:
        try:
            async with session.get(NWS_API, params=params, headers=headers, timeout=20) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("NWS poll failed: %s", err)
            return
        manager.nws_last_poll = dt_util.utcnow()
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            event, alert_id = props.get("event", ""), props.get("id", "")
            if event not in NWS_EVENT_LEVELS or not alert_id or manager.nws_seen(alert_id):
                continue
            text = f"{event}. {props.get('headline', '')}. {props.get('description', '')}"
            manager.async_ingest(
                "nws",
                text,
                {"id": alert_id, "event": event, "url": props.get("@id")},
                forced_level=Level.from_key(NWS_EVENT_LEVELS[event]),
                assume_zone_hit=True,
                may_trigger=may_trigger and event not in NWS_ADVISORY_ONLY,
            )
        manager.push_update()

    entry.async_create_background_task(hass, _poll(), f"{DOMAIN}_nws_first_poll")
    return async_track_time_interval(hass, _poll, timedelta(seconds=NWS_POLL_S))
