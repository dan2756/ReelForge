# The browser extension

ReelForge can fill in each platform's upload page for you. You pick a video on the Publish tab and press Post. Your
browser opens the upload page, the extension attaches the video and types the caption and hashtags, and then it
stops. You look it over and press the platform's own Post button.

It is optional. ReelForge opens, updates and works the same without it, and the buttons that open an upload page
for you to post by hand stay as they are.

**Where this stands:** it is tested against copies of the upload pages, not yet on the live sites. Check each post
before you press Post. The last section of this page says exactly what has been tested and how.

## What it is, and what it is not

- It is a small extension for Chrome, Edge and Brave (version 121 or newer) that you load from the `extension/`
  folder in your ReelForge folder. It is not in any store.
- It works in your everyday browser, with the accounts you are signed in to there. ReelForge never sees a password
  or a cookie. The extension asks for neither.
- It does one thing on a platform's site: fill in the upload page of a post you started. It does nothing on those
  sites at any other time. On reelforge.tools it answers whether it is installed, and that is all. It touches no
  other site.
- It is not a bot. A post starts only when you press Post, in ReelForge's window or in the extension's own popup.
  Your AI cannot start one: there is no command, script or pipeline step that does.
- It is not an official integration. It uses the same upload pages you would use by hand, so when a platform
  changes its page, the extension can lose its way until ReelForge is updated. It then says so and leaves you to
  finish by hand.

None of the four platforms' terms has a rule that allows a tool to fill in its upload page for you. X's developer
guidelines go further: they name browser automation as grounds for permanent suspension. That is why the extension
stops before the Post button unless you say otherwise, and why it never presses Post on X. If you would rather not
use it on a platform at all, use that platform's by-hand button.

## Set it up

1. Open your browser's extensions page: type `chrome://extensions` in the address bar (`edge://extensions` in Edge,
   `brave://extensions` in Brave).
2. Switch on **Developer mode**, at the top right.
3. Press **Load unpacked** and pick the `extension` folder inside your ReelForge folder. `reelforge extension folder`
   prints where it is and shows it in your file manager.
4. Connect it: in ReelForge, go to Publish and connect your browser, or run `reelforge extension pair`. A ReelForge
   page opens in your browser and the extension connects there by itself. The page works once, for ten minutes.

`reelforge extension` says where things stand: the folder, the extension's id, and the browsers that are connected.
`reelforge doctor` has a row for it too.

Things to know:

- **One browser at a time is enough.** You can connect more than one (Chrome and Edge, say). Each is listed, and
  each can be disconnected in ReelForge.
- **The pair page opens in your default browser.** If the extension is in another one, copy the page's address and
  open it there. The page says so too.
- **After an update of ReelForge**, press Reload on the extension's card on the extensions page, so the browser
  runs the new version. If the two fall too far apart, ReelForge says which side to update.
- **If you move the ReelForge folder**, load the extension again from its new place. It keeps its id. You may have
  to connect it again.
- **A private window** runs no extensions unless you allow it there.
- **ReelForge has to be open** while a post is under way: the extension gets the video from it.

## Two ways to post

| | Attach and fill (the default) | Press Post for me |
|---|---|---|
| Ends | with everything filled in and the platform's Post button waiting for you | when the extension has pressed that button |
| Who presses Post | you | the extension, once |
| Where | TikTok, YouTube, Instagram, X | TikTok, YouTube, Instagram. Never X |
| Switched on | nothing to switch on | in the extension's own settings, for each platform, and only for a post you started with "stop before posting" switched off |

Press Post for me is off until you switch it on, and it cannot be switched on from ReelForge's folder: the choice is
kept inside the browser, where your AI cannot reach it. It is offered for a platform only after one post there that
the extension filled in, you pressed, and ReelForge saw go up. ReelForge allows the press once for each post. If it
cannot confirm that the post went up, it marks the post "not sure it posted" and never tries again by itself: you
check your profile and say what happened.

