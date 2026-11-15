// The Publish tab on a channel of its own in a scratch folder (ui/test/settings_preload.py: one video, nothing
// connected), with its real Python side: what the window has to get right before anything is posted from it.

import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { headless, type Headless } from "../scripts/headless"
import { ROOT } from "../src/bridge"
import { toasts } from "../src/ui/Panels"

setDefaultTimeout(30_000)
let open: Headless | null = null
let tmp = ""

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
