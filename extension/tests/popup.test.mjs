// The popup: what it says about the connection, the videos, a post from start to finish, and what went wrong.
import { FakeApp } from "./fake_app.mjs"
import { equal, ok, until, sleep } from "./harness.mjs"
import { toReady } from "./posting.test.mjs"

const text = locator => locator.textContent().then(value => value.replace(/\s+/g, " ").trim())
const status = popup => popup.locator("#status").textContent()

export const tests = {
  async "with no ReelForge open it says so, and finds it once it is"({ app, browser }) {
    const port = app.port
    await app.stop()
    const popup = await browser.popup()
    await until("the screen", () => popup.locator("[data-screen=not_running]").count())
    equal(await status(popup), "Not running", "the status pill")
    const screen = await text(popup.locator("[data-screen=not_running]"))
    ok(screen.includes("ReelForge is not running") && screen.includes("only works together with the app"), `what it says (${screen})`)
    await app.start(port)
    await popup.getByRole("button", { name: "Look again" }).click()
    await until("the next screen", () => popup.locator("[data-screen=not_connected]").count())
  },

  async "with ReelForge open but not paired it says how to connect"({ browser }) {
    const popup = await browser.popup()
    await until("the screen", () => popup.locator("[data-screen=not_connected]").count())
    equal(await status(popup), "Not connected", "the status pill")
    const screen = await text(popup.locator("[data-screen=not_connected]"))
    ok(screen.includes("this browser is not paired") && screen.includes("reelforge extension"), `what it says (${screen})`)
  },

  async "ReelForge's demo, and a ReelForge or an extension that is too old, each get their own words"({ app, browser }) {
    const popup = await browser.popup()
    const shows = async (name, words) => {
      await popup.getByRole("button", { name: "Look again" }).click()
      await until(`the ${name} screen`, () => popup.locator(`[data-screen=${name}]`).count())
      ok((await text(popup.locator(`[data-screen=${name}]`))).includes(words), `the ${name} screen says "${words}"`)
    }
    await until("the first screen", () => popup.locator("[data-screen]").count())
    app.demo = true
    await shows("demo", "Posting is off while ReelForge shows example videos")
    app.demo = false
    app.protocol = 2
    await shows("extension_outdated", "press Reload on ReelForge")
    app.protocol = 0
    await shows("app_outdated", "Update ReelForge")
  },

  async "connected: the channel's name, each video and where it stands on each platform"({ app, browser, video }) {
    const film = video()
    app.addVideo({ name: "ep33_rain.mp4", title: "What if it rained for a year?", review: "stale", postable: false, why: "The review is for an earlier cut. Ask your AI to review this one." })
    app.posted.set(`${film.id}:youtube`, "https://youtu.be/already")
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await until("the videos", () => popup.locator("[data-video]").count())
    equal(await status(popup), "Rendered Wrong", "the status pill")
    const card = popup.locator(`[data-video="${film.id}"]`)
    equal(await card.locator("h3").textContent(), "What if hail doubled every 2 seconds?", "the title")
    equal(await card.locator("p").first().textContent(), "ep32_hail.mp4 · 300 KB · approved", "file, size and review")
    equal(await card.locator(".chip").allTextContents(), ["TikTok: not posted", "YouTube: already there", "Instagram: not posted", "X: not posted"], "platform chips")
    ok(await card.getByRole("button", { name: "Post" }).isEnabled(), "Post is offered")
    const held = popup.locator("[data-video]").first()
    ok((await text(held)).includes("The review is for an earlier cut"), "a video that cannot be posted says why")
    equal(await held.getByRole("button").count(), 0, "and has no Post button")
  },

  async "connected with nothing to post says so"({ app, browser }) {
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await until("the empty line", async () => (await text(popup.locator("main"))).includes("No finished videos yet"))
  },

  async "Post from the popup: choose, read the exact text and the account, then the pages open and progress shows"({ app, browser, video }) {
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await until("the choose screen", () => popup.locator("[data-screen=choose]").count())
    equal(app.jobs.size, 0, "jobs before anything is confirmed")
    const pick = platform => popup.locator(`[data-key="pick:${platform}"]`)
    for (const platform of ["tiktok", "youtube", "instagram", "x"]) await pick(platform).uncheck()
    ok(await popup.getByRole("button", { name: "Continue" }).isDisabled(), "Continue is off with nothing chosen")
    for (const platform of ["tiktok", "x"]) await pick(platform).check()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    // The batch exists now, but nothing is claimed and no tab is open until the person has read it.
    equal([...app.jobs.values()].map(job => [job.platform, job.state, job.press]), [["tiktok", "queued", false], ["x", "queued", false]], "jobs while confirming")
    equal(browser.tabs("www.tiktok.com").length + browser.tabs("x.com").length, 0, "tabs while confirming")
    const tiktok = popup.locator("[data-confirm=tiktok]")
    equal(await tiktok.locator("pre").allTextContents(), [film.text.tiktok.caption], "TikTok's exact text")
    ok((await text(tiktok)).includes("as @fixtureuser") && (await text(tiktok)).includes("You press Post yourself."), `TikTok's account and who presses (${await text(tiktok)})`)
    // "as @handle" is checked only where the upload page shows its account (Instagram and X). On TikTok and
    // YouTube the popup says so, instead of letting the line read as a promise.
    ok((await text(popup.locator("[data-unchecked=tiktok]"))).includes("TikTok's upload page does not show its account, so ReelForge cannot check it."), "TikTok's account is not checked, and the popup says so")
    equal(await popup.locator("[data-confirm=x] [data-unchecked]").count(), 0, "no such line where the account is checked")
    equal(await popup.locator("[data-confirm=x] pre").allTextContents(), [film.text.x.text], "X's exact text")
    await popup.getByRole("button", { name: "Open the upload pages" }).click()
    await until("the jobs to show", async () => (await popup.locator("[data-job]").count()) === 2)
    const row = popup.locator("[data-job][data-platform=tiktok]")
    await until("TikTok to be ready in the popup", async () => (await row.getAttribute("data-state")) === "ready", 30000)
    ok((await text(row)).includes("Ready. Check it, then press Post"), `the row (${await text(row)})`)
    equal(browser.tabs("www.tiktok.com").length, 1, "TikTok tabs")
    await until("the video list to follow", async () => (await popup.locator(`[data-video="${film.id}"] [data-platform=tiktok]`).textContent()) === "TikTok: ready to press")
    // Show tab brings the upload page to the front.
    await row.getByRole("button", { name: "Show tab" }).click()
    await until("the tab to be in front", () => browser.tabs("www.tiktok.com")[0].evaluate(() => document.visibilityState === "visible"))
  },

  async "a job shows its progress while the page works"({ app, site, browser, video }) {
    site.mode.youtube = "slow"
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const made = app.createBatch(film, ["youtube"])
    await browser.post(app, made.batch)
    const popup = await browser.popup()
    const row = popup.locator("[data-job]")
    await until("a progress bar with a number", async () => Number(await row.locator("[role=progressbar]").getAttribute("aria-valuenow")) > 0, 30000)
    await until("the row to say what YouTube is doing", async () => (await text(row)).includes("YouTube is taking the video in"))
    await row.getByRole("button", { name: "Stop" }).click()
    await until(() => `the job to be cancelled (it is ${made.jobs[0].state})`, () => made.jobs[0].state === "cancelled")
  },

  async "Cancel on the confirm screen ends the waiting jobs and opens nothing"({ app, browser, video }) {
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    await popup.getByRole("button", { name: "Cancel" }).click()
    await until("the jobs to be cancelled", () => [...app.jobs.values()].every(job => job.state === "cancelled"))
    await sleep(300)
    equal(browser.context.pages().filter(page => page.url().startsWith("https://")).length, 0, "upload tabs")
    equal(await popup.locator("[data-job]").count(), 0, "jobs in the popup")
    // Called off as they were: no browser ever took them, so ReelForge keeps no trace of a post that was begun.
    equal([app.requests("POST", /^\/v1\/jobs\/.*\/claim$/).length, [...app.jobs.values()].map(job => job.claimed_by)], [0, [null, null, null, null]], "claims, and who held the jobs")
  },

  async "posts that waited too long on the confirm screen are not called started"({ app, browser, video }) {
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    for (const job of app.jobs.values()) job.created -= 11 * 60 * 1000  // the popup sat open over a coffee
    await popup.getByRole("button", { name: /Open the upload/ }).click()
    await until("the popup to say so", async () => (await text(popup.locator("main"))).includes("waited more than ten minutes"))
    ok((await text(popup.locator("[data-flash]"))).includes("Nothing was opened. Press Post again."), "what it says")
    equal(await popup.locator("[data-screen=confirm]").count(), 0, "the confirm screen")
    equal(browser.context.pages().filter(page => page.url().startsWith("https://")).length, 0, "upload tabs")
    equal([...app.jobs.values()].map(job => job.state), ["expired", "expired", "expired", "expired"], "the jobs")
    equal((await browser.storage()).pending, undefined, "nothing is kept to come back to")
  },

  async "a popup closed on the confirm screen comes back to it"({ app, browser, video }) {
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    await popup.close()
    const again = await browser.popup()
    await until("the confirm screen again", () => again.locator("[data-screen=confirm]").count())
    equal(await again.locator("[data-confirm=tiktok] pre").allTextContents(), [film.text.tiktok.caption], "TikTok's exact text")
    equal([...app.jobs.values()].every(job => job.state === "queued"), true, "nothing started meanwhile")
  },

  async "a platform that already has the video is shown as blocked, not posted twice"({ app, browser, video }) {
    const film = video()
    const { job } = await toReady({ app, browser }, "tiktok", film)
    equal(job.state, "ready", "the first job")
    const popup = await browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await until("the choose screen", () => popup.locator("[data-screen=choose]").count())
    ok(await popup.getByLabel("TikTok").isDisabled(), "TikTok cannot be chosen while a post is under way there")
    for (const name of ["YouTube", "Instagram"]) await popup.getByLabel(name).uncheck()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    equal([...app.jobs.values()].map(job => job.platform), ["tiktok", "x"], "jobs")
  },

  async "when ReelForge blocks every platform that was chosen, the popup says why and opens nothing"({ app, browser, video }) {
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    await until("the choose screen", () => popup.locator("[data-screen=choose]").count())
    // Between the popup's last look and the press of Continue, the stats reader found the video on every platform.
    for (const platform of ["tiktok", "youtube", "instagram", "x"]) app.posted.set(`${film.id}:${platform}`, `https://example.com/${platform}`)
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the blocked platforms", async () => (await popup.locator("[data-blocked]").count()) === 4)
    ok((await text(popup.locator("[data-blocked=tiktok]"))).includes("TikTok: Already posted: ReelForge found this video's post there."), "why TikTok is blocked")
    ok((await text(popup.locator("[data-screen=confirm]"))).includes("Nothing new to post."), "nothing to post")
    equal(await popup.getByRole("button", { name: /Open the upload/ }).count(), 0, "buttons that would open a page")
    equal((await browser.storage()).pending, undefined, "nothing is kept to come back to")
    await popup.getByRole("button", { name: "Back" }).click()
    await until("the list again", () => popup.locator("[data-video]").count())
  },

  async "a stopped job says what happened and what to do, and offers the text to finish by hand"({ app, site, browser, video }) {
    site.mode.x = "signed_out"
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const made = app.createBatch(film, ["x"])
    await browser.post(app, made.batch)
    await until("the job to stop", () => made.jobs[0].state === "failed")
    const popup = await browser.popup()
    const row = popup.locator("[data-job]")
    await until("the row", () => row.count())
    const words = await text(row)
    ok(words.includes("This browser is not signed in to X. Sign in, then post again."), `what happened (${words})`)
    ok(words.includes("It has to be this browser that is signed in to X."), `what to do (${words})`)
    await row.locator("summary").click()
    equal(await row.locator("pre").allTextContents(), [film.text.x.text], "the text to paste by hand")
    // Copy puts exactly that text on the clipboard. The test stands in for the clipboard itself.
    await popup.evaluate(() => {
      window.copied = []
      navigator.clipboard.writeText = async value => { window.copied.push(value) }
    })
    await row.getByRole("button", { name: "Copy" }).click()
    await until("the copy", () => popup.evaluate(() => window.copied.length))
    equal(await popup.evaluate(() => window.copied), [film.text.x.text], "what was copied")
    await row.getByRole("button", { name: "Dismiss" }).click()
    await until("the row to go", async () => (await popup.locator("[data-job]").count()) === 0)
  },

  async "a post nobody could confirm says so and where to answer, and stops asking once ReelForge has the answer"({ app, site, browser, video }) {
    site.mode.tiktok = "mute"
    const { job, tab } = await toReady({ app, browser }, "tiktok", video())
    await tab.locator("[data-e2e=post_video_button]").click()
    await until("the press to be noticed", async () => (await browser.jobRecord(job.id)).pressedByPerson === true)
    await tab.close()
    await until("unknown", () => job.state === "unknown")
    const popup = await browser.popup()
    const row = popup.locator("[data-job]")
    await until("the row", () => row.count())
    const words = await text(row)
    ok(words.includes("no confirmation was seen"), `what happened (${words})`)
    ok(words.includes("Check your TikTok profile. Then tell ReelForge whether it posted, on its Publish tab."), `what to do (${words})`)
    equal(await row.locator(".chip").textContent(), "not sure", "the state chip")
    // The answer is given in ReelForge ("It didn't post"). The popup takes it and stops asking.
    Object.assign(job, { state: "cancelled", detail: "you said it didn't post" })
    await until("the popup to take ReelForge's word", async () => (await row.locator(".chip").textContent()) === "cancelled", 20000)
    ok(!(await text(row)).includes("tell ReelForge whether it posted"), `what the row says now (${await text(row)})`)
  },

  async "a post the person pressed themselves has no Stop: a press cannot be taken back"({ app, site, browser, video }) {
    site.mode.tiktok = "mute"
    const { job, tab } = await toReady({ app, browser }, "tiktok", video())
    const popup = await browser.popup()
    const row = popup.locator("[data-job]")
    await until("the row with its Stop", () => row.getByRole("button", { name: "Stop" }).count())
    await tab.locator("[data-e2e=post_video_button]").click()
    await until("the press to be noticed", async () => (await browser.jobRecord(job.id)).pressedByPerson === true)
    await until("the row to say so", async () => (await text(row)).includes("You pressed Post. Waiting for TikTok to say it is up"))
    equal(await row.getByRole("button", { name: "Stop" }).count(), 0, "Stop buttons")
    // A popup drawn before the press still had its Stop. The worker refuses that one.
    equal(await popup.evaluate(id => chrome.runtime.sendMessage({ t: "ui.cancel", job: id }), job.id), { ok: false }, "Stop, asked for anyway")
    equal([job.state, (await browser.jobRecord(job.id)).state], ["ready", "ready"], "the job, in ReelForge and here")
    ok(job.events.some(event => event.state === "ready" && event.step === "press"), `ReelForge was told of the press (${JSON.stringify(job.events.slice(-3))})`)
  },

  async "two channels open: the popup offers both and follows the choice"({ app, browser, video }) {
    video()
    // A second ReelForge window, on another channel of the same install: same app id, same paired browsers.
    const other = new FakeApp({ profile: { key: "second", name: "Second Channel" } })
    other.appId = app.appId
    await other.start()
    try {
      equal(await browser.pair(app), "ok", "pairing")
      other.paired = app.paired
      const popup = await browser.popup()
      await until("the channel list", () => popup.locator("select").count())
      equal(await popup.locator("select option").allTextContents(), ["Rendered Wrong", "Second Channel"], "channels")
      equal(await status(popup), "Rendered Wrong", "the status pill")
      await popup.locator("select").selectOption({ label: "Second Channel" })
      await until("the pill to follow", async () => (await status(popup)) === "Second Channel")
      await until("the other channel's empty list", async () => (await text(popup.locator("main"))).includes("No finished videos yet"))
    } finally {
      await other.stop()
    }
  },

  async "Disconnect on the settings page unpairs here and at the app"({ app, browser }) {
    equal(await browser.pair(app), "ok", "pairing")
    const options = await browser.options()
    await until("the connection line", async () => (await options.locator("#connection").textContent()).startsWith("Connected to Rendered Wrong"))
    await options.locator("#disconnect").click()
    await until("the app to forget this browser", () => app.paired.length === 0)
    equal((await browser.storage("local")).tokens, undefined, "tokens kept in the browser")
    await until("the page to say so", async () => (await options.locator("#connection").textContent()).includes("not connected"))
    const popup = await browser.popup()
    await until("the popup to say so", () => popup.locator("[data-screen=not_connected]").count())
  },

  async "keyboard: Tab reaches the buttons and the focus ring shows"({ app, browser, video }) {
    video()
    equal(await browser.pair(app), "ok", "pairing")
    const popup = await browser.popup()
    await until("the videos", () => popup.locator("[data-video]").count())
    await popup.keyboard.press("Tab")
    equal(await popup.evaluate(() => document.activeElement.id), "settings", "first stop")
    await popup.keyboard.press("Tab")
    equal(await popup.evaluate(() => [document.activeElement.textContent, getComputedStyle(document.activeElement).outlineColor, getComputedStyle(document.activeElement).outlineStyle]),
      ["Post", "rgb(61, 255, 154)", "solid"], "second stop and its focus ring")
    // The list redraws every few seconds. Focus stays on the same button through a redraw.
    await sleep(3500)
    equal(await popup.evaluate(() => document.activeElement.textContent), "Post", "focus after a redraw")
    await popup.keyboard.press("Enter")
    await until("the choose screen", () => popup.locator("[data-screen=choose]").count())
  },
}
