"""Fork: export flashcards or a lesson's vocabulary as an Anki .apkg deck."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.flashcard import Flashcard
from app.models.study_plan import StudyPlan
from app.models.user import User
from app.models.user_language import UserLanguage
from app.services.anki_export import (
    CARD_TYPES,
    AnkiCard,
    AnkiExportOptions,
    build_apkg,
    enrich_cards,
)
from app.services.language_helpers import get_language_name

router = APIRouter(prefix="/api/anki", tags=["anki"])


class AnkiExportRequest(BaseModel):
    deck_name: str = Field(default="", max_length=100)
    card_type: str = "basic"
    include_definition: bool = True
    include_example: bool = True
    enrich: bool = False


def _validated_options(payload: AnkiExportRequest, fallback_name: str) -> AnkiExportOptions:
    if payload.card_type not in CARD_TYPES:
        raise HTTPException(status_code=422, detail="invalid_card_type")
    name = payload.deck_name.strip() or fallback_name
    return AnkiExportOptions(
        deck_name=name,
        card_type=payload.card_type,
        include_definition=payload.include_definition,
        include_example=payload.include_example,
    )


def _apkg_response(data: bytes, deck_name: str) -> Response:
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in deck_name).strip()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe or "freelingo"}.apkg"'
        },
    )


@router.post("/flashcards")
@limiter.limit("10/minute")
async def export_saved_flashcards(
    request: Request,
    payload: AnkiExportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export the learner's saved flashcards for the active language."""
    from app.services.user_language_service import get_active_language

    active_lang = await get_active_language(db, current_user.id)
    if not active_lang:
        raise HTTPException(status_code=404, detail="No active language set")
    plan_result = await db.execute(
        select(StudyPlan).where(
            StudyPlan.user_language_id == active_lang.id,
            StudyPlan.is_active.is_(True),
        )
    )
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="No active study plan found")

    result = await db.execute(
        select(Flashcard).where(
            Flashcard.user_id == current_user.id,
            Flashcard.study_plan_id == plan.id,
        )
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="no_flashcards")

    language_name = get_language_name(plan.target_language)
    options = _validated_options(payload, f"FreeLingo {language_name}")
    cards = [
        AnkiCard(
            word=row.word,
            translation=row.translation,
            definition=row.definition,
            example_sentence=row.example_sentence,
        )
        for row in rows
    ]
    if payload.enrich:
        cards = await enrich_cards(cards, language_name)
    return _apkg_response(build_apkg(cards, options), options.deck_name)


@router.post("/lessons/{lesson_id}")
@limiter.limit("10/minute")
async def export_lesson_vocabulary(
    request: Request,
    lesson_id: int,
    payload: AnkiExportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export one lesson's vocabulary list as a deck."""
    from app.models.lesson import Lesson

    result = await db.execute(
        select(Lesson)
        .join(StudyPlan, Lesson.study_plan_id == StudyPlan.id)
        .join(UserLanguage, StudyPlan.user_language_id == UserLanguage.id)
        .where(Lesson.id == lesson_id, UserLanguage.user_id == current_user.id)
    )
    lesson = result.scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    content = lesson.content if isinstance(lesson.content, dict) else {}
    vocabulary = content.get("vocabulary")
    cards: list[AnkiCard] = []
    if isinstance(vocabulary, list):
        for item in vocabulary:
            if not isinstance(item, dict):
                continue
            word = str(item.get("word") or "").strip()
            if not word:
                continue
            cards.append(
                AnkiCard(
                    word=word,
                    translation=str(
                        item.get("translation") or item.get("definition") or ""
                    ).strip(),
                    definition=str(item.get("definition") or "").strip(),
                    example_sentence=str(item.get("example") or "").strip(),
                )
            )
    if not cards:
        raise HTTPException(status_code=404, detail="no_vocabulary")

    plan_result = await db.execute(
        select(StudyPlan).where(StudyPlan.id == lesson.study_plan_id)
    )
    plan = plan_result.scalar_one_or_none()
    language_name = get_language_name(plan.target_language) if plan else ""
    options = _validated_options(payload, lesson.title)
    if payload.enrich:
        cards = await enrich_cards(cards, language_name or "the target language")
    return _apkg_response(build_apkg(cards, options), options.deck_name)
