// ReelForge's two local pages: /pair (connect this browser) and /post/<batch> (where each post stands).
//
// The page asks ReelForge, on this computer, once a second, and draws what it is told: the words for each post
// are written in ReelForge (dashboard/extension_api.py), so they are the same here and in its window. The
// extension makes itself known by setting data-rf-extension on <html>; when it hasn't after 2.5 seconds, the
// page says so and shows how to add it. Nothing is loaded from, or sent to, anywhere but ReelForge itself.
(() => {
  "use strict"

  const root = document.documentElement
  const page = root.dataset.rfPage
  const batch = root.dataset.rfBatch || ""
  const key = new URLSearchParams(location.search).get("k") || ""
  const el = id => document.getElementById(id)
  const show = (node, on) => { if (node) node.hidden = !on }
  const write = (node, text) => { if (node && node.textContent !== text) node.textContent = text }

  let answer = null     // what ReelForge last said
  let waited = false    // the extension has had its 2.5 seconds
  let closed = false    // ReelForge stopped answering
  let over = false      // this page's key ran out, or ReelForge was started again: nothing more to ask
  let delay = 1000
  let timer = 0

  const QUICK = 1000, CALM = 5000, SLOWEST = 15000

  // What the extension may say went wrong on this page, and what to do about it.
  const TROUBLE = {
    bad_secret: "This page was used already. In ReelForge, connect your browser again to get a new one.",
    expired: "This page is more than ten minutes old. In ReelForge, connect your browser again to get a new one.",
    extension_outdated: "This extension is older than your ReelForge. On your browser's extensions page, press Reload on ReelForge's card, then reload this page.",
    protocol: "Your ReelForge is older than this extension. Update ReelForge on its Updates tab, then try again.",
    wrong_origin: "This copy of the extension isn't the one ReelForge lets in. Load the extension from ReelForge's own folder (the steps below say how).",
    app_unreachable: "The extension couldn't reach ReelForge. Is ReelForge still open?",
    demo: "ReelForge is showing its demo, which never posts.",
    no_batch: "ReelForge has no posts for this page any more. Start again from its Publish tab.",
  }
  const trouble = code => TROUBLE[code] ||
    `The extension couldn't do this (${code}). Reload this page to try again, or start again from ReelForge.`

  function notice(text, bad) {
    const box = el("notice")
    write(box, text)
    box.classList.toggle("bad", Boolean(bad))
    show(box, Boolean(text))
  }

  // ---- copying (the folder's path, this page's address)

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // No clipboard permission here: the old way, through a text box that is selected for a moment.
      const box = document.createElement("textarea")
      box.value = text
      box.setAttribute("readonly", "")
      box.className = "offscreen"
      document.body.append(box)
      box.select()
      let done = false
      try { done = document.execCommand("copy") } catch { done = false }
      box.remove()
      return done
    }
  }

  for (const button of document.querySelectorAll("[data-copy]")) {
    const label = button.textContent
    button.addEventListener("click", async () => {
      const text = button.dataset.copy === "address" ? location.href : el("folder").textContent
      const done = await copy(text)
      write(button, done ? "Copied" : "Couldn't copy: select it instead")
      button.classList.toggle("done", done && button.classList.contains("btn"))
      setTimeout(() => { write(button, label); button.classList.remove("done") }, 2500)
    })
  }

  // ---- asking ReelForge

  async function ask() {
    const url = page === "pair" ? `/v1/page/pair?k=${encodeURIComponent(key)}`
      : `/v1/page/batch/${batch}?k=${encodeURIComponent(key)}`
    let response
    try {
      response = await fetch(url, { cache: "no-store", credentials: "omit", signal: AbortSignal.timeout(8000) })
    } catch {
      closed = true
      delay = Math.min(delay * 2, SLOWEST)  // ask less and less often while it is gone
      return
    }
    closed = false
    if (response.status === 403 || response.status === 404) {
      over = true  // the key ran out (two hours), or this is another run of ReelForge
      return
    }
    try {
      if (response.ok) answer = await response.json()
    } catch {
      // Not an answer this page understands: keep the last one and ask again.
    }
    delay = QUICK
  }

  async function tick() {
    clearTimeout(timer)
    await ask()
    draw()
    if (over || finished()) return
    // A page nobody is looking at, or a batch with nothing left under way, needs no answer every second.
    const calm = document.hidden || (page === "post" && answer && !answer.jobs.some(job => job.tone === "busy" || job.state === "queued"))
    timer = setTimeout(tick, Math.max(delay, calm ? CALM : QUICK))
  }

  document.addEventListener("visibilitychange", () => { if (!document.hidden && !over && !finished()) tick() })

  // ---- the extension, as far as this page can see it

  const extension = () => root.hasAttribute("data-rf-extension")
  const paired = () => root.dataset.rfPair === "ok" || (answer && answer.state === "paired")
  const finished = () => page === "pair" && paired()
  // "error:<code>" from an attribute the extension sets -> the code
  const failed = value => (value || "").startsWith("error:") ? value.slice(6) : ""

  function setup(on) {
    const where = (answer && answer.extension) || answer || {}
    if (where.folder) write(el("folder"), where.folder)
    if (on && where.folder_there === false) {
      write(el("setup-folder"), `This ReelForge has no extension folder yet (${where.folder}). Update ReelForge on its Updates tab, then come back to this page.`)
    }
    show(el("setup"), on)
  }

  // ---- drawing: the pair page

  function drawPair() {
    const wrong = failed(root.dataset.rfPair)
    if (answer) write(el("note"), answer.note || "")
    if (paired()) {
      write(el("title"), "This browser is connected")
      write(el("lead"), "")
      write(el("done-who"), answer && answer.name ? `ReelForge knows it as ${answer.name}.` : "ReelForge and the extension know each other now.")
      show(el("done"), true)
      setup(false)
      notice("")
      return
    }
    if (over) {
      write(el("lead"), "")
      notice("This page is from an earlier look at ReelForge. In ReelForge, connect your browser again to get a new one.")
      setup(false)
      return
    }
    if (wrong) {
      write(el("lead"), "The extension is here, but it couldn't connect.")
      notice(trouble(wrong), true)
      setup(wrong === "wrong_origin")
      return
    }
    if (closed) {
      notice("ReelForge was closed. Open it again, then connect your browser there to get a new page.")
    } else {
      notice("")
    }
    if (extension()) {
      write(el("lead"), "The extension is here. Connecting…")
      setup(false)
    } else if (waited) {
      write(el("lead"), "ReelForge is waiting for its extension in this browser.")
      setup(true)
    }
  }

  // ---- drawing: the post page

  const rows = new Map()   // job id -> the parts of its row
  const byHand = new Map() // platform -> what ReelForge said after Post by hand

  function megabytes(size) {
    return `${(size / 1024 / 1024).toFixed(1)} MB`
  }

  function makeRow(job) {
    const item = document.createElement("li")
    item.className = "row"
    const dot = document.createElement("span")
    dot.className = "dot"
    dot.setAttribute("aria-hidden", "true")
    const what = document.createElement("div")
    what.className = "what"
    const name = document.createElement("div")
    name.className = "name"
    const title = document.createElement("span")
    const handle = document.createElement("span")
    handle.className = "handle mono"
    name.append(title, handle)
    const line = document.createElement("div")
    line.className = "line"
    line.setAttribute("role", "status")
    const bar = document.createElement("progress")
    bar.max = 100
    bar.hidden = true
    const note = document.createElement("div")
    note.className = "note"
    const acts = document.createElement("div")
    acts.className = "acts"
    const link = document.createElement("a")
    link.className = "btn dark small"
    link.target = "_blank"
    link.rel = "noopener noreferrer"
    link.hidden = true
    const hand = document.createElement("button")
    hand.type = "button"
    hand.className = "btn small"
    hand.hidden = true
    hand.addEventListener("click", () => postByHand(hand.dataset.platform, hand))
    acts.append(link, hand)
    what.append(name, line, bar, note, acts)
    item.append(dot, what)
    el("rows").append(item)
    return { item, title, handle, line, bar, note, acts, link, hand }
  }

  function drawRow(job, stuck) {
    const row = rows.get(job.id) || makeRow(job)
    rows.set(job.id, row)
    const told = byHand.get(job.platform)
    row.item.dataset.tone = job.tone
    write(row.title, job.name)
    write(row.handle, job.handle || "")
    write(row.line, job.line)
    write(row.note, (told && told.message) || job.note || "")
    const uploading = job.state === "uploading" && typeof job.progress === "number"
    show(row.bar, uploading)
    if (uploading) {
      row.bar.value = Math.round(job.progress * 100)
      row.bar.setAttribute("aria-label", `${job.name} upload`)
    }
    // One link at most: the post once it is up, or the upload page when ReelForge couldn't open it for you.
    const href = job.url || (told && told.url) || ""
    show(row.link, href.startsWith("https://"))
    if (href.startsWith("https://")) {
      row.link.href = href
      write(row.link, job.url ? "Open the post" : `Open ${job.name}'s upload page`)
    }
    const handed = job.state === "cancelled" && job.detail === "posted by hand"
    const offer = job.by_hand && !handed && (job.state !== "queued" || stuck) && !closed && !over
    show(row.hand, offer)
    show(row.acts, offer || href.startsWith("https://"))
    row.hand.dataset.platform = job.platform
    write(row.hand, "Post by hand")
    row.hand.setAttribute("aria-label", `Post to ${job.name} by hand`)
  }

  async function postByHand(platform, button) {
    button.disabled = true
    let told = { message: "ReelForge didn't answer. Is it still open?" }
    try {
      const response = await fetch("/v1/page/manual", {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "omit",
        body: JSON.stringify({ k: key, batch, platform }), signal: AbortSignal.timeout(20000),
      })
      const out = await response.json()
      told = { message: String(out.message || "ReelForge couldn't do that."), url: out.ok && !out.opened ? String(out.upload_url || "") : "" }
    } catch {
      // told already says so
    }
    byHand.set(platform, told)
    button.disabled = false
    tick()
  }

  function drawPost() {
    const state = root.dataset.rfBatchState || ""
    const wrong = failed(state)
    const jobs = (answer && answer.jobs) || []
    const queued = jobs.some(job => job.state === "queued")
    const missing = waited && !extension()
    const stuck = missing || state === "unpaired" || Boolean(wrong)

    if (answer) {
      const video = answer.video
      write(el("video"), video ? `${video.name} · ${megabytes(video.size)}` : "")
      write(el("note"), answer.note || "")
      show(el("empty"), jobs.length === 0)
    }
    for (const job of jobs) drawRow(job, stuck)
    for (const [id, row] of rows) {
      if (!jobs.some(job => job.id === id)) {
        row.item.remove()
        rows.delete(id)
      }
    }
    setup(missing && queued)

    if (over) {
      notice("This page can't follow ReelForge any more: it is from an earlier run, or more than two hours old. ReelForge's Publish tab shows where each post stands.")
    } else if (closed) {
      notice("ReelForge was closed. Open it again to see where this stands.")
    } else if (wrong && queued) {
      notice(trouble(wrong), true)
    } else if (state === "unpaired" && queued) {
      notice("The extension in this browser isn't connected to ReelForge yet. In ReelForge, go to Publish and connect your browser, then start the post again. You can also post by hand below.")
    } else if (missing && !queued && jobs.some(job => job.tone === "busy" || job.tone === "ready")) {
      notice("The extension is working on this in another browser. This page only follows along.")
    } else {
      notice("")
    }
  }

  function draw() {
    if (page === "pair") drawPair()
    else drawPost()
  }

  // The extension sets its attributes whenever it gets to it: before this script runs, or well after.
  new MutationObserver(draw).observe(root, { attributes: true })
  for (const name of ["reelforge:extension", "reelforge:pair", "reelforge:batch"]) window.addEventListener(name, draw)
  setTimeout(() => { waited = true; draw() }, 2500)
  tick()
})()
