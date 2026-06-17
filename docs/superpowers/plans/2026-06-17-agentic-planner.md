# Agentic Planner — AI layer redesign (tool-use agent)

> Replaces the rigid 8-way intent **classifier** with a Claude **tool-use agent** that reads, reasons over the DB, and acts via tools. The deterministic solver, repo, use-cases, and Notion sink are REUSED as the agent's tools. Grounded in the customer acceptance (PDF `opisanie_proekta_final` §1/§9/§11/§12/§13/§21, `docs/acceptance.md` A–J, `docs/mvp-acceptance-tests.md` MVP-01..13).

**Goal:** the bot understands a full message, decomposes it into the right actions/sequence, decides assignees from DB knowledge (roles/skills/load — no chat-context dependency required), and executes — instead of labelling into one of 8 enums and falling to "не понял".

**Principle kept (PDF §1):** LLM = understanding + orchestration + explanation; **all scheduling math stays in the deterministic solver** (exposed as a tool). Only the agent writes; destructive plan changes go through manager confirmation (§13/§21); every write is audited.

---

## Architecture

`PlannerAgent` runs the Anthropic tool-use loop (SDK 0.105.2 supports `tools=`):

```
messages = [{"role": "user", "content": <message + context block>}]
for _ in range(MAX_ITERS):           # MAX_ITERS = 6
    resp = await client.messages.create(
        model=_MODEL, system=AGENT_SYSTEM_PROMPT, tools=TOOL_SCHEMAS,
        messages=messages, max_tokens=1024, temperature=0,
    )
    if resp.stop_reason != "tool_use":
        return _text(resp)                      # final natural-language reply
    messages.append({"role": "assistant", "content": resp.content})
    results = []
    for b in resp.content:
        if b.type == "tool_use":
            out = await toolbox.execute(b.name, b.input)   # -> str (incl. errors)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
    messages.append({"role": "user", "content": results})
return "Не успел обработать — переформулируй короче."   # iteration cap hit
```

