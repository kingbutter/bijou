#!/usr/bin/env bash
#
# Install Bijou on a bare-metal Debian box.
# Run from the repo root:  sudo ./deploy/install.sh
#
# Idempotent: safe to re-run to update an existing install.
set -euo pipefail

PREFIX=/opt/bijou
ENVFILE=/etc/bijou.env
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -f "$REPO/app/bijou.py" ]] || die "run this from the repo, not a copy of the script"

command -v python3 >/dev/null || die "python3 is not installed"

echo "==> user and directories"
id bijou &>/dev/null || useradd --system --home "$PREFIX" --shell /usr/sbin/nologin bijou
install -d -o bijou -g bijou "$PREFIX"

echo "==> application"
install -o bijou -g bijou -m 0644 "$REPO/app/bijou.py" "$PREFIX/bijou.py"
install -d -o bijou -g bijou "$PREFIX/static" "$PREFIX/static/fonts"
for f in index.html setup.html; do
  install -o bijou -g bijou -m 0644 "$REPO/app/static/$f" "$PREFIX/static/$f"
done
for f in "$REPO"/app/static/fonts/*; do
  [[ -e "$f" ]] && install -o bijou -g bijou -m 0644 "$f" "$PREFIX/static/fonts/"
done

echo "==> configuration"
if [[ -f "$ENVFILE" ]]; then
  echo "    $ENVFILE exists, leaving it alone"
else
  install -m 0600 "$REPO/.env.example" "$ENVFILE"
  echo "    wrote $ENVFILE — edit it before starting"
fi

echo "==> systemd"
install -m 0644 "$REPO/deploy/bijou.service" /etc/systemd/system/bijou.service
systemctl daemon-reload

systemctl enable --now bijou
systemctl restart bijou
sleep 2

if ! curl -fsS "http://127.0.0.1:8080/healthz" >/dev/null 2>&1; then
  echo
  echo "==> started, but not answering yet. Check: journalctl -u bijou -n 30"
  exit 0
fi

PORT="$(grep -oP '^BIJOU_PORT=\K[0-9]+' "$ENVFILE" 2>/dev/null || echo 8080)"
HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST="${HOST:-localhost}"

if grep -q '^PLEX_TOKEN=.\+' "$ENVFILE"; then
  cat <<EOF

==> Running.

  Display    http://$HOST:$PORT/
  Logs       journalctl -u bijou -f
  Health     curl localhost:$PORT/healthz
EOF
else
  cat <<EOF

==> Running, but not connected to Plex yet.

  Open http://$HOST:$PORT/setup to sign in and get your settings, paste them
  into $ENVFILE, then:

      sudo systemctl restart bijou
EOF
fi

cat <<EOF

  Kiosk (only if this box drives the monitor) — see docs/NUC.md
EOF
