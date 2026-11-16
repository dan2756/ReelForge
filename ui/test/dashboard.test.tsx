// The ReelForge window itself, drawn without a terminal on demo data with its real Python side: what fits where, the
// keys, clicks on chips, headers, the bell and the drawer's notifications, and the dialogs. (What the Python side
// does when they're pressed is tested in dashboard/tests/.)

import { TextAttributes } from "@opentui/core"
import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { clickText, find } from "../scripts/shots"
import { ROOT } from "../src/bridge"
import { DRAWER_WIDTH } from "../src/dashboard/Drawer"
import { TILE_ROWS } from "../src/dashboard/Overview"
import { TAB_NAMES, TOP_ROWS } from "../src/dashboard/state"
import { DASH, MARK } from "../src/theme"
import { toasts } from "../src/ui/Panels"

const PRELOAD = { REELFORGE_BRIDGE_PRELOAD: join(ROOT, "scripts", "shots_preload.py") }
setDefaultTimeout(30_000) // each test starts a real window (2.5 s to draw it) before its clicks and keys
let open: Headless | null = null

async function window(tab = "overview", size: [number, number] = [180, 50], drawer = false, more = {}): Promise<Headless> {
  open = await headless("dashboard", { demo: true, only_demos: true, tab, drawer, ...more }, size[0], size[1], PRELOAD)
  await open.settle(2500)
  return open
}

afterEach(async () => {
  await open?.close()
  open = null
  toasts.clear() // the pop-ups live in the module, so the next window would start with this one's
})

const frame = (h: Headless) => h.t.captureCharFrame()
const rows = (h: Headless) => frame(h).split("\n")

/** The tab that's open: its name bold in the header, where the others are plain ("" while Publish is open: it's the
 * button at the right, not a tab). */
function activeTab(h: Headless): string {
  const names = Object.values(TAB_NAMES) as string[]
  const lit = h.t.captureSpans().lines[1]!.spans.find((s) => s.attributes & TextAttributes.BOLD && names.includes(s.text.trim()))
  return lit?.text.trim() ?? ""
}

const hex = (c: { toInts(): [number, number, number, number] }) => "#" + c.toInts().slice(0, 3).map((v) => v.toString(16).padStart(2, "0")).join("")

/** The Publish button's label: green on the window's colour, or dark on green (filled) while Publish is open. */
function publishButton(h: Headless): { fg: string; bg: string } | null {
  const span = h.t.captureSpans().lines[1]!.spans.find((s) => s.text.includes("Publish"))
  return span ? { fg: hex(span.fg), bg: hex(span.bg) } : null
}
const publishOpen = (h: Headless) => publishButton(h)?.fg === DASH.bg

/** Row y as cells: each character with its background colour. */
function cells(h: Headless, y: number): { ch: string; bg: string }[] {
  const out: { ch: string; bg: string }[] = []
  for (const s of h.t.captureSpans().lines[y]!.spans) {
    const chars = [...s.text]
    for (let i = 0; i < s.width; i++) out.push({ ch: chars[i] ?? " ", bg: hex(s.bg) })
  }
  return out
}

const KEYS: Record<string, "ESCAPE" | "RETURN" | "TAB" | "DELETE"> = { escape: "ESCAPE", return: "RETURN", tab: "TAB", delete: "DELETE" }

async function press(h: Headless, key: string) {
  h.t.mockInput.pressKey(KEYS[key] ?? key)
  await h.settle(300)
}

/** Wait (up to 5 s) for what a click asked the Python side for: a slow computer takes longer than one settle. Each look
 * comes after a settle, as in clickText, so the frame is never read while the window is still drawing it. */
async function until(h: Headless, text: string) {
  for (let i = 0; i < 25; i++) {
    await h.settle(200)
    if (frame(h).includes(text)) return
  }
}

/** On the Videos tab, put the highlight on the newest video: sorted by Age the oldest come first, so End lands on it.
 * Fix episode lists the videos newest first under "Not one of my videos", so its dialog then opens on its second row
 * at any hour. Left alone, the highlight is on the demo's top video by score, and which one that is follows the clock
 * (dashboard/demo.py posts at 11 am and 7 pm): from two and a half hours after the morning's post until as long after
 * the evening's it is the oldest video, the list's last row, with no row under it for Down to move to and, in a short
 * window, the list's first row scrolled out of sight above it. */
async function highlightNewestVideo(h: Headless) {
  await clickText(h, "Age", 1) // its header: the table is sorted by it, and has the keyboard
  await until(h, "Age ▼")
  expect(frame(h)).toContain("Age ▼")
  h.t.mockInput.pressKey("END")
  await h.settle(500)
}

