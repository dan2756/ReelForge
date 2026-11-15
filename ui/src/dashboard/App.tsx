// ReelForge's window: the header (the anvil, the profile button, seven tabs in the order picked on the Settings tab
// with a marker that slides to the open one, then at the right the green Publish button, Refresh / Help / Quit with
// when the stats were read under Refresh, and the bell; a window too narrow for all of that in one row puts the
// profile button and Refresh / Help / Quit over the tabs), the tab, the status bar at the foot (the DEMO or TEST badge,
// a platform whose last read failed, the keys), the notifications drawer sliding in from the right, dialogs, and
// pop-ups. Keys: 1-7 open the tabs in the order they're shown, u Publish, r / R refresh, n the notifications, p the
// profiles, Esc closes the drawer or leaves a text field, Tab moves between buttons and fields, Ctrl+Q quits.

import { TextAttributes, type KeyEvent, type Renderable } from "@opentui/core"
import { useKeyboard, useRenderer, useTerminalDimensions } from "@opentui/react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useView, type Bridge } from "../bridge"
import { FocusContext, FocusManager, ThemeContext } from "../lib/focus"
import { fit } from "../lib/fit"
import { cellLen, segsLen, type Parts, type Segs } from "../lib/styled"
import { DASH, MARK, mix } from "../theme"
import { Cells } from "../ui/Cells"
import { bell, BELL_WIDTH, logoCells, shade, widthOf } from "../ui/Graphics"
import { ActionContext, Line } from "../ui/Line"
import { useProgress, useTween } from "../ui/Motion"
import { ToastStack, toasts } from "../ui/Panels"
import { Accounts } from "./Accounts"
import { Compare } from "./Compare"
import { Dialogs } from "./Dialogs"
import { Drawer } from "./Drawer"
import { Overview } from "./Overview"
import { ProfileButton } from "./Profiles"
import { Publish } from "./Publish"
import { Settings } from "./Settings"
import { Stats } from "./Stats"
import { DashContext, DashState, PUBLISH_KEY, TAB_NAMES, tabOrder, useDash, type Nav } from "./state"
import type { Init, NotesView, ProfileView, UpdateView } from "./types"
import { Updates } from "./Updates"
import { Videos } from "./Videos"

export function DashboardApp({ bridge, init, quit, nav }: { bridge: Bridge; init: Init; quit: (code?: number, result?: object) => void; nav?: Nav }) {
  const state = useMemo(() => new DashState(bridge, init, quit, nav), [bridge, init, quit, nav])
  const focus = useMemo(() => new FocusManager(), [])
  useEffect(() => {
    const offs = [
      bridge.on("toast", (t) => toasts.add(t)),
      bridge.on("tab", (d) => state.set({ tab: d.tab })),
      bridge.on("pick", (d) => state.pick(d.id)),
      bridge.on("focus", (d) => setTimeout(() => focus.show(d.id), 50)),
      bridge.on("exit", (d) => quit(d.code ?? 0, d.result ?? undefined)), // result: {restart: true} from Restart
      // A picked video's caption and hashtags, for the Publish form (whichever tab is open when it's picked).
      bridge.on("form", (d) => state.set({ caption: d.caption ?? "", tags: d.tags ?? "" })),
      // Another ReelForge window moved a tab (the Settings tab's Tab order): this one follows.
      bridge.on("order", (d) => state.set({ order: tabOrder(d.order) })),
    ]
    // Now that every handler is there (this effect runs after the tabs' own): the Python side starts sending. Its
    // first words include the picked video's caption and the pop-ups it has waiting, which nothing keeps for later.
    bridge.send("ready")
    if (state.drawer) bridge.send("drawer_opened")
    return () => offs.forEach((off) => off())
  }, [bridge, state, focus, quit])
  return (
    <ThemeContext.Provider value={DASH}>
      <FocusContext.Provider value={focus}>
        <DashContext.Provider value={state}>
          <ActionContext.Provider value={state.run}>
            <Window focus={focus} />
          </ActionContext.Provider>
        </DashContext.Provider>
      </FocusContext.Provider>
    </ThemeContext.Provider>
  )
}

