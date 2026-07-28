#!/usr/bin/env python3
"""
Bijou — an illuminated Plex poster display.

Serves the display page and talks to Plex. Python standard library only:
no pip, no venv, no web server in front of it.

Configuration comes from the environment. See .env.example.
"""

__version__ = "1.0.0"

import concurrent.futures
import json
import os
import random
import re
import signal
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = Path(os.environ.get("BIJOU_STATIC") or (HERE / "static"))


def env(name, default=""):
    return os.environ.get(name, default).strip()


def env_int(name, default):
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def env_list(name):
    return [p.strip() for p in env(name).split(",") if p.strip()]


class Config:
    def __init__(self):
        self.host = env("BIJOU_BIND", "0.0.0.0")
        self.port = env_int("BIJOU_PORT", 8080)

        scheme = "https" if env("PLEX_HTTPS", "0") == "1" else "http"
        self.plex = f"{scheme}://{env('PLEX_HOST', '127.0.0.1')}:{env_int('PLEX_PORT', 32400)}"
        self.token = env("PLEX_TOKEN")
        self.sections = env_list("PLEX_SECTIONS") or ["1"]

        # A session counts as "the theater" if its player IP or name contains
        # one of these. Empty means any playback on the server counts.
        self.clients = env_list("PLEX_CLIENT_MATCH")

        self.selection = env("BIJOU_SELECTION", "unwatched")  # unwatched|all|recent
        self.recent_take = env_int("BIJOU_RECENT_TAKE", 40)

        self.rotate = env_int("BIJOU_ROTATE_SECONDS", 30)
        self.session_poll = env_int("BIJOU_SESSION_POLL", 3)
        self.queue_refresh = env_int("BIJOU_QUEUE_REFRESH", 1800)
        self.width = env_int("BIJOU_POSTER_WIDTH", 900)
        self.art_cache_mb = env_int("BIJOU_ART_CACHE_MB", 96)

        # The token helper at /setup. It stores nothing — it walks the Plex
        # PIN login and shows you a config block to paste. Set 0 to remove it.
        self.setup = env("BIJOU_SETUP", "1") != "0"

        # plex.tv wants a stable per-install identifier. Deriving it from the
        # hostname keeps repeated logins from filling your device list with
        # one-off entries.
        seed = f"bijou-{socket.gethostname()}"
        self.client_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


CFG = Config()

# How long a "Now Showing" state survives Plex being unreachable
STALE_AFTER = 60


# ── Plex ──────────────────────────────────────────────────────────────

def plex_get(path, params=None, raw=False, timeout=12):
    """GET from Plex. Returns the MediaContainer dict, or (bytes, type) if raw."""
    q = dict(params or {})
    q["X-Plex-Token"] = CFG.token
    url = f"{CFG.plex}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={
        "Accept": "*/*" if raw else "application/json",
        "User-Agent": "bijou",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if raw:
            return r.read(), r.headers.get("Content-Type", "image/jpeg")
        return json.loads(r.read().decode("utf-8")).get("MediaContainer", {})


def runtime(ms):
    if not ms or ms < 60_000:
        return None
    minutes = round(ms / 60_000)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def shape(m):
    """Reduce a Plex metadata blob to what the display actually draws."""
    if m.get("type") == "episode":
        title = m.get("grandparentTitle") or m.get("title", "")
        thumb = m.get("grandparentThumb") or m.get("thumb", "")
        meta = []
        if m.get("parentIndex") is not None and m.get("index") is not None:
            meta.append(f"S{int(m['parentIndex']):02d}E{int(m['index']):02d}")
        if m.get("title"):
            meta.append(m["title"])
    else:
        title = m.get("title", "")
        thumb = m.get("thumb", "")
        meta = [str(x) for x in (m.get("year"), m.get("contentRating"),
                                 runtime(m.get("duration"))) if x]
    return {
        "key": str(m.get("ratingKey") or abs(hash(title + thumb))),
        "title": title,
        "thumb": thumb,
        "meta": meta,
    }


def is_theater(session):
    if not CFG.clients:
        return True
    player = session.get("Player", {})
    haystack = f"{player.get('address', '')} {player.get('title', '')}".lower()
    return any(c.lower() in haystack for c in CFG.clients)


