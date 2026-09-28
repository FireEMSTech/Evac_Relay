# Roadmap and data partnerships

Today Evac Relay reads alerts the way a resident receives them: email, text, calls. That works, but email is slow and texts to VoIP numbers are unreliable. Official machine-readable feeds would be faster and more reliable.

## Candidate sources

| Source | What it would add | Access path | Status |
|---|---|---|---|
| **IPAWS All-Hazards Information Feed** (FEMA) | Official, digitally signed CAP messages from EAS, Wireless Emergency Alerts, and public authorities, including county evacuation WEAs | Free. Requires an IPAWS User Portal account and an approved Memorandum of Agreement ([FEMA](https://www.fema.gov/emergency-managers/practitioners/integrated-public-alert-warning-system/technology-developers/all-hazards-information-feed)) | Best first target |
| **Genasys Protect** zone status | Live per-zone status (order, warning, shelter, normal), no text parsing | No public API found. Needs a data agreement with Genasys and/or participating counties | Ask |
| **Watch Duty** | Fire perimeters, incident updates, evacuation zone overlays | No public API. Undocumented endpoints exist but should not be used in distributed software; they depend on a nonprofit's infrastructure and terms | Partnership outreach in progress |
| **Everbridge / LakeCoAlerts** | Structured delivery instead of email parsing | County-level integration; ask Lake County OES | Ask |
| **Cal OES** | Statewide coordination, other county profiles | Relationship, not a feed | Later |

## Recommended architecture for official feeds

An IPAWS MOA is held by a provider, not by each household. The practical shape is one small relay service run under the MOA:

1. The relay polls the IPAWS feed within its request limits, verifies signatures, and keeps California alerts.
2. Each Evac Relay install fetches from the relay, or receives pushes, and does point-in-polygon and zone matching locally, so no household address leaves the house.
3. The integration treats the relay as another source feeding the same classifier, with CAP fields (event code, polygon, geocodes) used directly instead of keyword parsing.

## Asks for agencies

- **Lake County OES**: confirm the LakeCoAlerts email sender and wording; include zone IDs in every evacuation message and in WEA text; consider structured status via Genasys.
- **Genasys**: read-only zone status API or feed for approved integrators.
- **Watch Duty**: data partnership for zone status and incident context.
- **FEMA IPAWS**: MOA application as an alert distributor.

## Other planned work

- More county profiles (wording and zone formats). The generic profile understands the Cal OES standard terms (Evacuation Order, Evacuation Warning, Shelter in Place), which most California counties use with Genasys Protect zones. States on the "Ready, Set, Go" scale (Oregon, Washington, parts of Colorado and Arizona: Level 1 Be Ready, Level 2 Be Set, Level 3 Go Now) need a profile that maps those levels.
- Apple TV full-screen video once pyatv ships play_url support for tvOS 26 and newer and Home Assistant picks it up.
- Optional LLM summary of long alert text for announcements, never used for the alarm decision.
