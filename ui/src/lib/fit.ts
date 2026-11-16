// Titles and lines made of parts, each with an importance: 0 always shows, higher numbers go first when there isn't
// room for all of them (a chart's title on a narrow chart, a best-time line, the Videos tab's hint).

import { segsLen, truncate, type Parts, type Segs } from "./styled"

/** As fit, for a place that can grow downwards (the Videos tab's line of chips): when even the parts that always show
 * don't fit one row, they go on under themselves, whole part by whole part, instead of the last of them being left
 * out. A chip that isn't drawn can't be clicked: in an 80-column window Fix episode and Export CSV were gone. */
export function fitRows(parts: Parts, width: number): Segs[] {
  const must = parts.filter(([, rank]) => rank === 0)
  if (must.reduce((n, [t]) => n + segsLen(t), 0) <= width) return [fit(parts, width)]
  const rows: Segs[] = []
  let used = 0
  for (const [t] of must) {
    if (!rows.length || used + segsLen(t) > width) {
      rows.push([])
      used = 0
    }
    rows[rows.length - 1]!.push(...t)
    used += segsLen(t)
  }
  return rows.map((row) => truncate(row, Math.max(width, 1)))
}

/** A one-row heading's segments as parts that show whole or not at all, the last of them going first. For a heading
 * that ends in numbers: cut at the panel's edge, "+7.94K" read "+7." (the Accounts tab's followers chart in a window
 * 80 columns wide). */
export function whole(segs: Segs): Parts {
  return segs.map((seg, i): [Segs, number] => [[seg], i])
}

/** Join the parts, leaving out the least important (highest number, the last of those first) until they fit `width`.
 * If even the ones that always show don't fit, the last of them go too, so nothing is cut off halfway (a number, a
 * button); only a first part wider than everything is cut. */
export function fit(parts: Parts, width: number): Segs {
  const keep = [...parts]
  while (keep.length > 1 && keep.reduce((n, [t]) => n + segsLen(t), 0) > width) {
    let drop = 0
    for (let i = 0; i < keep.length; i++) {
      if (keep[i]![1] > keep[drop]![1] || (keep[i]![1] === keep[drop]![1] && i > drop)) drop = i
    }
    keep.splice(drop, 1)
  }
  return truncate(
    keep.flatMap(([t]) => t),
    Math.max(width, 1),
  )
}
