# Changelog

All notable changes to Evac Relay. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-27

First public release.

### Added

- Config-flow setup wizard: home location, evacuation zones, county alert sign-up, IMAP email source, scanner stream, NWS backup, optional Twilio.
- Deterministic keyword classifier for evacuation orders, warnings, shelter-in-place, lifts, and hedged language, with per-zone scoping.
- Alarm state machine with escalation-only rules, 15-minute dedupe, persistence across restarts, and a self-expiring test mode.
- Sources: Home Assistant IMAP events (sender filter, DMARC check, 3-hour age limit), Twilio SMS and voice webhooks with signature validation, NWS `api.weather.gov` polling.
- Entities: evacuation level, alarm active, acknowledged, last message, NWS last poll; acknowledge, clear, and test buttons.
- Services: `acknowledge`, `clear`, `test`, `end_test`, `ingest`, `dispatch`.
- Bundled **Evac Relay: house alarm** blueprint: critical phone push with Acknowledge action, repeating speaker and voice-satellite announcements, TV wake and full-screen alert video, Android TV overlays, scanner stream playback.
- Repairs issues for incomplete setup, missing IMAP entries, Twilio URL problems, and outdated blueprint copies.
- Lake County, CA county profile and setup guide.

[Unreleased]: https://github.com/FireEMSTech/Evac_Relay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/FireEMSTech/Evac_Relay/releases/tag/v0.1.0
