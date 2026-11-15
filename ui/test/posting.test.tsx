// Posting from the window, on a channel of its own in a scratch folder (ui/test/posting_preload.py: four platforms that
// read nothing, three finished videos) with its real Python side and the real ledger: where each post stands on the
// Publish tab, the question before a post, what the extension does next (played by the test, the way the local server
// would write it down), the extension's own dialog, and what the window says when something isn't there: no extension,
// no video, no platform, a window that isn't listening, a call that failed. No browser opens and nothing is posted:
// each page that would open is listed in a file.

import { Database } from "bun:sqlite"
import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createTestRenderer } from "@opentui/core/testing"
import { createRoot } from "@opentui/react"
import { headless, type Headless } from "../scripts/headless"
import { clickText, find } from "../scripts/shots"
import { Bridge, ROOT } from "../src/bridge"
import { DashboardApp } from "../src/dashboard/App"
import type { ExtensionView, Init, PostingView } from "../src/dashboard/types"
import { toasts } from "../src/ui/Panels"

setDefaultTimeout(40_000)
let open: Headless | null = null
let tmp = ""
const SERVER = { REELFORGE_EXTENSION_PORT: "0" } // this window listens for the extension, on any free port
const CAPTION = "What if hail doubled every 2 seconds?"

/** A window on the scratch channel, with what the test asks the preload for. */
async function kit(ask: object = {}, size: [number, number] = [150, 45], env: Record<string, string> = SERVER, tab = "publish"): Promise<Headless> {
  tmp = mkdtempSync(join(tmpdir(), "reelforge-posting-"))
  open = await headless("dashboard", { tab }, size[0], size[1], {
    REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "posting_preload.py"),
    REELFORGE_TEST_ROOT: tmp,
    REELFORGE_TEST_POSTING: JSON.stringify(ask),
    REELFORGE_OPENED: join(tmp, "opened.txt"),
    REELFORGE_COPIED: join(tmp, "copied.txt"),
    ...env,
  })
  await open.settle(2500)
  return open
}

afterEach(async () => {
  await open?.close()
  open = null
  toasts.clear()
  if (tmp) rmSync(tmp, { recursive: true, force: true })
  tmp = ""
})

const frame = (h: Headless) => h.t.captureCharFrame()
const flat = (h: Headless) => frame(h).replace(/\s+/g, " ") // what's on screen, whatever line it wrapped at
const listed = (file: string) => (existsSync(join(tmp, file)) ? readFileSync(join(tmp, file), "utf-8").split("\n").filter(Boolean) : [])
const posting = (h: Headless) => h.bridge.view<PostingView>("posting")!
const state = (h: Headless, key: string) => posting(h).rows.find((r) => r.key === key)?.state
const popups = () => toasts.get().map((t) => (t.title ? `${t.title}: ${t.body}` : t.body)) // the pop-ups showing, oldest first

/** The scratch channel's stats database, from outside its window: the way another ReelForge window or the hourly job
 * gets at it. */
function inStats<T>(use: (db: Database) => T): T {
  const db = new Database(join(tmp, "dashboard", "data", "stats.db"))
  try {
    return use(db)
  } finally {
    db.close()
  }
}
const jobsMade = () => inStats((db) => (db.query("SELECT COUNT(*) AS n FROM post_jobs").get() as { n: number }).n)

/** Wait (up to 6 s) for something the Python side does after a click: each look comes after a settle. */
async function until(h: Headless, ok: () => boolean) {
  for (let i = 0; i < 30 && !ok(); i++) await h.settle(200)
}

/** A platform's row on the Publish tab: the line that starts with its name, and the ones under it up to the next. */
function row(h: Headless, name: string): string {
  const rows = frame(h).split("\n")
  const at = rows.findIndex((r) => new RegExp(`┃ ${name} +[○●✓×?]`).test(r))
  if (at < 0) return ""
  const text = (r: string) => r.slice(r.lastIndexOf("┃") + 1).replace(/[█▀▄]\s*$/, "").trim() // (less the scrollbar)
  const next = rows.findIndex((r, i) => i > at && /┃ (\S|$)/.test(r.slice(r.lastIndexOf("┃"))))
  return rows.slice(at, next < 0 ? at + 4 : next).map(text).join(" ").trim()
}

