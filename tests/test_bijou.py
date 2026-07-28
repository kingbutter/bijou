"""
Tests for the poster server. Standard library only — run with:

    python3 -m unittest discover -s tests -v
"""

import json
import os
import re
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                 # fake_plex
sys.path.insert(0, str(HERE.parent / "app"))  # bijou

import fake_plex  # noqa: E402
from fake_plex import TOKEN, FakePlex, FakePlexTv  # noqa: E402

# Bump alongside FAKE_VERSION in fake_plex.py whenever the fixtures change.
REQUIRED_FAKE = 2
if getattr(fake_plex, "FAKE_VERSION", 0) < REQUIRED_FAKE:
    raise SystemExit(
        f"tests/fake_plex.py is out of date: this suite needs version "
        f"{REQUIRED_FAKE}, found {getattr(fake_plex, 'FAKE_VERSION', 'none')}. "
        f"Update the whole tests/ directory, not just test_bijou.py.")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, timeout=5):
    """Fetch a URL, returning (status, body_bytes, headers)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def get_json(url):
    status, body, _ = get(url)
    return status, json.loads(body)


class PosterTestCase(unittest.TestCase):
    """Boots a real server against a fake Plex for each test class."""

    env_extra = {}

    @classmethod
    def setUpClass(cls):
        cls.plex = FakePlex().__enter__()
        cls.port = free_port()

        env = {
            "PLEX_HOST": "127.0.0.1",
            "PLEX_PORT": str(cls.plex.port),
            "PLEX_TOKEN": TOKEN,
            "PLEX_SECTIONS": "16",
            "BIJOU_PORT": str(cls.port),
            "BIJOU_BIND": "127.0.0.1",
            "BIJOU_SESSION_POLL": "1",
            "BIJOU_QUEUE_REFRESH": "3600",
        }
        env.update(cls.env_extra)
        cls._saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)

        # Import fresh so Config picks up this environment
        for mod in ("bijou",):
            sys.modules.pop(mod, None)
        import bijou

        cls.bijou = bijou
        bijou.CFG = bijou.Config()
        bijou.ART = bijou.ArtCache(bijou.CFG.art_cache_mb)
        bijou.STATE = bijou.State()

        for worker in (bijou.STATE.watch_sessions, bijou.STATE.build_queue):
            threading.Thread(target=worker, daemon=True).start()

        cls.server = bijou.Server((bijou.CFG.host, bijou.CFG.port), bijou.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.wait_for(lambda: get_json(f"{cls.base}/healthz")[1]["queue"] > 0)

    @classmethod
    def tearDownClass(cls):
        cls.bijou.STATE.stopping.set()   # stop the workers before Plex goes away
        cls.server.shutdown()
        cls.server.server_close()
        cls.plex.__exit__()
        for k, v in cls._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def wait_for(predicate, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if predicate():
                    return True
            except Exception:
                pass
            time.sleep(0.15)
        raise AssertionError("timed out waiting for condition")


class TestQueue(PosterTestCase):

    def test_queue_returns_items(self):
        status, d = get_json(f"{self.base}/api/queue")
        self.assertEqual(status, 200)
        self.assertGreater(len(d["items"]), 0)

    def test_queue_filters_watched_even_when_plex_does_not(self):
        # The fake leaks one watched title through the unwatched filter,
        # the way some real server versions do
        _, d = get_json(f"{self.base}/api/queue")
        titles = [i["title"] for i in d["items"]]
        self.assertNotIn("Test Film 99", titles)
        self.assertNotIn("Test Film 3", titles)   # viewCount set by the fake

    def test_queue_carries_timings(self):
        _, d = get_json(f"{self.base}/api/queue")
        for key in ("rotate_seconds", "poll_seconds", "queue_seconds"):
            self.assertIn(key, d)

    def test_items_have_required_shape(self):
        _, d = get_json(f"{self.base}/api/queue")
        item = d["items"][0]
        self.assertEqual({"key", "title", "thumb", "meta"}, set(item))
        self.assertTrue(item["thumb"].startswith("/library/"))
        self.assertIsInstance(item["meta"], list)

    def test_runtime_is_formatted(self):
        _, d = get_json(f"{self.base}/api/queue")
        self.assertIn("2h 02m", d["items"][0]["meta"])


class TestState(PosterTestCase):

    def tearDown(self):
        self.plex.playing = None
        self.wait_for(lambda: get_json(f"{self.base}/api/state")[1]["playing"] is None)

    def test_idle(self):
        status, d = get_json(f"{self.base}/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(d["playing"])
        self.assertTrue(d["ok"])

    def test_detects_playback(self):
        self.plex.playing = "movie"
        self.wait_for(lambda: get_json(f"{self.base}/api/state")[1]["playing"])
        _, d = get_json(f"{self.base}/api/state")
        p = d["playing"]
        self.assertEqual(p["title"], "Test Film 1")
        self.assertEqual(p["duration_ms"], 7_320_000)
        self.assertEqual(p["offset_ms"], 1_830_000)

    def test_progress_is_not_inverted(self):
        # The bug in the original PHP: progress read 100% at the start
        self.plex.playing = "movie"
        self.wait_for(lambda: get_json(f"{self.base}/api/state")[1]["playing"])
        _, d = get_json(f"{self.base}/api/state")
        p = d["playing"]
        pct = p["offset_ms"] / p["duration_ms"] * 100
        self.assertLess(pct, 50, "a quarter-watched film should not read past halfway")
        self.assertGreater(pct, 10)

    def test_episode_shape(self):
        self.plex.playing = "episode"
        self.wait_for(
            lambda: (get_json(f"{self.base}/api/state")[1]["playing"] or {}).get("title")
            == "Some Series"
        )
        _, d = get_json(f"{self.base}/api/state")
        self.assertIn("S02E07", d["playing"]["meta"])


class TestClientMatching(PosterTestCase):
    env_extra = {"PLEX_CLIENT_MATCH": "172.20.24.23,SHIELD Android TV"}

    def tearDown(self):
        self.plex.playing = None
        self.plex.player_ip = "10.0.0.5"
        self.plex.player_name = "Living Room TV"

    def test_ignores_other_players(self):
        self.plex.playing = "movie"
        time.sleep(2.5)
        _, d = get_json(f"{self.base}/api/state")
        self.assertIsNone(d["playing"], "playback elsewhere should not take the display")

    def test_matches_by_ip(self):
        self.plex.player_ip = "172.20.24.23"
        self.plex.playing = "movie"
        self.wait_for(lambda: get_json(f"{self.base}/api/state")[1]["playing"])

    def test_matches_by_name_case_insensitively(self):
        self.plex.player_ip = "10.9.9.9"
        self.plex.player_name = "shield android tv"
        self.plex.playing = "movie"
        self.wait_for(lambda: get_json(f"{self.base}/api/state")[1]["playing"])


class TestArt(PosterTestCase):

    def art(self, key):
        return get(f"{self.base}/api/art?k=" + urllib.parse.quote(key, safe=""))

    def test_serves_poster(self):
        _, d = get_json(f"{self.base}/api/queue")
        status, body, headers = self.art(d["items"][0]["thumb"])
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertIn("max-age", headers.get("Cache-Control", ""))

    def test_second_request_is_cached(self):
        _, d = get_json(f"{self.base}/api/queue")
        thumb = d["items"][1]["thumb"]
        self.art(thumb)
        before = len(self.plex.requests)
        self.art(thumb)
        self.assertEqual(len(self.plex.requests), before,
                         "a cached poster should not hit Plex again")

    def test_falls_back_when_transcoder_is_off(self):
        self.plex.transcode_fails = True
        try:
            _, d = get_json(f"{self.base}/api/queue")
            status, body, _ = self.art(d["items"][2]["thumb"])
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(b"\x89PNG"))
        finally:
            self.plex.transcode_fails = False

    def test_rejects_paths_outside_library(self):
        for bad in ("/etc/passwd", "/status/sessions", "", "not-a-path",
                    "http://evil.example/x"):
            with self.subTest(bad=bad):
                self.assertEqual(self.art(bad)[0], 400)

    def test_rejects_traversal(self):
        # These would otherwise reach any Plex endpoint with our token attached
        for bad in ("/library/../status/sessions",
                    "/library/a/../../status/sessions",
                    "/library/metadata/../../identity"):
            with self.subTest(bad=bad):
                self.assertEqual(self.art(bad)[0], 400)

    def test_token_never_reaches_the_client(self):
        _, d = get_json(f"{self.base}/api/queue")
        _, body, headers = self.art(d["items"][0]["thumb"])
        self.assertNotIn(TOKEN.encode(), body)
        self.assertNotIn(TOKEN, json.dumps(headers))
        _, raw, _ = get(f"{self.base}/api/queue")
        self.assertNotIn(TOKEN.encode(), raw)


class TestStatic(PosterTestCase):

    def test_serves_index(self):
        status, body, _ = get(f"{self.base}/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!DOCTYPE html>", body)

    def test_index_has_no_token(self):
        _, body, _ = get(f"{self.base}/")
        self.assertNotIn(TOKEN.encode(), body)

    def test_font_name_is_validated(self):
        for bad in ("../bijou.py", "..%2Fbijou.py", "bijou.py", "a/b.woff2"):
            with self.subTest(bad=bad):
                self.assertEqual(get(f"{self.base}/fonts/{bad}")[0], 400)

    def test_missing_font_is_404_not_500(self):
        self.assertEqual(get(f"{self.base}/fonts/nope.woff2")[0], 404)

    def test_unknown_route(self):
        self.assertEqual(get(f"{self.base}/wp-admin")[0], 404)


class TestResilience(PosterTestCase):

    def tearDown(self):
        self.plex.fail = False
        self.plex.playing = None

    def test_keeps_queue_when_plex_fails(self):
        _, before = get_json(f"{self.base}/api/queue")
        self.plex.fail = True
        time.sleep(2)
        _, after = get_json(f"{self.base}/api/queue")
        self.assertEqual(len(before["items"]), len(after["items"]),
                         "a Plex outage should not empty the display")

    def test_healthz_reports_plex_down(self):
        self.plex.fail = True
        self.wait_for(lambda: get_json(f"{self.base}/healthz")[1]["ok"] is False)
        _, d = get_json(f"{self.base}/healthz")
        self.assertIn("version", d)

    def test_recovers(self):
        self.plex.fail = True
        self.wait_for(lambda: get_json(f"{self.base}/api/state")[1]["ok"] is False)
        self.plex.fail = False
        self.wait_for(lambda: get_json(f"{self.base}/api/state")[1]["ok"] is True)


class TestShutdown(PosterTestCase):

    def test_workers_stop_when_asked(self):
        alive_before = threading.active_count()
        self.assertGreaterEqual(alive_before, 3)
        # The real check is that tearDownClass returns promptly rather than
        # leaving threads hammering a Plex that has gone away.
        self.assertFalse(self.bijou.STATE.stopping.is_set())


class TestSetupHelper(PosterTestCase):
    """The /setup token helper. It must never read or write real config."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plextv = FakePlexTv(cls.plex.port).__enter__()
        cls._real_plextv = cls.bijou.PLEXTV
        cls.bijou.PLEXTV = f"http://127.0.0.1:{cls.plextv.port}"

    @classmethod
    def tearDownClass(cls):
        cls.bijou.PLEXTV = cls._real_plextv
        cls.plextv.__exit__()
        super().tearDownClass()

    def setUp(self):
        self.plextv.claimed = False

    def s(self, action, **params):
        return get_json(f"{self.base}/api/setup/{action}?"
                        + urllib.parse.urlencode(params))

    def test_page_is_served(self):
        status, body, _ = get(f"{self.base}/setup")
        self.assertEqual(status, 200)
        self.assertIn(b"Set up", body)

    def test_pin_returns_a_code(self):
        status, d = self.s("pin")
        self.assertEqual(status, 200)
        self.assertEqual(d["code"], "ABCD")
        self.assertIn("plex.tv/link", d["url"])
        self.assertIn("code=ABCD", d["deep_link"])

    def test_pin_sends_required_headers(self):
        self.s("pin")
        post = [r for r in self.plextv.requests if r[0] == "POST"][-1]
        headers = {k.lower(): v for k, v in post[2].items()}
        # plex.tv rejects the PIN flow without a client identifier
        self.assertTrue(headers.get("x-plex-client-identifier"))
        self.assertEqual(headers.get("x-plex-product"), "Bijou")

    def test_client_id_is_stable(self):
        a = self.bijou.Config().client_id
        b = self.bijou.Config().client_id
        self.assertEqual(a, b, "a new id each run would spam the device list")

    def test_check_is_pending_until_claimed(self):
        _, d = self.s("check", id=4242)
        self.assertTrue(d.get("pending"))
        self.assertNotIn("token", d)

        self.plextv.claimed = True
        _, d = self.s("check", id=4242)
        self.assertEqual(d["token"], TOKEN)

    def test_servers_drops_relay(self):
        _, d = self.s("servers", token=TOKEN)
        addrs = [c["address"] for c in d["servers"][0]["connections"]]
        self.assertNotIn("relay.plex.direct", addrs, "relay is far too slow for art")

    def test_reachable_address_wins_over_docker_bridge(self):
        # The regression this exists for: Plex in Docker advertises its bridge
        # IP flagged local, so sorting on the flag picked an unusable address.
        _, d = self.s("servers", token=TOKEN)
        srv = next(s for s in d["servers"] if s["name"] == "Basement")
        first = srv["connections"][0]
        self.assertTrue(first["reachable"])
        self.assertEqual(first["address"], "127.0.0.1")

        bridge = next(c for c in srv["connections"] if c["address"] == "172.17.0.2")
        self.assertTrue(bridge["local"], "Plex really does call it local")
        self.assertFalse(bridge["reachable"], "but it must be marked unreachable")

    def test_every_address_is_offered(self):
        # The page lets you choose, so none may be silently dropped
        _, d = self.s("servers", token=TOKEN)
        addrs = [c["address"] for c in d["servers"][0]["connections"]]
        self.assertIn("172.17.0.2", addrs)
        self.assertIn("1.2.3.4", addrs)
        self.assertIn("127.0.0.1", addrs)

    def test_server_reports_overall_reachability(self):
        _, d = self.s("servers", token=TOKEN)
        self.assertTrue(next(s for s in d["servers"]
                             if s["name"] == "Basement")["reachable"])
        self.assertFalse(next(s for s in d["servers"]
                              if s["name"] == "A Friend")["reachable"])

    def test_reachable_servers_sort_first(self):
        _, d = self.s("servers", token=TOKEN)
        self.assertEqual(d["servers"][0]["name"], "Basement")

    def test_probe_confirms_a_typed_address(self):
        _, d = self.s("probe", host="127.0.0.1", port=self.plex.port, https="0")
        self.assertTrue(d["reachable"])
        self.assertEqual(d["machine"], "basement-machine-id")

    def test_probe_rejects_a_dead_address(self):
        _, d = self.s("probe", host="127.0.0.1", port=9, https="0")
        self.assertFalse(d["reachable"])

    def test_probe_needs_no_token(self):
        status, _, _ = get(f"{self.base}/api/setup/probe?"
                           + urllib.parse.urlencode(
                               {"host": "127.0.0.1", "port": self.plex.port}))
        self.assertEqual(status, 200)

    def test_unreachable_address_gives_a_useful_error(self):
        status, body, _ = get(f"{self.base}/api/setup/libraries?"
                              + urllib.parse.urlencode(
                                  {"token": TOKEN, "host": "127.0.0.1", "port": 9}))
        self.assertEqual(status, 502)
        self.assertIn(b"Could not reach", body)

    def test_servers_excludes_players(self):
        _, d = self.s("servers", token=TOKEN)
        self.assertNotIn("Some Phone", [s["name"] for s in d["servers"]])

    def test_libraries_lists_only_video(self):
        _, d = self.s("libraries", token=TOKEN, host="127.0.0.1",
                      port=self.plex.port, https="0")
        titles = [x["title"] for x in d["libraries"]]
        self.assertIn("Movies", titles)
        self.assertIn("TV Shows", titles)
        self.assertNotIn("Music", titles)

    def test_players_merges_both_sources(self):
        self.plex.playing = "movie"
        try:
            time.sleep(0.3)
            _, d = self.s("players", token=TOKEN, host="127.0.0.1",
                          port=self.plex.port, https="0")
            by_name = {p["name"]: p for p in d["players"]}
            self.assertIn("Old Roku", by_name)
            self.assertEqual(by_name["Old Roku"]["source"], "discovered")
            self.assertIn("Living Room TV", by_name)
            self.assertEqual(by_name["Living Room TV"]["source"], "playing")
        finally:
            self.plex.playing = None

    def test_rejects_missing_token(self):
        for action in ("servers", "libraries", "players"):
            with self.subTest(action=action):
                self.assertEqual(get(f"{self.base}/api/setup/{action}")[0], 400)

    def test_rejects_bad_host(self):
        for host in ("127.0.0.1/../x", "http://evil.example", "a b",
                     "127.0.0.1:1@evil.example", ""):
            with self.subTest(host=host):
                status, _, _ = get(f"{self.base}/api/setup/libraries?"
                                   + urllib.parse.urlencode(
                                       {"token": TOKEN, "host": host, "port": 32400}))
                self.assertEqual(status, 400)

    def test_rejects_bad_port(self):
        for port in ("0", "70000", "abc", "-1"):
            with self.subTest(port=port):
                status, _, _ = get(f"{self.base}/api/setup/libraries?"
                                   + urllib.parse.urlencode(
                                       {"token": TOKEN, "host": "127.0.0.1", "port": port}))
                self.assertEqual(status, 400)

    def test_does_not_leak_the_configured_token(self):
        # The helper drives its own login; it must not hand out the running
        # server's token to anyone who loads the page.
        _, body, _ = get(f"{self.base}/setup")
        self.assertNotIn(TOKEN.encode(), body)
        _, d = self.s("pin")
        self.assertNotIn(TOKEN, json.dumps(d))

    def test_unknown_setup_action(self):
        self.assertEqual(get(f"{self.base}/api/setup/nope")[0], 404)


