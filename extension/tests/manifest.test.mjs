// The manifest: the id the app allows by name, and no more reach than the contract gives.
import crypto from "node:crypto"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { EXTENSION_ID } from "./fake_app.mjs"
import { EXTENSION_DIR, equal, ok } from "./harness.mjs"
import { ACCOUNT_SHOWN, GUARD_HOOKS, PACKED_HOOKS, PLATFORMS, keptHooks } from "../shared/consts.js"

const manifest = JSON.parse(readFileSync(join(EXTENSION_DIR, "manifest.json"), "utf8"))
const selectors = JSON.parse(readFileSync(join(EXTENSION_DIR, "selectors.json"), "utf8"))
const source = name => readFileSync(join(EXTENSION_DIR, name), "utf8")

// The hooks a newer list never replaces for a platform, as worker/selectors.js works them out from the packed list.
function keptFor(platform) {
  const own = selectors.platforms[platform]
  const named = [own.success, own.challenge, own.signed_out].flatMap(sign => [...(sign?.hooks || []), sign?.gone_after_press]).filter(Boolean)
  return keptHooks(own.hooks, [...PACKED_HOOKS[platform], ...GUARD_HOOKS[platform], ...named])
}

export const tests = {
  // Chrome's rule: SHA-256 of the public key, first 16 bytes, each hex digit moved to the letters a to p.
  "the key gives the id the app expects, and Chrome agrees"({ browser }) {
    const digest = crypto.createHash("sha256").update(Buffer.from(manifest.key, "base64")).digest("hex").slice(0, 32)
    const id = [...digest].map(digit => String.fromCharCode(97 + parseInt(digit, 16))).join("")
    equal(id, EXTENSION_ID, "id worked out from the key")
    equal(browser.id, EXTENSION_ID, "id Chrome gave the loaded extension")
  },

  "it asks for the contract's permissions and nothing else"() {
    equal(manifest.manifest_version, 3, "manifest version")
    equal(manifest.name, "ReelForge", "name")
    equal([...manifest.permissions].sort(), ["alarms", "scripting", "storage"], "permissions")
    equal([...manifest.host_permissions].sort(), [
      "http://127.0.0.1/*", "https://reelforge.tools/*", "https://studio.youtube.com/*", "https://www.instagram.com/*",
      "https://www.tiktok.com/*", "https://www.youtube.com/*", "https://x.com/*",
    ], "host permissions")
    ok(!manifest.optional_permissions && !manifest.optional_host_permissions && !manifest.externally_connectable && !manifest.web_accessible_resources, "no extra reach")
    // Static scripts only on ReelForge's two local pages and on reelforge.tools. Nothing is declared for a platform.
    equal(manifest.content_scripts.map(script => script.matches), [["http://127.0.0.1/pair*", "http://127.0.0.1/post/*"], ["https://reelforge.tools/*"]], "content script matches")
    for (const size of ["16", "32", "48", "128"]) {
      const png = readFileSync(join(EXTENSION_DIR, manifest.icons[size]))
      equal(png.readUInt32BE(16), Number(size), `icon ${size} width`)
    }
  },

  "where the account is said to be checked, the page has a hook for it"() {
    const hooked = Object.keys(selectors.platforms).filter(platform => selectors.platforms[platform].hooks.account)
    equal([...ACCOUNT_SHOWN].sort(), hooked.sort(), "platforms with an account hook")
  },

  async "a newer list from the app replaces no sign, no hook a sign names and no hook that guards a post"({ browser }) {
    // Asked of the extension's own code, on one of its pages: a list one version newer in which every hook is
    // the whole page and every sign says something else.
    const popup = await browser.popup()
    const seen = await popup.evaluate(async () => {
      const sel = await import("/worker/selectors.js")
      const packed = await (await fetch(chrome.runtime.getURL("selectors.json"))).json()
      const theirs = JSON.parse(JSON.stringify(packed))
      theirs.version = packed.version + 1
      for (const platform of Object.values(theirs.platforms)) {
        // (and the hooks the engine reads on every platform, where the packed list has none of them)
        for (const name of [...Object.keys(platform.hooks), "error", "account", "post_link", "post_confirm"]) platform.hooks[name] = { strategies: [{ id: "page", css: "body" }], timeout_ms: 1234 }
        Object.assign(platform, { success: { url: ["/"], hooks: ["file_input"], gone_after_press: "file_input", timeout_ms: 4321 }, challenge: {}, signed_out: {} })
      }
      if (!sel.valid(theirs)) return null
      await chrome.storage.local.set({ selectors: theirs })
      const out = {}
      for (const name of Object.keys(packed.platforms)) out[name] = await sel.selectorsFor(name)
      return out
    })
    ok(seen, "the newer list is one the extension takes")
    for (const platform of Object.keys(PLATFORMS)) {
      const own = selectors.platforms[platform]
      const got = seen[platform]
      equal(got.version, selectors.version + 1, `${platform}: the list in use`)
      equal(got.success, { ...own.success, timeout_ms: 4321 }, `${platform}'s sign that a post went out (only its wait is the app's)`)
      equal([got.challenge, got.signed_out], [own.challenge, own.signed_out], `${platform}'s challenge and signed-out signs`)
      const named = [own.success, own.challenge, own.signed_out].flatMap(sign => [...(sign?.hooks || []), sign?.gone_after_press]).filter(Boolean)
      ok(named.length > 0 && GUARD_HOOKS[platform].includes("captcha"), `${platform} has hooks to keep`)
      for (const hook of keptFor(platform)) {
        // A kept hook the packed list does not have is not taken from a newer list either: the engine reads
        // "error" and "account" on every platform, and one a newer list added would be read there.
        if (!own.hooks[hook]) {
          equal(got.hooks[hook], undefined, `${platform}'s ${hook}: none, as packed`)
          continue
        }
        equal(got.hooks[hook].strategies, own.hooks[hook].strategies, `${platform}'s ${hook}: where it looks`)
      }
      equal(got.hooks.file_input.strategies, [{ id: "page", css: "body" }], `${platform}'s file_input: any other hook is the app's`)
    }
    // The account is compared wherever the page shows it, YouTube's last screen is left alone unless its answers
    // are the channel's own, its own error message stops a job, and Instagram's Share is looked for in its own
    // dialog: those hooks are among the kept ones.
    for (const platform of ACCOUNT_SHOWN) ok(GUARD_HOOKS[platform].includes("account"), `${platform}'s account hook is kept`)
    for (const hook of ["kids_no", "kids_yes", "visibility", "upload_done", "error"]) ok(keptFor("youtube").has(hook), `YouTube's ${hook} is kept`)
    equal(seen.youtube.hooks.error.strategies, selectors.platforms.youtube.hooks.error.strategies, "YouTube's error message: where it looks")
    for (const hook of ["dialog", "reels_notice"]) equal(seen.instagram.hooks[hook].strategies, selectors.platforms.instagram.hooks[hook].strategies, `Instagram's ${hook}: where it looks`)
  },

  "every hook the engine reads by name, and every hook a script reads before the press, is one a newer list cannot replace"() {
    // What a newer list may still change: hooks only typed into or given the video (rf.fill, rf.find), and these
    // steps on the way, which a wrong newer list can only stop: the platform's own final button and its guards
    // are kept, so none of them can lead to a press.
    const steps = {
      tiktok: {},
      youtube: {},
      instagram: { heading: "which screen of the dialog is showing, to see that Next took", caption: "that the caption's screen is there" },
      x: { attached: "that the video is in before the text is typed (X is never pressed)" },
    }
    const reads = text => new Set([...text.matchAll(/rf\.(?:probe|read)\("(\w+)"/g), ...text.matchAll(/rf\.sel\.hooks\.(\w+)/g)].map(match => match[1]))
    // engine.js and run.js run on every platform. Each hook they read by name is kept on every one of them.
    const engine = reads(source("platform/engine.js") + source("platform/run.js"))
    ok(engine.has("error") && engine.has("account"), `the engine's own reads are found (${[...engine]})`)
    for (const platform of Object.keys(PLATFORMS)) {
      for (const hook of engine) ok(GUARD_HOOKS[platform].includes(hook), `${platform}: ${hook}, read by the engine, is kept`)
    }
    // rf.guard() runs on every look before the press: every sign it reads is one of the packed signs.
    const guard = /rf\.guard = \(\) => \{([\s\S]*?)\n  \}/.exec(source("platform/engine.js"))[1]
    ok(reads(guard).has("error"), "rf.guard reads the platform's error message")
    for (const [, sign] of guard.matchAll(/rf\.sign\("(\w+)"\)/g)) ok(["challenge", "signed_out", "success"].includes(sign), `rf.guard's ${sign} sign is a packed one`)
    for (const platform of Object.keys(PLATFORMS)) {
      const script = source(`platform/${platform}.js`)
      const read = reads(script)
      for (const [, hook] of script.matchAll(/(?:final|hold): "(\w+)"/g)) read.add(hook)
      for (const [, hook] of script.matchAll(/\b(visibility_\w+):/g)) read.add(hook)  // (YouTube's choices, read through a list)
      ok(read.size > 0, `${platform}: hooks are found`)
      for (const hook of read) {
        ok(selectors.platforms[platform].hooks[hook], `${platform} has the hook ${hook}`)
        ok(keptFor(platform).has(hook) || steps[platform][hook], `${platform}.js reads ${hook} before the press: it is kept, or a step a newer list may change`)
      }
      for (const hook of Object.keys(steps[platform])) ok(read.has(hook) && !keptFor(platform).has(hook), `${platform}'s ${hook} is still a step the script reads`)
    }
  },

  "every hook a script clicks is one a newer list from the app cannot replace"() {
    const packed = PACKED_HOOKS
    equal(Object.keys(packed).sort(), Object.keys(PLATFORMS).sort(), "platforms")
    for (const platform of Object.keys(PLATFORMS)) {
      const script = source(`platform/${platform}.js`)
      const final = /final: "(\w+)"/.exec(script)[1]
      // rf.click(await rf.find("create")), rf.click(rf.probe("create_post")), and the hooks a loop picks from.
      const clicked = new Set([final])
      for (const [, hook] of script.matchAll(/rf\.click\((?:await )?rf\.(?:find|probe)\("(\w+)"\)\)/g)) clicked.add(hook)
      if (platform === "youtube") clicked.add("next_button")
      if (platform === "instagram") for (const hook of ["reels_ok", "next"]) clicked.add(hook)
      equal([...packed[platform]].sort(), [...clicked].sort(), `${platform}'s clicked hooks`)
      for (const hook of packed[platform]) ok(selectors.platforms[platform].hooks[hook], `${platform} has the hook ${hook}`)
      // And no click in a platform's script goes around the guard.
      ok(!/\.click\(\)/.test(script), `${platform}.js clicks only through rf.click`)
    }
    ok(source("platform/run.js").includes("rf.pressFinal(button)") && !/rf\.click\(/.test(source("platform/run.js")), "run.js presses the final button in one place only")
  },

  "the description says what it needs and what it does"() {
    for (const word of ["ReelForge app", "this computer", "Attaches", "fills in the text", "TikTok", "YouTube", "Instagram", "X", "press Post", "switch that on"]) {
      ok(manifest.description.includes(word), `description mentions "${word}"`)
    }
  },
}