describe("the Overview fits", () => {
  for (const [w, hh, drawer] of [
    [110, 40, false],
    [150, 45, true],
    [200, 52, true],
  ] as const) {
    test(`at ${w}x${hh}${drawer ? " with the drawer open" : ""}: every tile names its platform, and nothing spills out of one`, async () => {
      const h = await window("overview", [w, hh], drawer)
      const top = TOP_ROWS + 1 // the header, its hairline, a row of space
      const edge = drawer ? w - DRAWER_WIDTH - 2 : w // the drawer covers the right-hand tiles
      // the tiles are the runs of the panels' colour along their first row, with the window's colour between them
      const first = cells(h, top).slice(0, edge)
      const gaps = first.map((c, x) => (c.bg === DASH.bg && x > 1 && x < edge - 1 && first[x - 1]!.bg === DASH.surface ? x : -1)).filter((x) => x >= 0)
      expect(gaps.length).toBe(drawer ? gaps.length : 4) // five tiles side by side
      for (let y = top; y < top + TILE_ROWS; y++) {
        const row = cells(h, y)
        for (const x of gaps) expect(row[x]).toEqual({ ch: " ", bg: DASH.bg })
      }
      const names = rows(h).slice(top, top + 2).join("")
      for (const name of [["All platforms", "All"], ["TikTok", "TT"], ["YouTube", "YT"]]) expect(name.some((n) => names.includes(n))).toBe(true)
    })
  }
})

describe("the Overview in an 80-column window", () => {
  test("every tile's numbers say what they are, and the day's change never runs into the name", async () => {
    const h = await window("overview", [80, 24])
    const tiles = rows(h).slice(TOP_ROWS + 1, TOP_ROWS + 1 + TILE_ROWS)
    // (a column short, four of the five tiles were three bare numbers: 18K, 2.8M, 199K)
    expect(tiles.join(" ").split(" views").length - 1).toBe(5)
    expect(tiles.join(" ").split(" followers").length - 1).toBeGreaterThanOrEqual(2) // where the number leaves it room
    expect(tiles[1]).not.toMatch(/[A-Za-z][▲▼]/)
    for (const name of ["All", "TT", "YT", "IG", "X"]) expect(tiles[1]).toContain(name)
  })
})

describe("the Videos tab in an 80-column window", () => {
  test("keeps Open post, Fix episode and Export CSV, on a row under Show", async () => {
    const h = await window("videos", [80, 24])
    const lines = rows(h)
    const show = lines.findIndex((r) => r.includes("SHOW"))
    expect(lines[show + 1]).toContain("Open post   Fix episode   Export CSV") // (they were left out: no room on Show's row)
    await clickText(h, " Fix episode ", 2)
    await until(h, "WHICH VIDEO IS THIS?")
    expect(frame(h)).toContain("WHICH VIDEO IS THIS?")
  })
})

describe("the Compare tab in an 80-column window", () => {
  test("has the episodes above the scoreboard, whose headings are no longer cut off", async () => {
    const h = await window("compare", [80, 24])
    const at = (text: string) => rows(h).findIndex((r) => r.includes(text))
    expect(rows(h)[at("EPISODES")]).toContain("Newest   Oldest   Most views")
    expect(at("EPISODES")).toBeLessThan(at("PLATFORM SCOREBOARD  which platform is working"))
    expect(frame(h)).toMatch(/MEDIAN VIEWS PER VIDEO · AT \d+ HOURS/) // (it ended "· AT 2" beside the list)
    expect(frame(h)).toContain("◆ Platform scoreboard")
  })
})

describe("a dialog in a window 24 rows tall", () => {
  test("keeps its words whole and its buttons in view: the list gives way, not the lines above it", async () => {
    const h = await window("videos", [80, 24])
    await highlightNewestVideo(h)
    await clickText(h, " Fix episode ", 2)
    await until(h, "WHICH VIDEO IS THIS?")
    const flat = frame(h).replace(/[│┃]/g, " ").replace(/\s+/g, " ")
    // (squeezed, its lines were drawn over each other: "ReelForgehmatchesopostsdtoavideosdbyotheirecaption")
    expect(flat).toContain("ReelForge matches posts to videos by their caption. If it got this one wrong, pick the right video")
    expect(frame(h)).toContain("Not one of my videos")
    expect(frame(h)).toMatch(/▐ Cancel ▌ ▐ This one ▌/)
  })
})

describe("the header and the bell", () => {
  test("the bell sits in the top right corner with the unread count, and opens and closes the drawer", async () => {
    const h = await window("overview", [122, 30])
    expect(rows(h)[0]).toMatch(/\d▌\s*$/) // the unread count's pill, on the bell's shoulder
    expect(frame(h)).not.toContain("NOTIFICATIONS")
    await h.t.mockMouse.click(122 - 3, 1)
    await h.settle(400)
    expect(frame(h)).toContain("NOTIFICATIONS")
    await h.t.mockMouse.click(122 - 8, 0)
    await h.settle(400)
    expect(frame(h)).not.toContain("NOTIFICATIONS")
  })

  test("Quit quits", async () => {
    const h = await window()
    await clickText(h, " Quit ", 2)
    expect(h.exits).toEqual([{ code: 0, result: undefined }])
  })

  test("Publish is a green button in a box at the right, not a tab: it opens Publish and stays filled there", async () => {
    const h = await window()
    const top = rows(h)
    expect(top[1]).toMatch(/Overview {2,}Videos {2,}Compare {2,}Stats {2,}Accounts {2,}Settings {2,}Updates ● +│ ↑ Publish │ +↻ Refresh +\? Help +Quit/)
    const x = [...top[1]!].indexOf("│")
    expect([...top[0]!].slice(x, x + 13).join("")).toBe("╭───────────╮")
    expect([...top[2]!].slice(x, x + 13).join("")).toBe("╰───────────╯")
    expect(publishButton(h)).toEqual({ fg: DASH.good, bg: DASH.bg })
    await clickText(h, "↑ Publish", 2)
    expect(frame(h)).toContain("BY HAND")
    expect(publishOpen(h)).toBe(true)
    expect(activeTab(h)).toBe("") // no tab along the top is lit, and the bar under them has gone
    expect(top[2]!.slice(0, x)).toContain("▔")
    expect(rows(h)[2]!.slice(0, x)).not.toContain("▔")
    await clickText(h, "Videos", 2)
    expect(activeTab(h)).toBe("Videos")
    expect(publishOpen(h)).toBe(false)
  })

  test("in a narrower window the button keeps its word and its box, and the bell its corner", async () => {
    for (const [w, button, signs] of [
      [122, "│ Publish │", "↻   ?   ×"],
      [110, "│ Publish │", "↻   ?   ×"],
      [100, "│Publish│", "↻ ? ×"], // the narrowest: everything a little closer
    ] as const) {
      const h = await window("publish", [w, 30])
      const top = rows(h)
      expect(top[1]!.indexOf(button)).toBeGreaterThan(top[1]!.indexOf("Updates ●") + 9) // never touching the tabs
      expect(top[1]).toContain(signs)
      expect(top[0]).toMatch(/\d▌\s*$/) // the bell's unread count, in the corner
      expect(publishOpen(h)).toBe(true)
      await open?.close()
      open = null
    }
  })
})

