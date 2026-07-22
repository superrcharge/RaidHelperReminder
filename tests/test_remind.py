"""Unit tests for the pure logic in remind.py - no network calls.

Run from the project root:
    python -m unittest discover tests
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remind

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "server_events.json")

with open(FIXTURE, "r", encoding="utf-8") as f:
    SERVER_EVENTS = json.load(f)

KARA = SERVER_EVENTS["postedEvents"][0]   # starts 1767225600, channel 222...
GRUUL = SERVER_EVENTS["postedEvents"][1]  # starts 1767398400, no signups


def member(uid, roles, nick=None, name="user", bot=False):
    u = {"id": uid, "username": name}
    if bot:
        u["bot"] = True
    return {"user": u, "nick": nick, "roles": roles}


MEMBERS = [
    member("100", ["7001"], nick="Tankadin"),
    member("101", ["7001"], nick="Shadowmike"),
    member("102", ["7001"], nick="Flaky"),
    member("103", ["7001"], nick="OnBench"),
    member("104", ["7001"], nick="Slacker"),          # never signs up
    member("105", ["7002"], nick="TeamBOnly"),        # different team
    member("106", ["8000"], nick="SocialMember"),     # not in any audience
    member("900", ["7001"], nick="SomeBot", bot=True),
]

CONFIG = {
    "discord": {"guild_id": "9999999999999999999", "fallback_channel_id": ""},
    "raidhelper": {"server_id": "9999999999999999999"},
    "audiences": {
        "default": {"role_ids": ["7001", "7002"]},
        "teamA": {"role_ids": ["7001"]},
    },
    "audience_rules": [
        {"match": {"channel_id": "2222222222222222222"}, "audience": "teamA"},
    ],
    "reminder_windows_hours": [48, 24],
    "messages": {"default": "Hi {member_name}, sign up for {event_title}: {signup_link}"},
    "treat_as_no_response": [],
}


class WindowLogic(unittest.TestCase):
    def test_outside_all_windows(self):
        start = KARA["startTime"]
        self.assertEqual(remind.windows_due(start, [48, 24], start - 72 * 3600), [])

    def test_inside_wide_window_only(self):
        start = KARA["startTime"]
        self.assertEqual(remind.windows_due(start, [48, 24], start - 30 * 3600), [48])

    def test_inside_both_windows(self):
        start = KARA["startTime"]
        self.assertEqual(sorted(remind.windows_due(start, [48, 24], start - 3600)), [24, 48])

    def test_event_already_started(self):
        start = KARA["startTime"]
        self.assertEqual(remind.windows_due(start, [48, 24], start + 60), [])


class AudienceLogic(unittest.TestCase):
    def test_rule_match_by_channel(self):
        name, spec = remind.pick_audience(KARA, CONFIG)
        self.assertEqual(name, "teamA")
        self.assertEqual(spec, {"role_ids": ["7001"]})

    def test_falls_back_to_default(self):
        name, spec = remind.pick_audience(GRUUL, CONFIG)
        self.assertEqual(name, "default")
        self.assertEqual(spec, {"role_ids": ["7001", "7002"]})

    def test_role_audience_excludes_bots_and_other_roles(self):
        got = {m["nick"] for m in remind.audience_members(MEMBERS, {"role_ids": ["7001"]})}
        self.assertEqual(got, {"Tankadin", "Shadowmike", "Flaky", "OnBench", "Slacker"})

    def test_user_ids_audience(self):
        got = {m["nick"] for m in remind.audience_members(MEMBERS, {"user_ids": ["104", "106"]})}
        self.assertEqual(got, {"Slacker", "SocialMember"})

    def test_combined_roles_and_user_ids(self):
        spec = {"role_ids": ["7002"], "user_ids": ["106"]}
        got = {m["nick"] for m in remind.audience_members(MEMBERS, spec)}
        self.assertEqual(got, {"TeamBOnly", "SocialMember"})


class AnnouncementMentions(unittest.TestCase):
    """A raid must ping its own team, never the whole raider pool."""

    def test_mention_audience_pings_the_events_team(self):
        ann = {"mention_audience": True, "mention_role_ids": ["9999"]}
        # KARA resolves to teamA (role 7001), so 7001 is pinged - not 9999.
        self.assertEqual(remind.announcement_mentions(ann, KARA, CONFIG),
                         "<@&7001> ")

    def test_without_the_flag_the_literal_roles_are_used(self):
        ann = {"mention_role_ids": ["9999"]}
        self.assertEqual(remind.announcement_mentions(ann, KARA, CONFIG),
                         "<@&9999> ")

    def test_falls_back_when_the_audience_has_no_roles(self):
        cfg = json.loads(json.dumps(CONFIG))
        cfg["audiences"]["teamA"] = {}
        ann = {"mention_audience": True, "mention_role_ids": ["9999"]}
        self.assertEqual(remind.announcement_mentions(ann, KARA, cfg),
                         "<@&9999> ")

    def test_user_id_audiences_ping_the_users(self):
        cfg = json.loads(json.dumps(CONFIG))
        cfg["audiences"]["teamA"] = {"user_ids": ["104"]}
        ann = {"mention_audience": True, "mention_role_ids": ["9999"]}
        self.assertEqual(remind.announcement_mentions(ann, KARA, cfg),
                         "<@104> ")


class ChannelAccessAudience(unittest.TestCase):
    """The workaround for channels that don't line up with a single role:
    compute who can actually SEE the channel from permission overwrites."""

    GUILD = "9999999999999999999"
    # @everyone can view by default; role 7002 has no special perms; 5000 is admin
    ROLES_MAP = {
        GUILD: remind.VIEW_CHANNEL,   # @everyone role id == guild id
        "7001": 0,
        "7002": 0,
        "5000": remind.ADMINISTRATOR,
    }
    # Private channel: @everyone denied VIEW, role 7001 allowed, member 106 allowed
    OVERWRITES = [
        {"id": GUILD, "type": 0, "allow": "0", "deny": str(remind.VIEW_CHANNEL)},
        {"id": "7001", "type": 0, "allow": str(remind.VIEW_CHANNEL), "deny": "0"},
        {"id": "106", "type": 1, "allow": str(remind.VIEW_CHANNEL), "deny": "0"},
    ]

    def viewers(self, members):
        return {
            m["nick"]
            for m in remind.audience_members(
                members,
                {"channel_access": "555"},
                guild_id=self.GUILD,
                roles_map=self.ROLES_MAP,
                overwrites_for=lambda cid: self.OVERWRITES,
            )
        }

    def test_role_allowed_members_can_view(self):
        self.assertIn("Slacker", self.viewers(MEMBERS))        # has 7001
        self.assertNotIn("TeamBOnly", self.viewers(MEMBERS))   # 7002 not allowed

    def test_member_overwrite_grants_access(self):
        self.assertIn("SocialMember", self.viewers(MEMBERS))   # member overwrite

    def test_admin_bypasses_deny(self):
        admin = member("777", ["5000"], nick="TheBoss")
        self.assertIn("TheBoss", self.viewers(MEMBERS + [admin]))

    def test_member_overwrite_deny_beats_role_allow(self):
        ows = self.OVERWRITES + [
            {"id": "104", "type": 1, "allow": "0", "deny": str(remind.VIEW_CHANNEL)}
        ]
        got = {
            m["nick"]
            for m in remind.audience_members(
                MEMBERS, {"channel_access": "555"},
                guild_id=self.GUILD, roles_map=self.ROLES_MAP,
                overwrites_for=lambda cid: ows,
            )
        }
        self.assertNotIn("Slacker", got)


class ResponseLogic(unittest.TestCase):
    def test_all_signups_count_by_default(self):
        ids = remind.responded_user_ids(KARA["signUps"], [])
        self.assertEqual(ids, {"100", "101", "102", "103"})

    def test_tentative_can_be_nagged(self):
        ids = remind.responded_user_ids(KARA["signUps"], ["Tentative"])
        self.assertEqual(ids, {"100", "101", "103"})


class MessageFormatting(unittest.TestCase):
    def test_placeholders(self):
        msg = remind.format_message(
            "Hi {member_name}: {event_title} at {event_time} {event_time_relative} -> {signup_link}",
            KARA, MEMBERS[0], "999")
        self.assertIn("Hi Tankadin", msg)
        self.assertIn("Karazhan - Team A", msg)
        self.assertIn("<t:1767225600:F>", msg)
        self.assertIn("<t:1767225600:R>", msg)
        self.assertIn("discord.com/channels/999/2222222222222222222/1111111111111111111", msg)

    def test_no_member_falls_back_to_there(self):
        msg = remind.format_message("Hi {member_name}", KARA, None, "999")
        self.assertEqual(msg, "Hi there")

    def test_per_window_template(self):
        cfg = {"messages": {"default": "d", "per_window": {"24": "final"}}}
        self.assertEqual(remind.pick_template(cfg, 24), "final")
        self.assertEqual(remind.pick_template(cfg, 48), "d")

    def test_digest_lists_events_chronologically(self):
        items = [(GRUUL, 168), (KARA, 168)]
        msg = remind.build_digest(CONFIG, items, MEMBERS[4], "999")
        self.assertIn("Slacker", msg)
        self.assertLess(msg.index("Karazhan"), msg.index("Gruul"))


class StateLogic(unittest.TestCase):
    def test_mark_and_check(self):
        state = {"events": {}}
        self.assertFalse(remind.already_sent(state, KARA["id"], 48, "104"))
        remind.mark_sent(state, KARA, 48, "104")
        self.assertTrue(remind.already_sent(state, KARA["id"], 48, "104"))
        self.assertFalse(remind.already_sent(state, KARA["id"], 24, "104"))

    def test_announced_mark_and_check(self):
        state = {"events": {}}
        self.assertFalse(remind.already_announced(state, KARA["id"], "0:15"))
        remind.mark_announced(state, KARA, "0:15")
        self.assertTrue(remind.already_announced(state, KARA["id"], "0:15"))

    def test_prune_drops_old_events(self):
        state = {"events": {}}
        remind.mark_sent(state, KARA, 48, "104")
        remind.prune_state(state, KARA["startTime"] + 2 * 86400)
        self.assertEqual(state["events"], {})


class FullRuns(unittest.TestCase):
    """Full run() against the fixture with all network calls mocked."""

    def run_once(self, now, state=None, dry_run=True, config=None, mode="all"):
        state = state if state is not None else {"events": {}}
        logs, dms, channel_msgs = [], [], []

        def fake_dm(uid, content, token):
            dms.append((uid, content))
            return True

        def fake_channel(cid, content, token):
            channel_msgs.append((cid, content))
            return True

        with mock.patch.object(remind, "fetch_upcoming_events", return_value=SERVER_EVENTS["postedEvents"]), \
             mock.patch.object(remind, "fetch_guild_members", return_value=MEMBERS), \
             mock.patch.object(remind, "send_dm", side_effect=fake_dm), \
             mock.patch.object(remind, "send_channel_message", side_effect=fake_channel):
            remind.run(config or CONFIG, state, now, dry_run, "tok", "key",
                       log=logs.append, mode=mode)
        return state, logs, dms, channel_msgs

    def test_dry_run_finds_the_slacker(self):
        now = KARA["startTime"] - 30 * 3600  # inside 48h window for Kara only
        state, logs, dms, _ = self.run_once(now)
        joined = "\n".join(logs)
        self.assertIn("Slacker", joined)          # 104 missing from Team A event
        self.assertNotIn("SocialMember", joined)  # not in audience
        self.assertNotIn("TeamBOnly", joined)     # kara audience is teamA only
        self.assertEqual(dms, [])                 # dry run sends nothing

    def test_real_run_sends_and_dedupes(self):
        now = KARA["startTime"] - 30 * 3600
        state, logs, dms, _ = self.run_once(now, dry_run=False)
        self.assertEqual([uid for uid, _ in dms], ["104"])
        # Second run, same window: nobody gets a second DM.
        state2, logs2, dms2, _ = self.run_once(now + 60, state=state, dry_run=False)
        self.assertEqual(dms2, [])
        # Inside the 24h window a new reminder goes out to the same member.
        state3, logs3, dms3, _ = self.run_once(KARA["startTime"] - 3600, state=state, dry_run=False)
        self.assertEqual([uid for uid, _ in dms3], ["104"])
        # And the stale 48h window doesn't cause a duplicate afterwards.
        state4, logs4, dms4, _ = self.run_once(KARA["startTime"] - 3000, state=state, dry_run=False)
        self.assertEqual(dms4, [])

    def test_digest_sends_one_dm_for_multiple_events(self):
        # Friday-style config: one wide window covering both raids, digest on.
        cfg = dict(CONFIG)
        cfg["reminder_windows_hours"] = [168]
        cfg["digest_dms"] = True
        now = KARA["startTime"] - 30 * 3600  # both events within 168h
        state, logs, dms, _ = self.run_once(now, dry_run=False, config=cfg)
        slacker_dms = [c for uid, c in dms if uid == "104"]
        self.assertEqual(len(slacker_dms), 1)          # ONE digest, not two DMs
        self.assertIn("Karazhan", slacker_dms[0])
        self.assertIn("Gruul", slacker_dms[0])
        # TeamBOnly (105) is in Gruul's default audience but not Kara's teamA:
        teamb_dms = [c for uid, c in dms if uid == "105"]
        self.assertEqual(len(teamb_dms), 1)
        self.assertNotIn("Karazhan", teamb_dms[0])
        # Re-run: nothing new.
        state2, logs2, dms2, _ = self.run_once(now + 60, state=state, dry_run=False, config=cfg)
        self.assertEqual(dms2, [])

    def test_announcement_fires_in_window_and_dedupes(self):
        cfg = dict(CONFIG)
        cfg["announcements"] = [{
            "minutes_before": 15,
            "mention_role_ids": ["7777"],
            "text": "Raid invites has started for tonight's raid. Please have your "
                    "gear and consumes. Whisper Kcin or an Officer for an invite!",
        }]
        # 30 minutes out: nothing yet.
        state, logs, dms, posts = self.run_once(
            KARA["startTime"] - 30 * 60, dry_run=False, config=cfg, mode="announcements")
        self.assertEqual(posts, [])
        # 10 minutes out: fires once, in the event's own channel, pinging the role.
        state, logs, dms, posts = self.run_once(
            KARA["startTime"] - 10 * 60, state=state, dry_run=False, config=cfg,
            mode="announcements")
        kara_posts = [p for p in posts if p[0] == str(KARA["channelId"])]
        self.assertEqual(len(kara_posts), 1)
        self.assertIn("<@&7777>", kara_posts[0][1])
        self.assertIn("Whisper Kcin", kara_posts[0][1])
        # Re-run 5 minutes out: no duplicate.
        state, logs, dms, posts2 = self.run_once(
            KARA["startTime"] - 5 * 60, state=state, dry_run=False, config=cfg,
            mode="announcements")
        self.assertEqual(posts2, [])

    def test_mode_reminders_skips_announcements(self):
        cfg = dict(CONFIG)
        cfg["announcements"] = [{"minutes_before": 15, "text": "x"}]
        state, logs, dms, posts = self.run_once(
            KARA["startTime"] - 10 * 60, dry_run=False, config=cfg, mode="reminders")
        self.assertEqual(posts, [])


if __name__ == "__main__":
    unittest.main()
