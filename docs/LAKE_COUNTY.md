# Lake County, CA setup

This is the household checklist behind the setup wizard's **Lake County, CA** profile.

## Before you start

- Home Assistant with the companion app on each phone.
- A Gmail account Home Assistant will read. A new one used only for alerts is best.
- About 30 minutes.

## 1. Find your zone

1. Open [Genasys Protect](https://protect.genasys.com/) and search your address. Lake County [uses Genasys Protect zones](https://lakecountyca.gov/1040/Genasys-Protect-Formerly-Zonehaven) and announces warnings, orders, and shelter-in-place advisories by zone number.
2. Write down the zone ID exactly as shown.
3. Add zones for any other places you care about.

## 2. Enroll in LakeCoAlerts

[LakeCoAlerts](https://www.lakecountyca.gov/869/LakeCoAlerts) runs on Everbridge. It supports text, voice, and email contacts, and sends alerts based on the street addresses you register.

1. Enroll and register your home address (and other addresses you care about).
2. Add the Home Assistant Gmail address as an email contact.
3. Keep your own phones as text and voice contacts.
4. Optional, advanced: add a Twilio number as a text and voice contact (see the README).

Lake County also sends Wireless Emergency Alerts, Nixle, and door-to-door notification. Evac Relay does not replace any of them.

## 3. Connect the email

1. On the Gmail account, turn on 2-step verification and create an app password.
2. Wait for the first LakeCoAlerts email (a test or routine notice), then create a filter from its sender that applies a label such as `evac`. Using a real message avoids guessing the sender address.
3. In Home Assistant add the IMAP integration: `imap.gmail.com`, port 993, the app password, folder `evac`, search `UNSEEN`.
4. Evac Relay > Configure > Zones and alert sources > select the IMAP account.

## 4. Scanner

Search the [Broadcastify directory](https://www.broadcastify.com/listen/) for a Lake County fire feed and paste its direct stream URL into Evac Relay.

## 5. Alarm and test

1. Create an automation from **Evac Relay: house alarm**.
2. Press **Test alarm**. You should get a phone alert labeled TEST, one announcement cycle, and the TV test screen. The test clears itself.
3. Check **Settings > Repairs** for anything left unfinished.
