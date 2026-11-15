// The jobs this browser is working on: one tab per job, from claim to the final word.
//
// Chrome stops this worker after 30 seconds of quiet and starts it again for the next event, so nothing here
// lives in a variable for longer than one event. Each job is a record in chrome.storage.session (kept across a
// worker restart, gone when the browser closes), and the app's ledger is the lasting copy.
import * as app from "./app.js"
import { selectorsFor, refresh } from "./selectors.js"
import { PLATFORMS, PRESS_PLATFORMS, rank, isFinal, isLive, platformName } from "../shared/consts.js"

const session = chrome.storage.session
const local = chrome.storage.local

export const ALARM = "reelforge-tick"

const MINUTE = 60 * 1000
const QUIET_LIMIT = 3 * MINUTE     // a page that went silent before it was ready
const PAUSE_LIMIT = 15 * MINUTE    // a wrong-account question nobody answered
const CONFIRM_LIMIT = 10 * MINUTE  // Post was pressed for the person, and the page never confirmed it
const READY_LIMIT = 45 * MINUTE    // the app stops waiting on a ready job after this long too
const KEEP_DONE = 60 * MINUTE      // how long a finished job stays in the popup
const PIECE = 8 * 1024 * 1024      // the most video one message may carry (Chrome's limit is 64 MiB)

const BATCH_ID = /^pb_[0-9a-f]{16}$/
const JOB_ID = /^pj_[0-9a-f]{16}$/
// What a tab may report. "posting" is missing on purpose: only the app's press grant puts a job there.
const TAB_STATES = ["opening", "uploading", "filling", "ready", "posted", "failed", "cancelled", "unknown"]
const SCRIPTS = platform => ["platform/engine.js", `platform/${platform}.js`, "platform/run.js"]

const key = id => "job:" + id

export async function getJob(id) {
  return (await session.get(key(id)))[key(id)] || null
}

export async function allJobs() {
  const all = await session.get(null)
  return Object.keys(all).filter(k => k.startsWith("job:")).map(k => all[k])
}

async function jobOfTab(tabId) {
  return (await allJobs()).find(job => job.tabId === tabId && isLive(job)) || null
}

const save = job => session.set({ [key(job.id)]: job })

// Two events for one job never interleave their read and write, or the later one would undo the earlier.
const chains = new Map()

function locked(id, work) {
  const run = (chains.get(id) || Promise.resolve()).then(work, work)
  chains.set(id, run.catch(() => {}))
  return run
}

function patch(id, change) {
  return locked(id, async () => {
    const job = await getJob(id)
    if (!job) return null
    const next = { ...job, ...change }
    await save(next)
    return next
  })
}

// chrome.storage.session is emptied when the browser closes or the extension is reloaded, and a job in the
// middle of its work would then be forgotten here while the app still waits for it. So the few facts needed to
// end such a job are also kept in chrome.storage.local, for as long as the job is live.
let ledgerWork = Promise.resolve()

// One change to that list at a time: two jobs reporting together must not write over each other.
function ledger(change) {
  ledgerWork = ledgerWork.then(async () => {
    const all = (await local.get("ledger")).ledger || {}
    change(all)
    await local.set({ ledger: all })
  }).catch(() => {})
  return ledgerWork
}

function remember(job) {
  return ledger(all => {
    if (isLive(job)) {
      all[job.id] = { port: job.port, app_id: job.app_id, profile_key: job.profile_key, platform: job.platform, state: job.state, pressed: job.pressedByPerson === true, at: Date.now() }
    } else {
      delete all[job.id]
    }
  })
}

