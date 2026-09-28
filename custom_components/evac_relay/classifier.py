"""Classify evacuation alert text into a level for a set of zones.

Pure Python with no Home Assistant imports so it can be unit tested and
reused. Matching is deliberately keyword-based and deterministic: the alarm
decision must never depend on a language model's reading of the message.

How a message is read:

1. Split into sentences (on . ! ? ; and line breaks).
2. In each sentence, every level phrase ("evacuation order", "evacuation
   warning", "shelter in place") opens a *scope* that runs to the next level
   phrase. Zone IDs bind to the scope they appear in; zone IDs before the
   first level phrase bind to it ("LAK-E123 is under an evacuation warning").
   A short heading line ("Evacuation Order:" or just "Warning:") carries into
   the following lines that list zones.
3. A scope is *lifted* only when a lifting verb is tied to its level phrase
   ("the order for Cobb has been lifted", "order reduced to", "lifted the
   order"), so "patients airlifted" or "classes cancelled, evacuation order
   for ..." never cancel an order. A scope is *hedged* when the level is only
   a possibility ("an evacuation order may follow", "possible evacuation
   order") and is ignored.
4. The result is the highest live level bound to one of our zones. Messages
   from address-targeted sources (county email and texts) that name no zone
   IDs at all count as ours, because the county already targeted them by
   the household's registered address.

When a rule is uncertain it errs toward sounding the alarm.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum
import html
from itertools import pairwise
import re


class Level(IntEnum):
    """Alert levels, ordered so a higher value never downgrades to a lower one."""

    NONE = 0
    WARNING = 1
    SHELTER = 2
    ORDER = 3

    @property
    def key(self) -> str:
        """Lowercase string used for entity states and blueprint inputs."""
        return self.name.lower()

    @classmethod
    def from_key(cls, key: str) -> Level:
        """Parse a lowercase level key."""
        return cls[key.strip().upper()]


@dataclass(frozen=True)
class Profile:
    """Keyword set for one jurisdiction's alert wording. Phrases match as whole words."""

    name: str
    order: tuple[str, ...]
    shelter: tuple[str, ...]
    warning: tuple[str, ...]
    # Anywhere in a scope: the scope is a lift.
    clearing_strong: tuple[str, ...]
    # Only when tied to the level phrase by position or by "is/has been/was".
    clearing_verbs: tuple[str, ...]
    # Right after the level phrase ("order may follow") or right before it ("possible order").
    hedges_after: tuple[str, ...]
    hedges_before: tuple[str, ...]
    # Zones in this scope also belong to the next level ("warning ... replaced by an order").
    carry_forward: tuple[str, ...]


GENERIC = Profile(
    name="generic",
    order=(
        "EVACUATION ORDER",
        "EVACUATION ORDERS",
        "EVAC ORDER",
        "EVAC ORDERS",
        "MANDATORY EVACUATION",
        "MANDATORY EVACUATIONS",
        "MANDATORY EVAC",
        "IMMEDIATE EVACUATION",
        "EVACUATE NOW",
        "EVACUATE IMMEDIATELY",
    ),
    shelter=("SHELTER IN PLACE",),
    warning=(
        "EVACUATION WARNING",
        "EVACUATION WARNINGS",
        "EVAC WARNING",
        "EVAC WARNINGS",
        "VOLUNTARY EVACUATION",
        "VOLUNTARY EVACUATIONS",
        "PREPARE TO EVACUATE",
    ),
    clearing_strong=(
        "REPOPULATION",
        "REPOPULATE",
        "ALL CLEAR",
        "NO LONGER IN EFFECT",
        "RESCINDED",
        "ALLOWED TO RETURN",
        "MAY RETURN HOME",
    ),
    clearing_verbs=("LIFTED", "CANCELLED", "CANCELED", "EXPIRED", "REDUCED TO", "DOWNGRADED", "TERMINATED"),
    hedges_after=("MAY FOLLOW", "MAY BE ISSUED", "COULD BE ISSUED", "MAY BE NEEDED", "IS POSSIBLE", "ARE POSSIBLE"),
    hedges_before=("POSSIBLE", "POTENTIAL", "FUTURE"),
    carry_forward=("REPLACED BY", "UPGRADED TO", "ELEVATED TO", "ESCALATED TO"),
)

# Lake County, CA uses Genasys Protect zones and the Cal OES standardized
# terms (Evacuation Order / Evacuation Warning / Shelter in Place).
LAKE_COUNTY_CA = replace(GENERIC, name="lake_county_ca")

PROFILES: dict[str, Profile] = {p.name: p for p in (GENERIC, LAKE_COUNTY_CA)}

