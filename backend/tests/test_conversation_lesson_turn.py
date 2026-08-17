"""Guided lessons driven through the conversation pipeline (fork feature).

The pipeline must teach an assigned lesson step by step over the live voice
socket: the current step is injected into the system prompt, the tutor gets a
`lesson_step_result` tool alongside the memory tool, and the client is told
about progress through `lesson_state` / `lesson_completed` frames.

With `lesson_session=None` every one of those behaviours must be completely
absent — the fork must be inert for ordinary conversations.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.conversation_pipeline import ConversationPipeline
from app.services.llm_adapter import LLMToolCall, LLMToolResultEvent
from app.services.lesson_voice import (
    LESSON_STEP_RESULT_TOOL_NAME,
    LessonSession,
    LessonStep,
)
from app.services.memory_service import SAVE_USER_MEMORY_TOOL_NAME

STEP_ONE_BODY = "STEPONEBODYMARKER — order a coffee politely."
STEP_TWO_BODY = "STEPTWOBODYMARKER — recap what was covered."


class FakeWS:
    """Same shape as tests/test_pronunciation_turn.py, plus `receive`/`close`
    so `run()` can be driven with scripted client events."""

    def __init__(self, incoming: list[dict] | None = None, fail_bytes: bool = False):
        self.sent: list[tuple[str, object]] = []
        self._incoming = list(incoming or [])
        self._fail_bytes = fail_bytes

    async def send_json(self, data: object) -> None:
        self.sent.append(("json", data))

    async def send_bytes(self, data: bytes) -> None:
        if self._fail_bytes:
            raise RuntimeError("socket closed")
        self.sent.append(("bytes", data))

    async def receive(self) -> dict:
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "websocket.disconnect"}

    async def close(self, code: int = 1000) -> None:
        self.sent.append(("close", code))

    def json_messages(self) -> list[dict]:
        return [m[1] for m in self.sent if m[0] == "json"]

    def types(self) -> list[str]:
        return [m.get("type") for m in self.json_messages()]

    def frames(self, frame_type: str) -> list[dict]:
        return [m for m in self.json_messages() if m.get("type") == frame_type]


def _chunk(text: str = "") -> MagicMock:
    c = MagicMock()
    c.choices = [MagicMock()]
    c.choices[0].delta.content = text
    return c


class FakeToolStream:
    """A stream that reports one already-executed tool call, then speaks."""

    def __init__(self, result) -> None:
        self.tool_results = [result]

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        yield LLMToolResultEvent(result=self.tool_results[0])
        yield _chunk("Muy bien.")


def _make_llm(
    messages_seen: list,
    kwargs_seen: list,
    tool_call: LLMToolCall | None = None,
    reply: str = "Muy bien.",
):
    async def _plain_stream():
        yield _chunk(reply)

    async def _chat(messages, **kwargs):
        messages_seen.append(messages)
        kwargs_seen.append(kwargs)
        if tool_call is not None and "tool_executor" in kwargs:
            result = await kwargs["tool_executor"](tool_call)
            return FakeToolStream(result)
        return _plain_stream()

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=_chat)
    return llm


def _steps(count: int = 2) -> list[LessonStep]:
    all_steps = [
        LessonStep(index=0, kind="intro", title="Introduction", body=STEP_ONE_BODY, goal="Ready."),
        LessonStep(index=1, kind="wrap_up", title="Wrap up", body=STEP_TWO_BODY, goal="Recapped."),
    ]
    return all_steps[:count]


def _session(lesson_id: int = 42, steps: int = 2) -> LessonSession:
    return LessonSession(lesson_id=lesson_id, title="Ordering coffee", steps=_steps(steps))


def _pipeline(
    lesson_session: LessonSession | None = None,
    messages_seen: list | None = None,
    kwargs_seen: list | None = None,
    tool_call: LLMToolCall | None = None,
    reply: str = "Muy bien.",
) -> ConversationPipeline:
    stt = AsyncMock()
    stt.transcribe = AsyncMock(return_value="Un café, por favor")
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"mp3")
    return ConversationPipeline(
        llm=_make_llm(
            messages_seen if messages_seen is not None else [],
            kwargs_seen if kwargs_seen is not None else [],
            tool_call=tool_call,
            reply=reply,
        ),
        tts=tts,
        stt=stt,
        cefr_level="B1",
        target_language="es-ES",
        lesson_session=lesson_session,
    )


def _lesson_call(result: str = "passed") -> LLMToolCall:
    return LLMToolCall(
        id="call_lesson_1",
        name=LESSON_STEP_RESULT_TOOL_NAME,
        arguments={"result": result},
        raw_arguments=json.dumps({"result": result}),
    )


def _advance_event() -> dict:
    return {
        "type": "websocket.receive",
        "text": json.dumps({"type": "client_event", "event": "lesson_advance"}),
    }


DISCONNECT = {"type": "websocket.disconnect"}


# 1 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_lesson_means_no_lesson_frames() -> None:
    """Inertness: without a session a turn is byte-for-byte today's turn."""
    messages_seen: list = []
    kwargs_seen: list = []
    pipeline = _pipeline(messages_seen=messages_seen, kwargs_seen=kwargs_seen)
    ws = FakeWS()

    await pipeline._process(b"audio", ws)

    types = ws.types()
    assert "turn_complete" in types
    assert "lesson_state" not in types
    assert "lesson_completed" not in types
    assert "<guided_lesson>" not in messages_seen[0][0]["content"]
    tool_names = [t.name for t in kwargs_seen[0]["tools"]]
    assert tool_names == [SAVE_USER_MEMORY_TOOL_NAME]


