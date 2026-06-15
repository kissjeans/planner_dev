# Plan 015: Bound the Anthropic client (timeout, retries) and give explain_plan a fallback

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Open `src/planner/infra/llm/claude.py` and confirm the
> quoted excerpt matches the live code. On any mismatch, treat it as a STOP
> condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug (resilience)
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

`ClaudeIntentParser` creates `AsyncAnthropic(api_key=api_key)` with SDK
defaults — a request timeout of roughly 10 minutes. During an Anthropic outage
every incoming bot message holds a handler for up to that long before the
regex fallback fires; concurrent stuck handlers pile up. Intent parsing is a
chat interaction: if Claude has not answered within ~10 seconds, falling back
to the regex parser is strictly better. Separately, `explain_plan` has **no**
error handling at all — any API error propagates to the caller. Two small
changes: explicit `timeout`/`max_retries` on the client, and a
return-the-input fallback on `explain_plan`.

## Current state

`src/planner/infra/llm/claude.py`:

```python
# claude.py:57
    def __init__(self, api_key: str, fallback: BasicIntentParser | None = None) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._fallback = fallback or BasicIntentParser()
```

```python
# claude.py:77
    async def explain_plan(self, plan_summary: str) -> str:
        resp = await self._client.messages.create(
            model=_MODEL,
            max_tokens=400,
            system=EXPLAIN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": plan_summary}],
        )
        return _extract_text(resp)
```

`parse` (lines 63-75) already has a correct broad try/except → fallback; do
not change it beyond what the client construction provides.

Module has `log = structlog.get_logger(__name__)` (line 23) — reuse it.

Test file: `tests/unit/infra/test_claude_parser.py` — read it first and reuse
its existing mocking style for the Anthropic client (it exists and is the
pattern source; the tests there mock `messages.create`).

## Commands you will need

| Purpose   | Command                                                       | Expected on success |
|-----------|----------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/infra/llm/claude.py --strict`         | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                                  | exit 0              |
| Tests     | `uv run pytest tests/unit/infra/test_claude_parser.py -v`      | all pass            |

## Scope

**In scope**:
- `src/planner/infra/llm/claude.py`
- `tests/unit/infra/test_claude_parser.py`

**Out of scope** (do NOT touch):
- `infra/llm/basic.py`, `prompts.py`, `ports.py`.
- The `parse` method's fallback logic (already correct).
- Model choice (`_MODEL`) and `max_tokens`.

## Git workflow

- Branch: `advisor/015-llm-client-hardening`
- Commit message: `fix(llm): explicit client timeout/retries + explain_plan fallback`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Bound the client

In `claude.py`, add constants near `_MODEL` and use them in `__init__`:

```python
_TIMEOUT_S = 10.0   # chat UX: past this, the regex fallback is better
_MAX_RETRIES = 1
```

```python
        self._client = AsyncAnthropic(
            api_key=api_key, timeout=_TIMEOUT_S, max_retries=_MAX_RETRIES
        )
```

**Verify**: `uv run mypy src/planner/infra/llm/claude.py --strict` → exit 0

### Step 2: explain_plan degrades to the raw summary

```python
    async def explain_plan(self, plan_summary: str) -> str:
        try:
            resp = await self._client.messages.create(
                model=_MODEL,
                max_tokens=400,
                system=EXPLAIN_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": plan_summary}],
            )
            return _extract_text(resp) or plan_summary
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the bot
            log.warning("claude_explain_failed", error=str(exc))
            return plan_summary
```

**Verify**: `uv run ruff check src/planner/infra/llm/claude.py` → exit 0

### Step 3: Tests

In `tests/unit/infra/test_claude_parser.py`, following the file's existing
client-mocking pattern, add:

```python
async def test_client_constructed_with_timeout_and_retries(monkeypatch):
    captured: dict = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)
    from planner.infra.llm.claude import ClaudeIntentParser
    ClaudeIntentParser("key")
    assert captured["timeout"] == 10.0
    assert captured["max_retries"] == 1


async def test_explain_plan_falls_back_to_summary_on_error():
    from planner.infra.llm.claude import ClaudeIntentParser
    p = ClaudeIntentParser("key")
    p._client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("down")))
    )
    out = await p.explain_plan("сводка плана")
    assert out == "сводка плана"
```

(Use the import/helper names the file already has — `SimpleNamespace`,
`AsyncMock` — add imports if absent. Note `from anthropic import AsyncAnthropic`
happens inside `__init__`, so monkeypatching the `anthropic` module attribute
works.)

**Verify**: `uv run pytest tests/unit/infra/test_claude_parser.py -v` → all pass, including 2 new tests

## Test plan

- New: constructor kwargs test (timeout=10.0, max_retries=1); explain_plan
  error → returns input summary unchanged.
- Regression: all existing tests in `test_claude_parser.py` (parse happy path,
  fence stripping, fallback on bad JSON) must pass — `parse` behavior is
  untouched.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/infra/llm/claude.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/infra/test_claude_parser.py -v` exits 0; 2 new tests pass
- [ ] `grep -n "timeout" src/planner/infra/llm/claude.py` shows the explicit client timeout
- [ ] `git status --porcelain` lists only the two in-scope files as modified
- [ ] `plans/README.md` status row for 015 updated

## STOP conditions

Stop and report back if:

- `claude.py` no longer matches the excerpts.
- The installed `anthropic` SDK rejects `timeout`/`max_retries` kwargs
  (very old version) — report the version instead of guessing alternatives.
- Existing tests construct the client in a way that breaks under the new
  kwargs (they should not — construction args are additive).

## Maintenance notes

- 10 s is a UX choice for chat; if Whisper-transcribed long voice messages later
  need bigger budgets, raise `_TIMEOUT_S` deliberately, don't remove it.
- `explain_plan` returning the raw summary means the user sees the mechanical
  plan text instead of prose during outages — acceptable degradation; reviewer
  should confirm no caller treats the summary-echo as an error.