_TAGS = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[^A-Z0-9,]+")
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+|<br\s*/?>|</p>|</div>|</td>|</tr>|</li>|</h\d>", re.IGNORECASE)
_AUX = r"(?:IS|ARE|HAS BEEN|HAVE BEEN|WAS|WERE|IS NOW|ARE NOW|BEING|HAS|HAVE)"
_FILLER = {"FOR", "ZONE", "ZONES", "IN", "AND", "THE", "OF", "AREA", "AREAS", "ALL", "A", "AN"}
_MAX_UNRELATED_WORDS = 4
_HEADINGS = {
    "ORDER": Level.ORDER,
    "ORDERS": Level.ORDER,
    "WARNING": Level.WARNING,
    "WARNINGS": Level.WARNING,
}
_HEADING_LIFT = re.compile(r"^ (ORDERS?|WARNINGS?)(?: (LIFTED|CANCELLED|CANCELED))? $")


def normalize(text: str) -> str:
    """Uppercase, decode HTML, keep letters, digits, and commas as single-space tokens.

    'LAK-E123', '“lak e123”' and '<td>LAK-E123</td>' all become ' LAK E123 '.
    """
    text = _TAGS.sub(" ", html.unescape(text or "")).upper().replace(",", " , ")
    return " " + " ".join(_NON_WORD.sub(" ", text).split()) + " "


def parse_zones(raw: str | list[str] | None) -> list[str]:
    """Split a zone list on commas, semicolons, or new lines into normalized tokens."""
    if not raw:
        return []
    items = raw if isinstance(raw, list) else re.split(r"[,;\n]+", raw)
    zones = [" ".join(normalize(i).replace(",", " ").split()) for i in items]
    return list(dict.fromkeys(z for z in zones if z))


_GENERIC_ZONE_WORDS = {"ZONE", "ZONES", "AREA", "EVACUATION", "SECTOR", "DISTRICT"}


def invalid_zones(zones: list[str]) -> list[str]:
    """Zones too generic to match safely.

    A zone ID needs letters and digits and must not be a bare number behind a
    generic word ("Zone 5"), which would match unrelated text.
    """

    def ok(zone: str) -> bool:
        tokens = zone.split()
        return (
            bool(re.search(r"[A-Z]", zone))
            and bool(re.search(r"\d", zone))
            and len(zone.replace(" ", "")) >= 3
            and tokens[0] not in _GENERIC_ZONE_WORDS
        )

    return [z for z in zones if not ok(z)]


def _zone_id_pattern(zones: list[str]) -> re.Pattern[str]:
    """Recognize zone IDs shaped like ours, including other households' zones."""
    prefixes = sorted({z.split()[0] for z in zones if len(z.split()) > 1})
    if prefixes:
        return re.compile("|".join(rf" {re.escape(p)} [A-Z]*\d[A-Z0-9]*(?= )" for p in prefixes))
    return re.compile(r" (?=[A-Z]{1,4}\d)[A-Z]{1,4}\d{2,5}[A-Z]?(?= )")


def _alt(phrases: tuple[str, ...]) -> str:
    return "|".join(re.escape(normalize(p).strip()) for p in sorted(phrases, key=len, reverse=True))


@dataclass
class _Scope:
    level: Level
    body: str  # text from the level phrase to the next level phrase
    zones: set[str] = field(default_factory=set)
    lifted: bool = False
    hedged: bool = False


@dataclass(frozen=True)
class Classification:
    """Result of classifying one message."""

    level: Level
    clearing: bool
    zone_hit: bool
    matched_zones: tuple[str, ...] = field(default_factory=tuple)

    @property
    def actionable(self) -> bool:
        """True when this message should raise the house alarm."""
        return self.level > Level.NONE and self.zone_hit and not self.clearing


