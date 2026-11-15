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
