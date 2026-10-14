"""qa-reviewer findings on Phase 4 + measurement on a held-out real sample."""

import json
from pathlib import Path

import pytest

from app.services.news_provider.classifier import classify

HELDOUT = Path(__file__).parent / "fixtures/newsapi/heldout_laliga_es_2026-10-12_labelled.json"
BARCA, ESPANYOL, BETIS, SEVILLA, LEVANTE, VALENCIA = "81", "80", "90", "559", "88", "95"


# --- Held-out evaluation -------------------------------------------------------------
# 100 real articles from a different day than the tuning sample, labelled by hand.
# Measured BEFORE adapting anything to it: 46 articles predicted with teams, 3 false
# positives (precision 93.5 %), 0 false negatives found. Nothing was tuned on it. The 3
# known errors are mentions of a club in a story that is not club news (ex-player
# interview, the coach's real estate, a reserve-team article filed in the club's URL
# section): a keyword classifier can't tell those apart.


def test_heldout_precision_and_recall():
    data = json.loads(HELDOUT.read_text(encoding="utf-8"))
    tp = fp = fn = 0
    errors = []
    for a in data["articles"]:
        pred = set(classify(a["title"] or "", a["description"], a["content"], a["url"]).team_ids)
        gold = set(a["label_team_ids"])
        tp += len(pred & gold)
        fp += len(pred - gold)
        fn += len(gold - pred)
        if pred != gold:
            errors.append((a["title"][:60], sorted(pred), sorted(gold)))
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    assert precision >= 0.93, errors
    assert recall >= 0.98, errors
    assert len(errors) == 3, errors  # the three documented known errors, nothing new


# --- QA F4 #1: generic words are not football context ---------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Huelga de metro en Valencia: la jornada deja colas",
        "Sevilla aprueba el plan de transporte metropolitano",
        "El equipo tecnico del Ayuntamiento de Getafe presenta el plan",
        "Malaga celebra la jornada de puertas abiertas del museo",
        "Barcelona: el Gobierno convoca una jornada de reflexion",
        "Villarreal: el ayuntamiento celebra una jornada cultural",
        "Che, en Valencia hay feria",
    ],
)
def test_city_news_with_generic_words_is_not_a_team(text):
    assert classify(text, None, None).team_ids == set()


# --- QA F4 #2: a city as a place is not that city's club --------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("El Espanyol de Barcelona gana con un gol de cabeza", {ESPANYOL}),
        ("El Betis entrena en Sevilla antes del derbi con gol de Isco", {BETIS}),
        ("El Levante entrena en Valencia antes del derbi", {LEVANTE}),
        ("Sevilla y Betis empatan en un derbi con dos goles", {SEVILLA, BETIS}),  # still clubs
        ("Gol del Valencia ante el Betis en Mestalla", {VALENCIA, BETIS}),
    ],
)
def test_place_preposition_means_the_city(text, expected):
    assert classify(text, None, None).team_ids == expected


# --- QA F4 #3: other Racing clubs ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "El Racing de Estrasburgo gana con gol en la jornada",
        "El Racing Club de Lens remonta con un gol",
        "El Racing de Cordoba gana con un gol",
        "Ford Racing vuelve a la Formula 1",
    ],
)
def test_other_racing_clubs(text):
    assert "5335" not in classify(text, None, None).team_ids


def test_racing_de_santander_still_matches():
    assert classify("El Racing de Santander gana 2-0", None, None).team_ids == {"5335"}
    assert classify("Gol del Racing en El Sardinero", None, None).team_ids == {"5335"}


# --- QA F4 #7: betting content is never served as news ---------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Bet365 cuotas pronostico Barca vs Real Madrid apuesta gol",
        "Pronostico y cuotas del Sevilla - Betis: gol de Isco",
        "Las casas de apuestas dan favorito al Atleti en el derbi",
    ],
)
def test_betting_content_is_discarded(text):
    result = classify(text, None, None)
    assert result.team_ids == set()
    assert result.reason == "betting_content"


def test_figurative_apuesta_is_fine():
    text = "La apuesta del Villarreal por la cantera da un gol"
    assert classify(text, None, None).team_ids == {"94"}


# --- QA F4 #8: legit football no longer thrown away ------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("El Barca gana 3-0 con triple cambio al descanso", {BARCA}),
        ("El Barca tuvo el 60-40 de posesion y gano con dos goles", {BARCA}),
    ],
)
def test_football_with_basketball_like_words(text, expected):
    assert classify(text, None, None).team_ids == expected


def test_futsal_is_other_sport():
    assert classify("El Real Betis Futsal gana el derbi", None, None).reason == "other_sport"


# --- QA F4 #9: institutions and lightning ----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "La Real Sociedad Geografica premia a un explorador",
        "La Real Sociedad Española de Química celebra su congreso",
        "Un rayo cae en Valencia durante la jornada de tormentas",
    ],
)
def test_non_club_namesakes(text):
    assert classify(text, None, None).team_ids == set()
