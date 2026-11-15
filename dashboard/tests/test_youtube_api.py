"""Posting to YouTube through its own API, the Google side (dashboard.platforms.youtube_api): connecting (the
consent round trip with its PKCE proof and state, a wrong state, a refusal, no answer), the sign-in (renewed when it
runs out, "connect again" once Google has ended it), the upload in pieces and everything that can go wrong with one
(a 503, a lost answer, a lost last answer the session can and can't account for, every refusal Google documents),
disconnecting, the file it keeps and the command. "Google" is the stand-in in dashboard/tests/google.py, on this
computer; "the browser" follows the consent page's redirect the way yours would. Nothing reaches the internet, and
nothing waits: the pauses between tries are only counted."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.client
import io
import json
import os
import socket
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import httpx

from dashboard import config, db, desktop
from dashboard.platforms import youtube_api
from dashboard.platforms.youtube_api import ApiError, Stopped, Unknown
from dashboard.tests.google import CHANNEL, SCOPE, Google

KIT = Path(__file__).resolve().parent.parent.parent
CLIENT_ID = "1234567890-abcdefghijklmnop.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-client-secret-of-the-test"
BYTES = bytes(range(256)) * 14  # 3,584 bytes: four pieces of a thousand
SIZE = len(BYTES)
STORE_PATH = youtube_api.store_path  # the real one: every test here keeps its file in a scratch folder instead
PIECE = youtube_api.CHUNK            # the real size of a piece (the tests send pieces of a thousand bytes)


def google_for(test: unittest.TestCase, root: Path) -> Google:
    """Google's stand-in for one test, with the addresses this file asks pointed at it, the pauses between tries
    only counted (test.pauses), and "your browser" (test.browser: whether one opens; test.opened; test.shown: what
    it ended up showing) following Google's consent page wherever it sends you. Also a client file as Google Cloud
    hands one out (test.client_file), whose own addresses point somewhere that must never be asked."""
    test.google = Google()
    test.addCleanup(test.google.close)
    test.pauses, test.browser, test.opened, test.shown = [], True, [], []
    base = test.google.base

    def open_url(url: str) -> bool:
        test.opened.append(url)
        if not test.browser:
            return False
        if url.startswith(base):  # (any other page a test opens is only noted)
            r = httpx.get(url, follow_redirects=True, timeout=10)
            test.shown.append((r.status_code, r.text))
        return True

    for target, name, value in (
            (youtube_api, "AUTH_URL", f"{base}/auth"), (youtube_api, "TOKEN_URL", f"{base}/token"),
            (youtube_api, "REVOKE_URL", f"{base}/revoke"),
            (youtube_api, "UPLOAD_URL", f"{base}/upload/youtube/v3/videos"),
            (youtube_api, "CHUNK", 1000), (youtube_api, "sleep", test.pauses.append),
            (desktop, "open_url", open_url)):
        patch = mock.patch.object(target, name, value)
        patch.start()
        test.addCleanup(patch.stop)
    env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
    env.start()
    test.addCleanup(env.stop)
    youtube_api._access.clear()
    test.addCleanup(youtube_api._access.clear)
    test.client_file = root / "client_secret_1234567890-abc.apps.googleusercontent.com.json"
    test.client_file.write_text(json.dumps({"installed": {
        "client_id": CLIENT_ID, "project_id": "night-physics", "client_secret": CLIENT_SECRET,
        "auth_uri": "https://elsewhere.invalid/auth", "token_uri": "https://elsewhere.invalid/token",
        "redirect_uris": ["http://localhost"]}}), encoding="utf-8")
    return test.google


class ApiTest(unittest.TestCase):
    """A channel's data folder in a scratch place and Google's stand-in listening (google_for)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = self.root / "data" / "youtube_api.json"
        patch = mock.patch.object(youtube_api, "store_path", lambda: self.store)
        patch.start()
        self.addCleanup(patch.stop)
        google_for(self, self.root)
        self.open_url = desktop.open_url
        self.video = self.root / "ep01.mp4"
        self.video.write_bytes(BYTES)
        self.events: list[tuple] = []

    def connect(self, file: Path | None = ...) -> youtube_api.Consent:
        consent = youtube_api.connect(self.client_file if file is ... else file)
        self.assertTrue(consent.done.wait(10))
        return consent

    def refused(self, code: str, fn, *args, **kw) -> ApiError:
        with self.assertRaises(ApiError) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def upload(self, privacy: str = "private", title: str = "What if gravity doubled? #shorts", **kw) -> dict:
        self.events.clear()
        return youtube_api.upload(
            self.video, title, "#whatif #physics", privacy, kw.pop("made_for_kids", False),
            started=lambda uri: self.events.append(("started", len(self.google.puts))),
            progress=lambda sent, size: self.events.append(("progress", sent)),
            last=lambda: self.events.append(("last", len(self.google.puts))), **kw)

    def ranges(self) -> list[str]:
        return [p["range"] for p in self.google.puts]

    def on_file(self) -> dict:
        return json.loads(self.store.read_text("utf-8"))


