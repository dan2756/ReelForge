// What happens when something goes away mid-job: the tab, the page, the worker, the app.
import { equal, ok, until, sleep, note } from "./harness.mjs"
import { toReady } from "./posting.test.mjs"

const FINAL = "[data-e2e=post_video_button]"

async function start({ app, browser }, platform, film, options) {
  if (!app.paired.length) equal(await browser.pair(app), "ok", "pairing")
  const made = app.createBatch(film, [platform], options)
  await browser.post(app, made.batch)
  return made.jobs[0]
}

export const tests = {
  async "a tab closed before the post was ready: tab_closed, nothing posted"(t) {
    t.site.mode.tiktok = "slow"
    const job = await start(t, "tiktok", t.video())
    await until("the caption step", () => job.state === "filling")
    await (await t.browser.tab("www.tiktok.com")).close()
    await until(() => `the job to stop (it is ${job.state})`, () => job.state === "failed")
    equal([job.error_code, job.error_message], ["tab_closed", "The tab was closed before the post was ready. Nothing was posted."], "error")
  },

  async "a tab closed at ready, before Post was pressed: tab_closed, nothing posted"(t) {
    const { job, tab } = await toReady(t, "tiktok", t.video())
    await tab.close()
    await until(() => `the job to stop (it is ${job.state})`, () => job.state === "failed")
    equal([job.error_code, job.error_message], ["tab_closed", "The tab was closed before Post was pressed. Nothing was posted."], "error")
  },

  async "a tab closed after the person pressed Post, with no confirmation seen: unknown, not failed"(t) {
    // This copy of TikTok shows nothing after the press, so the extension cannot tell what happened.
    t.site.mode.tiktok = "mute"
    const { job, tab } = await toReady(t, "tiktok", t.video())
    await tab.locator(FINAL).click()
    await until("the press to be noticed", async () => (await t.browser.jobRecord(job.id)).pressedByPerson === true)
    await tab.close()
    await until(() => `the job to end (it is ${job.state})`, () => job.state === "unknown")
    equal(job.error_code, "unknown_outcome", "error code")
    // An unknown job still holds its place: the app will not make a second one for this video and platform.
    equal(t.app.createBatch(t.app.videos[0], ["tiktok"]).blocked.length, 1, "a second job is blocked")
  },

  async "a full page load to the platform's posted page is seen as posted"(t) {
    t.site.mode.tiktok = "fullnav"
    const { job, tab } = await toReady(t, "tiktok", t.video())
    await tab.locator(FINAL).click()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    ok(tab.url().endsWith("/tiktokstudio/content"), "the tab moved to the posts page")
    equal((await note(tab)).line, "Posted", "the note on the new page")
  },

  async "a reload at ready ends the job and attaches nothing a second time"(t) {
    const { job, tab } = await toReady(t, "tiktok", t.video())
    const files = t.app.requests("GET", /\/file$/).length
    await tab.reload()
    await until(() => `the job to stop (it is ${job.state})`, () => job.state === "failed", 30000)
    equal([job.error_code, job.error_message], ["tab_closed", "TikTok's upload page was left before Post was pressed. Nothing was posted."], "error")
    equal([await tab.evaluate(() => fixture.files.length), t.app.requests("GET", /\/file$/).length], [0, files], "files on the new page, and requests for the video")
  },

  async "once Post has been pressed for the person, a reload never presses or attaches again: unknown"(t) {
    t.site.mode.tiktok = "confirm"
    equal(await t.browser.pair(t.app), "ok", "pairing")
    t.app.pressUnlocked.add("tiktok")
    const settings = await t.browser.popup()
    await settings.evaluate(() => chrome.storage.local.set({ press: { tiktok: true } }))
    const { job, tab } = await toReady(t, "tiktok", t.video(), { press: true })
    await until("the press", () => job.state === "posting")
    await until("the page's question", async () => (await note(tab)).line === "TikTok is asking you something")
    await tab.reload()
    await until(() => `the job to end (it is ${job.state})`, () => job.state === "unknown", 30000)
    equal(job.error_code, "unknown_outcome", "error code")
    equal(await tab.evaluate(() => [fixture.files.length, fixture.clicks]), [0, 0], "files and presses on the new page")
    equal(t.app.requests("POST", /\/press$/).length, 1, "press requests")
    equal(t.app.requests("POST", /^\/v1\/jobs\/.*\/claim$/).length, 1, "claims")
  },

  async "the worker stopped in the middle of the video: the transfer picks up where it was"(t) {
    const film = t.video(13 * 1024 * 1024)
    t.app.delay.file = 350
    const job = await start(t, "tiktok", film)
    await until("the first piece", () => t.app.requests("GET", /\/file$/).length >= 1)
    await t.browser.stopWorker()
    await until(() => `ready (it is ${job.state}: ${job.error_message})`, () => job.state === "ready" || job.state === "failed", 30000)
    equal(job.state, "ready", "state")
    const tab = await t.browser.tab("www.tiktok.com")
    equal(await tab.evaluate(() => [fixture.files.length, fixture.files[0].size, fixture.sha256]), [1, film.size, film.id], "the file on the page")
    equal(t.browser.tabs("www.tiktok.com").length, 1, "TikTok tabs")
    equal(t.app.requests("POST", /^\/v1\/jobs\/.*\/claim$/).length, 1, "claims")
  },

  async "the worker stopped at ready: the person's post is still seen, and a closed tab still reported"(t) {
    const made = t.app.createBatch(t.video(), ["tiktok", "x"])
    equal(await t.browser.pair(t.app), "ok", "pairing")
    await t.browser.post(t.app, made.batch)
    await until("both ready", () => made.jobs.every(job => job.state === "ready"), 30000)
    await t.browser.stopWorker()
    await sleep(300)
    await (await t.browser.tab("www.tiktok.com")).locator(FINAL).click()
    await until(() => `TikTok posted (it is ${made.jobs[0].state})`, () => made.jobs[0].state === "posted")
    await t.browser.stopWorker()
    await sleep(300)
    await (await t.browser.tab("x.com")).close()
    await until(() => `X to stop (it is ${made.jobs[1].state})`, () => made.jobs[1].state === "failed")
    equal(made.jobs[1].error_code, "tab_closed", "error code")
  },

  async "the app closed while the video was being handed over: app_unreachable, passed on when it is back"(t) {
    const film = t.video(13 * 1024 * 1024)
    t.app.delay.file = 400
    const job = await start(t, "tiktok", film)
    await until("the first piece", () => t.app.requests("GET", /\/file$/).length >= 1)
    await t.app.stop()
    const tab = await t.browser.tab("www.tiktok.com")
    await until("the note to say so", async () => (await note(tab))?.line === "Stopped")
    ok((await note(tab)).more.startsWith("ReelForge closed before the video was handed over"), "what the note says")
    equal(await tab.evaluate(() => fixture.files.length), 0, "files given to the page")
    const record = await t.browser.jobRecord(job.id)
    equal([record.state, record.error_code, record.outbox.length > 0], ["failed", "app_unreachable", true], "the browser's own record, with the news still waiting")
    // The popup shows it while ReelForge is away.
    const popup = await t.browser.popup()
    await until("the popup to show it", async () => (await popup.locator("main").textContent()).includes("ReelForge closed before the video was handed over"))
    ok((await popup.locator("#status").textContent()) === "Not running", "the popup says ReelForge is not running")
    // ReelForge comes back, on another port. The news is delivered without anyone asking twice.
    equal(job.state, "uploading", "what the app knew when it closed")
    await t.app.start(47826)
    await popup.getByRole("button", { name: "Look again" }).click()
    await until(() => `the app to hear (it is ${job.state})`, () => job.state === "failed", 20000)
    equal(job.error_code, "app_unreachable", "error code at the app")
  },

  async "the app closed after the video was attached: the page is still finished, and the app told later"(t) {
    t.site.mode.tiktok = "slow"
    const job = await start(t, "tiktok", t.video())
    await until("the caption step", () => job.state === "filling")
    await t.app.stop()
    const tab = await t.browser.tab("www.tiktok.com")
    await until("the page to be ready", async () => (await note(tab))?.line === "Check it, then press Post", 20000)
    equal(job.state, "filling", "what the app knew when it closed")
    await t.app.start()
    const popup = await t.browser.popup()
    await until(() => `the app to hear (it is ${job.state})`, () => job.state === "ready", 20000)
    await popup.close()
  },

  async "with no post under way, the person's own tabs do not wake the worker"({ browser }) {
    const page = await browser.popup()
    await sleep(800)
    const session = await browser.context.newCDPSession(page)
    const statuses = []
    session.on("ServiceWorker.workerVersionUpdated", event => statuses.push(...event.versions.map(version => version.runningStatus)))
    await session.send("ServiceWorker.enable")
    // Leave the extension's own page, so nothing of the extension's is open and talking to the worker.
    await page.goto("about:blank")
    await session.send("ServiceWorker.stopAllWorkers")
    await until("the worker to stop", () => statuses.at(-1) === "stopped")
    statuses.length = 0
    const other = await browser.context.newPage()
    await other.goto("https://x.com/compose/post")
    await other.goto("https://www.tiktok.com/login")
    await other.close()
    await sleep(1500)
    equal(statuses, [], "worker starts while an ordinary tab opened, moved and closed")
    equal(await other.isClosed(), true, "the tab was really closed")
  },

  async "a post ended in ReelForge stops the tab's work"(t) {
    const { job, tab } = await toReady(t, "tiktok", t.video())
    // The person cancels it in the window.
    Object.assign(job, { state: "cancelled", detail: "cancelled in ReelForge" })
    const popup = await t.browser.popup()
    await until("the browser to take the app's word", async () => (await t.browser.jobRecord(job.id)).state === "cancelled")
    await until("the note to say so", async () => (await note(tab)).line === "Stopped")
    equal((await note(tab)).more, "This post was ended in ReelForge.", "what the note says")
    ok((await tab.locator(FINAL).evaluate(el => getComputedStyle(el).outlineStyle)) === "none", "the outline is gone")
    await popup.close()
  },
}
