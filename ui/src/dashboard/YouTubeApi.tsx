// Posting to YouTube through its own API, as the window shows and starts it. On the Publish tab, above Post…: one
// quiet line for the connection (ApiLine), which is only its button until a client file was given. That button opens
// the connection's dialog (ApiDialog): where it stands, the client file, Connect, Disconnect, and the two choices for
// posts to come. The YouTube row's own button opens the question before a post (ApiPostDialog): exactly what will be
// sent, who can see it, and that this way of posting has never been run against YouTube itself.
//
// dashboard/youtube_api_tab.py sends the "youtube_api" view and writes its words; docs/YOUTUBE-API.md is the page for
// the person. Nothing here uploads by itself: an upload starts with your yes in the question, and Try again on a row.
// The sign-in is kept by the window's Python side while the window is open and never in a file, so every window
// connects before it can post.

import { TextAttributes } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/react"
import { useEffect, useRef, useState } from "react"
import { useView } from "../bridge"
import { cellLen, plain, wrappedRows, type Action, type Parts, type Seg } from "../lib/styled"
import { DASH } from "../theme"
import { Button } from "../ui/Button"
import { TextField } from "../ui/Fields"
import { Fitted } from "../ui/Line"
import { DialogButtons, Modal, modalRoom, Scroll, toasts, Wrapped } from "../ui/Panels"
import { useDash, type ApiPostData } from "./state"
import type { PostingRow, PostingView, YouTubeApiView } from "./types"

/** The connection to YouTube's API, on the Publish tab: the row above Post…, which was empty. So the tab is no taller
 * with it, and everything that starts a post still shows in a window 30 rows tall. For a channel that never gave a
 * client file it is one quiet button at the row's end. With a client on file the row also says where the connection
 * stands, in the Python side's words (and, while a post is on offer on the YouTube row, how far this way of posting
 * is tested). Only where it can be used: with YouTube connected, and never in the demo. */
export function ApiLine({ rows }: { rows: PostingRow[] }) {
  const s = useDash()
  const api = useView<YouTubeApiView>(s.bridge, "youtube_api")
  if (!api || s.init.demo || !rows.some((r) => r.key === "youtube")) return <box height={1} flexShrink={0} />
  const button = <Button id="api-open" label="YouTube API…" variant="ghost" onPress={() => s.set({ dialog: { kind: "youtube_api" } })} />
  if (api.state === "off")
    return (
      <box flexDirection="row" height={1} justifyContent="flex-end" flexShrink={0}>
        {button}
      </box>
    )
  // Its first words always, then how far it is tested, then the rest piece by piece while there's room (the Python
  // side sends the line in pieces that each end on a whole word). A click on any of it opens the dialog, as the
  // button does.
  const open: Action = ["youtube_api"]
  const clickable = (seg: Seg): Seg => [seg[0], seg[1], open]
  const parts: Parts = [
    [api.line.slice(0, 1).map(clickable), 0],
    [api.words.caveat ? [[`  ${api.words.caveat}`, DASH.faint, open]] : [], 1],
    ...api.line.slice(1).map((seg, i): Parts[number] => [[clickable(seg)], 2 + i]),
  ]
  return (
    <box flexDirection="row" height={1} flexShrink={0} gap={1}>
      <Fitted parts={parts} flexGrow={1} />
      {button}
    </box>
  )
}

// ---------------------------------------------------------------- the connection

const WIDTH = 96 // the connection's dialog
const CHOICE = 24 // columns for what each of the two choices is called, before its options

/** YouTube's API: where the connection stands, and everything that changes it. With no client on file (or one Google
 * no longer knows), the box for the client file's path. With one, Connect (Google's consent page opens in your
 * browser, and the dialog says what it is waiting for), Disconnect, which asks first, and the two choices for posts
 * to come: who can see a new post, and the made-for-kids answer, which is "not answered" until you give one. Each
 * choice is kept as soon as it is picked. connect: it was opened by the YouTube row's Connect API button, and that
 * press was the ask: Google's page opens at once. */
