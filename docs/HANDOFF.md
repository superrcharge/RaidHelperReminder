# Handoff Checklist

This document is the complete handover: what's done, what's left to do, what's
left to test, and exactly where every secret and variable goes. Work through
it top to bottom. For background on *how* anything works, see
[GUIDE.md](GUIDE.md) — but this checklist stands on its own.

**Never used GitHub?** Read [GITHUB-BASICS.md](GITHUB-BASICS.md) first
(5 minutes). Every step below — editing the config, adding secrets, running
tests — is done in the web browser with normal buttons; that page shows
exactly which buttons. Nothing to install, no command line.

**Verifying the original asks were met?** [GUIDE.md section 1](GUIDE.md)
restates the five original requirements verbatim (recurring signups, Friday
5PM non-signer reminders, gear/consumes DM on signup, the 8:15PM "invites
started" post, attendance tracking) and traces each one to exactly how and
where it is addressed — which ones are Raid-Helper Premium configuration and
which ones this project provides. Section 3 of the guide explains how the
automation works in plain language, with a diagram.

---

## 1. Current state (nothing here needs re-doing)

| Item | Status |
|---|---|
| Reminder script (`remind.py`) | Complete. 27 unit tests pass. |
| Digest DMs (one DM listing all unsigned raids) | Complete, tested. |
| Per-team audiences (roles / user lists / channel access) | Complete, tested. |
| Raid-time announcements ("invites started", @role ping) | Complete, tested. |
| Duplicate prevention (`state.json`) | Complete, tested. |
| GitHub Actions schedules (announcements every 15 min; reminders Fri 5PM ET) | In place, **manually disabled** (step 5 enables it). |
| Windows Task Scheduler alternative (`run_local.ps1`) | Complete. |
| Live test against a real Discord server | **NOT done yet** — needs the credentials below. That is what sections 3-5 walk through. |

Nothing has ever been sent to anyone. The workflow is disabled specifically
so it cannot run (and fail) before the secrets exist.

---

## 2. Values to collect (do this first)

Gather these eight values before touching anything else.

| # | Value | Where to get it | Where it goes |
|---|---|---|---|
| 1 | **Discord bot token** | Go to **discord.com/developers/applications** (the "Discord Developer Portal" - a website; log in with your normal Discord account) -> **New Application** button (top right; name it e.g. "Raid Reminder") -> **Bot** (left sidebar) -> **Reset Token** button. While on that Bot page: scroll to *Privileged Gateway Intents* and enable **Server Members Intent**. Then **OAuth2** (left sidebar) -> *URL Generator* -> check `bot` + permission **Send Messages** -> open the generated URL (bottom of page) to invite the bot to your server. | Secret `DISCORD_BOT_TOKEN` (section 3) |
| 2 | **Raid-Helper API key** | In the Discord app, in any channel's message box in your server, type `/apikey` and pick **show**. Raid-Helper replies with a message only you can see. | Secret `RAIDHELPER_API_KEY` (section 3) |
| 3 | **Server ID** | Discord app: **User Settings** (gear icon next to your username, bottom-left) -> **Advanced** -> enable **Developer Mode** (one-time; it adds the "Copy ID" right-click options used below). Then right-click the **server name** (very top of the left sidebar) -> **Copy Server ID**. | `config.json`: `discord.guild_id` AND `raidhelper.server_id` (same value in both) |
| 4 | **Team A role ID** | Discord app: click the server name (top-left) -> **Server Settings** -> **Roles** -> right-click the role -> **Copy Role ID** | `config.json`: `audiences.teamA.role_ids` |
| 5 | **Team B role ID** | same | `config.json`: `audiences.teamB.role_ids` |
| 6 | **@raiders role ID** (the role the 8:15 announcement pings) | same | `config.json`: `announcements[0].mention_role_ids` |
| 7 | **Team A + Team B signup channel IDs** | Discord app: right-click each signup channel (in the channel list, left side) -> **Copy Channel ID** | `config.json`: `audience_rules` (routes each event to the right team) |
| 8 | *(optional)* **Fallback channel ID** (public ping for members whose DMs are closed) | same | `config.json`: `discord.fallback_channel_id` ("" = feature off) |

> If channel access does NOT line up with team roles, an audience can instead
> use `"channel_access": "<channelId>"` — everyone who can see that channel
> counts as expected. Caveats (admins match everywhere; the bot needs access
> to that channel) in [GUIDE.md section 4](GUIDE.md).

---

## 3. Where the SECRETS go (values 1 and 2 - never in files)

**GitHub (the hosting we set up):**

1. Open the repo on github.com
2. Click **Settings** - the right-most tab in the row along the top of the project page (Code / Issues / ... / Settings). This is the *project's* settings, not your account's. Then in the left sidebar: **Secrets and variables -> Actions -> New repository secret** (green button).
3. Create exactly these two, names must match character-for-character:
   - Name: `DISCORD_BOT_TOKEN` - Value: the bot token
   - Name: `RAIDHELPER_API_KEY` - Value: the API key

That's the only place secrets live. They are write-only (nobody can read them
back, not even the owner) and are NOT copied if the repo is transferred - a
new owner re-adds them (2 minutes, by design).

