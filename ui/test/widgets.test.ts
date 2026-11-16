// The Overview's cards and titles never cut anything off; the big numbers, sparklines and the bell are block art in one
// colour per cell; release notes' Markdown reads as clean lists; the Python side's colours come out in the window's
// palette (src/dashboard/cards.ts, src/lib/fit.ts, src/ui/Graphics.tsx, src/ui/Panels.tsx, src/theme.ts).

import { describe, expect, test } from "bun:test"
import { headerFit, tabWindow } from "../src/dashboard/App"
import { wrap } from "../src/dashboard/Drawer"
import { cardLines, wrapLines } from "../src/dashboard/cards"
import { metricTitle, pickVariant } from "../src/dashboard/titles"
import type { MetricView, Variant } from "../src/dashboard/types"
import { fit, fitRows, whole } from "../src/lib/fit"
import { fmtNum, shortNum } from "../src/lib/format"
import { cellLen, plain, segsLen, wrappedRows, type Parts } from "../src/lib/styled"
import { PLATFORM, restyle, tone } from "../src/theme"
import { bell, BELL_WIDTH, faceCells, faceWidth, sparkline, stackedSparkline } from "../src/ui/Graphics"
import { stretched } from "../src/ui/Lists"
import { inline, mdBlocks, toasts } from "../src/ui/Panels"

const VALUES = { followers: 12_900, views: 1_234_567, likes: 98_765 }
const CHANGES = { followers: 1_030, views: 19_400, likes: -40 }

describe("cards", () => {
  test("short numbers", () => {
    const cases: [number | null, string][] = [
      [null, "—"], [999, "999"], [1_030, "1K"], [9_940, "9.9K"], [12_900, "13K"], [999_600, "1M"], [1_234_567, "1.2M"],
      [-19_400, "-19K"],
    ]
    for (const [n, text] of cases) expect(shortNum(n)).toBe(text)
    expect([fmtNum(950), fmtNum(1_234), fmtNum(12_345), fmtNum(1_234_567)]).toEqual(["950", "1.23K", "12.3K", "1.23M"])
  })

  test("card lines always fit", () => {
    for (let width = 0; width < 60; width++) {
      for (const line of cardLines(VALUES, CHANGES, width)) {
        expect(segsLen(line)).toBeLessThanOrEqual(width)
        expect(plain(line)).not.toContain("…")
      }
    }
  })

  test("card lines shorten in order", () => {
    const lines = (width: number) => cardLines(VALUES, CHANGES, width).map(plain)
    expect(lines(40)[0]).toBe("  12.9K followers +1.03K 24h") // room for everything
    expect(lines(24)[0]).toBe("12.9K followers +1.03K") // the " 24h" goes first
    expect(lines(21)).toEqual(["12.9K followers +1K", "1.23M views     +19K", "98.8K likes     −40"])
    expect(lines(13)).toEqual(["13K followers", "1.2M views", "99K likes"]) // labels outlast the changes
    // a column short for "followers": views and likes keep their names (an 80-column window's tiles are this wide)
    expect(cardLines({ followers: 2_540, views: 596_000, likes: 29_100 }, {}, 13).map(plain)).toEqual(["2.5K", "596K views", " 29K likes"])
    expect(lines(5)).toEqual([" 13K", "1.2M", " 99K"])
  })

  test("messages wrap instead of cutting off", () => {
    const text = [[["not tracking yet", ""]], [["Accounts tab: add your handle", ""]]] as [string, string][][]
    expect(wrapLines(text, 20).map(plain)).toEqual(["not tracking yet", "Accounts tab: add", "your handle"])
  })

  test("titles drop whole parts", () => {
    const parts: Parts = [
      [[["LIKES  ", ""]], 0],
      [[["198.6K total   ", ""]], 0],
      [[["+38K in 24h", ""]], 1],
    ]
    expect(plain(fit(parts, 30))).toBe("LIKES  198.6K total   ")
    expect(plain(fit(parts, 12))).toBe("LIKES  ") // not "LIKES  198.6"
  })

  test("a heading that came as one line of segments drops whole ones from its end, never half a number", () => {
    const heading = whole([["FOLLOWERS  ", "bold"], ["since Nov 5  ", ""], ["11.7K", "bold"], ["  ", ""], ["+7.94K", ""]])
    expect(plain(fit(heading, 40))).toBe("FOLLOWERS  since Nov 5  11.7K  +7.94K")
    expect(plain(fit(heading, 34)).trimEnd()).toBe("FOLLOWERS  since Nov 5  11.7K") // not "…  11.7K  +7."
    expect(plain(fit(heading, 26))).toBe("FOLLOWERS  since Nov 5  ")
  })
})

