// The manifest: the id the app allows by name, and no more reach than the contract gives.
import crypto from "node:crypto"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { EXTENSION_ID } from "./fake_app.mjs"
import { EXTENSION_DIR, equal, ok } from "./harness.mjs"
import { ACCOUNT_SHOWN, PACKED_HOOKS, PLATFORMS } from "../shared/consts.js"

const manifest = JSON.parse(readFileSync(join(EXTENSION_DIR, "manifest.json"), "utf8"))
const selectors = JSON.parse(readFileSync(join(EXTENSION_DIR, "selectors.json"), "utf8"))
const source = name => readFileSync(join(EXTENSION_DIR, name), "utf8")

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
