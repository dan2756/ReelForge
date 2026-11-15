// What the window makes of a call its Python side fails: a pop-up that says so, and the screen as it was, on the demo's
// window and the first-run screens (ui/test/failing_preload.py) and on a channel in a scratch folder
// (ui/test/posting_preload.py), each with the calls a test names raising. Never a silent nothing, and never a promise
// nobody caught: bun fails a test for one of those by itself, so every test here would fail without the catch it is
// about. (A failing Post… is in posting.test.tsx.)

import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { createTestRenderer } from "@opentui/core/testing"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { clickText, find } from "../scripts/shots"
import { ROOT } from "../src/bridge"
import { RENDERER } from "../src/root"
import { toasts } from "../src/ui/Panels"

setDefaultTimeout(40_000)
let open: Headless | null = null
let tmp = ""

afterEach(async () => {
  await open?.close()
  open = null
  toasts.clear()
  if (tmp) rmSync(tmp, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 })
  tmp = ""
})

const frame = (h: Headless) => h.t.captureCharFrame()
const SAID = "Something went wrong"

async function until(h: Headless, ok: () => boolean) {
  for (let i = 0; i < 30 && !ok(); i++) await h.settle(200)
}

/** The demo's window, or a first-run screen, with these calls failing. */
async function failing(screen: "dashboard" | "setup", options: Record<string, unknown>, fail: string, size: [number, number] = [150, 46]): Promise<Headless> {
  open = await headless(screen, options, size[0], size[1], {
    REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "failing_preload.py"),
    REELFORGE_TEST_FAIL: fail,
  })
  await open.settle(2500)
  return open
}

/** A channel in a scratch folder, with what the test asks its preload for. */
async function kit(ask: object, tab: string, size: [number, number] = [150, 68]): Promise<Headless> {
  tmp = mkdtempSync(join(tmpdir(), "reelforge-errors-"))
  open = await headless("dashboard", { tab }, size[0], size[1], {
    REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "posting_preload.py"),
    REELFORGE_TEST_ROOT: tmp,
    REELFORGE_TEST_POSTING: JSON.stringify(ask),
  })
  await open.settle(3000)
  return open
}

describe("OpenTUI's developer console", () => {
  test("never opens over the window, whatever goes wrong", async () => {
    const t = await createTestRenderer({ ...RENDERER, width: 40, height: 10 })
    const error = console.error
    console.error = () => {} // (the renderer writes the error down, which is all it does now)
    try {
      process.emit("unhandledRejection" as never, new Error("a promise nobody caught") as never, Promise.resolve() as never)
    } finally {
      console.error = error
    }
    expect(t.renderer.console.visible).toBe(false)
    t.renderer.destroy()
  })
})

describe("a call that fails in the ReelForge window", () => {
  test("Type in stats: Save says so, and the dialog stays with what was typed", async () => {
    const h = await kit({ needs_login: ["x"], fail: "save_logged_stats" }, "accounts")
    await clickText(h, "Type in stats", 2)
    await until(h, () => frame(h).includes("LOG STATS"))
    await h.t.mockInput.typeText("1234")
    await h.settle(200)
    await clickText(h, "▐ Save ▌", 3)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain("RuntimeError: boom")
    expect(frame(h)).toContain("LOG STATS") // nothing was saved, so it didn't close as if it had
    expect(frame(h)).toContain("1234")
  })

  test("a sign-up page that doesn't open isn't said to have opened", async () => {
    const h = await failing("dashboard", { demo: true, only_demos: true, tab: "accounts", unconnected: ["x"] }, "open_signup", [180, 50])
    await clickText(h, " Sign up → ", 2)
    await until(h, () => frame(h).includes("MAKE YOUR X ACCOUNT"))
    await clickText(h, "Open X sign-up →", 2)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    expect(frame(h)).not.toContain("✓ Opened")
    expect(frame(h)).toContain("MAKE YOUR X ACCOUNT") // still there, to try again
  })

  test("the Settings tab's typed-name dialogs: the pop-up says it didn't happen", async () => {
    const h = await kit({ fail: ["factory_reset", "uninstall"] }, "settings")
    for (const [button, title, go] of [
      ["Factory reset…", "FACTORY RESET", "Reset ReelForge"],
      ["Uninstall…", "UNINSTALL REELFORGE", "Uninstall ReelForge"],
    ] as const) {
      await clickText(h, button)
      await until(h, () => frame(h).includes(title))
      await h.t.mockInput.typeText("Night Physics")
      await h.settle(300)
      await clickText(h, `▐ ${go} ▌`, 3)
      await until(h, () => frame(h).includes(SAID))
      expect(frame(h)).toContain("RuntimeError: boom")
      expect(h.exits).toEqual([]) // and ReelForge stays open
      toasts.clear()
      await h.settle(200)
    }
  })
})

