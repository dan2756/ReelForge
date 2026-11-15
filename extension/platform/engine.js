// The tools every platform script uses. This file is put into a tab only when the worker opened that tab for
// a job; it does nothing by itself.
//
// Elements are found through the hooks in selectors.json. A hook is an ordered list of ways to find one
// element: stable attributes and ids first, then role and name, then position relative to something already
// found, then visible text. Each way has its own wait. Open shadow roots and same-origin iframes are searched
// too. A way that matches two elements is not a match: guessing between two buttons is how the wrong one gets
// pressed. Which way worked is remembered and reported, so a page change shows up before it breaks anything.
(() => {
  if (globalThis.__reelforge) return
  const rf = (globalThis.__reelforge = { flows: {}, used: {}, sel: { hooks: {} }, job: null, phase: "start", stopped: false })

  // Thrown to end the job's work in this tab, with one of the failure codes the app knows.
  class Stop extends Error {
    constructor(code, message, more) {
      super(message)
      this.code = code
      Object.assign(this, more)
    }
  }
  rf.Stop = Stop

  const list = value => (Array.isArray(value) ? value : value == null ? [] : [value])
  const clean = text => String(text ?? "").replace(/[​-‍﻿]/g, "").replace(/\s+/g, " ").trim()
  const lower = text => clean(text).toLowerCase()
  rf.clean = clean

  // ---- finding ---------------------------------------------------------------------------------------------

  let scanned = null

  // The document, plus every open shadow root and same-origin iframe inside it (or inside one element).
  function roots(from) {
    if (!from && scanned) return scanned
    const found = [from || document]
    if (from?.shadowRoot) found.push(from.shadowRoot)
    for (let i = 0; i < found.length; i++) {
      for (const el of found[i].querySelectorAll("*")) {
        if (el.shadowRoot) found.push(el.shadowRoot)
        else if (el.localName === "iframe") {
          let inner = null
          try {
            inner = el.contentDocument
          } catch {
            // Another site's frame: not ours to read.
          }
          if (inner?.documentElement) found.push(inner)
        }
      }
    }
    return found
  }

  // One look at the page can ask for many hooks. The scan for shadow roots and frames is done once per look.
  function oneLook(work) {
    if (scanned) return work()
    scanned = roots()
    try {
      return work()
    } finally {
      scanned = null
    }
  }

  function visible(el) {
    if (!el.isConnected) return false
    const style = el.ownerDocument.defaultView?.getComputedStyle(el)
    if (!style || style.visibility === "hidden" || style.display === "none") return false
    return el.getClientRects().length > 0
  }
  rf.visible = visible

  rf.enabled = el => !el.disabled && !el.hasAttribute("disabled")
    && el.getAttribute("aria-disabled") !== "true" && el.getAttribute("data-disabled") !== "true"

  rf.checked = el => Boolean(el) && (el.checked === true || el.hasAttribute("checked") || el.getAttribute("aria-checked") === "true")

  const CLICKABLE = "button, [role='button'], a"
  const IMPLICIT = {
    button: "button, input[type='button'], input[type='submit']",
    textbox: "textarea, input[type='text'], input:not([type]), [contenteditable='true'], [contenteditable='']",
    dialog: "dialog",
    link: "a[href]",
    radio: "input[type='radio']",
    heading: "h1, h2, h3, h4, h5, h6",
  }

  // Everything a person or a screen reader could call this element.
  function names(el) {
    const out = [el.getAttribute("aria-label")]
    const by = el.getAttribute("aria-labelledby")
    if (by) out.push(by.split(/\s+/).map(id => el.getRootNode().getElementById?.(id)?.textContent || "").join(" "))
    for (const attribute of ["aria-placeholder", "placeholder", "data-placeholder", "title", "alt"]) out.push(el.getAttribute(attribute))
    // What is typed in a text box is its value, not its name.
    if (!el.isContentEditable && !/^(textarea|input)$/.test(el.localName)) out.push(el.textContent)
    for (const picture of el.querySelectorAll("svg[aria-label], img[alt]")) out.push(picture.getAttribute("aria-label") || picture.getAttribute("alt"))
    return out.map(lower).filter(Boolean)
  }

  // The text a person can see in a document, a shadow root or one element. textContent would also read scripts
  // and hidden translation tables.
  function seenText(scope) {
    if (scope.body) return scope.body.innerText
    if (typeof scope.innerText === "string") return scope.innerText
    return [...scope.children].filter(el => !/^(style|script|template)$/.test(el.localName)).map(el => el.innerText).join(" ")
  }

  function select(scope, way) {
    try {
      if (way.page_text) {
        const text = lower(seenText(scope))
        return list(way.page_text).some(part => text.includes(lower(part))) ? [scope.body || scope.host || scope] : []
      }
      const fits = (have, wanted) => wanted.some(want => (way.match === "contains" ? have.includes(want) : have === want))
      let found
      if (way.role) {
        if (!/^[a-z]+$/.test(way.role)) return []
        found = [...scope.querySelectorAll(`[role='${way.role}']` + (IMPLICIT[way.role] ? ", " + IMPLICIT[way.role] : ""))]
        if (way.name) {
          const wanted = list(way.name).map(lower)
          found = found.filter(el => names(el).some(have => fits(have, wanted)))
        }
      } else {
        found = [...scope.querySelectorAll(way.css || CLICKABLE)]
        if (way.text) {
          const wanted = list(way.text).map(lower)
          found = found.filter(el => fits(lower(el.textContent), wanted))
        }
      }
      // A button inside a button-like wrapper would count twice: keep the inner one.
      if (way.role || way.text) found = found.filter(el => !found.some(other => other !== el && el.contains(other)))
      if (way.closest) found = found.map(el => el.closest(way.closest)).filter(Boolean)
      return found
    } catch {
      // A selector this browser cannot read is simply a way that found nothing.
      return []
    }
  }

  function collect(scopes, hook, way) {
    const out = new Set()
    for (const scope of scopes) {
      for (const el of select(scope, way)) if (hook.visible === false || visible(el)) out.add(el)
    }
    return [...out]
  }

  // Every element one way finds right now. "within" looks inside another hook's element; "near" climbs from
  // another hook's element to the closest ancestor that holds a match.
  function matches(hook, way, depth) {
    if (way.within) {
      const anchor = rf.probe(way.within, depth + 1)
      return anchor ? collect(roots(anchor), hook, way) : []
    }
    if (way.near) {
      for (let up = rf.probe(way.near, depth + 1)?.parentElement; up; up = up.parentElement) {
        const got = collect(roots(up), hook, way)
        if (got.length) return got
      }
      return []
    }
    return collect(roots(), hook, way)
  }

  function pick(hook, way, got) {
    if (hook.any ? got.length > 0 : got.length === 1) return got[0]
    // "pick" is for the few places where the sources say "the first of these" and nothing better exists.
    return Number.isInteger(way.pick) && got.length > way.pick ? got[way.pick] : null
  }

  // A look without waiting: the hook's element if any way finds it now, else null.
  rf.probe = (name, depth = 0) => {
    const hook = rf.sel.hooks[name]
    if (!hook || depth > 3) return null
    return oneLook(() => {
      for (const way of hook.strategies) {
        const el = pick(hook, way, matches(hook, way, depth))
        if (el) return el
      }
      return null
    })
  }

  const WATCH = { subtree: true, childList: true, attributes: true, characterData: true }

  // Waits for something on the page. A MutationObserver on the document and on every shadow root and frame
  // there is now wakes it on each change (at most once per `pace` ms); the slow timer covers roots that appear
  // later. Every look also checks that the page has not turned into a sign-in page, a verification or an error.
  rf.waitFor = (test, { timeout = 15000, step, pace = 60 } = {}) => new Promise((resolve, reject) => {
    let over = false
    let soon = null
    const end = (settle, value) => {
      if (over) return
      over = true
      observer.disconnect()
      clearTimeout(soon)
      clearInterval(slow)
      clearTimeout(limit)
      settle(value)
    }
    const look = () => {
      soon = null
      if (over) return
      try {
        const value = oneLook(() => {
          rf.guard()
          return test()
        })
        if (value) end(resolve, value)
      } catch (error) {
        end(reject, error)
      }
    }
    const observer = new MutationObserver(() => {
      soon ||= setTimeout(look, pace)
    })
    for (const root of roots()) observer.observe(root, WATCH)
    const slow = setInterval(look, Math.max(400, pace))
    const limit = setTimeout(() => end(reject, new Stop("timeout", `Nothing happened on ${rf.job.name}'s page in time. The tab stays open: you can finish by hand there.`, { step })), timeout)
    look()
  })

  // The hook's element, waited for. Every way is tried on each look, best first, each for its own time (the
  // hook's timeout_ms unless the way has one). The best way wins at once. A weaker way that finds the element
  // is held back for a moment first, in case the page is still arriving and a better way is about to match.
  // The way that won is remembered. When none does, the failure says how many elements each one found.
  const GRACE = 1500
  rf.find = async name => {
    const hook = rf.sel.hooks[name]
    const ways = hook?.strategies || []
    const wait = way => way.timeout_ms || hook.timeout_ms || 10000
    const counts = ways.map(() => 0)
    const started = Date.now()
    let weaker = null
    try {
      const won = await rf.waitFor(() => {
        for (const [index, way] of ways.entries()) {
          if (Date.now() - started > wait(way)) continue
          const got = matches(hook, way, 0)
          counts[index] = got.length
          const el = pick(hook, way, got)
          if (!el) continue
          if (index === 0) return { el, index }
          if (weaker?.index !== index) weaker = { index, since: Date.now() }
          return Date.now() - weaker.since >= GRACE ? { el, index } : null
        }
        weaker = null
        return null
      }, { timeout: Math.max(0, ...ways.map(wait)) })
      rf.used[name] = ways[won.index].id || String(won.index)
      if (won.index > 0) rf.fallback = true
      return won.el
    } catch (error) {
      if (error.code !== "timeout") throw error
      throw new Stop("hook_not_found", `${rf.job.name} has changed its page: ReelForge could not find ${hook?.label || name.replace(/_/g, " ")}. You can finish by hand in this tab.`,
        { detail: `hook=${name} tried=${ways.map((way, index) => `${way.id || index}:${counts[index]}`).join(",") || "nothing"}` })
    }
  }

  // A value read off a hook's element: an attribute or its text, cut down by the hook's pattern.
  rf.read = name => {
    const el = rf.probe(name)
    const how = rf.sel.hooks[name]?.read
    if (!el || !how) return null
    const value = how.attr ? el.getAttribute(how.attr) : clean(el.innerText)
    if (!value || !how.pattern) return value || null
    try {
      return new RegExp(how.pattern).exec(value)?.[1] || null
    } catch {
      return null
    }
  }

  // A sign is a page-level fact: signed out, a verification, the platform's "it's posted". It shows either in
  // the address (host and path only: a query string may quote some other address) or as a hook on the page.
  rf.sign = name => {
    const sign = rf.sel[name]
    if (!sign) return false
    const where = location.host + location.pathname
    return list(sign.url).some(part => where.includes(part)) || list(sign.hooks).some(hook => rf.probe(hook))
  }

  rf.guard = () => {
    if (rf.stopped) throw new Stop("stopped", "stopped")
    // Once everything is filled in, what the page shows is for the person to deal with.
    if (rf.phase === "watch") return
    if (rf.sign("challenge")) throw new Stop("challenge", `${rf.job.name} is asking you to verify. ReelForge leaves that to you: finish it in this tab.`)
    const error = clean(rf.probe("error")?.innerText)
    if (error) throw new Stop("platform_error", `${rf.job.name} says: ${error.slice(0, 200)}`)
    if (rf.phase === "start" && rf.sign("signed_out")) throw new Stop("not_signed_in", `This browser is not signed in to ${rf.job.name}. Sign in, then post again.`)
  }

  // ---- doing -----------------------------------------------------------------------------------------------

  // True for the platform's own final button (Post, Publish, Share), and for anything around or inside it.
  // The rules are written in each platform's script, not in selectors.json: the hook for the final button, a
  // fixed selector where the sources give one, and the words the button carries.
  rf.isFinal = el => {
    const flow = rf.flows[rf.job.platform]
    const button = rf.probe(flow.final)
    if (button && (button === el || button.contains(el) || el.contains(button))) return true
    if (flow.last.css && (el.closest(flow.last.css) || el.querySelector(flow.last.css))) return true
    return names(el).some(name => flow.last.names.includes(name))
  }

  // A click on the way to the final screen (Next, Create, OK). It is never a click on the final button,
  // whatever a hook found: a list of hooks that pointed "Next" at Publish would otherwise publish with nobody
  // having asked for it. The one place the final button is pressed is rf.pressFinal, which run.js calls only
  // with the person's switch, their yes and ReelForge's grant.
  rf.click = el => {
    if (rf.isFinal(el)) {
      throw new Stop("hook_not_found", `${rf.job.name} has changed its page: a step on the way led to its ${rf.flows[rf.job.platform].button} button, which ReelForge did not press. You can finish by hand in this tab.`,
        { detail: "a step's hook found the final button" })
    }
    el.click()
  }

  rf.pressFinal = el => el.click()

  rf.readText = el => (/^(textarea|input)$/.test(el.localName) ? el.value : el.innerText)

  const sameText = (a, b) => clean(a) === clean(b)

  // True once `test` holds and still holds a moment later. An editor that keeps its own copy of the text may
  // show what was typed for an instant and then put its own text back.
  async function settles(test, wait = 1500) {
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
    for (const end = Date.now() + wait; ;) {
      if (test()) {
        await pause(200)
        if (test()) return true
      }
      if (Date.now() > end) return false
      await pause(50)
    }
  }

  // Puts text in a box in one go, replacing what was there (YouTube and TikTok start with the file's name),
  // and reads it back. Rich editors keep their own copy of the text, so writing to the DOM is not enough: the
  // text is inserted the way typing inserts it, and if the editor does not take that, the way a paste does.
  // Nothing touches the real clipboard. Returns how it worked, or null when the box did not take the text.
  rf.setText = async (el, text) => {
    const doc = el.ownerDocument
    el.focus()
    if (/^(textarea|input)$/.test(el.localName)) {
      el.value = text
      el.dispatchEvent(new Event("input", { bubbles: true }))
      el.dispatchEvent(new Event("change", { bubbles: true }))
      return sameText(el.value, text) ? "value" : null
    }
    doc.getSelection().selectAllChildren(el)
    // An empty text (a YouTube description with no hashtags, say) means the box is to be left empty.
    if (text === "") {
      if (clean(rf.readText(el))) doc.execCommand("delete")
      return (await settles(() => sameText(rf.readText(el), ""))) ? "cleared" : null
    }
    doc.execCommand("insertText", false, text)
    if (await settles(() => sameText(rf.readText(el), text))) return "typed"
    el.focus()
    doc.getSelection().selectAllChildren(el)
    const data = new DataTransfer()
    data.setData("text/plain", text)
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }))
    return (await settles(() => sameText(rf.readText(el), text))) ? "pasted" : null
  }

  function bytesOf(b64) {
    const text = atob(b64)
    const bytes = new Uint8Array(text.length)
    for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i)
    return bytes
  }

  // Gets the job's video from the worker, a piece at a time, and checks it is the file the person confirmed:
  // same size, same SHA-256. This script never talks to ReelForge itself; a request from here would be made
  // as the platform's page.
  rf.fetchVideo = async onPiece => {
    const { size, name, sha256 } = rf.job.video
    const PIECE = 4 * 1024 * 1024
    const parts = []
    let port = null
    let waiting = null
    let drops = 0
    const settle = (how, value) => {
      const now = waiting
      waiting = null
      now?.[how](value)
    }
    const ask = (offset, length) => new Promise((resolve, reject) => {
      waiting = { resolve, reject }
      try {
        if (!port) {
          port = chrome.runtime.connect({ name: "rf-file" })
          port.onMessage.addListener(message => settle("resolve", message))
          port.onDisconnect.addListener(() => {
            port = null
            settle("reject", new Error("dropped"))
          })
        }
        port.postMessage({ offset, length })
      } catch (error) {
        port = null
        settle("reject", error)
      }
    })
    for (let offset = 0; offset < size;) {
      rf.guard()
      let answer
      try {
        answer = await ask(offset, Math.min(PIECE, size - offset))
      } catch {
        // The worker was restarted mid-transfer. A new connection wakes it, and it answers from this offset.
        if (++drops > 5) throw new Stop("app_unreachable", "The extension stopped answering while the video was handed over. Nothing was posted.")
        await new Promise(resolve => setTimeout(resolve, 250))
        continue
      }
      if (answer.t === "error") throw new Stop(answer.code === "cancelled" ? "stopped" : answer.code, answer.message)
      const bytes = bytesOf(answer.b64)
      if (answer.offset !== offset || !bytes.length) throw new Stop("changed", "The video did not arrive whole. Nothing was posted.")
      parts.push(bytes)
      offset += bytes.length
      onPiece(offset / size)
    }
    port?.disconnect()
    const file = new File(parts, name, { type: "video/mp4", lastModified: Date.now() })
    const wrong = new Stop("changed", "The video that arrived is not the one you confirmed. Nothing was posted.")
    if (file.size !== size) throw wrong
    // crypto.subtle only exists on https pages, which all four upload pages are.
    if (crypto.subtle && sha256) {
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))
      if ([...digest].map(byte => byte.toString(16).padStart(2, "0")).join("") !== sha256) throw wrong
    }
    return file
  }

  // Gives the file to the page's own file input, the way picking it in the file dialog would. Setting
  // input.files fires nothing, so the two events a real pick sends are sent by hand. The check comes before
  // them: many pages empty the input inside their own change handler once they have the file.
  rf.attach = (input, file) => {
    const data = new DataTransfer()
    data.items.add(file)
    input.files = data.files
    const took = input.files.length === 1 && input.files[0].size === file.size
    if (took) {
      input.dispatchEvent(new Event("input", { bubbles: true }))
      input.dispatchEvent(new Event("change", { bubbles: true }))
    }
    return took
  }

  // ---- showing ---------------------------------------------------------------------------------------------

  const NOTE_CSS = `
    .card { box-sizing: border-box; width: 300px; padding: 12px 14px; border-radius: 10px; background: #18191c; color: #e8e8e6;
      border: 1px solid #2c2e33; border-left: 4px solid #a6a7ab; box-shadow: 0 8px 28px rgba(0, 0, 0, .45);
      font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .card[data-tone="ready"] { border-left-color: #e8213a; }
    .card[data-tone="good"] { border-left-color: #3dff9a; }
    .card[data-tone="bad"], .card[data-tone="warn"] { border-left-color: #e8213a; }
    .top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; color: #a6a7ab; font-size: 11px;
      letter-spacing: .06em; text-transform: uppercase; }
    button { all: unset; cursor: pointer; padding: 0 4px; border-radius: 4px; color: #a6a7ab; font-size: 12px; text-transform: none; letter-spacing: 0; }
    button:hover { color: #e8e8e6; }
    button:focus-visible { outline: 2px solid #3dff9a; outline-offset: 2px; }
    .line { font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
    .card[data-tone="ready"] .line { font-size: 15px; }
    .more { margin-top: 4px; color: #a6a7ab; overflow-wrap: anywhere; }
    .more[hidden] { display: none; }
  `

  // The small note in the corner of the upload page. It lives in a shadow root with its own style sheet, so the
  // page's styles cannot reach it and its styles cannot leak out. The root is closed: the note can name the
  // account ReelForge has for the platform, which the page's own scripts were never told. It is built node by
  // node: some of these pages forbid HTML from strings.
  let note = null
  rf.say = (line, tone = "work", more = "") => {
    if (!note) {
      const host = document.createElement("div")
      host.setAttribute("data-reelforge-note", "")
      for (const [property, value] of [["all", "initial"], ["position", "fixed"], ["right", "16px"], ["bottom", "16px"], ["z-index", "2147483647"]]) {
        host.style.setProperty(property, value, "important")
      }
      const root = host.attachShadow({ mode: "closed" })
      const sheet = new CSSStyleSheet()
      sheet.replaceSync(NOTE_CSS)
      root.adoptedStyleSheets = [sheet]
      const make = (tag, name, text) => {
        const el = document.createElement(tag)
        el.className = name
        if (text) el.textContent = text
        return el
      }
      const card = make("div", "card")
      card.setAttribute("role", "status")
      card.setAttribute("aria-live", "polite")
      const top = make("div", "top")
      const hide = make("button", "", "Hide")
      hide.type = "button"
      hide.addEventListener("click", () => host.remove())
      top.append(make("span", "", "ReelForge"), hide)
      note = { host, card, line: make("div", "line"), more: make("div", "more") }
      card.append(top, note.line, note.more)
      root.append(card)
    }
    if (!note.host.isConnected) document.documentElement.append(note.host)
    note.card.dataset.tone = tone
    note.line.textContent = line
    note.more.textContent = more
    note.more.hidden = !more
  }

  // Scrolls the button the person should press into view and rings it in ReelForge's red.
  let ringed = null
  rf.outline = el => {
    rf.unoutline()
    el.scrollIntoView({ block: "center", inline: "nearest" })
    ringed = { el, outline: el.style.getPropertyValue("outline"), offset: el.style.getPropertyValue("outline-offset") }
    el.style.setProperty("outline", "3px solid #e8213a", "important")
    el.style.setProperty("outline-offset", "3px", "important")
  }
  rf.unoutline = () => {
    if (!ringed) return
    ringed.el.style.setProperty("outline", ringed.outline)
    ringed.el.style.setProperty("outline-offset", ringed.offset)
    ringed = null
  }

  // ---- telling the worker ----------------------------------------------------------------------------------

  // A message to the worker. The browser may stop the worker in the middle of an answer; the next try starts
  // it again, and everything said here is safe to say twice. No answer after three tries, or an extension that
  // was reloaded or removed under this page, means there is nobody left to work for.
  rf.send = async message => {
    for (let tries = 0; tries < 3 && chrome.runtime?.id; tries++) {
      try {
        return await chrome.runtime.sendMessage(message)
      } catch {
        await new Promise(resolve => setTimeout(resolve, 300))
      }
    }
    return null
  }

  let reported = {}

  // One report to the worker, which keeps it and passes it to ReelForge. The ways that found the hooks since
  // the last report ride along.
  rf.report = async event => {
    const fresh = Object.entries(rf.used).filter(([hook, way]) => reported[hook] !== way)
    reported = { ...rf.used }
    const answer = await rf.send({ t: "job.event", ...event, strategy: fresh.map(([hook, way]) => `${hook}=${way}`).join(",") || undefined })
    if (!answer || answer.stop) rf.stopped = true
    return answer
  }

  // A step the person can read: the note in the corner says it, and so does ReelForge.
  rf.step = async (state, step, line, more) => {
    rf.stepNow = step
    if (line) rf.say(line)
    await rf.report({ state, step, ...more })
    rf.guard()
  }

  // Fills one box and proves it. A box that will not take the text is a page that changed.
  rf.fill = async (step, hook, text, line) => {
    const el = await rf.find(hook)
    await rf.step("filling", step, line)
    const how = await rf.setText(el, text)
    if (!how) {
      throw new Stop("hook_not_found", `${rf.job.name}'s ${rf.sel.hooks[hook]?.label || "text box"} did not take the text. You can paste it in by hand.`,
        { detail: `hook=${hook} typed and pasted, neither stayed` })
    }
    rf.used[hook] += "+" + how
    return el
  }

  // The fraction in an "Uploading 42%" kind of label, sent at most once a second and only when it moves.
  let lastPercent = -1
  let lastAt = 0
  rf.progress = (state, step, fraction) => {
    const percent = Math.floor(fraction * 100)
    if (percent === lastPercent || Date.now() - lastAt < 1000) return
    lastPercent = percent
    lastAt = Date.now()
    rf.report({ state, step, progress: fraction })
  }
})()