describe("the header in a narrow window", () => {
  test("at 80 columns nothing is pushed off the edge: every tab, Publish, Refresh, Help, Quit and the bell, each doing its job", async () => {
    const h = await window("overview", [80, 24])
    const top = rows(h)
    expect(top[0]).toContain("Night Physics ▾") // the profile button and the signs sit over the tabs
    expect(top[0]).toContain("↻ ? ×")
    expect(top[0]).toMatch(/\d▌\s*$/) // the bell's unread count, in the corner
    expect(top[1]).toMatch(/^ Overview +Videos +Compare +Stats +Accounts +Settings +Updates ● +│Publish│/)
    for (const row of top.slice(0, 3)) expect([...row].length).toBeLessThanOrEqual(80)
    await clickText(h, "↻ ? ×", 2) // ?
    expect(frame(h)).toContain("HOW REELFORGE WORKS")
    await press(h, "escape")
    await h.t.mockMouse.click(80 - 4, 1) // the bell
    await h.settle(400)
    expect(frame(h)).toContain("NOTIFICATIONS")
    await press(h, "escape")
    await clickText(h, "│Publish│", 3)
    expect(publishOpen(h)).toBe(true)
    h.bridge.emit("refreshed", { ago: "5m ago", reading: "refreshing TikTok…" })
    await h.settle(300)
    expect(rows(h)[0]).toContain("↻ ? ×  reading…") // a read under way, beside the signs
    await clickText(h, "↻ ? ×", 4) // ×
    expect(h.exits).toEqual([{ code: 0, result: undefined }])
  })

  test("narrower still, the tabs that fit show whole, the open one among them, and ‹ › reach the rest", async () => {
    const h = await window("overview", [60, 24])
    expect(rows(h)[1]).toMatch(/^ Overview Videos Compare Stats › +│Publish│/)
    expect(rows(h)[0]).toContain("↻ ? ×")
    await press(h, "6")
    expect(activeTab(h)).toBe("Settings")
    expect(rows(h)[1]).toMatch(/^ ‹ Compare Stats Accounts Settings › +│Publish│/)
    await clickText(h, "Settings ›", 9) // ›: the next tab along
    expect(activeTab(h)).toBe("Updates")
    expect(rows(h)[1]).toMatch(/^ ‹ Stats Accounts Settings Updates ● +│Publish│/)
    await clickText(h, "‹ Stats", 0) // ‹: the one before the first shown
    expect(activeTab(h)).toBe("Compare")
  })

  test("the keys at the foot go one at a time as the window narrows, the help last", async () => {
    const h = await window("overview", [180, 24])
    const bar = () => rows(h)[23]!.trim().replace(/^DEMO\s+/, "")
    const ALL = "Tab next button   1-7 tabs   u publish   r refresh   n notifications   p profiles   ? help"
    expect(bar()).toBe(ALL)
    let last = ALL
    for (const w of [100, 90, 80, 70, 60, 50, 40]) {
      h.t.resize(w, 24)
      await h.settle(200)
      const now = bar()
      expect(now.length).toBeLessThanOrEqual(last.length)
      expect(now).toMatch(/\? help$/) // the help lists every key: it stays longest
      for (const hint of now.split(/ {3}/)) expect(ALL).toContain(hint) // whole hints only, never half of one
      last = now
    }
    expect(last).not.toBe(ALL)
  })

  test("a notification's line that exactly fills the drawer keeps its last letter", async () => {
    // The words were wrapped to one column more than is drawn (the list's scrollbar column): "…doubled in size ever"
    // for "every".
    const h = await window("overview", [120, 30], true)
    const notes = h.bridge.view<{ items: { body: string }[] }>("notes")!.items.filter((n) => n.body)
    expect(notes.length).toBeGreaterThan(0)
    const drawn = frame(h).split("\n").map((r) => [...r].slice(120 - DRAWER_WIDTH).join("")).join(" ").replace(/[┃│▌]/g, " ").replace(/\s+/g, " ")
    // (the start of each body: as far as its first two rows go, whatever the numbers after it are today)
    for (const n of notes.slice(0, 4)) expect(drawn).toContain(n.body.replace(/\s+/g, " ").slice(0, 44))
  })

  test("n opens and closes the notifications, as the bell does", async () => {
    const h = await window("overview", [122, 30])
    expect(frame(h)).not.toContain("NOTIFICATIONS")
    await press(h, "n")
    expect(frame(h)).toContain("NOTIFICATIONS")
    await press(h, "n")
    expect(frame(h)).not.toContain("NOTIFICATIONS")
  })
})

