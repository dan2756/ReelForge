// Press Post for me: only with the person's switch, the post's own setting, their yes for this batch in this
// browser and the app's grant, once, never on X.
import { equal, ok, until, sleep, note, quickSelectors, launch } from "./harness.mjs"
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

// Holds ReelForge's answer to the job's first report of each state named (ReelForge has taken the report), until the
// test lets it go: a ReelForge whose database is busy. (Held for less than the five seconds the extension waits.)
function holdReports(app, job, states) {
  const handle = app.handle.bind(app)
  const held = {}
  for (const state of states) {
    held[state] = { arrived: false }
    held[state].gate = new Promise(resolve => (held[state].release = resolve))
  }
  app.handle = async (request, response) => {
    if (request.method === "POST" && request.url === `/v1/jobs/${job.id}/event`) {
      const end = response.end.bind(response)
      response.end = (...args) => {
        const one = held[job.state]
        if (!one || one.arrived) return end(...args)
        one.arrived = true
        one.gate.then(() => end(...args))
        return response
      }
    }
    return handle(request, response)
  }
  return held
}

// ReelForge is closed as soon as it has answered the job's "ready" (and, with `port`, opened again there), so the
// extension's next request, the press, finds it gone from the port it knew.
function closeAfterReady(app, job, port) {
  const handle = app.handle.bind(app)
  let closed = false
  app.handle = async (request, response) => {
    const out = await handle(request, response)
    if (!closed && job.state === "ready") {
      closed = true
      await app.stop()
      if (port) await app.start(port)
    }
    return out
  }
}

// ReelForge is closed as soon as its answer to the press request has gone out: the extension's next request finds
// nobody.
function closeAfterPress(app) {
  const handle = app.handle.bind(app)
  app.handle = async (request, response) => {
    if (request.method === "POST" && /\/press$/.test(request.url)) response.once("finish", () => app.stop())
    return handle(request, response)
  }
}

