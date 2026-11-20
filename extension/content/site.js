// Runs on https://reelforge.tools only. The site can ask two things: "are you installed?" (hello) and "is the
// ReelForge app on this computer reachable and paired?" (status). It gets a version and three facts back. It
// cannot start a post and never sees a caption, a file name or a channel name.
(() => {
  const PROTOCOL = 1
  const ORIGIN = "https://reelforge.tools"
  const version = chrome.runtime.getManifest().version

  window.addEventListener("message", async event => {
    if (event.source !== window || event.origin !== ORIGIN) return
    const data = event.data
    if (!data || data.source !== "reelforge-site" || (data.type !== "hello" && data.type !== "status")) return
    const answer = { source: "reelforge-extension", type: data.type, version, protocol: PROTOCOL }
    if (data.type === "status") {
      let status = null
      try {
        status = await chrome.runtime.sendMessage({ t: "site.status" })
      } catch {
        // The worker did not answer (the extension was just reloaded): say the app is not reachable.
      }
      answer.app_reachable = status?.app_reachable === true
      answer.paired = status?.paired === true
      answer.app_version = typeof status?.app_version === "string" ? status.app_version : null
    }
    window.postMessage(answer, ORIGIN)
  })
})()
