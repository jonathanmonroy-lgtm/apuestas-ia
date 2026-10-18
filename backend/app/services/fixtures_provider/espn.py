"""ESPN adapter: the multi-country source for "matches of the day" (no key, any date).

UNOFFICIAL: site.api.espn.com is the public JSON behind espn.com, not a documented API
with terms for third parties. Acceptable in INTERNAL MODE only; it may change or vanish
without notice, so everything here fails closed (FormatError) instead of guessing.

Verified 2026-10-17 with real calls:
- /sports/soccer/all/scoreboard?dates=YYYYMMDD&limit=1000 returns every league ESPN
  covers that day in ONE request (429 matches, 65 leagues; without `limit` it stops at 100).
  A date range ("20261016-20261018") is refused (400): one request per day.
- Past and future dates both work (2026-09-01: 83 matches; 2026-11-01: 265).
- Events carry no league slug reliably (half have none in their links): the league id in
  the uid ("s:600~l:700~e:...") is mapped to its slug ("eng.1") with the
  /leagues/dropdown catalogue, and the slug prefix gives the country.
- Odds: ONE bookmaker (DraftKings, American format), on ~15% of matches, also while the
  match is live. We keep only matches not started yet ("pre"): pre-match odds only.
"""

import re
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from pydantic import AwareDatetime, BaseModel, ValidationError

from app.core.logging import get_logger
from app.models.enums import EventStatus, Market, Provider, Selection, Sport
from app.services.errors import FormatError
from app.services.fixtures_provider.api_football import FixtureOdds
from app.services.fixtures_provider.base import (
    CompetitionDTO,
    FixtureDTO,
    FixturesBatch,
    ScoreDTO,
    SeasonDTO,
    TeamDTO,
)
from app.services.http import ProviderHttpClient, RawResponse
from app.services.odds_provider.base import PriceDTO

log = get_logger(__name__)

BASE_URL = "https://site.api.espn.com/apis/site/v2"
SCOREBOARD_LIMIT = 1000
INTERNATIONAL = "World"  # same label the API and the menu use for international play

# status name -> (our status, is a played 90-minute score available?)
STATUS_MAP: dict[str, tuple[EventStatus, bool]] = {
    "STATUS_SCHEDULED": (EventStatus.SCHEDULED, False),
    "STATUS_DELAYED": (EventStatus.SCHEDULED, False),
    "STATUS_FIRST_HALF": (EventStatus.LIVE, False),
    "STATUS_HALFTIME": (EventStatus.LIVE, False),
    "STATUS_SECOND_HALF": (EventStatus.LIVE, False),
    "STATUS_IN_PROGRESS": (EventStatus.LIVE, False),
    "STATUS_END_OF_REGULATION": (EventStatus.LIVE, False),
    "STATUS_OVERTIME": (EventStatus.LIVE, False),
    "STATUS_EXTRA_TIME": (EventStatus.LIVE, False),
    "STATUS_SHOOTOUT": (EventStatus.LIVE, False),
    "STATUS_FULL_TIME": (EventStatus.FINISHED, True),
    "STATUS_FINAL": (EventStatus.FINISHED, True),
    # After extra time / penalties ESPN's score may include extra-time goals and there is
    # no separate 90-minute score: finished, but no result rather than a wrong one.
    "STATUS_FINAL_AET": (EventStatus.FINISHED, False),
    "STATUS_FINAL_PEN": (EventStatus.FINISHED, False),
    "STATUS_POSTPONED": (EventStatus.POSTPONED, False),
    # Suspended may be mid-match or for days: we don't know which, so we don't say.
    "STATUS_SUSPENDED": (EventStatus.UNKNOWN, False),
    "STATUS_CANCELED": (EventStatus.CANCELLED, False),
    "STATUS_ABANDONED": (EventStatus.CANCELLED, False),
    "STATUS_FORFEIT": (EventStatus.FINISHED, False),
}
# Unknown names fall back on ESPN's coarse state, logged so the map can be extended.
STATE_FALLBACK = {
    "pre": EventStatus.SCHEDULED,
    "in": EventStatus.LIVE,
    "post": EventStatus.UNKNOWN,
}

