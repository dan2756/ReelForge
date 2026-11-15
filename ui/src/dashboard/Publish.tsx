// The Publish tab: every finished video with its post already written. For the picked video, under its caption and
// hashtags: POST, where its post stands on each platform and Post…, which has the browser extension fill in each upload
// page for you to check and press Post on (Posting.tsx); then BY HAND, each platform's own button, which opens its
// upload page, copies exactly the text it needs and shows the file, so you drag it in and press Post. ReelForge spots
// the post by itself either way. Then the exact text each platform gets, and the uploads so far. Under the list, the
// best time to post on each platform.

import { TextAttributes } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/react"
import { useEffect } from "react"
import { useView } from "../bridge"
import { cellLen, type Parts, type Segs } from "../lib/styled"
import { DASH, mix } from "../theme"
import { Button } from "../ui/Button"
import { TextField } from "../ui/Fields"
import { Fitted, Line } from "../ui/Line"
import { OptionList, Table } from "../ui/Lists"
import { Asleep } from "../ui/Mascot"
import { Panel, Reading, Scroll } from "../ui/Panels"
import { PostBlock } from "./Posting"
import { useDash } from "./state"
import type { FilesView, PlatformInfo, PreviewView, TableView } from "./types"

/** Columns the window needs for the videos beside the form. Narrower, the videos go above it and the whole tab scrolls
 * as one page, so the form keeps the window's width. */
const SIDE_BY_SIDE = 96
/** Videos listed above the form in a narrow window before their list scrolls by itself. */
const STACKED_VIDEOS = 2

export function Publish() {
  const { width } = useTerminalDimensions()
  if (width < SIDE_BY_SIDE) {
    return (
      <Scroll flexGrow={1} scrollY>
        <box height={1} flexShrink={0} />
        <Files rows={STACKED_VIDEOS} />
        <box height={1} flexShrink={0} />
        <Form page />
        <box height={1} flexShrink={0} />
        <Best />
      </Scroll>
    )
  }
  return (
    <box flexDirection="row" flexGrow={1} marginTop={1} gap={1}>
      {/* A third of the width for the videos (a min width would count towards Yoga's share of the rest). */}
      <box flexDirection="column" width="33%" minWidth={44} flexShrink={0} gap={1}>
        <Files />
        <Best />
      </box>
      <Form />
    </box>
  )
}

/** The videos. rows: this many show before the list scrolls (a narrow window, where it sits above the form); otherwise
 * it takes the height it's given. */
function Files({ rows }: { rows?: number }) {
  const s = useDash()
  const files = useView<FilesView>(s.bridge, "files")
  const count = files?.items.length ?? 0
  return (
    <Panel flexGrow={rows ? undefined : 1} flexShrink={rows ? 0 : 1} paddingX={1}>
      {files ? (
        <box height={1} marginBottom={1} paddingLeft={1} flexShrink={0}>
          <Line segs={files.heading} base={DASH.faint} />
        </box>
      ) : (
        // (before the list has come: an empty list here would say "no videos yet", with the mascot asleep over it)
        <box paddingLeft={1} marginBottom={1} flexShrink={0}>
          <Reading what="your videos" inside />
        </box>
      )}
      <OptionList
        id="file-list"
        items={files?.items ?? []}
        selected={files?.selected}
        spaced
        maxLines={rows ? Math.max(Math.min(count, rows) * 3, 3) : undefined}
        onSelect={(id) => {
          s.bridge.emit("files", { ...files!, selected: id })
          s.bridge.send("pick_file", id)
        }}
        onMenu={(id) => void s.openDialog("remove", s.bridge.call("remove_menu", id))}
        empty={
          files ? (
            <Asleep>
              <box flexDirection="column">
                {(files.empty ?? []).map((l, i) => (
                  <Line key={i} segs={l} />
                ))}
              </box>
            </Asleep>
          ) : null
        }
      />
    </Panel>
  )
}

function Best() {
  const s = useDash()
  const best = useView<{ title: Parts; lines: Parts[] }>(s.bridge, "best")
  return (
    <Panel flexShrink={0} paddingBottom={1}>
      <box height={1} marginBottom={1} flexShrink={0}>
        <Fitted parts={best?.title} base={DASH.faint} flexGrow={0} />
      </box>
      {(best?.lines ?? []).map((l, i) => (
        <Fitted key={i} parts={l} flexGrow={0} />
      ))}
    </Panel>
  )
}

/** A field's name and its count, then the hint after them where the form has room for all of it (it was cut off
 * mid-word where it hadn't). */
function Label({ segs, fallback, first }: { segs: Segs | undefined; fallback: string; first?: boolean }) {
  const all = segs ?? [[fallback, ""]]
  return (
    <box marginTop={first ? 1 : 0} flexShrink={0}>
      <Fitted parts={[[all.slice(0, 2), 0], [all.slice(2), 1]]} base={DASH.muted} flexGrow={1} />
    </box>
  )
}

/** The picked video's post: its caption and hashtags, POST, BY HAND, the preview and the uploads so far. Everything
 * that starts a post sits above the preview, so it shows in a window 30 rows tall. page: the whole tab scrolls (a
 * narrow window), so this doesn't scroll by itself. */
