# Plan 017: Replace python-jose with PyJWT on the admin-session auth path

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Open `src/planner/web/auth.py` and confirm the quoted
> excerpt matches the live code. On any mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dependencies / security hygiene
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

Admin-session JWTs are signed/verified with `python-jose`, a library with a
weak maintenance history and known past CVEs (CVE-2024-33663 algorithm
confusion, CVE-2024-33664 decode DoS). The current usage is the safest subset —
HS256 only, algorithms pinned on decode — so there is no live exploit here;
this is hygiene: PyJWT is the actively maintained standard for exactly this
HS256 encode/decode use, and the migration is mechanical. The first step
records the locked python-jose version so the decision trail is honest.

## Current state

- `src/planner/web/auth.py` — the only module importing jose:
  ```python
  from jose import JWTError, jwt
  ...
  def create_jwt(claims: dict[str, Any], secret: str) -> str:
      payload = dict(claims)
      payload["exp"] = datetime.now(UTC) + timedelta(hours=JWT_TTL_HOURS)
      token: str = jwt.encode(payload, secret, algorithm=JWT_ALGO)
      return token


  def decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
      try:
          claims: dict[str, Any] = jwt.decode(token, secret, algorithms=[JWT_ALGO])
          return claims
      except JWTError:
          return None
  ```
  (`JWT_ALGO = "HS256"`.) Confirm jose appears nowhere else:
  `grep -rn "jose" src/ tests/` must show only this module.
- `pyproject.toml:29` — `"python-jose[cryptography]",` in `dependencies`.
- Tests covering the behavior contract (must pass unchanged):
  `tests/unit/test_auth.py` lives at `tests/unit/web/test_auth.py` —
  `test_jwt_round_trip`, `test_jwt_wrong_secret_returns_none`; plus the cookie
  flows in `tests/unit/web/test_web_e2e.py`.
- PyJWT behavior notes the executor needs:
  - `jwt.encode(payload, secret, algorithm="HS256")` returns `str` (PyJWT ≥ 2).
  - `jwt.decode(token, secret, algorithms=["HS256"])` validates `exp`
    automatically and raises subclasses of `jwt.PyJWTError`
    (`ExpiredSignatureError`, `InvalidSignatureError`, ...).
  - `datetime` values in `exp` are supported in encode, same as jose.

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Re-lock   | `uv sync`                                            | exit 0; pyjwt installed, python-jose removed |
| Typecheck | `uv run mypy src/planner/web/auth.py --strict`       | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                        | exit 0              |
| Tests     | `uv run pytest tests/unit/web -v`                    | all pass            |

(Note: this plan legitimately modifies `pyproject.toml` + `uv.lock` — that IS
the change, run `uv sync` after editing dependencies.)

## Scope

**In scope**:
- `src/planner/web/auth.py`
- `pyproject.toml` (+ `uv.lock` via `uv sync`)

**Out of scope** (do NOT touch):
- `tests/unit/web/test_auth.py` and `test_web_e2e.py` — they are the behavior
  contract; if any of them needs changing, the migration broke behavior (STOP).
- Token claims, TTL, cookie handling, algorithms.

## Git workflow

- Branch: `advisor/017-migrate-jose-to-pyjwt`
- Commit message: `chore(deps): replace python-jose with PyJWT for session tokens`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Record the current jose version (decision trail)

```
grep -B1 -A3 'name = "python-jose"' uv.lock | head -8
```

Note the `version = "..."` line in your final report. Proceed regardless of
version (the migration rationale is maintenance, not a specific CVE in the
locked version).

**Verify**: command prints a version line.

### Step 2: Swap the dependency

In `pyproject.toml` `dependencies`, replace `"python-jose[cryptography]",`
with `"pyjwt>=2.8",` (HS256 needs no crypto extra). Then:

```
uv sync
```

**Verify**: `uv sync` exits 0; `grep -c "python-jose" pyproject.toml` → `0`

### Step 3: Migrate auth.py

Replace the import and the two functions' library calls:

```python
import jwt
```

```python
def create_jwt(claims: dict[str, Any], secret: str) -> str:
    payload = dict(claims)
    payload["exp"] = datetime.now(UTC) + timedelta(hours=JWT_TTL_HOURS)
    token: str = jwt.encode(payload, secret, algorithm=JWT_ALGO)
    return token


def decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
    try:
        claims: dict[str, Any] = jwt.decode(token, secret, algorithms=[JWT_ALGO])
        return claims
    except jwt.PyJWTError:
        return None
```

(Only the import line and the exception class change; call sites are
API-compatible.)

**Verify**: `uv run mypy src/planner/web/auth.py --strict` → exit 0

### Step 4: Full behavior check against the untouched tests

```
uv run pytest tests/unit/web -v
```

All auth + web e2e tests must pass WITHOUT modification — round trip, wrong
secret → None, telegram login flows, cookie set/clear.

**Verify**: all pass; `grep -rn "jose" src/ tests/ pyproject.toml` → no matches

## Test plan

- No new tests: `tests/unit/web/test_auth.py` (round trip, wrong-secret) and
  `tests/unit/web/test_web_e2e.py` (cookie/login flows) already pin the
  contract and must pass unchanged — that is the migration's proof.
- Optional sanity (only if it fits the file's style): an expired-token test
  using a negative TTL would also pass identically under PyJWT; not required.

## Done criteria

ALL must hold:

- [ ] `uv sync` exits 0; `uv.lock` no longer contains python-jose
- [ ] `uv run mypy src/planner/web/auth.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/web -v` exits 0 with ZERO test-file changes
- [ ] `grep -rn "jose" src/ tests/ pyproject.toml` returns nothing
- [ ] `git status --porcelain` shows only `auth.py`, `pyproject.toml`, `uv.lock`
- [ ] `plans/README.md` status row for 017 updated (include the recorded jose version)

## STOP conditions

Stop and report back if:

- `grep -rn "jose" src/ tests/` shows usage outside `web/auth.py` (wider blast
  radius than planned).
- Any existing web test fails after Step 3 — the migration must be
  behavior-identical; do not edit tests to make them pass.
- `uv sync` cannot resolve `pyjwt>=2.8` (environment/network issue) — report.

## Maintenance notes

- PyJWT validates `exp` by default — identical to the jose setup here. If
  claims like `aud`/`iss` are ever added, PyJWT requires explicit options;
  note for whoever extends the token.
- All existing sessions remain valid across the swap (same HS256, same secret —
  the token format is unchanged).
- Reviewer focus: the decode must keep `algorithms=[JWT_ALGO]` pinned (never
  accept the header's algorithm), and the broad `PyJWTError` catch must not
  widen to bare `Exception`.