function Window({ focus }: { focus: FocusManager }) {
  const s = useDash()
  const renderer = useRenderer()
  // The copy buttons' fallback when the system clipboard can't be reached: the terminal's own (OSC 52).
  useEffect(() => s.bridge.on("clipboard", (d) => void renderer.copyToClipboardOSC52(d.text)), [s, renderer])
  useKeyboard((key: KeyEvent) => {
    if (key.ctrl && (key.name === "q" || key.name === "c")) return s.quit(0)
    if (key.name === "escape") {
      if (s.dialog) return s.set({ dialog: null })
      if (focus.typing) return focus.blur()
      if (s.drawer) return s.toggleDrawer(false)
      return
    }
    if (focus.handleKey(key)) return
    if (s.dialog || focus.typing || key.ctrl || key.meta) return
    const tab = /^[1-9]$/.test(key.name) ? s.order[Number(key.name) - 1] : key.name === PUBLISH_KEY ? "publish" : null
    if (tab) return s.setTab(tab)
    if (key.name === "r") return s.bridge.send("refresh", key.shift)
    if (key.name === "n") return s.toggleDrawer() // the bell, for the keyboard
    if (key.name === "p") return s.openProfiles()
    if (key.name === "?" || key.sequence === "?") return s.set({ dialog: { kind: "help" } })
  })
  return (
    <box flexDirection="column" width="100%" height="100%" backgroundColor={DASH.bg}>
      <Header />
      <TabContent />
      <StatusBar />
      <Drawer />
      <Dialogs />
      <ToastStack />
    </box>
  )
}

/** The open tab. A tab you switch to slides in a few columns from the side you moved towards (Publish, from the right,
 * where its button is) and comes up out of the window's colour. */
function TabContent() {
  const s = useDash()
  const at = s.order.indexOf(s.tab)
  const i = at < 0 ? s.order.length : at
  const last = useRef(i)
  const dir = i >= last.current ? 1 : -1
  useEffect(() => {
    last.current = i
  }, [i])
  const p = useProgress(s.tab, { ms: 220, ease: "outQuad" })
  const shift = Math.round((1 - p) * 3) * dir
  const veil = Math.round((1 - p) * 0xd0)
  return (
    <box flexDirection="column" flexGrow={1} paddingLeft={1 + Math.max(shift, 0)} paddingRight={1 + Math.max(-shift, 0)}>
      {s.tab === "overview" && <Overview />}
      {s.tab === "videos" && <Videos />}
      {s.tab === "compare" && <Compare />}
      {s.tab === "publish" && <Publish />}
      {s.tab === "accounts" && <Accounts />}
      {s.tab === "updates" && <Updates />}
      {s.tab === "settings" && <Settings />}
      {s.tab === "totals" && <Stats />}
      {veil > 8 ? <box position="absolute" left={0} top={0} width="100%" height="100%" zIndex={20} backgroundColor={DASH.bg + veil.toString(16).padStart(2, "0")} /> : null}
    </box>
  )
}

// ---------------------------------------------------------------- the header

const ANVIL_STOPS = ["#ffffff", "#d3d8de", "#8e97a3"]

/** How the header fits: the anvil, the gap between tabs, Refresh / Help / Quit as words or signs, the room for the
 * profile's name, and tight, the narrow windows' header (the signs a space apart, and less room round the tabs and
 * inside the Publish button's box). stacked: a window too narrow for one row, where the profile button and the signs
 * sit over the tabs; tabsRoom is then the columns the tabs have. */
interface Fit {
  anvil: boolean
  gap: number
  words: boolean
  tight: boolean
  nameRoom: number
  stacked?: boolean
  tabsRoom?: number
}

/** The Publish button's label: with its arrow while Refresh, Help and Quit beside it are words, without once they're
 * signs. */
const publishLabel = (words: boolean) => (words ? "↑ Publish" : "Publish")
/** Columns the Publish button takes: the gap it keeps from the tabs, its label (a space either side unless tight), its
 * box, and the gap after it. */
