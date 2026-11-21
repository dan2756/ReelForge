// Posting with the browser extension, as the window shows and starts it. On the Publish tab: where the picked video's
// post stands on each platform, with what can be done about it, and Post…, which asks first (PostDialog: each
// platform's exact text, its account, what stops it) and then hands the posts to the extension through a page in your
// browser. The extension itself (ExtensionDialog: its state, the browsers it's connected in, connecting and
// disconnecting one, the steps that set it up) and its one line on the Accounts tab (ExtensionStrip). YouTube's own
// API, a second way to post there, is YouTubeApi.tsx: its line sits above Post…, and its buttons on the YouTube row
// are drawn here like any other row's.
//
// dashboard/posting_tab.py sends the "posting" and "extension" views and writes their words. Nothing here posts by
// itself: a post starts with your press in the question, and ends at the platform's own Post button. The question's
// "Stop before the Post button" switch, off, is one of three things the extension wants before it presses that
// button: Press Post for me on in its own settings, and your yes for that post in the browser, are the other two.
// The extension is optional, and everything on the Publish tab's By hand rows works without it.

import { TextAttributes, type ScrollBoxRenderable } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/react"
import { useEffect, useRef, useState } from "react"
import { useView } from "../bridge"
import { useFocus, useFocusManager } from "../lib/focus"
import { cellLen, plain, segsLen, wrappedRows, type Parts, type Segs } from "../lib/styled"
import { DASH } from "../theme"
import { Button, type Cell } from "../ui/Button"
import { Switch } from "../ui/Fields"
import { Fitted } from "../ui/Line"
import { DialogButtons, Modal, modalRoom, Panel, Scroll, toasts, useWidth, Wrapped } from "../ui/Panels"
import { useDash, type PostData } from "./state"
import type { ExtensionInstall, ExtensionView, FilesView, PlatformInfo, PostInfoPlatform, PostingRow, PostingView } from "./types"
import { ApiLine, focusRow } from "./YouTubeApi"

/** A line from the Python side as parts for a narrow place: its first piece always, the rest while there's room. Every
 * piece opens the extension's dialog when clicked. */
function foldable(line: Segs): Parts {
  return line.map((seg, i) => [[[seg[0], seg[1], ["extension"]]], i ? 1 : 0])
}

/** Whether this window can hand a post to the extension now, as far as the extension goes: one is connected and of
 * this ReelForge's version, and this window is listening for it. */
const reachable = (ext: ExtensionView | undefined) => Boolean(ext && (ext.state === "on" || ext.state === "waiting") && !ext.problem)

const HINT_BUTTONS = 24 // columns Set it up and Hide take beside the hint, with the gaps
const POST_BUTTON = 11 // ... Post… and the gap after it
const BROWSERS_BUTTON = 15 // ... and Browsers…, with the gap before it
const DOUBLE_CLICK = 450 // ms: a second press this soon after the first is the second click of a double click
/** The clock a double click is told by (not the one on the wall, which can be set back). A test sets it, so that its
 * two clicks are as far apart as it says however slowly its machine draws. */
export const pressClock = { now: () => performance.now() }

/** POST, on the Publish tab: the picked video's review, a row for each connected platform, the line for YouTube's own
 * API (YouTubeApi.tsx: one quiet button until it is set up), then Post… with the extension's state beside it, and
 * while the extension isn't set up one line that says it exists. */
