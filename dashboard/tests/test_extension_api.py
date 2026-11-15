"""The local server the browser extension talks to (dashboard.extension_api), on a free port, asked the way a
browser asks it (http.client): who gets in and who doesn't, pairing, every route, the two local pages, the command
line, doctor's row, and that the extension's folder ships and its connections file survives an update. "The
extension" is the test: nothing here opens a browser, a clipboard or a platform's site, and the connections file,
the extension's folder and the stats database are scratch ones."""

from __future__ import annotations

import contextlib
import http.client
import inspect
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dashboard import config, db, desktop, extension_api, posting
from dashboard.config import PLATFORM_KEYS
from dashboard.service import Dashboard
from dashboard.tests.test_posting import CAPTION, CHROME, EDGE, TAGS, LedgerTest, approve
from dashboard.tests.test_posting_tab import NAMES, PostingTest
from dashboard.tests.test_publish import PublishTest, Reader
from dashboard.tests.window import Window
from studio import doctor, update

KIT = Path(__file__).resolve().parent.parent.parent
EXTENSION = f"chrome-extension://{extension_api.EXTENSION_ID}"
# The public key in the extension's manifest.json: it is what makes Chrome give the extension the id ReelForge lets in.
KEY = ("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu3aM0botKJ6u+h+aFFxRSpLYS+ii4U+wVpj+XuC/XIIhV1fAGk2dBO"
       "5gdYSrEG0vdKaV+4juGSP2oy9dfBgi2zM7ts/soellah6seKSUAMGSA5vxXfHswTmxv8y3UoSj3GqS469tcaM+dsnWkU6zugTe"
       "3yrmpic6okTtjp5ldf0frXUpHkoy9fohXoIoewu806gO/e4j5tZSwI1+vPLaRV+VZFw1WOeCKNiZ8jElqDVLoKU2yANRg8Gsbu"
       "LLuesq73peZTWxNsmC+ewiqu3YDH3B/tOWvZjaKwgLotsTqSxCZ54Yg4ehlhsLO2sVMuXhKqz4T4NKMw7wWl2nnpUI8wIDAQAB")
TIKTOK_URL = "https://www.tiktok.com/@me/video/7300000000000000001"


class Answer:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status, self.headers, self.body = status, headers, body

    @property
    def json(self) -> dict:
        return json.loads(self.body.decode("utf-8"))

    @property
    def error(self) -> str:
        return self.json.get("error", "")


def ask(port: int, method: str, path: str, body=None, *, token: str | None = None, origin: str | None = None,
        host: str | None = "", headers: dict | None = None, raw: bytes | None = None, kind: str | None = "",
        length: int | None = None) -> Answer:
    """One request, as a browser would send it. host: "" for the right one, None for none at all. raw: the body's
    bytes as they are (body: an object, sent as JSON). kind: the Content-Type ("": JSON when there is a body).
    length: a Content-Length to claim, whatever is sent."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host or f"127.0.0.1:{port}")
        if token is not None:
            conn.putheader(extension_api.TOKEN_HEADER, token)
        if origin is not None:
            conn.putheader("Origin", origin)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        data = raw if raw is not None else json.dumps(body).encode("utf-8") if body is not None else None
        if data is not None or length is not None:
            if kind == "":
                kind = "application/json"
            if kind:
                conn.putheader("Content-Type", kind)
            conn.putheader("Content-Length", str(len(data or b"") if length is None else length))
        conn.endheaders(data)
        response = conn.getresponse()
        return Answer(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
    finally:
        conn.close()


class App(extension_api.App):
    """ReelForge without its window, on the test's channel, counting how often it is told something changed."""

    def __init__(self, cfg: dict, every: dict):
        super().__init__(cfg, every)
        self.changes = 0

    def profile(self) -> dict:
        return {"key": ".", "name": "Night Physics"}

    def changed(self) -> None:
        self.changes += 1


class ApiTest(LedgerTest):
    """The server listening on a free port for a channel with one finished video and four platforms connected. The
    extension's folder and its connections file are in the scratch folder; the browser, the clipboard and the file
    manager are stand-ins."""

    def setUp(self) -> None:
        super().setUp()
        self.store = self.root / "data" / "extension.json"
        self.folder = self.root / "extension"
        self.folder.mkdir()
        (self.folder / "manifest.json").write_text(json.dumps({"manifest_version": 3, "key": KEY}), "utf-8")
        (self.folder / "selectors.json").write_text(json.dumps({"version": 7, "platforms": {}}), "utf-8")
        self.copied: list[str] = []
        self.opened: list[str] = []
        self.revealed: list[Path] = []
        self.browser = True  # whether a browser opens
        for target, name, stand_in in (
                (extension_api, "store_path", lambda: self.store), (extension_api, "folder", lambda: self.folder),
                (desktop, "copy", lambda text: self.copied.append(text) or True),
                (desktop, "reveal", lambda path: self.revealed.append(Path(path))),
                (desktop, "open_url", lambda url: self.opened.append(url) or self.browser)):
            patch = mock.patch.object(target, name, stand_in)
            patch.start()
            self.addCleanup(patch.stop)
        self.app = App(self.cfg, {key: Reader(key, name) for key, name in NAMES.items()})
        self.server = extension_api.Server(self.app, ports=(0,))
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)
        self.port = self.server.port

    def ask(self, method: str, path: str, body=None, **kw) -> Answer:
        return ask(self.port, method, path, body, **kw)

    def secret(self, url: str) -> str:
        """The pair page, opened: the secret it carries for the extension."""
        page = self.ask("GET", url.split(str(self.port), 1)[1])
        self.assertEqual(page.status, 200, page.body)
        return re.search(r'data-rf-secret="([^"]*)"', page.body.decode()).group(1)

    def claim(self, secret: str, install: str = CHROME, **more) -> Answer:
        return self.ask("POST", "/v1/pair/claim", {"secret": secret, "install": install, "name": "Chrome on this PC",
                                                   "extension_version": "0.1.0", "protocol": 1, **more},
                        origin=EXTENSION)

    def pair(self, install: str = CHROME) -> str:
        """A browser connects, the way the extension does it: its token."""
        answer = self.claim(self.secret(self.server.pair_url()), install)
        self.assertEqual(answer.status, 200, answer.body)
        return answer.json["token"]

    def paired(self) -> list[dict]:
        return json.loads(self.store.read_text("utf-8"))["paired"]

    def later(self, seconds: float):
        """As if this much time had passed for the server."""
        real = time.monotonic
        patch = mock.patch.object(extension_api, "clock", lambda: real() + seconds)
        patch.start()
        self.addCleanup(patch.stop)


