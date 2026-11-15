// Finding the ReelForge app on this computer and talking to it.
//
// Every request goes from the worker to 127.0.0.1, never from a page: a page's request would be made as the
// platform's site, and the app would have no way to tell it from the site's own scripts.
import { PROTOCOL, PORTS } from "../shared/consts.js"

const local = chrome.storage.local

export const version = chrome.runtime.getManifest().version

// Thrown when nothing answers on the port. Callers keep what they wanted to say and try again later.
export class AppDown extends Error {}

// One id per copy of the extension, made the first time it is needed. The app lists paired browsers by it.
export async function install() {
  const got = await local.get("install")
  if (got.install) return got.install
  const id = crypto.randomUUID()
  await local.set({ install: id })
  return id
}

// "Chrome on macOS": what the app shows next to this browser on its pair page.
export function browserName() {
  const data = navigator.userAgentData
  const brands = (data?.brands || []).map(b => b.brand)
  const browser = ["Microsoft Edge", "Brave", "Opera", "Vivaldi", "Google Chrome", "Chromium"].find(b => brands.includes(b)) || "Chromium"
  const system = data?.platform || ""
  return (browser.replace("Microsoft ", "").replace("Google ", "") + (system ? " on " + system : "")).slice(0, 80)
}

async function tokens() {
  return (await local.get("tokens")).tokens || {}
}

export async function tokenFor(appId) {
  return (await tokens())[appId] || null
}