# League slug prefix -> (provider-style English country name, ISO 3166-1 alpha-2).
# Names match API-Football's ("England", "USA"), which the frontend already translates.
COUNTRY_BY_PREFIX: dict[str, tuple[str, str | None]] = {
    "arg": ("Argentina", "AR"), "aus": ("Australia", "AU"), "aut": ("Austria", "AT"),
    "bel": ("Belgium", "BE"), "bol": ("Bolivia", "BO"), "bra": ("Brazil", "BR"),
    "can": ("Canada", "CA"), "chi": ("Chile", "CL"), "chn": ("China", "CN"),
    "col": ("Colombia", "CO"), "crc": ("Costa-Rica", "CR"), "cyp": ("Cyprus", "CY"),
    "den": ("Denmark", "DK"), "ecu": ("Ecuador", "EC"), "eng": ("England", None),
    "esp": ("Spain", "ES"), "fra": ("France", "FR"), "ger": ("Germany", "DE"),
    "gre": ("Greece", "GR"), "gua": ("Guatemala", "GT"), "hon": ("Honduras", "HN"),
    "idn": ("Indonesia", "ID"), "ind": ("India", "IN"), "irl": ("Ireland", "IE"),
    "isr": ("Israel", "IL"), "ita": ("Italy", "IT"), "jam": ("Jamaica", "JM"),
    "jpn": ("Japan", "JP"), "kor": ("South-Korea", "KR"), "ksa": ("Saudi-Arabia", "SA"),
    "mex": ("Mexico", "MX"), "mys": ("Malaysia", "MY"), "ned": ("Netherlands", "NL"),
    "nir": ("Northern-Ireland", None), "nor": ("Norway", "NO"), "pan": ("Panama", "PA"),
    "par": ("Paraguay", "PY"), "per": ("Peru", "PE"), "por": ("Portugal", "PT"),
    "rsa": ("South-Africa", "ZA"), "rus": ("Russia", "RU"), "sco": ("Scotland", None),
    "slv": ("El-Salvador", "SV"), "sui": ("Switzerland", "CH"), "swe": ("Sweden", "SE"),
    "tha": ("Thailand", "TH"), "tur": ("Turkey", "TR"), "uru": ("Uruguay", "UY"),
    "usa": ("USA", "US"), "ven": ("Venezuela", "VE"), "wal": ("Wales", None),
}  # fmt: skip
# Confederations, FIFA, club friendlies...: international, filed under "World".
INTERNATIONAL_PREFIXES = {
    "afc", "aff", "caf", "campeones", "club", "concacaf", "conmebol", "fifa", "friendly",
    "global", "nonfifa", "uefa",
}  # fmt: skip

_LEAGUE_ID = re.compile(r"~l:(\d+)~")
# Only real bookmakers' lines are prices. ESPN also publishes consensus/projection
# "providers" in other sports; anything not listed here is ignored (and logged).
BOOKMAKER_PROVIDER_IDS = {"100": "draftkings"}
# A day fails as a whole only when this share of its events cannot be read.
MAX_SKIPPED_SHARE = 0.5
_HUNDRED = Decimal(100)
_PRICE_STEP = Decimal("0.001")  # the odd_snapshot.price column keeps 3 decimals


def country_of(slug: str | None) -> tuple[str, str | None]:
    """'col.1' -> ('Colombia', 'CO'); 'uefa.champions' or unknown -> ('World', None)."""
    prefix = (slug or "").split(".", 1)[0]
    if prefix in COUNTRY_BY_PREFIX:
        return COUNTRY_BY_PREFIX[prefix]
    if prefix and prefix not in INTERNATIONAL_PREFIXES:
        log.warning("espn_unknown_league_prefix", slug=slug)
    return INTERNATIONAL, None


def american_to_decimal(value: str | int | None) -> Decimal | None:
    """'+230' -> 3.300, '-155' -> 1.645 (rounded to the column's 3 decimals). None when the
    value is missing or not a usable American price (e.g. 'EVEN', 'OFF', '0')."""
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() == "EVEN":
        return Decimal("2.000")
    try:
        american = Decimal(text)
    except InvalidOperation:
        return None
    if not american.is_finite():
        return None  # "NaN", "Infinity"
    if american >= _HUNDRED:
        price = 1 + american / _HUNDRED
    elif american <= -_HUNDRED:
        price = 1 + _HUNDRED / -american
    else:
        return None  # |American| < 100 does not exist: refuse rather than guess
    return price.quantize(_PRICE_STEP, rounding=ROUND_HALF_EVEN)


# --- minimal schema of what we rely on (extra fields ignored) ---
class _StatusType(BaseModel):
    name: str
    state: str


class _Status(BaseModel):
    type: _StatusType


class _Team(BaseModel):
    id: str
    displayName: str
    shortDisplayName: str | None = None


class _Competitor(BaseModel):
    homeAway: str
    score: str | None = None
    team: _Team


