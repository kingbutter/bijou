"""
A stand-in Plex server, just real enough to test against.

Bumped whenever the tests need a new endpoint or fixture. FAKE_VERSION is
checked by the suite, so a stale copy of this file reports itself rather than
producing a scatter of unrelated assertion failures.
"""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Bumped whenever the fixtures change; the suite refuses to run against an
# older copy rather than producing a scatter of unrelated failures.
FAKE_VERSION = 2

TOKEN = "test-token"

# A one-pixel PNG, so the art proxy has something real to hand back
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def movie(i, watched=False):
    m = {
        "ratingKey": str(100 + i),
        "type": "movie",
        "title": f"Test Film {i}",
        "year": 2000 + i,
        "contentRating": "PG-13",
        "duration": 7_320_000,
        "thumb": f"/library/metadata/{100 + i}/thumb/17000000{i}",
    }
    if watched:
        m["viewCount"] = 1
    return m


def episode():
    return {
        "ratingKey": "900",
        "type": "episode",
        "title": "The One With The Test",
        "grandparentTitle": "Some Series",
        "grandparentThumb": "/library/metadata/900/thumb/1700",
        "parentIndex": 2,
        "index": 7,
        "duration": 1_500_000,
    }


class FakePlexTv:
    """Stands in for plex.tv/api/v2 during the setup-helper tests."""

    def __init__(self, pms_port):
        self.claimed = False        # flip to simulate the user approving the PIN
        self.pms_port = pms_port
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                outer.requests.append(("POST", self.path,
                                       dict(self.headers)))
                if self.path.startswith("/pins"):
                    return self._json({"id": 4242, "code": "ABCD", "authToken": None})
                self.send_error(404)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                outer.requests.append(("GET", u.path, dict(self.headers)))

                if u.path.startswith("/pins/"):
                    return self._json({
                        "id": 4242, "code": "ABCD",
                        "authToken": TOKEN if outer.claimed else None,
                    })

                if u.path == "/resources":
                    if q.get("X-Plex-Token") != [TOKEN]:
                        return self.send_error(401)
                    return self._json([
                        {"name": "Basement", "provides": "server", "owned": True,
                         "clientIdentifier": "basement-machine-id",
                         "connections": [
                             # Plex in Docker advertises its bridge IP and
                             # flags it local, but nothing else can reach it.
                             {"protocol": "http", "address": "172.17.0.2",
                              "port": 32400, "local": True, "relay": False},
                             {"protocol": "https", "address": "1.2.3.4",
                              "port": 32400, "local": False, "relay": False},
                             {"protocol": "http", "address": "127.0.0.1",
                              "port": outer.pms_port, "local": True, "relay": False},
                             {"protocol": "https", "address": "relay.plex.direct",
                              "port": 443, "local": False, "relay": True},
                         ]},
                        {"name": "A Friend", "provides": "server", "owned": False,
                         "connections": [{"protocol": "http", "address": "10.0.0.9",
                                          "port": 32400, "local": True, "relay": False}]},
                        {"name": "Some Phone", "provides": "player", "owned": True,
                         "connections": []},
                    ])
                self.send_error(404)

        self._handler = Handler

    def __enter__(self):
        ThreadingHTTPServer.daemon_threads = True
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()


class FakePlex:
    """Context manager that serves a fake Plex on an ephemeral port."""

    def __init__(self):
        self.playing = None        # None | "movie" | "episode"
        self.player_ip = "10.0.0.5"
        self.player_name = "Living Room TV"
        self.requests = []
        self.fail = False          # when True, every route 500s
        self.transcode_fails = False

        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, container):
                body = json.dumps({"MediaContainer": container}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                outer.requests.append((u.path, q))

                if u.path == "/identity":
                    return self._json({"machineIdentifier": "basement-machine-id",
                                       "version": "1.41.0"})

                if q.get("X-Plex-Token") != [TOKEN]:
                    return self.send_error(401, "no token")
                if outer.fail:
                    return self.send_error(500, "boom")

                if u.path == "/status/sessions":
                    if not outer.playing:
                        return self._json({"size": 0})
                    s = episode() if outer.playing == "episode" else movie(1)
                    s = dict(s)
                    s["viewOffset"] = 1_830_000
                    s["Player"] = {"address": outer.player_ip,
                                   "title": outer.player_name}
                    return self._json({"size": 1, "Metadata": [s]})

                if u.path.startswith("/library/sections/") and u.path.endswith("/all"):
                    items = [movie(i, watched=(i % 3 == 0)) for i in range(1, 10)]
                    if q.get("unwatched") == ["1"]:
                        # Deliberately leak one watched item through, the way
                        # some real server versions do
                        items = [m for m in items if not m.get("viewCount")]
                        items.append(movie(99, watched=True))
                    return self._json({"size": len(items), "Metadata": items})

                if u.path == "/library/sections":
                    return self._json({"size": 3, "Directory": [
                        {"key": "16", "title": "Movies", "type": "movie"},
                        {"key": "2", "title": "TV Shows", "type": "show"},
                        {"key": "9", "title": "Music", "type": "artist"},
                    ]})

                if u.path == "/clients":
                    return self._json({"size": 1, "Server": [
                        {"name": "Old Roku", "host": "10.0.0.44"},
                    ]})

                if u.path == "/photo/:/transcode":
                    if outer.transcode_fails:
                        return self.send_error(500, "no transcoder")
                    return self._png()

                if u.path.startswith("/library/metadata/"):
                    return self._png()

                self.send_error(404)

            def _png(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(PNG)))
                self.end_headers()
                self.wfile.write(PNG)

        self._handler = Handler

    def __enter__(self):
        ThreadingHTTPServer.daemon_threads = True
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
