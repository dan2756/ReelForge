// A stand-in for the ReelForge app's local server, for the extension's tests.
//
// It implements section 5 of the posting contract (the HTTP API the extension talks to) and nothing more, so
// that the real server can take its place. What it keeps is in memory: one channel, a list of videos, the job
// ledger with the contract's state rules, paired browsers, and a log of every request for the tests to read.
//
// It answers the way dashboard/extension_api.py and dashboard/posting.py do: the same refusals in the same
// order and the same words, the same reasons a platform is blocked for, the same cleaning of what the
// extension reports. real.test.mjs holds the two together: it asks both the same hundred questions and
// compares the answers, and it runs the extension against the real server.
import http from "node:http"
import crypto from "node:crypto"

export const EXTENSION_ID = "pikflpmjggoledmjbgbkjigbnincplgb"
export const EXTENSION_ORIGIN = `chrome-extension://${EXTENSION_ID}`
export const PORTS = Array.from({ length: 10 }, (_, i) => 47821 + i)

const ORDER = ["queued", "claimed", "opening", "uploading", "filling", "ready", "posting", "posted"]
const UNDER_WAY = ORDER.slice(0, -1)
const ENDED = ["failed", "cancelled", "expired", "posted"]
const FREE = ["failed", "cancelled", "expired"]
const FROM_EXTENSION = ["opening", "uploading", "filling", "ready", "posted", "failed", "cancelled", "unknown"]
// The id each platform's stats reader uses, and the sites a post's link may be on (as in the real server).
const POST_ID = { tiktok: /^\d{6,}$/, instagram: /^\d{6,}$/, x: /^\d{6,}$/, youtube: /^[\w-]{11}$/ }
const POST_HOSTS = { tiktok: ["tiktok.com"], youtube: ["youtube.com", "youtu.be"], instagram: ["instagram.com"], x: ["x.com", "twitter.com"] }
const UPLOAD_PAGES = {
  tiktok: "https://www.tiktok.com/tiktokstudio/upload",
  youtube: "https://www.youtube.com/upload",
  instagram: "https://www.instagram.com/",
  x: "https://x.com/compose/post",
}
const NAMES = { tiktok: "TikTok", youtube: "YouTube", instagram: "Instagram", x: "X" }

const sha256 = data => crypto.createHash("sha256").update(data).digest("hex")
const id = prefix => prefix + crypto.randomBytes(8).toString("hex")
const now = () => new Date().toISOString().replace(/\.\d+Z$/, "+00:00")

export class FakeApp {
  constructor({ profile = { key: "main", name: "Rendered Wrong" }, handles = {} } = {}) {
    this.appId = crypto.randomUUID()
    this.profile = profile
    this.version = "2.3.0"
    this.protocol = 1
    this.demo = false
    this.handles = { tiktok: "@fixtureuser", youtube: "@fixtureuser", instagram: "@fixtureuser", x: "@fixtureuser", ...handles }
    this.paired = []            // { install, name, version, token_sha256, paired_at, last_seen }
    this.secrets = new Map()    // pairing secret -> when it stops working
    this.pageKeys = new Set()
    this.videos = []
    this.jobs = new Map()
    this.posted = new Map()     // "<video>:<platform>" -> url, for posts the stats reader has already linked
    this.pressUnlocked = new Set()
    this.uploadPages = { ...UPLOAD_PAGES }
    this.selectors = { version: 1, platforms: {} }
    this.delay = { file: 0 }    // milliseconds added to every file request
    this.busy = 0               // how many reports to answer with 503 (the database is busy), before taking one
    this.fileChanged = false    // the video was replaced after the job was made
    this.corrupt = false        // the bytes on disk no longer match the job's hash
    this.log = []               // every request, in order
    this.heartbeats = []
    this.server = null
    this.port = null
  }

  // ---- what a test sets up -------------------------------------------------------------------------------

  addVideo({ name = "ep32_hail.mp4", title = "What if hail doubled every 2 seconds?", bytes = crypto.randomBytes(300 * 1024), review = "approved", postable = true, why = "", text } = {}) {
    const video = {
      id: sha256(bytes), name, title, bytes, size: bytes.length, review, postable, why,
      text: text || {
        tiktok: { caption: "What if hail doubled every 2 seconds? #fyp #whatif #hail" },
        instagram: { caption: "What if hail doubled every 2 seconds? #whatif #hail" },
        youtube: { title: "What if hail doubled every 2 seconds?", description: "#whatif #hail #shorts" },
        x: { text: "What if hail doubled every 2 seconds? It gets out of hand fast." },
      },
    }
    this.videos.unshift(video)
    return video
  }