/** What the open dialog says, as one line of words (whatever row each wrapped at), without the tab round it. */
function dialog(h: Headless): string {
  const rows = frame(h).split("\n").map((r) => [...r])
  const top = rows.findIndex((r, y) => y > 0 && r.includes("╭")) // (row 0 has the Publish button's box)
  if (top < 0) return ""
  const left = rows[top]!.indexOf("╭")
  const right = rows[top]!.lastIndexOf("╮")
  const bottom = rows.findIndex((r, y) => y > top && r[left] === "╰")
  return rows.slice(top + 1, bottom).map((r) => r.slice(left + 1, right).join("")).join(" ").replace(/\s+/g, " ")
}

/** Click some text at or below a row (a button in a dialog that is also on the tab under it). */
async function clickBelow(h: Headless, text: string, from: number, dx = 2) {
  const at = find(h, text, from)
  if (!at) throw new Error(`"${text}" isn't on screen below row ${from}`)
  await h.t.mockMouse.click(at[0] + dx, at[1])
  await h.settle(500)
}

describe("where each post stands", () => {
  test("a row for each platform says it in words, with the buttons for it, and they do what they say", async () => {
    const h = await kit({ extension: "on", jobs: { tiktok: ["uploading"], youtube: ["ready"], instagram: ["opening", "failed"], x: ["ready", "unknown"] } })
    expect(flat(h)).toContain("POST ✓ QA passed and the review says APPROVED.")
    expect(row(h, "TikTok")).toMatch(/● uploading 43% +▐ Cancel ▌/)
    expect(row(h, "YouTube")).toContain("● ready: check it in the browser, then press Post there")
    expect(row(h, "YouTube")).toMatch(/▐ It's posted ▌ ▐ Cancel ▌/)
    expect(row(h, "Instagram")).toContain("× failed at finding the upload box: Instagram changed its page, so the extension lost its way: post this one by hand")
    expect(row(h, "Instagram")).toContain("▐ Try again ▌")
    expect(row(h, "X")).toContain("? did it post? ReelForge lost track: check your profile there")
    expect(row(h, "X")).toMatch(/▐ It's posted ▌ ▐ It didn't post ▌/)
    // Your word on the one ReelForge lost track of, and a cancel: the ledger takes both, and the rows follow.
    await clickText(h, "It didn't post", 2)
    await until(h, () => state(h, "x") === "cancelled")
    expect(row(h, "X")).toContain("○ cancelled")
    await clickText(h, "▐ Cancel ▌", 3) // TikTok's, the first on screen
    await until(h, () => state(h, "tiktok") === "cancelled")
    expect(row(h, "TikTok")).toContain("○ cancelled (you cancelled it)")
    expect(row(h, "TikTok")).toContain("▐ Try again ▌")
    await clickText(h, "It's posted", 2) // YouTube's: filled in, and you pressed Post there
    await until(h, () => state(h, "youtube") === "posted")
    expect(row(h, "YouTube")).toContain("✓ posted")
    expect(frame(h)).not.toContain("Something went wrong")
  })

  test("a post a stats read found has its numbers and its link; one nobody picked up can be tried again", async () => {
    const h = await kit({ extension: "on", jobs: { tiktok: ["queued"], youtube: ["expired"] }, spotted: { instagram: 2100 } })
    expect(row(h, "TikTok")).toMatch(/● waiting for the extension to pick it up +▐ Cancel ▌/)
    expect(row(h, "YouTube")).toContain("○ the extension didn't pick it up")
    expect(row(h, "YouTube")).toContain("▐ Try again ▌")
    expect(row(h, "Instagram")).toMatch(/✓ posted +2\.10K views, 24h ago/)
    expect(row(h, "Instagram")).toMatch(/▐ Open ▌ ▐ Post again… ▌/)
    expect(row(h, "X")).toBe("X          ○ not posted")
    expect(frame(h)).toContain("Instagram ✓") // its by-hand button: it's up there
    await clickText(h, "▐ Open ▌", 3)
    await until(h, () => listed("opened.txt").length > 0)
    expect(listed("opened.txt")).toEqual(["https://example.invalid/instagram/1"])
  })

  test("everything that starts a post shows in a window 120 columns by 30 rows", async () => {
    const h = await kit({}, [120, 30])
    const f = frame(h)
    for (const text of ["POST  ✓ QA passed", "TikTok     ○ not posted", "X          ○ not posted", "▐ Post… ▌", "○ Browser extension not connected", "BY HAND", "▐ Open all 4 → ▌", "Show file", "TikTok →", "YouTube →", "Instagram →", "X →", "Copy TikTok text", "Copy YouTube hashtags", "Copy Instagram text"])
      expect(f).toContain(text)
  })
})

describe("Post…", () => {
  test("asks first with each platform's exact text and account, then hands the ticked ones to the page in your browser", async () => {
    const h = await kit({ extension: "on" })
    await clickText(h, "Post…", 2)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    const said = dialog(h)
    for (const text of [
      "ep03_hail.mp4",
      "✓ QA passed and the review says APPROVED.",
      "ReelForge opens each upload page in your browser, attaches the video and fills in the text. You check it and press Post.",
      "Tested against copies of the upload pages, not yet on the live sites",
      `✓ TikTok @me Caption ${CAPTION} #fyp #whatif #weather`,
      `✓ YouTube @me Title ${CAPTION} #shorts Description #fyp #whatif #weather`,
      `✓ Instagram @me Caption ${CAPTION} #fyp #whatif #weather`,
      `✓ X @me Post ${CAPTION}`,
      "▐ Start 4 posts ▌",
    ])
      expect(said).toContain(text)
    expect(frame(h)).not.toContain("Stop before the Post button") // nothing to switch: you press Post
    await clickText(h, "✓  X", 1) // not on X this time
    expect(frame(h)).toContain("○  X")
    expect(frame(h)).toContain("▐ Start 3 posts ▌")
    await clickText(h, "Start 3 posts", 2)
    await until(h, () => listed("opened.txt").length > 0 && !frame(h).includes("POST THIS VIDEO"))
    // The page ReelForge opened is its own, on this computer, for this batch: the extension takes the posts there.
    const [url] = listed("opened.txt")
    expect(url).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/post\/pb_[0-9a-f]{16}\?k=/)
    const page = await fetch(url!)
    expect(page.status).toBe(200)
    expect(await page.text()).toContain('data-rf-page="post"')
    await until(h, () => state(h, "instagram") === "queued")
    for (const name of ["TikTok", "YouTube", "Instagram"]) expect(row(h, name)).toContain("● waiting for the extension to pick it up")
    expect(row(h, "X")).toBe("X          ○ not posted")
    // The extension at work: nothing is pressed here, and the row follows within a second or two.
    await h.bridge.call("test_extension", "tiktok", ["uploading"])
    await until(h, () => state(h, "tiktok") === "uploading")
    expect(row(h, "TikTok")).toContain("● uploading 43%")
    await h.bridge.call("test_extension", "tiktok", ["filling", "ready"])
    await until(h, () => state(h, "tiktok") === "ready")
    expect(row(h, "TikTok")).toContain("● ready: check it in the browser, then press Post there")
    await h.bridge.call("test_extension", "tiktok", ["posted"])
    await until(h, () => state(h, "tiktok") === "posted")
    expect(row(h, "TikTok")).toContain("✓ posted")
    expect(frame(h)).not.toContain("Something went wrong")
  })

  test("says what stops a platform, and posts a video that is already up there only when you tick it", async () => {
    const h = await kit({ extension: "on", jobs: { youtube: ["uploading"] }, spotted: { instagram: 2100 } })
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(dialog(h)).toContain("– YouTube @me A post of this video is already under way there. Cancel it first.")
    expect(dialog(h)).toContain("○ Instagram @me Already posted: ReelForge found this video's post there. Tick it to post there again.")
    expect(frame(h)).toContain("▐ Start 2 posts ▌") // TikTok and X
    await clickText(h, "–  YouTube", 1) // no box to tick
    expect(frame(h)).toContain("▐ Start 2 posts ▌")
    await clickText(h, "○  Instagram", 1)
    expect(frame(h)).toContain("✓  Instagram")
    await clickText(h, "Start 3 posts", 2)
    await until(h, () => state(h, "instagram") === "queued")
    expect(state(h, "youtube")).toBe("uploading") // as it was
    expect([state(h, "tiktok"), state(h, "x")]).toEqual(["queued", "queued"])
  })

  test("Post again on a platform's row asks about that platform alone", async () => {
    const h = await kit({ extension: "on", spotted: { instagram: 2100 } })
    await clickText(h, "Post again…", 2)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(frame(h)).toContain("✓  Instagram")
    expect(frame(h)).toContain("○  TikTok") // free, and not asked for
    expect(frame(h)).toContain("▐ Start 1 post ▌")
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(300)
    expect(frame(h)).not.toContain("POST THIS VIDEO")
    expect(state(h, "instagram")).toBe("spotted") // nothing was started
  })

  test("the question is about one video: when another is the picked one by now it closes, and its yes starts nothing", async () => {
    const h = await kit({ extension: "on" })
    await clickText(h, "clip.mp4", 2)
    await until(h, () => posting(h).video?.name === "clip.mp4")
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(dialog(h)).toContain("clip.mp4")
    expect(dialog(h)).toContain("Caption Clip #fyp #whatif #weather")
    const asked = posting(h).video!.path
    // Another ReelForge window hides clip.mp4 (what its Hide writes): this window's list then picks its newest video
    // by itself, under the question.
    inStats((db) => {
      db.run("INSERT OR REPLACE INTO meta (key, value) VALUES ('hidden_videos', ?)", [JSON.stringify([asked])])
      db.run("INSERT INTO meta (key, value) VALUES ('changes', '1') ON CONFLICT (key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")
    })
    await until(h, () => posting(h).video?.name === "ep03_hail.mp4" && !frame(h).includes("POST THIS VIDEO"))
    expect(posting(h).video?.name).toBe("ep03_hail.mp4")
    expect(frame(h)).not.toContain("POST THIS VIDEO") // nothing left to say yes to
    expect(popups()).toContain("Nothing was started: clip.mp4 isn't the picked video any more, so the question about it closed.")
    // A yes that was on its way as the pick changed: the Python side starts nothing for it either.
    expect(await h.bridge.call("post_with_extension", ["tiktok", "x"], "Clip", "#fyp #whatif #weather", true, [], asked)).toBeNull()
    await h.settle(600)
    expect(popups().at(-1)).toContain("The question was about clip.mp4, and ep03_hail.mp4 is the picked video now.")
    expect(jobsMade()).toBe(0)
    expect(posting(h).rows.every((r) => r.state === "none")).toBe(true)
    expect(listed("opened.txt")).toEqual([]) // no page for the extension either
  })

  test("a video that isn't reviewed can't go with the extension and says why, and by hand is not held back", async () => {
    const h = await kit({ extension: "on" })
    await clickText(h, "ep02_duck.mp4", 2)
    await until(h, () => posting(h).video?.name === "ep02_duck.mp4" && posting(h).review.state === "unreviewed")
    expect(flat(h)).toContain("POST ! QA passed, but there is no review yet (build/qa/review.md has to say APPROVED).")
    await clickText(h, "▐ Post… ▌", 3) // greyed out
    await h.settle(600)
    expect(frame(h)).not.toContain("POST THIS VIDEO")
    expect(frame(h)).not.toContain("BROWSER EXTENSION")
    await clickText(h, "TikTok →", 2)
    await until(h, () => listed("opened.txt").some((l) => l.includes("tiktok.com")))
    expect(listed("opened.txt")).toEqual(["reveal ep02_duck.mp4", "https://www.tiktok.com/tiktokstudio/upload"])
    expect(listed("copied.txt")[0]).toBe("What if a duck doubled every 2 seconds? #fyp #whatif #weather")
    // Its row says so until a stats read spots the post: all ReelForge knows of a post made by hand.
    await until(h, () => row(h, "TikTok").includes("upload page opened"))
    expect(row(h, "TikTok")).toMatch(/○ not posted +upload page opened 0m ago, not spotted yet/)
  })

  test("where the extension may press Post, the question has a switch for it, and it stops before Post unless you flip it", async () => {
    // Press Post for me is on offer for a platform only once a post made with the extension is up there: TikTok, here.
    const h = await kit({ extension: "on", jobs: { tiktok: ["ready", "posted"] } })
    await clickText(h, "clip.mp4", 2) // another video
    await until(h, () => posting(h).video?.name === "clip.mp4")
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(dialog(h)).toContain("Not checked by ReelForge: this video didn't come from the pipeline.")
    expect(dialog(h)).toContain("Stop before the Post button")
    expect(dialog(h)).toContain("You check it and press Post.") // as it starts: you press it
    expect(dialog(h)).not.toContain("the extension presses Post too")
    const [x, y] = find(h, "Stop before the Post button")!
    await h.t.mockMouse.click(x + 31, y) // its switch
    await h.settle(300)
    expect(dialog(h)).toContain("Where you switched on Press Post for me in the extension's settings, the extension presses Post too. Everywhere else, and always on X, you check it and press Post.")
    await clickText(h, "✓  YouTube", 1) // TikTok and X will do
    await clickText(h, "✓  Instagram", 1)
    expect(dialog(h)).toContain("Stop before the Post button") // still: TikTok is ticked
    await clickText(h, "Start 2 posts", 2)
    await until(h, () => listed("opened.txt").length > 0 && !frame(h).includes("POST THIS VIDEO"))
    // What the page in the browser is told about the two posts: the press is allowed on TikTok, and never on X.
    const batch = await (await fetch(listed("opened.txt")[0]!.replace("/post/", "/v1/page/batch/"))).json()
    expect(Object.fromEntries(batch.jobs.map((j: { platform: string; press: boolean }) => [j.platform, j.press]))).toEqual({ tiktok: true, x: false })
  })

  test("the demo shows the question and starts nothing", async () => {
    open = await headless("dashboard", { demo: true, only_demos: true, tab: "publish" }, 150, 45, { REELFORGE_BRIDGE_PRELOAD: join(ROOT, "scripts", "shots_preload.py") })
    const h = open
    await h.settle(2500)
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(dialog(h)).toContain("This is the demo: nothing is posted from it.")
    expect(frame(h)).toContain("–  TikTok")
    expect(frame(h)).toContain("▐ Close ▌")
    await clickText(h, "▐ Start ▌", 3) // greyed out
    expect(frame(h)).toContain("POST THIS VIDEO")
    expect(posting(h).rows.every((r) => r.state === "none")).toBe(true)
  })
})

