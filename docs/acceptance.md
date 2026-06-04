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

- [ ] **G. Read-only member** — non-admin sends `/task создать проект Z` → "Только админ
      может править план."; same member `/load` → answered.

- [ ] **H. Daily summary** — 09:30 the bot posts team load + overloads to the chat.
      _Verify:_ APScheduler job fired.

- [ ] **I. Audit** — open `/audit` in the admin → all changes shown with actor + time.

- [ ] **J. Confirm workflow** — bot proposes a plan → manager: «правка: убери #17 из Альфы»
      → bot accumulates edits into a new proposal → «ок» → commit.
      _Verify:_ committed plan matches the edits.

## Pass criterion

All A–J pass on the first run. Any failure is captured as a regression test
before re-running.
