# Intent Parsing Reliability Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make voice/text commands parse **deterministically and correctly** — fix the three proven root causes behind "Не понял команду", harden the regex fallback, and add a regression eval set to measure intent coverage.

**Architecture:** Intent parsing = `ClaudeIntentParser` (LLM, Haiku) with a `BasicIntentParser` regex safety net, both behind `IntentParserPort`. The bot handler `_handle_text` builds a `ChatContext` and dispatches the parsed `Intent`. No new components — we fix configuration, prompt, context population, the fallback, and tests.

**Tech Stack:** Python 3.12, anthropic SDK, pydantic, aiogram, pytest/pytest-asyncio.

## Root cause (proven against the live API during diagnosis)

1. **`temperature` unset → 1.0** in `claude.py` → same command parses `load` one run, `clarify` next. → set `temperature=0`.
2. **Prompt gaps** — `load` intent omits availability vocabulary (слоты/свободно/занят/доступно) and availability *questions* fall to `clarify`. → extend the `load` description.
3. **Empty `ChatContext`** (`task_router.py:167`) — `known_people`/`known_projects` never populated → no roster for name/project resolution. → build from `repo.list_people()` / `repo.list_projects()`.
4. **Fallback hazard** — `BasicIntentParser` turns ANY non-empty text into a `CaptureTaskIntent`, so on a Claude timeout a question ("сколько слотов?") becomes a bogus stored task. → recognise availability/load questions and avoid capturing questions as tasks.

Proven: `temperature=0` + the prompt tweak made all four screenshot-failing phrases parse `load` deterministically (3/3).

---

## File Structure

| File | Change |
|---|---|
| `src/planner/infra/llm/claude.py` | Add `temperature=0` to the intent `messages.create` call |
| `src/planner/infra/llm/prompts.py` | Extend `load` coverage in `INTENT_SYSTEM_PROMPT` |
| `src/planner/bot/handlers/task_router.py` | Populate `ChatContext` from repo in `_handle_text` |
| `src/planner/infra/llm/basic.py` | Harden: availability/question handling |
| `tests/unit/infra/test_claude_parser.py` | Add temperature + prompt-coverage tests |
| `tests/unit/bot/test_handler_coverage.py` | Add context-population test |
| `tests/unit/test_basic_parser.py` | Add hardening tests |
| `tests/eval/test_intent_eval.py` | NEW — opt-in live regression eval |
| `pyproject.toml` | Register `live` pytest marker |

---

## Task 1: temperature=0 on the intent call

**Files:** Modify `src/planner/infra/llm/claude.py`; Test `tests/unit/infra/test_claude_parser.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/infra/test_claude_parser.py`:

```python
@pytest.mark.asyncio
async def test_parse_uses_temperature_zero():
    """Intent classification must be deterministic (temperature=0)."""
    parser = _make_parser()
    parser._client.messages.create = AsyncMock(
        return_value=_json_resp('{"kind": "load", "person_name": null}')
    )
    ctx = ChatContext(today=date(2026, 6, 5))
    await parser.parse("загрузка команды", ctx)
    kwargs = parser._client.messages.create.call_args.kwargs
    assert kwargs["temperature"] == 0
```

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/unit/infra/test_claude_parser.py::test_parse_uses_temperature_zero -v`
Expected: FAIL — `KeyError: 'temperature'` (call has no temperature).

- [ ] **Step 3: Implement** — in `src/planner/infra/llm/claude.py`, add a constant near the other module constants:

```python
_TEMPERATURE = 0  # deterministic classification — same command, same intent
```

Then in `parse()`, add `temperature=_TEMPERATURE,` to the `self._client.messages.create(...)` call (the intent one), e.g.:

```python
            resp = await self._client.messages.create(
                model=_MODEL,
                max_tokens=400,
                temperature=_TEMPERATURE,
                system=INTENT_SYSTEM_PROMPT + _JSON_INSTRUCTION,
                messages=[{"role": "user", "content": build_user_message(text, ctx)}],
            )
