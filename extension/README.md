# The ReelForge browser extension

Posting a finished video by hand is four upload pages, four file pickers and a lot of pasting. This extension does
that part in your own browser, where you are already signed in: it opens each platform's upload page, hands it the
video, fills in exactly the text ReelForge shows you, and stops. You look at the page and press Post.

It needs the ReelForge app running on the same computer. On its own it does nothing.

**How far it has been tested.** Against copies of the four upload pages, built from the page hooks other open-source
uploaders rely on. Not yet on the live sites: that needs real signed-in accounts, and the list of what to check is
[at the end](#what-still-has-to-be-checked-with-real-accounts). If a live page differs from the copy, the
extension stops and says which part it could not find, and you finish by hand in the same tab.

## Set it up

It works in Chrome, Edge and Brave, version 121 or newer.

1. Open your browser's extensions page: `chrome://extensions` (Edge: `edge://extensions`, Brave:
   `brave://extensions`).
2. Switch on **Developer mode** (top right in Chrome and Brave, bottom left in Edge).
3. Click **Load unpacked** and choose the `extension` folder inside your ReelForge folder.
4. Pin **ReelForge** to the toolbar (the puzzle-piece button), so its window is one click away.
5. Connect it: ReelForge opens a pairing page in this browser, and the extension pairs with it by itself. Its
   window then shows your channel's name with a green dot.

After ReelForge updates, press **Reload** on the extension's card on that same page. The browser keeps running the
old copy until you do. Your browser may also remind you that a developer-mode extension is loaded. That is this one.

One browser profile is one connection. If you keep each channel's accounts in a separate browser profile, load and
pair the extension in each.

## What a post looks like

You start it: **Post** in ReelForge's Publish tab, or **Post** in the extension's window. In the extension's window
you first choose the platforms, then see the exact text and the account for each one. Nothing opens before that.

For each platform the extension then:

1. opens one tab on the platform's own upload page (the first one comes to the front, the rest load behind it);
2. checks this browser is signed in there, and where the page shows the account (Instagram and X), that it is the
   one ReelForge has for the channel. A different account is a question in the extension's window (**Post as
   @other** or **Stop**), never a silent stop and never a silent go. TikTok's and YouTube's upload pages do not
   show their account, so there the extension's window and the note on the page tell you to look yourself;
3. hands the page the video. The bytes come from ReelForge through the extension, and the page gets them only if
   their size and SHA-256 match what you confirmed;
4. fills in the text, in one go, and reads it back;
5. scrolls the platform's final button into view, rings it in red and leaves a note in the corner: **Check it, then
   press Post**. With Press Post for me (below), while the extension asks ReelForge for the press, the note says
   **Pressing Post for you** instead, and the button is brought into view but ringed only if the extension does
   not press it after all.
   Where the page does not show its account, every one of these notes says to look which account the tab is on.

Then it waits. When the platform shows its own sign that the post went out, ReelForge is told. The tab is left
open.

Keep the tab and the browser open until then. If a tab is closed, or the whole browser, ReelForge is told what
state the post was in (the next time the browser opens, if need be), and nothing is ever picked up again by
itself.

It never touches "Made for kids", the altered-content question or who can see the video. On YouTube, if "Made for
kids" or the visibility has no saved answer, it stops at that screen and says what is left. It never acts on a
captcha or a verification: it stops and tells you.

ReelForge's own log-ins (its Accounts tab) live in ReelForge's private browser. They say nothing about this browser.
What counts here is whether this browser is signed in.

## Press Post for me

Off by default. The switches are stored only in the extension. ReelForge never reads or sets them, and they are
not kept in its folder.

The platforms' terms restrict automated posting, and nobody can promise an account is safe when a program presses
the button. Pressing Post yourself is the safer choice.

If you still want it, it takes all of these:

- you switch it on for that platform on the extension's settings page;
- ReelForge has already seen one post to that platform go through with you pressing (until then the switch is
  locked and says why);
- you switch "stop before posting" off for that one post;
- you say yes to it for that post, in this browser, with your own click: the extension's own button when you start
  the post in its window, or **Yes, press Post for me** on the page ReelForge opens when you start it there. The
  extension listens for that click itself, and a click made by a script does not count. A post page that was only
  opened presses nothing;
- ReelForge grants the press, once per post. The extension asks once and never again, even when the answer gets
  lost on the way: then it presses only if ReelForge's record of the post says the press was given, and otherwise
  clicks nothing and says so in its note;
- you have not pressed **Stop** in the popup while ReelForge was being asked, or just before. A Stop then wins:
  nothing is clicked, Post is not ringed, and the note on the page says **Stopped** at once, before ReelForge has
  taken the stop. **Cancel** on the post's row in ReelForge then works the same way: ReelForge says no, and the note
  says the post was ended in ReelForge, even when ReelForge is closed straight after (its no says how it has the
  post). If an older ReelForge says no without that and can't be reached afterwards, Post is not ringed, and the
  note says not to press it if you cancelled the post;
