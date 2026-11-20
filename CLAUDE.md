<!-- reelforge-bootstrap: first-time setup replaces this file with one written for your channel. -->
# ReelForge (not set up yet)

This folder is **ReelForge**, a kit for making faceless short-form videos with you, Claude. New channels
make **2D videos**: you write the script and draw the scenes in code, a free offline voice reads it, and big
word-by-word captions follow the voice. A channel can make **3D videos** instead, which Blender builds and renders.
Python mixes the sound, ffmpeg encodes, an automatic QA gate checks every video, and the **ReelForge** window
(type `reelforge` in a terminal) is where the person you're working with sees their stats and posts from.

It hasn't been set up for their channel yet. First-time setup replaces this file with a full CLAUDE.md written from
their answers (their channel, format, platforms, style, caption rules and quality bar).

## Getting them set up

1. **Install.** If there is no `.venv` folder here, ask them to double-click `setup.bat` (Windows) or run
   `./setup.sh` (macOS/Linux). It lists what ReelForge still needs, sets it all up inside this folder once they
   say yes, then checks everything. You can run it for them only if they agree to all of it: `setup.bat -Yes` /
   `./setup.sh --yes` downloads the voice for 2D videos (about 420 MB), Blender 4.5 for 3D videos (about 400 MB;
   only once a channel makes 3D videos), ffmpeg (60-150 MB), the sound effects (about 55 MB)
   and Bun with the ReelForge window's packages (about 60 MB) into this folder (on macOS/Linux also a private Python
   if theirs is older than 3.11.4, and Chromium if they have no Chrome or Edge), adds the `reelforge` command to their PATH and ReelForge's icon (a desktop shortcut on
   Windows, ReelForge in Applications on macOS, an app-menu entry on Linux).
2. **Their channel.** Either they run `channel.bat` / `./channel.sh` (a few questions in a full-screen box), or you
   interview them here in the chat:
   - The first big choice is `video_type`: `2d` (the default: drawn in code, voiced, captioned) or `blender`
     (3D scenes, no voiceover). Explain the difference in a line each. If they pick `blender`, run setup again
     afterwards: it then offers Blender.
   - Get the questions: `.venv\Scripts\python -m studio channel --questions` (macOS/Linux: `.venv/bin/python`).
     Each has a key and its options, and most have a default (which AI they use, one or both, has none: ask).
   - Ask them conversationally, a few at a time, offering the options as choices and the defaults as suggestions.
     Skip questions whose `only_if` doesn't apply. `background_stats` adds an hourly background job that reads
     their stats while ReelForge is closed: ask it plainly; their answer is the permission.
   - No account on a platform yet? Help them make it: each handle question has its `signup` page and steps, and the
     first has `gmail`, a new Gmail just for the channel to offer first (it signs them in to YouTube, and the
     others can sign up with it). They sign up themselves, in their own browser: never make an account for them or
     ask for a password. A handle they skip, they can connect later on ReelForge's Accounts tab.
   - Write their answers to `my-studio/answers.json` (`{"key": value, ...}`), then run
     `.venv\Scripts\python -m studio channel --answers my-studio/answers.json`. It checks the answers and says
     what to fix.
3. **Read the new CLAUDE.md** that first-time setup wrote, and follow it from then on. Start by asking them for a
   file or a description of their channel, its brief: first-time setup only asks a few questions, and the new
   CLAUDE.md says where the brief goes.
4. `.venv\Scripts\python -m studio doctor` checks everything at any time; `--render-test` also renders a frame.
5. **More channels later?** Each one is a profile with its own folder in `profiles/`: `reelforge profiles new` runs the
   same interview for it (or, interviewing them in chat, `--new --answers FILE.json`). The CLAUDE.md first-time
   setup writes then lists every channel and where its notes are. A new channel's accounts are best made in a
   private window of their browser, so their first channel stays logged in: the `gmail` item's `another_channel`
   says how.

## Always

- Never post, upload or send anything. Finished videos go to ReelForge's Publish tab, and they post from there:
  by hand, with the browser extension (`docs/EXTENSION.md`), or for YouTube through its API. Every post starts with
  their own press.
- Never start a post for them or get round that press: not through the window's back end
  (`python -m studio.bridge`, `post_with_extension`, `post_action`), not through the extension's local server
  (`reelforge extension serve`, `reelforge extension pair`, anything on `127.0.0.1` ports 47821 to 47830), and not
  by changing the extension, its local pages or its connections (`extension/`, `dashboard/web/`,
  `dashboard/data/extension.json`). Connecting a browser and switching on Press Post for me are theirs to do.
- That covers posting to YouTube through its API (`docs/YOUTUBE-API.md`) too: connecting and posting are theirs
  to press in ReelForge's window. Never open Google's consent page for them, drive the window's back end
  (`python -m studio.bridge`) to connect or post, or call `post_with_api`, `youtube_api.connect`,
  `youtube_api.upload` or `youtube_api_tab.run`.
- Use your strongest model at high effort for video work.
- Read `README.md` for what the kit does, and `docs/` for the pipeline, the quality bar and the sound rules.