# 2 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lesson_overlay_is_in_the_system_prompt() -> None:
    messages_seen: list = []
    pipeline = _pipeline(lesson_session=_session(), messages_seen=messages_seen)

    await pipeline._process(b"audio", FakeWS())

    system_prompt = messages_seen[0][0]["content"]
    assert "<guided_lesson>" in system_prompt
    assert STEP_ONE_BODY in system_prompt


# 3 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overlay_follows_the_current_step() -> None:
    messages_seen: list = []
    session = _session()
    session.advance("passed")
    pipeline = _pipeline(lesson_session=session, messages_seen=messages_seen)

    await pipeline._process(b"audio", FakeWS())

    system_prompt = messages_seen[0][0]["content"]
    assert STEP_TWO_BODY in system_prompt
    assert STEP_ONE_BODY not in system_prompt


# 4 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lesson_tool_is_offered_alongside_memory_tool() -> None:
    kwargs_seen: list = []
    pipeline = _pipeline(lesson_session=_session(), kwargs_seen=kwargs_seen)

    await pipeline._process(b"audio", FakeWS())

    tool_names = [t.name for t in kwargs_seen[0]["tools"]]
    assert SAVE_USER_MEMORY_TOOL_NAME in tool_names
    assert LESSON_STEP_RESULT_TOOL_NAME in tool_names


# 5 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_call_advances_and_emits_lesson_state() -> None:
    session = _session()
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._lesson_state_sent_index = 0  # as the greeting would have left it
    ws = FakeWS()

    await pipeline._process(b"audio", ws)

    assert session.current_index == 1
    states = ws.frames("lesson_state")
    assert len(states) == 1
    assert states[0]["lesson_id"] == 42
    assert states[0]["step_index"] == 1
    assert states[0]["total"] == 2
    assert states[0]["steps"][0]["status"] == "done"
    assert states[0]["steps"][1]["status"] == "current"
    assert "turn_id" in states[0]
    assert "lesson_completed" not in ws.types()
    # The frame must follow the assistant's audio for the turn, not precede it.
    kinds = [k for k, _ in ws.sent]
    assert "bytes" in kinds
    assert kinds.index("bytes") < [
        i for i, (k, v) in enumerate(ws.sent) if k == "json" and v.get("type") == "lesson_state"
    ][0]