describe("pop-ups", () => {
  test("a click anywhere on the pop-ups closes one, even on the row between two: nothing under them shows through", async () => {
    const h = await window("accounts", [180, 50], false, { unconnected: ["x"] })
    for (const n of [1, 2]) h.bridge.emit("toast", { title: `Pop-up ${n}`, body: "It stays until it's clicked.", timeout: 60 })
    await h.settle(500)
    const [x, y] = find(h, "Pop-up 1")!
    await h.t.mockMouse.click(x, y + 3) // under its title: its line, its padding, then the row between the two
    await h.settle(600)
    expect(frame(h)).not.toContain("Pop-up 1")
    expect(frame(h)).toContain("Pop-up 2")
  })
})

describe("keys", () => {
  test("1-7 open the tabs in the order they're shown and u opens Publish, but not while you type; Esc leaves the field", async () => {
    const h = await window()
    expect(activeTab(h)).toBe("Overview")
    await press(h, "2")
    expect(activeTab(h)).toBe("Videos")
    await press(h, "4")
    expect(activeTab(h)).toBe("Stats")
    await press(h, "8") // there are seven
    expect(activeTab(h)).toBe("Stats")
    await press(h, "u")
    expect(publishOpen(h)).toBe(true)
    for (let i = 0; i < 30 && !find(h, "What if everyone"); i++) await h.settle(100) // the picked video's caption
    await h.t.mockMouse.click(find(h, "What if everyone")![0] + 3, find(h, "What if everyone")![1])
    await h.settle(200)
    h.t.mockInput.pressKey("END")
    await h.t.mockInput.typeText(" 3u")
    await h.settle(300)
    expect(publishOpen(h)).toBe(true)
    expect(activeTab(h)).toBe("")
    expect(frame(h)).toContain("time? 3u")
    await press(h, "escape")
    await press(h, "3")
    expect(activeTab(h)).toBe("Compare")
    expect(publishOpen(h)).toBe(false)
  })

  test("? opens the help, Esc closes it", async () => {
    const h = await window()
    await h.t.mockInput.typeText("?")
    await h.settle(300)
    expect(frame(h)).toContain("HOW REELFORGE WORKS")
    await press(h, "escape")
    expect(frame(h)).not.toContain("HOW REELFORGE WORKS")
  })

  test("a dialog that closes gives the keyboard back to the button that had it", async () => {
    const h = await window("updates")
    /** The label of the button that has the keyboard: it's the underlined one. */
    const focused = () => {
      for (const line of h.t.captureSpans().lines) {
        const text = line.spans.filter((sp) => sp.attributes & TextAttributes.UNDERLINE).map((sp) => sp.text).join("").trim()
        if (text) return text
      }
      return ""
    }
    for (let i = 0; i < 6 && focused() !== "Release page →"; i++) await press(h, "tab")
    expect(focused()).toBe("Release page →")
    await h.t.mockInput.typeText("?")
    await h.settle(300)
    expect(frame(h)).toContain("HOW REELFORGE WORKS")
    expect(focused()).toBe("Close") // the dialog's own button, while it's open
    await press(h, "return")
    expect(frame(h)).not.toContain("HOW REELFORGE WORKS")
    expect(focused()).toBe("Release page →") // (nothing had it: Tab started again from the top)
    // Esc out of a text field takes the keyboard away for good: a dialog doesn't hand it back afterwards.
    await press(h, "u")
    for (let i = 0; i < 30 && !find(h, "What if everyone"); i++) await h.settle(100)
    await clickText(h, "What if everyone", 3)
    await press(h, "escape")
    await h.t.mockInput.typeText("?")
    await h.settle(300)
    await press(h, "return")
    await press(h, "3") // so 3 is a key again, not a character in the caption
    expect(activeTab(h)).toBe("Compare")
  })

  test("Tab moves between buttons, and Enter presses one", async () => {
    const h = await window("updates")
    expect(frame(h)).not.toContain("HOW REELFORGE WORKS")
    for (let i = 0; i < 2; i++) await press(h, "tab") // Check now, then Release page... back round to Check now
    await press(h, "escape")
    await clickText(h, " Help ", 2)
    expect(frame(h)).toContain("HOW REELFORGE WORKS")
    await press(h, "return") // Close has the keyboard
    expect(frame(h)).not.toContain("HOW REELFORGE WORKS")
  })
})