class _Venue(BaseModel):
    fullName: str | None = None


class _Competition(BaseModel):
    # Missing = not confirmed: never invent a confirmed kick-off time (QA, as in phase 2).
    timeValid: bool = False
    venue: _Venue | None = None
    altGameNote: str | None = None
    competitors: list[_Competitor]
    odds: list[dict[str, Any] | None] | None = None


class _Season(BaseModel):
    year: int


class _Event(BaseModel):
    id: str
    uid: str
    date: AwareDatetime
    status: _Status
    season: _Season
    competitions: list[_Competition]


class League(BaseModel):
    id: str
    slug: str
    name: str


def slim_scoreboard(payload: Any) -> Any:
    """What we keep on the run for audit: the fields we read, not ~1 MB per day of play-by-
    play, broadcasts and links (raw payloads are kept for 30 days)."""
    if not isinstance(payload, dict):
        return payload
    out = []
    for e in payload.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        out.append({
            "id": e.get("id"), "uid": e.get("uid"), "date": e.get("date"),
            "status": e.get("status"), "season": e.get("season"),
            "competitions": [{
                "timeValid": comp.get("timeValid"), "venue": comp.get("venue"),
                "altGameNote": comp.get("altGameNote"),
                "competitors": [
                    {"homeAway": c.get("homeAway"), "score": c.get("score"),
                     "team": {k: (c.get("team") or {}).get(k)
                              for k in ("id", "displayName", "shortDisplayName")}}
                    for c in comp.get("competitors") or []
                ],
                "odds": [
                    {"provider": o.get("provider"), "moneyline": o.get("moneyline")} if o else None
                    for o in comp.get("odds") or []
                ],
            }],
        })  # fmt: skip
    return {"events": out}