```

Leave `explain_plan` unchanged (prose, determinism not required).

- [ ] **Step 4: Run it, verify it passes**

Run: `uv run pytest tests/unit/infra/test_claude_parser.py -v`
Expected: PASS (all, including the new test).

- [ ] **Step 5: Commit**

```bash
git add src/planner/infra/llm/claude.py tests/unit/infra/test_claude_parser.py
git commit -m "fix: set temperature=0 for deterministic intent parsing"
```

---

## Task 2: extend load coverage in the prompt

**Files:** Modify `src/planner/infra/llm/prompts.py`; Test `tests/unit/infra/test_claude_parser.py`

- [ ] **Step 1: Write the failing test** — append:

```python
def test_intent_prompt_covers_availability_load():
    """load must cover availability vocabulary and availability questions."""
    p = INTENT_SYSTEM_PROMPT.lower()
    for marker in ("слот", "свобод", "занят", "доступ"):
        assert marker in p, f"prompt missing load marker: {marker}"
    # availability questions must be steered to load, not clarify
    assert "вопрос" in p and "load" in p
```

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/unit/infra/test_claude_parser.py::test_intent_prompt_covers_availability_load -v`
Expected: FAIL — markers absent.

- [ ] **Step 3: Implement** — in `src/planner/infra/llm/prompts.py`, replace the single `load` bullet line with this expanded version (keep all other bullets unchanged):

```python
- load: показать загрузку/доступность. person_name (или пусто = вся команда),
  date_range. Слова-маркеры: загрузка, нагрузка, слоты, свободен, свободно,
  занят, доступно, доступность, сколько времени, успеваем, кто чем занят.
  ВАЖНО: ВОПРОС о доступности/загрузке/слотах человека или команды — это load
  (а НЕ clarify и НЕ capture_task). Пример: «сколько слотов у Рая?» → load
  person_name=Рай; «кто сейчас свободен?» → load (пусто).
```

- [ ] **Step 4: Run it, verify it passes**

Run: `uv run pytest tests/unit/infra/test_claude_parser.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/planner/infra/llm/prompts.py tests/unit/infra/test_claude_parser.py
git commit -m "fix: prompt covers availability/load questions"
```

---

## Task 3: populate ChatContext from repo

**Files:** Modify `src/planner/bot/handlers/task_router.py`; Test `tests/unit/bot/test_handler_coverage.py`

