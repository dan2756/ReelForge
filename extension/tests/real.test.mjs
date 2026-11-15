// The extension against ReelForge's real local server and ledger (real.mjs), not the stand-in: pairing, a post
// from the post page to "posted" in the stats, the popup's Post and Cancel, the press with its one grant, and
// what each half does when the other is busy, restarted or told something it did not expect. The upload pages
// are still the copies. `app` here is a RealApp.
import { equal, ok, until, sleep, note } from "./harness.mjs"

export const real = true

const HOST = { tiktok: "www.tiktok.com", youtube: "studio.youtube.com", instagram: "www.instagram.com", x: "x.com" }
const text = locator => locator.textContent().then(value => value.replace(/\s+/g, " ").trim())

// A video on the Publish list and a browser that is connected.
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

export const tests = {
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
  },

  async "four platforms at once, and a second Post of the same video is refused while they are under way"(t) {
    const film = await ready(t)
    const { jobs } = await post(t, film, ["tiktok", "youtube", "instagram", "x"])
    for (const platform of Object.keys(HOST)) await becomes(t.app, jobs[platform], ["ready"], 45000)
    const again = await t.app.ask("batch", { path: film.path, platforms: ["tiktok", "x"] })
    equal([again.batch, again.blocked.map(block => block.reason)], [null, ["in_progress", "in_progress"]], "a second Post")
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
    const second = await post(t, await t.app.ask("video", { name: "ep33_rain.mp4" }), ["tiktok"], { press: true, yes: true })
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