function Form({ page }: { page?: boolean }) {
  const s = useDash()
  const { init } = s
  const preview = useView<PreviewView>(s.bridge, "preview")
  const uploads = useView<TableView>(s.bridge, "uploads")
  useEffect(() => {
    // the preview of the form as it is (another tab and back, or nothing picked yet)
    s.bridge.send("form_changed", s.caption, s.tags)
  }, [])
  const changed = (caption: string, tags: string) => s.formTyped(caption, tags)
  const body = (
    <>
      <Label first segs={preview?.caption} fallback="Caption" />
      <TextField
        id="f-caption"
        value={s.caption}
        maxLength={200}
        placeholder={init.caption_placeholder}
        onChange={(v) => changed(v, s.tags)}
      />
      <Label segs={preview?.tags} fallback="Hashtags" />
      <TextField id="f-tags" value={s.tags} placeholder={init.tags_placeholder} onChange={(v) => changed(s.caption, v)} />
      <PostBlock />
      <ByHand />
      <box backgroundColor={mix(DASH.surface, DASH.bg, 0.5)} marginTop={2} paddingLeft={2} paddingRight={2} paddingTop={1} paddingBottom={1} flexDirection="column" flexShrink={0}>
        <text fg={DASH.text} attributes={TextAttributes.BOLD} flexShrink={0} marginBottom={1}>
          PREVIEW
        </text>
        {(preview?.rows ?? []).map((r) => (
          <box key={r.name} flexDirection="row" flexShrink={0}>
            <text fg={r.color} attributes={TextAttributes.BOLD} width={12} flexShrink={0}>
              {r.name}
            </text>
            <text fg={DASH.text} wrapMode="word" flexGrow={1}>
              {r.text}
            </text>
          </box>
        ))}
      </box>
      <box marginTop={1} marginBottom={1} flexShrink={0}>
        <Line segs={uploads?.heading} />
      </box>
      {/* (with nothing uploaded yet, two rows for the words that say so) */}
      <Table id="uploads-table" data={uploads} cursor={false} rowsShown={Math.min(uploads?.rows.length || 2, 11)} />
    </>
  )
  return (
    <Panel flexGrow={page ? undefined : 1} flexShrink={page ? 0 : 1} paddingTop={0} paddingBottom={page ? 1 : 0}>
      {page ? (
        body
      ) : (
        <Scroll flexGrow={1} scrollY>
          {/* A column kept free beside the scrollbar: text that wrapped before the scrollbar showed keeps the width
              it had then, and its last column would be drawn over. */}
          <box flexDirection="column" flexShrink={0} paddingRight={1}>
            {body}
          </box>
        </Scroll>
      )}
    </Panel>
  )
}

/** BY HAND: posting without the extension, as it always worked. A row for each connected platform: its button opens
 * its upload page, copies its text and shows the file (a ✓ on it once the post is up there), and beside it the buttons
 * that copy each piece of its text again. Open all opens every page at once. */
function ByHand() {
  const s = useDash()
  const { init } = s
  const posted = useView<{ done: Record<string, boolean> }>(s.bridge, "posted")
  const connected = useView<{ platforms: PlatformInfo[] }>(s.bridge, "platforms")
  const ps = connected?.platforms ?? init.platforms // only the connected platforms have buttons
  const copies = init.copies
  const width = Math.max(0, ...ps.map((p) => cellLen(`${p.name} →`))) + 4 // the platforms' buttons, one under the other
  // the caption on its own, for when neither X (whose post it is) nor YouTube (its title) has a button
  const captionAlone = ps.length > 0 && !ps.some((p) => p.key === "x" || p.key === "youtube")
  const heading: Parts = [
    [[["BY HAND  ", "bold"]], 0],
    [[["each button opens the upload page", DASH.faint]], 1],
    [[[", copies that platform's text and shows the file", DASH.faint]], 2],
  ]
  return (
    <box flexDirection="column" marginTop={1} flexShrink={0}>
      <box flexDirection="row" height={1} flexShrink={0} gap={1}>
        <Fitted parts={heading} flexGrow={1} />
        {ps.length > 1 ? <Button id="open-all" label={`Open all ${ps.length} →`} onPress={() => s.bridge.send("open_all", s.caption, s.tags)} /> : null}
        <Button id="reveal" label="Show file" variant="ghost" onPress={() => s.bridge.send("reveal_file")} />
      </box>
      {ps.map((p) => (
        <box key={p.key} flexDirection="row" flexShrink={0} gap={1}>
          <Button
            id={`open-${p.key}`}
            label={`${p.name} ${posted?.done?.[p.key] ? "✓" : "→"}`}
            width={width}
            accent={p.color}
            onPress={() => s.bridge.send("open_upload", p.key, s.caption, s.tags)}
          />
          {/* its copy buttons, which go on under themselves where the row is too narrow for them */}
          <box flexDirection="row" flexWrap="wrap" flexGrow={1} flexShrink={1} minWidth={0} columnGap={1}>
            {(copies[p.key] ?? []).map(([id, label]) => (
              <Button key={id} id={id} label={label} variant="ghost" onPress={() => s.bridge.send("copy_field", id, s.caption, s.tags)} />
            ))}
          </box>
        </box>
      ))}
      {captionAlone ? (
        <box flexDirection="row" flexShrink={0}>
          <Button id="copy-caption-alone" label="Copy caption" variant="ghost" onPress={() => s.bridge.send("copy_field", "copy-caption-alone", s.caption, s.tags)} />
        </box>
      ) : null}
    </box>
  )
}