describe("without the extension", () => {
  test("Post… explains the one-time set-up and offers by hand right there; connecting a browser shows at once", async () => {
    const h = await kit()
    expect(frame(h)).toContain("○ Browser extension not connected")
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("BROWSER EXTENSION"))
    const said = dialog(h)
    for (const text of [
      "○ Browser extension not connected",
      "You check it and press Post.",
      "not yet on the live sites",
      "SET IT UP, ONCE",
      "1. Open your browser's extensions page: type chrome://extensions in the address bar.",
      "3. Press Load unpacked and pick the extension folder inside your ReelForge folder.",
      "4. Press Connect a browser.",
      "Or post by hand now:",
    ])
      expect(said).toContain(text)
    // By hand, from here: the platform's own flow, as its button on the tab does it.
    await clickBelow(h, "TikTok →", find(h, "Or post by hand now:")![1])
    await until(h, () => listed("opened.txt").some((l) => l.includes("tiktok.com")))
    expect(listed("opened.txt")).toEqual(["reveal ep03_hail.mp4", "https://www.tiktok.com/tiktokstudio/upload"])
    expect(listed("copied.txt")[0]).toBe(`${CAPTION} #fyp #whatif #weather`)
    // Connect a browser opens ReelForge's own page for it, and the dialog waits for the extension there.
    await clickText(h, "▐ Connect a browser ▌", 3)
    await until(h, () => listed("opened.txt").some((l) => l.includes("/pair?k=")))
    expect(listed("opened.txt").at(-1)).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/pair\?k=/)
    expect(dialog(h)).toContain("Waiting for your browser")
    // The extension connects (what the pair page does, written the way the local server writes it).
    const store = join(tmp, "dashboard", "data", "extension.json")
    const now = new Date().toISOString().replace(/\.\d+Z$/, "+00:00")
    writeFileSync(store, JSON.stringify({ ...JSON.parse(readFileSync(store, "utf-8")), paired: [{ install: "install-edge", name: "Edge on this PC", version: "0.1.0", token_sha256: "cd".repeat(32), paired_at: now, last_seen: now }] }))
    await until(h, () => frame(h).includes("CONNECTED BROWSERS"))
    expect(dialog(h)).toContain("● Browser extension connected Edge on this PC · v0.1.0")
    expect(dialog(h)).not.toContain("Waiting for your browser")
    expect(frame(h)).not.toContain("Or post by hand now:")
    toasts.clear() // the pop-ups about the two pages that opened sit over the dialog's corner
    await h.settle(300)
    await clickBelow(h, "▐ Post… ▌", find(h, "Connect another browser")![1], 3) // straight on to the question
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(frame(h)).toContain("▐ Start 4 posts ▌")
  })

  test("one line says the extension exists, and Hide puts it away for good", async () => {
    const h = await kit({}, [120, 30])
    expect(frame(h)).toContain("It can fill in the upload pages for you.")
    expect(frame(h)).toMatch(/▐ Set it up ▌ +Hide/)
    await clickText(h, " Hide ", 2)
    await until(h, () => h.bridge.view<ExtensionView>("extension")!.hint_dismissed)
    expect(frame(h)).not.toContain("It can fill in the upload pages for you.")
    expect(frame(h)).not.toContain("Set it up")
    expect(frame(h)).toContain("○ Browser extension not connected") // still said, in one line
    expect(frame(h)).toContain("▐ Post… ▌")
  })

  test("a channel with no platform connected says where to connect one, and one with no video says so", async () => {
    let h = await kit({ platforms: [] })
    expect(flat(h).split("Connect an account on the Accounts tab first.").length).toBe(2) // said once
    expect(frame(h)).toContain("Show file")
    expect(frame(h)).not.toContain("Open all")
    await clickText(h, "Open Accounts", 2)
    await until(h, () => frame(h).includes("○ not connected"))
    expect(frame(h)).toContain("○ not connected")
    await open?.close()
    rmSync(tmp, { recursive: true, force: true })
    h = await kit({ videos: false })
    expect(frame(h)).toContain("POST  No videos yet.")
    await clickText(h, "▐ Post… ▌", 3) // greyed out
    expect(frame(h)).not.toContain("POST THIS VIDEO")
    expect(frame(h)).not.toContain("BROWSER EXTENSION")
  })
})

