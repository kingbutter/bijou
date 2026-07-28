#!/usr/bin/env bash
# Run the kiosk stack once, verbosely, and print why it stopped.
# Safe to run over SSH — it uses the real VT via systemd-run.
set -uo pipefail

URL="${1:-http://127.0.0.1:8080/}"
UNIT=kiosk-test

echo "== DRM connectors =="
for c in /sys/class/drm/card*-*; do
  [ -e "$c/status" ] && echo "  $(basename "$c"): $(cat "$c/status")"
done

echo
echo "== groups for kiosk =="
id kiosk 2>/dev/null || echo "  no kiosk user!"

echo
echo "== stopping the real kiosk unit =="
systemctl stop bijou-kiosk.service 2>/dev/null
systemctl stop "$UNIT.service" 2>/dev/null
systemctl reset-failed "$UNIT.service" 2>/dev/null

echo
echo "== is the poster server actually up? =="
curl -s --max-time 3 "${URL%/}/healthz" || echo "  no answer from $URL"

echo
echo "== launching cage with debug logging =="
systemd-run --unit="$UNIT" --collect \
  --property=PAMName=login \
  --property=TTYPath=/dev/tty1 \
  --property=TTYReset=yes \
  --property=TTYVHangup=yes \
  --property=StandardInput=tty-fail \
  --property=StandardOutput=journal \
  --property=StandardError=journal \
  --property=Conflicts=getty@tty1.service \
  --uid=kiosk \
  /usr/bin/cage -d -- /usr/bin/chromium --kiosk --ozone-platform=wayland \
    --user-data-dir=/home/kiosk/.chromium-kiosk --no-first-run "$URL" >/dev/null

sleep 8
echo
echo "== output =="
journalctl -u "$UNIT" -b --no-pager -o cat | tail -60
echo
echo "== result =="
systemctl show "$UNIT" -p Result -p ExecMainStatus 2>/dev/null
systemctl stop "$UNIT.service" 2>/dev/null
systemctl reset-failed "$UNIT.service" 2>/dev/null
