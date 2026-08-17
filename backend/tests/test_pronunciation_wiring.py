"""Pronunciation provider wiring (fork: pronunciation assessment)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.conversation_pipeline import ConversationPipeline
from app.services.pronunciation_service import (
    AzurePronunciationService,
    NullPronunciationService,
)


def _pipeline(**kwargs) -> ConversationPipeline:
    return ConversationPipeline(
        llm=AsyncMock(), tts=AsyncMock(), stt=AsyncMock(), cefr_level="B1", **kwargs
    )


def test_pipeline_defaults_to_no_pronunciation_service():
    assert _pipeline().pronunciation is None


def test_pipeline_accepts_pronunciation_service():
    svc = NullPronunciationService()
    assert _pipeline(pronunciation=svc).pronunciation is svc


def test_build_pronunciation_service_disabled_by_default(monkeypatch):
    from app.main import build_pronunciation_service

    monkeypatch.setattr("app.main.settings.PRONUNCIATION_PROVIDER", "none")
    assert isinstance(build_pronunciation_service(), NullPronunciationService)


def test_build_pronunciation_service_azure(monkeypatch):
    from app.main import build_pronunciation_service

    monkeypatch.setattr("app.main.settings.PRONUNCIATION_PROVIDER", "azure")
    monkeypatch.setattr("app.main.settings.AZURE_SPEECH_KEY", "k")
    monkeypatch.setattr("app.main.settings.AZURE_SPEECH_REGION", "westeurope")
    assert isinstance(build_pronunciation_service(), AzurePronunciationService)


def test_build_pronunciation_service_azure_requires_credentials(monkeypatch):
    from app.main import build_pronunciation_service

    monkeypatch.setattr("app.main.settings.PRONUNCIATION_PROVIDER", "azure")
    monkeypatch.setattr("app.main.settings.AZURE_SPEECH_KEY", "")
    monkeypatch.setattr("app.main.settings.AZURE_SPEECH_REGION", "westeurope")
    with pytest.raises(ValueError, match="AZURE_SPEECH_KEY"):
        build_pronunciation_service()
