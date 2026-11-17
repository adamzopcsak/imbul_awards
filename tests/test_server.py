"""Tests of server.py: they start the real server on a free port with a temporary data folder. Discord is never
called: the OAuth exchange is replaced by a function that returns a made-up user."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402

ADMIN = "100000000000000001"
FRIEND = "200000000000000002"
STRANGER = "300000000000000003"
ORIGIN = "https://imbulawards.yagi.hu"


class ServerTest(unittest.TestCase):
    closes_at = "2099-12-11T01:00:00+01:00"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        nominees = json.loads(server.NOMINEES_FILE.read_text(encoding="utf-8"))
        nominees["votingClosesAt"] = self.closes_at
        nominees["opensAt"] = "2098-01-01T00:00:00+01:00"  # not open to everyone by itself, whatever today is
        ballot_file = Path(self.tmp.name) / "nominees.json"
        ballot_file.write_text(json.dumps(nominees), encoding="utf-8")
        config = server.Config(env={
            "AWARDS_PUBLIC_URL": ORIGIN, "DISCORD_CLIENT_ID": "1", "DISCORD_CLIENT_SECRET": "x",
            "AWARDS_ADMIN_USERS": ADMIN,
        })
        self.app = server.App(config, Path(self.tmp.name) / "data", server.Ballot(ballot_file))
        self.httpd = server.make_server("127.0.0.1", 0, self.app)
        self.port = self.httpd.server_address[1]
        self.discord_users = {}
        self.guild_members = set()  # who the fake Discord says is on the Imbul server
        self.asked_guilds = []

        def fake_discord(config, code, with_guilds=False):
            me = dict(self.discord_users[code])
            self.asked_guilds.append(with_guilds)
            if with_guilds:
                me["guild_ids"] = ["111111111111111111"] + ([config.guild_id] if me["id"] in self.guild_members else [])
            return me
        self.httpd.RequestHandlerClass.discord_user = staticmethod(fake_discord)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        all_headers = dict(headers or {})
        if cookie:
            all_headers["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            all_headers.setdefault("Content-Type", "application/json")
            all_headers.setdefault("Origin", ORIGIN)
        conn.request(method, path, body=data, headers=all_headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw
        return response, payload

    @staticmethod
    def set_cookies(response):
        jar = {}
        for header in response.headers.get_all("Set-Cookie") or []:
            name, value = header.split(";", 1)[0].split("=", 1)
            jar[name] = value
        return jar

    def login(self, user_id, name="Valaki", scope="identify"):
        """The whole Discord flow; returns the session cookie header, or None if refused (and the redirect)."""
        response, _ = self.request("GET", "/login")
        self.assertEqual(response.status, 302)
        location = urllib.parse.urlsplit(response.headers["Location"])
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", server.DISCORD_AUTHORIZE_URL)
        params = dict(urllib.parse.parse_qsl(location.query))
        self.assertEqual(params["redirect_uri"], "https://imbulawards.yagi.hu/auth/callback")
        self.assertEqual(params["scope"], scope)
        state_cookie = self.set_cookies(response)[server.STATE_COOKIE]
        code = f"code-{user_id}"
        self.discord_users[code] = {"id": user_id, "username": name.lower(), "global_name": name,
                                    "avatar": "0123456789abcdef0123456789abcdef"}
        response, _ = self.request("GET", f"/auth/callback?code={code}&state={params['state']}",
                                   cookie=f"{server.STATE_COOKIE}={state_cookie}")
        self.assertEqual(response.status, 302)
        session = self.set_cookies(response).get(server.SESSION_COOKIE)
        return (f"{server.SESSION_COOKIE}={session}" if session else None), response.headers["Location"]


class LoginTests(ServerTest):
    def test_page_is_served_with_a_strict_policy(self):
        response, body = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"Imbul Awards", body if isinstance(body, bytes) else b"")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("max-age", response.headers["Strict-Transport-Security"])

    def test_nobody_logged_in(self):
        response, _ = self.request("GET", "/api/me")
        self.assertEqual(response.status, 401)
        response, _ = self.request("GET", "/api/ballot")
        self.assertEqual(response.status, 401)

    def test_admin_gets_in(self):
        cookie, location = self.login(ADMIN, "Yagi")
        self.assertEqual(location, "/")
        response, me = self.request("GET", "/api/me", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(me["id"], ADMIN)
        self.assertTrue(me["isAdmin"])
        self.assertTrue(me["avatarUrl"].startswith("https://cdn.discordapp.com/avatars/"))

    def test_session_cookie_flags(self):
        response, _ = self.request("GET", "/login")
        header = response.headers["Set-Cookie"]
        for flag in ("Path=/", "HttpOnly", "SameSite=Lax", "Secure"):
            self.assertIn(flag, header)

    def test_everyone_else_is_refused_and_noted(self):
        cookie, location = self.login(STRANGER, "Idegen")
        self.assertIsNone(cookie)
        self.assertEqual(location, "/?hiba=nincs-jog")
        self.login(STRANGER, "Idegen")
        admin, _ = self.login(ADMIN)
        _, access = self.request("GET", "/api/admin/access", cookie=admin)
        self.assertEqual([(p["id"], p["name"], p["attempts"]) for p in access["refused"]], [(STRANGER, "Idegen", 2)])
        self.assertEqual([p["id"] for p in access["people"]], [ADMIN])
        self.assertFalse(access["everyone"])

    def test_wrong_state_is_refused(self):
        self.discord_users["c"] = {"id": ADMIN, "username": "yagi"}
        response, _ = self.request("GET", "/auth/callback?code=c&state=forged")
        self.assertEqual(response.headers["Location"], "/?hiba=sikertelen")
        self.assertNotIn(server.SESSION_COOKIE + "=", (response.headers.get("Set-Cookie") or "").replace(
            server.SESSION_COOKIE + "_state", ""))

    def test_forged_session_is_ignored(self):
        cookie, _ = self.login(ADMIN)
        forged = cookie[:-3] + ("aaa" if not cookie.endswith("aaa") else "bbb")
        response, _ = self.request("GET", "/api/me", cookie=forged)
        self.assertEqual(response.status, 401)

    def test_logout_clears_the_cookie(self):
        cookie, _ = self.login(ADMIN)
        response, _ = self.request("POST", "/logout", body={}, cookie=cookie)
        self.assertEqual(response.status, 204)
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])

    def test_dev_login_is_off_unless_asked_and_never_on_https(self):
        response, _ = self.request("GET", f"/dev-login?id={ADMIN}&name=Yagi")
        self.assertEqual(response.status, 404)
        self.assertFalse(server.Config(env={"AWARDS_PUBLIC_URL": ORIGIN}, dev_login=True).dev_login)
        self.assertTrue(server.Config(env={}, dev_login=True).dev_login)


class AccessTests(ServerTest):
    def test_admin_lets_a_friend_in_and_takes_it_back(self):
        admin, _ = self.login(ADMIN)
        self.assertIsNone(self.login(FRIEND)[0])
        response, access = self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": True},
                                        cookie=admin)
        self.assertEqual(response.status, 200)
        self.assertEqual(sorted(p["id"] for p in access["people"]), [ADMIN, FRIEND])
        self.assertEqual(access["refused"], [])
        friend, _ = self.login(FRIEND, "Haver")
        response, me = self.request("GET", "/api/me", cookie=friend)
        self.assertEqual(response.status, 200)
        self.assertFalse(me["isAdmin"])
        # Taking it back works at once, even with a live session
        _, access = self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": False}, cookie=admin)
        response, _ = self.request("GET", "/api/ballot", cookie=friend)
        self.assertEqual(response.status, 403)
        # They stay on the list, marked as taken away, and can be given back
        friend_row = next(p for p in access["people"] if p["id"] == FRIEND)
        self.assertEqual(friend_row["revoked"]["by"], "Valaki")
        self.assertEqual(access["allowedNow"], 1)
        self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": True}, cookie=admin)
        response, _ = self.request("GET", "/api/ballot", cookie=friend)
        self.assertEqual(response.status, 200)

    def everyone(self, admin, allowed):
        response, access = self.request("POST", "/api/admin/access/everyone", body={"allowed": allowed}, cookie=admin)
        self.assertEqual(response.status, 200)
        return access

    def test_everyone_on_the_server_gets_in(self):
        admin, _ = self.login(ADMIN)
        self.guild_members = {FRIEND}
        # Off: Discord is not even asked about servers, and a member is refused like anyone
        self.assertIsNone(self.login(FRIEND)[0])
        self.assertEqual(self.asked_guilds[-1], False)
        access = self.everyone(admin, True)
        self.assertTrue(access["everyone"])
        friend, location = self.login(FRIEND, "Haver", scope="identify guilds")
        self.assertEqual(location, "/")
        response, _ = self.request("GET", "/api/ballot", cookie=friend)
        self.assertEqual(response.status, 200)
        # Not on the server: still refused
        self.assertIsNone(self.login(STRANGER, scope="identify guilds")[0])
        _, access = self.request("GET", "/api/admin/access", cookie=admin)
        friend_row = next(p for p in access["people"] if p["id"] == FRIEND)
        self.assertEqual(friend_row["granted"]["by"], "a szerver tagjaként")
        self.assertEqual([p["id"] for p in access["refused"]], [STRANGER])

    def test_taken_away_one_stays_out_while_everyone_may_vote(self):
        admin, _ = self.login(ADMIN)
        self.guild_members = {FRIEND}
        self.everyone(admin, True)
        friend, _ = self.login(FRIEND, scope="identify guilds")
        self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": False}, cookie=admin)
        self.assertIsNone(self.login(FRIEND, scope="identify guilds")[0])

    def test_nobody_but_the_admins(self):
        admin, _ = self.login(ADMIN)
        self.guild_members = {FRIEND}
        self.request("POST", "/api/admin/access", body={"userId": STRANGER, "allowed": True}, cookie=admin)
        self.everyone(admin, True)
        friend, _ = self.login(FRIEND, scope="identify guilds")
        stranger, _ = self.login(STRANGER, scope="identify guilds")
        access = self.everyone(admin, False)
        self.assertFalse(access["everyone"])
        self.assertEqual(access["changed"], 2)
        self.assertEqual(access["allowedNow"], 1)  # the admin
        for cookie in (friend, stranger):
            response, _ = self.request("GET", "/api/ballot", cookie=cookie)
            self.assertEqual(response.status, 403)
        response, _ = self.request("GET", "/api/ballot", cookie=admin)
        self.assertEqual(response.status, 200)
        self.assertIsNone(self.login(FRIEND)[0])  # and the server no longer lets them in
        # Everyone again: those taken away get it back
        access = self.everyone(admin, True)
        self.assertEqual(access["changed"], 2)
        response, _ = self.request("GET", "/api/ballot", cookie=friend)
        self.assertEqual(response.status, 200)

    def test_everyone_switch_is_admin_only(self):
        admin, _ = self.login(ADMIN)
        self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": True}, cookie=admin)
        friend, _ = self.login(FRIEND)
        response, _ = self.request("POST", "/api/admin/access/everyone", body={"allowed": True}, cookie=friend)
        self.assertEqual(response.status, 403)

    def test_admin_cannot_be_locked_out(self):
        admin, _ = self.login(ADMIN)
        response, body = self.request("POST", "/api/admin/access", body={"userId": ADMIN, "allowed": False},
                                      cookie=admin)
        self.assertEqual(response.status, 400)

    def test_bad_ids_are_refused(self):
        admin, _ = self.login(ADMIN)
        for user_id in ("123", "abc", 100000000000000001, ""):
            with self.subTest(user_id=user_id):
                response, _ = self.request("POST", "/api/admin/access", body={"userId": user_id, "allowed": True},
                                           cookie=admin)
                self.assertEqual(response.status, 400)

    def test_admin_pages_are_admin_only(self):
        admin, _ = self.login(ADMIN)
        self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": True}, cookie=admin)
        friend, _ = self.login(FRIEND)
        for method, path, body in (("GET", "/api/admin/summary", None), ("GET", "/api/admin/access", None),
                                   ("PUT", "/api/admin/winners", {"category": "goty", "nominee": 0}),
                                   ("POST", "/api/admin/access", {"userId": STRANGER, "allowed": True})):
            with self.subTest(path=path):
                response, _ = self.request(method, path, body=body, cookie=friend)
                self.assertEqual(response.status, 403)

    def test_writes_from_another_site_are_refused(self):
        admin, _ = self.login(ADMIN)
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": "https://imbulstats.yagi.hu"}):
            with self.subTest(headers=headers):
                response, _ = self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": True},
                                           cookie=admin, headers=headers)
                self.assertEqual(response.status, 403)
        response, _ = self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": True},
                                   cookie=admin, headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 400)


class VoteTests(ServerTest):
    def setUp(self):
        super().setUp()
        self.admin, _ = self.login(ADMIN, "Yagi")
        self.request("POST", "/api/admin/access", body={"userId": FRIEND, "allowed": True}, cookie=self.admin)
        self.friend, _ = self.login(FRIEND, "Haver")

    def vote(self, cookie, category, kind, nominee):
        return self.request("PUT", "/api/votes", body={"category": category, "kind": kind, "nominee": nominee},
                            cookie=cookie)

    def test_ballot_has_every_category_with_both_questions(self):
        response, ballot = self.request("GET", "/api/ballot", cookie=self.friend)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(ballot["categories"]), 30)
        self.assertEqual(ballot["categories"][0]["id"], "goty")
        self.assertEqual(ballot["votes"], {})
        self.assertFalse(ballot["closed"])

    def test_vote_change_and_withdraw(self):
        response, body = self.vote(self.friend, "goty", "predict", 2)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["votes"], {"goty": {"predict": 2}})
        self.vote(self.friend, "goty", "pick", 5)
        self.vote(self.friend, "goty", "predict", 1)
        _, ballot = self.request("GET", "/api/ballot", cookie=self.friend)
        self.assertEqual(ballot["votes"], {"goty": {"predict": 1, "pick": 5}})
        _, body = self.vote(self.friend, "goty", "predict", None)
        self.assertEqual(body["votes"], {"goty": {"pick": 5}})

    def test_bad_votes_are_refused(self):
        for category, kind, nominee in (("nope", "predict", 0), ("goty", "maybe", 0), ("goty", "pick", 6),
                                        ("goty", "pick", -2), ("goty", "pick", "1"), ("goty", "pick", True),
                                        ("goty", "pick", -1.0)):
            with self.subTest(category=category, kind=kind, nominee=nominee):
                response, _ = self.vote(self.friend, category, kind, nominee)
                self.assertEqual(response.status, 400)

    def test_no_idea_is_an_answer_in_every_category(self):
        response, body = self.vote(self.friend, "goty", "predict", server.NO_IDEA)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["votes"], {"goty": {"predict": -1}})
        self.vote(self.admin, "goty", "predict", server.NO_IDEA)
        self.vote(self.admin, "goty", "pick", 0)
        _, summary = self.request("GET", "/api/admin/summary", cookie=self.admin)
        self.assertEqual(summary["totals"]["goty"]["noIdea"], {"predict": 2, "pick": 0})
        self.assertEqual(summary["totals"]["goty"]["predict"], [0] * 6)
        self.assertEqual(summary["totals"]["rpg"]["noIdea"], {"predict": 0, "pick": 0})
        # It is never a winner, and never a correct guess
        response, _ = self.request("PUT", "/api/admin/winners", body={"category": "goty", "nominee": -1},
                                   cookie=self.admin)
        self.assertEqual(response.status, 400)
        _, summary = self.request("PUT", "/api/admin/winners", body={"category": "goty", "nominee": 0},
                                  cookie=self.admin)
        self.assertEqual({s["id"]: s["correct"] for s in summary["scores"]}, {ADMIN: 0, FRIEND: 0})

    def test_strangers_cannot_vote(self):
        response, _ = self.vote(None, "goty", "pick", 0)
        self.assertEqual(response.status, 401)

    def test_admin_sees_everything_and_the_leaderboard(self):
        self.vote(self.friend, "goty", "predict", 2)
        self.vote(self.friend, "goty", "pick", 5)
        self.vote(self.friend, "rpg", "predict", 0)
        self.vote(self.admin, "goty", "predict", 2)
        self.vote(self.admin, "goty", "pick", 2)
        self.vote(self.admin, "rpg", "predict", 3)
        _, summary = self.request("GET", "/api/admin/summary", cookie=self.admin)
        self.assertEqual(summary["totals"]["goty"]["predict"], [0, 0, 2, 0, 0, 0])
        self.assertEqual(summary["totals"]["goty"]["pick"], [0, 0, 1, 0, 0, 1])
        voters = {v["id"]: v for v in summary["voters"]}
        self.assertEqual(voters[FRIEND]["votes"], {"goty": {"predict": 2, "pick": 5}, "rpg": {"predict": 0}})
        self.assertEqual(voters[FRIEND]["name"], "Haver")
        self.assertEqual(voters[ADMIN]["count"], 3)

        self.request("PUT", "/api/admin/winners", body={"category": "goty", "nominee": 2}, cookie=self.admin)
        response, summary = self.request("PUT", "/api/admin/winners", body={"category": "rpg", "nominee": 3},
                                         cookie=self.admin)
        self.assertEqual(response.status, 200)
        self.assertEqual(summary["winners"], {"goty": 2, "rpg": 3})
        self.assertEqual([(s["id"], s["correct"], s["agreed"]) for s in summary["scores"]],
                         [(ADMIN, 2, 1), (FRIEND, 1, 0)])
        _, summary = self.request("PUT", "/api/admin/winners", body={"category": "rpg", "nominee": None},
                                  cookie=self.admin)
        self.assertEqual(summary["winners"], {"goty": 2})


class ClosedVoteTests(ServerTest):
    closes_at = "2020-01-01T00:00:00+01:00"

    def test_no_votes_after_the_deadline(self):
        admin, _ = self.login(ADMIN)
        _, ballot = self.request("GET", "/api/ballot", cookie=admin)
        self.assertTrue(ballot["closed"])
        response, _ = self.request("PUT", "/api/votes", body={"category": "goty", "kind": "pick", "nominee": 0},
                                   cookie=admin)
        self.assertEqual(response.status, 409)
        # The admin can still mark the winners
        response, _ = self.request("PUT", "/api/admin/winners", body={"category": "goty", "nominee": 0},
                                   cookie=admin)
        self.assertEqual(response.status, 200)


class OpeningTests(ServerTest):
    """opensAt: the yagi.hu hub reveals the tile, and every member of the server gets in, without anyone acting."""

    def open_now(self):
        self.app.ballot.opens_at = server.datetime(2020, 1, 1, tzinfo=server.timezone.utc)

    def test_reveal_answer_only_from_the_opening(self):
        self.app.ballot.opens_at = server.datetime(2099, 1, 1, tzinfo=server.timezone.utc)
        response, body = self.request("GET", "/api/reveal")
        self.assertEqual(response.status, 404)
        self.assertNotIn("Awards", json.dumps(body))
        self.open_now()
        response, body = self.request("GET", "/api/reveal")
        self.assertEqual(response.status, 200)
        self.assertEqual((body["icon"], body["name"], body["url"], body["host"]),
                         ("🏆", "Imbul Awards", "https://imbulawards.yagi.hu", "imbulawards.yagi.hu"))

    def test_members_get_in_by_themselves_from_the_opening(self):
        self.guild_members = {FRIEND}
        self.app.ballot.opens_at = server.datetime(2099, 1, 1, tzinfo=server.timezone.utc)
        self.assertIsNone(self.login(FRIEND)[0])
        self.open_now()
        friend, _ = self.login(FRIEND, scope="identify guilds")
        self.assertIsNotNone(friend)
        self.assertIsNone(self.login(STRANGER, scope="identify guilds")[0])  # not on the server
        admin, _ = self.login(ADMIN, scope="identify guilds")
        _, access = self.request("GET", "/api/admin/access", cookie=admin)
        self.assertTrue(access["everyone"])
        self.assertTrue(access["everyoneAuto"])

    def test_the_admins_choice_wins_over_the_opening(self):
        self.guild_members = {FRIEND}
        admin, _ = self.login(ADMIN)
        self.request("POST", "/api/admin/access/everyone", body={"allowed": False}, cookie=admin)
        self.open_now()
        self.assertIsNone(self.login(FRIEND)[0])  # "nobody but the admins" stays so
        _, access = self.request("GET", "/api/admin/access", cookie=admin)
        self.assertFalse(access["everyone"])
        self.assertFalse(access["everyoneAuto"])

    def test_opening_is_before_the_deadline(self):
        ballot = server.Ballot()
        self.assertLess(ballot.opens_at, ballot.closes_at)
        self.assertEqual(ballot.opens_at.isoformat(), "2026-12-01T10:00:00+01:00")


class DijazottakTests(ServerTest):
    """After the deadline: the admin shows each category's result ("Díjazottak") to everyone, or hides it again."""

    def setUp(self):
        super().setUp()
        self.admin, _ = self.login(ADMIN, "Yagi")
        for user_id in (FRIEND, STRANGER):
            self.request("POST", "/api/admin/access", body={"userId": user_id, "allowed": True}, cookie=self.admin)
        self.friend, _ = self.login(FRIEND, "Haver")
        self.other, _ = self.login(STRANGER, "Harmadik")
        votes = {self.admin: (2, 0), self.friend: (2, 1), self.other: (0, server.NO_IDEA)}
        for cookie, (predict, pick) in votes.items():
            for kind, nominee in (("predict", predict), ("pick", pick)):
                self.request("PUT", "/api/votes", body={"category": "goty", "kind": kind, "nominee": nominee},
                             cookie=cookie)
        self.request("PUT", "/api/votes", body={"category": "rpg", "kind": "pick", "nominee": 3}, cookie=self.friend)

    def close(self):
        self.app.ballot.closes_at = server.datetime(2020, 1, 1, tzinfo=server.timezone.utc)

    def reveal(self, category, shown=True, cookie=None):
        return self.request("PUT", "/api/admin/reveal", body={"category": category, "shown": shown},
                            cookie=cookie or self.admin)

    def test_nothing_before_the_deadline_even_if_shown(self):
        self.reveal("goty")
        _, ballot = self.request("GET", "/api/ballot", cookie=self.friend)
        self.assertEqual(ballot["results"], {})

    def test_nothing_until_the_admin_shows_it(self):
        self.close()
        _, ballot = self.request("GET", "/api/ballot", cookie=self.friend)
        self.assertEqual(ballot["results"], {})

    def test_shown_category_has_the_most_voted_in_both_questions(self):
        self.close()
        response, summary = self.reveal("goty")
        self.assertEqual(response.status, 200)
        self.assertEqual(summary["revealed"], ["goty"])
        _, ballot = self.request("GET", "/api/ballot", cookie=self.other)
        self.assertEqual(ballot["results"], {"goty": {"predict": {"winners": [2], "votes": 2},
                                                      "pick": {"winners": [0, 1], "votes": 1}}})  # a tie: both
        # Hidden again
        self.reveal("goty", False)
        _, ballot = self.request("GET", "/api/ballot", cookie=self.other)
        self.assertEqual(ballot["results"], {})

    def test_no_idea_never_wins_and_no_votes_means_no_winner(self):
        self.close()
        self.reveal("rpg")
        self.reveal("action")
        _, ballot = self.request("GET", "/api/ballot", cookie=self.friend)
        self.assertEqual(ballot["results"]["rpg"], {"predict": {"winners": [], "votes": 0},
                                                    "pick": {"winners": [3], "votes": 1}})
        self.assertEqual(ballot["results"]["action"]["pick"], {"winners": [], "votes": 0})

    def test_real_winners_only_after_the_deadline(self):
        self.request("PUT", "/api/admin/winners", body={"category": "goty", "nominee": 4}, cookie=self.admin)
        _, ballot = self.request("GET", "/api/ballot", cookie=self.friend)
        self.assertEqual(ballot["actual"], {})
        self.close()
        _, ballot = self.request("GET", "/api/ballot", cookie=self.friend)
        self.assertEqual(ballot["actual"], {"goty": 4})  # shown even if the Imbul result is not
        self.assertEqual(ballot["results"], {})

    def test_only_the_admin_shows_or_hides(self):
        response, _ = self.reveal("goty", cookie=self.friend)
        self.assertEqual(response.status, 403)
        response, _ = self.reveal("nope")
        self.assertEqual(response.status, 400)
        response, _ = self.request("PUT", "/api/admin/reveal", body={"category": "goty", "shown": "yes"},
                                   cookie=self.admin)
        self.assertEqual(response.status, 400)

    def test_status_is_public(self):
        response, status = self.request("GET", "/api/status")
        self.assertEqual(response.status, 200)
        self.assertFalse(status["closed"])
        self.close()
        _, status = self.request("GET", "/api/status")
        self.assertTrue(status["closed"])


