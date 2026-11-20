// Which button, list, table or field has the keyboard. Tab and Shift+Tab move through them in reading order (top to
// bottom, left to right, as they're laid out right now); a dialog keeps Tab inside itself, and gives the keyboard back
// to what had it when it closes. The one with the keyboard gets the keys first (Enter presses a button, the arrows
// move through a list), then the app's own keys run.

import { ScrollBoxRenderable, type BoxRenderable, type KeyEvent, type Renderable } from "@opentui/core"
import { createContext, useContext, useEffect, useRef, useSyncExternalStore, type ReactNode } from "react"
import { DASH, type Theme } from "../theme"

export interface Focusable {
  id: string
  scope: string
  node: () => Renderable | null
  /** Handle a key while this has the keyboard; true if it did. */
  onKey?: (key: KeyEvent) => boolean
  /** A text field: it types the keys itself, so the app's one-key shortcuts don't run. */
  input?: boolean
  disabled?: () => boolean
  /** The stop this one comes right before, where reading order by rows would put it in the middle of something: a
   * list in a column beside a form, which starts a row under the form's first field. */
  before?: () => string | undefined
}

export class FocusManager {
  focused: string | null = null
  private items = new Map<string, Focusable>()
  private listeners = new Set<() => void>()
  scopes: string[] = ["app"]
  /** In each scope, what last had the keyboard there: a dialog that closes hands it back. */
  private last = new Map<string, string>()
  /** The scrolling areas on screen, each with its scope: Page Up and Page Down scroll one. */
  private scrolls = new Map<ScrollBoxRenderable, string>()

