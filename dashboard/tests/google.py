"""A stand-in for Google on this computer, for the tests of posting to YouTube through its API
(dashboard/platforms/youtube_api.py): the consent page, the token and revoke addresses, and YouTube's resumable
upload, answering the way Google documents them. It keeps what it was sent, counts the sessions and the videos it
made, and can be told to misbehave (script, then, refuse...). Nothing here reaches the internet."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CHANNEL = {"id": "UCnightphysics0123456789", "title": "Night Physics"}


class Google:
    """Listening on a free port (base). What it is told to do:

    consent        "yes", "no" (you refuse on the consent page) or "other_state" (the answer that comes back
                   carries a state the person's try didn't make). As at Google, a yes gives a sign-in that lasts
                   only when the page was asked for one (access_type=offline), and for a client that was allowed
                   before only when the page was told to ask again (prompt=consent)
    lasts          seconds the sign-in lasts, if Google is to say so (a consent screen in Testing: 7 days)
    scope          the permission the token answer names (None: it names none)
    no_refresh     the token answer has no refresh token
    token_script   what the next calls to the token address get instead of an answer: "503", or an error word
                   ("invalid_grant")
    start_script   the same for starting an upload: "503", "drop", "401", "redirect" (it points somewhere else on
                   this server: anything that follows is noted in strays), or (status, reason) for a refusal
    script, then   the same for each piece and each "where does it stand": None (the usual answer), "503" (the
                   piece is not kept), "503-kept" (it is, and the answer is still 503), "lost" (the piece is
                   kept, and made into the video if it was the last, but no answer comes), "drop" (no answer,
                   nothing kept), "ignored" (answered as usual, but the piece is not kept), "401", "404", or
                   (status, reason) for a refusal. then: what every one gets once the script has run out.
    stores_as      who YouTube lets see every video, whatever was asked ("private": what it used to do to
                   uploads from an unverified project)
    channel_kids   the channel's own audience setting: whether a video uploaded with no made-for-kids answer is
                   treated as made for kids
    upload_status  the uploadStatus of the videos it makes ("uploaded" unless a test says otherwise)
    session_at     where the session's address points (None: here)
    on_put         called with how many pieces and questions came before, as each one arrives
    hold           an Event each piece waits for before it is answered (an upload that takes its time)
    """

    def __init__(self) -> None:
        self.consent, self.lasts, self.scope, self.no_refresh = "yes", None, SCOPE, False
        self.token_script: list = []
        self.start_script: list = []
        self.script: list = []
        self.then = None
        self.stores_as: str | None = None
        self.channel_kids = False
        self.upload_status = "uploaded"
        self.session_at: str | None = None
        self.on_put = None
        self.hold: threading.Event | None = None
        # What it was sent, and what it made.
        self.asked: list[dict] = []          # each consent page opened: its query
        self.token_calls: list[dict] = []    # each call to the token address: its form
        self.revoke_calls: list[str] = []
        self.strays: list[tuple] = []        # anything asked of an address that isn't one of Google's here
        self.starts: list[dict] = []         # each upload started: {"query", "headers", "body"}
        self.puts: list[dict] = []           # each piece or question: {"range", "length", "bearer"}
        self.codes: dict[str, dict] = {}
        self.allowed: set[str] = set()       # the clients somebody said yes to before
        self.grants: dict[str, str] = {}     # refresh token -> the client it was given to
        self.access: set[str] = set()        # the access tokens that are good
        self.sessions: dict[str, dict] = {}  # id -> {"size", "data", "body", "video"}
        self.videos: list[dict] = []
        self.lock = threading.Lock()
        self.httpd = _Server(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.google = self
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def secrets(self) -> list[str]:
        """Everything it handed out that must never show anywhere: the codes, the tokens, the sessions' ids."""
        return [*self.codes, *self.grants, *self.access, *self.sessions]

    def expire_access(self) -> None:
        """Every access token it gave stops working, as after an hour."""
        self.access.clear()

    def end_grant(self) -> None:
        """Every sign-in it gave is over: revoked in the Google Account, or run out after its seven days."""
        self.grants.clear()
        self.access.clear()

    def video(self, session: dict) -> dict:
        body, asked = session["body"], session["body"].get("status", {})
        declared = asked.get("selfDeclaredMadeForKids")
        said = isinstance(declared, bool)
        return {"kind": "youtube#video", "id": session["video"],
                "snippet": {"channelId": CHANNEL["id"], "channelTitle": CHANNEL["title"],
                            "title": body.get("snippet", {}).get("title"),
                            "description": body.get("snippet", {}).get("description"),
                            "categoryId": body.get("snippet", {}).get("categoryId")},
                "status": {"uploadStatus": self.upload_status,
                           **({"rejectionReason": "duplicate"} if self.upload_status == "rejected" else {}),
                           "privacyStatus": self.stores_as or asked.get("privacyStatus", "public"),
                           **({"selfDeclaredMadeForKids": declared} if said else {}),
                           "madeForKids": declared if said else self.channel_kids}}


