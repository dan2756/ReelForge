// What every test needs: a browser with the unpacked extension loaded, and a few ways to wait.
import { createRequire } from "node:module"
import { mkdtempSync, rmSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import { EXTENSION_ID } from "./fake_app.mjs"

// Playwright is installed for the whole machine, where only require() finds it.
const require = createRequire(import.meta.url)
const { chromium } = require("playwright")

export const EXTENSION_DIR = dirname(dirname(fileURLToPath(import.meta.url)))

export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

// Waits until `probe` gives something truthy. `what` (text, or a function that gives it at the end) says what
// was being waited for when it does not.
export async function until(what, probe, timeout = 15000) {
  const end = Date.now() + timeout
  let last
  for (;;) {
    try {
      last = await probe()
      if (last) return last
    } catch (error) {
      last = error
    }
    if (Date.now() > end) throw new Error(`timed out waiting for ${typeof what === "function" ? what() : what}` + (last instanceof Error ? `: ${last.message}` : ""))
    await sleep(50)
  }
}

// JSON with every object's keys in order, so two objects with the same content compare equal.
const canonical = value => JSON.stringify(value, (key, inner) => (inner && typeof inner === "object" && !Array.isArray(inner)
  ? Object.fromEntries(Object.keys(inner).sort().map(name => [name, inner[name]])) : inner))

export function equal(actual, expected, what) {
  const a = canonical(actual)
  const e = canonical(expected)
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`)
}

export function ok(value, what) {
  if (!value) throw new Error(what)
}

// A fresh browser profile with the extension loaded from this folder, as "Load unpacked" would load it.
export async function launch(site) {
  const profile = mkdtempSync(join(tmpdir(), "reelforge-ext-"))
  const context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    viewport: { width: 1100, height: 800 },
    args: [`--disable-extensions-except=${EXTENSION_DIR}`, `--load-extension=${EXTENSION_DIR}`, "--no-proxy-server", ...(site ? site.browserFlags : [])],
  })
  let [worker] = context.serviceWorkers()
  worker ||= await context.waitForEvent("serviceworker", { timeout: 15000 })
  const id = new URL(worker.url()).host
  const browser = {
    context, id,

    async close() {
      await context.close()
      rmSync(profile, { recursive: true, force: true })
    },

    // The popup and the options page, opened as ordinary tabs so the tests can look inside them.
    async popup() {
      const page = await context.newPage()
      await page.goto(`chrome-extension://${EXTENSION_ID}/popup/popup.html`)
      return page
    },
    async options() {
      const page = await context.newPage()
      await page.goto(`chrome-extension://${EXTENSION_ID}/options/options.html`)
      return page
    },

    // Opens ReelForge's pair page, as the app would, and waits for the extension to pair.
    async pair(app) {
      const page = await context.newPage()
      await page.goto(app.pairUrl())
      await page.waitForFunction(() => document.documentElement.dataset.rfPair, null, { timeout: 10000 })
      const state = await page.evaluate(() => document.documentElement.dataset.rfPair)
      await page.close()
      return state
    },

    // Opens ReelForge's post page for a batch and waits for the extension's answer to it.
    async post(app, batch) {
      const page = await context.newPage()
      await page.goto(app.postUrl(batch))
      await page.waitForFunction(() => document.documentElement.dataset.rfBatchState, null, { timeout: 10000 })
      return page
    },

    // The tab the extension opened on one of the platforms.
    async tab(host, timeout = 10000) {
      return until(`a tab on ${host}`, () => context.pages().find(page => new URL(page.url()).host === host), timeout)
    },
    tabs(host) {
      return context.pages().filter(page => new URL(page.url()).host === host)
    },

    // The extension's own records, read from one of its pages.
    async storage(area = "session") {
      const page = context.pages().find(p => p.url().startsWith(`chrome-extension://${EXTENSION_ID}/`)) || await browser.popup()
      return page.evaluate(name => chrome.storage[name].get(null), area)
    },
    async jobRecord(jobId) {
      return (await browser.storage())[`job:${jobId}`] || null
    },

    // Stops the service worker the way Chrome does when it has been idle. The next event starts a new one.
    async stopWorker() {
      const page = context.pages().find(p => p.url().startsWith(`chrome-extension://${EXTENSION_ID}/`)) || await browser.popup()
      const session = await context.newCDPSession(page)
      await session.send("ServiceWorker.enable")
      await session.send("ServiceWorker.stopAllWorkers")
      await session.detach()
    },
  }
  return browser
}

// The packed selectors as a newer copy from the app, with every wait cut short. For tests of things that are
// missing from a page: the ways of looking are the same, the patience is not.
export function quickSelectors(longest = 2500) {
  const data = JSON.parse(readFileSync(join(EXTENSION_DIR, "selectors.json"), "utf8"))
  data.version += 1
  for (const platform of Object.values(data.platforms)) {
    platform.signed_in.timeout_ms = longest
    for (const hook of Object.values(platform.hooks)) hook.timeout_ms = Math.min(hook.timeout_ms || 10000, longest)
  }
  return data
}

// What the extension's note in the corner of an upload page says.
export function note(page) {
  return page.evaluate(() => {
    const host = document.querySelector("[data-reelforge-note]")
    const card = host?.shadowRoot?.querySelector(".card")
    return card ? { tone: card.dataset.tone, line: card.querySelector(".line").textContent, more: card.querySelector(".more").textContent } : null
  })
}
