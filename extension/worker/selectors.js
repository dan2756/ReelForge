// The page hooks each platform script looks for. They are data: lists of ways to find an element. The steps
// themselves live in this extension's code, so a newer list can only change where the code looks.
//
// The list packed with the extension is used unless the ReelForge app on this computer has a newer one, which
// lets a fixed hook reach the browser before the person has pressed Reload on the extension.
import * as app from "./app.js"
import { PLATFORMS } from "../shared/consts.js"

const local = chrome.storage.local
const LIMIT = 64 * 1024

let packed = null

async function builtIn() {
  packed ||= await (await fetch(chrome.runtime.getURL("selectors.json"))).json()
  return packed
}

const plain = value => typeof value === "string" || typeof value === "number" || typeof value === "boolean"
  || (Array.isArray(value) && value.every(item => typeof item === "string"))

// A list from the app is taken only when it has the expected shape and holds nothing but plain values.
export function valid(data) {
  if (!data || typeof data !== "object" || !Number.isInteger(data.version)) return false
  if (!data.platforms || typeof data.platforms !== "object") return false
  if (JSON.stringify(data).length > LIMIT) return false
  for (const [name, platform] of Object.entries(data.platforms)) {
    if (!PLATFORMS[name] || !platform || typeof platform.hooks !== "object") return false
    for (const hook of Object.values(platform.hooks)) {
      if (!hook || !Array.isArray(hook.strategies)) return false
      for (const strategy of hook.strategies) {
        if (!strategy || typeof strategy !== "object" || !Object.values(strategy).every(plain)) return false
      }
    }
  }
  return true
}

async function active() {
  const own = await builtIn()
  const { selectors: fromApp } = await local.get("selectors")
  return fromApp && valid(fromApp) && fromApp.version > own.version ? fromApp : own
}

export async function selectorsFor(platform) {
  const all = await active()
  return { version: all.version, ...(all.platforms[platform] || { hooks: {} }) }
}

// Called after a heartbeat, with the version the app says it has.
export async function refresh(found, theirVersion) {
  if (!Number.isInteger(theirVersion) || theirVersion <= (await active()).version) return
  try {
    const res = await app.api(found, "/v1/selectors")
    if (res.ok && valid(res.data) && res.data.version === theirVersion) await local.set({ selectors: res.data })
  } catch {
    // The packed list keeps working; the next heartbeat asks again.
  }
}
