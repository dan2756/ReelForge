// The keyboard on the tabs that scroll, drawn without a terminal on demo data (and a scratch channel) with the real
// Python side, in windows short enough that most of each tab is below the fold: whatever Tab lands on is brought into
// view before Enter can press it, Page Up and Page Down scroll a tab or a dialog longer than the window, and a
// control the Python side gives the keyboard to (Log in, after a clicked notification) shows too. (The Publish tab's
// own test of this is in publish.test.tsx.)

import { TextAttributes } from "@opentui/core"
import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { ROOT } from "../src/bridge"
import { DASH } from "../src/theme"
import { toasts } from "../src/ui/Panels"

const PRELOAD = { REELFORGE_BRIDGE_PRELOAD: join(ROOT, "scripts", "shots_preload.py") }
const PAGE_DOWN = "\u001B[6~"
const PAGE_UP = "\u001B[5~"
setDefaultTimeout(60_000)
let open: Headless | null = null
let tmp = ""

async function window(tab: string, size: [number, number]): Promise<Headless> {
  open = await headless("dashboard", { demo: true, only_demos: true, tab }, size[0], size[1], PRELOAD)
  await open.settle(2500)
  return open
}

afterEach(async () => {
  await open?.close()
  open = null
  toasts.clear()
  if (tmp) rmSync(tmp, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 })
  tmp = ""
})

const frame = (h: Headless) => h.t.captureCharFrame()
const hex = (c: { toInts(): [number, number, number, number] }) => "#" + c.toInts().slice(0, 3).map((v) => v.toString(16).padStart(2, "0")).join("")

/** What on screen says it has the keyboard, each with its row: a button's underlined label, a list's or a table's
 * highlighted row, a text field's lit background. A control that is scrolled out of view shows none of them. */
function marks(h: Headless): [string, number][] {
  const out: [string, number][] = []
  h.t.captureSpans().lines.forEach((line, y) => {
    const underlined = line.spans.filter((sp) => sp.attributes & TextAttributes.UNDERLINE).map((sp) => sp.text).join("").trim()
    if (underlined) out.push([`_${underlined}`, y])
    for (const bg of [DASH.cursor, DASH.inputFocus]) if (line.spans.some((sp) => hex(sp.bg) === bg)) out.push([bg, y])
  })
  return out
}

/** The marks in `now` that weren't in `before` (wherever each was then), counting repeats. */
function added(now: [string, number][], before: [string, number][]): [string, number][] {
  const left = before.map(([m]) => m)
  return now.filter(([m]) => {
    const i = left.indexOf(m)
    if (i < 0) return true
    left.splice(i, 1)
    return false
  })
}

describe("Tab", () => {
  // Each tab in a window where part of it is below the fold, how many times Tab is pressed there (round its stops at
  // least once), and the least number of different stops that should show.
  for (const [tab, w, hh, presses, least] of [
    ["settings", 100, 30, 34, 12], // the channel, the FAQ, the tab order, this computer, the profiles, the danger zone
    ["accounts", 120, 30, 10, 3], // four panels, two of them scrolled away
    ["overview", 100, 30, 3, 1], // the charts one under another, then Top performers
    ["compare", 120, 30, 5, 2], // the episodes, and the table under the charts
    ["totals", 100, 24, 6, 3], // a pill for each platform
  ] as const)
    test(`on ${tab} at ${w}x${hh}: whatever it lands on is on screen`, async () => {
      const h = await window(tab, [w, hh])
      const still = marks(h) // (a link in the release notes is underlined too: what was there before any Tab)
      const seen = new Set<string>()
      for (let i = 0; i < presses; i++) {
        h.t.mockInput.pressKey("TAB")
        await h.settle(90)
        const lit = added(marks(h), still)
        expect(lit.length).toBeGreaterThan(0) // something on screen has the keyboard: never a control out of sight
        seen.add(lit.map(([m, y]) => `${m}@${y}`).join("|"))
      }
      expect(seen.size).toBeGreaterThanOrEqual(least)
    })

  test("on Settings, the arrows in the FAQ keep the highlighted question on screen", async () => {
    const h = await window("settings", [100, 24])
    const last = "Does ReelForge cost anything?"
    expect(frame(h)).not.toContain(last)
    const lit = () => {
      for (const line of h.t.captureSpans().lines) {
        const text = line.spans.filter((sp) => hex(sp.bg) === DASH.cursor).map((sp) => sp.text).join("").trim()
        if (text) return text
      }
      return ""
    }
    for (let i = 0; i < 12 && !lit().includes("Which model"); i++) {
      h.t.mockInput.pressKey("TAB")
      await h.settle(120)
    }
    expect(lit()).toContain("Which model does my AI use")
    for (let i = 0; i < 20 && !lit().includes(last); i++) {
      h.t.mockInput.pressArrow("down")
      await h.settle(120)
      expect(lit()).not.toBe("") // each question it moves to shows
    }
    expect(lit()).toContain(last)
  })
})

