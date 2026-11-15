// Posting to YouTube through its own API, from the window, on posting.test.tsx's scratch channel with its real Python
// side and the real ledger. Google is the stand-in the Python tests use, listening on this computer inside the Python
// side (ui/test/youtube_api_preload.py), and "your browser" is played there too: no browser opens, nothing reaches
// Google, and nothing is posted anywhere. From a channel that never heard of the API to a posted video: the client
// file, Connect and Google's page, the question before a post, every state of a post on the YouTube row, a window
// opened afterwards (which is not connected), and Disconnect.

import { Database } from "bun:sqlite"
import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test"
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { TextAttributes } from "@opentui/core"
import { headless, type Headless } from "../scripts/headless"
import { clickText } from "../scripts/shots"
import { ROOT } from "../src/bridge"
import type { PostingView, YouTubeApiView } from "../src/dashboard/types"
import { toasts } from "../src/ui/Panels"

setDefaultTimeout(60_000)
let open: Headless | null = null
let tmp = ""
const CAPTION = "What if hail doubled every 2 seconds?"
const TAGS = "#fyp #whatif #weather"
const LINK = "https://youtube.com/shorts/vid00000001"
const UNPROVEN = "This way of posting is tested against a stand-in for Google, never against Google itself. Post as private first and check the video in YouTube Studio."
const NOT_HERE = "ReelForge keeps YouTube's sign-in only while it is open, in its memory and never in a file. Connect before you post through the API: it is one press, then Google's page."

/** A window on the scratch channel, with what the test asks the preload for. It doesn't listen for the browser
 * extension: nothing here needs it. again: on the folder the last window of this test had, as a ReelForge opened
 * later. */
async function kit(ask: object = {}, size: [number, number] = [150, 45], again = false): Promise<Headless> {
  if (!again) tmp = mkdtempSync(join(tmpdir(), "reelforge-youtube-api-"))
  open = await headless("dashboard", { tab: "publish" }, size[0], size[1], {
    REELFORGE_BRIDGE_PRELOAD: join(ROOT, "ui", "test", "youtube_api_preload.py"),
    REELFORGE_TEST_ROOT: tmp,
    REELFORGE_TEST_POSTING: JSON.stringify(ask),
    REELFORGE_OPENED: join(tmp, "opened.txt"),
    REELFORGE_COPIED: join(tmp, "copied.txt"),
    REELFORGE_EXTENSION_PORT: "off",
  })
  await open.settle(2500)
  return open
}

afterEach(async () => {
  if (open) await open.bridge.call("test_google", "release").catch(() => {}) // no upload outlives its test
  await open?.close()
  open = null
  toasts.clear()
  if (tmp) rmSync(tmp, { recursive: true, force: true })
  tmp = ""
})

const frame = (h: Headless) => h.t.captureCharFrame()
const flat = (h: Headless) => frame(h).replace(/\s+/g, " ")
const listed = (file: string) => (existsSync(join(tmp, file)) ? readFileSync(join(tmp, file), "utf-8").split("\n").filter(Boolean) : [])
const posting = (h: Headless) => h.bridge.view<PostingView>("posting")!
const youtube = (h: Headless) => posting(h).rows.find((r) => r.key === "youtube")!
const api = (h: Headless) => h.bridge.view<YouTubeApiView>("youtube_api")!
const popups = () => toasts.get().map((t) => (t.title ? `${t.title}: ${t.body}` : t.body))
const store = () => join(tmp, "dashboard", "data", "youtube_api.json")
const onFile = () => (existsSync(store()) ? JSON.parse(readFileSync(store(), "utf-8")) : null)

interface Sent {
  consents: number
  starts: { snippet: { title: string; description: string; categoryId: string }; status: Record<string, unknown> }[]
  pieces: number
  videos: string[]
  revoked: number
}
/** The stand-in for Google: move it along, and read what it was sent and what it made. */
const google = (h: Headless, what = "sent", ...args: unknown[]) => h.bridge.call<Sent>("test_google", what, ...args)

function inStats<T>(use: (db: Database) => T): T {
  const db = new Database(join(tmp, "dashboard", "data", "stats.db"))
  try {
    return use(db)
  } finally {
    db.close()
  }
}
const jobsMade = () => inStats((db) => (db.query("SELECT COUNT(*) AS n FROM post_jobs").get() as { n: number }).n)

/** The label of the control that has the keyboard: it's the underlined one. */
function focused(h: Headless): string {
  for (const line of h.t.captureSpans().lines) {
    const text = line.spans.filter((sp) => sp.attributes & TextAttributes.UNDERLINE).map((sp) => sp.text).join("").trim()
    if (text) return text
  }
  return ""
}

/** Wait (up to 8 s) for something the Python side does, and for the window to have drawn it. */
async function until(h: Headless, ok: () => boolean) {
  for (let i = 0; i < 40 && !ok(); i++) await h.settle(200)
  await h.settle(100)
}

