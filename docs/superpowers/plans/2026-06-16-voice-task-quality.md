# Voice→Task Quality Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Cut STT errors on domain jargon + team names, and let a single captured task carry **multiple assignees** ("Андрея и Рай").

**Architecture:** Two independent changes. (A) `FasterWhisperSTT` passes an `initial_prompt` (team names + domain glossary) to bias the decoder. (B) `CaptureTaskIntent.assignee_name: str` becomes `assignee_names: list[str]`, and `CaptureTaskUseCase` assigns each resolved person (the `assignments` table has a composite PK so multiple assignees = multiple rows). **Only the capture path changes — the admin-board `TaskMeta.assignee_name` is a different field and must NOT be touched.**

**Tech Stack:** Python 3.12, faster-whisper, pydantic, aiogram, pytest.

## Scope guard (do NOT touch)
These `assignee_name` references are the admin board, unrelated to this work — leave them exactly as they are:
`src/planner/app/ports.py:90`, `src/planner/app/admin_board.py`, `src/planner/infra/db/repo.py:344`, `src/planner/web/templates/schedule.html`, `tests/unit/app/test_admin_board.py`, `tests/integration/test_repo_full.py`, `tests/unit/web/test_web_e2e.py`.

---

## Task 1: STT initial_prompt (bias decoder toward names + jargon)

**Files:** Modify `src/planner/infra/stt/faster_whisper.py`; Test `tests/unit/test_faster_whisper_stt.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_faster_whisper_stt.py`:

```python
@pytest.mark.asyncio
async def test_transcribe_passes_initial_prompt(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT, _INITIAL_PROMPT

    _cls, model = mock_model_cls
    stt = FasterWhisperSTT()
    await stt.transcribe(b"data")
    kwargs = model.transcribe.call_args.kwargs
    assert kwargs["initial_prompt"] == _INITIAL_PROMPT
    assert "Рай" in _INITIAL_PROMPT and "бриф" in _INITIAL_PROMPT
```

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/unit/test_faster_whisper_stt.py::test_transcribe_passes_initial_prompt -v`
Expected: FAIL — cannot import `_INITIAL_PROMPT` / no `initial_prompt` kwarg.

- [ ] **Step 3: Implement** — in `src/planner/infra/stt/faster_whisper.py`:

Add after `_MODEL_SIZE = "small"`:

```python
# Biases the decoder toward team names and presales jargon the base model
# otherwise mangles ("ресёрч"→"ресурс", "Рай"→"Ирай"). Tune as the team changes.
_INITIAL_PROMPT = (
    "Планирование задач команды пресейла. "
    "Имена: Андрей, Рай, Айгуль, Лёша, Катя, Дима. "
    "Термины: бриф, ресёрч, КП, дедлайн, оффер, лид, пресейл, МТС, Мегафон."
)
```

Change `__init__` to accept it and store it:

```python
    def __init__(
        self, model_size: str = _MODEL_SIZE, initial_prompt: str | None = _INITIAL_PROMPT
    ) -> None:
        self._model_size = model_size
        self._initial_prompt = initial_prompt
        self._model: Any = None  # lazy-loaded on first transcribe
```

Pass it in `_transcribe_sync`:

```python
    def _transcribe_sync(self, audio: bytes) -> str:
        model = self._load_model()
        segments, _info = model.transcribe(
            io.BytesIO(audio), language="ru", initial_prompt=self._initial_prompt
        )
        return "".join(segment.text for segment in segments).strip()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/unit/test_faster_whisper_stt.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/planner/infra/stt/faster_whisper.py tests/unit/test_faster_whisper_stt.py
git commit -m "feat: bias faster-whisper with team names + jargon glossary"
```

---

## Task 2: multi-assignee — intent schema

**Files:** Modify `src/planner/domain/intent.py`; Test `tests/unit/test_intent.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_intent.py`:

```python
def test_capture_task_accepts_multiple_assignees():
    from planner.domain.intent import CaptureTaskIntent

    i = CaptureTaskIntent(task_title="ресёрч по МТС", assignee_names=["Андрей", "Рай"])
    assert i.assignee_names == ["Андрей", "Рай"]


def test_capture_task_assignees_default_empty():
    from planner.domain.intent import CaptureTaskIntent

    i = CaptureTaskIntent(task_title="бриф")
    assert i.assignee_names == []
```

If `tests/unit/test_intent.py` has an existing test that builds `CaptureTaskIntent(assignee_name=...)` or asserts `.assignee_name`, update it to `assignee_names=[...]` / `.assignee_names` in this same step.

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/unit/test_intent.py -v`
Expected: new tests FAIL — `assignee_names` is not a field.

- [ ] **Step 3: Implement** — in `src/planner/domain/intent.py`, replace the `assignee_name` line in `CaptureTaskIntent` (currently `assignee_name: str | None = Field(default=None, max_length=200)`) with:

```python
    assignee_names: list[str] = Field(default_factory=list, max_length=10)
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/unit/test_intent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/planner/domain/intent.py tests/unit/test_intent.py
git commit -m "feat: CaptureTaskIntent supports multiple assignees"
```

---

## Task 3: multi-assignee — capture use case

**Files:** Modify `src/planner/app/capture_task.py`; Test `tests/unit/app/test_capture_task.py`

**Context:** `CaptureResult.assignee_name: str | None` → `assignee_names: list[str]`. `execute()` loops over `intent.assignee_names`, resolves each via `repo.get_person_by_name`, assigns each via `repo.assign_task`. The `assignments` table keys on `(task_id, person_id)`, so multiple `assign_task` calls create multiple assignees.

