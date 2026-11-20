// What each tab shows when it has nothing to show yet. Before its view has arrived from the Python side: one line
// that says what is being read (a Python side that never answers, so nothing arrives), which turns into what to do
// when it takes too long. On a channel with its platforms connected and nothing read yet (ui/test/posting_preload.py,
// with the real Python side): what goes in each empty table, and what to do to get it there.

import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { createTestRenderer, type TestRendererSetup } from "@opentui/core/testing"
import { createRoot } from "@opentui/react"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { Bridge, ROOT } from "../src/bridge"
import { DashboardApp } from "../src/dashboard/App"
import type { Init } from "../src/dashboard/types"
import { RENDERER } from "../src/root"
import { READING, toasts } from "../src/ui/Panels"

setDefaultTimeout(40_000)
const LATE = READING.lateMs
let open: Headless | null = null
let tmp = ""
let drawn: { t: TestRendererSetup; close: () => void } | null = null

afterEach(async () => {
  drawn?.close()
  drawn = null
  READING.lateMs = LATE
  await open?.close()
  open = null
  toasts.clear()
  if (tmp) rmSync(tmp, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 })
  tmp = ""
})

/** A Python side that never answers: what the window draws before any view has arrived. */
class Silent extends Bridge {
  call<T = any>(): Promise<T> {
    return new Promise<T>(() => {})
  }
}

const ORDER = ["overview", "videos", "compare", "totals", "accounts", "settings", "updates"]

/** The window on a tab, with nothing sent to it. */
async function silent(tab: string, more: Partial<Init> = {}): Promise<TestRendererSetup> {
  const init = {
    platforms: [], tab, order: ORDER, default_order: ORDER, drawer: false, demo: false, anvil: [], quadrants: false, help: [], keys: [],
    default_tags: "", caption_placeholder: "", tags_placeholder: "", copies: {}, badges_how: "",
    profile: { key: ".", name: "Night Physics", main: true, several: false, elsewhere: 0 }, ...more,
  } as unknown as Init
  const t = await createTestRenderer({ ...RENDERER, width: 120, height: 30 })
  const root = createRoot(t.renderer)
  root.render(<DashboardApp bridge={new Silent()} init={init} quit={() => {}} />)
  drawn = {
    t,
    close: () => {
      root.unmount()
      t.renderer.destroy()
    },
  }
  for (let i = 0; i < 3; i++) await t.renderOnce()
  await Bun.sleep(300)
  for (let i = 0; i < 3; i++) await t.renderOnce()
  return t
}

describe("before a tab's view has arrived", () => {
  test("every tab says what it is reading, and none looks like a channel with nothing in it", async () => {
    for (const [tab, reading] of [
      ["overview", "Reading your stats…"],
      ["videos", "Reading your videos…"],
      ["compare", "Reading your episodes…"],
      ["totals", "Reading your stats…"],
      ["accounts", "Reading your accounts…"],
      ["settings", "Reading your settings…"],
      ["updates", "Reading which version you have…"],
      ["publish", "Reading your videos…"],
    ] as const) {
      const t = await silent(tab)
      const f = t.captureCharFrame()
      expect(f).toContain(reading)
      // (what an empty chart, an empty list and an empty table said while their numbers were still on the way)
      for (const wrong of ["Not enough history yet", "No videos yet", "Pick a video above", "EPISODES", "BY PLATFORM"]) expect(f).not.toContain(wrong)
      expect(f).toContain("Night Physics ▾") // the header is there from the start
      drawn?.close()
      drawn = null
    }
  })

  test("the drawer says it is reading the notifications", async () => {
    const t = await silent("overview", { drawer: true })
    expect(t.captureCharFrame()).toContain("Reading your notifications…")
  })

  test("a view that doesn't come says so after a while, with what to do", async () => {
    READING.lateMs = 900
    const t = await silent("videos")
    expect(t.captureCharFrame()).not.toContain("taking longer than it should")
    await Bun.sleep(900)
    for (let i = 0; i < 3; i++) await t.renderOnce()
    const f = t.captureCharFrame().replace(/\s+/g, " ")
    expect(f).toContain("Reading your videos…")
    expect(f).toContain("This is taking longer than it should. Press r to read again, or open another tab and come back.")
    expect(f).toContain("run: reelforge doctor")
  })
})

describe("a channel with nothing read yet", () => {
  async function kit(tab: string, size: [number, number] = [150, 45]): Promise<Headless> {
    tmp = mkdtempSync(join(tmpdir(), "reelforge-states-"))
    open = await headless("dashboard", { tab }, size[0], size[1], {
      REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "posting_preload.py"),
      REELFORGE_TEST_ROOT: tmp,
      REELFORGE_TEST_POSTING: "{}",
    })
    await open.settle(3000)
    return open
  }
  const flat = (h: Headless) => h.t.captureCharFrame().replace(/[┃█▀▄]/g, " ").replace(/\s+/g, " ")

  test("the Videos tab says how a video gets there, and its chart doesn't ask for one to be picked", async () => {
    const h = await kit("videos", [120, 30])
    expect(flat(h)).toContain("No videos read yet. Post one from the Publish tab (u), and it shows up here at the next refresh (r).")
    expect(flat(h)).toContain("A video's views since it went up show here, against your typical video's, once there is one above.")
    expect(flat(h)).not.toContain("Pick a video above")
    h.t.mockInput.pressKey("TAB") // nothing in the table to move through: Tab doesn't stop on it
    await h.settle(200)
    h.t.mockInput.pressKey("u") // so the keys still work (the hint's own way to Publish)
    await h.settle(600)
    expect(h.t.captureCharFrame()).toContain("BY HAND")
  })

  test("Top performers and Recent uploads say what goes in them", async () => {
    const h = await kit("overview", [150, 56]) // (tall enough for the uploads, at the foot of the Publish tab)
    expect(flat(h)).toContain("No video is ranked yet. One shows up here once it has 50 views")
    h.t.mockInput.pressKey("u")
    await h.settle(800)
    for (let i = 0; i < 20 && !flat(h).includes("RECENT UPLOADS"); i++) await h.settle(200)
    expect(flat(h)).toContain("RECENT UPLOADS")
    expect(flat(h)).toContain("Nothing yet. A post you start, with Post… or a platform's button under BY HAND, is listed here")
  })
})
