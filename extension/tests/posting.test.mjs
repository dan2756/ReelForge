// A batch from ReelForge's post page, through to "ready" on each platform, and the person pressing Post.
import { equal, ok, until, note, quickSelectors } from "./harness.mjs"

const HOST = { tiktok: "www.tiktok.com", youtube: "studio.youtube.com", instagram: "www.instagram.com", x: "x.com" }
const FINAL = {
  tiktok: "[data-e2e=post_video_button]", youtube: "#done-button", x: "[data-testid=tweetButton]",
  instagram: "[role=dialog] [role=button]:text-is('Share')",
}

// Starts one platform's job from the post page and waits until the app has it as ready.
export async function toReady({ app, browser }, platform, video, options = {}) {
  if (!app.paired.length) equal(await browser.pair(app), "ok", "pairing")
  const made = app.createBatch(video, [platform], options)
  const post = await browser.post(app, made.batch)
  equal(await post.evaluate(() => document.documentElement.dataset.rfBatchState), "started", "post page state")
  const tab = await browser.tab(HOST[platform])
  const job = made.jobs[0]
  await until(() => `the ${platform} job to be ready (it is ${job.state}: ${job.error_message || job.step})`,
    () => ["ready", "posting", "posted", "failed", "unknown"].includes(job.state), 30000)
  return { job, tab, post, batch: made.batch }
}

async function attachedAndFilled({ app, browser, video }, platform, read) {
  const film = video()
  const { job, tab } = await toReady({ app, browser }, platform, film)
  equal(job.state, "ready", `${platform} state (${job.error_message})`)
  const seen = await tab.evaluate(() => ({ files: fixture.files, sha256: fixture.sha256 }))
  equal(seen.files.length, 1, "files the page was given")
  equal(seen.files[0], { name: film.name, size: film.size, type: "video/mp4", trusted: false }, "the file the page was given")
  equal(seen.sha256, film.id, "SHA-256 the page computed over the file")
  equal(await read(tab), film.text[platform], "text in the page's boxes")
  // Nothing was pressed: the page is filled in and waiting for the person.
  equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [0, false], "presses of the final button")
  equal(app.requests("POST", /\/press$/).length, 0, "press requests")
  const said = await note(tab)
  equal(said.line, "Check it, then press Post", "the note on the page")
  const button = tab.locator(FINAL[platform])
  ok((await button.evaluate(el => getComputedStyle(el).outlineColor)) === "rgb(232, 33, 58)", "final button outlined in ReelForge's red")
  ok(await button.evaluate(el => { const box = el.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight }), "final button scrolled into view")
  equal(job.events.map(event => event.state).filter((state, i, all) => state !== all[i - 1]), ["opening", "uploading", "filling", "ready"], "states the app was told")
  return { job, tab, film, button }
}

const box = selector => tab => tab.evaluate(sel => {
  // The YouTube copy keeps its page in a shadow root.
  const root = document.querySelector("ytcp-app")?.shadowRoot || document
  return root.querySelector(sel).innerText.trim()
}, selector)

