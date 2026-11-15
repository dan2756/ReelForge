# Posting to YouTube through its own API

ReelForge can post a video to YouTube through Google's own API, the YouTube Data API. You pick a video on the
Publish tab, choose who can see it, and press Post. ReelForge uploads it and shows the result.

It is optional. ReelForge works the same without it. Posting by hand and posting with the
[browser extension](EXTENSION.md) stay as they are, and you can use any of the three for the same channel.

**Where this stands:** it was built from Google's published description of the API and tested against a stand-in
for Google on the computer it was built on. It has never uploaded a video to Google. Treat your first post as a
trial: leave it private, and check it in YouTube Studio. The last section of this page says exactly what has been
tested and how.

## What it is, and who it is for

- It is the official way to upload: the same API that upload tools and schedulers use. Nothing is typed into a
  web page for you, and no browser is driven.
- It runs on a Google Cloud project that you make yourself, with a sign-in client that is yours. Nothing is
  ReelForge's at Google, nothing is shared between ReelForge users, and nothing goes through a server of
  ReelForge's. There is none.
- It asks Google for one permission: uploading videos to your channel. It cannot read your account, see your
  other videos, or delete anything.
- It is for one person posting to their own channel. Setting it up takes about fifteen minutes in Google Cloud,
  once.
- It is not a bot. A post starts only when you press it in ReelForge's window. Your AI cannot start one: there is
  no command, script or pipeline step that does.

