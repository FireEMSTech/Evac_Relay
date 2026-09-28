# Security Policy

## Important Disclaimer

This is a **public passion project** shared with friends. It is **not intended to be a life-safety reliable tool** and should not be relied upon as a sole notification or emergency response system. The owner assumes **no liability** for any use of this software, whether as a primary tool or in combination with other systems.

Use this project at your own risk and responsibility.

## Supported Versions

Only the latest release on the `main` branch receives fixes.

## Reporting a Vulnerability

Evac Relay runs inside Home Assistant and can sound a house-wide alarm, so a bug that lets an outsider trigger, silence, or spoof an alert is a security issue.

Please do not disclose vulnerabilities in public issues or other public channels. Use GitHub's private reporting (**Security > Report a vulnerability** on this repository) or contact the project owner directly via GitHub.

Please include:

- Description of the vulnerability
- Home Assistant version and Evac Relay version
- Steps to reproduce (if applicable)
- Potential impact
- Suggested fix (if you have one)

You can expect an initial response within a reasonable timeframe. However, as this is a passion project maintained in the owner's spare time, please be patient with response and resolution times.

**Note:** Given that this project is not intended for life-safety applications and is provided as-is, vulnerability resolution timelines are not guaranteed.

## Scope

In scope:

- Bypassing Twilio signature validation or the IMAP sender/DMARC checks.
- Non-admin users calling the admin-only services (`clear`, `test`, `end_test`, `ingest`, `dispatch`).
- Leaking the Twilio auth token, webhook URL, or scanner URL (which may contain credentials) into logs, history, or diagnostics.
- Anything that lets untrusted text sound the alarm without `can_trigger: true`.

Out of scope: the reliability caveats in the README (email delay, internet or power loss), and issues in Home Assistant core or third-party integrations.
