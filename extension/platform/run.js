// Runs one job in the tab the worker opened for it: signed in, attach, fill, then stop at the platform's own
// button with a note for the person. It presses that button only when the person asked for that, the app grants
// it, and never on X. Afterwards it keeps watching for the platform's own sign that the post went out.
//
// "void": the worker's executeScript call would otherwise wait for this whole job to end before it returned.
void (async () => {
  // One run per page. The worker injects again after every full page load, and may inject twice into one.
  if (globalThis.__reelforgeRun) return
  globalThis.__reelforgeRun = true
  const rf = globalThis.__reelforge
  const hello = await rf.send({ t: "job.hello" })
  // Not a job's tab (any more): do nothing at all.
  if (!hello?.ok) return
  const job = (rf.job = hello.job)
  rf.sel = { hooks: {}, ...hello.selectors }
  const flow = rf.flows[job.platform]
  const P = job.name

  // While the page works the worker hears from it at least every 20 seconds, so a frozen page can be told
  // from a slow upload.
  const alive = setInterval(() => rf.send({ t: "job.alive" }), 20000)

  let resume = null
  chrome.runtime.onMessage.addListener((message, from, reply) => {
    if (from.id !== chrome.runtime.id) return
    if (message?.t === "ping") reply({ alive: true })
    if (message?.t === "override") resume?.()
    if (message?.t === "stop") {
      rf.stopped = true
      clearInterval(alive)
      rf.unoutline()
      rf.say("Stopped", "bad", message.message || "")
      resume?.()
    }
  })

  const handle = text => String(text || "").trim().replace(/^@/, "").toLowerCase()

  // The post's address and id, where the page shows them. YouTube shows the link before the video is published
  // and takes it away with the dialog, so what was seen once is kept.
  const known = {}
  function postInfo() {
    const href = rf.probe("post_link")?.getAttribute("href")
    try {
      if (href) known.url = new URL(href, location.href).href
    } catch {
      // Not an address after all.
    }
    const id = rf.read("post_link")
    if (id) known.post_id = id
    return { ...known }
  }

  async function posted() {
    rf.unoutline()
    rf.say("Posted", "good")
    await rf.report({ state: "posted", step: "confirm", ...postInfo() })
  }

  // The person's own press of the final button (or Ctrl+Enter where that posts, which is X). If the tab then
  // closes before the platform confirms, ReelForge knows to ask "did it post?" instead of saying nothing
  // happened. Either way the button has to be there and switched on: a key pressed in a text box on a page
  // that cannot post yet is not a press.
  let pressed = job.state === "posting" || job.pressedByPerson
  function noticePress() {
    const tell = () => {
      if (pressed) return
      pressed = true
      rf.send({ t: "job.event", pressedByPerson: true })
    }
    const live = () => {
      const button = rf.probe(flow.final)
      return button && rf.enabled(button) ? button : null
    }
    const onClick = event => {
      const button = live()
      if (event.isTrusted && button && event.composedPath().includes(button)) tell()
    }
    const onKey = event => {
      if (flow.keys && event.isTrusted && event.key === "Enter" && (event.ctrlKey || event.metaKey) && live()) tell()
    }
    for (const doc of new Set([document, rf.probe(flow.final)?.ownerDocument || document])) {
      doc.addEventListener("click", onClick, true)
      doc.addEventListener("keydown", onKey, true)
    }
  }

  // The form closing after the final button was pressed. On YouTube and X that is what a post going out looks
  // like, in any language. It has to stay closed for two seconds, so the platform's own sign (with the post's
  // link) gets the chance to come first.
  let goneSince = 0
  function formGone() {
    const form = rf.sel.success?.gone_after_press
    if (!pressed || !form || rf.probe(form)) {
      goneSince = 0
      return false
    }
    goneSince ||= Date.now()
    return Date.now() - goneSince >= 2000
  }

  // The platform's own sign that the post went out. If it is already showing when the page is ready and nobody
  // has pressed anything, it is not a sign of posting on this page at all, and is ignored from then on.
  let signBroken = false
  const sent = () => (!signBroken && rf.sign("success")) || formGone()

  // The sign is showing. It means "posted" only when a press was seen first: the page that lists a channel's
  // posts looks the same to this script whether a post just went out or the person simply went there. Without
  // a press nobody can say, so it is "not sure": never "posted" (which would also unlock Press Post for me),
  // and never "nothing was posted" against the platform's own sign.
  async function signSeen() {
    if (pressed) return posted()
    throw new rf.Stop("unknown_outcome", `${P} shows its sign that a post went out, but ReelForge did not see ${flow.button} being pressed. Check your profile before you post again.`, { state: "unknown" })
  }

  async function watch() {
    rf.phase = "watch"
    noticePress()
    // The wait is a little longer than the 45 minutes ReelForge itself waits on a ready post.
    await rf.waitFor(sent, { timeout: 46 * 60 * 1000, pace: 1000 })
    await signSeen()
  }

  // Press Post for the person. Asked for once; the app's ledger grants it once. From the click on, the job
  // ends as posted (the platform's own sign) or as "not sure", never as failed: a page that shows nothing for
  // a few seconds may still have taken the click, and a post called failed would be offered again.
  async function press(button, cannot) {
    const stay = async why => {
      rf.say("Check it, then press Post", "ready", `ReelForge did not press for you: ${why}`)
      await rf.report({ step: "ready", detail: `not pressed: ${why}` })
    }
    if (cannot) return stay(cannot)
    const grant = await rf.send({ t: "job.press" })
    if (!grant?.ok) return stay(grant?.message || "the extension did not answer.")
    // From here on nothing is ever pressed again, whatever happens.
    rf.phase = "watch"
    rf.unoutline()
    rf.say("Pressing Post for you", "work")
    const before = location.href
    pressed = true
    rf.pressFinal(button)
    await rf.report({ step: "press" })
    const asking = () => rf.probe("post_confirm") || rf.sign("challenge")
    let reacted = true
    try {
      await rf.waitFor(() => rf.sign("success") || asking() || !button.isConnected || !rf.visible(button) || !rf.enabled(button) || location.href !== before,
        { timeout: 8000 })
    } catch (error) {
      if (error.code !== "timeout") throw error
      // Nothing showed. That is not proof the click was ignored, so nothing is concluded yet and nothing is
      // pressed again: the person is told to look, and the watch for the platform's sign goes on. If they
      // press the button themselves now, that sign is how ReelForge hears of it.
      reacted = false
      rf.say(`${P} has not reacted`, "warn", `ReelForge pressed ${flow.button} and the page shows nothing yet. Look at this tab: if it has not posted, press ${flow.button} yourself.`)
      await rf.report({ step: "press", detail: `${P} showed no reaction to the press yet` })
    }
    if (reacted) {
      await rf.report({ step: "confirm" })
      if (asking() && !rf.sign("success")) rf.say(`${P} is asking you something`, "warn", "Answer it in this tab. ReelForge will not.")
    }
    try {
      await rf.waitFor(sent, { timeout: rf.sel.success?.timeout_ms || 5 * 60 * 1000, pace: 500 })
    } catch (error) {
      if (error.code !== "timeout") throw error
      if (reacted) throw new rf.Stop("unknown_outcome", `Post was pressed on ${P} and no confirmation was seen. Check your profile before you post again.`, { state: "unknown", step: "confirm" })
      throw new rf.Stop("press_rejected", `ReelForge pressed ${flow.button} on ${P} and saw no reaction, and no sign that it posted. Check your profile before you post again.`, { state: "unknown", step: "press" })
    }
    await posted()
    return true
  }

  async function prepare() {
    await rf.step("opening", "signed_in", `Checking you're signed in to ${P}`)
    await rf.waitFor(() => rf.sign("signed_in"), { timeout: rf.sel.signed_in?.timeout_ms || 30000, step: "signed_in" }).catch(error => {
      if (error.code !== "timeout") throw error
      throw new rf.Stop("hook_not_found", `${P} has changed its page: ReelForge could not tell whether you are signed in.`, { detail: "hook=signed_in" })
    })
    const seen = rf.read("account")
    await rf.report({ step: "signed_in", account: { signed_in: true, handle: seen ? "@" + handle(seen) : null } })
    if (seen && job.handle && handle(seen) !== handle(job.handle) && !job.accountOk) {
      // A different account is a question for the person, never a silent stop and never a silent go.
      rf.say(`Signed in to ${P} as @${handle(seen)}`, "warn", `ReelForge has ${job.handle} for this channel. Open the ReelForge extension to continue or stop.`)
      await rf.report({ step: "signed_in", detail: `Signed in as @${handle(seen)}, but ReelForge has ${job.handle}. Waiting for your answer in the extension.`, paused: { seen: "@" + handle(seen) } })
      await new Promise(resolve => (resume = resolve))
      rf.guard()
    }

    await rf.step("opening", "find_input", `Finding where ${P} takes the video`)
    if (flow.open) await flow.open()
    const input = await rf.find("file_input")
    if (input.files?.length) throw new rf.Stop("platform_error", `${P}'s page already has a video on it. Finish or clear that one first.`)

    await rf.step("uploading", "attach", "Handing the video to the page", { progress: 0 })
    const file = await rf.fetchVideo(fraction => rf.progress("uploading", "attach", fraction))
    // The worker is told before the page gets the video. From here a reload cannot be recovered from, and a
    // script that starts over on a fresh page must never hand the video over a second time.
    await rf.report({ state: "uploading", step: "attach", progress: 1, attached: true })
    rf.guard()
    if (!rf.attach(input, file)) {
      throw new rf.Stop("hook_not_found", `${P}'s page did not take the video. You can add it by hand in this tab.`, { detail: "hook=file_input files not set" })
    }
    rf.phase = "filled"
    noticePress()

    const end = (await flow.fill(job)) || {}
    // The person may have been quicker than the page: YouTube lets a video be published while it is still
    // going up. A form that is gone by now was either sent (a press was seen) or closed.
    const form = rf.sel.success?.gone_after_press
    if (form && !rf.probe(form)) {
      if (pressed) return posted()
      // The video was on the page and the form is gone. Most likely it was closed, but a press this script
      // did not see would look the same, so the person is asked instead of being told nothing was posted.
      throw new rf.Stop("unknown_outcome", `${P}'s upload form closed before ReelForge had finished, and it saw no press of ${flow.button}. Check your profile before you post again.`, { state: "unknown" })
    }
    const button = end.hold ? rf.probe(end.hold) : await rf.find(flow.final)
    if (button) rf.outline(button)
    if (!pressed && rf.sign("success")) signBroken = true
    let hint = end.more || (flow.button === "Post" ? "Nothing is posted until you do." : `On ${P} the button says ${flow.button}.`)
    // Where the page does not show which account it is on, nothing was checked: the person has to look.
    if (!rf.sel.hooks.account && job.handle) hint += ` ReelForge has ${job.handle} for this channel, and could not check the account here: look at which one this tab is on.`
    rf.say("Check it, then press Post", "ready", hint)
    await rf.report({ state: "ready", step: "ready", detail: end.detail || (rf.fallback ? `${P}'s page has changed a little; still working` : undefined), ...postInfo() })
    rf.guard()
    clearInterval(alive)
    const done = job.press && !flow.neverPress && await press(button, end.hold ? "something on the page is still yours to answer." : end.noPress)
    if (!done) await watch()
  }

  // A fresh page for a job whose video was already attached: the form is gone. The only good news left is the
  // platform's own "posted" page (TikTok goes there after Post), and only when Post was pressed before the
  // page changed. Nothing is ever attached or pressed again.
  async function afterReload() {
    rf.phase = "watch"
    clearInterval(alive)
    try {
      await rf.waitFor(() => rf.sign("success"), { timeout: 20000 })
    } catch (error) {
      if (error.code !== "timeout") throw error
      if (pressed) {
        throw new rf.Stop("unknown_outcome", `Post was pressed on ${P} and no confirmation was seen. Check your profile before you post again.`, { state: "unknown" })
      }
      throw new rf.Stop("tab_closed", `${P}'s upload page was left ${job.state === "ready" ? "before Post was pressed" : "before the post was ready"}. Nothing was posted.`)
    }
    await signSeen()
  }

  try {
    if (!flow) throw new rf.Stop("extension_outdated", `This extension does not know ${P} yet. Reload it from your ReelForge folder.`)
    // Every box this platform has must come with its text, even if that text is empty. Nothing is made up here.
    if (flow.fields.some(field => typeof job.text?.[field] !== "string")) {
      throw new rf.Stop("extension_outdated", `ReelForge did not send the text this extension fills in on ${P}. Reload the extension from your ReelForge folder.`)
    }
    if (job.attached || job.state === "ready" || job.state === "posting") await afterReload()
    else await prepare()
  } catch (error) {
    clearInterval(alive)
    if (error.code === "stopped" || rf.stopped) return
    // Anything that is not one of the known stops is the page surprising the script: say so, with no page content.
    const known = error instanceof rf.Stop
    const code = known ? error.code : "hook_not_found"
    const message = known ? error.message : `${P}'s page did something ReelForge did not expect. You can finish by hand in this tab.`
    rf.unoutline()
    rf.say(error.state === "unknown" ? "Not sure it posted" : "Stopped", "bad", message)
    await rf.report({
      state: error.state || "failed", step: error.step || rf.stepNow, error_code: code, error_message: message,
      detail: known ? error.detail : `script error: ${String(error?.message || error).slice(0, 120)}`,
      ...(code === "not_signed_in" ? { account: { signed_in: false, handle: null } } : {}),
    })
  }
})()