export function PostBlock() {
  const s = useDash()
  const v = useView<PostingView>(s.bridge, "posting")
  const ext = useView<ExtensionView>(s.bridge, "extension")
  const files = useView<FilesView>(s.bridge, "files")
  const [asking, setAsking] = useState(false) // the question is being put together (a new video is read once first)
  const demo = s.init.demo
  const picked = Boolean(v?.video)
  // A window that isn't listening for the extension matters once a browser is connected: until then it's one more
  // line in the extension's dialog, not a warning for someone who posts by hand.
  const problem = ext && ext.state !== "off" ? ext.problem : ""
  // Post explains the set-up, in place of the question, while only the extension is in the way: none connected, one
  // that needs an update, or a window that isn't listening for it.
  const setup = Boolean(v && picked && !demo && (v.setup || (v.can_post && problem)))
  const enabled = Boolean(v && picked && !asking && (v.can_post || setup || demo))
  const press = () => {
    if (setup) return s.set({ dialog: { kind: "extension", from: "post" } })
    setAsking(true)
    void s.openPost().finally(() => setAsking(false))
  }
  const nothing = files && !files.items.length ? plain(files.empty?.[0]) || "No videos yet." : (v?.why ?? "")
  const head: Segs = !v ? [["Looking at this video…", DASH.muted]] : picked ? v.review.line : [[nothing, DASH.muted]]
  // Beside the button: why it can't be pressed (unless a line above already says so: the review's own, or the one for
  // a channel with no platform connected), why this window can't reach the extension, or how the extension is.
  const stuck = v && picked && !demo && !v.can_post && !v.setup && v.rows.length && !plain(v.review.line).includes(v.why) ? v.why : ""
  const nameWidth = Math.max(0, ...(v?.rows ?? []).map((r) => cellLen(r.name))) + 2
  const [width, measure] = useWidth()
  // The extension's line folds to its first words beside the button. Where even those don't fit, they wrap, and only
  // they: the browser's name and when it was last seen are in the extension's dialog, and wrapped here in a column
  // 18 wide they took six rows and pushed BY HAND off a window 100 columns by 30.
  const line = v?.extension.line ?? []
  const browsers = Boolean(v && v.extension.state !== "off" && !problem)
  const fits = segsLen(line.slice(0, 1)) <= width - POST_BUTTON - (browsers ? BROWSERS_BUTTON : 0)
  // The second click of a double click. A press moves its post on, and the buttons with it, in the same place: Cancel
  // becomes Try again, and It's posted comes up where It didn't post was. A row whose words get shorter takes one line
  // where it took two, and the rows under it move up: another platform's button is under the pointer. That second
  // click was a press of whatever had come up: a post just called off started again, or one that never went up
  // written down as posted. So a press on a row that comes straight after another, on the same spot, does nothing
  // when anything about the rows changed in between. (On the same spot: a press somewhere else that soon is one you
  // meant, Cancel on one row and then on the next, and it does what it says.)
  const shown = JSON.stringify([head, (v?.rows ?? []).map((r) => [r.key, r.line, r.actions, Boolean(r.api?.force)])])
  // the rows at the last press, when that was, and the cell the pointer pressed at (none: the keyboard)
  const last = useRef<{ shown: string; at: number; cell?: Cell }>({ shown, at: -DOUBLE_CLICK })
  const pressRow = (row: PostingRow, action: string, cell?: Cell) => {
    const now = pressClock.now()
    const was = last.current
    // (a cell to either side is still the same spot: the hand moves a little between the two clicks)
    const sameSpot = cell && was.cell ? cell.y === was.cell.y && Math.abs(cell.x - was.cell.x) <= 1 : !cell && !was.cell
    if (now - was.at < DOUBLE_CLICK && was.shown !== shown && sameSpot) return
    last.current = { shown, at: now, cell }
    s.postAction(row, action)
  }
  return (
    <box flexDirection="column" marginTop={1} flexShrink={0} onSizeChange={measure}>
      <box flexDirection="row" flexShrink={0}>
        <text fg={DASH.text} attributes={TextAttributes.BOLD} wrapMode="none" flexShrink={0}>
          {"POST  "}
        </text>
        <Wrapped segs={head} width={Math.max(width - 6, 0)} />
      </box>
      {(v?.rows ?? []).map((row) => (
        <PostRow key={row.key} row={row} labels={v!.labels} nameWidth={nameWidth} room={width - nameWidth} press={(action, at) => pressRow(row, action, at)} />
      ))}
      {v && picked && !v.rows.length ? (
        <box flexDirection="row" flexShrink={0} gap={2}>
          <Wrapped segs={[[v.why, DASH.muted]]} flexGrow={0} />
          <Button id="post-accounts" label="Open Accounts" onPress={() => s.setTab("accounts")} />
        </box>
      ) : null}
      {/* (the row above Post…: empty, as it was, unless this channel can post through YouTube's API) */}
      <ApiLine rows={v?.rows ?? []} />
      <box flexDirection="row" flexShrink={0} gap={2}>
        <Button id="post-go" label="Post…" variant="primary" disabled={!enabled} onPress={press} />
        {stuck || problem || !fits ? <Wrapped segs={stuck || problem ? [[stuck || problem, DASH.warn]] : line.slice(0, 1)} /> : <Fitted parts={foldable(line)} flexGrow={1} />}
        {/* the extension's dialog, for the keyboard too (its line is a click away already) */}
        {browsers ? <Button id="ext-browsers" label="Browsers…" variant="ghost" onPress={() => s.set({ dialog: { kind: "extension" } })} /> : null}
      </box>
      {ext && ext.state === "off" && !ext.hint_dismissed && !demo ? (
        <box flexDirection="row" height={1} flexShrink={0} gap={1}>
          {/* its words only while the part that always shows fits beside the buttons: half a sentence says nothing */}
          {Math.min(...ext.hint.filter(([, rank]) => !rank).map(([t]) => segsLen(t))) <= width - HINT_BUTTONS ? <Fitted parts={ext.hint} flexGrow={1} /> : null}
          <Button id="ext-setup" label="Set it up" onPress={() => s.set({ dialog: { kind: "extension" } })} />
          <Button id="ext-hide" label="Hide" variant="ghost" onPress={() => s.bridge.send("dismiss_extension_hint")} />
        </box>
      ) : null}
    </box>
  )
}