describe("Page Up and Page Down", () => {
  test("scroll a tab longer than the window, with or without a button holding the keyboard", async () => {
    const h = await window("settings", [100, 30])
    expect(frame(h)).toContain("THIS CHANNEL")
    expect(frame(h)).not.toContain("DANGER ZONE")
    for (let i = 0; i < 8 && !frame(h).includes("DANGER ZONE"); i++) {
      h.t.mockInput.pressKey(PAGE_DOWN)
      await h.settle(150)
    }
    expect(frame(h)).toContain("DANGER ZONE")
    expect(frame(h)).not.toContain("THIS CHANNEL")
    h.t.mockInput.pressKey("TAB") // a button has the keyboard: the page still scrolls
    await h.settle(150)
    for (let i = 0; i < 8 && !frame(h).includes("THIS CHANNEL"); i++) {
      h.t.mockInput.pressKey(PAGE_UP)
      await h.settle(150)
    }
    expect(frame(h)).toContain("THIS CHANNEL")
  })

  test("scroll the help, which is longer than its dialog, and leave the tab under it alone", async () => {
    const h = await window("settings", [120, 30])
    await h.t.mockInput.typeText("?")
    await h.settle(400)
    expect(frame(h)).toContain("HOW REELFORGE WORKS")
    const keys = "move between buttons, lists and fields"
    expect(frame(h)).not.toContain(keys) // the keys are at its end
    for (let i = 0; i < 30 && !frame(h).includes(keys); i++) {
      h.t.mockInput.pressKey(PAGE_DOWN)
      await h.settle(120)
    }
    expect(frame(h)).toContain(keys)
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(300)
    expect(frame(h)).toContain("THIS CHANNEL") // the tab is where it was
  })

  test("in a table that has the keyboard they move through its rows, as they always did", async () => {
    const h = await window("videos", [140, 30])
    const titles = h.bridge.view<{ rows: { cells: [string, string][][] }[] }>("videos")!.rows.map((r) => r.cells[1]![0]![0])
    h.t.mockInput.pressKey("TAB")
    await h.settle(150)
    h.t.mockInput.pressKey(PAGE_DOWN)
    await h.settle(300)
    const lit = frame(h).split("\n").find((r) => r.includes("▌●")) ?? ""
    expect(lit).not.toContain(titles[0]!.slice(0, 30)) // the highlight moved down a page of rows
    expect(titles.some((t, i) => i > 2 && lit.includes(t.slice(0, 30)))).toBe(true)
  })
})

describe("a control the Python side gives the keyboard to", () => {
  test("is brought into view: Log in, in a panel scrolled away", async () => {
    tmp = mkdtempSync(join(tmpdir(), "reelforge-keyboard-"))
    open = await headless("dashboard", { tab: "accounts" }, 120, 30, {
      REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "posting_preload.py"),
      REELFORGE_TEST_ROOT: tmp,
      REELFORGE_TEST_POSTING: JSON.stringify({ needs_login: ["x"] }),
    })
    const h = open
    await h.settle(3000)
    expect(frame(h)).not.toContain("▐ Log in ▌") // X's panel is in the second row, below the fold
    h.bridge.emit("focus", { id: "login-x" }) // as after a click on "X needs you to log in again"
    await h.settle(500)
    expect(frame(h)).toContain("▐ Log in ▌")
    expect(marks(h).map(([m]) => m)).toContain("_Log in")
  })
})