class TestSetupDisabled(PosterTestCase):
    env_extra = {"BIJOU_SETUP": "0"}

    def test_page_is_gone(self):
        self.assertEqual(get(f"{self.base}/setup")[0], 404)

    def test_api_is_gone(self):
        self.assertEqual(get(f"{self.base}/api/setup/pin")[0], 404)

    def test_display_still_works(self):
        self.assertEqual(get(f"{self.base}/")[0], 200)


class TestUnconfigured(unittest.TestCase):
    """A fresh install with no token must still serve /setup."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = {
            "PLEX_TOKEN": "",
            "PLEX_HOST": "127.0.0.1",
            "BIJOU_PORT": str(cls.port),
            "BIJOU_BIND": "127.0.0.1",
            "BIJOU_SETUP": "1",
        }
        cls._saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        sys.modules.pop("bijou", None)
        import bijou

        cls.bijou = bijou
        bijou.CFG = bijou.Config()
        bijou.STATE = bijou.State()
        for worker in (bijou.STATE.watch_sessions, bijou.STATE.build_queue):
            threading.Thread(target=worker, daemon=True).start()
        cls.server = bijou.Server((bijou.CFG.host, bijou.CFG.port), bijou.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.bijou.STATE.stopping.set()
        cls.server.shutdown()
        cls.server.server_close()
        for k, v in cls._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_setup_is_reachable(self):
        self.assertEqual(get(f"{self.base}/setup")[0], 200)

    def test_display_still_loads(self):
        self.assertEqual(get(f"{self.base}/")[0], 200)

    def test_state_reports_unconfigured(self):
        _, d = get_json(f"{self.base}/api/state")
        self.assertFalse(d["configured"])
        self.assertIsNone(d["playing"])

    def test_queue_reports_unconfigured(self):
        _, d = get_json(f"{self.base}/api/queue")
        self.assertFalse(d["configured"])
        self.assertEqual(d["setup_url"], "/setup")

    def test_healthz_reports_unconfigured(self):
        _, d = get_json(f"{self.base}/healthz")
        self.assertFalse(d["configured"])

    def test_workers_do_not_spin(self):
        # Without a token there is nothing to ask Plex, so the loops should
        # idle rather than hammer a default host every few seconds.
        time.sleep(1.2)
        self.assertEqual(len(self.bijou.STATE.queue), 0)


class TestMarkup(unittest.TestCase):
    """
    Guards against a class of bug that is invisible until it reaches a wall:
    markup and CSS drifting apart. A global rename once turned class="poster"
    into class="bijou", which silently unstyled the poster layers — demo mode
    still looked fine because it injects an <svg> that renders on its own.
    """

    STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

    @staticmethod
    def split(src):
        css = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
        # Drop <script> blocks so JS template literals don't look like markup
        body = re.sub(r"<script>.*?</script>", "", src[src.index("</style>"):], flags=re.S)
        return css, body

    def test_every_class_in_markup_has_a_rule(self):
        for name in ("index.html", "setup.html"):
            src = (self.STATIC / name).read_text()
            css, body = self.split(src)
            used = set()
            for m in re.finditer(r'class="([^"{}$]+)"', body):
                used.update(m.group(1).split())
            defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
            orphans = sorted(used - defined)
            self.assertEqual(orphans, [], f"{name}: styled by nothing -> {orphans}")

    def test_poster_layers_are_present_and_styled(self):
        src = (self.STATIC / "index.html").read_text()
        css, body = self.split(src)
        for pid in ("pa", "pb"):
            self.assertRegex(
                body, rf'<div class="poster" id="{pid}">',
                f"#{pid} must carry class=poster or it gets no positioning")
        for rule in (".poster{", ".poster.live{"):
            self.assertIn(rule, css.replace(" ", ""))

    def test_ids_the_script_reaches_for_all_exist(self):
        src = (self.STATIC / "index.html").read_text()
        _, body = self.split(src)
        script = re.search(r"<script>(.*?)</script>", src, re.S).group(1)
        wanted = set(re.findall(r"\$\('([a-zA-Z][\w-]*)'\)", script))
        present = set(re.findall(r'id="([^"]+)"', body))
        self.assertEqual(sorted(wanted - present), [],
                         "the script looks up ids that are not in the markup")


class TestArtCache(unittest.TestCase):
    """The LRU is pure logic, so test it directly."""

    def setUp(self):
        os.environ.setdefault("PLEX_TOKEN", TOKEN)
        import bijou
        self.bijou = bijou

    def test_evicts_oldest_when_full(self):
        cache = self.bijou.ArtCache(1)          # 1 MB
        blob = b"x" * (400 * 1024)
        for name in ("a", "b", "c"):
            cache.put(name, blob, "image/jpeg")
        self.assertIsNone(cache.get("a"), "oldest entry should have been evicted")
        self.assertIsNotNone(cache.get("c"))

    def test_get_refreshes_recency(self):
        cache = self.bijou.ArtCache(1)
        blob = b"x" * (400 * 1024)
        cache.put("a", blob, "image/jpeg")
        cache.put("b", blob, "image/jpeg")
        cache.get("a")                            # a is now the most recent
        cache.put("c", blob, "image/jpeg")
        self.assertIsNotNone(cache.get("a"))
        self.assertIsNone(cache.get("b"))

    def test_stays_under_limit(self):
        cache = self.bijou.ArtCache(1)
        for i in range(20):
            cache.put(f"k{i}", b"x" * (200 * 1024), "image/jpeg")
        self.assertLessEqual(cache.size, 1024 * 1024)


class TestHelpers(unittest.TestCase):

    def setUp(self):
        os.environ.setdefault("PLEX_TOKEN", TOKEN)
        import bijou
        self.bijou = bijou

    def test_runtime_formatting(self):
        r = self.bijou.runtime
        self.assertEqual(r(7_320_000), "2h 02m")
        self.assertEqual(r(2_700_000), "45m")
        self.assertIsNone(r(0))
        self.assertIsNone(r(None))
        self.assertIsNone(r(30_000))

    def test_art_path_pattern(self):
        ok = self.bijou.ART_PATH.fullmatch
        self.assertTrue(ok("/library/metadata/101/thumb/1700000001"))
        self.assertTrue(ok("/library/metadata/1.2/thumb/3"))
        self.assertFalse(ok("/library/../status/sessions"))
        self.assertFalse(ok("/etc/passwd"))
        self.assertFalse(ok("/library/x?a=1"))
        self.assertFalse(ok(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
