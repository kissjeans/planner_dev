# planner_dev — Implementation Spec & Plan

**Дата:** 2026-06-04
**Автор:** Claude (Opus 4.7)
**Источники:** `opisanie_proekta_final.pdf` (6 стр., ТЗ), `donor-repos-research.md.pdf` (10 стр., каталог OSS), `planner_bot_full_flow.html` (17-шаговый flow).

---

## 0. Context

Команда пресейла агентства (6 человек) ведёт несколько проектов параллельно и сейчас распределяет задачи вручную. Нужен «виртуальный сотрудник»: Telegram-бот, который автоматически расставляет задачи по людям и дням с учётом capacity, зависимостей и дедлайнов, сигнализирует о перегрузах и о недостижимых сроках.

Эта спека — мост между ТЗ и реализацией. Цель — описать систему настолько подробно, чтобы её можно было разложить на bite-sized задачи и поручить ИИ-агентам, при этом сохранив прослеживаемость к ТЗ.

### Принципы проектирования (фильтр всех решений)

| Принцип | Как применён здесь |
|---|---|
| Requirements First | Каждая фича прокинута к пункту ТЗ; всё, что не из ТЗ, отброшено. |
| KISS | Один процесс, один деплой, никаких микросервисов. |
| YAGNI | OR-Tools/v2-солвер, multi-tenancy, ось сложности — вне MVP. |
| Boring Tech | Python 3.12, Postgres 16, Redis 7, aiogram 3, SQLAlchemy 2. |
| SOLID | Solver/LLM/IO разделены интерфейсами (`SolverPort`, `IntentParserPort`, `CalendarPort`). |
| DRY | Шаблоны задач и привязки исполнителей живут в одной таблице seed-data. |
| SoC | 4 слоя: `bot` (Telegram) ↔ `app` (use-cases) ↔ `domain` (solver/calendar/audit) ↔ `infra` (DB/LLM/STT/calendar API). |
| LoD | Хендлеры дёргают только сервисы; сервисы не лезут в SQLAlchemy-сессии хендлеров. |
| Optimize for Change | `SolverPort` интерфейс позволит заменить жадный алгоритм на PyJobShop без переписывания вызывающего кода. |
| Incremental | 6 спринтов, каждый завершается работающим срезом и тестами. |
| Convention | Используем структуру `wakaree/aiogram_bot_template` как референс. |
| Cost Awareness | Whisper API ~$0.006/мин, Claude Haiku 4.5 для NL→intent (90% качества Sonnet за 1/3 цены), Postgres+Redis на dev-машине = $0. |
| Explicit Trade-Off | Жадный алгоритм не оптимален → раздел 14 («Что осознанно отложено»). |

### Зафиксированные решения (ответы пользователя)

| Развилка | Выбор | Обоснование |
|---|---|---|
| Деплой | Локально на машине разработчика (Docker Compose) | KISS на старте, прод откладывается. |
| STT | OpenAI Whisper API | Нулевая инфра, $0.006/мин, ru ок. |
| Жёсткое редактирование расписания | Web-админка FastAPI + HTMX в одном процессе с ботом | Делим БД/модели/солвер, никаких фронт-фреймворков. |
| Тенантность | Single-tenant: одна команда, один Telegram-чат | YAGNI на максимум. |

---

## 1. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         Telegram chat                            │
│              (команда пишет /task, голос, кнопки)               │
└──────────────┬───────────────────────────────────┬───────────────┘
               │ updates (long-poll)              │ replies/PNG
               ▼                                   │
