"""Decides which LaLiga teams (if any) a news article is about. Rule-based on purpose:
explainable, testable against labelled real articles, no model to drift.

Rules (on title + description + the provider's content snippet + the URL path, which is
often the most honest signal: /baloncesto/euroliga/, /futbol/fc-barcelona/;
accent/case-insensitive):
1. Other sports out: basketball and other clubs share names with football clubs
   (Barça, Real Madrid, Valencia Basket). Their vocabulary or a basketball-like score
   (both sides >= 40, e.g. 97-102) discards the article.
2. A team's `strong` term ("Valencia CF", "Barça") is enough on its own.
3. A `weak` term ("Valencia", "Levante", "Deportivo") counts only if the article has
   football context (gol, LaLiga, estadio, entrenador...). "partido" is NOT context:
   it is also a political party, and city-named clubs collide exactly with politics.
4. Per-team exclusions remove other clubs that contain the same word
   ("Atlético Nacional", "Barcelona SC", "Deportivo Alavés" is not the Dépor), and
   per-team vetoes switch a weak term off when the article is clearly about a namesake
   (Racing + "Avellaneda"/"Independiente" is Racing Club of Argentina).
Known limits (heuristic): football from other countries sharing a weak name can slip
through when it has football context; precision measured on real articles in tests.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.services.news_provider.team_terms import TEAM_TERMS


def normalize(text: str) -> str:
    text = re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", " ", text)  # some feeds embed HTML
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _term_regex(terms: list[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    alternatives = "|".join(re.escape(normalize(t)) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


OTHER_SPORT = _term_regex([
    "euroliga", "eurocup", "eurocopa de baloncesto", "acb", "liga endesa", "baloncesto",
    "basket", "nba", "alero", "pivot", "triple", "canasta", "rebotes", "tiros libres",
    "palau blaugrana", "roig arena", "movistar arena", "zalgiris", "maccabi", "olympiacos",
    "armani", "balonmano", "futbol sala", "voleibol", "waterpolo", "hockey", "liga f",
    "femenino", "futbol femenino",
])  # fmt: skip
BASKETBALL_SCORE = re.compile(r"(?<!\d)(\d{2,3})\s?-\s?(\d{2,3})(?!\d)")
FOOTBALL_CONTEXT = _term_regex([
    "futbol", "futbolista", "laliga", "la liga", "primera division", "champions",
    "europa league", "conference league", "copa del rey", "supercopa", "gol", "goles",
    "golazo", "doblete", "penalti", "fuera de juego", "entrenador", "tecnico", "fichaje",
    "delantero", "portero", "porteria", "centrocampista", "defensa central", "estadio",
    "arbitro", "var", "tarjeta roja", "alineacion", "once inicial", "jornada", "derbi",
    "metropolitano", "bernabeu", "camp nou", "montjuic", "mestalla", "sanchez-pizjuan",
    "villamarin", "la ceramica", "san mames", "anoeta", "reale arena", "el sadar",
    "vallecas", "balaidos", "riazor", "mendizorroza", "martinez valero", "la rosaleda",
    "ciutat de valencia", "coliseum", "rcde stadium", "el sardinero",
    "rojiblanco", "rojiblancos", "azulgrana", "blaugrana", "cule", "cules", "colchonero",
    "madridista", "merengue", "verdiblanco", "che", "groguet", "periquito", "celeste",
])  # fmt: skip
TEAM_EXCLUSIONS: dict[str, list[str]] = {
    "78": [r"atletico (nacional|mineiro|tucuman|san luis|ottawa|baleares|paranaense|junior)"],
    "81": [r"barcelona (sc|sporting club|de guayaquil)"],
    "560": [
        r"deportivo (alaves|cali|saprissa|la guaira|toluca|tachira|pasto|quito|cuenca)",
        r"mundo deportivo",  # the newspaper
    ],
    "5335": [r"racing (club de avellaneda|de avellaneda|de ferrol|ferrol|de montevideo)"],
    "87": [r"rayo (majadahonda|cantabria)"],
    "95": [r"ciutat de valencia"],  # Levante's stadium, not Valencia CF
}
# If any of these appears, the team's WEAK terms don't count (strong ones still do).
TEAM_VETOES: dict[str, list[str]] = {
    "5335": ["avellaneda", "independiente", "liga profesional", "argentina", "argentino"],
}


@dataclass(frozen=True)
class _Matcher:
    team_id: str
    strong: re.Pattern[str] | None
    weak: re.Pattern[str] | None
    exclusions: tuple[re.Pattern[str], ...] = field(default=())
    veto: re.Pattern[str] | None = None


MATCHERS = [
    _Matcher(
        team_id,
        _term_regex(list(terms.strong)),
        _term_regex(list(terms.weak)),
        tuple(re.compile(p) for p in TEAM_EXCLUSIONS.get(team_id, [])),
        _term_regex(TEAM_VETOES.get(team_id, [])),
    )
    for team_id, terms in TEAM_TERMS.items()
]


@dataclass(frozen=True)
class Classification:
    team_ids: frozenset[str]  # football-data team ids
    reason: str  # "teams" | "other_sport" | "no_team"


def is_other_sport(text: str) -> bool:
    if OTHER_SPORT and OTHER_SPORT.search(text):
        return True
    return any(int(a) >= 40 and int(b) >= 40 for a, b in BASKETBALL_SCORE.findall(text))


def url_words(url: str | None) -> str:
    """'https://x.com/futbol/fc-barcelona/2026/a-b' -> 'futbol fc barcelona 2026 a b'."""
    if not url:
        return ""
    path = urlsplit(url).path
    return re.sub(r"[/_\-.]+", " ", path)


def classify(
    title: str, description: str | None, content: str | None, url: str | None = None
) -> Classification:
    text = normalize(" ".join(filter(None, (title, description, content, url_words(url)))))
    if is_other_sport(text):
        return Classification(frozenset(), "other_sport")

    football = bool(FOOTBALL_CONTEXT and FOOTBALL_CONTEXT.search(text))
    teams = set()
    for m in MATCHERS:
        team_text = text
        for exclusion in m.exclusions:
            team_text = exclusion.sub(" ", team_text)
        vetoed = bool(m.veto and m.veto.search(team_text))
        if (m.strong and m.strong.search(team_text)) or (
            football and not vetoed and m.weak and m.weak.search(team_text)
        ):
            teams.add(m.team_id)
    return Classification(frozenset(teams), "teams" if teams else "no_team")