  pairUrl() {
    const secret = crypto.randomBytes(24).toString("base64url")
    this.secrets.set(secret, Date.now() + 10 * 60 * 1000)
    const key = crypto.randomBytes(16).toString("hex")
    this.pageKeys.add(key)
    this.pairSecret = secret
    return `http://127.0.0.1:${this.port}/pair?k=${key}`
  }

  postUrl(batch) {
    const key = crypto.randomBytes(16).toString("hex")
    this.pageKeys.add(key)
    return `http://127.0.0.1:${this.port}/post/${batch}?k=${key}`
  }

  // The person pressing Post in the window: one batch, one queued job per platform.
  createBatch(video, platforms, { press = false } = {}) {
    const batch = id("pb_")
    const jobs = []
    const blocked = []
    for (const platform of platforms) {
      const live = [...this.jobs.values()].find(job => job.video === video && job.platform === platform && !FREE.includes(job.state))
      const url = this.posted.get(`${video.id}:${platform}`)
      const length = platform === "x" ? video.text.x.text.length : 0
      if (length > 280) {
        blocked.push({ platform, reason: "too_long", message: `An X post takes 280 characters and this one has ${length}. Shorten the caption.` })
      } else if (live) {
        const [reason, message] = live.state === "posted" ? ["posted", "Already posted with ReelForge's help."]
          : live.state === "unknown" ? ["unknown", "ReelForge doesn't know whether the last try posted. Check your profile there, then say so on the Publish tab."]
            : ["in_progress", "A post of this video is already under way there. Cancel it first."]
        blocked.push({ platform, reason, message, job: live.id, state: live.state, ...(live.url ? { url: live.url } : {}) })
      } else if (url) blocked.push({ platform, reason: "spotted", message: "Already posted: ReelForge found this video's post there.", url })
      else {
        const job = {
          id: id("pj_"), batch, platform, via: "extension", state: "queued", step: null, progress: null, detail: null,
          error_code: null, error_message: null, post_id: null, url: null, press: press && platform !== "x", review: video.review, video,
          text: video.text[platform], claimed_by: null, pressed: false, created: Date.now(), updated_at: now(), events: [], strategies: [],
        }
        this.jobs.set(job.id, job)
        jobs.push(job)
      }
    }
    return { batch, jobs, blocked }
  }

  job(platform) {
    return [...this.jobs.values()].reverse().find(job => job.platform === platform) || null
  }

  requests(method, pattern) {
    return this.log.filter(entry => entry.method === method && pattern.test(entry.path))
  }

  // ---- the server ----------------------------------------------------------------------------------------

  async start(port) {
    this.server = http.createServer((request, response) => this.handle(request, response).catch(error => {
      if (!response.headersSent) response.writeHead(500)
      response.end(String(error))
    }))
    for (const candidate of port ? [port] : PORTS) {
      const listening = await new Promise(resolve => {
        this.server.once("error", () => resolve(false))
        this.server.listen(candidate, "127.0.0.1", () => resolve(true))
      })
      if (listening) {
        this.port = candidate
        return this
      }
    }
    throw new Error("no free port between 47821 and 47830: is ReelForge, or another test run, using them?")
  }

  async stop() {
    if (!this.server) return
    this.server.closeAllConnections()
    await new Promise(resolve => this.server.close(resolve))
    this.server = null
  }

  view(job, { text = true } = {}) {
    const out = {
      id: job.id, batch: job.batch, platform: job.platform, via: job.via, state: job.state, step: job.step, progress: job.progress,
      detail: job.detail, error_code: job.error_code, error_message: job.error_message, post_id: job.post_id, url: job.url,
      press: job.press, review: job.review, video: { name: job.video.name, size: job.video.size, sha256: job.video.id },
      text: job.text, upload_url: this.uploadPages[job.platform], handle: this.handles[job.platform] || "", updated_at: job.updated_at,
    }
    if (!text) delete out.text
    return out
  }

  // Whether "press Post for me" may be offered for a platform: never on X, and elsewhere once a post made with
  // the extension's help is up there (another one than `job`). A test can also say so itself (pressUnlocked).
  unlocked(platform, job = null) {
    return platform !== "x" && (this.pressUnlocked.has(platform)
      || [...this.jobs.values()].some(other => other !== job && other.platform === platform && other.via === "extension" && other.state === "posted"))
  }

