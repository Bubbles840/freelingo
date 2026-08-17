from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.app_logger import get_logger
from app.models.user import User
from app.services.language_helpers import (
    get_iso639,
    get_language_name,
    get_native_language_name,
)
from app.services.lesson_voice import (
    LESSON_STEP_RESULT_TOOL_NAME,
    build_lesson_step_tool,
    execute_lesson_step_result,
)
from app.services.llm_adapter import (
    LLMError,
    LLMStream,
    LLMStreamReset,
    LLMTimeoutError,
    LLMToolResult,
    LLMToolResultEvent,
    LLMUnavailableError,
)
from app.services.memory_service import (
    SAVE_USER_MEMORY_TOOL_NAME,
    build_memory_context,
    build_save_user_memory_tool,
    execute_save_user_memory,
    get_user_memories,
)
from app.services.prompts.common import get_language_prompt_overlay
from app.services.prompts.tutor import build_conversation_system_prompt
from app.services.pronunciation_service import format_pronunciation_annotation
from app.services.quota_service import record_session_seconds
from app.utils.db import db_session

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger(__name__)

TTS_MAX_RETRIES = 1
TTS_RETRY_DELAY_SECONDS = 0.2

WARNING_ADVANCE_SECONDS = 60  # How many seconds before timeout to send the warning
PRONUNCIATION_TIMEOUT_SECONDS = 2.0
LESSON_ADVANCE_DEBOUNCE_SECONDS = 2.0
# Fork: markdown markers the model sometimes emits in spoken replies.
_MARKDOWN_MARKER_RE = re.compile(r"\*{1,3}|`+")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def _build_conversation_system_prompt(
    *,
    student_name: str,
    cefr_level: str,
    native_language: str,
    target_language_name: str,
    user_context: str,
    memory_context: str,
    language_prompt_overlay: str = "",
    memory_tools_enabled: bool = True,
    pronunciation_feedback_enabled: bool = False,
    lesson_overlay: str = "",
    roleplay_overlay: str = "",
) -> str:
    return build_conversation_system_prompt(
        student_name=student_name,
        cefr_level=cefr_level,
        native_language=native_language,
        target_language_name=target_language_name,
        user_context=user_context,
        memory_context=memory_context,
        language_prompt_overlay=language_prompt_overlay,
        memory_tools_enabled=memory_tools_enabled,
        pronunciation_feedback_enabled=pronunciation_feedback_enabled,
        lesson_overlay=lesson_overlay,
        roleplay_overlay=roleplay_overlay,
    )