/** YouTube's row on the Publish tab, as one line of words: from its name to the next platform's. */
function row(h: Headless): string {
  const rows = frame(h).split("\n")
  const at = rows.findIndex((r) => /┃ YouTube +[○●✓×?]/.test(r))
  const next = rows.findIndex((r, i) => i > at && /┃ Instagram +[○●✓×?]/.test(r))
  if (at < 0 || next < 0) return ""
  return rows.slice(at, next).map((r) => r.slice(r.lastIndexOf("┃") + 1).replace(/[█▀▄]\s*$/, "")).join(" ").replace(/\s+/g, " ").trim()
}

/** What the open dialog says, as one line of words. */
function dialog(h: Headless): string {
  const rows = frame(h).split("\n").map((r) => [...r])
  const top = rows.findIndex((r, y) => y > 0 && r.includes("╭"))
  if (top < 0) return ""
  const left = rows[top]!.indexOf("╭")
  const right = rows[top]!.lastIndexOf("╮")
  const bottom = rows.findIndex((r, y) => y > top && r[left] === "╰")
  return rows.slice(top + 1, bottom).map((r) => r.slice(left + 1, right).join("")).join(" ").replace(/\s+/g, " ")
}

/** The connection's dialog, from the Publish tab's button. */
async function openApi(h: Headless) {
  await clickText(h, "YouTube API…", 2)
  await until(h, () => frame(h).includes("YOUTUBE API"))
}

/** The question before a post, from the YouTube row's button. */
async function ask(h: Headless, button = "Post with API…") {
  await clickText(h, button, 3)
  await until(h, () => frame(h).includes("POST TO YOUTUBE THROUGH ITS API"))
}

describe("a channel that never used YouTube's API", () => {
  test("has one quiet button for it above Post…, nothing on the YouTube row, and the tab is no taller", async () => {
    const h = await kit({}, [120, 30])
    const rows = frame(h).split("\n")
    const at = rows.findIndex((r) => r.includes("YouTube API…"))
    expect(rows[at]).toMatch(/┃ +YouTube API… +$/) // nothing else on its row
    expect(rows[at + 1]).toContain("▐ Post… ▌") // the row above Post…, which was empty
    expect(rows[at - 1]).toMatch(/┃ X +○ not posted/)
    expect(row(h)).toBe("YouTube ○ not posted")
    // Everything that starts a post is in view, as it was without the button.
    for (const text of ["POST  ✓ QA passed", "▐ Post… ▌", "BY HAND", "▐ Open all 4 → ▌", "TikTok →", "YouTube →", "Instagram →", "X →", "Copy YouTube hashtags"]) expect(frame(h)).toContain(text)
    expect(Object.keys(posting(h).labels).sort()).toEqual(["cancel", "connect_api", "mark_posted", "not_posted", "open_post", "post_again", "post_api", "post_api_again", "retry"])
  })

  test("its dialog says what this is and asks for the client file, and only a Desktop client's file is taken", async () => {
    const h = await kit()
    await openApi(h)
    const said = dialog(h)
    for (const text of [
      "○ YouTube API not connected",
      "Optional. It posts to YouTube through Google's own API, with a Google Cloud project you make yourself. docs/YOUTUBE-API.md has the steps.",
      UNPROVEN,
      "CLIENT FILE The client file you downloaded from your Google Cloud project (client_secret_....json): type or paste where it is.",
      "▐ Connect with this file ▌",
      "Guide: docs/YOUTUBE-API.md",
      "▐ Close ▌",
    ])
      expect(said).toContain(text)
    for (const text of ["FOR POSTS TO COME", "Disconnect", "▐ Connect ▌"]) expect(said).not.toContain(text) // nothing to choose or end yet
    // The box for the path has the keyboard. An empty one, a file that isn't there and a Web application's client
    // are each turned away, and nothing opens.
    h.t.mockInput.pressKey("RETURN")
    await h.settle(400)
    expect(listed("opened.txt")).toEqual([])
    await h.t.mockInput.typeText(join(tmp, "nothing.json"))
    h.t.mockInput.pressKey("RETURN")
    await until(h, () => popups().length > 0)
    expect(popups().at(-1)).toBe(`YouTube's API: There is no file at ${join(tmp, "nothing.json")}.`)
    for (let i = 0; i < "nothing.json".length; i++) h.t.mockInput.pressKey("BACKSPACE")
    await h.t.mockInput.typeText("client_secret_web.json")
    await clickText(h, "Connect with this file", 2)
    await until(h, () => popups().some((p) => p.includes("Web application")))
    expect(popups().at(-1)).toBe("YouTube's API: client_secret_web.json is the file of a Web application client. ReelForge needs a Desktop app client: make one in Google Cloud and download its JSON.")
    expect([api(h).state, onFile(), listed("opened.txt")]).toEqual(["off", null, []])
    expect(frame(h)).toContain("YOUTUBE API") // the dialog is still there, with what was typed
    expect(frame(h)).toContain("client_secret_web.json")
    h.t.mockInput.pressKey("ESCAPE") // out of the box
    h.t.mockInput.pressKey("ESCAPE") // and out of the dialog
    await h.settle(400)
    expect(frame(h)).not.toContain("YOUTUBE API")
  })

  test("the demo has no line for it", async () => {
    open = await headless("dashboard", { demo: true, only_demos: true, tab: "publish" }, 150, 45, { REELFORGE_BRIDGE_PRELOAD: join(ROOT, "scripts", "shots_preload.py") })
    await open.settle(2500)
    expect(frame(open)).toContain("▐ Post… ▌")
    expect(frame(open)).not.toContain("YouTube API")
    expect(frame(open)).not.toContain("with API")
  })
})

