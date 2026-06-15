# Plan 001: Fail startup when JWT_SECRET is left at the insecure default in production

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Instead, open each file in "Current state" and confirm the
> quoted excerpt matches the live code. On any mismatch, treat it as a STOP
> condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

`jwt_secret` defaults to the literal string `"dev-insecure-change-me"`, which is
public (it is in the source). Admin sessions are signed with this secret
(`web/auth.py:create_jwt`). Anyone who knows the default can mint a
`{"is_admin": true}` JWT offline and present it as the `planner_session` cookie
to reach every admin page — no network access to the box required. The QA report
(`docs/qa-report-2026-06-11.md`, finding C2) forged such a token against the
running instance. This plan makes the process refuse to start in production
(`DEBUG=false`) while the secret is still the default, forcing the operator to
set a real one. It does not fix an already-leaked secret — see Maintenance notes
on rotation.

## Current state

- `src/planner/settings.py` — Pydantic settings model. The insecure default:
  ```python
  # settings.py:38
      jwt_secret: str = "dev-insecure-change-me"
      """Secret used to sign admin-session JWTs (spec section 9.2)."""
  ...
  # settings.py:45
      debug: bool = False
  ...
  # settings.py:53
  def get_settings() -> Settings:
      """Get application settings instance."""
      return Settings()  # type: ignore[call-arg]  # values populated from env / .env
  ```
- `src/planner/main.py` — the real entrypoint. It calls `get_settings()` first:
  ```python
  # main.py:30
  async def main() -> None:
      settings = get_settings()

      configure_logging(json_logs=not settings.debug, ...)
  ```
- `.env.example` — has **no** `JWT_SECRET` line, so anyone copying it inherits
  the insecure default. Last lines are:
  ```
  # Application Configuration
  # Timezone for scheduling (default: Europe/Moscow)
  TIMEZONE=Europe/Moscow

  # Debug mode (default: false)
  DEBUG=false
  ```
- `tests/unit/test_settings.py` — existing settings tests construct
  `Settings(_env_file=None)` with the required fields and rely on defaults for
  the rest. Match that style for new tests.

Convention note: settings tests pass `_env_file=None` so the repo's real `.env`
is not read. Always do that in new settings tests or they become
environment-dependent.

## Commands you will need

| Purpose   | Command                                              | Expected on success     |
|-----------|------------------------------------------------------|-------------------------|
| Typecheck | `uv run mypy src/planner/settings.py src/planner/main.py --strict` | exit 0, no errors |
| Lint      | `uv run ruff check src tests`                        | exit 0                  |
| Tests     | `uv run pytest tests/unit/test_settings.py tests/unit/test_main.py -v` | all pass |

## Scope

**In scope** (the only files you should modify):
- `src/planner/settings.py`
- `src/planner/main.py`
- `.env.example`
- `tests/unit/test_settings.py`

**Out of scope** (do NOT touch):
- `src/planner/web/auth.py` — the signing/verification is correct; only the
  secret's provenance is the problem.
- The actual `.env` file — never read, write, or print its contents.
- Any other route/handler.

## Git workflow

- Branch: `advisor/001-jwt-secret-fail-fast`
- Commit message style is conventional commits (see `git log`, e.g.
  `feat: capability-based assignee suggestion`). Use
  `fix(security): fail startup on default JWT secret in production`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add the default constant and a guard function in settings.py

In `src/planner/settings.py`, lift the default into a module constant and add a
validation function. Replace the literal on the `jwt_secret` field with the
constant, and add the function after `get_settings`:

```python
DEFAULT_JWT_SECRET = "dev-insecure-change-me"
```
```python
    jwt_secret: str = DEFAULT_JWT_SECRET
    """Secret used to sign admin-session JWTs (spec section 9.2)."""
```
```python
def ensure_secure_config(settings: Settings) -> None:
    """Refuse to run in production with security-critical defaults left unset.

    Raises RuntimeError when DEBUG is false and JWT_SECRET is still the public
    default — an attacker who knows the default can forge admin sessions.
    """
    if not settings.debug and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is the insecure default. Set a strong JWT_SECRET "
            '(generate one with: python -c "import secrets; '
            "print(secrets.token_urlsafe(32))\") or run with DEBUG=true for "
            "local development only."
        )
```