# 6 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_final_step_emits_lesson_completed() -> None:
    session = _session(lesson_id=7, steps=1)
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._lesson_state_sent_index = 0  # as the greeting would have left it
    ws = FakeWS()

    await pipeline._process(b"audio", ws)

    assert session.is_complete
    states = ws.frames("lesson_state")
    completed = ws.frames("lesson_completed")
    assert len(states) == 1
    assert states[0]["lesson_id"] == 7
    assert states[0]["step_index"] == 1
    assert len(completed) == 1
    assert completed[0]["lesson_id"] == 7
    assert "turn_id" in completed[0]
    types = ws.types()
    assert types.index("lesson_state") < types.index("lesson_completed")


# 7 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_advance_client_event_advances_and_emits_state() -> None:
    session = _session()
    pipeline = _pipeline(lesson_session=session)
    ws = FakeWS(
        incoming=[
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "client_event", "event": "lesson_advance"}),
            },
            {"type": "websocket.disconnect"},
        ]
    )

    await pipeline.run(ws)

    assert session.current_index == 1
    states = ws.frames("lesson_state")
    assert any(s["step_index"] == 1 for s in states)
    assert "lesson_completed" not in ws.types()


# 8 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_advance_without_lesson_is_ignored() -> None:
    pipeline = _pipeline(lesson_session=None)
    ws = FakeWS(
        incoming=[
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "client_event", "event": "lesson_advance"}),
            },
            {"type": "websocket.disconnect"},
        ]
    )

    await pipeline.run(ws)

    types = ws.types()
    assert "lesson_state" not in types
    assert "lesson_completed" not in types
    assert "error" not in types


# 9 ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_messages_contain_the_overlay() -> None:
    kwargs_seen: list = []
    pipeline = _pipeline(lesson_session=_session(), kwargs_seen=kwargs_seen)

    await pipeline._process(b"audio", FakeWS())

    fallback_messages = kwargs_seen[0]["fallback_messages"]
    fallback_prompt = fallback_messages[0]["content"]
    assert "<guided_lesson>" in fallback_prompt
    assert STEP_ONE_BODY in fallback_prompt
    # The fallback is the tool-less variant: it must not advertise tools.
    assert SAVE_USER_MEMORY_TOOL_NAME not in fallback_prompt


# 10 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_tool_name_returns_error_result() -> None:
    kwargs_seen: list = []
    pipeline = _pipeline(lesson_session=_session(), kwargs_seen=kwargs_seen)

    await pipeline._process(b"audio", FakeWS())

    executor = kwargs_seen[0]["tool_executor"]
    call = LLMToolCall(id="call_x", name="not_a_tool", arguments={}, raw_arguments="{}")
    result = await executor(call)

    assert result.is_error is True
    assert result.content["error"] == "unknown_tool"
    assert result.call is call


# ── Fix round 1 (review findings) ───────────────────────────────────────────


# 11 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lesson_tool_respects_the_tools_unsupported_latch() -> None:
    """A provider that already rejected tools must not be offered the lesson
    tool either. Otherwise every remaining turn of the lesson costs a rejected
    tool call plus a `fallback_messages` re-issue — the latch exists to stop
    exactly that, and the memory tool already obeys it."""
    kwargs_seen: list = []
    messages_seen: list = []
    pipeline = _pipeline(
        lesson_session=_session(), kwargs_seen=kwargs_seen, messages_seen=messages_seen
    )
    pipeline._memory_tools_available = False

    await pipeline._process(b"audio", FakeWS())

    assert "tools" not in kwargs_seen[0]
    assert "tool_executor" not in kwargs_seen[0]
    assert "fallback_messages" not in kwargs_seen[0]
    # The tutor can still teach — the overlay rides the ordinary system prompt;
    # only automatic advancement is lost, which the manual advance covers.
    assert STEP_ONE_BODY in messages_seen[0][0]["content"]