**Only if running on a PC instead of GitHub:** copy `secrets.example.env` to
`secrets.local.env` next to the script and fill in the two lines. That file
is gitignored and never leaves the machine.

Never put either value in `config.json`, in a commit, or in Discord chat.
If a value ever leaks: bot token -> Discord Developer Portal (discord.com/developers/applications, same site as value 1) -> Bot -> Reset Token;
API key -> `/apikey` -> refresh. Then update the secret.

---

## 4. Where the VARIABLES go (values 3-8 - the config file)

Create `config.json` in the repo root (root = the top-level file list you see on the project's front page, next to README) — entirely in the browser: open
`config.example.json`, copy its contents, then repo front page -> **Add file
-> Create new file**, name it `config.json`, paste, replace the placeholder
IDs with values 3-8, and click **Commit changes** (commit = save; see
[GITHUB-BASICS.md](GITHUB-BASICS.md)). All later edits use the pencil icon
on the file. The example file is already
shaped for this exact setup - two teams, Friday digest reminders, the 8:15
"invites started" announcement with the Kcin wording - so it's fill-in-the-
blanks, not authoring. Every field is explained in
[GUIDE.md section 7](GUIDE.md); the ones you will actually touch:

- `discord.guild_id` + `raidhelper.server_id` <- value 3 (same ID, both places)
- `audiences.teamA` / `teamB` `role_ids` <- values 4, 5
- `audience_rules` channel IDs <- value 7 (maps each signup channel to its team)
- `announcements[0].mention_role_ids` <- value 6
- `announcements[0].text` <- already contains the requested wording; edit freely
- `discord.fallback_channel_id` <- value 8 or leave `""`
- `reminder_windows_hours: [168]` + the Friday cron = weekly Friday-5PM-ET
  reminders. Don't change unless the cadence changes.

Changing anything later = edit `config.json`, commit. That's the whole
deployment process.

---

## 5. What's left to TEST (in this order)

Each step has a pass condition. Stop at any failure and check the
troubleshooting table in [GUIDE.md section 8](GUIDE.md).

**5.1 - Enable the workflow.** **Actions** tab (in the same top tab row as Settings) -> **Send signup reminders** (left sidebar) -> the **"..."** menu (top right, next to the search box) -> **Enable workflow**.
*Pass: the workflow shows as enabled.*

**5.2 - Dry run (sends nothing, ever).** Actions tab -> Send signup
reminders -> **Run workflow** (grey dropdown button on the right side of the
blue banner) -> mode `all`, tick **dry_run** -> green **Run workflow**. The
run appears in the list after a few seconds; click it, then click the
**remind** job to read the log.
*Pass: the log lists each upcoming event with "N expected, N responded,
N missing" using numbers that match reality, and "[dry-run] would DM ..."
lines name the right people. Both secrets and all IDs are proven correct at
this point. Nothing was sent.*

**5.3 - Live DM smoke test (one person only).** In `config.json`, temporarily
change ONE audience to `{ "user_ids": ["YOUR_OWN_DISCORD_USER_ID"] }` (right-click
your own name in the member list on the right side of any channel, or on one
of your messages -> **Copy User ID**), commit, and Run workflow with mode
`reminders`, dry_run OFF, while you are not signed up to that team's event.
*Pass: exactly one DM arrives, to you, with correct event title, local time,
and a working signup link. The run's final commit updates `state.json`.*

**5.4 - Duplicate suppression.** Run the workflow again with the same settings.
*Pass: log says nothing new to send; no second DM.*

**5.5 - Announcement smoke test.** Within 15 minutes before a raid start
(or create a throwaway test event starting in ~10 minutes, with Raid-Helper's `/create` in any private test channel), Run workflow with
mode `announcements`, dry_run OFF.
*Pass: one message appears in the event's signup channel pinging @raiders
with the "Raid invites has started..." text. Re-running posts no duplicate.*

**5.6 - Restore the real config.** Revert the 5.3 audience change, commit.
*Pass: `config.json` matches section 4 again.*

**5.7 - Done.** Leave the workflow enabled. From here on: signups create
themselves (Raid-Helper recurring events), Friday 5PM ET the non-signers get
their digest DM, 8:15 PM the invite announcement posts, attendance tracks
in Raid-Helper. Zero manual steps per event.

*(Optional 5.8 - fallback ping: have a member disable "Direct Messages from
server members" (Discord: click the server name -> **Privacy Settings** -> toggle off **Direct Messages**), leave them unsigned, run reminders. Pass: they get publicly
pinged in the fallback channel instead.)*

---

## 6. Also verify in Raid-Helper itself (no code - probably already done)

- [ ] Each raid night exists as a **weekly recurring event** posting into the
      right team's signup channel (premium feature).
- [ ] Events have `< response: ... >` set so sign-ups get the gear/consumes DM
      (premium advanced setting).
- [ ] `attendance` is on (default) - optionally tag per team
      (`< attendance: teamA >`) for per-team `/attendance` stats.

All three are set in each event's *advanced options* - easiest via the
Raid-Helper web dashboard (**raid-helper.dev** -> Login, top right, with your
Discord account -> your server -> the event), or with the `/edit` command in
Discord. Details for all three: [GUIDE.md section 2](GUIDE.md).

---

## 7. Ownership transfer (when handing the repo over)

1. Repo -> Settings -> General -> Danger Zone -> **Transfer ownership** to the
   new owner's GitHub account.
2. New owner: re-add the two secrets (section 3) - secrets do not transfer.
3. New owner: confirm the workflow is still enabled (Actions tab) and click
   Run workflow -> dry_run as a sanity check.
4. **The Discord bot application** is owned by the previous owner's Discord
   account and does NOT transfer with the repo. Easiest fix (10 min): the new
   owner creates their *own* application (HANDOFF section 2, value 1 — same
   steps: New Application -> Bot -> Reset Token -> enable Server Members
   Intent -> OAuth2 invite with View Channels + Send Messages), then replaces
   the `DISCORD_BOT_TOKEN` secret with their token. The config does not care
   which bot sends the messages. The old bot can then be kicked from the
   server and its application deleted.
5. Optional: previous owner deletes their local clone; nothing secret is in it.

Day-to-day ownership = editing `config.json` (roles, times, wording) and
reading the Actions log when curious. The operations reference, including
pausing, secret rotation, and the troubleshooting table, is
[GUIDE.md section 8](GUIDE.md).