## What is automatic, and what never is

Automatic, once you have pressed Post in ReelForge or in the popup:

- opening one tab for each platform you ticked
- checking that the browser is signed in there, and to the account ReelForge has for that platform
- attaching the video
- typing the title, caption and hashtags, exactly as ReelForge showed them to you when you confirmed
- saying where each post stands, in ReelForge's window and on the page that opened

Never automatic:

- anything on X beyond attaching the video and typing the post
- the "made for kids" answer, the altered-content disclosure and who can see the post, unless the page already
  shows your channel's saved choice
- a captcha, or any "prove it's you" step: the extension stops and says so
- a second try of a post that may have gone up
- closing the tab: it stays open so you can see the result

## How a post goes

1. You press Post. ReelForge makes one job for each platform, with the exact text, and opens a local page
   (`http://127.0.0.1:<port>/post/...`) in your browser.
2. The extension sees that page, takes the jobs and opens the upload pages. The local page shows a row for each
   platform: queued, opening, uploading, filling in, ready, posted, or failed with the reason.
3. At **ready**, you check the post and press the platform's button. When the page shows that it went up, the row
   says posted.

The Publish tab shows the same states. A video can have one post under way for each platform, and a video that is
up somewhere is not posted there again unless you ask for it by name. A video is known by its content, not its
file name, so a renamed copy counts as the same video.

If the extension is not in the browser that opened the page, the page says so, shows the set-up steps, and has a
**Post by hand** button for each platform. That button does what the buttons on the Publish tab do: it copies the
text, shows the file and opens the upload page.

A video that came from the pipeline is posted this way only when QA passed on that exact file and the review says
APPROVED for that cut. Posting by hand is not held back by this.

## What keeps it safe

The extension talks to ReelForge through a small server that ReelForge runs on your computer while its window is
open (`dashboard/extension_api.py`).

- **This computer only.** The server listens on `127.0.0.1`, on the first free port from 47821 to 47830. Nothing
  outside your computer can reach it.
- **Its own name only.** A request is refused unless it is addressed to exactly `127.0.0.1` and that port. This
  stops a web page from reaching it under a made-up name.
- **No web pages.** A request that comes from a web page is refused, whatever it asks for. The browser says where a
  request comes from, and only the extension's own id (and ReelForge's own two pages) is let in. No answer ever
  says "any site may read this".
- **Pairing.** A browser is let in once, on the pair page. The page carries a secret that works one time, for ten
  minutes. The extension trades it for a token.
- **The token.** Every call after that carries the token. ReelForge keeps only its SHA-256, in
  `dashboard/data/extension.json`, a file only you can read. Disconnecting a browser ends its token at once.
- **Little to ask for.** The extension can see the Publish list, take a post you started, fetch that post's video,
  and report on it. A video is asked for by its job, never by a path. None of the window's own actions (removing a
  video, resetting, deleting a profile) can be reached this way.
- **The two local pages** carry a key in their address that is good for two hours and for that one page. They load
  nothing from the internet.
- **The demo** answers "this is the demo" and nothing else.

What reelforge.tools can ask the extension: whether it is installed, its version, and whether ReelForge is open and
connected. It gets no captions, no file names and no channel names, and it cannot start anything.

What this does not protect against: a program already running as you on this computer. It can read your videos and
your ReelForge folder directly, with or without the extension.

## Why not the platforms' own posting APIs

Each platform has an API for posting. This table says what stands between a single creator and using it. It only
repeats what was checked for this project, and says how: **read** means the platform's own published source was
read directly, **excerpt** means only a search excerpt of the platform's own page could be read, which is good
enough to plan on and not good enough to quote as fact.

