"""Constants for Evac Relay."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "evac_relay"
MANUFACTURER: Final = "Evac Relay"

CONF_ZONES: Final = "zones"
CONF_PROFILE: Final = "profile"
CONF_IMAP_ENTRIES: Final = "imap_entries"
CONF_IMAP_SENDERS: Final = "imap_senders"
CONF_NWS_ENABLED: Final = "nws_enabled"
CONF_NWS_TRIGGERS_ALARM: Final = "nws_triggers_alarm"
CONF_LATITUDE: Final = "latitude"
CONF_LONGITUDE: Final = "longitude"
CONF_TWILIO_ENABLED: Final = "twilio_enabled"
CONF_TWILIO_AUTH_TOKEN: Final = "twilio_auth_token"
CONF_TWILIO_ALLOWED_FROM: Final = "twilio_allowed_from"
CONF_PUBLIC_URL: Final = "public_webhook_url"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_CLOUDHOOK_URL: Final = "cloudhook_url"

DEFAULT_PROFILE: Final = "lake_county_ca"

EVENT_MESSAGE: Final = f"{DOMAIN}_message"
# Fired when an alarm starts or escalates (and for tests). The blueprint triggers on it.
EVENT_ALARM: Final = f"{DOMAIN}_alarm"
EVENT_CLEARED: Final = f"{DOMAIN}_cleared"
SIGNAL_UPDATE: Final = f"{DOMAIN}_update_{{}}"

MEDIA_URL_PATH: Final = "/evac_relay_media"

# Ignore email older than this so a restart never replays an old order.
MAX_MESSAGE_AGE_S: Final = 3 * 3600
# Identical text from the same source inside this window is a duplicate.
DEDUPE_WINDOW_S: Final = 15 * 60
# A test alarm ends itself after this long even if the automation never clears it.
TEST_EXPIRE_S: Final = 10 * 60

NWS_POLL_S: Final = 300
NWS_API: Final = "https://api.weather.gov/alerts/active"
NWS_USER_AGENT: Final = "evac-relay/0.1 (Home Assistant custom integration)"
NWS_EVENT_LEVELS: Final = {
    "Evacuation Immediate": "order",
    "Shelter In Place Warning": "shelter",
    "Fire Warning": "warning",
    "Civil Danger Warning": "warning",
    "Civil Emergency Message": "warning",
}
# Used for many non-evacuation notices, so never allowed to sound the alarm.
NWS_ADVISORY_ONLY: Final = {"Civil Emergency Message"}

TWIML_EMPTY: Final = '<?xml version="1.0" encoding="UTF-8"?><Response/>'
# Record the county's robocall and let Twilio transcribe it.
TWIML_RECORD: Final = (
    '<?xml version="1.0" encoding="UTF-8"?><Response>'
    '<Record maxLength="120" timeout="8" playBeep="false" trim="trim-silence" '
    'transcribe="true" transcribeCallback={callback}/></Response>'
)

CONF_ACK_DISCLAIMER: Final = "acknowledge_disclaimer"
CONF_COUNTY_REGISTERED: Final = "county_alerts_registered"
CONF_SCANNER_URL: Final = "scanner_url"

GMAIL_APP_PASSWORD_URL: Final = "https://support.google.com/accounts/answer/185833"
IMAP_DOCS_URL: Final = "https://www.home-assistant.io/integrations/imap/"

# Placeholders shown in the setup wizard, per county profile.
COUNTY_LINKS: Final[dict[str, dict[str, str]]] = {
    "lake_county_ca": {
        "county": "Lake County",
        "alert_system": "LakeCoAlerts",
        "alert_signup_url": "https://www.lakecountyca.gov/869/LakeCoAlerts",
        "zone_url": "https://protect.genasys.com/",
        "scanner_directory_url": "https://www.broadcastify.com/listen/",
        "gmail_app_password_url": GMAIL_APP_PASSWORD_URL,
        "imap_docs_url": IMAP_DOCS_URL,
    },
    "generic": {
        "county": "your county",
        "alert_system": "your county's emergency alert system",
        "alert_signup_url": "https://www.ready.gov/alerts",
        "zone_url": "https://protect.genasys.com/",
        "scanner_directory_url": "https://www.broadcastify.com/listen/",
        "gmail_app_password_url": GMAIL_APP_PASSWORD_URL,
        "imap_docs_url": IMAP_DOCS_URL,
    },
}