// Called when the worker starts and on every tick: ends, at the app, each job this browser held when it was
// closed. Nothing is picked up again. A job that had been pressed, or could have been, is "not sure".
export async function orphans() {
  const held = (await local.get("ledger")).ledger || {}
  const install = await app.install()
  for (const [id, entry] of Object.entries(held)) {
    if (await getJob(id)) continue
    const p = platformName(entry.platform)
    const pressed = entry.state === "posting" || entry.pressed
    const body = pressed
      ? { install, state: "unknown", error_code: "unknown_outcome", error_message: `The browser was closed after Post was pressed on ${p}, before any confirmation. Check your profile before you post again.` }
      : { install, state: "failed", error_code: "tab_closed", error_message: `The browser was closed, or the extension reloaded, ${entry.state === "ready" ? "before Post was pressed" : "before the post was ready"}. Nothing was posted.` }
    try {
      await withApp({ ...entry }, found => app.api(found, `/v1/jobs/${id}/event`, { method: "POST", body }))
    } catch {
      // ReelForge is not open. The news keeps for a day, then the app's own rules have long since applied.
      if (Date.now() - entry.at < 24 * 60 * MINUTE) continue
    }
    await ledger(all => delete all[id])
  }
}

const short = (text, limit = 300) => (text == null ? undefined : String(text).slice(0, limit))

// ---- tabs and the alarm ------------------------------------------------------------------------------------

// Tab events are listened to only while a job is live. With no job, Chrome has no reason to wake this worker
// when the person opens or closes a page. The worker adds the listeners every time it starts (an event that
// woke it has to find them there), and settle() takes them away again when no job is live.
export function listenToTabs() {
  if (!chrome.tabs.onUpdated.hasListener(onTabUpdated)) chrome.tabs.onUpdated.addListener(onTabUpdated)
  if (!chrome.tabs.onRemoved.hasListener(onTabRemoved)) chrome.tabs.onRemoved.addListener(onTabRemoved)
}

// Called when the worker starts and whenever a job starts or ends. With a live job it listens to tabs and ticks
// every 30 seconds (the recovery path). With none it stops listening and ticks every 5 minutes (the heartbeat,
// so ReelForge can say this browser is connected), and not at all before this browser has ever been paired.
// Calls run one after another, so an older look at the jobs can never undo what a newer one decided.
let settling = Promise.resolve()

export function settle() {
  settling = settling.then(async () => {
    const busy = (await allJobs()).some(isLive)
    if (busy) {
      listenToTabs()
    } else {
      chrome.tabs.onUpdated.removeListener(onTabUpdated)
      chrome.tabs.onRemoved.removeListener(onTabRemoved)
    }
    const paired = Object.keys((await local.get("tokens")).tokens || {}).length > 0
    const period = busy ? 0.5 : paired ? 5 : 0
    const have = await chrome.alarms.get(ALARM)
    if (!period) {
      if (have) await chrome.alarms.clear(ALARM)
    } else if (!have || have.periodInMinutes !== period) {
      await chrome.alarms.create(ALARM, { periodInMinutes: period })
    }
  }).catch(() => {})
  return settling
}

function allowedPage(platform, url) {
  try {
    const page = new URL(url)
    return page.protocol === "https:" && PLATFORMS[platform].hosts.includes(page.hostname)
  } catch {
    return false
  }
}

async function onTabUpdated(tabId, info) {
  if (info.status !== "complete") return
  const job = await jobOfTab(tabId)
  if (job) await inject(job)
}

async function onTabRemoved(tabId) {
  const job = await jobOfTab(tabId)
  if (job) await tabGone(job)
}