async function request(port, path, { method = "GET", token, body, range, timeout = 5000 } = {}) {
  const headers = {}
  if (token) headers["X-ReelForge-Token"] = token
  if (body !== undefined) headers["Content-Type"] = "application/json"
  if (range) headers.Range = range
  try {
    return await fetch(`http://127.0.0.1:${port}${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store", credentials: "omit", redirect: "error", signal: AbortSignal.timeout(timeout),
    })
  } catch (error) {
    throw new AppDown(String(error?.message || error))
  }
}

async function json(response) {
  try {
    return await response.json()
  } catch {
    return null
  }
}

// What answers on one port, or null when it is not ReelForge. `paired` is the app's own word that our token
// is good, so a token it has forgotten shows as "not connected" and not as a list of failing buttons.
async function hello(port, token) {
  let response
  try {
    response = await request(port, "/v1/hello", { token, timeout: 1500 })
  } catch {
    return null
  }
  const data = response.ok ? await json(response) : null
  if (!data || data.app !== "reelforge" || typeof data.app_id !== "string") return null
  return {
    port,
    app_id: data.app_id,
    version: String(data.version || ""),
    protocol: data.protocol,
    profile: { key: String(data.profile?.key || ""), name: String(data.profile?.name || "") },
    demo: data.demo === true,
    paired: data.paired === true,
    // Which side is behind, so the message can say what to update.
    mismatch: data.protocol === PROTOCOL ? null : data.protocol > PROTOCOL ? "extension" : "app",
  }
}

// The app on one port, with `paired` checked against the token we hold for it.
export async function at(port) {
  if (!PORTS.includes(port)) return null
  const first = await hello(port)
  if (!first) return null
  const token = await tokenFor(first.app_id)
  // The token goes only to an app that has just named itself as the one it belongs to.
  if (!token || first.mismatch || first.demo) return first
  const second = await hello(port, token)
  return second && second.app_id === first.app_id ? second : first
}

let found = null

// Every ReelForge window that is open (one per channel), lowest port first. The answer is kept for a moment
// so the popup, the website bridge and a job can all ask without ten requests each.
export async function discover(fresh = false) {
  if (!fresh && found && Date.now() - found.at < 5000) return found.apps
  const apps = (await Promise.all(PORTS.map(at))).filter(Boolean)
  found = { at: Date.now(), apps }
  return apps
}

// Called when a window that was there stops answering, so the next look is a real one.
export function forget() {
  found = null
}

// The window a job belongs to. Its port can change when ReelForge is closed and opened again mid-job.
export async function find(appId, profileKey, fresh = false) {
  const apps = await discover(fresh)
  return apps.find(a => a.app_id === appId && a.profile.key === profileKey) || null
}

// The window the popup shows: the one the person picked, else the first connected one, else the first.
export async function current() {
  const apps = await discover()
  const { selected } = await local.get("selected")
  return apps.find(a => selected && a.app_id === selected.app_id && a.profile.key === selected.profile_key)
    || apps.find(a => a.paired) || apps[0] || null
}

export async function select(port) {
  const app = (await discover()).find(a => a.port === port)
  if (app) await local.set({ selected: { app_id: app.app_id, profile_key: app.profile.key } })
}

// A call that needs the token. Returns { ok, status, data }. Throws AppDown when nothing answers.
export async function api(app, path, { method = "GET", body, timeout } = {}) {
  const token = await tokenFor(app.app_id)
  const response = await request(app.port, path, { method, token, body, timeout })
  return { ok: response.ok, status: response.status, data: await json(response) }
}

// One piece of a job's video. The ETag is the file's SHA-256, so a file swapped between two pieces shows.
export async function filePiece(app, jobId, offset, length) {
  const token = await tokenFor(app.app_id)
  const response = await request(app.port, `/v1/jobs/${jobId}/file`, {
    token, range: `bytes=${offset}-${offset + length - 1}`, timeout: 25000,
  })
  if (!response.ok) return { ok: false, status: response.status, data: await json(response) }
  let bytes
  try {
    bytes = new Uint8Array(await response.arrayBuffer())
  } catch (error) {
    throw new AppDown(String(error?.message || error))
  }
  // An app that ignores Range sends the whole file with 200: take the piece that was asked for.
  if (response.status === 200) bytes = bytes.subarray(offset, offset + length)
  return { ok: true, bytes, etag: (response.headers.get("ETag") || "").replace(/^W\//, "").replace(/"/g, "") }
}

// Pairing: the pair page's secret buys a token, once. Only the token's owner (this app) ever gets it back.
export async function pair(port, secret) {
  const app = await at(port)
  if (!app) return "app_unreachable"
  if (app.mismatch) return app.mismatch === "extension" ? "extension_outdated" : "protocol"
  let response
  try {
    response = await request(port, "/v1/pair/claim", {
      method: "POST",
      body: { secret, install: await install(), name: browserName(), extension_version: version, protocol: PROTOCOL },
    })
  } catch {
    return "app_unreachable"
  }
  const data = await json(response)
  if (!response.ok) return String(data?.error || "http_" + response.status)
  if (typeof data?.token !== "string" || data.token.length < 43 || data.app_id !== app.app_id) return "bad_answer"
  await local.set({ tokens: { ...(await tokens()), [data.app_id]: data.token } })
  found = null
  return "ok"
}

// Disconnect: tell every open ReelForge window to forget this browser, then forget the tokens here.
export async function unpair() {
  const id = await install()
  for (const app of await discover(true)) {
    if (!app.paired) continue
    try {
      await api(app, "/v1/unpair", { method: "POST", body: { install: id } })
    } catch {
      // The token is dropped here either way; a window that was closed forgets it the next time it is asked.
    }
  }
  await local.remove(["tokens", "selected", "selectors"])
  found = null
}

// The heartbeat. It tells the app this browser is here and which accounts it last saw signed in, and brings
// back the version of the app's selectors so newer ones can be fetched.
export async function seen(app) {
  const { accounts = {} } = await local.get("accounts")
  const body = { install: await install(), name: browserName(), extension_version: version, protocol: PROTOCOL, accounts }
  const res = await api(app, "/v1/seen", { method: "POST", body })
  return res.ok ? res.data : null
}

// What one job's tab saw about the account, kept for the next heartbeat.
export async function sawAccount(platform, signedIn, handle) {
  const { accounts = {} } = await local.get("accounts")
  accounts[platform] = { signed_in: signedIn, handle: handle || null }
  await local.set({ accounts })
}