  // The ledger's rules for what a browser reports, as dashboard/posting.py has them (allowed and _write):
  // forwards only, no way out of an ended job, "posting" only through the press, and a state said again is a
  // sign of life that may bring its step. False: not a move this job can make.
  move(job, body) {
    let state = body.state
    if (!FROM_EXTENSION.includes(state)) return false
    // Once the press was granted, the browser cannot call the job over: that is "not sure", with its reason.
    if (job.state === "posting" && (state === "failed" || state === "cancelled")) state = "unknown"
    const again = state === job.state
    if (!again) {
      // An ended job stays ended, and "not sure" is settled only by the person or a stats read, in ReelForge.
      if (ENDED.includes(job.state) || job.state === "unknown") return false
      if (state === "unknown" ? job.state === "queued" : state !== "failed" && state !== "cancelled" && ORDER.indexOf(state) < ORDER.indexOf(job.state)) return false
    }
    // What the extension says is cleaned before it is kept, as the real server cleans it.
    const progress = typeof body.progress === "number" && Number.isFinite(body.progress) ? Math.max(0, Math.min(body.progress, 1)) : null
    const code = line(body.error_code, 40)
    const said = {
      step: line(body.step, 40), progress, detail: line(body.detail, 300), error_code: code && /^[a-z_]+$/.test(code) ? code : null,
      error_message: line(body.error_message, 300), post_id: line(body.post_id, 80), url: postUrl(job.platform, body.url),
    }
    let given = Object.fromEntries(Object.entries(said).filter(([, value]) => value !== null))
    const only = names => Object.fromEntries(Object.entries(given).filter(([name]) => names.includes(name)))
    if (again) {
      // Over: only a word on why can follow. Up: only its id and link. And nothing replaces what is there.
      if (FREE.includes(state)) given = only(["detail"])
      else if (state === "posted") given = only(["post_id", "url"])
      if ((FREE.includes(state) || state === "posted") && Object.entries(given).every(([name, value]) => job[name] === value)) return true
      given = Object.fromEntries(Object.entries(given).filter(([name]) => ["step", "progress", "detail"].includes(name) || !job[name]))
    } else {
      // A new state starts with a clean step, progress and detail.
      Object.assign(job, { state, step: null, progress: null, detail: null })
    }
    if (given.post_id !== undefined && !POST_ID[job.platform].test(given.post_id)) delete given.post_id
    Object.assign(job, given, { updated_at: now() })
    if (line(body.strategy, 200)) job.strategies.push(line(body.strategy, 200))
    job.events.push({ state, step: given.step ?? null, detail: given.detail ?? null, error_code: given.error_code ?? null })
    return true
  }

  async handle(request, response) {
    const url = new URL(request.url, `http://127.0.0.1:${this.port}`)
    const path = url.pathname
    const method = request.method
    const origin = request.headers.origin
    const entry = { method, path, origin: origin || null, token: Boolean(request.headers["x-reelforge-token"]), status: 0, body: null }
    this.log.push(entry)
    // The extension's own origin is named in every answer to it. Never "*".
    let cors = {}
    const send = (status, data, headers = {}) => {
      entry.status = status
      const body = typeof data === "string" || Buffer.isBuffer(data) ? data : JSON.stringify(data)
      response.writeHead(status, { "Content-Type": typeof data === "string" ? "text/html; charset=utf-8" : "application/json; charset=utf-8", "Content-Length": Buffer.byteLength(body), "Cache-Control": "no-store", ...cors, ...headers })
      response.end(body)
    }
    const fail = (status, error, message) => send(status, { error, message })
    const nothing = () => fail(404, "not_found", "ReelForge has nothing at that address.")

    // The rules for every request, in the real server's order.
    const own = `127.0.0.1:${this.port}`
    if (request.headers.host !== own) return fail(421, "wrong_host", `ReelForge answers only as http://${own}.`)
    if (["cross-site", "same-site"].includes(request.headers["sec-fetch-site"])) return fail(403, "wrong_origin", "Web pages can't talk to ReelForge.")
    if (origin === EXTENSION_ORIGIN) cors = { "Access-Control-Allow-Origin": origin, Vary: "Origin" }
    else if (origin && origin !== `http://${own}`) return fail(403, "wrong_origin", "Web pages can't talk to ReelForge.")
    if (method === "OPTIONS") {
      if (origin !== EXTENSION_ORIGIN) return fail(403, "wrong_origin", "Web pages can't talk to ReelForge.")
      return send(204, "", { "Content-Type": "text/plain", "Access-Control-Allow-Headers": "X-ReelForge-Token, Content-Type", "Access-Control-Allow-Methods": "GET, POST", "Access-Control-Max-Age": "600" })
    }
    if (method !== "GET" && method !== "POST") return fail(405, "method", "ReelForge takes GET and POST only.")
    let body = {}
    if (method === "POST") {
      const length = request.headers["content-length"] || ""
      const refuse = (...why) => {
        request.resume()  // what was sent is let through, so the answer that says why arrives
        return fail(...why)
      }
      if ((request.headers["content-type"] || "").split(";")[0].trim().toLowerCase() !== "application/json") return refuse(415, "not_json", "ReelForge takes JSON only.")
      if (!/^\d+$/.test(length)) return refuse(411, "no_length", "ReelForge needs a Content-Length.")
      if (Number(length) > 64 * 1024) return refuse(413, "too_large", "That is more than ReelForge takes in one request (64 KiB).")
      const chunks = []
      for await (const chunk of request) chunks.push(chunk)
      try {
        body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}")
      } catch {
        body = null
      }
      if (!body || typeof body !== "object" || Array.isArray(body)) return fail(400, "bad_json", "ReelForge couldn't read that as a JSON object.")
      entry.body = body
    }