// Puts the platform script in the job's tab. It runs again after every full page load there (YouTube's upload
// address redirects into Studio), and the script itself refuses to start twice in one document.
async function inject(job) {
  try {
    await chrome.scripting.executeScript({ target: { tabId: job.tabId }, files: SCRIPTS(job.platform) })
  } catch (error) {
    // A tab on its way out fails the same way as a tab on another site. A moment later the two differ: the
    // closed one is gone, and the other is still there with an address this extension is not allowed to see.
    await new Promise(resolve => setTimeout(resolve, 400))
    const tab = await chrome.tabs.get(job.tabId).catch(() => null)
    const p = platformName(job.platform)
    if (!tab) return tabGone(job)
    if (/error page/i.test(String(error?.message || error))) return leftPage(job, "platform_error", `${p}'s page did not load. Check your connection, then post again.`)
    // Still on the platform: the page was between two loads. The next load brings the script in.
    if (tab.url && allowedPage(job.platform, tab.url)) return
    // From an upload address, a page on another site is the platform's sign-in.
    return leftPage(job, "not_signed_in", `${p} sent this tab to another site, most likely to sign in. Sign in to ${p} in this browser, then post again.`)
  }
}

// Post was pressed, by ReelForge or by the person. The person's press can come before the page is called
// ready: the video only has to be on it (YouTube publishes while a video is still going up).
const wasPressed = job => job.state === "posting" || job.pressedByPerson === true

// The job's tab is showing something the script cannot run on.
function leftPage(job, code, message) {
  if (wasPressed(job)) return unsure(job)
  if (job.state === "ready") return finish(job.id, "failed", "tab_closed", "The upload page was left before Post was pressed. Nothing was posted.")
  return finish(job.id, "failed", code, message)
}

function tabGone(job) {
  if (wasPressed(job)) return unsure(job)
  const when = job.state === "ready" ? "before Post was pressed" : "before the post was ready"
  return finish(job.id, "failed", "tab_closed", `The tab was closed ${when}. Nothing was posted.`)
}

// Post was pressed and the page never said it worked. This is never tried again: only the person can tell.
function unsure(job) {
  return finish(job.id, "unknown", "unknown_outcome",
    `Post was pressed on ${platformName(job.platform)} and no confirmation was seen. Check your profile before you post again.`)
}

async function stopTab(job, message) {
  if (job.tabId == null) return
  try {
    await chrome.tabs.sendMessage(job.tabId, { t: "stop", message })
  } catch {
    // No script is listening there: the tab is closed or showing another page.
  }
}

// ---- reporting ---------------------------------------------------------------------------------------------

// Reaches the job's ReelForge window. It may have been closed and opened again on another port since the job
// started, so a failed call looks for it once more. Changes job.port in place; the caller saves.
async function withApp(job, call) {
  try {
    const res = await call({ port: job.port, app_id: job.app_id })
    // 401, 404 or 421 from that port: it may be another program there now, or a ReelForge that forgot us.
    if (![401, 404, 421].includes(res.status)) return res
  } catch (error) {
    if (!(error instanceof app.AppDown)) throw error
  }
  const again = await app.find(job.app_id, job.profile_key, true)
  if (!again || !again.paired) throw new app.AppDown("ReelForge is not answering")
  job.port = again.port
  return call(again)
}

// Sends what is waiting, oldest first. What cannot be sent stays in the outbox for the next tick.
async function flush(job) {
  const install = await app.install()
  while (job.outbox.length) {
    let res
    try {
      res = await withApp(job, found => app.api(found, `/v1/jobs/${job.id}/event`, { method: "POST", body: { install, ...job.outbox[0] } }))
    } catch {
      job.offline = true
      return
    }
    job.offline = false
    // ReelForge is there but could not take it just now (its database was busy, or something went wrong
    // inside): the report waits here for the next tick, like one that found nobody.
    if (res.status >= 500) return
    // A refusal is not sent again: the app sees this job differently (cancelled in the window, say), and the
    // next tick takes its word for it.
    job.outbox.shift()
  }
}

