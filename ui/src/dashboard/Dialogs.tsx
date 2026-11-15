// Dialogs over the window: how ReelForge works, typing in stats, removing a video, which video a post is, making an
// account (or the channel's Gmail), disconnecting one, logging ReelForge's browser out of one, the stats from before a
// handle changed, the question before a post and the browser extension (Posting.tsx), the connection to YouTube's own
// API and the question before a post through it (YouTubeApi.tsx), the profile button's menu and deleting a profile
// (Profiles.tsx), and the Settings tab's (Settings.tsx).

import { TextAttributes } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/react"
import { useState } from "react"
import { wrappedRows, type Segs } from "../lib/styled"
import { DASH } from "../theme"
import { Button } from "../ui/Button"
import { RadioSet, TextField } from "../ui/Fields"
import { OptionList } from "../ui/Lists"
import { DialogButtons, Modal, modalRoom, Note, Scroll } from "../ui/Panels"
import { useFocusManager } from "../lib/focus"
import { ExtensionDialog, PostDialog } from "./Posting"
import { DeleteProfile, ProfileMenu } from "./Profiles"
import { DiscardProfile, FactoryReset, MoveFolder, Uninstall } from "./Settings"
import { PUBLISH_KEY, useDash, type EpisodeData, type LogData, type SignupData } from "./state"
import { ApiDialog, ApiPostDialog } from "./YouTubeApi"

export function Dialogs() {
  const s = useDash()
  const d = s.dialog
  if (!d) return null
  const close = () => s.set({ dialog: null })
  if (d.kind === "help") return <Help close={close} />
  if (d.kind === "log") return <LogStats data={d.data} close={close} />
  if (d.kind === "remove") return <Remove data={d.data} close={close} />
  if (d.kind === "signup") return <SignUp data={d.data} close={close} />
  if (d.kind === "disconnect") return <Disconnect data={d.data} close={close} />
  if (d.kind === "logout") return <LogOut data={d.data} close={close} />
  if (d.kind === "handle_change") return <HandleChange data={d.data} close={close} />
  if (d.kind === "post") return <PostDialog data={d.data} close={close} />
  if (d.kind === "extension") return <ExtensionDialog from={d.from} close={close} />
  if (d.kind === "youtube_api") return <ApiDialog connect={d.connect} close={close} />
  if (d.kind === "api_post") return <ApiPostDialog data={d.data} close={close} />
  if (d.kind === "profiles") return <ProfileMenu data={d.data} close={close} />
  if (d.kind === "delete_profile") return <DeleteProfile data={d.data} close={close} />
  if (d.kind === "discard_profile") return <DiscardProfile data={d.data} close={close} />
  if (d.kind === "factory_reset") return <FactoryReset data={d.data} close={close} />
  if (d.kind === "uninstall") return <Uninstall data={d.data} close={close} />
  if (d.kind === "move_folder") return <MoveFolder data={d.data} close={close} />
  return <EpisodePicker data={d.data} close={close} />
}

/** How ReelForge works: the tabs first, in the order they sit along the top, each with the number that opens it, then
 * Publish (the green button at the right) with its key, then the rest. */
function Help({ close }: { close: () => void }) {
  const s = useDash()
  const place = (tab: string) => (s.order as string[]).indexOf(tab) // -1 for Publish: it isn't along the top
  const rank = (tab: string) => (place(tab) < 0 ? s.order.length : place(tab))
  const key = (tab: string) => (place(tab) < 0 ? PUBLISH_KEY : String(place(tab) + 1))
  const topics = s.init.help.map((topic, i) => ({ topic, rank: topic[2] ? rank(topic[2]) : s.order.length + 1 + i })).sort((a, b) => a.rank - b.rank)
  return (
    <Modal name="help" title="HOW REELFORGE WORKS">
      <Scroll maxHeight={30} flexShrink={1} scrollY>
        {/* a column kept free beside the scrollbar, which is drawn over the last one of text that wrapped before it showed */}
        <box flexDirection="column" flexShrink={0} paddingRight={1}>
          {topics.map(({ topic: [title, text, tab] }) => (
            <box key={title} flexDirection="column" marginBottom={1} flexShrink={0}>
              <text fg={DASH.text} attributes={TextAttributes.BOLD}>
                {tab ? `${title} (${key(tab)})` : title}
              </text>
              <text fg={DASH.muted} wrapMode="word">
                {text}
              </text>
            </box>
          ))}
          <text fg={DASH.text} attributes={TextAttributes.BOLD} flexShrink={0}>
            Keys
          </text>
          {s.init.keys.map(([key, what]) => (
            // each key in a column as wide as the longest, what it does wrapping beside it
            <box key={key} flexDirection="row" flexShrink={0}>
              <text attributes={TextAttributes.BOLD} width={Math.max(...s.init.keys.map(([k]) => k.length), 0) + 4} flexShrink={0} wrapMode="none">
                {`  ${key}`}
              </text>
              <text fg={DASH.muted} wrapMode="word" flexGrow={1} flexShrink={1}>
                {what}
              </text>
            </box>
          ))}
          <text fg={DASH.muted} flexShrink={0}>
            Everything else is a button on the page it applies to.
          </text>
        </box>
      </Scroll>
      <DialogButtons>
        <Button id="help-close" label="Close" variant="primary" autoFocus onPress={close} />
      </DialogButtons>
    </Modal>
  )
}