    const token = request.headers["x-reelforge-token"]
    const browser = token ? this.paired.find(p => crypto.timingSafeEqual(Buffer.from(p.token_sha256), Buffer.from(sha256(token)))) : null

    if (method === "GET" && path === "/v1/hello") {
      const answer = { app: "reelforge", protocol: this.protocol, version: this.version, app_id: this.appId, profile: this.profile, demo: this.demo, paired: Boolean(browser) && !this.demo }
      // The proof that this is the ReelForge a connected browser's token came from: an HMAC over the nonce it
      // sent, this port and this app's id, keyed with the token's SHA-256.
      const known = this.paired.find(p => p.install === url.searchParams.get("install"))
      const nonce = url.searchParams.get("nonce") || ""
      if (known && /^[0-9a-f]{32}$/.test(nonce) && !this.demo) {
        answer.proof = crypto.createHmac("sha256", Buffer.from(known.token_sha256, "hex")).update(`reelforge hello\n${nonce}\n${this.port}\n${this.appId}`).digest("hex")
      }
      return send(200, answer)
    }
    if (this.demo) return fail(403, "demo", "ReelForge is showing its demo, which never posts.")

    const unpaired = () => fail(401, "unpaired", "This browser isn't connected to ReelForge. Connect it from the Publish tab, or with: reelforge extension pair")
    const badKey = () => fail(403, "bad_key", "This page is too old to follow ReelForge. Start again from ReelForge.")
    // Which side to update, when the two speak another version of the link.
    const older = theirs => (Number.isInteger(theirs) && theirs > this.protocol ? "This ReelForge is older than the browser extension. Update ReelForge on its Updates tab."
      : "The browser extension is older than this ReelForge. Press Reload on its card on your browser's extensions page.")
    const key = method === "POST" ? body.k : url.searchParams.get("k")
    const route = /^\/v1\/jobs\/(pj_[0-9a-f]{16})\/(claim|file|event|press)$/.exec(path)
    const batch = /^\/v1\/batch\/(pb_[0-9a-f]{16})$/.exec(path)