**Context:** `_handle_text` currently does `ctx = ChatContext(today=date.today())`. `RepoPort` exposes `async def list_people() -> list[PersonRecord]` (`.name`) and `async def list_projects() -> list[ProjectRecord]` (`.title`). When `repo` is `None` (degraded/echo mode) keep the empty context.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/bot/test_handler_coverage.py` (match the file's existing import/fixture style; this is the intent):

```python
@pytest.mark.asyncio
async def test_handle_text_populates_context_from_repo():
    """_handle_text must pass known_people/known_projects to the parser."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from planner.bot.handlers.task_router import _handle_text
    from planner.domain.intent import LoadIntent

    captured = {}

    class _Parser:
        async def parse(self, text, ctx):
            captured["ctx"] = ctx
            return LoadIntent(person_name=None)

    repo = SimpleNamespace(
        list_people=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), name="Рай")]),
        list_projects=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), title="МТС")]),
    )
    msg = SimpleNamespace(answer=AsyncMock())
    await _handle_text(
        msg, "сколько слотов у Рая?", _Parser(), {"is_admin": True},
        repo=repo, actor_record=SimpleNamespace(id=uuid4(), name="Андрей"),
    )
    assert "Рай" in captured["ctx"].known_people
    assert "МТС" in captured["ctx"].known_projects
```

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/unit/bot/test_handler_coverage.py::test_handle_text_populates_context_from_repo -v`
Expected: FAIL — `known_people` empty.

- [ ] **Step 3: Implement** — in `src/planner/bot/handlers/task_router.py`, replace line 167 (`ctx = ChatContext(today=date.today())`) with:

```python
    known_people: tuple[str, ...] = ()
    known_projects: tuple[str, ...] = ()
    if repo is not None:
        known_people = tuple(p.name for p in await repo.list_people())
        known_projects = tuple(pr.title for pr in await repo.list_projects())
    ctx = ChatContext(
        today=date.today(),
        known_people=known_people,
        known_projects=known_projects,
    )
```

- [ ] **Step 4: Run it, verify it passes**

Run: `uv run pytest tests/unit/bot/test_handler_coverage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/planner/bot/handlers/task_router.py tests/unit/bot/test_handler_coverage.py
git commit -m "fix: populate ChatContext with roster and projects from repo"
```

---

## Task 4: harden BasicIntentParser fallback

**Files:** Modify `src/planner/infra/llm/basic.py`; Test `tests/unit/test_basic_parser.py`

**Context:** Today `_load_kw = ("load", "загруз", "нагруз", "загузк", "нагузк")`. Anything unmatched and non-empty becomes `CaptureTaskIntent`, so on a Claude timeout a question becomes a bogus task. Two changes: (a) add availability markers to the load keywords; (b) if the text is a question (ends with `?`) that matched no actionable intent, return `ClarifyIntent` instead of capturing it as a task.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_basic_parser.py` (match existing style):

```python
@pytest.mark.asyncio
async def test_availability_question_is_load():
    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    from datetime import date
    ctx = ChatContext(today=date(2026, 6, 5), known_people=("Рай",))
    out = await BasicIntentParser().parse("сколько слотов у Рая?", ctx)
    assert out.kind == "load"


@pytest.mark.asyncio
async def test_plain_question_not_captured_as_task():
    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    from datetime import date
    ctx = ChatContext(today=date(2026, 6, 5))
    out = await BasicIntentParser().parse("ты изменила загрузку?", ctx)
    assert out.kind in ("load", "clarify")  # never a captured task


@pytest.mark.asyncio
async def test_imperative_still_captured_as_task():
    """Regression: real task-like messages still capture."""
    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    from datetime import date
    ctx = ChatContext(today=date(2026, 6, 5))
    out = await BasicIntentParser().parse("подготовить бриф по МТС", ctx)
    assert out.kind == "capture_task"
```

- [ ] **Step 2: Run them, verify they fail**

Run: `uv run pytest tests/unit/test_basic_parser.py -v -k "availability or plain_question or imperative_still"`
Expected: the availability and plain-question tests FAIL (currently → capture_task); imperative test PASSES.

- [ ] **Step 3: Implement** — in `src/planner/infra/llm/basic.py`:

(a) Extend the load keywords (the `_load_kw` tuple in `parse_sync`):

```python
        _load_kw = (
            "load", "загруз", "нагруз", "загузк", "нагузк",
            "слот", "свобод", "занят", "доступ",
        )
```

(b) Before the final `capture_task` default, add a question guard. Replace the trailing block:

```python
        # Default: capture the message as a task (low-friction path). Only
        # truly empty input falls through to clarify.
        if low:
            return CaptureTaskIntent(
                task_title=text.strip(),
                assignee_name=_resolve_person(text, ctx),
                deadline=_parse_date(text, ctx.today),
            )
        return ClarifyIntent(...)
```

with:

```python
        # A question that matched no actionable intent is not a task — capturing
        # it would store garbage. Ask again instead.
        if low.endswith("?"):
            return ClarifyIntent(
                question="Это вопрос о загрузке? Уточни: «сколько слотов у Рая?»"
            )
        # Default: capture the message as a task (low-friction path). Only
        # truly empty input falls through to clarify.
        if low:
            return CaptureTaskIntent(
                task_title=text.strip(),
                assignee_name=_resolve_person(text, ctx),
                deadline=_parse_date(text, ctx.today),
            )
        return ClarifyIntent(
            question=(
                "Не понял. Напиши задачу текстом, например:\n"
                "«подготовить бриф по МТС, Андрей задача твоя»"
            )
        )
```

(Keep the existing `ClarifyIntent(...)` final return exactly as it was — shown above for placement.)

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/unit/test_basic_parser.py -v`
Expected: PASS (all, including pre-existing tests — verify none regressed).

- [ ] **Step 5: Commit**

```bash
git add src/planner/infra/llm/basic.py tests/unit/test_basic_parser.py
git commit -m "fix: basic parser handles availability/questions, not bogus tasks"
```

---

## Task 5: regression eval set (opt-in live)

**Files:** Create `tests/eval/test_intent_eval.py`; Modify `pyproject.toml`

**Context:** Live Claude calls cost money and need a network + key, so the eval is **skipped unless** `RUN_LLM_EVAL=1` and `ANTHROPIC_API_KEY` is set. It is the measurable coverage gate the user asked for.

- [ ] **Step 1: Register the marker** — in `pyproject.toml` under `[tool.pytest.ini_options]`, add:

```toml
markers = [
    "live: hits real external APIs (LLM); opt-in via RUN_LLM_EVAL=1",
]
```

- [ ] **Step 2: Create `tests/eval/test_intent_eval.py`**

```python
"""Live regression eval for intent parsing (opt-in).