const publishW = (words: boolean, tight: boolean) =>
  (tight ? 1 : 2) + cellLen(publishLabel(words)) + (tight ? 2 : 4) + (words ? 4 : tight ? 2 : 3)

/** Refresh, Help and Quit: as words, or as signs (a space apart when tight). */
function actions(words: boolean, tight: boolean): Segs {
  const gap = words ? "    " : tight ? " " : "   "
  const [refresh, help, quit] = words ? ["↻ Refresh", "? Help", "Quit"] : ["↻", "?", "×"]
  return [
    [refresh, DASH.muted, ["refresh"]],
    [gap, ""],
    [help, DASH.muted, ["help"]],
    [gap, ""],
    [quit, DASH.muted, ["quit"]],
  ]
}

/** How the header fits the window's width (dot: the columns of the profile button's red dot, when it has one): the gap between tabs,
 * the actions as words or signs, the profile's name cut short, the anvil, and then the room round everything, given up
 * in that order. Narrower still (under 96 columns with seven tabs), the profile button and the signs go over the tabs,
 * which then have the row to themselves beside the Publish button and the bell: nothing is ever pushed off the right
 * edge. The Publish button always stays. */
export function headerFit(width: number, name: string, anvilW: number, tabsW: (gap: number) => number, dot = 0): Fit {
  const bellW = BELL_WIDTH + 4
  // the padding, the anvil, the tabs' margin, the bell and the margin before it; then the actions and Publish
  const fixed = (anvil: boolean, tight = false) => 1 + (anvil ? anvilW + 2 : 0) + (tight ? 2 : 3) + bellW + 2
  const rest = (words: boolean, tight = false) => segsLen(actions(words, tight)) + publishW(words, tight)
  const full = width
  width -= dot // (the red dot beside the profile button, on the same row as everything else)
  for (const anvil of [true, false])
    for (const [gap, words] of [
      [4, true],
      [3, false],
      [2, false],
    ] as const) {
      const room = width - fixed(anvil) - tabsW(gap) - rest(words)
      if (room >= Math.min(cellLen(name), 12) + 6) return { anvil, gap, words, tight: false, nameRoom: room - 6 }
    }
  // A narrow window: the tabs closer together and the profile's name cut to a few letters, then everything tight.
  for (const tight of [false, true])
    for (const gap of [2, 1]) {
      const room = width - fixed(false, tight) - tabsW(gap) - rest(false, tight)
      if (room >= Math.min(cellLen(name), 4) + 6) return { anvil: false, gap, words: false, tight, nameRoom: room - 6 }
    }
  // Stacked: the tabs' row is the window less its padding, the Publish button and the bell; the row over it has the
  // profile button at its left and the signs (with when the stats were read, while there's room) at its right.
  const tabsRoom = Math.max(full - 1 - (publishW(false, true) - 1) - bellW, 1) // the button a column closer to the bell
  const gap = [3, 2, 1].find((g) => tabsW(g) <= tabsRoom) ?? 1
  const signs = segsLen(actions(false, true))
  return { anvil: false, gap, words: false, tight: true, stacked: true, tabsRoom, nameRoom: Math.max(tabsRoom - dot - signs - 2 - 6, 1) }
}