describe("connecting", () => {
  test("the client file, then Google's page: the dialog waits for your answer there, and your yes connects this window", async () => {
    const h = await kit({ api: { consent: "later" } })
    await openApi(h)
    await h.t.mockInput.typeText(join(tmp, "client_secret_test.json"))
    h.t.mockInput.pressKey("RETURN")
    await until(h, () => api(h).state === "connecting")
    expect(listed("opened.txt")).toEqual(["google consent"]) // Google's page opened, once
    const said = dialog(h)
    expect(said).toContain("● Waiting for your answer on Google's page it opened in your browser")
    expect(said).toContain("Google's consent page is open in your browser. Pick the Google account your channel belongs to and allow ReelForge to upload videos.")
    expect(said).toContain("You have ten minutes, and the browser has to be on this computer. After that, connect again.")
    expect(onFile()).toBeNull() // nothing is kept before your yes
    expect(youtube(h).actions).toEqual([]) // and nothing is on offer
    expect(popups()).toEqual([]) // the dialog says it: no pop-up over its buttons
    await google(h, "answer", "yes")
    await until(h, () => api(h).state === "on")
    expect(dialog(h)).toContain("● YouTube API connected Google names the channel with the first upload")
    expect(dialog(h)).toContain("posts start as private")
    expect(dialog(h)).toContain(UNPROVEN)
    expect(dialog(h)).not.toContain("CLIENT FILE") // a client is on file now
    expect(dialog(h)).not.toContain("Waiting for your answer")
    expect(popups()).toEqual([])
    // What is on file is the client and nothing of the sign-in, which this window holds.
    expect(Object.keys(onFile()).sort()).toEqual(["client_id", "client_secret"])
    await clickText(h, "▐ Close ▌", 3)
    await until(h, () => youtube(h).actions.includes("post_api"))
    expect(row(h)).toMatch(/^YouTube ○ not posted ▐ Post with API… ▌$/)
    expect(flat(h)).toContain("● YouTube API connected tested on a stand-in only")
  })

  test("a no on Google's page connects nothing and says so", async () => {
    const h = await kit({ api: { consent: "later" } })
    await openApi(h)
    await h.t.mockInput.typeText(join(tmp, "client_secret_test.json"))
    await clickText(h, "Connect with this file", 2)
    await until(h, () => api(h).state === "connecting")
    toasts.clear()
    await google(h, "answer", "no")
    await until(h, () => api(h).state === "off")
    expect(popups()).toEqual(["YouTube's API: You said no on Google's page, so nothing was connected."])
    expect(dialog(h)).toContain("○ YouTube API not connected")
    expect(dialog(h)).toContain("CLIENT FILE")
    expect([onFile(), youtube(h).actions]).toEqual([null, []])
  })

  test("with a client on file, a window that has just opened says it is not connected, and Connect is one press", async () => {
    const h = await kit({ api: { client: true } })
    expect(flat(h)).toContain("○ YouTube API: not connected in this window")
    expect(row(h)).toMatch(/^YouTube ○ not posted ▐ Connect API ▌$/)
    await openApi(h)
    expect(dialog(h)).toContain(NOT_HERE)
    expect(dialog(h)).toContain(UNPROVEN)
    expect(dialog(h)).not.toContain("CLIENT FILE") // Connect uses the one on file
    expect(focused(h)).toBe("Connect")
    h.t.mockInput.pressKey("RETURN")
    await until(h, () => api(h).state === "on")
    expect(listed("opened.txt")).toEqual(["google consent"])
    expect(dialog(h)).toContain("● YouTube API connected")
    expect(dialog(h)).not.toContain("▐ Connect ▌")
    expect((await google(h)).consents).toBe(1)
  })

  test("the YouTube row's own Connect API button opens the dialog and Google's page in one press", async () => {
    const h = await kit({ api: { client: true, consent: "later" } })
    await clickText(h, "Connect API", 2)
    await until(h, () => api(h).state === "connecting")
    expect(listed("opened.txt")).toEqual(["google consent"]) // that press was the ask
    expect(dialog(h)).toContain("YOUTUBE API ● Waiting for your answer on Google's page")
    expect(dialog(h)).toContain("You have ten minutes, and the browser has to be on this computer.")
    expect(dialog(h)).toContain("▐ Open Google's page again ▌")
    expect(focused(h)).toBe("Close")
    await google(h, "answer", "yes")
    await until(h, () => api(h).state === "on")
    expect(dialog(h)).toContain("● YouTube API connected")
    expect(popups()).toEqual([]) // the dialog said all of it: nothing piled up over the rows
    h.t.mockInput.pressKey("RETURN") // Close
    await until(h, () => youtube(h).actions.includes("post_api"))
    expect(frame(h)).not.toContain("YOUTUBE API")
    expect(row(h)).toMatch(/^YouTube ○ not posted ▐ Post with API… ▌$/)
    expect([(await google(h)).consents, jobsMade()]).toEqual([1, 0]) // one page, and connecting posts nothing
    // The Python side's own row action still connects, for whoever calls it, and says how it went in pop-ups.
    await google(h, "end_grant")
    await h.bridge.call("post_action", "youtube", "connect_api")
    await until(h, () => popups().length === 1)
    expect(popups()[0]).toContain("Connecting YouTube's API: Google's consent page is opening in your browser.")
  })
})