# ── plex.tv, for the token helper at /setup ───────────────────────────
#
# Nothing here is persisted. The helper walks the PIN login, reads back your
# servers and libraries, and hands you a config block to paste. The token
# passes through to your browser and is never written to disk or logged.

PLEXTV = "https://plex.tv/api/v2"


def plextv(path, params=None, token=None, method="GET"):
    q = dict(params or {})
    if token:
        q["X-Plex-Token"] = token
    url = f"{PLEXTV}{path}"
    if q:
        url += "?" + urllib.parse.urlencode(q)

    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    for k, v in {
        "Accept": "application/json",
        "X-Plex-Product": "Bijou",
        "X-Plex-Version": __version__,
        "X-Plex-Client-Identifier": CFG.client_id,
        "X-Plex-Device": "Bijou Poster Case",
        "X-Plex-Device-Name": socket.gethostname(),
        "X-Plex-Platform": "Python",
    }.items():
        req.add_header(k, v)

    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


def setup_pin():
    """Start a login. Returns the code the person types at plex.tv/link."""
    d = plextv("/pins", {"strong": "true"}, method="POST")
    return {
        "id": d.get("id"),
        "code": d.get("code"),
        "url": "https://plex.tv/link",
        # Pre-fills the code so it's one click from a phone
        "deep_link": (
            "https://app.plex.tv/auth#?" + urllib.parse.urlencode({
                "clientID": CFG.client_id,
                "code": d.get("code", ""),
                "context[device][product]": "Bijou",
            })
        ),
    }


def setup_check(pin_id):
    """Has the PIN been claimed yet?"""
    d = plextv(f"/pins/{int(pin_id)}")
    token = d.get("authToken")
    return {"token": token} if token else {"pending": True}


def probe(base, timeout=2.5):
    """
    Can we actually reach this address? /identity needs no token and returns
    the server's machine ID, which also confirms we found the right server.

    Plex advertises whatever addresses the server can see on itself. When it
    runs in Docker that includes the container's bridge IP, which is flagged
    "local" but is unreachable from anywhere else — so trusting the flag
    instead of testing is how you end up pointed at 172.17.0.2.
    """
    try:
        req = urllib.request.Request(f"{base}/identity",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            c = json.loads(r.read().decode("utf-8")).get("MediaContainer", {})
        return c.get("machineIdentifier") or "unknown"
    except Exception:
        return None


def probe_all(conns):
    """Test every candidate at once rather than serially."""
    if not conns:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(conns))) as pool:
        futures = {pool.submit(probe, c["url"]): c for c in conns}
        for f in concurrent.futures.as_completed(futures, timeout=12):
            c = futures[f]
            try:
                c["machine"] = f.result()
            except Exception:
                c["machine"] = None
            c["reachable"] = c["machine"] is not None


def setup_servers(token):
    """
    Every Plex Media Server this account knows about, with each advertised
    address tested from this machine so the caller can pick one that works.
    """
    out, flat = [], []
    for res in plextv("/resources", {"includeHttps": 1, "includeRelay": 0}, token=token):
        if "server" not in (res.get("provides") or ""):
            continue
        conns = []
        for c in res.get("connections") or []:
            if c.get("relay") or not c.get("address"):
                continue
            https = c.get("protocol") == "https"
            conn = {
                "address": c.get("address"),
                "port": c.get("port") or 32400,
                "https": https,
                "local": bool(c.get("local")),
                "reachable": None,
                "machine": None,
            }
            conn["url"] = f"{'https' if https else 'http'}://{conn['address']}:{conn['port']}"
            conns.append(conn)
            flat.append(conn)
        if conns:
            out.append({
                "name": res.get("name"),
                "owned": bool(res.get("owned")),
                "id": res.get("clientIdentifier"),
                "connections": conns,
            })

    probe_all(flat)

    for srv in out:
        # Reachable first, then LAN, then plain http (cheaper for poster art)
        srv["connections"].sort(
            key=lambda c: (not c["reachable"], not c["local"], c["https"]))
        srv["reachable"] = any(c["reachable"] for c in srv["connections"])
        for c in srv["connections"]:
            c.pop("url", None)

    out.sort(key=lambda s: (not s["reachable"], not s["owned"], s["name"] or ""))
    return {"servers": out}


