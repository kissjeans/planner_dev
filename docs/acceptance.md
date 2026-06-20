# Acceptance — Real-World Tests (spec section 16)

Run after Sprint 6 in a real Telegram chat with a test team. Tick each scenario
on first pass (≤ 2 "не понял, переспроси" allowed). A failing scenario becomes a
failing test, gets fixed, and is re-run.

## Scenarios

- [ ] **A. Forward project** — `/task Новый проект "Альфа", шаблон standard, дедлайн 25 июня` →
      bot proposes a plan (who/what/when + overloads) → Confirm.
      _Verify:_ `plan_versions.status='committed'`, audit row with actor = manager.

- [ ] **B. Backward mode** — `/task Новый проект "Бета", шаблон lite, дедлайн НЕ ЗАДАН` →
      bot returns earliest critical-path date, nothing written to DB.

- [ ] **C. What-if** — on a committed project: `/whatif сдвинуть дедлайн Альфы на 30 июня` →
      bot reports moved tasks + overload delta.
      _Verify:_ DB untouched unless the manager confirms.

- [ ] **D. Overload + levers** — create 3 projects on one person → bot says deadline
      unreachable, offers levers (lite / +person / shift) → apply lite → replan.

- [ ] **E. Vacation** — `/vacation Айгуль 10-12 июня` → bot shifts her tasks, nothing drops.
      _Verify:_ `day_overrides` rows added, replan ran, deadline not missed.

- [ ] **F. Voice input** — voice: «Прогони что-если, перенесём дедлайн Альфы на четверг» →
      bot transcribes, interprets, returns the diff.

- [ ] **G. Chat-member rights (R8)** — any participant of the team chat sends
      `/task создать проект Z` → accepted and committable (no read-only tier);
      a stranger outside the team chat is ignored.

- [ ] **H. Daily summary (R7)** — 10:00 Europe/Moscow the bot posts team load +
      overloads to the chat. _Verify:_ APScheduler job fired.

- [ ] **I. Audit** — open `/audit` in the admin → all changes shown with actor + time.

- [ ] **J. Confirm workflow** — bot proposes a plan → manager: «правка: убери #17 из Альфы»
      → bot accumulates edits into a new proposal → «ок» → commit.
      _Verify:_ committed plan matches the edits.

## Cluster C — real-data edits (R1–R8, plan §10)

Anchored on the real presales template (`seed/`) after `alembic upgrade head` +
`python -m seed.load_seed`. R1–R5 are covered by automated tests
(`tests/unit/domain/solver/test_greedy_c2.py`, `tests/unit/test_seed_yaml.py`,
`tests/unit/domain/test_capability.py`); R6–R8 are verified in a live chat.

- [ ] **R1. Feedback lag** — task #18 (Обратная связь) starts on the 5th working
      day after #17 ends (RU calendar). _Auto:_ solver lag + seed dependency.
- [ ] **R2. Required pair** — task #16 (Вычитка с Алисой) is scheduled strictly
      for Тоня + Алиса on the same day; the duration is not shortened.
- [ ] **R3. Priority executor** — the starred priority person is taken first when
      a slot is free; the plan falls through to the next only when they are blocked.
- [ ] **R4. Binding beats skills** — a hard-bound task goes to its bound person
      even when someone else matches the required skills better.
- [ ] **R5. Lite scope** — the lite template omits #5/#9/#11/#12 and drops the
      #16→#12 dependency; planning does not fail.
- [ ] **R6. Notion master card** — creating a project produces a Notion card
      (task checklist + empty «Бриф» + «Идеи»); marking a task done ticks its box.
      Degrades to a no-op when Notion is not configured.
- [ ] **R7. Daily summary** — 10:00 Europe/Moscow load + overloads posted to chat.
- [ ] **R8. Chat-member commit** — any team-chat participant can commit a plan;
      overloads are soft (flagged, never auto-rebalanced).

## Pass criterion

All A–J and R1–R8 pass on the first run. Any failure is captured as a regression
test before re-running.
