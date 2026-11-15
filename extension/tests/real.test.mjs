// The extension against ReelForge's real local server and ledger (real.mjs), not the stand-in: pairing, a post
// from the post page to "posted" in the stats, the popup's Post and Cancel, the press with its one grant, what
// refuses a second post, each way a post stops (signed out, a page that changed, a closed tab, a browser that
// never comes back), disconnecting, and what each half does when the other is busy, restarted or told
// something it did not expect. The upload pages are still the copies. `app` here is a RealApp.
import http from "node:http"
import { equal, ok, until, sleep, note, launch, quickSelectors } from "./harness.mjs"
import { FakeApp, EXTENSION_ORIGIN } from "./fake_app.mjs"

export const real = true

const HOST = { tiktok: "www.tiktok.com", youtube: "studio.youtube.com", instagram: "www.instagram.com", x: "x.com" }
const text = locator => locator.textContent().then(value => value.replace(/\s+/g, " ").trim())

// Where each copy of an upload page keeps the text, by the name the job's text gives it.
const BOXES = {
  tiktok: { caption: ".public-DraftEditor-content" },
  youtube: { title: "#title-textarea #textbox", description: "#description-textarea #textbox" },
  instagram: { caption: "[data-lexical-editor]" },
  x: { text: "[role=dialog] [data-testid=tweetTextarea_0]" },
}
const filled = (tab, platform) => tab.evaluate(boxes => {
  const root = document.querySelector("ytcp-app")?.shadowRoot || document  // the YouTube copy sits in a shadow root
  return Object.fromEntries(Object.entries(boxes).map(([field, selector]) => [field, root.querySelector(selector).innerText.trim()]))
}, BOXES[platform])

// A finished, approved video on the Publish list and a browser that is connected.
async function ready({ app, browser }, size) {
  const film = await app.ask("video", size ? { size } : {})
  equal(await browser.pair(app), "ok", "pairing")
  return film
}

// Post pressed in ReelForge's window: the batch, its page opened in the browser, and each job's id.
async function post({ app, browser }, film, platforms, options = {}) {
  const made = await app.ask("batch", { path: film.path, platforms, press: options.press === true })
  const page = await browser.post(made.post, null, { yes: options.yes === true })
  return { ...made, page }
}

const becomes = (app, id, states, timeout = 30000) => {
  let last
  return until(() => `the job to be ${states} (it is ${last})`, async () => states.includes(last = await app.state(id)) && last, timeout)
}

// One request, as any program on this computer can make it: no browser, so every header is the test's own.
function send(port, method, path, { token, body, raw, headers = {} } = {}) {
  const data = raw ?? (body === undefined ? null : JSON.stringify(body))
  return new Promise((resolve, reject) => {
    const request = http.request({
      host: "127.0.0.1", port, method, path,
      headers: {
        ...(token ? { "X-ReelForge-Token": token } : {}),
        ...(data === null ? {} : { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) }),
        ...headers,
      },
    }, response => {
      const chunks = []
      response.on("data", chunk => chunks.push(chunk))
      response.on("end", () => {
        const bytes = Buffer.concat(chunks)
        let json = null
        try {
          json = JSON.parse(bytes.toString("utf8"))
        } catch {
          // Not JSON: a page, a video, or nothing.
        }
        resolve({ status: response.statusCode, headers: response.headers, bytes, json })
      })
    })
    request.on("error", reject)
    request.end(data ?? undefined)
  })
}

// An answer with everything left out that two servers cannot share: ids, tokens, hashes, times, the port. What
// stays is what the extension goes by: the status, the code, the sentence, each field and its value.
function comparable(answer, port) {
  const plain = value => {
    if (Array.isArray(value)) return value.map(plain)
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).filter(([key]) => key !== "version").map(([key, inner]) => [key,
        // The sample texts are each side's own. Which boxes a platform has is what the extension goes by.
        key === "text" && inner && typeof inner === "object" ? Object.keys(inner).sort() : key === "token" ? `<token of ${String(inner).length >= 43 ? "43 or more" : "too few"}>` : plain(inner)]))
    }
    if (typeof value !== "string") return value
    return value.replace(/p([bj])_[0-9a-f]{16}/g, "<p$1>").replace(/[0-9a-f]{64}/g, "<sha256>").replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, "<uuid>")
      .replace(/\d{4}-\d\d-\d\dT[\d:]+\+00:00/g, "<time>").replaceAll(String(port), "<port>")
  }
  const kept = ["content-type", "accept-ranges", "etag", "content-range", "access-control-allow-origin", "access-control-allow-headers", "access-control-allow-methods"]
  return {
    status: answer.status,
    headers: plain(Object.fromEntries(kept.filter(name => answer.headers[name]).map(name => [name, answer.headers[name].replace(/;\s*charset=utf-8/i, "")]))),
    body: answer.json ? plain(answer.json) : `${answer.bytes.length} bytes`,
  }
}

