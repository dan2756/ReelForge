// The page hooks each platform script looks for. They are data: lists of ways to find an element. The steps
// themselves live in this extension's code.
//
// The list packed with the extension is used unless the ReelForge app on this computer has a newer one, which
// lets a fixed hook reach the browser before the person has pressed Reload on the extension. Not everything
// comes from the newer list. Where the code looks decides what it clicks and what it concludes, so these are
// always the packed ones, and change only when the person reloads the extension: the hooks whose element is
// clicked (Next, Create, OK and the final button itself); the platform's signs that a post went out, that it is
// asking the person to prove something, and that nobody is signed in, with every hook those signs name; the
// hooks that guard a post (the captcha, the platform's own error message, the account, YouTube's audience and
// visibility answers: GUARD_HOOKS); and every hook one of these looks inside or near. A newer list can change how
// long they are waited for, and nothing else about them.
import * as app from "./app.js"
import { PLATFORMS, PACKED_HOOKS, GUARD_HOOKS, keptHooks } from "../shared/consts.js"

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
  const own = (await builtIn()).platforms[platform] || { hooks: {} }
  const theirs = all.platforms[platform] || { hooks: {} }
  const hooks = { ...theirs.hooks }
  const wait = theirs => (theirs?.timeout_ms > 0 ? { timeout_ms: theirs.timeout_ms } : {})
  // The signs are the packed ones. Only how long to wait for them may differ.
  const signs = {}
  for (const name of ["success", "challenge", "signed_out"]) signs[name] = own[name] ? { ...own[name], ...wait(theirs[name]) } : undefined
  const named = Object.values(signs).flatMap(sign => (sign ? [...(Array.isArray(sign.hooks) ? sign.hooks : []), sign.gone_after_press] : []))
  const clicked = PACKED_HOOKS[platform] || []
  for (const name of keptHooks(own.hooks, [...clicked, ...(GUARD_HOOKS[platform] || []), ...named.filter(Boolean)])) {
    // (A clicked hook is taken whole, as it always was. The others keep the app's word on how long to wait.)
    if (own.hooks[name]) hooks[name] = clicked.includes(name) ? own.hooks[name] : { ...own.hooks[name], ...wait(theirs.hooks[name]) }
    else delete hooks[name]
  }
  return { version: all.version, ...theirs, hooks, ...signs }
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