// Every ring Post is given on the page from now on, kept in the page as window.rings.
const watchRings = tab => tab.evaluate(() => {
  const button = document.querySelector("[data-e2e=post_video_button]")
  window.rings = []
  new MutationObserver(() => window.rings.push(button.style.getPropertyValue("outline"))).observe(button, { attributes: true, attributeFilter: ["style"] })
})

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

  async "the browser closed while ReelForge is asked for the press: its ending never says nothing was posted"({ app, site, browser, video }) {
    // ReelForge says yes, and the browser is closed before its answer arrives. Nothing was pressed, but ReelForge
    // has the press as given, so the post is "not sure" there, and its words say Post was not pressed.
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: true })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await until(() => `ReelForge to say yes (it is ${job.state})`, () => job.state === "posting")
    await browser.close(true)
    held.release()
    const again = await launch(site, browser.profile)
    try {
      await until(() => `the job to be ended (it is ${job.state})`, () => job.state === "unknown", 20000)
      equal(job.error_message, "The browser was closed, or the extension reloaded, while ReelForge was being asked to press Post on TikTok. Post was not pressed.", "its ending")
      equal(presses(app).length, 1, "press requests")
    } finally {
      await again.close()
    }
  },

  async "the tab closed, or taken to another site, while ReelForge is asked for the press after it said yes: ReelForge's row says Post was not pressed, never that nothing was posted"({ app, site, browser, video }) {
    // ReelForge says yes at once and its answer takes a moment to come back. Its row keeps the first words it is
    // given for an ending, so those must already be true when ReelForge has the press as given.
    await switchOn(browser, app, "tiktok")
    for (const [how, words] of [
      ["closed", "The tab was closed while ReelForge was being asked to press Post on TikTok. Post was not pressed."],
      ["taken to X", "The upload page was left while ReelForge was being asked to press Post on TikTok. Post was not pressed."],
    ]) {
      const held = holdPress(app, { decided: true })
      const made = app.createBatch(video(), ["tiktok"], { press: true })
      await browser.post(app, made.batch, { yes: true })
      const tab = await browser.tab("www.tiktok.com")
      const job = made.jobs[0]
      await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
      await until(() => `ReelForge to say yes (it is ${job.state})`, () => job.state === "posting")
      if (how === "closed") await tab.close()
      else await tab.goto("https://x.com/compose/post")
      await until(() => `the job to end (it is ${job.state})`, () => job.state === "unknown", 20000)
      held.release()
      await sleep(1000)
      equal([job.error_code, job.error_message], ["tab_closed", words], `ReelForge's row, ${how}`)
      const kept = await browser.jobRecord(job.id)
      ok(kept.state === "unknown" && !kept.error_message.includes("Nothing was posted"), `the extension's own record, ${how} (${kept.state}: ${kept.error_message})`)
      equal(presses(app).length, 1, `press requests, ${how}`)
      delete app.handle  // (holdPress's own, for the next round)
      app.log.length = 0
      if (how !== "closed") await tab.close()
    }
  },

  async "the page reloaded while ReelForge is asked for the press: nothing is clicked, and no word says Post was pressed, or that nothing was posted"({ app, browser, video }) {
    // ReelForge says yes at once and its answer takes a moment to come back. The person reloads TikTok's page in
    // that moment: the page that asked is gone, and the reloaded one only watches. Nothing can click for that yes.
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: true })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await until(() => `ReelForge to say yes (it is ${job.state})`, () => job.state === "posting")
    const before = (await browser.jobRecord(job.id)).touched
    await tab.reload()
    await until("the reloaded page to say hello", async () => (await browser.jobRecord(job.id)).touched > before, 20000)
    held.release()
    await until(() => `the job to end (it is ${job.state})`, () => job.state === "unknown", 10000)
    await until("the page to hear of it", async () => (await note(tab))?.line === "Stopped", 10000)
    const said = "ReelForge said yes to pressing Post on TikTok after its upload page was left, so Post was not pressed."
    equal([job.error_code, job.error_message], ["unknown_outcome", said], "ReelForge's row")
    const kept = await browser.jobRecord(job.id)
    equal([kept.state, kept.error_message], ["unknown", said], "the extension's own record")
    ok((await note(tab)).more.startsWith(said), `what the note says (${(await note(tab)).more})`)
    equal([presses(app).length, await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks])], [1, [0, 0]], "press requests, and presses the page saw")
  },

  async "the page reloaded while ReelForge is asked for the press, and its yes comes before the new page has loaded: nothing is clicked, and ReelForge's row says Post was not pressed"({ app, site, browser, video }) {
    // ReelForge says yes at once and its answer takes a moment to come back. The person reloads TikTok's page in that
    // moment, and the new page takes its time to load (TikTok's real one takes seconds): here one image of it waits
    // until the test lets it go, so the new page can't say hello before ReelForge's yes is back.
    await browser.context.addInitScript(() => {
      // Every click on Post in this tab, across the reload (the tab keeps its sessionStorage).
      document.addEventListener("click", event => {
        if (event.target?.closest?.("[data-e2e=post_video_button]")) sessionStorage.rfClicks = String(Number(sessionStorage.rfClicks || 0) + 1)
      }, true)
    })
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: true })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await until(() => `ReelForge to say yes (it is ${job.state})`, () => job.state === "posting")
    let loaded
    const image = new Promise(resolve => (loaded = resolve))
    await tab.route("https://www.tiktok.com/__slow.png", async route => {
      await image
      await route.fulfill({ status: 200, contentType: "image/png", body: Buffer.alloc(0) }).catch(() => {})
    })
    const page = site.pages.tiktok.replace("__MODE__", "ok").replace("</body>", '<img src="/__slow.png" alt=""></body>')
    await tab.route("https://www.tiktok.com/tiktokstudio/upload", route => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: page }))
    try {
      await tab.reload({ waitUntil: "commit" })
      // The page that asked is gone, and the new one has not said hello: the worker knows all the same.
      await until("the extension to know the page that asked is gone", async () => (await browser.jobRecord(job.id)).pressPageGone === true, 10000)
      held.release()
      await until(() => `the job to end (it is ${job.state})`, () => job.state === "unknown", 10000)
      const said = "ReelForge said yes to pressing Post on TikTok after its upload page was left, so Post was not pressed."
      equal([job.error_code, job.error_message], ["unknown_outcome", said], "ReelForge's row")
      const kept = await browser.jobRecord(job.id)
      equal([kept.state, kept.error_code, kept.error_message], ["unknown", "unknown_outcome", said], "the extension's own record")
      // The page that is there now hears of it once it has loaded.
      loaded()
      await until("the new page to hear of it", async () => (await note(tab))?.line === "Stopped", 15000)
      const told = await note(tab)
      ok(told.tone === "bad" && told.more.startsWith(said), `what the note says (${JSON.stringify(told)})`)
      equal([presses(app).length, await tab.evaluate(() => [Number(sessionStorage.rfClicks || 0), fixture.clicks, fixture.posted])], [1, [0, 0, false]], "press requests, and clicks on Post in the tab across both pages")
    } finally {
      loaded()
    }
  },

  async "the page reloaded while ReelForge is asked for the press, which it then refuses: the page's words say only that Post was not pressed"({ app, browser, video }) {
    // The switch is on in the browser, but this ReelForge has no assisted post on TikTok yet: it will say no. The
    // reloaded page can't know the answer, so its words are true either way.
    await forceOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    const before = (await browser.jobRecord(job.id)).touched
    await tab.reload()
    await until("the reloaded page to say hello", async () => (await browser.jobRecord(job.id)).touched > before, 20000)
    held.release()
    await until("the refusal", () => presses(app).some(entry => entry.status === 409))
    // The reloaded page waits 20 seconds for TikTok's sign that a post went out, then gives up.
    await until(() => `the job to end (it is ${job.state})`, () => job.state === "failed", 40000)
    const said = "TikTok's upload page was left while ReelForge was being asked to press Post. Post was not pressed."
    equal([job.error_code, job.error_message], ["tab_closed", said], "ReelForge's row")
    equal(await note(tab), { tone: "bad", line: "Stopped", more: said }, "the note")
    equal(await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks]), [0, 0], "presses the page saw")
  },

  async "the page reloaded after ReelForge refused the press: its words say Post was not pressed and nothing was posted, not that ReelForge was being asked"({ app, browser, video }) {
    // The switch is on in the browser, but this ReelForge has no assisted post on TikTok yet: it says no, and the page
    // asks the person to press Post. They reload it instead. ReelForge was not being asked by then: it had said no.
    await forceOn(browser, app, "tiktok")
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until("the refusal on the page", async () => (await note(tab))?.line === "Check it, then press Post", 30000)
    equal((await browser.jobRecord(job.id)).pressAnswer, "refused", "what the extension keeps of the press")
    await tab.reload()
    // The reloaded page waits 20 seconds for TikTok's sign that a post went out, then gives up.
    await until(() => `the job to end (it is ${job.state})`, () => job.state === "failed", 40000)
    const said = "TikTok's upload page was left before Post was pressed. Nothing was posted."
    equal([job.error_code, job.error_message], ["tab_closed", said], "ReelForge's row")
    equal(await note(tab), { tone: "bad", line: "Stopped", more: said }, "the note")
    equal([presses(app).length, await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks])], [1, [0, 0]], "press requests, and presses the page saw")
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
    equal((await note(tab)).more, `ReelForge did not press for you: its answer did not come back, and it is not answering now (it may have been closed), so nothing was clicked.${LOOK}`, "what the note says")
    equal([presses(app).length, await tab.evaluate(() => fixture.clicks)], [1, 0], "press requests, and presses the page saw")
    const kept = await browser.jobRecord(job.id)
    equal([kept.state, kept.pressAnswer, job.state], ["ready", "lost", "posting"], "the job here, and in ReelForge (which said yes)")
    // ReelForge answers again, and the person presses Post themselves: that press is the post.
    lose.batch = false
    await tab.locator("[data-e2e=post_video_button]").click()
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted", 20000)
    equal([presses(app).length, await tab.evaluate(() => [fixture.clicks, fixture.trustedClicks])], [1, [1, 1]], "press requests, and presses the page saw")
  },

  async "ReelForge closed just before the press is asked: nothing is clicked, and the page says ReelForge is not running"({ app, browser, video }) {
    // ReelForge takes the page's "ready" and is closed before the press request goes, which then finds nobody.
    await switchOn(browser, app, "tiktok")
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    const job = made.jobs[0]
    closeAfterReady(app, job)
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    await until("the note to say why", async () => (await note(tab))?.line === "Check it, then press Post", 30000)
    equal((await note(tab)).more, `ReelForge did not press for you: ReelForge is not running, so it could not allow the press.${LOOK}`, "what the note says")
    equal([presses(app).length, await tab.evaluate(() => fixture.clicks)], [0, 0], "press requests, and presses the page saw")
    // No request reached ReelForge: nothing it could have said yes to, so a later ending may say nothing was posted.
    equal((await browser.jobRecord(job.id)).pressAnswer, "refused", "what the extension keeps of the press")
  },

  async "ReelForge opened again on another port just before the press is asked: the press is asked there, once, and Post is pressed"({ app, browser, video }) {
    await switchOn(browser, app, "tiktok")
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    const job = made.jobs[0]
    closeAfterReady(app, job, 47826)
    await browser.post(app, made.batch, { yes: true })
    await until(() => `posted (it is ${job.state})`, () => job.state === "posted", 30000)
    equal([app.port, presses(app).map(entry => entry.status)], [47826, [200]], "ReelForge's port, and press requests")
    equal(await (await browser.tab("www.tiktok.com")).evaluate(() => [fixture.clicks, fixture.trustedClicks]), [1, 0], "presses the page saw")
  },

  async "the person's own press while the video is going up is the only press, whatever was asked for"({ app, site, browser, video }) {
    // Press Post for me is on, the post's own setting says press and the person said yes. They press Save
    // themselves while YouTube is still taking the video in, and the page shows nothing yet ("held_mute": it
    // takes no click, and the upload ends only when the test says). When everything is filled in, the form is
    // still there: a second press would post twice.
    site.mode.youtube = "held_mute"
    await switchOn(browser, app, "youtube")
    const made = app.createBatch(video(), ["youtube"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("studio.youtube.com")
    const job = made.jobs[0]
    await until(() => `the wait for the upload (it is ${job.state} ${job.step})`, () => job.step === "upload" && job.events.some(event => event.step === "visibility"), 30000)
    await tab.locator("#done-button").click()
    await until("the press to be noticed", async () => (await browser.jobRecord(job.id)).pressedByPerson === true)
    await tab.evaluate(() => fixture.finishUpload())
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

  async "Stop in the popup while the page's ready is on its way to ReelForge, with ReelForge slow to take the stop: Post is not ringed, and nobody is asked to press it"({ app, browser, video }) {
    // The note says "Pressing Post for you" just before the page tells ReelForge it is ready, so a Stop in answer to
    // it lands while that report is on its way. ReelForge is slow to answer it and the stop alike. By the time the
    // page asks for the press the post is over here, though ReelForge has not yet answered the stop.
    await switchOn(browser, app, "tiktok")
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    const job = made.jobs[0]
    const held = holdReports(app, job, ["ready", "cancelled"])
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    await until(() => `ready at ReelForge (it is ${job.state})`, () => held.ready.arrived, 30000)
    await watchRings(tab)
    const popup = await browser.popup()
    await popup.locator(`[data-job="${job.id}"]`).getByRole("button", { name: "Stop" }).click()
    await sleep(300)
    held.ready.release()
    const lines = []
    for (const end = Date.now() + 2500; Date.now() < end; await sleep(100)) lines.push((await note(tab))?.line)
    ok(held.cancelled.arrived, "ReelForge has the stop, and has not answered it yet")
    ok(!lines.includes("Check it, then press Post"), `the notes while ReelForge takes the stop (${[...new Set(lines)]})`)
    // The page hears of the stop at once, not when ReelForge has answered it.
    equal(await note(tab), { tone: "bad", line: "Stopped", more: "You stopped this post." }, "the note before ReelForge answers the stop")
    held.cancelled.release()
    await until("the stop's words", async () => (await note(tab)).more !== "You stopped this post.", 10000)
    equal(await note(tab), { tone: "bad", line: "Stopped", more: "You stopped this post. Nothing was posted by ReelForge." }, "the note")
    equal(await tab.evaluate(() => [window.rings.filter(Boolean), fixture.clicks, fixture.trustedClicks]), [[], 0, 0], "rings round Post, and presses the page saw")
    equal(presses(app).length, 0, "press requests")
    ok(!job.events.some(event => String(event.detail).startsWith("Not pressed")), `what the app was told (${job.events.map(event => event.detail)})`)
    equal([job.state, (await browser.jobRecord(job.id)).state], ["cancelled", "cancelled"], "the job, in ReelForge and here")
  },

  async "Cancel in ReelForge while the extension asks it for the press: nothing is clicked, Post is not ringed, and the page says it was ended there"({ app, browser, video }) {
    // The person cancels the post in ReelForge's window while the press request waits for ReelForge, which then
    // refuses the press: the post is not ready any more. The extension's next look at the post would be up to half
    // a minute away; the page must not ask for a press meanwhile, or the post would go up with ReelForge calling it
    // cancelled.
    await switchOn(browser, app, "tiktok")
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await watchRings(tab)
    Object.assign(job, { state: "cancelled", detail: "cancelled in ReelForge" })
    held.release()
    await until("the refusal", () => presses(app).some(entry => entry.status === 409))
    await until("the note to say so", async () => (await note(tab)).line !== "Pressing Post for you", 10000)
    await sleep(1000)
    equal(await note(tab), { tone: "bad", line: "Stopped", more: "This post was ended in ReelForge." }, "the note")
    equal(await tab.evaluate(() => [window.rings.filter(Boolean), fixture.clicks, fixture.trustedClicks]), [[], 0, 0], "rings round Post, and presses the page saw")
    equal([job.state, (await browser.jobRecord(job.id)).state], ["cancelled", "cancelled"], "the job, in ReelForge and here")
    equal(app.requests("POST", /\/event$/).filter(entry => entry.status !== 200).map(entry => [entry.status, entry.body.state, entry.body.detail]), [], "reports ReelForge turned down")
  },

  async "Cancel in ReelForge while the extension asks it for the press, and ReelForge closed right after its no: the page says it was ended there, and asks for no press"({ app, browser, video }) {
    // ReelForge refuses the press (the post is cancelled there), and is closed as soon as it has answered: the
    // extension can't ask it anything more. Its no says how it has the post, and that is enough.
    await switchOn(browser, app, "tiktok")
    closeAfterPress(app)
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await watchRings(tab)
    Object.assign(job, { state: "cancelled", detail: "cancelled in ReelForge" })
    held.release()
    await until("the refusal", () => presses(app).some(entry => entry.status === 409))
    await until("the note to say so", async () => (await note(tab)).line !== "Pressing Post for you", 10000)
    await sleep(1000)
    equal(await note(tab), { tone: "bad", line: "Stopped", more: "This post was ended in ReelForge." }, "the note")
    equal(await tab.evaluate(() => [window.rings.filter(Boolean), fixture.clicks, fixture.trustedClicks]), [[], 0, 0], "rings round Post, and presses the page saw")
    await until("ReelForge to be closed", () => app.server === null)
    const kept = await browser.jobRecord(job.id)
    equal([kept.state, kept.detail], ["cancelled", "cancelled in ReelForge"], "the job here")
    ok(!job.events.some(event => String(event.detail).startsWith("Not pressed")), `what the app was told (${job.events.map(event => event.detail)})`)
  },

  async "Cancel in an older ReelForge, whose no to the press does not say how it has the post, closed right after: Post is not ringed, and the page says not to press it if the post was cancelled"({ app, browser, video }) {
    // As above, with a ReelForge whose no carries nothing more: the extension can't learn that the post was ended
    // there. It does not ask for a press as if all were well.
    app.pressRows = false
    await switchOn(browser, app, "tiktok")
    closeAfterPress(app)
    const held = holdPress(app, { decided: false })
    const made = app.createBatch(video(), ["tiktok"], { press: true })
    await browser.post(app, made.batch, { yes: true })
    const tab = await browser.tab("www.tiktok.com")
    const job = made.jobs[0]
    await until(() => `the press request (it is ${job.state} ${job.step})`, () => held.arrived, 30000)
    await watchRings(tab)
    Object.assign(job, { state: "cancelled", detail: "cancelled in ReelForge" })
    held.release()
    await until("the refusal", () => presses(app).some(entry => entry.status === 409))
    await until("the note to say so", async () => (await note(tab)).line !== "Pressing Post for you", 15000)
    await sleep(1000)
    const said = "ReelForge said no to pressing Post, and it can't be reached now to say why. If you cancelled this post in ReelForge, don't press Post. Otherwise check it, then press Post yourself."
    equal(await note(tab), { tone: "warn", line: "ReelForge said no", more: `${said}${LOOK}` }, "the note")
    equal(await tab.evaluate(() => [window.rings.filter(Boolean), fixture.clicks, fixture.trustedClicks]), [[], 0, 0], "rings round Post, and presses the page saw")
    equal((await browser.jobRecord(job.id)).pressAnswer, "refused", "what the extension keeps of the press")
    // ReelForge is opened again, and the extension's next look takes its ending (the open popup makes it sooner).
    await until("ReelForge to be closed", () => app.server === null)
    await app.start(app.port)
    const popup = await browser.popup()
    await until("the page to hear of it", async () => (await note(tab))?.more === "This post was ended in ReelForge.", 40000)
    await popup.close()
    equal([(await browser.jobRecord(job.id)).state, await tab.evaluate(() => fixture.clicks)], ["cancelled", 0], "the job here, and presses the page saw")
  },

  async "a newer list that moves YouTube's error hook does not get a press past YouTube's own error"({ app, site, browser, video }) {
    // The app's newer list points the hook for YouTube's error message at nothing. The packed one is used all
    // the same: the engine stops a job at that message before anything is pressed. ("held": the upload never
    // ends by itself, so the error comes first however slow the machine is.)
    site.mode.youtube = "held"
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
