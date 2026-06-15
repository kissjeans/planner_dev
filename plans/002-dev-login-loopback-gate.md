# Plan 002: Restrict /dev-login to loopback clients so it cannot grant admin over the network

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

`GET /dev-login` mints a 12-hour admin session with no credentials. It is gated
only by `DEBUG`, and the deployed config runs `DEBUG=true` with the server bound
to `0.0.0.0:8000` (`main.py:53`). Any device on the same network can hit
`/dev-login` and become admin (QA report finding C1, reproduced live). The route
exists for legitimate local development (the Telegram Login Widget needs a public
domain). This plan keeps that convenience but refuses the route unless the
request originates from a loopback address — so a developer on `localhost` still
gets in, but a LAN peer gets a 404.

## Current state

- `src/planner/web/routes/auth.py` — the route, gated only by debug:
  ```python
  # auth.py:32
  @router.get("/dev-login")
  async def dev_login(request: Request) -> RedirectResponse:
      """Local-only admin login bypass (Telegram widget needs a public domain).

      Enabled only when ``DEBUG=true``; returns 404 in production.
      """
      if not request.app.state.debug:
          raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
      claims = {"sub": "dev", "name": "Dev Admin", "tg_id": 0, "is_admin": True}
      token = create_jwt(claims, request.app.state.jwt_secret)
      resp = RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)
      _set_session_cookie(resp, token)
      return resp
  ```
- `src/planner/main.py:53` binds the server to all interfaces:
  ```python
      server = uvicorn.Server(
          uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
      )
  ```
- `tests/unit/web/test_web_e2e.py` — existing tests pin the CURRENT behavior and
  MUST be updated by this plan:
  ```python
  # test_web_e2e.py:256
  def test_dev_login_disabled_without_debug(client):
      r = client.get("/dev-login", follow_redirects=False)
      assert r.status_code == 404

  # test_web_e2e.py:261
  def test_dev_login_mints_admin_session_when_debug():
      settings = _settings().model_copy(update={"debug": True})
      app = create_app(WebFakeRepo(), settings)
      c = TestClient(app)
      r = c.get("/dev-login", follow_redirects=False)
      assert r.status_code == 303
      ...
  ```
  Note: Starlette's `TestClient` accepts a `client=(host, port)` tuple that sets
  `request.client.host`. The default is `("testclient", 50000)`. Use this to
  simulate loopback vs. LAN callers in the updated tests.

## Commands you will need

| Purpose   | Command                                              | Expected on success     |
|-----------|------------------------------------------------------|-------------------------|
| Typecheck | `uv run mypy src/planner/web/routes/auth.py --strict`| exit 0, no errors       |
| Lint      | `uv run ruff check src tests`                        | exit 0                  |
| Tests     | `uv run pytest tests/unit/web/test_web_e2e.py -v`    | all pass                |

## Scope

**In scope**:
- `src/planner/web/routes/auth.py`
- `tests/unit/web/test_web_e2e.py`

**Out of scope** (do NOT touch):
- `main.py` host binding — changing `0.0.0.0` is a deployment decision tracked
  separately; do not alter it here.
- `/login/telegram`, `/login`, `/logout` routes — they are correct.
- `web/auth.py` (the helpers module) — no change needed.

## Git workflow

- Branch: `advisor/002-dev-login-loopback-gate`
- Commit message: `fix(security): restrict /dev-login to loopback callers`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Gate /dev-login on loopback origin in addition to debug

In `src/planner/web/routes/auth.py`, add a loopback constant near the top
(after the imports / `router = APIRouter()`):

```python
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
```

Then change the guard in `dev_login` so it 404s unless BOTH debug is on AND the
caller is loopback:

```python
@router.get("/dev-login")
async def dev_login(request: Request) -> RedirectResponse:
    """Local-only admin login bypass (Telegram widget needs a public domain).

    Enabled only when ``DEBUG=true`` AND the request comes from loopback;
    returns 404 otherwise so it can never grant admin over the network.
    """
    client_host = request.client.host if request.client else ""
    if not request.app.state.debug or client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    claims = {"sub": "dev", "name": "Dev Admin", "tg_id": 0, "is_admin": True}
    token = create_jwt(claims, request.app.state.jwt_secret)
    resp = RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(resp, token)
    return resp
```

**Verify**: `uv run mypy src/planner/web/routes/auth.py --strict` → exit 0

### Step 2: Update the existing dev-login tests and add a LAN-rejection test

In `tests/unit/web/test_web_e2e.py`, replace the two existing dev-login tests
(currently at lines ~256 and ~261) with versions that set the client host
explicitly. The positive case must use a loopback client; add a negative case
for a LAN client:

```python
def test_dev_login_disabled_without_debug():
    # debug off → 404 even from loopback
    app = create_app(WebFakeRepo(), _settings())  # _settings() has debug=False
    c = TestClient(app, client=("127.0.0.1", 5000))
    r = c.get("/dev-login", follow_redirects=False)
    assert r.status_code == 404


def test_dev_login_mints_admin_session_from_loopback():
    settings = _settings().model_copy(update={"debug": True})
    app = create_app(WebFakeRepo(), settings)
    c = TestClient(app, client=("127.0.0.1", 5000))
    r = c.get("/dev-login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"
    assert c.cookies.get(COOKIE_NAME)


def test_dev_login_rejected_from_non_loopback_even_with_debug():
    settings = _settings().model_copy(update={"debug": True})
    app = create_app(WebFakeRepo(), settings)
    c = TestClient(app, client=("10.0.0.5", 5000))  # LAN peer
    r = c.get("/dev-login", follow_redirects=False)
    assert r.status_code == 404
```

If the installed Starlette `TestClient` does not accept a `client=` keyword
(raises `TypeError`), STOP and report — do not silently drop the host
simulation.

**Verify**: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass, including the new non-loopback rejection test

## Test plan

- Tests live in `tests/unit/web/test_web_e2e.py` (the established web-route test
  file using `TestClient` + `WebFakeRepo`).
- Cases: (1) debug off → 404 from loopback; (2) debug on + loopback → 303 admin
  session; (3) debug on + non-loopback `10.0.0.5` → 404 (the regression this
  plan prevents).
- Verification: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/web/routes/auth.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/web/test_web_e2e.py -v` exits 0
- [ ] A test proves `/dev-login` returns 404 for a non-loopback client even when `debug=True`
- [ ] `git status --porcelain` lists only the two in-scope files as modified
- [ ] `plans/README.md` status row for 002 updated

## STOP conditions

Stop and report back if:

- The `dev_login` route no longer matches the "Current state" excerpt.
- `TestClient` does not support the `client=` keyword in this repo's Starlette
  version (you cannot simulate the LAN caller — report so the test approach can
  be revised).
- Verification fails twice after a reasonable fix attempt.

## Maintenance notes

- Defense in depth: the deployment still binds `0.0.0.0`. Pair this with setting
  `DEBUG=false` in production (and plan 001's startup guard). The loopback gate
  is the last line, not the only one.
- If a reverse proxy is introduced, `request.client.host` may become the proxy's
  address (often loopback) — at that point this gate is no longer sufficient and
  `/dev-login` should be removed entirely or moved behind the proxy's auth.
- A reviewer should confirm the guard rejects when `request.client` is `None`
  (the `client_host = "" → not in _LOOPBACK_HOSTS` path).
