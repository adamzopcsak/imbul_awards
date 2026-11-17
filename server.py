"""Imbul Awards: the Imbul a Bakterház Discord server's own Game Awards vote, at imbulawards.yagi.hu (standard library only).

Every category of The Game Awards (nominees.json) has two votes: who you think will win ("predict") and who you would
give the award to ("pick"). Login is with Discord; only the admins (AWARDS_ADMIN_USERS) and the people the admin let in
on the admin page may vote. Everyone else who tries to log in is refused, and shows up on the admin page, where one
click lets them in. The admin sees every vote, the totals per category, and (once the winners are set) who guessed best.

Paths:
  GET    /                          the page (static/index.html, app.js, style.css, favicon.svg)
  GET    /version.json              the deployed version, written by deploy.sh (404 in local development)
  GET    /login                     off to Discord's login
  GET    /auth/callback             back from Discord
  POST   /logout                    log out
  GET    /api/status                whether voting is closed (public, for the welcome page)
  GET    /api/me                    who is logged in (401 if nobody, 403 if they may not vote)
  GET    /api/ballot                the categories, the nominees and my votes
  PUT    /api/votes                 {"category", "kind": "predict" | "pick", "nominee": index, -1 or null}
                                    (-1 is "Ötletem sincs, ember", null takes the vote back)
Admin only:
  GET    /api/admin/summary         every vote, the totals, the winners and the guessing leaderboard
  PUT    /api/admin/winners         {"category", "nominee": index or null}: the real winner
  PUT    /api/admin/reveal          {"category", "shown": true | false}: show the server's result in that category
                                    to everyone (after the deadline), or hide it again
  GET    /api/admin/access          who may vote, and who tried to log in without access
  POST   /api/admin/access          {"userId", "allowed": true | false}: let in / give back, or take away
  POST   /api/admin/access/everyone {"allowed": true}: every member of the Discord server gets in at their login;
                                    {"allowed": false}: nobody but the admins (as on Imbul Stats)

Configuration (environment, on the server in /etc/imbul-awards.env):
  AWARDS_PUBLIC_URL        https://imbulawards.yagi.hu (the Discord redirect is <this>/auth/callback)
  DISCORD_CLIENT_ID        the Discord application's OAuth2 client
  DISCORD_CLIENT_SECRET
  AWARDS_ADMIN_USERS       Discord user ids of the admins, comma separated (they can always log in)
  AWARDS_SESSION_SECRET    optional; else one is generated into the data folder on first start

Run locally (no Discord needed: --dev-login adds /dev-login?id=...&name=..., only on plain http):
  python server.py --port 8006 --data data --dev-login
then open http://127.0.0.1:8006/
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
NOMINEES_FILE = APP_DIR / "nominees.json"
IMAGES_FILE = APP_DIR / "images.json"
# The nominees' pictures are shown straight from Wikimedia (see scripts/fetch_images.py)
IMAGE_URL = re.compile(r"https://(upload|thumb)\.wikimedia\.org/[A-Za-z0-9_./%()'!,~+-]+")
VERSION_FILE = APP_DIR / "version.json"
PREFIX = ""  # the app has the whole of imbulawards.yagi.hu
STATIC_FILES = {
    PREFIX + "/": ("index.html", "text/html; charset=utf-8"),
    PREFIX + "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    PREFIX + "/style.css": ("style.css", "text/css; charset=utf-8"),
    PREFIX + "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"
DISCORD_GUILDS_URL = "https://discord.com/api/v10/users/@me/guilds"
DEFAULT_GUILD_ID = "368820263510605835"  # Imbul a Bakterház
EVERYONE = "everyone"  # granted_by of those let in as members of the server

SESSION_COOKIE = "imbul_awards"
STATE_COOKIE = "imbul_awards_state"
SESSION_DAYS = 30
STATE_SECONDS = 600
MAX_BODY_BYTES = 4_000
KINDS = ("predict", "pick")
NO_IDEA = -1  # the "Ötletem sincs, ember" answer, offered in every category next to the nominees
USER_ID = re.compile(r"[1-9][0-9]{15,20}")  # a Discord snowflake, kept as a string (too big for a JavaScript number)
AVATAR_HASH = re.compile(r"(a_)?[0-9a-f]{32}")
SECURITY_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; "
                               "img-src 'self' https://cdn.discordapp.com https://upload.wikimedia.org https://thumb.wikimedia.org; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Robots-Tag": "noindex",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    username   TEXT NOT NULL,
    avatar     TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS access (
    user_id    TEXT PRIMARY KEY,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL
);
-- Taken away on the admin page: they stay on the access list, to be given back one by one (as on Imbul Stats)
CREATE TABLE IF NOT EXISTS revoked (
    user_id    TEXT PRIMARY KEY,
    revoked_at TEXT NOT NULL,
    revoked_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refused (
    user_id    TEXT PRIMARY KEY,
    last_at    TEXT NOT NULL,
    attempts   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    user_id    TEXT NOT NULL,
    category   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    nominee    INTEGER NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (user_id, category, kind)
);
-- Categories whose result (the most voted nominees) the admin has shown to everyone
CREATE TABLE IF NOT EXISTS revealed (
    category   TEXT PRIMARY KEY,
    at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS winners (
    category   TEXT PRIMARY KEY,
    nominee    INTEGER NOT NULL,
    at         TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ids(text):
    return frozenset(part.strip() for part in (text or "").split(",") if USER_ID.fullmatch(part.strip()))


class Config:
    def __init__(self, env=os.environ, dev_login=False):
        self.public_url = (env.get("AWARDS_PUBLIC_URL") or "http://127.0.0.1:8006").rstrip("/")
        self.client_id = env.get("DISCORD_CLIENT_ID", "")
        self.client_secret = env.get("DISCORD_CLIENT_SECRET", "")
        self.admins = parse_ids(env.get("AWARDS_ADMIN_USERS"))
        # "Everyone on the server" lets in the members of this Discord server (Imbul a Bakterház by default)
        self.guild_id = env.get("AWARDS_GUILD_ID") or DEFAULT_GUILD_ID
        self.session_secret = env.get("AWARDS_SESSION_SECRET", "")
        self.secure = self.public_url.startswith("https://")
        # The dev login skips Discord entirely, so it never runs on a real (https) site
        self.dev_login = dev_login and not self.secure
        parts = urllib.parse.urlsplit(self.public_url)
        self.origin = f"{parts.scheme}://{parts.netloc}"

    @property
    def redirect_uri(self):
        return self.public_url + PREFIX + "/auth/callback"


class ValidationError(ValueError):
    pass


class Ballot:
    """The categories and nominees (nominees.json). A nominee is referred to by its index in its category."""

    def __init__(self, path=NOMINEES_FILE, images_path=IMAGES_FILE):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.title = data["title"]
        self.ceremony = data.get("ceremony", "")
        self.closes_at = datetime.fromisoformat(data["votingClosesAt"])
        self.categories = data["categories"]
        self.by_id = {category["id"]: category for category in self.categories}
        # {nominee name: {title, url}} from scripts/fetch_images.py; a nominee without one gets a placeholder
        images = json.loads(Path(images_path).read_text(encoding="utf-8")) if Path(images_path).is_file() else {}
        self.images = {name: entry["url"] for name, entry in images.items()
                       if isinstance(entry, dict) and isinstance(entry.get("url"), str) and IMAGE_URL.fullmatch(entry["url"])}

    def closed(self):
        return datetime.now(timezone.utc) >= self.closes_at

    def check(self, category_id, nominee, allow_no_idea=False):
        """ValidationError unless `nominee` is an index in that category (None is allowed: no vote; NO_IDEA too,
        for a vote, but never as a winner)."""
        category = self.by_id.get(category_id)
        if category is None:
            raise ValidationError("Nincs ilyen kategória.")
        if nominee is None or (allow_no_idea and type(nominee) is int and nominee == NO_IDEA):
            return
        if isinstance(nominee, bool) or not isinstance(nominee, int) or not 0 <= nominee < len(category["nominees"]):
            raise ValidationError("Nincs ilyen jelölt.")

    def public(self):
        return {
            "title": self.title,
            "ceremony": self.ceremony,
            "closesAt": self.closes_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "closed": self.closed(),
            "categories": [{"id": c["id"], "name": c["name"], "original": c["original"],
                            "nominees": [{"name": n[0], "detail": n[1], "image": self.images.get(n[0])}
                                         for n in c["nominees"]]}
                           for c in self.categories],
        }


class Store:
    """SQLite in the data folder. One connection per call; a lock keeps the writes in order."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "awards.db"
        self.lock = threading.Lock()
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _read(self, sql, args=()):
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(sql, args)]
        finally:
            conn.close()

    def _write(self, *statements):
        with self.lock:
            conn = self._connect()
            try:
                with conn:
                    for sql, args in statements:
                        conn.execute(sql, args)
            finally:
                conn.close()

    def session_secret(self):
        path = self.folder / "session_secret"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            os.chmod(path, 0o600)
        return path.read_text(encoding="utf-8").strip()

    # --- People

    def remember_user(self, user):
        stamp = now_iso()
        self._write(("INSERT INTO users (id, name, username, avatar, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(id) DO UPDATE SET name = excluded.name, username = excluded.username, "
                     "avatar = excluded.avatar, last_seen = excluded.last_seen",
                     (user["id"], user["name"], user["username"], user.get("avatar"), stamp, stamp)))

    def users(self):
        return {row["id"]: row for row in self._read("SELECT * FROM users")}

    def touch(self, user_id):
        self._write(("UPDATE users SET last_seen = ? WHERE id = ?", (now_iso(), user_id)))

    def granted(self):
        return {row["user_id"]: row for row in self._read("SELECT * FROM access")}

    def refused(self):
        return {row["user_id"]: row for row in self._read("SELECT * FROM refused")}

    def note_refused(self, user_id):
        self._write(("INSERT INTO refused (user_id, last_at, attempts) VALUES (?, ?, 1) ON CONFLICT(user_id) DO UPDATE "
                     "SET last_at = excluded.last_at, attempts = attempts + 1", (user_id, now_iso())))

    def revoked(self):
        return {row["user_id"]: row for row in self._read("SELECT * FROM revoked")}

    def grant(self, user_id, by):
        """Gives access (or gives it back, if it was taken away)."""
        self._write(("INSERT OR IGNORE INTO access (user_id, granted_at, granted_by) VALUES (?, ?, ?)",
                     (user_id, now_iso(), by)),
                    ("DELETE FROM revoked WHERE user_id = ?", (user_id,)),
                    ("DELETE FROM refused WHERE user_id = ?", (user_id,)))

    def revoke(self, user_ids, by):
        """Takes access away; they stay on the list, to be given back one by one."""
        stamp = now_iso()
        self._write(*[("INSERT OR IGNORE INTO revoked (user_id, revoked_at, revoked_by) VALUES (?, ?, ?)",
                       (user_id, stamp, by)) for user_id in user_ids])

    def is_granted(self, user_id):
        return bool(self._read("SELECT 1 FROM access WHERE user_id = ? AND user_id NOT IN (SELECT user_id FROM revoked)",
                               (user_id,)))

    def is_revoked(self, user_id):
        return bool(self._read("SELECT 1 FROM revoked WHERE user_id = ?", (user_id,)))

    # --- "Everyone on the server"

    def everyone(self):
        return any(row["value"] == "1" for row in self._read("SELECT value FROM settings WHERE key = 'everyone'"))

    def grant_everyone(self, by):
        """From now on every member of the server gets in at their Discord login; those taken away get it back.
        Returns how many got it back."""
        given_back = len(self.revoked())
        self._write(("INSERT INTO settings (key, value) VALUES ('everyone', '1') ON CONFLICT(key) DO UPDATE "
                     "SET value = excluded.value", ()),
                    ("INSERT INTO settings (key, value) VALUES ('everyone_by', ?) ON CONFLICT(key) DO UPDATE "
                     "SET value = excluded.value", (f"{by} {now_iso()}",)),
                    ("DELETE FROM revoked", ()))
        return given_back

    def revoke_everyone(self, by, admins):
        """Nobody but the admins gets in: the server's members no longer get in by themselves, and everyone given
        access is taken away (they stay on the list). Returns how many lost it."""
        ids = [uid for uid in self.granted() if uid not in admins and uid not in self.revoked()]
        self._write(("INSERT INTO settings (key, value) VALUES ('everyone', '0') ON CONFLICT(key) DO UPDATE "
                     "SET value = excluded.value", ()))
        self.revoke(ids, by)
        return len(ids)

    # --- Votes

    def votes_of(self, user_id):
        result = {}
        for row in self._read("SELECT category, kind, nominee FROM votes WHERE user_id = ?", (user_id,)):
            result.setdefault(row["category"], {})[row["kind"]] = row["nominee"]
        return result

    def all_votes(self):
        return self._read("SELECT * FROM votes ORDER BY at")

    def vote(self, user_id, category, kind, nominee):
        if nominee is None:
            self._write(("DELETE FROM votes WHERE user_id = ? AND category = ? AND kind = ?", (user_id, category, kind)))
        else:
            self._write(("INSERT INTO votes (user_id, category, kind, nominee, at) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT(user_id, category, kind) DO UPDATE SET nominee = excluded.nominee, at = excluded.at",
                         (user_id, category, kind, nominee, now_iso())))

    def revealed(self):
        return {row["category"]: row["at"] for row in self._read("SELECT * FROM revealed")}

    def set_revealed(self, category, shown):
        if shown:
            self._write(("INSERT OR IGNORE INTO revealed (category, at) VALUES (?, ?)", (category, now_iso())))
        else:
            self._write(("DELETE FROM revealed WHERE category = ?", (category,)))

    def winners(self):
        return {row["category"]: row["nominee"] for row in self._read("SELECT category, nominee FROM winners")}

    def set_winner(self, category, nominee):
        if nominee is None:
            self._write(("DELETE FROM winners WHERE category = ?", (category,)))
        else:
            self._write(("INSERT INTO winners (category, nominee, at) VALUES (?, ?, ?) ON CONFLICT(category) "
                         "DO UPDATE SET nominee = excluded.nominee, at = excluded.at", (category, nominee, now_iso())))