YouTube is the only platform where ReelForge does this. For TikTok, Instagram and X there is no official way for
one person to post that is practical ([why](EXTENSION.md#why-not-the-platforms-own-posting-apis)).

## Set it up in Google Cloud

You need the Google account your YouTube channel belongs to. The names of Google's pages change now and then.
These steps follow Google's help as it could be read when this was written, and nobody has walked through them
for ReelForge, so a label may differ on your screen.

1. **Make a project.** Open [console.cloud.google.com](https://console.cloud.google.com), sign in, and create a
   new project. Any name will do, such as "My channel uploads". Use one project for a channel. Google does not
   allow several projects made to get around its daily limits.
2. **Enable the API.** In the project, open APIs & Services, then Library. Search for "YouTube Data API v3" and
   press Enable.
3. **Set up the consent screen.** Open "OAuth consent screen" (Google now calls it Google Auth Platform). Give
   the app a name and your email address, and choose **External** as the audience. This is the screen Google
   shows you when you connect ReelForge.
4. **Add yourself as a test user.** On the Audience page, under Test users, add the Google account your channel
   belongs to. While the app is in Testing, only the test users can connect.
5. **Make a Desktop client.** On the Clients page, create a client and choose **Desktop app** as its type. Not
   "Web application": ReelForge refuses that kind.
6. **Download the JSON at once.** Press Download JSON on the client you just made. The file is called
   `client_secret_<a long name>.json`. Google shows a new client's secret only once, so download it now.

Keep the file out of any folder you share or commit. ReelForge's own `.gitignore` already leaves out
`client_secret*.json`.

## Connect ReelForge

In ReelForge's window, connect YouTube's API and pick the file. Or run:

```
reelforge youtube-api connect path/to/client_secret_....json
```

Google's consent page opens in your browser.

1. Pick the Google account your channel belongs to. If the account has more than one channel, Google asks which.
2. Google says it hasn't verified the app. The app is your own Google Cloud project. Continue.
3. Allow the one thing it asks for, uploading videos (Google words it "Manage your YouTube videos").

Google then sends you back to a page that says "ReelForge is connected to YouTube". That page is served by
ReelForge on your own computer (`127.0.0.1`), for that one answer, and then it stops listening.

Things to know:

- **You have ten minutes** to answer on Google's page. After that, connect again.
- **The browser has to be on this computer.** Google sends the answer to an address only this computer has.
- **Each channel connects by itself.** With more than one profile, connect each one. They can use the same
  client file, as long as each channel's Google account is a test user of that project.
- **ReelForge can't tell you which channel it is connected to** until the first upload. The permission it asks
  for doesn't cover reading your account. The answer to an upload names the channel, and ReelForge shows it from
  then on. This is one more reason to post privately the first time.
- **You can delete the downloaded file** once you are connected. Connecting again does not need it.

## How a post goes

1. You press Post for YouTube's API in the window and choose who can see the video: private, unlisted or public.
   It is **private until you change it**. You can choose otherwise for one post, or change what your posts start
   as.
2. ReelForge makes one job, as it does for the extension, and uploads the video in pieces. The row shows the
   percent. ReelForge has to stay open while it uploads.
3. When YouTube has the whole file, the row says posted, with the video's link. ReelForge reads YouTube's answer
   and tells you if YouTube stored the video another way than you asked. See "The limits" below.

The same rules as for the extension apply. A video can have one post under way to YouTube, whichever way it goes.
A video that is up on YouTube, or that a stats read has found there, is not posted again unless you ask for it by
name. A video is known by its content, not its file name. A video that came from the pipeline is posted only when
QA passed on that exact file and the review says APPROVED for that cut.

One video uploads at a time for a channel. A second one waits its turn.

### What a post sends

| | |
|---|---|
| Title | what **Copy YouTube caption** copies: your caption, then `#shorts` |
| Description | what **Copy YouTube hashtags** copies |
| Who can see it | private, unlisted or public: your choice, always sent |
| Made for kids | No, unless you changed your answer in ReelForge. Always sent |
| Category | People & Blogs |

ReelForge does not set the altered-content answer, tags, a thumbnail, a playlist or a schedule. Set those in
YouTube Studio. It leaves telling your subscribers to YouTube, which does it for a public video unless told
otherwise. YouTube has no switch for Shorts: by its own rule a vertical video of up to three minutes is one
(excerpt).

### If something stops in the middle

- **The connection drops.** ReelForge waits a little and asks YouTube how much of the file it has, then carries on
  from there. It never starts the upload over by itself.
- **The last piece went out and no answer came.** ReelForge asks YouTube whether the upload finished. If YouTube
  can't be asked either, the post is marked **not sure it posted**. Look in YouTube Studio, then say on the
  Publish tab whether it is there. ReelForge never tries such a post again by itself, because the video may be up.
- **You cancel.** No more of the file is sent, and YouTube makes no video from a file it only has part of.
- **You close ReelForge.** The next time it opens, the post is settled. If the last piece had not gone out,
  nothing was posted and the row says failed. If it had, ReelForge asks YouTube once: finished means posted,
  anything else means not sure.

## The limits, and how sure each one is

Google's documentation could not all be opened from where this was built. So each limit says how it is known:
**read** means Google's own published source was read directly, **excerpt** means only a search excerpt of
Google's own page could be read, which is good enough to plan on and not good enough to quote as fact. None of
them has been confirmed by an upload.

| | What is known | How |
|---|---|---|
| Uploads a day | 100 a day for your project, counted apart from everything else, since 1 June 2026. They start again at midnight Pacific time. An older copy of Google's page says an upload costs 1,600 of 10,000 units a day, which would be 6. Your project's Quotas page in Google Cloud is the authority. | excerpt |
| Cost | No charge was found for the API within its daily allowance. | not read on a page of Google's |
| Private only | For years, a video uploaded through a project Google had not audited was locked to private. Google's description of the API now says "Videos uploaded from unverified API projects are not restricted to private viewing mode." Google's revision history has an entry for it dated 2 November 2026. Older pages of Google's own still describe the lock. | the sentence: read. The entry: excerpt |
| A choice left out | If a request does not say who can see the video, YouTube makes it public. ReelForge always says. | read |
| The sign-in ends after 7 days | While your consent screen is in Testing, Google ends the sign-in after 7 days and ReelForge says "connect again". | excerpt |
| The warning on Google's page | Google shows "Google hasn't verified this app" for a project it has not reviewed. An app that only you use does not need the review. | excerpt |
| Size | A file can be up to 256 GB. | read |
| The permission | Uploading needs only `youtube.upload`. Reading your account or changing a video later would need more, and ReelForge does not ask for it. | read |

Because the "private only" rule changed weeks before this was built, and no upload has confirmed the change,
**ReelForge reads the result back every time.** If you asked for public and YouTube stored the video as private,
the post still counts as posted, and its row says so: change who can see it in YouTube Studio. If that keeps
happening, your project is still being held to private, and posting through the API can only ever give you
private videos to publish by hand in Studio.

### About the 7 days

You have two ways to live with it:

- **Connect again each time.** It is one press and Google's page, with no file to pick.
- **Move the app out of Testing.** On the Audience page in Google Cloud, publish the app (its status becomes "In
  production"). Google's help says the sign-in then stops ending after 7 days, that the warning page stays, and
  that an app only you use does not need Google's review (excerpt). Nobody has tried this for ReelForge.

When Google's answer says how long the sign-in lasts, ReelForge shows the day it runs out.

## When something goes wrong

A failed post says why in a sentence, and nothing was posted. These are the ones you are most likely to meet.

| It says | What to do |
|---|---|
| YouTube's sign-in has run out or was taken back at Google | Connect again |
| Google no longer knows the OAuth client | The client was deleted in Google Cloud. Make a new Desktop client and connect with its file |
| Your Google Cloud project has used up today's YouTube API allowance | Wait until midnight Pacific time, or post this one by hand |
| The YouTube Data API v3 isn't switched on in your Google Cloud project | Do step 2 above, wait a minute, post again |
| The Google account you connected has no YouTube channel | Connect again and pick the account, or the channel, that is yours |
| Google didn't give ReelForge permission to upload | Connect again and allow it on Google's page |
| YouTube didn't take the title | Shorten the caption, and take out any `<` or `>` |
| YouTube couldn't be reached | Check your connection, then press Retry |
| The video file changed after you pressed Post | Check the video, then post again |
| ReelForge was closed during the upload | Nothing was posted. Press Retry |
| YouTube took the upload and rejected it | Look at the video in YouTube Studio. The reason is in brackets: "duplicate" is a video YouTube already has |
| YouTube refused the upload (some word) | The word in brackets is Google's own, for a refusal ReelForge has no sentence for. Post this one by hand |

**Not sure it posted** is not a failure. It means the video may be up. Look in YouTube Studio first.

You can always post by hand instead. The button for YouTube's upload page on the Publish tab works as ever.

## What ReelForge keeps, and where

One file for each channel:

- your first channel: `dashboard/data/youtube_api.json`
- a channel you added: `profiles/<its folder>/dashboard/data/youtube_api.json`

It holds the client's id and secret from the file you picked, the sign-in Google gave ReelForge (a refresh
token), your choices (who can see a post, the made-for-kids answer), the channel the last upload went to, and the
address of an upload while one is under way.

- Only you can read it (on macOS and Linux its mode is 0600), and it is written whole, never half.
- It is never part of the kit: git leaves the folder out, and an update never touches it.
- It moves with the ReelForge folder when you move it.
- A factory reset moves it into the backup with your stats. The sign-in in it still works, so disconnect first
  if you want it ended.
- ReelForge never prints these values, writes them to a log, shows them in its window or puts them in a message.
  They go to Google's sign-in and upload addresses and nowhere else. The addresses written in the client file
  are not used.

What this does not protect against: a program already running as you on this computer. It can read the file.

## Disconnect

In the window, or:

```
reelforge youtube-api disconnect
```

ReelForge deletes the file first, then tells Google to end the sign-in. If Google could not be told (you were
offline), it says so. Then take the access away yourself at
[myaccount.google.com/connections](https://myaccount.google.com/connections). You can also delete the client, or
the whole project, in Google Cloud. That ends every sign-in made with it.

## Commands

```
reelforge youtube-api                 where the connection stands: connected or not, the channel, your choices
reelforge youtube-api connect FILE    connect with the client file from your Google Cloud project
reelforge youtube-api connect         connect again, with the client already on file
reelforge youtube-api disconnect      forget the connection and tell Google to end it
```

None of these posts anything. `reelforge -p "Channel name" youtube-api ...` works on one profile.

## For developers

The Google side is `dashboard/platforms/youtube_api.py`: its opening comment says how connecting and uploading
work. The window's side, and what moves a post's job along the ledger, is `dashboard/youtube_api_tab.py`. The
ledger itself is `dashboard/posting.py`, shared with the extension: a job made here has `via = "api"`, and the
extension is never handed one.

What the window gets and calls:

| | |
|---|---|
| view `youtube_api` | `state` (`off`, `connecting`, `on`, `again`), `line`, `lines`, `client`, `channel`, `privacy`, `made_for_kids`, `privacies`, `note`, `docs` |
| `youtube_api_info(caption, tags)` | the view, and for the picked video `send`, `fields`, `left`, `can_post`, `why`, `force`, `blocked`, `review` |
| `youtube_api_connect(path)` | reads the client file and opens Google's page. `""` connects again with the client on file |
| `youtube_api_disconnect()` | forgets the connection and tells Google |
| `youtube_api_defaults(privacy, made_for_kids)` | your choices for the posts to come |
| `post_with_api(caption, tags, privacy, force)` | your press: makes the job and starts the upload |
| view `posting` | the YouTube row has `post_api` among its `actions` while a post could start, with `api: {"force": ...}` |

A job goes `queued`, `uploading` (with `progress`), `posting` (the last piece is on its way), then `posted`, or
ends as `failed` or `unknown`. Retry on a failed row goes through the API again. A posted job whose `step` is
`confirm` has a sentence in `detail` about how YouTube stored the video: something for the person to look at.

The addresses ReelForge talks to are constants in `youtube_api.py`. No setting or environment variable changes
them. The tests patch them to a stand-in on the same computer.

## What has and hasn't been verified

Tested here, by automated tests that run with ReelForge's own (`dashboard/tests/test_youtube_api.py`,
`test_youtube_api_tab.py`), against a stand-in for Google that runs on the same computer
(`dashboard/tests/google.py`) and answers the way Google's documentation describes:

- connecting: the one permission asked for, the PKCE proof, the state, an answer with another state, saying no,
  nobody answering, a file that is not a Desktop client, and that the listener answers once and closes
- the sign-in: renewed when it runs out, "connect again" when Google has ended it, and that Google is not asked
  about an ended one over and over
- the upload: in pieces, a 503 in the middle and carrying on, a piece that arrived although its answer was an
  error, the last answer lost and found, the last answer lost for good (not sure it posted, and one upload only),
  a sign-in that ends in the middle, and every refusal ReelForge has a sentence for
- the read-back: a video YouTube stored as private when public was asked for
- that who can see the video and the made-for-kids answer are in every request
- the ledger: what may not be posted twice, in both directions between the extension and the API; cancelling;
  two videos taking turns; ReelForge closed before and during the last piece
- that an update, a move and a factory reset treat the file as described above
- that the client's id and secret, the tokens, Google's code and the upload's address appear nowhere in what is
  printed, logged, sent to the window, answered to it, put in an error, or kept in the stats database

Seen from Google itself, without any account:

- Google's published description of the API (revision 20261112): the upload call, the permission it needs, the
  fields ReelForge sends and reads back, and the two sentences quoted under "The limits"
- Google's published sign-in configuration: the three addresses ReelForge uses, and that it takes the S256 proof
- that those addresses exist: the upload address refused a request with no sign-in, and the token and revoke
  addresses refused made-up values, each in the form ReelForge reads

Not verified:

- **Anything with a real sign-in.** No Google Cloud project was made, no consent page was opened, and no token
  was ever issued to ReelForge.
- **Any upload to YouTube.** Not a private one, not a public one. Whether a public upload from your own project
  stays public, how YouTube words a refusal, and what its answer to a finished upload looks like in full are
  known only from Google's documentation.
- The words Google uses for a refusal. ReelForge's list was written from Google's reference as it was known when
  this was built. That page could not be opened then.
- The steps in Google Cloud, on a real screen.
- The limits marked "excerpt".
- Windows and macOS. The tests ran on Linux.
- The window. Its Python side is built and tested; the window does not show this yet.
