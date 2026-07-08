"""Pin the incident-driven discipline rules in AGENT_SYSTEM_PROMPT.

Live incident (work chat, 2026-07-08): the agent captured duplicate tasks from
complaint messages («Ты её ни на кого не назначил», «он не умеет ставить
исполнителя?») and answered praise («красота») with a counter-question.
These tests pin the prompt rules; real behavior is covered by the opt-in
live eval (tests/eval/test_agent_live.py, RUN_AGENT_EVAL=1).
"""

from planner.infra.llm.prompts import AGENT_SYSTEM_PROMPT


def test_prompt_declares_meta_comments_are_not_commands():
    assert "МЕТА-КОММЕНТАРИИ — НЕ КОМАНДЫ" in AGENT_SYSTEM_PROMPT


def test_prompt_forbids_repeated_capture():
    assert "не создавай задачу или проект ПОВТОРНО" in AGENT_SYSTEM_PROMPT


def test_prompt_wants_short_reply_to_praise_without_counter_questions():
    assert "БЕЗ встречных вопросов" in AGENT_SYSTEM_PROMPT


def test_prompt_ignores_messages_addressed_to_other_people():
    assert "@упоминания другого человека" in AGENT_SYSTEM_PROMPT
