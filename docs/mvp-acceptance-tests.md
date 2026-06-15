# MVP Acceptance Tests — Customer-Facing

**Product:** Telegram bot for auto-scheduling a presales team's tasks.
**Purpose:** Verify the MVP against the customer's stated assumptions. Each test maps one
assumption → a concrete pass/fail check the customer can run in a real Telegram chat + admin web.
**Legend:** ✅ verified · ⚠️ verified with defect · ❓ not verifiable this pass (needs live Telegram).
Defect IDs reference `docs/qa-report-2026-06-11.md`.

> Run order: set up a test team (≥3 people, 1 admin) in a Telegram group + the bot, and open
> the admin web. Allow ≤2 "не понял, переспроси" per scenario before marking fail.

---

## Customer assumptions → test matrix

| # | Customer assumption (MVP) | Test ID |
|---|---|---|
| 1 | "I forward a task to the chat and it's saved — no questions asked" | MVP-01 |
| 2 | "I create a project and the bot proposes a full plan (who/what/when)" | MVP-02 |
| 3 | "If I give no deadline, it tells me the earliest possible date" | MVP-03 |
| 4 | "I confirm or edit the proposed plan with a button" | MVP-04 |
| 5 | "I can see team load at a glance" | MVP-05 |
| 6 | "I add a vacation and the bot reshuffles tasks" | MVP-06 |
| 7 | "I can simulate a change before committing it" | MVP-07 |
| 8 | "The bot suggests who should take a task by skill" | MVP-08 |
| 9 | "Only managers can change plans; everyone can read" | MVP-09 |
| 10 | "There's an admin screen with plans, team, and an audit trail" | MVP-10 |
| 11 | "The bot posts a daily load summary" | MVP-11 |
| 12 | "I can talk to it with voice" | MVP-12 |
| 13 | "I log into the admin with my Telegram account" | MVP-13 |

---

## MVP-01 — Capture task from chat
- **Precond:** admin or team member in chat with the bot.
- **Steps:** send `/task подготовить бриф по МТС, Андрей задача твоя, к 20 июня`.
- **Expected:** "✓ Записал" with task / project (МТС) / assignee (Андрей) / deadline. Row in `tasks`, audit `capture_task`.
- **Status ⚠️** — Capture works, but: any sender (not just team) can write (**H2**); a date-like token such as "32.13" crashes the regex fallback (**M3**); no length cap on title (**M8**).

## MVP-02 — Create project + proposed plan
- **Steps:** `/task Новый проект "Альфа", шаблон standard, дедлайн 25 июня`.
- **Expected:** bot replies with a plan summary (who/what/when + overloads) and ✅Подтвердить / ✏️Правка buttons. Nothing committed yet.
- **Status ❓** — Requires live Telegram + seeded template/people to verify solver output. Logic path present.

## MVP-03 — Backward mode (no deadline)
- **Steps:** `/task Новый проект "Бета", шаблон lite` (no deadline).
- **Expected:** earliest critical-path end date returned; **DB untouched**.
- **Status ❓** — Needs live solver run; not verified this pass.

## MVP-04 — Confirm / edit workflow
- **Steps:** on a proposal, tap ✅Подтвердить (admin); separately tap ✏️Правка → type an edit → "ок".
- **Expected:** Confirm → `plan_versions.status='committed'` + audit. Edit → accumulates edits into a new proposal until "ок".
- **Status ⚠️** — Confirm path present (admin-gated). **Edit loop is broken (M7):** edit isn't tied to the original plan and the state clears after one message; typed "ок" in text is a no-op (**L4**). Double-tap Confirm has a race (**M5**).

## MVP-05 — Team load
- **Steps:** `/load` and `/load Андрей`.
- **Expected:** 14-day load heatmap PNG (or "нет активных людей").
- **Status ❓** — Image build path present; live render not exercised.

## MVP-06 — Vacation reshuffle
- **Steps:** `/vacation Айгуль 10-12 июня`.
- **Expected:** `day_overrides` rows added, tasks shift, deadline not missed, confirmation message.
- **Status ⚠️** — Bot path handles "person not found" correctly. **Web `/team/vacation` silently reports success for a nonexistent person (M2)** and 500s on a bad date (**M1**).

## MVP-07 — What-if simulation
- **Steps:** `/whatif сдвинуть дедлайн Альфы на 30 июня`.
- **Expected:** reports moved tasks + overload delta; **DB untouched** until confirm.
- **Status ❓** — Needs live solver; "удали проект «X»" via `/task` misclassifies as create (**M4**) — verify what-if drop wording.

## MVP-08 — Suggest assignee by skill
- **Steps:** `/suggest Копирайтинг, Редактура`.
- **Expected:** ranked people by skill coverage + current load; read-only (assigns no one).
- **Status ❓** — Handler + use-case present; needs seeded roles/skills to verify ranking.

## MVP-09 — Role gate (write = admin, read = open)
- **Steps:** non-admin sends `/task создать проект Z`; same member sends `/load`.
- **Expected:** write → "Только админ может править план."; `/load` → answered.
- **Status ⚠️** — Gate works for `add_project`/`what_if`/`vacation`/`confirm`/`assign`. **But `capture_task` is NOT gated (H2)** — non-admin can still create projects/tasks via a plain task message.

## MVP-10 — Admin web (plans / team / audit)
- **Steps:** open `/plan`, `/team`, `/audit`, `/schedule`, `/calendar`, `/load-board`.
- **Expected:** authenticated admin sees data; audit shows every change with actor + time.
- **Status ⚠️** — Pages render. **Auth is bypassable (C1 `/dev-login`, C2 forged JWT).** Audit attribution is unreliable: reassign actor = None, web actor = random UUID (**L5**). Bad paging input 500s (**M1**).

## MVP-11 — Daily summary
- **Expected:** at the scheduled time the bot posts team load + overloads to `TEAM_CHAT_ID`.
- **Status ❓** — APScheduler job registered; firing + failure handling not verified.

## MVP-12 — Voice input
- **Steps:** send a voice message replying to the bot.
- **Expected:** transcribe → interpret → act.
- **Status ⚠️** — **Disabled in this build** (no OpenAI key) → "Голосовые сообщения не поддерживаются". When enabled, note unbounded download + `assert` control-flow (**L6**).

## MVP-13 — Telegram login to admin
- **Steps:** open `/login`, authenticate via Telegram Login Widget.
- **Expected:** valid signature → admin session; invalid → 401.
- **Status ⚠️** — Signature verification + JWT cookie present and correct. **Undermined by C1/C2** (login can be skipped entirely); cookie missing `Secure` (**L2**).

---

## MVP exit criteria (recommended)

**Must pass before customer demo / pilot:**
- MVP-01, 02, 04 (confirm), 05, 09 functional in a live chat.
- **Blockers cleared:** C1, C2 (admin bypass) and H1 (throttle + allowlist) — without these the MVP is not safe to expose.

**Should pass:**
- MVP-03, 06, 07, 10, 13; fix M1, M2, M7.

**Nice-to-have for MVP:**
- MVP-08, 11, 12.

## Regression hook
Each failing scenario above is reproduced as an automated test in `tests/` before its fix is
merged, then re-run here. Tie each MVP-ID to a test module so the matrix stays live.
</content>