describe("the choices for posts to come", () => {
  test("who can see a new post and the made-for-kids answer are kept as soon as they are picked", async () => {
    const h = await kit({ api: { client: "on" } })
    await openApi(h)
    const said = dialog(h)
    expect(said).toContain("FOR POSTS TO COME Who can see a new post Made for kids")
    expect(said).toContain("● Private")
    expect(said).toContain("● Not answered") // nobody has answered for you
    expect(said).toContain("A new post starts as the one you pick here. You can choose otherwise for one post, in its question.")
    expect(said).toContain('"Made for kids" is a declaration you make to YouTube, so ReelForge never makes it for you. Until you answer, a post sends none and your channel\'s own audience setting in YouTube Studio applies.')
    expect([api(h).privacy, api(h).made_for_kids]).toEqual(["private", null])
    await clickText(h, "○  Unlisted", 4)
    await until(h, () => api(h).privacy === "unlisted")
    expect(dialog(h)).toContain("● Unlisted")
    expect(dialog(h)).toContain("posts start as unlisted")
    await clickText(h, "○  No", 4)
    await until(h, () => api(h).made_for_kids === false)
    expect(dialog(h)).toContain("● No")
    expect(dialog(h)).not.toContain("Not answered") // an answer can be changed, and not taken back
    expect([onFile().privacy, onFile().made_for_kids]).toEqual(["unlisted", false])
    // The question before a post shows both, as they will be sent.
    await clickText(h, "▐ Close ▌", 3)
    await ask(h)
    expect(dialog(h)).toContain("Who can see it ┃ ○ Private ┃ ● Unlisted ┃ ○ Public Made for kids No Category People & Blogs")
  })
})

describe("the question before a post", () => {
  test("shows exactly what will be sent and how far this is tested, and starts nothing until Post", async () => {
    const h = await kit({ api: { client: "on" } })
    await ask(h)
    const said = dialog(h)
    for (const text of [
      "ep03_hail.mp4 0.0 MB ✓ QA passed and the review says APPROVED.",
      "ReelForge uploads the video to YouTube itself, through Google's API, with exactly this. The YouTube row shows the percent. Keep ReelForge open until it says posted.",
      UNPROVEN,
      `Title ${CAPTION} #shorts Description ${TAGS} Who can see it ┃ ● Private ┃ ○ Unlisted ┃ ○ Public`,
      "Made for kids Not sent: you haven't answered, so your channel's own setting applies",
      "Category People & Blogs",
      "ReelForge doesn't set the altered-content answer, tags, a thumbnail or a schedule. Set those in YouTube Studio.",
      "▐ Cancel ▌ ▐ Post to YouTube ▌",
    ])
      expect(said).toContain(text)
    // Cancel has the keyboard: an Enter still held from the row's button starts nothing.
    expect(focused(h)).toBe("Cancel")
    h.t.mockInput.pressKey("RETURN")
    await h.settle(500)
    expect(frame(h)).not.toContain("POST TO YOUTUBE THROUGH ITS API")
    expect([jobsMade(), (await google(h)).starts]).toEqual([0, []])
    // Tab goes on to Post and round to the choice of who can see it; Esc closes, and still nothing was sent.
    await ask(h)
    h.t.mockInput.pressKey("TAB")
    await h.settle(200)
    expect(focused(h)).toBe("Post to YouTube")
    h.t.mockInput.pressKey("TAB")
    await h.settle(200)
    h.t.mockInput.pressArrow("down")
    await h.settle(200)
    h.t.mockInput.pressKey("RETURN") // unlisted, for this one post
    await h.settle(300)
    expect(dialog(h)).toContain("┃ ○ Private ┃ ● Unlisted ┃ ○ Public")
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(400)
    expect(frame(h)).not.toContain("POST TO YOUTUBE THROUGH ITS API")
    expect([jobsMade(), (await google(h)).starts, api(h).privacy]).toEqual([0, [], "private"]) // (and posts still start as private)
  })

  test("is about one video: when another is the picked one by now it closes, and its yes starts nothing", async () => {
    const h = await kit({ api: { client: "on", kids: false } })
    await clickText(h, "clip.mp4", 2)
    await until(h, () => posting(h).video?.name === "clip.mp4")
    await ask(h)
    expect(dialog(h)).toContain("clip.mp4")
    expect(dialog(h)).toContain(`Title Clip #shorts Description ${TAGS}`)
    const asked = posting(h).video!.path
    // Another ReelForge window hides clip.mp4: this window's list then picks its newest video by itself, under the
    // question.
    inStats((db) => {
      db.run("INSERT OR REPLACE INTO meta (key, value) VALUES ('hidden_videos', ?)", [JSON.stringify([asked])])
      db.run("INSERT INTO meta (key, value) VALUES ('changes', '1') ON CONFLICT (key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")
    })
    await until(h, () => posting(h).video?.name === "ep03_hail.mp4" && !frame(h).includes("POST TO YOUTUBE THROUGH ITS API"))
    expect(frame(h)).not.toContain("POST TO YOUTUBE THROUGH ITS API") // nothing left to say yes to
    expect(popups()).toContain("Nothing was started: clip.mp4 isn't the picked video any more, so the question about it closed.")
    // A yes that was on its way as the pick changed: the Python side starts nothing for it either.
    expect(await h.bridge.call("post_with_api", "Clip", TAGS, "private", false, { video: asked, made_for_kids: false })).toBeNull()
    await h.settle(600)
    expect(popups().at(-1)).toBe("Nothing was started: The question was about clip.mp4, and ep03_hail.mp4 is the picked video now. Press Post with API… again to post this one.")
    expect([jobsMade(), (await google(h)).starts]).toEqual([0, []])
    expect(row(h)).toMatch(/^YouTube ○ not posted ▐ Post with API… ▌$/)
  })

  test("a video that isn't reviewed can't go this way either, and says why", async () => {
    const h = await kit({ api: { client: "on" } })
    await clickText(h, "ep02_duck.mp4", 2)
    await until(h, () => posting(h).video?.name === "ep02_duck.mp4" && posting(h).review.state === "unreviewed")
    await ask(h)
    expect(dialog(h)).toContain("ep02_duck.mp4 0.0 MB ! QA passed, but there is no review yet")
    expect(dialog(h)).toContain("Who can see it Private Made for kids") // nothing to choose: it can't go
    expect(dialog(h)).toContain("▐ Close ▌")
    await clickText(h, "Post to YouTube", 2) // greyed out
    await h.settle(500)
    expect(frame(h)).toContain("POST TO YOUTUBE THROUGH ITS API")
    expect([jobsMade(), (await google(h)).starts]).toEqual([0, []])
  })
})

