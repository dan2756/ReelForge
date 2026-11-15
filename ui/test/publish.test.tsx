// The Publish tab with its real Python side: the form at start, on a channel of its own in a scratch folder
// (ui/test/settings_preload.py: one video, nothing connected), and on demo data what posting by hand keeps (every
// button, doing what it did). Posting with the extension is in posting.test.tsx.

import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { clickText } from "../scripts/shots"
import { ROOT } from "../src/bridge"
import { toasts } from "../src/ui/Panels"

setDefaultTimeout(30_000)
let open: Headless | null = null
let tmp = ""

/** The demo's Publish tab. No browser opens and nothing is copied: opened.txt and copied.txt in tmp say what would. */
async function demo(size: [number, number], more = {}): Promise<Headless> {
  tmp = mkdtempSync(join(tmpdir(), "reelforge-publish-"))
  open = await headless("dashboard", { demo: true, only_demos: true, tab: "publish", ...more }, size[0], size[1], {
    REELFORGE_BRIDGE_PRELOAD: join(ROOT, "scripts", "shots_preload.py"),
    REELFORGE_OPENED: join(tmp, "opened.txt"),
    REELFORGE_COPIED: join(tmp, "copied.txt"),
  })
  await open.settle(2500)
  return open
}

const listed = (file: string) => (existsSync(join(tmp, file)) ? readFileSync(join(tmp, file), "utf-8").split("\n").filter(Boolean) : [])

/** Wait (up to 5 s) for what a press asked the Python side for. */
async function until(h: Headless, ok: () => boolean) {
  for (let i = 0; i < 25 && !ok(); i++) await h.settle(200)
}

afterEach(async () => {
  await open?.close()
  open = null
  toasts.clear()
  if (tmp) rmSync(tmp, { recursive: true, force: true })
  tmp = ""
})

const frame = (h: Headless) => h.t.captureCharFrame()

describe("the Publish form at start", () => {
  test("the newest video's caption is in the form as soon as the window is up, however quick the Python side is", async () => {
    // This channel's Python side has its first video picked a few milliseconds after it answers: before the window has
    // drawn itself. The caption used to be sent then, to nobody, and the form stayed empty.
    tmp = mkdtempSync(join(tmpdir(), "reelforge-publish-"))
    open = await headless("dashboard", { tab: "publish" }, 160, 44, {
      REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "settings_preload.py"),
      REELFORGE_TEST_ROOT: tmp,
    })
    const h = open
    await h.settle(2500)
    expect(h.bridge.view<{ selected: string }>("files")!.selected).toContain("ep01_duck.mp4")
    expect(frame(h)).toContain("Ep01 duck") // in the caption's field
    expect(frame(h)).toMatch(/Caption +9\/100/)
    expect(frame(h)).not.toContain("one short line that hooks the viewer") // the empty field's words
  })
})

describe("posting by hand", () => {
  const CAPTION = "What if everyone on Earth jumped at the same time?"
  const TAGS = "#fyp #whatif #science #physics #earth"

  test("keeps every button under BY HAND: a platform's upload page with its copy buttons beside it, Open all and Show file", async () => {
    const h = await demo([200, 56], { unconnected: ["x"] })
    const rows = frame(h).split("\n")
    const at = (label: string) => {
      const y = rows.findIndex((r) => r.includes(label))
      return [y, y < 0 ? -1 : rows[y]!.indexOf(label)] as const
    }
    const [heading] = at("BY HAND")
    expect(rows[heading]).toContain("each button opens the upload page, copies that platform's text and shows the file")
    expect(rows[heading]).toMatch(/▐ Open all 3 → ▌ +Show file/)
    // one row for each connected platform, in their order, each with its own copy buttons after its button
    const [tiktok] = at("TikTok →")
    const [youtube, button] = at("YouTube →")
    const [instagram] = at("Instagram →")
    expect([tiktok, youtube, instagram]).toEqual([heading + 1, heading + 2, heading + 3])
    expect(rows[tiktok]).toContain("Copy TikTok text")
    expect(rows[instagram]).toContain("Copy Instagram text")
    const [captionRow, caption] = at("Copy caption")
    const [tagsRow, tags] = at("Copy YouTube hashtags")
    expect([captionRow, tagsRow]).toEqual([youtube, youtube])
    expect(button).toBeLessThan(caption)
    expect(caption).toBeLessThan(tags)
    expect(frame(h).split("Copy caption").length).toBe(2) // X isn't connected: no second Copy caption
    expect(rows.findIndex((r) => r.includes("BY HAND"))).toBeGreaterThan(rows.findIndex((r) => r.includes("▐ Post… ▌"))) // the quieter way, under Post…
  })

  test("each button does what it did: the text copied, the file shown, the upload page opened", async () => {
    const h = await demo([200, 56])
    await clickText(h, "Copy YouTube hashtags", 2)
    await until(h, () => listed("copied.txt").length > 0)
    expect(listed("copied.txt")).toEqual([TAGS, "-- copied --"])
    await clickText(h, "YouTube →", 2)
    await until(h, () => listed("opened.txt").length > 0)
    expect(listed("opened.txt")).toEqual(["reveal demo_everyone_jumps.mp4", "https://www.youtube.com/upload"])
    expect(listed("copied.txt").slice(2)).toEqual([`${CAPTION} #shorts`, "-- copied --"]) // its title
    expect(frame(h).replace(/\s+/g, " ")).toContain("Upload page opened")
    await clickText(h, "Open all 4 →", 2)
    await until(h, () => listed("opened.txt").length >= 7)
    expect(listed("opened.txt").slice(2)).toEqual([
      "reveal demo_everyone_jumps.mp4",
      "https://www.tiktok.com/tiktokstudio/upload",
      "https://www.youtube.com/upload",
      "https://www.instagram.com/",
      "https://x.com/compose/post",
    ])
    expect(listed("copied.txt").slice(4)).toEqual([`${CAPTION} ${TAGS}`, "-- copied --"]) // the TikTok text
  })
})
