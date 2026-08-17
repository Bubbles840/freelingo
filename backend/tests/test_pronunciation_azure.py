"""Azure pronunciation adapter (fork) — SDK-based."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.pronunciation_service import (
    AZURE_MAX_AUDIO_BYTES,
    AzurePronunciationService,
    PronunciationResult,
    WordScore,
)


def _result() -> PronunciationResult:
    return PronunciationResult(
        overall=88, fluency=90, words=[WordScore("hola", 95)]
    )


@pytest.mark.asyncio
async def test_assess_returns_the_sdk_result():
    svc = AzurePronunciationService(api_key="k", region="eastus")
    with patch(
        "app.services.pronunciation_service._assess_sync", return_value=_result()
    ) as sync:
        out = await svc.assess(b"wav", "es-ES", reference_text="hola")
    assert out is not None and out.overall == 88
    assert sync.call_args.args[3] == "es-ES"
    assert sync.call_args.args[4] == "hola"


@pytest.mark.asyncio
async def test_assess_skips_oversized_audio_without_calling_the_sdk():
    svc = AzurePronunciationService(api_key="k", region="eastus")
    with patch("app.services.pronunciation_service._assess_sync") as sync:
        out = await svc.assess(b"x" * (AZURE_MAX_AUDIO_BYTES + 1), "es-ES")
    assert out is None
    sync.assert_not_called()


@pytest.mark.asyncio
async def test_assess_degrades_to_none_when_the_sdk_raises():
    svc = AzurePronunciationService(api_key="k", region="eastus")
    with patch(
        "app.services.pronunciation_service._assess_sync",
        side_effect=RuntimeError("sdk exploded"),
    ):
        assert await svc.assess(b"wav", "es-ES") is None


def test_enabled_flag():
    assert AzurePronunciationService(api_key="k", region="r").enabled is True