┌──────────────────────────────────────────────────┴───────────────┐
│                    planner_dev (single Python process)           │
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │  aiogram 3  │◄──►│  app / use- │◄──►│  domain (solver,    │  │
│  │  + dialog   │    │  cases      │    │  calendar, audit)   │  │
│  │  (bot/)     │    │  (app/)     │    │  (domain/)          │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
│                              ▲                  ▲                │
│                              │                  │                │
│  ┌─────────────┐             │                  │                │
│  │  FastAPI    │─────────────┘                  │                │
│  │  + HTMX     │  (web-админка, тот же app/)    │                │
│  │  (web/)     │                                 │                │
│  └─────────────┘                                 │                │
│                              ┌──────────────────┴──────────────┐ │
│                              │           infra/                │ │
│                              │  ┌──────┐ ┌──────┐ ┌──────────┐ │ │
│                              │  │  DB  │ │ LLM  │ │ Calendar │ │ │
│                              │  │ (SA) │ │(Claud│ │(isdayoff)│ │ │
│                              │  └──────┘ └──────┘ └──────────┘ │ │
│                              │  ┌──────┐ ┌────────────────────┐│ │
│                              │  │ STT  │ │     APScheduler    ││ │
│                              │  │(Whis)│ │   (daily summary)  ││ │
│                              │  └──────┘ └────────────────────┘│ │
│                              └─────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
        │                                              │
        ▼                                              ▼
┌────────────────┐                          ┌─────────────────────┐
│  Postgres 16   │                          │      Redis 7        │
│  (state, audit)│                          │  (FSM, cache)       │
└────────────────┘                          └─────────────────────┘
```

**Ключевые свойства:**
- Один процесс = одна точка записи в БД (раздел 17 ТЗ).
- Бот и админка делят один и тот же application layer → DRY.
- Все внешние интеграции спрятаны за портами (адаптеры в `infra/`) → swappable.

---

## 2. Tech Stack (обоснование каждого выбора)

| Слой | Технология | Версия | Почему |
|---|---|---|---|
| Язык | Python | 3.12 | Один runtime для бота, солвера, веб; стандарт всех доноров. |
| Bot framework | aiogram | 3.14+ | async, FSM, middlewares; русское сообщество; раздел 15 ТЗ. |
| Bot UI | aiogram-dialog | latest | Calendar / Multiselect / Counter / подтверждения out-of-the-box. |
| Web | FastAPI + Jinja2 + htmx | latest | Минимум JS, server-rendered, одна Python-кодовая база. |
| ORM | SQLAlchemy | 2.x (async) | mainstream + sqlalchemy-history совместима. |
| Migrations | Alembic | latest | Стандарт SA. |
| DB | Postgres | 16 | JSONB для интентов, отличный аудит, sqlalchemy-history. |
| Cache/FSM | Redis | 7 | aiogram FSM storage, кеш интентов. |
| Solver MVP | NetworkX | 3.x | topo_sort + dag_longest_path + descendants; раздел 9 ТЗ. |
| Audit | sqlalchemy-history | latest | Журнал «кто/что/когда» + откат; разделы 14, 17 ТЗ. |
| LLM (intent) | Claude Haiku 4.5 via Anthropic SDK + instructor | latest | Pydantic schema, ретраи, mid-cost; раздел 1 ТЗ. |
| STT | OpenAI Whisper API | latest | $0.006/мин, ru ок. |
| Calendar | isdayoff.ru REST + d10xa/holidays-calendar JSON snapshot | live API | Раздел 5 ТЗ; snapshot — оффлайн-fallback. |
| Scheduler | APScheduler | 4.x | Дневная сводка перегрузов. |
| Charts | matplotlib + seaborn | latest | PNG в чат, без JS. |
| HTTP client | httpx | latest | async, isdayoff + Whisper. |
| Validation | Pydantic | 2.x | intent schemas, settings. |
| Logging | structlog | latest | JSON-логи. |
| Tests | pytest + pytest-asyncio + testcontainers + aiogram.test_utils | latest | Реальный Postgres в тестах. |
| Lint/format | ruff + mypy | latest | One-tool linter. |
| Packaging | uv + pyproject.toml | latest | Совпадает со skeleton. |
| Process | Docker Compose | latest | Локальный запуск. |

**Намеренно отброшено (YAGNI):**
- OR-Tools / PyJobShop — раздел 18 ТЗ, фаза v2.
- Celery — APScheduler хватает на дневную сводку.
- React / Vue — htmx закрывает админку.
- Sentry — на dev-машине лог-файлы достаточно.
- Multi-org schema — single-tenant.

---

## 3. Folder Structure

```
planner_dev/
├── docker-compose.yml          # postgres + redis + app
├── Dockerfile
├── pyproject.toml              # uv-managed deps
├── alembic.ini
├── .env.example
├── README.md
├── CLAUDE.md                   # уже есть
│
├── alembic/
│   └── versions/               # автогенерируемые миграции
│
├── seed/
│   ├── team.yaml               # 6 человек + capacity
│   ├── tasks_standard.yaml     # 20 задач шаблона standard
│   ├── tasks_lite.yaml         # задачи lite (раздел 6 ТЗ)
│   └── load_seed.py            # CLI: python -m seed.load
│
├── src/planner/
│   ├── __init__.py
│   ├── settings.py             # Pydantic Settings, читает .env
│   ├── main.py                 # точка входа: бот + FastAPI + scheduler
│   │
│   ├── domain/                 # чистая бизнес-логика, без IO
│   │   ├── __init__.py
│   │   ├── models.py           # dataclasses: Task, Project, Person, Assignment, Plan
│   │   ├── solver/
│   │   │   ├── __init__.py
│   │   │   ├── ports.py        # SolverPort (interface)
│   │   │   ├── greedy.py       # NetworkX-based реализация
│   │   │   ├── critical_path.py# обратный режим
│   │   │   └── diff.py         # «что-если» дифф через descendants
│   │   ├── calendar/
│   │   │   ├── ports.py        # CalendarPort
│   │   │   └── rules.py        # working_day, business_days_between
│   │   ├── permissions.py      # роли: admin / member
│   │   └── intent.py           # Pydantic-схема Intent (раздел 5 flow)
│   │
│   ├── app/                    # use-cases (orchestration)
│   │   ├── __init__.py
│   │   ├── add_project.py      # шаг 9 flow
│   │   ├── replan.py           # шаги 11–12
│   │   ├── what_if.py          # раздел 14 ТЗ
│   │   ├── confirm_plan.py     # шаги 14–16
│   │   ├── load_summary.py     # хитмап PNG
│   │   ├── set_vacation.py     # раздел 5 ТЗ
│   │   └── render/             # PNG renderers (изолированы от бота)
│   │       ├── gantt.py
│   │       └── heatmap.py
│   │
│   ├── infra/                  # внешние адаптеры
│   │   ├── db/
│   │   │   ├── base.py         # Base, async session factory
│   │   │   ├── models.py       # SQLAlchemy ORM
│   │   │   ├── repo.py         # CRUD (один writer — agent)
│   │   │   └── audit.py        # sqlalchemy-history wiring
│   │   ├── llm/
│   │   │   ├── claude.py       # Anthropic SDK + instructor
│   │   │   └── prompts.py
│   │   ├── stt/
│   │   │   └── whisper.py      # OpenAI Whisper API
│   │   ├── calendar/
│   │   │   ├── isdayoff.py     # live REST
│   │   │   └── snapshot.py     # d10xa JSON fallback
│   │   └── scheduler.py        # APScheduler config
│   │
│   ├── bot/                    # Telegram I/O
│   │   ├── __init__.py
│   │   ├── runner.py           # Dispatcher, middlewares
│   │   ├── middlewares/
│   │   │   ├── permissions.py  # admin gate (раздел 16 ТЗ)
│   │   │   └── logging.py
│   │   ├── handlers/
│   │   │   ├── start.py
│   │   │   ├── task_router.py  # /task, @mention
│   │   │   ├── load.py         # /load
│   │   │   ├── whatif.py       # /whatif
│   │   │   ├── confirm.py      # callback confirm/edit
│   │   │   └── vacation.py     # /vacation
│   │   ├── dialogs/            # aiogram-dialog
│   │   │   ├── add_project.py
│   │   │   ├── confirm_plan.py
│   │   │   └── vacation.py
│   │   └── replies/            # human-readable formatters
│   │       └── plan_explainer.py
│   │
│   └── web/                    # FastAPI админка
│       ├── __init__.py
│       ├── app.py              # FastAPI() instance
│       ├── auth.py             # Telegram Login Widget
│       ├── routes/
│       │   ├── plan.py         # /plan, /plan/edit
│       │   ├── team.py         # /team
│       │   └── audit.py        # /audit
│       └── templates/          # Jinja2 + htmx
│           ├── base.html
│           ├── plan.html
│           └── team.html
│
├── tests/
│   ├── conftest.py             # testcontainers Postgres, fake LLM/STT
│   ├── unit/
│   │   ├── domain/             # solver, calendar, intent — без IO
│   │   ├── app/                # use-cases с фейковыми портами
│   │   └── infra/              # mappers, audit
│   ├── integration/
│   │   ├── test_db.py
│   │   ├── test_calendar_isdayoff.py  # vcrpy кассеты
│   │   └── test_llm_intent.py         # vcrpy кассеты
│   └── e2e/
│       ├── test_add_project_flow.py
│       ├── test_what_if_flow.py
│       └── test_vacation_flow.py
│
└── docs/
    ├── architecture.md         # эта спека
    ├── data_model.md           # ER + migration story
    ├── intent_schema.md
    └── acceptance.md
```

**Правило размера файла:** ≤ 400 строк. Если разрастается — сплит по ответственности.

---

## 4. Domain Model (структура БД)

### 4.1 Таблицы (Postgres)

```sql
-- Люди
people (
  id            uuid pk,
  tg_user_id    bigint unique nullable,
  name          text not null,        -- "Андрей", "Рай", …
  role_label    text,                 -- "Менеджер по продажам"
  capacity_h    int not null default 8,
  is_admin      bool not null default false,
  is_active     bool not null default true,
  is_external   bool not null default false  -- дизайн = true
)

-- Шаблоны проектов: standard / lite
templates (
  id            uuid pk,
  code          text unique not null, -- 'standard' | 'lite'
  name          text not null
)

-- Задачи внутри шаблона (раздел 19 ТЗ)
template_tasks (
  id                uuid pk,
  template_id       uuid fk templates,
  ord               int not null,         -- № задачи 1..N
  name              text not null,
  duration_hours    int not null,         -- Y в часах
  duration_is_window bool not null default false, -- true = окно в рабочих днях
  is_splittable     bool not null default false,
  allow_two_assignees bool not null default false,
  optional_in_lite  bool not null default false  -- раздел 6 ТЗ
)

-- Допустимые исполнители для задачи шаблона + строгость
template_task_assignees (
  template_task_id  uuid fk template_tasks,
  person_id         uuid fk people,
  strictness        text not null check (strictness in ('A','B','C')),
  primary key (template_task_id, person_id)
)

-- Зависимости внутри шаблона
template_dependencies (
  template_task_id  uuid fk template_tasks,
  depends_on_id     uuid fk template_tasks,
  link_type         text not null check (link_type in ('FS','SS')),
  primary key (template_task_id, depends_on_id)
)

-- Активные проекты
projects (
  id            uuid pk,
  title         text not null,
  template_id   uuid fk templates,
  brief_return_date date,        -- раздел 10 ТЗ (вход)
  deadline      date,            -- раздел 10 ТЗ (выход)
  status        text not null default 'planning',
  created_at    timestamptz default now(),
  created_by    uuid fk people
)

-- Конкретные задачи проекта
tasks (
  id            uuid pk,
  project_id    uuid fk projects,
  template_task_id uuid fk template_tasks nullable,
  name          text not null,
  duration_hours int not null,
  start_date    date,
  end_date      date,
  status        text not null default 'not_done',
                -- not_done / done / preliminary / confirmed
  is_preliminary bool not null default false,
  is_splittable bool not null default false,
  allow_two_assignees bool not null default false
)

-- Назначения (раздел 9 ТЗ)
assignments (
  task_id    uuid fk tasks,
  person_id  uuid fk people,
  hours      int not null,           -- часть Y, если splittable
  primary key (task_id, person_id)
)

-- Зависимости конкретных задач (копии из шаблона)
dependencies (
  task_id        uuid fk tasks,
  depends_on_id  uuid fk tasks,
  link_type      text not null check (link_type in ('FS','SS')),
  primary key (task_id, depends_on_id)
)

-- Отпуска / переопределения дня (раздел 5 ТЗ)
day_overrides (
  person_id  uuid fk people,
  day        date,
  capacity_h int not null,        -- 0 = отпуск, 4 = полдня
  reason     text,
  primary key (person_id, day)
)

-- Состояние плана: предложенный vs зафиксированный
plan_versions (
  id          uuid pk,
  project_id  uuid fk projects,
  status      text not null,       -- proposed / committed
  created_at  timestamptz default now(),
  created_by  uuid fk people,
  payload     jsonb not null       -- snapshot всех задач/назначений
)

-- Аудит через sqlalchemy-history (отдельные _version-таблицы)
```

### 4.2 Инварианты (валидируются в `domain/`)

1. У каждой задачи в проекте `duration_hours > 0`.
2. Сумма `assignments.hours` для задачи = `tasks.duration_hours`.
3. `dependencies` — DAG (no cycles). Проверка через `nx.is_directed_acyclic_graph`.
4. Если `tasks.status = 'done'` — солвер её не двигает (раздел 10 ТЗ).
5. `assignments.person_id` ∈ `template_task_assignees.person_id` (если задача из шаблона).

---

## 5. Domain Layer — Solver

### 5.1 SolverPort (интерфейс)

```python
# domain/solver/ports.py
from typing import Protocol
from datetime import date
from .models import PlanRequest, PlanResult, PlanDiff

class SolverPort(Protocol):
    def plan(self, req: PlanRequest) -> PlanResult: ...
    def critical_path_end(self, req: PlanRequest, start: date) -> date: ...
    def diff(self, base: PlanResult, modified: PlanResult) -> PlanDiff: ...
```

### 5.2 Жадный солвер на NetworkX

```python
# domain/solver/greedy.py
class GreedySolver(SolverPort):
    def plan(self, req: PlanRequest) -> PlanResult:
        # 1. построить DiGraph по зависимостям
        # 2. nx.topological_sort
        # 3. для каждой задачи: найти earliest_slot
        #    where (executor ok) AND (capacity ok) AND (deps closed)
        # 4. учесть day_overrides (отпуск = 0)
        # 5. вернуть PlanResult со списком (task, person, start, end, hours/day)
        # 6. собрать список перегрузов (мягкий сигнал — не блокируем)
```

**Точные сигнатуры функций (структура каждой функции):**

```python
def build_dag(tasks: list[Task], deps: list[Dependency]) -> nx.DiGraph:
    """Шаг 1 жадного. Узел = task_id, edge = depends_on -> task; attr 'link_type'."""

def earliest_slot(
    task: Task,
    person: Person,
    earliest_start: date,
    calendar: CalendarPort,
    capacity_index: CapacityIndex,
) -> date:
    """Поиск раннего рабочего дня, где задача поместится с учётом capacity и календаря."""

def capacity_remaining(person_id: UUID, day: date, idx: CapacityIndex) -> int:
    """X - sum(assignments[person, day]); учитывает day_overrides."""

def split_across_days(
    task: Task, person: Person, start: date, calendar: CalendarPort,
    capacity_idx: CapacityIndex,
) -> list[DayAllocation]:
    """Если is_splittable: режем Y по дням; иначе одна аллокация."""

def critical_path_end(req: PlanRequest, start: date) -> date:
    """Раздел 9: обратный режим. nx.dag_longest_path_length по часам/calendar."""

def diff(base: PlanResult, modified: PlanResult) -> PlanDiff:
    """Раздел 14: descendants для проектирования сдвига; вернуть {moved_tasks, new_overloads, removed_overloads}."""

def is_overloaded(person: Person, day: date, assigns: list[Assignment]) -> bool:
    """sum(hours) > capacity_h. Мягкий сигнал (раздел 11)."""
```

**Сложность:**
- `plan()`: O(V + E) для топосорта, O(V × D) для аллокации, где D = горизонт планирования (~120 дней).
- `diff()`: O(V + E) для descendants.

### 5.3 Жёсткие vs мягкие ограничения (раздел 9 ТЗ)

| Тип | Ограничение | Реализация |
|---|---|---|
| Жёсткое | task → executor binding | `template_task_assignees` |
| Жёсткое | capacity / день | `capacity_remaining` |
| Жёсткое | FS-зависимости | `earliest_start = max(end of deps[FS])` |
| Жёсткое | deadline | `if end > deadline: emit RiskFlag` |
| Жёсткое | отпуска | `day_overrides[person, day].capacity_h == 0` |
| Мягкое | баланс загрузки | вес 0.4 в objective (фаза 2) |
| Мягкое | минимум перегрузов | вес 0.3 |
| Мягкое | приоритет проекта | вес 0.2 |
| Мягкое | меньше переключений | вес 0.1 |

MVP оставляет мягкие цели вне солвера: жадный делает «первый допустимый слот», без оптимизации. Это явный trade-off — отдельный раздел в `docs/architecture.md`.

---

## 6. LLM Layer — Intent Parsing

### 6.1 Intent schema (Pydantic)

```python
# domain/intent.py
from typing import Literal
from pydantic import BaseModel
from datetime import date
from uuid import UUID

class AddProjectIntent(BaseModel):
    kind: Literal['add_project'] = 'add_project'
    title: str
    template_code: Literal['standard', 'lite']
    deadline: date | None = None        # backward mode if None
    brief_return_date: date | None = None
    notes: str | None = None

class LoadIntent(BaseModel):
    kind: Literal['load'] = 'load'
    person_name: str | None = None        # None = вся команда
    date_range: tuple[date, date] | None = None  # None = ближайшие 14 дней

class WhatIfIntent(BaseModel):
    kind: Literal['what_if'] = 'what_if'
    operation: Literal['shift_deadline','add_person','switch_to_lite','drop_project']
    project_title: str | None = None
    new_deadline: date | None = None
    person_name: str | None = None

class VacationIntent(BaseModel):
    kind: Literal['vacation'] = 'vacation'
    person_name: str
    day_from: date
    day_to: date
    capacity_h: int = 0   # 0 = полный отпуск

class ConfirmIntent(BaseModel):
    kind: Literal['confirm'] = 'confirm'
    plan_version_id: UUID | None = None  # последний предложенный — по контексту

class AssignIntent(BaseModel):
    kind: Literal['assign'] = 'assign'
    task_ref: str        # «задача 13 в проекте X»
    person_name: str

Intent = AddProjectIntent | LoadIntent | WhatIfIntent | VacationIntent | ConfirmIntent | AssignIntent
```

### 6.2 Парсинг через instructor

```python
# infra/llm/claude.py
import instructor
from anthropic import AsyncAnthropic

class ClaudeIntentParser(IntentParserPort):
    def __init__(self, api_key: str):
        client = AsyncAnthropic(api_key=api_key)
        self.client = instructor.from_anthropic(client)

    async def parse(self, text: str, chat_context: ChatContext) -> Intent:
        return await self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            response_model=Intent,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": self._build_user(text, chat_context)},
            ],
            max_tokens=400,
            max_retries=2,
        )
