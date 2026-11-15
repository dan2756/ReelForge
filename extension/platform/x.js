// X's composer. The video goes in before the text: X clears what was typed when media is added after it.
// The extension never presses Post here, whatever is switched on: X's rules name scripting its website.
(() => {
  const rf = globalThis.__reelforge
  rf.flows.x = {
    final: "post_button",
    button: "Post",
    fields: ["text"],
    neverPress: true,

    async fill(job) {
      await rf.step("uploading", "upload", "X is taking the video in")
      await rf.waitFor(() => rf.probe("attached"), { timeout: 2 * 60 * 1000, step: "upload" })
      await rf.fill("fill_caption", "composer", job.text.text, "Filling in the post")
      await rf.step("filling", "upload", "X is getting the video ready")
      await rf.find("post_button")
      await rf.waitFor(() => {
        const post = rf.probe("post_button")
        return post && rf.enabled(post)
      }, { timeout: 15 * 60 * 1000, step: "upload" })
    },
  }
})()
