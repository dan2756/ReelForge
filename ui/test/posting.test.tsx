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
import { TextAttributes } from "@opentui/core"
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
const NO_SERVER = { REELFORGE_EXTENSION_PORT: "off" } // ... and this one doesn't, whatever the tests were started with
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

/** The label of the button that has the keyboard: it's the underlined one. */
function focused(h: Headless): string {
  for (const line of h.t.captureSpans().lines) {
    const text = line.spans.filter((sp) => sp.attributes & TextAttributes.UNDERLINE).map((sp) => sp.text).join("").trim()
    if (text) return text
  }
  return ""
}

/** Wait (up to 6 s) for something the Python side does after a click: each look comes after a settle. */
async function until(h: Headless, ok: () => boolean) {
  for (let i = 0; i < 30 && !ok(); i++) await h.settle(200)
}

/** A platform's row on the Publish tab: the line that starts with its name, and the ones under it up to the next (or,
 * under the last platform's, up to the row for YouTube's API, whose button is at its right-hand end). */
function row(h: Headless, name: string): string {
  const rows = frame(h).split("\n")
  const at = rows.findIndex((r) => new RegExp(`┃ ${name} +[○●✓×?]`).test(r))
  if (at < 0) return ""
  const text = (r: string) => r.slice(r.lastIndexOf("┃") + 1).replace(/[█▀▄]\s*$/, "").trim() // (less the scrollbar)
  const next = rows.findIndex((r, i) => i > at && (/┃ (\S|$)/.test(r.slice(r.lastIndexOf("┃"))) || r.includes("YouTube API…")))
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

/** For the batch whose page ReelForge opened (its address, from opened.txt): platform -> whether the extension may
 * press Post there, as the page in the browser is told it. */
async function pressFor(page: string): Promise<Record<string, boolean>> {
  const batch = await (await fetch(page.replace("/post/", "/v1/page/batch/"))).json()
  return Object.fromEntries(batch.jobs.map((j: { platform: string; press: boolean }) => [j.platform, j.press]))
}

/** Click a button on a platform's row of the Publish tab (the same words can be on another platform's row). */
async function clickOnRow(h: Headless, name: string, text: string) {
  const at = frame(h).split("\n").findIndex((r) => new RegExp(`┃ ${name} +[○●✓×?]`).test(r))
  if (at < 0) throw new Error(`${name} has no row on screen`)
  await clickBelow(h, text, at)
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
    // (its video was on TikTok's page when you called it off, where you can finish by hand: that is yours to say)
    expect(row(h, "TikTok")).toMatch(/▐ Try again ▌ ▐ It's posted ▌/)
    await clickOnRow(h, "YouTube", "It's posted") // filled in, and you pressed Post there
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

  test("a row whose words wrap keeps every letter, whatever length its lines come to", async () => {
    // The form scrolls, and a text in a scrolling area is laid out a column or two wider than it has: a line that
    // came to exactly that width lost its last letter at the form's edge ("ReelForg sent no answer").
    const h = await kit({ extension: "on" })
    const view = posting(h)
    for (let n = 70; n <= 90; n++) {
      const said = `✓ posted ${"x".repeat(n - 19)} ReelForge sent no answer to that, because you haven't given one, so this is your channel's own setting.`
      h.bridge.emit("posting", { ...view, rows: view.rows.map((r) => (r.key === "youtube" ? { ...r, state: "posted", line: [[said, ""]], actions: ["open_post", "post_again"] } : r)) })
      await h.settle(120)
      expect(row(h, "YouTube")).toContain(said)
    }
    // The review's own line beside POST wraps the same way.
    const head = () => {
      const rows = frame(h).split("\n")
      const at = rows.findIndex((r) => r.includes("┃ POST  "))
      const text = (r: string) => r.slice(r.lastIndexOf("┃") + 1).replace(/[█▀▄]\s*$/, "").trim()
      return rows.slice(at, rows.findIndex((r) => /┃ TikTok +[○●✓×?]/.test(r))).map(text).join(" ").replace(/\s+/g, " ")
    }
    for (let n = 70; n <= 90; n++) {
      const said = `! ${"x".repeat(n - 12)} ReelForge has to see a review that says APPROVED for this cut before it posts it.`
      h.bridge.emit("posting", { ...view, review: { state: "unreviewed", line: [[said, ""]] } })
      await h.settle(120)
      expect(head()).toBe(`POST ${said}`)
    }
  })

  test("everything that starts a post shows in a window 120 columns by 30 rows", async () => {
    const h = await kit({}, [120, 30])
    const f = frame(h)
    for (const text of ["POST  ✓ QA passed", "TikTok     ○ not posted", "X          ○ not posted", "▐ Post… ▌", "○ Browser extension not connected", "BY HAND", "▐ Open all 4 → ▌", "Show file", "TikTok →", "YouTube →", "Instagram →", "X →", "Copy TikTok text", "Copy YouTube hashtags", "Copy Instagram text"])
      expect(f).toContain(text)
  })

  test("in a window 100 columns by 30 rows a connected extension's line keeps to its first words, and BY HAND stays in view", async () => {
    // Between 96 and about 106 columns the words don't fit beside Post… and Browsers… in one row. Wrapped whole, with
    // the browser's name and when it was last seen, they took six rows there and left only BY HAND's heading in view.
    for (const how of ["on", "waiting"]) {
      const h = await kit({ extension: how }, [100, 30])
      const rows = frame(h).split("\n")
      const post = rows.findIndex((r) => r.includes("▐ Post… ▌"))
      expect(rows[post]).toMatch(/▐ Post… ▌ +● Browser extension +Browsers…/)
      expect(rows[post + 1]).toMatch(/┃ +connected +$/)
      expect(rows.findIndex((r) => r.includes("BY HAND"))).toBe(post + 3) // the words' two rows, and the space under them
      for (const text of ["Chrome on this PC", "v0.1.0", "seen ", "wakes up"]) expect(frame(h)).not.toContain(text) // in its dialog
      for (const text of ["▐ Open all 4 → ▌", "Show file", "TikTok →", "YouTube →", "Instagram →", "Copy TikTok text", "Copy YouTube hashtags", "Copy Instagram text"])
        expect(frame(h)).toContain(text)
      await open?.close()
      open = null
      rmSync(tmp, { recursive: true, force: true })
    }
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
    // What that page is told about the three posts: Post is pressed for none of them. It stops before, as asked.
    expect(await pressFor(url!)).toEqual({ tiktok: false, youtube: false, instagram: false })
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

  test("a Post again that is called off leaves the post standing: its row says posted, and Post… still asks before posting there", async () => {
    const h = await kit({ extension: "on", jobs: { x: ["ready"] } })
    await clickText(h, "It's posted", 2)
    await until(h, () => state(h, "x") === "posted")
    expect(row(h, "X")).toContain("✓ posted")
    await clickText(h, "Post again…", 2)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    await clickText(h, "Start 1 post", 2)
    await until(h, () => state(h, "x") === "queued")
    await clickText(h, "▐ Cancel ▌", 3) // the new try, on X's row
    await until(h, () => state(h, "x") !== "queued")
    // The post that was up is still up, as far as ReelForge knows: the row says so again, not "cancelled".
    expect(state(h, "x")).toBe("posted")
    expect(row(h, "X")).toContain("✓ posted")
    expect(row(h, "X")).toContain("▐ Post again… ▌")
    expect(row(h, "X")).not.toContain("Try again")
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(dialog(h)).toContain("○ X @me Already posted with ReelForge's help. Tick it to post there again.")
    expect(frame(h)).toContain("▐ Start 3 posts ▌") // the other three: X only when you tick it
  })

  test("a Post again that fails leaves a post a stats read found standing: its row says posted, not failed", async () => {
    const h = await kit({ extension: "on", spotted: { instagram: 2100 } })
    await clickText(h, "Post again…", 2)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    await clickText(h, "Start 1 post", 2)
    await until(h, () => state(h, "instagram") === "queued")
    await h.bridge.call("test_extension", "instagram", ["opening", "failed"])
    await until(h, () => state(h, "instagram") === "spotted")
    // The video is up there, whatever became of the second try: the row goes by the post, with its own buttons.
    expect(state(h, "instagram")).toBe("spotted")
    expect(row(h, "Instagram")).toMatch(/✓ posted +2\.10K views, 24h ago/)
    expect(row(h, "Instagram")).toMatch(/▐ Open ▌ ▐ Post again… ▌/)
    expect(row(h, "Instagram")).not.toContain("failed")
    expect(row(h, "Instagram")).not.toContain("Try again")
    expect(frame(h)).toContain("Instagram ✓") // its by-hand button says so too
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(dialog(h)).toContain("○ Instagram @me Already posted: ReelForge found this video's post there. Tick it to post there again.")
  })

  test("the question opens with the keyboard on Cancel, and Enter twice on Start is one yes", async () => {
    const h = await kit({ extension: "on" })
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(focused(h)).toBe("Cancel")
    h.t.mockInput.pressKey("RETURN") // an Enter still held from pressing Post…: it starts nothing
    await h.settle(500)
    expect(frame(h)).not.toContain("POST THIS VIDEO")
    expect(jobsMade()).toBe(0)
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    h.t.mockInput.pressKey("TAB")
    await h.settle(200)
    expect(focused(h)).toBe("Start 4 posts")
    h.t.mockInput.pressKey("RETURN")
    h.t.mockInput.pressKey("RETURN") // again, before the window has drawn "Opening your browser…"
    await until(h, () => state(h, "x") === "queued" && !frame(h).includes("POST THIS VIDEO"))
    await h.settle(800)
    expect(posting(h).rows.map((r) => r.state)).toEqual(["queued", "queued", "queued", "queued"])
    expect(jobsMade()).toBe(4)
    expect(listed("opened.txt").length).toBe(1) // one batch, one page
    expect(popups()).toEqual([]) // and no "not started: a post is already under way" about the posts it just started
  })

  test("a yes is to the file the question was about: one written over under its name since starts nothing", async () => {
    const h = await kit({ extension: "on" })
    await clickText(h, "clip.mp4", 2)
    await until(h, () => posting(h).video?.name === "clip.mp4")
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    // A new render lands on the file while the question is open (the same name, other bytes).
    writeFileSync(posting(h).video!.path, "another render of the clip")
    h.t.mockInput.pressKey("TAB")
    await h.settle(200)
    expect(focused(h)).toBe("Start 4 posts")
    h.t.mockInput.pressKey("RETURN")
    await until(h, () => popups().length > 0)
    expect(popups()).toEqual(["Nothing was started: clip.mp4 changed after the question was shown: it is another file now, so nothing was started. Check the video, then press Post… again."])
    expect(jobsMade()).toBe(0)
    expect(listed("opened.txt")).toEqual([]) // no page for the extension
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
    expect(dialog(h)).not.toContain("Yes, press Post for me")
    const [x, y] = find(h, "Stop before the Post button")!
    await h.t.mockMouse.click(x + 31, y) // its switch
    await h.settle(300)
    // The switch alone presses nothing: the extension's own setting and your click for this post in the browser too.
    expect(dialog(h)).toContain("The extension presses Post only where Press Post for me is on in its settings, and after you click Yes, press Post for me on ReelForge's own page, which opens first. Never on X. Otherwise you press Post.")
    await clickText(h, "✓  YouTube", 1) // TikTok and X will do
    await clickText(h, "✓  Instagram", 1)
    expect(dialog(h)).toContain("Stop before the Post button") // still: TikTok is ticked
    await clickText(h, "Start 2 posts", 2)
    await until(h, () => listed("opened.txt").length > 0 && !frame(h).includes("POST THIS VIDEO"))
    // What the page in the browser is told about the two posts: the press is allowed on TikTok, and never on X.
    expect(await pressFor(listed("opened.txt")[0]!)).toEqual({ tiktok: true, x: false })
  })

  test("in a window 80 columns by 24 rows the question keeps its platforms in view with that switch flipped", async () => {
    // What the extension wants before it presses took six rows of that window, which left none for the platforms: two
    // of them were drawn over each other in the row that was there. It is three rows now.
    const h = await kit({ extension: "on", jobs: { tiktok: ["ready", "posted"] } }, [80, 24])
    const clip = h.bridge.view<{ items: { id: string }[] }>("files")!.items.find((i) => i.id.endsWith("clip.mp4"))! // (below the two videos the list has room for)
    await h.bridge.call("pick_file", clip.id)
    await until(h, () => posting(h).video?.name === "clip.mp4")
    h.t.mockInput.pressKey("\u001b[6~") // Page Down, to POST
    await h.settle(400)
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    const [x, y] = find(h, "Stop before the Post button")!
    await h.t.mockMouse.click(x + 31, y) // its switch
    await h.settle(300)
    for (const text of ["Never on X. Otherwise you press Post.", "✓  TikTok     @me", "Caption      Clip #fyp #whatif #weather", "Stop before the Post button", "▐ Cancel ▌", "▐ Start 4 posts ▌"])
      expect(frame(h)).toContain(text)
  })

  test("with that switch left alone, Post is pressed for you nowhere, also where Press Post for me is unlocked", async () => {
    const h = await kit({ extension: "on", jobs: { tiktok: ["ready", "posted"] } })
    await clickText(h, "clip.mp4", 2)
    await until(h, () => posting(h).video?.name === "clip.mp4")
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    expect(dialog(h)).toContain("Stop before the Post button") // on offer: TikTok is unlocked
    expect(dialog(h)).toContain("You check it and press Post.")
    await clickText(h, "Start 4 posts", 2)
    await until(h, () => listed("opened.txt").length > 0 && !frame(h).includes("POST THIS VIDEO"))
    expect(await pressFor(listed("opened.txt")[0]!)).toEqual({ tiktok: false, youtube: false, instagram: false, x: false })
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

describe("RECENT UPLOADS", () => {
  test("says where each post stands at every width: the file's name gives way, not the Post column", async () => {
    // The room went to the Video column, and Post, the one that says where a post stands, was cut at every width:
    // in a window 100 columns wide it was one letter.
    const h = await kit({ extension: "on", jobs: { tiktok: ["ready", "posted"], x: ["ready"] } }, [150, 45])
    for (const [w, rows, said] of [
      [150, 45, /ep03_hail\.mp4 +● +ready: check it in the browser, then press Post/],
      [120, 30, /ep03_hail\.mp4 +● +ready: check it in the bro/],
      [100, 30, /ep03_hail\.mp4 +● +ready: ch/],
      [80, 24, /ep03_hail\.mp4 +● +ready: check it in the browser,/],
    ] as const) {
      h.t.resize(w, rows)
      await h.settle(500)
      for (let i = 0; i < 12 && !/ep03_hail\.mp4 +●/.test(frame(h)); i++) {
        h.t.mockInput.pressKey("\u001b[6~") // Page Down, to the table
        await h.settle(250)
      }
      const lines = frame(h).split("\n")
      expect(lines.find((r) => /ep03_hail\.mp4 +●/.test(r)) ?? "").toMatch(said)
      expect(lines.find((r) => /ep03_hail\.mp4 +✓/.test(r)) ?? "").toMatch(/ep03_hail\.mp4 +✓ +(posted wi|live · )/)
    }
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
    expect(dialog(h)).toContain("● Waiting for your browser: the page that opened connects it by itself. No extension in that browser yet? The page says how to add it.")
    expect(popups().filter((p) => p.includes("Connecting your browser"))).toEqual([]) // the dialog says it: no pop-up over its buttons
    // The extension connects (what the pair page does, written the way the local server writes it).
    const store = join(tmp, "dashboard", "data", "extension.json")
    const now = new Date().toISOString().replace(/\.\d+Z$/, "+00:00")
    writeFileSync(store, JSON.stringify({ ...JSON.parse(readFileSync(store, "utf-8")), paired: [{ install: "install-edge", name: "Edge on this PC", version: "0.1.0", token_sha256: "cd".repeat(32), paired_at: now, last_seen: now }] }))
    await until(h, () => frame(h).includes("CONNECTED BROWSERS"))
    expect(dialog(h)).toContain("● Browser extension connected Edge on this PC · v0.1.0")
    expect(dialog(h)).not.toContain("Waiting for your browser")
    expect(frame(h)).not.toContain("Or post by hand now:")
    toasts.clear() // the pop-up about the upload page that opened by hand sits over the dialog's corner
    await h.settle(300)
    await clickBelow(h, "▐ Post… ▌", find(h, "Connect another browser")![1], 3) // straight on to the question
    await until(h, () => frame(h).includes("POST THIS VIDEO"))
    // TikTok's upload page was opened by hand a moment ago, further up. A post made there shows nowhere here until a
    // stats read finds it, so TikTok starts unticked: it goes only when you say again.
    expect(dialog(h)).toContain("Its upload page was opened by hand in the last six hours. A post made there may be up before a stats read has seen it. Tick it to post there again.")
    expect(frame(h)).toContain("▐ Start 3 posts ▌")
  })

  test("Show the extension folder says what it did in the dialog, where no pop-up covers Connect a browser", async () => {
    // Step 3 of the set-up, and Connect a browser is step 4. The fifteen-second pop-up sat on both, and on Close.
    const h = await kit({}, [120, 30])
    await clickText(h, "▐ Set it up ▌", 3)
    await until(h, () => frame(h).includes("BROWSER EXTENSION"))
    await clickText(h, "▐ Show the extension folder ▌", 3)
    await until(h, () => dialog(h).includes("The extension's folder:"))
    expect(dialog(h)).toMatch(/The extension's folder: (It is showing in .* and its path is copied\. On your browser's extensions page, switch on Developer mode, press Load unpacked and pick it\.|This ReelForge has no extension folder yet)/)
    expect(popups()).toEqual([])
    for (const [w, rows] of [[120, 30], [100, 30], [80, 24]] as const) {
      h.t.resize(w, rows)
      await h.settle(400)
      for (const button of ["▐ Connect a browser ▌", "▐ Show the extension folder ▌", "▐ Close ▌"]) expect(frame(h)).toContain(button)
    }
    // Connect a browser is one click away: the first click is the button's, not a pop-up's.
    await clickText(h, "▐ Connect a browser ▌", 3)
    await until(h, () => listed("opened.txt").some((l) => l.includes("/pair?k=")))
    expect(dialog(h)).toContain("● Waiting for your browser")
    expect(dialog(h)).not.toContain("The extension's folder:")
  })

  test("one line says the extension exists, and Hide puts it away for good", async () => {
    const h = await kit({}, [120, 30])
    // What it does comes with how far that has been tested, in this window too, which only has room for the middle
    // of the line: nothing else on the tab says it.
    expect(frame(h)).toMatch(/┃ Fills in upload pages \(tested on copies\)\. +▐ Set it up ▌ +Hide/)
    await clickText(h, " Hide ", 2)
    await until(h, () => h.bridge.view<ExtensionView>("extension")!.hint_dismissed)
    expect(frame(h)).not.toContain("Fills in upload pages")
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
    // A double click on Disconnect, on any column of it, asks and then lands on Keep it, which comes up in its place.
    // The red Disconnect came up there, and the browser was disconnected before the question could be read.
    const at = find(h, "▐ Disconnect ▌")!
    for (const dx of [0, 6, 13]) {
      await h.t.mockMouse.click(at[0] + dx, at[1])
      await h.settle(150)
      expect(dialog(h)).toContain("Disconnect Chrome on this PC?")
      await h.t.mockMouse.click(at[0] + dx, at[1])
      await h.settle(400)
      expect([dx, store(), dialog(h).includes("Disconnect Chrome on this PC?")]).toEqual([dx, 1, false])
    }
    await clickText(h, "▐ Disconnect ▌", 3)
    expect(frame(h)).toMatch(/▐ Disconnect ▌ ▐  Keep it   ▌/) // as wide as the button it takes the place of
    await clickText(h, "▐ Disconnect ▌", 3) // the red one, beside Keep it
    await until(h, () => store() === 0)
    expect(store()).toBe(0)
    await until(h, () => frame(h).includes("SET IT UP, ONCE"))
    expect(frame(h)).toContain("○ Browser extension not connected")
    expect(flat(h)).toContain("Chrome on this PC is disconnected.")
  })

  test("a browser that connects again is seen too: the dialog stops waiting for it", async () => {
    const h = await kit({ extension: "on" })
    await clickText(h, "Browsers…", 2)
    await until(h, () => frame(h).includes("CONNECTED BROWSERS"))
    await clickText(h, "▐ Connect another browser ▌", 3)
    await until(h, () => listed("opened.txt").some((l) => l.includes("/pair?k=")))
    expect(dialog(h)).toContain("Waiting for your browser")
    // The page that opened is taken by the extension in the browser that is connected already (after Reload on its
    // card, say), the way the extension does it: its secret for a token, from the extension's own address. That is
    // the same browser connected anew, so there is one in the list before and one after.
    const url = listed("opened.txt").at(-1)!
    const secret = (await (await fetch(url)).text()).match(/data-rf-secret="([^"]+)"/)![1]
    const claim = await fetch(new URL("/v1/pair/claim", url), {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "chrome-extension://pikflpmjggoledmjbgbkjigbnincplgb" },
      body: JSON.stringify({ secret, install: "install-chrome", name: "Chrome on this PC", extension_version: "0.1.0", protocol: 1 }),
    })
    expect(claim.status).toBe(200)
    await until(h, () => !dialog(h).includes("Waiting for your browser"))
    expect(dialog(h)).not.toContain("Waiting for your browser")
    expect(dialog(h)).toContain("● Browser extension connected Chrome on this PC · v0.1.0")
    expect(h.bridge.view<ExtensionView>("extension")!.installs.map((b) => b.install)).toEqual(["install-chrome"])
    // No pop-up came with the page that opened, so the dialog's buttons are there to press.
    expect(popups()).toEqual([])
    await clickBelow(h, "▐ Close ▌", find(h, "Connect another browser")![1], 3)
    expect(frame(h)).not.toContain("CONNECTED BROWSERS")
  })

  test("a window that isn't listening says why, instead of starting a post it can't hand over", async () => {
    const h = await kit({ extension: "on" }, [150, 45], NO_SERVER)
    const why = "ReelForge isn't listening for the extension here (it is switched off)."
    expect(flat(h)).toContain(`▐ Post… ▌ ${why}`)
    await clickText(h, "▐ Post… ▌", 3)
    await until(h, () => frame(h).includes("BROWSER EXTENSION"))
    expect(dialog(h)).toContain(why)
    expect(frame(h)).toContain("Or post by hand now:")
    expect(frame(h)).not.toContain("POST THIS VIDEO")
    expect(posting(h).rows.every((r) => r.state === "none")).toBe(true)
  })

  test("in a window that isn't listening, Post again… and Try again say why too, and every row stays as it is", async () => {
    const h = await kit({ extension: "on", jobs: { tiktok: ["opening", "failed"], x: ["ready", "posted"] }, spotted: { instagram: 2100 } }, [150, 45], NO_SERVER)
    const why = "ReelForge isn't listening for the extension here (it is switched off)."
    const made = jobsMade()
    for (const [name, key, button, stands] of [
      ["X", "x", "Post again…", "posted"],
      ["Instagram", "instagram", "Post again…", "spotted"],
      ["TikTok", "tiktok", "Try again", "failed"],
    ] as const) {
      await clickOnRow(h, name, button)
      await until(h, () => frame(h).includes("BROWSER EXTENSION"))
      expect(dialog(h)).toContain(why)
      expect(frame(h)).toContain("Or post by hand now:")
      expect(frame(h)).not.toContain("POST THIS VIDEO")
      h.t.mockInput.pressKey("ESCAPE")
      await h.settle(400)
      expect(frame(h)).not.toContain("BROWSER EXTENSION")
      expect(state(h, key)).toBe(stands)
    }
    expect(row(h, "X")).toContain("✓ posted")
    expect(row(h, "TikTok")).toContain("× failed")
    expect(jobsMade()).toBe(made) // not one made to be called off
    expect(popups()).toEqual([])
    // A call that reaches the Python side all the same is held back there, before any job is made.
    await h.bridge.call("post_action", "x", "post_again")
    await h.bridge.call("post_with_extension", ["x"], CAPTION, "#fyp #whatif #weather", true, ["x"], posting(h).video!.path)
    await h.settle(500)
    expect(popups().at(-1)).toBe(why)
    expect(jobsMade()).toBe(made)
    expect(state(h, "x")).toBe("posted")
    expect(listed("opened.txt")).toEqual([])
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