# 12 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overlay_is_resynced_after_the_memory_refresh_await() -> None:
    """`_refresh_memory_prompt` builds the prompt twice, straddling a DB await.
    A manual advance handled by `run()` during that await must not leave the
    second build teaching the step the client has already moved past."""
    session = _session()
    messages_seen: list = []
    pipeline = _pipeline(lesson_session=session, messages_seen=messages_seen)
    pipeline._user_id = 1  # take the DB branch of _refresh_memory_prompt

    class _FakeDB:
        async def get(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def _fake_db_session():
        yield _FakeDB()

    async def _get_user_memories(_db, _user_id):
        session.advance("passed")  # the advance that lands during the await
        return []

    with (
        patch("app.services.conversation_pipeline.db_session", _fake_db_session),
        patch(
            "app.services.conversation_pipeline.get_user_memories",
            side_effect=_get_user_memories,
        ),
    ):
        await pipeline._process(b"audio", FakeWS())

    system_prompt = messages_seen[0][0]["content"]
    assert STEP_TWO_BODY in system_prompt
    assert STEP_ONE_BODY not in system_prompt


# 13 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repeated_manual_advances_in_one_turn_advance_once() -> None:
    """Spammed `lesson_advance` frames must not walk the lesson to completion.
    Task 6 wires this frame to the real completion endpoint, so an unthrottled
    path is free XP."""
    session = _session()
    pipeline = _pipeline(lesson_session=session)
    pipeline._greet = AsyncMock()  # keep the turn counter still
    ws = FakeWS(incoming=[_advance_event(), _advance_event(), _advance_event(), DISCONNECT])

    await pipeline.run(ws)

    assert session.current_index == 1
    assert len(ws.frames("lesson_state")) == 1
    assert "lesson_completed" not in ws.types()


# 14 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_advance_after_a_tool_advance_in_the_same_turn_is_ignored() -> None:
    """A manual advance racing the tool result inside one turn would otherwise
    skip a step the tutor never taught."""
    session = _session()
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._greet = AsyncMock()

    await pipeline._process(b"audio", FakeWS())
    assert session.current_index == 1  # advanced by the tool

    ws = FakeWS(incoming=[_advance_event(), DISCONNECT])
    await pipeline.run(ws)

    assert session.current_index == 1
    assert ws.frames("lesson_state") == []


@pytest.mark.asyncio
async def test_manual_advance_is_allowed_again_after_the_cooldown() -> None:
    """The guard is a short cooldown, not a lockout: the legitimate rhythm
    (advance → converse → advance) must keep working."""
    session = _session(steps=2)
    pipeline = _pipeline(lesson_session=session)
    pipeline._greet = AsyncMock()

    ws = FakeWS(incoming=[_advance_event(), DISCONNECT])
    await pipeline.run(ws)
    assert session.current_index == 1

    await pipeline._process(b"audio", FakeWS())  # a real turn in between
    pipeline._last_lesson_advance_at -= 10  # cooldown elapses

    ws2 = FakeWS(incoming=[_advance_event(), DISCONNECT])
    await pipeline.run(ws2)

    assert session.is_complete
    assert len(ws2.frames("lesson_completed")) == 1


@pytest.mark.asyncio
async def test_manual_advance_works_after_a_tool_advance_once_cooldown_passes() -> None:
    """The learner's deliberate skip must work even when the tool advanced the
    same turn — a per-turn lock left the Next Section button dead in practice,
    because the tool advances most turns."""
    session = _session(steps=3)
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._greet = AsyncMock()

    await pipeline._process(b"audio", FakeWS())
    assert session.current_index == 1  # advanced by the tool

    pipeline._last_lesson_advance_at -= 10  # cooldown elapses, no new turn

    ws = FakeWS(incoming=[_advance_event(), DISCONNECT])
    await pipeline.run(ws)

    assert session.current_index == 2
    assert len(ws.frames("lesson_state")) == 1


# 15 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_failure_after_a_tool_advance_still_emits_lesson_state() -> None:
    """The step advanced server-side before TTS failed; the panel must not be
    left showing a step already recorded done."""
    session = _session()
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._lesson_state_sent_index = 0  # as the greeting would have left it
    ws = FakeWS(fail_bytes=True)

    await pipeline._process(b"audio", ws)

    codes = [m.get("code") for m in ws.json_messages()]
    assert "tts_failed" in codes
    states = ws.frames("lesson_state")
    assert len(states) == 1
    assert states[0]["step_index"] == 1


# 16 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_greeting_that_produces_nothing_still_emits_initial_lesson_state() -> None:
    """`_greet` can end without a `turn_complete` (empty text, aborted send,
    exception). The step panel must still render on every one of those."""
    session = _session()
    pipeline = _pipeline(lesson_session=session, reply="")
    ws = FakeWS()

    await pipeline._greet(ws)

    assert "turn_complete" not in ws.types()
    states = ws.frames("lesson_state")
    assert len(states) == 1
    assert states[0]["step_index"] == 0
    assert session.current_index == 0  # greeting never advances the lesson


@pytest.mark.asyncio
async def test_failed_greeting_still_emits_initial_lesson_state() -> None:
    session = _session()
    pipeline = _pipeline(lesson_session=session)
    pipeline.llm.chat = AsyncMock(side_effect=RuntimeError("provider down"))
    ws = FakeWS()

    await pipeline._greet(ws)

    assert len(ws.frames("lesson_state")) == 1


# ── Fix round 2 (final review findings) ─────────────────────────────────────
#
# A cancelled greeting and a cancelled turn both leave the client's panel out
# of sync with the server-side session, with nothing that would ever resend it.
# The next turn must resync — including the completion frame, which is what
# makes the client write back real XP.


def _hanging(event: asyncio.Event):
    """A coroutine function that parks forever once it has been entered."""

    async def _hang(*_args, **_kwargs):
        event.set()
        await asyncio.sleep(3600)

    return AsyncMock(side_effect=_hang)


def _plain_chat(reply: str = "Vale."):
    """An LLM that never calls a tool — used for the turn *after* a cancellation."""

    async def _stream():
        yield _chunk(reply)

    async def _chat(_messages, **_kwargs):
        return _stream()

    return AsyncMock(side_effect=_chat)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# 17 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_barge_in_during_the_greeting_is_resynced_on_the_next_turn() -> None:
    """Speaking over the greeting cancels it before it can publish the opening
    `lesson_state`, and the panel only renders once a state frame arrives — so
    without a resync the learner gets no panel and no manual advance for the
    whole session (the only advance path on a tools-less provider)."""
    session = _session()
    pipeline = _pipeline(lesson_session=session)
    ws = FakeWS()

    greeting_started = asyncio.Event()
    normal_chat = pipeline.llm.chat
    pipeline.llm.chat = _hanging(greeting_started)
    pipeline.current_task = asyncio.create_task(pipeline._greet(ws))
    await greeting_started.wait()

    pipeline.llm.chat = normal_chat
    await pipeline.handle_audio(b"audio", ws)  # barge-in cancels the greeting
    await pipeline.current_task

    assert "barge_in" in ws.types()
    states = ws.frames("lesson_state")
    assert len(states) == 1
    assert states[0]["step_index"] == 0
    assert "lesson_completed" not in ws.types()


# 18 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_cancelled_after_a_tool_advance_is_resynced_next_turn() -> None:
    """The tool mutates the session mid-stream but `lesson_state` only goes out
    after TTS. A cancellation in between leaves the panel a step behind for
    good; the next turn must correct it."""
    session = _session(steps=2)
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._lesson_state_sent_index = 0  # as the greeting would have left it

    tts_started = asyncio.Event()
    normal_tts = pipeline.tts.synthesize
    pipeline.tts.synthesize = _hanging(tts_started)
    ws1 = FakeWS()
    task = asyncio.create_task(pipeline._process(b"audio", ws1))
    await tts_started.wait()
    await _cancel(task)

    assert session.current_index == 1  # the advance happened server-side
    assert ws1.frames("lesson_state") == []  # ...and was never published

    pipeline.tts.synthesize = normal_tts
    pipeline.llm.chat = _plain_chat()
    ws2 = FakeWS()
    await pipeline._process(b"audio", ws2)

    states = ws2.frames("lesson_state")
    assert len(states) == 1
    assert states[0]["step_index"] == 1
    assert "lesson_completed" not in ws2.types()


# 19 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_final_step_is_resynced_with_lesson_completed() -> None:
    """Worst case: the lost advance was the last step, so `lesson_completed`
    never reached the client, which means no `/complete` POST — no XP, no
    streak, no plan advancement."""
    session = _session(lesson_id=7, steps=1)
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._lesson_state_sent_index = 0

    tts_started = asyncio.Event()
    normal_tts = pipeline.tts.synthesize
    pipeline.tts.synthesize = _hanging(tts_started)
    ws1 = FakeWS()
    task = asyncio.create_task(pipeline._process(b"audio", ws1))
    await tts_started.wait()
    await _cancel(task)

    assert session.is_complete
    assert ws1.frames("lesson_completed") == []

    pipeline.tts.synthesize = normal_tts
    pipeline.llm.chat = _plain_chat()
    ws2 = FakeWS()
    await pipeline._process(b"audio", ws2)

    states = ws2.frames("lesson_state")
    completed = ws2.frames("lesson_completed")
    assert len(states) == 1
    assert states[0]["step_index"] == 1
    assert len(completed) == 1
    assert completed[0]["lesson_id"] == 7
    types = ws2.types()
    assert types.index("lesson_state") < types.index("lesson_completed")


# 20 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lost_completion_is_resynced_on_a_manual_advance_event() -> None:
    """`run()` bails on `is_complete`, so a learner whose completion was lost
    has only the "Next section" button left — it must catch them up."""
    session = _session(lesson_id=9, steps=1)
    pipeline = _pipeline(lesson_session=session)
    pipeline._greet = AsyncMock()
    session.advance("passed")  # completed server-side, never published

    ws = FakeWS(incoming=[_advance_event(), DISCONNECT])
    await pipeline.run(ws)

    assert len(ws.frames("lesson_state")) == 1
    assert len(ws.frames("lesson_completed")) == 1
    assert ws.frames("lesson_completed")[0]["lesson_id"] == 9


# 21 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lesson_completed_is_never_emitted_twice() -> None:
    """The post-TTS fast path and the resync are both live; the latch is what
    stops the client double-POSTing the completion."""
    session = _session(lesson_id=7, steps=1)
    pipeline = _pipeline(lesson_session=session, tool_call=_lesson_call())
    pipeline._lesson_state_sent_index = 0
    pipeline._greet = AsyncMock()

    ws1 = FakeWS()
    await pipeline._process(b"audio", ws1)  # fast path emits it
    assert len(ws1.frames("lesson_completed")) == 1

    pipeline.llm.chat = _plain_chat()
    ws2 = FakeWS()
    await pipeline._process(b"audio", ws2)  # resync must not repeat it
    assert ws2.frames("lesson_completed") == []
    assert ws2.frames("lesson_state") == []

    ws3 = FakeWS(incoming=[_advance_event(), DISCONNECT])
    await pipeline.run(ws3)  # nor the manual-advance catch-up
    assert ws3.frames("lesson_completed") == []


# 22 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resync_is_inert_without_a_lesson_session() -> None:
    """No session means no extra frames and no extra work on any turn."""
    pipeline = _pipeline(lesson_session=None)
    ws = FakeWS()

    await pipeline._process(b"audio", ws)

    types = ws.types()
    assert "lesson_state" not in types
    assert "lesson_completed" not in types
    assert pipeline._lesson_state_sent_index is None
    assert pipeline._lesson_completed_sent is False

    bare = FakeWS()
    await pipeline._resync_lesson_state(bare, 1)
    assert bare.sent == []


# 23 ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resync_sends_nothing_when_the_client_is_already_in_step() -> None:
    """The resync is a safety net, not a per-turn broadcast."""
    session = _session()
    pipeline = _pipeline(lesson_session=session)
    pipeline._lesson_state_sent_index = 0

    ws = FakeWS()
    await pipeline._process(b"audio", ws)

    assert ws.frames("lesson_state") == []
    assert "lesson_completed" not in ws.types()
