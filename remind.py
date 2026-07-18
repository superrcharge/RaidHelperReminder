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
        "RaidHelperReminder (https://github.com/superrcharge/RaidHelperReminder, 1.0)",
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


def fetch_guild_roles(guild_id, token):
    """Fetch all guild roles as {role_id: permissions_int}."""
    status, payload = http_json(
        "GET", f"{DISCORD_API}/guilds/{guild_id}/roles", headers=discord_headers(token)
    )
    if status != 200 or not isinstance(payload, list):
        raise RuntimeError(f"Discord roles fetch returned {status}: {payload!r}")
    return {str(r["id"]): int(r.get("permissions") or 0) for r in payload}


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

def windows_due(start_time, windows_hours, now):
    """Reminder windows that currently apply to an event.

    A window W applies when the event starts within the next W hours.
    Windows for events already started never apply.
    """
    seconds_left = start_time - now
    if seconds_left <= 0:
        return []
    return [w for w in windows_hours if seconds_left <= w * 3600]


def format_message(template, event, member, guild_id):
    start = int(event.get("startTime") or 0)
    signup_link = "https://discord.com/channels/{}/{}/{}".format(
        guild_id, event.get("channelId"), event.get("id")
    )
    return (
        template.replace("{member_name}", member_display_name(member))
        .replace("{event_title}", event.get("title") or "our next event")
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
                  report=None):
    if report is None:
        report = {"dms": [], "closed": [], "unsigned": {}}
    guild_id = config["discord"]["guild_id"]
    windows = sorted(config.get("reminder_windows_hours") or [24])
    fallback_channel = (config.get("discord") or {}).get("fallback_channel_id") or ""
    treat_as_no_response = config.get("treat_as_no_response") or []
    digest = bool(config.get("digest_dms"))

    # plans[user_id] = {"member": ..., "items": [(event, min_due_window)]}
    plans = {}
    dm_count = 0

    for event in events:
        title = event.get("title") or "?"
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
            report["unsigned"][title] = names_list(missing)

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
                report["dms"].append(member_display_name(member))
            else:
                dms_closed.append(member)
                log(f"  DMs closed for {member_display_name(member)} ({uid}).")
                report["closed"].append(member_display_name(member))
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

    log(f"Reminders done. {dm_count} DM(s) sent." if not dry_run else "Reminders done (dry run).")


# ---------------------------------------------------------------------------
# Announcements - channel messages around raid time
# ---------------------------------------------------------------------------

def names_list(members, cap=30):
    """Readable comma list of member display names, capped."""
    names = ", ".join(member_display_name(m) for m in members[:cap])
    if len(members) > cap:
        names += f" (+{len(members) - cap} more)"
    return names


def run_announcements(config, state, events, now, dry_run, bot_token, log, ctx=None,
                      report=None):
    if report is None:
        report = {"announced": [], "unsigned": {}, "failed": []}
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
            mentions = "".join(f"<@&{rid}> " for rid in ann.get("mention_role_ids") or [])
            text = mentions + format_message(ann.get("text") or "", event, None, guild_id)
            channel = str(ann.get("channel_id") or event.get("channelId"))
            if dry_run:
                log(f"  [dry-run] would announce in channel {channel} for "
                    f"'{event.get('title')}': {text}")
            else:
                if send_channel_message(channel, text, bot_token):
                    sent += 1
                    log(f"  Announcement posted for '{event.get('title')}' in {channel}.")
                    chan_label = ctx.get_channel_name(channel) if ctx else str(channel)
                    report["announced"].append((event.get("title") or "?", chan_label))
                    # Tell the officers who is still unsigned at invite time.
                    if ctx is not None:
                        _an, spec = pick_audience(event, config)
                        responded = responded_user_ids(
                            event_signups(event),
                            config.get("treat_as_no_response") or [])
                        expected = audience_members(
                            ctx.get_members(), spec,
                            guild_id=config["discord"]["guild_id"],
                            roles_map=ctx.get_roles_map() if spec.get("channel_access") else None,
                            overwrites_for=ctx.get_overwrites if spec.get("channel_access") else None,
                        )
                        still = [m for m in expected
                                 if str(m["user"]["id"]) not in responded]
                        if still:
                            log(f"  Still unsigned for '{event.get('title')}' "
                                f"({len(still)}): " + names_list(still))
                            report["unsigned"][event.get("title") or "?"] = names_list(still)
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


def run(config, state, now, dry_run, bot_token, rh_api_key, log=print, mode="all"):
    server_id = config["raidhelper"]["server_id"]
    guild_id = config["discord"]["guild_id"]
    windows = sorted(config.get("reminder_windows_hours") or [24])

    horizon = max(windows) * 3600 + 900  # a little margin past the widest window
    events = fetch_upcoming_events(server_id, rh_api_key, horizon)
    log(f"Fetched {len(events)} upcoming event(s) within {max(windows)}h.")

    # Optional run report: if discord.log_channel_id is set, a clean summary
    # (display names, #channel-names, one line per fact) is posted there
    # after the run - officers see what fired without opening the GitHub
    # log. The GitHub log itself stays verbose (raw ids) for debugging.
    report = {"dms": [], "closed": [], "announced": [], "unsigned": {}, "failed": []}

    ctx = DiscordContext(guild_id, bot_token)
    if mode in ("all", "reminders"):
        run_reminders(config, state, events, now, dry_run, bot_token, ctx, log,
                      report=report)
    if mode in ("all", "announcements"):
        run_announcements(config, state, events, now, dry_run, bot_token, log, ctx,
                          report=report)
    prune_state(state, now)

    log_channel = (config.get("discord") or {}).get("log_channel_id") or ""
    if log_channel and not dry_run and bot_token and any(report.values()):
        lines = [f"**Raid Reminder — run report** (<t:{now}:f>)"]
        if report["dms"]:
            lines.append("📨 Reminder DMs sent: " + ", ".join(report["dms"]))
        if report["closed"]:
            lines.append("📪 Couldn't DM (privacy settings): " + ", ".join(report["closed"]))
        for title, chan in report["announced"]:
            lines.append(f"📣 Invites announcement posted in {chan} for **{title}**")
        if report["unsigned"]:
            lines.append("⏳ Still unsigned:")
            for title, names in report["unsigned"].items():
                lines.append(f"> **{title}** — {names}")
        for f in report["failed"]:
            lines.append(f"⚠️ {f}")
        text = "\n".join(lines)
        if len(text) > 1900:  # Discord message limit is 2000 chars
            text = text[:1900] + "\n… (truncated - full detail in the Actions log)"
        if not send_channel_message(log_channel, text, bot_token):
            log(f"FAILED to post run report to log channel {log_channel} - "
                "check the bot can view + send there.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Remind unsigned members of Raid-Helper events.")
    parser.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"))
    parser.add_argument("--state", default=os.path.join(os.path.dirname(__file__), "state.json"))
    parser.add_argument("--mode", choices=["all", "reminders", "announcements"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending")
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
        run(config, state, int(time.time()), dry_run, bot_token, rh_api_key, mode=args.mode)
    except RuntimeError as e:
        # Clean one-line error for schedulers/logs instead of a traceback.
        sys.exit(f"ERROR: {e}")
    finally:
        if not dry_run:
            save_state(state, args.state)


if __name__ == "__main__":
    main()