// One step forward for a job: kept here first, then sent to the app. A report that would move a job backwards
// or out of a finished state is dropped, as the app would drop it.
export function report(id, event) {
  return locked(id, async () => {
    const job = await getJob(id)
    if (!job || isFinal(job.state)) return { ok: false, stop: true }
    // A job is "claimed" only for the moment before its tab opens; the app hears of it as "opening" from then on.
    const state = event.state || (job.state === "claimed" ? "opening" : job.state)
    if (!isFinal(state) && rank(state) < rank(job.state)) return { ok: true }
    const wire = { state }
    for (const field of ["step", "progress", "detail", "error_code", "error_message", "post_id", "url", "strategy"]) {
      if (event[field] !== undefined && event[field] !== null) wire[field] = event[field]
    }
    const next = { ...job, ...wire, touched: Date.now(), outbox: [...job.outbox] }
    if (state !== job.state && event.progress === undefined) next.progress = null
    if (state === "ready" && job.state !== "ready") next.readyAt = Date.now()
    if (isFinal(state)) next.done = Date.now()
    // Once the app has granted the press the job is "posting" there, a state only the app sets: steps on the
    // way to the ending stay here, and only the ending is sent.
    if (!(state === "posting")) {
      // A newer report of the same step replaces the one still waiting (upload percentages).
      const last = next.outbox[next.outbox.length - 1]
      if (last && last.state === wire.state && last.step === wire.step && !isFinal(wire.state)) next.outbox.pop()
      next.outbox.push(wire)
    }
    await save(next)
    if (next.state !== job.state) await remember(next)
    await flush(next)
    await save(next)
    return { ok: true }
  }).then(async result => {
    if (isFinal(event.state || "")) await settle()
    return result
  })
}

async function finish(id, state, code, message, step) {
  const job = await getJob(id)
  if (!job || isFinal(job.state)) return
  await report(id, { state, error_code: code, error_message: message, step })
  await stopTab(job, message)
}

// ---- starting ----------------------------------------------------------------------------------------------

let starting = Promise.resolve()

// A batch handed over by the local post page or the popup. Handing the same batch over twice (a reloaded
// page, a second copy of it) opens nothing new: a job already held here is skipped, and the app grants a
// claim only once.
export function startBatch(port, batch) {
  const run = starting.then(() => doStartBatch(port, batch))
  starting = run.catch(() => {})
  return run
}

async function doStartBatch(port, batch) {
  if (!BATCH_ID.test(String(batch))) return "error:bad_batch"
  const found = await app.at(port)
  if (!found) return "error:app_unreachable"
  if (found.mismatch) return found.mismatch === "extension" ? "error:extension_outdated" : "error:protocol"
  if (found.demo) return "error:demo"
  if (!found.paired) return "unpaired"
  let res
  try {
    res = await app.api(found, `/v1/batch/${batch}`)
  } catch {
    return "error:app_unreachable"
  }
  if (!res.ok || !Array.isArray(res.data?.jobs)) return "error:" + (res.data?.error || "http_" + res.status)
  await beat(found)
  let front = !(await allJobs()).some(isLive)
  for (const job of res.data.jobs) {
    if (job.via !== "extension" || job.state !== "queued" || !JOB_ID.test(String(job.id))) continue
    if (await getJob(job.id)) continue
    if (await startJob(found, job.id, front)) front = false
  }
  return "started"
}