describe("the extension's dialog", () => {
  test("lists each connected browser with what it's signed in to, and disconnects one only after asking", async () => {
    const h = await kit({ extension: "on" })
    expect(frame(h)).toContain("● Browser extension connected") // folded to one line beside Post…
    await clickText(h, "Browsers…", 2)
    await until(h, () => frame(h).includes("CONNECTED BROWSERS"))
    const said = dialog(h)
    expect(said).toContain("● Browser extension connected Chrome on this PC · v0.1.0")
    expect(said).toContain("● Chrome on this PC v0.1.0 · seen")
    expect(said).toContain("signed in there: TikTok @me · YouTube yes · Instagram no")
    expect(said).toContain("It is apart from the accounts connected on the Accounts tab")
    expect(frame(h)).not.toContain("SET IT UP, ONCE")
    const store = () => JSON.parse(readFileSync(join(tmp, "dashboard", "data", "extension.json"), "utf-8")).paired.length
    await clickText(h, "▐ Disconnect ▌", 3)
    expect(dialog(h)).toContain("Disconnect Chrome on this PC?")
    expect(store()).toBe(1) // asked, not done
    await clickText(h, "Keep it", 2)
    expect(dialog(h)).not.toContain("Disconnect Chrome on this PC?")
    await clickText(h, "▐ Disconnect ▌", 3)
    await clickText(h, "▐ Disconnect ▌", 3) // the red one, in its place now
    await until(h, () => store() === 0)
    expect(store()).toBe(0)
    await until(h, () => frame(h).includes("SET IT UP, ONCE"))
    expect(frame(h)).toContain("○ Browser extension not connected")
    expect(flat(h)).toContain("Chrome on this PC is disconnected.")
  })

  test("a window that isn't listening says why, instead of starting a post it can't hand over", async () => {
    const h = await kit({ extension: "on" }, [150, 45], {}) // as the tests run by default: no local server
    const why = "ReelForge isn't listening for the extension here (it is switched off)."
    expect(flat(h)).toContain(`▐ Post… ▌ ${why}`)
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("BROWSER EXTENSION"))
    expect(dialog(h)).toContain(why)
    expect(frame(h)).toContain("Or post by hand now:")
    expect(frame(h)).not.toContain("POST THIS VIDEO")
    expect(posting(h).rows.every((r) => r.state === "none")).toBe(true)
  })

  test("an extension of another version says which side to update", async () => {
    const h = await kit({ extension: "old" })
    expect(frame(h)).toContain("● Browser extension needs an update")
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("CONNECTED BROWSERS"))
    expect(dialog(h)).toContain("The browser extension is older than this ReelForge. Press Reload on its card on your browser's extensions page.")
    expect(frame(h)).toContain("· needs an update")
    expect(frame(h)).not.toContain("POST THIS VIDEO")
  })
})