| Platform | What its API would need | What stands in the way |
|---|---|---|
| YouTube | Your own Google Cloud project and a desktop sign-in. The upload call and its permissions are known (read). | Little. Uploads from your own unreviewed project are no longer locked to private, by the API's current description (read), but this changed in late 2026 and has not been confirmed with a real upload. About 100 uploads a day (excerpt). Sign-in lasts 7 days while the project is in testing (excerpt). This is the one platform where an official way to post is practical, and it is planned as a second way to post to YouTube. |
| TikTok | A developer app, for which TikTok asks for website addresses you have verified (excerpt). | Posts from an app TikTok has not audited are private only, on a private account (excerpt). TikTok lists a tool for uploading to your own account as something it does not approve (excerpt). So there is no official way for one creator to post publicly. |
| Instagram | A professional account and a Meta developer app. No review is needed for your own account (excerpt). | A local file can only be sent on the path that needs a linked Facebook Page (the upload itself: read, in Meta's own sample; that it is limited to that path: excerpt). Otherwise the video has to sit at a public address (excerpt). There is no draft or private mode to try it with (excerpt). |
| X | A developer app and paid credits: $0.015 a post, $0.20 if the post has a link (read). | It costs money for every post. Its sign-in changes on 31 January 2027, when the older kind stops working (read). X's developer guidelines forbid automating its website (read), which is why the extension never presses Post there. |

So TikTok, Instagram and X go through the extension. YouTube can too.

## When something goes wrong

The row for a post says what happened. These are the reasons the extension gives, and what to do.

| It says | What happened | What to do |
|---|---|---|
| `extension_outdated` | The extension is older than your ReelForge | Press Reload on its card on the extensions page, then post again |
| `app_unreachable` | The extension could not reach ReelForge | Open ReelForge, then post again |
| `not_signed_in` | That browser is not signed in to the platform | Sign in there, in that browser, then post again. ReelForge's own log-in on the Accounts tab is a different browser and does not count |
| `wrong_account` | That browser is signed in to another account | Switch accounts there, then post again |
| `hook_not_found` | The platform changed its page and the extension lost its way | Finish by hand in the open tab, or use Post by hand. Update ReelForge when you can |
| `challenge` | The platform is asking you to prove it's you | Do what it asks in the tab. Then post again, or finish by hand there |
| `platform_error` | The platform showed an error of its own | Read it in the tab. You can finish by hand there |
| `timeout` | Nothing happened in time | The tab is still open. Finish by hand there |
| `tab_closed` | The tab was closed before the post went up | Post again when you are ready |
| `press_rejected` | The platform did not take the extension's press | Press Post yourself in the tab |
| `changed` | The video file was replaced after you pressed Post in ReelForge | Check the video in ReelForge, then post again |
| `unknown_outcome` | ReelForge cannot tell whether it posted | Check your profile on the platform. Then say on the Publish tab whether it posted |

Two states are worth knowing:

- **The extension didn't pick it up.** A post that no browser takes within ten minutes expires. Nothing happened.
  Start it again.
- **Not sure it posted.** Post was pressed, or was about to be, and no confirmation came. ReelForge never tries
  such a post again. It settles when you say what happened, or when a stats read finds the post.

You can always finish by hand: in the tab the extension opened, or with a by-hand button.

## Commands

```
reelforge extension            where the extension stands: its folder, its id, the browsers connected
reelforge extension folder     print its folder and show it in the file manager
reelforge extension pair       connect a browser: opens the pair page and waits until the extension has connected
reelforge extension serve      listen for the extension without ReelForge's window, until Ctrl-C
```

None of these starts a post.

## For developers

The server is `dashboard/extension_api.py`; its opening comment lists who gets in and why. The ledger of posts is
`dashboard/posting.py`. The two local pages are in `dashboard/web/`. The window's side is `dashboard/posting_tab.py`.

Calls are JSON under `/v1`. `hello` and `pair/claim` need no token; the rest need the `X-ReelForge-Token` header.

| Call | What it does |
|---|---|
| `GET /v1/hello` | who is here: the app, its version, its channel, whether your token is good |
| `POST /v1/pair/claim` | trade a pair page's secret for a token (the extension only) |
| `POST /v1/seen` | the extension's hello, with what it last saw signed in |
| `GET /v1/state` | the channel, its platforms, the newest videos and where each stands |
| `POST /v1/batches` | Post pressed in the popup: jobs for a video, with its saved text |
| `GET /v1/batch/<batch>` | the jobs of one press of Post |
| `POST /v1/jobs/<id>/claim` | take a job. The first browser to ask has it |
| `GET /v1/jobs/<id>/file` | the job's video, whole or a `Range` of it |
| `POST /v1/jobs/<id>/event` | report a step, a failure or the post |
| `POST /v1/jobs/<id>/press` | ask to press Post. Granted once, where it is allowed |
| `GET /v1/selectors` | `extension/selectors.json`, the list of what to look for on each upload page |
| `POST /v1/unpair` | disconnect this browser |
| `GET /pair`, `GET /post/<batch>` | the two local pages (they need their key) |
| `GET /v1/page/pair`, `GET /v1/page/batch/<batch>`, `POST /v1/page/manual` | what those pages ask |

A refusal is `{"error": "<code>", "message": "<a plain sentence>"}` with a 4xx status: `wrong_host` (421),
`wrong_origin` (403), `unpaired` (401), `demo` (403), `bad_key` (403), `too_large` (413), `not_json` (415),
`bad_json` (400), `protocol` (426), `not_found` (404), and `409` for a job that cannot do what was asked
(`already_claimed`, `not_claimable`, `not_yours`, `bad_transition`, `changed`, `ended`, `not_ready`,
`already_pressed`, `press_not_allowed`).

In tests, `REELFORGE_EXTENSION_PORT` picks the port (`0`: any free one, `off`: none). With `STUDIO_DRY_SYSTEM=1`
and no port given, ReelForge does not listen at all, so a test run never sits on the ports your own ReelForge and
browser use.

## What has and hasn't been verified

Tested here, by automated tests that run with ReelForge's own (`dashboard/tests/test_extension_api.py`,
`test_posting.py`, `test_posting_tab.py`):

