// What the popup and the options page show and change.
import * as app from "./app.js"
import { nudge } from "./jobs.js"
import { PRESS_PLATFORMS } from "../shared/consts.js"

const local = chrome.storage.local

let lastBeat = 0

// Everything the popup needs in one answer: which ReelForge windows are open, which one is shown, whether
// this browser is connected to it, and that window's list of videos.
export async function state(fresh) {
  const apps = await app.discover(fresh === true)
  const shown = await app.current()
  let connection = "not_running"
  if (shown) {
    connection = shown.mismatch === "extension" ? "extension_outdated" : shown.mismatch === "app" ? "app_outdated"
      : shown.demo ? "demo" : shown.paired ? "connected" : "not_connected"
  }
  let data = null
  let problem = null
  if (connection === "connected") {
    try {
      const res = await app.api(shown, "/v1/state")
      await nudge()
      if (res.ok) data = res.data
      else problem = String(res.data?.message || "ReelForge could not list your videos.")
      // Opening the popup is a sign of life worth telling the app about, but not on every redraw.
      if (Date.now() - lastBeat > 30000) {
        lastBeat = Date.now()
        await app.seen(shown)
      }
    } catch {
      app.forget()
      connection = "not_running"
    }
  }
  const { press = {} } = await local.get("press")
  return { version: app.version, connection, apps, port: shown?.port ?? null, state: data, problem, press }
}

// Press Post for me, per platform. Stored only here, in the browser. It can be switched on only for a platform
// the connected ReelForge says is unlocked (one assisted post there already went through), and never for X.
export async function setPress(platform, on) {
  if (!PRESS_PLATFORMS.includes(platform)) return { ok: false, message: "Press Post for me is not offered there." }
  const { press = {} } = await local.get("press")
  if (on === true) {
    const now = await state()
    const unlocked = now.state?.platforms?.find(p => p.key === platform)?.press_unlocked === true
    if (!unlocked) return { ok: false, message: "Post there once yourself first, with ReelForge open." }
  }
  press[platform] = on === true
  await local.set({ press })
  return { ok: true, press }
}

export async function disconnect() {
  await app.unpair()
  return { ok: true }
}