describe("accounts", () => {
  test("a platform that isn't connected has a box for its @handle: a typo says why, Sign up shows the steps", async () => {
    const h = await window("accounts", [180, 50], false, { unconnected: ["x"] })
    expect(frame(h)).toContain("○ not connected")
    expect(frame(h)).toContain("Only connected platforms show up in ReelForge. Connect X here any time.")
    expect(frame(h)).toContain("Disconnect") // on the connected ones
    await clickText(h, "@yourname", 2)
    await h.t.mockInput.typeText("two words")
    await press(h, "return") // Enter connects
    expect(frame(h)).toContain("That isn't a handle")
    await clickText(h, " Sign up → ", 2)
    await until(h, "MAKE YOUR X ACCOUNT")
    expect(frame(h)).toContain("MAKE YOUR X ACCOUNT")
    expect(frame(h)).toContain("Pick a username that matches your channel")
    await press(h, "escape")
    expect(frame(h)).not.toContain("MAKE YOUR X ACCOUNT")
  })

  test("X's Connect button connects the @handle typed in its box (the demo saves nothing, and says so)", async () => {
    const h = await window("accounts", [180, 50], false, { unconnected: ["x"] })
    await clickText(h, "@yourname", 2)
    await h.t.mockInput.typeText("@mychannel")
    await h.settle(300)
    const at = find(h, " Connect ", find(h, "@mychannel")![1])! // on the box's row (the hint above says Connect too)
    await h.t.mockMouse.click(at[0] + 2, at[1])
    await until(h, "Demo mode: nothing is saved.")
    expect(frame(h)).toContain("Demo mode: nothing is saved.")
    expect(frame(h)).not.toContain("Something went wrong")
  })

  test("Open profile opens the platform's page (from a test, nothing opens: the page is listed)", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "reelforge-opened-"))
    const list = join(tmp, "opened.txt")
    try {
      open = await headless("dashboard", { demo: true, only_demos: true, tab: "accounts" }, 180, 50, { ...PRELOAD, REELFORGE_OPENED: list })
      const h = open
      await h.settle(2500)
      await clickText(h, "Open profile", 2) // TikTok's, the first panel
      for (let i = 0; i < 25 && !existsSync(list); i++) await h.settle(200)
      expect(readFileSync(list, "utf-8")).toBe("https://www.tiktok.com/@yourchannel\n")
      expect(frame(h)).not.toContain("Something went wrong")
    } finally {
      await open?.close()
      open = null
      rmSync(tmp, { recursive: true, force: true })
    }
  })

  test("with nothing connected, ReelForge opens on Accounts and offers a Gmail first", async () => {
    const h = await window("", [180, 50], false, { unconnected: ["tiktok", "youtube", "instagram", "x"] })
    expect(activeTab(h)).toBe("Accounts")
    expect(frame(h)).toContain("No accounts connected yet")
    await clickText(h, " Make a Gmail first ", 2)
    await until(h, "MAKE A GMAIL FOR YOUR CHANNEL")
    expect(frame(h)).toContain("MAKE A GMAIL FOR YOUR CHANNEL")
    expect(frame(h)).not.toContain("Your @handle")
  })
})

describe("tables and charts", () => {
  test("a header sorts, and again flips the order", async () => {
    const h = await window("videos")
    expect(frame(h)).not.toContain("Views 12h ▼")
    await clickText(h, "Views 12h", 2)
    expect(frame(h)).toContain("Views 12h ▼")
    await clickText(h, "Views 12h ▼", 2)
    expect(frame(h)).toContain("Views 12h ▲")
  })

  test("the highlighted video stays highlighted when you leave the Videos tab and come back", async () => {
    const h = await window("videos")
    const keys = h.bridge.view<{ rows: { key: string }[] }>("videos")!.rows.map((r) => r.key)
    const charted = () => h.bridge.view<{ key: string | null }>("detail")?.key
    /** How far down the table the highlighted row is: the one with the lit edge. */
    const highlighted = () => {
      const lines = rows(h)
      const head = lines.findIndex((r) => r.includes("Views 12h"))
      return lines.slice(head + 1).findIndex((r) => r.includes("▌●"))
    }
    expect(highlighted()).toBe(0)
    h.t.mockInput.pressKey("TAB") // the table has the keyboard
    await h.settle(200)
    for (let i = 0; i < 2; i++) {
      h.t.mockInput.pressArrow("down")
      await h.settle(200)
    }
    for (let i = 0; i < 25 && charted() !== keys[2]; i++) await h.settle(200)
    expect(highlighted()).toBe(2)
    expect(charted()).toBe(keys[2]!) // the chart under the table is the third video's
    await press(h, "1")
    await press(h, "2")
    await h.settle(500)
    expect(activeTab(h)).toBe("Videos")
    expect(highlighted()).toBe(2) // (it went back to the first row, over the third video's chart)
    expect(charted()).toBe(keys[2]!)
  })

  test("Newest, Oldest and Most views beside Episodes sort the Compare tab's list, under the platform scoreboard", async () => {
    const h = await window("compare")
    const listed = () => h.bridge.view<{ items: { id: string }[] }>("episodes")!.items.map((i) => i.id).join()
    const settled = async (ok: () => boolean) => {
      for (let i = 0; i < 25 && !ok(); i++) await h.settle(200)
    }
    expect(rows(h).find((r) => r.includes("EPISODES"))).toContain("Newest   Oldest   Most views")
    const newest = listed().split(",")
    const oldest = [newest[0]!, ...newest.slice(1).reverse()].join()
    await clickText(h, " Oldest ", 2)
    await settled(() => listed() === oldest)
    expect(listed()).toBe(oldest)
    await clickText(h, " Most views ", 2)
    await settled(() => /\dK 4\/4/.test(frame(h)))
    expect(frame(h)).toMatch(/\d\.\dK 4\/4/) // each episode's views beside it
    expect(frame(h)).toContain("◆ Platform scoreboard")
    await clickText(h, " Newest ", 2)
    await settled(() => listed() === newest.join())
    expect(listed()).toBe(newest.join())
  })

  test("the range chips and a platform's key change the charts", async () => {
    const h = await window()
    expect(frame(h)).toContain("in 24h")
    await clickText(h, " 7D ", 2)
    expect(frame(h)).toContain("in 7d")
    await clickText(h, "■ TikTok", 2)
    const views = h.bridge.view<{ legend: [string, string][] }>("trends")!.legend
    expect(views.find((s) => s[0] === "■ TikTok")![1]).toContain("strike")
  })

  test("pointing at a bar reads its numbers", async () => {
    const h = await window()
    const title = find(h, "VIEWS")!
    const before = rows(h)[title[1]]
    await h.t.mockMouse.moveTo(title[0] + 30, title[1] + 8)
    await h.settle(300)
    expect(rows(h)[title[1]]).not.toBe(before)
    expect(rows(h)[title[1]]).toMatch(/VIEWS\s+\S+ \d+( |:)/)
  })
})

