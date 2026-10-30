## What's new in 2.1.0

- **ReelForge can move into a folder of its own.** The Settings tab says where it is, under On this computer.
  - Kept somewhere else, like the Desktop? **Move…** puts it where the install line does: your user folder's ReelForge.
  - Everything in it goes along in one step. The reelforge command, the icon and Background refresh follow it.
  - In a terminal it's `reelforge move`, or `reelforge move FOLDER` for another folder on the same drive.
- **The install line finds a ReelForge you already have** somewhere else, and points you to Move, not a second copy.
- **Uninstall puts Windows Terminal's ReelForge profile in the Recycle Bin** too, as it says it does.

## What's new in 2.0.0

- **Bigger version numbers.** This is 2.0.0, and the updates after it go 2.1.0, 2.2.0 and so on.
- **Each channel keeps to its own settings** and delivery folder, even when another profile is picked in the terminal.
- **Refresh every 0 minutes means only when you press ↻ Refresh,** for browser-read and new accounts too.
  - The hourly background job is unchanged.
- **Your marks survive a move.** Posted marks, upload matches and hidden videos keep working on a new computer.
  - That's a Windows stats database moved to a Mac or Linux PC.
- **Folders named like videos** (clip.mp4) are left off the Publish tab.
- **Any channel name works on Windows.** CON, PRN, AUX, NUL, COM1-9 and LPT1-9 get a folder Windows can make.
- **0 hashtags works everywhere:** new episodes, captions, caption checks, the publish check and the window's hints.
- **JSON for scripts and support:** `reelforge doctor --json` writes the health check as JSON.
  - `reelforge profiles --json` lists your profiles as JSON. Both only read.

Already have ReelForge? Open the **Updates** tab and click **Update and restart**.

---

ReelForge lets Claude Code or Codex make faceless short videos for TikTok, YouTube Shorts, Instagram Reels and X,
then shows you how each one does once you've posted it. Nothing is ever posted for you.

## Install

- **Windows 10 / 11:** open PowerShell, paste `irm https://github.com/dan2756/ReelForge/releases/latest/download/install.ps1 | iex` and press Enter.
- **Mac or Linux:** open Terminal, paste `curl -fsSL https://github.com/dan2756/ReelForge/releases/latest/download/install.sh | bash` and press Enter.

It downloads ReelForge straight from GitHub into a ReelForge folder in your user folder, checks it against `SHA256SUMS.txt` and starts setup, so neither your browser nor your computer warns about it. The other files below are what it downloads.

Installed ReelForge with the old Setup.exe? Its Updates tab can't install this version, so paste the Windows line into PowerShell instead: it finds that ReelForge and updates it in place, keeping your channel, videos, stats, logins and settings.

Setup shows what ReelForge still needs (Blender 4.5 LTS, ffmpeg, the sound effects, the `reelforge` command,
ReelForge's icon, and on a Mac or Linux PC a private Python or a Chromium if it hasn't got one), sets it all up inside
the ReelForge folder once you say yes, then asks a few questions about your channel. You'll need Claude Code (a
paid Claude plan or an API account) or Codex, a Mac with Apple Silicon or a PC with a recent NVIDIA or AMD graphics card (an Intel
Mac works too, more slowly), and about 15 GB of free disk space.

## Then

1. Open the ReelForge folder in Claude Code (the Claude desktop app's **Code** tab, then **Open folder…**), or run
   `codex` in it.
2. Say **"make my first video"**.
3. Type `reelforge publish` in a terminal (or open ReelForge's icon and click the green **Publish** button) to post it.

Already have ReelForge? Its **Updates** tab (`reelforge updates`) installs this version in one click.

See the README for the full guide.