/** One platform: its name, where its post stands in the Python side's words, and a button for each thing that can be
 * done about it: at the end of the line while it all fits in one (room: the columns after the name), else under the
 * words, which then wrap under themselves. A button says its words (labels), never an action's own name: one the
 * Python side sent no words for isn't drawn. On a row whose video is up on YouTube while this window is connected to
 * YouTube's API (row.api.force), one more button asks about posting it there again that way. press: what a press of
 * one does (PostBlock's, which knows the second click of a double click). */
function PostRow({ row, labels, nameWidth, room, press }: { row: PostingRow; labels: Record<string, string>; nameWidth: number; room: number; press: (action: string, at?: Cell) => void }) {
  const actions = [...row.actions, ...(row.api?.force ? ["post_api_again"] : [])].filter((a) => labels[a])
  const label = (action: string) => labels[action]!
  const buttons = actions.reduce((n, a) => n + cellLen(label(a)) + 5, 0) // each with its ends and the gap before it
  const beside = segsLen(row.line) + buttons <= room
  return (
    <box flexDirection="row" flexShrink={0}>
      <text fg={row.color} attributes={TextAttributes.BOLD} width={nameWidth} flexShrink={0} wrapMode="none">
        {row.name}
      </text>
      <box flexDirection={beside ? "row" : "column"} flexGrow={1} flexShrink={1} minWidth={0}>
        <box flexDirection="row" flexGrow={beside ? 1 : 0} flexShrink={beside ? 1 : 0}>
          {/* (under the name, the words have the row's measured width: see Wrapped) */}
          <Wrapped segs={row.line} width={beside ? undefined : Math.max(room, 0)} />
        </box>
        {actions.length ? (
          // (under the words they go on under themselves where the row is too narrow for all of them: a posted
          // YouTube row has three, and the third was cut off in a window 100 columns wide)
          <box flexDirection="row" flexWrap={beside ? "no-wrap" : "wrap"} flexShrink={0} columnGap={1}>
            {actions.map((action) => (
              <Button key={action} id={`post-${row.key}-${action}`} label={label(action)} onPress={(at) => press(action, at)} />
            ))}
          </box>
        ) : null}
      </box>
    </box>
  )
}