- you have not pressed the button yourself already. Your own press, even one made while the video is still going
  up or while ReelForge is being asked for the press, is the post's press: the extension then clicks nothing, and
  ReelForge would refuse another.

It is never offered for X. X's rules name scripting its website and suspend accounts for it.

After the press the extension waits for the platform's own confirmation. If the page shows no reaction, it says so
in its note and tells you to look: press the button yourself if the post has not gone out, and the platform's
confirmation is how ReelForge hears of it. If no confirmation comes, the post is marked "not sure" and ReelForge
asks you whether it posted. It is never marked failed after a press, because a failed post is offered again.
Nothing is ever pressed, attached or tried a second time.

## What it may do, and why

| Permission | Why |
|---|---|
| `storage` | Its pairing token, your "Press Post for me" switches, and the state of posts in progress. All of it stays in this browser, where only the extension's own pages and worker can read it. Nothing uses the browser's sync storage. |
| `alarms` | A check every 30 seconds while a post is under way (it picks a post back up after the browser restarts its background worker), and one every 5 minutes otherwise, so ReelForge can say this browser is connected. No alarm at all before the first pairing. |
| `scripting` | Putting the platform script into a tab, and only into a tab the extension itself opened for a post you started, while that tab is on that post's own platform. |
| `http://127.0.0.1/*` | Talking to ReelForge on this computer (ports 47821 to 47830) and running on its two local pages, `/pair` and `/post/…`. Not `localhost`, and no other local page. |
| `https://www.tiktok.com/*`, `https://www.youtube.com/*`, `https://studio.youtube.com/*`, `https://www.instagram.com/*`, `https://x.com/*` | The four upload pages. YouTube's upload address redirects into Studio, so it needs both. |
| `https://reelforge.tools/*` | The website can ask "is the extension installed?" and "is the app reachable and paired?". It gets a version number and three yes-or-no facts. It cannot start anything and never sees a caption, a file name or a channel name. |

It asks for no `tabs`, `cookies`, `debugger`, `webRequest` or all-sites access. It loads no code from anywhere and
has no analytics. It talks to ReelForge on this computer and to the platform page you are posting to, and to
nobody else. Its token goes only to a port that has just proved to be the ReelForge this browser connected to: a
program that merely answers on one of ReelForge's ports is told nothing and believed in nothing.

It is loaded from your ReelForge folder, and that is the limit of all of this. A program that can change the files
in that folder, or that runs as you, can change what the extension does. `docs/EXTENSION.md` says so under "What
keeps it safe".

On a platform's site it does nothing at all unless a post you started opened that tab. With no post under way, your
other tabs do not even wake it.

## When a post stops

Every stop has a reason, shown in the extension's window, in the note on the page and in ReelForge. The tab stays
open, and what was already attached or filled in stays there.

| Reason | What happened |
|---|---|
| `not_signed_in` | This browser is not signed in to the platform, or the platform sent the tab to its sign-in page. |
| `wrong_account` | The browser is signed in as someone else and you chose Stop (or did not answer in 15 minutes). |
| `hook_not_found` | The platform changed its page. The message names the part that could not be found. |
| `challenge` | A captcha or a verification. Finish it yourself. |
| `platform_error` | The page showed an error of its own. Its words are passed on. |
| `changed` | The video file is no longer the one you confirmed. |
| `tab_closed` | The tab was closed, or left the platform, before Post was pressed. Nothing was posted. When that happened while ReelForge was being asked for the press, before its answer came back, the words say only that Post was not pressed: ReelForge may have said yes, and then it keeps the post as "not sure", with these words. |
| `timeout` | The page stopped answering. |
| `app_unreachable` | ReelForge was closed before the video was handed over. |
| `press_rejected` | Press Post for me pressed, and the page showed no reaction and no sign that it posted. The post is "not sure", like the next row, not failed. |
| `unknown_outcome` | Post was pressed, or may have been, and no confirmation was seen. Also when the platform shows its sign that a post went out and no press was seen, and when the upload form closed before the extension had finished. Check your profile, then tell ReelForge whether it posted, on its Publish tab. Nothing is posted again until you have. |
| `extension_outdated` | ReelForge is newer than this copy of the extension. Press Reload on its card. |

A stopped post has **Finish by hand** in the extension's window: the text with a Copy button, and the platform's
upload page. ReelForge's Publish tab shows where the video file is.

## The page hooks: `selectors.json`

Each platform's page is found through named hooks in [`selectors.json`](selectors.json). A hook is an ordered list
of ways to find one element:

1. a stable attribute or id (`data-testid`, `data-e2e`, `#next-button`, `name="VIDEO_MADE_FOR_KIDS_NOT_MFK"`);
2. a role and an accessible name (`"role": "button", "name": ["Post"]`);
3. a place relative to another hook (`"within"` looks inside that hook's element, `"near"` climbs from it);
4. visible text.

All ways are tried on each look, best first. The best one wins at once; a weaker one that matches is held back a
moment, in case the page is still arriving. A way that matches two elements does not count, except in a hook marked
`"any": true`, which only asks "is this on the page?". Open shadow roots and same-origin frames are searched. The
way that worked is reported with the step, so "the first choice stopped matching" shows up in ReelForge before
anything breaks.

`signed_in`, `signed_out`, `challenge` and `success` are page-level signs: part of the address, a hook, or both.
`gone_after_press` names the form whose closing, after the final button was pressed, also counts as posted.

The file is data, and the steps live in `platform/*.js`. Where the code looks decides what it clicks, though, so two
rules are written in code. A click on the way to the last screen is refused when its element is the platform's
final button, sits around or inside it, or carries its words (`rf.click` in `platform/engine.js`). And these are
always taken from the copy packed with the extension: the hooks whose element gets clicked and the final button's
hook (`PACKED_HOOKS` in `shared/consts.js`); the `success`, `challenge` and `signed_out` signs with every hook they
name; the hooks whose reading decides whether a post goes on (`GUARD_HOOKS`: on every platform the captcha, which
the challenge sign names, the platform's own error message, the account, the link a post's id is read from and the
question after the press; and YouTube's audience and visibility answers, its upload's progress and end); and every
hook one of those looks inside or near (Instagram's Share is found inside its dialog, so the dialog is kept too). A
newer list can only change how long those are waited for, and it cannot add one of the engine's hooks where the
packed list has none. What it may change are the hooks that are only typed into or given the video (the file input
among them), and a few steps on the way, which a wrong list can only stop. `tests/manifest.test.mjs` reads the
scripts (every `rf.find`, `rf.probe`, `rf.read` and `rf.sel.hooks` of a hook by name) and fails when a hook they read
before the press is neither kept nor named there as one a newer list may change.

To fix a hook, edit the file and raise `"version"`. ReelForge serves its own copy to the extension, and for every
other hook a newer one is used at once, without a Reload. A fix to one of the kept hooks or signs takes a Reload. The role and
text ways are in English, plus the Chinese labels the sources list for Instagram; the attribute and id ways do not
depend on language.

Where the hooks come from, by the date of the file they were read in: YouTube Studio from
[fawazahmed0/youtube-uploader](https://github.com/fawazahmed0/youtube-uploader) (2026-01) and
[dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload) (2026-06); TikTok from
[wkaisertexas/tiktok-uploader](https://github.com/wkaisertexas/tiktok-uploader) (2026-02),
[haziq-exe/TikTokAutoUploader](https://github.com/haziq-exe/TikTokAutoUploader) (2026-06), dreammis (2025-11) and
[leaperone/MultiPost-Extension](https://github.com/leaperone/MultiPost-Extension) (2026-08); Instagram from dreammis
(2026-10) and MultiPost (2026-05); X from [JimLiu/baoyu-skills](https://github.com/JimLiu/baoyu-skills) (2026-03)
and MultiPost (2026-08). Only the page hooks were taken, which are facts about those pages. No code was copied.

These hooks are this extension's own guesses, in no source that was read: X's profile link
(`AppTabBar_Profile_Link`) and the toast link to the new post, Instagram's profile-picture link and its "Your reel
has been shared" wording, and YouTube's `ytcp-video-share-dialog`. A wrong one means an account check or a
"posted" that does not happen, not a wrong post.

## What is in this folder

| | |
|---|---|
| `manifest.json` | Manifest V3. Its `key` pins the extension's id, which ReelForge allows by name. |
| `background.js`, `worker/` | The service worker: finding the app, pairing, the heartbeat, taking a batch, one tab per post, the video hand-over, reports, the press grant, recovery. Its state is in `chrome.storage.session`, so it survives the browser stopping the worker. |
| `platform/` | What runs in an upload tab: `engine.js` (finding, filling, attaching, the note), one small file per platform, and `run.js` (the steps every post goes through). |
| `content/` | The two always-on scripts: `local.js` for ReelForge's pair and post pages, `site.js` for reelforge.tools. |
| `popup/`, `options/`, `ui/` | The extension's window and its settings page. |
| `shared/consts.js` | The protocol number, the ports, the four platforms, and the words for each state and failure. |
| `icons/`, `tools/make_icons.py` | The icons, made from `assets/icon/reelforge.png`. |
| `tests/` | See below. |

## Tests

```
node extension/tests/run.mjs               # everything
node extension/tests/run.mjs --stand-in    # only the suites with a stand-in for ReelForge
node extension/tests/run.mjs --real        # only the suite with ReelForge's real server
node extension/tests/run.mjs press         # only tests with "press" in their name
```

They need Node 22 and Playwright with its own Chromium (branded Chrome and Edge no longer load an extension from
the command line). ReelForge must be closed while they run: they use its ports. A full run takes about nine
minutes: six and a half with the stand-in, two and a half with the real server.

The tests load this folder as an unpacked extension into headless Chromium, with copies of the four upload pages
(`tests/fixtures/`). The browser is told to resolve the platforms' names to a server on this computer, so the
extension sees the real addresses while nothing leaves the machine and no account is used.

Most suites give the extension a stand-in for the ReelForge app (`tests/fake_app.mjs`, the app's HTTP API and
nothing more), which lets a test set up anything.

The last suite (`tests/real.test.mjs`) swaps the stand-in for ReelForge's real server and ledger, started by
`tests/real_app.py` on a scratch channel in a temporary folder: a finished video with QA passed and an approved
review, four connected platforms, its own stats database and its own file of connected browsers. It needs the
Python that has ReelForge's packages: the folder's own `.venv`, or the one `REELFORGE_PYTHON` names. Without one
it is skipped, and the run says so (with `--real`, a run that tested nothing fails). It goes through pairing on
the real pair page, a post from the real post page and one from the popup, four platforms to "ready" with the
file compared by size and SHA-256, the press and its refusals, a second post of the same video, signed out,
another account, a page that changed, a closed tab, a browser that never comes back, ReelForge restarted in the
middle of a post, and Disconnect. `docs/EXTENSION.md` has the whole list.

Its first test holds the stand-in to the real server: it sends both the same hundred or so requests, every call
the extension makes and the ways each can be refused, and compares the answers one by one.

`.github/workflows/extension.yml` runs both on every push, on Linux, and a release waits for it. It has run there and
passed.

**The copies prove the extension's own machinery, not the live sites.** They show that the file really arrives
(the page computes its size and SHA-256), that the exact text lands in the right boxes, that nothing is pressed
without your click and the grant, that no step clicks the final button, and that each failure is noticed and
reported. With the real server they also show that the extension and ReelForge agree. They cannot show that
TikTok's page still has the hook it had when someone last wrote it down.

## What still has to be checked with real accounts

Nothing below can be checked from a machine with no route to the platforms. Each needs a signed-in account, once,
and none needs a public post: YouTube keeps a draft when the dialog is closed (or set the visibility to Private),
TikTok has Save draft, and Instagram and X can be stopped before the final button.

1. Every hook in `selectors.json`, on today's pages, in your language and for your kind of account.
2. That each site takes a video given to its file input by a script, without a file dialog. The evidence is other
   projects' code, the newest from 2026-10.
3. That each caption box takes the text the way this extension inserts it (as typing first, as a paste second),
   and keeps it.
4. Signed out: that each site really lands where the hooks expect (YouTube on Google's sign-in, TikTok on
   `/login`, X on `/i/flow/login`, Instagram's log-in form).
5. The account check on X and Instagram (their hooks are guesses, see above). TikTok and YouTube have no account
   check: no source names a place on their upload pages that shows the handle.
6. The signs of a post going out: TikTok's move to `/tiktokstudio/content`, YouTube's dialog after Publish,
   Instagram's wording after Share, X's toast. And that the post's id read from YouTube's link and X's toast is the
   one ReelForge's stats use. Also that your own press of each final button is noticed (a real click on the
   element the hook finds): a sign with no press seen is reported as "not sure", not as posted.
7. Instagram's flow for a video on your account: the Create menu, the "shared as reels" notice, the two Next
   screens, and whether a professional account differs.
8. YouTube's "Made for kids" and visibility with and without saved channel defaults, and the altered-content
   question, which this extension never answers.
9. Whether a tab that loads in the background still uploads at full speed, with four posts started at once.
10. Press Post for me, on each of the three platforms where it is offered: that a scripted press is accepted at
    all, how long each page takes to react to it (the extension says "has not reacted" after 8 seconds), and what
    TikTok asks afterwards. Use a private or draft post.
11. How often TikTok asks for a verification, or slows uploads, with this way of posting.
12. Real file sizes. The video is handed over in 4 MB pieces and checked against its SHA-256. Measured here with
    stand-in files: 64 MB in about 3 seconds, 150 MB in about 7.
13. Branded Chrome, Edge and Brave. The tests run in Chromium 153. In particular, that the browser shows no
    local-network prompt when the extension talks to ReelForge.

If this ever goes to a browser store: the description in the manifest is longer than the 132 characters a listing
allows, the store's current policy text has to be read first, and the store gives the extension a different id,
which ReelForge has to be told to allow.
