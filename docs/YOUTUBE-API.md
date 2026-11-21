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
- It asks Google for one permission: uploading to your channel. With it ReelForge cannot read your account, list
  your videos, change a video or delete one.
- It is for one person posting to their own channel. Setting it up takes about fifteen minutes in Google Cloud,
  once.
- It is not a bot. A post starts only when you press it in ReelForge's window. No `reelforge` command posts, and
  none signs in.
- Nothing in this folder can post by itself. The sign-in Google gives ReelForge is kept in the memory of the open
  window that connected, never in a file. A script, or an AI working in this folder, has nothing to upload with:
  it would first have to open Google's page and get your yes there. If Google's page ever opens when you did not
  press Connect, close it.

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

On the Publish tab, above Post…, press **YouTube API…**. The button is there once YouTube is connected on the
Accounts tab and a video is picked on the Publish tab. Its dialog has a box for the client file. Type or paste
where the file you downloaded is, and press **Connect with this file**. Google's consent page opens in your
browser, and the dialog says that it is waiting for your answer there.

1. Pick the Google account your channel belongs to. If the account has more than one channel, Google asks which.
2. Google says it hasn't verified the app. The app is your own Google Cloud project. Continue.
3. Allow the one thing it asks for, uploading videos (Google words it "Manage your YouTube videos").

Google then sends you back to a page that says "ReelForge is connected to YouTube". That page is served by
ReelForge on your own computer (`127.0.0.1`), for that one answer, and then it stops listening.

The dialog now says "YouTube API connected", and the YouTube row on the Publish tab has a **Post with API…**
button. ReelForge has kept the two values it needs from the file, the client's id and its secret, so you can
delete the downloaded file.

Under **For posts to come**, the same dialog has your two choices: who can see a new post, and your made-for-kids
answer, which reads "Not answered" until you give one.

The next time you open ReelForge, the YouTube row has **Connect API** in place of Post with API…. Press it, or
**Connect** in the dialog: Google's page opens again, and no file is asked for.

You can also give ReelForge the file from a terminal. This keeps the two values and connects nothing: connecting
is only in the window.

```
reelforge youtube-api client path/to/client_secret_....json
```

Things to know:

- **You connect each time you open ReelForge.** The sign-in lives in the open window and goes when it closes.
  It is one press and Google's page, with no file to pick. A window that has just opened says it is not connected.
  This is what keeps a script or an AI in this folder from posting: there is no sign-in on disk to post with.
- **You have ten minutes** to answer on Google's page. After that, connect again.
- **The browser has to be on this computer.** Google sends the answer to an address only this computer has.
- **Each channel connects by itself.** With more than one profile, connect each one. They can use the same
  client file, as long as each channel's Google account is a test user of that project.
- **ReelForge can't tell you which channel it is connected to** until the first upload. The permission it asks
  for doesn't cover reading your account. The answer to an upload names the channel, and ReelForge shows it from
  then on. This is one more reason to post privately the first time.

## How a post goes

1. On the Publish tab, pick a video and press **Post with API…** on the YouTube row. A question shows exactly
   what will be sent, and lets you choose who can see this video: private, unlisted or public. It is **private
   until you change it**: here for this one post, or in the YouTube API… dialog for the posts to come. Nothing is
   uploaded until you press **Post to YouTube** there.
2. ReelForge makes one job, as it does for the extension, and uploads the video in pieces. The row shows the
   percent, and **Cancel** stops it. ReelForge has to stay open while it uploads.
3. When YouTube has the whole file, the row says posted, and **Open** opens the video. ReelForge reads YouTube's
   answer and tells you on the row if YouTube stored the video another way than you asked. See "The limits" below.

The same rules as for the extension apply. A video can have one post under way to YouTube, whichever way it goes.
A video that is up on YouTube, or that a stats read has found there, is not posted again unless you ask for it by
name: its row has **Again with API…**, whose question says that it makes a second video and whose button reads
**Post it again**. The same goes for six hours after you opened YouTube's upload page by hand for the video: a
post made there is one ReelForge can't see until a stats read finds it, so the question says that this would be a
second video if you posted it there. A video is known by its content, not its file name. A video that came from the
pipeline is posted only when QA passed on that exact file and the review says APPROVED for that cut.

One video uploads at a time for a channel. A second one waits its turn, for up to ten minutes, and its row says
so. If the first upload takes longer than that, the second never starts. Its row says it never started, and if
you are looking at another video by then, a pop-up says it too. Press Post with API… on it again once the
first is up.

### What a post sends