describe("a post through the API", () => {
  test("goes from your yes to uploading with its percent, the last piece, and posted with its link", async () => {
    const h = await kit({ api: { client: "on", kids: false, hold: 1 } })
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "uploading" && row(h).includes("44%"))
    expect(frame(h)).not.toContain("POST TO YOUTUBE THROUGH ITS API")
    // One piece of three is up (the video is nine bytes, a piece four), and the second waits for this test.
    expect(row(h)).toMatch(/^YouTube ● uploading 44% to YouTube, as private ▐ Cancel ▌$/)
    expect([youtube(h).via, youtube(h).busy]).toEqual(["api", true])
    await google(h, "hold", 2) // let the second through: the third is the last
    await until(h, () => youtube(h).state === "posting")
    expect(row(h)).toBe("YouTube ● the last piece is on its way: waiting for YouTube to say it's up") // no button: only YouTube's word ends it
    await google(h, "release")
    await until(h, () => youtube(h).state === "posted")
    expect(row(h)).toContain("✓ posted")
    expect(row(h)).toContain("It is private, as you chose. Change who can see it in YouTube Studio when you're ready.")
    expect(row(h)).toMatch(/▐ Open ▌ ▐ Post again… ▌ ▐ Again with API… ▌$/)
    expect(popups()).toEqual([]) // the row says it, with its buttons: no pop-up sits on them
    // What Google got: that title and description, private, and the answer on file about kids. One video.
    const sent = await google(h)
    expect(sent.starts).toEqual([{ snippet: { title: `${CAPTION} #shorts`, description: TAGS, categoryId: "22" }, status: { privacyStatus: "private", selfDeclaredMadeForKids: false } }])
    expect([sent.videos, sent.pieces]).toEqual([["vid00000001"], 3])
    // Open opens the video's own link, and the connection now knows the channel.
    await clickText(h, "▐ Open ▌", 3)
    await until(h, () => listed("opened.txt").length > 0)
    expect(listed("opened.txt")).toEqual([LINK])
    expect(api(h).channel?.title).toBe("Night Physics")
    expect(jobsMade()).toBe(1)
  })

  test("is only posted again by the button that says so", async () => {
    const h = await kit({ api: { client: "on", kids: false } })
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "posted")
    toasts.clear()
    // The row's own call posts nothing a second time, whoever makes it.
    await h.bridge.call("post_action", youtube(h).job, "post_api")
    await h.settle(500)
    expect(popups().at(-1)).toContain("Already posted")
    expect((await google(h)).starts.length).toBe(1)
    toasts.clear()
    await ask(h, "Again with API…")
    expect(dialog(h)).toContain("Already posted with ReelForge's help. Posting it makes a second video on your channel.")
    expect(dialog(h)).toContain("▐ Cancel ▌ ▐ Post it again ▌")
    expect(focused(h)).toBe("Cancel")
    await clickText(h, "Post it again", 2)
    await until(h, () => jobsMade() === 2 && youtube(h).state === "posted")
    expect((await google(h)).videos).toEqual(["vid00000001", "vid00000002"])
  })

  test("that YouTube stored another way than you chose is posted, with an amber note on its row", async () => {
    const h = await kit({ api: { client: "on", kids: false, google: { stores_as: "private" } } })
    await ask(h)
    await clickText(h, "○  Public", 4) // for this one post
    expect(dialog(h)).toContain("┃ ○ Private ┃ ○ Unlisted ┃ ● Public")
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "posted")
    expect((await google(h)).starts[0]!.status.privacyStatus).toBe("public") // what you chose is what was sent
    expect(row(h)).toContain("✓ posted")
    expect(row(h)).toContain("YouTube stored it as private, not public as you chose. Change who can see it in YouTube Studio.")
    const note = youtube(h).line.at(-1)!
    expect(note[1]).not.toBe(youtube(h).line[1]![1]) // in another colour than the quiet words beside "posted": amber
    expect(popups()).toEqual([])
    expect(api(h).privacy).toBe("private") // the choice was for that post only
  })

  test("sent with no made-for-kids answer says so on its row, and says it when YouTube has the video as made for kids", async () => {
    const h = await kit({ api: { client: "on", google: { channel_kids: true } } })
    await ask(h)
    expect(dialog(h)).toContain("Made for kids Not sent: you haven't answered, so your channel's own setting applies")
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "posted")
    expect((await google(h)).starts[0]!.status).toEqual({ privacyStatus: "private" }) // no answer was sent
    expect(row(h)).toContain("YouTube has it as made for kids. ReelForge sent no answer to that, because you haven't given one, so this is your channel's own setting. Check its audience in YouTube Studio.")
  })

  test("that YouTube refuses says why in a sentence, and Try again goes through the API again", async () => {
    const h = await kit({ extension: "on", api: { client: "on", kids: false, google: { start_script: [[403, "quotaExceeded"]] } } })
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "failed")
    const why = "Your Google Cloud project has used up today's YouTube API allowance. It starts again at midnight Pacific time. Nothing was posted."
    expect(row(h)).toContain(`× failed: ${why}`)
    expect(row(h)).toMatch(/▐ Try again ▌ ▐ Post with API… ▌$/)
    expect(popups()).toEqual([])
    expect((await google(h)).videos).toEqual([])
    // This window isn't listening for the browser extension, which stops a Try again that needs it. This one
    // doesn't: it went through the API, and goes through it again.
    toasts.clear()
    await clickText(h, "Try again", 2)
    await until(h, () => youtube(h).state === "posted")
    expect(frame(h)).not.toContain("BROWSER EXTENSION")
    expect(row(h)).toContain("✓ posted")
    expect([(await google(h)).videos, jobsMade(), listed("opened.txt")]).toEqual([["vid00000001"], 2, []]) // and no page for the extension
  })

  test("whose last piece got no answer is not sure it posted, and takes your word either way", async () => {
    const h = await kit({ api: { client: "on", kids: false, google: { script: [null, null, "lost"], then: "drop" } } })
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "unknown")
    expect(row(h)).toContain("? did it post? ReelForge lost track: check YouTube Studio before trying again")
    expect(row(h)).toMatch(/▐ It's posted ▌ ▐ It didn't post ▌$/) // and no Try again: the video may be up
    expect(popups()).toEqual([])
    await clickText(h, "It didn't post", 2)
    await until(h, () => youtube(h).state === "cancelled")
    expect(row(h)).toMatch(/▐ Try again ▌ ▐ Post with API… ▌$/)
    // Once more, lost the same way, and this time you find it in YouTube Studio.
    await google(h, "tell", { script: [null, null, "lost"], then: "drop" })
    await clickText(h, "Try again", 2)
    await until(h, () => youtube(h).state === "unknown")
    await clickText(h, "It's posted", 2)
    await until(h, () => youtube(h).state === "posted")
    expect(row(h)).toContain("✓ posted")
    expect((await google(h)).starts.length).toBe(2) // two tries, each sent once
  })

  test("can be cancelled while it uploads, and a second video waits its turn", async () => {
    const h = await kit({ api: { client: "on", kids: false, hold: 0 } })
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "uploading")
    await clickText(h, "clip.mp4", 2)
    await until(h, () => posting(h).video?.name === "clip.mp4")
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "queued")
    expect(row(h)).toMatch(/^YouTube ● waiting its turn another video is uploading to YouTube first ▐ Cancel ▌$/)
    await clickText(h, "▐ Cancel ▌", 3)
    await until(h, () => youtube(h).state === "cancelled")
    expect(row(h)).toContain("○ cancelled (you cancelled it)")
    await clickText(h, "ep03_hail.mp4", 2)
    await until(h, () => posting(h).video?.name === "ep03_hail.mp4")
    expect(youtube(h).state).toBe("uploading")
    await clickText(h, "▐ Cancel ▌", 3)
    await until(h, () => youtube(h).state === "cancelled")
    await google(h, "release")
    await h.settle(800)
    expect(youtube(h).state).toBe("cancelled")
    const sent = await google(h)
    expect([sent.starts.length, sent.videos]).toEqual([1, []]) // the first was called off after one piece, the second never started
  })
})

