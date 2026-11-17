#!/bin/sh
# Deploy the merged main branch from GitHub to the VPS: the app goes to /opt/imbul-awards and runs as the
# imbul-awards systemd service on 127.0.0.1:8006. The votes live in /var/lib/imbul-awards and are never touched by a
# deploy; the Discord settings are in /etc/imbul-awards.env (set up once by hand). Caddy (deploy/Caddyfile.snippet, a site
# block in /etc/caddy/Caddyfile) serves imbulawards.yagi.hu from here.
set -e

KEY="${KEY:-$HOME/.ssh/yagismot_deploy}"
HOST="${HOST:-root@138.199.196.51}"
APP=/opt/imbul-awards

cd "$(dirname "$0")"
git fetch -q origin main
COMMIT=$(git rev-parse --short origin/main)
# The page shows the version (VERSION on origin/main) and checks version.json every minute: when a newer version is
# live, a "Frissült az oldal" dialog offers a reload.
VERSION=$(git show origin/main:VERSION | tr -d '[:space:]')
RELEASED=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Run the tests on origin/main itself (the code about to go live, not the local checkout): a failing test stops the
# deploy. The first Python that runs (python3, then python) is used.
for candidate in python3 python; do
	if "$candidate" -c "" 2>/dev/null; then PYTHON=$candidate; break; fi
done
[ -n "$PYTHON" ] || { echo "No Python found to run the tests; deploy stopped." >&2; exit 1; }
CHECKOUT=$(mktemp -d)
trap 'git worktree remove --force "$CHECKOUT" 2>/dev/null; rm -rf "$CHECKOUT"' EXIT
git worktree add -q --detach "$CHECKOUT" origin/main
(cd "$CHECKOUT" && "$PYTHON" -m unittest discover -s tests -q) || { echo "Tests failed; deploy stopped." >&2; exit 1; }

# Unpack into a fresh folder, then swap it in. core.autocrlf=false keeps the LF line endings on Windows.
git -c core.autocrlf=false archive --format=tar origin/main server.py nominees.json images.json static deploy/imbul-awards.service | ssh -i "$KEY" "$HOST" "set -e
  test -f /etc/imbul-awards.env || { echo 'Missing /etc/imbul-awards.env (see README); deploy stopped.' >&2; exit 1; }
  rm -rf $APP.new $APP.old
  mkdir $APP.new
  tar -x -C $APP.new
  echo '{\"version\": \"$VERSION\", \"commit\": \"$COMMIT\", \"released\": \"$RELEASED\"}' > $APP.new/version.json
  chmod -R u=rwX,go=rX $APP.new
  mv $APP.new/deploy/imbul-awards.service /etc/systemd/system/imbul-awards.service
  rmdir $APP.new/deploy
  if [ -d $APP ]; then mv $APP $APP.old; fi
  mv $APP.new $APP
  rm -rf $APP.old
  systemctl daemon-reload
  systemctl enable --quiet imbul-awards
  systemctl restart imbul-awards
  systemctl is-active imbul-awards"

echo "Deployed version $VERSION (origin/main $COMMIT) to https://imbulawards.yagi.hu"
