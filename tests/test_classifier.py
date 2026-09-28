"""Classifier behavior on realistic and adversarial alert wording."""

from __future__ import annotations

import pytest

ZONES = "LAK-E123"


def run(classifier, text, zones=ZONES, **kw):
    return classifier.classify(text, classifier.parse_zones(zones), classifier.PROFILES["lake_county_ca"], **kw)


@pytest.mark.parametrize(
    ("text", "level", "actionable"),
    [
        # Plain alerts
        ("EVACUATION ORDER: Zone LAK-E123. Leave now.", "order", True),
        ("Evacuation Warning issued for zones LAK-E120, LAK-E123", "warning", True),
        ("Shelter-in-place advisory for LAK-E123", "shelter", True),
        ("EVACUATION ORDER. Zones affected: LAK-E123.", "order", True),
        # Formatting noise
        ("EVACUATION ORDER—LAK-E123", "order", True),
        ("Evacuation order for “LAK-E123”", "order", True),
        ("<p>EVACUATION&nbsp;ORDER</p><table><tr><td>LAK-E123</td></tr></table>", "order", True),
        ("EVACUATION ORDER #LAK-E123!", "order", True),
        # Words that must not cancel a real order
        ("EVACUATION ORDER for LAK-E123. Patients are being airlifted.", "order", True),
        ("EVACUATION ORDER for LAK-E123. Schools and events cancelled.", "order", True),
        ("EVACUATION ORDER for LAK-E123, Hwy 20 reduced to one lane.", "order", True),
        # Mixed messages resolve per clause
        (
            "Evacuation Warning for LAK-E100 has been lifted. Evacuation ORDER now in effect for LAK-E123.",
            "order",
            True,
        ),
        ("Evacuation Order remains for LAK-E120. LAK-E123 is under an Evacuation Warning.", "warning", True),
        ("Evacuation Warning for LAK-E123. An evacuation order may follow.", "warning", True),
        # Lifts and downgrades
        ("Evacuation Order lifted for LAK-E123. Repopulation begins.", "order", False),
        ("The evacuation order for zone LAK-E123 has been lifted", "order", False),
        (
            "Evacuation orders for zones LAK-E120, LAK-E121, LAK-E122 and LAK-E123 have been lifted",
            "order",
            False,
        ),
        # Downgrade: the zone is now under a warning (the manager never lowers a running alarm).
        ("Evacuation Order reduced to Evacuation Warning, zone lak e123", "warning", True),
        ("Officials lifted the evacuation order for LAK-E123", "order", False),
        # Other zones and non-alerts
        ("EVACUATION ORDER for LAK-E999", "order", False),
        ("Hwy 29 closed at Diener Dr", "none", False),
        ("Evacuation order issued for zone LAK-E1234", "order", False),
        # Re-review cases: hedges, comma-joined clauses, replacements, sections, abbreviations
        ("EVACUATION ORDER LAK-E123 LEAVE NOW, EVACUATION WARNING LAK-E124 BE PREPARED TO LEAVE", "order", True),
        ("Evacuation Order for LAK-E123 and Evacuation Warning for LAK-E124, leave as soon as possible", "order", True),
        ("Classes cancelled, EVACUATION ORDER for LAK-E123", "order", True),
        ("Hwy 29 reduced to one lane, Evacuation Order for LAK-E123", "order", True),
        ("EVACUATION ORDER for LAK-E123, all events have been cancelled", "order", True),
        ("Evacuation warning has been lifted and an evacuation order issued for LAK-E123", "order", True),
        ("The Evacuation Warning for LAK-E123 was lifted and replaced by an Evacuation Order", "order", True),
        ("Evacuation Order for LAK-E120 and Evacuation Warning for LAK-E123", "warning", True),
        ("EVACUATION ORDER:\nLAK-E120\nEVACUATION WARNING:\nLAK-E123", "warning", True),
        ("ORDER LIFTED:\nLAK-E123\nWARNING:\nLAK-E130", "order", False),
        ("ORDERS:\nLAK-E120, LAK-E123\nWARNINGS:\nLAK-E130", "order", True),
        ("EVAC ORDER LAK-E123", "order", True),
        ("Mandatory evac for LAK-E123", "order", True),
        ("The evacuation order for the Cobb area has been lifted. LAK-E123", "order", False),
        ("Evacuation Order\nThe Evacuation Order for LAK-E123 has been lifted.", "order", False),
        # A suffixed sub-zone of ours counts as ours (louder is the intended failure mode).
        ("EVACUATION ORDER LAK-E123-A", "order", True),
    ],
)
def test_classify(classifier, text, level, actionable):
    result = run(classifier, text)
    assert result.level.key == level
    assert result.actionable is actionable


def test_prefix_zone_does_not_match_longer_zone(classifier):
    assert run(classifier, "EVACUATION ORDER LAK-E123", zones="LAK-E12").zone_hit is False


def test_no_zones_means_every_evac_message_hits(classifier):
    assert run(classifier, "EVACUATION ORDER for LAK-E999", zones="").actionable


def test_address_targeted_without_zone_mention(classifier):
    text = "EVACUATION ORDER for the area north of Hwy 20 between Nice and Lucerne. Leave now."
    assert not run(classifier, text).actionable
    assert run(classifier, text, address_targeted=True).actionable
    boiler = "Evacuation Order north of Hwy 20 near Kelseyville. Know your zone: protect.genasys.com"
    assert run(classifier, boiler, address_targeted=True).actionable
    for other in ("EVACUATION ORDER for zone LAK-E999.", "EVACUATION ORDER for LAK-E999"):
        assert not run(classifier, other, address_targeted=True).actionable


def test_lift_with_level_subject_is_not_an_alarm(classifier):
    text = "Evacuation Order\nThe Evacuation Order for LAK-E123 has been lifted."
    result = run(classifier, text, address_targeted=True)
    assert result.clearing
    assert not result.actionable


def test_invalid_zones(classifier):
    assert classifier.invalid_zones(classifier.parse_zones("Zone 5, Kelseyville North, LAK-E123")) == [
        "ZONE 5",
        "KELSEYVILLE NORTH",
    ]
    assert classifier.parse_zones("LAK E123") == ["LAK E123"]


@pytest.mark.parametrize("raw", ["LAK-E123, LAK-E124", "LAK-E123; LAK-E124", "LAK-E123\nLAK-E124"])
def test_zone_list_separators(classifier, raw):
    assert classifier.parse_zones(raw) == ["LAK E123", "LAK E124"]


def test_level_ordering(classifier):
    level = classifier.Level
    assert level.ORDER > level.SHELTER > level.WARNING > level.NONE
    assert level.from_key("order") is level.ORDER
