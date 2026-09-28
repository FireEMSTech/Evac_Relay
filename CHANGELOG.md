# Changelog

All notable changes to Evac Relay. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.3] - 2026-09-27

### Changed

- The scanner URL field rejects a Broadcastify feed *page* (`broadcastify.com/listen/...`) with an explanation; speakers need the direct stream, which Broadcastify now provides only to Premium accounts.
- Apple TV guidance: list Apple TVs under Speakers (audio AirPlay takes over the screen). The TVs input is for Cast devices; pyatv cannot play video URLs on tvOS 26 or newer.
- Roadmap notes on county portability (Cal OES terms vs. "Ready, Set, Go" levels).

## [0.1.2] - 2026-09-27

### Fixed

- The blueprint update check compares parsed YAML, not bytes. A copy saved through Home Assistant's blueprint API (which re-serializes it) no longer shows as outdated, and is adopted as an Evac Relay copy for future automatic updates.

## [0.1.1] - 2026-09-27

### Fixed

- Announcements and phone messages in test mode ran the words together ("This is a test.Evacuation warning"); Home Assistant strips rendered variables, so the space is now added where the prefix is joined.

### Changed

- The bundled blueprint now updates itself: Evac Relay remembers the hash of the copy it installed, so an unmodified copy is replaced by a newer bundled version (automations keep their inputs) and only an edited copy raises the Repairs issue.
- Documented that Apple TV full-screen alerts depend on AirPlay video playback working from Home Assistant; on current tvOS the attempt often fails ("not authenticated" or HTTP 500) and only pauses what was playing. Test before leaving an Apple TV in the TVs input.

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

[Unreleased]: https://github.com/FireEMSTech/Evac_Relay/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/FireEMSTech/Evac_Relay/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/FireEMSTech/Evac_Relay/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/FireEMSTech/Evac_Relay/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/FireEMSTech/Evac_Relay/releases/tag/v0.1.0