async function startJob(found, id, front) {
  let res
  try {
    res = await app.api(found, `/v1/jobs/${id}/claim`, { method: "POST", body: { install: await app.install() } })
  } catch {
    return false
  }
  // 409: another browser took it, or it is no longer waiting. Either way it is not ours to open.
  if (!res.ok || res.data?.id !== id) return false
  const claimed = res.data
  const now = Date.now()
  const job = {
    id, batch: claimed.batch, platform: claimed.platform, port: found.port, app_id: found.app_id, profile_key: found.profile.key,
    video: { name: String(claimed.video?.name || "video.mp4"), size: Number(claimed.video?.size), sha256: String(claimed.video?.sha256 || "") },
    text: claimed.text || {}, handle: String(claimed.handle || ""), press: claimed.press === true,
    state: "claimed", step: null, progress: null, detail: null, error_code: null, error_message: null, post_id: null, url: null,
    tabId: null, created: now, touched: now, outbox: [],
  }
  await save(job)
  await remember(job)
  listenToTabs()
  await settle()
  if (!PLATFORMS[job.platform] || !allowedPage(job.platform, claimed.upload_url) || !(job.video.size > 0)) {
    await finish(id, "failed", "extension_outdated", "ReelForge asked for something this extension does not know. Reload the extension from your ReelForge folder.", "open_tab")
    return false
  }
  // The first tab of a batch comes to the front; the others load behind it.
  const tab = await chrome.tabs.create({ url: claimed.upload_url, active: front }).catch(() => null)
  if (!tab) {
    await finish(id, "failed", "tab_closed", "The browser could not open a tab for this post. Nothing was posted.", "open_tab")
    return false
  }
  await patch(id, { tabId: tab.id })
  await report(id, { state: "opening", step: "open_tab" })
  // A page that finished loading before the tab id was saved has had no script yet.
  const now2 = await chrome.tabs.get(tab.id).catch(() => null)
  if (now2?.status === "complete") await inject({ ...job, tabId: tab.id })
  return true
}

// ---- what a job's tab asks for -----------------------------------------------------------------------------

async function pressSwitch(platform) {
  const { press = {} } = await local.get("press")
  return PRESS_PLATFORMS.includes(platform) && press[platform] === true
}

// The script in a job's tab asks who it is working for. A tab with no live job gets no answer and does nothing.
export async function tabHello(tabId) {
  const job = await patchLive(tabId, { touched: Date.now() })
  if (!job) return { ok: false }
  return {
    ok: true,
    version: app.version,
    selectors: await selectorsFor(job.platform),
    job: {
      id: job.id, platform: job.platform, name: platformName(job.platform), text: job.text, video: job.video, handle: job.handle,
      state: job.state, attached: job.attached === true, accountOk: job.accountOk === true,
      pressedByPerson: job.pressedByPerson === true, pressAsked: job.pressAsked === true,
      // Press for me needs all three: the person switched it on here, switched "stop before posting" off for
      // this post, and it is not X. The app still has the last word when the press is asked for.
      press: job.press && !job.pressAsked && await pressSwitch(job.platform),
    },
  }
}

async function patchLive(tabId, change) {
  const job = await jobOfTab(tabId)
  return job ? patch(job.id, change) : null
}

export async function tabAlive(tabId) {
  return { ok: Boolean(await patchLive(tabId, { touched: Date.now() })) }
}

export async function tabEvent(tabId, message) {
  const job = await jobOfTab(tabId)
  if (!job) return { ok: false, stop: true }
  const change = {}
  if (message.attached === true) change.attached = true
  if (message.pressedByPerson === true) change.pressedByPerson = true
  if (message.paused) change.paused = { code: "wrong_account", seen: short(message.paused.seen, 60), expected: job.handle, at: Date.now() }
  if (Object.keys(change).length) {
    const now = await patch(job.id, change)
    if (now && change.pressedByPerson) await remember(now)
  }
  if (message.account) await app.sawAccount(job.platform, message.account.signed_in === true, short(message.account.handle, 60))
  if (message.state !== undefined && !TAB_STATES.includes(message.state)) return { ok: false }
  if (message.state === undefined && message.step === undefined && message.detail === undefined) return { ok: true }
  const progress = Number(message.progress)
  return report(job.id, {
    state: message.state,
    step: short(message.step, 40), detail: short(message.detail),
    progress: message.progress == null || !(progress >= 0) ? undefined : Math.min(1, progress),
    error_code: short(message.error_code, 40), error_message: short(message.error_message),
    post_id: short(message.post_id, 80), url: short(message.url, 300), strategy: short(message.strategy, 200),
  })
}