  subscribe = (fn: () => void) => {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private changed() {
    for (const l of this.listeners) l()
  }

  register(f: Focusable): () => void {
    this.items.set(f.id, f)
    return () => {
      if (this.items.get(f.id) === f) {
        this.items.delete(f.id)
        if (this.focused === f.id) this.blur()
      }
    }
  }

  has(id: string): boolean {
    return this.items.has(id)
  }

  focus(id: string | null) {
    if (id === this.focused) return
    this.focused = id
    const item = id ? this.items.get(id) : undefined
    if (item) this.last.set(item.scope, item.id)
    else if (!id) this.last.delete(this.scope) // the keyboard was taken away (Esc out of a field): nothing to hand back
    this.changed()
  }

  blur() {
    this.focus(null)
  }

  /** Give a control the keyboard and bring it into view (the Python side asks for one by name: Log in, after a
   * clicked notification, in a panel that may be scrolled away). */
  show(id: string) {
    this.focus(id)
    const node = this.items.get(id)?.node()
    if (node) reveal(node)
  }

  addScroll(node: ScrollBoxRenderable, scope: string): () => void {
    this.scrolls.set(node, scope)
    return () => void this.scrolls.delete(node)
  }

  get current(): Focusable | undefined {
    return this.focused ? this.items.get(this.focused) : undefined
  }

  /** Whether a text field has the keyboard (the app's one-key shortcuts wait). */
  get typing(): boolean {
    return Boolean(this.current?.input)
  }

  pushScope(name: string) {
    this.scopes.push(name)
    this.changed()
  }

  /** A dialog closed: the keyboard goes back to what had it under the dialog, if that is still there. (Asked for
   * when the dialog opened, it was too late: its own first button had the keyboard by then.) */
  popScope(name: string) {
    const i = this.scopes.lastIndexOf(name)
    if (i > 0) this.scopes.splice(i, 1)
    this.last.delete(name)
    const back = this.last.get(this.scope)
    if (back && this.items.has(back)) this.focused = back
    else if (this.current?.scope !== this.scope) this.focused = null
    this.changed()
  }

  get scope(): string {
    return this.scopes[this.scopes.length - 1]!
  }

  /** Everything Tab reaches in the top scope, in reading order. */
  order(): Focusable[] {
    const out: [Focusable, number, number][] = []
    for (const f of this.items.values()) {
      if (f.scope !== this.scope || f.disabled?.()) continue
      const n = f.node()
      if (!n || !isShown(n) || n.width <= 0 || n.height <= 0) continue
      out.push([f, n.y, n.x])
    }
    const order = out.sort((a, b) => a[1] - b[1] || a[2] - b[2]).map((x) => x[0])
    for (const f of [...order]) {
      const target = f.before?.()
      const at = target ? order.findIndex((o) => o.id === target) : -1
      if (at < 0) continue
      order.splice(order.indexOf(f), 1)
      order.splice(order.findIndex((o) => o.id === target), 0, f)
    }
    return order
  }

  cycle(dir: 1 | -1) {
    const items = this.order()
    if (!items.length) return
    const i = items.findIndex((f) => f.id === this.focused)
    const next = i < 0 ? (dir > 0 ? 0 : items.length - 1) : (i + dir + items.length) % items.length
    this.focus(items[next]!.id)
    const node = items[next]!.node()
    if (node) reveal(node)
  }

  /** Give a key to whatever has the keyboard: Tab moves on, the rest go to it. True if it was used. */
  handleKey(key: KeyEvent): boolean {
    if (key.name === "tab") {
      this.cycle(key.shift ? -1 : 1)
      key.preventDefault()
      return true
    }
    const f = this.current
    if (f && f.scope === this.scope && f.onKey?.(key)) return true
    return this.page(key)
  }

  /** Page Up and Page Down, when what has the keyboard has no use for them: scroll the area it sits in, or else the
   * biggest one on screen (the tab, or the open dialog's), a screenful less a row. Without this a tab or a dialog
   * longer than the window could only be read with the mouse wheel. Not while a text field has the keyboard. */
  private page(key: KeyEvent): boolean {
    const by = key.name === "pagedown" ? 1 : key.name === "pageup" ? -1 : 0
    if (!by || this.typing) return false
    const here = [...this.scrolls].filter(([n, scope]) => scope === this.scope && isShown(n) && n.scrollHeight > n.viewport.height).map(([n]) => n)
    const node = this.current?.scope === this.scope ? this.current.node() : null
    const area = here.find((n) => node && inside(node, n)) ?? here.sort((a, b) => b.viewport.width * b.viewport.height - a.viewport.width * a.viewport.height)[0]
    if (!area) return false
    area.scrollBy({ x: 0, y: by * Math.max(area.viewport.height - 1, 1) })
    key.preventDefault()
    return true
  }
}

function inside(n: Renderable, area: Renderable): boolean {
  for (let p = n.parent as Renderable | null; p; p = p.parent as Renderable | null) if (p === area) return true
  return false
}

/** Scroll every scrolling area a control (or a row of one) sits in, up or down, until it shows: Tab reaches the ones
 * scrolled out of view too, and Enter would press a button nobody can see. */
export function reveal(n: Renderable) {
  for (let p = n.parent as Renderable | null; p; p = p.parent as Renderable | null) {
    if (!(p instanceof ScrollBoxRenderable)) continue
    const top = p.viewport.y
    const bottom = top + p.viewport.height
    // above the area: up to its first row; below it: down until its last row shows, but never past its first
    const y = n.y < top ? n.y - top : n.y + n.height > bottom ? Math.min(n.y + n.height - bottom, n.y - top) : 0
    if (y) p.scrollBy({ x: 0, y })
  }
}

function isShown(n: Renderable): boolean {
  for (let p: Renderable | null = n; p; p = p.parent as Renderable | null) if (!p.visible) return false
  return true
}

export const FocusContext = createContext<FocusManager>(new FocusManager())
export const ScopeContext = createContext<string>("app")
export const ThemeContext = createContext<Theme>(DASH)

export function useTheme(): Theme {
  return useContext(ThemeContext)
}

export function useFocusManager(): FocusManager {
  return useContext(FocusContext)
}

/** Take part in Tab order as `id`: returns whether this has the keyboard, and a function that gives it the keyboard.
 * Put `ref` on the renderable that marks where it is. */
export function useFocus(
  id: string,
  opts: { onKey?: (key: KeyEvent) => boolean; input?: boolean; disabled?: boolean; autoFocus?: boolean; before?: string } = {},
) {
  const fm = useFocusManager()
  const scope = useContext(ScopeContext)
  const ref = useRef<BoxRenderable | null>(null)
  const latest = useRef(opts)
  latest.current = opts
  useEffect(
    () =>
      fm.register({
        id,
        scope,
        node: () => ref.current,
        onKey: (key) => latest.current.onKey?.(key) ?? false,
        input: opts.input,
        disabled: () => Boolean(latest.current.disabled),
        before: () => latest.current.before,
      }),
    [fm, id, scope, opts.input],
  )
  useEffect(() => {
    if (opts.autoFocus) fm.focus(id)
  }, [fm, id, opts.autoFocus])
  const focused = useSyncExternalStore(fm.subscribe, () => fm.focused === id && fm.scope === scope)
  return { ref, focused, focus: () => fm.focus(id) }
}

/** A dialog's own Tab order: while it's open, Tab stays inside it. */
export function FocusScope({ name, children }: { name: string; children: ReactNode }) {
  const fm = useFocusManager()
  useEffect(() => {
    fm.pushScope(name)
    return () => fm.popScope(name)
  }, [fm, name])
  return <ScopeContext.Provider value={name}>{children}</ScopeContext.Provider>
}
