// YouTube Studio's upload dialog. The video is attached on its first screen; the title and description are on
// the next; then come three presses of Next before the screen with Publish (or Save) on it.
//
// Three things here are never answered by ReelForge: "Made for kids", the altered-content question and who can
// see the video. When "Made for kids" or the visibility has no saved answer the script stops there and says
// what is left to do, instead of choosing for the person.
(() => {
  const rf = globalThis.__reelforge
  const VISIBILITY = { visibility_public: "Public", visibility_unlisted: "Unlisted", visibility_private: "Private" }

  rf.flows.youtube = {
    final: "done_button",
    button: "Publish or Save",
    last: { css: "#done-button", names: ["publish", "save", "schedule"] },
    fields: ["title", "description"],

    async fill(job) {
      await rf.step("uploading", "upload", "YouTube is taking the video in")
      // The title box arrives once YouTube has started on the file, which can take a while for a big one.
      await rf.fill("fill_title", "title", job.text.title, "Filling in the title")
      const description = await rf.fill("fill_caption", "description", job.text.description, "Filling in the description")
      // A hashtag picker opened by the description would sit on top of the buttons.
      description.blur()

      await rf.step("filling", "audience", "Looking at your saved answers")
      const kids = await rf.waitFor(() => rf.probe("kids_no") && rf.probe("kids_yes"), { timeout: 8000 }).catch(error => {
        if (error.code !== "timeout") throw error
        return null
      })
      if (!kids || !(rf.checked(rf.probe("kids_no")) || rf.checked(rf.probe("kids_yes")))) {
        return { hold: "next_button", detail: "Made for kids is not answered", more: "Answer \"Made for kids\" on this screen. Then press Next until you reach Publish." }
      }

      // Next, until the screen with the visibility choices. Each press gets a moment to change the screen
      // before the page is looked at again. A press too many does nothing: the last screen has no Next.
      for (let presses = 0; ; presses++) {
        const next = await rf.waitFor(() => {
          if (rf.probe("visibility")) return "there"
          const button = rf.probe("next_button")
          return button && rf.enabled(button) ? button : null
        }, { timeout: 30000, step: "audience" }).catch(error => {
          if (error.code !== "timeout") throw error
          return null
        })
        if (next === "there") break
        if (!next || presses > 3) return { hold: "next_button", detail: "Next did not reach the last screen", more: "Press Next until you reach Publish." }
        rf.click(next)
        await rf.waitFor(() => rf.probe("visibility"), { timeout: 1500 }).catch(error => {
          if (error.code !== "timeout") throw error
        })
      }

      const chosen = Object.keys(VISIBILITY).find(hook => rf.checked(rf.probe(hook)))
      await rf.step("filling", "visibility", null, { detail: chosen ? `Visibility: ${VISIBILITY[chosen]}, as saved on your channel` : undefined })
      if (!chosen) return { hold: "done_button", detail: "Visibility is not chosen", more: "Choose who can see it, then press Publish." }

      // Publishing while the file is still going up leaves the video stuck part-way. With no progress label to
      // read, the wait is skipped and so is any press: the person can see the page, the script cannot.
      if (rf.probe("progress") || rf.probe("upload_done")) {
        await rf.step("filling", "upload", "YouTube is still taking the video in")
        await rf.waitFor(() => {
          const percent = Number(rf.read("progress"))
          if (percent > 0 && percent <= 100) rf.progress("filling", "upload", percent / 100)
          // YouTube lets a video be published while it is still going up. Then the dialog goes, and so does
          // this wait: what happened is worked out by the caller.
          return rf.probe("upload_done") || !rf.probe("upload_dialog")
        }, { timeout: 30 * 60 * 1000, step: "upload" })
        return { detail: `Visibility: ${VISIBILITY[chosen]}` }
      }
      return { detail: `Visibility: ${VISIBILITY[chosen]}`, noPress: "ReelForge could not see whether the upload had finished." }
    },
  }
})()