Run with: RUN_LLM_EVAL=1 uv run pytest tests/eval -v
Skipped by default (no network/key spend in normal CI).
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from planner.infra.llm.ports import ChatContext

pytestmark = pytest.mark.live

_RUN = os.environ.get("RUN_LLM_EVAL") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))

# (phrase, expected_kind) — real commands incl. the reported failures.
CASES = [
    ("Сколько слотов у Раи?", "load"),
    ("Подскажи количество слотов, которые сейчас доступны у Rai.", "load"),
    ("Какое количество специалистов у нас сейчас свободно?", "load"),
    ("Загрузи команду", "load"),
    ("Рай уходит в отпуск с 1 июня по 26 августа", "vacation"),
    ("Оформить бриф по клиенту МТС, задача на Рая", "capture_task"),
    ("создай проект «Альфа», распланируй", "add_project"),
    ("ок", "confirm"),
]

PEOPLE = ("Андрей", "Рай", "Айгуль", "Лёша", "Катя", "Дима")
ALIASES = {"rai": "Рай", "раи": "Рай", "рай": "Рай"}


@pytest.mark.skipif(not _RUN, reason="set RUN_LLM_EVAL=1 and ANTHROPIC_API_KEY")
@pytest.mark.asyncio
@pytest.mark.parametrize("phrase,expected", CASES)
async def test_intent_eval(phrase, expected):
    from planner.infra.llm.claude import ClaudeIntentParser

    parser = ClaudeIntentParser(os.environ["ANTHROPIC_API_KEY"])
    ctx = ChatContext(today=date(2026, 6, 16), known_people=PEOPLE, aliases=ALIASES)
    out = await parser.parse(phrase, ctx)
    assert out.kind == expected, f"{phrase!r} -> {out.kind} (expected {expected})"
```

- [ ] **Step 3: Verify it is skipped by default**

Run: `uv run pytest tests/eval -v`
Expected: all cases SKIPPED (reason mentions `RUN_LLM_EVAL`). No API calls, no spend.

- [ ] **Step 4: Verify it collects with no marker warning**

Run: `uv run pytest tests/eval --collect-only -q`
Expected: collects 8 items, no `PytestUnknownMarkWarning`.

- [ ] **Step 5: Commit**

```bash
git add tests/eval/test_intent_eval.py pyproject.toml
git commit -m "test: add opt-in live intent regression eval"
```

---

## Task 6: full verification

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass (eval skipped). Report counts.

- [ ] **Step 2: Lint + types**

Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 3: Live eval (manual, optional — needs key + network)**

Run: `set -a; . ./.env; set +a; RUN_LLM_EVAL=1 uv run pytest tests/eval -v`
Expected: report pass rate (this is the coverage number). Note any case that fails — that informs further prompt tuning, not a code bug.

---

## Notes
- Task 3 adds two DB reads per message. Acceptable for chat volume; do NOT add caching now (YAGNI).
- Aliases have no DB table — `aliases` stays empty at runtime. Out of scope; the eval passes them only to exercise resolution.
- The merged behavior change: availability questions now answer with load instead of "Не понял команду". Restart the running app after merge to pick it up.