// The exactly-once press. The app's ledger decides; this side makes sure it is asked at most once per job,
// even if the worker is restarted between asking and hearing back.
export async function tabPress(tabId) {
  const job = await jobOfTab(tabId)
  const no = (error, message) => ({ ok: false, error, message })
  if (!job || job.state !== "ready") return no("not_ready", "This post is not ready to be pressed.")
  if (!job.press || !(await pressSwitch(job.platform))) return no("press_not_allowed", "Press Post for me is off for this post.")
  const first = await locked(job.id, async () => {
    const fresh = await getJob(job.id)
    if (!fresh || fresh.pressAsked) return false
    await save({ ...fresh, pressAsked: true })
    return true
  })
  if (!first) return no("already_pressed", "Post was already pressed for this job.")
  let res
  try {
    res = await withApp(job, async found => app.api(found, `/v1/jobs/${job.id}/press`, { method: "POST", body: { install: await app.install() } }))
  } catch {
    return no("app_unreachable", "ReelForge is not running, so it could not allow the press.")
  }
  if (!res.ok || res.data?.ok !== true) return no(String(res.data?.error || "press_not_allowed"), String(res.data?.message || "ReelForge did not allow the press."))
  const now = await patch(job.id, { state: "posting", step: "press", progress: null, pressedAt: Date.now(), touched: Date.now(), port: job.port })
  if (now) await remember(now)
  return { ok: true }
}

function base64(bytes) {
  let text = ""
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000))
  return btoa(text)
}

// The video goes to the tab one piece per request, over a Port. The tab asks for a piece by its offset, so a
// worker that was restarted in the middle simply answers the next request.
export function onConnect(port) {
  if (port.name !== "rf-file" || port.sender?.id !== chrome.runtime.id || !port.sender.tab) return
  port.onMessage.addListener(async message => {
    let answer
    try {
      answer = await piece(port.sender.tab.id, message)
    } catch (error) {
      answer = { t: "error", code: "app_unreachable", message: String(error?.message || error) }
    }
    try {
      port.postMessage(answer)
    } catch {
      // The tab went away while the piece was on its way.
    }
  })
}

async function piece(tabId, message) {
  const job = await jobOfTab(tabId)
  const error = (code, text) => ({ t: "error", code, message: text })
  if (!job || job.attached || rank(job.state) > rank("uploading")) return error("cancelled", "This post is no longer waiting for its video.")
  const offset = Number(message?.offset)
  const length = Math.min(Number(message?.length), PIECE, job.video.size - offset)
  if (!Number.isInteger(offset) || offset < 0 || !(length > 0)) return error("cancelled", "That piece of the video does not exist.")
  const port = job.port
  let got
  try {
    got = await withApp(job, found => app.filePiece(found, job.id, offset, length))
  } catch {
    return error("app_unreachable", "ReelForge closed before the video was handed over. Nothing was posted.")
  }
  if (job.port !== port) await patch(job.id, { port: job.port })
  if (!got.ok) {
    if (got.data?.error === "changed") return error("changed", "The video file was replaced after you confirmed this post. Nothing was posted.")
    return error("cancelled", String(got.data?.message || "ReelForge no longer has this post."))
  }
  if ((got.etag && got.etag !== job.video.sha256) || got.bytes.length !== length) {
    return error("changed", "The video file changed while it was being handed over. Nothing was posted.")
  }
  await patch(job.id, { touched: Date.now() })
  return { t: "data", offset, b64: base64(got.bytes) }
}

// ---- the 30 second tick ------------------------------------------------------------------------------------

export async function beat(found) {
  if (!found?.paired) return
  try {
    const answer = await app.seen(found)
    if (answer) await refresh(found, answer.selectors_version)
  } catch {
    // Nothing to do when the app is not there: the popup says so.
  }
}

// The recovery path. Everything that normally happens on an event (a tab closing, a page reporting) is checked
// again here, for the case where the worker or the browser was restarted and the event was lost.
export async function tick() {
  await orphans()
  const now = Date.now()
  for (const job of await allJobs()) {
    if (isLive(job)) await watch(job, now)
    else if (job.outbox.length) await resend(job.id)
    else if (now - (job.done || job.touched) > KEEP_DONE) await session.remove(key(job.id))
  }
  for (const found of await app.discover(true)) await beat(found)
  await settle()
}

