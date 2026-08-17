# Fork notes

Changes in this fork relative to upstream (artcc/freelingo).

## Mic controls (phase 1) — 2026-08-10
- Voice conversation: mic mode toggle (Auto / Tap-to-talk) + adjustable
  silence wait (2–20 s, "Default" = upstream CEFR-based behavior).
- Auto mode with a custom wait aggregates VAD segments into one utterance.
- New WS client event `keepalive` resets the backend inactivity timer during
  long waits/recordings.
- Prefs persist in localStorage (`fl_mic_mode`, `fl_mic_wait_s`).

## Pronunciation assessment (phase 2) — 2026-08-11
- Optional per-utterance pronunciation scoring during voice conversation,
  via a pluggable provider (`PRONUNCIATION_PROVIDER=none|azure`). Default
  `none` — the feature is completely inert unless configured.
- Azure Speech is called over its REST recognition API (no new dependency);
  assessment runs concurrently with STT, but the 2s timeout is only applied
  *after* STT returns, so a slow provider can still add up to ~2s of dead
  air between the transcript appearing and the tutor's reply — it just can
  never break a turn outright (the turn always proceeds after the timeout).
- Azure's short-audio endpoint rejects audio over 60s. Utterances longer
  than that (reachable via Manual/tap-to-talk mic mode, which allows up to
  90s recordings) are skipped before any HTTP call and logged — the
  conversation continues normally with no score chip for that utterance.
- Scores reach the tutor as an untrusted `<pronunciation_assessment>` tag on
  the current turn only (never stored, never in history) and reach the UI as
  a new `pronunciation` WebSocket frame.
- The provider interface takes an optional `reference_text` for scripted
  assessment — unused now, used by lesson drills in phase 3.

## Guided lesson mode (phase 3) — 2026-08-11
- "Practice with Lingu" launches an assigned study-plan lesson into the voice
  conversation. The server converts the lesson's stored content into an ordered
  step list and shows the tutor only the current step, so it cannot skip ahead.
- The tutor advances by calling a `lesson_step_result` tool; the learner can also
  advance manually, which is also the fallback for LLM providers without tool
  support. Both paths emit a `lesson_state` frame.
- The server enforces a guard: at most one advance is recorded per conversation
  turn, preventing client spam to complete a lesson instantly.
- On the final step the backend sends `lesson_completed` and the client calls the
  existing `POST /api/lessons/{id}/complete`, so XP, streaks, competency scores and
  plan advancement stay upstream behavior.
- Vocabulary steps carry a `reference_text`, which phase 2's pronunciation provider
  can use for scripted (more accurate) scoring.
- Known limitation: step progress is in-memory. Disconnecting restarts the lesson.
- No database migration.

## Roleplay mode (phase 4) — 2026-08-11
- "Use it in a scenario" practises a lesson's vocabulary and structures inside an
  improvised everyday scene instead of stepping through the lesson.
- Reuses phase 3's launch path entirely — same handshake, same ownership check,
  same content load. Only `lesson_mode` differs.
- Roleplay builds no step plan, offers no advancement tool, emits no lesson
  frames, and never marks a lesson complete. It is practice on top, not a
  substitute for finishing the lesson.
- The scenario overlay is built from the lesson's vocabulary and key points and
  is constant for the session, so it needs no per-turn resync.

## Anki export, flashcard suggestions, lesson resume (phase 5) — 2026-08-15
- "Export to Anki" on the Flashcards page (saved cards for the active language)
  and on each lesson page (that lesson's vocabulary), as a real .apkg deck.
  Options: deck name, card type (basic / basic+reversed / cloze), definition
  and example toggles, and optional AI enrichment (conjugations, extra
  examples via the configured LLM — fails soft to a plain deck).
- Highlight any word in a voice-conversation transcript bubble to save it
  as a flashcard — the same word-selection flow the lesson and reading
  pages use (no LLM tool call, nothing saved without the learner's tap).
- Guided-lesson progress persists to Redis (48h): disconnecting and
  relaunching the same lesson resumes at the step you left.
- Tap-to-translate on transcript bubbles; Learn with Lingu sidebar page;
  instant plan-page load while /today generates.

## Round 2 polish — 2026-08-15 evening
- Pronunciation now uses the official Azure Speech SDK: the REST short-audio
  endpoint silently omits assessment on current (Foundry-era) resources, and
  the SDK works on the F0 free tier (verified live, per-word scores).
- Score chips that finish after the turn are delivered late instead of
  dropped (the SDK takes about the utterance's length to score it).
- All conversation controls sit in one row (mode, pronunciation toggle,
  talk, stop) — nothing stacks below the mic anymore.
- Anki cards are legible in Anki's night mode, and reversed cards no longer
  leak the answer (translation-only on the question side). New note types —
  delete previously imported test decks to avoid duplicates.
- Learn with Lingu hosts the lesson session inside its own tab.