class _Reader:
    def __init__(self, zones: list[str], profile: Profile) -> None:
        self.zones = zones
        self.levels = [
            (Level.ORDER, _alt(profile.order)),
            (Level.SHELTER, _alt(profile.shelter)),
            (Level.WARNING, _alt(profile.warning)),
        ]
        self.level_re = re.compile(rf" (?:{'|'.join(p for _, p in self.levels)})(?= )")
        verbs = _alt(profile.clearing_verbs)
        self.lift_after = re.compile(rf"^ (?:{_AUX} )?(?:{verbs}) ")
        self.lift_aux = re.compile(rf" {_AUX} (?:{verbs}) ")
        # "lifted the order" lifts it; "reduced to a warning" names the new level, so it is excluded.
        backward = tuple(v for v in profile.clearing_verbs if not v.endswith(" TO") and v != "DOWNGRADED")
        self.lift_before = re.compile(rf" (?:{_alt(backward)})(?: THE| ALL| AN| A)? $")
        self.hedge_after = re.compile(rf"^ (?:[A-Z]+ )?(?:{_alt(profile.hedges_after)}) ")
        self.hedge_before = re.compile(rf" (?:{_alt(profile.hedges_before)})(?: [A-Z]+)? $")
        self.strong = [normalize(p) for p in profile.clearing_strong]
        self.carry = [normalize(p) for p in profile.carry_forward]
        self.any_zone = _zone_id_pattern(zones)

    def _level_of(self, phrase: str) -> Level:
        for level, alt in self.levels:
            if re.fullmatch(rf" (?:{alt})", phrase):
                return level
        return Level.NONE

    def _zones_in(self, text: str) -> set[str]:
        padded = f" {text.strip()} "
        return {z for z in self.zones if f" {z} " in padded}

    def _lifted(self, body: str, after: str, lead: str) -> bool:
        if any(s in body for s in self.strong):
            return True
        if self.lift_after.search(after) or self.lift_before.search(lead):
            return True
        match = self.lift_aux.search(after)
        if not match:
            return False
        # Between the level phrase and "has been lifted" there may only be zone IDs, fillers,
        # and a short place name, so "order for LAK-E123, all events have been cancelled"
        # is not read as a lift.
        between = self.any_zone.sub(" ", after[: match.start() + 1])
        segments = between.split(" , ")
        unrelated = [w for w in between.split() if w not in _FILLER and w != ","]
        last_segment = [w for w in segments[-1].split() if w not in _FILLER]
        if len(segments) > 1 and last_segment:
            return False
        return len(unrelated) <= _MAX_UNRELATED_WORDS

    def read(self, text: str) -> tuple[list[_Scope], bool]:
        scopes: list[_Scope] = []
        heading: _Scope | None = None
        names_zones = False
        for raw in _SENTENCES.split(text or ""):
            sentence = normalize(raw)
            words = sentence.replace(",", " ").split()
            if not words:
                continue
            names_zones = names_zones or bool(self.any_zone.search(sentence))
            matches = list(self.level_re.finditer(sentence))

            if not matches:
                bare = _HEADING_LIFT.match(" " + " ".join(words) + " ")
                if bare:
                    heading = _Scope(_HEADINGS[bare.group(1)], sentence, lifted=bool(bare.group(2)))
                    scopes.append(heading)
                elif heading is not None and (found := self._zones_in(sentence)):
                    heading.zones |= found
                elif not self.any_zone.search(sentence):
                    heading = None
                continue

            sentence_scopes: list[_Scope] = []
            for i, m in enumerate(matches):
                end = matches[i + 1].start() + 1 if i + 1 < len(matches) else len(sentence)
                after = sentence[m.end() : end]
                lead = " " + " ".join(sentence[: m.start() + 1].split()[-3:]) + " "
                body = sentence[m.start() : end]
                scope = _Scope(level=self._level_of(m.group(0)), body=body, zones=self._zones_in(after))
                scope.lifted = self._lifted(body, after, lead)
                scope.hedged = bool(self.hedge_after.search(after) or self.hedge_before.search(lead))
                sentence_scopes.append(scope)
            sentence_scopes[0].zones |= self._zones_in(sentence[: matches[0].start() + 1])
            for prev, nxt in pairwise(sentence_scopes):
                if any(c in prev.body for c in self.carry):
                    nxt.zones |= prev.zones
            scopes.extend(sentence_scopes)
            last = sentence_scopes[-1]
            heading = last if not last.zones and len(words) <= 6 else None
        return scopes, names_zones


def classify(
    text: str,
    zones: list[str],
    profile: Profile = GENERIC,
    *,
    address_targeted: bool = False,
) -> Classification:
    """Classify alert text; see the module docstring for the rules."""
    all_scopes, names_zones = _Reader(zones, profile).read(text)
    matched = tuple(z for z in zones if any(z in s.zones for s in all_scopes))
    scopes = [s for s in all_scopes if not s.hedged]
    if not scopes:
        return Classification(Level.NONE, False, bool(matched) or not zones, matched)

    live = [s for s in scopes if not s.lifted]
    ours_live = [s for s in live if s.zones or not zones]
    if ours_live:
        return Classification(max(s.level for s in ours_live), False, True, matched)

    ours_lifted = [s for s in scopes if s.lifted and s.zones]
    if ours_lifted:
        return Classification(max(s.level for s in ours_lifted), True, True, matched)

    if live and address_targeted and not names_zones:
        return Classification(max(s.level for s in live), False, True, matched)

    return Classification(max(s.level for s in scopes), not live, False, matched)