class ConversationPipeline:
    """
    Orchestrates STT → LLM → TTS streaming with barge-in and session timeout.
    """

    def __init__(
        self,
        llm: object,
        tts: object,
        stt: object,
        cefr_level: str = "B1",
        native_language: str = "es",
        target_language: str = "en-GB",
        student_name: str = "Student",
        max_duration: int = 1800,
        inactivity_timeout: int = 180,
        initial_context: list[dict] | None = None,
        user_id: int | None = None,
        conversation_id: int | None = None,
        bio: str | None = None,
        learning_goals: str | None = None,
        memories: list | None = None,
        voice: str = "",
        study_plan_id: int | None = None,
        pronunciation: object | None = None,
        lesson_session: object | None = None,
        roleplay_overlay: str = "",
    ) -> None:
        self.llm = llm
        self.tts = tts
        self.stt = stt
        self.pronunciation = pronunciation
        self.lesson_session = lesson_session
        self.roleplay_overlay = roleplay_overlay
        # Gate on the provider's `enabled` capability, not on `is not None`:
        # the router always attaches a live provider (NullPronunciationService
        # by default), so an `is not None` check would never actually be off.
        self._pronunciation_enabled = getattr(pronunciation, "enabled", False)
        # Fork: learner-controlled switch layered on top of the provider
        # capability — session-scoped, flippable mid-conversation.
        self._pronunciation_user_enabled = True
        self._voice = voice
        self._stt_language = get_iso639(target_language)
        self._target_language = target_language
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._study_plan_id = study_plan_id
        # Build user context section
        _ctx_parts: list[str] = []
        if learning_goals:
            try:
                import json as _json  # noqa: PLC0415

                goals = _json.loads(learning_goals)
                if isinstance(goals, list) and goals:
                    _ctx_parts.append(f"Learning goals: {', '.join(goals)}")
            except ValueError, TypeError:
                pass
        if bio and bio.strip():
            _ctx_parts.append(f"About the student: {bio.strip()}")
        user_context = (
            ("\nStudent context:\n" + "\n".join(f"- {p}" for p in _ctx_parts) + "\n")
            if _ctx_parts
            else ""
        )
        self._memory_context = build_memory_context(memories or [])
        target_language_name = get_language_name(target_language)
        language_prompt_overlay = get_language_prompt_overlay(target_language)
        self._prompt_args = {
            "student_name": student_name,
            "cefr_level": cefr_level,
            "native_language": get_native_language_name(native_language),
            "target_language_name": target_language_name,
            "user_context": user_context,
            "language_prompt_overlay": language_prompt_overlay,
            "pronunciation_feedback_enabled": self._pronunciation_enabled,
            "lesson_overlay": "",
            "roleplay_overlay": roleplay_overlay,
        }
        self.system_prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
        )
        self.max_duration = max_duration
        self.inactivity_timeout = inactivity_timeout
        self._redis: object | None = None  # injected after construction
        self._recorded = False
        self._freemium_voice = False
        self._memory_tools_available = True

        self.current_task: asyncio.Task | None = None
        # Pre-populate history from optional chat context
        if initial_context:
            self.history: list[dict] = [
                {"role": m["role"], "content": m["content"]}
                for m in initial_context
                if isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)
                and m["content"].strip()
            ][-10:]
        else:
            self.history = []
        self._session_start = time.monotonic()
        self._last_activity = time.monotonic()
        self._timer_tasks: list[asyncio.Task] = []
        self._pending_saves: list[asyncio.Task] = []
        self._inactivity_warning_sent = False
        self._send_lock = asyncio.Lock()
        self._turn_id = 0
        # Fork (guided lessons): at most one step advance per turn.
        self._last_lesson_advance_turn: int | None = None
        self._last_lesson_advance_at: float = 0.0
        # Fork (guided lessons): the step index the client has actually been
        # told about, and whether it has been told the lesson is finished.
        # `None` means nothing has been published yet. Both are the ground
        # truth for the next-turn resync (see `_resync_lesson_state`).
        self._lesson_state_sent_index: int | None = None
        self._lesson_completed_sent = False
        self._client_close_reason: str | None = None

    @staticmethod
    def _fmt_exc(exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _task_state(task: asyncio.Task | None) -> str:
        if task is None:
            return "none"
        if task.cancelled():
            return "cancelled"
        if task.done():
            if task.exception() is not None:
                return "done:error"
            return "done"
        return "running"

    @staticmethod
    async def _cancel_pronunciation_task(assess_task: asyncio.Task | None) -> None:
        """Cancel a pronunciation assessment task and retrieve any outcome.

        A bare `.cancel()` on a task that has already finished (with a result
        or an exception) is a no-op — a stored exception would never be
        retrieved, which later surfaces as "Task exception was never
        retrieved". We must retrieve it without swallowing a *new* barge-in
        cancellation of the caller (`_process`) that might land during this
        very await — `await assess_task` wrapped in `except BaseException`
        would catch that too, silently turning the caller's own cancellation
        into a normal return instead of letting `CancelledError` propagate.
        `asyncio.wait` does not raise on a failed/cancelled child, so it lets
        us retrieve the outcome quietly while leaving an outer cancellation
        free to propagate as usual.
        """
        if assess_task is None:
            return
        assess_task.cancel()
        await asyncio.wait({assess_task})
        if not assess_task.cancelled():
            assess_task.exception()

    async def _send_json(self, ws: WebSocket, data: dict) -> None:
        async with self._send_lock:
            await ws.send_json(data)

    def _next_turn_id(self) -> int:
        self._turn_id += 1
        return self._turn_id

    async def _send_status(self, ws: WebSocket, turn_id: int, value: str) -> None:
        await self._send_json(ws, {"type": "status", "value": value, "turn_id": turn_id})

    def _sync_lesson_overlay(self) -> None:
        """Point the prompt at the lesson's current step (fork: guided lessons)."""
        if self.lesson_session is None:
            return
        self._prompt_args["lesson_overlay"] = self.lesson_session.overlay()

    def _record_lesson_advance(self, turn_id: int) -> None:
        """Mark this turn as having already advanced the lesson (fork).

        Both the tool path and the manual `lesson_advance` event record here so
        neither can advance a step the other already advanced, and so a burst of
        client events cannot walk the whole lesson to completion.
        """
        if self._last_lesson_advance_turn is None or turn_id > self._last_lesson_advance_turn:
            self._last_lesson_advance_turn = turn_id
        self._last_lesson_advance_at = time.monotonic()

    def _lesson_progress_key(self) -> str | None:
        if self.lesson_session is None or self._user_id is None:
            return None
        return f"voice_lesson_progress:{self._user_id}:{self.lesson_session.lesson_id}"

    async def _persist_lesson_progress(self) -> None:
        """Save step progress to Redis so a dropped session can resume (fork)."""
        key = self._lesson_progress_key()
        if key is None or self._redis is None:
            return
        try:
            import json as _json

            if self.lesson_session.is_complete:
                await self._redis.delete(key)
            else:
                await self._redis.set(
                    key,
                    _json.dumps(
                        {
                            "i": self.lesson_session.current_index,
                            "r": self.lesson_session.results,
                        }
                    ),
                    ex=172800,  # two days
                )
        except Exception:  # noqa: BLE001 — resume is best-effort
            logger.warning("[pipeline] Could not persist lesson progress")

    async def _send_late_pronunciation(
        self, ws: WebSocket, turn_id: int, assess_task: "asyncio.Task"
    ) -> None:
        """Deliver a score chip that finished after the turn moved on (fork)."""
        try:
            result = await assess_task
        except Exception:  # noqa: BLE001 — includes provider failures
            return
        if result is None:
            return
        payload = result.to_payload()
        payload["type"] = "pronunciation"
        payload["turn_id"] = turn_id
        try:
            await self._send_json(ws, payload)
        except Exception:  # noqa: BLE001 — socket may already be gone
            logger.debug("[pipeline] Late pronunciation frame not delivered")

    async def _send_lesson_state(self, ws: WebSocket, turn_id: int) -> None:
        """Publish lesson progress (fork: guided lessons)."""
        if self.lesson_session is None:
            return
        payload = self.lesson_session.state_payload()
        payload["type"] = "lesson_state"
        payload["turn_id"] = turn_id
        await self._send_json(ws, payload)
        # Recorded only after the send succeeded, so a frame lost to a dead
        # socket or a cancellation is retried by the next-turn resync.
        self._lesson_state_sent_index = payload["step_index"]

    async def _send_lesson_completed(self, ws: WebSocket, turn_id: int) -> None:
        """Announce that the final lesson step is done (fork: guided lessons).

        Latched: completion is the frame that makes the client POST `/complete`
        for real XP, so it is emitted from both the post-TTS fast path and the
        next-turn resync. The latch is what keeps those two from ever sending
        it twice — every emission in the pipeline goes through here.
        """
        if self.lesson_session is None or self._lesson_completed_sent:
            return
        await self._send_json(
            ws,
            {
                "type": "lesson_completed",
                "lesson_id": self.lesson_session.lesson_id,
                "turn_id": turn_id,
            },
        )
        self._lesson_completed_sent = True

    async def _resync_lesson_state(self, ws: WebSocket, turn_id: int) -> None:
        """Re-publish lesson progress the client may never have received (fork).

        The tool advance mutates the session mid-stream, but `lesson_state` /
        `lesson_completed` only go out after the turn's TTS, and `_greet` skips
        its opening state when it is cancelled. A barge-in, an `interrupt`
        frame or a close request landing in either window leaves the client
        stuck a step behind — or, if the advance was the final step, never
        told to write back the completion at all. Called at the top of each
        turn, this is the safety net for both; the post-TTS emission remains
        the fast path.

        Best-effort: a send failure here must not turn an otherwise healthy
        turn into an error, and it must not latch anything (see above).
        """
        if self.lesson_session is None:
            return
        if (
            self._lesson_state_sent_index == self.lesson_session.current_index
            and not (self.lesson_session.is_complete and not self._lesson_completed_sent)
        ):
            return
        try:
            if self._lesson_state_sent_index != self.lesson_session.current_index:
                await self._send_lesson_state(ws, turn_id)
            if self.lesson_session.is_complete:
                await self._send_lesson_completed(ws, turn_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[pipeline] Lesson state resync failed: %s", self._fmt_exc(exc))

    async def _send_initial_lesson_state(self, ws: WebSocket, turn_id: int) -> None:
        """Best-effort opening `lesson_state` (fork: guided lessons).

        The step panel must render on every way the greeting can end — including
        an empty or failed greeting — so this is called from all of them except
        cancellation. A dead socket here must not turn a merely unsuccessful
        greeting into an unretrieved task exception, hence the swallow.
        """
        if self.lesson_session is None:
            return
        try:
            await self._send_lesson_state(ws, turn_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[pipeline] Initial lesson_state send failed: %s", self._fmt_exc(exc))

    async def _send_memory_updated(self, ws: WebSocket, turn_id: int) -> None:
        send_task = asyncio.create_task(
            self._send_json(ws, {"type": "memory_updated", "turn_id": turn_id})
        )
        try:
            await asyncio.shield(send_task)
        except asyncio.CancelledError:
            try:
                await send_task
            except Exception as exc:
                logger.debug(
                    "[pipeline] Memory update notification failed during cancellation: %s",
                    self._fmt_exc(exc),
                )
            raise

    @staticmethod
    def _extract_speech_text(raw_text: str) -> str:
        return ConversationPipeline._clean_sentence(raw_text).strip()

    @staticmethod
    def _stream_text(chunk: object) -> str:
        if isinstance(chunk, str):
            return chunk
        choices = getattr(chunk, "choices", None)
        if not choices:
            return ""
        return getattr(getattr(choices[0], "delta", None), "content", None) or ""

    async def _refresh_memory_prompt(self) -> None:
        self._sync_lesson_overlay()
        self.system_prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
            memory_tools_enabled=self._memory_tools_available,
        )
        if self._user_id is None:
            return
        try:
            async with db_session() as db:
                memories = await get_user_memories(db, self._user_id)
                user = await db.get(User, self._user_id)
                if user is not None:
                    self._prompt_args["native_language"] = get_native_language_name(
                        user.native_language
                    )
            self._memory_context = build_memory_context(memories)
            # Re-synced because the DB await above yields the loop: a manual
            # `lesson_advance` handled by `run()` in that window would otherwise
            # leave this rebuild teaching the step the client has moved past.
            self._sync_lesson_overlay()
            self.system_prompt = _build_conversation_system_prompt(
                **self._prompt_args,
                memory_context=self._memory_context,
                memory_tools_enabled=self._memory_tools_available,
            )
        except Exception:
            logger.exception("[pipeline] Failed to refresh global memory context")

    async def _safe_send_bytes(self, ws: WebSocket, data: bytes) -> bool:
        try:
            async with self._send_lock:
                await ws.send_bytes(data)
            return True
        except RuntimeError as exc:
            logger.debug("[pipeline] Socket closed while sending audio: %s", exc)
            return False
        except Exception as exc:
            logger.debug("[pipeline] Audio send failed: %s", self._fmt_exc(exc))
            return False

    async def _close_ws(self, ws: WebSocket, code: int = 1000) -> None:
        logger.debug("[pipeline] Closing websocket with code=%s", code)
        async with self._send_lock:
            await ws.close(code=code)

    async def _synthesize_chunk(self, text: str) -> bytes:
        attempts = TTS_MAX_RETRIES + 1
        trace_id = uuid.uuid4().hex[:10]
        logger.info(
            "[pipeline] TTS request scheduling — trace=%s len=%d attempts=%d",
            trace_id,
            len(text),
            attempts,
        )
        for attempt in range(1, attempts + 1):
            try:
                logger.debug(
                    "[pipeline] TTS request start: len=%d chars, attempt=%d/%d",
                    len(text),
                    attempt,
                    attempts,
                )
                audio = await self.tts.synthesize(text, self._voice or None, self._stt_language)
                if not audio:
                    raise RuntimeError("TTS returned empty audio payload")
                return audio
            except TimeoutError:
                if attempt >= attempts:
                    logger.warning(
                        "[pipeline] TTS request timed out (%d/%d attempts)",
                        attempt,
                        attempts,
                    )
                    raise
                logger.warning(
                    "[pipeline] TTS request timed out (%d/%d); retrying",
                    attempt,
                    attempts,
                )
                await asyncio.sleep(TTS_RETRY_DELAY_SECONDS * attempt)
            except asyncio.CancelledError:
                logger.warning(
                    "[pipeline] TTS request cancelled: trace=%s len=%d attempt=%d",
                    trace_id,
                    len(text),
                    attempt,
                )
                raise
            except GeneratorExit:
                logger.warning(
                    "[pipeline] TTS request interrupted by GeneratorExit: trace=%s len=%d attempt=%d",
                    trace_id,
                    len(text),
                    attempt,
                )
                raise
            except Exception as exc:
                if attempt >= attempts:
                    raise
                logger.warning(
                    "[pipeline] TTS request failed (%d/%d): %s",
                    attempt,
                    attempts,
                    exc,
                )
                await asyncio.sleep(TTS_RETRY_DELAY_SECONDS * attempt)

    @staticmethod
    def _split_tts_sentences(text: str) -> list[str]:
        """Split assistant text into ordered TTS chunks using full stops."""
        chunks: list[str] = []
        start = 0
        for idx, char in enumerate(text):
            if char != ".":
                continue
            sentence = text[start : idx + 1].strip()
            if sentence:
                chunks.append(sentence)
            start = idx + 1

        tail = text[start:].strip()
        if tail:
            chunks.append(tail)

        chunks = chunks or ([text.strip()] if text.strip() else [])
        # Fork: drop chunks with no letters (e.g. a bare "¿...?" example) —
        # LLM-based TTS models respond to punctuation-only input by literally
        # apologising aloud in English instead of staying silent.
        speakable = [c for c in chunks if _HAS_LETTER_RE.search(c)]
        return speakable if speakable else []

    async def _synthesize_and_send_response(
        self,
        ws: WebSocket,
        *,
        text: str,
        turn_id: int,
        transcript_payload: dict,
    ) -> tuple[int, int, bool, bool]:
        """Synthesize ordered sentence chunks and send the transcript after first audio."""
        chunks_sent = 0
        audio_bytes_sent = 0
        transcript_sent = False
        send_aborted = False

        for idx, sentence in enumerate(self._split_tts_sentences(text), start=1):
            try:
                audio = await self._synthesize_chunk(sentence)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[pipeline] TTS sentence failed after retry: turn_id=%s sentence=%s error=%s",
                    turn_id,
                    idx,
                    self._fmt_exc(exc),
                )
                continue

            send_ok = await self._safe_send_bytes(ws, audio)
            if not send_ok:
                logger.warning(
                    "[pipeline] Audio send failed and remaining chunks are being aborted: turn_id=%s",
                    turn_id,
                )
                send_aborted = True
                break

            chunks_sent += 1
            audio_bytes_sent += len(audio)

            if not transcript_sent:
                await self._send_json(ws, transcript_payload)
                transcript_sent = True

        if chunks_sent == 0 and not transcript_sent and not send_aborted:
            logger.warning(
                "[pipeline] All TTS sentence chunks failed; publishing text-only transcript: turn_id=%s",
                turn_id,
            )
            await self._send_json(ws, transcript_payload)
            transcript_sent = True

        return chunks_sent, audio_bytes_sent, transcript_sent, send_aborted

    @staticmethod
    def _clean_sentence(raw_sentence: str) -> str:
        """Normalize visible assistant text before synthesis.

        Fork: also strips markdown emphasis/heading markers — the model
        occasionally emits **bold** or *italics* despite instructions, and the
        TTS engine either reads the asterisks or trips over them.
        """
        cleaned = _MARKDOWN_MARKER_RE.sub("", raw_sentence)
        cleaned = _MARKDOWN_HEADING_RE.sub("", cleaned)
        return cleaned.strip()

    async def _greet(self, ws: WebSocket) -> None:
        """Generate and stream an opening greeting from the assistant."""
        turn_id = self._next_turn_id()
        trigger = {
            "role": "user",
            "content": "[Session started. Greet the student warmly and naturally — one or two sentences max — and invite them to speak.]",
        }
        if self.lesson_session is not None:
            # Fork (guided lessons): open on the lesson, not on small talk.
            trigger = {
                "role": "user",
                "content": (
                    "[Session started. Greet the student briefly and begin the guided "
                    "lesson at the current step described in your instructions.]"
                ),
            }
        elif self.roleplay_overlay:
            # Fork (roleplay mode): open in character, not with a generic greeting.
            trigger = {
                "role": "user",
                "content": (
                    "[Session started. Do not greet the student as a tutor. Open the "
                    "scenario from your instructions: set the scene in one sentence, "
                    "saying who you are and where you both are, then stay in "
                    "character and speak your first line.]"
                ),
            }
        self._sync_lesson_overlay()
        greeting_prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
            memory_tools_enabled=False,
        )
        messages = [{"role": "system", "content": greeting_prompt}] + self.history[-20:] + [trigger]

        try:
            full_response = ""
            llm_stream = await self.llm.chat(messages, stream=True)
            async for chunk in llm_stream:
                token = self._stream_text(chunk)
                full_response += token
            clean_full_response = self._extract_speech_text(full_response)
            if not clean_full_response:
                await self._send_initial_lesson_state(ws, turn_id)
                return
            await self._send_status(ws, turn_id, "thinking")

            (
                chunks_sent,
                _audio_bytes,
                transcript_sent,
                send_aborted,
            ) = await self._synthesize_and_send_response(
                ws,
                text=clean_full_response,
                turn_id=turn_id,
                transcript_payload={
                    "type": "transcript",
                    "role": "assistant",
                    "text": clean_full_response,
                    "final": True,
                    "turn_id": turn_id,
                },
            )
            if send_aborted or not transcript_sent:
                await self._send_initial_lesson_state(ws, turn_id)
                return

            self.history.append({"role": "assistant", "content": clean_full_response})
            self._pending_saves.append(
                asyncio.create_task(self._save_message("assistant", clean_full_response))
            )
            await self._send_status(ws, turn_id, "listening")
            await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})
            # Fork (guided lessons): let the client render the step panel at once.
            await self._send_initial_lesson_state(ws, turn_id)
        except asyncio.CancelledError:
            logger.warning(
                "[pipeline] Greeting cancelled for turn_id=%s",
                turn_id,
            )
            raise
        except Exception as exc:
            logger.error("[pipeline] Greeting failed: %s", exc)
            await self._send_initial_lesson_state(ws, turn_id)

    async def run(self, ws: WebSocket) -> None:
        """Main loop: starts timeout watchers then handles incoming messages."""
        logger.info("[pipeline] Conversation loop starting")
        self._timer_tasks = [
            asyncio.create_task(self._max_duration_watcher(ws)),
            asyncio.create_task(self._inactivity_watcher(ws)),
        ]
        self.current_task = asyncio.create_task(self._greet(ws))
        try:
            while True:
                try:
                    data = await ws.receive()
                except RuntimeError:
                    # Client disconnected — ws.receive() raises RuntimeError after disconnect
                    logger.info("[pipeline] ws.receive raised RuntimeError — disconnect")
                    break
                except Exception as exc:
                    logger.error("[pipeline] ws.receive failed: %s", self._fmt_exc(exc))
                    break
                if data.get("type") == "websocket.disconnect":
                    logger.info("[pipeline] websocket.disconnect event received")
                    break
                if "bytes" in data:
                    await self.handle_audio(data["bytes"], ws)
                elif "text" in data:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "client_event":
                        if msg.get("event") == "session_close_request":
                            self._client_close_reason = msg.get("reason", "manual")
                            logger.info(
                                "[pipeline] Client requested session close: reason=%s",
                                self._client_close_reason,
                            )
                            break
                        if msg.get("event") == "pronunciation_toggle":
                            # Fork: let the learner spend assessment quota only
                            # on the words they care about.
                            self._pronunciation_user_enabled = bool(
                                msg.get("enabled", True)
                            )
                            continue
                        if msg.get("event") == "keepalive":
                            # Fork (mic controls): client holds the floor during
                            # long thinking pauses / manual recordings.
                            self._last_activity = time.monotonic()
                            self._inactivity_warning_sent = False
                            continue
                        if msg.get("event") == "lesson_advance":
                            # Fork (guided lessons): manual step-through, and the
                            # only way to advance when the provider has no tools.
                            if self.lesson_session is None:
                                continue
                            if self.lesson_session.is_complete:
                                # Nothing left to advance, but a completion lost
                                # to a cancelled turn would otherwise never reach
                                # the client (and never award its XP), and no
                                # further turn is guaranteed. Cheapest catch-up
                                # point there is.
                                await self._resync_lesson_state(ws, self._turn_id)
                                continue
                            since_last = (
                                time.monotonic() - self._last_lesson_advance_at
                            )
                            if since_last < LESSON_ADVANCE_DEBOUNCE_SECONDS:
                                # Debounce, not a per-turn lock: the tool
                                # advances most turns, so refusing "this turn
                                # already advanced" left the button dead almost
                                # always. A short cooldown still stops a burst
                                # of events walking the lesson to completion,
                                # while a deliberate click after the tutor's
                                # advance is honoured — the learner's explicit
                                # skip is allowed.
                                logger.debug(
                                    "[pipeline] Ignoring lesson_advance: %.2fs since last advance",
                                    since_last,
                                )
                                continue
                            just_completed = self.lesson_session.advance("passed")
                            turn_id = self._next_turn_id()
                            self._record_lesson_advance(turn_id)
                            await self._persist_lesson_progress()
                            await self._send_lesson_state(ws, turn_id)
                            if just_completed:
                                await self._send_lesson_completed(ws, turn_id)
                            continue
                        logger.debug("[pipeline] Received unknown client_event: %s", msg)
                        continue
                    if msg.get("type") == "interrupt":
                        await self.cancel_current()
                        await self._send_json(ws, {"type": "interrupted"})
        finally:
            logger.info(
                "[pipeline] Conversation loop ending: current_task=%s",
                self._task_state(self.current_task),
            )
            logger.info(
                "[pipeline] Session close reason=%s",
                self._client_close_reason or "peer_disconnect/unknown",
            )
            await self.cancel_current()
            for t in self._timer_tasks:
                t.cancel()

    async def handle_audio(self, audio_bytes: bytes, ws: WebSocket) -> None:
        self._last_activity = time.monotonic()
        self._inactivity_warning_sent = False  # reset on new activity
        logger.debug(
            "[pipeline] Audio chunk received (%d bytes); current_task=%s",
            len(audio_bytes),
            self._task_state(self.current_task),
        )
        # Barge-in: cancel ongoing response if a new audio chunk arrives
        if self.current_task and not self.current_task.done():
            logger.info(
                "[pipeline] Barge-in requested: canceling active turn before STT",
            )
            await self.cancel_current()
            logger.info("[pipeline] Barge-in: previous turn cancelled")
            await self._send_json(ws, {"type": "barge_in"})
        self.current_task = asyncio.create_task(self._process(audio_bytes, ws))

    async def _process(self, audio_bytes: bytes, ws: WebSocket) -> None:
        turn_t0 = time.perf_counter()
        turn_id = self._next_turn_id()
        stt_ms: float | None = None
        llm_ms: float | None = None
        tts_send_ms: float | None = None
        tts_bytes: int = 0
        tts_chunks_sent = 0
        assistant_transcript_sent = False
        send_aborted = False

        # Fork (guided lessons): catch the client up on anything a cancelled
        # greeting or a cancelled turn never managed to send. A no-op (and no
        # await at all) whenever the client is already in step.
        await self._resync_lesson_state(ws, turn_id)

        # 1. STT. Pronunciation assessment starts AFTER it (fork): scripted
        # assessment against the Whisper transcript scores exactly the words
        # the learner sees in their bubble — unscripted mode ran Azure's own
        # recognition in parallel and the two transcriptions could disagree.
        assess_task: asyncio.Task | None = None
        assess_handed_off = False

        pronunciation_result = None
        try:
            try:
                await self._send_status(ws, turn_id, "transcribing")
                stt_t0 = time.perf_counter()
                user_text = await self.stt.transcribe(
                    audio_bytes, "audio.wav", "audio/wav", self._stt_language
                )
                stt_ms = (time.perf_counter() - stt_t0) * 1000
                logger.info("[pipeline] STT result: %r", user_text)
            except Exception as exc:
                await self._cancel_pronunciation_task(assess_task)
                logger.error("[pipeline] STT failed: %s", exc)
                logger.info(
                    "pipeline_turn_metrics",
                    stage="stt_failed",
                    stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
                    llm_ms=None,
                    tts_send_ms=None,
                    tts_chunks_sent=0,
                    tts_audio_bytes=0,
                    turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
                )
                await self._send_json(
                    ws,
                    {
                        "type": "error",
                        "code": "stt_failed",
                        "message": str(exc),
                        "turn_id": turn_id,
                    },
                )
                return

            user_text = user_text.strip()
            if not user_text:
                logger.info("[pipeline] Empty STT result — ignoring audio chunk")
                await self._cancel_pronunciation_task(assess_task)
                await self._send_status(ws, turn_id, "listening")
                return
            # Fork (guided lessons): one more learner turn on the current step —
            # feeds the overlay's stall nudge (rebuilt below via the prompt refresh).
            if self.lesson_session is not None:
                self.lesson_session.note_student_turn()

            await self._send_json(
                ws,
                {
                    "type": "transcript",
                    "role": "user",
                    "text": user_text,
                    "final": True,
                    "turn_id": turn_id,
                },
            )

            # 1b. Pronunciation assessment (best-effort; never blocks a turn)
            if self._pronunciation_enabled and self._pronunciation_user_enabled:
                try:
                    assess_task = asyncio.create_task(
                        self.pronunciation.assess(
                            audio_bytes,
                            self._target_language,
                            reference_text=user_text,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — a broken provider must never abort a turn
                    logger.warning(
                        "[pipeline] Failed to start pronunciation assessment: %s", exc
                    )
                    assess_task = None
            if assess_task is not None:
                try:
                    pronunciation_result = await asyncio.wait_for(
                        # shield: wait_for cancels its awaitable on timeout,
                        # which would kill the very task the late-delivery
                        # waiter needs to finish.
                        asyncio.shield(assess_task),
                        timeout=PRONUNCIATION_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # Fork: the SDK takes roughly the utterance's length to
                    # score it, so missing this window is normal — hand the
                    # task to a waiter that delivers the chip when it lands.
                    logger.info(
                        "[pipeline] Pronunciation still running — delivering late"
                    )
                    assess_handed_off = True
                    self._pending_saves.append(
                        asyncio.create_task(
                            self._send_late_pronunciation(ws, turn_id, assess_task)
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — best-effort by design
                    logger.warning("[pipeline] Pronunciation assessment failed: %s", exc)
                if pronunciation_result is not None:
                    payload = pronunciation_result.to_payload()
                    payload["type"] = "pronunciation"
                    payload["turn_id"] = turn_id
                    await self._send_json(ws, payload)
        finally:
            # Barge-in (`handle_audio`) cancels this turn's task on every new
            # audio chunk, and that cancellation can land at any await above
            # (status send, STT, transcript send). The assessment task is not
            # tracked in `_timer_tasks` or `_pending_saves`, so without this
            # it would leak: a live provider HTTP call outliving the turn.
            if assess_task is not None and not assess_handed_off:
                if not assess_task.done():
                    assess_task.cancel()
                elif not assess_task.cancelled():
                    # Already finished (result or exception) by the time
                    # cancellation landed here — retrieve it synchronously
                    # (no await, so this can't itself swallow a fresh outer
                    # cancellation) so a failed provider never surfaces as
                    # "Task exception was never retrieved".
                    assess_task.exception()

        # 2. Streaming LLM
        self.history.append({"role": "user", "content": user_text})
        # NOTE: user message is intentionally saved *after* a successful turn
        # (alongside the assistant reply) so no orphan rows are written on
        # LLM failures or barge-in cancellations.
        await self._refresh_memory_prompt()
        messages = [{"role": "system", "content": self.system_prompt}] + self.history[-20:]
        annotation = format_pronunciation_annotation(pronunciation_result)
        if annotation and messages:
            messages = messages[:-1] + [
                {**messages[-1], "content": f"{messages[-1]['content']}\n{annotation}"}
            ]

        full_response = ""
        clean_full_response = ""
        memory_updated = False
        # Fork (guided lessons): collected during the stream, sent after the audio.
        lesson_advanced = False
        lesson_completed = False
        llm_stream = None
        try:
            await self._send_status(ws, turn_id, "thinking")

            llm_t0 = time.perf_counter()

            async def execute_memory_tool(call):
                if self._user_id is None:
                    raise RuntimeError("Memory tool requires an authenticated user")
                async with db_session() as db_mem:
                    return await execute_save_user_memory(
                        db_mem,
                        self._user_id,
                        call,
                        "voice",
                        study_plan_id=self._study_plan_id,
                    )

            async def execute_tool(call):
                # The adapter takes a single executor and runs at most one call
                # per turn, so tools are dispatched by name here.
                if call.name == LESSON_STEP_RESULT_TOOL_NAME:
                    if self.lesson_session is None:
                        return LLMToolResult(
                            call=call,
                            content={"advanced": False, "error": "unknown_tool"},
                            is_error=True,
                        )
                    completed_step = self.lesson_session.current
                    lesson_result = execute_lesson_step_result(self.lesson_session, call)
                    if lesson_result.content.get("advanced") is True:
                        self._record_lesson_advance(turn_id)
                        await self._persist_lesson_progress()
                        # Fork: write the learner's spoken answer back to the
                        # lesson-page exercise row, so both views stay in sync.
                        if completed_step is not None and completed_step.exercise_id:
                            self._pending_saves.append(
                                asyncio.create_task(
                                    self._save_exercise_answer(
                                        completed_step.exercise_id,
                                        call.arguments.get("student_answer"),
                                        call.arguments.get("result"),
                                        call.arguments.get("note"),
                                    )
                                )
                            )
                    return lesson_result
                if call.name == SAVE_USER_MEMORY_TOOL_NAME:
                    return await execute_memory_tool(call)
                return LLMToolResult(
                    call=call,
                    content={"error": "unknown_tool"},
                    is_error=True,
                )

            memory_kwargs = {}
            turn_tools = []
            if self._memory_tools_available:
                turn_tools.append(
                    build_save_user_memory_tool(str(self._prompt_args["native_language"]))
                )
            # The lesson tool is subject to the same `tools_unsupported` latch as
            # the memory tool: once a provider has rejected tools, offering one
            # anyway costs a rejected call plus a fallback re-issue every turn.
            if self.lesson_session is not None and self._memory_tools_available:
                turn_tools.append(build_lesson_step_tool())
            if turn_tools:
                self._sync_lesson_overlay()
                fallback_messages = [
                    {
                        "role": "system",
                        "content": _build_conversation_system_prompt(
                            **self._prompt_args,
                            memory_context=self._memory_context,
                            memory_tools_enabled=False,
                        ),
                    }
                ] + self.history[-20:]
                if annotation and fallback_messages:
                    fallback_messages = fallback_messages[:-1] + [
                        {
                            **fallback_messages[-1],
                            "content": f"{fallback_messages[-1]['content']}\n{annotation}",
                        }
                    ]
                memory_kwargs = {
                    "tools": turn_tools,
                    "tool_executor": execute_tool,
                    "fallback_messages": fallback_messages,
                }
            llm_stream = await self.llm.chat(messages, stream=True, **memory_kwargs)
            try:
                async for chunk in llm_stream:
                    if isinstance(chunk, LLMToolResultEvent):
                        memory_updated = memory_updated or chunk.result.content.get("saved") is True
                        if chunk.result.call.name == LESSON_STEP_RESULT_TOOL_NAME:
                            lesson_advanced = (
                                lesson_advanced or chunk.result.content.get("advanced") is True
                            )
                            lesson_completed = (
                                lesson_completed
                                or chunk.result.content.get("lesson_complete") is True
                            )
                            # Fork: the post-tool continuation is appended to
                            # the same reply — without a seam the transcript
                            # glues the halves together ("¿cómo te llamas?Ahora").
                            if full_response and not full_response[-1].isspace():
                                full_response += " "
                        continue
                    if isinstance(chunk, LLMStreamReset):
                        full_response = ""
                        continue
                    token = self._stream_text(chunk)
                    full_response += token
            finally:
                memory_updated = memory_updated or any(
                    result.content.get("saved") is True
                    for result in getattr(llm_stream, "tool_results", [])
                )
                # Fork (guided lessons): same sweep for a stream that exposes
                # its tool results without ever yielding the event (e.g. when
                # the continuation was cut short).
                for result in getattr(llm_stream, "tool_results", []):
                    if result.call.name != LESSON_STEP_RESULT_TOOL_NAME:
                        continue
                    lesson_advanced = lesson_advanced or result.content.get("advanced") is True
                    lesson_completed = (
                        lesson_completed or result.content.get("lesson_complete") is True
                    )
                if memory_updated:
                    await self._send_memory_updated(ws, turn_id)
            llm_ms = (time.perf_counter() - llm_t0) * 1000

            clean_full_response = self._extract_speech_text(full_response)
            if clean_full_response:
                tts_send_t0 = time.perf_counter()
                (
                    tts_chunks_sent,
                    tts_bytes,
                    assistant_transcript_sent,
                    send_aborted,
                ) = await self._synthesize_and_send_response(
                    ws,
                    text=clean_full_response,
                    turn_id=turn_id,
                    transcript_payload={
                        "type": "transcript",
                        "role": "assistant",
                        "text": clean_full_response,
                        "final": True,
                        "turn_id": turn_id,
                    },
                )
                tts_send_ms = (time.perf_counter() - tts_send_t0) * 1000
                if send_aborted or not assistant_transcript_sent:
                    logger.warning(
                        "[pipeline] No assistant audio was sent and turn is being aborted: turn_id=%s",
                        turn_id,
                    )
                    self.history.pop()
                    await self._send_json(
                        ws,
                        {
                            "type": "error",
                            "code": "tts_failed",
                            "message": "Failed to synthesize assistant audio.",
                            "turn_id": turn_id,
                        },
                    )
                    if lesson_advanced:
                        # The step advanced server-side before TTS failed, so the
                        # panel must not be left on a step already recorded done.
                        await self._send_lesson_state(ws, turn_id)
                        if lesson_completed:
                            await self._send_lesson_completed(ws, turn_id)
                    return
            else:
                logger.warning(
                    "[pipeline] Skipping TTS synthesis because assistant text is empty after cleaning"
                )
                self.history.pop()
                await self._send_status(ws, turn_id, "listening")
                await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})
                if lesson_advanced:
                    # The step still advanced server-side, so the client's panel
                    # must not be left showing a step the tutor has moved past.
                    await self._send_lesson_state(ws, turn_id)
                    if lesson_completed:
                        await self._send_lesson_completed(ws, turn_id)
                return

        except asyncio.CancelledError:
            logger.warning(
                "[pipeline] Turn cancelled before completion: turn_id=%s",
                turn_id,
            )
            raise
        except (LLMTimeoutError, LLMUnavailableError, LLMError) as exc:
            logger.error("[pipeline] LLM failed: %s", exc)
            logger.info(
                "pipeline_turn_metrics",
                stage="llm_failed",
                stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
                llm_ms=round(llm_ms, 1) if llm_ms is not None else None,
                tts_send_ms=round(tts_send_ms, 1) if tts_send_ms is not None else None,
                tts_chunks_sent=tts_chunks_sent,
                tts_audio_bytes=tts_bytes,
                turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
            )
            await self._send_json(
                ws,
                {
                    "type": "error",
                    "code": "llm_failed",
                    "message": str(exc),
                    "turn_id": turn_id,
                },
            )
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()
            return
        except Exception as exc:
            logger.error("[pipeline] TTS failed: %s", exc)
            logger.info(
                "pipeline_turn_metrics",
                stage="tts_failed",
                stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
                llm_ms=round(llm_ms, 1) if llm_ms is not None else None,
                tts_send_ms=round(tts_send_ms, 1) if tts_send_ms is not None else None,
                tts_chunks_sent=tts_chunks_sent,
                tts_audio_bytes=tts_bytes,
                turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
            )
            await self._send_json(
                ws,
                {
                    "type": "error",
                    "code": "tts_failed",
                    "message": str(exc),
                    "turn_id": turn_id,
                },
            )
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()
            return
        finally:
            if llm_stream is not None:
                if getattr(llm_stream, "tools_unsupported", False):
                    self._memory_tools_available = False
                self._pending_saves.append(asyncio.create_task(self._save_usage(llm_stream)))

        self.history.append({"role": "assistant", "content": clean_full_response})
        # Persist both sides of the turn together — only reached on success.
        self._pending_saves.append(asyncio.create_task(self._save_message("user", user_text)))
        self._pending_saves.append(
            asyncio.create_task(self._save_message("assistant", clean_full_response))
        )

        logger.info("[pipeline] Turn complete — assistant: %r", clean_full_response[:120])
        logger.info(
            "pipeline_turn_metrics",
            stage="ok",
            stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
            llm_ms=round(llm_ms, 1) if llm_ms is not None else None,
            tts_send_ms=round(tts_send_ms, 1) if tts_send_ms is not None else None,
            tts_chunks_sent=tts_chunks_sent,
            tts_audio_bytes=tts_bytes,
            turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
        )
        await self._send_status(ws, turn_id, "listening")

        await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})

        # Fork (guided lessons): announced only after the turn's audio has gone
        # out, so the panel never jumps ahead of what the student has heard.
        if lesson_advanced:
            await self._send_lesson_state(ws, turn_id)
            if lesson_completed:
                await self._send_lesson_completed(ws, turn_id)

    async def _save_usage(self, stream: object) -> None:
        """Persists token usage from an LLMStream to the DB.

        Completely defensive — silently ignores any error, including:
        - stream not being an LLMStream instance
        - provider not returning usage (all fields None)
        - DB connectivity issues
        """
        if self._user_id is None:
            return
        try:
            if not isinstance(stream, LLMStream):
                return
            if stream.prompt_tokens is None and stream.completion_tokens is None:
                return
            # Lazy import to avoid circular imports
            from app.models.llm_usage import LLMUsage  # noqa: PLC0415

            async with db_session() as db:
                db.add(
                    LLMUsage(
                        user_id=self._user_id,
                        source="conversation",
                        prompt_tokens=stream.prompt_tokens,
                        completion_tokens=stream.completion_tokens,
                        total_tokens=stream.total_tokens,
                        study_plan_id=self._study_plan_id,
                    )
                )
                await db.commit()
        except Exception:
            logger.debug("[pipeline] Failed to save token usage — ignored")

    async def _save_exercise_answer(
        self,
        exercise_id: int,
        student_answer: object,
        result: object,
        note: object,
    ) -> None:
        """Fork: record a voice-lesson exercise outcome on its Exercise row.

        Same contract as the other background saves: completely defensive,
        never blocks or breaks the voice pipeline.
        """
        try:
            from sqlalchemy import update  # noqa: PLC0415

            from app.models.lesson import Exercise  # noqa: PLC0415

            answer = student_answer.strip() if isinstance(student_answer, str) else ""
            passed = result == "passed"
            feedback = note.strip() if isinstance(note, str) else None
            async with db_session() as db:
                await db.execute(
                    update(Exercise)
                    .where(Exercise.id == exercise_id)
                    .values(
                        user_answer=answer or ("(voice)" if passed else ""),
                        score=100.0 if passed else 0.0,
                        feedback=feedback,
                        answered_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
                await db.commit()
        except Exception:
            logger.debug("[pipeline] Failed to save exercise answer — ignored")

    async def _save_message(self, role: str, content: str) -> None:
        """Persists a conversation transcript message to chat_history.

        Completely defensive — silently ignores any error.
        The save is sent to the background via asyncio.create_task so it never
        blocks the voice pipeline.
        """
        if self._user_id is None or self._conversation_id is None:
            return
        try:
            from sqlalchemy import update  # noqa: PLC0415

            from app.models.chat_history import ChatHistory  # noqa: PLC0415
            from app.models.conversation import Conversation  # noqa: PLC0415

            async with db_session() as db:
                db.add(
                    ChatHistory(
                        user_id=self._user_id,
                        conversation_id=self._conversation_id,
                        role=role,
                        content=content,
                        study_plan_id=self._study_plan_id,
                        target_language=self._target_language,
                    )
                )
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == self._conversation_id)
                    .values(updated_at=datetime.now(UTC).replace(tzinfo=None))
                )
                await db.commit()
        except Exception:
            logger.debug("[pipeline] Failed to save message — ignored")

    async def cancel_current(self) -> None:
        if self.current_task and not self.current_task.done():
            logger.debug(
                "[pipeline] cancel_current: cancelling task state=%s",
                self._task_state(self.current_task),
            )
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("[pipeline] cancel_current await failed: %s", self._fmt_exc(exc))
        if self.history and self.history[-1]["role"] == "user":
            self.history.pop()

    async def cleanup(self) -> None:
        # Record actual session duration once, best-effort
        if not self._recorded:
            self._recorded = True
            elapsed = int(time.monotonic() - self._session_start)
            if self._redis is not None and self._user_id is not None and elapsed > 0:
                try:
                    await record_session_seconds(self._redis, self._user_id, elapsed)
                except Exception:
                    logger.debug("[pipeline] Failed to record session seconds — ignored")
                if self._freemium_voice:
                    try:
                        from app.services.freemium_service import record_voice_usage

                        await record_voice_usage(self._redis, self._user_id, elapsed)
                    except Exception:
                        logger.debug("[pipeline] Failed to record freemium voice seconds — ignored")
        await self.cancel_current()
        for t in self._timer_tasks:
            t.cancel()
        # Wait for any in-flight DB saves (transcripts, token usage) so they
        # are not silently cancelled when the event loop shuts down the task.
        if self._pending_saves:
            await asyncio.gather(*self._pending_saves, return_exceptions=True)
        logger.debug("[pipeline] Cleanup complete")

    # --- Timeout watchers ---

    async def _max_duration_watcher(self, ws: WebSocket) -> None:
        """Closes session after max_duration seconds, with a 60s warning."""
        warn_at = self.max_duration - WARNING_ADVANCE_SECONDS
        if warn_at > 0:
            await asyncio.sleep(warn_at)
            logger.info(
                "[pipeline] Max duration warning — %ss remaining",
                WARNING_ADVANCE_SECONDS,
            )
            await self._send_json(
                ws,
                {
                    "type": "session_warning",
                    "reason": "max_duration",
                    "remaining_seconds": WARNING_ADVANCE_SECONDS,
                },
            )
            await asyncio.sleep(WARNING_ADVANCE_SECONDS)
        else:
            await asyncio.sleep(self.max_duration)
        logger.info("[pipeline] Session ended by max_duration")
        await self._send_json(ws, {"type": "session_end", "reason": "max_duration"})
        await self._close_ws(ws, code=1000)

    async def _inactivity_watcher(self, ws: WebSocket) -> None:
        """Closes session if user is silent for inactivity_timeout seconds."""
        while True:
            await asyncio.sleep(5)  # check every 5 seconds
            elapsed = time.monotonic() - self._last_activity
            remaining = self.inactivity_timeout - elapsed

            if 0 < remaining <= WARNING_ADVANCE_SECONDS and not self._inactivity_warning_sent:
                self._inactivity_warning_sent = True
                logger.info("[pipeline] Inactivity warning — %ds remaining", int(remaining))
                await self._send_json(
                    ws,
                    {
                        "type": "session_warning",
                        "reason": "inactivity",
                        "remaining_seconds": int(remaining),
                    },
                )

            if elapsed >= self.inactivity_timeout:
                logger.info("[pipeline] Session ended by inactivity (elapsed %.0fs)", elapsed)
                await self._send_json(ws, {"type": "session_end", "reason": "inactivity"})
                await self._close_ws(ws, code=1000)
                return