describe("a line of chips and a table in a narrow window", () => {
  test("the chips that always show go on a second row before any of them is left out", () => {
    const parts: Parts = [
      [[["SHOW  All   TikTok   YouTube   Instagram   X    ", ""]], 0],
      [[["Open post   Fix episode   Export CSV", ""]], 0],
      [[["    36 videos · click a header to sort", ""]], 1],
    ]
    expect(fitRows(parts, 130).map(plain)).toEqual(["SHOW  All   TikTok   YouTube   Instagram   X    Open post   Fix episode   Export CSV    36 videos · click a header to sort"])
    expect(fitRows(parts, 100).map(plain)).toEqual(["SHOW  All   TikTok   YouTube   Instagram   X    Open post   Fix episode   Export CSV"]) // the hint goes first
    expect(fitRows(parts, 76).map(plain)).toEqual(["SHOW  All   TikTok   YouTube   Instagram   X    ", "Open post   Fix episode   Export CSV"])
    expect(plain(fit(parts, 76))).not.toContain("Export CSV") // (one row left them out)
  })

  test("the title column gives way so more of the numbers show, down to a width that still reads", () => {
    const columns = [["platform", 4], ["title", 44], ["views", 8], ["score", 6]].map(([key, width]) => ({ key: key as string, label: [], width: width as number, right: false }))
    const title = (width: number) => stretched(columns, width, "title").find((c) => c.key === "title")!.width
    expect(title(100)).toBe(44 + 100 - 71) // room to spare: the title takes it
    expect(title(71)).toBe(44)
    expect(title(60)).toBe(33) // too narrow: it gives up what the numbers need
    expect(title(30)).toBe(24) // but stays readable
  })
})

