# Manual Testing Checklist

## Phase 1 — Mic controls

To begin manual testing, start the dev stack with `./run-dev.sh` (see DEVELOPMENT.md). A configured `.env` with an LLM key is required.

- [ ] Start a voice conversation. Confirm identical default behavior (no slider touched): AI replies after a normal pause.
- [ ] Set wait to 15 s. Speak, pause 10 s mid-sentence, continue, stop. Confirm: no reply until 15 s after you truly finish, and the AI's answer addresses your *whole* statement (one utterance).
- [ ] Wait ≥ the 15 s in silence and confirm no `inactivity` session warning appears mid-wait (keepalive working).
- [ ] Switch to Manual. Tap to talk, speak for 60+ s with long pauses, tap to send. Confirm the full recording transcribes and gets one reply, and the session doesn't die during recording.
- [ ] Reload the page. Confirm mode + slider values persisted.
- [ ] Switch modes mid-session and confirm no errors in the browser console or backend logs.

## Phase 2 — Pronunciation assessment

Requires `PRONUNCIATION_PROVIDER=azure`, `AZURE_SPEECH_KEY`, and
`AZURE_SPEECH_REGION` in `.env`. Azure's free tier covers 5 audio hours/month.

- [ ] With the provider left at `none` (default), a voice conversation behaves exactly as before — no score chips anywhere.
- [ ] With Azure configured, speaking a sentence shows a score chip under your transcript bubble within a second or two.
- [ ] Deliberately mispronounce a word — it appears in the "Work on:" line and the score drops.
- [ ] Speak clearly — the score is high and no weak-word line appears.
- [ ] The tutor mentions pronunciation only when the score is poor, and never reads the tag or numbers aloud.
- [ ] Set an invalid `AZURE_SPEECH_KEY` — the conversation still works normally, just with no score chips (check backend logs for a warning).
- [ ] Assistant bubbles never show a score chip.
- [ ] Switch to Manual mic mode and record for more than 60 s (Azure's short-audio limit). Confirm the conversation still works normally — transcript and reply arrive as usual — but with no score chip for that utterance (expected behavior, not a bug), and that the backend logs an info-level "skipping assessment" line instead of an error.

## Phase 3 — Guided lesson mode

- [ ] The plan page unit drawer shows a "Practice with Lingu" button beside each lesson's normal action.
- [ ] Clicking it opens the voice conversation and the tutor introduces that specific lesson (not a generic greeting).
- [ ] The lesson panel lists the steps, the current one is highlighted, and it scrolls as you advance.
- [ ] Hiding the panel leaves only the "step x of y" strip; the preference survives a reload.
- [ ] The tutor teaches one step at a time and does not skip ahead or read out future steps.
- [ ] "Next section →" advances immediately even if the tutor has not called the tool.
- [ ] Completing the last step shows the completion message, and the lesson shows as completed on the plan page afterwards.
- [ ] If the completion save fails (e.g., stop the backend right as the lesson ends), the panel shows a retryable error with a "try again" control, and retrying after the backend is back completes the lesson exactly once.
- [ ] XP and streak update the same as completing that lesson by text.
- [ ] Watch the unit's competency bar: a voice completion records the neutral default of 0.5 for that unit (the voice path never answers Exercise rows, so upstream's default applies), so voice-practising a unit you previously scored higher on by text will *lower* its competency. Confirm this is what you see, and decide whether it is acceptable before wider use.
- [ ] Starting a voice conversation normally (not via the lesson button) behaves exactly as before — no panel, no lesson talk.
- [ ] With a local Ollama model that does not support tools, the lesson still teaches and "Next section →" still advances it.

## Phase 4 — Roleplay mode

- [ ] The unit drawer shows "Use it in a scenario" beside "Practice with Lingu".
- [ ] Launching it opens the voice conversation and the tutor sets a scene in one sentence, then stays in character.
- [ ] The tutor actually plays the character and never refuses ("I can't take on a different persona…") or falls back to an ordinary chat — check this on a small local Ollama model too, where the persona lock bites hardest.
- [ ] Narrow the browser to a 375 px-wide phone viewport and open the unit drawer: each lesson row still shows its title legibly with all three buttons present (the actions wrap onto their own line rather than squeezing the title out).
- [ ] The tutor steers you toward the lesson's vocabulary without lecturing, and corrects in character.
- [ ] No lesson panel, no step list, and no "Next section" button appear in roleplay mode.
- [ ] Finishing a roleplay does NOT mark the lesson complete on the plan page and awards no XP.
- [ ] "Practice with Lingu" still runs the normal guided lesson, unchanged.
- [ ] A plain voice conversation (launched from the sidebar, not from a lesson) is unaffected by both modes.

## Phase 5 — Anki, suggestions, resume, translate

- [ ] Flashcards page → Export to Anki → the .apkg downloads and imports into Anki with your saved cards.
- [ ] Re-exporting and re-importing updates cards instead of duplicating them.
- [ ] A lesson page shows "Export to Anki" (bottom right) and exports that lesson's vocabulary.
- [ ] Cloze type blanks the word inside its example sentence.
- [ ] AI-expand adds conjugations/extras to card backs (slower export; needs the LLM key).
- [ ] In a voice conversation, highlight a word in any transcript bubble — a tooltip offers to save it; saving adds it to the Flashcards page, dismissing saves nothing.
- [ ] Quit a guided lesson mid-way, relaunch the same lesson — it resumes at the step you left (panel shows the right checkmarks).
- [ ] "Translate" under any transcript bubble shows the message in your native language; tapping again hides it.
- [ ] Learn with Lingu page lists today's + catch-up lessons and both launch buttons work.
