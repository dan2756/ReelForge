// The two static content scripts: ReelForge's own local pages, and the reelforge.tools website.
import crypto from "node:crypto"
import { equal, ok, until, sleep } from "./harness.mjs"

const dataset = page => page.evaluate(() => ({ ...document.documentElement.dataset }))

export const tests = {
  async "the pair page pairs the browser, and the app keeps only the token's hash"({ app, browser }) {
    const page = await browser.context.newPage()
    await page.goto(app.pairUrl())
    await page.waitForFunction(() => document.documentElement.dataset.rfPair)
    const data = await dataset(page)
    equal(data.rfPair, "ok", "pair state on the page")
    const version = JSON.parse((await import("node:fs")).readFileSync(new URL("../manifest.json", import.meta.url), "utf8")).version
    equal(data.rfExtension, version, "extension version on the page")
    // The page hears both events, with their details, across the gap between the extension and the page.
    equal(await page.evaluate(() => window.heard), [
      { name: "reelforge:extension", detail: { version, protocol: 1 } },
      { name: "reelforge:pair", detail: { state: "ok" } },
    ], "events the page heard")
    equal(app.paired.length, 1, "paired browsers")
    const claim = app.requests("POST", /^\/v1\/pair\/claim$/)[0]
    equal([claim.origin, claim.status, claim.body.protocol, claim.body.extension_version], [`chrome-extension://${browser.id}`, 200, 1, version], "the claim")
    ok(claim.body.name.length > 3 && claim.body.install.length >= 32, "the claim names the browser and this install")
    // The heartbeat follows at once, so ReelForge can say the browser is there.
    await until("a heartbeat", () => app.heartbeats.length > 0, 2000)
    equal([app.heartbeats[0].install, app.heartbeats[0].protocol], [claim.body.install, 1], "the heartbeat")
    const { tokens } = await browser.storage("local")
    const token = tokens[app.appId]
    ok(typeof token === "string" && token.length >= 43, "a token is kept for this app's id")
    equal(crypto.createHash("sha256").update(token).digest("hex"), app.paired[0].token_sha256, "the app's stored hash is the token's")
  },

  async "a pair page that was already used does not pair again"({ app, browser }) {
    const url = app.pairUrl()
    const first = await browser.context.newPage()
    await first.goto(url)
    await first.waitForFunction(() => document.documentElement.dataset.rfPair === "ok")
    const again = await browser.context.newPage()
    await again.goto(url)
    await again.waitForFunction(() => document.documentElement.dataset.rfPair)
    equal((await dataset(again)).rfPair, "error:bad_secret", "second use of the same secret")
    equal(app.paired.length, 1, "paired browsers")
  },

  async "a pair page from a newer ReelForge says the extension is the one to update"({ app, browser }) {
    app.pageProtocol = 2
    equal(await browser.pair(app), "error:extension_outdated", "pair state")
    equal(app.requests("POST", /pair\/claim/).length, 0, "claims sent")
  },

  async "a post page in a browser that is not paired says so and starts nothing"({ app, browser, video }) {
    const made = app.createBatch(video(), ["tiktok"])
    const page = await browser.post(app, made.batch)
    equal((await dataset(page)).rfBatchState, "unpaired", "batch state on the page")
    equal(await page.evaluate(() => window.heard.at(-1)), { name: "reelforge:batch", detail: { state: "unpaired" } }, "the batch event")
    equal(made.jobs[0].state, "queued", "the job")
    equal(browser.tabs("www.tiktok.com").length, 0, "TikTok tabs")
  },

  async "another program's page under /pair on 127.0.0.1 is left alone"({ app, browser }) {
    const page = await browser.context.newPage()
    await page.goto(`http://127.0.0.1:${app.port}/pairing-notes`)
    await sleep(500)
    equal(await dataset(page), {}, "attributes the extension set")
    equal(app.requests("POST", /pair\/claim/).length, 0, "claims sent")
  },

  async "the website gets hello and status, and nothing else"({ app, browser, video }) {
    video()
    equal(await browser.pair(app), "ok", "pairing")
    const version = JSON.parse((await import("node:fs")).readFileSync(new URL("../manifest.json", import.meta.url), "utf8")).version
    const page = await browser.context.newPage()
    await page.goto("https://reelforge.tools/")
    const answers = () => page.evaluate(() => window.answers)

    await page.evaluate(() => window.say({ source: "reelforge-site", type: "hello" }))
    await until("the hello answer", async () => (await answers()).length === 1)
    equal((await answers())[0], { source: "reelforge-extension", type: "hello", version, protocol: 1 }, "hello answer")

    await page.evaluate(() => window.say({ source: "reelforge-site", type: "status" }))
    await until("the status answer", async () => (await answers()).length === 2)
    equal((await answers())[1], { source: "reelforge-extension", type: "status", version, protocol: 1, app_reachable: true, paired: true, app_version: app.version }, "status answer")

    // Everything else is met with silence: no way to start a post, read the videos or learn the channel's name.
    const before = app.log.length
    for (const type of ["state", "videos", "post", "batch", "pair", "ui.state", "ui.prepare", "job.hello", "site.status", undefined]) {
      await page.evaluate(type => window.say({ source: "reelforge-site", type, t: type, video: "x", platforms: ["tiktok"], port: 47821, batch: "pb_0000000000000000" }), type)
    }
    await page.evaluate(() => window.say({ source: "someone-else", type: "hello" }))
    await page.evaluate(() => window.say("hello"))
    await sleep(600)
    equal((await answers()).length, 2, "answers after the other messages")
    equal(app.log.slice(before).filter(entry => entry.path !== "/v1/hello").map(entry => entry.path), [], "requests the app got because of them")
    const said = JSON.stringify(await answers())
    for (const secret of [app.profile.name, app.videos[0].name, app.videos[0].title, app.appId]) ok(!said.includes(secret), `the answers do not hold "${secret}"`)
  },

  async "the website is told when the app is not there"({ app, browser }) {
    await app.stop()
    const page = await browser.context.newPage()
    await page.goto("https://reelforge.tools/")
    await page.evaluate(() => window.say({ source: "reelforge-site", type: "status" }))
    await until("the status answer", () => page.evaluate(() => window.answers.length === 1))
    const answer = await page.evaluate(() => window.answers[0])
    equal([answer.app_reachable, answer.paired, answer.app_version], [false, false, null], "status with no app")
  },
}