- every rule in "What keeps it safe": the address, where a request comes from, the token, pairing once and its ten
  minutes, two browsers asking at once, the size and kind of a request, the demo, and that none of the window's own
  actions can be reached
- every call in the table above, against a real server on a free port, with the test playing the extension
- the video route: whole, in parts, a part that does not exist, a video replaced after the post was started
- the ledger: every state a post can be in, what refuses a second post, the one press of Post
- that the extension's folder goes into the downloads and is replaced by an update, and that the file of connected
  browsers survives an update, a move and goes to the backup on a factory reset

Looked at, not automated:

- the two local pages, in headless Chromium at 1100 and 360 pixels wide: no extension, working, ended, by hand,
  ReelForge closed, connected, and a pairing that failed. They made no request to anywhere but ReelForge.
- the extension itself with this server, in Chromium, by hand. It connected through the pair page and was handed a
  batch for all four platforms. With the extension's own copies of the upload pages served under the platforms'
  addresses, it took each job, fetched the video from ReelForge, attached it, filled in the text and stopped at
  ready, and the local page followed every step. With no pages to reach at all, every post ended as failed and
  said why, on the page and in the extension's popup. These were runs by hand: the extension's own tests use a
  stand-in for this server, and the server's tests a stand-in for the extension.

Not verified:

- **Anything on a live platform page.** No upload page was loaded and no account was used. Whether today's TikTok,
  YouTube, Instagram and X pages accept a video attached this way, where their boxes and buttons are, whether a
  scripted press of Post works, and how each shows that a post went up: all of that is only known from copies of
  those pages.
- Chrome, Edge and Brave as you install them. The runs here used Chromium.
- The server on Windows and macOS. Its tests ran on Linux here.
- A real video's size and time through the extension.
- The API facts marked "excerpt" in the table above, and whether YouTube's change for unreviewed projects holds for
  a real upload.