def setup_probe(base):
    """Test one address the caller typed in by hand."""
    machine = probe(base)
    return {"reachable": machine is not None, "machine": machine}


def setup_libraries(base, token):
    """Movie and show libraries on the chosen server."""
    req = urllib.request.Request(
        f"{base}/library/sections?" + urllib.parse.urlencode({"X-Plex-Token": token}),
        headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        c = json.loads(r.read().decode("utf-8")).get("MediaContainer", {})
    return {"libraries": [
        {"key": d.get("key"), "title": d.get("title"), "type": d.get("type")}
        for d in c.get("Directory", []) if d.get("type") in ("movie", "show")
    ]}


def setup_players(base, token):
    """
    Candidate theater players.

    /clients only lists players that announced themselves over GDM and accept
    remote control, so it is frequently empty. Anything currently streaming is
    a far better signal, so both are merged and the source is labelled.
    """
    seen, out = set(), []

    def add(name, address, source):
        key = (name or "", address or "")
        if key in seen or not (name or address):
            return
        seen.add(key)
        out.append({"name": name, "address": address, "source": source})

    for path, extract in (
        ("/clients", lambda c: [(d.get("name"), d.get("host") or d.get("address"))
                                for d in c.get("Server", [])]),
        ("/status/sessions", lambda c: [((m.get("Player") or {}).get("title"),
                                         (m.get("Player") or {}).get("address"))
                                        for m in c.get("Metadata", [])]),
    ):
        try:
            req = urllib.request.Request(
                f"{base}{path}?" + urllib.parse.urlencode({"X-Plex-Token": token}),
                headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                c = json.loads(r.read().decode("utf-8")).get("MediaContainer", {})
            for name, address in extract(c):
                add(name, address, "playing" if "sessions" in path else "discovered")
        except Exception as e:
            log(f"setup players {path}: {e}")

    return {"players": out}


# ── Live state, kept fresh by background threads ──────────────────────

class State:
    """What the display needs, refreshed on a timer rather than on request."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.playing = None
        self.queue = []
        self.plex_ok = False
        self.last_ok = 0.0
        self.queue_built = 0.0

    def snapshot_state(self):
        with self.lock:
            return {
                "playing": self.playing,
                "ok": self.plex_ok,
                "configured": bool(CFG.token),
            }

    def snapshot_queue(self):
        with self.lock:
            items = list(self.queue)
        return {
            "items": items,
            "configured": bool(CFG.token),
            "setup_url": "/setup" if CFG.setup else None,
            "rotate_seconds": CFG.rotate,
            "poll_seconds": max(2, CFG.session_poll),
            "queue_seconds": CFG.queue_refresh,
        }

    # -- workers --

    def watch_sessions(self):
        while not self.stopping.is_set():
            if not CFG.token:
                self.stopping.wait(5)
                continue
            try:
                container = plex_get("/status/sessions", timeout=8)
                found = None
                for s in container.get("Metadata", []):
                    if not is_theater(s):
                        continue
                    found = shape(s)
                    found["duration_ms"] = int(s.get("duration") or 0)
                    found["offset_ms"] = int(s.get("viewOffset") or 0)
                    break
                with self.lock:
                    self.playing, self.plex_ok = found, True
                    self.last_ok = time.time()
            except Exception as e:
                with self.lock:
                    self.plex_ok = False
                    # Keep the poster up through a blip, but don't leave
                    # "Now Showing" on screen for a film that ended an hour ago.
                    if time.time() - self.last_ok > STALE_AFTER:
                        self.playing = None
                log(f"sessions: {e}")
            self.stopping.wait(CFG.session_poll)

    def build_queue(self):
        while not self.stopping.is_set():
            if not CFG.token:
                self.stopping.wait(5)
                continue
            items = []
            try:
                for section in CFG.sections:
                    params = {"type": 1}
                    if CFG.selection == "unwatched":
                        params["unwatched"] = 1
                    elif CFG.selection == "recent":
                        params["sort"] = "addedAt:desc"
                    c = plex_get(f"/library/sections/{section}/all", params, timeout=30)
                    for m in c.get("Metadata", []):
                        # Some server versions ignore ?unwatched=1
                        if CFG.selection == "unwatched" and int(m.get("viewCount") or 0):
                            continue
                        if not m.get("thumb"):
                            continue
                        items.append(shape(m))

                if CFG.selection == "recent":
                    items = items[:CFG.recent_take]

                if items:
                    random.shuffle(items)
                    with self.lock:
                        self.queue = items
                        self.queue_built = time.time()
                    log(f"queue: {len(items)} titles")
                else:
                    log("queue: Plex returned nothing; keeping the previous list")
            except Exception as e:
                log(f"queue: {e}")

            # Retry sooner if we have never managed to build one
            self.stopping.wait(CFG.queue_refresh if self.queue else 30)


STATE = State()


# ── Artwork cache ─────────────────────────────────────────────────────

class ArtCache:
    """Posters, resized by Plex, held in memory so a kiosk restart is instant."""

    def __init__(self, limit_mb):
        self.limit = limit_mb * 1024 * 1024
        self.lock = threading.Lock()
        self.items = {}   # key -> (bytes, content_type)
        self.order = []
        self.size = 0

    def get(self, key):
        with self.lock:
            hit = self.items.get(key)
            if hit:
                self.order.remove(key)
                self.order.append(key)
            return hit

    def put(self, key, body, ctype):
        with self.lock:
            if key in self.items:
                return
            self.items[key] = (body, ctype)
            self.order.append(key)
            self.size += len(body)
            while self.size > self.limit and len(self.order) > 1:
                old = self.order.pop(0)
                self.size -= len(self.items.pop(old)[0])


ART = ArtCache(CFG.art_cache_mb)


def fetch_art(thumb):
    """Ask Plex to resize the poster; fall back to the original if it won't."""
    hit = ART.get(thumb)
    if hit:
        return hit

    source = f"{CFG.plex}{thumb}?X-Plex-Token={urllib.parse.quote(CFG.token)}"
    try:
        body, ctype = plex_get("/photo/:/transcode", {
            "width": CFG.width,
            "height": int(CFG.width * 1.5),
            "minSize": 1,
            "upscale": 1,
            "url": source,
        }, raw=True, timeout=20)
    except Exception:
        body, ctype = plex_get(thumb, raw=True, timeout=20)

    ART.put(thumb, body, ctype)
    return body, ctype


# ── HTTP ──────────────────────────────────────────────────────────────

def log(msg):
    print(f"[bijou] {msg}", flush=True)


SAFE_FILES = {"/": "index.html", "/index.html": "index.html"}

# Artwork requests are forwarded to Plex with our token attached, so the path
# has to be pinned to library art. Note that "." is legal in Plex thumb paths
# but ".." would let a caller climb out to any endpoint on the server.
ART_PATH = re.compile(r"/library/(?!.*\.\.)[A-Za-z0-9/._-]+")
FONT_NAME = re.compile(r"[A-Za-z0-9._-]+\.woff2")
# Hostnames and bare IPs only — no scheme, path, port or credentials smuggled in
HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]{0,251}[A-Za-z0-9])?")