describe("chart titles", () => {
  const slot = (i: number) => ({ label: `Fri ${i} PM`, when: `at ${i} PM`, hour: i, day: 24, weekday: "Fri", clock: `${i} PM`, date: "Oct 24" })
  const variant = (step: number, n: number): Variant => ({
    step_h: step,
    n,
    unit: step === 1 ? "per hour" : `per ${step}h`,
    slots: Array.from({ length: n }, (_, i) => slot(i)),
    series: [{ key: "tiktok", name: "TikTok", color: "#25f4ee", values: Array.from({ length: n }, (_, i) => i + 1) }],
    marks: [],
  })

  test("a narrow chart draws combined buckets: two columns and a gap a bar, or one and a gap", () => {
    const variants = [variant(1, 24), variant(2, 12), variant(3, 8)]
    expect(pickVariant(variants, 72).step_h).toBe(1) // 24 bars x 3 columns
    expect(pickVariant(variants, 50).step_h).toBe(2)
    expect(pickVariant(variants, 30).step_h).toBe(3) // two columns and a gap at 3 hours beats one column at 1
    expect(pickVariant([variant(1, 24), variant(2, 12)], 30).step_h).toBe(2) // else one column and a gap
    expect(pickVariant(variants, 10).step_h).toBe(3) // the coarsest when nothing fits
  })

  test("the title reads the hovered bar's numbers", () => {
    const m: MetricView = { span: "24h", hourly: true, summary: { latest: 52_000, gained: 1_200, pace: 80, change: 0.25 } }
    const v = variant(1, 24)
    const still = plain(fit(metricTitle("views", m, v, null), 200))
    expect(still).toContain("VIEWS  per hour   52.0K total   +1.20K in 24h   now 80/h   ▲ 25% vs the 24h before")
    expect(still).toContain("best 24 at 23 PM")
    const hovered = plain(fit(metricTitle("views", m, v, 5), 200))
    expect(hovered).toBe("VIEWS  Fri 5 PM  +6  TT 6  ")
  })

  test("a bar that's an estimate says so, and is never the best one", () => {
    const m: MetricView = { span: "24h", hourly: true, summary: { latest: 52_000, gained: 1_200, pace: 80, change: null } }
    const v = variant(1, 24)
    v.series.push({ key: "youtube", name: "YouTube", color: "#ff3d4e", values: Array.from({ length: 24 }, () => 2) })
    // the computer was off from 9 PM: the last three bars are estimates, all of the 10 PM and 11 PM ones, YouTube's
    // half of the 9 PM one
    v.series[0]!.guess = Array.from({ length: 24 }, (_, i) => (i >= 22 ? i + 1 : 0))
    v.series[1]!.guess = Array.from({ length: 24 }, (_, i) => (i >= 22 ? 2 : i === 21 ? 1 : 0))
    expect(plain(fit(metricTitle("views", m, v, null), 200))).toContain("best 23 at 20 PM")
    expect(plain(fit(metricTitle("views", m, v, 23), 200))).toBe("VIEWS  Fri 23 PM  ≈+26  estimated  TT 24  YT 2  so far")
    expect(plain(fit(metricTitle("views", m, v, 21), 200))).toBe("VIEWS  Fri 21 PM  ≈+24  partly estimated  TT 22  YT ≈2  ")
  })
})

describe("release notes' Markdown", () => {
  const NOTES = [
    "## What's new in 1.0.5",
    "",
    "- **A new window, built with [OpenTUI](https://github.com/anomalyco/opentui).** ReelForge's window, setup's screens",
    "  and its questions are rebuilt on OpenTUI.",
    "- **Macs and Linux PCs.** Everything works there too.",
    "  - **One-line install:** paste `curl -fsSL https://x/install.sh | bash`",
    "    into Terminal.",
    "  - **Nothing to install first.**",
    "",
    "| Your computer | Download |",
    "|---|---|",
    "| **Windows** | `irm https://x \\| iex` |",
  ].join("\n")

  test("a wrapped bullet stays one item, nested ones go a level in, a table becomes items", () => {
    const blocks = mdBlocks(NOTES)
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "gap", "item", "item", "item", "item", "gap", "item"])
    const items = blocks.filter((b) => b.kind === "item")
    expect(items.map((b) => b.depth)).toEqual([0, 0, 1, 1, 0])
    expect(items[0]!.text.endsWith("setup's screens and its questions are rebuilt on OpenTUI.")).toBe(true)
    expect(items[2]!.text).toBe("**One-line install:** paste `curl -fsSL https://x/install.sh | bash` into Terminal.")
    expect(items[4]!.text).toBe("**Windows**: `irm https://x | iex`")
  })

  test("a link inside bold shows as its text, still bold", () => {
    const segs = inline("**A window, built with [OpenTUI](https://x/y).** Rest \\*x\\*", "#fff", "#0f0")
    expect(plain(segs)).toBe("A window, built with OpenTUI. Rest *x*")
    expect(segs.find((s) => s[0] === "OpenTUI")![1]).toBe("underline bold #fff")
  })

  test("no gap at the start or the end, and blank lines never pile up", () => {
    const blocks = mdBlocks("\n\n\nHello\n\n\n\n---\n\n- one\n\n")
    expect(blocks.map((b) => b.kind)).toEqual(["para", "gap", "item"])
  })
})

