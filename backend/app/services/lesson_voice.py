"""Voice-driven lesson stepping (fork feature).

Converts an existing generated lesson (the JSON blob stored on ``lessons.content``)
into an ordered list of teaching steps, and tracks which step a live voice session
is on. The server owns this structure deliberately: the tutor is only ever shown
the current step, so it cannot skip ahead or invent a curriculum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.llm_adapter import LLMTool, LLMToolCall, LLMToolResult

LESSON_STEP_KINDS = ("intro", "explanation", "key_point", "vocabulary", "exercise", "wrap_up")
# Fork: steps where the tutor is expected to TEACH at length, not just prompt.
TEACHING_STEP_KINDS = ("intro", "explanation", "key_point")

LESSON_STEP_RESULT_TOOL_NAME = "lesson_step_result"

_MAX_STEP_TITLE = 60


def _step_title(text: str) -> str:
    """Trim a step title at a word boundary — mid-word cuts read as broken UI."""
    if len(text) <= _MAX_STEP_TITLE:
        return text
    cut = text[:_MAX_STEP_TITLE].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return f"{cut}…"


_MAX_KEY_POINTS = 6
_MAX_VOCABULARY = 8
_MAX_EXERCISES = 6
_MAX_EXAMPLES = 4

_TAG_MARKER_RE = re.compile(r"</?(?:guided_lesson|roleplay)>", re.IGNORECASE)


def _neutralize_tag_markers(value: str) -> str:
    """Strip literal ``<guided_lesson>``/``</guided_lesson>`` and
    ``<roleplay>``/``</roleplay>`` sequences.

    This is deliberately NOT HTML escaping. The overlay is interpolated into a
    plain-text LLM prompt (read aloud by a voice tutor), not into HTML, so normal
    punctuation — apostrophes, ampersands, accents — must survive untouched
    (e.g. "l'école", "qu'est-ce que", "A & B"). The only thing lesson text must
    not be able to do is forge or prematurely close one of our own wrapper tags,
    so we remove just those literal markers, case-insensitively, and leave
    everything else alone. Shared by both the guided-lesson step overlay and
    the roleplay overlay, since both interpolate untrusted lesson text.
    """
    return _TAG_MARKER_RE.sub("", value)


@dataclass(frozen=True)
class LessonStep:
    index: int
    kind: str
    title: str
    body: str
    goal: str
    reference_text: str | None = None
    # Fork: set on exercise steps sourced from the lesson's Exercise rows, so
    # the client can render the real question/choices and the pipeline can
    # write the learner's answer back to the row.
    exercise_id: int | None = None
    prompt: str | None = None
    options: list[str] | None = None


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def build_lesson_steps(
    content: dict,
    objectives: list[str],
    title: str,
    db_exercises: list[dict] | None = None,
) -> list[LessonStep]:
    """Turn stored lesson content into an ordered teaching plan.

    Defensive by design: anything malformed is skipped rather than raised, so a
    partially-generated lesson still yields a usable session.
    """
    raw: list[dict] = []
    safe_title = _clean(title) or "this lesson"

    goal_list = [g for g in (objectives or []) if _clean(g)]
    intro_body = f"This lesson is called “{safe_title}”."
    if goal_list:
        intro_body += " By the end the student should be able to: " + "; ".join(goal_list) + "."
    explanation = content.get("explanation") if isinstance(content, dict) else None
    raw.append(
        {
            "kind": "intro",
            "title": "Introduction",
            "body": intro_body,
            "goal": "The student understands what this lesson covers and is ready to begin.",
        }
    )

    # Fork: a dedicated explanation step, mirroring the lesson page — the
    # concept is taught in full (what it is, when it is used, exactly how it
    # is formed, with examples) BEFORE any practice is asked for.
    if isinstance(explanation, dict):
        overview = _clean(explanation.get("text"))
        if overview:
            explain_body = f"Explain this concept fully before any practice: {overview}"
            native_expl = (
                content.get("native_explanation") if isinstance(content, dict) else None
            )
            native_text = (
                _clean(native_expl.get("text")) if isinstance(native_expl, dict) else ""
            )
            if native_text:
                explain_body += (
                    " (Reference version in the student's native language, so you "
                    f"can clarify the rule in it if they struggle: {native_text})"
                )
            raw.append(
                {
                    "kind": "explanation",
                    "title": "How it works",
                    "body": explain_body,
                    "goal": (
                        "The student has heard the complete explanation — what it is, "
                        "when to use it, and how to form it, with examples — and says "
                        "they understand (or asks a question you answer). Do NOT ask "
                        "them to produce their own sentence in this step."
                    ),
                }
            )

    if isinstance(explanation, dict):
        examples = explanation.get("examples")
        example_lines: list[str] = []
        if isinstance(examples, list):
            for ex in examples[:_MAX_EXAMPLES]:
                if not isinstance(ex, dict):
                    continue
                sentence = _clean(ex.get("sentence"))
                if not sentence:
                    continue
                note = _clean(ex.get("note"))
                example_lines.append(f"{sentence} ({note})" if note else sentence)
        key_points = explanation.get("key_points")
        if isinstance(key_points, list):
            for point in key_points[:_MAX_KEY_POINTS]:
                text = _clean(point)
                if not text:
                    continue
                body = f"Teaching point: {text}"
                if example_lines:
                    body += " Examples you may use: " + "; ".join(example_lines)
                raw.append(
                    {
                        "kind": "key_point",
                        "title": _step_title(text),
                        "body": body,
                        "goal": (
                            "The student can explain or use this point correctly in a "
                            "sentence they produce themselves."
                        ),
                    }
                )

    taught_words: list[str] = []
    vocabulary = content.get("vocabulary") if isinstance(content, dict) else None
    if isinstance(vocabulary, list):
        for item in vocabulary[:_MAX_VOCABULARY]:
            if not isinstance(item, dict):
                continue
            word = _clean(item.get("word"))
            if not word:
                continue
            taught_words.append(word)
            definition = _clean(item.get("definition"))
            example = _clean(item.get("example"))
            body = f"Target word: {word}."
            if definition:
                body += f" It means: {definition}."
            if example:
                body += f" Example: {example}"
            raw.append(
                {
                    "kind": "vocabulary",
                    "title": word,
                    "body": body,
                    "goal": (
                        f"The student says “{word}” aloud and uses it in a sentence "
                        "of their own."
                    ),
                    "reference_text": word,
                }
            )

    # Fork: the lesson page's real practice problems (Exercise rows) take
    # precedence over whatever the content JSON happens to embed — they carry
    # ids (for writing the learner's answer back) and the same options the
    # lesson page shows.
    exercise_items: list[dict] = []
    if db_exercises:
        exercise_items = [item for item in db_exercises if isinstance(item, dict)]
    else:
        content_exercises = content.get("exercises") if isinstance(content, dict) else None
        if isinstance(content_exercises, list):
            exercise_items = [item for item in content_exercises if isinstance(item, dict)]
    for item in exercise_items[:_MAX_EXERCISES]:
        question = _clean(item.get("question"))
        answer = _clean(item.get("correct"))
        if not question or not answer:
            continue
        body = f"Ask this question aloud: {question}"
        choices: list[str] = []
        options = item.get("options")
        if isinstance(options, list):
            choices = [_clean(o) for o in options if _clean(o)]
            if choices:
                body += " Read the choices aloud: " + ", ".join(choices) + "."
        body += (
            " The student answers by SPEAKING. When they answer, report it via the "
            "lesson tool's student_answer field."
        )
        explanation_text = _clean(item.get("explanation"))
        goal = f"The student answers with “{answer}”."
        if explanation_text:
            goal += f" If they miss it, explain: {explanation_text}"
        raw.append(
            {
                "kind": "exercise",
                "title": _step_title(question),
                "body": body,
                "goal": goal,
                "exercise_id": item.get("id"),
                "prompt": question,
                "options": choices or None,
            }
        )

    wrap_body = (
        "Final challenge: ask the student ONE question that makes them combine "
        "what this lesson taught in a single answer"
    )
    if taught_words:
        wrap_body += (
            " — for example a mini exchange using: "
            + ", ".join(taught_words[:_MAX_VOCABULARY])
            + ""
        )
    wrap_body += (
        ". After their attempt, give a one-sentence recap of what was covered "
        "and praise the thing they did best. Do not ask what they thought of "
        "the class."
    )
    raw.append(
        {
            "kind": "wrap_up",
            "title": "Wrap up",
            "body": wrap_body,
            "goal": (
                "The student has attempted the final combined answer and heard "
                "the recap."
            ),
        }
    )

    return [
        LessonStep(
            index=i,
            kind=str(entry["kind"]),
            title=str(entry["title"]),
            body=str(entry["body"]),
            goal=str(entry["goal"]),
            reference_text=entry.get("reference_text"),
            exercise_id=entry.get("exercise_id"),
            prompt=entry.get("prompt"),
            options=entry.get("options"),
        )
        for i, entry in enumerate(raw)
    ]


class LessonSession:
    """Tracks which step a live voice lesson is on. Not persisted."""

    def __init__(self, lesson_id: int, title: str, steps: list[LessonStep]) -> None:
        self.lesson_id = lesson_id
        self.title = title
        self.steps = steps
        self.current_index = 0
        self.results: list[str] = []
        # Fork: learner turns spent on the current step. Drives the overlay's
        # escalating "decide now" nudge so a step can't silently stall when
        # the model narrates completion instead of calling the tool.
        self.turns_on_step = 0

    @property
    def current(self) -> LessonStep | None:
        if self.current_index >= len(self.steps):
            return None
        return self.steps[self.current_index]

    @property
    def is_complete(self) -> bool:
        return self.current_index >= len(self.steps)

    def fast_forward(self, step_index: int, results: list[str] | None = None) -> None:
        """Resume a previously-persisted session at ``step_index`` (fork).

        Bounded and defensive: anything out of range clamps, results are
        normalised, and a full-length index leaves the session complete.
        """
        target = max(0, min(int(step_index), len(self.steps)))
        stored = list(results or [])
        self.results = [
            r if r in ("passed", "struggled") else "struggled"
            for r in stored[:target]
        ]
        while len(self.results) < target:
            self.results.append("passed")
        self.current_index = target
        self.turns_on_step = 0

    def advance(self, result: str) -> bool:
        """Record the current step's outcome and move on.

        Returns True only on the transition that completes the lesson.
        """
        if self.is_complete:
            return False
        self.results.append(result if result in ("passed", "struggled") else "struggled")
        self.current_index += 1
        self.turns_on_step = 0
        return self.is_complete

    def note_student_turn(self) -> None:
        """Count one learner utterance against the current step (fork)."""
        if not self.is_complete:
            self.turns_on_step += 1

    def state_payload(self) -> dict:
        steps = []
        for step in self.steps:
            if step.index < self.current_index:
                status = "done"
            elif step.index == self.current_index:
                status = "current"
            else:
                status = "pending"
            entry: dict = {"title": step.title, "status": status, "kind": step.kind}
            # Only the current exercise shows its full prompt/choices — the
            # panel must not spoil upcoming questions.
            if step.kind == "exercise" and status == "current":
                if step.prompt:
                    entry["prompt"] = _neutralize_tag_markers(step.prompt)
                if step.options:
                    entry["options"] = [_neutralize_tag_markers(o) for o in step.options]
            steps.append(entry)
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "step_index": self.current_index,
            "total": len(self.steps),
            "steps": steps,
        }

    def overlay(self) -> str:
        """Render the prompt overlay for the current step only."""
        step = self.current
        if step is None:
            return ""
        return (
            "<guided_lesson>\n"
            f"You are teaching the lesson “{_neutralize_tag_markers(self.title)}” by voice.\n"
            f"Step {step.index + 1} of {len(self.steps)} — {_neutralize_tag_markers(step.title)}\n"
            f"What to teach now: {_neutralize_tag_markers(step.body)}\n"
            f"What the student must show: {_neutralize_tag_markers(step.goal)}\n"
            f"{self._depth_guidance(step)}"
            "Teach ONLY this step. Do not move on by yourself and do not mention "
            "later steps — you cannot see them. Ask at most ONE question per reply, always "
            "as the very last sentence — never ask something and then keep talking "
            "past it. When the student has shown what this step "
            f"requires, call the {LESSON_STEP_RESULT_TOOL_NAME} tool with result "
            "\"passed\" (or \"struggled\" if they could not manage it after a couple "
            "of tries) and the lesson will advance automatically. When you call the "
            "tool, put it BEFORE your feedback text, and keep that feedback to one "
            "short sentence. Never just SAY the student has completed or mastered "
            "this step — if that is true, calling the tool is how you say it. "
            "If the student asks a side question (how to say something, what to "
            "do next, a grammar doubt), answer it briefly in one or two sentences, "
            "then return to this step's goal.\n"
            f"{self._stall_nudge()}"
            "</guided_lesson>"
        )

    @staticmethod
    def _depth_guidance(step: LessonStep) -> str:
        """How much to say on this step (fork).

        Teaching steps get room to actually teach — the lesson page gives the
        learner a full explanation before exercises, and the voice lesson must
        too. Practice steps stay short so the learner does the talking.
        """
        if step.kind == "explanation":
            return (
                "How to teach this step: this is the main teaching moment. Explain "
                "the concept properly in about five to eight short spoken sentences: "
                "what it is, when it is used, and exactly HOW it is formed (endings, "
                "vowel or stem changes, word order — the concrete mechanics), with "
                "two or three example sentences. Speak in simple sentences for the "
                "student's level; you may restate the core rule once in the "
                "student's native language if it is complex. Finish by asking "
                "whether that makes sense or if they want an example again — not "
                "by asking them to produce a sentence.\n"
            )
        if step.kind == "key_point":
            return (
                "How to teach this step: state the point clearly, give two example "
                "sentences, then ask the student to try one of their own. Three to "
                "five short spoken sentences.\n"
            )
        if step.kind == "intro":
            return "Keep this to two or three spoken sentences.\n"
        return "Keep to one or two spoken sentences, then wait for the student.\n"

    def _stall_nudge(self) -> str:
        """Escalating instruction when a step has run long (fork).

        Silent for the first couple of turns; from the third learner turn on
        the model must make a decision each turn instead of drifting.
        """
        n = self.turns_on_step
        if n < 3:
            return ""
        return (
            f"STEP CHECK: the student has now spoken {n} times on this step. "
            "Before you reply, decide: (a) if at ANY point in this conversation "
            "they demonstrated the goal above, call "
            f"{LESSON_STEP_RESULT_TOOL_NAME} with \"passed\" now; (b) if they "
            "have tried and still cannot, give one final short hint and call it "
            "with \"struggled\"; (c) only if they have not attempted the goal at "
            "all yet, ask them for it directly in one sentence. Do not keep "
            "the student on this step any longer than that.\n"
        )


def build_lesson_step_tool() -> LLMTool:
    return LLMTool(
        name=LESSON_STEP_RESULT_TOOL_NAME,
        description=(
            "Record that the student has finished the current lesson step and move "
            "the lesson to the next step. Call this only once the student has "
            "demonstrated what the current step requires, or after a couple of "
            "unsuccessful tries."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "enum": ["passed", "struggled"],
                    "description": (
                        "\"passed\" if the student demonstrated the step, "
                        "\"struggled\" if they could not after a couple of tries."
                    ),
                },
                "note": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optional one-line note about how the student did.",
                },
                "student_answer": {
                    "type": "string",
                    "maxLength": 300,
                    "description": (
                        "For exercise steps: the answer the student gave, verbatim."
                    ),
                },
            },
            "required": ["result"],
            "additionalProperties": False,
        },
    )


def execute_lesson_step_result(
    session: LessonSession, call: LLMToolCall
) -> LLMToolResult:
    if call.name != LESSON_STEP_RESULT_TOOL_NAME:
        return LLMToolResult(
            call=call,
            content={"advanced": False, "error": "unknown_tool"},
            is_error=True,
        )
    if session.is_complete:
        return LLMToolResult(
            call=call,
            content={"advanced": False, "lesson_complete": True},
        )
    result = call.arguments.get("result")
    just_completed = session.advance(result if isinstance(result, str) else "struggled")
    content: dict = {
        "advanced": True,
        "lesson_complete": just_completed,
        "step_index": session.current_index,
        "total": len(session.steps),
    }
    nxt = session.current
    if nxt is not None:
        # Without this, the rest of the model's reply is generated against the
        # PREVIOUS step's overlay and it improvises small talk until the
        # learner's next turn re-renders the prompt. Handing the new step back
        # in the tool result lets it start teaching it immediately.
        content["next_step"] = {
            "title": nxt.title,
            "teach_now": nxt.body,
            "goal": nxt.goal,
        }
        content["instruction"] = (
            "Continue this same reply by teaching next_step now — do not "
            "change the subject or ask unrelated questions. The student has "
            "NOT spoken since their last message: never react to an answer "
            "they have not given (no 'perfecto', 'muy bien' for next_step). "
            "If the text you wrote before this tool call ended with a "
            "question to the student, drop that thread — move straight into "
            "next_step. Ask the student exactly ONE question or prompt, at "
            "the very END of your reply, and it must be about next_step."
        )
    return LLMToolResult(call=call, content=content)


# ── Roleplay mode (fork: phase 4) ───────────────────────────────────────────

LESSON_MODES = ("guided", "roleplay")

_MAX_ROLEPLAY_VOCABULARY = 10
_MAX_ROLEPLAY_POINTS = 4


def normalize_lesson_mode(value: object) -> str:
    """Coerce a client-supplied lesson mode; anything unknown means guided."""
    if isinstance(value, str) and value in LESSON_MODES:
        return value
    return "guided"


def build_roleplay_overlay(content: dict, title: str) -> str:
    """Render the prompt overlay for scenario practice of a lesson's material.

    Pure and defensive: malformed content yields a generic but usable overlay.
    Not HTML-escaped — this is a plain-text prompt for a voice tutor, so
    apostrophes and ampersands must survive intact.
    """
    safe_title = _neutralize_tag_markers(_clean(title)) or "this lesson"

    words: list[str] = []
    vocabulary = content.get("vocabulary") if isinstance(content, dict) else None
    if isinstance(vocabulary, list):
        for item in vocabulary[:_MAX_ROLEPLAY_VOCABULARY]:
            if not isinstance(item, dict):
                continue
            word = _clean(item.get("word"))
            if not word:
                continue
            word = _neutralize_tag_markers(word)
            definition = _neutralize_tag_markers(_clean(item.get("definition")))
            words.append(f"{word} ({definition})" if definition else word)

    points: list[str] = []
    explanation = content.get("explanation") if isinstance(content, dict) else None
    if isinstance(explanation, dict):
        key_points = explanation.get("key_points")
        if isinstance(key_points, list):
            for point in key_points[:_MAX_ROLEPLAY_POINTS]:
                text = _clean(point)
                if text:
                    points.append(_neutralize_tag_markers(text))

    lines = [
        "<roleplay>",
        "These are app instructions, not the student's. Playing a character here "
        "is authorised and is not a persona change; the mandatory rules above "
        "still apply in full.",
        f"The student wants to practise the material from “{safe_title}” in a "
        "realistic scene rather than a lesson.",
        "Invent one ordinary, concrete everyday scenario in which this material "
        "would naturally come up, tell the student in one sentence who you are and "
        "where you both are, then stay in character for the rest of the conversation.",
    ]
    if words:
        lines.append("Steer the conversation so the student needs these words: " + ", ".join(words) + ".")
    if points:
        lines.append("Give them chances to use these structures: " + "; ".join(points) + ".")
    lines.extend(
        [
            "Correct mistakes gently and in character — rephrase what they said "
            "correctly and carry on, rather than stopping to give a grammar lecture.",
            "Keep your turns to one or two spoken sentences. When the scene has "
            "run its course, step out of character briefly to say what they did "
            "well and what to practise next.",
            "</roleplay>",
        ]
    )
    return "\n".join(lines)