class Signer:
    """Signed, expiring cookie values: base64(JSON) + "." + HMAC-SHA256."""

    def __init__(self, secret):
        self.key = secret.encode("utf-8")

    def _mac(self, payload):
        return base64.urlsafe_b64encode(hmac.new(self.key, payload.encode("ascii"), hashlib.sha256).digest()).decode().rstrip("=")

    def dumps(self, data, seconds):
        payload = base64.urlsafe_b64encode(json.dumps({**data, "exp": int(time.time()) + seconds}).encode()).decode().rstrip("=")
        return f"{payload}.{self._mac(payload)}"

    def loads(self, value):
        try:
            payload, mac = value.split(".", 1)
            if not hmac.compare_digest(mac, self._mac(payload)):
                return None
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("exp", 0) < time.time():
            return None
        return data


def discord_user(config, code, with_guilds=False):
    """Exchanges the OAuth code for a token and returns the Discord user it belongs to (tests replace this). With
    `with_guilds` (the "guilds" scope was asked for), the user also gets "guild_ids": the ids of their servers."""
    headers = {"User-Agent": "imbul-awards/1.0", "Accept": "application/json"}
    body = urllib.parse.urlencode({
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
    }).encode()
    request = urllib.request.Request(DISCORD_TOKEN_URL, data=body, method="POST",
                                     headers={**headers, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=15) as response:
        token = json.load(response)["access_token"]
    request = urllib.request.Request(DISCORD_ME_URL, headers={**headers, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=15) as response:
        me = json.load(response)
    if with_guilds:
        # Only used to see whether they are on the server; the list is not stored
        request = urllib.request.Request(DISCORD_GUILDS_URL, headers={**headers, "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                me["guild_ids"] = [str(guild.get("id")) for guild in json.load(response)]
        except (urllib.error.URLError, OSError, ValueError):
            me["guild_ids"] = []  # the scope was refused or Discord failed: treated as not a member
    return me


def clean_user(me):
    """The parts of a Discord user we keep."""
    user_id = str(me.get("id", ""))
    if not USER_ID.fullmatch(user_id):
        raise ValueError("not a Discord user")
    username = str(me.get("username") or "")[:40] or user_id
    avatar = me.get("avatar")
    return {"id": user_id, "username": username, "name": str(me.get("global_name") or username)[:64],
            "avatar": avatar if isinstance(avatar, str) and AVATAR_HASH.fullmatch(avatar) else None}


def avatar_url(user):
    if user and user.get("avatar"):
        return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=64"
    return None


class App:
    def __init__(self, config, data_folder, ballot=None):
        self.config = config
        self.store = Store(data_folder)
        self.ballot = ballot or Ballot()
        self.signer = Signer(config.session_secret or self.store.session_secret())

    def is_admin(self, user_id):
        return user_id in self.config.admins

    def may_vote(self, user_id):
        return self.is_admin(user_id) or self.store.is_granted(user_id)

    def person(self, user_id, users=None):
        user = (users if users is not None else self.store.users()).get(user_id)
        return {"id": user_id, "name": user["name"] if user else user_id, "username": user["username"] if user else None,
                "avatarUrl": avatar_url(user)}

    def summary(self):
        """Everything for the admin page: every vote, the totals per category and kind, and the guessing scores."""
        users = self.store.users()
        votes = self.store.all_votes()
        winners = self.store.winners()
        voters = sorted({vote["user_id"] for vote in votes}, key=lambda uid: self.person(uid, users)["name"].lower())
        totals = {c["id"]: {**{kind: [0] * len(c["nominees"]) for kind in KINDS}, "noIdea": dict.fromkeys(KINDS, 0)}
                  for c in self.ballot.categories}
        by_user = {uid: {} for uid in voters}
        for vote in votes:
            counts = totals.get(vote["category"], {}).get(vote["kind"])
            if counts is not None and 0 <= vote["nominee"] < len(counts):
                counts[vote["nominee"]] += 1
            elif counts is not None and vote["nominee"] == NO_IDEA:
                totals[vote["category"]]["noIdea"][vote["kind"]] += 1
            by_user[vote["user_id"]].setdefault(vote["category"], {})[vote["kind"]] = vote["nominee"]
        scores = []
        for uid in voters:
            guessed = [cid for cid, winner in winners.items() if by_user[uid].get(cid, {}).get("predict") == winner]
            agreed = [cid for cid, winner in winners.items() if by_user[uid].get(cid, {}).get("pick") == winner]
            predicted = sum(1 for cid in winners if "predict" in by_user[uid].get(cid, {}))
            scores.append({**self.person(uid, users), "correct": len(guessed), "agreed": len(agreed),
                           "predicted": predicted})
        scores.sort(key=lambda s: (-s["correct"], -s["agreed"], s["name"].lower()))
        summary = {
            "voters": [{**self.person(uid, users), "votes": by_user[uid],
                        "count": sum(len(kinds) for kinds in by_user[uid].values())} for uid in voters],
            "totals": totals,
            "winners": winners,
            "scores": scores,
            "decided": len(winners),
            "revealed": sorted(self.store.revealed()),
            "closed": self.ballot.closed(),
        }
        summary["results"] = self.results(totals)
        return summary

    def results(self, totals=None):
        """The server's result in every category the admin has shown: per question, the nominees with the most votes
        (all of them on a tie; "Ötletem sincs" never wins, and nobody wins without a vote)."""
        if totals is None:
            totals = self.summary()["totals"]
        results = {}
        for category in self.store.revealed():
            if category not in totals:
                continue
            results[category] = {}
            for kind in KINDS:
                counts = totals[category][kind]
                top = max(counts, default=0)
                results[category][kind] = {"winners": [i for i, c in enumerate(counts) if top and c == top], "votes": top}
        return results

    def access_list(self):
        """For the admin page, as on Imbul Stats: everyone who may vote or had access taken away (the most recent
        visitor first, those who never came last), whether everyone on the server gets in, and the refused logins."""
        users = self.store.users()
        granted = self.store.granted()
        revoked = self.store.revoked()

        def by_name(by):
            if by == EVERYONE:
                return "a szerver tagjaként"
            return self.person(by, users)["name"]

        people = []
        for uid in set(granted) | set(self.config.admins):
            given, taken = granted.get(uid), revoked.get(uid)
            user = users.get(uid)
            people.append({
                **self.person(uid, users),
                "isAdmin": uid in self.config.admins,
                "lastSeen": user["last_seen"] if user else None,
                "granted": given and uid not in self.config.admins and {"at": given["granted_at"],
                                                                        "by": by_name(given["granted_by"])},
                "revoked": taken and {"at": taken["revoked_at"], "by": by_name(taken["revoked_by"])},
            })
        seen = sorted((p for p in people if p["lastSeen"]), key=lambda p: p["lastSeen"], reverse=True)
        people = seen + sorted((p for p in people if not p["lastSeen"]), key=lambda p: p["name"].lower())
        refused = self.store.refused()
        return {
            "everyone": self.store.everyone(),
            "people": people,
            "allowedNow": sum(1 for p in people if not p["revoked"]),
            "refused": sorted(({**self.person(uid, users), "lastAt": row["last_at"], "attempts": row["attempts"]}
                               for uid, row in refused.items()
                               if (uid not in granted or uid in revoked) and uid not in self.config.admins),
                              key=lambda p: p["lastAt"], reverse=True),
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "imbul-awards"
    sys_version = ""
    app = None  # set by make_server
    discord_user = staticmethod(discord_user)  # tests replace it

    def log_message(self, format, *args):  # noqa: A002 - signature of the base class
        pass  # Caddy keeps the access log

    # --- Responses

    def _send(self, status, body=b"", content_type="application/json; charset=utf-8", headers=None, cookies=()):
        self.send_response(status)
        for name, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(name, value)
        if self.app.config.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        if body or status not in (HTTPStatus.NO_CONTENT, HTTPStatus.FOUND, HTTPStatus.MOVED_PERMANENTLY):
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, payload, cookies=()):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), cookies=cookies)

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _redirect(self, location, cookies=()):
        self._send(HTTPStatus.FOUND, headers={"Location": location}, cookies=cookies)

    def _cookie(self, name, value, seconds):
        parts = [f"{name}={value}", "Path=/", f"Max-Age={seconds}", "HttpOnly", "SameSite=Lax"]
        if self.app.config.secure:
            parts.append("Secure")
        return "; ".join(parts)

    # --- Requests

    def _path(self):
        return self.path.split("?", 1)[0]

    def _query(self):
        return dict(urllib.parse.parse_qsl(self.path.split("?", 1)[1])) if "?" in self.path else {}

    def _cookies(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001 - a broken Cookie header counts as no cookie
            return {}
        return {name: morsel.value for name, morsel in jar.items()}

    def _session_user_id(self):
        data = self.app.signer.loads(self._cookies().get(SESSION_COOKIE, ""))
        user_id = data.get("uid") if data else None
        return user_id if isinstance(user_id, str) and USER_ID.fullmatch(user_id) else None

    def _same_origin(self):
        """Writes only from our own page: the browser tells where the request came from (SameSite=Lax would still
        let a sibling *.yagi.hu site send the cookie)."""
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        scheme = "https" if self.app.config.secure else "http"
        return origin is None or origin in (self.app.config.origin, f"{scheme}://{self.headers.get('Host', '')}")

    def _body(self):
        if self.headers.get_content_type() != "application/json":
            raise ValidationError("Content-Type: application/json kell")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError("hibás Content-Length") from None
        if not 0 < length <= MAX_BODY_BYTES:
            raise ValidationError("hibás méretű kérés")
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValidationError("hibás JSON") from None
        if not isinstance(body, dict):
            raise ValidationError("JSON objektum kell")
        return body

    def _voter(self):
        """The logged-in user who may vote, or None after answering 401 / 403 (access is re-checked every time)."""
        user_id = self._session_user_id()
        if user_id is None:
            self._error(HTTPStatus.UNAUTHORIZED, "Jelentkezz be Discorddal.")
            return None
        if not self.app.may_vote(user_id):
            self._error(HTTPStatus.FORBIDDEN, "Még nincs jogod szavazni.")
            return None
        return user_id

    def _admin(self):
        user_id = self._voter()
        if user_id is not None and not self.app.is_admin(user_id):
            self._error(HTTPStatus.FORBIDDEN, "Ez csak az adminnak elérhető.")
            return None
        return user_id

    # --- GET

    def do_GET(self):
        path = self._path()
        routes = {
            PREFIX + "/login": self._login,
            PREFIX + "/auth/callback": self._callback,
            PREFIX + "/dev-login": self._dev_login,
            PREFIX + "/api/status": self._status,
            PREFIX + "/api/me": self._me,
            PREFIX + "/api/ballot": self._ballot,
            PREFIX + "/api/admin/summary": self._summary,
            PREFIX + "/api/admin/access": self._access,
        }
        if path in STATIC_FILES:
            name, content_type = STATIC_FILES[path]
            self._send(HTTPStatus.OK, (STATIC_DIR / name).read_bytes(), content_type,
                       headers={"Cache-Control": "no-cache"})
        elif path == PREFIX + "/version.json" and VERSION_FILE.is_file():
            self._send(HTTPStatus.OK, VERSION_FILE.read_bytes())
        elif path in routes:
            routes[path]()
        else:
            self._error(HTTPStatus.NOT_FOUND, "Nincs ilyen oldal.")

    do_HEAD = do_GET

    def _login(self):
        config = self.app.config
        if not (config.client_id and config.client_secret):
            return self._redirect(PREFIX + "/?hiba=beallitas")
        state = secrets.token_urlsafe(24)
        # While everyone on the server may vote, Discord is also asked which servers they are in
        scope = "identify guilds" if self.app.store.everyone() else "identify"
        query = urllib.parse.urlencode({"client_id": config.client_id, "response_type": "code",
                                        "redirect_uri": config.redirect_uri, "scope": scope,
                                        "state": state, "prompt": "none"})
        self._redirect(f"{DISCORD_AUTHORIZE_URL}?{query}",
                       cookies=[self._cookie(STATE_COOKIE, self.app.signer.dumps({"s": state}, STATE_SECONDS), STATE_SECONDS)])

    def _callback(self):
        query = self._query()
        saved = self.app.signer.loads(self._cookies().get(STATE_COOKIE, ""))
        clear_state = self._cookie(STATE_COOKIE, "", 0)
        state = query.get("state", "")
        if "error" in query or not query.get("code") or not saved or not hmac.compare_digest(saved.get("s", ""), state):
            return self._redirect(PREFIX + "/?hiba=sikertelen", cookies=[clear_state])
        try:
            me = self.discord_user(self.app.config, query["code"], self.app.store.everyone())
            user = clean_user(me)
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return self._redirect(PREFIX + "/?hiba=sikertelen", cookies=[clear_state])
        self._finish_login(user, [clear_state], me.get("guild_ids") or [])

    def _dev_login(self):
        if not self.app.config.dev_login:
            return self._error(HTTPStatus.NOT_FOUND, "Nincs ilyen oldal.")
        query = self._query()
        try:
            user = clean_user({"id": query.get("id"), "username": query.get("name"), "global_name": query.get("name")})
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "id: Discord azonosító kell")
        # &tag=1: as if they were on the server
        self._finish_login(user, [], [self.app.config.guild_id] if query.get("tag") == "1" else [])

    def _finish_login(self, user, cookies, guild_ids=()):
        store = self.app.store
        store.remember_user(user)
        if (not self.app.may_vote(user["id"]) and store.everyone() and self.app.config.guild_id in guild_ids
                and not store.is_revoked(user["id"])):
            store.grant(user["id"], EVERYONE)  # on the server while everyone may vote: in, and on the list
        if not self.app.may_vote(user["id"]):
            self.app.store.note_refused(user["id"])
            return self._redirect(PREFIX + "/?hiba=nincs-jog", cookies=[*cookies, self._cookie(SESSION_COOKIE, "", 0)])
        session = self.app.signer.dumps({"uid": user["id"]}, SESSION_DAYS * 86400)
        self._redirect(PREFIX + "/", cookies=[*cookies, self._cookie(SESSION_COOKIE, session, SESSION_DAYS * 86400)])

    def _me(self):
        user_id = self._voter()
        if user_id is None:
            return
        self.app.store.touch(user_id)  # "Utoljára" on the admin page's access list
        self._json(HTTPStatus.OK, {**self.app.person(user_id), "isAdmin": self.app.is_admin(user_id)})

    def _status(self):
        self._json(HTTPStatus.OK, {"closed": self.app.ballot.closed(), "closesAt": self.app.ballot.public()["closesAt"]})

    def _ballot(self):
        user_id = self._voter()
        if user_id is None:
            return
        ballot = self.app.ballot.public()
        # After the deadline: the results of the categories the admin has shown (nothing else about the votes)
        results = self.app.results() if ballot["closed"] else {}
        self._json(HTTPStatus.OK, {**ballot, "votes": self.app.store.votes_of(user_id), "results": results})

    def _summary(self):
        if self._admin() is not None:
            self._json(HTTPStatus.OK, self.app.summary())

    def _access(self):
        if self._admin() is not None:
            self._json(HTTPStatus.OK, self.app.access_list())

    # --- Writes

    def _write(self, handler):
        if not self._same_origin():
            return self._error(HTTPStatus.FORBIDDEN, "Más oldalról nem lehet.")
        try:
            handler()
        except ValidationError as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))

    def do_POST(self):
        path = self._path()
        if path == PREFIX + "/logout":
            return self._write(self._logout)
        if path == PREFIX + "/api/admin/access":
            return self._write(self._set_access)
        if path == PREFIX + "/api/admin/access/everyone":
            return self._write(self._set_everyone)
        self._error(HTTPStatus.NOT_FOUND, "Nincs ilyen oldal.")

    def do_PUT(self):
        path = self._path()
        if path == PREFIX + "/api/votes":
            return self._write(self._vote)
        if path == PREFIX + "/api/admin/reveal":
            return self._write(self._set_revealed)
        if path == PREFIX + "/api/admin/winners":
            return self._write(self._set_winner)
        self._error(HTTPStatus.NOT_FOUND, "Nincs ilyen oldal.")

    def _logout(self):
        self._send(HTTPStatus.NO_CONTENT, cookies=[self._cookie(SESSION_COOKIE, "", 0)])

    def _vote(self):
        user_id = self._voter()
        if user_id is None:
            return
        body = self._body()
        if body.get("kind") not in KINDS:
            raise ValidationError("kind: predict vagy pick")
        self.app.ballot.check(body.get("category"), body.get("nominee"), allow_no_idea=True)
        if self.app.ballot.closed():
            return self._error(HTTPStatus.CONFLICT, "A szavazás lezárult.")
        self.app.store.vote(user_id, body["category"], body["kind"], body.get("nominee"))
        self._json(HTTPStatus.OK, {"votes": self.app.store.votes_of(user_id)})

    def _set_revealed(self):
        if self._admin() is None:
            return
        body = self._body()
        self.app.ballot.check(body.get("category"), None)
        if not isinstance(body.get("shown"), bool):
            raise ValidationError("shown: true vagy false")
        self.app.store.set_revealed(body["category"], body["shown"])
        self._json(HTTPStatus.OK, self.app.summary())

    def _set_winner(self):
        if self._admin() is None:
            return
        body = self._body()
        self.app.ballot.check(body.get("category"), body.get("nominee"))
        self.app.store.set_winner(body["category"], body.get("nominee"))
        self._json(HTTPStatus.OK, self.app.summary())

    def _set_access(self):
        admin_id = self._admin()
        if admin_id is None:
            return
        body = self._body()
        user_id = body.get("userId")
        if not isinstance(user_id, str) or not USER_ID.fullmatch(user_id.strip()):
            raise ValidationError("Ez nem Discord azonosító (17-20 számjegy).")
        if not isinstance(body.get("allowed"), bool):
            raise ValidationError("allowed: true vagy false")
        user_id = user_id.strip()
        if user_id in self.app.config.admins and not body["allowed"]:
            raise ValidationError("Az admin jogát itt nem lehet elvenni.")
        if body["allowed"]:
            self.app.store.grant(user_id, admin_id)
        else:
            self.app.store.revoke([user_id], admin_id)
        self._json(HTTPStatus.OK, self.app.access_list())

    def _set_everyone(self):
        """{"allowed": true}: every member of the server gets in; false: nobody but the admins (as on Imbul Stats)."""
        admin_id = self._admin()
        if admin_id is None:
            return
        body = self._body()
        if not isinstance(body.get("allowed"), bool):
            raise ValidationError("allowed: true vagy false")
        if body["allowed"]:
            count = self.app.store.grant_everyone(admin_id)
        else:
            count = self.app.store.revoke_everyone(admin_id, self.app.config.admins)
        self._json(HTTPStatus.OK, {**self.app.access_list(), "changed": count})


def make_server(host, port, app):
    handler = type("BoundHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8006)
    parser.add_argument("--data", default=str(APP_DIR / "data"), help="folder of awards.db")
    parser.add_argument("--dev-login", action="store_true", help="local testing without Discord (never on https)")
    parser.add_argument("--closes-at", help="local testing with --dev-login: another voting deadline, e.g. "
                                            "2026-11-01T00:00:00+01:00 to see the Díjazottak page")
    args = parser.parse_args()
    config = Config(dev_login=args.dev_login)
    app = App(config, args.data)
    if args.closes_at:
        if not config.dev_login:
            parser.error("--closes-at only works with --dev-login, on plain http")
        app.ballot.closes_at = datetime.fromisoformat(args.closes_at)
    server = make_server(args.host, args.port, app)
    if not config.admins:
        print("Warning: AWARDS_ADMIN_USERS is empty, nobody can log in.", flush=True)
    print(f"Serving http://{args.host}:{args.port}{PREFIX}/ (data: {args.data})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