export function ApiDialog({ connect: now, close }: { connect?: boolean; close: () => void }) {
  const s = useDash()
  const api = useView<YouTubeApiView>(s.bridge, "youtube_api")
  const { width } = useTerminalDimensions()
  const [path, setPath] = useState("")
  const [bad, setBad] = useState(false) // the file at that path wasn't taken (a pop-up says why)
  const [leaving, setLeaving] = useState(false) // Disconnect was pressed: asking
  const [busy, setBusy] = useState(false)
  /** Connect: with the client file at this path, or ("") with the one on file. The answer is whether Google's page is
   * opening; why not comes as a pop-up, and the view follows your answer on that page. (true: this dialog says how
   * it goes, so the Python side sends no pop-up to sit on its buttons unless something went wrong.) */
  const connect = async (file: string) => {
    if (busy) return
    setBusy(true)
    const opening = await s.bridge.ask<boolean>("youtube_api_connect", file, true)
    setBusy(false)
    setBad(Boolean(file) && !opening)
    if (opening) setPath("")
  }
  useEffect(() => {
    if (now && api?.state === "again" && !api.wants_file) void connect("")
  }, [])
  if (!api) {
    return (
      <Modal name="youtube-api" title="YOUTUBE API" width={WIDTH}>
        <text fg={DASH.muted} flexShrink={0}>
          Looking at the connection…
        </text>
        <DialogButtons>
          <Button id="api-close" label="Close" autoFocus onPress={close} />
        </DialogButtons>
      </Modal>
    )
  }
  const waiting = api.state === "connecting"
  const on = api.state === "on"
  // (typed: the box's own word on what is in it, which is ahead of this dialog's when Enter follows a paste at once)
  const withFile = (typed = path) => (typed.trim() ? void connect(typed) : setBad(true))
  const kids = api.made_for_kids === null ? "" : api.made_for_kids ? "yes" : "no"
  // "Not answered" is there until you answer: an answer can be changed, and not taken back.
  const kidsOptions: [string, string][] = [...(api.made_for_kids === null ? ([["", "Not answered"]] as [string, string][]) : []), ["no", "No"], ["yes", "Yes"]]
  // The rows of the part that scrolls, as it will wrap, so it is as tall as what's in it and no taller.
  // (room: two columns short of the dialog's, for the scrollbar and a column of air beside it)
  const room = modalRoom(WIDTH, width) - 2
  const choices = api.client && !api.wants_file // (a client Google no longer knows: the file first)
  const bodyRows =
    api.lines.reduce((n, l) => n + wrappedRows(plain(l), room), api.lines.length ? 1 : 0) +
    (choices ? 2 + 2 + 1 + wrappedRows(api.words.start_as, room) + wrappedRows(api.words.kids, room) : 0)
  return (
    <Modal name="youtube-api" title="YOUTUBE API" width={WIDTH}>
      <box flexDirection="row" flexShrink={0}>
        <Wrapped segs={api.line} />
      </box>
      {waiting ? (
        <box flexDirection="row" marginTop={1} flexShrink={0}>
          <Wrapped segs={[[api.words.waiting, DASH.warn]]} />
        </box>
      ) : null}
      {/* What scrolls, in a window too short for all of it, so that the box for the file, what it is for and the
          buttons always show. Its words are given their width (room): see Wrapped. */}
      <Scroll maxHeight={bodyRows} flexShrink={1} scrollY>
        <box flexDirection="column" flexShrink={0} paddingRight={2}>
          {api.lines.map((l, i) => (
            <box key={i} flexDirection="row" flexShrink={0} marginTop={i ? 0 : 1}>
              <Wrapped segs={l} width={room} />
            </box>
          ))}
          {choices ? (
            <>
              <text fg={DASH.text} attributes={TextAttributes.BOLD} marginTop={1} flexShrink={0}>
                FOR POSTS TO COME
              </text>
              <box flexDirection="row" height={1} flexShrink={0}>
                <text fg={DASH.text} width={CHOICE} flexShrink={0} wrapMode="none">
                  Who can see a new post
                </text>
                <Picks id="api-privacy" options={api.privacies.map((p) => [p.key, p.label])} value={api.privacy} onChange={(key) => s.bridge.send("youtube_api_defaults", key, null)} />
              </box>
              <box flexDirection="row" height={1} flexShrink={0}>
                <text fg={DASH.text} width={CHOICE} flexShrink={0} wrapMode="none">
                  Made for kids
                </text>
                <Picks id="api-kids" options={kidsOptions} value={kids} onChange={(key) => (key ? s.bridge.send("youtube_api_defaults", null, key === "yes") : undefined)} />
              </box>
              <box flexDirection="column" marginTop={1} flexShrink={0}>
                <Wrapped segs={[[api.words.start_as, DASH.muted]]} width={room} />
                <Wrapped segs={[[api.words.kids, DASH.muted]]} width={room} />
              </box>
            </>
          ) : null}
        </box>
      </Scroll>
      {/* (while Google's page is waiting, the box alone: what it is for was read on the way here, and with it the
          dialog's buttons no longer fit in a window 24 rows tall) */}
      {api.wants_file && !waiting ? (
        <box flexDirection="column" marginTop={1} flexShrink={0}>
          <text fg={DASH.text} attributes={TextAttributes.BOLD} flexShrink={0}>
            CLIENT FILE
          </text>
          <box flexDirection="row" flexShrink={0}>
            <Wrapped segs={[[api.words.file, DASH.muted]]} />
          </box>
        </box>
      ) : null}
      {api.wants_file ? (
        <box flexDirection="row" height={3} flexShrink={0} gap={1} alignItems="center">
          <TextField
            id="api-file"
            flexGrow={1}
            value={path}
            bad={bad}
            autoFocus
            placeholder="/path/to/client_secret_….json"
            onChange={(v) => {
              setPath(v)
              setBad(false)
            }}
            onSubmit={(typed) => withFile(typed)}
          />
          <Button id="api-file-connect" label="Connect with this file" variant="primary" disabled={busy} onPress={() => withFile()} />
        </box>
      ) : null}
      {leaving ? (
        <box flexDirection="row" marginTop={1} flexShrink={0}>
          <Wrapped segs={[[api.words.leave, DASH.warn]]} />
        </box>
      ) : null}
      <box flexDirection="row" height={1} marginTop={leaving ? 1 : 2} flexShrink={0} gap={1}>
        <text fg={DASH.faint} wrapMode="none" flexGrow={1} flexShrink={1}>
          {leaving ? "" : `Guide: ${api.docs}`}
        </text>
        {leaving ? (
          <>
            <Button id="api-keep" label="Keep it" autoFocus onPress={() => setLeaving(false)} />
            <Button
              id="api-drop"
              label="Disconnect"
              variant="error"
              onPress={() => {
                setLeaving(false)
                s.bridge.send("youtube_api_disconnect")
              }}
            />
          </>
        ) : (
          <>
            {/* (not while Google's page is waiting: one thing at a time, and with it where the guide is no longer
                fits beside the buttons in a window 80 columns wide) */}
            {api.client && !waiting ? <Button id="api-disconnect" label="Disconnect" onPress={() => setLeaving(true)} /> : null}
            <Button id="api-close" label="Close" autoFocus={on || waiting} onPress={close} />
            {api.client && !on && !api.wants_file ? (
              <Button id="api-connect" label={waiting ? "Open Google's page again" : "Connect"} variant="primary" autoFocus={!waiting} disabled={busy} onPress={() => void connect("")} />
            ) : null}
          </>
        )}
      </box>
    </Modal>
  )
}