describe("a call that fails on the first-run screens", () => {
  test("the channel's questions that can't be read say so, and Esc still leaves", async () => {
    const h = await failing("setup", { start: "onboard", preview: true }, "onboard_start", [140, 46])
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(300)
    expect(frame(h)).toContain("Leave without saving?")
  })

  test("Next that fails keeps the step and its answers", async () => {
    const h = await failing("setup", { start: "onboard", preview: true }, "onboard_next", [140, 46])
    expect(frame(h)).toContain("1/8  You")
    await clickText(h, "  Next  ", 4)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    expect(frame(h)).toContain("1/8  You")
  })

  test("Create my studio that fails stops saying it is writing", async () => {
    const h = await failing("setup", { start: "onboard", preview: true }, "onboard_create", [140, 46])
    for (let step = 0; step < 7; step++) await clickText(h, "  Next  ", 4)
    expect(frame(h)).toContain("8/8")
    await clickText(h, "  Create my studio  ", 4) // the button (the words above it name it too)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    expect(frame(h)).not.toContain("Writing your studio…")
    expect(h.exits).toEqual([])
  })

  test("a sign-up guide that can't be read opens no empty dialog", async () => {
    const h = await failing("setup", { start: "onboard", preview: true }, "signup_guide", [140, 46])
    for (let step = 0; step < 4; step++) await clickText(h, "  Next  ", 4)
    await clickText(h, "No account? Sign up", 2)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    expect(frame(h)).toContain("5/8  Your accounts")
    expect(find(h, "Your @handle")).toBeNull()
  })

  test("inside a sign-up guide: a page that doesn't open isn't said to have opened, and Done that fails keeps the guide", async () => {
    const h = await failing("setup", { start: "onboard", preview: true }, "open_signup,signup_handle", [140, 46])
    for (let step = 0; step < 4; step++) await clickText(h, "  Next  ", 4)
    await clickText(h, "No account? Sign up", 2)
    await until(h, () => frame(h).includes("Make your TikTok account"))
    await clickText(h, "Open TikTok sign-up →", 2)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    expect(frame(h)).not.toContain("Opened")
    toasts.clear()
    await h.settle(200)
    await clickText(h, "▐ Done ▌", 3)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    expect(frame(h)).toContain("Make your TikTok account")
  })

  test("the step after the tools: the screen stays, with its button to press again", async () => {
    const h = await failing("setup", { start: "tools", preview: true }, "after_tools", [120, 40])
    for (let i = 0; i < 40 && !find(h, "Desktop shortcut"); i++) await h.settle(100)
    await clickText(h, "Skip for now", 2)
    await until(h, () => frame(h).includes("Health check"))
    const next = /▐ +(Next: your channel|Continue) +▌/
    expect(frame(h)).toMatch(next)
    await clickText(h, frame(h).match(next)![1]!, 2)
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    expect(frame(h)).toContain("Health check")
    expect(frame(h)).toMatch(next)
  })

  test("the last screen keeps its buttons when what it says can't be read", async () => {
    const h = await failing("setup", { start: "done", preview: true }, "done_info", [130, 40])
    await until(h, () => frame(h).includes(SAID))
    expect(frame(h)).toContain(SAID)
    await clickText(h, "Close", 2)
    expect(h.exits).toEqual([{ code: 0, result: undefined }])
  })
})