/** Type in numbers for platforms ReelForge can't read itself (Instagram and X before logging in). */
function LogStats({ data, close }: { data: LogData; close: () => void }) {
  const s = useDash()
  const fill = (key: string) => {
    const last = data.last[key]
    return {
      followers: last?.followers === null || last?.followers === undefined ? "" : String(last.followers),
      views: Object.fromEntries(data.episodes.map((e) => [String(e.id), last?.views[String(e.id)] !== undefined ? String(last.views[String(e.id)]) : ""])),
    }
  }
  const [platform, setPlatform] = useState(data.start)
  const [values, setValues] = useState(() => fill(data.start))
  const [problem, setProblem] = useState("")
  const save = async () => {
    let error: string | null
    try {
      error = await s.bridge.call<string | null>("save_logged_stats", platform, values.followers, values.views)
    } catch (e) {
      return s.bridge.failed(e) // nothing was saved: the dialog stays, with what was typed
    }
    if (error) setProblem(error)
    else close()
  }
  return (
    <Modal name="log" title="TYPE IN STATS">
      <RadioSet
        id="ls-platform"
        options={data.platforms.map((p) => [p.key, p.name])}
        value={platform}
        onChange={(key) => {
          setPlatform(key)
          setValues(fill(key))
        }}
      />
      <box marginTop={1} marginBottom={1} flexShrink={0}>
        <text fg={DASH.muted} wrapMode="word">
          Copy the numbers from the app. Blank fields are skipped; 1.2K and 3,400 both work. Enter saves.
        </text>
      </box>
      <box flexDirection="row" height={3} flexShrink={0}>
        <box flexGrow={1} justifyContent="center">
          <text attributes={TextAttributes.BOLD}>Followers</text>
        </box>
        <TextField
          id="ls-followers"
          width={18}
          autoFocus
          value={values.followers}
          placeholder="followers"
          onChange={(v) => setValues((x) => ({ ...x, followers: v }))}
          onSubmit={() => void save()}
        />
      </box>
      {data.episodes.length ? (
        <text fg={DASH.muted} attributes={TextAttributes.BOLD} marginTop={1} flexShrink={0}>
          VIEWS PER VIDEO
        </text>
      ) : (
        <text fg={DASH.muted} flexShrink={0}>
          Videos show up here once you've uploaded them from the Publish tab.
        </text>
      )}
      {/* as tall as its rows, up to five of them: by itself a scrolling area takes all the height it can get */}
      <Scroll maxHeight={Math.min(data.episodes.length * 3, 15)} flexShrink={1} scrollY>
        {data.episodes.map((e) => (
          <box key={e.id} flexDirection="row" height={3} flexShrink={0}>
            <box flexGrow={1} justifyContent="center">
              <text wrapMode="none">{e.title}</text>
            </box>
            <TextField
              id={`ls-ep-${e.id}`}
              width={18}
              value={values.views[String(e.id)] ?? ""}
              placeholder="views"
              onChange={(v) => setValues((x) => ({ ...x, views: { ...x.views, [String(e.id)]: v } }))}
              onSubmit={() => void save()}
            />
          </box>
        ))}
      </Scroll>
      {problem ? (
        // (what's wrong with what was typed is red everywhere: here, under an @handle, in first-time setup. A number
        // lower than the one saved is not wrong, it is asked about: amber, and Save again is the answer)
        <text fg={problem.includes("Press Save again") ? DASH.warn : DASH.bad} marginTop={1} wrapMode="word" flexShrink={0}>
          {problem}
        </text>
      ) : null}
      <DialogButtons>
        <Button id="ls-cancel" label="Cancel" onPress={close} />
        <Button id="ls-save" label="Save" variant="primary" onPress={() => void save()} />
      </DialogButtons>
    </Modal>
  )
}