class Rules(ApiTest):
    """What every request has to get past before any route is looked at."""

    def test_hello_needs_no_token_and_says_who_is_here(self):
        hello = self.ask("GET", "/v1/hello")
        self.assertEqual(hello.status, 200)
        self.assertEqual(hello.json, {"app": "reelforge", "protocol": 1, "version": extension_api.KIT_VERSION,
                                      "app_id": json.loads(self.store.read_text("utf-8"))["app_id"],
                                      "profile": {"key": ".", "name": "Night Physics"}, "demo": False,
                                      "paired": False})
        self.assertRegex(hello.json["app_id"], r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
        self.assertEqual(hello.headers["cache-control"], "no-store")
        self.assertEqual(hello.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("access-control-allow-origin", hello.headers)
        token = self.pair()
        self.assertTrue(self.ask("GET", "/v1/hello", token=token).json["paired"])
        self.assertFalse(self.ask("GET", "/v1/hello", token=token[:-1] + "x").json["paired"])
        self.assertFalse(self.ask("GET", "/v1/hello", token="").json["paired"])

    def test_only_its_own_name_and_port_is_served(self):
        for host in (f"localhost:{self.port}", f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}",
                     f"127.0.0.1.evil.example:{self.port}", f"[::1]:{self.port}", f"127.0.0.1:{self.port}.", None):
            with self.subTest(host=host):
                answer = self.ask("GET", "/v1/hello", host=host)
                self.assertEqual((answer.status, answer.error), (421, "wrong_host"))
        twice = self.ask("GET", "/v1/hello", headers={"Host": "evil.example"})  # after the right one
        self.assertEqual(twice.status, 421)

    def test_a_web_page_is_refused_whatever_it_asks_for(self):
        token = self.pair()
        for origin in ("https://evil.example", "null", f"http://localhost:{self.port}", "http://127.0.0.1",
                       f"https://127.0.0.1:{self.port}", "chrome-extension://" + "a" * 32,
                       EXTENSION + ".evil.example", "https://reelforge.tools"):
            with self.subTest(origin=origin):
                for answer in (self.ask("GET", "/v1/hello", origin=origin),
                               self.ask("GET", "/v1/state", origin=origin, token=token),
                               self.ask("POST", "/v1/seen", {"protocol": 1}, origin=origin, token=token),
                               self.ask("POST", "/v1/pair/claim", {"secret": "x"}, origin=origin)):
                    self.assertEqual((answer.status, answer.error), (403, "wrong_origin"))
                    self.assertNotIn("access-control-allow-origin", answer.headers)
        two = self.ask("GET", "/v1/hello", origin=EXTENSION, headers={"Origin": "https://evil.example"})
        self.assertEqual(two.status, 403)

    def test_a_request_a_browser_marks_as_coming_from_another_site_is_refused(self):
        # (a page's <img> or a no-cors fetch carries no Origin at all, but browsers say where it came from)
        for site in ("cross-site", "same-site"):
            answer = self.ask("GET", "/v1/hello", headers={"Sec-Fetch-Site": site})
            self.assertEqual((answer.status, answer.error), (403, "wrong_origin"))
        for site in ("none", "same-origin"):  # the extension's worker, and ReelForge's own pages
            self.assertEqual(self.ask("GET", "/v1/hello", headers={"Sec-Fetch-Site": site}).status, 200)

    def test_the_extension_and_the_local_pages_are_let_in_by_name(self):
        ours = self.ask("GET", "/v1/hello", origin=EXTENSION)
        self.assertEqual(ours.status, 200)
        self.assertEqual((ours.headers["access-control-allow-origin"], ours.headers["vary"]), (EXTENSION, "Origin"))
        own = self.ask("GET", "/v1/hello", origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(own.status, 200)
        self.assertNotIn("access-control-allow-origin", own.headers)  # the same origin needs no leave
        # A copy from a store has another id: it is let in once its id is in the connections file.
        other = "chrome-extension://" + "b" * 32
        self.assertEqual(self.ask("GET", "/v1/hello", origin=other).status, 403)
        extension_api.change_store(lambda store: store["extension_ids"].extend(["b" * 32, "not an id", 7]))
        listed = self.ask("GET", "/v1/hello", origin=other)
        self.assertEqual((listed.status, listed.headers["access-control-allow-origin"]), (200, other))
        self.assertEqual(self.ask("GET", "/v1/hello", origin="chrome-extension://not an id").status, 403)

    def test_a_preflight_is_answered_for_the_extension_only(self):
        ours = self.ask("OPTIONS", "/v1/seen", origin=EXTENSION,
                        headers={"Access-Control-Request-Method": "POST",
                                 "Access-Control-Request-Headers": "x-reelforge-token, content-type"})
        self.assertEqual((ours.status, ours.body), (204, b""))
        self.assertEqual(ours.headers["access-control-allow-origin"], EXTENSION)
        self.assertEqual(ours.headers["access-control-allow-headers"], "X-ReelForge-Token, Content-Type")
        self.assertEqual(ours.headers["access-control-allow-methods"], "GET, POST")
        for origin in ("https://evil.example", f"http://127.0.0.1:{self.port}", None):
            answer = self.ask("OPTIONS", "/v1/seen", origin=origin,
                              headers={"Access-Control-Request-Method": "POST"})
            self.assertEqual(answer.status, 403)
            for name in answer.headers:
                self.assertFalse(name.startswith("access-control"), name)

    def test_no_answer_ever_allows_every_origin(self):
        token = self.pair()
        for answer in (self.ask("GET", "/v1/hello"), self.ask("GET", "/v1/state", token=token, origin=EXTENSION),
                       self.ask("GET", "/assets/page.css"), self.ask("GET", "/nope"),
                       self.ask("OPTIONS", "/v1/hello", origin=EXTENSION)):
            self.assertNotEqual(answer.headers.get("access-control-allow-origin"), "*")

    def test_only_get_and_post(self):
        for method in ("PUT", "DELETE", "PATCH", "HEAD"):
            answer = self.ask(method, "/v1/hello")
            self.assertEqual(answer.status, 405, method)
        self.assertIn(self.ask("BREW", "/v1/hello").status, (400, 405, 501))

    def test_a_body_is_json_of_64_kib_at_most(self):
        token = self.pair()
        big = self.ask("POST", "/v1/seen", token=token, length=extension_api.MAX_BODY + 1)  # refused unread
        self.assertEqual((big.status, big.error), (413, "too_large"))
        sent = self.ask("POST", "/v1/seen", token=token, raw=b"x" * (extension_api.MAX_BODY + 1))  # and when it comes
        self.assertEqual(sent.status, 413)
        padded = json.dumps({"protocol": 1, "name": "x" * (extension_api.MAX_BODY - 40)}).encode()
        self.assertLessEqual(len(padded), extension_api.MAX_BODY)
        self.assertEqual(self.ask("POST", "/v1/seen", token=token, raw=padded).status, 200)
        for kind in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", None):
            answer = self.ask("POST", "/v1/seen", token=token, raw=b'{"protocol": 1}', kind=kind)
            self.assertEqual((answer.status, answer.error), (415, "not_json"), kind)
        for raw in (b"{not json", b"[1, 2]", b'"text"', b"7", b'{"progress": NaN}', b'{"a": Infinity}', b"\xff\xfe",
                    b"[" * 5000):
            answer = self.ask("POST", "/v1/seen", token=token, raw=raw)
            self.assertEqual((answer.status, answer.error), (400, "bad_json"), raw[:20])
        none = self.ask("POST", "/v1/seen", token=token, headers={"Content-Type": "application/json"})
        self.assertEqual((none.status, none.error), (411, "no_length"))
        chunked = self.ask("POST", "/v1/seen", token=token, headers={"Content-Type": "application/json",
                                                                     "Transfer-Encoding": "chunked"})
        self.assertEqual(chunked.status, 411)

    def test_there_is_nothing_anywhere_else(self):
        token = self.pair()
        for method, path in (("GET", "/"), ("GET", "/v1"), ("GET", "/v1/"), ("GET", "/v1/nope"),
                             ("GET", "/v1/hello/"), ("POST", "/v1/hello"), ("GET", "/v1/seen"), ("GET", "/index.html"),
                             ("GET", "/v1/jobs/pj_0000000000000000/nope"), ("GET", "/v1/jobs/../hello"),
                             ("GET", "/v1/jobs/x/file"), ("GET", "/post"), ("GET", "/post/"), ("GET", "/pair/"),
                             ("GET", "/favicon.ico"), ("GET", "/v1//hello"), ("GET", "/v1/hello%2f"),
                             ("GET", "http://evil.example/v1/hello"), ("POST", "/v1/batch/pb_0000000000000000")):
            with self.subTest(path=path):
                answer = self.ask(method, path, {} if method == "POST" else None, token=token)
                self.assertEqual((answer.status, answer.error), (404, "not_found"))

    def test_none_of_the_windows_own_calls_can_be_reached(self):
        token = self.pair()
        calls = sorted(name for name, fn in inspect.getmembers(Dashboard, callable) if getattr(fn, "_rpc", False))
        self.assertIn("remove_video", calls)       # (the list is the real one)
        self.assertIn("post_with_extension", calls)
        self.assertGreater(len(calls), 60)
        for name in calls:
            for method, path, body in (("GET", f"/{name}", None), ("GET", f"/v1/{name}", None),
                                       ("POST", f"/v1/{name}", {"a": []}), ("POST", f"/rpc/{name}", {"a": []}),
                                       ("POST", "/", {"id": 1, "m": name, "a": []}),
                                       ("POST", "/v1/rpc", {"id": 1, "m": name, "a": []})):
                answer = self.ask(method, path, body, token=token, origin=EXTENSION)
                self.assertEqual(answer.status, 404, (method, path))
        # Nor is there a way round: the server knows nothing of the bridge, and its routes are a fixed list.
        source = Path(extension_api.__file__).read_text("utf-8")
        self.assertNotIn("bridge import", source)
        self.assertNotIn("getattr(self.server", source)
        self.assertEqual(posting.recent(), [])

    def test_the_token_is_asked_for_everywhere_but_hello_and_the_pages(self):
        job = self.job()
        token = self.pair()
        routes = [("GET", "/v1/state", None), ("GET", f"/v1/batch/{job['batch']}", None),
                  ("GET", "/v1/selectors", None),
                  ("GET", f"/v1/jobs/{job['id']}/file", None), ("POST", "/v1/seen", {"protocol": 1}),
                  ("POST", "/v1/batches", {"video": "x", "platforms": ["tiktok"]}),
                  ("POST", f"/v1/jobs/{job['id']}/claim", {}), ("POST", f"/v1/jobs/{job['id']}/event", {}),
                  ("POST", f"/v1/jobs/{job['id']}/press", {}), ("POST", "/v1/unpair", {})]
        for method, path, body in routes:
            with self.subTest(path=path):
                for bad in (None, "", "nope", token[:-1], token + "x", token.upper(), "é"):
                    headers = {extension_api.TOKEN_HEADER: bad.encode("utf-8").decode("latin-1")} if bad else {}
                    answer = self.ask(method, path, body, headers=headers, origin=EXTENSION)
                    self.assertEqual((answer.status, answer.error), (401, "unpaired"), (path, bad))
        self.assertEqual(self.state(job), "queued")  # nothing was touched
        self.assertEqual(self.ask("GET", "/v1/state", token=token).status, 200)
        # A token in the address, or as a cookie, is not a token.
        self.assertEqual(self.ask("GET", f"/v1/state?token={token}").status, 401)
        self.assertEqual(self.ask("GET", "/v1/state", headers={"Cookie": f"token={token}"}).status, 401)

    def test_the_demo_answers_hello_and_nothing_else(self):
        token = self.pair()
        job = self.job()
        pair_page, post_page = self.server.pair_url(), self.server.post_url(job["batch"])
        self.app.demo = True
        hello = self.ask("GET", "/v1/hello", token=token)
        self.assertEqual((hello.status, hello.json["demo"], hello.json["paired"]), (200, True, False))
        for method, path, body in (("GET", "/v1/state", None), ("GET", pair_page.split(str(self.port), 1)[1], None),
                                   ("GET", post_page.split(str(self.port), 1)[1], None),
                                   ("GET", "/assets/page.css", None),
                                   ("POST", "/v1/batches", {"video": "x", "platforms": ["tiktok"]}),
                                   ("POST", f"/v1/jobs/{job['id']}/claim", {}),
                                   ("POST", "/v1/pair/claim", {"secret": "x", "protocol": 1})):
            answer = self.ask(method, path, body, token=token, origin=EXTENSION)
            self.assertEqual((answer.status, answer.error), (403, "demo"), path)
        self.assertEqual(self.state(job), "queued")

    def test_a_stuck_client_holds_nothing_up(self):
        self.server.timeout = 0.4
        stuck = socket.create_connection(("127.0.0.1", self.port))
        self.addCleanup(stuck.close)
        stuck.sendall(b"POST /v1/seen HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: 500\r\n" % self.port)  # ...
        started = time.monotonic()
        self.assertEqual(self.ask("GET", "/v1/hello").status, 200)  # the others are answered meanwhile
        self.assertLess(time.monotonic() - started, 2)
        stuck.settimeout(5)
        self.assertEqual(stuck.recv(100), b"")  # and the stuck one is let go of
        self.assertLess(time.monotonic() - started, 4)


class Pairing(ApiTest):
    def test_a_browser_connects_once_with_the_pages_secret(self):
        url = self.server.pair_url()
        self.assertRegex(url, rf"^http://127\.0\.0\.1:{self.port}/pair\?k=[\w-]{{20,}}$")
        page = self.ask("GET", url.split(str(self.port), 1)[1])
        html = page.body.decode()
        self.assertIn(f'<html lang="en" data-rf-page="pair" data-rf-protocol="1" data-rf-port="{self.port}" '
                      'data-rf-secret="', html)
        self.assertEqual(self.ask("GET", "/v1/page/pair?" + url.split("?")[1]).json["state"], "waiting")
        self.assertIsNone(self.server.paired_on(url))
        secret = self.secret(url)
        answer = self.claim(secret)
        self.assertEqual(answer.status, 200)
        token = answer.json["token"]
        self.assertRegex(token, r"^[\w-]{43,}$")
        self.assertEqual(answer.json["app_id"], self.ask("GET", "/v1/hello").json["app_id"])
        # On file: who, and the token's SHA-256. Never the token.
        (entry,) = self.paired()
        self.assertEqual({k: entry[k] for k in ("install", "name", "version", "protocol")},
                         {"install": CHROME, "name": "Chrome on this PC", "version": "0.1.0", "protocol": 1})
        self.assertRegex(entry["token_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(entry["paired_at"], entry["last_seen"])
        text = self.store.read_text("utf-8")
        self.assertNotIn(token, text)
        self.assertNotIn(secret, text)
        self.assertEqual(sorted(f.name for f in self.store.parent.iterdir()), ["extension.json", "extension.json.lock"])
        if os.name != "nt":
            self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)
        # The page hears of it, and so does whoever is waiting at the command line.
        told = self.ask("GET", "/v1/page/pair?" + url.split("?")[1]).json
        self.assertEqual((told["state"], told["name"]), ("paired", "Chrome on this PC"))
        self.assertEqual(self.server.paired_on(url), "Chrome on this PC")
        self.assertGreaterEqual(self.app.changes, 1)
        self.assertTrue(self.ask("GET", "/v1/hello", token=token).json["paired"])

    def test_the_secret_works_once(self):
        url = self.server.pair_url()
        secret = self.secret(url)
        self.assertEqual(self.claim(secret).status, 200)
        again = self.claim(secret, EDGE)
        self.assertEqual((again.status, again.error), (403, "bad_secret"))
        self.assertEqual(len(self.paired()), 1)
        self.assertEqual(self.secret(url), "")  # the page, opened again, no longer carries it
        guess = self.claim("x" * 43)
        self.assertEqual((guess.status, guess.error), (403, "bad_secret"))
        self.assertEqual(self.claim("").status, 403)

    def test_the_secret_runs_out_after_ten_minutes(self):
        secret = self.secret(self.server.pair_url())
        self.later(extension_api.SECRET_FOR + 1)
        late = self.claim(secret)
        self.assertEqual((late.status, late.error), (410, "expired"))
        self.assertFalse(self.store.exists() and self.paired())

    def test_only_the_extension_itself_can_claim(self):
        secret = self.secret(self.server.pair_url())
        body = {"secret": secret, "install": CHROME, "name": "x", "extension_version": "1", "protocol": 1}
        for origin in (None, f"http://127.0.0.1:{self.port}", "https://evil.example", "chrome-extension://" + "c" * 32):
            answer = self.ask("POST", "/v1/pair/claim", body, origin=origin)
            self.assertEqual((answer.status, answer.error), (403, "wrong_origin"), origin)
        self.assertEqual(self.claim(secret).status, 200)  # and none of those used the secret up

    def test_another_version_of_the_link_is_told_which_side_to_update(self):
        secret = self.secret(self.server.pair_url())
        newer = self.claim(secret, protocol=2)
        self.assertEqual((newer.status, newer.error), (426, "protocol"))
        self.assertIn("Update ReelForge", newer.json["message"])
        older = self.claim(secret, protocol=0)
        self.assertEqual(older.status, 426)
        self.assertIn("Reload", older.json["message"])
        self.assertEqual(self.claim(secret, protocol="1").status, 426)
        self.assertEqual(self.claim(secret).status, 200)

    def test_two_asking_at_once_with_one_secret_get_one_token(self):
        secret = self.secret(self.server.pair_url())
        gate, answers = threading.Barrier(2), []

        def claim(install: str) -> None:
            gate.wait()
            answers.append(self.claim(secret, install))

        threads = [threading.Thread(target=claim, args=(install,)) for install in (CHROME, EDGE)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(sorted(a.status for a in answers), [200, 403])
        self.assertEqual(len(self.paired()), 1)

    def test_what_a_claim_has_to_say(self):
        secret = self.secret(self.server.pair_url())
        for body in ({"secret": secret, "protocol": 1}, {"secret": secret, "protocol": 1, "install": ""},
                     {"secret": secret, "protocol": 1, "install": 7}, {"secret": 7, "protocol": 1, "install": CHROME}):
            self.assertEqual(self.ask("POST", "/v1/pair/claim", body, origin=EXTENSION).status, 400)
        name = "Chrome\x1b[31m on\n\tthis PC" + "!" * 200
        answer = self.ask("POST", "/v1/pair/claim", {"secret": secret, "protocol": 1, "install": CHROME, "name": name},
                          origin=EXTENSION)
        self.assertEqual(answer.status, 200)
        (entry,) = self.paired()
        self.assertEqual(entry["name"], ("Chrome[31m on this PC" + "!" * 200)[:80])  # one line, no control characters

    def test_connecting_again_replaces_the_token(self):
        old = self.pair()
        new = self.pair()
        self.assertNotEqual(old, new)
        self.assertEqual(len(self.paired()), 1)
        self.assertEqual(self.ask("GET", "/v1/state", token=old).status, 401)
        self.assertEqual(self.ask("GET", "/v1/state", token=new).status, 200)
        other = self.pair(EDGE)
        self.assertEqual([p["install"] for p in self.paired()], [CHROME, EDGE])
        self.assertEqual(self.ask("GET", "/v1/state", token=other).status, 200)

    def test_only_so_many_browsers_are_remembered(self):
        with mock.patch.object(extension_api, "PAIRED_MOST", 3):
            for n in range(5):
                self.pair(f"install-{n}")
        self.assertEqual([p["install"] for p in self.paired()], ["install-2", "install-3", "install-4"])

    def test_disconnecting_ends_the_token(self):
        token, other = self.pair(), self.pair(EDGE)
        # Whose connection goes is the token's word: naming another browser in the request changes nothing.
        gone = self.ask("POST", "/v1/unpair", {"install": EDGE}, token=token, origin=EXTENSION)
        self.assertEqual((gone.status, gone.json), (200, {"ok": True}))
        self.assertEqual([p["install"] for p in self.paired()], [EDGE])
        self.assertEqual(self.ask("GET", "/v1/state", token=token).status, 401)
        self.assertEqual(self.ask("GET", "/v1/state", token=other).status, 200)
        self.assertEqual(extension_api.unpair(EDGE), "Chrome on this PC")  # (what the window's button does)
        self.assertIsNone(extension_api.unpair(EDGE))
        self.assertEqual(self.ask("GET", "/v1/state", token=other).status, 401)

    def test_a_hello_from_the_extension_is_kept_with_what_it_saw(self):
        token = self.pair()
        before = self.paired()[0]["last_seen"]
        with mock.patch.object(db, "now_iso", lambda: "2030-01-01T00:00:00+00:00"):
            answer = self.ask("POST", "/v1/seen", {
                "install": "someone-else", "name": "Chrome on macOS", "extension_version": "0.2.0", "protocol": 1,
                "accounts": {"tiktok": {"signed_in": True, "handle": "@renderedwrongtv"},
                             "x": {"signed_in": "yes", "handle": 7}, "myspace": {"signed_in": True}, "youtube": "?"}},
                token=token, origin=EXTENSION)
        self.assertEqual((answer.status, answer.json), (200, {"ok": True, "selectors_version": 7}))
        (entry,) = self.paired()
        self.assertEqual((entry["install"], entry["name"], entry["version"]), (CHROME, "Chrome on macOS", "0.2.0"))
        self.assertNotEqual(entry["last_seen"], before)
        self.assertEqual(entry["accounts"], {"tiktok": {"signed_in": True, "handle": "@renderedwrongtv"},
                                             "x": {"signed_in": None, "handle": None}})
        (self.folder / "selectors.json").unlink()
        self.assertEqual(self.ask("POST", "/v1/seen", {"protocol": 1}, token=token).json["selectors_version"], 0)

    def test_an_extension_that_fell_behind_is_refused_and_shows_as_old(self):
        token = self.pair()
        answer = self.ask("POST", "/v1/seen", {"protocol": 2}, token=token, origin=EXTENSION)
        self.assertEqual((answer.status, answer.error), (426, "protocol"))
        self.assertIn("Update ReelForge", answer.json["message"])
        self.assertEqual(self.paired()[0]["protocol"], 2)  # the window reads it from here
        self.assertEqual(self.ask("POST", "/v1/seen", {}, token=token).status, 426)
        self.assertIsNone(self.paired()[0]["protocol"])
        self.assertEqual(self.ask("POST", "/v1/seen", {"protocol": 1}, token=token).status, 200)
        self.assertEqual(self.paired()[0]["protocol"], 1)

    def test_last_seen_is_not_written_at_every_request(self):
        token = self.pair()
        written = self.store.stat().st_mtime_ns
        told = self.app.changes
        for _ in range(5):
            self.assertEqual(self.ask("GET", "/v1/selectors", token=token).status, 200)
        self.assertEqual((self.store.stat().st_mtime_ns, self.app.changes), (written, told))
        self.later(extension_api.SEEN_EVERY + 1)
        with mock.patch.object(db, "now_iso", lambda: "2030-01-01T00:00:00+00:00"):
            self.ask("GET", "/v1/selectors", token=token)
        self.assertEqual(self.paired()[0]["last_seen"], "2030-01-01T00:00:00+00:00")
        self.assertEqual(self.app.changes, told + 1)


class Store(ApiTest):
    """The connections file by itself."""

    def test_it_is_made_when_the_server_starts_and_is_yours_alone(self):
        store = json.loads(self.store.read_text("utf-8"))
        self.assertEqual((store["extension_ids"], store["paired"]), ([], []))
        self.assertEqual(extension_api.app_id(), store["app_id"])  # the same one each time
        if os.name != "nt":
            self.store.chmod(0o644)
            extension_api.change_store(lambda s: s["paired"].append({"install": CHROME}))
            self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)

    def test_nothing_is_written_when_nothing_changed(self):
        before = self.store.stat().st_mtime_ns
        self.assertEqual(extension_api.change_store(lambda s: len(s["paired"])), 0)
        self.assertEqual(self.store.stat().st_mtime_ns, before)

    def test_a_file_that_is_not_json_means_nothing_is_connected_and_a_new_one_takes_its_place(self):
        token = self.pair()
        self.store.write_text("{not json", "utf-8")
        self.assertEqual(extension_api.read_store(), {})
        self.assertEqual(self.ask("GET", "/v1/state", token=token).status, 401)
        self.store.write_text("[1, 2]", "utf-8")
        self.assertEqual(extension_api.read_store(), {})
        self.pair()
        self.assertEqual(len(self.paired()), 1)
        self.store.write_text(json.dumps({"app_id": "x", "paired": ["junk", {"install": CHROME}, 7]}), "utf-8")
        self.assertIsNone(extension_api.whose(extension_api.read_store(), "anything"))
        extension_api.change_store(lambda s: None)
        self.assertEqual(self.paired(), [{"install": CHROME}])
        for junk in (7, "text", {"a": 1}, None):  # a list that isn't one, an id list that isn't one
            self.store.write_text(json.dumps({"app_id": "x", "paired": junk, "extension_ids": junk}), "utf-8")
            self.assertEqual(self.ask("GET", "/v1/state", token=token, origin=EXTENSION).status, 401)
            self.assertEqual(extension_api.paired(extension_api.read_store()), [])

    def test_a_file_that_cannot_be_read_just_now_is_never_written_over(self):
        self.pair()
        before = self.store.read_text("utf-8")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("in use")):
            with self.assertRaises(OSError):
                extension_api.change_store(lambda s: s["paired"].clear())
        self.assertEqual(self.store.read_text("utf-8"), before)

    def test_a_folder_that_cannot_be_written_means_no_server_and_no_error(self):
        blocked = self.root / "blocked"
        blocked.write_text("a file where the data folder would go", "utf-8")
        with mock.patch.object(extension_api, "store_path", lambda: blocked / "data" / "extension.json"):
            server = extension_api.Server(self.app, ports=(0,))
            self.assertFalse(server.start())
            self.assertIsNone(server.port)
            server.stop()  # nothing to stop: harmless

    def test_a_port_that_is_taken_is_passed_over(self):
        taken = self.port
        second = extension_api.Server(self.app, ports=(taken,))
        self.assertFalse(second.start())
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        third = extension_api.Server(self.app, ports=(taken, port))
        self.addCleanup(third.stop)
        self.assertTrue(third.start())
        self.assertEqual(third.port, port)
        self.assertEqual(ask(port, "GET", "/v1/hello").status, 200)
        third.stop()
        with self.assertRaises(OSError):
            ask(port, "GET", "/v1/hello")

    def test_which_ports_a_reelforge_tries(self):
        with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "", "REELFORGE_EXTENSION_PORT": ""}):
            self.assertEqual(extension_api.ports_here(), tuple(range(47821, 47831)))
            os.environ["STUDIO_DRY_SYSTEM"] = "1"  # the tests, and the screenshots: off the ports you use
            self.assertEqual(extension_api.ports_here(), ())
            os.environ["REELFORGE_EXTENSION_PORT"] = "0"
            self.assertEqual(extension_api.ports_here(), (0,))
            os.environ["REELFORGE_EXTENSION_PORT"] = "off"
            os.environ["STUDIO_DRY_SYSTEM"] = ""
            self.assertEqual(extension_api.ports_here(), ())

    def test_the_id_comes_from_the_manifests_key(self):
        self.assertEqual(extension_api.manifest_id(self.folder), extension_api.EXTENSION_ID)
        (self.folder / "manifest.json").write_text(json.dumps({"manifest_version": 3}), "utf-8")
        self.assertEqual(extension_api.manifest_id(self.folder), "")
        with self.assertRaises(OSError):
            extension_api.manifest_id(self.root / "nowhere")


