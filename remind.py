#!/usr/bin/env python3
"""
RaidHelperReminder - DM people who are expected at a raid but haven't signed up,
and post scheduled announcements around raid time.

What this script does, in one paragraph:
It asks Raid-Helper "what events are coming up on our Discord server?", and for
each event that is inside one of the configured reminder windows (e.g. 48h and
24h before start) it compares the sign-up list against the Discord members who
are expected to attend (the "audience" - defined by roles, an explicit user
list, or channel access). Everyone expected who has not responded gets a direct
message reminding them to sign up - either one DM per event, or a single
"digest" DM listing every unsigned raid. It can also post an announcement into
the event's channel shortly before raid time (e.g. "invites have started").
A small state file remembers what was already sent so nothing is ever sent
twice. Run it on a schedule (GitHub Actions, Task Scheduler, cron) and the
whole process is hands-off.

Design constraints:
- Pure Python standard library. No pip installs, nothing to break.
- All behavior lives in config.json. Secrets live in environment variables:
    DISCORD_BOT_TOKEN   - token of the Discord bot that sends the DMs
    RAIDHELPER_API_KEY  - Raid-Helper server API key (get it with /apikey)
- Safe to re-run at any frequency: state.json makes everything idempotent.

Usage:
    python remind.py                        # reminders + announcements
    python remind.py --mode reminders       # only the sign-up reminders
    python remind.py --mode announcements   # only the raid-time announcements
    python remind.py --dry-run              # show what WOULD be sent
    python remind.py --config other.json --state other-state.json
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

RAIDHELPER_API = "https://raid-helper.xyz/api/v4"
DISCORD_API = "https://discord.com/api/v10"

# Discord permission bits (https://discord.com/developers/docs/topics/permissions)
ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
MENTION_EVERYONE = 1 << 17   # also governs pinging roles that are not mentionable

# ---------------------------------------------------------------------------
# Small HTTP helper (stdlib only)
# ---------------------------------------------------------------------------

def http_json(method, url, headers=None, body=None, max_retries=4):
    """Perform an HTTP request and decode the JSON response.

    Discord rate-limits aggressively; on 429 we sleep for the advised
    'retry_after' and try again. Returns (status_code, decoded_json).
    """
    data = None
    headers = dict(headers or {})
    # Discord's docs require a real User-Agent; its edge (Cloudflare) can
    # reject data-center requests carrying urllib's default one with an
    # HTML 403 that never reaches the API proper.
    headers.setdefault(
        "User-Agent",
        "RaidHelperReminder (https://github.com/kcintv/RaidHelperReminder, 1.0)",
    )
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                # Not JSON (e.g. a Cloudflare block page). Surface a snippet
                # of the raw body so error messages say what actually happened.
                payload = raw.decode("utf-8", "replace")[:300]
            if e.code == 429 and attempt < max_retries - 1:
                retry_after = 2.0
                if isinstance(payload, dict):
                    retry_after = float(payload.get("retry_after", retry_after))
                time.sleep(retry_after + 0.5)
                continue
            return e.code, payload
    return 429, None


# ---------------------------------------------------------------------------
# Raid-Helper API
# ---------------------------------------------------------------------------

def fetch_upcoming_events(server_id, api_key, horizon_seconds):
    """Fetch all events starting between now and now+horizon, with sign-ups."""
    now = int(time.time())
    headers = {
        "Authorization": api_key,
        "IncludeSignUps": "true",
        "StartTimeFilter": str(now),
        "EndTimeFilter": str(now + horizon_seconds),
    }
    events = []
    page = 1
    while True:
        headers["Page"] = str(page)
        status, payload = http_json(
            "GET", f"{RAIDHELPER_API}/servers/{server_id}/events", headers=headers
        )
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(
                f"Raid-Helper API returned {status}: {payload!r} - "
                "check RAIDHELPER_API_KEY (refresh with /apikey in Discord)."
            )
        events.extend(payload.get("postedEvents") or [])
        if page >= int(payload.get("pages") or 1):
            break
        page += 1
    return events


def fetch_event_details(event_id):
    """Fetch a single event (includes sign-ups; no auth required)."""
    status, payload = http_json("GET", f"{RAIDHELPER_API}/events/{event_id}")
    if status != 200 or not isinstance(payload, dict) or payload.get("status") == "failed":
        return None
    return payload


def event_signups(event):
    """Return the sign-up list for an event, fetching details if missing."""
    signups = event.get("signUps")
    if signups is None:
        details = fetch_event_details(event.get("id"))
        signups = (details or {}).get("signUps") or []
    return signups


# ---------------------------------------------------------------------------
# Discord API
# ---------------------------------------------------------------------------

def discord_headers(token):
    return {"Authorization": f"Bot {token}"}


def fetch_guild_members(guild_id, token):
    """Fetch ALL members of the guild (paginated, 1000 per page).

    Requires the 'Server Members Intent' toggle on the bot application.
    """
    members = []
    after = "0"
    while True:
        url = f"{DISCORD_API}/guilds/{guild_id}/members?limit=1000&after={after}"
        status, payload = http_json("GET", url, headers=discord_headers(token))
        if status != 200 or not isinstance(payload, list):
            raise RuntimeError(
                f"Discord members fetch returned {status}: {payload!r} - "
                "check DISCORD_BOT_TOKEN and that Server Members Intent is enabled."
            )
        members.extend(payload)
        if len(payload) < 1000:
            break
        after = payload[-1]["user"]["id"]
    return members


def fetch_guild_roles_raw(guild_id, token):
    """Fetch all guild roles as the raw Discord objects."""
    status, payload = http_json(
        "GET", f"{DISCORD_API}/guilds/{guild_id}/roles", headers=discord_headers(token)
    )
    if status != 200 or not isinstance(payload, list):
        raise RuntimeError(f"Discord roles fetch returned {status}: {payload!r}")
    return payload


def fetch_guild_roles(guild_id, token):
    """Fetch all guild roles as {role_id: permissions_int}."""
    return {str(r["id"]): int(r.get("permissions") or 0)
            for r in fetch_guild_roles_raw(guild_id, token)}


def fetch_channel_overwrites(channel_id, token):
    """Fetch a channel's permission overwrites (the bot must be able to see it)."""
    status, payload = http_json(
        "GET", f"{DISCORD_API}/channels/{channel_id}", headers=discord_headers(token)
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(
            f"Discord channel fetch returned {status}: {payload!r} - "
            "for channel_access audiences the bot itself needs access to that channel."
        )
    return payload.get("permission_overwrites") or []


def send_dm(user_id, content, token):
    """DM a user. Returns True on success, False if their DMs are closed."""
    status, payload = http_json(
        "POST",
        f"{DISCORD_API}/users/@me/channels",
        headers=discord_headers(token),
        body={"recipient_id": user_id},
    )
    if status != 200 or not isinstance(payload, dict):
        return False
    channel_id = payload["id"]
    status, _ = http_json(
        "POST",
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=discord_headers(token),
        body={"content": content},
    )
    return status == 200


def send_channel_message(channel_id, content, token):
    status, _ = http_json(
        "POST",
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=discord_headers(token),
        body={"content": content},
    )
    return status == 200


# ---------------------------------------------------------------------------
# Audiences - who is "expected" at an event
#
# An audience can be defined three ways (combinable; a member qualifies if
# they match ANY of them):
#   role_ids:       members holding at least one of these roles (recommended)
#   user_ids:       an explicit list of member ids (escape hatch)
#   channel_access: everyone who can SEE the given channel - computed from the
#                   channel's permission overwrites. The workaround for setups
#                   where channel access doesn't line up with a single role.
# ---------------------------------------------------------------------------

def pick_audience(event, config):
    """Decide which audience an event should target.

    audience_rules are checked in order; the first match wins. A rule matches
    on the event's channel id, a case-insensitive title substring, or the
    template id. If nothing matches, the 'default' audience is used.
    Returns (audience_name, audience_spec_dict).
    """
    audiences = config.get("audiences") or {}
    for rule in config.get("audience_rules") or []:
        if rule_matches(rule.get("match") or {}, event):
            name = rule.get("audience")
            if name in audiences:
                return name, audiences[name]
    return "default", audiences.get("default") or {}


def announcement_mentions(ann, event, config):
    """The ping prefix for an announcement.

    With "mention_audience": true the post pings whatever audience the event
    already resolves to - Red raids ping Raid Team Red, Blue ping Raid Team
    Blue - so the ping follows the same channel rules as the reminder DMs and
    there is no second mapping to keep in sync. Falls back to the literal
    mention_role_ids when the audience carries no roles.
    """
    if ann.get("mention_audience"):
        _name, spec = pick_audience(event, config)
        parts = [f"<@&{rid}> " for rid in spec.get("role_ids") or []]
        parts += [f"<@{uid}> " for uid in spec.get("user_ids") or []]
        if parts:
            return "".join(parts)
    return "".join(f"<@&{rid}> " for rid in ann.get("mention_role_ids") or [])


def rule_matches(match, event):
    if "channel_id" in match and str(event.get("channelId")) != str(match["channel_id"]):
        return False
    if "title_contains" in match and match["title_contains"].lower() not in (event.get("title") or "").lower():
        return False
    if "template_id" in match and str(event.get("templateId")) != str(match["template_id"]):
        return False
    return True


def member_channel_permissions(member, roles_map, everyone_role_id, overwrites):
    """Compute a member's effective permissions in a channel.

    Standard Discord algorithm: base = union of role permissions;
    Administrator bypasses everything; then apply @everyone overwrite,
    aggregated role overwrites, and finally the member overwrite.
    """
    everyone_id = str(everyone_role_id)
    member_roles = set(map(str, member.get("roles") or []))
    base = roles_map.get(everyone_id, 0)
    for rid in member_roles:
        base |= roles_map.get(rid, 0)
    if base & ADMINISTRATOR:
        return ~0  # admins see everything
    allow_e = deny_e = allow_r = deny_r = allow_m = deny_m = 0
    uid = str((member.get("user") or {}).get("id"))
    for ow in overwrites or []:
        oid = str(ow.get("id"))
        typ = int(ow.get("type") or 0)
        allow = int(ow.get("allow") or 0)
        deny = int(ow.get("deny") or 0)
        if typ == 0 and oid == everyone_id:
            allow_e, deny_e = allow, deny
        elif typ == 0 and oid in member_roles:
            allow_r |= allow
            deny_r |= deny
        elif typ == 1 and oid == uid:
            allow_m, deny_m = allow, deny
    perms = base
    perms = (perms & ~deny_e) | allow_e
    perms = (perms & ~deny_r) | allow_r
    perms = (perms & ~deny_m) | allow_m
    return perms


def audience_members(members, spec, guild_id=None, roles_map=None, overwrites_for=None):
    """Members (excluding bots) that belong to the audience spec.

    roles_map/overwrites_for are only needed when spec uses channel_access:
    roles_map is {role_id: permissions_int}; overwrites_for(channel_id)
    returns that channel's permission overwrites.
    """
    role_ids = set(map(str, spec.get("role_ids") or []))
    user_ids = set(map(str, spec.get("user_ids") or []))
    channel_id = spec.get("channel_access")
    overwrites = overwrites_for(channel_id) if (channel_id and overwrites_for) else None

    result = []
    for m in members:
        user = m.get("user") or {}
        if user.get("bot"):
            continue
        uid = str(user.get("id"))
        ok = False
        if role_ids and role_ids & set(map(str, m.get("roles") or [])):
            ok = True
        if not ok and uid in user_ids:
            ok = True
        if not ok and channel_id and roles_map is not None:
            perms = member_channel_permissions(m, roles_map, guild_id, overwrites)
            ok = bool(perms & VIEW_CHANNEL)
        if ok:
            result.append(m)
    return result


def responded_user_ids(signups, treat_as_no_response):
    """User ids that have responded to the event in any way.

    By default every sign-up counts as a response - including Bench, Late,
    Tentative and Absence - because those people DID look at the event.
    Class names listed in config 'treat_as_no_response' (e.g. ["Tentative"])
    are excluded, so those members keep getting reminded.
    """
    ignore = {c.lower() for c in (treat_as_no_response or [])}
    ids = set()
    for s in signups or []:
        if (s.get("className") or "").lower() in ignore:
            continue
        uid = s.get("userId")
        if uid:
            ids.add(str(uid))
    return ids


def member_display_name(member):
    if not member:
        return "there"
    user = member.get("user") or {}
    return member.get("nick") or user.get("global_name") or user.get("username") or "there"


# ---------------------------------------------------------------------------
# Timing and message formatting
# ---------------------------------------------------------------------------

def _nth_sunday(year, month, n):
    """Date of the nth Sunday of a month (n starts at 1)."""
    first = datetime.date(year, month, 1)
    # weekday(): Monday=0 ... Sunday=6
    return first + datetime.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def eastern_offset_hours(ts):
    """US Eastern's UTC offset at a unix timestamp: -4 in DST, else -5.

    Worked out arithmetically rather than with zoneinfo on purpose - this
    project ships zero dependencies, and zoneinfo needs the tzdata package on
    Windows, where run_local.ps1 runs. US rule (unchanged since 2007): DST
    starts 2AM local on the 2nd Sunday in March (07:00 UTC) and ends 2AM local
    on the 1st Sunday in November (06:00 UTC).
    """
    dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None)
    starts = datetime.datetime.combine(_nth_sunday(dt.year, 3, 2), datetime.time(7, 0))
    ends = datetime.datetime.combine(_nth_sunday(dt.year, 11, 1), datetime.time(6, 0))
    return -4 if starts <= dt < ends else -5