describe("block art", () => {
  test("the bell is three rows of blocks in one colour", () => {
    for (const quads of [true, false]) {
      const rows = bell(quads, "#e8eaed")
      expect(rows.length).toBe(3)
      expect(new Set(rows.map((r) => r.length))).toEqual(new Set([BELL_WIDTH]))
      expect(new Set(rows.flat().filter((c) => c.ch !== " ").map((c) => c.fg))).toEqual(new Set(["#e8eaed"]))
    }
  })

  test("the big numbers: every character a tile shows has a shape, three rows tall, a column between characters", () => {
    for (const text of ["17.3K", "2.27K", "285", "2.66M", "1.2B", "0"]) {
      const rows = faceCells(text, ["#ffffff", "#888888"])
      expect(rows.length).toBe(3)
      expect(Math.max(...rows.map((r) => r.length))).toBe(faceWidth(text))
    }
    expect(faceWidth("17.3K")).toBe(17) // 3 + 1 + 3 + 1 + 1 + 1 + 3 + 1 + 3
    expect(faceWidth("1.2x")).toBe(Infinity) // a character it has no shape for: not drawn in the face at all
    const shades = faceCells("8", ["#ffffff", "#000000"]).map((r) => r.find((c) => c.fg)?.fg)
    expect(shades[0]).toBe("#ffffff") // white at the top, darker down the number
    expect(shades[2]).not.toBe("#ffffff")
  })

  test("sparklines keep to their size, eighths of a row, and grow in from nothing", () => {
    const data = [0, 1, 4, 9, 16, 25, 36, 49]
    const full = sparkline(data, 8, 2, "#25f4ee", "#12151a")
    expect(full.length).toBe(2)
    expect(full.every((r) => r.length === 8)).toBe(true)
    expect(full[0]![7]!.ch).toBe("█") // the newest, tallest reading fills its column
    const none = sparkline(data, 8, 2, "#25f4ee", "#12151a", 0)
    expect(none.flat().every((c) => c.ch === " " || c.ch === "▁")).toBe(true)
    const stacked = stackedSparkline([{ color: "#ff0000", values: [1, 2] }, { color: "#0000ff", values: [1, 2] }], 2, 2, "#000000")
    expect(stacked[0]![1]!.ch).toBe("█") // the tallest column reaches the top
    expect(stacked[1]![0]!.ch).toBe("▄") // two platforms in one cell: the lower as eighths on the upper's colour
    expect(stacked[1]![0]!.bg).not.toBeNull()
  })
})

describe("the header in a narrow window", () => {
  const TABS = ["Overview", "Videos", "Compare", "Stats", "Accounts", "Settings", "Updates ●"].map((t) => t.length)
  const tabsW = (gap: number) => TABS.reduce((n, w) => n + w, 0) + gap * (TABS.length - 1)

  test("one row while it fits; narrower, the profile button and the signs go over the tabs, which keep the row", () => {
    expect(headerFit(100, "Night Physics", 9, tabsW).stacked).toBeFalsy()
    const at80 = headerFit(80, "Night Physics", 9, tabsW)
    expect(at80.stacked).toBe(true)
    expect(at80.tabsRoom).toBeGreaterThanOrEqual(tabsW(at80.gap)) // an 80-column terminal shows every tab
    for (let width = 30; width < 100; width++) {
      const fit = headerFit(width, "Night Physics", 9, tabsW, 2)
      if (fit.stacked) expect(fit.nameRoom).toBeGreaterThanOrEqual(1)
    }
  })

  test("too narrow for every tab: whole names only, the open one always among them, and room for ‹ and ›", () => {
    const shown = (open: number, room: number) => tabWindow(TABS, 1, open, room)
    const used = ([a, b]: [number, number]) => TABS.slice(a, b + 1).reduce((n, w) => n + w, 0) + (b - a) + (a > 0 ? 2 : 0) + (b < TABS.length - 1 ? 2 : 0)
    expect(shown(0, 57)).toEqual([0, 6]) // they all fit
    expect(shown(0, 38)).toEqual([0, 3]) // Overview Videos Compare Stats ›
    expect(shown(5, 38)).toEqual([2, 5]) // ‹ Compare Stats Accounts Settings ›
    expect(shown(6, 38)).toEqual([3, 6]) // ‹ Stats Accounts Settings Updates ●
    expect(shown(-1, 38)).toEqual([0, 3]) // Publish is open: from the first one
    for (let room = 14; room <= 57; room++)
      for (let open = 0; open < TABS.length; open++) {
        const [a, b] = shown(open, room)
        expect(a).toBeLessThanOrEqual(open)
        expect(b).toBeGreaterThanOrEqual(open)
        expect(used([a, b])).toBeLessThanOrEqual(room)
      }
  })
})