class Jobs(ApiTest):
    """What the extension does with a post: sees it, takes it, fetches the video, reports, and (once) presses."""

    def setUp(self) -> None:
        super().setUp()
        self.token = self.pair()

    def get(self, path: str, **kw) -> Answer:
        return self.ask("GET", path, token=kw.pop("token", self.token), **kw)

    def post(self, path: str, body: dict | None = None, **kw) -> Answer:
        return self.ask("POST", path, body or {}, token=kw.pop("token", self.token), origin=EXTENSION, **kw)

    def event(self, job: dict, state: str, token: str | None = None, **more) -> Answer:
        return self.post(f"/v1/jobs/{job['id']}/event", {"install": CHROME, "state": state, **more},
                         token=token or self.token)

    def test_the_popup_sees_the_channel_its_platforms_and_its_videos(self):
        self.video.with_suffix(".json").write_text(json.dumps({"caption": CAPTION, "tags": TAGS}), "utf-8")
        hidden = self.root / "videos" / "hidden.mp4"
        hidden.write_bytes(b"not for the list")
        db.set_meta("hidden_videos", [str(hidden)])
        state = self.get("/v1/state").json
        self.assertEqual(state["profile"], {"key": ".", "name": "Night Physics"})
        self.assertEqual(state["platforms"], [{"key": key, "name": name, "handle": "@me", "connected": True,
                                               "via": "extension", "press_unlocked": False}
                                              for key, name in NAMES.items()])
        (video,) = state["videos"]
        self.assertEqual(video, {"id": posting.digest(self.video), "name": "ep01.mp4", "size": 13, "title": CAPTION,
                                 "review": "none", "postable": True, "why": "",
                                 "platforms": {key: {"state": "none", "job": None, "url": None} for key in NAMES}})
        self.assertEqual(state["active"], [])
        job = self.job()
        self.walk(job, "ready", "posted")
        db.add_post("youtube", "dQw4w9WgXcQ", job["episode_id"], CAPTION, "https://youtu.be/dQw4w9WgXcQ")
        queued = self.job("x")
        state = self.get("/v1/state").json
        where = state["videos"][0]["platforms"]
        self.assertEqual(where["tiktok"], {"state": "posted", "job": job["id"], "url": None})
        self.assertEqual(where["youtube"], {"state": "spotted", "job": None, "url": "https://youtu.be/dQw4w9WgXcQ"})
        self.assertEqual(where["x"], {"state": "queued", "job": queued["id"], "url": None})
        self.assertEqual(state["active"], [queued["batch"]])
        self.assertTrue(state["platforms"][0]["press_unlocked"])   # TikTok: a post made with its help is up
        self.assertFalse(state["platforms"][3]["press_unlocked"])  # never X

    def test_a_video_that_is_not_approved_says_why_it_cannot_go(self):
        ep = self.root / "episodes" / "ep02_duck"
        (ep / "final").mkdir(parents=True)
        (ep / "episode.json").write_text(json.dumps({"id": "ep02_duck", "title": CAPTION}), "utf-8")
        video = ep / "final" / "ep02_duck.mp4"
        video.write_bytes(b"rendered")
        approve(ep, video, review=None)
        shown = {v["name"]: v for v in self.get("/v1/state").json["videos"]}["ep02_duck.mp4"]
        self.assertEqual((shown["review"], shown["postable"]), ("unreviewed", False))
        self.assertIn("no review yet", shown["why"])
        made = self.post("/v1/batches", {"video": shown["id"], "platforms": ["tiktok"]})
        self.assertEqual((made.status, made.json["batch"], made.json["jobs"]), (200, None, []))
        self.assertEqual([b["reason"] for b in made.json["blocked"]], ["review"])

    def test_post_in_the_popup_makes_the_jobs_with_the_videos_saved_text(self):
        self.video.with_suffix(".json").write_text(json.dumps({"caption": CAPTION, "tags": TAGS}), "utf-8")
        sha = posting.digest(self.video)
        made = self.post("/v1/batches", {"video": sha, "platforms": ["tiktok", "youtube", "myspace"], "press": False,
                                         "install": CHROME})
        self.assertEqual(made.status, 201)
        self.assertRegex(made.json["batch"], r"^pb_[0-9a-f]{16}$")
        tiktok, youtube = made.json["jobs"]
        self.assertEqual(tiktok, {
            "id": tiktok["id"], "batch": made.json["batch"], "platform": "tiktok", "via": "extension",
            "state": "queued", "step": None, "progress": None, "detail": None, "error_code": None,
            "error_message": None, "post_id": None, "url": None, "press": False, "review": "none",
            "video": {"name": "ep01.mp4", "size": 13, "sha256": sha}, "text": {"caption": f"{CAPTION} #fyp {TAGS}"},
            "upload_url": "https://www.tiktok.com/tiktokstudio/upload", "handle": "@me",
            "updated_at": tiktok["updated_at"]})
        self.assertEqual(youtube["text"], {"title": f"{CAPTION} #shorts", "description": TAGS})
        self.assertEqual(made.json["blocked"], [])
        again = self.post("/v1/batches", {"video": sha, "platforms": ["tiktok"]})
        self.assertEqual((again.status, again.json["batch"]), (200, None))
        self.assertEqual([(b["platform"], b["reason"], b["job"]) for b in again.json["blocked"]],
                         [("tiktok", "in_progress", tiktok["id"])])
        self.assertEqual(self.get(f"/v1/batch/{made.json['batch']}").json, {"batch": made.json["batch"],
                                                                           "jobs": [tiktok, youtube]})
        missing = self.get("/v1/batch/pb_0000000000000000")
        self.assertEqual((missing.status, missing.error), (404, "no_batch"))
        for body, status, error in (({"video": "0" * 64, "platforms": ["tiktok"]}, 404, "no_video"),
                                    ({"video": sha, "platforms": []}, 400, "no_platform"),
                                    ({"video": sha, "platforms": ["myspace"]}, 400, "no_platform"),
                                    ({"video": sha}, 400, "bad_request"), ({"platforms": ["x"]}, 400, "bad_request"),
                                    ({"video": str(self.video), "platforms": ["x"]}, 404, "no_video")):  # never a path
            answer = self.post("/v1/batches", body)
            self.assertEqual((answer.status, answer.error), (status, error), body)

    def test_press_for_me_is_only_what_the_person_asked_for(self):
        sha = posting.digest(self.video)
        for press, kept in ((True, True), ("yes", False), (1, False), (None, False)):
            made = self.post("/v1/batches", {"video": sha, "platforms": ["tiktok", "x"], "press": press})
            self.assertEqual([j["press"] for j in made.json["jobs"]], [kept, False], press)  # never on X
            for j in made.json["jobs"]:
                posting.cancel(j["id"])

    def test_the_first_browser_to_claim_a_job_has_it(self):
        job = self.job()
        other = self.pair(EDGE)
        mine = self.post(f"/v1/jobs/{job['id']}/claim", {"install": EDGE})  # (the token says it is Chrome)
        self.assertEqual((mine.status, mine.json["state"], mine.json["text"]),
                         (200, "claimed", {"caption": f"{CAPTION} #fyp {TAGS}"}))
        self.assertEqual(posting.get(job["id"])["claimed_by"], CHROME)
        self.assertEqual(self.post(f"/v1/jobs/{job['id']}/claim").status, 200)  # asked again: the same answer
        theirs = self.post(f"/v1/jobs/{job['id']}/claim", token=other)
        self.assertEqual((theirs.status, theirs.error), (409, "already_claimed"))
        self.event(job, "failed", error_code="timeout")
        over = self.post(f"/v1/jobs/{job['id']}/claim", token=other)
        self.assertEqual((over.status, over.error), (409, "not_claimable"))
        unknown = self.post("/v1/jobs/pj_0000000000000000/claim")
        self.assertEqual((unknown.status, unknown.error), (404, "not_found"))
        self.assertGreaterEqual(self.app.changes, 2)  # the window was told each time

    def test_two_browsers_claiming_at_once_get_one_job(self):
        job = self.job()
        tokens = {CHROME: self.token, EDGE: self.pair(EDGE)}
        gate, answers = threading.Barrier(2), {}

        def claim(install: str) -> None:
            gate.wait()
            answers[install] = self.post(f"/v1/jobs/{job['id']}/claim", token=tokens[install])

        threads = [threading.Thread(target=claim, args=(install,)) for install in tokens]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        self.assertEqual(sorted(a.status for a in answers.values()), [200, 409])
        winner = next(install for install, a in answers.items() if a.status == 200)
        self.assertEqual(posting.get(job["id"])["claimed_by"], winner)

    def test_what_the_extension_reports_moves_the_job_along(self):
        job = self.job()
        early = self.event(job, "opening")
        self.assertEqual((early.status, early.error), (409, "not_yours"))  # not before it has claimed it
        self.post(f"/v1/jobs/{job['id']}/claim")
        answer = self.event(job, "opening", step="open_tab", strategy="tab=upload_url")
        self.assertEqual((answer.status, answer.json["state"], answer.json["step"]), (200, "opening", "open_tab"))
        up = self.event(job, "uploading", step="upload", progress=0.42, detail="12 of 28 MB")
        self.assertEqual((up.json["progress"], up.json["detail"]), (0.42, "12 of 28 MB"))
        self.assertEqual(self.event(job, "uploading", progress=7).json["progress"], 1.0)
        self.assertEqual(self.event(job, "uploading", progress=-1).json["progress"], 0.0)
        for junk in ("half", True, None, [0.5]):
            self.assertEqual(self.event(job, "uploading", progress=junk).json["progress"], 0.0)  # as it was
        back = self.event(job, "opening")
        self.assertEqual((back.status, back.error), (409, "bad_transition"))
        for state in ("posting", "claimed", "queued", "expired", "nonsense", None, 7):  # not the extension's to say
            refused = self.event(job, state)
            self.assertEqual((refused.status, refused.error), (409, "bad_transition"), state)
        other = self.event(job, "filling", token=self.pair(EDGE))
        self.assertEqual((other.status, other.error), (409, "not_yours"))
        self.assertEqual(self.event({"id": "pj_0000000000000000"}, "opening").status, 404)
        self.event(job, "filling", step="fill_caption", strategy="caption=role,post=text")
        self.event(job, "ready", step="ready")
        done = self.event(job, "posted", post_id="7300000000000000001", url=TIKTOK_URL)
        self.assertEqual((done.status, done.json["state"], done.json["post_id"], done.json["url"]),
                         (200, "posted", "7300000000000000001", TIKTOK_URL))
        with db.connect() as c:  # it is in the stats as this video's, before any read
            post = c.execute("SELECT * FROM posts WHERE platform = 'tiktok'").fetchone()
        self.assertEqual((post["post_id"], post["episode_id"], post["url"]),
                         ("7300000000000000001", job["episode_id"], TIKTOK_URL))
        self.assertEqual(self.event(job, "failed").status, 409)  # a post that is up stays up
        log = [detail for _, _, detail in self.events(job) if detail and detail.startswith("found by")]
        self.assertEqual(log, ["found by tab=upload_url", "found by caption=role,post=text"])

    def test_what_the_extension_says_is_cleaned_before_it_is_kept(self):
        job = self.job()
        self.post(f"/v1/jobs/{job['id']}/claim")
        told = self.event(job, "failed", step="find_input\x00\n", error_code="Not A Code!",
                          error_message="TikTok said:\n\x1b[2J  come back   later " + "x" * 400,
                          detail=["a list"], post_id={"no": 1}, url="javascript:alert(1)").json
        self.assertEqual((told["step"], told["error_code"], told["detail"], told["post_id"], told["url"]),
                         ("find_input", None, None, None, None))
        self.assertEqual(told["error_message"], ("TikTok said: [2J come back later " + "x" * 400)[:300])

    def test_a_posts_link_is_kept_only_when_it_is_on_that_platforms_own_site(self):
        for url, kept in ((TIKTOK_URL, True), ("https://tiktok.com/@me/video/1", True),
                          ("http://www.tiktok.com/@me/video/1", False), ("https://tiktok.com.evil.example/x", False),
                          ("https://evil.example/www.tiktok.com", False),
                          ("https://www.tiktok.com@evil.example/", False),
                          ("file:///etc/passwd", False), ("https://youtu.be/dQw4w9WgXcQ", False), ("", False),
                          ("https://[::1", False)):
            self.assertEqual(extension_api.post_url("tiktok", url) is not None, kept, url)
        short = "https://youtu.be/dQw4w9WgXcQ"
        self.assertEqual(extension_api.post_url("youtube", short), short)
        self.assertIsNone(extension_api.post_url("myspace", "https://myspace.com/x"))

    def test_the_one_press_of_post(self):
        first = self.job()
        self.post(f"/v1/jobs/{first['id']}/claim")
        self.event(first, "ready")
        refused = self.post(f"/v1/jobs/{first['id']}/press", {"install": CHROME})
        self.assertEqual((refused.status, refused.json["ok"], refused.json["error"]), (409, False, "press_not_allowed"))
        self.assertIn("Press Post yourself", refused.json["message"])
        self.event(first, "posted", url=TIKTOK_URL)  # the first one there is always pressed by the person
        video = self.root / "videos" / "ep02.mp4"
        video.write_bytes(b"the second video")
        job = self.job(video=video, press=True)
        self.post(f"/v1/jobs/{job['id']}/claim")
        early = self.post(f"/v1/jobs/{job['id']}/press")
        self.assertEqual((early.status, early.json["error"]), (409, "not_ready"))
        self.event(job, "ready")
        thief = self.post(f"/v1/jobs/{job['id']}/press", token=self.pair(EDGE))
        self.assertEqual((thief.status, thief.json["error"]), (409, "not_yours"))
        granted = self.post(f"/v1/jobs/{job['id']}/press")
        self.assertEqual((granted.status, granted.json), (200, {"ok": True}))
        self.assertEqual(self.state(job), "posting")
        again = self.post(f"/v1/jobs/{job['id']}/press")
        self.assertEqual((again.status, again.json["error"]), (409, "already_pressed"))
        self.assertEqual(self.post("/v1/jobs/pj_0000000000000000/press").status, 404)
        x = self.job("x", video=video, press=True)
        self.post(f"/v1/jobs/{x['id']}/claim")
        self.event(x, "ready")
        self.assertEqual(self.post(f"/v1/jobs/{x['id']}/press").json["error"], "press_not_allowed")  # never on X

    def test_the_selectors_are_served_as_data(self):
        self.assertEqual(self.get("/v1/selectors").json, {"version": 7, "platforms": {}})
        (self.folder / "selectors.json").write_text("[1]", "utf-8")
        self.assertEqual(self.get("/v1/selectors").status, 404)
        (self.folder / "selectors.json").unlink()
        answer = self.get("/v1/selectors")
        self.assertEqual((answer.status, answer.error), (404, "no_selectors"))

    def test_a_busy_database_is_a_calm_answer(self):
        import sqlite3

        with mock.patch.object(posting, "active_batches", side_effect=sqlite3.OperationalError("database is locked")):
            answer = self.get("/v1/state")
        self.assertEqual((answer.status, answer.error), (503, "busy"))

    def test_something_going_wrong_inside_is_an_answer_too_and_says_nothing_of_what(self):
        with mock.patch.object(extension_api, "state", side_effect=RuntimeError("secret/path/to/somewhere")), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            answer = self.get("/v1/state")
        self.assertEqual((answer.status, answer.error), (500, "internal"))
        self.assertNotIn("somewhere", answer.body.decode())
        self.assertIn("RuntimeError", log.getvalue())  # the window's log has it


