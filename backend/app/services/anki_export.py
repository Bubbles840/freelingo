"""Fork: build Anki .apkg decks from flashcard-shaped data.

Pure builder: a list of card dicts plus options in, .apkg bytes out.
IDs and note GUIDs are deterministic so re-importing an updated deck
UPDATES existing notes in Anki instead of duplicating them.
"""

from __future__ import annotations

import hashlib
import html
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import genanki
from pydantic import BaseModel

from app.core.app_logger import get_logger

logger = get_logger(__name__)

# Fixed, randomly-generated once — stable across exports so Anki recognises
# the models. Never change these.
_BASIC_MODEL_ID = 1607392325
_REVERSED_MODEL_ID = 1607392326
_CLOZE_MODEL_ID = 1607392321

CARD_TYPES = ("basic", "basic_reversed", "cloze")

_CARD_CSS = """.card {
 font-family: -apple-system, "Helvetica Neue", sans-serif;
 font-size: 22px;
 text-align: center;
 color: #1a1a1a;
 background-color: #fdfdf8;
}
.word { font-size: 30px; font-weight: 700; }
.example { margin-top: 14px; font-style: italic; color: #555; }
.definition { margin-top: 10px; color: #333; }
.extra { margin-top: 10px; color: #333; }
.cloze { font-weight: 700; color: #0b6bcb; }
/* Anki night mode — without these the muted grays vanish on dark bg */
.card.night_mode, .card.nightMode {
 color: #ececec;
 background-color: #2c2c2c;
}
.night_mode .example, .nightMode .example { color: #b8b8b8; }
.night_mode .definition, .nightMode .definition { color: #d6d6d6; }
.night_mode .extra, .nightMode .extra { color: #d6d6d6; }
.night_mode .cloze, .nightMode .cloze { color: #6cb2ff; }
"""

@dataclass(frozen=True)
class AnkiCard:
    word: str
    translation: str
    definition: str = ""
    example_sentence: str = ""
    extra: str = ""  # optional AI enrichment (conjugations, usage notes, …)


@dataclass(frozen=True)
class AnkiExportOptions:
    deck_name: str
    card_type: str = "basic"  # basic | basic_reversed | cloze
    include_definition: bool = True
    include_example: bool = True


def _extra_html(card: AnkiCard) -> str:
    return f'<div class="extra">{html.escape(card.extra)}</div>' if card.extra else ""


def _basic_model(reversed_too: bool) -> genanki.Model:
    """Four separate fields so each side shows only what it should.

    The old two-field (Front/Back) design leaked answers on reversed cards:
    the back carried the example sentence, which contains the target word.
    """
    recognition_answer = (
        "{{FrontSide}}<hr id=answer>"
        '<div class="word">{{Translation}}</div>'
        '{{#Definition}}<div class="definition">{{Definition}}</div>{{/Definition}}'
        '{{#Example}}<div class="example">{{Example}}</div>{{/Example}}'
        '{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}'
    )
    templates = [
        {
            "name": "Recognition",
            "qfmt": '<div class="word">{{Word}}</div>',
            "afmt": recognition_answer,
        }
    ]
    if reversed_too:
        templates.append(
            {
                "name": "Recall",
                # Translation ONLY on the question side — definition and
                # example would give the answer away.
                "qfmt": '<div class="word">{{Translation}}</div>',
                "afmt": (
                    "{{FrontSide}}<hr id=answer>"
                    '<div class="word">{{Word}}</div>'
                    '{{#Example}}<div class="example">{{Example}}</div>{{/Example}}'
                    '{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}'
                ),
            }
        )
    return genanki.Model(
        _REVERSED_MODEL_ID if reversed_too else _BASIC_MODEL_ID,
        "FreeLingo Basic (reversed)" if reversed_too else "FreeLingo Basic",
        fields=[
            {"name": "Word"},
            {"name": "Translation"},
            {"name": "Definition"},
            {"name": "Example"},
            {"name": "Extra"},
        ],
        templates=templates,
        css=_CARD_CSS,
    )


def _cloze_model() -> genanki.Model:
    return genanki.Model(
        _CLOZE_MODEL_ID,
        "FreeLingo Cloze",
        model_type=genanki.Model.CLOZE,
        fields=[{"name": "Text"}, {"name": "Back Extra"}],
        templates=[
            {
                "name": "Cloze",
                "qfmt": "{{cloze:Text}}",
                "afmt": "{{cloze:Text}}<hr id=answer>{{Back Extra}}",
            }
        ],
        css=_CARD_CSS,
    )


