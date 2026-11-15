// What goes wrong before a post is ready, and what the person and the app are told about it.
import { equal, ok, until, sleep, note, quickSelectors } from "./harness.mjs"
import { toReady } from "./posting.test.mjs"

// Starts a job that is expected to stop, and waits for it to.
async function stopped({ app, browser, video }, platform, film = video()) {
  if (!app.paired.length) equal(await browser.pair(app), "ok", "pairing")
  const made = app.createBatch(film, [platform])
  await browser.post(app, made.batch)
  const job = made.jobs[0]
  await until(() => `the ${platform} job to stop (it is ${job.state}: ${job.step})`, () => job.state === "failed", 40000)
  return job
}

const signedOut = (platform, host) => async t => {
  t.site.mode[platform] = "signed_out"
  const job = await stopped(t, platform)
  equal([job.error_code, job.step], ["not_signed_in", platform === "youtube" ? "open_tab" : "signed_in"], "error code and step")
  ok(/sign in/i.test(job.error_message), `message (${job.error_message})`)
  equal(t.app.requests("GET", /\/file$/).length, 0, "requests for the video")
  if (host) equal((await note(await t.browser.tab(host))).line, "Stopped", "the note on the page")
  // What was seen is passed on with the next heartbeat, apart from whether ReelForge's own browser is logged in.
  if (platform !== "youtube") {
    const popup = await t.browser.popup()
    await until("a heartbeat with the account", () => t.app.heartbeats.at(-1)?.accounts?.[platform])
    equal(t.app.heartbeats.at(-1).accounts[platform], { signed_in: false, handle: null }, "account in the heartbeat")
    await popup.close()
  }
}