function Header() {
  const s = useDash()
  const { width } = useTerminalDimensions()
  const anvil = useMemo(() => shade(logoCells(s.init.anvil), ANVIL_STOPS), [s.init.anvil])
  const update = useView<UpdateView>(s.bridge, "update")
  const labels = s.order.map((t) => TAB_NAMES[t] + (t === "updates" && update?.newer ? " ●" : ""))
  const tabsW = (gap: number) => labels.reduce((n, l) => n + cellLen(l), 0) + gap * (labels.length - 1)
  const profile = useView<ProfileView>(s.bridge, "profile") ?? s.init.profile
  const fit = headerFit(width, profile.name, widthOf(anvil), tabsW, profile.elsewhere > 0 ? 2 : 0)
  const buttons = actions(fit.words, fit.tight)
  return (
    <box flexDirection="column" flexShrink={0}>
      {fit.stacked ? (
        <box flexDirection="row" height={3} flexShrink={0} paddingLeft={1}>
          <box flexDirection="column" flexGrow={1} flexShrink={1} minWidth={0} overflow="hidden">
            <box flexDirection="row" height={1} flexShrink={0}>
              <ProfileButton room={fit.nameRoom} height={1} />
              <box flexGrow={1} />
              <Line segs={buttons} />
              <Refreshed room={fit.tabsRoom! - segsLen(buttons) - Math.min(cellLen(profile.name), fit.nameRoom) - 6 - (profile.elsewhere > 0 ? 2 : 0) - 2} beside />
            </box>
            <Tabs labels={labels} gap={fit.gap} tight room={fit.tabsRoom} />
          </box>
          <PublishButton words={false} tight close />
          <Bell />
        </box>
      ) : (
        <box flexDirection="row" height={3} flexShrink={0} paddingLeft={1}>
          {fit.anvil && anvil.length ? <Cells grid={anvil} marginRight={2} /> : null}
          <ProfileButton room={fit.nameRoom} />
          <Tabs labels={labels} gap={fit.gap} tight={fit.tight} />
          <box flexGrow={1} />
          <PublishButton words={fit.words} tight={fit.tight} />
          <box flexDirection="column" height={3} flexShrink={0} marginRight={2}>
            <box height={1} />
            <Line segs={buttons} />
            <Refreshed words={fit.words} room={segsLen(buttons) + 3} />
          </box>
          <Bell />
        </box>
      )}
      <text fg={DASH.border} wrapMode="none" height={1} flexShrink={0}>
        {"─".repeat(Math.max(width, 1))}
      </text>
    </box>
  )
}

/** Under ↻ Refresh: when the stats were last read ("updated 3m ago", or "3m ago" under the signs), or amber while a
 * read is under way, in this window or another (which platforms, while there's room). It never makes the header wider:
 * room is the columns it has before the bell. beside: after the signs on their own row (a narrow window), while it
 * fits there. */
function Refreshed({ words = false, room, beside }: { words?: boolean; room: number; beside?: boolean }) {
  const s = useDash()
  const v = useView<{ ago: string; reading: string }>(s.bridge, "refreshed")
  if (!v) return null
  const text = v.reading ? (!words ? "reading…" : cellLen(v.reading) <= room ? v.reading : "refreshing…") : words ? `updated ${v.ago}` : v.ago
  if (beside)
    return cellLen(text) + 2 <= room ? (
      <text fg={v.reading ? MARK : DASH.faint} wrapMode="none" selectable={false} flexShrink={0} marginLeft={2}>
        {text}
      </text>
    ) : null
  return (
    <box position="absolute" left={0} top={2} width={cellLen(text)} height={1}>
      <text fg={v.reading ? MARK : DASH.faint} wrapMode="none" selectable={false}>
        {text}
      </text>
    </box>
  )
}

/** The tabs, in the order picked on the Settings tab: the open one white and bold with a short bar under it that slides
 * to whichever you pick (and off the right-hand end, towards the Publish button, while Publish is open); a red dot on
 * Updates when a new version is out. room: the columns they have on a row of their own (a narrow window). Where even
 * that is too few for every tab, they slide along so the open one always shows, with ‹ or › at the end more are past
 * (a click on one opens the tab before or after). */