describe("badges", () => {
  /** Put up the open tab's line of badges (today's on the Overview, the highlighted video's on Videos: from the demo's
   * numbers, they depend on the hour, since a badge needs a good day so far), point at it: does all of what badges are
   * show under it? Then point away. */
  async function explained(h: Headless, view: "badges" | "video_badges", badge: string): Promise<boolean> {
    h.bridge.emit(view, { line: [[[[`★ ${badge}`, "bold #ffcc33"], ["  +1.2K so far · #1 of 30   ", "#8b93a1"]], 0]] })
    await until(h, badge)
    const [x, y] = find(h, badge)!
    await h.t.mockMouse.moveTo(x + 2, y)
    await h.settle(300)
    const shown = frame(h).includes("Badges compare you with yourself") && frame(h).includes("ties count against you.")
    await h.t.mockMouse.moveTo(1, rows(h).length - 1)
    await h.settle(300)
    return shown
  }

  test("pointing at the badges shows what they are over the charts, on a tab opened again too", async () => {
    const h = await window()
    const today = "Today: your best day yet for new TikTok followers"
    const video = "Your best TikTok video yet for views"
    expect(await explained(h, "badges", today)).toBe(true)
    await clickText(h, "Videos", 2)
    expect(await explained(h, "video_badges", video)).toBe(true)
    // opened again, a tab draws its charts after the badges: they covered all of the pop-up but its top edge
    await clickText(h, "Overview", 2)
    expect(await explained(h, "badges", today)).toBe(true)
    await clickText(h, "Videos", 2)
    expect(await explained(h, "video_badges", video)).toBe(true)
  })
})

describe("the drawer", () => {
  const noteTitles = (h: Headless) => h.bridge.view<{ items: { title: [string, string][] }[] }>("notes")!.items.map((n) => n.title.map((s) => s[0]).join(""))
  // The same four notifications at any hour (ui/test/notes_preload.py): the demo's own depend on the clock, and a
  // test that counted on their order failed at some hours of the day.
  const NOTES = ["A new video is ready to post", "A video passed 100K views on TikTok", /^ReelForge \S+ is out$/, "TikTok passed 10K followers"]

  async function drawer(): Promise<Headless> {
    open = await headless("dashboard", { demo: true, only_demos: true, tab: "overview", drawer: true }, 180, 50, {
      REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "notes_preload.py"),
    })
    await open.settle(2500)
    return open
  }

  test("a notification's × deletes it without opening it", async () => {
    const h = await drawer()
    const before = noteTitles(h)
    const at = find(h, before[0]!.slice(0, 20))!
    await h.t.mockMouse.click([...rows(h)[at[1]]!].lastIndexOf("×"), at[1]) // its × (beside its title)
    await h.settle(500)
    expect(noteTitles(h)).toEqual(before.slice(1))
    expect(activeTab(h)).toBe("Overview")
  })

  test("Delete deletes the highlighted one; a click opens what it's about", async () => {
    const h = await drawer()
    const before = noteTitles(h)
    expect(before.length).toBe(NOTES.length)
    before.forEach((title, i) => expect(title).toMatch(NOTES[i]!))
    const gone = async (n: number) => {
      for (let i = 0; i < 25 && noteTitles(h).length !== n; i++) await h.settle(200)
    }
    h.bridge.emit("focus", { id: "notes" }) // the list has the keyboard
    await h.settle(200)
    h.t.mockInput.pressKey("HOME")
    await h.settle(200)
    for (let i = 0; i < 2; i++) {
      h.t.mockInput.pressArrow("down") // the highlight, down to the update's
      await h.settle(200)
    }
    await press(h, "delete")
    await gone(3)
    expect(noteTitles(h)).toEqual([before[0]!, before[1]!, before[3]!])
    expect(activeTab(h)).toBe("Overview") // deleted, not opened
    h.t.mockInput.pressKey("BACKSPACE") // a Mac's delete key: the highlight moved on to the next one, which goes too
    await gone(2)
    expect(noteTitles(h)).toEqual([before[0]!, before[1]!])
    await clickText(h, "A video passed 100K", 2) // one click goes to what it's about
    for (let i = 0; i < 25 && activeTab(h) !== "Videos"; i++) await h.settle(200)
    expect(activeTab(h)).toBe("Videos")
  })

  test("Esc closes it", async () => {
    const h = await drawer()
    await press(h, "escape")
    expect(frame(h)).not.toContain("NOTIFICATIONS")
  })
})