export const tests = {
  async "TikTok: file attached, caption filled, stops at Post, then sees the person post"(t) {
    const { job, tab, button } = await attachedAndFilled(t, "tiktok", async tab => ({ caption: await box(".public-DraftEditor-content")(tab) }))
    for (const step of ["open_tab", "signed_in", "find_input", "attach", "fill_caption", "upload", "ready"]) {
      ok(job.events.some(event => event.step === step), `step ${step} was reported`)
    }
    await button.click()
    await until("posted", () => job.state === "posted")
    equal(await tab.evaluate(() => fixture.text), job.text.caption, "what TikTok posted")
    equal((await note(tab)).line, "Posted", "the note after posting")
    // An assisted post that went through is what unlocks "press Post for me" for the platform.
    ok(t.app.pressUnlocked.has("tiktok"), "press unlocked after an assisted post")
  },

  async "YouTube: follows the redirect into Studio, fills title and description, leaves the audience and visibility alone"(t) {
    const { job, tab, button } = await attachedAndFilled(t, "youtube", async tab => ({
      title: await box("#title-textarea #textbox")(tab), description: await box("#description-textarea #textbox")(tab),
    }))
    ok(tab.url().startsWith("https://studio.youtube.com/channel/"), "the tab ended up in Studio")
    equal(await tab.evaluate(() => fixture.radioClicks), 0, "clicks on Made for kids and visibility")
    for (const step of ["fill_title", "fill_caption", "audience", "visibility"]) ok(job.events.some(event => event.step === step), `step ${step} was reported`)
    ok(job.events.some(event => event.detail === "Visibility: Private, as saved on your channel"), "the saved visibility was reported, not changed")
    // The video's id is on the page before it is published, and is passed on as soon as the page is ready.
    equal([job.post_id, job.url], ["FIXTUREvid01", "https://youtu.be/FIXTUREvid01"], "post id and url")
    await button.click()
    await until("posted", () => job.state === "posted")
    equal(await tab.evaluate(() => fixture.text), job.text, "what YouTube saved")
  },

  async "Instagram: opens Create, goes past the reels notice and two Next screens, fills the caption, stops at Share"(t) {
    const { job, tab, button } = await attachedAndFilled(t, "instagram", async tab => ({ caption: await box("[data-lexical-editor]")(tab) }))
    await button.click()
    await until("posted", () => job.state === "posted")
    equal(await tab.evaluate(() => fixture.text), job.text.caption, "what Instagram shared")
    equal(job.post_id, null, "no post id is made up for Instagram")
  },

  async "X: attaches before typing, uses the composer in the dialog, stops at Post"(t) {
    const { job, tab, button } = await attachedAndFilled(t, "x", async tab => ({ text: await box("[role=dialog] [data-testid=tweetTextarea_0]")(tab) }))
    equal(await tab.evaluate(() => document.querySelector("main [data-testid=tweetTextarea_0]").innerText), "", "the timeline's own composer was left alone")
    // This copy of X refuses typed input, so the text went in as a paste. The way that worked is reported.
    ok(job.strategies.join(",").includes("composer=testid-in-dialog+pasted"), `strategy reported (${job.strategies})`)
    await button.click()
    await until("posted", () => job.state === "posted")
    equal([job.post_id, job.url], ["1799999999999999999", "https://x.com/fixtureuser/status/1799999999999999999"], "status id and url from X's toast")
  },

  async "YouTube: a save that only closes the dialog still counts, because the person pressed Save first"({ app, site, browser, video }) {
    site.mode.youtube = "quiet_save"
    const { job, tab } = await toReady({ app, browser }, "youtube", video())
    await tab.locator("#done-button").click()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(job.post_id, "FIXTUREvid01", "the video id found before the dialog closed")
  },

  async "X: the composer closing after the person's press counts as posted, without a made-up id"({ app, site, browser, video }) {
    site.mode.x = "no_toast"
    const { job, tab } = await toReady({ app, browser }, "x", video())
    await tab.locator("[data-testid=tweetButton]").click()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal([job.post_id, job.url], [null, null], "post id and url")
  },

  async "a video of several pieces arrives whole"({ app, browser, video }) {
    const film = video(9 * 1024 * 1024 + 123)
    const { job, tab } = await toReady({ app, browser }, "tiktok", film)
    equal(job.state, "ready", `state (${job.error_message})`)
    equal(await tab.evaluate(() => [fixture.files[0].size, fixture.sha256]), [film.size, film.id], "size and hash on the page")
    const pieces = app.log.filter(entry => entry.path.endsWith("/file"))
    equal(pieces.map(entry => entry.range), ["bytes=0-4194303", "bytes=4194304-8388607", `bytes=8388608-${film.size - 1}`], "pieces asked for")
    // The worker fetches the video. The page's own origin never asks the app for anything.
    ok(app.log.every(entry => !entry.origin || entry.origin.startsWith("chrome-extension://") || entry.origin.startsWith("http://127.0.0.1")), "no request came from a platform page")
  },

  async "one batch for four platforms opens one tab each"({ app, browser, video }) {
    equal(await browser.pair(app), "ok", "pairing")
    const made = app.createBatch(video(), ["tiktok", "youtube", "instagram", "x"])
    await browser.post(app, made.batch)
    await until("all four ready", () => made.jobs.every(job => job.state === "ready"), 40000)
    for (const host of Object.values(HOST)) equal(browser.tabs(host).length, 1, `tabs on ${host}`)
    equal(app.requests("POST", /^\/v1\/jobs\/.*\/claim$/).filter(entry => entry.status === 200).length, 4, "claims")
  },

  async "TikTok's form inside a frame is found"({ app, site, browser, video }) {
    site.mode.tiktok = "iframe"
    const film = video()
    const { job, tab } = await toReady({ app, browser }, "tiktok", film)
    equal(job.state, "ready", `state (${job.error_message})`)
    const frame = tab.frames().find(f => f !== tab.mainFrame())
    equal(await frame.evaluate(() => [fixture.sha256, document.querySelector(".public-DraftEditor-content").innerText.trim()]), [film.id, film.text.tiktok.caption], "hash and caption inside the frame")
  },

  async "a renamed hook falls back to the next way of finding it, and says so"({ app, site, browser, video }) {
    site.mode.x = "no_testid"
    const { job } = await toReady({ app, browser }, "x", video())
    equal(job.state, "ready", `state (${job.error_message})`)
    ok(job.strategies.join(",").includes("file_input=accept-video-in-dialog"), `strategy reported (${job.strategies})`)
    ok(job.events.some(event => event.detail === "X's page has changed a little; still working"), "the app is told the page changed")
  },

  async "handing the same batch over again opens no second tab"({ app, browser, video }) {
    const film = video()
    const { job, post, batch } = await toReady({ app, browser }, "tiktok", film)
    equal(job.state, "ready", "state")
    await post.reload()
    await post.waitForFunction(() => document.documentElement.dataset.rfBatchState)
    const second = await browser.post(app, batch)
    equal(await second.evaluate(() => document.documentElement.dataset.rfBatchState), "started", "second page state")
    await new Promise(resolve => setTimeout(resolve, 700))
    equal(browser.tabs("www.tiktok.com").length, 1, "TikTok tabs")
    equal(app.requests("POST", /^\/v1\/jobs\/.*\/claim$/).length, 1, "claims asked for")
    equal(await browser.tabs("www.tiktok.com")[0].evaluate(() => fixture.files.length), 1, "files given to the page")
  },

  async "newer selectors from the app are used"({ app, site, browser, video }) {
    // The app's copy of selectors.json is newer and knows a hook the packed one does not: with it, the
    // "no_input" copy of TikTok (no file input the packed list can find) is no longer a dead end.
    site.mode.tiktok = "no_input"
    const packed = quickSelectors()
    packed.platforms.tiktok.signed_in.hooks = ["drop_zone"]
    packed.platforms.tiktok.hooks.drop_zone = { strategies: [{ id: "container", css: "div.upload-container" }] }
    app.selectors = packed
    equal(await browser.pair(app), "ok", "pairing")
    const made = app.createBatch(video(), ["tiktok"])
    await browser.post(app, made.batch)
    const job = made.jobs[0]
    await until("the job to fail at the input", () => job.state === "failed", 40000)
    // Signed-in was recognised through the hook only the newer list has; the input is still missing.
    ok(job.events.some(event => event.step === "find_input"), "got past the signed-in check with the newer selectors")
    equal(job.error_code, "hook_not_found", "error code")
  },
}
