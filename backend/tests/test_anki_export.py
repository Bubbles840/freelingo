"""Fork: Anki export — builder and endpoints."""

from __future__ import annotations

import io
import sqlite3
import tempfile
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from app.services.anki_export import (
    AnkiCard,
    AnkiExportOptions,
    _cloze_text,
    build_apkg,
    enrich_cards,
)

_CARDS = [
    AnkiCard(
        word="bolígrafo",
        translation="pen",
        definition="Writing instrument",
        example_sentence="Mi bolígrafo es azul.",
    ),
    AnkiCard(word="hola", translation="hello", example_sentence="¡Hola, Ana!"),
]


def _notes_in(apkg: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(apkg)) as z:
        name = next(n for n in z.namelist() if n.startswith("collection.anki2"))
        with tempfile.NamedTemporaryFile(suffix=".anki2") as tmp:
            tmp.write(z.read(name))
            tmp.flush()
            conn = sqlite3.connect(tmp.name)
            rows = [r[0] for r in conn.execute("SELECT flds FROM notes")]
            conn.close()
    return rows


def test_build_apkg_contains_every_card():
    data = build_apkg(_CARDS, AnkiExportOptions(deck_name="Test Deck"))
    notes = _notes_in(data)
    assert len(notes) == 2
    joined = "\n".join(notes)
    assert "bolígrafo" in joined and "pen" in joined
    assert "Mi bolígrafo es azul." in joined


def test_option_toggles_control_the_back():
    data = build_apkg(
        _CARDS,
        AnkiExportOptions(
            deck_name="T", include_definition=False, include_example=False
        ),
    )
    joined = "\n".join(_notes_in(data))
    assert "Writing instrument" not in joined
    assert "Mi bolígrafo es azul." not in joined
    assert "pen" in joined


def test_cloze_blanks_the_word_inside_its_example():
    assert (
        _cloze_text(_CARDS[0]) == "Mi {{c1::bolígrafo}} es azul."
    )
    # word missing from example → falls back to clozing the word itself
    assert _cloze_text(AnkiCard(word="adiós", translation="bye")) == "{{c1::adiós}}"


def test_identical_exports_are_deterministic():
    opts = AnkiExportOptions(deck_name="Stable")
    a = _notes_in(build_apkg(_CARDS, opts))
    b = _notes_in(build_apkg(_CARDS, opts))
    assert a == b


def test_skips_cards_with_blank_words():
    cards = _CARDS + [AnkiCard(word="   ", translation="x")]
    assert len(_notes_in(build_apkg(cards, AnkiExportOptions(deck_name="T")))) == 2


@pytest.mark.asyncio
async def test_enrich_merges_extras_and_survives_llm_failure():
    from app.services.anki_export import _EnrichmentResponse, _CardExtra

    ok = _EnrichmentResponse(
        cards=[_CardExtra(word="Bolígrafo", extra="el bolígrafo / los bolígrafos")]
    )
    with patch(
        "app.services.llm_adapter.llm_adapter.structured_output",
        new=AsyncMock(return_value=ok),
    ):
        out = await enrich_cards(_CARDS, "Spanish")
    assert out[0].extra == "el bolígrafo / los bolígrafos"  # case-insensitive match
    assert out[1].extra == ""

    with patch(
        "app.services.llm_adapter.llm_adapter.structured_output",
        new=AsyncMock(side_effect=RuntimeError("down")),
    ):
        out = await enrich_cards(_CARDS, "Spanish")
    assert [c.extra for c in out] == ["", ""]  # untouched, export still works


@pytest.mark.asyncio
async def test_flashcard_export_requires_auth(client):
    r = await client.post("/api/anki/flashcards", json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_invalid_card_type_is_rejected(client):
    r = await client.post(
        "/api/auth/register",
        json={
            "username": "ankier",
            "display_name": "A",
            "email": "ankier@example.com",
            "password": "Passw0rd!123",
            "native_language": "en",
            "accept_terms": True,
        },
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post(
        "/api/anki/flashcards", json={"card_type": "banana"}, headers=headers
    )
    assert r.status_code in (404, 422)  # 404 when no plan yet is also acceptable


def test_reversed_recall_question_never_contains_the_word_or_example():
    """The Recall side asks translation→word; example/definition would leak it."""
    data = build_apkg(
        _CARDS, AnkiExportOptions(deck_name="T", card_type="basic_reversed")
    )
    import io as _io, zipfile as _zip, tempfile as _tmp, sqlite3 as _sql

    with _zip.ZipFile(_io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.startswith("collection.anki2"))
        with _tmp.NamedTemporaryFile(suffix=".anki2") as tmp:
            tmp.write(z.read(name))
            tmp.flush()
            conn = _sql.connect(tmp.name)
            templates = [
                r[0] for r in conn.execute("SELECT models FROM col")
            ]
            conn.close()
    import json as _json

    models = _json.loads(templates[0])
    recall = next(
        t
        for m in models.values()
        for t in m["tmpls"]
        if t["name"] == "Recall"
    )
    assert "{{Word}}" not in recall["qfmt"]
    assert "{{Example}}" not in recall["qfmt"]
    assert "{{Definition}}" not in recall["qfmt"]
    assert "{{Translation}}" in recall["qfmt"]
