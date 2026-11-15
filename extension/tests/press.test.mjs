// Press Post for me: only with the person's switch, the post's own setting and the app's grant, once, never on X.
import { equal, ok, until, sleep, note } from "./harness.mjs"
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
  async "with the switch on, the post's own setting and the app's grant, it presses once and sees it posted"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true })
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(presses(app).map(entry => entry.status), [200], "press requests")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks, fixture.posted]), [1, 0, true], "presses the page saw")
    equal(job.events.map(event => event.state).filter((state, i, all) => state !== all[i - 1]), ["opening", "uploading", "filling", "ready", "posting", "posted"], "states")
    equal((await note(tab)).line, "Posted", "the note")
  },

  async "YouTube: the press waits for the upload, then publishes and reports the video's id"({ app, browser, video }) {
    await switchOn(browser, app, "youtube")
    const { job, tab } = await toReady({ app, browser }, "youtube", video(), { press: true })
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.radioClicks]), [1, 0], "presses of Save, and of the audience and visibility choices")
    ok(job.events.some(event => event.step === "upload"), "the upload was waited for")
    equal(job.post_id, "FIXTUREvid01", "video id")
  },

  async "YouTube: with Made for kids unanswered nothing is pressed, whatever is switched on"({ app, site, browser, video }) {
    site.mode.youtube = "kids_unset"
    await switchOn(browser, app, "youtube")
    const { job, tab } = await toReady({ app, browser }, "youtube", video(), { press: true })
    equal(job.state, "ready", "state")
    await until("the note to say why", async () => (await note(tab)).more.includes("ReelForge did not press"))
    equal(presses(app).length, 0, "press requests")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.radioClicks]), [0, 0], "presses the page saw")
    ok(job.events.some(event => event.detail === "Made for kids is not answered"), "the app is told what is left")
  },

  async "without the switch nothing is pressed and the app is not even asked"({ app, browser, video }) {
    app.pressUnlocked.add("tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true })
    await sleep(1200)
    equal([job.state, presses(app).length], ["ready", 0], "state and press requests")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
    equal((await note(tab)).line, "Check it, then press Post", "the note")
  },

  async "with \"stop before posting\" left on for the post, nothing is pressed"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: false })
    await sleep(1200)
    equal([job.state, presses(app).length], ["ready", 0], "state and press requests")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
  },

  async "when the app refuses the grant, nothing is pressed and the person is told"({ app, browser, video }) {
    // The switch is on in the browser, but this ReelForge has no assisted post on TikTok yet.
    await forceOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true })
    await until("the refusal", () => presses(app).length === 1)
    await until("the note to say why", async () => (await note(tab)).more.includes("ReelForge did not press for you"))
    equal([presses(app)[0].status, job.state], [409, "ready"], "the refusal and the state")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
    ok(job.events.some(event => String(event.detail).startsWith("not pressed:")), "the app is told it was not pressed")
  },

  async "a grant that was already used is refused and nothing is pressed a second time"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const film = video()
    equal(await browser.pair(app), "ok", "pairing")
    const made = app.createBatch(film, ["tiktok"], { press: true })
    // As if the press had been granted once before (to this browser before a restart, say).
    made.jobs[0].pressed = true
    await browser.post(app, made.batch)
    const job = made.jobs[0]
    await until("the refusal", () => presses(app).length === 1, 30000)
    await sleep(1000)
    equal([presses(app).map(entry => entry.status), job.state], [[409], "ready"], "press requests and state")
    const tab = await browser.tab("www.tiktok.com")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
  },

  async "X is never pressed, even with everything forced on"({ app, browser, video }) {
    await forceOn(browser, app, "x")
    app.pressUnlocked.add("x")
    const { job, tab } = await toReady({ app, browser }, "x", video(), { press: true })
    await sleep(1200)
    equal([job.state, presses(app).length], ["ready", 0], "state and press requests")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
  },

  async "a page that ignores the scripted press gets press_rejected, and is not pressed again"({ app, site, browser, video }) {
    site.mode.tiktok = "trusted_only"
    await switchOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true })
    await until(() => `the failure (it is ${job.state})`, () => job.state === "failed", 20000)
    equal(job.error_code, "press_rejected", "error code")
    ok(job.error_message.includes("press Post yourself"), `message (${job.error_message})`)
    await sleep(800)
    equal(await tab.evaluate(() => [fixture.clicks, fixture.posted]), [1, false], "presses the page saw")
    equal(presses(app).length, 1, "press requests")
    equal((await note(tab)).line, "Stopped", "the note")
  },

  async "a question after the press is left to the person, and their answer is seen"({ app, site, browser, video }) {
    site.mode.tiktok = "confirm"
    await switchOn(browser, app, "tiktok")
    const { job, tab } = await toReady({ app, browser }, "tiktok", video(), { press: true })
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
