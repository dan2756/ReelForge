// The small pages around the four fixtures: where a signed-out visitor lands, what a full-page "posted"
// navigation shows, and a page on reelforge.tools that talks to the extension's website bridge.
const page = (title, body) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title}</title></head><body>${body}</body></html>`

export const loginPage = name => page(`${name} log in (ReelForge test copy)`,
  '<h1>Log in</h1><form><input name="username"><input name="password" type="password"><button type="submit" data-testid="loginButton">Log in</button></form>')

export const tiktokContent = page("TikTok Studio posts (ReelForge test copy)", "<h1>Posts</h1><p>Your video has been uploaded</p>")

export const emptyPage = page("Nothing here", "<p>Nothing here.</p>")

// What the website does to ask the extension about itself. Every answer it hears is kept for the tests.
export const sitePage = page("ReelForge (test copy)", `<h1>ReelForge</h1>
<script>
  window.answers = []
  window.addEventListener("message", event => {
    if (event.source === window && event.data && event.data.source === "reelforge-extension") window.answers.push(event.data)
  })
  window.say = message => window.postMessage(message, location.origin)
</script>`)
