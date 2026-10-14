import json
from pathlib import Path

import pytest

from app.services.news_provider.classifier import classify, normalize
from app.services.news_provider.team_terms import TEAM_TERMS, newsapi_query

# 25 real articles returned by the production query on 2026-10-14, labelled BY HAND by
# reading each one (title, description, content snippet and URL).
REAL = Path(__file__).parent / "fixtures/newsapi/everything_laliga_es_real.json"
BARCA, ATLETI, VILLARREAL, BETIS, OSASUNA, SEVILLA, LEVANTE = (
    "81", "78", "94", "90", "79", "559", "88",
)  # fmt: skip
OTHER_SPORT = "other_sport"
NONE = "no_team"

# url fragment -> expected result
LABELS = {
    "domino-la-ia-que-transforma": NONE,  # Barcelona = city
    "fabrica-viviendas-prometida-sanchez": NONE,  # Valencia = city
    "barcelona-tambien-arrasa-en-la-champions": {BARCA},
    "valencia-rumia-derrota-mas-dura": OTHER_SPORT,  # Valencia Basket
    "barca-firma-remontada-autoridad-estambul": {BARCA},
    "pedro-martinez-previsto-acudir-mercado": OTHER_SPORT,  # Real Madrid basketball
    "venimos-demostrando-nuestra-reaccion": {VILLARREAL},
    "6ace70c3e85ece01258b459e": NONE,  # Valencia politics
    "hilton-barcelona": NONE,
    "barcelona-el-prat-gana-conectividad": NONE,
    "asi-queda-clasificacion-euroliga": OTHER_SPORT,
    "allegri-falta-encontrar-equilibrio": {VILLARREAL},  # Napoli coach at Villarreal
    "valencia-basket-desploma-olympiacos": OTHER_SPORT,
    "fermin-nivel-alto-no-hay-relajarse": {BARCA},  # team only in the URL
    "metropolitano-vuelca-koke-familia": {ATLETI},
    "triple-robinson-sella-triunfo-agonico": OTHER_SPORT,  # Barça basketball
    "tres-pequenos-grande-real-madrid": OTHER_SPORT,
    "tres-negocios-centenarios": NONE,
    "1x1-barca-maccabi": OTHER_SPORT,
    "izquierda-valenciana-negocia": NONE,
    "negociacion-frente-amplio-encalla": NONE,  # "partidos" = political parties
    "pleno-espanol-champions-barca-atletico-villarreal": {BARCA, ATLETI, VILLARREAL},
    "atletico-manchester-united-champions": {ATLETI},
    "isco-apunta-champions": {BETIS, OSASUNA},
    "abanico-goleador-sevilla": {SEVILLA, LEVANTE},  # NOT Valencia: "Ciutat de Valencia"
    # Found in the second real run (false positives before the article-form rule/veto):
    "clasico-de-avellaneda": NONE,  # Racing Club (Argentina), not Racing de Santander
    "portada-hoy-edicion-bizkaia-araba": {"77"},  # Athletic; "Mundo Deportivo" = newspaper
}


def articles() -> list[dict]:
    return json.loads(REAL.read_text(encoding="utf-8"))["articles"]


def label_for(url: str):
    matches = [v for k, v in LABELS.items() if k in url]
    assert len(matches) == 1, url
    return matches[0]


@pytest.mark.parametrize("article", articles(), ids=lambda a: a["url"][8:60])
def test_real_article_classification(article):
    expected = label_for(article["url"])
    result = classify(article["title"], article["description"], article["content"], article["url"])
    if isinstance(expected, set):
        assert result.team_ids == expected
    else:
        assert result.team_ids == frozenset()
        assert result.reason == expected


def test_labels_cover_the_whole_fixture():
    assert len(articles()) == len(LABELS) == 27


# --- rules in isolation ---


def test_weak_city_name_needs_football_context():
    assert classify("Valencia acoge un congreso de vivienda", None, None).team_ids == set()
    assert classify("El Valencia gana con un gol en el descuento", None, None).team_ids == {"95"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("El director deportivo del Sevilla habla del fichaje", {SEVILLA}),  # not the Dépor
        ("Un delantero muy atlético marcó el gol de la jornada", set()),  # not the Atleti
    ],
)
def test_generic_adjectives_are_not_teams(text, expected):
    assert classify(text, None, None).team_ids == expected


def test_article_forms_still_match():
    assert classify("El Deportivo gana con un gol de Yeremay", None, None).team_ids == {"560"}
    assert classify("Gol del Atlético en el descuento", None, None).team_ids == {ATLETI}


def test_party_is_not_football_context():
    text = "Los partidos negocian en Valencia y Sevilla la candidatura"
    assert classify(text, None, None).reason == NONE


def test_basketball_score_is_other_sport():
    assert classify("El Barça gana 90-87 al Maccabi", None, None).reason == OTHER_SPORT
    assert classify("El Barça gana 3-1 al Galatasaray", None, None).team_ids == {BARCA}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Atlético Nacional golea en la liga colombiana", set()),
        ("El Deportivo Alavés gana con un gol de cabeza", {"263"}),  # Alavés, not the Dépor
        ("Gol del Barcelona SC en Guayaquil", set()),
        ("El Racing de Ferrol empata en la jornada 9", set()),
    ],
)
def test_other_clubs_sharing_a_word_are_excluded(text, expected):
    assert classify(text, None, None).team_ids == expected


def test_normalize_strips_accents_case_and_html():
    assert normalize("<![CDATA[<p>Barça y ATLÉTICO</p>]]>") == "barca y atletico"


def test_query_fits_newsapi_limit_and_covers_20_teams():
    assert len(newsapi_query()) <= 500
    assert len(TEAM_TERMS) == 20
