# Building the display box

Notes for a dedicated machine that both serves the display and drives the
monitor. Written for an Intel NUC running Debian 13 (trixie), but nothing here
is NUC-specific beyond the graphics driver package.

If the monitor is driven by something else — a Pi, a smart TV browser, a tablet
— skip straight to the [main README](../README.md); you only need the server.

## Base install

Debian 13 (trixie) netinst, with **no** desktop task selected in tasksel —
just standard system utilities and the SSH server. Grab the current image from
<https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/>; non-free firmware
is in the official images now, so the plain netinst is the one you want.

This display takes no input at all, which is why it uses `cage` on Wayland
rather than X11 with a window manager — fewer moving parts than autologin plus
startx plus openbox. If you're adapting this for a touchscreen, note that
libinput calibration is considerably easier on X11, and that may be reason
enough to go the other way.

### 1. Packages

```bash
sudo apt update && sudo apt install --no-install-recommends -y python3 curl ca-certificates
```

That's the entire dependency list. The code needs Python 3.10 or newer and
trixie ships 3.13; `ca-certificates` is only needed so the `/setup` helper can
reach plex.tv over HTTPS.

### 2. The Bijou service

```bash
git clone https://github.com/OWNER/bijou.git
cd bijou
sudo ./deploy/install.sh
```

It creates the `bijou` system user, copies the app to `/opt/bijou`, installs
the systemd unit, starts it, and prints what to do next. An existing
`/etc/bijou.env` is left alone, so re-running the script is how you update.

Bijou starts without a token so the setup helper is reachable. Open
`http://<this-box>:8080/setup` from any machine, sign in to Plex, paste the
block it gives you into `/etc/bijou.env`, then:

```bash
sudo systemctl restart bijou
```

Check it:

```bash
curl -s localhost:8080/healthz   # {"ok":true,"queue":412,"playing":false,...}
journalctl -u bijou -f
```

`ok: false` means Plex isn't answering. An empty queue means the section ID is
wrong, or everything in that library is already watched.

### 3. Kiosk

Only if this box drives the monitor.

```bash
sudo apt install --no-install-recommends -y cage chromium libgl1-mesa-dri firmware-misc-nonfree
sudo useradd -m -G video,render,input,tty kiosk
sudo cp deploy/bijou-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable --now getty@tty1
sudo systemctl enable --now bijou-kiosk
```

`cage` is a Wayland kiosk compositor — it runs exactly one fullscreen app and
nothing else. No desktop, no window manager, no display manager, no X.

Rotation is done by the page, not the compositor: the kiosk URL ends in
`?rotate=90`. Use `270` if it comes up upside down for the way your monitor is
mounted, or `180` for a landscape panel hung the wrong way up.

Cage has its own `-r` flag for this, but on Intel graphics it commonly gives a
black screen — rotated scanout needs a plane configuration the driver won't
always provide, and the commit succeeds while displaying nothing. Rotating in
the page sidesteps that entirely. If you want to use `-r` anyway, drop the
`?rotate` parameter and try `WLR_DRM_NO_ATOMIC=1`, `WLR_DRM_NO_MODIFIERS=1`, or
`WLR_RENDERER=pixman` as an `Environment=` line, one at a time.

Give it a DHCP reservation so the URL never moves.

If the screen stays black, `journalctl -u bijou-kiosk -b` usually says why.
The two usual causes are the `kiosk` user missing from the `video` or `render`
group, and a missing DRI driver for the NUC's integrated graphics.

### 4. Optional: no internet at all

The page pulls two webfonts from Google. To cut that, follow
`app/static/fonts/README.txt` — drop two woff2 files in and uncomment a block
in `index.html`. Without them the display falls back to condensed system fonts,
which is less handsome but works.

---


## Ports

Bijou listens on 8080 and nothing else. If you're colocating other services on
this box, that's the only port to route around.

## Troubleshooting the kiosk

`deploy/kiosk-debug.sh` runs the whole stack once, verbosely, and prints why it
stopped. Run it with sudo over SSH.

**Restart loop with `Deactivated successfully`.** Cage is exiting cleanly, not
crashing. Three usual causes: `getty@tty1` fighting for the VT (the shipped
unit has `Conflicts=getty@tty1.service`, so check it applied); no connected
output at startup; or chromium unable to write its profile directory.

**Check what the kernel sees:**

```bash
for c in /sys/class/drm/card*-*; do echo "$(basename "$c"): $(cat "$c/status")"; done
```

If everything says `disconnected`, it's a cable, adapter or EDID problem, not
software.

**Black screen only when rotated.** Use the page's `?rotate=90` rather than
cage's `-r`. Intel graphics frequently fail rotated scanout — the atomic commit
succeeds and displays nothing. The shipped unit already does it this way.

**Black screen generally.** `journalctl -u bijou-kiosk -b`. Usually the
`kiosk` user missing from `video` or `render`, or a missing DRI driver.