describe("a window opened afterwards", () => {
  test("is not connected: it offers Connect, its Post starts nothing, and the post that is up still shows", async () => {
    let h = await kit({ api: { client: "on", kids: false } })
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "posted")
    await h.close()
    toasts.clear()
    // ReelForge opened again, on the same folder: the sign-in went with the window that closed.
    h = await kit({}, [150, 45], true)
    expect([api(h).state, api(h).client]).toEqual(["again", true])
    expect(flat(h)).toContain("○ YouTube API: not connected in this window")
    expect(row(h)).toContain("✓ posted")
    expect(row(h)).toMatch(/▐ Open ▌ ▐ Post again… ▌ ▐ Connect API ▌$/)
    expect(row(h)).not.toContain("with API")
    await clickText(h, "clip.mp4", 2)
    await until(h, () => posting(h).video?.name === "clip.mp4")
    expect(row(h)).toMatch(/^YouTube ○ not posted ▐ Connect API ▌$/) // no post on offer
    // However a post is asked for, nothing starts: there is no sign-in to post with.
    const made = jobsMade()
    expect(await h.bridge.call("post_with_api", "Clip", TAGS, "private", false)).toBeNull()
    await h.bridge.call("post_action", "youtube", "post_api")
    await h.settle(600)
    expect(popups().at(-1)).toBe(`YouTube's API: ${NOT_HERE}`)
    expect([jobsMade(), (await google(h)).starts]).toEqual([made, []])
    expect(row(h)).toMatch(/^YouTube ○ not posted ▐ Connect API ▌$/)
    // The question, should it be asked for all the same, says to connect first and has no yes.
    const info = await h.bridge.call<{ can_post: boolean; why: string }>("youtube_api_info", "Clip", TAGS)
    expect([info.can_post, info.why]).toEqual([false, NOT_HERE])
    // Connect, and it is on offer again.
    toasts.clear()
    await clickText(h, "Connect API", 2)
    await until(h, () => api(h).state === "on")
    h.t.mockInput.pressKey("ESCAPE")
    await until(h, () => youtube(h).actions.includes("post_api") && !frame(h).includes("YOUTUBE API"))
    expect(row(h)).toMatch(/^YouTube ○ not posted ▐ Post with API… ▌$/)
  })

  test("a post that failed for want of a sign-in says to connect first, and Try again says it again", async () => {
    const h = await kit({ api: { client: "on", kids: false } })
    await google(h, "end_grant") // Google ends the sign-in: revoked there, or its seven days are up
    await ask(h)
    await clickText(h, "Post to YouTube", 2)
    await until(h, () => youtube(h).state === "failed")
    const why = "YouTube's sign-in has run out or was taken back at Google. Connect again. While your Google Cloud consent screen is in Testing, Google ends it after 7 days."
    expect(row(h)).toContain(`× failed: ${why} Nothing was posted.`)
    expect(row(h)).toMatch(/▐ Try again ▌ ▐ Connect API ▌$/)
    expect(flat(h)).toContain("● YouTube API: connect again")
    toasts.clear()
    await clickText(h, "Try again", 2)
    await until(h, () => popups().length > 0)
    expect(popups()).toEqual([`YouTube's API: ${why}`])
    expect(jobsMade()).toBe(1)
  })
})

