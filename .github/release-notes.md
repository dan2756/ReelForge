## What's new in 3.1.0

- **Before you update: copy your own sounds list and Codex settings.** This update puts the kit's over yours, as updates have.
  - Added sounds to assets/sfx/manifest.json? Copy your entries first, and add them to the new list after.
  - Changed .codex/config.toml? Copy it first, and put your changes back in after.
  - Channel settings you edited by hand stay as you left them, in this update too. Videos, stats and logins always do.
  - From 3.1 on, updates keep those two too, finish one that stopped part way, and won't install a release with no checksums.
- **Publish shows where every post stands.** Under POST, a row for each platform says whether the video is up there.
  - ReelForge keeps a record of the posts it helps with, and asks before it starts a second post of a video somewhere.
  - A video that hasn't passed its checks, or whose review is of an earlier cut, says so before you post it.
- **A browser extension can fill in the upload pages (optional).** Post… shows each platform's text and asks first.
  - Then your own browser opens each upload page, and the extension attaches the video and types the text.
  - It stops there: you check the page and press Post. It presses Post only where you switched that on, never on X.
  - It is tested against copies of the upload pages, not yet on the live sites. docs/EXTENSION.md is its guide.
  - New: an open ReelForge window listens for it on this computer only (a port from 47821 to 47830), set up or not.
- **Post to YouTube through its own API (optional).** With a Google Cloud project of your own, from a press on Publish.
  - Private unless you choose otherwise. It has never uploaded to YouTube itself yet: docs/YOUTUBE-API.md says more.
- **2D videos get their voice on every Windows PC.** With the ffmpeg setup downloads there, 3.0 left the voice empty.
  - No 2D video could be finished on such a PC. If one of yours stopped at the sound, run its build step again.
  - A build no longer stops on a new line of script with a sound on it.
  - The video step won't use frames that were drawn before the script was voiced again.
  - An episode's `gain_db` for the voice now moves it against the rest of the mix. In 3.0 any value gave the same mix.
- **A 3D channel stays a 3D channel.** new-episode no longer hands it the 2D demo, whatever its format.
  - `channel --answers` with answers your AI wrote out again keeps the kind of video the channel makes.
  - Picking 3D in first-time setup no longer leaves the voiceover's loudness (-16 LUFS) on videos with no voice.
  - Set up a 3D channel with 3.0? It may still be at -16 LUFS: Change your answers, on Settings, has Quiet (-25.5).
- **A typo in a settings file is one line that names the file.** `reelforge doctor` reports it too.
  - Connecting an account changes only that platform. A no to the hourly stats read stays a no, however it's spelled.
- **Stats you can put right.** A video's views typed in by mistake can be corrected, and a changed handle is noticed.
  - A read that got only part of a platform says which part. Log out and Disconnect are on the Accounts tab.
- **A 3D render asks before it reuses frames of an older scene.** That holds for scenes built with 3.1 on.
  - It says how far it is and goes back for missing frames. `make_episode.py <episode> --status` says where one stands.
  - A wrong spec stops the build in seconds, and a video step that fails leaves the last good video alone.
- **The window is drawn for a small terminal too.** From 80 columns by 24 rows, panels stack and tabs scroll.
- **Every release is tested first.** It is published only once its own tests have passed on Windows, macOS and Linux.

## What's new in 3.0.0

- **2D videos with a voiceover are the new default.** New channels pick **2D** or **3D (Blender)** in first-time setup.
  - Your AI writes the script and draws each scene in code. A free voice that runs on your computer reads it.
  - Big word-by-word captions follow the voice, the word being said lit up.
  - No graphics card needed: a 2D video renders in a few minutes. Setup downloads the voice (about 420 MB).
  - Pick the voice and a 2D look in first-time setup: flat cartoon, infographic, paper cut-out, doodle or neon.
- **Blender is still there for 3D videos.** Setup downloads it once a channel makes 3D videos, and not before.
- **Your channels stay as they are.** A channel set up before 3.0 keeps making 3D videos until you change it.
- **A new 2D demo** to start from: "A million dollars now, or a penny doubled for 30 days?"
- **QA checks the voice** too: made from the latest script, clear over the music, and no long silences.
- **Two music beds and new sounds** for voiced videos, made by ReelForge itself, so they're free to use.

## What's new in 2.2.1