// ---------------------------------------------------------------- the question before a post

/** Whether a platform can be ticked: nothing stops it, or it is up there already and can be posted again. */
const tickable = (p: PostInfoPlatform) => !p.blocked || p.again
/** Why a platform can't go, with how to post there again all the same where that can be asked for. Nothing when it's
 * what stops every platform (said): the dialog says that once, above them. */
const inTheWay = (p: PostInfoPlatform, said: string[]) =>
  p.blocked && !said.includes(p.blocked.message) ? p.blocked.message + (p.again ? " Tick it to post there again." : "") : ""
const TICK = 3 // columns for a platform's tick box, and the indent of what's under it
const labelWidth = (platforms: PostInfoPlatform[]) => Math.max(0, ...platforms.flatMap((p) => p.fields.map((f) => cellLen(f.label)))) + 2

/** Post…: the video and its review, what happens next in a sentence, then each platform with its tick box, its account
 * and the exact text for each of its boxes (or why it can't go). Start hands the ticked ones to the extension, through
 * a page ReelForge opens in your browser. Nothing is posted here: the extension fills in each upload page and stops,
 * unless you flip the switch here and also say yes on that page (the sentence under the video says which it is). */
export function PostDialog({ data, close }: { data: PostData; close: () => void }) {
  const s = useDash()
  const { info, only } = data
  const [ticked, setTicked] = useState<string[]>(() =>
    info.platforms.filter((p) => (only ? only.includes(p.key) && tickable(p) : !p.blocked)).map((p) => p.key),
  )
  const fm = useFocusManager()
  const [stop, setStop] = useState(true)
  const [busy, setBusy] = useState(false)
  const list = useRef<ScrollBoxRenderable | null>(null)
  const chosen = info.platforms.filter((p) => ticked.includes(p.key))
  const mayPress = chosen.some((p) => p.press) // only then is there anything to switch
  const ok = info.can_post && chosen.length > 0 && !busy
  // Start was pressed. A ref, since a second Enter can come before the window has drawn again: `busy` is still false
  // to that press, and a second yes came back as a warning that the posts just started were "already under way".
  const sent = useRef(false)
  const go = async () => {
    if (!ok || sent.current) return
    sent.current = true
    setBusy(true)
    // What it started and what it refused come back as the rows on the tab and as pop-ups. The video goes with the
    // yes: the Python side starts nothing when another one is picked by now.
    await s.bridge.ask("post_with_extension", chosen.map((p) => p.key), data.caption, data.tags, stop || !mayPress, chosen.filter((p) => p.again).map((p) => p.key), info.video.path, info.video.sha256)
    if (s.dialog?.kind !== "post") return // (Esc closed it while the browser was opening)
    close()
    focusRow(fm, s.bridge.view<PostingView>("posting")?.rows, chosen.map((p) => p.key))
  }
  // The question is about one video. The Python side picks another by itself when this one leaves the list (hidden or
  // removed in another window, or its file is gone): the question then goes, since nothing it shows would be what
  // Start posts.
  const now = useView<PostingView>(s.bridge, "posting")
  const moved = Boolean(now && now.video?.path !== info.video.path)
  useEffect(() => {
    if (!moved || sent.current) return // (a yes that is on its way is the Python side's to answer, and it says so)
    toasts.add({ title: "Nothing was started", body: `${info.video.name} isn't the picked video any more, so the question about it closed.`, severity: "warning", timeout: 10 })
    close()
  }, [moved])
  const review = now?.review.line ?? [[info.review.line, DASH.muted]]
  const count = `${chosen.length} post${chosen.length === 1 ? "" : "s"}`
  // The platforms' rows as they will wrap, so their scrolling area is as tall as they are and no taller.
  const room = modalRoom(100, useTerminalDimensions().width) - TICK
  const said = [info.why, info.review.line] // what stops the whole post isn't repeated under each platform
  const rows = info.platforms.reduce(
    (n, p) => n + 1 + (inTheWay(p, said) ? wrappedRows(inTheWay(p, said), room) : 0) + p.fields.reduce((m, f) => m + wrappedRows(f.text, room - labelWidth(info.platforms)), 0),
    0,
  )
  return (
    <Modal name="post" title="POST THIS VIDEO">
      <box flexDirection="row" flexShrink={0}>
        <Wrapped segs={[[info.video.name, "bold"], [`  ${info.video.size}    `, DASH.muted], ...review]} />
      </box>
      {info.can_post || info.why === info.review.line || info.review.state === "demo" ? null : (
        <box flexDirection="row" marginTop={1} flexShrink={0}>
          <Wrapped segs={[[info.why, DASH.warn]]} />
        </box>
      )}
      <box flexDirection="row" marginTop={1} flexShrink={0}>
        <Wrapped segs={[[mayPress && !stop ? info.how_press : info.how, DASH.text]]} />
      </box>
      <box flexDirection="row" marginBottom={1} flexShrink={0}>
        <Wrapped segs={[[info.note, DASH.muted]]} />
      </box>
      <Scroll ref={list} maxHeight={rows} flexShrink={1} scrollY>
        <Ticks platforms={info.platforms} ticked={ticked} said={said} onChange={setTicked} onMove={(key) => list.current?.scrollChildIntoView(`tick-${key}`)} />
      </Scroll>
      {mayPress ? (
        <box flexDirection="row" height={1} marginTop={1} flexShrink={0} gap={2}>
          <text fg={DASH.text} wrapMode="none">
            Stop before the Post button
          </text>
          <Switch id="post-stop" value={stop} onChange={setStop} />
        </box>
      ) : null}
      <DialogButtons>
        {/* Cancel has the keyboard when the question opens, as in every dialog whose yes can't be taken back: an Enter
            still held from pressing Post… closes it, where on Start it began every ticked post before a word was read. */}
        <Button id="post-cancel" label={info.can_post ? "Cancel" : "Close"} autoFocus onPress={close} />
        <Button id="post-start" label={busy ? "Opening your browser…" : chosen.length ? `Start ${count}` : "Start"} variant="primary" disabled={!ok} onPress={() => void go()} />
      </DialogButtons>
    </Modal>
  )
}

