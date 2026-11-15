// TikTok Studio's upload page. After the video is attached the page shows the caption box (a Draft.js editor
// that starts with the file's name) and keeps Post switched off until it has taken the whole video in.
(() => {
  const rf = globalThis.__reelforge
  rf.flows.tiktok = {
    final: "post_button",
    button: "Post",

    async fill(job) {
      await rf.fill("fill_caption", "caption", job.text.caption, "Filling in the caption")
      await rf.step("filling", "upload", "TikTok is taking the video in")
      // The button has to be there now, even if it stays off for a long upload.
      await rf.find("post_button")
      await rf.waitFor(() => {
        const post = rf.probe("post_button")
        return post && rf.enabled(post)
      }, { timeout: 30 * 60 * 1000, step: "upload" })
    },
  }
})()
