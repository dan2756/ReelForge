// What the Python side (dashboard/service.py) sends the window: the start-up facts, then one view per piece of the
// window, each sent again whenever it changes.

import type { Mark, Series, Slot } from "../lib/chart"
import type { Option, TableData } from "../ui/Lists"
import type { Parts, Segs } from "../lib/styled"

export interface PlatformInfo {
  key: string
  name: string
  color: string
  short: string
}

export interface Init {
  platforms: PlatformInfo[]
  tab: string
  order: string[] // the tabs along the top, left to right (dashboard/tabs.py)
  default_order: string[] // ... until you pick another order on the Settings tab
  drawer: boolean
  test: boolean
  demo: boolean
  screenshots: boolean
  title: string
  files: string
  files_window: string
  bin: string
  anvil: [string, string | null, string | null][][]
  quadrants: boolean
  help: ([string, string] | [string, string, string])[] // [title, what it says, and for a tab its id]
  keys: [string, string][]
  version: string
  max_tags: number
  first_tag: string
  default_tags: string
  caption_placeholder: string
  tags_placeholder: string
  copies: Record<string, [string, string][]> // each platform's copy buttons (id, label), top to bottom
  badges_how: string
  ranges: Record<string, string>
  videos_dir: string
  profile: ProfileView
}

/** The profile this window shows (one profile for each channel: studio/profiles.py), for the button at the top left.
 * elsewhere: the other profiles' unread notifications (a red dot on the button). */
export interface ProfileView {
  key: string
  name: string
  main: boolean
  several: boolean
  elsewhere: number
}

/** One profile in the profile button's menu. */
export interface ProfileRow {
  key: string
  number: number
  name: string
  current: boolean
  main: boolean
  unread: number
  where: string // its folder (profiles/<name>), or "" for the ReelForge folder's own
  why: string // why this window can't delete it (its own, the first one, open in another window), or ""
  platforms: { key: string; short: string; name: string; color: string }[]
}

export interface ProfilesMenu {
  rows: ProfileRow[]
  current: string
  demo: boolean
  onboarded: boolean
}

export interface CardView {
  id: string
  color: string
  titles: string[]
  gains: number[]
  captions: string[]
  values?: Record<string, number | null>
  changes?: Record<string, number | null>
  message?: Segs
}

export interface Variant {
  step_h: number
  n: number
  unit: string
  slots: Slot[]
  series: Series[]
  marks: Mark[]
}

export interface MetricView {
  empty?: string | null
  variants?: Variant[]
  span?: string
  hourly?: boolean
  summary?: { latest: number | null; gained: number | null; pace: number | null; change: number | null }
}

export interface TrendsView {
  range: string
  ranges: Segs
  legend: Segs
  legend_short: Segs
  metrics: Record<string, MetricView>
}

/** A platform at the top of the Stats tab: ticked (counted in the totals) or left out; has: it has stats to count. */
export interface TotalsToggle {
  key: string
  name: string
  short: string
  color: string
  counted: boolean
  has: boolean
}

/** A bucket on a Stats chart, for the hover read-out: the total at its end (or now) and whether that's an estimate,
 * what it gained in the bucket and how much of the bucket is drawn as an estimate (share: 0 to 1), and each counted
 * platform's reading then (est: the ones estimated then, in a gap in their readings). */
export interface TotalsHover {
  total: number
  gain: number | null
  each: Record<string, number | null>
  est?: string[]
  estimated?: boolean
  share?: number
}

/** A platform in a Stats chart's breakdown: its number now, its gain over the chart's span, its share of the total,
 * what it gained in each bucket, and when it was last read once that's a while ago. */
export interface TotalsRow {
  key: string
  name: string
  short: string
  color: string
  counted: boolean
  left_out: boolean
  value: number | null
  gained: number | null
  share: number | null
  gains: (number | null)[]
  stale: string | null // "last read 34h ago": a platform whose reads stopped a while ago
}

/** One of the Stats tab's charts (dashboard/stats_tab.py totals_metric): views, followers or likes added up. */
export interface TotalsMetric {
  name: string
  empty?: string | null
  n?: number
  step_h?: number
  slots?: Slot[]
  series?: Series[]
  hover?: (TotalsHover | null)[]
  marks?: Mark[]
  title?: Parts
  note?: Segs | null
  rows?: TotalsRow[]
  total?: { value: number | null; gained: number | null }
}

export interface TotalsView {
  range: string
  ranges: Segs
  toggles: TotalsToggle[]
  metrics: Record<string, TotalsMetric>
}

export type TableView = TableData & { title?: Parts; hint?: Parts; select?: string | null; focus?: boolean; heading?: Segs }

export interface DetailView {
  key: string | null
  title: Segs
  series?: Series[]
  span?: number
  empty?: string | null
}

export interface CompareChart {
  key: string
  heading: Segs
  series?: Series[]
  span?: number
  empty?: string | null
}

export interface CompareView {
  heading: Segs
  bars: [string, [string, number, string, string][]][]
  charts: CompareChart[]
  table: TableData
}

export interface FilesView {
  heading: Segs
  items: Option[]
  empty: Segs[] | null
  selected: string | null
}