```

**Промпт `INTENT_SYSTEM_PROMPT` (`infra/llm/prompts.py`):**
- Описать каждый kind с примерами.
- Жёстко: «если уверенности нет — задай уточняющий вопрос через `ConfirmIntent` с пустыми полями». (Шаг 8 flow.)
- Резолв имён («Лёху» → "Лёша") — таблица алиасов в контексте.
- Резолв дат («к среде») — текущая дата + RU-локаль.

### 6.3 Объяснение результата (LLM)

```python
async def explain_plan(plan: PlanResult, mode: ExplainMode) -> str:
    """Шаг 13 flow. Не парсит, просто формулирует. Промпт: 'Объясни план в 5 строках, выдели перегрузы и риски.'"""
```

---

## 7. Application Layer — Use-Cases

### 7.1 AddProject

```python
# app/add_project.py
class AddProjectUseCase:
    def __init__(self, repo: Repo, solver: SolverPort, calendar: CalendarPort): ...

    async def execute(self, intent: AddProjectIntent, actor: Person) -> PlanResult:
        # 1. валидация: title уникален, deadline в будущем, шаблон существует
        # 2. cоздать project (status=planning)
        # 3. инстанцировать tasks из template_tasks (deep copy)
        # 4. собрать PlanRequest: текущие задачи команды + новые
        # 5. solver.plan(req) → PlanResult
        # 6. если deadline пропущен → emit RiskFlag c рычагами (раздел 12 ТЗ)
        # 7. записать PlanVersion(status='proposed') в БД
        # 8. вернуть PlanResult для рендера в боте/админке
