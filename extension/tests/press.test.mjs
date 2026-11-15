// Press Post for me: only with the person's switch, the post's own setting, their yes for this batch in this
// browser and the app's grant, once, never on X.
import { equal, ok, until, sleep, note, quickSelectors } from "./harness.mjs"
import { toReady } from "./posting.test.mjs"

const presses = app => app.requests("POST", /\/press$/)

// Switches "Press Post for me" on for a platform the way a person does: on the options page, once the app
// says one assisted post there went through.
async function switchOn(browser, app, platform) {
  equal(await browser.pair(app), "ok", "pairing")
  app.pressUnlocked.add(platform)
  const options = await browser.options()
  const box = options.locator(`#press-${platform}`)
  await until("the switch to unlock", () => box.isEnabled())
  await box.check()
  await until("the switch to be saved", async () => (await browser.storage("local")).press?.[platform] === true)
  await options.close()
}

// The same switch set straight in the extension's storage, for the cases the options page refuses.
async function forceOn(browser, app, platform) {
  equal(await browser.pair(app), "ok", "pairing")
  const page = await browser.popup()
  await page.evaluate(platform => chrome.storage.local.set({ press: { [platform]: true } }), platform)
  await page.close()
}

export const tests = {
  async "with the switch on, the post's own setting, the person's yes and the app's grant, it presses once and sees it posted"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const { job, tab, post } = await toReady({ app, browser }, "tiktok", video(), { press: true, yes: true })
    equal(await post.evaluate(() => [document.documentElement.dataset.rfPress, window.heard.filter(event => event.name === "reelforge:press").map(event => event.detail)]), ["ok", [{ state: "ok" }]], "what the post page heard about the yes")
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(presses(app).map(entry => entry.status), [200], "press requests")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks, fixture.posted]), [1, 0, true], "presses the page saw")
    equal(job.events.map(event => event.state).filter((state, i, all) => state !== all[i - 1]), ["opening", "uploading", "filling", "ready", "posting", "posted"], "states")
    equal((await note(tab)).line, "Posted", "the note")
  },

  async "YouTube: the press waits for the upload, then publishes and reports the video's id"({ app, browser, video }) {
    await switchOn(browser, app, "youtube")
    const { job, tab } = await toReady({ app, browser }, "youtube", video(), { press: true, yes: true })
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.radioClicks]), [1, 0], "presses of Save, and of the audience and visibility choices")
    ok(job.events.some(event => event.step === "upload"), "the upload was waited for")
    equal(job.post_id, "FIXTUREvid1", "video id")
  },

  async "YouTube: with Made for kids unanswered nothing is pressed, whatever is switched on"({ app, site, browser, video }) {
    site.mode.youtube = "kids_unset"
    await switchOn(browser, app, "youtube")
    const { job, tab } = await toReady({ app, browser }, "youtube", video(), { press: true, yes: true })
    equal(job.state, "ready", "state")
    await until("the note to say why", async () => (await note(tab)).more.includes("ReelForge did not press"))
    equal(presses(app).length, 0, "press requests")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.radioClicks]), [0, 0], "presses the page saw")
    ok(job.events.some(event => event.detail === "Made for kids is not answered"), "the app is told what is left")
  },

  async "a post page that nobody clicked on never leads to a press, whatever ReelForge asked for"({ app, browser, video }) {
    // Everything a program on the computer can arrange by itself is in place: the switch is on, the app's job
    // says press, the app would grant it, and the post page is opened. What is missing is a person's click.
    await switchOn(browser, app, "tiktok")
    const { job, tab, post } = await toReady({ app, browser }, "tiktok", video(), { press: true })
    await until("the note to say why", async () => (await note(tab)).more.includes("nobody said yes to it for this post in this browser"))
    equal([job.state, presses(app).length], ["ready", 0], "state and press requests")
    // A click the page's own script makes on the button is not a person's either.
    await post.evaluate(() => document.querySelector("[data-rf-press]").click())
    await sleep(800)
    equal([await post.evaluate(() => document.documentElement.dataset.rfPress || null), presses(app).length], [null, 0], "what a scripted click on the page's button did")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [0, false], "presses the page saw")
    // The person can still press Post themselves.
    await tab.locator("[data-e2e=post_video_button]").click()
    await until("posted", () => job.state === "posted")
  },

  async "a post started with the popup's own button can be pressed for the person"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const film = video()
    const popup = await browser.popup()
    await popup.locator(`[data-video="${film.id}"]`).getByRole("button", { name: "Post" }).click()
    for (const platform of ["youtube", "instagram", "x"]) await popup.locator(`[data-key="pick:${platform}"]`).uncheck()
    await popup.locator("[data-key=press]").check()
    await popup.getByRole("button", { name: "Continue" }).click()
    await until("the confirm screen", () => popup.locator("[data-screen=confirm]").count())
    ok((await popup.locator("[data-confirm=tiktok]").textContent()).includes("ReelForge presses Post for you once everything is in."), "the confirm screen says who presses")
    await popup.getByRole("button", { name: "Open the upload page" }).click()
    const job = app.job("tiktok")
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted", 30000)
    equal(presses(app).map(entry => entry.status), [200], "press requests")
    equal(await (await browser.tab("www.tiktok.com")).evaluate(() => [fixture.clicks, fixture.trustedClicks]), [1, 0], "presses the page saw")
  },

  async "without the switch nothing is pressed and the app is not even asked"({ app, browser, video }) {
    app.pressUnlocked.add("tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true, yes: true })
    await sleep(1200)
    equal([job.state, presses(app).length], ["ready", 0], "state and press requests")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
    equal((await note(tab)).line, "Check it, then press Post", "the note")
  },

  async "with \"stop before posting\" left on for the post, nothing is pressed"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: false, yes: true })
    await sleep(1200)
    equal([job.state, presses(app).length], ["ready", 0], "state and press requests")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
  },

  async "when the app refuses the grant, nothing is pressed and the person is told"({ app, browser, video }) {
    // The switch is on in the browser, but this ReelForge has no assisted post on TikTok yet.
    await forceOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true, yes: true })
    await until("the refusal", () => presses(app).length === 1)
    await until("the note to say why", async () => (await note(tab)).more.includes("ReelForge did not press for you"))
    equal([presses(app)[0].status, job.state], [409, "ready"], "the refusal and the state")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
    ok(job.events.some(event => String(event.detail).startsWith("not pressed:")), "the app is told it was not pressed")
  },

  async "a grant that was already used is refused and nothing is pressed a second time"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    const job = made.jobs[0]
    // As if the press had been granted once before and the answer lost on its way (the worker was stopped):
    // the app has the job as "posting" by the time the page asks.
    const move = app.move.bind(app)
    app.move = (which, body) => {
      const moved = move(which, body)
      if (moved && which === job && body.state === "ready") Object.assign(job, { state: "posting", pressed: true })
      return moved
    }
    await browser.post(app, made.batch, { yes: true })
    await until("the refusal", () => presses(app).length === 1, 30000)
    await sleep(1000)
    equal(presses(app).map(entry => [entry.status, entry.body.install.length > 0]), [[409, true]], "press requests")
    const tab = await browser.tab("www.tiktok.com")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
    ok((await note(tab)).more.includes("Post was already pressed for this one"), `the note (${(await note(tab)).more})`)
  },

  async "X is never pressed, even with everything forced on"({ app, browser, video }) {
    await forceOn(browser, app, "x")
    app.pressUnlocked.add("x")
    const made = app.createBatch(video(), ["x"], { press: true })
    const job = made.jobs[0]
    job.press = true  // even if a job said so, which ReelForge's ledger never lets one on X
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("x.com")
    await until(() => `ready (it is ${job.state})`, () => job.state === "ready", 30000)
    await sleep(1200)
    equal([job.state, presses(app).length], ["ready", 0], "state and press requests")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
  },

  async "a page that shows nothing after the press is never called failed: not sure, and no second post"({ app, site, browser, video }) {
    // The click may have been taken although the page showed nothing. "Failed" would free the video, and
    // a retry would press Post a second time.
    site.mode.tiktok = "trusted_only"
    const list = quickSelectors()
    list.platforms.tiktok.success = { timeout_ms: 4000 }  // (how long the sign is waited for: five minutes as packed)
    app.selectors = list
    await switchOn(browser, app, "tiktok")
    const film = video()
    const { job, tab } = await toReady({ app, browser }, "tiktok", film, { press: true, yes: true })
    await until("the note to say the page has not reacted", async () => (await note(tab)).line === "TikTok has not reacted", 20000)
    equal(job.state, "posting", "the job meanwhile")
    await until(() => `the job to end (it is ${job.state})`, () => job.state !== "posting", 20000)
    equal([job.state, job.error_code], ["unknown", "press_rejected"], "state and code")
    ok(job.error_message.includes("saw no reaction") && job.error_message.includes("Check your profile"), `message (${job.error_message})`)
    equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [1, false], "presses the page saw")
    equal(presses(app).length, 1, "press requests")
    equal((await note(tab)).line, "Not sure it posted", "the note")
    const again = app.createBatch(film, ["tiktok"], { press: true })
    equal([again.jobs.length, again.blocked[0]?.reason], [0, "unknown"], "a second post of the video")
  },

  async "after a press the page ignored, the person's own press is seen as the post"({ app, site, browser, video }) {
    site.mode.tiktok = "trusted_only"
    await switchOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true, yes: true })
    await until("the note to say the page has not reacted", async () => (await note(tab)).line === "TikTok has not reacted", 20000)
    ok((await note(tab)).more.includes("press Post yourself"), `what the note says (${(await note(tab)).more})`)
    await tab.locator("[data-e2e=post_video_button]").click()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks]), [2, 1], "presses the page saw")
    equal(presses(app).length, 1, "press requests")
  },

  async "a question after the press is left to the person, and their answer is seen"({ app, site, browser, video }) {
    site.mode.tiktok = "confirm"
    await switchOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true, yes: true })
    await until("the question to be noticed", async () => (await note(tab)).line === "TikTok is asking you something")
    equal([job.state, await tab.evaluate(() => fixture.clicks)], ["posting", 1], "state and presses")
    await tab.locator(".common-modal-footer button").click()
    await until("posted", () => job.state === "posted")
  },

  async "the options page: locked until the app unlocks it, with the reason, and X is not offered"({ app, browser }) {
    equal(await browser.pair(app), "ok", "pairing")
    const options = await browser.options()
    await until("the switches", () => options.locator("#press-tiktok").count())
    for (const platform of ["tiktok", "youtube", "instagram"]) {
      ok(await options.locator(`#press-${platform}`).isDisabled(), `${platform} switch is locked`)
      ok(!(await options.locator(`#press-${platform}`).isChecked()), `${platform} switch is off`)
      ok((await options.locator(`#why-${platform}`).textContent()).startsWith("Locked until one post to"), `${platform} says why it is locked`)
    }
    equal(await options.locator("#press-x input").count(), 0, "switches for X")
    ok((await options.locator("#press-x").textContent()).includes("Not offered"), "X says it is not offered")
    const page = await options.locator("main").textContent()
    ok(page.includes("terms restrict automated posting") && page.includes("Pressing Post yourself is the safer choice"), "the plain statement about the platforms' terms")
    equal((await browser.storage("local")).press, undefined, "nothing is switched on to begin with")
    // The worker refuses what the page would not offer.
    const forced = await options.evaluate(() => Promise.all([
      chrome.runtime.sendMessage({ t: "ui.press", platform: "x", on: true }),
      chrome.runtime.sendMessage({ t: "ui.press", platform: "tiktok", on: true }),
    ]))
    equal(forced.map(answer => answer.ok), [false, false], "forcing X, and a locked platform")
    app.pressUnlocked.add("instagram")
    await until("Instagram to unlock", () => options.locator("#press-instagram").isEnabled())
    await options.locator("#press-instagram").check()
    await until("it to be saved", async () => (await browser.storage("local")).press?.instagram === true)
    ok(await options.locator("#press-tiktok").isDisabled(), "TikTok stays locked")
  },
}