class Connecting(ApiTest):
    def test_the_round_trip_asks_for_one_permission_and_proves_itself_with_pkce(self):
        consent = self.connect()
        self.assertEqual(consent.outcome, {"ok": True, "code": "connected",
                                           "message": "ReelForge is connected to YouTube's API."})
        (asked,) = self.google.asked
        self.assertEqual(asked["scope"], SCOPE)  # uploading videos, and nothing wider
        self.assertEqual((asked["client_id"], asked["response_type"], asked["code_challenge_method"]),
                         (CLIENT_ID, "code", "S256"))
        self.assertRegex(asked["redirect_uri"], r"^http://127\.0\.0\.1:\d+$")
        self.assertEqual(asked["state"], consent.state)
        self.assertNotIn(consent.verifier, self.opened[0])  # only its SHA-256 goes to the consent page...
        (trade,) = self.google.token_calls
        proof = base64.urlsafe_b64encode(hashlib.sha256(trade["code_verifier"].encode()).digest()).rstrip(b"=")
        self.assertEqual(proof.decode(), asked["code_challenge"])  # ...and the trade for the sign-in proves it
        self.assertEqual((trade["grant_type"], trade["client_id"], trade["client_secret"], trade["redirect_uri"]),
                         ("authorization_code", CLIENT_ID, CLIENT_SECRET, asked["redirect_uri"]))
        self.assertGreaterEqual(len(trade["code_verifier"]), 43)
        kept = self.on_file()
        (refresh,) = self.google.grants
        self.assertEqual((kept["client_id"], kept["client_secret"], kept["refresh_token"]),
                         (CLIENT_ID, CLIENT_SECRET, refresh))
        self.assertNotIn("access_token", kept)  # the short-lived one is never written down
        self.assertEqual(youtube_api.status()["state"], "on")
        self.assertEqual(self.shown[0][0], 200)
        self.assertIn("ReelForge is connected to YouTube", self.shown[0][1])
        self.assertEqual(youtube_api.access_token(), next(iter(self.google.access)))  # the one the trade gave
        self.assertEqual(len(self.google.token_calls), 1)

    def test_the_listener_answers_that_one_request_and_closes(self):
        consent = self.connect()
        port = int(consent.redirect.rsplit(":", 1)[1])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()

    def test_the_addresses_in_the_client_file_are_never_used(self):
        self.connect()
        self.assertTrue(self.opened[0].startswith(self.google.base + "/auth?"))
        self.assertNotIn("elsewhere.invalid", self.store.read_text("utf-8"))

    def test_an_answer_with_another_state_connects_nothing(self):
        self.google.consent = "other_state"
        consent = self.connect()
        self.assertEqual((consent.outcome["ok"], consent.outcome["code"]), (False, "state"))
        self.assertIn("didn't come from the page ReelForge opened", consent.outcome["message"])
        self.assertEqual(self.google.token_calls, [])  # the code that came with it is never traded
        self.assertFalse(self.store.exists())
        self.assertEqual(self.shown[0][0], 400)
        self.assertIn("ReelForge wasn&#x27;t connected".replace("&#x27;", "'"), self.shown[0][1])
        self.assertEqual(youtube_api.status()["state"], "off")

    def test_saying_no_on_googles_page_connects_nothing(self):
        self.google.consent = "no"
        consent = self.connect()
        self.assertEqual(consent.outcome, {"ok": False, "code": "refused",
                                           "message": "You said no on Google's page, so nothing was connected."})
        self.assertEqual(self.google.token_calls, [])
        self.assertFalse(self.store.exists())

    def test_nobody_answering_ends_after_ten_minutes(self):
        now = [1000.0]
        with mock.patch.object(youtube_api, "clock", lambda: now[0]), \
                mock.patch.object(desktop, "open_url", lambda url: True):  # a browser you never look at
            consent = youtube_api.connect(self.client_file)
            self.assertFalse(consent.done.wait(0.6))
            now[0] += youtube_api.CONSENT_FOR
            self.assertTrue(consent.done.wait(5))
        self.assertEqual(consent.outcome["code"], "timeout")
        self.assertIn("ten minutes", consent.outcome["message"])
        self.assertFalse(self.store.exists())

    def test_anything_else_asked_of_the_listener_is_not_the_answer(self):
        with mock.patch.object(desktop, "open_url", lambda url: True):
            consent = youtube_api.connect(self.client_file)
        port = int(consent.redirect.rsplit(":", 1)[1])
        for path, host in (("/favicon.ico", f"127.0.0.1:{port}"), (f"/?state={consent.state}&code=x", "evil.example")):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.putrequest("GET", path, skip_host=True)
            conn.putheader("Host", host)
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 404)
            conn.close()
        self.assertIsNone(consent.outcome)  # still waiting for you
        self.open_url(consent.url())
        self.assertTrue(consent.done.wait(10))
        self.assertTrue(consent.outcome["ok"])

    def test_no_browser_connects_nothing_and_stops_listening(self):
        self.browser = False
        e = self.refused("no_browser", youtube_api.connect, self.client_file)
        self.assertIn("couldn't open your browser", e.message)
        self.assertNotIn(CLIENT_ID, e.message)  # the page's address carries it, so the address is never shown
        self.assertFalse(self.store.exists())

    def test_cancelling_connects_nothing(self):
        with mock.patch.object(desktop, "open_url", lambda url: True):
            consent = youtube_api.connect(self.client_file)
        consent.cancel()
        self.assertTrue(consent.done.wait(5))
        self.assertEqual(consent.outcome["code"], "cancelled")
        with self.assertRaises(httpx.ConnectError):  # an answer that comes after: nobody is listening
            self.open_url(consent.url())
        self.assertFalse(self.store.exists())

    def test_a_file_that_is_not_a_desktop_client_says_which_file_and_why(self):
        missing = self.root / "nowhere.json"
        self.assertIn(f"There is no file at {missing}", self.refused("client_file", youtube_api.connect, missing).message)
        junk = self.root / "notes.txt"
        junk.write_text("my-password-is-hunter2", encoding="utf-8")
        said = self.refused("client_file", youtube_api.connect, junk).message
        self.assertIn("notes.txt isn't a client file from Google Cloud", said)
        self.assertNotIn("hunter2", said)  # nothing of what is in the file
        web = self.root / "web.json"
        web.write_text(json.dumps({"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}}), "utf-8")
        said = self.refused("client_file", youtube_api.connect, web).message
        self.assertIn("Web application client", said)
        self.assertIn("Desktop app client", said)
        half = self.root / "half.json"
        half.write_text(json.dumps({"installed": {"client_id": CLIENT_ID}}), "utf-8")
        self.refused("client_file", youtube_api.connect, half)
        self.assertEqual(self.opened, [])  # no page was opened for any of them
        self.assertEqual(self.refused("client_file", youtube_api.connect, None).message[:21], "Pick the client file ")

    def test_google_giving_no_lasting_sign_in_or_not_the_permission_connects_nothing(self):
        self.google.no_refresh = True
        consent = self.connect()
        self.assertEqual((consent.outcome["ok"], consent.outcome["code"]), (False, "refused"))
        self.google.no_refresh, self.google.scope = False, "openid email"
        consent = self.connect()
        self.assertEqual((consent.outcome["ok"], consent.outcome["code"]), (False, "scope"))
        self.assertIn("permission to upload videos", consent.outcome["message"])
        self.assertFalse(self.store.exists())

    def test_google_refusing_the_trade_says_so_in_its_word_only(self):
        self.google.token_script = ["invalid_client"]
        consent = self.connect()
        self.assertEqual((consent.outcome["ok"], consent.outcome["code"]), (False, "refused"))
        self.assertIn("(invalid_client)", consent.outcome["message"])
        self.assertIn("Desktop app client", consent.outcome["message"])
        self.assertFalse(self.store.exists())

    def test_connecting_again_needs_no_file_and_keeps_your_choices(self):
        self.connect()
        youtube_api.set_defaults("unlisted", True)
        first = self.on_file()["refresh_token"]
        self.google.end_grant()
        youtube_api._access.clear()  # (as when the token it has in mind has run out)
        self.refused("not_connected", youtube_api.access_token)
        self.assertEqual(youtube_api.status()["state"], "again")
        consent = self.connect(None)
        self.assertTrue(consent.outcome["ok"])
        said = youtube_api.status()
        self.assertEqual((said["state"], said["why"], said["privacy"], said["made_for_kids"]),
                         ("on", None, "unlisted", True))
        self.assertNotEqual(self.on_file()["refresh_token"], first)
        self.assertNotIn("lapsed", self.on_file())

    def test_how_long_the_sign_in_lasts_is_kept_when_google_says(self):
        self.connect()
        self.assertIsNone(youtube_api.status()["expires"])
        self.google.lasts = 604799  # a consent screen in Testing
        self.connect()
        self.assertRegex(youtube_api.status()["expires"], r"^\d{4}-\d\d-\d\dT")


class SignIn(ApiTest):
    def setUp(self) -> None:
        super().setUp()
        self.connect()
        self.google.token_calls.clear()

    def test_a_token_is_asked_for_only_when_the_last_one_has_run_out(self):
        first = youtube_api.access_token()
        self.assertEqual(youtube_api.access_token(), first)
        self.assertEqual(self.google.token_calls, [])
        refresh = self.on_file()["refresh_token"]
        youtube_api._access[refresh] = (first, 0.0)  # as an hour later
        second = youtube_api.access_token()
        self.assertNotEqual(second, first)
        (call,) = self.google.token_calls
        self.assertEqual((call["grant_type"], call["refresh_token"], call["client_id"], call["client_secret"]),
                         ("refresh_token", refresh, CLIENT_ID, CLIENT_SECRET))
        self.assertEqual(youtube_api.access_token(fresh=True) in self.google.access, True)
        self.assertEqual(len(self.google.token_calls), 2)

    def test_a_sign_in_google_has_ended_is_connect_again_and_is_not_asked_about_twice(self):
        self.google.end_grant()
        youtube_api._access.clear()
        e = self.refused("not_connected", youtube_api.access_token)
        self.assertEqual(e.message, youtube_api.AGAIN["grant"])
        self.assertIn("Connect again", e.message)
        self.assertEqual(len(self.google.token_calls), 1)
        said = youtube_api.status()
        self.assertEqual((said["state"], said["why"], said["client"]), ("again", "grant", True))
        kept = self.on_file()
        self.assertNotIn("refresh_token", kept)  # the dead one is gone; the client stays, to connect again with
        self.assertEqual(kept["client_id"], CLIENT_ID)
        for _ in range(3):
            self.assertEqual(self.refused("not_connected", youtube_api.access_token).message, e.message)
        self.assertEqual(len(self.google.token_calls), 1)  # Google isn't asked again and again

    def test_a_client_google_no_longer_knows_says_so(self):
        youtube_api._access.clear()
        self.google.token_script = ["invalid_client"]
        e = self.refused("not_connected", youtube_api.access_token)
        self.assertIn("no longer knows the OAuth client", e.message)
        self.assertEqual((youtube_api.status()["state"], youtube_api.status()["why"]), ("again", "client"))

    def test_google_being_busy_is_waited_out(self):
        youtube_api._access.clear()
        self.google.token_script = ["503", "503"]
        self.assertIn(youtube_api.access_token(), self.google.access)
        self.assertEqual(self.pauses, [1.0, 2.0])
        self.assertEqual(youtube_api.status()["state"], "on")

    def test_no_internet_is_said_plainly_and_ends_nothing(self):
        youtube_api._access.clear()
        with socket.socket() as s:  # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        with mock.patch.object(youtube_api, "TOKEN_URL", f"http://127.0.0.1:{closed}/token"):
            e = self.refused("offline", youtube_api.access_token)
        self.assertIn("couldn't be reached", e.message)
        self.assertEqual(len(self.pauses), youtube_api.TRIES - 1)
        self.assertEqual(youtube_api.status()["state"], "on")  # still connected: it was the network

    def test_an_error_of_another_kind_is_said_by_its_word(self):
        youtube_api._access.clear()
        self.google.token_script = ["server_error"]
        e = self.refused("refused", youtube_api.access_token)
        self.assertIn("(server_error)", e.message)
        self.assertEqual(youtube_api.status()["state"], "on")

    def test_not_connected_at_all(self):
        youtube_api.disconnect()
        self.assertEqual(self.refused("not_connected", youtube_api.access_token).message, youtube_api.NOT_CONNECTED)


class Uploading(ApiTest):
    def setUp(self) -> None:
        super().setUp()
        self.connect()
        self.google.token_calls.clear()

    def one_video(self) -> dict:
        """The one video Google has, after one session."""
        self.assertEqual((len(self.google.starts), len(self.google.sessions), len(self.google.videos)), (1, 1, 1))
        return self.google.videos[0]

    def test_a_video_goes_up_in_pieces(self):
        made = self.upload()
        self.assertEqual(self.ranges(), [f"bytes 0-999/{SIZE}", f"bytes 1000-1999/{SIZE}", f"bytes 2000-2999/{SIZE}",
                                         f"bytes 3000-{SIZE - 1}/{SIZE}"])
        self.assertEqual(bytes(self.one_video()["data"]), BYTES)
        self.assertEqual(made, {"id": "vid00000001", "privacy": "private", "made_for_kids": False,
                                "upload_status": "uploaded", "reason": "", "channel_id": CHANNEL["id"],
                                "channel_title": CHANNEL["title"]})
        # The session is noted before any of the file goes, and the ledger hears of the last piece before it does.
        self.assertEqual(self.events, [("started", 0), ("progress", 1000), ("progress", 2000), ("progress", 3000),
                                       ("last", 3)])
        self.assertEqual(self.pauses, [])

    def test_a_video_smaller_than_one_piece_is_its_own_last_piece(self):
        with mock.patch.object(youtube_api, "CHUNK", PIECE):
            made = self.upload()
        self.assertEqual(self.ranges(), [f"bytes 0-{SIZE - 1}/{SIZE}"])
        self.assertEqual(self.events, [("started", 0), ("last", 0)])  # the ledger hears before anything goes
        self.assertEqual((made["id"], bytes(self.one_video()["data"])), ("vid00000001", BYTES))
        self.assertEqual((PIECE > SIZE, PIECE % (256 * 1024)), (True, 0))  # a multiple of 256 KiB, as YouTube asks

    def test_who_can_see_it_and_the_kids_answer_always_go_with_it_and_are_asked_back(self):
        self.upload("unlisted", title="Café ✓ 日本 #shorts", made_for_kids=True)
        (start,) = self.google.starts
        self.assertEqual(start["query"], {"uploadType": "resumable", "part": "snippet,status"})
        self.assertEqual(start["body"], {
            "snippet": {"title": "Café ✓ 日本 #shorts", "description": "#whatif #physics", "categoryId": "22"},
            "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": True}})
        self.assertEqual((start["headers"]["x-upload-content-length"], start["headers"]["x-upload-content-type"],
                          start["headers"]["content-type"]), (str(SIZE), "video/mp4", "application/json; charset=UTF-8"))
        with self.assertRaises(ValueError):  # never left to YouTube, whose own default is public
            self.upload("")
        self.assertEqual(len(self.google.starts), 1)

    def test_a_503_in_the_middle_is_waited_out_and_the_upload_carries_on_from_where_youtube_stands(self):
        self.google.script = [None, "503"]
        made = self.upload()
        self.assertEqual(self.ranges(), [f"bytes 0-999/{SIZE}", f"bytes 1000-1999/{SIZE}", f"bytes */{SIZE}",
                                         f"bytes 1000-1999/{SIZE}", f"bytes 2000-2999/{SIZE}",
                                         f"bytes 3000-{SIZE - 1}/{SIZE}"])
        self.assertEqual(self.pauses, [1.0])
        self.assertEqual((made["id"], bytes(self.one_video()["data"])), ("vid00000001", BYTES))

    def test_a_piece_that_arrived_although_its_answer_was_an_error_is_not_sent_again(self):
        self.google.script = [None, "503-kept"]
        self.upload()
        self.assertEqual(self.ranges(), [f"bytes 0-999/{SIZE}", f"bytes 1000-1999/{SIZE}", f"bytes */{SIZE}",
                                         f"bytes 2000-2999/{SIZE}", f"bytes 3000-{SIZE - 1}/{SIZE}"])
        self.assertEqual(bytes(self.one_video()["data"]), BYTES)

    def test_youtube_having_less_than_was_sent_gets_the_rest_again(self):
        self.google.script = [None, "drop", "drop"]  # the second piece never arrives, nor does the first question
        self.upload()
        self.assertEqual(self.ranges()[:5], [f"bytes 0-999/{SIZE}", f"bytes 1000-1999/{SIZE}", f"bytes */{SIZE}",
                                             f"bytes */{SIZE}", f"bytes 1000-1999/{SIZE}"])
        self.assertEqual(self.pauses, [1.0, 1.0])
        self.assertEqual(bytes(self.one_video()["data"]), BYTES)

    def test_the_last_answer_lost_and_the_session_says_it_is_finished(self):
        self.google.script = [None, None, None, "lost"]
        made = self.upload()
        self.assertEqual(self.ranges()[-2:], [f"bytes 3000-{SIZE - 1}/{SIZE}", f"bytes */{SIZE}"])
        self.assertEqual(made["id"], "vid00000001")
        self.assertEqual(bytes(self.one_video()["data"]), BYTES)  # one session, one video
        self.assertEqual(self.events.count(("last", 3)), 1)

    def test_the_last_answer_lost_and_nobody_can_say_is_unknown_and_never_a_second_upload(self):
        self.google.script, self.google.then = [None, None, None, "lost"], "drop"
        with self.assertRaises(Unknown):
            self.upload()
        self.assertEqual(self.ranges()[4:], [f"bytes */{SIZE}"] * youtube_api.TRIES)  # it asked, and asked again
        self.one_video()  # it is up at YouTube, and no second session was ever started for it

    def test_the_last_piece_never_arriving_is_sent_again_in_the_same_session(self):
        self.google.script = [None, None, None, "drop"]
        made = self.upload()
        self.assertEqual(self.ranges()[3:], [f"bytes 3000-{SIZE - 1}/{SIZE}", f"bytes */{SIZE}",
                                             f"bytes 3000-{SIZE - 1}/{SIZE}"])
        self.assertEqual(made["id"], "vid00000001")
        self.one_video()
        self.assertEqual([e for e in self.events if e[0] == "last"], [("last", 3)])  # said once

    def test_a_connection_that_stays_down_before_the_last_piece_is_a_plain_failure(self):
        self.google.script, self.google.then = [None, "drop"], "drop"
        e = self.refused("offline", self.upload)
        self.assertIn("couldn't be reached", e.message)
        self.assertEqual((len(self.google.sessions), self.google.videos), (1, []))  # nothing can have been posted
        self.assertNotIn(("last", 1), self.events)

    def test_pieces_that_keep_failing_while_the_session_answers_end_it_too(self):
        self.google.then = "503"
        self.google.script = [None] + ["503", None] * 30  # each piece fails, each question is answered
        e = self.refused("offline", self.upload)
        self.assertIn("kept breaking", e.message)
        self.assertEqual(self.google.videos, [])
        self.assertEqual(self.ranges().count(f"bytes 1000-1999/{SIZE}"), youtube_api.TRIES + 1)

    def test_youtube_never_taking_more_of_the_file_ends_it_instead_of_going_round_for_ever(self):
        self.google.script, self.google.then = [None], "ignored"  # it answers every piece and keeps none
        e = self.refused("offline", self.upload)
        self.assertIn("kept breaking", e.message)
        self.assertEqual(self.ranges(), [f"bytes 0-999/{SIZE}"] + [f"bytes 1000-1999/{SIZE}"] * (youtube_api.TRIES + 1))
        self.assertEqual(self.google.videos, [])

    def test_a_no_to_the_question_after_the_last_piece_is_still_unknown(self):
        self.google.script = [None, None, None, "lost", (403, "quotaExceeded")]  # the video is up; the question isn't answered
        with self.assertRaises(Unknown):
            self.upload()
        self.one_video()
        self.google.sessions.clear(), self.google.videos.clear(), self.google.starts.clear()
        self.google.script = [None, "drop", (403, "quotaExceeded")]  # before the last piece, it is a plain failure
        e = self.refused("quota", self.upload)
        self.assertFalse(e.definite)
        self.assertEqual(self.google.videos, [])

    def test_youtube_having_all_of_it_without_saying_what_it_made_is_asked_again(self):
        whole = httpx.Response(308, headers={"Range": f"bytes=0-{SIZE - 1}"})
        self.assertEqual(youtube_api._stands(whole, SIZE), ("unclear", None))
        self.assertEqual(youtube_api._stands(httpx.Response(308, headers={"Range": "bytes=0-99"}), SIZE), ("have", 100))
        self.assertEqual(youtube_api._stands(httpx.Response(308), SIZE), ("have", 0))
        self.assertEqual(youtube_api._stands(None, SIZE), ("unclear", None))

    def test_a_token_that_runs_out_in_the_middle_is_renewed_once(self):
        self.google.script = [None, "401"]
        self.upload()
        self.assertEqual([c["grant_type"] for c in self.google.token_calls], ["refresh_token"])
        self.assertEqual(self.ranges()[1:4], [f"bytes 1000-1999/{SIZE}", f"bytes */{SIZE}", f"bytes 1000-1999/{SIZE}"])
        self.assertEqual(self.pauses, [])
        self.assertNotEqual(self.google.puts[1]["bearer"], self.google.puts[2]["bearer"])
        self.assertEqual(bytes(self.one_video()["data"]), BYTES)

    def test_a_sign_in_that_ends_in_the_middle_stops_the_upload(self):
        self.google.script, self.google.then = [None, "401"], "401"
        self.google.grants.clear()
        e = self.refused("not_connected", self.upload)
        self.assertEqual(e.message, youtube_api.AGAIN["grant"])
        self.assertEqual(self.google.videos, [])
        self.assertEqual(youtube_api.status()["state"], "again")

    def test_a_session_youtube_let_go_of(self):
        self.google.script = [None, "404"]
        self.assertIn("let go of this upload", self.refused("session", self.upload).message)
        self.google.script = [None, None, None, "404"]  # on the last piece nobody knows
        with self.assertRaises(Unknown):
            self.upload()
        self.assertEqual(self.google.videos, [])

    def test_a_refusal_of_the_last_piece_in_googles_own_words_is_a_failure(self):
        self.google.script = [None, None, None, (403, "forbidden")]
        e = self.refused("forbidden", self.upload)
        self.assertTrue(e.definite)
        self.assertEqual(self.google.videos, [])

    def test_every_refusal_google_documents_is_a_sentence(self):
        for reason, (code, sentence) in youtube_api.REFUSALS.items():
            with self.subTest(reason):
                self.google.start_script = [(400 if reason.startswith("invalid") else 403, reason)]
                e = self.refused(code, self.upload)
                self.assertEqual(e.message, sentence)
                self.assertRegex(e.message, r"^[A-Z].*\.$")
                self.assertNotIn("The request was refused", e.message)  # Google's own free text isn't passed on
                self.assertNotIn(reason, e.message)
                self.assertTrue(e.definite)
        self.assertIn("midnight Pacific", youtube_api.REFUSALS["quotaExceeded"][1])
        self.assertEqual((self.google.sessions, self.google.puts, self.events), ({}, [], []))

    def test_a_refusal_this_file_has_no_sentence_for_is_said_by_googles_word(self):
        self.google.start_script = [(403, "somethingNew")]
        self.assertEqual(self.refused("refused", self.upload).message, "YouTube refused the upload (somethingNew).")
        with mock.patch.object(youtube_api, "UPLOAD_URL", f"{self.google.base}/nowhere"):  # a 404 that isn't JSON
            self.assertEqual(self.refused("refused", self.upload).message, "YouTube refused the upload (error 404).")

    def test_being_told_to_slow_down_or_wait_is_waited_out_before_anything_is_sent(self):
        self.google.start_script = [(403, "rateLimitExceeded"), "503", "drop"]
        self.upload()
        self.assertEqual(self.pauses, [1.0, 2.0, 4.0])
        self.assertEqual((len(self.google.starts), len(self.google.sessions), len(self.google.videos)), (4, 1, 1))

    def test_youtube_out_of_reach_from_the_start(self):
        self.google.start_script = ["drop"] * youtube_api.TRIES
        e = self.refused("offline", self.upload)
        self.assertIn("didn't start", e.message)
        self.assertEqual((self.google.sessions, self.events), ({}, []))

    def test_a_token_google_stopped_taking_early_is_renewed_once_at_the_start(self):
        self.google.expire_access()
        self.upload()
        self.assertEqual([c["grant_type"] for c in self.google.token_calls], ["refresh_token"])
        self.google.start_script = ["401", "401"]
        self.assertIn("Connect again", self.refused("not_connected", self.upload).message)

    def test_an_upload_address_that_is_not_googles_gets_nothing(self):
        self.google.session_at = "https://elsewhere.invalid/upload"
        e = self.refused("odd", self.upload)
        self.assertIn("doesn't know", e.message)
        self.assertEqual((self.google.puts, self.events), ([], []))
        for uri, ours in (("https://www.googleapis.com/upload/youtube/v3/videos?upload_id=x", True),
                          ("https://youtube.googleapis.com/upload/youtube/v3/videos?upload_id=x", True),
                          ("http://www.googleapis.com/upload?upload_id=x", False),
                          ("https://googleapis.com.elsewhere.invalid/upload", False),
                          ("https://notgoogleapis.com/upload", False),
                          ("https://www.googleapis.com/upload?upload_id=x y", False), (None, False)):
            self.assertEqual(youtube_api._ours(uri), ours, uri)

    def test_calling_it_off_between_two_pieces_sends_no_more(self):
        with self.assertRaises(Stopped):
            self.upload(stop=lambda: len(self.google.puts) >= 2)
        self.assertEqual((len(self.google.puts), self.google.videos), (2, []))

    def test_what_youtube_stored_comes_back_as_it_stored_it(self):
        self.google.stores_as = "private"  # what it used to do to uploads from an unverified project
        made = self.upload("public")
        self.assertEqual(self.google.starts[0]["body"]["status"]["privacyStatus"], "public")
        self.assertEqual(made["privacy"], "private")

    def test_a_file_that_changes_while_it_goes_up(self):
        def shorten(sent, size):
            self.video.write_bytes(BYTES[:1500])

        with self.assertRaises(ApiError) as caught:
            youtube_api.upload(self.video, "t", "d", "private", False, started=lambda uri: None, progress=shorten)
        self.assertEqual(caught.exception.code, "changed")
        self.assertEqual(self.google.videos, [])
        self.video.write_bytes(b"")
        self.refused("empty", self.upload)

    def test_asking_a_saved_session_where_it_stands(self):
        uris = []
        self.google.script, self.google.then = [None, None, None, "lost"], "drop"
        with self.assertRaises(Unknown):
            youtube_api.upload(self.video, "t", "d", "private", False, started=uris.append)
        self.assertIn("couldn't be reached", self.refused("offline", youtube_api.ask, uris[0], SIZE).message)
        self.google.then = None
        self.assertEqual(youtube_api.ask(uris[0], SIZE)["id"], "vid00000001")  # it had finished
        self.google.script = [None, "drop"]
        self.google.then = "drop"
        self.refused("offline", youtube_api.upload, self.video, "t", "d", "private", False, started=uris.append)
        self.google.then = None
        self.assertIsNone(youtube_api.ask(uris[1], SIZE))  # it never got the whole file
        before = len(self.google.puts)
        self.refused("session", youtube_api.ask, "https://elsewhere.invalid/upload?upload_id=x", SIZE)
        self.assertEqual(len(self.google.puts), before)
        self.assertEqual(len(self.google.videos), 1)


class Disconnecting(ApiTest):
    def test_the_file_goes_and_google_is_told_to_end_the_grant(self):
        self.connect()
        refresh = self.on_file()["refresh_token"]
        self.assertEqual(youtube_api.disconnect(), {"was": True, "revoked": True})
        self.assertEqual(self.google.revoke_calls, [refresh])
        self.assertEqual(self.google.grants, {})
        self.assertFalse(self.store.exists())
        self.assertEqual(youtube_api.status()["state"], "off")
        self.refused("not_connected", youtube_api.access_token)  # the token it had in mind is gone too
        self.assertEqual(youtube_api.disconnect(), {"was": False, "revoked": None})

    def test_google_out_of_reach_is_said_and_the_file_still_goes(self):
        self.connect()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        with mock.patch.object(youtube_api, "REVOKE_URL", f"http://127.0.0.1:{closed}/revoke"):
            self.assertEqual(youtube_api.disconnect(), {"was": True, "revoked": False})
        self.assertFalse(self.store.exists())

    def test_a_sign_in_google_no_longer_knows_counts_as_ended(self):
        self.connect()
        self.google.end_grant()
        self.assertEqual(youtube_api.disconnect(), {"was": True, "revoked": True})

    def test_a_connection_that_had_already_run_out_has_nothing_to_end(self):
        self.connect()
        self.google.end_grant()
        youtube_api._access.clear()
        self.refused("not_connected", youtube_api.access_token)
        self.assertEqual(youtube_api.disconnect(), {"was": True, "revoked": None})
        self.assertEqual(self.google.revoke_calls, [])
        self.assertFalse(self.store.exists())

    def test_disconnecting_what_was_never_connected_writes_nothing(self):
        self.assertEqual(youtube_api.disconnect(), {"was": False, "revoked": None})
        self.assertFalse(self.store.parent.exists())


class Store(ApiTest):
    def test_the_file_is_yours_alone_and_written_whole(self):
        self.connect()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)
        youtube_api.set_defaults("public")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)
        self.assertEqual(sorted(f.name for f in self.store.parent.iterdir()),
                         ["youtube_api.json", "youtube_api.json.lock"])  # no half-written copy is left behind

    def test_a_copy_left_behind_with_other_rights_is_not_trusted_with_them(self):
        self.store.parent.mkdir()
        left = self.store.with_name(f"youtube_api.json.{os.getpid()}.tmp")
        left.write_text("{}", "utf-8")
        os.chmod(left, 0o644)
        self.connect()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)

    def test_a_file_that_cannot_be_read_counts_as_not_connected(self):
        self.store.parent.mkdir()
        self.store.write_text("{not json", "utf-8")
        self.assertEqual(youtube_api.status()["state"], "off")
        self.store.write_text('["a", "list"]', "utf-8")
        self.assertEqual(youtube_api.read_store(), {})
        self.store.write_text(json.dumps({"client_id": "has a space", "client_secret": "x", "refresh_token": "y"}), "utf-8")
        self.assertEqual(youtube_api.status()["state"], "off")  # nothing odd is ever put in a request

    def test_your_choices_start_safe_and_only_take_what_they_know(self):
        said = youtube_api.status()
        self.assertEqual((said["privacy"], said["made_for_kids"]), ("private", False))
        self.connect()
        youtube_api.set_defaults("everyone")  # not a choice there is
        self.assertEqual(youtube_api.status()["privacy"], "private")
        youtube_api.set_defaults("public", True)
        said = youtube_api.status()
        self.assertEqual((said["privacy"], said["made_for_kids"]), ("public", True))
        youtube_api.set_defaults(made_for_kids=False)
        self.assertEqual((youtube_api.status()["privacy"], youtube_api.status()["made_for_kids"]), ("public", False))

    def test_a_session_is_noted_and_dropped(self):
        self.connect()
        youtube_api.save_session("pj_0123456789abcdef", "https://www.googleapis.com/upload?upload_id=abc", 10)
        saved = youtube_api.session("pj_0123456789abcdef")
        self.assertEqual((saved["uri"], saved["size"]), ("https://www.googleapis.com/upload?upload_id=abc", 10))
        self.assertIsNone(youtube_api.session("pj_another"))
        youtube_api.drop_session("pj_0123456789abcdef")
        self.assertIsNone(youtube_api.session("pj_0123456789abcdef"))
        self.assertNotIn("sessions", self.on_file())

    def test_the_channel_the_last_upload_went_to_is_remembered(self):
        self.connect()
        self.assertIsNone(youtube_api.status()["channel"])
        youtube_api.note_channel({"channel_id": CHANNEL["id"], "channel_title": CHANNEL["title"]})
        self.assertEqual(youtube_api.status()["channel"], CHANNEL)

    def test_each_channel_keeps_its_own_in_its_own_data_folder(self):
        data = self.root / "profiles" / "cool" / "dashboard" / "data"
        with mock.patch.object(config, "DATA_DIR", data, create=True), mock.patch.object(db, "_path", None):
            self.assertEqual(STORE_PATH(), data / "youtube_api.json")  # with no stats open: its profile's
        with mock.patch.object(db, "_path", self.root / "elsewhere" / "stats.db"):
            self.assertEqual(STORE_PATH(), self.root / "elsewhere" / "youtube_api.json")  # beside the stats
        ignored = (KIT / ".gitignore").read_text("utf-8").splitlines()
        for line in ("dashboard/data/", "profiles/", "client_secret*.json"):
            self.assertIn(line, ignored)  # neither the file nor the client file you downloaded is ever committed


