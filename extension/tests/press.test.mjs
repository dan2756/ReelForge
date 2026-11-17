// Press Post for me: only with the person's switch, the post's own setting, their yes for this batch in this
// browser and the app's grant, once, never on X.
import { equal, ok, until, sleep, note, quickSelectors } from "./harness.mjs"
import { toReady } from "./posting.test.mjs"

const presses = app => app.requests("POST", /\/press$/)

// What every note before Post says on TikTok, whose upload page does not show which account it is on.
const LOOK = " ReelForge has @fixtureuser for this channel, and could not check the account here: look at which one this tab is on."

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

// Holds ReelForge's answer to the press request until the test lets it go, so the person can press Post while
// ReelForge is being asked. "decided": the app decides at once and only its answer is slow to come back (a busy
// machine); otherwise the request waits before the app has looked at it.
function holdPress(app, { decided }) {
  const handle = app.handle.bind(app)
  const held = { arrived: false }
  const gate = new Promise(resolve => (held.release = resolve))
  app.handle = async (request, response) => {
    if (request.method === "POST" && /\/press$/.test(request.url)) {
      held.arrived = true
      if (decided) {
        const end = response.end.bind(response)
        response.end = (...args) => {
          gate.then(() => end(...args))
          return response
        }
      } else await gate
    }
    return handle(request, response)
  }
  return held
}