**Verify**: `uv run ruff check src/planner/settings.py` → exit 0

### Step 2: Call the guard at startup in main.py

In `src/planner/main.py`, import `ensure_secure_config` alongside `get_settings`
and call it immediately after settings are loaded:

```python
from planner.settings import ensure_secure_config, get_settings
```
```python
async def main() -> None:
    settings = get_settings()
    ensure_secure_config(settings)
```

**Verify**: `uv run mypy src/planner/settings.py src/planner/main.py --strict` → exit 0, no errors

### Step 3: Add JWT_SECRET to .env.example with generation guidance

Append to `.env.example` (after the `DEBUG=false` line):

```
# Admin session signing secret (REQUIRED in production).
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
# The app refuses to start with DEBUG=false while this is the default.
JWT_SECRET=
```

(Leaving it empty is fine — Pydantic will fall back to the default only in
local/debug runs; production must fill it.)

**Verify**: `grep -c JWT_SECRET .env.example` → `1`

### Step 4: Add unit tests for the guard

In `tests/unit/test_settings.py`, add a test class. Reuse the existing
`Settings(_env_file=None, ...)` construction style. Cover all three branches:

```python
class TestEnsureSecureConfig:
    def _base(self, **overrides):
        from planner.settings import Settings
        kwargs = dict(
            database_url="x", redis_url="x", bot_token="t", team_chat_id=1,
        )
        kwargs.update(overrides)
        return Settings(_env_file=None, **kwargs)

    def test_default_secret_in_production_raises(self):
        from planner.settings import ensure_secure_config
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            ensure_secure_config(self._base(debug=False))

    def test_default_secret_in_debug_is_allowed(self):
        from planner.settings import ensure_secure_config
        ensure_secure_config(self._base(debug=True))  # no raise

    def test_custom_secret_in_production_is_allowed(self):
        from planner.settings import ensure_secure_config
        ensure_secure_config(self._base(debug=False, jwt_secret="a-strong-secret"))
```

**Verify**: `uv run pytest tests/unit/test_settings.py -v` → all pass, including 3 new tests

## Test plan

- New tests: `TestEnsureSecureConfig` in `tests/unit/test_settings.py` —
  (1) default secret + `debug=False` raises, (2) default secret + `debug=True`
  passes, (3) custom secret + `debug=False` passes. Model after the existing
  `TestSettingsValidation` class in the same file.
- Regression: `tests/unit/test_main.py` must still pass. It mocks
  `get_settings` to return a `MagicMock`; `not magicmock.debug` evaluates to
  `False`, so `ensure_secure_config` short-circuits and never raises — confirm
  this by running the file.
- Verification: `uv run pytest tests/unit/test_settings.py tests/unit/test_main.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/settings.py src/planner/main.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/test_settings.py tests/unit/test_main.py -v` exits 0; 3 new `ensure_secure_config` tests pass
- [ ] `grep -n "dev-insecure-change-me" src/planner/settings.py` shows it only once, on the `DEFAULT_JWT_SECRET` constant line
- [ ] `git status --porcelain` lists only the four in-scope files as modified
- [ ] `plans/README.md` status row for 001 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The `jwt_secret` default in `settings.py` is no longer
  `"dev-insecure-change-me"` (someone already changed it — the excerpt drifted).
- `main.py` no longer calls `get_settings()` near the top of `main()`.
- `tests/unit/test_main.py` fails after Step 2 (the mock assumption broke —
  report the failure output rather than weakening the guard).
- Adding the guard would require changing more than the four in-scope files.

## Maintenance notes

- **Rotation**: a secret that was ever the public default must be treated as
  compromised. The operator should set a fresh random `JWT_SECRET`; all existing
  sessions are invalidated automatically (old cookies fail verification).
- This guard only covers `JWT_SECRET`. If other secret-bearing defaults are
  added later (e.g. `webhook_secret`), extend `ensure_secure_config` rather than
  adding scattered checks.
- A reviewer should confirm the guard is keyed on `not settings.debug` so local
  development is unaffected, and that the error message never echoes the secret
  value.
- Related: plan 002 locks down `/dev-login`, the other admin-bypass path.