class Kept(unittest.TestCase):
    """The file is yours: an update of ReelForge never replaces it, whichever channel's it is."""

    def test_an_update_leaves_every_channels_connection_as_it_is(self):
        from studio import update

        mine = {"dashboard/data/youtube_api.json": '{"client_id": "mine", "refresh_token": "mine"}',
                "profiles/cool-epic-facts/dashboard/data/youtube_api.json": '{"client_id": "the other channel\'s"}'}
        for rel in (*mine, "dashboard/data/youtube_api.json.lock", "dashboard/data/youtube_api.upload.lock"):
            self.assertTrue(update.kept(rel), rel)
        self.assertFalse(update.kept("dashboard/platforms/youtube_api.py"))
        self.assertFalse(update.kept("docs/YOUTUBE-API.md"))
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "ReelForge"
        for rel, text in {**mine, "studio/__init__.py": 'KIT_VERSION = "1.0.0"\n'}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, "utf-8")
        archive = Path(tmp.name) / "ReelForge-1.1.0-Windows.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n',
                              "dashboard/platforms/youtube_api.py": "# new\n",
                              "dashboard/data/youtube_api.json": '{"client_id": "the kit\'s"}'}.items():
                z.writestr(f"ReelForge-1.1.0/{rel}", text)
        work = Path(tmp.name) / "work"
        work.mkdir()
        update._copy_release(archive, root, work, lambda *a: None)
        self.assertTrue((root / "dashboard/platforms/youtube_api.py").is_file())
        for rel, text in mine.items():
            self.assertEqual((root / rel).read_text("utf-8"), text, rel)