// The same questions for ReelForge's real server and for its stand-in (fake_app.mjs): every route the extension
// calls, with what it may and may not ask. `side` has the port and the few things a test sets up on each.
async function conversation(side) {
  const said = []
  const ask = async (what, method, path, options) => {
    const answer = await send(side.port, method, path, options)
    said.push([what, comparable(answer, side.port)])
    return answer
  }
  const post = (what, path, token, body, more = {}) => ask(what, "POST", path, { token, body, ...more })
  const origin = { Origin: EXTENSION_ORIGIN }
  const pairing = async install => {
    const page = await send(side.port, "GET", (await side.pairUrl()).replace(/^http:\/\/[^/]+/, ""))
    return { secret: /data-rf-secret="([^"]+)"/.exec(page.bytes.toString("utf8"))[1], install, name: "Chromium on Linux", extension_version: "1.0.0", protocol: 1 }
  }

  // The rules for every request.
  await ask("another Host", "GET", "/v1/hello", { headers: { Host: `localhost:${side.port}` } })
  await ask("a web page's Origin", "GET", "/v1/hello", { headers: { Origin: "https://example.com" } })
  await ask("a request a web page's script made", "GET", "/v1/hello", { headers: { "Sec-Fetch-Site": "cross-site" } })
  await ask("PUT", "PUT", "/v1/hello")
  await ask("the extension's preflight", "OPTIONS", "/v1/seen", { headers: origin })
  await ask("a web page's preflight", "OPTIONS", "/v1/seen", { headers: { Origin: "https://example.com" } })
  await ask("hello", "GET", "/v1/hello")
  await ask("an address nothing is at", "GET", "/v1/nothing")
  await ask("an address nothing is at, posted to", "POST", "/v1/nothing", { body: {} })
  await ask("a call without a token", "GET", "/v1/state")
  await ask("a call with a token nobody gave out", "GET", "/v1/state", { token: "x".repeat(43) })
  await ask("a POST that is not JSON", "POST", "/v1/seen", { raw: "hello", headers: { "Content-Type": "text/plain" } })
  await ask("a POST of broken JSON", "POST", "/v1/seen", { raw: "{" })
  await ask("a POST of JSON that is no object", "POST", "/v1/seen", { raw: "[1]" })
  await ask("a POST of more than 64 KiB", "POST", "/v1/seen", { raw: JSON.stringify({ name: "x".repeat(70000) }) })

  // Pairing.
  await ask("the pair page with a key nobody gave out", "GET", "/pair?k=nope")
  const first = await pairing("parity-chromium")
  await post("pairing from somewhere that is not the extension", "/v1/pair/claim", null, first)
  await post("pairing in another version of the link", "/v1/pair/claim", null, { ...first, protocol: 2 }, { headers: origin })
  await post("pairing without saying which browser", "/v1/pair/claim", null, { ...first, install: "" }, { headers: origin })
  await post("pairing with a secret nobody gave out", "/v1/pair/claim", null, { ...first, secret: "nope" }, { headers: origin })
  const token = (await post("pairing", "/v1/pair/claim", null, first, { headers: origin })).json.token
  await post("the same secret a second time", "/v1/pair/claim", null, first, { headers: origin })
  await ask("hello from that browser, with a nonce", "GET", `/v1/hello?install=parity-chromium&nonce=${"ab".repeat(16)}`)
  await ask("hello with a nonce that is not one", "GET", "/v1/hello?install=parity-chromium&nonce=abc")
  const other = (await post("a second browser pairing", "/v1/pair/claim", null, await pairing("parity-edge"), { headers: origin })).json.token

  // The list, and Post.
  const film = await side.video("ep32_hail")
  await ask("the list", "GET", "/v1/state", { token })
  await post("Post with nothing said", "/v1/batches", token, {})
  await post("Post for no platform", "/v1/batches", token, { video: film.id, platforms: [] })
  await post("Post for a platform ReelForge does not have", "/v1/batches", token, { video: film.id, platforms: ["myspace"] })
  await post("Post for a video that is not listed", "/v1/batches", token, { video: "0".repeat(64), platforms: ["tiktok"] })
  await post("Post for neither", "/v1/batches", token, { video: "0".repeat(64), platforms: [] })
  const made = (await post("Post for TikTok and X, with the press", "/v1/batches", token, { video: film.id, platforms: ["tiktok", "x"], press: true })).json
  const [tiktok, x] = made.jobs.map(job => job.id)
  await post("the same Post again", "/v1/batches", token, { video: film.id, platforms: ["tiktok", "x"], press: true })
  await ask("the batch", "GET", `/v1/batch/${made.batch}`, { token })
  await ask("a batch nobody made", "GET", "/v1/batch/pb_0000000000000000", { token })

  // A job, from waiting to posted.
  const at = (id, what) => `/v1/jobs/${id}/${what}`
  await post("taking a job nobody made", at("pj_0000000000000000", "claim"), token, {})
  await post("a report before the job is taken", at(tiktok, "event"), token, { state: "opening" })
  await post("a report no browser can make, before the job is taken", at(tiktok, "event"), token, { state: "claimed" })
  await ask("the video before the job is taken", "GET", at(tiktok, "file"), { token })
  await post("the press before the job is taken", at(tiktok, "press"), token, {})
  await post("taking the job", at(tiktok, "claim"), token, {})
  await post("taking it again", at(tiktok, "claim"), token, {})
  await post("another browser taking it", at(tiktok, "claim"), other, {})
  await post("another browser reporting on it", at(tiktok, "event"), other, { state: "opening" })
  await ask("another browser asking for its video", "GET", at(tiktok, "file"), { token: other })
  await post("another browser asking for the press", at(tiktok, "press"), other, {})
  await post("another browser calling off the job that still waits", at(x, "event"), other, { state: "cancelled", detail: "cancelled before it started" })
  await post("a report on the job that was called off", at(x, "event"), other, { state: "opening" })
  await post("taking the job that was called off", at(x, "claim"), token, {})
  await post("a state only ReelForge sets", at(tiktok, "event"), token, { state: "posting" })
  await post("no state at all", at(tiktok, "event"), token, { step: "open_tab" })
  await post("opening, with more than a report may carry", at(tiktok, "event"), token, { state: "opening", step: "open_tab", progress: 7, detail: "one\ntwo\u0007   three", error_code: "Not A Code", post_id: 12 })
  await post("uploading, half way", at(tiktok, "event"), token, { state: "uploading", step: "attach", progress: 0.5, detail: "handing it over" })
  await post("filling, with nothing else said", at(tiktok, "event"), token, { state: "filling" })
  await post("a step back", at(tiktok, "event"), token, { state: "opening" })
  await post("the same state again, with its step", at(tiktok, "event"), token, { state: "filling", step: "fill_caption", strategy: "caption=role" })
  await post("the press before it is ready", at(tiktok, "press"), token, {})
  await ask("the whole video", "GET", at(tiktok, "file"), { token })
  await ask("its first ten bytes", "GET", at(tiktok, "file"), { token, headers: { Range: "bytes=0-9" } })
  await ask("from its tenth byte on", "GET", at(tiktok, "file"), { token, headers: { Range: "bytes=10-" } })
  await ask("its last five bytes", "GET", at(tiktok, "file"), { token, headers: { Range: "bytes=-5" } })
  await ask("a part that is not in it", "GET", at(tiktok, "file"), { token, headers: { Range: "bytes=999999999-" } })
  await ask("a Range that is not one", "GET", at(tiktok, "file"), { token, headers: { Range: "pages=1-2" } })
  await post("ready, with an id and a link that are not TikTok's", at(tiktok, "event"), token, { state: "ready", step: "ready", post_id: "abc", url: "https://example.com/v/1" })
  await post("posted, with TikTok's own", at(tiktok, "event"), token, { state: "posted", step: "confirm", post_id: "7300000000000000001", url: "https://www.tiktok.com/@fixtureuser/video/7300000000000000001" })
  await post("posted, said again with another id", at(tiktok, "event"), token, { state: "posted", post_id: "7300000000000000002" })
  await post("failed, once it is up", at(tiktok, "event"), token, { state: "failed", error_code: "tab_closed" })
  await ask("the video of a post that is up", "GET", at(tiktok, "file"), { token })
  await post("the press for a post that is up", at(tiktok, "press"), token, {})
  await ask("the list, with one post up", "GET", "/v1/state", { token })

  // The press: granted once, and from then on the browser cannot call the post failed.
  const next = await side.video("ep33_rain")
  const more = (await post("Post for the next video, with the press", "/v1/batches", token, { video: next.id, platforms: ["tiktok", "youtube"], press: true })).json
  const second = more.jobs.map(job => job.id)
  await post("taking it", at(second[0], "claim"), token, {})
  await post("ready", at(second[0], "event"), token, { state: "ready", step: "ready", detail: "all filled in" })
  await post("the press", at(second[0], "press"), token, {})
  await ask("the batch while Post is being pressed", "GET", `/v1/batch/${more.batch}`, { token })
  await post("the press again", at(second[0], "press"), token, {})
  await post("the browser calling it failed after the press", at(second[0], "event"), token, { state: "failed", step: "press", error_code: "press_rejected", error_message: "TikTok showed nothing." })
  await post("posted, once nobody is sure", at(second[0], "event"), token, { state: "posted" })
  await post("not sure, said again", at(second[0], "event"), token, { state: "unknown", detail: "still nothing" })
  await ask("the video of a post nobody is sure of", "GET", at(second[0], "file"), { token })
  await post("the same video while nobody is sure", "/v1/batches", token, { video: next.id, platforms: ["tiktok"] })
  // A job that fails stays failed, whatever is said next.
  await post("taking the other", at(second[1], "claim"), token, {})
  await post("failed, with its reason", at(second[1], "event"), token, { state: "failed", step: "signed_in", error_code: "not_signed_in", error_message: "Sign in to YouTube in this browser, then post again." })
  await post("failed, said again with another reason", at(second[1], "event"), token, { state: "failed", error_code: "timeout", detail: "later" })
  await post("cancelled, once it has failed", at(second[1], "event"), token, { state: "cancelled" })
  await post("not sure, once it has failed", at(second[1], "event"), token, { state: "unknown" })
  await post("the press for a post that failed", at(second[1], "press"), token, {})
  await post("the same video where it failed", "/v1/batches", token, { video: next.id, platforms: ["youtube"] })

  // A job nobody took in ten minutes.
  const late = (await post("Post for a third video", "/v1/batches", token, { video: (await side.video("ep34_snow")).id, platforms: ["instagram"] })).json
  await side.waited(late.jobs[0].id)
  await post("taking a job that waited more than ten minutes", at(late.jobs[0].id, "claim"), token, {})
  await ask("its batch", "GET", `/v1/batch/${late.batch}`, { token })

  // The rest of what the extension asks.
  await post("the browser's hello, with the accounts it saw", "/v1/seen", token, { install: "someone-else", name: "Chromium on Linux", extension_version: "1.0.0", protocol: 1, accounts: { x: { signed_in: true, handle: "@fixtureuser" } } })
  await post("its hello in another version of the link", "/v1/seen", token, { protocol: 2 })
  said.push(["the list of page hooks", (await send(side.port, "GET", "/v1/selectors", { token })).status])
  await ask("a post page with a key nobody gave out", "GET", `/post/${made.batch}?k=nope`)
  await ask("what a post page asks, with such a key", "GET", `/v1/page/batch/${made.batch}?k=nope`)
  await ask("what a pair page asks, with such a key", "GET", "/v1/page/pair?k=nope")
  await post("Post by hand, with such a key", "/v1/page/manual", null, { k: "nope", batch: made.batch, platform: "tiktok" })
  await post("disconnect", "/v1/unpair", token, {})
  await ask("a call with the token of a browser that disconnected", "GET", "/v1/state", { token })
  await ask("a call from the other browser", "GET", `/v1/batch/${made.batch}`, { token: other })

  // The demo answers hello and nothing else.
  await side.demo()
  await ask("hello, in the demo", "GET", `/v1/hello?install=parity-edge&nonce=${"cd".repeat(16)}`)
  await ask("the list, in the demo", "GET", "/v1/state", { token: other })
  return said
}

