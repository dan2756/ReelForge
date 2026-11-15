// The Accounts tab: connect the platforms you post to (ReelForge shows only the connected ones everywhere else), and
// for each connected one where its stats come from, how fresh they are, its log-in / log-out / profile / refresh
// buttons, and its followers over the time ReelForge has been reading them. Above them, one line for the browser
// extension (Posting.tsx): it posts from your everyday browser, which is apart from these accounts.

import { TextAttributes, type Renderable } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/react"
import { useCallback, useState } from "react"
import { useView } from "../bridge"
import { clockLabels, lines as drawLines, MARK, type Drawn } from "../lib/chart"
import { fmtNum } from "../lib/format"
import { lines, segsLen, truncate, type Segs } from "../lib/styled"
import { DASH } from "../theme"
import { Button } from "../ui/Button"
import { Chart } from "../ui/Chart"
import { TextField } from "../ui/Fields"
import { Fitted, Line } from "../ui/Line"
import { Panel, Reading, Scroll, Wrapped } from "../ui/Panels"
import { ExtensionStrip } from "./Posting"
import { useDash } from "./state"
import type { AccountPanelView, AccountsView, ExtensionView } from "./types"

/** Rows a row of panels needs before it would squeeze what's in it: the name, the status, the buttons, the note and
 * some of the chart. */
const ROW_ROWS = 20

export function Accounts() {
  const s = useDash()
  const accounts = useView<AccountsView>(s.bridge, "accounts")
  // The extension's line, in your own ReelForge once it's known how the extension is (the demo doesn't use it).
  const strip = Boolean(useView<ExtensionView>(s.bridge, "extension")) && !s.init.demo
  const panels = accounts?.panels ?? []
  const rows = [panels.slice(0, 2), panels.slice(2, 4)].filter((r) => r.length)
  // A window too short for the panels to share its height without squeezing them scrolls instead.
  const { height } = useTerminalDimensions()
  const room = height - 6 - (accounts?.hint ? 2 : 0) - (strip ? 2 : 0) // the header, the status bar, the space above
  const tight = (room - (rows.length - 1)) / Math.max(rows.length, 1) < ROW_ROWS
  const grid = rows.map((row, i) => (
    <box key={i} flexDirection="row" gap={1} {...(tight ? { height: ROW_ROWS + 6, flexShrink: 0 } : { flexGrow: 1, flexBasis: 0 })}>
      {row.map((p) => (
        <Account key={p.key} panel={p} picked={s.picked === `acct-${p.key}`} />
      ))}
      {row.length === 1 ? <box flexGrow={1} flexBasis={0} /> : null}
    </box>
  ))
  if (!accounts) return <Reading what="your accounts" />
  return (
    <box flexDirection="column" flexGrow={1} paddingTop={1} gap={1}>
      {accounts.hint ? (
        <box paddingLeft={1} flexShrink={0}>
          <Fitted parts={accounts.hint} />
        </box>
      ) : null}
      {strip ? <ExtensionStrip /> : null}
      {tight ? (
        <Scroll flexGrow={1} scrollY>
          <box flexDirection="column" gap={1} flexShrink={0}>
            {grid}
          </box>
        </Scroll>
      ) : (
        grid
      )}
    </box>
  )
}

function Account({ panel: p, picked }: { panel: AccountPanelView; picked: boolean }) {
  return (
    <Panel flexGrow={1} picked={picked} paddingBottom={1}>
      <text wrapMode="none" flexShrink={0} marginBottom={1}>
        <span fg={p.color} attributes={TextAttributes.BOLD}>
          {p.name}
        </span>
      </text>
      <Status panel={p} />
      {lines(p.body).map((l, i) => (
        // in a row each, so a line wraps to the panel's width and never gives up its height
        <box key={i} flexDirection="row" flexShrink={0}>
          <Wrapped segs={l.length ? l : [[" ", ""]]} />
        </box>
      ))}
      {p.state === "off" ? <Connect panel={p} /> : <Connected panel={p} />}
    </Panel>
  )
}

/** The first line: where the stats come from, and Disconnect at its right end, always. The words take their longest
 * form that fits beside it, and where neither does (a long @handle, a window 80 columns wide) the shorter is cut off
 * with "…": Disconnect is nowhere else, and it used to be the one left out. */
function Status({ panel: p }: { panel: AccountPanelView }) {
  const [width, setWidth] = useState(0)
  const out = p.disconnect
  const room = width - (out ? segsLen(out) + 2 : 0) // what the words have, two columns clear of Disconnect
  const words = segsLen(p.status) <= room ? p.status : (p.status_short ?? p.status)
  const cut = truncate(words, Math.max(room - 1, 0))
  const line: Segs = !width || segsLen(words) <= room ? words : [...cut.slice(0, -1), [(cut.at(-1)?.[0] ?? "").trimEnd() + "…", words.at(-1)?.[1]]]
  return (
    <box
      flexDirection="row"
      height={1}
      flexShrink={0}
      onSizeChange={function (this: Renderable) {
        setWidth(this.width)
      }}
    >
      <Line segs={line} flexGrow={1} />
      {out ? <Line segs={out} /> : null}
    </box>
  )
}

