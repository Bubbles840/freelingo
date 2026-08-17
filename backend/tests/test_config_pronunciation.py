"""Pronunciation provider settings (fork: pronunciation assessment)."""

from __future__ import annotations

from app.core.config import Settings


def _make_settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "SECRET_KEY": "test-secret",
    }
    base.update(overrides)
    return Settings(**base)


def test_pronunciation_defaults_to_none():
    s = _make_settings()
    assert s.PRONUNCIATION_PROVIDER == "none"
    assert s.AZURE_SPEECH_KEY == ""
    assert s.AZURE_SPEECH_REGION == ""


def test_pronunciation_settings_are_overridable():
    s = _make_settings(
        PRONUNCIATION_PROVIDER="azure",
        AZURE_SPEECH_KEY="k",
        AZURE_SPEECH_REGION="westeurope",
    )
    assert s.PRONUNCIATION_PROVIDER == "azure"
    assert s.AZURE_SPEECH_KEY == "k"
    assert s.AZURE_SPEECH_REGION == "westeurope"
