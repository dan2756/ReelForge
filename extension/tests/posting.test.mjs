// A batch from ReelForge's post page, through to "ready" on each platform, and the person pressing Post.
import { equal, ok, until, sleep, note, noteOpenToPage, quickSelectors } from "./harness.mjs"

const HOST = { tiktok: "www.tiktok.com", youtube: "studio.youtube.com", instagram: "www.instagram.com", x: "x.com" }
const FINAL = {
  tiktok: "[data-e2e=post_video_button]", youtube: "#done-button", x: "[data-testid=tweetButton]",
  instagram: "[role=dialog] [role=button]:text-is('Share')",
}

// Starts one platform's job from the post page and waits until the app has it as ready. `yes`: the person
// presses the post page's "Yes, press Post for me" button.
export async function toReady({ app, browser }, platform, video, { yes = false, ...options } = {}) {
  if (!app.paired.length) equal(await browser.pair(app), "ok", "pairing")
  const made = app.createBatch(video, [platform], options)
  const post = await browser.post(app, made.batch, { yes })
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
    equal(await noteOpenToPage(tab), false, "the page's own scripts can read the note")
    // An assisted post that went through is what unlocks "press Post for me" for the platform.
    ok(t.app.unlocked("tiktok"), "press unlocked after an assisted post")
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
    equal([job.post_id, job.url], ["FIXTUREvid1", "https://youtu.be/FIXTUREvid1"], "post id and url")
    // YouTube's page does not show which account it is on, so nothing was compared: the note says to look.
    ok((await note(tab)).more.includes("ReelForge has @fixtureuser for this channel, and could not check the account here"), `the note about the account (${(await note(tab)).more})`)
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
    equal(job.post_id, "FIXTUREvid1", "the video id found before the dialog closed")
  },

  async "YouTube: publishing while the video is still going up is seen as posted, not as a page gone wrong"({ app, site, browser, video }) {
    site.mode.youtube = "slow"
    equal(await browser.pair(app), "ok", "pairing")
    const made = app.createBatch(video(), ["youtube"])
    await browser.post(app, made.batch)
    const job = made.jobs[0]
    // The extension is on the last screen, waiting for the upload. The person does not wait.
    await until(() => `the wait for the upload (it is ${job.state} ${job.step})`, () => job.step === "upload" && job.events.some(event => event.step === "visibility"), 30000)
    const tab = await browser.tab("studio.youtube.com")
    await tab.locator("#done-button").click()
    await until(() => `posted (it is ${job.state}: ${job.error_message})`, () => job.state === "posted" || job.state === "failed")
    equal([job.state, job.post_id], ["posted", "FIXTUREvid1"], "state and video id")
  },

  async "X: the composer closing after the person's press counts as posted, without a made-up id"({ app, site, browser, video }) {
    site.mode.x = "no_toast"
    const { job, tab } = await toReady({ app, browser }, "x", video())
    await tab.locator("[data-testid=tweetButton]").click()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal([job.post_id, job.url], [null, null], "post id and url")
  },

  async "TikTok: going to the list of posts without pressing Post is never called posted"(t) {
    // The page that lists a channel's posts is TikTok's sign that a post went out. The person can also just go
    // there. Without a press nobody can say, and "posted" would block the video and unlock Press Post for me.
    const { job, tab } = await toReady(t, "tiktok", t.video())
    await tab.goto("https://www.tiktok.com/tiktokstudio/content")
    await until(() => `the job to end (it is ${job.state})`, () => job.state !== "ready", 30000)
    equal([job.state, job.error_code], ["unknown", "unknown_outcome"], "state and code")
    ok(job.error_message.includes("did not see Post being pressed"), `message (${job.error_message})`)
    equal([await tab.evaluate(() => window.fixture?.clicks ?? 0), t.app.unlocked("tiktok")], [0, false], "presses, and whether the press was unlocked")
  },

  async "TikTok: the same page reached without a load, and without a press, is not posted either"(t) {
    const { job, tab } = await toReady(t, "tiktok", t.video())
    await tab.evaluate(() => {
      history.pushState({}, "", "/tiktokstudio/content")
      document.getElementById("app").innerHTML = "<h1>Posts</h1><p>No posts yet</p>"
    })
    await until(() => `the job to end (it is ${job.state})`, () => job.state !== "ready", 30000)
    equal([job.state, job.error_code, t.app.unlocked("tiktok")], ["unknown", "unknown_outcome", false], "state, code and the press lock")
    equal((await note(tab)).line, "Not sure it posted", "the note")
  },

  async "YouTube: Ctrl+Enter in a text box and a closed dialog are not a post"(t) {
    const { job, tab } = await toReady(t, "youtube", t.video())
    await tab.evaluate(() => document.querySelector("ytcp-app").shadowRoot.querySelector("#description-textarea #textbox").focus())
    await tab.keyboard.press("Control+Enter")
    await sleep(400)
    equal((await t.browser.jobRecord(job.id)).pressedByPerson === true, false, "a press was noticed")
    await tab.evaluate(() => document.querySelector("ytcp-app").shadowRoot.querySelector("ytcp-uploads-dialog").remove())
    await sleep(3500)
    equal([job.state, await tab.evaluate(() => fixture.clicks), t.app.unlocked("youtube")], ["ready", 0, false], "state, presses of Save and the press lock")
  },

  async "X: Ctrl+Enter with the post ready is the person's press"(t) {
    t.site.mode.x = "mute"
    const { job, tab } = await toReady(t, "x", t.video())
    await tab.locator("[role=dialog] [data-testid=tweetTextarea_0]").focus()
    await tab.keyboard.press("Control+Enter")
    await until("the press to be noticed", async () => (await t.browser.jobRecord(job.id)).pressedByPerson === true)
  },

  async "YouTube: a Next that has become Publish is not clicked, with nobody having asked for a press"(t) {
    t.site.mode.youtube = "next_publishes"
    const { job, tab } = await toReady(t, "youtube", t.video())
    equal([job.state, job.error_code, job.detail], ["failed", "hook_not_found", "a step's hook found the final button"], "state, code and detail")
    ok(job.error_message.includes("led to its Publish or Save button, which ReelForge did not press"), `message (${job.error_message})`)
    equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [0, false], "presses the page saw")
    equal(t.app.requests("POST", /\/press$/).length, 0, "press requests")
  },

  async "Instagram: a Next that has become Share is not clicked"(t) {
    t.site.mode.instagram = "next_shares"
    const { job, tab } = await toReady(t, "instagram", t.video())
    equal([job.state, job.error_code], ["failed", "hook_not_found"], "state and code")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [0, false], "presses the page saw")
  },

  async "a newer list from the app cannot point a step at the final button"({ app, browser, video }) {
    // The app's selectors.json is newer and says Next is also found at #done-button, and that the last screen
    // is never recognised: with that, the fourth press of "Next" would be a press of Save. The hooks whose
    // element gets clicked are always the packed ones, and so is the hook the last screen is known by (it
    // guards the post: worker/selectors.js), so neither change is taken and the script stops where it always
    // does, at the last screen, with nothing pressed.
    const list = quickSelectors()
    list.platforms.youtube.hooks.next_button.strategies.push({ id: "evil", css: "#done-button" })
    list.platforms.youtube.hooks.visibility.strategies = [{ id: "never", css: "no-such-element" }]
    app.selectors = list
    equal(await browser.pair(app), "ok", "pairing")
    const made = app.createBatch(video(), ["youtube"])
    await browser.post(app, made.batch)
    const job = made.jobs[0]
    await until(() => `the job to stop at the last screen (it is ${job.state}: ${job.step})`, () => ["ready", "failed", "posted", "unknown"].includes(job.state), 60000)
    const tab = await browser.tab("studio.youtube.com")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [0, false], "presses the page saw")
    equal([job.state, job.detail], ["ready", "Visibility: Private"], "where it stopped")
    equal((await browser.storage("local")).selectors?.version, list.version, "the newer list was taken for everything else")
  },

  async "an empty text empties the box instead of leaving what the page put there"({ app, browser }) {
    // TikTok starts its caption box with the file's name. An empty caption has to take that out again.
    const film = app.addVideo({ text: { tiktok: { caption: "" } } })
    const { job, tab } = await toReady({ app, browser }, "tiktok", film)
    equal(job.state, "ready", `state (${job.error_message})`)
    equal(await tab.evaluate(() => document.querySelector(".public-DraftEditor-content").innerText.trim()), "", "the caption box")
    ok(job.strategies.join(",").includes("caption=draft-editor+cleared"), `how it was done (${job.strategies})`)
  },

  async "text that ReelForge did not send is never made up: the post stops before anything is attached"({ app, browser }) {
    const film = app.addVideo({ text: { youtube: { title: "Only a title" } } })
    const { job, tab } = await toReady({ app, browser }, "youtube", film)
    equal([job.state, job.error_code], ["failed", "extension_outdated"], "state and error code")
    equal(await tab.evaluate(() => fixture.files.length), 0, "files given to the page")
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