def slim_leagues(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    return {
        "leagues": [
            {k: lg.get(k) for k in ("id", "slug", "name")} for lg in payload.get("leagues") or []
        ]
    }


class EspnProvider:
    source = Provider.ESPN

    def __init__(self, *, client: ProviderHttpClient | None = None) -> None:
        self._http = client or ProviderHttpClient(
            source=self.source.value,
            base_url=BASE_URL,
            # No key. Identify ourselves honestly rather than impersonate a browser.
            auth_headers={"User-Agent": "apuestas-ia/0.1 (uso interno)"},
        )

    def close(self) -> None:
        self._http.close()

    def quota_remaining(self, raw: RawResponse) -> int | None:
        return None  # ESPN publishes no quota

    def fetch_scoreboard(self, day: date) -> RawResponse:
        return self._http.get_json(
            "/sports/soccer/all/scoreboard",
            params={"dates": day.strftime("%Y%m%d"), "limit": SCOREBOARD_LIMIT},
        )

    def fetch_leagues(self) -> RawResponse:
        return self._http.get_json(
            "/leagues/dropdown", params={"sport": "soccer", "limit": SCOREBOARD_LIMIT}
        )

    @staticmethod
    def normalize_leagues(raw: RawResponse) -> dict[str, League]:
        payload = raw.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("leagues"), list):
            raise FormatError("ESPN: expected an object with a 'leagues' list")
        out = {}
        for item in payload["leagues"]:
            try:
                lg = League.model_validate(item)
            except ValidationError:
                continue  # one entry without id/slug (seen: a FIFA qualifying playoff)
            out[lg.id] = lg
        if not out:
            raise FormatError("ESPN: the league catalogue is empty")
        return out

    def normalize_scoreboard(
        self, raw: RawResponse, leagues: dict[str, League]
    ) -> tuple[list[FixturesBatch], list[FixtureOdds]]:
        """One FixturesBatch per league (what upsert_batch handles) + the pre-match odds of
        the matches not started yet."""
        payload = raw.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise FormatError("ESPN: expected an object with an 'events' list")
        if len(payload["events"]) >= SCOREBOARD_LIMIT:
            log.warning("espn_scoreboard_limit_reached", limit=SCOREBOARD_LIMIT)
        # Event by event: one odd match (a cup tie with "TBD" teams, a missing field) is
        # skipped and logged, it does not sink the other ~400 of the day.
        by_league: dict[tuple[str, int], list[FixtureDTO]] = defaultdict(list)
        odds: list[FixtureOdds] = []
        unknown_leagues: set[str] = set()
        skipped = 0
        for item in payload["events"]:
            try:
                e = _Event.model_validate(item)
                m = _LEAGUE_ID.search(e.uid)
                if m is None:
                    raise ValueError("no league id in the uid")
                if m.group(1) not in leagues:
                    unknown_leagues.add(m.group(1))
                    continue  # no slug -> no country: skipped (logged) instead of misfiled
                fixture = self._fixture(e)
            except (ValidationError, ValueError) as exc:
                skipped += 1
                event_id = item.get("id") if isinstance(item, dict) else None
                log.warning("espn_event_skipped", event_id=event_id, reason=str(exc)[:300])
                continue
            by_league[(m.group(1), e.season.year)].append(fixture)
            if e.status.type.state == "pre" and (fo := self._odds(e, raw)) is not None:
                odds.append(fo)
        if unknown_leagues:
            log.warning("espn_leagues_not_in_catalogue", league_ids=sorted(unknown_leagues))
        total = len(payload["events"])
        if total and skipped / total > MAX_SKIPPED_SHARE:
            raise FormatError(f"ESPN: {skipped} of {total} events could not be read")

        batches = []
        for (league_id, season), fixtures in by_league.items():
            lg = leagues[league_id]
            country, code = country_of(lg.slug)
            batches.append(
                FixturesBatch(
                    source=self.source,
                    # The slug is the external id: stable, readable, used by skip lists.
                    competition=CompetitionDTO(
                        external_id=lg.slug, name=lg.name, sport=Sport.FOOTBALL,
                        country_code=code, country_name=country,
                    ),
                    season=SeasonDTO(external_id=f"{lg.slug}-{season}", name=str(season)),
                    fixtures=fixtures,
                )
            )  # fmt: skip
        return batches, odds

    @staticmethod
    def _fixture(e: _Event) -> FixtureDTO:
        name = e.status.type.name
        if name in STATUS_MAP:
            status, has_score = STATUS_MAP[name]
        else:
            status, has_score = STATE_FALLBACK.get(e.status.type.state, EventStatus.UNKNOWN), False
            log.warning("espn_unknown_status", status=name, state=e.status.type.state,
                        event_id=e.id)  # fmt: skip
        comp = e.competitions[0]
        sides = {c.homeAway: c for c in comp.competitors}
        if set(sides) != {"home", "away"}:
            raise ValueError(f"event {e.id}: expected one home and one away competitor")
        home, away = sides["home"], sides["away"]

        full_time = None
        if has_score:
            try:
                full_time = ScoreDTO(home=int(home.score or ""), away=int(away.score or ""))
            except ValueError:
                full_time = None  # a finished match without a readable score: no result
        note = comp.altGameNote or ""
        return FixtureDTO(
            external_id=e.id,
            starts_at=e.date,
            kickoff_confirmed=comp.timeValid,
            status=status,
            # Cups say "Taca de Portugal, 3rd Round": the part after the comma is the round.
            round=note.split(", ", 1)[1] if ", " in note else None,
            venue=comp.venue.fullName if comp.venue else None,
            home=TeamDTO(
                external_id=home.team.id,
                name=home.team.displayName,
                short_name=home.team.shortDisplayName,
            ),
            away=TeamDTO(
                external_id=away.team.id,
                name=away.team.displayName,
                short_name=away.team.shortDisplayName,
            ),
            full_time=full_time,
        )

    @staticmethod
    def _odds(e: _Event, raw: RawResponse) -> FixtureOdds | None:
        entries = [o for o in e.competitions[0].odds or [] if o]
        if not entries:
            return None
        prices: list[PriceDTO] = []
        for entry in entries:
            provider_id = str((entry.get("provider") or {}).get("id") or "")
            book = BOOKMAKER_PROVIDER_IDS.get(provider_id)
            if book is None:
                log.info("espn_odds_provider_ignored", provider_id=provider_id, event_id=e.id)
                continue
            line = entry.get("moneyline") or {}
            values = {
                sel: american_to_decimal(((line.get(side) or {}).get("close") or {}).get("odds"))
                for side, sel in (("home", Selection.HOME), ("draw", Selection.DRAW),
                                  ("away", Selection.AWAY))
            }  # fmt: skip
            if any(v is None or v <= 1 for v in values.values()):
                continue  # not a clean 1X2 from this bookmaker: skip it, never guess
            # ESPN gives no quote time: the moment we saw it is the honest timestamp.
            prices.extend(
                PriceDTO(bookmaker=book, market=Market.H2H, selection=sel, price=v,
                         captured_at=raw.fetched_at)
                for sel, v in values.items()
            )  # fmt: skip
        return FixtureOdds(fixture_external_id=e.id, prices=prices) if prices else None