// ReelForge takes the press request and decides it, and its answer is cut off on the way back: the browser gets the
// start of it, then the connection goes dead. (Cut before anything of the answer went, the browser itself would send
// the request again, on its own.) `batch`: from the press on, ReelForge can't be asked about the batch either.
function loseAnswers(app, { batch = false } = {}) {
  const handle = app.handle.bind(app)
  const lose = { lost: 0, batch }
  let pressed = false
  app.handle = async (request, response) => {
    if (request.method === "POST" && /\/press$/.test(request.url)) {
      pressed = true
      response.end = body => {
        lose.lost++
        response.write(String(body).slice(0, 3))
        setTimeout(() => request.socket.destroy(), 50)
        return response
      }
    } else if (pressed && lose.batch && request.method === "GET" && /^\/v1\/batch\//.test(request.url)) {
      response.end = () => {
        request.socket.destroy()
        return response
      }
    }
    return handle(request, response)
  }
  return lose
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
    ok(job.events.some(event => event.detail === "Not pressed: Press Post yourself: ReelForge isn't allowed to for this post."), `the app is told it was not pressed, and why (${job.events.map(event => event.detail)})`)
  },

  async "on a page that does not show its account, the note says to look, while ReelForge is asked for the press and after it says no"({ app, site, browser, video }) {
    // The switch is on in the browser, but this ReelForge has no assisted post on TikTok yet: it will say no.
    await forceOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    equal(await note(tab), { tone: "work", line: "Pressing Post for you", more: `Checking with ReelForge first.${LOOK}` }, "the note while ReelForge is asked")
    held.release()
    await until("the note to say why", async () => (await note(tab)).line === "Check it, then press Post")
    equal((await note(tab)).more, `ReelForge did not press for you: Press Post yourself: ReelForge isn't allowed to for this post.${LOOK}`, "the note after the no")
    equal(await tab.evaluate(() => fixture.clicks), 0, "presses the page saw")
  },

  async "while ReelForge is asked for the press, Post is brought into view, without its ring"({ app, browser, video }) {
    // (On this copy of TikTok, Post sits below a tall part of the form, out of view until something scrolls to it.)
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    equal(await tab.evaluate(() => {
      const button = document.querySelector("[data-e2e=post_video_button]")
      const box = button.getBoundingClientRect()
      return [box.top >= 0 && box.bottom <= innerHeight, button.style.getPropertyValue("outline"), scrollY > 0]
    }), [true, "", true], "Post in view, its ring, and the page scrolled to it")
    held.release()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
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
    // Not "press Post" as if nothing had been pressed: ReelForge has it as pressed, so the person looks first.
    const said = await note(tab)
    equal([said.line, said.more], ["Look at your profile first", `ReelForge has Post as pressed for this one already, though nothing was clicked here. Look at your TikTok profile before you press it.${LOOK}`], "the note")
  },

  async "ReelForge slow to answer the press (its database busy for six seconds) is waited for, and asked once"({ app, site, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await sleep(6000)
    held.release()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted", 20000)
    equal(presses(app).map(entry => entry.status), [200], "press requests")
    equal(await (await browser.tab("www.tiktok.com")).evaluate(() => fixture.clicks), 1, "presses the page saw")
  },

  async "a press answer that comes after the extension stopped waiting is not asked for again: ReelForge's own record says yes, and Post is pressed once"({ app, browser, video }) {
    // ReelForge says yes at once, and its answer takes longer than the extension waits (20 seconds).
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: true })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted", 40000)
    held.release()
    equal(presses(app).length, 1, "press requests")
    equal(await (await browser.tab("www.tiktok.com")).evaluate(() => fixture.clicks), 1, "presses the page saw")
  },

  async "a press answer cut off on its way back is not asked for again: ReelForge's own record says yes, and Post is pressed once"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const lose = loseAnswers(app)
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const job = made.jobs[0]
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted", 30000)
    equal([lose.lost, presses(app).length], [1, 1], "press answers cut off, and press requests")
    equal(await (await browser.tab("www.tiktok.com")).evaluate(() => fixture.clicks), 1, "presses the page saw")
  },

  async "a press answer that is lost while ReelForge can't be asked either: nothing is clicked, and the page says to check, then press Post"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const lose = loseAnswers(app, { batch: true })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until("the note to say why", async () => (await note(tab))?.line === "Check it, then press Post", 30000)
    await sleep(1000)
    equal((await note(tab)).more, `ReelForge did not press for you: its answer did not come back in time, so nothing was clicked.${LOOK}`, "what the note says")
    equal([presses(app).length, await tab.evaluate(() => fixture.clicks)], [1, 0], "press requests, and presses the page saw")
    const kept = await browser.jobRecord(job.id)
    equal([kept.state, kept.pressAnswer, job.state], ["ready", "lost", "posting"], "the job here, and in ReelForge (which said yes)")
    // ReelForge answers again, and the person presses Post themselves: that press is the post.
    lose.batch = false
    await tab.locator("[data-e2e=post_video_button]").click()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted", 20000)
    equal([presses(app).length, await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks])], [1, [1, 1]], "press requests, and presses the page saw")
  },

  async "the person's own press while the video is going up is the only press, whatever was asked for"({ app, site, browser, video }) {
    // Press Post for me is on, the post's own setting says press and the person said yes. They press Save
    // themselves while YouTube is still taking the video in, and the page shows nothing yet ("slow_mute": it
    // takes no click). When everything is filled in, the form is still there: a second press would post twice.
    site.mode.youtube = "slow_mute"
    await switchOn(browser, app, "youtube")
    const made = app.createBatch(video(), ["youtube"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("studio.youtube.com")
    const job = made.jobs[0]
    await until(() => `the wait for the upload (it is ${job.state} ${job.step})`, () => job.step === "upload" && job.events.some(event => event.step === "visibility"), 30000)
    await tab.locator("#done-button").click()
    await until("the press to be noticed", async () => (await browser.jobRecord(job.id)).pressedByPerson === true)
    await until(() => `ready (it is ${job.state} ${job.step})`, () => job.state === "ready", 40000)
    await sleep(2500)
    equal([presses(app).length, job.state], [0, "ready"], "press requests, and the job meanwhile")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks]), [1, 1], "presses the page saw")
    // The page's script did not even ask: nothing says "not pressed" as if the person had not.
    ok(!job.events.some(event => String(event.detail).startsWith("Not pressed")), `what the app was told (${job.events.map(event => event.detail)})`)
    equal((await note(tab)).line, "You pressed Publish or Save", "the note")
    // Nor would the worker have asked the app, had the page's script asked it.
    const extension = await browser.popup()
    const asked = await extension.evaluate(async tabId => (await import("/worker/jobs.js")).tabPress(tabId), (await browser.jobRecord(job.id)).tabId)
    equal([asked.ok, asked.error, presses(app).length], [false, "already_pressed", 0], "a press asked for all the same")
    // YouTube takes it in the end: the person's press is the post.
    await tab.evaluate(() => {
      const root = document.querySelector("ytcp-app").shadowRoot
      root.querySelector("ytcp-uploads-dialog").remove()
      const share = document.createElement("ytcp-video-share-dialog")
      share.innerHTML = '<h2>Video published</h2><a href="https://youtu.be/FIXTUREvid1">https://youtu.be/FIXTUREvid1</a>'
      root.append(share)
    })
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal([job.post_id, await tab.evaluate(() => fixture.clicks), presses(app).length], ["FIXTUREvid1", 1, 0], "the video's id, presses and press requests")
  },

  async "the person's press while ReelForge is asked to press is the only press, though the app's yes is on its way"({ app, site, browser, video }) {
    // The app says yes before it hears of the person's press, and its answer takes a moment to come back. The
    // person presses Post in that moment, and the page shows nothing yet ("mute": it takes no click).
    site.mode.tiktok = "mute"
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: true })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    // Meanwhile the page does not ask the person to press: the note says it is being pressed, and Post is not ringed.
    equal((await note(tab)).line, "Pressing Post for you", "the note while ReelForge is asked")
    equal(await tab.evaluate(() => document.querySelector("[data-e2e=post_video_button]").style.getPropertyValue("outline")), "", "the ring round Post")
    await tab.locator("[data-e2e=post_video_button]").click()
    await until("the press to be noticed", async () => (await browser.jobRecord(job.id)).pressedByPerson === true)
    held.release()
    await until("the note to say who pressed", async () => (await note(tab)).line === "You pressed Post", 10000)
    await sleep(1500)
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks]), [1, 1], "presses the page saw")
    equal([presses(app).map(entry => entry.status), job.state], [[200], "posting"], "press requests, and the job meanwhile")
    equal((await note(tab)).more, "Waiting for TikTok to say it is up.", "what the note says")
    ok(!job.events.some(event => String(event.detail).startsWith("Not pressed")), `what the app was told (${job.events.map(event => event.detail)})`)
    // TikTok takes it in the end: the person's press is the post.
    await tab.evaluate(() => done())
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(await tab.evaluate(() => fixture.clicks), 1, "presses the page saw in the end")
  },

  async "the person's press while ReelForge is asked to press is refused as theirs, and the page does not ask them for another"({ app, site, browser, video }) {
    // This time ReelForge hears of the person's press before it looks at the request, and refuses it: Post was
    // pressed already. That refusal is not "ReelForge did not press for you, press Post": they just did.
    site.mode.tiktok = "mute"
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await tab.locator("[data-e2e=post_video_button]").click()
    await until("ReelForge to hear of the press", () => job.events.some(event => event.step === "press"))
    held.release()
    await until("the refusal", () => presses(app).some(entry => entry.status === 409))
    await until("the note to say who pressed", async () => (await note(tab)).line === "You pressed Post", 10000)
    await sleep(1500)
    equal([(await note(tab)).line, (await note(tab)).more], ["You pressed Post", "Waiting for TikTok to say it is up."], "the note")
    equal(await tab.evaluate(() => document.querySelector("[data-e2e=post_video_button]").style.getPropertyValue("outline")), "", "the ring round Post")
    ok(!job.events.some(event => String(event.detail).startsWith("Not pressed")), `what the app was told (${job.events.map(event => event.detail)})`)
    equal([await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks]), job.state], [[1, 1], "ready"], "presses the page saw, and the job meanwhile")
    await tab.evaluate(() => done())
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted")
    equal(await tab.evaluate(() => fixture.clicks), 1, "presses the page saw in the end")
  },

  async "Stop in the popup while ReelForge is asked to press wins, though ReelForge said yes first: nothing is clicked, and it is not called nothing posted"({ app, site, browser, video }) {
    // ReelForge says yes at once and its answer takes a moment to come back. The person presses Stop in that
    // moment, on the note's "Pressing Post for you". ReelForge, which has the press as given, takes the stop as
    // "not sure". The tab clicks nothing all the same.
    site.mode.tiktok = "mute"
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: true })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await until(() => `ReelForge to say yes (it is ${job.state})`, () => job.state === "posting")
    const popup = await browser.popup()
    await popup.locator(`[data-job="${job.id}"]`).getByRole("button", { name: "Stop" }).click()
    await until(() => `the stop to be taken (it is ${job.state})`, () => job.state === "unknown")
    held.release()
    await until("the answer to the press", () => presses(app).some(entry => entry.status === 200))
    await sleep(1500)
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks]), [0, 0], "presses the page saw")
    const said = await note(tab)
    equal([said.line, said.tone], ["Stopped", "bad"], "the note")
    ok(said.more.startsWith("You stopped this just as ReelForge said yes to pressing Post on TikTok, and Post was not pressed.") && !said.more.includes("Nothing was posted"), `what the note says (${said.more})`)
    equal(await tab.evaluate(() => document.querySelector("[data-e2e=post_video_button]").style.getPropertyValue("outline")), "", "the ring round Post")
    const kept = await browser.jobRecord(job.id)
    equal([kept.state, kept.error_code, job.state, job.error_code], ["unknown", "unknown_outcome", "unknown", "unknown_outcome"], "the job, here and in ReelForge")
    ok(job.error_message.includes("Post was not pressed"), `what ReelForge's row says (${job.error_message})`)
    ok(!job.events.some(event => String(event.detail).startsWith("Not pressed")), `what the app was told (${job.events.map(event => event.detail)})`)
    await until("the popup to say it", async () => (await popup.locator(`[data-job="${job.id}"]`).textContent()).includes("Post was not pressed"))
    ok(!(await popup.locator(`[data-job="${job.id}"]`).textContent()).includes("Nothing was posted"), "the popup does not say nothing was posted")
  },

  async "Stop in the popup that reaches ReelForge before it decides the press: refused, nothing is clicked, and the page does not ask for a press"({ app, site, browser, video }) {
    site.mode.tiktok = "mute"
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    const popup = await browser.popup()
    await popup.locator(`[data-job="${job.id}"]`).getByRole("button", { name: "Stop" }).click()
    await until(() => `the stop to be taken (it is ${job.state})`, () => job.state === "cancelled")
    held.release()
    await until("the refusal", () => presses(app).some(entry => entry.status === 409))
    await sleep(1500)
    equal(presses(app).map(entry => entry.status), [409], "press requests")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks]), [0, 0], "presses the page saw")
    equal(await note(tab), { tone: "bad", line: "Stopped", more: "You stopped this post. Nothing was posted by ReelForge." }, "the note")
    equal(await tab.evaluate(() => document.querySelector("[data-e2e=post_video_button]").style.getPropertyValue("outline")), "", "the ring round Post")
    ok(!job.events.some(event => String(event.detail).startsWith("Not pressed")), `what the app was told (${job.events.map(event => event.detail)})`)
    equal([job.state, (await browser.jobRecord(job.id)).state], ["cancelled", "cancelled"], "the job, in ReelForge and here")
  },

  async "a newer list that moves YouTube's error hook does not get a press past YouTube's own error"({ app, site, browser, video }) {
    // The app's newer list points the hook for YouTube's error message at nothing. The packed one is used all
    // the same: the engine stops a job at that message before anything is pressed.
    site.mode.youtube = "slow"
    const list = quickSelectors()
    list.platforms.youtube.hooks.error = { any: true, strategies: [{ id: "moved", css: "no-such-error-element" }] }
    app.selectors = list
    await switchOn(browser, app, "youtube")
    const made = app.createBatch(video(), ["youtube"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("studio.youtube.com")
    const job = made.jobs[0]
    await until(() => `the wait for the upload (it is ${job.state} ${job.step})`, () => job.step === "upload" && job.events.some(event => event.step === "visibility"), 30000)
    await tab.evaluate(() => { document.querySelector("ytcp-app").shadowRoot.querySelector(".error-area").textContent = "Daily upload limit reached" })
    await until(() => `the job to end (it is ${job.state})`, () => ["failed", "posted", "unknown"].includes(job.state), 30000)
    equal([job.state, job.error_code, job.error_message], ["failed", "platform_error", "YouTube says: Daily upload limit reached"], "state, code and message")
    equal([await tab.evaluate(() => [fixture.clicks, fixture.posted]), presses(app).length], [[0, false], 0], "presses the page saw, and press requests")
    equal((await browser.storage("local")).selectors?.version, list.version, "the newer list was taken for everything else")
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