- **Context block** (first user message) embeds: today, roster (name/role/skills/load), known projects, recent chat turns (the existing ChatHistory) — so the agent has DB knowledge + continuity.
- `temperature=0` for determinism. On ANY API error → fall back to the existing `BasicIntentParser` single-intent path (degraded keyword mode) so the bot never dies.
- Without `ANTHROPIC_API_KEY` → the agent is not used at all; the regex fallback path runs (today's behaviour).

## Tools (thin wrappers over EXISTING app use-cases)

`ToolBox` holds `repo`, `solver`, `actor` (PersonRecord|None + is_admin), `actor_record`, `task_sink`, and dispatches `execute(name, input) -> str`. Each tool returns a SHORT human/agent-readable string (incl. a clear error string the model can react to — never raises).

| Tool | Input | Backed by | Gate |
|---|---|---|---|
| `get_team_load` | person_name?, days? | LoadSummaryUseCase data (text summary, per-person used vs capacity in days) | read |
| `find_assignees` | required_skills[] | SuggestAssigneesUseCase | read |
| `list_people` | — | repo.list_people + get_person_capabilities + committed load | read |
| `list_projects` | — | repo.list_projects | read |
| `what_if` | operation, project_title?, new_deadline?, person_name? | WhatIfUseCase | read |
| `capture_task` | title, assignees[]?, project?, deadline?, est_hours?, required_skills[]? | CaptureTaskUseCase (+ Notion mirror) | **admin** |
| `plan_project` | title, template(standard\|lite), deadline? | AddProjectUseCase → PROPOSED plan (returns summary + pv_id; manager confirms) | **admin** |
| `set_vacation` | person, day_from, day_to, capacity_h? | SetVacationUseCase | **admin** |
| `replan` | — | re-solve committed (build_replan_summary) | **admin** |
| `assign_task` | task_ref, person | build_assign_reply (set_task_assignee/reassign_in_plan) | **admin** |
| `confirm_plan` | plan_version_id? | ConfirmPlanUseCase (latest proposed if omitted) | **admin** |

**Guardrails:**
- Write tools check `actor.is_admin`; if not admin → return `"Только админ может менять план."` (agent relays).
- `plan_project` only PROPOSES (writes a `proposed` plan_version + the bot shows ✅/✏️ buttons); committing requires `confirm_plan` or the inline button (§13 manager-confirms).
- `capture_task`/`set_vacation` are direct admin writes (low-friction, matches today). 
- All writes already audited via the use-cases.
- `MAX_ITERS=6` caps tool loops. Per-message agent runs are independent.

## Decomposition (customer §9 — sequence/dependencies)
The agent decomposes a multi-task message by calling `capture_task` once per task (sequence as separate tasks; assignees resolved via `find_assignees` from DB). Full dependency-graph ad-hoc planning (FS/SS between free-form tasks) is **out of this phase** — for structured plans the agent uses `plan_project` (template), which carries deps. (Note this limit; revisit if the customer needs ad-hoc dep graphs.)

## Acceptance mapping (what this delivers)
- A (create project→plan→confirm) → `plan_project` + `confirm_plan`.
- B (backward mode) → `plan_project` deadline omitted (solver +2 buffer).
- C (what-if) → `what_if` (read-only).
- D (overload+levers) → agent runs `what_if(lite/+person/shift)` and offers options (§12).
- E (vacation→replan) → `set_vacation` + `replan`.
- F (voice) → STT unchanged → agent path.
- G (read-only member) → write tools admin-gated; reads open.
- H (daily summary) → unchanged scheduler.
- I (audit) → unchanged.
- J (confirm workflow) → `plan_project` proposes → edit/`confirm_plan`.
- MVP-08 (suggest by skill) → `find_assignees`.
- Compound/conditional/decompose (the reported failures) → native in the loop.

## File structure
| File | Responsibility |
|---|---|
| `src/planner/infra/llm/tools.py` | NEW — TOOL_SCHEMAS (JSON) + `ToolBox` (execute dispatch over use-cases) |
| `src/planner/infra/llm/agent.py` | NEW — `PlannerAgent.run(message, ctx, toolbox) -> AgentReply` (tool-use loop, fallback) |
| `src/planner/infra/llm/prompts.py` | add `AGENT_SYSTEM_PROMPT` (role + rules + confirm/levers/decompose policy) |
| `src/planner/bot/handlers/task_router.py` | NL path: when agent enabled → run agent; keep enum/BasicIntentParser fallback; thread deps + ChatHistory; render plan buttons / load photo as the agent indicates |
| `src/planner/bot/runner.py` | build ToolBox + PlannerAgent, wire into dispatcher |
| `src/planner/settings.py` | `agent_enabled: bool = True` (off → legacy enum path) |
| tests | tool unit tests; agent-loop tests (mocked multi-turn tool-use); live eval on acceptance examples |

---

## Tasks

### Task 1 — ToolBox + tool schemas (TDD)
- `infra/llm/tools.py`: `TOOL_SCHEMAS: list[dict]` (name/description/input_schema per the table) and `class ToolBox` with `__init__(repo, solver, actor, actor_record, task_sink)` + `async def execute(self, name: str, args: dict) -> str`.
- Each executor wraps the existing use-case, formats a short RU string, catches exceptions → `"Ошибка инструмента {name}: {e}"`. Admin gate inside write executors.
- Tests: each tool dispatches to its use-case (mock repo/use-cases) and returns a string; unknown tool → error string; admin gate blocks non-admin writes.

### Task 2 — PlannerAgent loop (TDD)
- `infra/llm/agent.py`: `PlannerAgent(api_key)` + `async def run(self, text, ctx, toolbox) -> AgentReply` where `AgentReply` carries `text` and optional `proposed_pv_id` (so the bot can attach ✅/✏️ buttons). Loop per the architecture; `MAX_ITERS`, `temperature=0`; on API error → `BasicIntentParser` fallback (return its describe text / dispatch). Build the context block from ctx (roster/load/projects/history).
- Tests: mock `client.messages.create` to return a tool_use turn then an end_turn → assert toolbox.execute called with the right name/args and final text returned; iteration cap; API error → fallback path.

### Task 3 — wire into the bot (TDD)
- `runner.py`: build ToolBox (per-update? — actor/repo are request-scoped; build ToolBox inside the handler with the request actor, agent is singleton). Expose `dp["agent"]`.
- `task_router.py _handle_text`: if `agent` present and settings.agent_enabled → build ToolBox(actor/repo/solver/sink) → `reply = await agent.run(text, ctx, toolbox)` → answer; if reply.proposed_pv_id → attach `_plan_keyboard`. Record history. Else (no agent / disabled / no key) → existing parse path. Keep `/load`, `/task`, callbacks working.
- Tests: agent path invoked when enabled; legacy path when disabled; buttons attached on proposed plan.

### Task 4 — live eval + verification
- `tests/eval/test_agent_live.py` (opt-in `RUN_AGENT_EVAL=1`): run the agent against the acceptance example utterances (compound, decompose, assign-by-skill, overload, vacation) with a real repo (testcontainers) + real Claude; assert the expected tools fired / DB state. Skipped by default.
- Full `uv run pytest -q`, `ruff`, `mypy` green.

## Test strategy note
Unit tests mock the Anthropic tool-use turns (the model output is the risk — see this session's 3 mock-masked real bugs), so Task 4's live eval against real Claude + real Postgres is the real gate before shipping.
