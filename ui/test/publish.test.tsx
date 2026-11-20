// The Publish tab with its real Python side: the form at start, on a channel of its own in a scratch folder
// (ui/test/settings_preload.py: one video, nothing connected), and on demo data what posting by hand keeps (every
// button, doing what it did) and that the keyboard never presses a button nobody can see. Posting with the extension
// is in posting.test.tsx.

import { TextAttributes } from "@opentui/core"
import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { clickText, find } from "../scripts/shots"
import { ROOT } from "../src/bridge"
import { DASH } from "../src/theme"
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

describe("what you type in the form", () => {
  test("is still there when you pick another video and come back, and the preview with it", async () => {
    const h = await demo([200, 56])
    for (let i = 0; i < 30 && !find(h, "What if everyone"); i++) await h.settle(100)
    await clickText(h, "What if everyone", 3) // the caption's field
    h.t.mockInput.pressKey("END")
    await h.t.mockInput.typeText(" My own words.")
    await h.settle(400)
    const mine = "What if everyone on Earth jumped at the same time? My own words."
    const shown = () => h.bridge.view<{ rows: { name: string; text: string }[] }>("preview")!.rows.find((r) => r.name === "X")!.text
    await until(h, () => shown() === mine)
    h.t.mockInput.pressKey("ESCAPE") // out of the field
    await h.settle(200)
    await clickText(h, "demo_doubling_duck.mp4", 2)
    await until(h, () => !frame(h).includes("My own words."))
    expect(frame(h)).not.toContain("My own words.") // the other video's own caption
    await clickText(h, "demo_everyone_jumps.mp4", 2)
    await until(h, () => frame(h).includes("My own words.") && shown() === mine)
    expect(frame(h)).toContain("jumped at the same time? My own words.") // (it came back as the video's own caption)
    expect(shown()).toBe(mine) // and what each platform would get follows
  })
})

describe("a field narrower than what's in it", () => {
  test("shows its start while it doesn't have the keyboard, and typing still goes on at the end", async () => {
    const h = await demo([100, 30])
    for (let i = 0; i < 30 && !find(h, "What if everyone"); i++) await h.settle(100)
    const caption = () => frame(h).split("\n")[find(h, "Caption")![1] + 2]!
    expect(caption()).toContain("What if everyone on Earth jumped at the same") // (it showed only its tail: "e on Earth jumped at the same time?")
    expect(frame(h)).toContain("#fyp #whatif #science #physics #earth")
    await clickText(h, "What if everyone", 3)
    await h.t.mockInput.typeText(" Yes.")
    await h.settle(400)
    expect(caption()).toContain("at the same time? Yes.") // with the keyboard, its end, where the typing went
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(300)
    expect(caption()).toContain("What if everyone on Earth jumped") // and its start again
    const x = h.bridge.view<{ rows: { name: string; text: string }[] }>("preview")!.rows.find((r) => r.name === "X")!.text
    expect(x).toBe("What if everyone on Earth jumped at the same time? Yes.")
  })
})

describe("headings", () => {
  test("are all bold in the window's one text colour, whoever writes them", async () => {
    const h = await demo([150, 56])
    const hex = (c: { toInts(): [number, number, number, number] }) => "#" + c.toInts().slice(0, 3).map((v) => v.toString(16).padStart(2, "0")).join("")
    const seen: Record<string, string> = {}
    // (the Python side's are just "bold": the renderer's own white, a shade off the window's, until Line gave them it)
    const HEADINGS = ["VIDEOS", "BEST TIME TO POST", "POST", "BY HAND", "PREVIEW", "RECENT UPLOADS"]
    for (const line of h.t.captureSpans().lines)
      for (const sp of line.spans) {
        const name = HEADINGS.find((t) => sp.text.trim() === t)
        if (name && sp.attributes & TextAttributes.BOLD) seen[name] = hex(sp.fg)
      }
    expect(seen).toEqual(Object.fromEntries(HEADINGS.map((t) => [t, DASH.text])))
  })
})

