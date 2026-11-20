// Shared by the four fixture pages. Each page keeps what happened to it in window.fixture, where the tests
// read it: the file its input was given (with a SHA-256 the page computes itself), every press of its final
// button and whether a person or a script made it, and the text that was in its boxes when it "posted".
window.fixture = { files: [], sha256: null, clicks: 0, trustedClicks: 0, posted: false, text: null, touched: [] }

window.takeFile = async function (input, event) {
  const file = input.files[0]
  if (!file) return null
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))
  fixture.sha256 = [...digest].map(byte => byte.toString(16).padStart(2, "0")).join("")
  fixture.files.push({ name: file.name, size: file.size, type: file.type, trusted: event.isTrusted })
  // Like the real pages: the input is emptied as soon as the page has the file.
  input.value = ""
  return file
}

// True when the press should count. Each fixture's final button goes through here.
window.pressed = function (event, button) {
  fixture.clicks++
  if (event.isTrusted) fixture.trustedClicks++
  if (button.getAttribute("aria-disabled") === "true" || button.dataset.disabled === "true" || button.disabled) return false
  // "trusted_only": the page ignores a scripted click, as a site is free to.  "mute": it ignores every click.
  if (MODE === "trusted_only" && !event.isTrusted) return false
  return !["mute", "slow_mute", "held_mute"].includes(MODE)
}
