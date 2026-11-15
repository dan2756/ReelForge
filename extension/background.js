// The service worker. Every listener is added here, in the first pass, because Chrome only replays the event
// that woke the worker to listeners that exist by then.
import * as app from "./worker/app.js"
import * as jobs from "./worker/jobs.js"
import * as panel from "./worker/panel.js"
import { PORTS } from "./shared/consts.js"

// The pairing token and the settings are for the worker, the popup and the options page. The scripts that run
// in web pages have no business reading them, so the browser is told not to let them.
chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" })

chrome.runtime.onInstalled.addListener(() => jobs.settle())
chrome.runtime.onStartup.addListener(() => jobs.settle())
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === jobs.ALARM) jobs.tick()
})
chrome.runtime.onConnect.addListener(jobs.onConnect)
jobs.listenToTabs()
jobs.settle()
jobs.orphans()

// Who is asking. A message is only ever acted on for the kind of page it is allowed to come from.
function sender(from) {
  if (from.id !== chrome.runtime.id) return null
  const url = from.url || ""
  if (url.startsWith(chrome.runtime.getURL(""))) return "ui"
  if (!from.tab || from.frameId !== 0) return null
  let page
  try {
    page = new URL(url)
  } catch {
    return null
  }
  if (page.protocol === "http:" && page.hostname === "127.0.0.1" && PORTS.includes(Number(page.port))) return "local"
  if (page.origin === "https://reelforge.tools") return "site"
  return "tab"
}

// A local page speaks only for the ReelForge it was served by: the port is read from its address, not from
// what it says.
const portOf = from => Number(new URL(from.url).port)

const routes = {
  // ReelForge's own pages on this computer.
  local: {
    pair: async (message, from) => {
      const state = await app.pair(portOf(from), String(message.secret || ""))
      // A first pairing starts the heartbeat, and the first beat goes out now.
      if (state === "ok") await jobs.beat(await app.at(portOf(from)))
      await jobs.settle()
      return { state }
    },
    batch: async (message, from) => ({ state: await jobs.startBatch(portOf(from), message.batch) }),
  },
  // The website learns three things and nothing else: is the app there, is this browser paired, its version.
  site: {
    "site.status": async () => {
      const found = await app.current()
      return { app_reachable: Boolean(found), paired: found?.paired === true, app_version: found ? found.version : null }
    },
  },
  // The script in a tab this worker opened for a job.
  tab: {
    "job.hello": (message, from) => jobs.tabHello(from.tab.id),
    "job.alive": (message, from) => jobs.tabAlive(from.tab.id),
    "job.event": (message, from) => jobs.tabEvent(from.tab.id, message),
    "job.press": (message, from) => jobs.tabPress(from.tab.id),
  },
  // The popup and the options page.
  ui: {
    "ui.state": message => panel.state(message.fresh),
    "ui.select": async message => { await app.select(Number(message.port)); return panel.state() },
    "ui.prepare": message => jobs.prepare(message.video, Array.isArray(message.platforms) ? message.platforms : [], message.press),
    "ui.batch": message => jobs.peek(Number(message.port), message.batch),
    "ui.start": async message => ({ state: await jobs.startBatch(Number(message.port), message.batch) }),
    "ui.discard": message => jobs.discard(Number(message.port), message.batch),
    "ui.override": message => jobs.override(String(message.job), message.go === true),
    "ui.focus": message => jobs.focus(String(message.job)),
    "ui.cancel": message => jobs.cancel(String(message.job)),
    "ui.resolve": message => jobs.resolve(String(message.job), message.posted === true),
    "ui.dismiss": message => jobs.dismiss(String(message.job)),
    "ui.press": message => panel.setPress(String(message.platform), message.on),
    "ui.disconnect": async () => { const done = await panel.disconnect(); await jobs.settle(); return done },
  },
}

chrome.runtime.onMessage.addListener((message, from, reply) => {
  const handler = routes[sender(from)]?.[message?.t]
  if (!handler) return false
  Promise.resolve()
    .then(() => handler(message, from))
    .then(reply, error => reply({ ok: false, error: "internal", message: String(error?.message || error) }))
  return true
})