// The popup is open: bring this browser's jobs and the app's ledger into line now instead of at the next tick.
let nudged = 0

export async function nudge() {
  if (Date.now() - nudged < 5000) return
  nudged = Date.now()
  for (const job of await allJobs()) {
    if (isLive(job)) await sync(job)
    else if (job.outbox.length) await resend(job.id)
  }
}

function resend(id) {
  return locked(id, async () => {
    const job = await getJob(id)
    if (!job) return
    await flush(job)
    await save(job)
  })
}

async function watch(job, now) {
  const p = platformName(job.platform)
  if (job.tabId == null) {
    if (now - job.created > 30000) await finish(job.id, "failed", "timeout", "The upload page was never opened. Nothing was posted.", "open_tab")
    return
  }
  const tab = await chrome.tabs.get(job.tabId).catch(() => null)
  if (!tab) return tabGone(job)
  if (job.paused) {
    if (now - job.paused.at > PAUSE_LIMIT) await finish(job.id, "failed", "wrong_account", `Nobody said whether to post to ${p} as ${job.paused.seen}. Nothing was posted.`, "signed_in")
  } else if (job.state === "posting") {
    if (now - (job.pressedAt || job.touched) > CONFIRM_LIMIT) await unsure(job)
  } else if (job.state === "ready") {
    if (now - (job.readyAt || job.touched) > READY_LIMIT) {
      await finish(job.id, "unknown", "unknown_outcome", `The ${p} tab sat ready for 45 minutes with no word. Check your profile, in case you pressed Post.`)
    }
  } else if (now - job.touched > QUIET_LIMIT) {
    await finish(job.id, "failed", "timeout", `The ${p} page stopped answering. The tab is still open: you can finish by hand there.`, job.step || "open_tab")
    return
  } else if (now - job.touched > 45000) {
    // Quiet for a while: the page may have loaded while the worker was not running.
    const alive = await chrome.tabs.sendMessage(job.tabId, { t: "ping" }).catch(() => null)
    if (!alive && tab.status === "complete") await inject(job)
  }
  await sync(job)
}

// Sends what is waiting and takes the app's word when it has already ended the job (cancelled in the window,
// expired, answered by the person there).
async function sync(job) {
  await locked(job.id, async () => {
    const fresh = await getJob(job.id)
    if (!fresh || isFinal(fresh.state)) return
    await flush(fresh)
    let theirs = null
    try {
      const res = await withApp(fresh, found => app.api(found, `/v1/batch/${fresh.batch}`))
      theirs = res.ok ? res.data?.jobs?.find(other => other.id === fresh.id) : null
    } catch {
      fresh.offline = true
    }
    if (theirs && isFinal(theirs.state)) {
      Object.assign(fresh, {
        state: theirs.state, detail: theirs.detail ?? fresh.detail, error_code: theirs.error_code ?? null,
        error_message: theirs.error_message ?? null, post_id: theirs.post_id ?? fresh.post_id, url: theirs.url ?? fresh.url,
        done: Date.now(), outbox: [],
      })
      await save(fresh)
      await remember(fresh)
      await stopTab(fresh, "This post was ended in ReelForge.")
      return
    }
    await save(fresh)
  })
}

// ---- what the popup asks for -------------------------------------------------------------------------------

