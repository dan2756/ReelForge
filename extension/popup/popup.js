// The popup: is ReelForge there, what is ready to post, what is being posted right now.
//
// A post started here goes through two screens before anything opens: choose the platforms, then read the
// exact text and the account for each one. Only the second screen's button opens the upload pages.
import { PLATFORMS, PRESS_PLATFORMS, platformName, isFinal, advice, stateLine } from "../shared/consts.js"
import { h } from "../shared/dom.js"

const main = document.getElementById("main")
const pill = document.getElementById("status")
const session = chrome.storage.session

const ask = (t, more = {}) => chrome.runtime.sendMessage({ t, ...more }).catch(() => null)

let ui = null                // the worker's picture of ReelForge
let jobs = []                // this browser's own jobs
let view = { name: "home" }  // home, choose or confirm
let busy = ""                // the button that is working
let flash = ""               // one line of feedback, until the next action
let drawn = ""

// ---- small pieces --------------------------------------------------------------------------------------------

// A button that shows it is working and cannot be pressed twice.
function button(label, key, action, kind = "", off = false) {
  return h("button", { type: "button", class: kind, data: { key }, disabled: off || busy !== "", text: busy === key ? "Working" : label, onclick: () => run(key, action) })
}

// A link to a post. Only a web address is ever made into a link: the address comes from a platform's page.
function postLink(url) {
  return /^https:\/\//.test(url || "") ? h("p", { class: "small" }, h("a", { href: url, target: "_blank", rel: "noreferrer", text: "Open the post" })) : null
}

async function run(key, action) {
  if (busy) return
  busy = key
  flash = ""
  draw()
  try {
    await action()
  } finally {
    busy = ""
    await refresh()
  }
}

const megabytes = bytes => (bytes >= 1048576 ? `${Math.round(bytes / 1048576)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`)

const REVIEW = {
  approved: "approved", stale: "review is for an earlier cut", rejected: "rejected in review", unreviewed: "not reviewed yet",
  qa_failed: "failed its checks", none: "not checked by ReelForge", demo: "example video",
}
const FIELD = { caption: "Caption", title: "Title", description: "Description", text: "Post" }
const WORD = {
  none: "not posted", queued: "waiting", claimed: "starting", opening: "starting", uploading: "in progress", filling: "in progress",
  ready: "ready to press", posting: "posting", posted: "posted", failed: "stopped", cancelled: "cancelled", expired: "never started",
  unknown: "not sure", spotted: "already there",
}
const tone = state => (state === "posted" || state === "spotted" ? "good" : state === "failed" || state === "unknown" ? "bad"
  : state === "none" || state === "cancelled" || state === "expired" ? "" : "busy")
// A platform takes a new post when nothing is on it and nothing is on its way.
const free = state => !state || ["none", "failed", "cancelled", "expired"].includes(state)

function texts(text) {
  return Object.keys(FIELD).filter(field => typeof text?.[field] === "string").map(field => ({ field, label: FIELD[field], value: text[field] }))
}

// ---- what is on screen ---------------------------------------------------------------------------------------

function connectionScreen() {
  const screens = {
    not_running: ["ReelForge is not running", "Open ReelForge on this computer. This extension only works together with the app: it attaches your finished video and fills in the text, here in your own browser."],
    not_connected: ["Not connected yet", "ReelForge is open, but this browser is not paired with it. In ReelForge, go to Publish and connect your browser, or type reelforge extension pair in a terminal. ReelForge opens a page here that pairs the two."],
    demo: ["ReelForge is showing its demo", "Posting is off while ReelForge shows example videos. Open your own channel to post."],
    extension_outdated: ["This extension is too old for your ReelForge", "Open your browser's extensions page and press Reload on ReelForge. That loads the newer copy from your ReelForge folder."],
    app_outdated: ["Your ReelForge is too old for this extension", "Update ReelForge, then look again."],
  }
  const [title, text] = screens[ui.connection]
  return [
    h("section", { data: { screen: ui.connection } },
      h("h3", { text: title }),
      h("p", { class: "soft", text }),
      h("div", { class: "row" }, button("Look again", "retry", () => refresh(true))),
    ),
    // A job can still be on screen when ReelForge has gone away under it.
    jobs.length ? jobsSection() : null,
  ]
}