class Handler(BaseHTTPRequestHandler):
    server_version = "PosterCase"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # the kiosk polls constantly; don't fill the journal with it

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        route = url.path
        try:
            if route in SAFE_FILES:
                self.send_file(STATIC / SAFE_FILES[route], "text/html; charset=utf-8")
            elif route == "/api/state":
                self.send_json(STATE.snapshot_state())
            elif route == "/api/queue":
                self.send_json(STATE.snapshot_queue())
            elif route == "/api/art":
                self.send_art(urllib.parse.parse_qs(url.query).get("k", [""])[0])
            elif route == "/setup" or route == "/setup/":
                if not CFG.setup:
                    return self.send_error(404, "Setup helper is disabled")
                self.send_file(STATIC / "setup.html", "text/html; charset=utf-8")
            elif route.startswith("/api/setup/"):
                self.setup_api(route[len("/api/setup/"):],
                               urllib.parse.parse_qs(url.query))
            elif route.startswith("/fonts/"):
                self.send_font(route[len("/fonts/"):])
            elif route == "/healthz":
                s = STATE.snapshot_state()
                self.send_json({
                    "ok": s["ok"],
                    "configured": bool(CFG.token),
                    "queue": len(STATE.queue),
                    "playing": bool(s["playing"]),
                    "version": __version__,
                })
            else:
                self.send_error(404, "Not found")
        except (BrokenPipeError, ConnectionResetError):
            pass  # kiosk browser went away mid-response

    # -- responses --

    def send_bytes(self, body, ctype, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj):
        self.send_bytes(json.dumps(obj).encode("utf-8"), "application/json")

    def send_file(self, path, ctype):
        if not path.is_file():
            return self.send_error(404, "Not found")
        self.send_bytes(path.read_bytes(), ctype)

    def setup_api(self, action, q):
        """
        The token helper. Reads nothing from the running config and writes
        nothing anywhere — each caller drives their own login.
        """
        if not CFG.setup:
            return self.send_error(404, "Setup helper is disabled")

        if action not in ("pin", "check", "servers", "probe", "libraries", "players"):
            return self.send_error(404, "Not found")

        def one(k):
            return (q.get(k) or [""])[0]

        try:
            if action == "pin":
                return self.send_json(setup_pin())
            if action == "check":
                return self.send_json(setup_check(one("id")))

            if action == "probe":
                base = self.setup_base(one("host"), one("port"), one("https"))
                if base is None:
                    return self.send_error(400, "Bad server address")
                return self.send_json(setup_probe(base))

            token = one("token")
            if not token:
                return self.send_error(400, "Missing token")
            if action == "servers":
                return self.send_json(setup_servers(token))

            base = self.setup_base(one("host"), one("port"), one("https"))
            if base is None:
                return self.send_error(400, "Bad server address")
            if action == "libraries":
                return self.send_json(setup_libraries(base, token))
            return self.send_json(setup_players(base, token))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return self.send_error(502, "Plex rejected the token")
            return self.send_error(502, f"Plex said {e.code}")
        except ValueError:
            return self.send_error(400, "Bad parameter")
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            log(f"setup {action}: {e}")
            # By far the most common cause: an address Plex advertised that
            # isn't reachable from here, e.g. a Docker bridge IP.
            return self.send_error(
                502, "Could not reach that address from this machine. "
                     "Pick a different one, or enter your server's LAN address.")
        except Exception as e:
            log(f"setup {action}: {e}")
            return self.send_error(502, "Something went wrong talking to Plex")

    @staticmethod
    def setup_base(host, port, https):
        """Build a server URL from caller input without trusting it blindly."""
        if not HOSTNAME.fullmatch(host or ""):
            return None
        try:
            p = int(port or 32400)
        except ValueError:
            return None
        if not 1 <= p <= 65535:
            return None
        return f"{'https' if https == '1' else 'http'}://{host}:{p}"

    def send_font(self, name):
        """Optional self-hosted webfonts, so the display needs no internet."""
        if not FONT_NAME.fullmatch(name or ""):
            return self.send_error(400, "Bad font name")
        path = STATIC / "fonts" / name
        if not path.is_file():
            return self.send_error(404, "Not found")
        self.send_bytes(path.read_bytes(), "font/woff2",
                        cache="public, max-age=31536000, immutable")

    def send_art(self, thumb):
        if not ART_PATH.fullmatch(thumb or ""):
            return self.send_error(400, "Bad artwork path")
        try:
            body, ctype = fetch_art(thumb)
        except Exception as e:
            log(f"art {thumb}: {e}")
            return self.send_error(502, "No artwork")
        self.send_bytes(body, ctype, cache="public, max-age=604800")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET


def main():
    if not CFG.token and not CFG.setup:
        raise SystemExit(
            "PLEX_TOKEN is not set and BIJOU_SETUP=0, so there is nothing to do. "
            "Set a token (see .env.example) or enable the /setup helper.")

    for worker in (STATE.watch_sessions, STATE.build_queue):
        threading.Thread(target=worker, daemon=True).start()

    server = Server((CFG.host, CFG.port), Handler)

    def shutdown(signum, _frame):
        # Docker and systemd both send SIGTERM; without this the container
        # takes the full 10s grace period to stop every time.
        log(f"signal {signum}, shutting down")
        STATE.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log(f"version {__version__}")
    if CFG.token:
        log(f"serving http://{CFG.host}:{CFG.port}  ->  {CFG.plex}  "
            f"sections {','.join(CFG.sections)}")
    else:
        log(f"serving http://{CFG.host}:{CFG.port}")
        log("no PLEX_TOKEN yet — open /setup to get one, then restart")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log("stopped")


if __name__ == "__main__":
    main()
