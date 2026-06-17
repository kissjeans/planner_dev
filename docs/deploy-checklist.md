# Deploy checklist

Run through this before promoting `planner_dev` to a production environment.

## Configuration

- [ ] `DEBUG=false` — debug mode enables the loopback `/dev-login` session
      shortcut; it must be off in production.
- [ ] `JWT_SECRET` set to a strong, random value. Generate with:
      `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
      The app refuses to start with `DEBUG=false` while this is unset.
- [ ] `BOT_TOKEN` and `ANTHROPIC_API_KEY` supplied via env / secret manager —
      never committed to the repo.

## Data layer

- [ ] `alembic upgrade head` applied against the production database.
- [ ] Postgres reachable on its configured `DATABASE_URL`.
- [ ] Redis reachable on its configured `REDIS_URL` (FSM storage).

## Runtime

- [ ] Bot starts and `set_my_commands` publishes the slash-command menu to
      Telegram (`register_bot_commands` on startup).
- [ ] faster-whisper model pre-pulled so the first voice message is not slow
      (default model size: `small`; `stt.warmup()` runs on startup but the
      model download should happen ahead of go-live, not on the hot path).