/** The platforms in the question, one focusable list: the arrows move (onMove: to this platform, so a short window
 * can keep it in view), Space or Enter (or a click) ticks. A platform that can't go has no box to tick and says why;
 * one the video is already up on starts unticked. */
function Ticks({
  platforms,
  ticked,
  said,
  onChange,
  onMove,
}: {
  platforms: PostInfoPlatform[]
  ticked: string[]
  said: string[]
  onChange: (keys: string[]) => void
  onMove: (key: string) => void
}) {
  const [cursor, setCursor] = useState(() => Math.max(platforms.findIndex(tickable), 0))
  const move = (to: number) => {
    setCursor(to)
    if (platforms[to]) onMove(platforms[to].key)
  }
  const toggle = (p: PostInfoPlatform | undefined) => {
    if (p && tickable(p)) onChange(ticked.includes(p.key) ? ticked.filter((k) => k !== p.key) : [...ticked, p.key])
  }
  const { ref, focused, focus } = useFocus("post-platforms", {
    disabled: !platforms.length,
    onKey: (key) => {
      const n = platforms.length
      if (key.name === "up") move((cursor - 1 + n) % n)
      else if (key.name === "down") move((cursor + 1) % n)
      else if (key.name === "space" || key.name === "return" || key.name === "enter") toggle(platforms[cursor])
      else return false
      return true
    },
  })
  const nameWidth = Math.max(0, ...platforms.map((p) => cellLen(p.name))) + 2
  return (
    <box ref={ref} flexDirection="column" flexShrink={0} paddingRight={1} onMouseDown={() => focus()}>
      {platforms.map((p, i) => {
        const on = ticked.includes(p.key)
        const here = focused && i === cursor
        return (
          <box
            key={p.key}
            id={`tick-${p.key}`}
            flexDirection="column"
            flexShrink={0}
            onMouseDown={() => {
              setCursor(i)
              toggle(p)
            }}
          >
            <box flexDirection="row" height={1} flexShrink={0} backgroundColor={here ? DASH.cursor : undefined}>
              <text fg={on ? DASH.good : DASH.faint} attributes={TextAttributes.BOLD} width={TICK} flexShrink={0}>
                {on ? "✓ " : tickable(p) ? "○ " : "– "}
              </text>
              <text fg={p.color} attributes={TextAttributes.BOLD} width={nameWidth} flexShrink={0} wrapMode="none">
                {p.name}
              </text>
              <text fg={DASH.muted} wrapMode="none">
                {p.handle}
              </text>
              {/* (the rest of this row is free: a platform with a second way to post can say which here) */}
            </box>
            {inTheWay(p, said) ? (
              <box flexDirection="row" paddingLeft={TICK} flexShrink={0}>
                <Wrapped segs={[[inTheWay(p, said), DASH.warn]]} />
              </box>
            ) : null}
            {p.fields.map((f) => (
              <box key={f.label} flexDirection="row" paddingLeft={TICK} flexShrink={0}>
                <text fg={DASH.faint} width={labelWidth(platforms)} flexShrink={0} wrapMode="none">
                  {f.label}
                </text>
                <text fg={on ? DASH.text : DASH.muted} wrapMode="word" flexGrow={1} flexShrink={1}>
                  {f.text}
                </text>
              </box>
            ))}
          </box>
        )
      })}
    </box>
  )
}