class NothingSecretShows(ApiTest):
    """Everything this file prints, raises or answers through a connect, uploads that go wrong in every way, a
    sign-in that ends, and a disconnect: none of it carries the client's id or secret, a token, a code or a
    session's address."""

    def test_through_a_whole_life(self):
        said: list[str] = []
        out, err = io.StringIO(), io.StringIO()

        def keep(fn, *args, **kw):
            try:
                said.append(repr(fn(*args, **kw)))
            except (ApiError, Unknown, Stopped, ValueError) as e:
                said.extend([repr(e), str(e), repr(getattr(e, "message", "")), repr(e.__cause__), repr(e.__context__)])

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            consent = self.connect()
            said.append(repr(consent.outcome))
            keep(youtube_api.status)
            keep(youtube_api.main, [])
            keep(self.upload)
            for script, then in (([None, "503", "drop", "401"], None), ([None, None, None, "lost"], "drop"),
                                 ([None, "drop"], "drop"), ([None, "404"], None)):
                self.google.script, self.google.then = list(script), then
                keep(self.upload)
            self.google.then = None
            for start in ((403, "quotaExceeded"), (403, "somethingNew"), "401"):
                self.google.start_script = [start]
                keep(self.upload)
            self.google.session_at = "https://elsewhere.invalid/upload"
            keep(self.upload)
            self.google.session_at = None
            self.google.token_script = ["server_error"]
            youtube_api._access.clear()
            keep(youtube_api.access_token)
            self.google.consent = "other_state"
            said.append(repr(self.connect().outcome))
            self.google.consent = "no"
            said.append(repr(self.connect().outcome))
            self.google.consent = "yes"
            self.google.token_script = ["invalid_grant"]
            said.append(repr(self.connect().outcome))
            secret = [CLIENT_ID, CLIENT_SECRET, consent.state, consent.verifier, *self.google.secrets()]
            self.google.end_grant()
            youtube_api._access.clear()
            keep(youtube_api.access_token)
            keep(youtube_api.status)
            keep(youtube_api.main, [])
            keep(youtube_api.main, ["disconnect"])
        said += [out.getvalue(), err.getvalue(), *(page for _, page in self.shown)]
        self.assertGreater(len(secret), 12)
        text = "\n".join(said)
        for value in secret:
            self.assertNotIn(value, text)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("YouTube refused the upload (somethingNew).", text)  # (it did say things)
        # Nor is any of it written anywhere but the one file, which is gone after the disconnect.
        left = [p for p in self.root.rglob("*") if p.is_file() and p not in (self.client_file, self.video)]
        self.assertEqual([p.name for p in left], ["youtube_api.json.lock"])
        self.assertEqual(left[0].read_bytes(), b"")