// The person's answer to "this browser is signed in as someone else".
export async function override(id, go) {
  const job = await getJob(id)
  if (!job || !job.paused || isFinal(job.state)) return { ok: false }
  const p = platformName(job.platform)
  if (!go) {
    await finish(id, "failed", "wrong_account", `This browser is signed in to ${p} as ${job.paused.seen}, and ReelForge has ${job.paused.expected}. Nothing was posted.`, "signed_in")
    return { ok: true }
  }
  const seen = job.paused.seen
  await patch(id, { paused: null, accountOk: true, touched: Date.now() })
  await report(id, { step: "signed_in", detail: `You chose to post as ${seen}.` })
  const told = await chrome.tabs.sendMessage(job.tabId, { t: "override" }).catch(() => null)
  if (!told) await inject({ ...job, paused: null })
  return { ok: true }
}

export async function focus(id) {
  const job = await getJob(id)
  if (job?.tabId == null) return { ok: false }
  try {
    const tab = await chrome.tabs.update(job.tabId, { active: true })
    await chrome.windows.update(tab.windowId, { focused: true })
    return { ok: true }
  } catch {
    return { ok: false }
  }
}

// Stop a job from the popup. Not offered once Post has been pressed for the person: that one has to be looked at.
export async function cancel(id) {
  const job = await getJob(id)
  if (!job || isFinal(job.state) || job.state === "posting") return { ok: false }
  await report(id, { state: "cancelled", detail: "stopped in the extension" })
  await stopTab(job, "You stopped this post. Nothing was posted by ReelForge.")
  return { ok: true }
}

export async function dismiss(id) {
  const job = await getJob(id)
  if (job && isFinal(job.state)) await session.remove(key(id))
  return { ok: true }
}

// The popup's Post, first half: the app writes the batch and answers with the exact text for each platform,
// so the person can read it before anything opens.
export async function prepare(video, platforms, press) {
  const found = await app.current()
  if (!found?.paired) return { ok: false, message: "ReelForge is not connected." }
  try {
    // ReelForge reads the whole video to fingerprint it before it answers, so this call gets more time.
    const res = await app.api(found, "/v1/batches", {
      method: "POST", timeout: 30000,
      body: { video: String(video), platforms: platforms.map(String), press: press === true, install: await app.install() },
    })
    if (!res.ok) return { ok: false, message: String(res.data?.message || "ReelForge could not prepare this post.") }
    return { ok: true, port: found.port, batch: res.data.batch, jobs: res.data.jobs || [], blocked: res.data.blocked || [] }
  } catch {
    return { ok: false, message: "ReelForge stopped answering. Open it and try again." }
  }
}

// A batch as the app has it now, for a popup that was closed on its confirm screen and opened again.
export async function peek(port, batch) {
  const found = await app.at(port)
  if (!found?.paired || !BATCH_ID.test(String(batch))) return { jobs: [] }
  try {
    const res = await app.api(found, `/v1/batch/${batch}`)
    return { jobs: res.ok && Array.isArray(res.data?.jobs) ? res.data.jobs : [] }
  } catch {
    return { jobs: [] }
  }
}

// The popup's Cancel on the confirm step: the jobs were written but nothing was opened. The app lets a waiting
// job be cancelled only by the browser that holds it, so a refusal is followed by claim, then cancel.
export async function discard(port, batch) {
  const found = await app.at(port)
  if (!found?.paired || !BATCH_ID.test(String(batch))) return { ok: false }
  const install = await app.install()
  try {
    const res = await app.api(found, `/v1/batch/${batch}`)
    for (const job of res.data?.jobs || []) {
      if (job.state !== "queued" || !JOB_ID.test(String(job.id)) || await getJob(job.id)) continue
      const body = { install, state: "cancelled", detail: "cancelled before it started" }
      const first = await app.api(found, `/v1/jobs/${job.id}/event`, { method: "POST", body })
      if (first.ok) continue
      const claim = await app.api(found, `/v1/jobs/${job.id}/claim`, { method: "POST", body: { install } })
      if (claim.ok) await app.api(found, `/v1/jobs/${job.id}/event`, { method: "POST", body })
    }
  } catch {
    // The app is gone. Jobs nobody claims expire there by themselves after ten minutes.
  }
  return { ok: true }
}
