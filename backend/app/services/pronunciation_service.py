"""Pronunciation assessment providers (fork feature).

Mirrors the STT/TTS provider pattern: duck-typed services selected by
``settings.PRONUNCIATION_PROVIDER`` in ``app.main`` and attached to
``app.state``. Assessment is always best-effort — every failure path
degrades to ``None`` so a conversation turn never breaks because scoring
was slow or unavailable.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field


from app.core.app_logger import get_logger

logger = get_logger(__name__)

WEAK_WORD_SCORE_THRESHOLD = 60
MAX_ANNOTATED_WEAK_WORDS = 5


@dataclass(frozen=True)
class WordScore:
    word: str
    score: int


@dataclass(frozen=True)
class PronunciationResult:
    overall: int
    fluency: int | None = None
    words: list[WordScore] = field(default_factory=list)

    def weak_words(self) -> list[str]:
        return [w.word for w in self.words if w.score < WEAK_WORD_SCORE_THRESHOLD]

    def to_payload(self) -> dict:
        return {
            "overall": self.overall,
            "fluency": self.fluency,
            "words": [{"word": w.word, "score": w.score} for w in self.words],
        }


class NullPronunciationService:
    """Disabled provider — the default. Never assesses anything."""

    # Callers must gate on `enabled`, not on `is not None`: the router always
    # attaches a live provider instance (this one by default), so checking
    # for `None` would never actually disable the feature.
    enabled: bool = False

    async def assess(
        self,
        audio_bytes: bytes,
        language: str,
        reference_text: str | None = None,
    ) -> PronunciationResult | None:
        return None


def format_pronunciation_annotation(result: PronunciationResult | None) -> str:
    """Render a one-line, untrusted-data annotation for the LLM.

    Returns "" when there is nothing to say, so callers can skip injection.
    """
    if result is None:
        return ""
    weak = [html.escape(w) for w in result.weak_words()][:MAX_ANNOTATED_WEAK_WORDS]
    body = f"overall {result.overall}/100"
    if weak:
        body += "; unclear words: " + ", ".join(weak)
    return (
        "<pronunciation_assessment>"
        f"{body}"
        "</pronunciation_assessment>"
    )


AZURE_PCM_BYTES_PER_SECOND = 32000
AZURE_MAX_AUDIO_SECONDS = 58
AZURE_MAX_AUDIO_BYTES = AZURE_PCM_BYTES_PER_SECOND * AZURE_MAX_AUDIO_SECONDS


def _assess_sync(
    api_key: str, region: str, audio_bytes: bytes, language: str, reference_text: str
) -> PronunciationResult | None:
    """Blocking SDK call — run via asyncio.to_thread.

    The REST short-audio endpoint silently omits PronunciationAssessment on
    current (Foundry-era) Speech resources; the official SDK's WebSocket
    protocol returns it fine, including on the F0 free tier.
    """
    import tempfile
    from pathlib import Path

    import azure.cognitiveservices.speech as speechsdk

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        path = Path(tmp.name)
    try:
        speech_config = speechsdk.SpeechConfig(subscription=api_key, region=region)
        audio_config = speechsdk.audio.AudioConfig(filename=str(path))
        pa_config = speechsdk.PronunciationAssessmentConfig(
            reference_text=reference_text or "",
            grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
            granularity=speechsdk.PronunciationAssessmentGranularity.Word,
        )
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, language=language, audio_config=audio_config
        )
        pa_config.apply_to(recognizer)
        result = recognizer.recognize_once()
        if result.reason != speechsdk.ResultReason.RecognizedSpeech:
            logger.info("[pronunciation] SDK returned no speech: %s", result.reason)
            return None
        pa = speechsdk.PronunciationAssessmentResult(result)
        if pa.pronunciation_score is None:
            return None
        words = [
            WordScore(word=w.word, score=int(round(w.accuracy_score)))
            for w in (pa.words or [])
            if w.word
        ]
        fluency = (
            int(round(pa.fluency_score)) if pa.fluency_score is not None else None
        )
        return PronunciationResult(
            overall=int(round(pa.pronunciation_score)),
            fluency=fluency,
            words=words,
        )
    finally:
        path.unlink(missing_ok=True)


class AzurePronunciationService:
    """Azure Speech pronunciation assessment via the official SDK."""

    enabled: bool = True

    def __init__(self, api_key: str, region: str) -> None:
        self._api_key = api_key
        self._region = region

    async def assess(
        self,
        audio_bytes: bytes,
        language: str,
        reference_text: str | None = None,
    ) -> PronunciationResult | None:
        if len(audio_bytes) > AZURE_MAX_AUDIO_BYTES:
            logger.info(
                "[pronunciation] Skipping assessment: %d bytes exceeds the %ds limit",
                len(audio_bytes),
                AZURE_MAX_AUDIO_SECONDS,
            )
            return None
        import asyncio

        try:
            return await asyncio.to_thread(
                _assess_sync,
                self._api_key,
                self._region,
                audio_bytes,
                language,
                reference_text or "",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort by design
            logger.warning("[pronunciation] Azure assessment failed: %s", exc)
            return None