class CommandLine(ApiTest):
    """reelforge youtube-api [connect [FILE] | disconnect] (youtube_api.main)."""

    def run_main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = youtube_api.main(list(argv))
        return code, out.getvalue()

    def test_with_no_word_it_says_where_the_connection_stands_and_that_it_is_unproven(self):
        code, said = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("connected  no: reelforge youtube-api connect FILE", said)
        self.assertIn(f"kept in    {self.store}  (nothing yet)", said)
        self.assertIn("never against Google itself", said)
        self.assertIn("docs/YOUTUBE-API.md", said)
        self.connect()
        youtube_api.note_channel({"channel_id": CHANNEL["id"], "channel_title": CHANNEL["title"]})
        code, said = self.run_main()
        self.assertIn("connected  yes, since 20", said)
        self.assertIn("channel    Night Physics (where the last upload went)", said)
        self.assertIn("posts      start as private, made for kids: no", said)
        self.assertNotIn(CLIENT_ID, said)
        self.assertNotIn(self.on_file()["refresh_token"], said)
        self.google.end_grant()
        youtube_api._access.clear()
        with self.assertRaises(ApiError):
            youtube_api.access_token()
        code, said = self.run_main()
        self.assertIn("connected  not any more.", said)
        self.assertIn("reelforge youtube-api connect\n", said)

    def test_connect_opens_the_page_waits_and_says_how_it_ended(self):
        code, said = self.run_main("connect", str(self.client_file))
        self.assertEqual(code, 0)
        self.assertIn("Google's consent page is opening in your browser", said)
        self.assertIn("ReelForge is connected to YouTube's API.", said)
        self.assertIn("Posts start as private", said)
        self.assertIn("never against Google itself", said)
        self.assertNotIn(CLIENT_ID, said)
        self.assertEqual(youtube_api.status()["state"], "on")
        self.google.consent = "no"
        code, said = self.run_main("connect")  # again, with the client on file
        self.assertEqual(code, 1)
        self.assertIn("You said no on Google's page", said)
        code, said = self.run_main("connect", str(self.root / "nowhere.json"))
        self.assertEqual((code, said), (1, f"There is no file at {self.root / 'nowhere.json'}.\n"))

    def test_disconnect_says_what_happened(self):
        self.assertEqual(self.run_main("disconnect"), (0, "YouTube's API wasn't connected.\n"))
        self.connect()
        code, said = self.run_main("disconnect")
        self.assertEqual((code, said), (0, "Disconnected. ReelForge has forgotten the connection, and Google has "
                                           "ended it.\n"))
        self.assertEqual(self.google.grants, {})
        self.connect()
        with mock.patch.object(youtube_api, "REVOKE_URL", "http://127.0.0.1:9/revoke"):
            code, said = self.run_main("disconnect")
        self.assertIn("Google couldn't be told", said)
        self.assertIn("https://myaccount.google.com/connections", said)
        self.assertFalse(self.store.exists())

    def test_no_word_starts_a_post(self):
        self.connect()
        for words in (["post"], ["upload", str(self.video)], ["post", str(self.video), "--public"], ["connect", "a", "b"]):
            code, said = self.run_main(*words)
            self.assertEqual(code, 2, words)
            self.assertIn("None of these posts anything.", said)
        self.assertEqual(self.run_main("help")[0], 0)
        self.assertEqual((self.google.starts, self.google.puts), ([], []))

    def test_the_reelforge_command_knows_it(self):
        import studio.__main__ as studio_main

        self.assertIn("reelforge youtube-api", studio_main.__doc__)
        with mock.patch.object(youtube_api, "main", return_value=7) as main:
            self.assertEqual(studio_main.main(["youtube-api", "disconnect"]), 7)
        main.assert_called_once_with(["disconnect"])
        self.assertIn("Did you mean reelforge youtube-api?", studio_main.unknown("youtube-ap"))


if __name__ == "__main__":
    unittest.main()