// ---------------------------------------------------------------- the extension itself

const WIDTH = 96 // the extension's dialog
const STEP = 4 // columns for a step's number

/** The browser extension: how it is, what it is (and why this window isn't listening for it, when it isn't), the
 * browsers it's connected in, each with what it was last seen signed in to and Disconnect (which asks first), and
 * while none is connected the steps that set it up. Connect a browser opens ReelForge's page in your browser, where the
 * extension connects by itself; the dialog shows it as soon as it has. from "post": Post was pressed without it, so the
 * platforms' by-hand buttons are here too, and Post… once it's connected. */
export function ExtensionDialog({ from, close }: { from?: "post"; close: () => void }) {
  const s = useDash()
  const ext = useView<ExtensionView>(s.bridge, "extension")
  const connected = useView<{ platforms: PlatformInfo[] }>(s.bridge, "platforms")
  const { width } = useTerminalDimensions()
  const [leaving, setLeaving] = useState<string | null>(null) // the browser Disconnect was pressed for
  const [waiting, setWaiting] = useState(false) // the page that connects a browser was opened
  const [shown, setShown] = useState("") // what Show the extension folder did, said here
  // Each connected browser with when it connected. One that connects again keeps its place in the list and gets a new
  // time, so this, not how many there are, says that the page that opened has connected a browser.
  const pairs = (ext?.installs ?? []).map((b) => `${b.install} ${b.paired}`)
  const before = useRef(pairs)
  useEffect(() => {
    if (pairs.some((p) => !before.current.includes(p))) setWaiting(false) // it connected
    before.current = pairs
  }, [pairs.join("\n")])
  if (!ext) {
    return (
      <Modal name="extension" title="BROWSER EXTENSION" width={WIDTH}>
        <text fg={DASH.muted} flexShrink={0}>
          Looking for the extension…
        </text>
        <DialogButtons>
          <Button id="ext-close" label="Close" autoFocus onPress={close} />
        </DialogButtons>
      </Modal>
    )
  }
  const pair = async () => {
    setShown("")
    if (await s.bridge.ask<boolean>("pair_extension")) setWaiting(true)
  }
  // (true: this dialog says what happened. As a pop-up it sat on this dialog's buttons for fifteen seconds, Connect a
  // browser among them, which is the next thing to press.)
  const showFolder = async () => setShown((await s.bridge.ask<string | null>("extension_folder", true)) || "")
  const ps = connected?.platforms ?? s.init.platforms
  // Under a browser's name: what it was last seen signed in to, or the question once Disconnect is pressed for it.
  const under = (b: ExtensionInstall) => (leaving === b.install ? `Disconnect ${b.name}? It can't take posts from ReelForge until you connect it again.` : b.signed)
  // The rows of the part that scrolls, as it will wrap, so it is as tall as what's in it and no taller.
  const room = modalRoom(WIDTH, width) - 1
  const bodyRows =
    ext.lines.reduce((n, l) => n + wrappedRows(plain(l), room), 0) +
    (ext.lines.length ? 1 : 0) +
    2 +
    (ext.installs.length
      ? ext.installs.reduce((n, b) => n + 1 + wrappedRows(under(b), room - 2), 0) + wrappedRows(ext.signed_note, room)
      : ext.steps.reduce((n, step) => n + wrappedRows(step, room - STEP), 0))
  return (
    <Modal name="extension" title="BROWSER EXTENSION" width={WIDTH}>
      <box flexDirection="row" flexShrink={0}>
        <Wrapped segs={ext.line} />
      </box>
      {waiting ? (
        <box flexDirection="row" flexShrink={0}>
          {/* (said here and not in a pop-up, which sat on this dialog's buttons) */}
          <Wrapped segs={[["● Waiting for your browser: the page that opened connects it by itself. No extension in that browser yet? The page says how to add it.", DASH.warn]]} />
        </box>
      ) : shown ? (
        <box flexDirection="row" flexShrink={0}>
          <Wrapped segs={[[`The extension's folder: ${shown}`, DASH.muted]]} />
        </box>
      ) : null}
      {/* What it is, then its browsers or its steps: the part that scrolls, in a window too short for all of it, so
          the buttons under it always show. */}
      <Scroll maxHeight={bodyRows} flexShrink={1} scrollY>
        <box flexDirection="column" flexShrink={0} paddingRight={1}>
          {ext.lines.map((l, i) => (
            <box key={i} flexDirection="row" flexShrink={0} marginTop={i ? 0 : 1}>
              <Wrapped segs={l} />
            </box>
          ))}
          <text fg={DASH.text} attributes={TextAttributes.BOLD} marginTop={1} flexShrink={0}>
            {ext.installs.length ? "CONNECTED BROWSERS" : "SET IT UP, ONCE"}
          </text>
          {ext.installs.map((b, i) => (
            <box key={b.install} flexDirection="column" flexShrink={0}>
              <box flexDirection="row" height={1} flexShrink={0} gap={1}>
                {/* In parts, so that what doesn't fit beside the buttons is left out whole: the name always, then
                    that it needs an update, then its version and when it was seen. As one line it was cut against
                    the button ("needs a▐ Disconnect ▌") in a window 80 columns wide. */}
                <Fitted
                  flexGrow={1}
                  parts={[
                    [[["● ", b.old ? DASH.bad : b.on ? DASH.good : DASH.warn], [b.name, `bold ${DASH.text}`]], 0],
                    [[[`   ${b.version ? `v${b.version} · ` : ""}${b.seen === "never" ? "not heard from yet" : `seen ${b.seen}`}`, DASH.muted]], 2],
                    ...(b.old ? ([[[[" · needs an update", DASH.bad]], 1]] as Parts) : []),
                  ]}
                />
                {leaving === b.install ? (
                  // Keep it comes up where Disconnect was, as wide as it, and the red button beside it. In that place
                  // the red one took the second click of a double click on Disconnect, and the browser was
                  // disconnected before the question under its name could be read.
                  <>
                    <Button
                      id={`ext-drop-${i}`}
                      label="Disconnect"
                      variant="error"
                      onPress={() => {
                        setLeaving(null)
                        s.bridge.send("unpair_extension", b.install)
                      }}
                    />
                    <Button id={`ext-keep-${i}`} label="Keep it" minWidth={14} autoFocus onPress={() => setLeaving(null)} />
                  </>
                ) : (
                  <Button id={`ext-disconnect-${i}`} label="Disconnect" onPress={() => setLeaving(b.install)} />
                )}
              </box>
              <box flexDirection="row" paddingLeft={2} flexShrink={0}>
                <Wrapped segs={[[under(b), leaving === b.install ? DASH.warn : DASH.muted]]} />
              </box>
            </box>
          ))}
          {ext.installs.length ? (
            <box flexDirection="row" flexShrink={0}>
              <Wrapped segs={[[ext.signed_note, DASH.faint]]} />
            </box>
          ) : (
            ext.steps.map((step, i) => (
              <box key={i} flexDirection="row" flexShrink={0}>
                <text attributes={TextAttributes.BOLD} width={STEP} flexShrink={0}>
                  {`${i + 1}.`.padStart(3)}
                </text>
                <text wrapMode="word" flexGrow={1} flexShrink={1}>
                  {step}
                </text>
              </box>
            ))
          )}
        </box>
      </Scroll>
      {from === "post" && !reachable(ext) && ps.length ? (
        <box flexDirection="row" flexWrap="wrap" marginTop={1} flexShrink={0} columnGap={1}>
          <text fg={DASH.muted} wrapMode="none" flexShrink={0}>
            {"Or post by hand now: "}
          </text>
          {ps.map((p) => (
            <Button key={p.key} id={`ext-hand-${p.key}`} label={`${p.name} →`} accent={p.color} onPress={() => s.bridge.send("open_upload", p.key, s.caption, s.tags)} />
          ))}
        </box>
      ) : null}
      <box flexDirection="row" flexWrap="wrap" marginTop={2} flexShrink={0} columnGap={1} rowGap={1}>
        <Button id="ext-pair" label={ext.installs.length ? "Connect another browser" : "Connect a browser"} variant={reachable(ext) ? "default" : "primary"} autoFocus={!reachable(ext)} onPress={() => void pair()} />
        <Button id="ext-folder" label="Show the extension folder" onPress={() => void showFolder()} />
        <box flexGrow={1} />
        {from === "post" && reachable(ext) ? <Button id="ext-post" label="Post…" variant="primary" autoFocus onPress={() => void s.openPost()} /> : null}
        <Button id="ext-close" label="Close" autoFocus={reachable(ext) && from !== "post"} onPress={close} />
      </box>
    </Modal>
  )
}

/** The extension's line on the Accounts tab: how it is, and the button that opens its dialog. It posts from your
 * everyday browser, so it's apart from the platforms' panels under it, which read your stats. */
export function ExtensionStrip() {
  const s = useDash()
  const ext = useView<ExtensionView>(s.bridge, "extension")
  if (!ext) return null
  return (
    <Panel flexShrink={0} paddingTop={0}>
      <box flexDirection="row" height={1} flexShrink={0} gap={2}>
        <text fg={DASH.text} attributes={TextAttributes.BOLD} wrapMode="none" flexShrink={0}>
          BROWSER EXTENSION
        </text>
        <Fitted parts={[...foldable(ext.line), [[["   for Post… on the Publish tab: optional", DASH.faint]], 2]]} flexGrow={1} />
        <Button id="ext-open" label={ext.installs.length ? "Browsers…" : "Set up…"} onPress={() => s.set({ dialog: { kind: "extension" } })} />
      </box>
    </Panel>
  )
}