describe("dialogs", () => {
  test("Remove asks what to do with the picked video; Cancel leaves it", async () => {
    const h = await window("publish", [200, 56])
    await clickText(h, " Remove ", 2)
    expect(frame(h)).toContain("REMOVE VIDEO")
    expect(frame(h)).toContain("demo_everyone_jumps.mp4")
    await clickText(h, " Cancel ", 2)
    expect(frame(h)).not.toContain("REMOVE VIDEO")
    expect(frame(h)).toContain("demo_everyone_jumps.mp4")
  })

  test("Fix episode lists the videos to file a post under", async () => {
    const h = await window("videos")
    await clickText(h, " Fix episode ", 2)
    expect(frame(h)).toContain("WHICH VIDEO IS THIS?")
    expect(frame(h)).toContain("Not one of my videos")
    await press(h, "escape")
    expect(frame(h)).not.toContain("WHICH VIDEO IS THIS?")
  })

  test("Fix episode opens with its list holding the keyboard: the arrows move, Enter picks", async () => {
    const h = await window("videos")
    await highlightNewestVideo(h)
    await clickText(h, " Fix episode ", 2)
    await until(h, "WHICH VIDEO IS THIS?")
    /** The option the list's lit edge is on, inside the dialog. */
    const lit = () => {
      const lines = rows(h)
      const top = lines.findIndex((r) => r.includes("WHICH VIDEO IS THIS?"))
      return lines.slice(top).find((r) => r.includes("▌ "))?.trim() ?? ""
    }
    const first = lit()
    expect(first).not.toBe("")
    h.t.mockInput.pressArrow("down") // no click and no Tab first
    await h.settle(300)
    expect(lit()).not.toBe(first)
    await press(h, "return")
    await h.settle(500)
    expect(frame(h)).not.toContain("WHICH VIDEO IS THIS?")
  })
})