def _cloze_text(card: AnkiCard) -> str:
    """Blank the target word out of its example sentence.

    Falls back to a plain "word — ?" cloze when the example doesn't
    contain the word (or there is no example at all).
    """
    word = card.word.strip()
    example = card.example_sentence.strip()
    if word and example:
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        if pattern.search(example):
            replaced = pattern.sub(
                lambda m: "{{c1::" + m.group(0) + "}}", html.escape(example), count=1
            )
            return replaced
    return "{{c1::" + html.escape(word) + "}}"


def build_apkg(cards: list[AnkiCard], options: AnkiExportOptions) -> bytes:
    """Render the deck and return the .apkg file contents."""
    # hashlib, not hash(): Python string hashing is salted per process,
    # and the deck id must be stable across exports for Anki to update
    # rather than duplicate.
    deck_id = int(hashlib.md5(options.deck_name.encode()).hexdigest()[:8], 16) or 1
    deck = genanki.Deck(deck_id, options.deck_name)

    if options.card_type == "cloze":
        model = _cloze_model()
    else:
        model = _basic_model(options.card_type == "basic_reversed")

    for card in cards:
        if not card.word.strip():
            continue
        if options.card_type == "cloze":
            back = (
                f'<div class="word">{html.escape(card.translation)}</div>'
                + (
                    f'<div class="definition">{html.escape(card.definition)}</div>'
                    if options.include_definition and card.definition
                    else ""
                )
                + _extra_html(card)
            )
            fields = [_cloze_text(card), back]
        else:
            fields = [
                html.escape(card.word),
                html.escape(card.translation),
                html.escape(card.definition)
                if options.include_definition
                else "",
                html.escape(card.example_sentence)
                if options.include_example
                else "",
                html.escape(card.extra),
            ]
        note = genanki.Note(
            model=model,
            fields=fields,
            guid=genanki.guid_for(options.deck_name, card.word, card.translation),
        )
        deck.add_note(note)

    with tempfile.NamedTemporaryFile(suffix=".apkg", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        genanki.Package(deck).write_to_file(str(path))
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


# ── Optional AI enrichment ──────────────────────────────────────────────────

_MAX_ENRICH_CARDS_PER_CALL = 25


class _CardExtra(BaseModel):
    word: str
    extra: str


class _EnrichmentResponse(BaseModel):
    cards: list[_CardExtra]


async def enrich_cards(
    cards: list[AnkiCard], target_language_name: str
) -> list[AnkiCard]:
    """Ask the LLM to expand each card (conjugations, extra example, note).

    Best-effort: any failure returns the original cards untouched so the
    export always succeeds.
    """
    from app.services.llm_adapter import llm_adapter

    enriched: dict[str, str] = {}
    for start in range(0, len(cards), _MAX_ENRICH_CARDS_PER_CALL):
        chunk = cards[start : start + _MAX_ENRICH_CARDS_PER_CALL]
        listing = "\n".join(
            f"- {c.word} ({c.translation})" for c in chunk if c.word.strip()
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"You expand {target_language_name} flashcards for a "
                    "spaced-repetition deck. For EACH word below, write one "
                    "short 'extra' block of genuinely useful additions: for "
                    "verbs, the key present-tense conjugations; for nouns, "
                    "gender/plural; plus one natural example sentence if "
                    "helpful. Plain text, max ~200 characters per word, no "
                    "markdown. Return every word you were given."
                ),
            },
            {"role": "user", "content": listing},
        ]
        try:
            result = await llm_adapter.structured_output(
                messages, _EnrichmentResponse
            )
            for item in result.cards:
                if item.extra.strip():
                    enriched[item.word.strip().lower()] = item.extra.strip()
        except Exception:  # noqa: BLE001 — enrichment must never break exports
            logger.warning("[anki] Enrichment failed for a chunk — exporting plain")

    if not enriched:
        return cards
    return [
        AnkiCard(
            word=c.word,
            translation=c.translation,
            definition=c.definition,
            example_sentence=c.example_sentence,
            extra=enriched.get(c.word.strip().lower(), c.extra),
        )
        for c in cards
    ]
