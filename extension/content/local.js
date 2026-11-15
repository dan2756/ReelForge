// Runs on ReelForge's own two pages on this computer (http://127.0.0.1:<port>/pair and /post/<batch>) and
// nowhere else. It tells the page the extension is here, then hands the worker what the page carries: the
// pairing secret, or the batch to start. The page never gets anything back but a state word.
(() => {
  const PROTOCOL = 1
  const root = document.documentElement
  const page = root.dataset.rfPage
  // Some other program's page on 127.0.0.1 that happens to live at /pair or /post/: leave it alone.
  if (!page) return
  const version = chrome.runtime.getManifest().version

  const announce = (name, attribute, state) => {
    if (attribute) root.setAttribute(attribute, state)
    window.dispatchEvent(new CustomEvent(name, { detail: attribute ? { state } : { version, protocol: PROTOCOL } }))
  }

  root.setAttribute("data-rf-extension", version)
  announce("reelforge:extension")

  const port = Number(root.dataset.rfPort)
  const ask = async message => {
    // The page must be the app it says it is: same port as the address bar, same protocol as this extension.
    if (String(port) !== location.port || location.hostname !== "127.0.0.1") return "error:bad_page"
    if (Number(root.dataset.rfProtocol) !== PROTOCOL) return Number(root.dataset.rfProtocol) > PROTOCOL ? "error:extension_outdated" : "error:protocol"
    try {
      const answer = await chrome.runtime.sendMessage(message)
      return String(answer?.state || "error:no_answer")
    } catch {
      return "error:no_answer"
    }
  }

  if (page === "pair") {
    ask({ t: "pair", secret: root.dataset.rfSecret || "" }).then(code => {
      announce("reelforge:pair", "data-rf-pair", code === "ok" || code.startsWith("error:") ? code : "error:" + code)
    })
  } else if (page === "post") {
    ask({ t: "batch", batch: root.dataset.rfBatch || "" }).then(state => {
      announce("reelforge:batch", "data-rf-batch-state", state)
    })
    // "Press Post for me" for this batch takes a person's own click on the page's button for it. A click made
    // by a script (the page's own, or a program driving the page) is not one, and opening this page is not
    // one either: without the click the extension fills everything in and stops, whatever ReelForge asked for.
    document.addEventListener("click", event => {
      if (!event.isTrusted || !(event.target instanceof Element) || !event.target.closest("[data-rf-press]")) return
      ask({ t: "batch.press", batch: root.dataset.rfBatch || "" }).then(state => {
        announce("reelforge:press", "data-rf-press", state)
      })
    }, true)
  }
})()