- **A big Copy prompt button on setup's last screen,** right under step 1, so it's the first thing you see.
  - The prompt itself isn't shown any more. The button copies it, with your ReelForge folder's path in it.

## What's new in 2.2.0

- **Which AI makes your videos is a tick list: Claude Code, Codex or both.** An earlier answer carries over (an old Both is both again).
  - With both, setup's last screen and the Settings tab name both, and the FAQ answers for both.
- **Copy the prompt is always on screen.** On setup's last screen it's the first button in the bottom row, even in a small window.
  - Run setup again and the prompt tells your AI what changed in your answers, so it uses them from the next video.
- **Open in Explorer** beside ReelForge's folder on the Settings tab (Finder on a Mac, the file manager on Linux).
- **The Publish tab's YouTube Copy caption** sits under YouTube's button and copies the title with #shorts.

## What's new in 2.1.1

- **Copy the prompt for your AI: nothing to drag in.** Setup's last screen shows it, with a **Copy the prompt** button.
  - It has your ReelForge folder's path in it. Paste it into Claude Code, Codex or any AI that works with your files.
  - Your AI gets to know your channel, then makes your first video.
  - **Copy prompt for your AI** on the Settings tab copies it again any time.
- **Claude Code or Codex: you pick which AI makes your videos.** Setup recommends neither, and Both is gone.
  - An earlier Both answer now counts as Claude Code. **Change your answers** on the Settings tab switches it.

## What's new in 2.1.0

- **ReelForge can move into a folder of its own.** The Settings tab says where it is, under On this computer.
  - Kept somewhere else, like the Desktop? **Move…** puts it where the install line does: your user folder's ReelForge.
  - Everything in it goes along in one step. The reelforge command, the icon and Background refresh follow it.
  - In a terminal it's `reelforge move`, or `reelforge move FOLDER` for another folder on the same drive.
- **The install line finds a ReelForge you already have** somewhere else, and points you to Move, not a second copy.
- **Uninstall puts Windows Terminal's ReelForge profile in the Recycle Bin** too, as it says it does.

Already have ReelForge? Open the **Updates** tab and click **Update and restart**.

---

ReelForge lets Claude Code or Codex make faceless short videos for TikTok, YouTube Shorts, Instagram Reels and X,
then shows you how each one does once you've posted it. Nothing is posted without your press.

## Install

- **Windows 10 / 11:** open PowerShell, paste `irm https://github.com/dan2756/ReelForge/releases/latest/download/install.ps1 | iex` and press Enter.
- **Mac or Linux:** open Terminal, paste `curl -fsSL https://github.com/dan2756/ReelForge/releases/latest/download/install.sh | bash` and press Enter.

It downloads ReelForge straight from GitHub into a ReelForge folder in your user folder, checks it against `SHA256SUMS.txt` and starts setup, so neither your browser nor your computer warns about it. The other files below are what it downloads.

Installed ReelForge with the old Setup.exe? Its Updates tab can't install this version, so paste the Windows line into PowerShell instead: it finds that ReelForge and updates it in place, keeping your channel, videos, stats, logins and settings.

Setup shows what ReelForge still needs (the voice for 2D videos, ffmpeg, the sound effects, the `reelforge` command,
ReelForge's icon, Blender 4.5 LTS once a channel makes 3D videos, and on a Mac or Linux PC a private Python or a
Chromium if it hasn't got one), sets it all up inside the ReelForge folder once you say yes, then asks a few questions
about your channel. You'll need Claude Code (a paid Claude plan or an API account) or Codex, and about 10 GB of free
disk space. 2D videos run on a Windows 10 or 11 PC, a Mac or a Linux PC (x64). 3D videos also need a Mac with Apple
Silicon or a PC with a recent NVIDIA or AMD graphics card (an Intel Mac works too, more slowly), and about 15 GB free.

## Then

1. Setup's last screen has the prompt for your AI: click **Copy the prompt** and paste it into Claude Code (the Claude
   desktop app's **Code** tab), Codex or any AI that can work with files on your computer. It has the ReelForge
   folder's path in it, so there's nothing to drag in or open first.
2. Your AI gets to know your channel, then makes your first video.
3. Type `reelforge publish` in a terminal (or open ReelForge's icon and click the green **Publish** button) to post it.

Already have ReelForge? Its **Updates** tab (`reelforge updates`) installs this version in one click.

See the README for the full guide.
