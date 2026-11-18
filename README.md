# Imbul Awards

The Imbul a Bakterház Discord server's own version of The Game Awards, at https://imbulawards.yagi.hu. The UI is in
Hungarian.

Every category of The Game Awards 2026 has two votes:

1. **Szerinted ki fogja megkapni a díjat?** (🔮, who you think will win)
2. **Te kinek adnád a díjat?** (❤️, who you would give it to)

The "who would you give it to" question also offers **🤷 Ötletem sincs, ember** (stored as nominee `-1`), for those
who know none of the nominees: it is counted separately on the admin page and never wins the Imbul-díj. The tip has no
such card: a tip can always be a bold guess.

The ballot is paged: one category per page, every nominee as a card with its picture. Clicking a card saves the vote
at once; another card changes it, but a vote cannot be taken back. Once both questions of a page are answered, the page lights up and the next
unanswered category slides in by itself; after the last one comes a summary of all your votes. The arrows, the dots
(gold when a category is done), the ← / → keys and the address (`#goty`, `#kesz` for the summary) move between pages
by hand. Votes can be changed until the deadline in `ballots/2026.json` (`votingClosesAt`), after which the ballot is
read-only.

## Díjazottak (after the deadline)

Once `votingClosesAt` has passed, nobody can vote any more (the server refuses it), and the ballot turns into the
**Díjazottak** page: the same pages, one category each, every nominee in black and white, with a note that the
winners are published during The Game Awards show, in step with it. Each voter's own choices are marked ("a te
tipped", "a te szavazatod").

On **Admin → Díjazottak** every category has a **Mutatás mindenkinek** button: then, in both questions, the nominee
with the most votes ("Ötletem sincs" never counts; all of them on a tie) turns colour, gets a gold frame and a
"🏆 Díjazott" badge, and the category's dot turns gold. **Elrejtés** takes it back. Open pages check every 20
seconds and light up a category as soon as it is shown. The welcome page says that voting is closed.

The real winners at The Game Awards are set on the same tab, one dropdown per category (**⭐ A gálán nyert**; the
same as the 🏆 button on Összesítés, and the guessing leaderboard counts from it). After the deadline they show to
everyone in the "who will win" row, kept apart from the server's own results:

- **⭐ A gálán nyert** (teal frame, badge at the bottom of the picture): the real winner; a voter whose tip it was
  sees "eltaláltad! 🎯".
- **🔮 Imbul tippje** / **🏆 Imbul-díj** (gold frame, badge at the top): the most votes in the "who will win" and
  the "who would you give it to" rows, once shown with Mutatás mindenkinek.

To try it locally: `python server.py --port 8006 --data data --dev-login --closes-at 2026-11-01T00:00:00+01:00`
(`--closes-at` and `--opens-at` work only with `--dev-login`).

## Years

Every year has its own file in `ballots/` (`2026.json`, next year `2027.json`, ...), with the year, the deadline
(`votingClosesAt`), the reveal (`opensAt`) and the nominees. The latest year is the current vote and what the page
shows by default; an earlier year can never be voted in any more, and shows its Díjazottak (results), page by page as
the current one. From the second year on, an **Év** dropdown in the header switches between them (kept in the address
as `?ev=2026`); the admin pages follow the chosen year too (totals, votes, Díjazottak, real winners).

The database keeps each year's votes, shown results and real winners apart (a `year` column). A database from before
the years is converted at the first start: its data becomes 2026's.

**Next year:** add `ballots/2027.json` (with the new nominees, `opensAt` and `votingClosesAt`), run
`scripts/fetch_images.py` for the new pictures, and deploy. The yagi.hu hub's reveal (`/hamarosan/2.json`) follows the
current year's `opensAt`.

## Who can get in

Login is with Discord (OAuth2, `identify` scope only). Only these people get in:

- the admins, `AWARDS_ADMIN_USERS` (they can never be locked out), and
- those the admin lets in on the admin page (**Admin → Hozzáférés**).

Everyone else is refused ("még nincs jogod szavazni") and listed under **Belépni próbáltak** on the admin page with
their Discord name and id, so one click lets them in. Access is checked on every request, so taking it away works at
once, even for someone who is logged in. At launch nobody but the admin has access.

As on Imbul Stats, **Admin → Hozzáférés** has two buttons:

