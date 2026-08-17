"""Pronunciation assessment inside a conversation turn (fork feature)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.conversation_pipeline import ConversationPipeline
from app.services.pronunciation_service import (
    AzurePronunciationService,
    NullPronunciationService,
    PronunciationResult,
    WordScore,
)


class FakeWS:
    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    async def send_json(self, data: object) -> None:
        self.sent.append(("json", data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(("bytes", data))


def _chunk(text: str = "") -> MagicMock:
    c = MagicMock()
    c.choices = [MagicMock()]
    c.choices[0].delta.content = text
    return c


def _make_llm(captured: list):
    async def _stream():
        yield _chunk("Muy bien")

    async def _chat(messages, **kwargs):
        captured.append(messages)
        return _stream()

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=_chat)
    return llm


def _pipeline(pronunciation=None, captured=None):
    stt = AsyncMock()
    stt.transcribe = AsyncMock(return_value="Hola bolígrafo")
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"mp3")
    return ConversationPipeline(
        llm=_make_llm(captured if captured is not None else []),
        tts=tts,
        stt=stt,
        cefr_level="B1",
        target_language="es-ES",
        pronunciation=pronunciation,
    )


def _result():
    return PronunciationResult(
        overall=62, fluency=84,
        words=[WordScore("Hola", 95), WordScore("bolígrafo", 41)],
    )


@pytest.mark.asyncio
async def test_no_pronunciation_frame_when_provider_absent():
    pipeline = _pipeline(pronunciation=None)
    ws = FakeWS()
    await pipeline._process(b"audio", ws)
    types = [m[1]["type"] for m in ws.sent if m[0] == "json"]
    assert "pronunciation" not in types


@pytest.mark.asyncio
async def test_pronunciation_frame_sent_with_payload():
    svc = AsyncMock()
    svc.assess = AsyncMock(return_value=_result())
    pipeline = _pipeline(pronunciation=svc)
    ws = FakeWS()
    await pipeline._process(b"audio", ws)
    frames = [m[1] for m in ws.sent if m[0] == "json" and m[1]["type"] == "pronunciation"]
    assert len(frames) == 1
    assert frames[0]["overall"] == 62
    assert frames[0]["fluency"] == 84
    assert frames[0]["words"][1] == {"word": "bolígrafo", "score": 41}
    assert "turn_id" in frames[0]


@pytest.mark.asyncio
async def test_assess_called_with_full_locale():
    svc = AsyncMock()
    svc.assess = AsyncMock(return_value=None)
    pipeline = _pipeline(pronunciation=svc)
    await pipeline._process(b"audio", FakeWS())
    assert svc.assess.await_args.args[1] == "es-ES"


@pytest.mark.asyncio
async def test_annotation_reaches_llm_but_not_history():
    captured: list = []
    svc = AsyncMock()
    svc.assess = AsyncMock(return_value=_result())
    pipeline = _pipeline(pronunciation=svc, captured=captured)
    await pipeline._process(b"audio", FakeWS())
    sent_user_msg = captured[0][-1]["content"]
    assert "pronunciation_assessment" in sent_user_msg
    assert "bolígrafo" in sent_user_msg
    assert pipeline.history[0]["content"] == "Hola bolígrafo"


@pytest.mark.asyncio
async def test_timeout_delivers_the_frame_late():
    """A slow assessment misses the LLM annotation but still reaches the UI."""
    slow = asyncio.Event()

    async def _slow(*args, **kwargs):
        await slow.wait()
        return _result()

    svc = AsyncMock()
    svc.assess = AsyncMock(side_effect=_slow)
    svc.enabled = True
    pipeline = _pipeline(pronunciation=svc)
    import app.services.conversation_pipeline as cp

    original = cp.PRONUNCIATION_TIMEOUT_SECONDS
    cp.PRONUNCIATION_TIMEOUT_SECONDS = 0.01
    try:
        ws = FakeWS()
        await pipeline._process(b"audio", ws)
        assert "pronunciation" not in [
            m[1]["type"] for m in ws.sent if m[0] == "json"
        ]
        slow.set()
        await asyncio.gather(*pipeline._pending_saves, return_exceptions=True)
    finally:
        cp.PRONUNCIATION_TIMEOUT_SECONDS = original
    frames = [
        m[1] for m in ws.sent if m[0] == "json" and m[1]["type"] == "pronunciation"
    ]
    assert len(frames) == 1
    assert frames[0]["overall"] == 62


@pytest.mark.asyncio
async def test_timeout_degrades_silently():
    async def _slow(*args, **kwargs):
        await asyncio.sleep(10)

    svc = AsyncMock()
    svc.assess = AsyncMock(side_effect=_slow)
    pipeline = _pipeline(pronunciation=svc)
    import app.services.conversation_pipeline as cp

    original = cp.PRONUNCIATION_TIMEOUT_SECONDS
    cp.PRONUNCIATION_TIMEOUT_SECONDS = 0.01
    try:
        ws = FakeWS()
        await pipeline._process(b"audio", ws)
    finally:
        cp.PRONUNCIATION_TIMEOUT_SECONDS = original
    types = [m[1]["type"] for m in ws.sent if m[0] == "json"]
    assert "pronunciation" not in types
    assert "error" not in types


@pytest.mark.asyncio
async def test_provider_exception_degrades_silently():
    svc = AsyncMock()
    svc.assess = AsyncMock(side_effect=RuntimeError("azure down"))
    pipeline = _pipeline(pronunciation=svc)
    ws = FakeWS()
    await pipeline._process(b"audio", ws)
    types = [m[1]["type"] for m in ws.sent if m[0] == "json"]
    assert "pronunciation" not in types
    assert "error" not in types


def _spy_create_task(created: list):
    """Wrap the real asyncio.create_task so tests can retrieve task handles
    for tasks created *inside* the pipeline (e.g. the assessment task),
    which the pipeline never exposes directly."""
    real_create_task = asyncio.create_task

    def _spy(coro, *args, **kwargs):
        t = real_create_task(coro, *args, **kwargs)
        created.append(t)
        return t

    return real_create_task, _spy


@pytest.mark.asyncio
async def test_no_assessment_started_when_stt_fails():
    # Fork (scripted assessment): the assessment needs the transcript as its
    # reference text, so it starts only after STT succeeds — an STT failure
    # must mean the provider is never called at all.
    svc = AsyncMock()
    svc.assess = AsyncMock(return_value=_result())
    pipeline = _pipeline(pronunciation=svc)
    pipeline.stt.transcribe = AsyncMock(side_effect=RuntimeError("STT down"))
    ws = FakeWS()

    await pipeline._process(b"audio", ws)

    codes = [m[1].get("code") for m in ws.sent if m[0] == "json"]
    assert "stt_failed" in codes
    svc.assess.assert_not_called()


@pytest.mark.asyncio
async def test_assess_receives_transcript_as_reference_text():
    # Fork (scripted assessment): Azure scores the exact words the learner
    # sees in their transcript bubble, so per-word results align with it.
    svc = AsyncMock()
    svc.assess = AsyncMock(return_value=_result())
    pipeline = _pipeline(pronunciation=svc)
    pipeline.stt.transcribe = AsyncMock(return_value="Hola bolígrafo")
    ws = FakeWS()

    await pipeline._process(b"audio", ws)

    assert svc.assess.await_count == 1
    assert svc.assess.await_args.kwargs.get("reference_text") == "Hola bolígrafo"


@pytest.mark.asyncio
async def test_process_cancellation_propagates_during_pronunciation_cleanup():
    """Regression: a barge-in cancellation of `_process` landing *inside*
    `_cancel_pronunciation_task` (i.e. while it awaits the assessment task's
    own cancellation) must still propagate CancelledError out of `_process`
    — not be swallowed and converted into a normal return that goes on to
    emit `stt_failed` after the turn was already cancelled."""

    entered_assess = asyncio.Event()

    async def _slow_assess(*args, **kwargs):
        entered_assess.set()
        await asyncio.sleep(10)
        return _result()

    svc = AsyncMock()
    svc.assess = AsyncMock(side_effect=_slow_assess)
    pipeline = _pipeline(pronunciation=svc)
    pipeline.stt.transcribe = AsyncMock(return_value="Hola bolígrafo")
    ws = FakeWS()

    process_task = asyncio.create_task(pipeline._process(b"audio", ws))
    # Wait until _process is inside the assessment wait (the scripted-mode
    # window: STT is done, wait_for(shield(assess_task)) is pending).
    await asyncio.wait_for(entered_assess.wait(), timeout=1)

    process_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await process_task

    codes = [m[1].get("code") for m in ws.sent if m[0] == "json"]
    assert "stt_failed" not in codes


@pytest.mark.asyncio
async def test_assessment_task_cancelled_on_barge_in():
    """Barge-in cancels the in-flight turn's task at any await point (status
    send, STT, transcript send). The assessment task must not leak: it is
    not tracked in `_timer_tasks` or `_pending_saves`, so a stray live HTTP
    call would otherwise outlive both the turn and the session."""

    async def _slow_assess(*args, **kwargs):
        await asyncio.sleep(10)
        return _result()

    svc = AsyncMock()
    svc.assess = AsyncMock(side_effect=_slow_assess)
    pipeline = _pipeline(pronunciation=svc)
    pipeline.stt.transcribe = AsyncMock(return_value="Hola bolígrafo")
    ws = FakeWS()

    created: list = []
    real_create_task, _spy = _spy_create_task(created)

    with patch("app.services.conversation_pipeline.asyncio.create_task", side_effect=_spy):
        process_task = real_create_task(pipeline._process(b"audio", ws))
        # Let _process get past STT and start the (slow) assessment — the
        # barge-in window with an in-flight provider call.
        for _ in range(50):
            if created:
                break
            await asyncio.sleep(0)
        assert created, "assessment task was never created before barge-in"
        assess_task = created[0]

        process_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await process_task

    # Give the cancelled assessment task a few ticks to settle.
    for _ in range(50):
        if assess_task.done():
            break
        await asyncio.sleep(0)
    assert assess_task.done()
    assert assess_task.cancelled()


@pytest.mark.asyncio
async def test_provider_assess_raising_synchronously_degrades_silently():
    """A provider whose `assess` raises synchronously — before ever handing
    back an awaitable — must not abort the turn or emit an error frame."""
    svc = MagicMock()
    svc.assess = MagicMock(side_effect=RuntimeError("boom"))
    pipeline = _pipeline(pronunciation=svc)
    ws = FakeWS()
    await pipeline._process(b"audio", ws)
    types = [m[1]["type"] for m in ws.sent if m[0] == "json"]
    assert "error" not in types
    assert "pronunciation" not in types
    assert "turn_complete" in types


@pytest.mark.asyncio
async def test_annotation_reaches_fallback_messages_too():
    """The tool-unsupported fallback path must carry the same annotation as
    the primary `messages` list — otherwise the system prompt advertises a
    <pronunciation_assessment> tag that never actually appears there."""
    captured_kwargs: list = []

    async def _stream():
        yield _chunk("Muy bien")

    async def _chat(messages, **kwargs):
        captured_kwargs.append(kwargs)
        return _stream()

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=_chat)
    stt = AsyncMock()
    stt.transcribe = AsyncMock(return_value="Hola bolígrafo")
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"mp3")
    svc = AsyncMock()
    svc.assess = AsyncMock(return_value=_result())

    pipeline = ConversationPipeline(
        llm=llm,
        tts=tts,
        stt=stt,
        cefr_level="B1",
        target_language="es-ES",
        pronunciation=svc,
    )
    await pipeline._process(b"audio", FakeWS())

    fallback_messages = captured_kwargs[0].get("fallback_messages")
    assert fallback_messages is not None
    assert "pronunciation_assessment" in fallback_messages[-1]["content"]


# --- Amendment (task-5): gate on `enabled`, not `is not None` ---
#
# The router always passes a live provider instance (NullPronunciationService
# by default), never `None`. Gating on `is not None` would therefore spawn an
# assessment task on every turn even with the feature disabled. Callers must
# gate on the provider's `enabled` capability flag instead.


@pytest.mark.asyncio
async def test_null_provider_produces_no_frame_and_is_never_assessed():
    svc = NullPronunciationService()
    # Spy on assess while preserving its real (None-returning) behavior, so we
    # can assert it was never even invoked — not just that it returned None.
    svc.assess = AsyncMock(wraps=svc.assess)
    pipeline = _pipeline(pronunciation=svc)
    ws = FakeWS()
    await pipeline._process(b"audio", ws)
    types = [m[1]["type"] for m in ws.sent if m[0] == "json"]
    assert "pronunciation" not in types
    svc.assess.assert_not_awaited()


def test_enabled_flag_distinguishes_null_and_azure_providers():
    assert NullPronunciationService.enabled is False
    assert AzurePronunciationService.enabled is True


@pytest.mark.asyncio
async def test_user_toggle_disables_assessment_even_with_a_live_provider():
    svc = AsyncMock()
    svc.assess = AsyncMock(return_value=_result())
    svc.enabled = True
    pipeline = _pipeline(pronunciation=svc)
    pipeline._pronunciation_user_enabled = False
    ws = FakeWS()
    await pipeline._process(b"audio", ws)
    svc.assess.assert_not_awaited()
    assert "pronunciation" not in [m[1]["type"] for m in ws.sent if m[0] == "json"]