export const tests = {
  "TikTok signed out: the redirect to its log-in page is reported as not signed in": signedOut("tiktok", "www.tiktok.com"),
  "YouTube signed out: a tab sent to Google's sign-in is reported as not signed in": signedOut("youtube"),
  "Instagram signed out: its log-in form is reported as not signed in": signedOut("instagram", "www.instagram.com"),
  "X signed out: the redirect to its log-in flow is reported as not signed in": signedOut("x", "x.com"),

  async "signed in is told to the app too, with the handle where the page shows one"(t) {
    const { job } = await toReady(t, "x", t.video())
    equal(job.state, "ready", "state")
    const popup = await t.browser.popup()
    await until("a heartbeat with the account", () => t.app.heartbeats.at(-1)?.accounts?.x)
    equal(t.app.heartbeats.at(-1).accounts.x, { signed_in: true, handle: "@fixtureuser" }, "account in the heartbeat")
    await popup.close()
  },

  async "a hook taken out of the page: hook_not_found, with the step and what was tried"(t) {
    t.site.mode.tiktok = "no_post"
    t.app.selectors = quickSelectors()
    const job = await stopped(t, "tiktok")
    equal([job.error_code, job.step], ["hook_not_found", "upload"], "error code and step")
    equal(job.error_message, "TikTok has changed its page: ReelForge could not find the Post button. You can finish by hand in this tab.", "message")
    // The diagnostic names the ways that were tried and how many elements each found. No page content.
    equal(job.detail, "hook=post_button tried=data-e2e:0,role:0,text:0", "detail")
    const tab = await t.browser.tab("www.tiktok.com")
    equal(await note(tab), { tone: "bad", line: "Stopped", more: job.error_message }, "the note on the page")
    // Everything up to there is still in the tab, for the person to finish by hand.
    equal(await tab.evaluate(() => [fixture.files.length, document.querySelector(".public-DraftEditor-content").innerText.trim()]), [1, t.app.videos[0].text.tiktok.caption], "file and caption left in place")
  },

  async "a page with no way in at all: hook_not_found at the first step"(t) {
    t.site.mode.tiktok = "no_input"
    t.app.selectors = quickSelectors()
    const job = await stopped(t, "tiktok")
    equal([job.error_code, job.step, job.detail], ["hook_not_found", "signed_in", "hook=signed_in"], "error code, step and detail")
  },

  async "a verification: challenge is reported and the page is not touched"(t) {
    t.site.mode.tiktok = "captcha"
    const job = await stopped(t, "tiktok")
    equal(job.error_code, "challenge", "error code")
    ok(job.error_message.includes("TikTok is asking you to verify"), `message (${job.error_message})`)
    const tab = await t.browser.tab("www.tiktok.com")
    await sleep(500)
    equal(await tab.evaluate(() => fixture.touched), [], "clicks on the verification")
  },

  async "the platform's own error is passed on in its words"(t) {
    t.site.mode.youtube = "error"
    const job = await stopped(t, "youtube")
    equal([job.error_code, job.error_message], ["platform_error", "YouTube says: Daily upload limit reached"], "error")
  },

  async "a video replaced after the post was confirmed: changed, and nothing is attached"(t) {
    t.app.fileChanged = true
    const job = await stopped(t, "tiktok")
    equal([job.error_code, job.step], ["changed", "attach"], "error code and step")
    equal(await (await t.browser.tab("www.tiktok.com")).evaluate(() => fixture.files.length), 0, "files given to the page")
  },

  async "bytes that do not match the confirmed video's hash: changed, and nothing is attached"(t) {
    t.app.corrupt = true
    const job = await stopped(t, "tiktok")
    equal(job.error_code, "changed", "error code")
    equal(await (await t.browser.tab("www.tiktok.com")).evaluate(() => fixture.files.length), 0, "files given to the page")
  },

  async "an upload page that is not on the platform's own site is never opened"(t) {
    t.app.uploadPages.tiktok = "https://reelforge.tools/somewhere-else"
    const job = await stopped(t, "tiktok")
    equal([job.error_code, job.step], ["extension_outdated", "open_tab"], "error code and step")
    await sleep(300)
    equal(t.browser.tabs("reelforge.tools").length + t.browser.tabs("www.tiktok.com").length, 0, "tabs opened")
  },

  async "a different account is a question, not a stop: continue from the popup"(t) {
    t.site.mode.x = "wrong_account"
    equal(await t.browser.pair(t.app), "ok", "pairing")
    const made = t.app.createBatch(t.video(), ["x"])
    await t.browser.post(t.app, made.batch)
    const job = made.jobs[0]
    await until("the app to be told", () => job.detail?.startsWith("Signed in as @someoneelse, but ReelForge has @fixtureuser"))
    const tab = await t.browser.tab("x.com")
    equal(await note(tab), { tone: "warn", line: "Signed in to X as @someoneelse", more: "ReelForge has @fixtureuser for this channel. Open the ReelForge extension to continue or stop." }, "the note on the page")
    await sleep(600)
    equal([job.state, await tab.evaluate(() => fixture.files.length)], ["opening", 0], "nothing is attached while it waits")
    const popup = await t.browser.popup()
    const card = popup.locator(`[data-job="${job.id}"]`)
    await until("the question in the popup", async () => (await card.textContent()).includes("signed in to X as @someoneelse"))
    await card.getByRole("button", { name: "Post as @someoneelse" }).click()
    await until(() => `ready (it is ${job.state})`, () => job.state === "ready")
    ok(job.events.some(event => event.detail === "You chose to post as @someoneelse."), "the choice is on the record")
    equal(await tab.evaluate(() => fixture.files.length), 1, "files given to the page")
  },

  async "a different account: Stop in the popup ends it with wrong_account"(t) {
    t.site.mode.x = "wrong_account"
    equal(await t.browser.pair(t.app), "ok", "pairing")
    const made = t.app.createBatch(t.video(), ["x"])
    await t.browser.post(t.app, made.batch)
    const job = made.jobs[0]
    await until("the app to be told", () => job.detail?.startsWith("Signed in as @someoneelse"))
    const popup = await t.browser.popup()
    await popup.locator(`[data-job="${job.id}"]`).getByRole("button", { name: "Stop" }).click()
    await until("the job to stop", () => job.state === "failed")
    equal(job.error_code, "wrong_account", "error code")
    const tab = await t.browser.tab("x.com")
    equal([await tab.evaluate(() => fixture.files.length), (await note(tab)).line], [0, "Stopped"], "nothing attached, and the note")
  },
}