    if (method === "GET") {
      // Local pages. Each carries a page key; no cookies.
      if (path === "/pair") {
        if (!this.pageKeys.has(key)) return badKey()
        return send(200, localPage({ page: "pair", protocol: this.pageProtocol ?? this.protocol, port: this.port, secret: this.pairSecret }))
      }
      const post = /^\/post\/(pb_[0-9a-f]{16})$/.exec(path)
      if (post) {
        if (!this.pageKeys.has(key)) return badKey()
        return send(200, localPage({ page: "post", protocol: this.protocol, port: this.port, batch: post[1] }))
      }
      // Another program's page that happens to live under /pair: no data-rf-page, so the extension ignores it.
      if (path === "/pairing-notes") return send(200, "<!doctype html><html><head><title>Someone else's page</title></head><body>pair*</body></html>")
      const pageBatch = /^\/v1\/page\/batch\/(pb_[0-9a-f]{16})$/.exec(path)
      if (pageBatch) {
        if (!this.pageKeys.has(key)) return badKey()
        const jobs = [...this.jobs.values()].filter(job => job.batch === pageBatch[1])
        const last = this.paired.map(p => p.last_seen).sort().pop()
        return send(200, {
          batch: pageBatch[1], jobs: jobs.map(job => this.view(job, { text: false })),
          video: jobs[0] ? { name: jobs[0].video.name, size: jobs[0].video.size, sha256: jobs[0].video.id } : null,
          extension: { paired: this.paired.length > 0, seen_seconds_ago: last ? Math.round((Date.now() - last) / 1000) : null },
        })
      }
      if (path === "/v1/page/pair") {
        if (!this.pageKeys.has(key)) return badKey()
        return send(200, { state: this.paired.length ? "paired" : "waiting", name: this.paired[0]?.name || "" })
      }
      // Everything else a GET can ask for needs the token.
      if (path !== "/v1/state" && path !== "/v1/selectors" && !batch && route?.[2] !== "file") return nothing()
      if (!browser) return unpaired()
      browser.last_seen = Date.now()
      if (path === "/v1/selectors") return send(200, this.selectors)
      if (batch) {
        const jobs = [...this.jobs.values()].filter(job => job.batch === batch[1])
        if (!jobs.length) return fail(404, "no_batch", "ReelForge has no such batch of posts.")
        return send(200, { batch: batch[1], jobs: jobs.map(job => this.view(job)) })
      }
      if (path === "/v1/state") {
        const videos = this.videos.slice(0, 20).map(video => {
          const platforms = {}
          // Only the platforms connected in ReelForge. A live job has the say; one that ended without a post
          // never hides a post the stats found.
          for (const platform of Object.keys(UPLOAD_PAGES).filter(key => this.handles[key])) {
            const mine = [...this.jobs.values()].reverse().filter(j => j.video === video && j.platform === platform)
            const job = mine.find(j => !FREE.includes(j.state)) || mine[0]
            const spotted = this.posted.get(`${video.id}:${platform}`)
            const own = job && (!FREE.includes(job.state) || !spotted)
            platforms[platform] = own ? { state: job.state, job: job.id, url: job.url || spotted || null } : { state: spotted ? "spotted" : "none", job: null, url: spotted || null }
          }
          return { id: video.id, name: video.name, size: video.size, title: video.title, review: video.review, postable: video.postable, why: video.why, platforms }
        })
        return send(200, {
          profile: this.profile,
          platforms: Object.keys(UPLOAD_PAGES).map(platform => ({
            key: platform, name: NAMES[platform], handle: this.handles[platform] || "", connected: Boolean(this.handles[platform]),
            via: "extension", press_unlocked: this.unlocked(platform),
          })),
          // The batches with a job still under way, oldest first.
          videos, active: [...new Set([...this.jobs.values()].filter(job => UNDER_WAY.includes(job.state)).map(job => job.batch))],
        })
      }
    } else {
      if (path === "/v1/pair/claim") {
        if (origin !== EXTENSION_ORIGIN) return fail(403, "wrong_origin", "Only ReelForge's own browser extension can connect.")
        if (body.protocol !== this.protocol) return fail(426, "protocol", older(body.protocol))
        if (typeof body.install !== "string" || !body.install || typeof body.secret !== "string") return fail(400, "bad_request", "The extension didn't say which browser it is.")
        const until = this.secrets.get(body.secret)
        if (!until) return fail(403, "bad_secret", "This pairing page was used already. Start again from ReelForge.")
        if (Date.now() > until) return fail(410, "expired", "This pairing page is more than ten minutes old. Start again from ReelForge.")
        this.secrets.delete(body.secret)
        const fresh = crypto.randomBytes(32).toString("base64url")
        this.paired = this.paired.filter(p => p.install !== body.install)
        this.paired.push({ install: body.install, name: body.name, version: body.extension_version, token_sha256: sha256(fresh), paired_at: Date.now(), last_seen: Date.now() })
        return send(200, { token: fresh, app_id: this.appId })
      }
      if (path === "/v1/page/manual") {
        if (!this.pageKeys.has(key)) return badKey()
        const job = [...this.jobs.values()].find(j => j.batch === body.batch && j.platform === body.platform)
        if (!job) return fail(404, "not_found", "ReelForge has no such post.")
        if (!["queued", "expired", "failed", "cancelled"].includes(job.state)) return fail(409, "not_manual", "That post is already under way in your browser. Finish it there, or cancel it on ReelForge's Publish tab first.")
        Object.assign(job, { state: "cancelled", detail: "posted by hand", updated_at: now() })
        return send(200, { ok: true })
      }
      // Everything else a POST can ask for needs the token. Who is asking is the token's word, never the body's.
      if (!["/v1/seen", "/v1/batches", "/v1/unpair"].includes(path) && !(route && route[2] !== "file")) return nothing()
      if (!browser) return unpaired()
      browser.last_seen = Date.now()
      if (path === "/v1/unpair") {
        this.paired = this.paired.filter(p => p !== browser)
        return send(200, { ok: true })
      }
      if (path === "/v1/seen") {
        this.heartbeats.push(body)  // (what it said is kept before the answer, as the real server keeps it)
        if (body.protocol !== this.protocol) return fail(426, "protocol", older(body.protocol))
        return send(200, { ok: true, selectors_version: this.selectors.version })
      }
      if (path === "/v1/batches") {
        if (typeof body.video !== "string" || !Array.isArray(body.platforms)) return fail(400, "bad_request", "Which video, and where to?")
        const ticked = body.platforms.filter(platform => typeof platform === "string" && this.handles[platform])
        if (!ticked.length) return fail(400, "no_platform", "Pick a platform that is connected in ReelForge.")
        const video = this.videos.find(v => v.id === body.video)
        if (!video) return fail(404, "no_video", "That video isn't on ReelForge's Publish list any more.")
        // A video that is not approved makes no batch: every platform is blocked, with the review's own sentence.
        if (!video.postable) {
          return send(200, { batch: null, jobs: [], blocked: ticked.map(platform => ({ platform, reason: "review", message: video.why || "This video cannot be posted." })) })
        }
        const made = this.createBatch(video, ticked, { press: body.press === true })
        // With every platform blocked there is no batch to speak of.
        const none = made.jobs.length === 0
        return send(none ? 200 : 201, { batch: none ? null : made.batch, jobs: made.jobs.map(job => this.view(job)), blocked: made.blocked })
      }
    }

