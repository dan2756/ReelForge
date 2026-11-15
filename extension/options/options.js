// Settings: press Post for me (per platform, off unless switched on here) and Disconnect.
//
// The switches live in this browser only (chrome.storage.local). ReelForge on the computer cannot read or set
// them, so nothing but a person on this page can turn the press on.
import { PLATFORMS, PRESS_PLATFORMS } from "../shared/consts.js"
import { h } from "../shared/dom.js"

const ask = (t, more = {}) => chrome.runtime.sendMessage({ t, ...more }).catch(() => null)
const $ = id => document.getElementById(id)

let ui = null
let drawn = ""

// Why a switch cannot be turned on right now, or "" when it can.
function locked(platform) {
  if (ui.connection !== "connected") return "Open ReelForge and connect this browser to change this."
  const theirs = ui.state?.platforms?.find(p => p.key === platform)
  if (!theirs?.connected) return `${PLATFORMS[platform].name} is not connected in ReelForge.`
  if (!theirs.press_unlocked) return `Locked until one post to ${PLATFORMS[platform].name} has gone through with you pressing ${PLATFORMS[platform].button}.`
  return ""
}

function draw() {
  const now = JSON.stringify(ui)
  if (now === drawn) return
  drawn = now
  const focused = document.activeElement?.id
  const shown = ui.apps.find(app => app.port === ui.port)
  const connected = ui.connection === "connected"
  $("status").dataset.tone = connected ? "good" : ui.connection === "not_running" ? "bad" : ""
  $("status").textContent = connected ? shown.profile.name || "Connected" : ui.connection === "not_running" ? "Not running" : "Not connected"
  $("connection").textContent = connected ? `Connected to ${shown.profile.name || "ReelForge"} (ReelForge ${shown.version}).`
    : ui.connection === "not_running" ? "ReelForge is not running on this computer."
      : "ReelForge is open, but this browser is not connected to it."
  $("disconnect").disabled = !connected
  $("about").textContent = `Extension ${ui.version}.`
  $("press").replaceChildren(
    ...PRESS_PLATFORMS.map(platform => {
      const on = ui.press[platform] === true
      const why = on ? "" : locked(platform)
      return h("label", { class: why ? "panel switch off" : "panel switch" },
        h("input", {
          type: "checkbox", id: `press-${platform}`, checked: on, disabled: Boolean(why),
          onchange: async event => {
            const answer = await ask("ui.press", { platform, on: event.target.checked })
            $("feedback").textContent = answer?.ok ? "" : answer?.message || "That could not be changed."
            await refresh()
          },
        }),
        h("span", { class: "grow" },
          h("div", { text: `Press Post for me on ${PLATFORMS[platform].name}` }),
          h("div", { class: "small soft", id: `why-${platform}`, text: why || (on ? "On. It still happens only for a post where you switch \"stop before posting\" off." : "Off. You press Post yourself.") })))
    }),
    h("div", { class: "panel switch off", id: "press-x" },
      h("span", { class: "grow" },
        h("div", { text: "X" }),
        h("div", { class: "small soft", text: "Not offered. X's rules name scripting its website and suspend accounts for it, so on X you always press Post yourself." }))),
  )
  if (focused) $(focused)?.focus()
}

async function refresh() {
  const answer = await ask("ui.state")
  if (!answer) return
  ui = answer
  draw()
}

$("disconnect").addEventListener("click", async () => {
  $("disconnect").disabled = true
  await ask("ui.disconnect")
  $("feedback").textContent = "Disconnected. ReelForge has forgotten this browser. Connect again from ReelForge when you want to."
  await refresh()
})

await refresh()
setInterval(refresh, 3000)