class ImageTests(unittest.TestCase):
    def test_only_wikimedia_pictures_are_passed_on(self):
        with tempfile.TemporaryDirectory() as folder:
            images = Path(folder) / "images.json"
            images.write_text(json.dumps({
                "Grand Theft Auto VI": {"title": "GTA VI", "url": "https://upload.wikimedia.org/wikipedia/en/4/46/GTA_VI.png"},
                "Pragmata": {"title": "Pragmata", "url": "https://evil.example/x.png"},
                "Saros": {"title": None, "url": None},
            }), encoding="utf-8")
            ballot = server.Ballot(server.NOMINEES_FILE, images)
        goty = ballot.public()["categories"][0]["nominees"]
        self.assertEqual(goty[2]["image"], "https://upload.wikimedia.org/wikipedia/en/4/46/GTA_VI.png")
        self.assertIsNone(goty[0]["image"])
        self.assertNotIn("Pragmata", ballot.images)
        self.assertNotIn("Saros", ballot.images)

    def test_every_stored_picture_is_usable(self):
        images = json.loads(server.IMAGES_FILE.read_text(encoding="utf-8"))
        for name, entry in images.items():
            if entry["url"]:
                with self.subTest(name=name):
                    self.assertRegex(entry["url"], server.IMAGE_URL)
                    self.assertNotIn("?", entry["url"])

    def test_pictures_are_allowed_by_the_page_policy(self):
        policy = server.SECURITY_HEADERS["Content-Security-Policy"]
        self.assertIn("https://upload.wikimedia.org", policy)


class WelcomePageTests(unittest.TestCase):
    def test_deadline_on_the_welcome_page_is_the_real_one(self):
        page = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        closes = server.Ballot().closes_at.astimezone(timezone(timedelta(hours=1)))  # Budapest in December
        self.assertEqual((closes.year, closes.month, closes.day, closes.hour, closes.minute), (2026, 12, 9, 23, 59))
        self.assertIn("2026. december 9-én 23:59-ig", page)


class NomineesFileTests(unittest.TestCase):
    def test_every_category_is_well_formed(self):
        data = json.loads(server.NOMINEES_FILE.read_text(encoding="utf-8"))
        ids = [c["id"] for c in data["categories"]]
        self.assertEqual(len(ids), len(set(ids)))
        for category in data["categories"]:
            with self.subTest(category=category["id"]):
                self.assertTrue(category["name"] and category["original"])
                self.assertGreaterEqual(len(category["nominees"]), 5)
                for nominee in category["nominees"]:
                    self.assertEqual(len(nominee), 2)
                    self.assertTrue(nominee[0])
        server.Ballot()  # the deadline parses, with a time zone
        self.assertIsNotNone(server.Ballot().closes_at.tzinfo)


if __name__ == "__main__":
    unittest.main()