export interface PreviewView {
  caption: Segs
  tags: Segs
  rows: { name: string; color: string; text: string }[]
}

/** Where the picked video's post stands on one platform (dashboard/posting_tab.py): none (nothing yet), spotted (a
 * stats read found the post) or its job's state, in a line of words, with what can be done about it. */
export interface PostingRow {
  key: string
  name: string
  color: string
  state: string
  line: Segs
  busy: boolean // ReelForge or the extension is at work on it
  url: string | null
  job: string | null
  actions: string[] // retry, cancel, mark_posted, not_posted, open_post, post_again
}

/** off: no browser is connected; waiting: one is, and hasn't been heard from lately (it wakes when a post starts); on;
 * old: it and ReelForge speak different versions of the link between them. */
export type ExtensionState = "off" | "waiting" | "on" | "old"

/** Posting the picked video (the "posting" view): its review, a row for each connected platform, and whether Post
 * can be pressed. setup: only the extension is in the way, so Post explains how to set it up. */
export interface PostingView {
  video: { path: string; name: string } | null
  review: { state: string; line: Segs }
  extension: { state: ExtensionState; line: Segs }
  can_post: boolean
  why: string
  setup: boolean
  rows: PostingRow[]
  labels: Record<string, string> // the words for each action's button
}

/** A browser the extension is connected in. signed: what it was last seen signed in to, in the extension's words about
 * its own browser (never the accounts connected in ReelForge). */
export interface ExtensionInstall {
  install: string
  name: string
  version: string
  paired: string // when it connected: new when the same browser connects again
  seen: string
  on: boolean
  old: boolean
  signed: string
}

/** The browser extension (the "extension" view). */
export interface ExtensionView {
  state: ExtensionState
  line: Segs
  lines: Segs[] // what it is, why this window isn't listening for it, how far it has been tested
  installs: ExtensionInstall[]
  port: number | null // null: this window isn't listening for it
  folder: string
  folder_there: boolean
  hint_dismissed: boolean
  hint: Parts // the Publish tab's one line about it while it isn't set up
  steps: string[] // setting it up, once
  signed_note: string // whose word "signed in there" is, under the connected browsers
  problem: string // why this window can't hand a post to it ("" when it can)
}

/** One platform in the question before a post (post_info): the exact text for each of its boxes, the account, and
 * what stops it if anything does. again: it is up there already, and can be posted again when ticked. press: the
 * extension may press Post there, once "stop before posting" is switched off here. It then still presses only where
 * Press Post for me is on in its own settings and you said yes for that post in the browser. */
export interface PostInfoPlatform {
  key: string
  name: string
  color: string
  handle: string
  fields: { label: string; text: string }[]
  blocked: { reason: string; message: string; url?: string } | null
  again: boolean
  press: boolean
}

export interface PostInfo {
  video: { path: string; name: string; size: string }
  review: { state: string; line: string }
  extension: { state: ExtensionState; line: Segs }
  platforms: PostInfoPlatform[]
  can_post: boolean
  why: string
  note: string // how far posting with the extension has been tested
  how: string // what happens next, in a sentence
  how_press: string // ... with "stop before posting" switched off: what else the extension wants before it presses
}

/** One platform on the Accounts tab: on (connected), login (its handle is in, waiting for the one log-in) or off
 * (not connected: a box for its @handle, Connect and Sign up). */
export interface AccountPanelView {
  key: string
  name: string
  color: string
  state: "on" | "login" | "off"
  status: Segs // the first line, with Disconnect at its right end while there's room
  status_short: Segs | null // ... for a narrow panel
  disconnect: Segs | null
  body: Segs
  note: Segs
  signup?: boolean
  logs_in?: boolean
  login_label?: string
  logs_out?: boolean // ReelForge's own browser is logged in to it: Log out
  old_handles?: string[] // it also holds stats read under these handles, from before the handle changed
  profile_disabled?: boolean
  refresh_disabled?: boolean
  show_log?: boolean
  chart?: null | {
    heading: Segs
    n: number
    step_h: number
    slots: Slot[]
    values: (number | null)[]
    guessed?: boolean[] // buckets that are an estimate (a gap in the readings), hatched
    series: Series[]
  }
}

export interface AccountsView {
  panels: AccountPanelView[]
  hint: Parts | null // "Connect the platforms you post to ..." while one isn't connected
}

export interface NoteView {
  id: number
  mark: Segs
  title: Segs
  body: string
  ago: string
  read: boolean
}

export interface NotesView {
  unread: number
  head: Segs
  close: Segs
  tools: Segs
  items: NoteView[]
  empty: string
}

export interface UpdateView {
  newer: boolean
  status: Segs
  apply: { show: boolean; disabled: boolean; label: string }
  check_disabled: boolean
  notes: string
  how: Segs
  updating: boolean
}

export interface UpdateStep {
  step?: Segs
  status?: Segs
  progress?: { done: number; total: number | null } | null
  done?: boolean // the update is in; ReelForge restarts
  failed?: boolean // it didn't finish, and the version you have keeps working
}