function Tabs({ labels, gap, tight, room }: { labels: string[]; gap: number; tight: boolean; room?: number }) {
  const s = useDash()
  const [hover, setHover] = useState<number | null>(null)
  const starts: number[] = []
  let x = 0
  for (const l of labels) {
    starts.push(x)
    x += cellLen(l) + gap
  }
  const end = Math.max(x - gap, 0)
  const i = s.order.indexOf(s.tab) // -1 while Publish is open: no tab is lit
  const at = useTween(i >= 0 ? starts[i]! : end, { ms: 220, ease: "outQuad" })
  const wide = useTween(i >= 0 ? cellLen(TAB_NAMES[s.order[i]!]) : 0, { ms: 220, ease: "outQuad" })
  const from = Math.round(at)
  const bar = Math.min(Math.round(at + wide), end) - from
  if (room !== undefined) {
    const [first, last] = tabWindow(labels.map(cellLen), gap, i, room)
    const before = first > 0 ? MORE : 0 // the columns ‹ takes
    const step = (to: number) => s.setTab(s.order[to]!)
    const more = (sign: string, to: number) => (
      <text fg={DASH.muted} wrapMode="none" selectable={false} flexShrink={0} onMouseDown={() => step(to)}>
        {sign}
      </text>
    )
    return (
      <box flexDirection="column" height={2} flexShrink={0} width={room} overflow="hidden">
        <box flexDirection="row" height={1} flexShrink={0}>
          {first > 0 ? more("‹ ", first - 1) : null}
          {names(first, last)}
          {last < labels.length - 1 ? more(" ›", last + 1) : null}
        </box>
        {marker(before - starts[first]!, room)}
      </box>
    )
  }
  return (
    <box flexDirection="column" flexShrink={0} marginLeft={tight ? 2 : 3}>
      <box height={1} />
      {names(0, labels.length - 1)}
      {marker(0, end)}
    </box>
  )

  /** The tabs from one to another, as they sit along the top. */
  function names(first: number, last: number) {
    return (
      <box flexDirection="row" height={1} flexShrink={0}>
        {s.order.map((tab, k) => {
          if (k < first || k > last) return null
          const on = k === i
          const name = TAB_NAMES[tab]
          return (
            <text
              key={tab}
              marginRight={k < last ? gap : 0}
              fg={on ? DASH.text : k === hover ? mix(DASH.muted, DASH.text, 0.6) : DASH.muted}
              attributes={on ? TextAttributes.BOLD : undefined}
              wrapMode="none"
              onMouseOver={() => setHover(k)}
              onMouseOut={() => setHover((h) => (h === k ? null : h))}
              onMouseDown={() => s.setTab(tab)}
            >
              {name}
              {labels[k] !== name ? <span fg={DASH.bad}> ●</span> : ""}
            </text>
          )
        })}
      </box>
    )
  }

  /** The bar under the open tab, moved `by` columns (the tabs shown may start past the first), inside `width`. */
  function marker(by: number, width: number) {
    const left = Math.min(Math.max(from + by, 0), width)
    const right = Math.min(Math.max(from + by + Math.max(bar, i >= 0 ? 1 : 0), 0), width)
    return (
      <text wrapMode="none" height={1} flexShrink={0}>
        <span>{" ".repeat(left)}</span>
        <span fg={DASH.text}>{"▔".repeat(right - left)}</span>
      </text>
    )
  }
}

const MORE = 2 // columns ‹ or › takes at an end of the tabs, with its space

/** Which tabs show in `room` columns when they don't all fit (a narrow window): whole names only, the open one always
 * among them, as many as fit from the first one on, with ‹ or › (MORE columns each) at an end more are past.
 * widths: each tab's columns; open: the open tab's place, or -1. Returns the first and the last one shown. */
export function tabWindow(widths: number[], gap: number, open: number, room: number): [number, number] {
  const n = widths.length
  const span = (a: number, b: number) => widths.slice(a, b + 1).reduce((sum, w) => sum + w, 0) + gap * (b - a)
  const fits = (a: number, b: number) => span(a, b) + (a > 0 ? MORE : 0) + (b < n - 1 ? MORE : 0) <= room
  let first = 0
  while (first < open && !fits(first, open)) first++
  let last = Math.max(open, first)
  while (last < n - 1 && fits(first, last + 1)) last++
  return [first, last]
}

/** Publish, at the right of the header: a green button in a box (u opens it too). While Publish is open it's filled
 * green, the way the open tab is lit along the top. */