/** One of a few, all in one row: ● marks the one picked, and a press (or Tab to one, then Enter) picks another. In a
 * row because these sit in a part of a dialog that scrolls in a short window: a list of them one under the other was
 * cut off there, and its arrows moved on to an option out of sight. */
function Picks({ id, options, value, onChange }: { id: string; options: [string, string][]; value: string; onChange: (key: string) => void }) {
  return (
    <box flexDirection="row" height={1} flexShrink={0} gap={1}>
      {options.map(([key, label]) => (
        <Button key={key} id={`${id}-${key || "none"}`} label={`${key === value ? "●" : "○"} ${label}`} variant={key === value ? "default" : "ghost"} onPress={() => onChange(key)} />
      ))}
    </box>
  )
}

// ---------------------------------------------------------------- the question before a post

/** The question before a post through YouTube's API: the video and its review, what happens after your yes, that this
 * way of posting has never been run against YouTube itself, then exactly what will be sent, a row for each thing,
 * with who can see it to choose for this one post, and what is left to YouTube Studio. A video that is up there
 * already is only posted again by the button that says so. Nothing is uploaded until Post, and the yes goes with
 * what the question showed: the Python side starts nothing when that is no longer so. */
export function ApiPostDialog({ data, close }: { data: ApiPostData; close: () => void }) {
  const s = useDash()
  const { info } = data
  const [privacy, setPrivacy] = useState(info.send?.privacy ?? info.privacy)
  const [busy, setBusy] = useState(false)
  const sent = useRef(false) // Post was pressed (a second Enter can come before the window has drawn again)
  const ok = info.can_post && Boolean(info.video && info.send) && !busy
  const go = async () => {
    if (!ok || sent.current) return
    sent.current = true
    setBusy(true)
    // What it started, and why not when it didn't, come back as the YouTube row and as pop-ups.
    await s.bridge.ask("post_with_api", data.caption, data.tags, privacy, info.force, { video: info.video!.path, made_for_kids: info.send!.made_for_kids })
    if (s.dialog?.kind === "api_post") close() // (Esc may have closed it meanwhile)
  }
  // The question is about one video. When another is the picked one by now (this one was hidden or removed in another
  // window, or its file is gone), it closes: nothing it shows would be what Post uploads.
  const now = useView<PostingView>(s.bridge, "posting")
  const moved = Boolean(info.video && now && now.video?.path !== info.video.path)
  useEffect(() => {
    if (!moved || sent.current) return // (a yes that is on its way is the Python side's to answer, and it says so)
    toasts.add({ title: "Nothing was started", body: `${info.video!.name} isn't the picked video any more, so the question about it closed.`, severity: "warning", timeout: 10 })
    close()
  }, [moved])
  const review = now?.review.line ?? (info.review ? [[info.review.line, DASH.muted] as Seg] : [])
  // What stops it, unless the review's own line above already says so. A video that is up there isn't stopped: it
  // says so, with what a post again means.
  const stuck = !info.can_post && info.why !== info.review?.line ? info.why : ""
  const again = info.force && info.blocked ? `${info.blocked.message} ${info.again}` : ""
  const labels = Math.max(0, ...info.fields.map((f) => cellLen(f.label))) + 2
  // The rows of what will be sent, as they will wrap, so their scrolling area is as tall as they are and no taller.
  const room = modalRoom(100, useTerminalDimensions().width) - 2
  const rows =
    info.fields.reduce((n, f) => n + (f.key === "privacy" && ok ? 1 : wrappedRows(f.text, room - labels)), 0) + (info.fields.length ? 1 + wrappedRows(info.left, room) : 0)
  return (
    <Modal name="api-post" title="POST TO YOUTUBE THROUGH ITS API">
      {info.video ? (
        <box flexDirection="row" flexShrink={0}>
          <Wrapped segs={[[info.video.name, "bold"], [`  ${info.video.size}    `, DASH.muted], ...review]} />
        </box>
      ) : null}
      {stuck || again ? (
        <box flexDirection="row" marginTop={info.video ? 1 : 0} flexShrink={0}>
          <Wrapped segs={[[stuck || again, DASH.warn]]} />
        </box>
      ) : null}
      {info.fields.length ? (
        <>
          <box flexDirection="row" marginTop={1} flexShrink={0}>
            <Wrapped segs={[[info.how, DASH.text]]} />
          </box>
          <box flexDirection="row" marginBottom={1} flexShrink={0}>
            <Wrapped segs={[[info.note, DASH.muted]]} />
          </box>
        </>
      ) : null}
      <Scroll maxHeight={rows} flexShrink={1} scrollY>
        <box flexDirection="column" flexShrink={0} paddingRight={2}>
          {info.fields.map((f) => (
            <box key={f.key} flexDirection="row" flexShrink={0}>
              <text fg={DASH.faint} width={labels} flexShrink={0} wrapMode="none">
                {f.label}
              </text>
              {f.key === "privacy" && ok ? (
                // who can see it: yours to choose for this one post, whatever posts start as
                <Picks id="api-post-privacy" options={info.privacies.map((p) => [p.key, p.label])} value={privacy} onChange={setPrivacy} />
              ) : (
                <Wrapped segs={[[f.text, DASH.text]]} width={room - labels} />
              )}
            </box>
          ))}
          {info.fields.length ? (
            <box flexDirection="row" marginTop={1} flexShrink={0}>
              <Wrapped segs={[[info.left, DASH.faint]]} width={room} />
            </box>
          ) : null}
        </box>
      </Scroll>
      <DialogButtons>
        {/* Cancel has the keyboard when the question opens: an Enter still held from the row's button closes it. */}
        <Button id="api-post-cancel" label={info.can_post ? "Cancel" : "Close"} autoFocus onPress={close} />
        <Button id="api-post-start" label={busy ? "Starting…" : info.force ? "Post it again" : "Post to YouTube"} variant="primary" disabled={!ok} onPress={() => void go()} />
      </DialogButtons>
    </Modal>
  )
}