export const tests = {
  async "the stand-in for ReelForge answers as ReelForge does: one conversation with both, compared answer by answer"({ app }) {
    const fake = await new FakeApp({ profile: { key: ".", name: "Rendered Wrong" } }).start()
    try {
      const real = await conversation({
        port: app.port, pairUrl: () => app.pairUrl(), video: name => app.ask("video", { name }),
        waited: job => app.ask("age", { job, minutes: 11, column: "created_at" }), demo: () => app.ask("demo", { on: true }),
      })
      const standIn = await conversation({
        port: fake.port, pairUrl: () => fake.pairUrl(), video: name => fake.addVideo({ name: `${name}.mp4` }),
        waited: job => { fake.jobs.get(job).created -= 11 * 60 * 1000 }, demo: () => { fake.demo = true },
      })
      const differ = real.flatMap(([what, answer], i) => (JSON.stringify(answer) === JSON.stringify(standIn[i][1]) ? []
        : [`${what}:\n          ReelForge  ${JSON.stringify(answer)}\n          stand-in   ${JSON.stringify(standIn[i][1])}`]))
      ok(real.length > 90 && real.length === standIn.length, `answers compared (${real.length} and ${standIn.length})`)
      ok(!differ.length, `${differ.length} of ${real.length} answers differ.\n        ${differ.join("\n        ")}`)
    } finally {
      await fake.stop()
    }
  },

  async "pairing: the pair page connects the browser, and ReelForge proves itself before it is believed"({ app, browser }) {
    equal(await app.ask("paired"), [], "browsers before")
    const page = await browser.context.newPage()
    await page.goto(await app.pairUrl())
    await page.waitForFunction(() => document.documentElement.dataset.rfPair === "ok")
    // The page is ReelForge's own, with its own script: it says so in words too.
    await until("the page to say it is connected", async () => (await text(page.locator("#title"))) === "This browser is connected")
    const [name] = await app.ask("paired")
    ok(name.length > 3, `the name ReelForge has for the browser (${name})`)
    const popup = await browser.popup()
    await until("the popup to be connected", async () => (await popup.locator("#status").textContent()) === "Rendered Wrong")
  },

  async "YouTube from ReelForge's post page: ready, the person publishes, and the post is in the stats under its id"(t) {
    const film = await ready(t)
    const { jobs, page } = await post(t, film, ["youtube"])
    await becomes(t.app, jobs.youtube, ["ready"])
    await until("the page to say it is ready", async () => (await text(page.locator("#rows"))).includes("Ready: check it and press Publish (or Save) in the YouTube tab"))
    ok((await text(page.locator("#rows"))).includes("Nothing is posted until you press it."), "the page says ReelForge stops here")
    equal(await page.locator("#press").isHidden(), true, "the question about pressing, for a post that stops before Post")
    const tab = await t.browser.tab(HOST.youtube)
    equal(await tab.evaluate(() => [fixture.files.length, fixture.clicks]), [1, 0], "the file on the page, and presses of Save")
    await tab.locator("#done-button").click()
    await becomes(t.app, jobs.youtube, ["posted"])
    const job = await t.app.job(jobs.youtube)
    equal([job.post_id, job.url, job.claimed_by !== null], ["FIXTUREvid1", "https://youtu.be/FIXTUREvid1", true], "the job's post id and link")
    // The id is the one the stats reader uses, so the post is filed under this video before any read.
    equal(await t.app.ask("posts"), [{ platform: "youtube", post_id: "FIXTUREvid1", episode_id: job.episode_id, link: "publish", url: "https://youtu.be/FIXTUREvid1" }], "the stats' posts")
    equal(await t.app.ask("events", { id: jobs.youtube }).then(all => all.filter((state, i) => state !== all[i - 1])), ["queued", "claimed", "opening", "uploading", "filling", "ready", "posted"], "the job's states, in order")
    await until("the page to say it is posted", async () => (await text(page.locator("#rows"))).includes("Posted"))
    equal(await t.app.ask("press_unlocked", { platform: "youtube" }), true, "an assisted post unlocks Press Post for me there")
    equal(job.review, "approved", "the review the job was made under")
    // The video is up there now: a second Post of it is refused, and says where the first one is.
    const again = await t.app.ask("batch", { path: film.path, platforms: ["youtube"] })
    equal([again.batch, again.blocked.map(block => [block.reason, block.job, block.url])], [null, [["posted", jobs.youtube, "https://youtu.be/FIXTUREvid1"]]], "a second Post of the video")
    const popup = await t.browser.popup()
    await until("the popup to offer the other three only", async () => (await popup.locator(`[data-video="${film.id}"] [data-platform=youtube]`).textContent().catch(() => "")) === "YouTube: posted")
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    ok(await popup.locator("[data-key=\"pick:youtube\"]").isDisabled(), "YouTube cannot be chosen again in the popup")
  },

  async "four platforms at once: each page has the whole video, by size and SHA-256, and its exact text. A second Post meanwhile is refused"(t) {
    // Bigger than one piece of the hand-over (8 MB), so ReelForge is asked for the file in parts.
    const film = await ready(t, 9 * 1024 * 1024 + 321)
    const { jobs } = await post(t, film, ["tiktok", "youtube", "instagram", "x"])
    for (const platform of Object.keys(HOST)) await becomes(t.app, jobs[platform], ["ready"], 60000)
    for (const platform of Object.keys(HOST)) {
      const tab = await t.browser.tab(HOST[platform])
      // The page works the hash out itself, over the bytes its file input was given.
      equal(await tab.evaluate(() => [fixture.files, fixture.sha256]), [[{ name: film.name, size: film.size, type: "video/mp4", trusted: false }], film.id], `the file on ${platform}'s page`)
      equal(await filled(tab, platform), JSON.parse((await t.app.job(jobs[platform])).text), `the text in ${platform}'s boxes`)
      equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [0, false], `presses of ${platform}'s final button`)
    }
    equal(JSON.parse((await t.app.job(jobs.tiktok)).text), { caption: "What if hail doubled every 2 seconds? #fyp #whatif #hail" }, "TikTok's text, as ReelForge wrote it")
    const again = await t.app.ask("batch", { path: film.path, platforms: ["tiktok", "x"] })
    equal([again.batch, again.blocked.map(block => [block.reason, block.state])], [null, [["in_progress", "ready"], ["in_progress", "ready"]]], "a second Post")
  },

  async "a report the database could not take just then arrives all the same"(t) {
    const film = await ready(t)
    const { jobs } = await post(t, film, ["tiktok"])
    await becomes(t.app, jobs.tiktok, ["ready"])
    await t.app.ask("busy", { count: 2 })
    await (await t.browser.tab(HOST.tiktok)).locator("[data-e2e=post_video_button]").click()
    await until("the browser to have it as posted", async () => (await t.browser.jobRecord(jobs.tiktok)).state === "posted")
    equal(await t.app.state(jobs.tiktok), "ready", "what ReelForge knows while its database is busy")
    const popup = await t.browser.popup()  // (or the half-minute check: nobody has to ask twice)
    await becomes(t.app, jobs.tiktok, ["posted"], 45000)
    await popup.close()
  },

  async "the popup: Post, read the text, Cancel. No job was taken and the upload log is as it was"(t) {
    const film = await ready(t)
    const popup = await t.browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    equal(await popup.locator("[data-confirm=tiktok] pre").allTextContents(), ["What if hail doubled every 2 seconds? #fyp #whatif #hail"], "TikTok's exact text, from the saved caption")
    equal((await t.app.ask("uploads")).length, 4, "upload-log lines while the question is open")
    await popup.getByRole("button", { name: "Cancel" }).click()
    await until("the jobs to be cancelled", async () => (await t.app.ask("jobs")).every(job => job.state === "cancelled"))
    equal((await t.app.ask("jobs")).map(job => [job.claimed_by, job.detail]), Array(4).fill([null, "cancelled before it started"]), "who held the jobs, and why they ended")
    equal(await t.app.ask("uploads"), [], "upload-log lines afterwards")
    equal(t.browser.context.pages().filter(page => page.url().startsWith("https://")).length, 0, "upload tabs")
  },

  async "the popup: Post opens the pages, and a video that is up shows as posted in its list"(t) {
    const film = await ready(t)
    const popup = await t.browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    for (const platform of ["youtube", "instagram", "x"]) await popup.locator(`[data-key="pick:${platform}"]`).uncheck()
    await popup.getByRole("button", { name: "Continue" }).click()
    await popup.getByRole("button", { name: "Open the upload page" }).click()
    const tab = await t.browser.tab(HOST.tiktok)
    const [job] = await t.app.ask("jobs")
    await becomes(t.app, job.id, ["ready"])
    await tab.locator("[data-e2e=post_video_button]").click()
    await becomes(t.app, job.id, ["posted"])
    await until("the list to say so", async () => (await popup.locator(`[data-video="${film.id}"] [data-platform=tiktok]`).textContent()) === "TikTok: posted")
  },

  async "Press Post for me: nothing is pressed for a page nobody clicked on, and once with the person's yes"(t) {
    const film = await ready(t)
    // One post there with the extension's help comes first, then the switch on its settings page.
    equal(await t.app.ask("unlock", { platform: "tiktok" }), true, "TikTok is unlocked")
    const options = await t.browser.options()
    await until("the switch to unlock", () => options.locator("#press-tiktok").isEnabled())
    await options.locator("#press-tiktok").check()
    await until("the switch to be saved", async () => (await t.browser.storage("local")).press?.tiktok === true)
    await options.close()

    // Everything a program can arrange is in place: a job that says press, and its post page opened.
    const first = await post(t, film, ["tiktok"], { press: true })
    await until("the page to ask", () => first.page.locator("#press").isVisible())
    await becomes(t.app, first.jobs.tiktok, ["ready"])
    const tab = await t.browser.tab(HOST.tiktok)
    await until("the note to say why nothing was pressed", async () => (await note(tab)).more.includes("nobody said yes to it for this post in this browser"))
    equal([await tab.evaluate(() => fixture.clicks), await t.app.state(first.jobs.tiktok)], [0, "ready"], "presses of Post, and the job")
    await until("the question to go once it is too late", () => first.page.locator("#press").isHidden())
    await tab.locator("[data-e2e=post_video_button]").click()  // the person presses it themselves
    await becomes(t.app, first.jobs.tiktok, ["posted"])
    await tab.close()

    // The same again with the person's own click on the page's button.
    const second = await post(t, await t.app.ask("video", { name: "ep33_rain" }), ["tiktok"], { press: true, yes: true })
    await until("the page to say it will", async () => (await text(second.page.locator("#press-title"))) === "The extension presses Post for you")
    await becomes(t.app, second.jobs.tiktok, ["posted"])
    const pressed = await t.browser.tab(HOST.tiktok)
    equal(await pressed.evaluate(() => [fixture.clicks, fixture.trustedClicks, fixture.posted]), [1, 0, true], "presses the page saw")
    equal(await t.app.ask("events", { id: second.jobs.tiktok }).then(all => all.filter((state, i) => state !== all[i - 1])), ["queued", "claimed", "opening", "uploading", "filling", "ready", "posting", "posted"], "the job's states")
  },

  async "after the press, a page that shows nothing leaves the job not sure, and ReelForge makes no second one"(t) {
    t.site.mode.tiktok = "trusted_only"
    const film = await ready(t)
    await t.app.ask("unlock", { platform: "tiktok" })
    const settings = await t.browser.popup()
    await settings.evaluate(() => chrome.storage.local.set({ press: { tiktok: true } }))
    await settings.close()
    const { jobs } = await post(t, film, ["tiktok"], { press: true, yes: true })
    await becomes(t.app, jobs.tiktok, ["posting"])
    const tab = await t.browser.tab(HOST.tiktok)
    await until("the note to say the page has not reacted", async () => (await note(tab)).line === "TikTok has not reacted", 20000)
    equal(await t.app.state(jobs.tiktok), "posting", "the job while nobody knows")
    // The tab is closed without an answer: the browser's "this one is over" must not free the video.
    await tab.close()
    await becomes(t.app, jobs.tiktok, ["unknown", "failed", "cancelled"])
    equal(await t.app.state(jobs.tiktok), "unknown", "the job")
    const again = await t.app.ask("batch", { path: film.path, platforms: ["tiktok"], press: true })
    equal([again.batch, again.blocked[0].reason], [null, "unknown"], "a second Post of the video")
    equal(await t.app.ask("resolve", { id: jobs.tiktok, posted: false }), "cancelled", "the person's \"It didn't post\"")
  },

  async "the person's press before the page was ready, then a closed tab: ReelForge takes \"not sure\""(t) {
    t.site.mode.youtube = "slow_mute"
    const film = await ready(t)
    const { jobs } = await post(t, film, ["youtube"])
    const tab = await t.browser.tab(HOST.youtube)
    await until("Save to be there while the video is still going up", () => tab.locator("#done-button").isVisible(), 30000)
    equal(await t.app.state(jobs.youtube), "filling", "the job when Save is pressed")
    await tab.locator("#done-button").click()
    await until("the press to be noticed", async () => (await t.browser.jobRecord(jobs.youtube)).pressedByPerson === true)
    await tab.close()
    await becomes(t.app, jobs.youtube, ["unknown", "failed"])
    const job = await t.app.job(jobs.youtube)
    equal([job.state, job.error_code], ["unknown", "unknown_outcome"], "the job at ReelForge")
    equal((await t.browser.jobRecord(jobs.youtube)).outbox, [], "nothing is left unsaid in the browser")
    // "It's posted", said in ReelForge, is the popup's answer too: it stops asking.
    const popup = await t.browser.popup()
    const card = popup.locator(`[data-job="${jobs.youtube}"]`)
    await until("the popup to show the post nobody is sure of", async () => (await card.locator(".chip").textContent().catch(() => "")) === "not sure")
    equal(await t.app.ask("resolve", { id: jobs.youtube, posted: true }), "posted", "the person's \"It's posted\"")
    await until("the popup to take ReelForge's word", async () => (await card.locator(".chip").textContent()) === "posted", 20000)
  },

  async "a video replaced after Post was pressed is not attached"(t) {
    const film = await ready(t)
    const made = await t.app.ask("batch", { path: film.path, platforms: ["tiktok"] })
    await t.app.ask("replace", { path: film.path })
    await t.browser.post(made.post)
    await becomes(t.app, made.jobs.tiktok, ["failed"])
    const job = await t.app.job(made.jobs.tiktok)
    equal([job.error_code, job.step], ["changed", "attach"], "error code and step")
    equal(await (await t.browser.tab(HOST.tiktok)).evaluate(() => fixture.files.length), 0, "files given to the page")
  },

  async "ReelForge closed and opened again on another port: the job's news finds it"(t) {
    t.site.mode.tiktok = "slow"
    const film = await ready(t)
    const { jobs } = await post(t, film, ["tiktok"])
    await becomes(t.app, jobs.tiktok, ["filling"])
    const before = t.app.port
    await t.app.ask("stop")
    const tab = await t.browser.tab(HOST.tiktok)
    await until("the page to be ready", async () => (await note(tab))?.line === "Check it, then press Post", 20000)
    equal(await t.app.state(jobs.tiktok), "filling", "what ReelForge knew when it closed")
    const port = await t.app.ask("start", { port: before === 47830 ? 47829 : before + 1 })
    ok(port !== before, "another port")
    const popup = await t.browser.popup()
    await becomes(t.app, jobs.tiktok, ["ready"], 30000)
    await popup.close()
  },

  async "accents, an emoji, quotes and angle brackets: the text ReelForge wrote is in every box, character for character"(t) {
    const caption = "Was wäre, wenn sich Hagel alle 2 Sekunden verdoppelt? 🌨️ <b>5 > 3</b> & \"so\" geht's"
    const film = await t.app.ask("video", { caption })
    equal(await t.browser.pair(t.app), "ok", "pairing")
    // From the popup, so the text is what ReelForge reads from the video's own files.
    const popup = await t.browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    const wrote = {
      tiktok: { caption: `${caption} #fyp #whatif #hail` }, instagram: { caption: `${caption} #whatif #hail` },
      youtube: { title: `${caption} #shorts`, description: "#whatif #hail" }, x: { text: caption },
    }
    for (const platform of Object.keys(HOST)) {
      equal(await popup.locator(`[data-confirm=${platform}] pre`).allTextContents(), Object.values(wrote[platform]), `what the popup shows for ${platform}`)
    }
    await popup.getByRole("button", { name: "Open the upload pages" }).click()
    const jobs = Object.fromEntries((await t.app.ask("jobs")).map(job => [job.platform, job]))
    for (const platform of Object.keys(HOST)) {
      await becomes(t.app, jobs[platform].id, ["ready"], 45000)
      equal([JSON.parse(jobs[platform].text), await filled(await t.browser.tab(HOST[platform]), platform)], [wrote[platform], wrote[platform]], `${platform}: what ReelForge wrote, and what is in the page's boxes`)
    }
  },

  async "the press is granted once: asked again it is refused, and for X it is refused whatever was switched on"(t) {
    const film = await ready(t)
    await t.app.ask("unlock", { platform: "tiktok" })
    // Everything forced on in the browser, X too, which its settings page never offers.
    const settings = await t.browser.popup()
    await settings.evaluate(() => chrome.storage.local.set({ press: { tiktok: true, x: true } }))
    await settings.close()
    const { jobs } = await post(t, film, ["tiktok", "x"], { press: true, yes: true })
    await becomes(t.app, jobs.tiktok, ["posted"])
    await becomes(t.app, jobs.x, ["ready"])
    equal(await (await t.browser.tab(HOST.tiktok)).evaluate(() => [fixture.clicks, fixture.posted]), [1, true], "presses of TikTok's Post")
    // The browser that was granted the press asks once more, and asks for X.
    const second = await t.browser.call(t.app.port, `/v1/jobs/${jobs.tiktok}/press`, {})
    equal([second.status, second.data], [409, { ok: false, error: "already_pressed", message: "Post was already pressed for this one." }], "a second ask for TikTok")
    const forX = await t.browser.call(t.app.port, `/v1/jobs/${jobs.x}/press`, {})
    equal([forX.status, forX.data.ok, forX.data.error], [409, false, "press_not_allowed"], "an ask for X")
    await sleep(1200)
    const x = await t.app.job(jobs.x)
    equal([x.state, x.press, await (await t.browser.tab(HOST.x)).evaluate(() => fixture.clicks)], ["ready", 0, 0], "X: the job, what it says about pressing, and presses of Post")
    equal((await t.app.ask("events", { id: jobs.tiktok })).filter(state => state === "posting").length, 1, "times TikTok's job went to posting")
  },

  async "signed out on all four: each post stops before the video is asked for, and ReelForge hears which accounts were seen"(t) {
    for (const platform of Object.keys(HOST)) t.site.mode[platform] = "signed_out"
    const film = await ready(t)
    const { jobs, page } = await post(t, film, Object.keys(HOST))
    for (const platform of Object.keys(HOST)) {
      await becomes(t.app, jobs[platform], ["failed"], 45000)
      const job = await t.app.job(jobs[platform])
      equal([job.error_code, job.step], ["not_signed_in", platform === "youtube" ? "open_tab" : "signed_in"], `${platform}: error code and step`)
      ok(/sign in/i.test(job.error_message), `${platform}: the sentence (${job.error_message})`)
      ok(!(await t.app.ask("events", { id: jobs[platform] })).includes("uploading"), `${platform}: the video was never asked for`)
    }
    // ReelForge's page says what happened and offers its own by-hand flow for each.
    await until("the page to offer Post by hand for all four", async () => (await page.getByRole("button", { name: /by hand$/ }).count()) === 4)
    equal((await text(page.locator("#rows"))).split("Failed at checking you're signed in").length - 1, 3, "rows that say where it failed (YouTube's tab never got as far)")
    ok((await text(page.locator("#rows"))).includes("Failed at opening the tab"), "YouTube's row says where it failed")
    // What the browser saw goes with its next hello. It is kept apart from the accounts connected in ReelForge.
    const popup = await t.browser.popup()
    const seen = { signed_in: false, handle: null }
    await until("ReelForge to have what the browser saw", async () => (await t.app.ask("browsers"))[0].accounts?.x)
    equal((await t.app.ask("browsers"))[0].accounts, { tiktok: seen, instagram: seen, x: seen }, "the accounts on file for this browser")
    await popup.close()
  },

  async "a page whose Post button is gone: hook_not_found with what was tried, the rest left in the tab, and Post by hand from ReelForge's page"(t) {
    t.site.mode.tiktok = "no_post"
    const film = await ready(t)
    const { jobs, page } = await post(t, film, ["tiktok"])
    await becomes(t.app, jobs.tiktok, ["failed"], 45000)
    const job = await t.app.job(jobs.tiktok)
    // What was tried and how many elements each way found. Nothing of the page's content.
    equal([job.error_code, job.step, job.detail], ["hook_not_found", "upload", "hook=post_button tried=data-e2e:0,role:0,text:0"], "error code, step and detail")
    const caption = JSON.parse(job.text).caption
    const tab = await t.browser.tab(HOST.tiktok)
    equal([await tab.evaluate(() => fixture.files.length), (await filled(tab, "tiktok")).caption], [1, caption], "the file and the caption, left in the tab")
    await until("the page to say so", async () => (await text(page.locator("#rows"))).includes("Failed at uploading"))
    ok((await text(page.locator("#rows"))).includes("TikTok has changed its page: ReelForge could not find the Post button."), "the page gives the extension's sentence")
    // Post by hand is ReelForge's own flow: the text copied, the upload page opened, the job set aside.
    await page.getByRole("button", { name: "Post to TikTok by hand" }).click()
    await until("ReelForge to set the job aside", async () => (await t.app.job(jobs.tiktok)).detail === "posted by hand")
    equal([await t.app.state(jobs.tiktok), await t.app.ask("desk")], ["cancelled", { opened: ["https://www.tiktok.com/tiktokstudio/upload"], copied: [caption] }], "the job, and what ReelForge opened and copied")
    await until("the page to say so", async () => (await text(page.locator("#rows"))).includes("You're posting this one by hand"))
  },

  async "a newer list of page hooks from ReelForge reaches an extension that has not been reloaded"(t) {
    // The packed list waits 30 seconds for a sign that somebody is signed in. This one waits 2.
    t.site.mode.tiktok = "no_input"
    await t.app.ask("selectors", { data: quickSelectors(2000) })
    const film = await ready(t)
    const { jobs } = await post(t, film, ["tiktok"])
    await becomes(t.app, jobs.tiktok, ["failed"], 15000)
    const job = await t.app.job(jobs.tiktok)
    equal([job.error_code, job.step, job.detail], ["hook_not_found", "signed_in", "hook=signed_in"], "error code, step and detail")
    equal((await t.browser.storage("local")).selectors.version, 2, "the list the browser now has")
  },

  async "another account in the browser is a question: ReelForge's page shows it, and the popup's answer goes on"(t) {
    t.site.mode.x = "wrong_account"
    const film = await ready(t)
    const { jobs, page } = await post(t, film, ["x"])
    const question = "Signed in as @someoneelse, but ReelForge has @fixtureuser. Waiting for your answer in the extension."
    await until("ReelForge to be told", async () => (await t.app.job(jobs.x)).detail === question)
    await until("the page to show it", async () => (await text(page.locator("#rows"))).includes(question))
    equal([await t.app.state(jobs.x), await (await t.browser.tab(HOST.x)).evaluate(() => fixture.files.length)], ["opening", 0], "the job, and files given to the page, while it waits")
    const popup = await t.browser.popup()
    await popup.locator(`[data-job="${jobs.x}"]`).getByRole("button", { name: "Post as @someoneelse" }).click()
    await becomes(t.app, jobs.x, ["ready"])
    ok((await t.app.ask("log", { id: jobs.x })).some(event => event.detail === "You chose to post as @someoneelse."), "the choice is in the job's log")
    await until("ReelForge to have the account the browser saw", async () => (await t.app.ask("browsers"))[0].accounts?.x?.handle === "@someoneelse", 40000)
  },

  async "a tab closed while the video was going in: nothing was posted, the page says so, and the video can be posted again"(t) {
    t.site.mode.tiktok = "slow"
    const film = await ready(t)
    const first = await post(t, film, ["tiktok"])
    await becomes(t.app, first.jobs.tiktok, ["filling"])
    await (await t.browser.tab(HOST.tiktok)).close()
    await becomes(t.app, first.jobs.tiktok, ["failed", "unknown", "cancelled"])
    const job = await t.app.job(first.jobs.tiktok)
    equal([job.state, job.error_code, job.error_message], ["failed", "tab_closed", "The tab was closed before the post was ready. Nothing was posted."], "the job")
    await until("the page to say so", async () => (await text(first.page.locator("#rows"))).includes("The tab was closed before the post was ready. Nothing was posted."))
    // It failed, so nothing stands in the way of the same video there.
    t.site.mode.tiktok = "ok"
    const second = await post(t, film, ["tiktok"])
    equal(second.blocked, [], "what blocks a second Post")
    await becomes(t.app, second.jobs.tiktok, ["ready"])
    // Closed again, now that it is ready and nobody pressed Post.
    await (await t.browser.tab(HOST.tiktok)).close()
    await becomes(t.app, second.jobs.tiktok, ["failed", "unknown"])
    equal((await t.app.job(second.jobs.tiktok)).error_message, "The tab was closed before Post was pressed. Nothing was posted.", "the second job")
  },

  async "a browser that never comes back: its posts end by the clock, and the one whose video was on a page says to check first"(t) {
    t.site.mode.tiktok = "slow"       // the video is on TikTok's page and the post is not ready yet
    t.site.mode.x = "wrong_account"   // X waits for an answer: nothing is on its page
    const film = await ready(t)
    const made = await post(t, film, ["tiktok", "x"])
    await becomes(t.app, made.jobs.tiktok, ["filling"])
    await until("X to wait for its answer", async () => (await t.app.job(made.jobs.x)).detail?.startsWith("Signed in as @someoneelse"))
    await t.browser.close(true)  // and it is not opened again
    equal([await t.app.state(made.jobs.tiktok), await t.app.state(made.jobs.x)], ["filling", "opening"], "what ReelForge knows when the browser goes")
    // Until then both block the video. The extension itself can be silent for 15 minutes on a question.
    for (const id of Object.values(made.jobs)) await t.app.ask("age", { job: id, minutes: 19 })
    equal([await t.app.ask("expire"), await t.app.state(made.jobs.x)], [0, "opening"], "after 19 minutes of silence")
    for (const id of Object.values(made.jobs)) await t.app.ask("age", { job: id, minutes: 21 })
    equal(await t.app.ask("expire"), 1, "jobs the clock ended after 21 minutes")
    const x = await t.app.job(made.jobs.x)
    equal([x.state, x.error_code, x.error_message], ["failed", "timeout", "The browser stopped reporting before the video was on the page. Nothing was posted."], "X, where nothing was attached")
    equal(await t.app.state(made.jobs.tiktok), "filling", "TikTok, whose video is on the page")
    await t.app.ask("age", { job: made.jobs.tiktok, minutes: 46 })
    equal(await t.app.ask("expire"), 1, "jobs the clock ended after 46 minutes")
    const tiktok = await t.app.job(made.jobs.tiktok)
    equal([tiktok.state, tiktok.detail], ["unknown", "no word from the browser since the video was on the page"], "TikTok")
    // X is free for another try. TikTok is not: its own Post button was there to press.
    const again = await t.app.ask("batch", { path: film.path, platforms: ["tiktok", "x"] })
    equal([Object.keys(again.jobs), again.blocked.map(block => [block.platform, block.reason])], [["x"], [["tiktok", "unknown"]]], "a second Post of the video")
    ok(again.blocked[0].message.includes("Check your profile there"), `what the refusal says (${again.blocked[0].message})`)
    // The batch's page, opened in whatever browser, says the same.
    const other = await launch(t.site)
    try {
      const page = await other.context.newPage()
      await page.goto(made.post)
      await until("the page to say so", async () => (await text(page.locator("#rows"))).includes("ReelForge can't tell whether this posted"))
      ok((await text(page.locator("#rows"))).includes("Check TikTok before trying again."), "the page says to check TikTok first")
    } finally {
      await other.close()
    }
  },

  async "Disconnect, in the extension and in ReelForge: the token is dead on both sides, and a post page opens nothing"(t) {
    const film = await ready(t)
    const [was] = await t.app.ask("browsers")
    const options = await t.browser.options()
    await until("the settings page to say it is connected", async () => (await text(options.locator("#connection"))).startsWith("Connected to Rendered Wrong"))
    await options.locator("#disconnect").click()
    await until("ReelForge to forget this browser", async () => (await t.app.ask("browsers")).length === 0)
    equal((await t.browser.storage("local")).tokens, undefined, "tokens kept in the browser")
    await until("the settings page to say so", async () => (await text(options.locator("#connection"))).includes("not connected"))
    await options.close()
    // A post started in ReelForge now: its page says this browser is not connected, and nothing opens.
    const made = await t.app.ask("batch", { path: film.path, platforms: ["tiktok"] })
    const page = await t.browser.post(made.post)
    equal(await page.evaluate(() => document.documentElement.dataset.rfBatchState), "unpaired", "what the extension told the page")
    await until("the page to say so", async () => (await text(page.locator("#notice"))).includes("isn't connected to ReelForge yet"))
    equal([await t.app.state(made.jobs.tiktok), t.browser.tabs(HOST.tiktok).length], ["queued", 0], "the job, and upload tabs")
    // Connected again, the same page hands the post over.
    equal(await t.browser.pair(t.app), "ok", "pairing again")
    await page.reload()
    await becomes(t.app, made.jobs.tiktok, ["ready"])
    const [now] = await t.app.ask("browsers")
    equal([now.install, now.paired_at >= was.paired_at], [was.install, true], "the same browser, connected anew")
    // Disconnect in ReelForge's window: the browser's token stops working at once.
    const popup = await t.browser.popup()
    await until("the popup to be connected", async () => (await popup.locator("#status").textContent()) === "Rendered Wrong")
    await t.app.ask("forget", { install: now.install })
    const refused = await t.browser.call(t.app.port, "/v1/state")
    equal([refused.status, refused.data.error], [401, "unpaired"], "a call with the token the browser still has")
    await until("the popup to say it is not connected", () => popup.locator("[data-screen=not_connected]").count())
  },

  async "a video whose review does not say APPROVED: the popup says why, and neither side starts a post"(t) {
    const film = await t.app.ask("video", { review: null })
    equal(await t.browser.pair(t.app), "ok", "pairing")
    const popup = await t.browser.popup()
    const card = popup.locator(`[data-video="${film.id}"]`)
    await until("the video in the popup", () => card.count())
    const why = "QA passed, but there is no review yet (build/qa/review.md has to say APPROVED)."
    ok((await text(card)).includes("not reviewed yet") && (await text(card)).includes(why), `what the popup says (${await text(card)})`)
    equal(await card.getByRole("button", { name: "Post" }).count(), 0, "Post buttons for it")
    // Asked all the same, as a browser that did not look would: no job is made.
    const asked = await t.browser.call(t.app.port, "/v1/batches", { video: film.id, platforms: ["tiktok", "x"] })
    equal([asked.status, asked.data], [200, { batch: null, jobs: [], blocked: ["tiktok", "x"].map(platform => ({ platform, reason: "review", message: why })) }], "ReelForge's answer")
    const made = await t.app.ask("batch", { path: film.path, platforms: ["tiktok"] })
    equal([made.batch, made.blocked.map(block => block.reason), await t.app.ask("jobs")], [null, ["review"], []], "Post in ReelForge's window, and the jobs there are")
  },

  async "an X post that is too long: the popup shows ReelForge's reason for X and the text for the others"(t) {
    const caption = "What if hail doubled every 2 seconds, and nobody could say when it would stop? " + "It gets out of hand fast. ".repeat(9).trim()
    const film = await t.app.ask("video", { caption })
    equal(await t.browser.pair(t.app), "ok", "pairing")
    const popup = await t.browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    for (const platform of ["youtube", "instagram"]) await popup.locator(`[data-key="pick:${platform}"]`).uncheck()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    equal(await text(popup.locator("[data-blocked=x]")), `X: An X post takes 280 characters and this one has ${caption.length}. Shorten the caption.`, "why X is left out")
    equal(await popup.locator("[data-confirm]").evaluateAll(all => all.map(one => one.dataset.confirm)), ["tiktok"], "the platforms to confirm")
    equal((await t.app.ask("jobs")).map(job => [job.platform, job.state]), [["tiktok", "queued"]], "the jobs ReelForge made")
    await popup.getByRole("button", { name: "Cancel" }).click()
    await until("the job to be cancelled", async () => (await t.app.ask("jobs")).every(job => job.state === "cancelled"))
  },

  async "a ReelForge that speaks a newer version of the link, and the demo: the pair page and the popup say which"(t) {
    await t.app.ask("protocol", { number: 2 })
    const page = await t.browser.context.newPage()
    await page.goto(await t.app.pairUrl())
    await page.waitForFunction(() => document.documentElement.dataset.rfPair)
    equal(await page.evaluate(() => document.documentElement.dataset.rfPair), "error:extension_outdated", "what the extension told the pair page")
    await until("the page to say what to do", async () => (await text(page.locator("#notice"))).startsWith("This extension is older than your ReelForge."))
    equal(await t.app.ask("paired"), [], "browsers connected")
    const popup = await t.browser.popup()
    await until("the popup to say so", () => popup.locator("[data-screen=extension_outdated]").count())
    await t.app.ask("protocol", { number: 1 })
    await t.app.ask("demo", { on: true })
    await popup.getByRole("button", { name: "Look again" }).click()
    await until("the popup to say it is the demo", () => popup.locator("[data-screen=demo]").count())
  },

  async "posts that waited more than ten minutes are not opened, and the page says so"(t) {
    const film = await ready(t)
    const made = await t.app.ask("batch", { path: film.path, platforms: ["tiktok", "x"] })
    for (const id of Object.values(made.jobs)) await t.app.ask("age", { job: id, minutes: 11, column: "created_at" })
    const page = await t.browser.post(made.post)
    equal(await page.evaluate(() => document.documentElement.dataset.rfBatchState), "error:not_claimed", "what the extension told the page")
    await until("the page to say so", async () => (await text(page.locator("#notice"))).includes("waited more than ten minutes"))
    equal((await t.app.ask("jobs")).map(job => job.state), ["expired", "expired"], "the jobs")
    equal(t.browser.tabs(HOST.tiktok).length + t.browser.tabs(HOST.x).length, 0, "upload tabs")
    equal(await t.app.ask("uploads"), [], "upload-log lines")
  },
}
