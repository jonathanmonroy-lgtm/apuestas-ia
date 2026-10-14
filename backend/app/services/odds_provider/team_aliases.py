"""The Odds API team name -> football-data.org team id (our fixtures source of truth).

The Odds API has no team ids, only names, and they differ from football-data's
("Athletic Bilbao" vs "Athletic Club"). This table is curated BY HAND, never fuzzy-matched:
a wrong automatic match would attach a match's odds to the wrong event.

Verified 2026-10-14 against real responses of both providers (LaLiga 2026-27, the 21
events then on offer matched by teams + kick-off). A name missing here sends that
event to `unmatched_odd`; fix it by adding the alias, never by guessing.
"""

ODDS_API_TO_FOOTBALL_DATA: dict[str, str] = {
    "Alavés": "263",
    "Athletic Bilbao": "77",
    "Atlético Madrid": "78",
    "Barcelona": "81",
    "CA Osasuna": "79",
    "Celta Vigo": "558",
    "Deportivo La Coruña": "560",
    "Elche CF": "285",
    "Espanyol": "80",
    "Getafe": "82",
    "Levante": "88",
    "Málaga": "84",
    "Rayo Vallecano": "87",
    "Real Betis": "90",
    "Real Madrid": "86",
    "Real Racing Club de Santander": "5335",
    "Real Sociedad": "92",
    "Sevilla": "559",
    "Valencia": "95",
    "Villarreal": "94",
}