describe("the extension on the Accounts tab", () => {
  test("has one line above the platforms, which opens its dialog; the demo has none", async () => {
    const h = await kit({ extension: "on" }, [160, 44], {}, "accounts")
    const rows = frame(h).split("\n")
    const line = rows.findIndex((r) => r.includes("BROWSER EXTENSION"))
    expect(rows[line]).toMatch(/BROWSER EXTENSION +● Browser extension connected +Chrome on this PC · v0\.1\.0 · seen \d+m ago.*▐ Browsers… ▌/)
    expect(line).toBeLessThan(rows.findIndex((r) => r.includes("TikTok"))) // above the platforms' panels, not one of them
    await clickText(h, "Browsers…", 2)
    await until(h, () => frame(h).includes("CONNECTED BROWSERS"))
    expect(flat(h)).toContain("signed in there: TikTok @me · YouTube yes · Instagram no")
    await open?.close()
    open = await headless("dashboard", { demo: true, only_demos: true, tab: "accounts" }, 160, 44, { REELFORGE_BRIDGE_PRELOAD: join(ROOT, "scripts", "shots_preload.py") })
    await open.settle(2500)
    expect(frame(open)).toContain("Open profile")
    expect(frame(open)).not.toContain("BROWSER EXTENSION")
  })

  test("not connected, it says so and offers the set-up", async () => {
    const h = await kit({}, [120, 30], {}, "accounts")
    const rows = frame(h).split("\n")
    expect(rows.find((r) => r.includes("BROWSER EXTENSION"))).toMatch(/○ Browser extension not connected.*▐ Set up… ▌/)
    await clickText(h, "Set up…", 2)
    await until(h, () => frame(h).includes("SET IT UP, ONCE"))
    expect(frame(h)).toContain("SET IT UP, ONCE")
    expect(frame(h)).not.toContain("Or post by hand now:") // that's for a press of Post…
  })
})

