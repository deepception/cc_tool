# Vault automation — pick ONE trigger

The vault is a proactive loop: a trigger runs `/vault process` with no human in
real time. Filing notes is routine work — run it on a small/cheap model and keep
the strongest models for judgment work.

## Option 1 — system cron (local; recommended start)

Runs on this machine, so the machine must be awake at trigger time — a laptop
that sleeps on lid-close will skip runs.

```cron
# crontab -e
# weekday mornings 07:00 — file the inbox, write the daily digest
0 7 * * 1-5 cd __PROJECT_DIR__ && claude -p --model sonnet --permission-mode acceptEdits "/vault process" >> /tmp/claude-vault-cron.log 2>&1
# Sunday 18:00 — weekly synthesis
0 18 * * 0 cd __PROJECT_DIR__ && claude -p --model sonnet --permission-mode acceptEdits "/vault synthesize" >> /tmp/claude-vault-cron.log 2>&1
```

Headless note: `claude -p` auto-denies anything on the settings.json `ask` list
(it can't show a prompt). `/vault process` only reads and edits files inside
`vault/` — covered by `acceptEdits`, no ask-listed commands — so it runs clean
unattended.

## Option 2 — `/schedule` (Anthropic cloud routine)

In a Claude Code session, create a routine: `/schedule run /vault process every
weekday at 07:00` (and a Sunday `/vault synthesize`). Runs in Anthropic's cloud —
no laptop involved — but the repo must be reachable there (e.g. on GitHub).

## Option 3 — `/loop` (long-lived local session)

In a session that stays up (tmux on an always-on box / VPS):
`/loop 12h /vault process`. Simplest wiring; dies with the session.

---

Empty inbox → the run exits immediately after one check, so a daily trigger on a
quiet vault costs almost nothing. Review spend occasionally with `/usage`.