function PublishButton({ words, tight, close }: { words: boolean; tight: boolean; close?: boolean }) {
  const s = useDash()
  const [hover, setHover] = useState(false)
  const open = s.tab === "publish"
  const green = hover ? mix(DASH.good, "#ffffff", 0.3) : DASH.good
  return (
    <box
      border
      borderStyle="rounded"
      borderColor={green}
      height={3}
      flexShrink={0}
      marginLeft={tight ? 1 : 2}
      marginRight={words ? 4 : close ? 1 : tight ? 2 : 3}
      onMouseOver={() => setHover(true)}
      onMouseOut={() => setHover(false)}
      onMouseDown={(e) => {
        if (e.button !== 0) return
        e.stopPropagation()
        s.setTab("publish")
      }}
    >
      <text wrapMode="none" selectable={false}>
        <span fg={open ? DASH.bg : green} bg={open ? green : hover ? mix(DASH.bg, DASH.good, 0.14) : undefined} attributes={TextAttributes.BOLD}>
          {tight ? publishLabel(words) : ` ${publishLabel(words)} `}
        </span>
      </text>
    </box>
  )
}

/** The bell, with how many notifications are unread on its shoulder; it opens and closes the drawer. */
function Bell() {
  const s = useDash()
  const notes = useView<NotesView>(s.bridge, "notes")
  const [hover, setHover] = useState(false)
  const unread = notes?.unread ?? 0
  const art = useMemo(() => bell(s.init.quadrants, s.drawer || hover ? "#ffffff" : mix(DASH.text, DASH.muted, 0.35)), [s.init.quadrants, s.drawer, hover])
  const badge = unread > 9 ? "9+" : String(unread)
  return (
    <box
      flexDirection="row"
      height={3}
      flexShrink={0}
      paddingLeft={1}
      paddingRight={1}
      backgroundColor={s.drawer ? DASH.surface : hover ? mix(DASH.bg, "#ffffff", 0.04) : undefined}
      onMouseOver={() => setHover(true)}
      onMouseOut={() => setHover(false)}
      onMouseDown={(e) => {
        e.stopPropagation()
        s.toggleDrawer()
      }}
    >
      <Cells grid={art} />
      <box width={unread ? cellLen(badge) + 2 : 2} flexShrink={0} marginLeft={-1}>
        {unread ? (
          <text wrapMode="none">
            <span fg={DASH.bad}>▐</span>
            <span fg="#ffffff" bg={DASH.bad} attributes={TextAttributes.BOLD}>
              {badge}
            </span>
            <span fg={DASH.bad}>▌</span>
          </text>
        ) : null}
      </box>
    </box>
  )
}

// ---------------------------------------------------------------- the status bar

/** The keys at the foot of the window, with as many tabs as there are along the top, each with how soon it goes when
 * the window is too narrow for them all (the highest first: the help, which lists every key, stays longest). */
const keysFor = (tabs: number): [string, string, number][] => [
  [`1-${tabs}`, "tabs", 1],
  [PUBLISH_KEY, "publish", 2],
  ["r", "refresh", 3],
  ["n", "notifications", 5],
  ["p", "profiles", 4],
  ["?", "help", 0],
]

/** The foot of the window: the Python side's header line (the DEMO or TEST badge, a nudge to connect an account
 * while none is, and a platform whose last read failed: click it for Accounts), and at the right as many of the keys
 * as there's room for. When the stats were read is under Refresh, at the top (Refreshed). */
function StatusBar() {
  const s = useDash()
  const header = useView<{ line: Segs; short?: Segs }>(s.bridge, "header")
  const [width, setWidth] = useState(0)
  const keys: Parts = keysFor(s.order.length).map(([k, what, rank]) => [[["   ", ""], [k, DASH.muted], [` ${what}`, DASH.faint]], rank])
  const full = header?.line ?? []
  const short = header?.short ?? full
  const room = width - 4 // its padding
  const line = segsLen(full) <= room ? full : short
  const left = room - segsLen(line) - 1 // what the keys have, a column clear of the line
  const hints = left >= Math.min(...keys.map(([k]) => segsLen(k))) ? fit(keys, left) : [] // (never half a hint)
  return (
    <box
      flexDirection="row"
      height={1}
      flexShrink={0}
      paddingLeft={2}
      paddingRight={2}
      backgroundColor={mix(DASH.bg, DASH.surface, 0.7)}
      onSizeChange={function (this: Renderable) {
        setWidth(this.width)
      }}
    >
      <Line segs={line} />
      <box flexGrow={1} />
      <Line segs={hints} />
    </box>
  )
}
