# Handoff: go-live and ownership transfer

This is the complete runbook for the two remaining events in this project's
life: **turning the reminders on for the whole guild** (section 2) and
**transferring ownership to the guild leader** (section 3). Everything else
is finished: the system was fully live-tested on the real server on
July 17, 2026 — real DMs, real announcements, real officer reports.

**Never used GitHub?** Read [GITHUB-BASICS.md](GITHUB-BASICS.md) first
(5 minutes). Every step below is done in the web browser with normal
buttons. Nothing to install, no command line.

**How does it all work?** [GUIDE.md](GUIDE.md) — the owner's manual:
what the bot does, every setting explained, operations, troubleshooting.

**Day-to-day settings editing** never touches this repo directly:
**https://kcintv.github.io/raid-console/** is the settings GUI
(teams, wording, timings — with a Save button that deploys).

---

## 1. Current state (nothing here needs re-doing)

| Item | Status |
|---|---|
| Reminder DMs, digests, duplicate suppression | ✅ live-tested July 17, 2026 |
| Raid-time announcements (60-min lead → posts 45–60 min before start, evening schedule) | ✅ live-tested |
| Per-raid run reports to the officers chat (names, #channels) | ✅ live-tested |
| Secrets (`DISCORD_BOT_TOKEN`, `RAIDHELPER_API_KEY`) | ✅ in place |
| Workflows (Friday reminders + evening announcements) | ✅ enabled and running |
| Settings console (GUI) | ✅ live |
| **Audiences** | ⚠️ **still pointed at the test user only** — that is the go-live switch below |

---

## 2. Go-live: flip the audiences to the real teams

> **Agreed order (July 17, 2026): ownership transfers FIRST (section 3),
> then going live is the new owner's first act** — run this section as the
> new owner, ideally right after phase E proves the console works. Nothing
> below changes either way.

Doing this arms the system for the whole guild: the next Friday 5PM ET run
DMs every real non-signer, and raid-time announcements post in the real
signup channels. Two prerequisites, then a two-minute edit.

- [ ] **2.1 — Guild leader posts a heads-up** in the guild (recommended, one
      line: *"New bot: if you haven't signed up for a raid by Friday
      afternoon, it will DM you a reminder. Sign up and it leaves you
      alone."*) so ~40 people aren't surprised by a first-time DM.
- [ ] **2.2 — Pick the go-live moment.** Any time before the Friday 5PM ET
      run works; flipping mid-week simply means the coming Friday is the
      first real send.
- [ ] **2.3 — Make the edit** (either way):
      - **Console:** open the [settings console](https://kcintv.github.io/raid-console/)
        → *Teams & audiences* → for each team, clear the **user IDs** box and
        set the **Role IDs** box: `default` → `1361002868781351152` (@raiders),
        `teamRed` → `1527492157483520060`, `teamBlue` → `1527492255462719568`.
        Leave the `testers` team as is (it keeps test-channel events private
        to the tester forever). Click **Save & deploy**.
      - **GitHub:** edit `config.json` (pencil icon), make the same three
        role_ids changes in the `audiences` block (the correct values are
        also written in the `_comment_audiences` line right above it).
- [ ] **2.4 — Verify with a dry run** (sends nothing): console → **Trigger
      dry run**, or Actions tab → *Send signup reminders* → Run workflow →
      tick dry_run. Open the newest run → `remind` job.
      *Pass: each upcoming raid shows realistic "N expected, N responded,
      N missing" numbers and the "would DM" list is the real non-signers.*
- [ ] **2.5 — Done.** From here everything is automatic: Friday 5PM ET
      digest DMs to non-signers, announcements 45–60 min before each raid,
      a per-raid report in the officers chat after anything happens.

---

## 3. Ownership transfer to the guild leader

**Target end state:** the guild leader **owns** the repo (and everything
with it: secrets, settings, the bot's config); the previous owner stays on
as a **write collaborator** — able to edit config, run workflows, and help,
but no longer the person anything depends on.

Work top to bottom; each phase has a pass condition. Rough total: 30–40
minutes, all in the browser. Steps marked **[old owner]** are done by Mike,
**[new owner]** by the guild leader.

### Phase 0 — spin the new owner up BEFORE transferring (recommended)

Do this first: it lets the guild leader drive everything — edit settings,
trigger runs, read logs — for days or weeks while the old owner still owns
the repo and can help. The actual transfer (phase B) then changes nothing
about his day-to-day.

- [x] **0.1 [new owner]** — create a free GitHub account: github.com →
      Sign up. Tell the old owner the exact username.
- [x] **0.2 [old owner]** — repo → **Settings** → **Collaborators** (left
      sidebar) → **Add people** → type the username → **Add**. (No role to
      pick — on a personal repo a collaborator automatically gets **write**
      access: edit files, run workflows, read logs. Owner-only things —
      secrets, repo settings, transfers — stay with you until the transfer,
      and nothing in phase 0 needs them.)
- [x] **0.3 [new owner]** — accept the invitation (GitHub emails a link).
      *Pass: the repo appears when you're logged in as you.*
- [x] **0.4 [new owner]** — you can now do everything in the browser:
      - **Change settings:** open `config.json` → pencil icon → edit →
        Commit changes ([GITHUB-BASICS.md](GITHUB-BASICS.md) shows every
        button; [GUIDE.md section 7](GUIDE.md) explains every field).
      - **Safe test run:** **Actions** tab → *Send signup reminders* →
        **Run workflow** → tick **dry_run** → read the log.
      - **See what happened:** Actions tab logs + the officers-chat reports.
- [x] **0.5 [new owner, optional]** — to use the **settings console**
      before the transfer, you need a token. **You create it yourself, on
      YOUR GitHub account — tokens are personal credentials and are never
      created for you or shared with you by anyone** (your collaborator
      access from 0.3 is what grants the rights; the token just proves to
      the console that you are you, and makes every change show under your
      name). GitHub's *fine-grained* tokens only work on repos you own, so
      pre-transfer use a **classic** token:
      1. Logged in as **you**, open **github.com/settings/tokens** →
         **Generate new token** → **Generate new token (classic)**.
      2. Note: `raid-console`. Expiration: 90 days is plenty (this token
         retires at the transfer anyway, phase E1b).
      3. Tick exactly one scope: **repo**. Nothing else.
      4. **Generate token** (green, bottom) → copy the `ghp_…` value.
      5. Open the console → set **Repo owner** to the OLD owner's username
         (`superrcharge` — the repo still lives there) → paste the token →
         **Connect**. It's stored in your own browser; you won't be asked
         again on this device.
      On a fresh account this token reaches only this one repo. After the
      transfer you'll delete it and switch to a fine-grained token
      (phase E / E1b).

### Phase A — before transferring (10 min)

- [x] **A1 [new owner]** — GitHub account exists (done in phase 0).
- [x] **A2 [old owner]** — confirm the system is healthy: officers chat got
      its expected reports this week, or run a dry run (Actions tab) and see
      it pass. Don't transfer a broken system.

### Phase B — transfer the repo (5 min)

- [x] **B1 [old owner]** — repo → **Settings** (right-most tab) → scroll to
      the bottom **Danger Zone** → **Transfer ownership** → type the new
      owner's GitHub username → confirm.
- [x] **B2 [new owner]** — accept the transfer (GitHub emails you a link).
      *Pass: the repo now shows under YOUR account, at
      github.com/YOUR-NAME/RaidHelperReminder.*
- [x] **B3 — know what did NOT transfer** (by design, both of you):
      the two **secrets** (phase D re-adds them), the **Discord bot**
      (owned by the old owner's Discord account — phase C replaces it),
      and the **old owner's access** — transferring removes it.
- [x] **B4 [new owner]** — add the old owner back as a **collaborator** so
      he can keep helping: repo → **Settings** → **Collaborators** →
      **Add people** → `superrcharge` → Add; old owner accepts the email
      invite. (Same mechanics as phase 0.2, roles reversed. He gets write
      access; owner-only powers are now yours alone.)
      *Note for the old owner: your console sign-in also flips — the repo
      is no longer yours, so your fine-grained token stops working. Use the
      classic-token path from step 0.5 if you still want console access,
      and set Repo owner to the NEW owner's username.*

### Phase C — new owner's own Discord bot (10 min)

The bot that sends messages must belong to the new owner's Discord account,
so the old owner isn't a hidden dependency forever.

- [x] **C1 [new owner]** — create the bot app: follow
      [GUIDE.md section 5.1](GUIDE.md) exactly — it is field-tested and
      calls out every trap (the MFA prompt on Reset Token, the green
      **Save Changes** bar that silently discards the Server Members Intent
      toggle if missed, Public Bot, the invite needing Manage Server).
      You end up with: a bot token copied, **Server Members Intent ON and
      saved**, and your bot visible in the server's member list.
- [ ] **C2 [new owner]** — give the new bot access to the private channels
      the old bot had: the **officers chat** (run reports) and the
      **test channel** — for each: right-click the channel → Edit Channel →
      Permissions → Add members or roles → your bot → ✓ View Channel,
      ✓ Send Messages → Save Changes.
- [x] **C3 [old owner, after C4–D2 verified]** — kick the old bot from the
      server (right-click it in the member list → Kick) and optionally
      delete the old application at discord.com/developers/applications.

### Phase D — secrets (5 min)

- [x] **D1 [new owner]** — repo → **Settings** → **Secrets and variables →
      Actions** → **New repository secret**, create exactly these two
      (names must match character-for-character):
      - `DISCORD_BOT_TOKEN` — the token from C1
      - `RAIDHELPER_API_KEY` — in Discord type `/apikey` → **show**
        (needs admin; you have it). If the key might have been shared
        during setup, run `/apikey` → **refresh** first and use the new one.
- [x] **D2 — verify everything [new owner]** — **Actions** tab → *Send
      signup reminders* → **Run workflow** → mode `all`, tick **dry_run** →
      open the run → `remind` job.
      *Pass: "Fetched N upcoming event(s)", realistic expected/responded/
      missing numbers, no errors. This proves the new token, the API key,
      the intent toggle, and the bot's server membership all at once.
      (A `members fetch returned 403` here = the Save Changes bar trap or
      wrong server — see the troubleshooting table, GUIDE section 8.)*

### Phase E — settings console (5 min)

- [ ] **E1 [new owner]** — open
      **https://kcintv.github.io/raid-console/** → change **Repo
      owner** to your GitHub username → follow the on-page token steps
      (fine-grained token, only the RaidHelperReminder repo, Contents +
      Actions read/write — this works now because you OWN the repo) →
      **Connect**.
      *Pass: your real teams and messages appear in the forms.*
- [ ] **E1b [new owner, only if you did phase 0.5]** — retire the
      pre-transfer classic token: click **Sign out** in the console first,
      then github.com/settings/tokens → the `raid-console` classic token →
      **Delete**. Then do E1 with the fine-grained token. One token, least
      access, no leftovers.
- [ ] **E2 [new owner]** — prove the loop: change anything trivial (e.g.
      add a space to a message), **Save & deploy**, see the commit appear on
      the repo, then change it back.
- [x] *(optional)* **E3 [old owner]** — transfer the `raid-console` repo the
      same way as phase B so the page URL moves under the new owner's
      account too. Not required: the page is public code with no data in it,
      and it works for the new owner regardless of who hosts it.

### Phase F — day-to-day ownership (read, no clicks)

- Editing anything = the console (or `config.json` pencil-edit). Deploys on
  save. [GUIDE.md section 7](GUIDE.md) explains every field.
- Raid day/time changes need **nothing** — the bot follows the Raid-Helper
  events automatically.
- Reading what happened = the officers-chat reports; full detail in the
  **Actions** tab logs.
- Pausing everything = Actions tab → each workflow → "…" → Disable.
  Re-enable the same way. Troubleshooting table: [GUIDE.md section 8](GUIDE.md).

---

## 4. Also verify in Raid-Helper itself (no code — probably already done)

- [ ] Each raid night exists as a **weekly recurring event** posting into the
      right team's signup channel (premium feature).
- [ ] Events have `< response: ... >` set so sign-ups get the gear/consumes DM
      (premium advanced setting).
- [ ] `attendance` is on (default) — optionally tag per team
      (`< attendance: teamA >`) for per-team `/attendance` stats.

All three are set in each event's *advanced options* — easiest via the
Raid-Helper web dashboard (**raid-helper.dev** → Login, top right, with your
Discord account → your server → the event), or with the `/edit` command in
Discord. Details: [GUIDE.md section 2](GUIDE.md).