/** What to do with a video in the Publish list: move it to the Recycle Bin (or Trash), or hide it from the list. */
function Remove({ data, close }: { data: { path: string; note: import("../lib/styled").Segs; bin: string }; close: () => void }) {
  const s = useDash()
  const choose = (choice: string) => {
    close()
    s.bridge.send("remove_video", data.path, choice)
  }
  return (
    <Modal name="remove" title="REMOVE VIDEO">
      <Note segs={data.note} />
      <DialogButtons>
        <Button id="rm-cancel" label="Cancel" autoFocus onPress={close} />
        <Button id="rm-hide" label="Hide from list" onPress={() => choose("hide")} />
        <Button id="rm-recycle" label={`Move to ${data.bin}`} variant="error" onPress={() => choose("recycle")} />
      </DialogButtons>
    </Modal>
  )
}

/** Which video a post is: one of your episodes, or not one of your videos. */
function EpisodePicker({ data, close }: { data: EpisodeData; close: () => void }) {
  const s = useDash()
  const [selected, setSelected] = useState(data.current ?? data.options[0]?.id ?? null)
  const choose = (id: string) => {
    close()
    s.bridge.send("set_episode", data.key, id)
  }
  return (
    <Modal name="episode" title="WHICH VIDEO IS THIS?">
      <Note segs={data.note} />
      {/* in a window too short for the whole list it gives way first (it scrolls by itself), before what's said above it */}
      <box marginTop={1} flexShrink={8} minHeight={3} flexDirection="column">
        <OptionList
          id="episode-choices"
          items={data.options}
          selected={selected}
          maxLines={16}
          autoFocus
          onSelect={setSelected}
          onActivate={choose}
        />
      </box>
      <DialogButtons>
        <Button id="ep-cancel" label="Cancel" onPress={close} />
        <Button id="ep-pick" label="This one" variant="primary" disabled={!selected} onPress={() => selected && choose(selected)} />
      </DialogButtons>
    </Modal>
  )
}

/** Making an account you don't have yet (or the channel's Gmail): a few steps, a button that opens the platform's own
 * sign-up page in your usual browser (a private window of it for a second channel, so the other one stays logged in),
 * and a box for the @handle you end up with, which Connect connects. ReelForge never makes the account, fills in a
 * form or sees a password. */
function SignUp({ data, close }: { data: SignupData; close: () => void }) {
  const s = useDash()
  const focus = useFocusManager()
  const [opened, setOpened] = useState("") // what opened, said under the button: a private window, for a second channel
  const [handle, setHandle] = useState(s.handles[data.key] ?? "")
  const [problem, setProblem] = useState("")
  const open = async () => {
    let said: string | null
    try {
      said = await s.bridge.call<string | null>("open_signup", data.key)
    } catch (e) {
      return s.bridge.failed(e) // nothing opened, so nothing here says it did
    }
    setOpened(said || "Opened in your browser. Come back here when you're done.")
    setTimeout(() => focus.focus(data.account ? "su-handle" : "su-done"), 30)
  }
  const done = (typed = handle) => {
    if (!data.account) return close()
    if (!typed.trim()) return setProblem("Type your new @handle first.")
    close()
    void s.connect(data.key, typed)
  }
  // The reason and the steps, as many rows as they wrap to. In a window too short for the whole dialog (three of the
  // four are a row taller than a window 24 rows tall takes) they scroll, and the button, the box for the handle and
  // the dialog's own buttons keep their rows. With nothing to give way, those were drawn on the dialog's border.
  const room = modalRoom(84, useTerminalDimensions().width) - 1 // a column kept free beside the scrollbar
  const rows = wrappedRows(data.why, room) + 1 + data.steps.reduce((n, step) => n + wrappedRows(step, room - 4), 0)
  return (
    <Modal name="signup" title={data.title} width={84}>
      <Scroll maxHeight={rows} flexShrink={1} scrollY>
        <box flexDirection="column" flexShrink={0} paddingRight={1}>
          <text fg={DASH.muted} wrapMode="word" flexShrink={0}>
            {data.why}
          </text>
          <box flexDirection="column" marginTop={1} flexShrink={0}>
            {data.steps.map((step, i) => (
              <box key={i} flexDirection="row" flexShrink={0}>
                <text attributes={TextAttributes.BOLD} width={4} flexShrink={0}>
                  {`${i + 1}.`.padStart(3)}
                </text>
                <text wrapMode="word" flexGrow={1} flexShrink={1}>
                  {step}
                </text>
              </box>
            ))}
          </box>
        </box>
      </Scroll>
      <box flexDirection="row" height={1} marginTop={1} marginLeft={4} flexShrink={0}>
        <Button id="su-open" label={data.button} variant="primary" autoFocus onPress={() => void open()} />
      </box>
      {opened ? (
        <text fg={DASH.good} marginLeft={4} wrapMode="word" flexShrink={0}>
          {`✓ ${opened}`}
        </text>
      ) : null}
      {data.account ? (
        <>
          <text attributes={TextAttributes.BOLD} marginTop={1} flexShrink={0}>
            Your @handle
          </text>
          <TextField id="su-handle" value={handle} placeholder="@yourname" bad={!!problem} onChange={setHandle} onSubmit={(v) => done(v)} />
          {problem ? (
            <text fg={DASH.bad} flexShrink={0}>
              {problem}
            </text>
          ) : null}
        </>
      ) : null}
      <DialogButtons>
        <Button id="su-cancel" label="Cancel" onPress={close} />
        <Button id="su-done" label={data.account ? "Connect" : "Done"} variant={data.account ? "primary" : "default"} onPress={() => done()} />
      </DialogButtons>
    </Modal>
  )
}