def eastern_hour(ts):
    """The hour of day (0-23) in US Eastern at a unix timestamp."""
    dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None)
    return (dt + datetime.timedelta(hours=eastern_offset_hours(ts))).hour


def windows_due(start_time, windows_hours, now):
    """Reminder windows that currently apply to an event.

    A window W applies when the event starts within the next W hours.
    Windows for events already started never apply.
    """
    seconds_left = start_time - now
    if seconds_left <= 0:
        return []
    return [w for w in windows_hours if seconds_left <= w * 3600]


def event_title(event, cap=80):
    """A short, single-line raid name fit to drop into a sentence.

    Raid-Helper's `title` field is the whole embed heading INCLUDING the
    description - newlines, "bring consumables", a channel link, the lot. Pasted
    raw into a DM that reads "you haven't signed up for **...**" it produces a
    wall of text with a bare URL in the middle of it. Take the first line only
    and cap it.
    """
    raw = (event.get("title") or "").split("\n")[0]
    raw = " ".join(raw.split())
    if not raw:
        return "our next event"
    return raw if len(raw) <= cap else raw[:cap - 1].rstrip() + "…"


def format_message(template, event, member, guild_id):
    start = int(event.get("startTime") or 0)
    signup_link = "https://discord.com/channels/{}/{}/{}".format(
        guild_id, event.get("channelId"), event.get("id")
    )
    return (
        template.replace("{member_name}", member_display_name(member))
        .replace("{event_title}", event_title(event))
        .replace("{event_time}", f"<t:{start}:F>")
        .replace("{event_time_relative}", f"<t:{start}:R>")
        .replace("{signup_link}", signup_link)
    )


