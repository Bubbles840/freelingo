"""Pronunciation service core types (fork: pronunciation assessment)."""

from __future__ import annotations

import pytest

from app.services.pronunciation_service import (
    NullPronunciationService,
    PronunciationResult,
    WordScore,
    format_pronunciation_annotation,
)


def _result(overall=62, fluency=70, words=None) -> PronunciationResult:
    if words is None:
        words = [WordScore("hola", 95), WordScore("boligrafo", 41)]
    return PronunciationResult(overall=overall, fluency=fluency, words=words)


def test_weak_words_filters_by_threshold():
    assert _result().weak_words() == ["boligrafo"]


def test_weak_words_empty_when_all_strong():
    r = _result(words=[WordScore("hola", 95), WordScore("gato", 88)])
    assert r.weak_words() == []


def test_to_payload_shape():
    payload = _result().to_payload()
    assert payload == {
        "overall": 62,
        "fluency": 70,
        "words": [
            {"word": "hola", "score": 95},
            {"word": "boligrafo", "score": 41},
        ],
    }


def test_annotation_includes_score_and_weak_words():
    text = format_pronunciation_annotation(_result())
    assert "62" in text
    assert "boligrafo" in text
    assert "hola" not in text


def test_annotation_without_weak_words_still_reports_score():
    r = _result(words=[WordScore("hola", 95)])
    text = format_pronunciation_annotation(r)
    assert "95" not in text  # word scores are not listed when all are strong
    assert "62" in text


def test_annotation_of_none_is_empty():
    assert format_pronunciation_annotation(None) == ""


def test_annotation_escapes_angle_brackets():
    r = _result(words=[WordScore("<script>", 10)])
    text = format_pronunciation_annotation(r)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


@pytest.mark.asyncio
async def test_null_service_returns_none():
    svc = NullPronunciationService()
    assert await svc.assess(b"audio", "es-ES") is None
