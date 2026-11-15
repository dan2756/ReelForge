// Instagram has no upload address: the form is a dialog opened from Create. After the video is attached there
// are two screens (crop, then edit) before the caption, and sometimes a notice that videos are shared as reels.
(() => {
  const rf = globalThis.__reelforge
  rf.flows.instagram = {
    final: "share",
    button: "Share",
    last: { css: "", names: ["share"] },
    fields: ["caption"],

    async open() {
      rf.click(await rf.find("create"))
      // Some accounts get a small menu first (Post, Story, Reel).
      await rf.waitFor(() => rf.probe("dialog") || rf.probe("create_post"), { timeout: 10000, step: "find_input" })
      if (!rf.probe("dialog")) rf.click(rf.probe("create_post"))
      await rf.find("dialog")
    },

    async fill(job) {
      await rf.step("uploading", "upload", "Instagram is taking the video in")
      const heading = () => rf.clean(rf.probe("heading")?.textContent)
      for (let screens = 0; !rf.probe("caption"); screens++) {
        if (screens > 5) throw new rf.Stop("hook_not_found", "Instagram has changed its page: its steps no longer lead to the caption.", { detail: "hook=next pressed 5 times" })
        const next = await rf.waitFor(() => rf.probe("caption") || rf.probe("reels_ok") || rf.probe("next"), { timeout: 2 * 60 * 1000, step: "upload" })
        if (next === rf.probe("caption")) break
        const before = heading()
        rf.click(next)
        // Each press has to change the screen before the next one is looked for.
        await rf.waitFor(() => rf.probe("caption") || !next.isConnected || !rf.visible(next) || heading() !== before, { timeout: 20000, step: "upload" })
      }
      await rf.fill("fill_caption", "caption", job.text.caption, "Filling in the caption")
    },
  }
})()