describe("Disconnect", () => {
  test("asks first, then forgets the client and tells Google", async () => {
    const h = await kit({ api: { client: "on", kids: false } })
    await openApi(h)
    expect(dialog(h)).toContain("Guide: docs/YOUTUBE-API.md ▐ Disconnect ▌ ▐ Close ▌")
    expect(focused(h)).toBe("Close")
    await clickText(h, "▐ Disconnect ▌", 3)
    expect(dialog(h)).toContain("Disconnect? ReelForge forgets the client, then tells Google that this window's access has ended. No ReelForge window can post through the API until you give the client file again.")
    expect(focused(h)).toBe("Keep it")
    expect([api(h).state, Object.keys(onFile()).includes("client_id"), (await google(h)).revoked]).toEqual(["on", true, 0]) // asked, not done
    h.t.mockInput.pressKey("RETURN") // Keep it
    await h.settle(300)
    expect(dialog(h)).not.toContain("Disconnect?")
    expect(api(h).state).toBe("on")
    await clickText(h, "▐ Disconnect ▌", 3)
    await clickText(h, "▐ Disconnect ▌", 3) // the red one, in its place now
    await until(h, () => api(h).state === "off")
    expect([onFile(), (await google(h)).revoked]).toEqual([null, 1])
    expect(dialog(h)).toContain("○ YouTube API not connected")
    expect(dialog(h)).toContain("CLIENT FILE")
    expect(popups().at(-1)).toBe("YouTube's API: Disconnected. ReelForge has forgotten the connection, and Google has ended it.")
    toasts.clear()
    h.t.mockInput.pressKey("ESCAPE")
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(400)
    expect(row(h)).toBe("YouTube ○ not posted") // nothing on the row any more
  })
})

