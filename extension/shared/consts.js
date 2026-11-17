// What the worker, the popup and the options page all need to agree on.

// Both sides send this. When they differ, each says which one to update.
export const PROTOCOL = 1

// ReelForge listens on the first free one of these, on 127.0.0.1 only.
export const PORTS = [47821, 47822, 47823, 47824, 47825, 47826, 47827, 47828, 47829, 47830]

// The only sites a job's tab may be opened on. A job whose upload page is anywhere else is refused, so the app
// can never make this extension open some other address.
export const PLATFORMS = {
  tiktok: { name: "TikTok", hosts: ["www.tiktok.com"], button: "Post", upload: "https://www.tiktok.com/tiktokstudio/upload" },
  youtube: { name: "YouTube", hosts: ["www.youtube.com", "studio.youtube.com"], button: "Publish or Save", upload: "https://www.youtube.com/upload" },
  instagram: { name: "Instagram", hosts: ["www.instagram.com"], button: "Share", upload: "https://www.instagram.com/" },
  x: { name: "X", hosts: ["x.com"], button: "Post", upload: "https://x.com/compose/post" },
}

// X's rules name scripting its website and suspend accounts for it, so the press is never offered there.
export const PRESS_PLATFORMS = ["tiktok", "youtube", "instagram"]

// The hooks of selectors.json that a newer list from the app never replaces: every element a platform's script
// clicks, and its final button. They change only with the extension itself (worker/selectors.js says why).
export const PACKED_HOOKS = {
  tiktok: ["post_button"],
  youtube: ["next_button", "done_button"],
  instagram: ["create", "create_post", "reels_ok", "next", "share"],
  x: ["post_button"],
}

// The hooks on every platform whose reading decides whether a post goes on: the captcha (through the challenge
// sign, which names it), the platform's own error message (rf.guard stops a job there), the account the tab is
// compared with, the link a post's id is read from, and the question a platform may ask after the press. All but
// the captcha are read by name in platform/engine.js and platform/run.js. The one other hook those two read by name
// is the file input the video is handed to, which a newer list may change: a wrong one can only stop a post, before
// anything is pressed. Where the packed list has no hook of these for a platform, a newer list cannot add one
// either (worker/selectors.js).
const READ_BY_THE_ENGINE = ["captcha", "error", "account", "post_link", "post_confirm"]

// The hooks that a newer list never replaces although nothing clicks them, because what the script concludes from
// them decides whether a post goes on: the engine's (above) and, on YouTube, the audience and
// visibility answers and the upload's progress and end, which decide whether the last screen is left for the
// person. The hooks that a platform's signs name (a post went out, a challenge, signed out) are kept with the
// signs themselves, and so is every hook a kept one looks inside or near (worker/selectors.js).
export const GUARD_HOOKS = Object.fromEntries(Object.entries({
  tiktok: [],
  youtube: ["kids_no", "kids_yes", "visibility", "visibility_public", "visibility_unlisted", "visibility_private", "progress", "upload_done"],
  instagram: [],
  x: [],
}).map(([platform, own]) => [platform, [...READ_BY_THE_ENGINE, ...own]]))

// These hooks, and every hook one of them looks inside or near, and so on, as a packed list (`hooks`) has them:
// Instagram's Share button is looked for inside its dialog, and a newer list that moved the dialog would move
// where Share is found.
export function keptHooks(hooks, names) {
  const out = new Set(names)
  for (const name of out) {
    for (const way of hooks[name]?.strategies || []) {
      for (const other of [way.within, way.near]) if (typeof other === "string") out.add(other)
    }
  }
  return out
}

// Where the upload page shows which account it is on, so the extension can compare it with the one ReelForge
// has (the platforms with an "account" hook in selectors.json). On the others nothing is compared, and the
// person is told to look at the tab.
export const ACCOUNT_SHOWN = ["instagram", "x"]

const ORDER = ["queued", "claimed", "opening", "uploading", "filling", "ready", "posting", "posted"]
const FINAL = ["posted", "failed", "cancelled", "expired", "unknown"]

export const rank = state => ORDER.indexOf(state)
export const isFinal = state => FINAL.includes(state)
export const isLive = job => !isFinal(job.state)

export const platformName = key => PLATFORMS[key]?.name || key

// What the person can do about each failure. The job's own sentence says what happened; this says what next.
export function advice(code, platform) {
  const p = platformName(platform)
  return {
    extension_outdated: "This extension is older than your ReelForge. Open your browser's extensions page and press Reload on ReelForge.",
    app_unreachable: "Open ReelForge, then post again.",
    // ReelForge's own log-ins (its Accounts tab) live in a different browser, so they do not count here.
    not_signed_in: `It has to be this browser that is signed in to ${p}. ReelForge's own log-in does not count here.`,
    wrong_account: `Switch to the right account on ${p}, then post again.`,
    hook_not_found: `${p} has changed its page. Finish by hand in the tab, and update ReelForge when you can.`,
    challenge: `Finish what ${p} asks for in the tab. Then post again, or finish by hand there.`,
    platform_error: `Look at what ${p} says in the tab. You can finish by hand there.`,
    timeout: "The tab is still open. You can finish by hand there.",
    tab_closed: "Post again when you are ready.",
    press_rejected: `Check your ${p} profile. Then tell ReelForge whether it posted, on its Publish tab.`,
    changed: "Check the video in ReelForge, then post again.",
    // Only the person can settle this one, and ReelForge takes that answer in its own window.
    unknown_outcome: `Check your ${p} profile. Then tell ReelForge whether it posted, on its Publish tab.`,
  }[code] || "You can finish by hand in the tab."
}

// One short line per state for the popup.
export function stateLine(job) {
  const p = platformName(job.platform)
  // The person's own press was seen, and the platform has not said yet that the post is up.
  if (job.pressedByPerson && isLive(job) && job.state !== "posting") return `You pressed ${PLATFORMS[job.platform]?.button || "Post"}. Waiting for ${p} to say it is up`
  switch (job.state) {
    case "queued": return "Waiting to start"
    case "claimed":
    case "opening": return job.step === "signed_in" ? `Checking you're signed in to ${p}` : `Opening ${p}`
    case "uploading": return job.step === "attach" ? "Handing the video to the page" : `${p} is taking the video in`
    case "filling": return job.step === "upload" ? `${p} is taking the video in` : "Filling in the text"
    case "ready": return `Ready. Check it, then press ${PLATFORMS[job.platform]?.button || "Post"}`
    case "posting": return "Pressing Post for you"
    case "posted": return "Posted"
    case "failed": return "Stopped"
    case "cancelled": return "Cancelled"
    case "expired": return "Never started"
    case "unknown": return "Not sure it posted"
    default: return job.state
  }
}
