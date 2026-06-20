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
- [ ] `TEAM_CHAT_ID` set to the real team chat. Membership in this chat grants
      full write rights (commit/replan); there is no read-only tier (C5/R8).
- [ ] Notion (optional): `NOTION_TOKEN`, `NOTION_DATABASE_ID` (per-task sync),
      `NOTION_PARENT_PAGE_ID` (project master cards). All empty → keyless no-op.

## Data layer

- [ ] `alembic upgrade head` applied against the production database
      (latest revision **0008**).
- [ ] `python -m seed.load_seed` run once against the production DB — loads the
      real team + standard/lite templates (idempotent).
- [ ] Postgres reachable on its configured `DATABASE_URL`.
- [ ] Redis reachable on its configured `REDIS_URL` (FSM storage).
- [ ] **Daily `pg_dump`** scheduled via cron, e.g.:
      ```
      0 3 * * *  docker exec planner_dev-postgres-1 pg_dump -U planner planner \
                 | gzip > /backups/planner-$(date +\%F).sql.gz
      ```
      Keep ≥ 7 daily dumps; verify a restore at least once.

## Network exposure

- [ ] Admin web (`:8000`) is **not** publicly exposed — bind to loopback or
      reach it only over VPN / SSH tunnel. The bot long-polls outbound, so no
      inbound port is needed for it.

## Runtime

- [ ] Bot starts and `set_my_commands` publishes the slash-command menu to
      Telegram (`register_bot_commands` on startup).
- [ ] faster-whisper model pre-pulled so the first voice message is not slow
      (default model size: `small`; `stt.warmup()` runs on startup but the
      model download should happen ahead of go-live, not on the hot path).
- [ ] Daily summary job registered (`daily_load_summary`, 10:00 Europe/Moscow);
      it posts team load + overloads to `TEAM_CHAT_ID`.
