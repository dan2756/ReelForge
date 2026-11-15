// The Accounts tab's own questions, on a channel of its own in a scratch folder (ui/test/posting_preload.py) with its
// real Python side: logging ReelForge's browser out of a platform, Disconnect and log out, and the stats from before
// a handle changed (keep or forget, with Cancel first). ReelForge's own browser is a stand-in that only remembers
// whether it is logged in: no browser opens. (The browser extension's line on this tab is in posting.test.tsx.)

import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { clickText, find } from "../scripts/shots"
import { ROOT } from "../src/bridge"
import type { AccountsView } from "../src/dashboard/types"
import { DASH } from "../src/theme"
import { toasts } from "../src/ui/Panels"

setDefaultTimeout(40_000)
let open: Headless | null = null
let tmp = ""

/** The Accounts tab of the scratch channel, tall enough for all four platforms' panels. */
async function kit(ask: object = {}, size: [number, number] = [160, 60]): Promise<Headless> {
  tmp = mkdtempSync(join(tmpdir(), "reelforge-accounts-"))
  open = await headless("dashboard", { tab: "accounts" }, size[0], size[1], {
    REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "posting_preload.py"),
    REELFORGE_TEST_ROOT: tmp,
    REELFORGE_TEST_POSTING: JSON.stringify(ask),
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
const flat = (h: Headless) => frame(h).replace(/\s+/g, " ")
const panel = (h: Headless, key: string) => h.bridge.view<AccountsView>("accounts")!.panels.find((p) => p.key === key)!
const hex = (c: { toInts(): [number, number, number, number] }) => "#" + c.toInts().slice(0, 3).map((v) => v.toString(16).padStart(2, "0")).join("")

async function until(h: Headless, ok: () => boolean) {
  for (let i = 0; i < 30 && !ok(); i++) await h.settle(200)
}

/** The colour a button is filled with: the background under the first letter of its label. */
function fill(h: Headless, label: string): string {
  const [x, y] = find(h, label)!
  let col = 0
  for (const span of h.t.captureSpans().lines[y]!.spans) {
    if (x >= col && x < col + span.width) return hex(span.bg)
    col += span.width
  }
  return ""
}

/** Click some text at or below a row (a dialog's button whose label is also on the tab above it). */
async function clickBelow(h: Headless, text: string, from: number, dx = 2) {
  const at = find(h, text, from)
  if (!at) throw new Error(`"${text}" isn't on screen below row ${from}`)
  await h.t.mockMouse.click(at[0] + dx, at[1])
  await h.settle(500)
}

describe("logging ReelForge's browser out", () => {
  test("Log out is there only where ReelForge's own browser is logged in, asks first, and Cancel has the keyboard", async () => {
    const h = await kit({ logged_in: ["instagram"] })
    expect(frame(h).split("▐ Log out ▌").length).toBe(2) // Instagram's, and nobody else's
    expect(panel(h, "instagram").logs_out).toBe(true)
    await clickText(h, "▐ Log out ▌", 3)
    await until(h, () => frame(h).includes("LOG OUT OF INSTAGRAM?"))
    expect(flat(h)).toContain("Your normal browser and your phone stay logged in.")
    expect(flat(h)).toContain("Instagram stays connected, with everything read so far.")
    h.t.mockInput.pressKey("RETURN") // Cancel has the keyboard: nothing happens
    await h.settle(500)
    expect(frame(h)).not.toContain("LOG OUT OF INSTAGRAM?")
    expect(panel(h, "instagram").logs_out).toBe(true)
    await clickText(h, "▐ Log out ▌", 3)
    await until(h, () => frame(h).includes("LOG OUT OF INSTAGRAM?"))
    await clickBelow(h, "▐ Log out ▌", find(h, "▐ Cancel ▌")![1], 3) // the dialog's own
    await until(h, () => panel(h, "instagram").logs_out === false)
    expect(panel(h, "instagram").logs_out).toBe(false)
    expect(flat(h)).toContain("ReelForge's browser is logged out of Instagram.")
    expect(frame(h)).not.toContain("▐ Log out ▌")
    expect(frame(h)).not.toContain("Something went wrong")
  })

  test("Disconnect is on every connected panel in a window 80 columns wide too, where its line is cut short to make room", async () => {
    // The panels are 34 columns wide there. Disconnect was left out when the line beside it didn't fit, and it is
    // nowhere else: at the smallest window ReelForge says it fits, a platform couldn't be disconnected.
    const h = await kit({ logged_in: ["x"] }, [80, 24])
    expect(frame(h)).toMatch(/┃ ● public stats for… +Disconnect +┃ ● public stats for… +Disconnect /) // TikTok and YouTube
    await clickText(h, " Disconnect ", 2)
    await until(h, () => frame(h).includes("DISCONNECT TIKTOK?"))
    expect(frame(h)).toContain("DISCONNECT TIKTOK?")
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(400)
    // A line with a shorter form takes that one whole (X's, read through ReelForge's browser), and wider panels have
    // room for all of it.
    h.t.resize(100, 60)
    await h.settle(600)
    expect(frame(h)).toMatch(/┃ ● public stats for @me +Disconnect +┃ ● reading @me +Disconnect /) // Instagram and X
    h.t.resize(160, 60)
    await h.settle(600)
    expect(frame(h)).toMatch(/● reading @me through ReelForge's browser +Disconnect /)
  })

  test("Disconnect offers Disconnect and log out while ReelForge's browser is logged in there, and only then", async () => {
    const h = await kit({ logged_in: ["x"] })
    await clickText(h, " Disconnect ", 2) // TikTok's, the first panel
    await until(h, () => frame(h).includes("DISCONNECT TIKTOK?"))
    expect(frame(h)).not.toContain("Disconnect and log out")
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(400)
    const y = find(h, "reading @me through ReelForge's browser")![1] // X's panel, the last one: its Disconnect ends the row
    await h.t.mockMouse.click([...frame(h).split("\n")[y]!].join("").lastIndexOf(" Disconnect ") + 2, y)
    await until(h, () => frame(h).includes("DISCONNECT X?"))
    expect(flat(h)).toContain("ReelForge's own browser stays logged in to X")
    expect(frame(h)).toMatch(/▐ Cancel ▌ ▐ Disconnect and log out ▌ ▐ Disconnect ▌/)
    await clickText(h, "Disconnect and log out", 2)
    await until(h, () => panel(h, "x").state === "off")
    expect(panel(h, "x").state).toBe("off") // disconnected: its @handle box is back
    expect(readFileSync(join(tmp, "dashboard", "config.toml"), "utf-8")).toMatch(/\[x\][^[]*handle = ""/)
    await until(h, () => flat(h).includes("ReelForge's browser is logged out of X."))
    expect(flat(h)).toContain("ReelForge's browser is logged out of X.") // and logged out
    expect(frame(h)).not.toContain("Something went wrong")
  })
})

describe("typing in stats", () => {
  test("numbers ReelForge can't take are said in red under the boxes, as under an @handle; good ones are saved", async () => {
    const h = await kit({ needs_login: ["x"] })
    await clickText(h, "Type in stats", 2)
    await until(h, () => frame(h).includes("TYPE IN STATS"))
    await h.t.mockInput.typeText("lots") // the followers box has the keyboard
    await h.settle(200)
    h.t.mockInput.pressKey("RETURN")
    const problem = "Whole numbers only, please"
    await until(h, () => frame(h).includes(problem))
    const [, y] = find(h, problem)!
    expect(hex(h.t.captureSpans().lines[y]!.spans.find((sp) => sp.text.includes(problem))!.fg)).toBe(DASH.bad)
    for (let i = 0; i < 4; i++) h.t.mockInput.pressKey("BACKSPACE")
    await h.t.mockInput.typeText("1,234")
    await h.settle(200)
    expect(find(h, "Cancel")![1] - find(h, "Followers")![1]).toBeLessThan(10) // no rows of nothing where videos would be
    h.t.mockInput.pressKey("RETURN")
    await until(h, () => !frame(h).includes("TYPE IN STATS"))
    expect(frame(h)).not.toContain("TYPE IN STATS")
    expect(frame(h)).not.toContain("Something went wrong")
  })
})

describe("stats from before a handle changed", () => {
  const ASK = { was: { tiktok: "@oldname" } }

  test("the panel says they're there, and its button asks: Cancel first, forget in red", async () => {
    const h = await kit(ASK)
    expect(flat(h)).toContain("Also holds stats read as @oldname, from before the handle changed.")
    await clickText(h, "Stats from @oldname…", 2)
    await until(h, () => frame(h).includes("TIKTOK WAS @OLDNAME BEFORE"))
    expect(flat(h)).toContain("If you renamed the account, keep them")
    expect(flat(h)).toContain("after ReelForge saves a copy of the whole stats database beside it")
    expect(frame(h)).toMatch(/▐ Cancel ▌ ▐ Keep them ▌ ▐ Forget @oldname's stats ▌/)
    expect(fill(h, "Forget @oldname's stats")).toBe(DASH.bad)
    expect(fill(h, "Keep them")).not.toBe(DASH.bad)
    h.t.mockInput.pressKey("RETURN") // Cancel has the keyboard
    await h.settle(500)
    expect(frame(h)).not.toContain("TIKTOK WAS @OLDNAME BEFORE")
    expect(panel(h, "tiktok").old_handles).toEqual(["@oldname"]) // nothing was decided
    // Forget: the other account's readings and videos go, after a copy of the stats is saved.
    await clickText(h, "Stats from @oldname…", 2)
    await until(h, () => frame(h).includes("TIKTOK WAS @OLDNAME BEFORE"))
    await clickText(h, "Forget @oldname's stats", 2)
    await until(h, () => (panel(h, "tiktok").old_handles ?? []).length === 0)
    expect(panel(h, "tiktok").old_handles).toEqual([])
    expect(flat(h)).toContain("Forgot @oldname's TikTok stats")
    expect(frame(h)).not.toContain("Stats from @oldname…")
    expect(frame(h)).not.toContain("Something went wrong")
  })

  test("Keep them files them under the handle the account has now", async () => {
    const h = await kit(ASK)
    await clickText(h, "Stats from @oldname…", 2)
    await until(h, () => frame(h).includes("TIKTOK WAS @OLDNAME BEFORE"))
    await clickText(h, "Keep them", 2)
    await until(h, () => (panel(h, "tiktok").old_handles ?? []).length === 0)
    expect(flat(h)).toContain("Kept: they count as @me's from now on.")
    expect(frame(h)).not.toContain("Stats from @oldname…")
    expect(flat(h)).not.toContain("Also holds stats read as @oldname")
  })
})