    // What is left is a job's own routes: claim, file, event and press.
    const job = this.jobs.get(route[1])
    if (!job) return fail(404, "not_found", "ReelForge has no such post.")
    const action = route[2]
    const over = ENDED.includes(job.state) || job.state === "unknown"

    if (action === "claim") {
      // The same browser asking again before it has started gets the same answer: its first may have been lost.
      if (job.state === "claimed" && job.claimed_by === browser.install) return send(200, this.view(job))
      if (job.via !== "extension") return fail(409, "not_claimable", "This post doesn't go through the extension.")
      if (over) return fail(409, "not_claimable", "This post is over.")
      if (job.state !== "queued") return fail(409, "already_claimed", "This post has been taken already.")
      if (Date.now() - job.created >= 10 * 60 * 1000) {
        Object.assign(job, { state: "expired", detail: "not picked up in time", updated_at: now() })
        return fail(409, "not_claimable", "This post waited too long to be picked up. Start it again.")
      }
      Object.assign(job, { state: "claimed", claimed_by: browser.install, updated_at: now() })
      return send(200, this.view(job))
    }
    if (action === "file") {
      if (job.claimed_by !== browser.install) return fail(409, "not_yours", "This post isn't this browser's: claim it first.")
      if (over) return fail(409, "ended", "This post is over.")
      if (this.fileChanged) return fail(409, "changed", `${job.video.name} was replaced or removed after this post was started. Nothing was posted: start it again from ReelForge.`)
      if (this.delay.file) await new Promise(resolve => setTimeout(resolve, this.delay.file))
      const bytes = this.corrupt ? Buffer.alloc(job.video.size, 7) : job.video.bytes
      const headers = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", ETag: `"${job.video.id}"` }
      entry.range = request.headers.range || null
      // "bytes=0-99", "bytes=100-" or "bytes=-100". Anything else, or no Range at all: the whole file.
      const range = /^bytes=(\d*)-(\d*)$/.exec((request.headers.range || "").trim())
      if (!range || (!range[1] && !range[2])) return send(200, bytes, headers)
      const from = range[1] ? Number(range[1]) : Math.max(bytes.length - Number(range[2]), 0)
      const to = range[1] ? Math.min(range[2] === "" ? bytes.length - 1 : Number(range[2]), bytes.length - 1) : bytes.length - 1
      if (from > to || (!range[1] && !Number(range[2]))) return send(416, { error: "range", message: "That part of the video doesn't exist." }, { "Content-Range": `bytes */${bytes.length}` })
      return send(206, bytes.subarray(from, to + 1), { ...headers, "Content-Range": `bytes ${from}-${to}/${bytes.length}` })
    }
    if (action === "event") {
      const state = body.state
      if (!FROM_EXTENSION.includes(state)) {
        return fail(409, "bad_transition", line(state, 40) ? `The extension can't make a post ${line(state, 40)}.` : "The extension didn't say where the post stands.")
      }
      // The database is busy: "try again in a moment", which the extension has to do by itself.
      if (this.busy > 0) {
        this.busy--
        return fail(503, "busy", "ReelForge's database is busy (database is locked). Try again in a moment.")
      }
      // Only the browser that took a job reports on it. One exception: a job still waiting is nobody's yet,
      // and any connected browser may call it off.
      if (job.claimed_by !== browser.install && !(job.state === "queued" && state === "cancelled")) return fail(409, "not_yours", "Another browser took this post.")
      const was = job.state
      if (!this.move(job, body)) return fail(409, "bad_transition", `A post that is ${was} can't become ${state}.`)
      return send(200, this.view(job))
    }
    // The press. In the ledger's order: whose job it is, allowed at all, then whether this is the moment.
    const no = (error, message) => send(409, { ok: false, error, message })
    if (job.claimed_by !== browser.install) return no("not_yours", "Another browser took this post.")
    if (!job.press || !this.unlocked(job.platform, job)) return no("press_not_allowed", "Press Post yourself: ReelForge isn't allowed to for this post.")
    if (job.pressed) return no("already_pressed", "Post was already pressed for this one.")
    // The step "press" under any other state is the extension's word that the person pressed Post in the tab.
    if (job.events.some(event => event.step === "press")) return no("already_pressed", "You pressed Post for this one yourself already.")
    if (job.state !== "ready") return no("not_ready", "This post isn't ready yet.")
    Object.assign(job, { pressed: true, state: "posting", step: "press", progress: null, detail: null, updated_at: now() })
    job.events.push({ state: "posting", step: "press", detail: null, error_code: null })
    return send(200, { ok: true })
  }
}