/** Not connected: a box for the @handle (a link to the profile works too), Connect, and Sign up for someone without
 * the account yet. One row, so even a short window shows all three. */
function Connect({ panel: p }: { panel: AccountPanelView }) {
  const s = useDash()
  const typed = s.handles[p.key] ?? ""
  const error = s.handleErrors[p.key]
  const signUp = () => void s.openDialog("signup", s.bridge.call("signup_guide", p.key))
  return (
    <>
      <box flexDirection="row" height={3} marginTop={1} flexShrink={0} alignItems="center">
        <box flexGrow={1} maxWidth={40} marginRight={2}>
          <TextField
            id={`handle-${p.key}`}
            value={typed}
            placeholder="@yourname"
            bad={!!error}
            flexGrow={1}
            onChange={(v) => s.set({ handles: { ...s.handles, [p.key]: v } })}
            onSubmit={(v) => void s.connect(p.key, v)}
          />
        </box>
        <Button id={`connect-${p.key}`} label="Connect" variant="primary" marginRight={1} onPress={() => void s.connect(p.key, s.handles[p.key] ?? "")} />
        {p.signup ? <Button id={`signup-${p.key}`} label="Sign up →" marginRight={1} onPress={signUp} /> : null}
      </box>
      {error ? (
        <text fg={DASH.bad} wrapMode="word" flexShrink={0}>
          {error}
        </text>
      ) : null}
      <box marginTop={1} flexShrink={0}>
        <Wrapped segs={p.note} />
      </box>
    </>
  )
}

/** Connected (or waiting for its one log-in): its buttons, then its followers chart taking whatever room is left. The
 * buttons come right after the status lines, so a short window never pushes them out of sight. */
function Connected({ panel: p }: { panel: AccountPanelView }) {
  const s = useDash()
  const chart = p.chart
  const draw = useCallback(
    (w: number, h: number, hover: number | null, grow: number): Drawn =>
      drawLines(chart!.series, chart!.n, w, h, (pw) => clockLabels(chart!.slots, pw, chart!.step_h), [], hover, false, true, true, { grow, fade: true }),
    [chart],
  )
  const title = (hover: number | null): Segs => {
    if (!chart) return []
    if (hover === null || hover < 0 || hover >= chart.n) return chart.heading
    const value = chart.values[hover]
    const guess = chart.guessed?.[hover] ?? false
    return [
      ["FOLLOWERS  ", "bold"],
      [chart.slots[hover]!.label + "  ", `bold ${MARK}`],
      value !== null && value !== undefined ? [(guess ? "≈" : "") + fmtNum(value), "bold"] : ["no reading yet", DASH.muted],
      ...(guess && value !== null && value !== undefined ? ([["  estimated", DASH.muted]] as Segs) : []),
    ]
  }
  const was = p.old_handles ?? []
  return (
    <>
      {/* on one row while they fit, else on under themselves: a short window never loses a button */}
      <box flexDirection="row" flexWrap="wrap" marginTop={1} flexShrink={0}>
        {p.logs_in ? (
          <Button id={`login-${p.key}`} label={p.login_label ?? "Log in"} variant="primary" marginRight={1} onPress={() => s.bridge.send("login", p.key)} />
        ) : null}
        {p.state === "on" ? (
          <>
            <Button id={`profile-${p.key}`} label="Open profile →" disabled={p.profile_disabled} marginRight={1} onPress={() => s.bridge.send("open_profile", p.key)} />
            <Button
              id={`refresh-${p.key}`}
              label="Refresh now"
              disabled={p.refresh_disabled}
              marginRight={1}
              onPress={() => s.bridge.send("refresh_platform", p.key)}
            />
          </>
        ) : null}
        {p.show_log ? (
          <Button
            id={`log-${p.key}`}
            label="Type in stats"
            marginRight={1}
            onPress={() => void s.openDialog("log", s.bridge.call("log_stats", p.key))}
          />
        ) : null}
        {p.logs_out ? (
          <Button id={`logout-${p.key}`} label="Log out" marginRight={1} onPress={() => void s.openDialog("logout", s.bridge.call("logout_info", p.key))} />
        ) : null}
        {was.length ? (
          // Stats read under another @handle before: keep them or forget them (the panel's amber line says they're there)
          <Button
            id={`was-${p.key}`}
            label={`Stats from ${was[0]}${was.length > 1 ? ` +${was.length - 1}` : ""}…`}
            marginRight={1}
            onPress={() => void s.openDialog("handle_change", s.bridge.call("handle_change_info", p.key))}
          />
        ) : null}
      </box>
      <box marginTop={1} flexShrink={0}>
        <Wrapped segs={p.note} />
      </box>
      {chart ? (
        <box marginTop={1} flexGrow={1} flexDirection="column">
          <Chart title={title} draw={draw} flexGrow={1} bare />
        </box>
      ) : null}
    </>
  )
}
