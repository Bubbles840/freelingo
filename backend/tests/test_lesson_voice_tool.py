"""Lesson advancement tool (fork: guided lesson mode)."""

from __future__ import annotations

from app.services.lesson_voice import (
    LESSON_STEP_RESULT_TOOL_NAME,
    LessonSession,
    LessonStep,
    build_lesson_step_tool,
    execute_lesson_step_result,
)
from app.services.llm_adapter import LLMToolCall


def _session(n: int = 3) -> LessonSession:
    steps = [
        LessonStep(index=i, kind="key_point", title=f"S{i}", body="b", goal="g")
        for i in range(n)
    ]
    return LessonSession(lesson_id=1, title="T", steps=steps)


def _call(name: str = LESSON_STEP_RESULT_TOOL_NAME, **args) -> LLMToolCall:
    return LLMToolCall(id="c1", name=name, arguments=args, raw_arguments="{}")


def test_tool_definition_shape():
    tool = build_lesson_step_tool()
    assert tool.name == LESSON_STEP_RESULT_TOOL_NAME
    props = tool.input_schema["properties"]
    assert props["result"]["enum"] == ["passed", "struggled"]
    assert tool.input_schema["required"] == ["result"]


def test_execute_advances_the_session():
    s = _session()
    result = execute_lesson_step_result(s, _call(result="passed"))
    assert result.is_error is False
    assert result.content["advanced"] is True
    assert result.content["lesson_complete"] is False
    assert s.current_index == 1


def test_execute_reports_lesson_completion():
    s = _session(n=1)
    result = execute_lesson_step_result(s, _call(result="passed"))
    assert result.content["lesson_complete"] is True
    assert s.is_complete is True


def test_execute_rejects_unknown_tool_name():
    s = _session()
    result = execute_lesson_step_result(s, _call(name="something_else", result="passed"))
    assert result.is_error is True
    assert result.content["error"] == "unknown_tool"
    assert s.current_index == 0


def test_execute_defaults_missing_result_to_struggled():
    s = _session()
    execute_lesson_step_result(s, _call())
    assert s.results == ["struggled"]


def test_execute_after_completion_is_not_an_error():
    s = _session(n=1)
    execute_lesson_step_result(s, _call(result="passed"))
    result = execute_lesson_step_result(s, _call(result="passed"))
    assert result.is_error is False
    assert result.content["advanced"] is False
    assert result.content["lesson_complete"] is True


def test_execute_returns_the_next_step_so_the_reply_stays_on_lesson():
    s = _session(n=3)
    result = execute_lesson_step_result(s, _call(result="passed"))
    nxt = result.content["next_step"]
    assert nxt["title"] == s.steps[1].title
    assert nxt["teach_now"] == s.steps[1].body
    assert nxt["goal"] == s.steps[1].goal
    assert "instruction" in result.content


def test_execute_on_the_final_step_has_no_next_step():
    s = _session(n=1)
    result = execute_lesson_step_result(s, _call(result="passed"))
    assert "next_step" not in result.content
    assert result.content["lesson_complete"] is True
