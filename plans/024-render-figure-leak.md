# Plan 024: matplotlib figures are always closed, even when a render raises

> **Executor instructions**: Follow step by step; run every verification before
> moving on. On a "STOP condition", stop and report. Update this plan's row in
> `plans/README.md` when done.
>
> **Drift check (run first)**: working tree was dirty at authoring time. Open
> `src/planner/app/render/gantt.py` and `heatmap.py` and confirm the quoted lines
> match before editing. On a mismatch, STOP.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: perf
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

Both PNG renderers create a figure with `plt.subplots(...)` and call
`plt.close(fig)` only on the success path — there is no `try/finally`. `plt.subplots`
registers the figure in pyplot's process-global figure manager (`Gcf`). If any
call between creation and close raises (`savefig`, `ax.text`, `set_yticklabels`
on bad data), `plt.close(fig)` never runs and the figure is retained for the life
of the process. This is a long-lived single process serving repeated renders from
the bot, the web admin, and the daily-summary job, so repeated failures
accumulate figures (and their memory) unboundedly.

## Current state

```python
# app/render/gantt.py:24-42
fig, ax = plt.subplots(figsize=(8, max(2, len(assignments) * 0.45)))
for i, a in enumerate(assignments):
    ...
    ax.text(...)
ax.set_yticks(...); ax.set_yticklabels(...); ax.invert_yaxis(); ax.set_xlabel(...)
fig.tight_layout()
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=110)
plt.close(fig)                       # <-- only runs if nothing above raised
return buf.getvalue()
```

```python
# app/render/heatmap.py:22-38
fig, ax = plt.subplots(figsize=(...))
img = ax.imshow(...)
... ax.set_xticklabels(...); ax.text(...); fig.colorbar(...)
fig.tight_layout()
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=110)
plt.close(fig)                       # <-- same pattern
return buf.getvalue()
```

Both already set the Agg backend (`matplotlib.use("Agg")`) so headless rendering
is fine. Existing tests `tests/unit/app/test_render.py` assert PNG magic bytes on
valid inputs only — no exception-path coverage.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Tests   | `uv run pytest tests/unit/app/test_render.py` | all pass |
| Types   | `mypy src/planner --strict` | exit 0 |
| Lint    | `ruff check .` | exit 0 |

## Scope

**In scope:**
- `src/planner/app/render/gantt.py`
- `src/planner/app/render/heatmap.py`
- `tests/unit/app/test_render.py`

**Out of scope:**
- A full rewrite to the matplotlib OO API (`Figure` + `FigureCanvasAgg`) — that's
  the better long-term pattern but a larger change; note it in Maintenance, don't
  do it here.
- Callers of the renderers (`bot/handlers/load.py`, `app/load_summary.py`).

## Git workflow

- Branch: `advisor/024-render-figure-leak`
- Conventional commits (e.g. `fix(render): close matplotlib figure in finally to stop leak on error`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Wrap each renderer's body in try/finally

In **both** `gantt.py` and `heatmap.py`, restructure so the figure is created,
then everything else runs in a `try` whose `finally` closes the figure:

```python
fig, ax = plt.subplots(figsize=(...))
try:
    ...  # all drawing + savefig
    return buf.getvalue()
finally:
    plt.close(fig)
```

Keep behavior on the happy path byte-for-byte identical (same draw calls, same
`savefig` args). Only the close placement changes.

**Verify**: `uv run pytest tests/unit/app/test_render.py` → all pass; `mypy src/planner --strict` → exit 0.

### Step 2: Add an exception-path test asserting no leak

In `tests/unit/app/test_render.py`, add a test that forces a render to raise
after the figure is created (e.g. monkeypatch `fig.savefig` to raise, or feed an
input that makes a draw call raise) and asserts:
- the exception propagates (the renderers don't swallow it), and
- no figures leak: `import matplotlib.pyplot as plt; assert plt.get_fignums() == []`
  after the call.

Capture the baseline (`plt.get_fignums()` empty) at test start to avoid
cross-test contamination.

**Verify**: `uv run pytest tests/unit/app/test_render.py` → all pass, new case included.

## Test plan

- New leak test per Step 2; existing PNG-magic tests unchanged.
- Verification: `uv run pytest tests/unit/app/test_render.py` → all pass.

## Done criteria

ALL must hold:

- [ ] Both renderers close the figure in a `finally` block.
- [ ] A test forces a render error and asserts `plt.get_fignums()` is empty afterward.
- [ ] `uv run pytest tests/unit/app/test_render.py` → exit 0 with the new case.
- [ ] `ruff check .` → exit 0; `mypy src/planner --strict` → exit 0.
- [ ] No files outside the in-scope list modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

- The leak test can't reliably assert on `plt.get_fignums()` due to test
  parallelism/ordering — make it robust (snapshot before/after) or report.

## Maintenance notes

- Recommended follow-up (separate plan): migrate both renderers to the OO API
  (`from matplotlib.figure import Figure; fig = Figure(...); FigureCanvasAgg(fig)`),
  which avoids the global `Gcf` registry entirely and is the documented pattern
  for server-side rendering — no `plt.close` needed at all, and no shared global
  state across the bot/web/scheduler call sites.
- Reviewer: confirm the happy-path PNG output is unchanged.
