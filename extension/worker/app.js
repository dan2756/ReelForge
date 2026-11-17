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
    // No answer. The request may have reached ReelForge all the same (its answer was cut off, or came too late),
    // and `sent` says so to a caller that must never send it twice (the press). (A refused connection looks the same
    // here, which is why that caller has the port answer a hello first.)
    const down = new AppDown(String(error?.message || error))
    down.sent = true
    throw down
  }
}

async function json(response) {
  try {
    return await response.json()
  } catch {
    return null
  }
}

const hex = bytes => [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, "0")).join("")

// What ReelForge answers to a nonce when it is the ReelForge a token came from: an HMAC over the nonce, the port
// it listens on and its id, keyed with the token's SHA-256 (which is all ReelForge keeps of the token).
async function proofFor(token, nonce, port, appId) {
  const text = new TextEncoder()
  const digest = await crypto.subtle.digest("SHA-256", text.encode(token))
  const key = await crypto.subtle.importKey("raw", digest, { name: "HMAC", hash: "SHA-256" }, false, ["sign"])
  return hex(await crypto.subtle.sign("HMAC", key, text.encode(`reelforge hello\n${nonce}\n${port}\n${appId}`)))
}

// The ports that have just proven to be the ReelForge a token came from, and when. Ten ports on this computer
// are easy for another program to listen on, and anyone can learn a ReelForge's id by asking it. So nothing is
// believed, and the token is sent nowhere, until the port has answered a fresh nonce the way only that
// ReelForge can. The port is part of the answer: the real ReelForge's answer, passed on by a program on
// another port, does not fit there.
const PROOF_FOR = 2000
const proven = new Map()

// What answers on one port, or null when it is not ReelForge. `paired` is true only with the proof, so a
// ReelForge that has forgotten this browser, and a program that only says it is ReelForge, both show as "not
// connected". No token is sent here.
export async function at(port) {
  if (!PORTS.includes(port)) return null
  proven.delete(port)
  const nonce = hex(crypto.getRandomValues(new Uint8Array(16)))
  let response
  try {
    response = await request(port, `/v1/hello?install=${encodeURIComponent(await install())}&nonce=${nonce}`, { timeout: 1500 })
  } catch {
    return null
  }
  const data = response.ok ? await json(response) : null
  if (!data || data.app !== "reelforge" || typeof data.app_id !== "string") return null
  // Which side is behind, so the message can say what to update.
  const mismatch = data.protocol === PROTOCOL ? null : data.protocol > PROTOCOL ? "extension" : "app"
  const token = await tokenFor(data.app_id)
  const itIs = Boolean(token) && typeof data.proof === "string" && data.proof === await proofFor(token, nonce, port, data.app_id)
  if (itIs) proven.set(port, { app_id: data.app_id, at: Date.now() })
  return {
    port,
    app_id: data.app_id,
    version: String(data.version || ""),
    protocol: data.protocol,
    profile: { key: String(data.profile?.key || ""), name: String(data.profile?.name || "") },
    demo: data.demo === true,
    paired: itIs && !mismatch && data.demo !== true,
    mismatch,
  }
}

// True when this port has proven, just now, to be the ReelForge the token for `appId` came from.
async function trusted(port, appId) {
  const have = proven.get(port)
  if (have && have.app_id === appId && Date.now() - have.at < PROOF_FOR) return true
  await at(port)
  return proven.get(port)?.app_id === appId
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

// The window a job belongs to. Its port can change when ReelForge is closed and opened again mid-job. The one
// that proved itself comes first: another program can give the same id and name on a lower port.
export async function find(appId, profileKey, fresh = false) {
  const apps = (await discover(fresh)).filter(a => a.app_id === appId && a.profile.key === profileKey)
  return apps.find(a => a.paired) || apps[0] || null
}

// The window the popup shows: the one the person picked, else the first connected one, else the first.
export async function current() {
  const apps = await discover()
  const { selected } = await local.get("selected")
  const picked = apps.filter(a => selected && a.app_id === selected.app_id && a.profile.key === selected.profile_key)
  return picked.find(a => a.paired) || picked[0] || apps.find(a => a.paired) || apps[0] || null
}

export async function select(port) {
  const app = (await discover()).find(a => a.port === port)
  if (app) await local.set({ selected: { app_id: app.app_id, profile_key: app.profile.key } })
}

// The token for a call, once the port has proven itself. Otherwise AppDown: to this extension a port that
// cannot prove it is ReelForge is a port with no ReelForge on it. `fresh`: proven by a hello made now, whatever an
// earlier one said a moment ago.
async function tokenTo(app, fresh = false) {
  if (fresh) proven.delete(app.port)
  if (!(await trusted(app.port, app.app_id))) throw new AppDown("nothing on this port proved to be the connected ReelForge")
  return tokenFor(app.app_id)
}

// A call that needs the token. Returns { ok, status, data }. Throws AppDown when nothing answers. `fresh`: for a call
// that is never made twice (the press). A failure to reach ReelForge is then found by the hello, before the call
// goes: such an AppDown carries no `sent`, so the caller may look for ReelForge on another port, or say it is closed.
export async function api(app, path, { method = "GET", body, timeout, fresh = false } = {}) {
  const token = await tokenTo(app, fresh)
  const response = await request(app.port, path, { method, token, body, timeout })
  return { ok: response.ok, status: response.status, data: await json(response) }
}

// One piece of a job's video. The ETag is the file's SHA-256, so a file swapped between two pieces shows.
export async function filePiece(app, jobId, offset, length) {
  const token = await tokenTo(app)
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

// Pairing: the pair page's secret buys a token, once. From then on the token goes only to a port that proves
// it is the ReelForge that gave it out.
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
