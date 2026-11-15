// The extension's tests:  node extension/tests/run.mjs [words from a test's name]
//
// They load the unpacked extension into headless Chromium (the copy Playwright ships; branded Chrome no longer
// loads extensions from the command line), with a stand-in for the ReelForge app on 127.0.0.1 and copies of the
// four upload pages served on the platforms' own addresses. Nothing leaves this computer and no account is
// used. They prove the extension's own machinery. They say nothing about today's live pages.
import crypto from "node:crypto"
import { FakeApp, PORTS } from "./fake_app.mjs"
import { FixtureSite } from "./site.mjs"
import { launch, sleep } from "./harness.mjs"

const suites = ["manifest", "bridges", "posting", "press", "failures", "recovery", "popup"]
const only = process.argv.slice(2).map(word => word.toLowerCase())

// ReelForge's ports must be free: a real ReelForge (or another run of these tests) would be found by the
// extension under test and change what it does.
async function portsFree() {
  for (const port of PORTS) {
    try {
      await fetch(`http://127.0.0.1:${port}/v1/hello`, { signal: AbortSignal.timeout(500) })
      return port
    } catch {
      // Nothing there: good.
    }
  }
  return null
}

const taken = await portsFree()
if (taken) {
  console.error(`Something is listening on 127.0.0.1:${taken}. Close ReelForge (and any other run of these tests) first.`)
  process.exit(2)
}

const site = await new FixtureSite().start()
const failures = []
let ran = 0
const started = Date.now()

for (const suite of suites) {
  const { tests } = await import(`./${suite}.test.mjs`)
  for (const [name, body] of Object.entries(tests)) {
    const title = `${suite}: ${name}`
    if (only.length && !only.every(word => title.toLowerCase().includes(word))) continue
    ran++
    site.reset()
    const app = await new FakeApp().start()
    const browser = await launch(site)
    const began = Date.now()
    try {
      // One slow step should fail its own test, not hang the run.
      await Promise.race([
        body({ app, site, browser, video: bytes => app.addVideo(bytes ? { bytes: crypto.randomBytes(bytes) } : {}) }),
        sleep(90000).then(() => { throw new Error("took longer than 90 seconds") }),
      ])
      console.log(`  ok    ${title}  (${((Date.now() - began) / 1000).toFixed(1)} s)`)
    } catch (error) {
      failures.push(title)
      console.log(`  FAIL  ${title}\n        ${String(error?.stack || error).split("\n").join("\n        ")}`)
      // What was on screen and in the ledger when it failed: usually enough to see why.
      for (const job of app.jobs.values()) console.log(`        job ${job.platform}: ${job.state} ${job.step || ""} ${job.error_code || ""} ${job.detail || ""}`)
      for (const page of browser.context.pages()) {
        const seen = await page.evaluate(() => document.body?.innerText.replace(/\s+/g, " ").slice(0, 300)).catch(() => "")
        console.log(`        page ${page.url().slice(0, 80)}: ${seen}`)
      }
    } finally {
      await browser.close().catch(() => {})
      await app.stop().catch(() => {})
    }
  }
}

await site.stop()
console.log(`\n${ran - failures.length} of ${ran} passed in ${Math.round((Date.now() - started) / 1000)} s`)
if (failures.length) console.log("failed:\n" + failures.map(title => "  " + title).join("\n"))
process.exit(failures.length || !ran ? 1 : 0)