class VideoFile(ApiTest):
    """GET /v1/jobs/<id>/file: the video's bytes, for the browser that claimed the job and nobody else."""

    def setUp(self) -> None:
        super().setUp()
        self.token = self.pair()
        self.bytes = bytes(range(256)) * 600  # 153,600 bytes
        self.video.write_bytes(self.bytes)
        self.made = self.job()
        self.path = f"/v1/jobs/{self.made['id']}/file"
        posting.claim(self.made["id"], CHROME)

    def get(self, range_: str | None = None, **kw) -> Answer:
        return self.ask("GET", self.path, token=kw.pop("token", self.token),
                        headers={"Range": range_} if range_ else None, **kw)

    def test_the_whole_video(self):
        answer = self.get()
        self.assertEqual((answer.status, answer.body), (200, self.bytes))
        self.assertEqual({k: answer.headers[k] for k in ("content-type", "content-length", "accept-ranges", "etag",
                                                         "cache-control")},
                         {"content-type": "video/mp4", "content-length": str(len(self.bytes)), "accept-ranges": "bytes",
                          "etag": f'"{self.made["video_sha256"]}"', "cache-control": "no-store"})
        self.assertNotIn("content-range", answer.headers)

    def test_a_part_of_it(self):
        size = len(self.bytes)
        for asked, first, last in (("bytes=0-9", 0, 9), ("bytes=100-199", 100, 199),
                                   (f"bytes={size - 5}-", size - 5, size - 1), ("bytes=-100", size - 100, size - 1),
                                   (f"bytes=1000-{size + 500}", 1000, size - 1), (f"bytes=-{size * 2}", 0, size - 1),
                                   ("bytes=0-0", 0, 0), ("bytes=65535-131072", 65535, 131072)):
            with self.subTest(range=asked):
                answer = self.get(asked)
                self.assertEqual(answer.status, 206)
                self.assertEqual(answer.body, self.bytes[first:last + 1])
                self.assertEqual(answer.headers["content-range"], f"bytes {first}-{last}/{size}")
                self.assertEqual(answer.headers["content-length"], str(last - first + 1))
                self.assertEqual(answer.headers["etag"], f'"{self.made["video_sha256"]}"')

    def test_a_part_that_is_not_there(self):
        size = len(self.bytes)
        for asked in (f"bytes={size}-", f"bytes={size + 10}-{size + 20}", "bytes=9-3", "bytes=-0"):
            with self.subTest(range=asked):
                answer = self.get(asked)
                self.assertEqual((answer.status, answer.error), (416, "range"))
                self.assertEqual(answer.headers["content-range"], f"bytes */{size}")

    def test_a_range_it_does_not_understand_gets_the_whole_video(self):
        for asked in ("bytes=0-9,20-29", "items=0-9", "bytes=a-b", "bytes=-", "0-9", "bytes=" + "9" * 30 + "-"):
            with self.subTest(range=asked):
                answer = self.get(asked)
                self.assertEqual((answer.status, len(answer.body)), (200, len(self.bytes)))

    def test_only_for_the_browser_that_claimed_it_while_it_is_under_way(self):
        other = self.get(token=self.pair(EDGE))
        self.assertEqual((other.status, other.error), (409, "not_yours"))
        self.assertEqual(self.ask("GET", self.path).status, 401)
        unclaimed = self.job("youtube")
        answer = self.ask("GET", f"/v1/jobs/{unclaimed['id']}/file", token=self.token)
        self.assertEqual((answer.status, answer.error), (409, "not_yours"))
        self.assertEqual(self.ask("GET", "/v1/jobs/pj_0000000000000000/file", token=self.token).status, 404)
        posting.advance(self.made["id"], "ready", install=CHROME)
        self.assertEqual(self.get("bytes=0-9").status, 206)
        for state in ("posted", "failed", "cancelled", "unknown"):
            with self.subTest(state=state):
                with db.connect() as c:
                    c.execute("UPDATE post_jobs SET state = ? WHERE id = ?", (state, self.made["id"]))
                over = self.get()
                self.assertEqual((over.status, over.error), (409, "ended"))

    def test_a_video_replaced_after_the_post_was_started_is_refused(self):
        stamp = self.video.stat()
        os.utime(self.video, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 5_000_000_000))  # touched: the same video
        self.assertEqual(self.get("bytes=0-9").status, 206)
        self.video.write_bytes(self.bytes[::-1])  # another cut of the same length
        changed = self.get("bytes=0-9")
        self.assertEqual((changed.status, changed.error), (409, "changed"))
        self.assertIn("ep01.mp4 was replaced or removed", changed.json["message"])
        self.assertNotIn(str(self.root), changed.body.decode())  # the name, never where it is
        self.video.write_bytes(self.bytes + b"more")
        self.assertEqual(self.get().error, "changed")
        self.video.unlink()
        self.assertEqual(self.get().error, "changed")
        self.video.write_bytes(self.bytes)  # the very same video again: fine
        self.assertEqual(self.get().status, 200)

    def test_the_path_is_the_jobs_never_the_requests(self):
        secret = self.root / "secret.txt"
        secret.write_text("not a video", "utf-8")
        for path in (f"{self.path}?path={secret}", f"{self.path}?file=../../secret.txt",
                     f"/v1/jobs/{self.made['id']}/file/../../../secret.txt", f"/v1/jobs/{self.made['id']}/file%2f..",
                     f"/v1/jobs/..%2f..%2fsecret.txt/file"):
            answer = self.ask("GET", path, token=self.token)
            self.assertNotIn(b"not a video", answer.body)
            self.assertIn(answer.status, (200, 404))
            if answer.status == 200:
                self.assertEqual(answer.body, self.bytes)