// A string from outside as one line of at most `limit` characters, without control characters. Null for
// anything that is not a string, or is empty (extension_api.line).
function line(value, limit) {
  if (typeof value !== "string") return null
  const text = [...value].filter(c => !/\p{Cc}/u.test(c) || /\s/.test(c)).join("").split(/\s+/).filter(Boolean).join(" ")
  return text.slice(0, limit) || null
}

// A post's link is kept only as an https address on the platform's own site, in the plainest shape. Null for
// anything else.
function postUrl(platform, value) {
  const url = line(value, 500)
  if (!url || !/^https:\/\/[a-z0-9.-]+(\/[^\s\\]*)?$/.test(url)) return null
  const host = url.slice(8).split("/")[0]
  return POST_HOSTS[platform].some(site => host === site || host.endsWith("." + site)) ? url : null
}

// ReelForge's two local pages, as far as the extension is concerned: the data attributes on <html>, and a
// page script that writes down the events the extension sends it.
function localPage({ page, protocol, port, secret, batch }) {
  const attributes = [`data-rf-page="${page}"`, `data-rf-protocol="${protocol}"`, `data-rf-port="${port}"`]
  if (secret) attributes.push(`data-rf-secret="${secret}"`)
  if (batch) attributes.push(`data-rf-batch="${batch}"`)
  return `<!doctype html>
<html ${attributes.join(" ")}>
<head><meta charset="utf-8"><title>ReelForge</title>
<script>
  window.heard = []
  for (const name of ["reelforge:extension", "reelforge:pair", "reelforge:batch", "reelforge:press"]) {
    window.addEventListener(name, event => window.heard.push({ name, detail: event.detail }))
  }
</script></head>
<body><h1>ReelForge</h1><p>${page === "pair" ? "Connecting your browser" : "Posting"}</p>
${page === "post" ? '<button type="button" id="press-yes" data-rf-press>Yes, press Post for me</button>' : ""}</body>
</html>`
}