describe("a notification's words in the drawer", () => {
  test("wrap to the drawer's columns, counting wide characters as the two they take", () => {
    expect(wrap("A video passed 100K views on TikTok", 20)).toEqual(["A video passed 100K", "views on TikTok"])
    const wide = "もし世界中の人が同時にジャンプしたら？ 🎬🎬🎬🎬🎬🎬🎬🎬🎬🎬🎬🎬🎬🎬 What if everyone jumped?"
    for (const width of [1, 2, 9, 20, 43]) {
      const lines = wrap(wide, width)
      for (const line of lines) expect(cellLen(line)).toBeLessThanOrEqual(Math.max(width, 2)) // (it ran to twice the width)
      expect(lines.join("").replace(/\s/g, "")).toBe(wide.replace(/\s/g, "")) // and nothing is lost
    }
  })
})

describe("pop-ups", () => {
  test("the same one again takes the first one's place; a different one stacks under it", () => {
    toasts.clear()
    for (let i = 0; i < 3; i++) toasts.add({ title: "Copied", body: "The caption is on the clipboard.", timeout: 60 })
    expect(toasts.get().length).toBe(1)
    toasts.add({ title: "Copied", body: "The hashtags are on the clipboard.", timeout: 60 })
    toasts.add({ title: "Copied", body: "The caption is on the clipboard.", severity: "warning", timeout: 60 })
    expect(toasts.get().map((t) => t.body)).toEqual(["The caption is on the clipboard.", "The hashtags are on the clipboard.", "The caption is on the clipboard."])
    toasts.clear()
  })
})

describe("the palette", () => {
  test("the Python side's colours, in styles and on their own, come out in the window's", () => {
    expect(tone("#ff4040")).toBe(PLATFORM.youtube!) // YouTube's red
    expect(tone("bold #3dff9a on #1f5c45")).not.toContain("#3dff9a")
    expect(tone("#25f4ee")).toBe("#25f4ee") // TikTok's stays
    const msg = JSON.parse('{"d": {"color": "#FF4040", "caption": "#fyp #ff4040ish #facade"}}', restyle)
    expect(msg.d.color).toBe(PLATFORM.youtube!)
    expect(msg.d.caption).toBe("#fyp #ff4040ish #facade") // a hashtag is never a colour
  })
})

describe("wrapped text", () => {
  test("the rows a text takes at a width, as it wraps at its spaces", () => {
    expect(wrappedRows("", 10)).toBe(1)
    expect(wrappedRows("one two", 10)).toBe(1)
    expect(wrappedRows("one two three", 10)).toBe(2) // "one two" / "three"
    expect(wrappedRows("one two three four five", 9)).toBe(3) // "one two" / "three" / "four five"
    expect(wrappedRows("exactly-10 and more", 10)).toBe(2)
    expect(wrappedRows("a-word-wider-than-the-width", 10)).toBe(3) // it breaks inside
    expect(wrappedRows("anything", 0)).toBe(8) // never less than a column
    expect(wrappedRows("one two", 7)).toBe(1)
    expect(wrappedRows("  one two", 7)).toBe(2) // an indented line: its spaces are drawn, so "two" no longer fits
    expect(wrappedRows("  one two", 9)).toBe(1)
    expect(wrappedRows("one  two", 7)).toBe(2) // and so is a doubled space inside it
  })
})
