"""lesson_id in the voice WS handshake (fork: guided lesson mode).

Required cases:
  1. test_handshake_without_lesson_id_starts_a_normal_session
     -> pipeline constructed with lesson_session=None
  2. test_handshake_with_owned_lesson_id_builds_a_lesson_session
     -> pipeline receives a LessonSession whose lesson_id matches and whose
        steps are non-empty
  3. test_handshake_with_unowned_lesson_id_is_rejected
     -> a lesson belonging to another user yields {"type":"error","code":"lesson_not_found"}
        and the socket closes with 1008
  4. test_handshake_with_unknown_lesson_id_is_rejected
     -> same for an id that does not exist
  5. test_handshake_with_non_integer_lesson_id_is_rejected
     -> "abc" and 0 both yield lesson_not_found
  6. test_lesson_with_empty_content_still_starts
     -> content {} yields a session with exactly the intro and wrap_up steps

Driving the real /ws/conversation route (rather than unit-testing the parsing
logic in isolation) is deliberate: ownership must be proven by an actual DB
join, and the reject paths must actually send-then-close over the socket, so
the same route the frontend hits is what's exercised here.

Two bits of test-only plumbing make that possible:

- httpx's plain ASGITransport does not speak the WebSocket ASGI protocol at
  all (a request to /ws/conversation 404s under it), so
  httpx_ws.transport.ASGIWebSocketTransport is used instead.
- conversation_ws() reaches the DB and Redis through the app's own
  module-level engine/connection (app.utils.db.db_session /
  app.utils.redis.redis_client), which are independent of this test file's
  sqlite fixture engine and of any real Redis. _patched_router below points
  both at test doubles so the route can actually run end-to-end here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.lesson import Lesson
from app.models.study_plan import StudyPlan
from app.models.user import User
from app.models.user_language import UserLanguage
from app.routers import conversation as conversation_router

# ---------------------------------------------------------------------------
# Test doubles for the router's DB / Redis access
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Enough surface for the quota `.get()` calls made before pipeline
    construction. Mirrors conftest's mock_redis, kept local since that fixture
    is wired to FastAPI dependency overrides, not to conversation.py's direct
    `_redis_client()` context-manager calls."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


@asynccontextmanager
async def _fake_redis_client():
    yield _FakeRedis()


@pytest.fixture
def patched_router(monkeypatch, test_engine):
    """Redirect conversation_ws()'s internal db_session()/_redis_client() calls
    to this test's sqlite engine and an in-memory fake Redis, and replace
    ConversationPipeline with a capturing mock so no real LLM/TTS/STT call is
    made. Returns the mock class so tests can inspect construction kwargs.
    """
    test_sessionmaker = async_sessionmaker(test_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _fake_db_session():
        async with test_sessionmaker() as session:
            yield session

    monkeypatch.setattr(conversation_router, "db_session", _fake_db_session)
    monkeypatch.setattr(conversation_router, "_redis_client", _fake_redis_client)
    # quota_service.check_all_quotas() re-imports `db_session` from
    # app.utils.db lazily (inside the function body) rather than using
    # conversation_router's module-level import, so the source needs
    # patching too for the monthly-token quota check to see the test DB.
    import app.utils.db as _utils_db

    monkeypatch.setattr(_utils_db, "db_session", _fake_db_session)

    fake_pipeline = MagicMock()
    fake_pipeline.run = AsyncMock()
    fake_pipeline.cleanup = AsyncMock()
    fake_pipeline_cls = MagicMock(return_value=fake_pipeline)
    monkeypatch.setattr(conversation_router, "ConversationPipeline", fake_pipeline_cls)

    app.state.tts_service = MagicMock()
    app.state.stt_service = MagicMock()

    yield fake_pipeline_cls

    app.state.tts_service = None
    app.state.stt_service = None


# ---------------------------------------------------------------------------
# Fixture builders (modelled on tests/test_conversation.py and conftest.py's
# make_study_plan helper)
# ---------------------------------------------------------------------------


async def _create_user_and_token(db_session, *, username: str, email: str) -> tuple[User, str]:
    user = User(
        username=username,
        email=email,
        display_name="Lesson Learner",
        hashed_password=hash_password("lessonpass"),
        role="user",
        native_language="es",
        target_language="en-US",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserLanguage(user_id=user.id, target_language="en-US", is_active=True))
    await db_session.commit()
    await db_session.refresh(user)
    token = create_access_token(user.id, user.role)
    return user, token


async def _make_plan_and_lesson(db_session, user: User, *, content: dict | None = None) -> tuple[StudyPlan, Lesson]:
    ul = (
        await db_session.execute(
            select(UserLanguage).where(
                UserLanguage.user_id == user.id,
                UserLanguage.target_language == "en-US",
            )
        )
    ).scalar_one()
    plan = StudyPlan(
        user_id=user.id,
        user_language_id=ul.id,
        cefr_level="B1",
        target_language="en-US",
        goals=[],
        duration_weeks=4,
        days_per_week=4,
        current_unit="",
        generated_plan={},
        is_active=True,
    )
    db_session.add(plan)
    await db_session.flush()
    lesson = Lesson(
        study_plan_id=plan.id,
        title="Greetings",
        lesson_type="grammar",
        cefr_level="B1",
        week_number=1,
        day_number=1,
        content=(
            content
            if content is not None
            else {
                "explanation": {"text": "How to greet people."},
                "vocabulary": [{"word": "hello", "definition": "a greeting"}],
            }
        ),
    )
    db_session.add(lesson)
    await db_session.commit()
    await db_session.refresh(lesson)
    return plan, lesson


async def _drive_handshake(
    token: str, extra_auth_fields: dict | None = None
) -> tuple[list[dict], int | None]:
    """Connect, send the auth frame (plus any lesson fields), and collect JSON
    frames sent back, along with the server's WebSocket close code (None if
    the connection never actually closed within the bounded wait below).

    Rejection paths always send-then-close, so the server disconnects right
    after the error frame — this is where a real close code (1008) is
    observed. Accepted sessions send nothing at the handshake stage and the
    mocked pipeline.run()/cleanup() return immediately without the router
    itself closing the socket (that's left to the real pipeline's receive
    loop, which isn't exercised here) — so a short bounded wait is used to
    drain any frames without hanging forever. Either way, awaiting the outer
    client/transport context managers' exit (which joins the server-side
    task) is what actually guarantees the router has finished running by the
    time callers inspect the patched ConversationPipeline mock.
    """
    auth_msg = {"type": "auth", "token": token}
    if extra_auth_fields:
        auth_msg.update(extra_auth_fields)

    received: list[dict] = []
    close_code: int | None = None
    transport = ASGIWebSocketTransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with aconnect_ws("/ws/conversation", ac) as ws:
            await ws.send_json(auth_msg)
            try:
                while True:
                    received.append(await ws.receive_json(timeout=2.0))
            except WebSocketDisconnect as exc:
                close_code = exc.code
            except TimeoutError:
                pass
    return received, close_code


# ---------------------------------------------------------------------------
# 1. No lesson_id -> normal session, lesson_session=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_without_lesson_id_starts_a_normal_session(db_session, patched_router) -> None:
    """Absent lesson_id must behave exactly as before: pipeline gets lesson_session=None."""
    _, token = await _create_user_and_token(db_session, username="nolesson", email="nolesson@example.com")

    await _drive_handshake(token)

    patched_router.assert_called_once()
    assert patched_router.call_args.kwargs["lesson_session"] is None


# ---------------------------------------------------------------------------
# 2. Owned lesson_id -> LessonSession built and passed through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_with_owned_lesson_id_builds_a_lesson_session(db_session, patched_router) -> None:
    """An owned lesson_id resolves to a LessonSession with matching id and steps."""
    user, token = await _create_user_and_token(db_session, username="ownedlesson", email="owned@example.com")
    _plan, lesson = await _make_plan_and_lesson(db_session, user)

    await _drive_handshake(token, {"lesson_id": lesson.id})

    patched_router.assert_called_once()
    session = patched_router.call_args.kwargs["lesson_session"]
    assert session is not None
    assert session.lesson_id == lesson.id
    assert len(session.steps) > 0


def _assert_rejected_as_lesson_not_found(frames: list[dict], close_code: int | None) -> None:
    """Shared assertion for every reject path: a single error frame naming
    lesson_not_found, followed by an actual 1008 close — not just an error
    frame with the connection left dangling or closed some other way."""
    assert frames, "expected an error frame before the socket closed"
    assert frames[-1]["type"] == "error"
    assert frames[-1]["code"] == "lesson_not_found"
    assert close_code == 1008, f"expected WS close code 1008, got {close_code!r}"


# ---------------------------------------------------------------------------
# 3. Lesson belongs to another user -> rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_with_unowned_lesson_id_is_rejected(db_session, patched_router) -> None:
    """A lesson owned by a different user must not be reachable by id guessing."""
    owner, _owner_token = await _create_user_and_token(
        db_session, username="lessonowner", email="owner@example.com"
    )
    _plan, lesson = await _make_plan_and_lesson(db_session, owner)

    attacker, attacker_token = await _create_user_and_token(
        db_session, username="lessonattacker", email="attacker@example.com"
    )

    frames, close_code = await _drive_handshake(attacker_token, {"lesson_id": lesson.id})

    _assert_rejected_as_lesson_not_found(frames, close_code)
    patched_router.assert_not_called()


@pytest.mark.asyncio
async def test_handshake_with_unowned_lesson_id_is_rejected_even_when_attacker_owns_other_lessons(
    db_session, patched_router
) -> None:
    """An attacker who legitimately owns lessons of their own must still be
    rejected when requesting someone else's lesson id — the join must key off
    the requested lesson's own study plan, not merely "does this user own
    *some* lesson". A mutation that checked "any lesson owned by user" instead
    of "this exact lesson owned by user" would slip through test 3 above (no
    lessons at all for the attacker) but must be caught here.
    """
    owner, _owner_token = await _create_user_and_token(
        db_session, username="lessonowner2", email="owner2@example.com"
    )
    _owner_plan, owner_lesson = await _make_plan_and_lesson(db_session, owner)

    attacker, attacker_token = await _create_user_and_token(
        db_session, username="lessonattacker2", email="attacker2@example.com"
    )
    await _make_plan_and_lesson(db_session, attacker)  # attacker owns a real lesson of their own

    frames, close_code = await _drive_handshake(attacker_token, {"lesson_id": owner_lesson.id})

    _assert_rejected_as_lesson_not_found(frames, close_code)
    patched_router.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Unknown lesson_id -> rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_with_unknown_lesson_id_is_rejected(db_session, patched_router) -> None:
    """An id that matches no lesson at all must be rejected the same way."""
    _, token = await _create_user_and_token(db_session, username="unknownlesson", email="unknown@example.com")

    frames, close_code = await _drive_handshake(token, {"lesson_id": 999999})

    _assert_rejected_as_lesson_not_found(frames, close_code)
    patched_router.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Non-integer / non-positive / out-of-range lesson_id -> rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_with_non_integer_lesson_id_is_rejected(db_session, patched_router) -> None:
    """Both a non-numeric string and 0 must be rejected as lesson_not_found."""
    _, token_a = await _create_user_and_token(db_session, username="badlesson1", email="bad1@example.com")
    frames_a, close_code_a = await _drive_handshake(token_a, {"lesson_id": "abc"})
    _assert_rejected_as_lesson_not_found(frames_a, close_code_a)

    _, token_b = await _create_user_and_token(db_session, username="badlesson2", email="bad2@example.com")
    frames_b, close_code_b = await _drive_handshake(token_b, {"lesson_id": 0})
    _assert_rejected_as_lesson_not_found(frames_b, close_code_b)

    patched_router.assert_not_called()


@pytest.mark.asyncio
async def test_handshake_with_infinite_lesson_id_is_rejected(db_session, patched_router) -> None:
    """`lesson_id: Infinity` — valid JSON (json.loads accepts it), but
    int(float('inf')) raises OverflowError. Must be rejected cleanly rather
    than crashing the handler with an unhandled exception."""
    _, token = await _create_user_and_token(db_session, username="infinitelesson", email="infinite@example.com")

    frames, close_code = await _drive_handshake(token, {"lesson_id": float("inf")})

    _assert_rejected_as_lesson_not_found(frames, close_code)
    patched_router.assert_not_called()


@pytest.mark.asyncio
async def test_handshake_with_huge_lesson_id_is_rejected(db_session, patched_router) -> None:
    """An integer far outside any DB integer column's native width (e.g. well
    past 64-bit signed range) must be rejected cleanly rather than crashing
    the handler when the driver binds it."""
    _, token = await _create_user_and_token(db_session, username="hugelesson", email="huge@example.com")

    frames, close_code = await _drive_handshake(token, {"lesson_id": 2**63})

    _assert_rejected_as_lesson_not_found(frames, close_code)
    patched_router.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Empty lesson content -> still starts, intro + wrap_up only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lesson_with_empty_content_still_starts(db_session, patched_router) -> None:
    """A lesson whose content is {} still yields a usable session: just intro + wrap_up."""
    user, token = await _create_user_and_token(db_session, username="emptylesson", email="empty@example.com")
    _plan, lesson = await _make_plan_and_lesson(db_session, user, content={})

    await _drive_handshake(token, {"lesson_id": lesson.id})

    patched_router.assert_called_once()
    session = patched_router.call_args.kwargs["lesson_session"]
    assert session is not None
    assert session.lesson_id == lesson.id
    assert [step.kind for step in session.steps] == ["intro", "wrap_up"]