```

### 7.2 WhatIf

```python
class WhatIfUseCase:
    async def execute(self, intent: WhatIfIntent) -> PlanDiff:
        # 1. clone текущего committed-плана в memory (без записи в БД)
        # 2. применить операцию к копии
        # 3. solver.plan() на копии
        # 4. solver.diff(base, modified) → PlanDiff
        # 5. вернуть дифф для рендера; PlanVersion(status='proposed') НЕ записываем
```

### 7.3 ConfirmPlan

```python
class ConfirmPlanUseCase:
    async def execute(self, plan_version_id: UUID, actor: Person):
        # 1. проверить actor.is_admin (раздел 16 ТЗ)
        # 2. загрузить PlanVersion(proposed)
        # 3. в одной транзакции:
        #    - PlanVersion.status = 'committed'
        #    - применить assignments/tasks/dependencies
        #    - audit log (sqlalchemy-history делает автоматически)
        # 4. инвалидировать предыдущий proposed
```

### 7.4 Прочие use-cases

- `LoadSummaryUseCase` — рендер хитмапа PNG (день × человек, 14 дней вперёд).
- `SetVacationUseCase` — upsert в `day_overrides` + триггер replan.
- `MarkTaskDoneUseCase` — статус задачи → `done`, солвер при следующем replan её не двигает.

---

## 8. Telegram Bot Layer

### 8.1 Маршрутизация (раздел 15 ТЗ)

| Команда | Хендлер | Use-case |
|---|---|---|
| `/start` | `handlers/start.py` | приветствие |
| `/task <текст>` | `task_router.py` | парсинг intent + ветвление |
| `@bot <текст>` или reply на бота | `task_router.py` | то же |
| голос reply на бота | `task_router.py` → Whisper | то же |
| `/load [имя]` | `handlers/load.py` | LoadSummaryUseCase |
| `/whatif <текст>` | `handlers/whatif.py` | WhatIfUseCase |
| `/vacation` | `dialogs/vacation.py` | aiogram-dialog wizard |
| callback `confirm:<plan_id>` | `handlers/confirm.py` | ConfirmPlanUseCase |
| callback `edit:<plan_id>` | `handlers/confirm.py` | возврат в FSM-петлю (шаг 14 flow) |

### 8.2 Middlewares

```python
# bot/middlewares/permissions.py
class PermissionsMiddleware(BaseMiddleware):
    """Раздел 16 ТЗ: write — только admin; read — всем."""
    WRITE_INTENTS = {AddProjectIntent, ConfirmIntent, WhatIfIntent, VacationIntent, AssignIntent}
    async def __call__(self, handler, event, data):
        intent = data.get('intent')
        actor = data['actor']
        if type(intent) in self.WRITE_INTENTS and not actor.is_admin:
            return await event.answer("Только админ может править план.")
        return await handler(event, data)