class Pages(ApiTest):
    """The two pages ReelForge opens in the browser, what they may ask, and their own files."""

    def setUp(self) -> None:
        super().setUp()
        made = self.batch("tiktok", "youtube")
        self.batch_id, (self.tiktok, self.youtube) = made["batch"], made["jobs"]
        self.url = self.server.post_url(self.batch_id)
        self.k = self.url.split("?k=")[1]

    def page(self) -> dict:
        answer = self.ask("GET", f"/v1/page/batch/{self.batch_id}?k={self.k}")
        self.assertEqual(answer.status, 200, answer.body)
        return answer.json

    def manual(self, platform: str = "tiktok", **kw) -> Answer:
        body = {"k": self.k, "batch": self.batch_id, "platform": platform, **kw.pop("body", {})}
        return self.ask("POST", "/v1/page/manual", body, origin=kw.pop("origin", f"http://127.0.0.1:{self.port}"), **kw)

    def test_the_post_page_carries_what_the_extension_reads(self):
        self.assertRegex(self.url, rf"^http://127\.0\.0\.1:{self.port}/post/{self.batch_id}\?k=[\w-]{{20,}}$")
        page = self.ask("GET", self.url.split(str(self.port), 1)[1])
        self.assertEqual((page.status, page.headers["content-type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(f'<html lang="en" data-rf-page="post" data-rf-protocol="1" data-rf-port="{self.port}" '
                      f'data-rf-batch="{self.batch_id}">', page.body.decode())
        self.assertNotIn("{{", page.body.decode())
        policy = page.headers["content-security-policy"]
        for rule in ("default-src 'none'", "script-src 'self'", "connect-src 'self'", "frame-ancestors 'none'"):
            self.assertIn(rule, policy)
        self.assertNotIn("unsafe", policy)
        self.assertEqual([page.headers[name] for name in ("referrer-policy", "cache-control", "x-frame-options")],
                         ["no-referrer", "no-store", "DENY"])
        self.assertNotIn("set-cookie", page.headers)

    def test_a_page_needs_its_own_key(self):
        other = self.job("x")
        theirs = self.server.post_url(other["batch"]).split("?k=")[1]
        pair = self.server.pair_url().split("?k=")[1]
        for path in (f"/post/{self.batch_id}", f"/post/{self.batch_id}?k=", f"/post/{self.batch_id}?k=nope",
                     f"/post/{self.batch_id}?k={theirs}", f"/post/{self.batch_id}?k={pair}", f"/pair?k={self.k}",
                     "/pair", f"/v1/page/batch/{self.batch_id}", f"/v1/page/batch/{self.batch_id}?k={theirs}",
                     f"/v1/page/batch/{other['batch']}?k={self.k}", f"/v1/page/pair?k={self.k}", "/v1/page/pair",
                     f"/post/{self.batch_id}?key={self.k}"):
            with self.subTest(path=path):
                answer = self.ask("GET", path)
                self.assertEqual((answer.status, answer.error), (403, "bad_key"))
        self.assertEqual(self.manual(body={"k": theirs}).error, "bad_key")
        self.assertEqual(self.manual(body={"k": None}).error, "bad_key")
        self.assertEqual(self.manual(body={"batch": other["batch"]}).error, "bad_key")
        self.assertEqual(self.state(self.tiktok), "queued")

    def test_a_key_runs_out_after_two_hours(self):
        self.assertEqual(self.ask("GET", self.url.split(str(self.port), 1)[1]).status, 200)
        self.later(extension_api.PAGE_FOR + 1)
        for answer in (self.ask("GET", self.url.split(str(self.port), 1)[1]),
                       self.ask("GET", f"/v1/page/batch/{self.batch_id}?k={self.k}"), self.manual()):
            self.assertEqual((answer.status, answer.error), (403, "bad_key"))
        self.server.pair_url()  # the old ones are dropped when the next page is made
        self.assertEqual(len(self.server.pages), 1)

    def test_the_page_is_told_where_each_post_stands_without_its_text(self):
        told = self.page()
        self.assertEqual((told["batch"], told["note"]), (self.batch_id, posting.TESTED))
        self.assertEqual(told["video"], {"name": "ep01.mp4", "size": 13, "sha256": posting.digest(self.video)})
        self.assertEqual(told["extension"], {"paired": False, "seen_seconds_ago": None, "folder": str(self.folder),
                                             "folder_there": True})
        self.assertEqual([(j["platform"], j["name"], j["state"], j["line"], j["tone"], j["by_hand"], j["handle"])
                          for j in told["jobs"]],
                         [("tiktok", "TikTok", "queued", "Queued: waiting for the extension", "wait", True, "@me"),
                          ("youtube", "YouTube", "queued", "Queued: waiting for the extension", "wait", True, "@me")])
        for job in told["jobs"]:
            self.assertNotIn("text", job)
        self.assertNotIn(CAPTION, json.dumps(told))
        self.pair()
        self.assertEqual(self.page()["extension"]["paired"], True)
        self.assertLessEqual(self.page()["extension"]["seen_seconds_ago"], 5)
        (self.folder / "manifest.json").unlink()
        self.assertFalse(self.page()["extension"]["folder_there"])

    def test_the_words_for_every_state(self):
        job = {**self.tiktok, "handle": "@me"}
        names = {"tiktok": "TikTok"}

        def words(**change) -> tuple[str, str, str, bool]:
            row = extension_api.page_job({**job, **change}, "TikTok", names)
            return row["line"], row["note"], row["tone"], row["by_hand"]

        self.assertEqual(words(state="claimed"), ("The extension has it", "", "busy", False))
        self.assertEqual(words(state="opening"), ("Opening TikTok", "", "busy", False))
        self.assertEqual(words(state="opening", step="signed_in")[0], "Checking you're signed in to TikTok")
        self.assertEqual(words(state="uploading", detail="12 of 28 MB"), ("Uploading", "12 of 28 MB", "busy", False))
        self.assertEqual(words(state="filling")[0], "Filling in the text")
        self.assertEqual(words(state="ready"), ("Ready: check it and press Post in the TikTok tab",
                                                "ReelForge stops here. Nothing is posted until you press it.",
                                                "ready", False))
        ready = extension_api.page_job({**job, "platform": "instagram", "state": "ready"}, "Instagram", names)
        self.assertEqual(ready["line"], "Ready: check it and press Share in the Instagram tab")
        self.assertEqual(words(state="posting")[0], "Pressing Post for you")
        self.assertEqual(words(state="posted", url=TIKTOK_URL), ("Posted", "", "good", False))
        self.assertIn("after the next stats read", words(state="posted")[1])
        line, note, tone, by_hand = words(state="failed", step="find_input", error_code="hook_not_found")
        self.assertEqual((line, tone, by_hand), ("Failed at finding the upload box", "bad", True))
        self.assertEqual(note, "TikTok changed its page, so the extension lost its way: post this one by hand. You "
                               "can finish in the tab that is open, or press Post by hand.")
        self.assertIn("TikTok said no. You can", words(state="failed", error_message="TikTok said no")[1])
        self.assertIn("then post again. You can", words(state="failed", error_message="check it, then post again.")[1])
        line, note, tone, by_hand = words(state="unknown")
        self.assertEqual((line, tone, by_hand), ("ReelForge can't tell whether this posted", "ask", False))
        self.assertTrue(note.startswith("Check TikTok before trying again."))
        self.assertEqual(words(state="expired")[0], "The extension didn't pick this up in time")
        self.assertEqual(words(state="cancelled", detail="posted by hand"),
                         ("You're posting this one by hand", "", "wait", True))
        self.assertEqual(words(state="cancelled", detail="you cancelled it"),
                         ("Cancelled", "You cancelled it.", "wait", True))
        for state in posting.LIVE + posting.FREE:  # every state has its words, and none is an em dash away from plain
            line, note, _, _ = words(state=state)
            self.assertTrue(line)
            self.assertNotIn(chr(0x2014), line + note)

    def test_post_by_hand_is_todays_flow_and_sets_the_job_aside(self):
        answer = self.manual("youtube")
        self.assertEqual(answer.status, 200, answer.body)
        self.assertEqual((answer.json["ok"], answer.json["opened"], answer.json["upload_url"]),
                         (True, True, "https://www.youtube.com/upload"))
        self.assertEqual(answer.json["message"],
                         "YouTube's upload page is opening. The title is copied: drag ep01.mp4 in from "
                         f"{desktop.FILES_WINDOW}, then paste. For the description, use Copy YouTube hashtags on "
                         "ReelForge's Publish tab.")
        self.assertEqual((answer.json["job"]["state"], answer.json["job"]["line"]),
                         ("cancelled", "You're posting this one by hand"))
        self.assertEqual((self.copied, self.revealed, self.opened),
                         ([f"{CAPTION} #shorts"], [self.video], ["https://www.youtube.com/upload"]))
        job = posting.get(self.youtube["id"])
        self.assertEqual((job["state"], job["detail"]), ("cancelled", "posted by hand"))
        # The upload log has the page that was opened, and no longer the post the extension never took.
        self.assertEqual([(u["platform"], u["status"]) for u in db.uploads()],
                         [("youtube", "manual"), ("tiktok", "extension")])
        self.assertEqual(self.state(self.tiktok), "queued")
        self.assertEqual(self.manual("youtube").status, 200)  # pressed again: the same again, still set aside
        self.assertGreaterEqual(self.app.changes, 2)

    def test_by_hand_with_no_browser_says_so_and_notes_no_page(self):
        self.browser = False
        answer = self.manual("tiktok")
        self.assertEqual((answer.status, answer.json["ok"], answer.json["opened"]), (200, True, False))
        self.assertTrue(answer.json["message"].startswith("ReelForge couldn't open your browser for TikTok. Open its "
                                                          "upload page yourself: the caption and hashtags is copied"))
        self.assertEqual(answer.json["upload_url"], "https://www.tiktok.com/tiktokstudio/upload")
        self.assertEqual(self.copied, [f"{CAPTION} #fyp {TAGS}"])
        self.assertEqual(self.state(self.tiktok), "cancelled")
        self.assertEqual([u["status"] for u in db.uploads()], ["extension"])  # YouTube's job: no page was noted

    def test_by_hand_is_not_for_a_post_the_browser_is_working_on(self):
        self.walk(self.tiktok, "uploading")
        answer = self.manual("tiktok")
        self.assertEqual((answer.status, answer.error), (409, "not_manual"))
        self.assertEqual((self.opened, self.copied), ([], []))
        self.assertEqual(self.state(self.tiktok), "uploading")
        posting.advance(self.tiktok["id"], "failed", install=CHROME, error_code="timeout")
        self.assertEqual(self.manual("tiktok").status, 200)  # once it has failed, it is
        self.assertEqual(self.manual("x").status, 404)       # no such post in this batch
        for body in ({"platform": 7}, {"batch": "nope"}, {"batch": None}):
            self.assertEqual(self.manual(body=body).status, 400)

    def test_by_hand_cannot_be_pressed_from_another_site(self):
        for kw in ({"origin": "https://evil.example"}, {"kind": "text/plain"},
                   {"kind": "application/x-www-form-urlencoded"}, {"headers": {"Sec-Fetch-Site": "cross-site"}}):
            answer = self.manual("tiktok", **kw)
            self.assertIn(answer.status, (403, 415), kw)
        self.assertEqual((self.opened, self.state(self.tiktok)), ([], "queued"))

    def test_the_pages_own_files_and_nothing_else(self):
        for name, kind in (("page.css", "text/css; charset=utf-8"), ("page.js", "text/javascript; charset=utf-8"),
                           ("icon.svg", "image/svg+xml")):
            answer = self.ask("GET", f"/assets/{name}")
            self.assertEqual((answer.status, answer.headers["content-type"]), (200, kind))
            self.assertEqual(answer.body, (extension_api.WEB / name).read_bytes())
            self.assertEqual(answer.headers["x-content-type-options"], "nosniff")
        source = Path(extension_api.__file__).read_bytes()
        for path in ("/assets/pair.html", "/assets/post.html", "/assets/", "/assets", "/assets/page.css/",
                     "/assets/../extension_api.py", "/assets/..%2fextension_api.py", "/assets/%2e%2e/extension_api.py",
                     "/assets/%2e%2e%2fextension_api.py", "/assets/..%5cextension_api.py",
                     "/assets/..\\extension_api.py",
                     "/assets/....//extension_api.py", "/assets/page.css/../../extension_api.py",
                     "/assets/page.css%00.py", "/assets//etc/passwd", "/assets/%2fetc%2fpasswd", "/assets/PAGE.CSS",
                     "/assets/page.css?x=/../../extension_api.py", "/../extension_api.py", "/%2e%2e/extension_api.py",
                     "/assets/page.js.map", "/web/page.js", "/dashboard/web/page.js"):
            with self.subTest(path=path):
                answer = self.ask("GET", path)
                if path.startswith("/assets/page.css?"):
                    self.assertEqual(answer.body, (extension_api.WEB / "page.css").read_bytes())  # the query is no path
                    continue
                self.assertIn(answer.status, (400, 404))
                self.assertNotEqual(answer.body, source)
                self.assertNotIn(b"def manifest_id", answer.body)

    def test_the_pages_load_nothing_from_anywhere_else(self):
        for name in ("pair.html", "post.html", "page.css", "page.js"):
            text = (extension_api.WEB / name).read_text("utf-8")
            with self.subTest(file=name):
                for found in re.findall(r"""(?:src|href|action)\s*=\s*["']([^"']+)""", text):
                    self.assertTrue(found.startswith("/assets/"), found)
                self.assertNotRegex(text, r"@import|url\(|@font-face|<iframe|<form|eval\(|innerHTML|document\.cookie"
                                          r"|localStorage|\bon\w+\s*=\s*[\"']")
                self.assertNotIn(chr(0x2014), text)
                self.assertIn(name == "page.js", ("fetch(" in text, True))
        script = (extension_api.WEB / "page.js").read_text("utf-8")
        self.assertEqual(sorted(set(re.findall(r"fetch\(([^,)]+)", script))), ['"/v1/page/manual"', "url"])
        self.assertNotRegex(script, r"https?://(?!\")")  # no address is written into it
        for name in ("pair.html", "post.html"):
            html = (extension_api.WEB / name).read_text("utf-8")
            self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', html)
        styles = (extension_api.WEB / "page.css").read_text("utf-8")
        self.assertIn("prefers-reduced-motion: reduce", styles)
        self.assertIn(":focus-visible", styles)

if __name__ == "__main__":
    unittest.main()