describe("when something goes wrong", () => {
  test("a call that fails says so in a pop-up, and the window carries on", async () => {
    const h = await kit({ extension: "on", fail: "post_info" })
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("Something went wrong"))
    expect(frame(h)).toContain("Something went wrong")
    expect(frame(h)).toContain("RuntimeError: boom")
    expect(frame(h)).not.toContain("POST THIS VIDEO")
    await clickText(h, "Browsers…", 2) // the rest still works
    await until(h, () => frame(h).includes("CONNECTED BROWSERS"))
    expect(frame(h)).toContain("CONNECTED BROWSERS")
  })

  test("until the Python side has said where the video stands, the form says it is looking, and Post waits", async () => {
    // A Python side that never answers: what the window draws before any view has arrived.
    class Silent extends Bridge {
      asked: string[] = []
      call<T = any>(method: string): Promise<T> {
        this.asked.push(method)
        return new Promise<T>(() => {})
      }
    }
    const bridge = new Silent()
    const init = { platforms: [], tab: "publish", order: [], default_order: [], drawer: false, demo: false, anvil: [], quadrants: false, help: [], keys: [], default_tags: "", caption_placeholder: "", tags_placeholder: "", copies: {}, profile: { key: ".", name: "Night Physics", main: true, several: false, elsewhere: 0 } } as unknown as Init
    const t = await createTestRenderer({ width: 120, height: 30 })
    const root = createRoot(t.renderer)
    root.render(<DashboardApp bridge={bridge} init={init} quit={() => {}} />)
    for (let i = 0; i < 3; i++) await t.renderOnce()
    await Bun.sleep(300)
    await t.renderOnce()
    const f = t.captureCharFrame()
    expect(f).toContain("POST  Looking at this video…")
    expect(f).toContain("▐ Post… ▌")
    expect(bridge.asked).toContain("ready") // the window said it is listening
    const at = f.split("\n").findIndex((r) => r.includes("▐ Post… ▌"))
    await t.mockMouse.click(f.split("\n")[at]!.indexOf("Post…") + 1, at)
    await t.renderOnce()
    expect(bridge.asked).not.toContain("post_info") // greyed out: nothing to ask about yet
    root.unmount()
    t.renderer.destroy()
  })
})