```

### 8.3 Диалоги (aiogram-dialog)

- `add_project` — Calendar для deadline, Multiselect для шаблона, TextInput для title.
- `confirm_plan` — рендер PlanResult в текст + Button(Yes/No).
- `vacation` — Multiselect persons, Calendar диапазон, Counter capacity_h.

### 8.4 Privacy mode

Telegram BotFather → privacy mode ON: бот не читает чат; реагирует только на `/task`, `@mention`, reply (шаг 1 flow).

---

## 9. Web Admin (FastAPI + htmx)

### 9.1 Маршруты

| Route | Метод | Назначение |
|---|---|---|
| `/login` | GET/POST | Telegram Login Widget; кладёт JWT в cookie |
| `/plan` | GET | список проектов + Gantt PNG |
| `/plan/{project_id}` | GET | детальный план |
| `/plan/{project_id}/task/{task_id}/edit` | POST | смена даты/исполнителя (только admin) |
| `/team` | GET | таблица людей + day_overrides |
| `/team/vacation` | POST | добавить отпуск |
| `/audit` | GET | пагинированный лог `sqlalchemy_history` |
| `/seed/reload` | POST | dev-only: перезалить seed |

### 9.2 Auth

Telegram Login Widget → проверяем подпись от BotFather → matching `people.tg_user_id` → JWT в http-only cookie. Никакой регистрации, никаких паролей (single-tenant).

### 9.3 Trade-off

Бот может всё то же, что админка, через aiogram-dialog. Админка — для удобных bulk-правок. Не дублируем use-cases — один `app/` слой, два UI.

---

## 10. Calendar Layer

```python
# domain/calendar/ports.py
class CalendarPort(Protocol):
    async def is_working_day(self, day: date) -> bool: ...
    async def business_days_between(self, a: date, b: date) -> int: ...
    async def next_working_day(self, day: date) -> date: ...