/** Are you sure? Disconnecting a platform hides it everywhere in ReelForge and stops its reads; its stats stay. While
 * ReelForge's own browser is logged in to it (Instagram, X), that log-in stays too unless you pick Disconnect and log
 * out. */
function Disconnect({ data, close }: { data: { key: string; title: string; note: Segs; logged_in?: boolean }; close: () => void }) {
  const s = useDash()
  const go = (logout: boolean) => {
    close()
    s.bridge.send("disconnect", data.key, logout)
  }
  return (
    <Modal name="disconnect" title={data.title}>
      <Note segs={data.note} />
      <DialogButtons>
        <Button id="dc-cancel" label="Cancel" autoFocus onPress={close} />
        {data.logged_in ? <Button id="disconnect-logout" label="Disconnect and log out" variant="error" onPress={() => go(true)} /> : null}
        <Button id="disconnect" label="Disconnect" variant="error" onPress={() => go(false)} />
      </DialogButtons>
    </Modal>
  )
}

/** Are you sure? Logging ReelForge's own browser out of a platform removes the log-in it keeps on this computer. The
 * platform stays connected with its stats, and isn't read again until you log in. */
function LogOut({ data, close }: { data: { key: string; title: string; note: Segs; button: string }; close: () => void }) {
  const s = useDash()
  return (
    <Modal name="logout" title={data.title}>
      <Note segs={data.note} />
      <DialogButtons>
        <Button id="lo-cancel" label="Cancel" autoFocus onPress={close} />
        <Button
          id="logout"
          label={data.button}
          variant="error"
          onPress={() => {
            close()
            s.bridge.send("logout", data.key)
          }}
        />
      </DialogButtons>
    </Modal>
  )
}

/** A platform whose stats were read under another @handle before: keep those stats (the account was renamed), or
 * forget them (a typo, or someone else's account). Forgetting removes readings and videos, after a copy of the stats
 * database is saved, so it's the red button and Cancel has the keyboard. */
function HandleChange({ data, close }: { data: { key: string; title: string; note: Segs; forget: string; keep: string }; close: () => void }) {
  const s = useDash()
  const keep = () => {
    close()
    s.bridge.send("keep_handle_stats", data.key)
  }
  const forget = () => {
    close()
    s.bridge.send("forget_handle_stats", data.key)
  }
  return (
    <Modal name="handle-change" title={data.title}>
      <Note segs={data.note} />
      <DialogButtons>
        <Button id="hc-cancel" label="Cancel" autoFocus onPress={close} />
        <Button id="hc-keep" label={data.keep} onPress={keep} />
        <Button id="hc-forget" label={data.forget} variant="error" onPress={forget} />
      </DialogButtons>
    </Modal>
  )
}
