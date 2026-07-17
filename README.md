# RaidHelperReminder

> **First time here? Never used GitHub before? No problem — start with
> [docs/GITHUB-BASICS.md](docs/GITHUB-BASICS.md)** (5 minutes: the only four
> things you'll ever do on this site, all in the browser, nothing to
> install). Then [docs/HANDOFF.md](docs/HANDOFF.md) is the step-by-step
> checklist to get this running, and [docs/GUIDE.md](docs/GUIDE.md) explains
> how everything works and how it meets the original requirements.

Automates the two things [Raid-Helper](https://raid-helper.dev) can't do by
itself:

1. **Reminds people who HAVEN'T signed up.** DMs every member who is expected
   at a raid but hasn't responded — e.g. every Friday at 5PM ET, one digest
   DM listing all their unsigned raids for the week.
2. **Raid-time announcements.** Posts a custom message (e.g. "Raid invites
   has started - whisper Kcin or an Officer!") into the signup channel,
   pinging a role, N minutes before each raid.

Everything else — recurring weekly signups, the "bring your consumes" DM on
sign-up, attendance tracking — is native Raid-Helper (Premium) configuration,
covered step-by-step in the guide.

Features:

- **Audiences per team**: any set of Discord roles, explicit user lists, or
  "everyone who can see this channel" (computed from channel permissions —
  the workaround when channels don't map to roles). Rules route each event
  to the right team's audience.
- Configurable reminder windows — fixed weekly ("Friday 5PM") or escalating
  relative ones (48h/24h/2h before each event).
- Digest mode: one DM listing all unsigned raids instead of a pile of pings.
- Never sends anything twice (`state.json` audit trail).
- Run reports in Discord: after any run that sent something, posts a summary
  (who was DMed, what was announced) to a channel of your choice
  (`log_channel_id`) — day-to-day visibility without opening GitHub.
- People who responded *anything* (Bench, Late, Tentative, Absence) are left
  alone (configurable).
- Single Python file, standard library only. Nothing to install.
- Runs anywhere a script can run on a schedule: GitHub Actions (free,
  recommended), Windows Task Scheduler, cron.

## Go live — current status and remaining steps

> **Handing this over or picking it up fresh? Start with
> [docs/HANDOFF.md](docs/HANDOFF.md)** — the complete checklist: every value
> to collect and where it goes, exactly where secrets and variables are
> placed, and the ordered test plan with pass conditions.

### Live progress tracker

*Last updated: **July 17, 2026** — go-live testing in progress. This table is
kept current as each step completes; details for every step are in the
numbered list below and in [docs/HANDOFF.md](docs/HANDOFF.md).*

| # | Step | Status |
|---|------|--------|
| 1 | Create the Discord bot (token copied, **Server Members Intent** on) | ✅ July 17 |
| 2 | Invite the bot to the server (needs Manage Server — else send invite URL to guild leader) | ✅ July 17 — bot is in the server |
| 3 | Get the Raid-Helper API key (`/apikey` → show; needs elevated perms) | ✅ July 17 — key received from guild leader |
| 4 | Collect IDs: server, team roles, @raiders role, signup channels | ✅ July 17 — all 8 collected (Red: Tue+Thu, Blue: Wed+Sun) |
| 5 | Add repo secrets `DISCORD_BOT_TOKEN` + `RAIDHELPER_API_KEY` | ✅ July 17 — both verified present |
| 6 | Commit real `config.json` | ✅ July 17 — teamRed/teamBlue audiences, 4 channel rules, fallback off |
| 7 | Enable the Actions workflow | ✅ July 17 |
| 8 | Dry-run (HANDOFF 5.2) — proves secrets + IDs, sends nothing | ✅ July 17 — 2 events, both audiences resolve, digest works. (Fixed en route: script needed a real User-Agent — Cloudflare 403 — and a fresh bot token after multiple resets) |
| 9 | Live DM smoke test to one person only (HANDOFF 5.3) | ✅ July 17 — "1 DM(s) sent", to Mike alone; state.json committed by the run |
| 10 | Duplicate-suppression re-run (HANDOFF 5.4) | ✅ July 17 — same run again: "0 DM(s) sent" |
| 11 | Announcement smoke test (HANDOFF 5.5) | ✅ July 17 — 2 posts to test channel, re-run posts 0; bot channel-access failure found+fixed en route |
| 12 | Restore real config — **live** (HANDOFF 5.6–5.7) | 🔄 Announcements: KEEPING automated raid-time posts (60 min before, decided July 17). Audiences still Mike-only — the one remaining switch, pending go-live date with guild leader |

**Status right now:** all code and docs are in place and tested. The Actions
workflow is **manually disabled** so it doesn't fail-and-email every 15
minutes while the secrets are missing. Remaining steps, in order:

1. **Create the Discord bot** (once, ~5 min): [docs/GUIDE.md section 5.1](docs/GUIDE.md)
   — new application at discord.com/developers, copy the bot token, enable
   **Server Members Intent**, invite it to the server with Send Messages only.
2. **Get the Raid-Helper API key**: type `/apikey` in the Discord server,
   pick **show**.
3. **Add both as repo secrets**: **Settings** (right-most tab at the top of
   this page) → **Secrets and variables → Actions** →
   `DISCORD_BOT_TOKEN` and `RAIDHELPER_API_KEY`.
4. **Commit a real `config.json`**: copy `config.example.json`, fill in the
   server ID, team role/channel IDs, and the @raiders role ID for
   announcements ([docs/GUIDE.md section 7](docs/GUIDE.md) explains every field).
5. **Enable the workflow**: **Actions** tab (top of this page) → "Send
   signup reminders" → "…" menu → Enable workflow.
6. **Dry-run first**: Actions tab → **Run workflow** button → tick
   **dry_run** → read the run's log; it prints exactly who would get what
   without sending anything.
7. **Live smoke test**: set one audience's `user_ids` to just your own
   Discord ID, run for real, confirm the DM arrives, then restore the real
   config.

For handover: Settings → Transfer ownership; the new owner re-adds the two
secrets (secrets never transfer, by design).

## Settings GUI

**https://superrcharge.github.io/raid-console/** — a browser page for editing
every setting in `config.json` (teams, routing, wording, timings, report
channels) with friendly forms instead of raw JSON, plus a dry-run button.
First use needs a one-time GitHub token (2 minutes; the page walks you
through it). Source: [raid-console](https://github.com/superrcharge/raid-console)
(public repo, code only — all settings stay in this private repo).

## Quick start

1. Read **[docs/GUIDE.md](docs/GUIDE.md)** — the complete setup and operations
   guide, written for non-programmers (~15 minutes of one-time setup), plus
   the Raid-Helper Premium settings for recurring events, sign-up DMs, and
   attendance.
2. Copy `config.example.json` → `config.json`, fill in your IDs.
3. Provide the two secrets (`DISCORD_BOT_TOKEN`, `RAIDHELPER_API_KEY`) as
   environment variables — GitHub Actions secrets or `secrets.local.env`.
4. Test safely: `python remind.py --dry-run` prints who *would* get what,
   without sending anything.

## Project layout

| File | Purpose |
|---|---|
| `remind.py` | The whole program — heavily commented, readable top to bottom |
| `config.json` | All behavior: audiences, rules, windows, messages, announcements |
| `state.json` | Auto-managed memory of everything already sent |
| `.github/workflows/remind.yml` | GitHub Actions schedules (announcements every 15 min; reminders Friday 5PM ET) |
| `run_local.ps1` + `secrets.example.env` | Windows / Task Scheduler alternative |
| `docs/GITHUB-BASICS.md` | GitHub for first-timers — the four browser-only actions you'll ever need |
| `docs/HANDOFF.md` | The go-live/handover checklist: values, secrets, ordered test plan |
| `docs/GUIDE.md` | The full how-to and operations guide |
| `tests/` | Unit tests: `python -m unittest discover tests` |