describe("profiles", () => {
  test("the profile button names the profile; p opens its menu, which lists every profile, and Esc closes it", async () => {
    const h = await window("overview", [180, 50])
    expect(rows(h)[1]).toContain("Night Physics ▾")
    await press(h, "p")
    expect(frame(h)).toContain("PROFILES")
    expect(frame(h)).toContain("Tiny Worlds")
    expect(frame(h)).toContain("3 new")
    expect(frame(h)).toContain("New profile")
    expect(frame(h)).toContain("Settings for Night Physics")
    expect(frame(h)).not.toContain("Advanced settings")
    await press(h, "escape")
    expect(frame(h)).not.toContain("PROFILES")
    await clickText(h, "Night Physics ▾", 2)
    expect(frame(h)).toContain("PROFILES")
  })

  test("the menu opens under its button in a window of any size, with its words in full", async () => {
    for (const size of [[180, 50], [120, 30], [80, 24]] as [number, number][]) {
      const h = await window("overview", size)
      const button = find(h, "▐ Night")![0] // the left end of the profile button
      await press(h, "p")
      expect([...rows(h)[3]!].indexOf("╭")).toBe(button) // the menu's corner, straight under it
      expect(frame(h)).toContain("one for each channel: its own accounts, stats and log-ins") // (its end was cut off)
      expect(frame(h)).toContain("Esc closes")
      await open?.close()
      open = null
    }
  })

  test("when the stats were read sits under Refresh at the top, and the bar at the foot doesn't list the platforms", async () => {
    for (const [w, ago] of [
      [180, /^updated \d+m ago/],
      [122, /^\d+m ago/],
      [100, /^\d+m ago/],
    ] as const) {
      const h = await window("overview", [w, 30], false, { screenshots: true })
      const top = rows(h)
      const at = [...top[1]!].indexOf("↻")
      expect([...top[2]!].slice(at).join("")).toMatch(ago)
      const bar = rows(h)[29]!
      for (const gone of ["TikTok", "YouTube", "Instagram", "TT ", "IG ", "updated"]) expect(bar).not.toContain(gone)
      expect(bar).toContain("u publish")
      expect(bar).toMatch(/\? help\s*$/) // the help, which lists every key, is the last to go
      expect(bar).toContain("1-7 tabs   u publish   r refresh   n notifications   p profiles   ? help")
      if (w === 180) expect(bar).toContain("Tab next button   1-7 tabs")
      await open?.close()
      open = null
    }
  })

  test("a read under way shows under Refresh in amber; a failed one shows at the bottom left, and a click opens Accounts", async () => {
    const h = await window()
    h.bridge.emit("refreshed", { ago: "5m ago", reading: "refreshing TikTok…" })
    await h.settle(300)
    const [x, y] = find(h, "refreshing TikTok…")!
    expect(y).toBe(2)
    expect(x).toBe([...rows(h)[1]!].indexOf("↻"))
    expect(hex(h.t.captureSpans().lines[2]!.spans.find((sp) => sp.text.includes("refreshing"))!.fg)).toBe(MARK)
    h.bridge.emit("header", { line: [["● Instagram: last refresh failed", "bold #ff5c6c", ["tab", "accounts"]]] })
    await h.settle(300)
    expect(rows(h)[49]).toContain("● Instagram: last refresh failed")
    await clickText(h, "Instagram: last refresh failed", 2)
    expect(activeTab(h)).toBe("Accounts")
  })

  test("picking another profile shows it in the same window, on its own Python side", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "reelforge-profiles-"))
    try {
      open = await headless("dashboard", { tab: "overview" }, 160, 40, {
        REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "profiles_preload.py"),
        REELFORGE_TEST_ROOT: tmp,
        REELFORGE_PROFILE: "profiles/alpha",
      })
      const h = open
      await h.settle(2500)
      expect(rows(h)[1]).toContain("Alpha ▾")
      await press(h, "p")
      expect(frame(h)).toContain("Beta")
      await press(h, "3") // 1 the ReelForge folder's own, 2 Alpha, 3 Beta
      expect(frame(h)).toContain("Opening that profile…") // (its Python side takes a moment to start)
      for (let i = 0; i < 30 && !rows(h)[1]!.includes("Beta ▾"); i++) await h.settle(200)
      expect(rows(h)[1]).toContain("Beta ▾")
      expect(activeTab(h)).toBe("Accounts") // nothing connected there yet: where you connect one
      expect(h.sides.active).not.toBe(h.bridge)
      expect(await Promise.race([h.bridge.exited.then(() => "exited"), Bun.sleep(8000).then(() => "running")])).toBe("exited")
      expect(readFileSync(join(tmp, "profiles", "last-opened.txt"), "utf-8").trim()).toBe("profiles/beta")
    } finally {
      await open?.close()
      open = null
      rmSync(tmp, { recursive: true, force: true })
    }
  })

  test("in a window 80 columns wide the box for a @handle shows what is typed in it, with its buttons under it", async () => {
    // Two panels side by side there, and beside Connect and Sign up the box was five cells wide: "@you", then "cs"
    // of @nightphysics. The first thing a new channel does, at the smallest size ReelForge is made for.
    const h = await window("accounts", [80, 24], false, { unconnected: ["tiktok", "youtube", "instagram", "x"] })
    await clickText(h, "@yourname", 2) // the whole placeholder shows
    await h.t.mockInput.typeText("@nightphysics")
    await h.settle(300)
    const lines = frame(h).split("\n")
    const at = lines.findIndex((r) => r.includes("@nightphysics"))
    expect(at).toBeGreaterThan(0)
    expect(lines[at + 2]).toMatch(/▐ Connect ▌ ▐ Sign up → ▌/)
    h.t.resize(140, 30) // with room for all three in a row, they are in one, as before
    await h.settle(500)
    expect(frame(h)).toMatch(/@nightphysics +▐ Connect ▌ ▐ Sign up → ▌/)
  })

  test("in a window 24 rows tall a sign-up dialog keeps its handle box and its buttons inside it: the steps scroll", async () => {
    // TikTok's, Instagram's and X's are a row taller than such a window takes. Nothing in them could give way, so
    // Cancel and Connect were drawn on the dialog's bottom border.
    const h = await window("accounts", [180, 50], false, { unconnected: ["tiktok", "youtube", "instagram", "x"] })
    await clickText(h, " Sign up → ", 2) // TikTok's
    await until(h, "MAKE YOUR TIKTOK ACCOUNT")
    expect(frame(h)).toContain("4. Type your @handle below and press Connect.") // all of it, where there is room
    for (const [w, rows] of [[80, 24], [100, 24], [120, 24], [80, 26]] as const) {
      h.t.resize(w, rows)
      await h.settle(500)
      const lines = frame(h).split("\n")
      for (const text of ["MAKE YOUR TIKTOK ACCOUNT", "Sign up on TikTok", "▐ Open TikTok sign-up → ▌", "Your @handle", "@yourname"]) expect(frame(h)).toContain(text)
      expect(lines.find((r) => r.includes("▐ Cancel ▌"))).toMatch(/│ +▐ Cancel ▌ ▐ Connect ▌ +│/) // inside, not on the border
      const handle = lines.findIndex((r) => r.includes("Your @handle"))
      expect(lines[handle - 1]).toMatch(/│ +│/) // its own row, with the empty one above it: nothing drawn over it
      expect(lines[handle - 2]).toContain("▐ Open TikTok sign-up → ▌")
    }
  })

  test("a second channel's Sign up opens its page in a private window, so the other channel stays logged in", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "reelforge-profiles-"))
    try {
      open = await headless("dashboard", {}, 160, 44, {
        REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "profiles_preload.py"),
        REELFORGE_TEST_ROOT: tmp,
        REELFORGE_PROFILE: "profiles/alpha",
      })
      const h = open
      await h.settle(2500)
      expect(activeTab(h)).toBe("Accounts") // nothing connected yet
      expect(frame(h)).toContain("New channel?")
      await clickText(h, " Sign up → ", 2) // TikTok's
      await until(h, "MAKE YOUR TIKTOK ACCOUNT")
      expect(frame(h)).toContain("MAKE YOUR TIKTOK ACCOUNT")
      expect(frame(h)).toContain("like @alpha")
      await clickText(h, "Open TikTok sign-up →", 2)
      await until(h, "Opened in a private Chrome window.")
      expect(frame(h)).toContain("Opened in a private Chrome window.") // a stand-in: nothing opens
    } finally {
      await open?.close()
      open = null
      rmSync(tmp, { recursive: true, force: true })
    }
  })
})
