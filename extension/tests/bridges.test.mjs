// The two static content scripts: ReelForge's own local pages, and the reelforge.tools website. And who the
// extension takes for ReelForge.
import crypto from "node:crypto"
import http from "node:http"
import { equal, ok, until, sleep } from "./harness.mjs"

// Another program on one of ReelForge's ports. It answers hello as ReelForge would, with any id and name it
// likes, says "paired" to everything, and offers a newer list of hooks. It has no token and no files: all it
// can do is listen. Everything it is sent is kept.
async function impostor(port, like) {
  const got = []
  const server = http.createServer((request, response) => {
    const path = request.url.split("?")[0]
    got.push({ method: request.method, path, token: request.headers["x-reelforge-token"] || null })
    const send = data => {
      const body = JSON.stringify(data)
      response.writeHead(200, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) })
      response.end(body)
    }
    request.resume()
    if (path === "/v1/hello") return send({ app: "reelforge", protocol: 1, version: like.version, app_id: like.appId, profile: like.profile, demo: false, paired: true, proof: "0".repeat(64) })
    if (path === "/v1/seen") return send({ ok: true, selectors_version: 9999 })
    if (path === "/v1/selectors") return send({ version: 9999, platforms: {} })
    if (path === "/v1/state") return send({ profile: like.profile, platforms: [], videos: [], active: [] })
    send({ ok: true })
  })
  await new Promise((resolve, reject) => server.once("error", reject).listen(port, "127.0.0.1", resolve))
  return { got, stop: () => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }) }
}

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

  async "another program on a lower port that says it is this ReelForge gets no token and is believed in nothing"({ app, browser, video }) {
    const film = video()
    // The real ReelForge moves up a port, and the other program sits on the first one, where it is asked first.
    await app.stop()
    const other = await impostor(47821, app)
    await app.start(47822)
    try {
      equal(await browser.pair(app), "ok", "pairing with the real one")
      const popup = await browser.popup()
      await until("the videos", () => popup.locator(`[data-video="${film.id}"]`).count())
      equal(await popup.locator("#status").textContent(), "Rendered Wrong", "the popup is connected, to the real one")
      // A whole post goes through the real one, heartbeats and all.
      const made = app.createBatch(film, ["tiktok"])
      await browser.post(app, made.batch)
      await until(() => `ready (it is ${made.jobs[0].state})`, () => made.jobs[0].state === "ready", 30000)
      await popup.getByRole("button", { name: "Settings" }).count()
      await sleep(3500)  // the popup has asked again by now
      ok(other.got.length > 0 && other.got.every(entry => entry.path === "/v1/hello"), `the other program was only ever said hello to (${JSON.stringify([...new Set(other.got.map(entry => entry.path))])})`)
      equal(other.got.filter(entry => entry.token).length, 0, "requests to it that carried the token")
      equal((await browser.storage("local")).selectors, undefined, "its list of hooks was not taken")
      ok(app.heartbeats.length > 0, "the real one got the heartbeats")
    } finally {
      await other.stop()
    }
  },

  async "another program on ReelForge's port while ReelForge is closed: not connected, and still no token"({ app, browser, video }) {
    video()
    equal(await browser.pair(app), "ok", "pairing")
    const port = app.port
    await app.stop()
    const other = await impostor(port, app)
    try {
      const popup = await browser.popup()
      await until("the screen", () => popup.locator("[data-screen]").count())
      equal(await popup.locator("[data-screen]").getAttribute("data-screen"), "not_connected", "what the popup shows")
      // The website's question gets the same answer.
      const page = await browser.context.newPage()
      await page.goto("https://reelforge.tools/")
      await page.evaluate(() => window.say({ source: "reelforge-site", type: "status" }))
      await until("the status answer", () => page.evaluate(() => window.answers.length === 1))
      equal(await page.evaluate(() => window.answers[0].paired), false, "paired, as the website is told")
      await sleep(600)
      equal(other.got.filter(entry => entry.token || entry.path !== "/v1/hello"), [], "what it was sent beyond hello")
      equal((await browser.storage("local")).selectors, undefined, "its list of hooks was not taken")
    } finally {
      await other.stop()
    }
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