def refusal(status: int, reason: str) -> tuple[int, dict]:
    """A refusal as Google's APIs word one."""
    return status, {"error": {"code": status, "message": f"The request was refused: {reason}.",
                              "errors": [{"message": f"Refused: {reason}.", "domain": "youtube.video",
                                          "reason": reason}]}}


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer's own looks the computer's name up first, which nothing here uses. On a Mac that lookup can
        # take half a minute, once in every process: each window test starts one (ui/test/youtube_api_preload.py).
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args) -> None:
        pass

    @property
    def google(self) -> Google:
        return self.server.google

    def reply(self, status: int, data: dict | None = None, headers: dict | None = None) -> None:
        body = b"" if data is None else json.dumps(data).encode("utf-8")
        self.send_response(status)
        for name, value in {"Content-Type": "application/json; charset=UTF-8", "Content-Length": str(len(body)),
                            **(headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def drop(self) -> None:
        """No answer at all: the connection just ends."""
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def bearer(self) -> str:
        return (self.headers.get("Authorization") or "").removeprefix("Bearer ")

    def do_GET(self) -> None:
        g, parts = self.google, urlsplit(self.path)
        if parts.path != "/auth":
            g.strays.append(("GET", parts.path, self.bearer()))
            self.reply(404, {})
            return
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        g.asked.append(query)
        back = {"state": query.get("state", "")}
        if g.consent == "no":
            back["error"] = "access_denied"
        else:
            code = "code-" + secrets.token_urlsafe(24)
            lasting = query.get("access_type") == "offline" and (
                query.get("prompt") == "consent" or query.get("client_id") not in g.allowed)
            g.allowed.add(query.get("client_id"))
            g.codes[code] = {"challenge": query.get("code_challenge"), "redirect_uri": query.get("redirect_uri"),
                             "client_id": query.get("client_id"), "used": False, "lasting": lasting}
            back.update(code=code, scope=query.get("scope", ""))
        if g.consent == "other_state":
            back["state"] = "somebody-elses-state"
        self.send_response(302)
        self.send_header("Location", f"{query.get('redirect_uri')}/?{urlencode(back)}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        g, parts = self.google, urlsplit(self.path)
        raw = self.body()
        if parts.path == "/token":
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8")).items()}
            g.token_calls.append(form)
            self.token(form)
        elif parts.path == "/revoke":
            token = parse_qs(raw.decode("utf-8")).get("token", [""])[0]
            g.revoke_calls.append(token)
            if token in g.grants:
                del g.grants[token]
                g.access.clear()
                self.reply(200, {})
            else:
                self.reply(400, {"error": "invalid_token"})
        elif parts.path == "/upload/youtube/v3/videos":
            self.start(parse_qs(parts.query), raw)
        else:
            g.strays.append(("POST", parts.path, self.bearer()))
            self.reply(404, {})

    def token(self, form: dict) -> None:
        g = self.google
        said = g.token_script.pop(0) if g.token_script else None
        if said == "503":
            self.reply(503, {})
            return
        if said:
            self.reply(401 if said == "invalid_client" else 400, {"error": said, "error_description": "No."})
            return
        if form.get("grant_type") == "authorization_code":
            code = g.codes.get(form.get("code", ""))
            proof = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
            if (code is None or code["used"] or code["redirect_uri"] != form.get("redirect_uri")
                    or code["client_id"] != form.get("client_id") or not form.get("client_secret")
                    or proof.rstrip(b"=").decode() != code["challenge"]):
                self.reply(400, {"error": "invalid_grant", "error_description": "Bad Request"})
                return
            code["used"] = True
            refresh = "refresh-" + secrets.token_urlsafe(24)
            lasting = code["lasting"] and not g.no_refresh
            if lasting:
                g.grants[refresh] = form["client_id"]
        elif form.get("grant_type") == "refresh_token":
            refresh = form.get("refresh_token", "")
            if g.grants.get(refresh) != form.get("client_id") or not form.get("client_secret"):
                self.reply(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
                return
        else:
            self.reply(400, {"error": "unsupported_grant_type"})
            return
        access = "access-" + secrets.token_urlsafe(24)
        g.access.add(access)
        answer = {"access_token": access, "expires_in": 3599, "token_type": "Bearer"}
        if g.scope:
            answer["scope"] = g.scope
        if form["grant_type"] == "authorization_code":
            if lasting:
                answer["refresh_token"] = refresh
            if g.lasts:
                answer["refresh_token_expires_in"] = g.lasts
        self.reply(200, answer)

    def start(self, query: dict, raw: bytes) -> None:
        g = self.google
        g.starts.append({"query": {k: v[0] for k, v in query.items()}, "body": json.loads(raw or b"{}"),
                         "headers": {k.lower(): v for k, v in self.headers.items()}, "bearer": self.bearer()})
        said = g.start_script.pop(0) if g.start_script else None
        if said == "drop":
            self.drop()
        elif said == "503":
            self.reply(503, {})
        elif said == "redirect":
            self.reply(307, None, {"Location": f"{g.base}/somewhere/else"})
        elif said == "401" or (said is None and self.bearer() not in g.access):
            self.reply(*refusal(401, "authError"))
        elif said:
            self.reply(*refusal(*said))
        else:
            sid = "session-" + secrets.token_urlsafe(24)
            g.sessions[sid] = {"size": int(self.headers["X-Upload-Content-Length"]), "data": bytearray(),
                               "body": g.starts[-1]["body"], "video": None}
            where = g.session_at or f"{g.base}/upload/youtube/v3/videos"
            self.reply(200, None, {"Location": f"{where}?uploadType=resumable&upload_id={sid}"})

    def do_PUT(self) -> None:
        g, parts = self.google, urlsplit(self.path)
        piece = self.body()
        sid = parse_qs(parts.query).get("upload_id", [""])[0]
        if g.on_put:
            g.on_put(len(g.puts))
        g.puts.append({"session": sid, "range": self.headers.get("Content-Range"), "length": len(piece),
                       "bearer": self.bearer()})
        if g.hold is not None:
            g.hold.wait(20)
        with g.lock:
            said = g.script.pop(0) if g.script else g.then
            session = g.sessions.get(sid)
            if said == "drop":
                self.drop()
                return
            if said in ("503", "404", "401"):
                self.reply(*refusal(int(said), {"503": "backendError", "404": "notFound", "401": "authError"}[said]))
                return
            if isinstance(said, tuple):
                self.reply(*refusal(*said))
                return
            if session is None:
                self.reply(*refusal(404, "notFound"))
                return
            if self.bearer() not in g.access:
                self.reply(*refusal(401, "authError"))
                return
            status, data, headers = self.take(session, b"" if said == "ignored" else piece, said == "ignored")
            if said == "503-kept":
                self.reply(503, {})
            elif said == "lost":
                self.drop()
            else:
                self.reply(status, data, headers)

    def take(self, session: dict, piece: bytes, ignore: bool = False) -> tuple[int, dict | None, dict]:
        """Keep a piece (or answer where the session stands) the way the resumable protocol says."""
        g = self.google
        given = self.headers.get("Content-Range") or ""
        sent = None if ignore else re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", given)
        if ignore:
            pass
        elif sent:
            first, final, size = (int(n) for n in sent.groups())
            if size != session["size"] or final - first + 1 != len(piece) or first > len(session["data"]):
                return *refusal(400, "invalidRange"), {}
            if not session["video"]:
                session["data"][first:] = piece
        elif not re.fullmatch(r"bytes \*/(\d+)", given) or piece:
            return *refusal(400, "invalidRange"), {}
        if len(session["data"]) == session["size"] and not session["video"]:
            session["video"] = f"vid{len(g.videos) + 1:08d}"  # 11 characters, as a video's id has
            g.videos.append(session)
        if session["video"]:
            return 200, g.video(session), {}
        have = len(session["data"])
        return 308, None, ({"Range": f"bytes=0-{have - 1}"} if have else {})