function progressBar(job) {
  if (isFinal(job.state) || job.state === "ready" || job.paused) return null
  const known = typeof job.progress === "number"
  return h("div", { class: known ? "bar" : "bar unknown", role: "progressbar", "aria-label": "Progress", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": known ? String(Math.round(job.progress * 100)) : null },
    h("i", { style: known ? `width:${Math.round(job.progress * 100)}%` : null }))
}

// Everything a job's text holds, each with its own Copy, and the platform's upload page: enough to finish a
// stopped post by hand. The video itself is in ReelForge, which is the only one that can show the file.
function byHand(job) {
  return h("details", { data: { hand: job.id } },
    h("summary", { class: "small", text: "Finish by hand" }),
    h("div", { class: "stack tight", style: "margin-top:8px" },
      texts(job.text).map(({ field, label, value }) => [
        h("div", { class: "row" }, h("span", { class: "small soft grow", text: label }),
          button("Copy", `copy:${job.id}:${field}`, async () => {
            await navigator.clipboard.writeText(value)
            flash = `${label} copied.`
          })),
        h("pre", { class: "quote small", text: value }),
      ]),
      h("p", { class: "small soft", text: `The video is ${job.video.name}. ReelForge's Publish tab shows where the file is.` }),
      h("div", { class: "row" }, button(`Open ${platformName(job.platform)}`, `open:${job.id}`, () => chrome.tabs.create({ url: PLATFORMS[job.platform].upload }))),
    ))
}

function jobCard(job) {
  const p = platformName(job.platform)
  const showTab = button("Show tab", `focus:${job.id}`, async () => {
    const done = await ask("ui.focus", { job: job.id })
    if (!done?.ok) flash = "That tab is closed."
  })
  let body
  let actions
  if (job.paused && !isFinal(job.state)) {
    body = h("div", { class: "notice bad" },
      h("p", { text: `This browser is signed in to ${p} as ${job.paused.seen}.` }),
      h("p", { class: "small soft", text: `ReelForge has ${job.paused.expected} for this channel. Nothing has been attached yet.` }))
    actions = [
      button(`Post as ${job.paused.seen}`, `go:${job.id}`, () => ask("ui.override", { job: job.id, go: true }), "primary"),
      button("Stop", `no:${job.id}`, () => ask("ui.override", { job: job.id, go: false })),
      showTab,
    ]
  } else if (job.state === "failed") {
    body = [
      h("div", { class: "notice bad" },
        h("p", { text: job.error_message || "This post stopped." }),
        h("p", { class: "small soft", text: advice(job.error_code, job.platform) })),
      byHand(job),
    ]
    actions = [showTab, button("Dismiss", `dismiss:${job.id}`, () => ask("ui.dismiss", { job: job.id }))]
  } else if (job.state === "unknown") {
    body = h("div", { class: "notice bad" },
      h("p", { text: job.error_message || `ReelForge could not tell whether this reached ${p}.` }),
      h("p", { class: "small soft", text: advice("unknown_outcome", job.platform) }))
    actions = [showTab, button("Dismiss", `dismiss:${job.id}`, () => ask("ui.dismiss", { job: job.id }))]
  } else if (isFinal(job.state)) {
    body = [
      h("p", { text: stateLine(job) }),
      postLink(job.url),
    ]
    actions = [button("Dismiss", `dismiss:${job.id}`, () => ask("ui.dismiss", { job: job.id }))]
  } else {
    body = [
      h("p", { text: stateLine(job) }),
      progressBar(job),
      job.detail ? h("p", { class: "small soft", text: job.detail }) : null,
      job.offline ? h("p", { class: "small soft", text: "ReelForge is not answering. This will be passed on when it is back." }) : null,
    ]
    actions = [showTab, job.state === "posting" ? null : button("Stop", `stop:${job.id}`, () => ask("ui.cancel", { job: job.id }))]
  }
  return h("div", { class: "panel job", data: { job: job.id, state: job.state, platform: job.platform } },
    h("div", { class: "row" }, h("h3", { class: "grow", text: p }), h("span", { class: "chip", "data-tone": tone(job.state), text: WORD[job.state] || job.state })),
    h("p", { class: "small soft", text: job.video.name }),
    body,
    h("div", { class: "row wrap" }, actions),
  )
}

function jobsSection() {
  return h("section", { data: { section: "jobs" } }, h("h2", { text: "Posting now" }), jobs.map(jobCard))
}

function videoCard(video, platforms) {
  const open = platforms.filter(p => free(video.platforms?.[p.key]?.state))
  return h("div", { class: "panel stack tight", data: { video: video.id } },
    h("h3", { text: video.title || video.name }),
    h("p", { class: "small soft", text: `${video.name} · ${megabytes(video.size)} · ${REVIEW[video.review] || video.review}` }),
    h("div", { class: "chips" }, platforms.map(p => {
      const state = video.platforms?.[p.key]?.state || "none"
      return h("span", { class: "chip", "data-tone": tone(state), data: { platform: p.key, state }, text: `${p.name}: ${WORD[state] || state}` })
    })),
    !video.postable ? h("p", { class: "small", text: video.why || "This video cannot be posted from here." })
      : !open.length ? h("p", { class: "small soft", text: "Posted, or on its way, everywhere." })
        : h("div", { class: "row" }, button("Post", `post:${video.id}`, async () => {
          view = { name: "choose", video: video.id, picked: Object.fromEntries(open.map(p => [p.key, true])), press: false }
        }, "primary")),
  )
}

function home() {
  const state = ui.state
  const platforms = (state?.platforms || []).filter(p => p.connected)
  const channels = ui.apps.length > 1
    ? h("label", { class: "row small soft" }, "Channel",
      h("select", { data: { key: "channel" }, onchange: event => run("channel", () => ask("ui.select", { port: Number(event.target.value) })) },
        ui.apps.map(app => h("option", { value: String(app.port), selected: app.port === ui.port, text: (app.profile.name || "ReelForge") + (app.paired ? "" : " (not connected)") }))))
    : null
  let videos
  if (ui.problem) videos = h("div", { class: "notice bad" }, h("p", { text: ui.problem }))
  else if (!state) videos = h("div", { class: "row soft" }, h("div", { class: "spinner" }), "Asking ReelForge for your videos")
  else if (!platforms.length) videos = h("p", { class: "empty", text: "No platform is connected in ReelForge yet. Connect one on its Accounts tab." })
  else if (!state.videos?.length) videos = h("p", { class: "empty", text: "No finished videos yet. A video your AI finishes shows up here, ready to post." })
  else videos = state.videos.map(video => videoCard(video, platforms))
  return [
    channels,
    jobs.length ? jobsSection() : null,
    h("section", { data: { section: "videos" } }, h("h2", { text: "Ready to post" }), videos),
  ]
}

// First screen of a post: where to. Nothing has been sent anywhere yet.
function choose() {
  const video = ui.state?.videos?.find(v => v.id === view.video)
  if (!video) {
    view = { name: "home" }
    return home()
  }
  const platforms = (ui.state.platforms || []).filter(p => p.connected)
  const picked = platforms.filter(p => view.picked[p.key] && free(video.platforms?.[p.key]?.state))
  // Press Post for me shows only where it could happen: switched on in Settings, unlocked in ReelForge, not X.
  const pressable = picked.filter(p => PRESS_PLATFORMS.includes(p.key) && ui.press[p.key] === true && p.press_unlocked === true)
  return h("section", { data: { screen: "choose" } },
    h("h3", { text: `Post ${video.title || video.name}` }),
    h("p", { class: "soft", text: "Where to? The next step shows the exact text for each one before anything opens." }),
    h("div", { class: "panel stack tight" }, platforms.map(p => {
      const state = video.platforms?.[p.key]?.state || "none"
      const can = free(state)
      return h("label", { class: can ? "check" : "check off" },
        h("input", { type: "checkbox", data: { key: `pick:${p.key}` }, checked: can && view.picked[p.key], disabled: !can,
          onchange: event => { view.picked[p.key] = event.target.checked; draw() } }),
        h("span", { class: "grow" }, p.name, h("span", { class: "small soft", text: p.handle ? `  ${p.handle}` : "" }),
          !can ? h("div", { class: "small soft", text: WORD[state] || state }) : null,
          can && p.via === "api" ? h("div", { class: "small soft", text: "ReelForge posts this one itself, through the platform's own API." }) : null))
    })),
    pressable.length
      ? h("label", { class: "check" },
        h("input", { type: "checkbox", data: { key: "press" }, checked: view.press, onchange: event => { view.press = event.target.checked; draw() } }),
        h("span", {}, `Press Post for me on ${pressable.map(p => p.name).join(" and ")}`,
          h("div", { class: "small soft", text: "Off: ReelForge fills everything in and you press Post. That is the safer choice." })))
      : h("p", { class: "small soft", text: "ReelForge fills everything in and stops. You press Post on each page yourself." }),
    flash ? h("div", { class: "notice bad" }, h("p", { text: flash })) : null,
    h("div", { class: "row" },
      button("Continue", "prepare", async () => {
        const answer = await ask("ui.prepare", { video: video.id, platforms: picked.map(p => p.key), press: view.press && pressable.length > 0 })
        if (!answer?.ok) {
          flash = answer?.message || "ReelForge did not answer."
          return
        }
        const pending = { port: answer.port, batch: answer.batch, title: video.title || video.name }
        // Kept until it is confirmed or cancelled: a popup closes whenever the person clicks elsewhere.
        if (answer.jobs.some(job => job.via === "extension")) await session.set({ pending })
        view = { name: "confirm", ...pending, jobs: answer.jobs, blocked: answer.blocked }
      }, "primary", !picked.length),
      button("Back", "back", async () => { view = { name: "home" } }),
    ),
  )
}

// Second screen: the exact text and the account for each platform. Its button is the one that opens tabs.
function confirm() {
  const mine = view.jobs.filter(job => job.via === "extension")
  const leave = async () => {
    await session.remove("pending")
    view = { name: "home" }
  }
  return h("section", { data: { screen: "confirm" } },
    h("h3", { text: `Post ${view.title}` }),
    mine.length ? h("p", { class: "soft", text: "This is exactly what goes in the boxes. Check it and the account." }) : null,
    view.blocked.map(item => h("div", { class: "notice", data: { blocked: item.platform } },
      h("p", { text: `${platformName(item.platform)}: ${item.message || item.reason}` }),
      postLink(item.url))),
    view.jobs.map(job => h("div", { class: "panel stack tight", data: { confirm: job.platform } },
      h("div", { class: "row" }, h("h3", { class: "grow", text: platformName(job.platform) }),
        h("span", { class: "small soft", text: job.handle ? `as ${job.handle}` : "no account set in ReelForge" })),
      texts(job.text).map(({ label, value }) => [h("span", { class: "small soft", text: label }), h("pre", { class: "quote", text: value })]),
      h("p", { class: "small soft", text: job.via !== "extension" ? "ReelForge posts this one itself, through the platform's own API."
        : job.press && PRESS_PLATFORMS.includes(job.platform) && ui.press[job.platform] ? "ReelForge presses Post for you once everything is in."
          : `You press ${PLATFORMS[job.platform]?.button || "Post"} yourself.` }),
    )),
    !view.jobs.length ? h("p", { class: "soft", text: "Nothing new to post." }) : null,
    flash ? h("div", { class: "notice bad" }, h("p", { text: flash })) : null,
    h("div", { class: "row" },
      mine.length ? button(mine.length > 1 ? "Open the upload pages" : "Open the upload page", "start", async () => {
        const answer = await ask("ui.start", { port: view.port, batch: view.batch })
        if (answer?.state === "error:not_claimed") {
          // Nothing is left to confirm here: the list is where to start again.
          await leave()
          flash = "These posts waited more than ten minutes, or another browser took them. Nothing was opened. Press Post again."
          return
        }
        if (answer?.state !== "started") {
          flash = "ReelForge did not start this post. Is it still open?"
          return
        }
        await leave()
      }, "primary") : null,
      button(mine.length ? "Cancel" : "Back", "discard", async () => {
        if (mine.length) await ask("ui.discard", { port: view.port, batch: view.batch })
        await leave()
      }),
    ),
  )
}

function draw() {
  const status = !ui ? ["busy", "Looking"]
    : ui.connection === "connected" ? ["good", ui.apps.find(app => app.port === ui.port)?.profile.name || "Connected"]
      : ui.connection === "not_connected" ? ["", "Not connected"]
        : ui.connection === "demo" ? ["", "Demo"]
          : ui.connection === "not_running" ? ["bad", "Not running"] : ["bad", "Update needed"]
  // Redrawn only when something changed, so a button under the pointer does not flicker and focus stays put.
  const now = JSON.stringify([ui, jobs, view, busy, flash])
  if (now === drawn) return
  drawn = now
  pill.dataset.tone = status[0]
  pill.textContent = status[1]
  pill.title = status[1]
  const focused = document.activeElement?.dataset?.key
  const scroll = main.scrollTop
  let content
  if (!ui) content = h("div", { class: "row soft", data: { screen: "loading" } }, h("div", { class: "spinner" }), "Looking for ReelForge on this computer")
  else if (ui.connection !== "connected") content = connectionScreen()
  else content = [
    flash && view.name === "home" ? h("div", { class: "notice", data: { flash: "" } }, h("p", { text: flash })) : null,
    view.name === "choose" ? choose() : view.name === "confirm" ? confirm() : home(),
  ]
  main.replaceChildren(...[content].flat(3).filter(Boolean))
  main.scrollTop = scroll
  if (focused) main.querySelector(`[data-key="${CSS.escape(focused)}"]`)?.focus()
}

// ---- keeping it current --------------------------------------------------------------------------------------

async function loadJobs() {
  const all = await session.get(null)
  jobs = Object.keys(all).filter(key => key.startsWith("job:")).map(key => all[key]).sort((a, b) => a.created - b.created)
}

async function refresh(fresh = false) {
  const answer = await ask("ui.state", { fresh })
  if (answer) ui = answer
  await loadJobs()
  draw()
}

// A post that was waiting on its confirm screen when the popup closed comes back to that screen.
async function restore() {
  const { pending } = await session.get("pending")
  if (!pending) return
  const answer = await ask("ui.batch", { port: pending.port, batch: pending.batch })
  const waiting = (answer?.jobs || []).filter(job => job.state === "queued")
  if (!waiting.length) return session.remove("pending")
  view = { name: "confirm", ...pending, jobs: waiting, blocked: [] }
}

document.getElementById("settings").addEventListener("click", () => chrome.runtime.openOptionsPage())
// Job records change as their tabs report; the popup follows without asking the worker.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && Object.keys(changes).some(key => key.startsWith("job:"))) loadJobs().then(draw)
})

draw()
await restore()
await refresh()
setInterval(refresh, 3000)