def pick_template(config, window):
    messages = config.get("messages") or {}
    per_window = messages.get("per_window") or {}
    return per_window.get(str(window)) or messages.get("default") or (
        "Hey {member_name}! You haven't signed up for **{event_title}** yet "
        "({event_time}, {event_time_relative}). Please sign up here: {signup_link}"
    )


def build_digest(config, items, member, guild_id):
    """One DM covering several unsigned events. items = [(event, window), ...]"""
    messages = config.get("messages") or {}
    header = messages.get("digest_header") or (
        "Hey {member_name}! You haven't signed up for these upcoming raids yet:"
    )
    line = messages.get("digest_line") or (
        "- **{event_title}** {event_time} ({event_time_relative}) - sign up: {signup_link}"
    )
    parts = [format_message(header, items[0][0], member, guild_id)]
    for event, _window in sorted(items, key=lambda it: int(it[0].get("startTime") or 0)):
        parts.append(format_message(line, event, member, guild_id))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# State file - the memory that prevents anything being sent twice
# ---------------------------------------------------------------------------

def load_state(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"events": {}}


def save_state(state, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def prune_state(state, now, grace_seconds=86400):
    """Forget events that started more than a day ago."""
    events = state.get("events") or {}
    state["events"] = {
        eid: rec
        for eid, rec in events.items()
        if int(rec.get("startTime") or 0) + grace_seconds > now
    }


def event_record(state, event):
    events = state.setdefault("events", {})
    return events.setdefault(
        str(event.get("id")),
        {"startTime": int(event.get("startTime") or 0), "sent": [], "announced": []},
    )


def already_sent(state, event_id, window, user_id):
    rec = (state.get("events") or {}).get(str(event_id)) or {}
    return f"{window}:{user_id}" in (rec.get("sent") or [])


def mark_sent(state, event, window, user_id):
    rec = event_record(state, event)
    key = f"{window}:{user_id}"
    if key not in rec.setdefault("sent", []):
        rec["sent"].append(key)


def already_announced(state, event_id, announce_key):
    rec = (state.get("events") or {}).get(str(event_id)) or {}
    return announce_key in (rec.get("announced") or [])


def mark_announced(state, event, announce_key):
    rec = event_record(state, event)
    if announce_key not in rec.setdefault("announced", []):
        rec["announced"].append(announce_key)


# ---------------------------------------------------------------------------
# Reminders - DM the people who haven't signed up
# ---------------------------------------------------------------------------

def run_reminders(config, state, events, now, dry_run, bot_token, ctx, log,
                  report=None, send_dms=True):
    """Work out who hasn't signed up, and optionally DM them.

    send_dms=True  (Friday 5PM ET) - DM each non-signer and report who got one.
    send_dms=False (Sunday 10AM ET) - "summary" mode: post the still-unsigned
    list to officers chat and send nothing. It must NOT mark_sent, or it would
    silently dedup away the following Friday's DMs for everyone it listed.
    """
    if report is None:
        report = {"events": {}, "failed": []}
    guild_id = config["discord"]["guild_id"]
    windows = sorted(config.get("reminder_windows_hours") or [24])
    fallback_channel = (config.get("discord") or {}).get("fallback_channel_id") or ""
    treat_as_no_response = config.get("treat_as_no_response") or []
    digest = bool(config.get("digest_dms"))

    # plans[user_id] = {"member": ..., "items": [(event, min_due_window)]}
    plans = {}
    dm_count = 0

    for event in events:
        title = event_title(event)
        start = int(event.get("startTime") or 0)
        closing = int(event.get("closingTime") or 0)
        due = windows_due(start, windows, now)
        if not due:
            continue
        if closing and closing < now:
            log(f"- '{title}': sign-ups already closed, skipping.")
            continue

        audience_name, spec = pick_audience(event, config)
        if not (spec.get("role_ids") or spec.get("user_ids") or spec.get("channel_access")):
            log(f"- '{title}': audience '{audience_name}' is empty, skipping.")
            continue

        members = ctx.get_members()
        responded = responded_user_ids(event_signups(event), treat_as_no_response)
        expected = audience_members(
            members, spec,
            guild_id=guild_id,
            roles_map=ctx.get_roles_map() if spec.get("channel_access") else None,
            overwrites_for=ctx.get_overwrites if spec.get("channel_access") else None,
        )
        missing = [m for m in expected if str(m["user"]["id"]) not in responded]

        log(
            f"- '{title}' starts <t:{start}> | audience '{audience_name}': "
            f"{len(expected)} expected, {len(responded)} responded, {len(missing)} missing."
        )
        if missing:
            log(f"  Still unsigned for '{title}' ({len(missing)}): "
                + names_list(missing))
            # The officer-facing "still unsigned" list belongs to the Sunday
            # summary run only. On Friday the report says who was DMed; adding
            # the same 40 names underneath it just buries that.
            if not send_dms:
                report_event(report, event)["unsigned"] = names_list(missing)

        if not send_dms:
            continue

        for member in missing:
            uid = str(member["user"]["id"])
            unsent = [w for w in due if not already_sent(state, event.get("id"), w, uid)]
            if not unsent:
                continue
            plan = plans.setdefault(uid, {"member": member, "items": []})
            plan["items"].append((event, min(due)))

    # Send: one digest DM per member, or one DM per member per event.
    dms_closed = []
    for uid, plan in plans.items():
        member, items = plan["member"], plan["items"]
        if digest and len(items) > 1:
            contents = [build_digest(config, items, member, guild_id)]
        else:
            contents = [
                format_message(pick_template(config, window), event, member, guild_id)
                for event, window in items
            ]
        delivered = True
        for content in contents:
            if dry_run:
                log(f"  [dry-run] would DM {member_display_name(member)} ({uid}): {content}")
            elif not send_dm(uid, content, bot_token):
                delivered = False
        if not dry_run:
            if delivered:
                dm_count += len(contents)
                log(f"  DM sent to {member_display_name(member)} ({uid}).")
                for event, _w in items:
                    report_event(report, event)["dms"].append(member_display_name(member))
            else:
                dms_closed.append(member)
                log(f"  DMs closed for {member_display_name(member)} ({uid}).")
                for event, _w in items:
                    report_event(report, event)["closed"].append(member_display_name(member))
        # Mark every due window for every event covered, so no other window
        # of the same event re-pings this member later in the same cycle.
        for event, _w in items:
            due = windows_due(int(event.get("startTime") or 0), windows, now)
            for w in due:
                mark_sent(state, event, w, uid)

    if dms_closed and fallback_channel and not dry_run:
        mentions = " ".join(f"<@{m['user']['id']}>" for m in dms_closed)
        text = (config.get("messages") or {}).get("fallback") or (
            "(couldn't DM you) You have upcoming raids you haven't signed up "
            "for - please check the signup channels!"
        )
        send_channel_message(fallback_channel, f"{mentions} {text}", bot_token)
        log(f"  Fallback channel ping sent for {len(dms_closed)} member(s).")

    if not send_dms:
        listed = sum(1 for e in report["events"].values() if e["unsigned"])
        log(f"Summary done. {listed} raid(s) with unsigned members; no DMs sent.")
    else:
        log(f"Reminders done. {dm_count} DM(s) sent." if not dry_run
            else "Reminders done (dry run).")


# ---------------------------------------------------------------------------
# Announcements - channel messages around raid time
# ---------------------------------------------------------------------------

def names_list(members, cap=30):
    """Readable comma list of member display names, capped."""
    names = ", ".join(member_display_name(m) for m in members[:cap])
    if len(members) > cap:
        names += f" (+{len(members) - cap} more)"
    return names


def report_event(report, event):
    """The per-raid bucket of the run report this event's facts go into."""
    eid = str(event.get("id"))
    return report.setdefault("events", {}).setdefault(eid, {
        "title": event_title(event),
        "start": int(event.get("startTime") or 0),
        "dms": [], "closed": [], "announced": None, "unsigned": "",
    })


def run_announcements(config, state, events, now, dry_run, bot_token, log, ctx=None,
                      report=None):
    if report is None:
        report = {"events": {}, "failed": []}
    """Post configured channel messages N minutes before each event starts.

    Example config entry (fires 15 minutes before start, in the event's own
    signup channel, pinging a role):
        { "minutes_before": 15,
          "mention_role_ids": ["ROLE_ID"],
          "text": "Raid invites has started for tonight's raid. ..." }
    Optional keys: "channel_id" (override the event's channel),
    "match" (same matching rules as audience_rules, to scope per team).
    """
    announcements = config.get("announcements") or []
    guild_id = config["discord"]["guild_id"]
    sent = 0

    for idx, ann in enumerate(announcements):
        minutes = int(ann.get("minutes_before") or 15)
        for event in events:
            if not rule_matches(ann.get("match") or {}, event):
                continue
            start = int(event.get("startTime") or 0)
            seconds_left = start - now
            if not (0 < seconds_left <= minutes * 60):
                continue
            key = f"{idx}:{minutes}"
            if already_announced(state, event.get("id"), key):
                continue
            mentions = announcement_mentions(ann, event, config)
            text = mentions + format_message(ann.get("text") or "", event, None, guild_id)
            channel = str(ann.get("channel_id") or event.get("channelId"))
            if dry_run:
                log(f"  [dry-run] would announce in channel {channel} for "
                    f"'{event_title(event)}': {text}")
            else:
                if send_channel_message(channel, text, bot_token):
                    sent += 1
                    log(f"  Announcement posted for '{event_title(event)}' in {channel}.")
                    chan_label = ctx.get_channel_name(channel) if ctx else str(channel)
                    report_event(report, event)["announced"] = chan_label
                    # Deliberately NO "still unsigned" list here: at T-60 the
                    # sign-up window is effectively over, so naming stragglers
                    # in officers chat is noise nobody can act on. That list
                    # belongs to the Friday digest run, where it says who was
                    # actually DMed. (Dropped July 22, 2026 at Mike's call.)
                else:
                    log(f"  FAILED to announce in channel {channel} - check bot access.")
                    chan_label = ctx.get_channel_name(channel) if ctx else str(channel)
                    report["failed"].append(
                        f"Announcement FAILED in {chan_label} - check the bot can view + send there.")
                    continue
            mark_announced(state, event, key)

    log(f"Announcements done. {sent} posted." if not dry_run else "Announcements done (dry run).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

class DiscordContext:
    """Lazy, cached access to guild data - fetched at most once per run."""

    def __init__(self, guild_id, token):
        self.guild_id = guild_id
        self.token = token
        self._members = None
        self._roles_map = None
        self._overwrites = {}
        self._chan_names = {}

    def get_members(self):
        if self._members is None:
            self._members = fetch_guild_members(self.guild_id, self.token)
        return self._members

    def get_roles_map(self):
        if self._roles_map is None:
            self._roles_map = fetch_guild_roles(self.guild_id, self.token)
        return self._roles_map

    def get_overwrites(self, channel_id):
        cid = str(channel_id)
        if cid not in self._overwrites:
            self._overwrites[cid] = fetch_channel_overwrites(cid, self.token)
        return self._overwrites[cid]

    def get_channel_name(self, channel_id):
        """'#channel-name' for run reports; falls back to the raw id."""
        cid = str(channel_id)
        if cid not in self._chan_names:
            status, payload = http_json(
                "GET", f"{DISCORD_API}/channels/{cid}",
                headers=discord_headers(self.token))
            name = payload.get("name") if (status == 200 and isinstance(payload, dict)) else None
            self._chan_names[cid] = f"#{name}" if name else cid
        return self._chan_names[cid]


def check_channels(config, bot_token, ctx, log):
    """Report whether the bot can actually post in every signup channel.

    Channel grants are the recurring failure in this project: they are per
    channel, they do not survive a channel being recreated, they died when the
    old bot was kicked, and Discord's "Sync Now" on a category silently drops
    the member override. Reading the boxes in the UI for four channels is
    error-prone, and the alternative - finding out at T-60 on raid night - is
    worse. This computes the bot's effective permissions the same way Discord
    does and prints a verdict per channel. Read-only: nothing is sent.
    """
    guild_id = config["discord"]["guild_id"]
    status, me = http_json("GET", f"{DISCORD_API}/users/@me",
                           headers=discord_headers(bot_token))
    if status != 200 or not isinstance(me, dict):
        raise RuntimeError(f"Could not identify the bot user: {status} {me!r}")
    bot_id = str(me.get("id"))
    log(f"Bot: {me.get('username')} ({bot_id})")

    status, self_member = http_json(
        "GET", f"{DISCORD_API}/guilds/{guild_id}/members/{bot_id}",
        headers=discord_headers(bot_token))
    if status != 200 or not isinstance(self_member, dict):
        raise RuntimeError(f"Bot is not a member of guild {guild_id}: {status}")

    # Signup channels get announcements (so they need to ping); the log and
    # fallback channels only need to receive plain messages.
    signup_channels = []
    for rule in config.get("audience_rules") or []:
        cid = (rule.get("match") or {}).get("channel_id")
        if cid and str(cid) not in signup_channels:
            signup_channels.append(str(cid))
    plain_channels = []
    for key in ("log_channel_id", "fallback_channel_id"):
        cid = (config.get("discord") or {}).get(key)
        if cid and str(cid) not in signup_channels:
            plain_channels.append(str(cid))

    roles_raw = fetch_guild_roles_raw(guild_id, bot_token)
    roles_map = {str(r["id"]): int(r.get("permissions") or 0) for r in roles_raw}
    role_info = {str(r["id"]): r for r in roles_raw}
    # MENTION_EVERYONE only matters for roles that are NOT mentionable: if a
    # role has "Allow anyone to @mention this role" on, any member can ping it
    # without the permission. Checking the channel bit alone reports a false
    # PROBLEM on a setup that demonstrably works.
    bot_can_ping_anything = False

    problems = []
    for cid in signup_channels + plain_channels:
        is_signup = cid in signup_channels
        name = ctx.get_channel_name(cid)
        try:
            overwrites = fetch_channel_overwrites(cid, bot_token)
        except Exception as e:                      # channel gone, or no access
            log(f"  {name} ({cid}): CANNOT READ - {e}")
            problems.append(f"{name}: channel unreadable (deleted, or bot not added)")
            continue
        perms = member_channel_permissions(self_member, roles_map, guild_id, overwrites)
        can_view = bool(perms & VIEW_CHANNEL)
        can_send = bool(perms & SEND_MESSAGES)
        missing = [n for n, ok in (("View Channel", can_view),
                                   ("Send Messages", can_send)) if not ok]

        ping_note = ""
        if is_signup:
            override = bool(perms & MENTION_EVERYONE) or bot_can_ping_anything
            # Which roles would this channel's announcement actually ping?
            fake = {"channelId": cid, "title": "", "id": "0"}
            _aud, spec = pick_audience(fake, config)
            want_roles = [str(r) for r in (spec.get("role_ids") or [])]
            blocked = [r for r in want_roles
                       if not role_info.get(r, {}).get("mentionable")]
            if blocked and not override:
                names = ", ".join(role_info.get(r, {}).get("name", r) for r in blocked)
                missing.append(f"cannot ping {names}")
                ping_note = f" ping={names}:BLOCKED"
            elif want_roles:
                names = ", ".join(role_info.get(r, {}).get("name", r) for r in want_roles)
                ping_note = f" ping={names}:ok"

        verdict = "OK" if not missing else "PROBLEM"
        log(f"  {name} ({cid}): view={can_view} send={can_send}{ping_note} -> {verdict}")
        if missing:
            problems.append(f"{name}: {', '.join(missing)}")

    if problems:
        log("CHANNEL PERMISSION PROBLEMS:")
        for p in problems:
            log(f"  - {p}")
    else:
        log(f"All {len(signup_channels) + len(plain_channels)} channel(s) OK.")
    return problems


def send_hour_ok(config, key, now, ignore_send_hour, log, label):
    """Is this the run that is really due, in US Eastern?

    GitHub cron is UTC and cannot follow DST, so each scheduled job is declared
    at BOTH possible UTC hours - one is right in summer, the other in winter -
    and this makes the wrong one a no-op. Do NOT go back to letting state dedup
    absorb the duplicate: dedup silences the SECOND run, so the EARLIER one
    always won and the Friday digest quietly drifted to 4PM ET every winter.
    """
    want = config.get(key)
    if want is None or ignore_send_hour:
        return True
    current = eastern_hour(now)
    if current != int(want):
        log(f"Skipping {label}: {current}:00 Eastern, "
            f"{label} are pinned to {int(want)}:00 Eastern.")
        return False
    return True


def run(config, state, now, dry_run, bot_token, rh_api_key, log=print, mode="all",
        ignore_send_hour=False):
    server_id = config["raidhelper"]["server_id"]
    guild_id = config["discord"]["guild_id"]
    windows = sorted(config.get("reminder_windows_hours") or [24])

    if mode == "check":
        # Permission audit only - no Raid-Helper call, nothing sent.
        check_channels(config, bot_token, DiscordContext(guild_id, bot_token), log)
        return

    # How far ahead we LOOK is deliberately separate from when we REMIND.
    # Reminders still fire per reminder_windows_hours (windows_due), so a wider
    # horizon never sends anything earlier - it only makes far-out events
    # visible in the log. Worth it because Raid-Helper posts the next instance
    # of a recurring event over a week ahead: on July 21, 2026 the recreated
    # Thursday raid sat 217h out and was completely invisible, which read as
    # "the bot is broken" when the event was simply beyond the horizon.
    horizon_hours = int(config.get("event_horizon_hours") or max(windows))
    horizon = horizon_hours * 3600 + 900  # a little margin past the window
    events = fetch_upcoming_events(server_id, rh_api_key, horizon)
    log(f"Fetched {len(events)} upcoming event(s) within {horizon_hours}h.")
    for ev in sorted(events, key=lambda e: int(e.get("startTime") or 0)):
        start = int(ev.get("startTime") or 0)
        aud, _spec = pick_audience(ev, config)
        flag = "" if (start - now) <= max(windows) * 3600 else "  [beyond reminder window]"
        log(f"    - <t:{start}> [{ev.get('id')}] '{event_title(ev)}' "
            f"in channel {ev.get('channelId')} -> audience '{aud}'{flag}")

    # Optional run reports: if discord.log_channel_id is set, ONE message per
    # raid is posted there after the run (display names, #channel-names) -
    # officers see what fired without opening the GitHub log, and names are
    # never ambiguous between raids. The GitHub log stays verbose (raw ids).
    report = {"events": {}, "failed": []}

    ctx = DiscordContext(guild_id, bot_token)
    if mode in ("all", "reminders"):
        if send_hour_ok(config, "reminders_send_hour_et", now,
                        ignore_send_hour, log, "reminders"):
            run_reminders(config, state, events, now, dry_run, bot_token, ctx, log,
                          report=report)
    if mode == "summary":
        # Sunday 10AM Eastern: officers get the still-unsigned list. Sends no
        # DMs and writes no state - see run_reminders(send_dms=False).
        if send_hour_ok(config, "summary_send_hour_et", now,
                        ignore_send_hour, log, "summary"):
            run_reminders(config, state, events, now, dry_run, bot_token, ctx, log,
                          report=report, send_dms=False)
    if mode in ("all", "announcements"):
        run_announcements(config, state, events, now, dry_run, bot_token, log, ctx,
                          report=report)
    prune_state(state, now)

    log_channel = (config.get("discord") or {}).get("log_channel_id") or ""
    if log_channel and not dry_run and bot_token:
        for ev in sorted(report["events"].values(), key=lambda e: e["start"]):
            if not (ev["dms"] or ev["closed"] or ev["announced"] or ev["unsigned"]):
                continue
            lines = [f"**📋 {ev['title']}** — starts <t:{ev['start']}:R>"]
            if ev["dms"]:
                lines.append("📨 Reminder DMs sent: " + ", ".join(ev["dms"]))
            if ev["closed"]:
                lines.append("📪 Couldn't DM (privacy settings): " + ", ".join(ev["closed"]))
            if ev["announced"]:
                lines.append(f"📣 Invites announcement posted in {ev['announced']}")
            if ev["unsigned"]:
                lines.append("⏳ Still unsigned: " + ev["unsigned"])
            # Blank line after the title, then one line per fact.
            text = lines[0] + "\n\n" + "\n".join(lines[1:])
            if len(text) > 1900:  # Discord message limit is 2000 chars
                text = text[:1900] + "\n… (truncated - full detail in the Actions log)"
            # Trailing zero-width-space line = a visual gap between the
            # stacked per-raid messages in the channel.
            text += "\n​"
            if not send_channel_message(log_channel, text, bot_token):
                log(f"FAILED to post run report to log channel {log_channel} - "
                    "check the bot can view + send there.")
                break
        if report["failed"]:
            send_channel_message(log_channel,
                                 "\n".join(f"⚠️ {f}" for f in report["failed"]),
                                 bot_token)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Remind unsigned members of Raid-Helper events.")
    parser.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"))
    parser.add_argument("--state", default=os.path.join(os.path.dirname(__file__), "state.json"))
    parser.add_argument("--mode",
                        choices=["all", "reminders", "announcements", "summary",
                                 "check"],
                        default="all")
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending")
    parser.add_argument("--ignore-send-hour", action="store_true",
                        help="run reminders even outside reminders_send_hour_et "
                             "(for manual test runs)")
    args = parser.parse_args(argv)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    dry_run = args.dry_run or bool(config.get("dry_run"))
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    rh_api_key = os.environ.get("RAIDHELPER_API_KEY", "")
    if not rh_api_key:
        sys.exit("RAIDHELPER_API_KEY environment variable is not set.")
    if not bot_token and not dry_run:
        sys.exit("DISCORD_BOT_TOKEN environment variable is not set.")

    state = load_state(args.state)
    try:
        run(config, state, int(time.time()), dry_run, bot_token, rh_api_key,
            mode=args.mode, ignore_send_hour=args.ignore_send_hour)
    except RuntimeError as e:
        # Clean one-line error for schedulers/logs instead of a traceback.
        sys.exit(f"ERROR: {e}")
    finally:
        if not dry_run:
            save_state(state, args.state)


if __name__ == "__main__":
    main()
