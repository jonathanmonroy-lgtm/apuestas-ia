"""How each LaLiga 2026-27 team is searched for and recognised in Spanish news.

Keyed by football-data.org team id (our Participant identity source). Curated BY HAND:
- `query`: terms sent to NewsAPI (joined with OR). Kept short: NewsAPI caps q at 500 chars.
- `strong`: forms that identify the club on their own ("Valencia CF", "Real Madrid").
- `weak`: forms that are ALSO a city, region, adjective or common word ("Valencia",
  "Levante", "Athletic"...). They only count when the article has football context
  (see classifier.py). This is the false-positive guard for city-named clubs.
  Plain Spanish words that football news use as adjectives ("director deportivo",
  "un delantero muy atlético", "Mundo Deportivo") only count with an article in front:
  "el/del/al Deportivo", found as false positives in real NewsAPI results.

Teams change every season (promotion/relegation): review this table each summer.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TeamTerms:
    query: tuple[str, ...]
    strong: tuple[str, ...]
    weak: tuple[str, ...] = ()


TEAM_TERMS: dict[str, TeamTerms] = {
    "263": TeamTerms(("Alavés",), ("Deportivo Alavés",), ("Alavés",)),  # also "from Álava"
    "77": TeamTerms(("Athletic",), ("Athletic Club", "Athletic de Bilbao"), ("Athletic",)),
    "78": TeamTerms(
        ("Atlético", "Atleti"),
        ("Atlético de Madrid", "Atlético Madrid", "Atleti"),
        ("el Atlético", "del Atlético", "al Atlético"),
    ),
    "81": TeamTerms(("Barça", "Barcelona"), ("FC Barcelona", "Barça"), ("Barcelona",)),
    "558": TeamTerms(("Celta",), ("Celta de Vigo", "RC Celta"), ("Celta",)),
    "560": TeamTerms(
        ("Dépor", "Deportivo"),
        ("Deportivo de La Coruña", "Deportivo La Coruña", "Dépor"),
        ("el Deportivo", "del Deportivo", "al Deportivo"),
    ),
    "285": TeamTerms(("Elche",), ("Elche CF",), ("Elche",)),
    "80": TeamTerms(("Espanyol",), ("RCD Espanyol",), ("Espanyol",)),
    "82": TeamTerms(("Getafe",), ("Getafe CF",), ("Getafe",)),
    "88": TeamTerms(("Levante",), ("Levante UD",), ("Levante",)),
    "84": TeamTerms(("Málaga",), ("Málaga CF",), ("Málaga",)),
    "79": TeamTerms(("Osasuna",), ("Osasuna",)),
    "87": TeamTerms(("Rayo Vallecano",), ("Rayo Vallecano",), ("Rayo",)),
    "90": TeamTerms(("Betis",), ("Real Betis", "Betis")),
    "86": TeamTerms(("Real Madrid",), ("Real Madrid",)),
    "5335": TeamTerms(
        ("Racing",),
        ("Racing de Santander", "Real Racing Club"),
        ("el Racing", "del Racing", "al Racing", "Racing"),
    ),
    "92": TeamTerms(("Real Sociedad",), ("Real Sociedad",)),
    "559": TeamTerms(("Sevilla",), ("Sevilla FC",), ("Sevilla",)),
    "95": TeamTerms(("Valencia",), ("Valencia CF",), ("Valencia",)),
    "94": TeamTerms(("Villarreal",), ("Villarreal CF",), ("Villarreal",)),
}


def newsapi_query() -> str:
    terms = sorted({t for team in TEAM_TERMS.values() for t in team.query})
    return " OR ".join(f'"{t}"' for t in terms)