# infra/calendar/isdayoff.py
class IsDayOffCalendar(CalendarPort):
    """GET https://isdayoff.ru/{YYYYMMDD}; кэш в Redis на 24 ч."""

# infra/calendar/snapshot.py
class SnapshotCalendar(CalendarPort):
    """d10xa/holidays-calendar JSON, обновляется ежегодно cron-задачей."""
```

**Fallback цепочка:** SnapshotCalendar → IsDayOffCalendar (если онлайн) → SnapshotCalendar (если нет). Реализуем через decorator `CachedCalendar(SnapshotCalendar, refresh_via=IsDayOffCalendar)`.

---

## 11. Scheduler (APScheduler)

```python
# infra/scheduler.py
def register_jobs(scheduler, deps):
    # Каждый рабочий день в 09:30 МСК
    scheduler.add_job(
        send_daily_load_summary,
        CronTrigger(day_of_week='mon-fri', hour=9, minute=30, timezone='Europe/Moscow'),
        kwargs={'bot': deps.bot, 'chat_id': deps.team_chat_id},
    )
    # Ежегодно 1 января — обновить snapshot календаря
    scheduler.add_job(refresh_calendar_snapshot, CronTrigger(month=1, day=1))
```

---

## 12. Testing Strategy

### 12.1 Уровни

| Уровень | Что покрывает | Tools |
|---|---|---|
| Unit | `domain/` solver, calendar rules, intent validators | pytest, без сети |
| Integration | `infra/db/`, `infra/calendar/` (vcrpy), `infra/llm/` (vcrpy) | testcontainers Postgres, vcrpy кассеты |
| E2E | Полные потоки бота через `aiogram.test_utils` | testcontainers + fake Whisper/Claude |

### 12.2 Target coverage: 80%+

### 12.3 TDD-цикл

Каждая задача из раздела 13 ниже:
1. RED: написать падающий тест.
2. GREEN: минимальная реализация.
3. REFACTOR.
4. Проверить coverage.

### 12.4 «Реальные тесты» (требование пользователя)

После завершения MVP — приёмочные сценарии в Telegram-чате с тестовой командой, описаны в разделе 16.

---

## 13. Implementation Roadmap (6 спринтов, bite-sized)

Каждый спринт = 1-2 дня агентной работы, завершается работающим срезом + тестами + commit.

### Спринт 1 — Skeleton & DB (Day 1)

**Цель:** проект запускается, миграции применены, seed загружен.

| # | Задача | Файлы | Тест |
|---|---|---|---|
| 1.1 | Сгенерировать pyproject.toml через `uv init` + добавить зависимости | `pyproject.toml`, `uv.lock` | `uv sync` ok |
| 1.2 | `docker-compose.yml` с postgres:16 + redis:7 + healthchecks | `docker-compose.yml` | `docker compose up -d` healthy |
| 1.3 | Pydantic Settings (.env) | `src/planner/settings.py` | unit: required env validation |
| 1.4 | SA Base + async session factory | `infra/db/base.py` | unit: session открывается |
| 1.5 | ORM модели (см. раздел 4.1) | `infra/db/models.py` | unit: mapper config |
| 1.6 | Alembic init + первая миграция | `alembic/` | `alembic upgrade head` ok |
| 1.7 | Seed загрузчик (YAML → DB) | `seed/load_seed.py`, `seed/*.yaml` | integration: после load — 6 people, 1 standard template c 20 task_templates |
| 1.8 | Подключить sqlalchemy-history | `infra/db/audit.py` | unit: insert/update создаёт _version row |
| 1.9 | Commit | — | — |

**Acceptance:** `make seed` → в БД 6 people, 2 шаблона (standard, lite), все 20 задач с зависимостями и привязками.

### Спринт 2 — Solver (Day 2)

**Цель:** Жадный солвер закрывает разделы 9–10 ТЗ.

| # | Задача | Файлы |
|---|---|---|
| 2.1 | Domain dataclasses (Task, Person, …) | `domain/models.py` |
| 2.2 | CalendarPort + SnapshotCalendar (хардкод JSON) | `domain/calendar/`, `infra/calendar/snapshot.py` |
| 2.3 | `build_dag` + cycle-check | `domain/solver/greedy.py` |
| 2.4 | `earliest_slot` + `capacity_remaining` | `domain/solver/greedy.py` |
| 2.5 | `split_across_days` | `domain/solver/greedy.py` |
| 2.6 | `GreedySolver.plan()` (forward) | `domain/solver/greedy.py` |
| 2.7 | `critical_path_end()` (backward) | `domain/solver/critical_path.py` |
| 2.8 | `diff()` через descendants | `domain/solver/diff.py` |
| 2.9 | Unit-тесты: 20+ кейсов | `tests/unit/domain/solver/` |

**Acceptance тесты солвера:**
- линейная цепочка 5 задач FS → end_date = sum(Y) на 1 человека.
- две задачи одному человеку с overlap → перегруз помечен.
- цикл в DAG → `NetworkXUnfeasible`.
- отпуск на 3 дня → задача сдвигается.
- splittable задача 16ч у Андрея с capacity 8 → 2 дня.
- 2 проекта параллельно: разные ресурсы, нет конфликта.
- 2 проекта на одного человека: capacity сводит в очередь, перегруза нет.
- задача `done` → солвер не двигает её start_date.
- backward режим: без deadline → critical path = max(EF) по графу.
- что-если: смена deadline → diff показывает X сдвинутых задач.

### Спринт 3 — Telegram Bot baseline (Day 3)

**Цель:** Бот принимает команды, разбирает интент, показывает планы. Без write.

| # | Задача | Файлы |
|---|---|---|
| 3.1 | aiogram 3 Dispatcher + Redis storage | `bot/runner.py` |
| 3.2 | PermissionsMiddleware | `bot/middlewares/permissions.py` |
| 3.3 | `/start` хендлер | `bot/handlers/start.py` |
| 3.4 | ClaudeIntentParser + промпт | `infra/llm/claude.py`, `infra/llm/prompts.py` |
| 3.5 | `/task` хендлер с парсингом | `bot/handlers/task_router.py` |
| 3.6 | `/load` хендлер (без рендера) | `bot/handlers/load.py` |
| 3.7 | aiogram_dialog `add_project` | `bot/dialogs/add_project.py` |
| 3.8 | Integration тесты с vcrpy + testcontainers | `tests/integration/test_llm_intent.py` |
| 3.9 | E2E: «создать проект X с дедлайном 15-го» → бот отвечает с предложенным планом | `tests/e2e/` |

### Спринт 4 — Confirm / What-If / Vacation / PNG (Day 4)

| # | Задача |
|---|---|
| 4.1 | `ConfirmPlanUseCase` + callback handler |
| 4.2 | `WhatIfUseCase` + хендлер |
| 4.3 | `SetVacationUseCase` + aiogram_dialog wizard |
| 4.4 | `LoadSummaryUseCase` + heatmap.py (seaborn) |
| 4.5 | `gantt.py` (matplotlib timeline) |
| 4.6 | `explain_plan()` через Claude |
| 4.7 | Whisper STT: voice → text |
| 4.8 | APScheduler: daily summary 09:30 |
| 4.9 | E2E: полный цикл «голос → план → confirm» |

### Спринт 5 — Web админка (Day 5)

| # | Задача |
|---|---|
| 5.1 | FastAPI app + Jinja2 + base.html |
| 5.2 | Telegram Login Widget + JWT cookie |
| 5.3 | `/plan` route + plan.html (htmx) |
| 5.4 | `/plan/{id}/edit` POST + admin gate |
| 5.5 | `/team` + `/team/vacation` |
| 5.6 | `/audit` (sqlalchemy-history запросы) |
| 5.7 | Один процесс: `uvicorn + aiogram + APScheduler` через `asyncio.gather` в `main.py` |
| 5.8 | E2E через httpx: создать проект через бота, подтвердить через админку, аудит показывает actor |

### Спринт 6 — Polish, observability, acceptance run (Day 6)

| # | Задача |
|---|---|
| 6.1 | Structlog JSON logging + correlation_id |
| 6.2 | Error-boundary: пользовательские ошибки в чат, traceback в лог |
| 6.3 | `make` targets: `dev`, `test`, `seed`, `migrate`, `lint`, `acceptance` |
| 6.4 | README с быстрым стартом |
| 6.5 | docs/acceptance.md (см. раздел 16) |
| 6.6 | Прогон acceptance-сценариев в реальном чате |
| 6.7 | mypy strict для `domain/` (без IO — типы должны быть железные) |
| 6.8 | Финальный coverage report |

---

## 14. Сознательно отложено (вне MVP)

Прямо из раздела 18 ТЗ + наши решения:
- PyJobShop / OR-Tools (фаза v2).
- Ось сложности (множитель длительности).
- Полная карта статусов задач.
- Детальная почасовка вместо «потолка дня».
- Sentry / Prometheus.
- Production deploy (Railway / VPS / K8s).
- Multi-tenant.
- E2E через реальный Telegram Bot API на CI (используем mock-update в тестах; реальный прогон — руками в acceptance).

Каждое решение зафиксировано в `docs/architecture.md` с разделом «Why deferred + when to revisit».

---

## 15. Risks & Mitigations

| Риск | Митигация |
|---|---|
| Claude API недоступен | Fallback: `BasicIntentParser` (regex для `/task add`, `/load`, `/whatif`) — деградация, но бот работает |
| isdayoff.ru down | Snapshot-fallback покрывает 2026 целиком |
| Whisper API лимиты | Fallback на «текст-only»: бот просит переписать текстом |
| sqlalchemy-history несовместим с SA 2.x async | Запасной план: руками писать `audit_log` row в каждом use-case |
| LLM возвращает «битый» intent | instructor max_retries=2 → потом ConfirmIntent с пустыми полями → бот переспрашивает |
| Перегрев солвера на 50+ задачах | замер: 50 задач × 6 человек × 120 дней ≈ 10ms. Безопасно. |
| Конкурентная запись из админки и бота | Один процесс + один writer (раздел 17 ТЗ) + advisory lock на project_id |

---

## 16. Acceptance — Real-World Tests

После Спринта 6 — приёмка в реальном Telegram-чате с тестовой командой.

### 16.1 Сценарии (исполняются вручную с командой)

**A. Создание проекта в прямом режиме (раздел 9 ТЗ)**
1. Менеджер: `/task Новый проект "Альфа", шаблон standard, дедлайн 25 июня`
2. Бот предлагает план: список задач, кто/что/когда, перегрузы.
3. Менеджер нажимает Confirm → план фиксируется.
4. **Verify:** в БД `plan_versions.status='committed'`, аудит-запись с actor=менеджер.

**B. Backward режим (раздел 9 ТЗ)**
1. `/task Новый проект "Бета", шаблон lite, дедлайн НЕ ЗАДАН`
2. Бот возвращает: «Самая ранняя дата КП: 12 июля». Без записи в БД.

**C. Что-если (раздел 14 ТЗ)**
1. На committed проекте: `/whatif сдвинуть дедлайн Альфы на 30 июня`.
2. Бот: «Сдвинет 5 задач, перегруз у Айгуль уйдёт во вторник.»
3. Менеджер игнорирует / подтверждает.
4. **Verify:** если confirm — изменения в БД; если нет — БД нетронута.

**D. Перегруз + рычаги (разделы 11–12 ТЗ)**
1. Создать 3 проекта подряд на одного человека.
2. Бот: «Дедлайн недостижим. Рычаги: lite / +человек / сдвиг.»
3. Применить lite (через диалог) → план перестраивается.

**E. Отпуск (раздел 5 ТЗ)**
1. `/vacation Айгуль 10-12 июня`.
2. Бот: «Задачи Айгуль на эти дни сдвинуты, ничего не упало.»
3. **Verify:** day_overrides добавлен, replan произошёл, deadline не пропущен.

**F. Voice вход (раздел 1 ТЗ, шаги 1, 4 flow)**
1. Голосовое: «Прогони что-если, перенесём дедлайн Альфы на четверг».
2. Бот: интерпретация + дифф.

**G. Read-only участник (раздел 16 ТЗ)**
1. Неадмин шлёт `/task создать проект Z`.
2. Бот: «Только админ может править план.»
3. Тот же `/load` → отвечает.

**H. Дневная сводка (раздел 11 ТЗ)**
1. Утром в 09:30 бот сам пишет в чат: «Сегодня: задачи команды. Перегруз у Лёши (10ч).»
2. **Verify:** APScheduler job сработал.

**I. Аудит (раздел 17 ТЗ)**
1. Открыть `/audit` в админке.
2. Видны все изменения с автором и временем.

**J. Воркфлоу подтверждения (раздел 13 ТЗ)**
1. Бот предлагает план.
2. Менеджер: «правка: убери #17 из Альфы».
3. Бот накапливает правки → новый предложенный план.
4. Менеджер: «ок» → фиксация.
5. **Verify:** committed-план соответствует правкам.

### 16.2 Критерий приёмки

Все сценарии A-J проходят при первом прогоне (не более 2 переходов в «не понял, переспроси»). Если какой-то падает — он становится failing test, чинится, перепроходим.

---

## 17. Verification (как агенту проверить, что план реализован)

После Спринта 6:

```bash
# 1. Запуск
docker compose up -d
uv sync
uv run alembic upgrade head
uv run python -m seed.load
uv run python -m planner.main &

# 2. Юнит и интеграционные тесты
uv run pytest tests/unit tests/integration -v --cov=src/planner --cov-report=term
# Ожидание: 80%+ coverage; 0 failing

# 3. E2E тесты (внутри pytest, с testcontainers)
uv run pytest tests/e2e -v
# Ожидание: 0 failing

# 4. Lint + types
uv run ruff check src tests
uv run mypy src/planner/domain --strict

# 5. Acceptance сценарии (раздел 16) — вручную с командой в тестовом чате
# Документ docs/acceptance.md с галочками после каждого сценария
```

---

## 18. Глоссарий и привязка к пунктам ТЗ

| Сущность кода | Раздел ТЗ |
|---|---|
| `Person.capacity_h` | 2, 4, 20 |
| `Template`, `lite` | 6 |
| `template_task_assignees.strictness` (A/B/C) | 7 |
| `Dependency.link_type` (FS/SS) | 8 |
| `GreedySolver`, `CriticalPath` | 9, 18 |
| `Task.status`, `is_preliminary` | 10 |
| `is_overloaded`, daily summary | 11 |
| `RiskFlag`, рычаги | 12 |
| `PlanVersion.status` proposed→committed | 13 |
| `WhatIfUseCase` | 14 |
| Telegram-handlers, dialogs | 15 |
| `PermissionsMiddleware` | 16 |
| Single writer + sqlalchemy-history | 17 |
| Что отложено | 18 |
| YAML seed | 19 |
| `capacity_h=8` дефолт | 20 |

---

## 19. Open Questions (не блокируют старт, но фиксируем)

1. Кто из команды — admin (`is_admin=true`)? Решение → в seed/team.yaml на старте Спринта 1.
2. Часовой пояс daily summary — захардкожено `Europe/Moscow`. Подтвердить.
3. Тестовый Telegram-чат + Bot Token — нужны от заказчика к Спринту 3.
4. Финальный список lite-задач (раздел 6 ТЗ) — нужен заказчиком до Спринта 1 (раздел 19 ТЗ).

Эти 4 вопроса — единственная зависимость от заказчика; всё остальное самодостаточно.

---

## 20. Self-review checklist

- [x] Каждый раздел ТЗ имеет привязку к компоненту кода (раздел 18).
- [x] Каждый компонент имеет обоснование (раздел 2 + раздел 0).
- [x] Зафиксированные ограничения (одна команда, один процесс, один writer) явные.
- [x] Trade-offs описаны (раздел 14, раздел 9, мягкие цели MVP=жадный).
- [x] Acceptance — реальные сценарии в чате (раздел 16).
- [x] Тесты разнесены по уровням (раздел 12).
- [x] Файловая структура соответствует ≤ 400 строк / файл (раздел 3).
- [x] Никаких новых технологий без обоснования (раздел 2).
- [x] Risks + mitigations (раздел 15).
- [x] Verification команды конкретные (раздел 17).