describe("the preview", () => {
  test("has no empty row between two platforms' texts, whatever the window's width", async () => {
    // A text that filled its row to the last column or two was given an empty row under it: YouTube's, in a window
    // 200 or 201 columns wide. Which width that is follows the caption's length, so every width in a range is tried.
    const h = await demo([188, 56])
    for (let w = 188; w <= 212; w++) {
      h.t.resize(w, 56)
      await h.settle(200)
      const rows = frame(h).split("\n")
      const first = rows.findIndex((r) => /┃ {3}TikTok {6}\S/.test(r))
      const last = rows.findIndex((r) => /┃ {3}X {11}\S/.test(r))
      expect([w, first > 0, last > first]).toEqual([w, true, true])
      // every row from TikTok's to X's has words where the texts are (a platform's name, or a wrapped line)
      for (const row of rows.slice(first, last + 1)) expect([w, row.slice(row.lastIndexOf("┃") + 1).trim()]).not.toEqual([w, ""])
    }
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

  test("in a narrow window the videos go above the form, and the wheel scrolls their list before it scrolls the page", async () => {
    const h = await demo([80, 24])
    const at = (text: string) => frame(h).split("\n").findIndex((r) => r.includes(text))
    expect(at("VIDEOS  3")).toBeGreaterThan(0)
    expect(at("VIDEOS  3")).toBeLessThan(at("Caption")) // above the form, which keeps the window's width
    expect(frame(h)).toContain("demo_doubling_duck.mp4")
    expect(frame(h)).not.toContain("demo_disaster_pov.mp4") // two show, and the list scrolls to the third
    const [x, y] = find(h, "demo_everyone_jumps.mp4")!
    await h.t.mockMouse.scroll(x + 2, y, "down")
    await h.settle(300)
    expect(frame(h)).toContain("demo_disaster_pov.mp4")
    expect(at("VIDEOS  3")).toBeGreaterThan(0) // the page stayed where it was
    for (let i = 0; i < 30 && at("BY HAND") < 0; i++) {
      await h.t.mockMouse.scroll(x + 2, 20, "down") // over the form: the page
      await h.settle(100)
    }
    expect(frame(h)).toContain("▐ Post… ▌")
    expect(frame(h)).toContain("BY HAND")
    expect(at("VIDEOS  3")).toBe(-1) // scrolled away
  })

  test("with the videos beside the form, Tab goes from the Caption box to Hashtags, and what is typed there is typed", async () => {
    // By rows the list of videos came between the two (it starts a row under the Caption box): the hashtags typed
    // after Tab ran the window's one-key shortcuts (p opened the profiles menu), and an arrow picked another video.
    const h = await demo([120, 30])
    for (const [size, typed] of [[[120, 30], "x"], [[150, 45], "y"]] as const) {
      h.t.resize(size[0], size[1])
      await h.settle(500)
      await clickText(h, CAPTION.slice(0, 12), 2) // into the Caption box
      h.t.mockInput.pressKey("TAB")
      await h.settle(200)
      await h.t.mockInput.typeText(typed + "p")
      await h.settle(300)
      expect(frame(h)).toContain(`${TAGS}${size[0] === 120 ? "xp" : "xpyp"}`) // typed into Hashtags, p and all
      expect(frame(h)).not.toContain("New profile") // ... and the profiles menu did not open
      h.t.mockInput.pressKey("TAB", { shift: true }) // back to the Caption box, not into the list
      await h.settle(200)
      await h.t.mockInput.typeText("!")
      await h.settle(300)
      expect(frame(h)).toContain(`${CAPTION}${size[0] === 120 ? "!" : "!!"}`)
    }
  })

  test("Tab brings a button that is out of sight into view before Enter can press it", async () => {
    const h = await demo([120, 22]) // too short for the By hand rows: they're below the fold
    expect(frame(h)).not.toContain("Copy Instagram text")
    /** The label of the button that has the keyboard: it's the underlined one. */
    const focused = () => {
      for (const line of h.t.captureSpans().lines) {
        const text = line.spans.filter((sp) => sp.attributes & TextAttributes.UNDERLINE).map((sp) => sp.text).join("").trim()
        if (text) return text
      }
      return ""
    }
    for (let i = 0; i < 30 && focused() !== "Copy Instagram text"; i++) {
      h.t.mockInput.pressKey("TAB")
      await h.settle(150)
    }
    expect(focused()).toBe("Copy Instagram text") // it has the keyboard, and it's drawn
    expect(frame(h)).toContain("Copy Instagram text")
    h.t.mockInput.pressKey("RETURN")
    await until(h, () => listed("copied.txt").length > 0)
    expect(listed("copied.txt")).toEqual([`${CAPTION} ${TAGS}`, "-- copied --"])
  })
})