| | |
|---|---|
| Title | what **Copy caption** on YouTube's row copies: your caption, then `#shorts` |
| Description | what **Copy YouTube hashtags** copies |
| Who can see it | private, unlisted or public: your choice, always sent |
| Made for kids | your answer, once you have given ReelForge one. Until then nothing is sent |
| Category | People & Blogs |

"Made for kids" is a declaration you make to YouTube, so ReelForge never makes it for you. Until you have
answered in ReelForge, a post carries no answer and the audience setting of your channel in YouTube Studio applies.
The row then says that no answer was sent and what YouTube made of the video, and asks you to look if YouTube has
it as made for kids. What YouTube does with an upload that carries no answer has not been seen on a real upload,
which is why ReelForge reads its answer back.

ReelForge does not set the altered-content answer, tags, a thumbnail, a playlist or a schedule. Set those in
YouTube Studio. It leaves telling your subscribers to YouTube, which does it for a public video unless told
otherwise. YouTube has no switch for Shorts: by its own rule a vertical video of up to three minutes is one
(excerpt).

### If something stops in the middle

- **The connection drops.** ReelForge waits a little and asks YouTube how much of the file it has, then carries on
  from there. It never starts the upload over by itself.
- **The last piece went out and no answer came.** ReelForge asks YouTube whether the upload finished. If YouTube
  can't be asked either, the post is marked **not sure it posted**. Look in YouTube Studio, then say on its row
  whether it is there: **It's posted** or **It didn't post**. ReelForge never tries such a post again by itself,
  because the video may be up.
- **You cancel.** No more of the file is sent, and YouTube makes no video from a file it only has part of.
- **You close ReelForge.** The next time it opens, the post is settled. If the last piece had not gone out,
  nothing was posted and the row says failed. If it had, the post is marked not sure it posted: the sign-in went
  with the window that closed, so ReelForge can't ask YouTube. Look in YouTube Studio. (A second window of the
  same channel that is still open and connected asks YouTube once: finished means posted.)

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
| The sign-in ends after 7 days | While your consent screen is in Testing, Google ends a sign-in after 7 days. You connect again each time ReelForge opens, so this only shows in a window left open for a week: it says "connect again". | excerpt |
| The warning on Google's page | Google shows "Google hasn't verified this app" for a project it has not reviewed. An app that only you use does not need the review. | excerpt |
| Size | A file can be up to 256 GB. | read |
| The permission | Uploading needs only `youtube.upload`. Google's description gives that permission four calls: uploading a video, and setting a thumbnail, a channel banner and a watermark, which ReelForge never does. Reading your account, listing, changing or deleting videos need more, and ReelForge does not ask for it. | read |

Because the "private only" rule changed weeks before this was built, and no upload has confirmed the change,
**ReelForge reads the result back every time.** If you asked for public and YouTube stored the video as private,
the post still counts as posted, and its row says so: change who can see it in YouTube Studio. If that keeps
happening, your project is still being held to private, and posting through the API can only ever give you
private videos to publish by hand in Studio.

### About the 7 days

ReelForge never keeps a sign-in longer than its window is open, so you connect again long before the 7 days are
up. They only matter if you leave ReelForge open for a week. Then you have two ways to live with it:

- **Connect again.** It is one press and Google's page, with no file to pick.
- **Move the app out of Testing.** On the Audience page in Google Cloud, publish the app (its status becomes "In
  production"). Google's help says the sign-in then stops ending after 7 days, that the warning page stays, and
  that an app only you use does not need Google's review (excerpt). Nobody has tried this for ReelForge.

When Google's answer says how long the sign-in lasts, ReelForge shows how many days are left.

## When something goes wrong

A failed post says why in a sentence, and nothing was posted. Its row has **Post with API…** again. A new try
starts with the question, like any post, so you see what it will send: the caption and your choices as they are
then, not as they were. These are the sentences you are most likely to meet.

| It says | What to do |
|---|---|
| ReelForge keeps YouTube's sign-in only while it is open | Connect in this window, then press Post with API… |
| YouTube's sign-in has run out or was taken back at Google | Connect again |
| Google no longer knows the OAuth client | The client was deleted in Google Cloud. Make a new Desktop client and connect with its file |
| Your Google Cloud project has used up today's YouTube API allowance | Wait until midnight Pacific time, or post this one by hand |
| The YouTube Data API v3 isn't switched on in your Google Cloud project | Do step 2 above, wait a minute, post again |
| The Google account you connected has no YouTube channel | Connect again and pick the account, or the channel, that is yours |
| Google didn't give ReelForge permission to upload | Connect again and allow it on Google's page |
| YouTube didn't take the title | Shorten the caption, and take out any `<` or `>` |
| YouTube couldn't be reached | Check your connection, then press Post with API… again |
| The video file changed after you pressed Post | Check the video, then post again |
| (the video's name) changed while it was being uploaded | Another cut was written over the file while it went up. ReelForge checks every piece against the video you said yes to, and the last piece never went: nothing was posted. Check the video, then post again |
| ReelForge was closed during the upload | Nothing was posted. Press Post with API… again |
| YouTube took the upload and rejected it | Look at the video in YouTube Studio. The reason is in brackets: "duplicate" is a video YouTube already has |
| YouTube refused the upload (some word) | The word in brackets is Google's own, for a refusal ReelForge has no sentence for. Post this one by hand. If it came in answer to the last piece, the post is "not sure it posted" instead, because such a word proves nothing |

**Not sure it posted** is not a failure. It means the video may be up. Look in YouTube Studio first.

You can always post by hand instead. The button for YouTube's upload page on the Publish tab works as ever.

## What ReelForge keeps, and where

One file for each channel:

- your first channel: `dashboard/data/youtube_api.json`
- a channel you added: `profiles/<its folder>/dashboard/data/youtube_api.json`

It holds the client's id and secret from the file you gave, your choices (who can see a post, and the
made-for-kids answer once you have given one), the channel the last upload went to, and the address of an upload
while one is under way.

**The sign-in is not in it, or in any file.** What Google gives ReelForge when you say yes (a refresh token) stays
in the memory of the window that connected and is gone when that window closes. The client's id and secret alone
can't post: they only let ReelForge ask you again on Google's page.

- Only you can read the file (on macOS and Linux its mode is 0600), and it is written whole, never half.
- It is never part of the kit: git leaves the folder out, and an update never touches it.
- It moves with the ReelForge folder when you move it.
- A factory reset moves it into the backup with your stats.
- ReelForge never prints these values or the sign-in, writes them to a log, shows them in its window or puts
  them in a message. They go to Google's sign-in and upload addresses and nowhere else. The addresses written in
  the client file are not used.

What this does and doesn't protect against: a program running as you on this computer, your AI included, finds
no sign-in to post with in this folder, and no command gives it one. It can still read the client's id and
secret, and it could open Google's page and wait for your yes, so only say yes to a page you opened yourself. Your
AI's guide tells it never to connect or post. A program that goes after the open window itself, by reading its
memory, is beyond what ReelForge can stop.

## Disconnect

In the window: **YouTube API…**, then **Disconnect**, which asks first. Or:

```
reelforge youtube-api disconnect
```

ReelForge deletes the file first. With the client gone, no ReelForge window can post through the API any more,
a connected one included. A window that is connected then tells Google to end the sign-in. If Google could not be
told (you were offline), it says so. The command, and a window that is not connected, have no sign-in to end at
Google. In both cases take the access away yourself at
[myaccount.google.com/connections](https://myaccount.google.com/connections). You can also delete the client, or
the whole project, in Google Cloud. That ends every sign-in made with it.

## Commands

```
reelforge youtube-api                 where it stands: the client, the channel, your choices
reelforge youtube-api client FILE     take the client file from your Google Cloud project
reelforge youtube-api disconnect      forget the client, so nothing can connect or post until you give it again
```

None of these posts anything, and none of them signs in. `reelforge -p "Channel name" youtube-api ...` works on
one profile.

## For developers

The Google side is `dashboard/platforms/youtube_api.py`: its opening comment says how connecting and uploading
work. The window's side, and what moves a post's job along the ledger, is `dashboard/youtube_api_tab.py`. The
ledger itself is `dashboard/posting.py`, shared with the extension: a job made here has `via = "api"`, and the
extension is never handed one.

What the window gets and calls:

| | |
|---|---|
| view `youtube_api` | `state` (`off`: no client on file, `connecting`, `on`, `again`: a client is on file and this window has no sign-in), `line`, `said`, `said_id` (one more each time something is said, the same words again included: the dialog compares it with the one that something typed since put away), `lines`, `client`, `wants_file` (connecting takes a client file), `channel`, `privacy`, `made_for_kids` (`true`, `false`, or `null` until the person has answered), `privacies`, `note`, `docs`, `words` (the sentences beside the dialog's controls) |
| `youtube_api_info(caption, tags)` | the view, and for the picked video `video` (with `sha256`: the file as it is now), `send`, `fields`, `left`, `can_post`, `why`, `force`, `blocked`, `review`, `how`, `again` |
| `youtube_api_connect(path, quiet)` | reads the client file and opens Google's page: `true` when it is opening. `""` connects again with the client on file. `quiet`: the dialog asked, which says itself how it goes. Then no pop-up says it, and when connecting can't start the answer is the sentence that says why |
| `youtube_api_disconnect()` | forgets the connection and tells Google |
| `youtube_api_dialog(shown)` | the window's dialog for the connection opened or closed. While it is open, how connecting and disconnecting went is in the view's `said`, and the dialog shows it until something is typed in it: then it shows nothing more until `said_id` moves on (words said again count too). No pop-up says it over the dialog's buttons |
| `youtube_api_defaults(privacy, made_for_kids)` | your choices for the posts to come. `made_for_kids` is the person's answer: `true` or `false`. Until it is given, `send.made_for_kids` is `null` and nothing is declared |
| `post_with_api(caption, tags, privacy, force, shown)` | your press: makes the job and starts the upload. `shown`: what the question showed (`{"video": its path, "sha256": the file's, "made_for_kids": ...}`), which the window sends back. When any of them is another one by now, nothing starts |
| view `posting` | while the API is connected and a post could start, the YouTube row has `api: {"force": ...}` (`force`: it would be a post again). Unless it would be one, `post_api` is among its `actions`. While the state is `again`, `connect_api` is there instead. Each row's `via` says how its job goes (`"extension"` or `"api"`), and `labels` has the words for every button |
| `post_action(job, "post_api")` | posts the picked video with the form's text and your choices, never "again", and without a question. The window does not call it: its button for `post_api` opens the question (`youtube_api_info`), and Post there calls `post_with_api`. Posting again takes `post_with_api(..., force)` |
| `post_action(job, "connect_api")` | the same as `youtube_api_connect("")`. The window's button for it opens the dialog and connects from there |

A job goes `queued`, `uploading` (with `progress`), `posting` (the last piece is on its way), then `posted`, or
ends as `failed` or `unknown`. A row whose job went through the API has no Try again (`retry`): a new try is a new
post, from `post_api` and the question, and `post_action(job, "retry")` on such a job uploads nothing. Nor does
such a row get the extension's Post again… (`post_again`) when its try ended without a post and the upload page
was opened by hand since: posting again there goes through the API too, with `api: {"force": true}` (the window's
**Again with API…**). A posted
job whose `step` is `confirm` has a sentence in `detail` about how YouTube stored the video: something for the
person to look at. One whose `step` is `note` went without a made-for-kids answer, and its `detail` says so.

The sign-in is held in the window's process (`youtube_api._held`) and never written down, so `status()` in any
other process says `again`, and `upload()` there has nothing to send with. A command never connects: its sign-in
would end with it.

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
- that the sign-in is in no file: a ReelForge opened later, and another program started with the same file, are
  not connected and send nothing, and a sign-in put into the file by hand is never used
- the upload: in pieces, a 503 in the middle and carrying on, a piece that arrived although its answer was an
  error, another cut written over the file as it goes up (the same size or longer, and only in a piece sent again):
  the last piece never goes, the last answer lost and found, the last answer lost for good (not sure it posted,
  and one upload only), a sign-in that ends in the middle, every refusal ReelForge has a sentence for, and a
  refusal of the last piece that proves nothing (a word ReelForge doesn't know, or a "no" to the piece sent a
  second time): not sure it posted, and no button that posts it again
- the read-back: a video YouTube stored as private when public was asked for
- that who can see the video is in every request, and that the made-for-kids answer is in it once you have given
  one and never before
- the ledger: what may not be posted twice, in both directions between the extension and the API; cancelling;
  two videos taking turns, and one whose turn doesn't come in time; ReelForge closed before and during the last
  piece, with and without a second window
  that is still connected
- that an update, a move and a factory reset treat the file as described above
- that the client's id and secret, the tokens, Google's code and the upload's address appear nowhere in what is
  printed, logged, sent to the window, answered to it, put in an error, or kept in the stats database
- the window (`ui/test/youtube_api.test.tsx`), drawn without a terminal and clicked through against the same
  stand-in, with "your browser" played by the test: the client file and a file that is refused, Connect and the
  wait for Google's page, a no there, the two choices, the question and what it sends, a yes after the video's
  file was replaced (nothing starts), a post from uploading to posted with its link, a refusal and the new try
  after it, a post again that is refused, a post that is not sure, Cancel, a window opened afterwards (not
  connected, and its Post starts nothing), and Disconnect

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
- Windows and macOS with a real browser. The tests run on GitHub's Windows, macOS and Linux machines on every
  push, against the stand-in, with the browser's part played by the tests.
- The window in a terminal, with a real browser and Google's own page. Its tests draw it without a terminal and
  answer for the browser.
