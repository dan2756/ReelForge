// The window drawn without a terminal, on OpenTUI's test renderer, with the real Python side: what the screenshots
// (scripts/shots.tsx) and the window's tests (test/) use.

import "./quiet"
import { createTestRenderer, type TestRendererSetup } from "@opentui/core/testing"
import { createRoot } from "@opentui/react"
import type { Bridge } from "../src/bridge"
import type { Init } from "../src/dashboard/types"
import { RENDERER, Root, Sides } from "../src/root"
import { SetupApp, type SetupInit } from "../src/setup/App"

export interface Headless {
  t: TestRendererSetup
  /** The Python side the window started with (after switching profiles: sides.active). */
  bridge: Bridge
  sides: Sides
  /** Let the window take in what the Python side sent: a few frames, a pause, a few more. */
  settle: (ms?: number) => Promise<void>
  exits: { code: number; result?: object }[]
  close: () => Promise<void>
}

export async function headless(
  screen: "dashboard" | "setup",
  options: Record<string, unknown>,
  width: number,
  height: number,
  env: Record<string, string> = {},
): Promise<Headless> {
  // Nothing on this computer changes, and the window doesn't listen for the browser extension unless `env` asks
  // (REELFORGE_EXTENSION_PORT: "0", any free port). Both are said here, so a test's window is the same whatever the
  // two variables are where the tests run: with the port set outside, every window of a set-up channel listened.
  const sides = new Sides({ STUDIO_DRY_SYSTEM: "1", REELFORGE_EXTENSION_PORT: "off", ...env }, "")
  const opened = await sides.open<Init | SetupInit>(screen, options)
  const bridge = opened.bridge
  sides.adopt(bridge)
  const t = await createTestRenderer({ ...RENDERER, width, height }) // as the real window's: Ctrl+C is a key, not the end
  // A frame read while the renderer is writing the next one out is refused (OutputBusy, once in many runs on GitHub's
  // machines). Waiting for it can't be done here: the renderer finishes on the event loop, which a read blocks (reading
  // again for 200 ms still failed). The screen still shows the frame before then, so that is the one given: the last
  // one read, at least as new as the end of the last settle(). Anything else it throws is the test's to see.
  const busy = (e: unknown) => String((e as Error)?.message ?? e).includes("OutputBusy")
  const chars = t.captureCharFrame
  const spans = t.captureSpans
  let lastChars: string | undefined
  let lastSpans: ReturnType<typeof spans> | undefined
  t.captureCharFrame = () => {
    try {
      return (lastChars = chars())
    } catch (e) {
      if (lastChars === undefined || !busy(e)) throw e
      return lastChars
    }
  }
  t.captureSpans = () => {
    try {
      return (lastSpans = spans())
    } catch (e) {
      if (lastSpans === undefined || !busy(e)) throw e
      return lastSpans
    }
  }
  const remember = () => {
    try {
      lastChars = chars()
      lastSpans = spans()
    } catch (e) {
      if (!busy(e)) throw e
    }
  }
  const exits: { code: number; result?: object }[] = []
  const quit = (code = 0, result?: object) => void exits.push({ code, result })
  const root = createRoot(t.renderer)
  if (screen === "setup") root.render(<SetupApp bridge={bridge} init={opened.init as SetupInit} quit={quit} options={options} />)
  else root.render(<Root sides={sides} first={{ kind: "dashboard", bridge, init: opened.init as Init, n: 0 }} quit={quit} />)
  const settle = async (ms = 400) => {
    for (let i = 0; i < 3; i++) await t.renderOnce()
    await Bun.sleep(ms)
    for (let i = 0; i < 3; i++) await t.renderOnce()
    remember()
  }
  return {
    t,
    bridge,
    sides,
    settle,
    exits,
    close: async () => {
      const { done } = await sides.stop()
      await Promise.race([done, Bun.sleep(5000)])
      sides.active?.kill()
      bridge.kill()
      root.unmount()
      t.renderer.destroy()
    },
  }
}

if (import.meta.main) {
  // bun scripts/headless.tsx dashboard '{"demo": true, "tab": "videos"}' 180 50
  const [screen = "dashboard", raw = "{}", w = "180", h = "50"] = process.argv.slice(2)
  const h1 = await headless(screen as "dashboard", JSON.parse(raw), Number(w), Number(h))
  await h1.settle(2500)
  console.log(h1.t.captureCharFrame())
  await h1.close()
  process.exit(0)
}