- **Jogosultság adása mindenkinek a szerveren:** from then on every member of the Imbul a Bakterház server
  (`AWARDS_GUILD_ID`, that server by default) gets in. Unlike Imbul Stats, this app has no member list, so it is
  checked at the login: while this is on, the Discord login also asks for the `guilds` scope (the user sees "which
  servers you are in"), and a member is let in and put on the access list ("a szerver tagjaként"). The server list is
  not stored. Those taken away earlier get access back.
- **Jogosultság megvonása mindenkitől:** turns that off and takes access away from everyone but the admins. They stay
  on the list and can be given back one by one (**Visszaadás**); one taken away (**Elvétel**) stays out even while
  everyone on the server may vote.

**The reveal, 2026-12-01 10:00** (`opensAt` in `ballots/2026.json`), happens by itself:

- The yagi.hu hub shows the project as **Imbul ******** ("Hamarosan", a countdown, no name or link; the linked tile is
  in the hub's admin-only `private/tiles.js` until then). At zero the hub asks `/hamarosan/2.json`, which Caddy passes
  to this app's `GET /api/reveal`: 404 before `opensAt`, the name, link and description from then on. The tile
  becomes a link, on open pages too, without a deploy.
- From the same moment every member of the server gets in, as if **Jogosultság adása mindenkinek a szerveren** had
  been pressed. Pressing either access button by hand before (or after) wins over this: "mindenkinek" opens it at
  once, "megvonás mindenkitől" keeps it closed even after `opensAt`.

## Admin

The admin votes like everyone else, and also gets the **Admin** tab:

- **Összesítés:** per category, how many 🔮 and ❤️ votes each nominee got. After the show, the 🏆 button marks the
  real winner.
- **Ki mire szavazott:** one person's votes in every category, and a table of everyone's votes.
- **Tippverseny:** who guessed the most winners (🔮); ties go to whose ❤️ favourite won more often.
- **Hozzáférés:** let people in by Discord id or from the refused list, and take access away.

## Nominees

`ballots/<year>.json` (now `ballots/2026.json`) holds a year's categories and nominees (Hungarian category name, the original English name, and each
nominee as `[name, studio or detail]`). Votes refer to a nominee by its position in its category, so do not reorder
or remove nominees once voting has started (fixing a typo is fine). The 2026 list was announced on 2026-11-17; the
sources are in the file.

**Pictures:** every nominee shows The Game Awards' own picture (the same as on thegameawards.com, square, including
photos of the performers, creators, players and teams). `scripts/tga_<year>.txt` lists them (read off the site by hand:
it sits behind a Cloudflare check, its picture host does not); `scripts/fetch_tga_images.py <year>` downloads them,
shrinks them to 480x480 JPEGs into `static/img/<year>/` (about 50 KB each, served by this app and cached by browsers)
and writes each one into the ballot as the nominee's third field. It needs Pillow (`python -m pip install pillow`),
the app itself does not. A nominee without one falls back to `images.json` (`scripts/fetch_images.py`: Wikipedia,
Wikimedia, a few Steam store images), and one whose picture does not load shows its initials.

## How it works

- `server.py` (Python standard library only) serves the page from `static/` and the JSON API (see its docstring).
- The data is one SQLite database, `awards.db`, in the data folder (`/var/lib/imbul-awards` on the server, never
  touched by a deploy): users, access, refused logins, votes and winners. The session signing key is generated into
  the same folder on first start (or set `AWARDS_SESSION_SECRET`); a new key logs everyone out.
- The session is a signed cookie (HMAC-SHA256, 30 days, HttpOnly, SameSite=Lax, Secure on HTTPS). Writes must be JSON
  and come from this site (Origin / Sec-Fetch-Site check). Every response has a strict Content-Security-Policy (no
  inline scripts or styles), `X-Frame-Options: DENY` and `nosniff`.
- **Versioning:** the version is in `VERSION` (semantic versioning); bump it in the pull request of a change.
  `deploy.sh` writes `version.json` (the version and the release time), shown at the bottom of the page. An open page
  checks it every minute, and once a newer version is live a "Frissült az oldal" dialog offers a reload, as on Imbul
  Forever.

## Run locally

```sh
python server.py --port 8006 --data data --dev-login
```

Then open http://127.0.0.1:8006/. `--dev-login` adds `/dev-login?id=<Discord id>&name=<name>`, which logs in without
Discord; it never works on an https URL. Set `AWARDS_ADMIN_USERS` to the id you use there to see the admin tab.
`data/` is git-ignored.

## Tests

```sh
python -m unittest discover -s tests -v
```

They start the real server on a free port with a temporary data folder; Discord is replaced by a fake. GitHub Actions
runs them on every pull request and push to `main` (`.github/workflows/tests.yml`), and `deploy.sh` runs them before
every deploy.

## Setup on the server (once)

1. **DNS:** `imbulawards.yagi.hu` must point to the VPS (it already did on 2026-11-17).
2. **Discord:** in the Developer Portal, the OAuth2 application (the one Imbul Stats uses is fine) → OAuth2 →
   Redirects: add `https://imbulawards.yagi.hu/auth/callback`.
3. **Settings:** copy `deploy/imbul-awards.env.example` to `/etc/imbul-awards.env` (`chmod 600`) and fill it in. The
   admin id is the same as `DASHBOARD_ADMIN_USERS` in Imbul Stats' `.env`.
4. **Caddy:** add the block in `deploy/Caddyfile.snippet` to `/etc/caddy/Caddyfile` (and to its copy in the
   yagismot_and_imbulstats repo), and install the yagi.hu repo's `server/yagi-admin.caddy.example` again (it has the
   `/hamarosan/2.json` route for the reveal), then `systemctl reload caddy`. Both before 2026-12-01 10:00.
5. Run `./deploy.sh`.

## Deploy

```sh
./deploy.sh
```

It runs the tests on `origin/main`, then unpacks `server.py`, `ballots/`, `images.json`, `static/` and the systemd unit into
`/opt/imbul-awards` on the VPS, writes `version.json`, installs `deploy/imbul-awards.service` and restarts it. It stops
if `/etc/imbul-awards.env` is missing.