describe("when something goes wrong, or the window is small", () => {
  test("a call that fails is a pop-up, and the window carries on", async () => {
    const h = await kit({ api: { client: true }, fail: ["youtube_api_info", "youtube_api_connect", "youtube_api_defaults"] })
    await openApi(h)
    await clickText(h, "▐ Connect ▌", 3)
    await until(h, () => popups().length > 0)
    expect(popups()).toEqual(["Something went wrong: RuntimeError: boom"])
    expect(dialog(h)).toContain("▐ Connect ▌") // still there, to press again
    toasts.clear()
    await clickText(h, "○  Public", 4)
    await until(h, () => popups().length > 0)
    expect(popups()).toEqual(["Something went wrong: RuntimeError: boom"])
    expect(dialog(h)).toContain("● Private") // nothing was changed, and nothing says it was
    toasts.clear()
    await clickText(h, "▐ Close ▌", 3)
    h.bridge.emit("posting", { ...posting(h), rows: posting(h).rows.map((r) => (r.key === "youtube" ? { ...r, actions: ["post_api"] } : r)) })
    await h.settle(300)
    await clickText(h, "Post with API…", 3)
    await until(h, () => popups().length > 0)
    expect(popups()).toEqual(["Something went wrong: RuntimeError: boom"])
    expect(frame(h)).not.toContain("POST TO YOUTUBE THROUGH ITS API")
    expect(frame(h)).toContain("▐ Post… ▌")
  })

  test("a button only ever shows its words: an action the window has none for isn't drawn", async () => {
    const h = await kit({ api: { client: "on" } })
    const view = posting(h)
    h.bridge.emit("posting", { ...view, rows: view.rows.map((r) => (r.key === "youtube" ? { ...r, actions: ["post_api", "connect_api", "something_new"], api: { force: true } } : r)) })
    await h.settle(300)
    expect(row(h)).toMatch(/▐ Post with API… ▌ ▐ Connect API ▌ ▐ Again with API… ▌$/)
    for (const name of ["post_api", "connect_api", "something_new", "post_api_again"]) expect(frame(h)).not.toContain(name)
  })

  test("in a window 80 columns by 24 rows both dialogs keep their buttons in view, and Tab reaches every control", async () => {
    const h = await kit({ api: { client: true } }, [80, 24])
    h.t.mockInput.pressKey("\u001b[6~") // Page Down: POST is below the videos in a narrow window
    await h.settle(400)
    await openApi(h)
    for (const text of ["YOUTUBE API", "○ YouTube API: not connected in this window", "▐ Disconnect ▌", "▐ Close ▌", "▐ Connect ▌"]) expect(frame(h)).toContain(text)
    expect(focused(h)).toBe("Connect")
    // Tab goes round: the two choices (scrolled into view), then the buttons.
    const seen: string[] = []
    for (let i = 0; i < 5; i++) {
      h.t.mockInput.pressKey("TAB")
      await h.settle(200)
      seen.push(focused(h))
    }
    expect(seen.slice(2)).toEqual(["Disconnect", "Close", "Connect"])
    expect(frame(h)).toContain("▐ Connect ▌")
    h.t.mockInput.pressKey("RETURN")
    await until(h, () => api(h).state === "on")
    expect(popups()).toEqual([]) // nothing over the dialog's buttons
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(400)
    expect(frame(h)).not.toContain("FOR POSTS TO COME")
    await until(h, () => youtube(h).actions.includes("post_api"))
    await ask(h)
    for (const text of ["POST TO YOUTUBE THROUGH ITS API", "ep03_hail.mp4", "▐ Cancel ▌", "▐ Post to YouTube ▌"]) expect(frame(h)).toContain(text)
    h.t.mockInput.pressKey("ESCAPE")
    await h.settle(400)
    expect(frame(h)).not.toContain("POST TO YOUTUBE THROUGH ITS API")
    expect(jobsMade()).toBe(0)
  })

  test("with the API connected, everything that starts a post still shows in a window 120 columns by 30 rows", async () => {
    const h = await kit({ api: { client: "on" } }, [120, 30])
    const f = frame(h)
    for (const text of ["POST  ✓ QA passed", "▐ Post with API… ▌", "● YouTube API connected  tested on a stand-in only", "YouTube API…", "▐ Post… ▌", "BY HAND", "▐ Open all 4 → ▌", "TikTok →", "YouTube →", "Instagram →", "X →", "Copy YouTube hashtags"])
      expect(f).toContain(text)
  })
})