- [ ] **Step 1: Update the failing tests** — in `tests/unit/app/test_capture_task.py`, every place that builds `CaptureTaskIntent(assignee_name="X")` becomes `assignee_names=["X"]`, and every assertion on `result.assignee_name` becomes the list form. Add one new test (match the file's existing fixture/repo-fake style):

```python
@pytest.mark.asyncio
async def test_capture_assigns_multiple_people(<existing fixtures>):
    # builds CaptureTaskIntent(task_title="ресёрч по МТС",
    #   assignee_names=["Андрей", "Рай"]) and a repo whose get_person_by_name
    # resolves both; asserts result.assignee_names == ["Андрей", "Рай"]
    # and that repo.assign_task was awaited twice.
    ...
```

(Use the existing repo fake/mocks in this file. The intent is: two resolved names → two `assign_task` calls → `result.assignee_names == ["Андрей", "Рай"]`.)

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/unit/app/test_capture_task.py -v`
Expected: FAIL (field/attribute errors + the new multi test).

- [ ] **Step 3: Implement** — in `src/planner/app/capture_task.py`:

(a) `CaptureResult`:

```python
@dataclass(frozen=True)
class CaptureResult:
    task_title: str
    project_title: str
    assignee_names: list[str]
    deadline_iso: str | None
```

(b) In `execute()`, replace the single-assignee block and the return/audit:

```python
        assignee_names: list[str] = []
        for name in intent.assignee_names:
            person = await self._repo.get_person_by_name(name)
            if person is not None:
                await self._repo.assign_task(task.id, person.id, _CAPTURE_HOURS)
                assignee_names.append(person.name)

        await self._repo.add_audit(
            actor.id if actor else None,
            "capture_task",
            "task",
            task.id,
            {
                "title": intent.task_title,
                "project": project.title,
                "assignees": assignee_names,
            },
        )
        return CaptureResult(
            task_title=intent.task_title,
            project_title=project.title,
            assignee_names=assignee_names,
            deadline_iso=intent.deadline.isoformat() if intent.deadline else None,
        )
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/unit/app/test_capture_task.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/planner/app/capture_task.py tests/unit/app/test_capture_task.py
git commit -m "feat: capture task assigns multiple people"
```

---

## Task 4: multi-assignee — bot replies + regex parser + prompt

**Files:** Modify `src/planner/bot/handlers/task_router.py`, `src/planner/infra/llm/basic.py`, `src/planner/infra/llm/prompts.py`; Tests `tests/unit/bot/test_handler_coverage.py`, `tests/unit/test_basic_parser.py`

- [ ] **Step 1: Update/add failing tests**

In `tests/unit/test_basic_parser.py`: any assertion on `.assignee_name` for a captured task → `.assignee_names`. Add:

```python
@pytest.mark.asyncio
async def test_basic_capture_assignee_is_list():
    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    from datetime import date
    ctx = ChatContext(today=date(2026, 6, 5), known_people=("Андрей",))
    out = await BasicIntentParser().parse("подготовить бриф, Андрей задача твоя", ctx)
    assert out.kind == "capture_task"
    assert out.assignee_names == ["Андрей"]
```

In `tests/unit/bot/test_handler_coverage.py`: update any `CaptureTaskIntent(assignee_name=...)` build or `.assignee_name` assertion to the list form.

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/unit/test_basic_parser.py tests/unit/bot/test_handler_coverage.py -v`
Expected: FAIL on the updated/new assertions.

- [ ] **Step 3: Implement**

(a) `src/planner/infra/llm/basic.py` — the capture_task default (currently sets `assignee_name=_resolve_person(text, ctx)`):

```python
        if low:
            person = _resolve_person(text, ctx)
            return CaptureTaskIntent(
                task_title=text.strip(),
                assignee_names=[person] if person else [],
                deadline=_parse_date(text, ctx.today),
            )
```

(b) `src/planner/bot/handlers/task_router.py`:

- In `build_capture_reply` (line ~117), change the кому line:

```python
        f"  кому: {', '.join(result.assignee_names) or '—'}",
```

- In `describe_intent` for `CaptureTaskIntent` (line ~129):

```python
    if isinstance(intent, CaptureTaskIntent):
        who = ", ".join(intent.assignee_names) or "не назначено"
        return f"Задача: {intent.task_title} (кому: {who})."
```

(c) `src/planner/infra/llm/prompts.py` — in the `capture_task` bullet, replace `assignee_name (кому — если назван, резолвь алиасы)` with:

```
assignee_names (кому — СПИСОК имён; один или несколько, напр.
  «поставь Андрея и Рай» → ["Андрей","Рай"]; резолвь алиасы; пусто = [])
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/unit/test_basic_parser.py tests/unit/bot/test_handler_coverage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/planner/bot/handlers/task_router.py src/planner/infra/llm/basic.py src/planner/infra/llm/prompts.py tests/unit/test_basic_parser.py tests/unit/bot/test_handler_coverage.py
git commit -m "feat: render and parse multiple assignees in capture flow"
```

---

## Task 5: full verification

- [ ] **Step 1: Full suite** — `uv run pytest -q` → all pass (eval skipped). Report counts.
- [ ] **Step 2: Lint + types** — `uv run ruff check src tests && uv run mypy src` → clean.
- [ ] **Step 3: Orphan grep** — `grep -rn "assignee_name\b" src | grep -v assignee_names` → should show ONLY the admin-board references listed in the Scope guard (ports.py:90, admin_board.py, repo.py:344). If any capture-path reference remains, fix it.

---

## Notes
- `_INITIAL_PROMPT` hardcodes the current seed roster — it is a tunable constant, not dynamic. Acceptable for now (YAGNI on per-request roster injection).
- STT will still not be 100% on jargon/names — `initial_prompt` reduces errors, does not eliminate them. A future option is bumping `small`→`medium`.
- After merge, the running bot must be restarted to pick up all changes.
