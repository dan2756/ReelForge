// Serves the fixture pages on the platforms' real origins.
//
// The browser under test is started with resolver rules that send www.tiktok.com, x.com and the others to this
// server on 127.0.0.1, so the extension sees the real addresses (its host permissions, its checks on a job's
// upload page) while no request ever leaves the computer. The certificate is made on the spot and thrown
// away; the browser is told to accept it.
import https from "node:https"
import crypto from "node:crypto"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { loginPage, tiktokContent, emptyPage, sitePage } from "./fixtures/pages.mjs"

export const HOSTS = ["www.tiktok.com", "www.youtube.com", "studio.youtube.com", "www.instagram.com", "x.com", "reelforge.tools", "accounts.google.com"]

const fixture = name => readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), "utf8")

// A self-signed certificate, written out byte by byte so the tests need neither openssl nor a key in the repo.
function certificate() {
  const length = n => (n < 128 ? Buffer.from([n]) : Buffer.from([0x80 | Math.ceil(Math.log2(n + 1) / 8), ...(n > 255 ? [n >> 8, n & 255] : [n])]))
  const tlv = (tag, ...parts) => {
    const body = Buffer.concat(parts)
    return Buffer.concat([Buffer.from([tag]), length(body.length), body])
  }
  const sequence = (...parts) => tlv(0x30, ...parts)
  const oid = hex => tlv(0x06, Buffer.from(hex, "hex"))
  const time = date => tlv(0x17, Buffer.from(date.toISOString().replace(/[-:T]/g, "").slice(2, 14) + "Z"))
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ec", { namedCurve: "P-256" })
  const ecdsaWithSha256 = sequence(oid("2a8648ce3d040302"))
  const name = sequence(tlv(0x31, sequence(oid("550403"), tlv(0x0c, Buffer.from("ReelForge extension tests")))))
  const body = sequence(
    tlv(0xa0, tlv(0x02, Buffer.from([2]))),
    tlv(0x02, Buffer.concat([Buffer.from([1]), crypto.randomBytes(8)])),
    ecdsaWithSha256, name,
    sequence(time(new Date(Date.now() - 864e5)), time(new Date(Date.now() + 7 * 864e5))),
    name,
    publicKey.export({ type: "spki", format: "der" }),
  )
  const der = sequence(body, ecdsaWithSha256, tlv(0x03, Buffer.from([0]), crypto.sign("sha256", body, privateKey)))
  return {
    cert: `-----BEGIN CERTIFICATE-----\n${der.toString("base64").match(/.{1,64}/g).join("\n")}\n-----END CERTIFICATE-----\n`,
    key: privateKey.export({ type: "pkcs8", format: "pem" }),
  }
}

export class FixtureSite {
  constructor() {
    // One word per platform says which copy of its page to serve: "ok", "signed_out", "captcha" and so on.
    this.mode = {}
    this.hits = []
    this.pages = { tiktok: fixture("tiktok.html"), youtube: fixture("youtube.html"), instagram: fixture("instagram.html"), x: fixture("x.html") }
    this.common = fixture("common.js")
  }

  reset() {
    this.mode = {}
    this.hits = []
  }

  async start() {
    this.server = https.createServer(certificate(), (request, response) => this.handle(request, response))
    await new Promise(resolve => this.server.listen(0, "127.0.0.1", resolve))
    this.port = this.server.address().port
    return this
  }

  async stop() {
    this.server.closeAllConnections()
    await new Promise(resolve => this.server.close(resolve))
  }

  // The flags that make a browser see this server as the seven real hosts.
  get browserFlags() {
    return [`--host-resolver-rules=${HOSTS.map(host => `MAP ${host} 127.0.0.1:${this.port}`).join(", ")}`, "--ignore-certificate-errors"]
  }

  handle(request, response) {
    const host = request.headers.host
    const path = request.url.split("?")[0]
    this.hits.push(host + path)
    const send = (status, body, type = "text/html; charset=utf-8", headers = {}) => {
      response.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store", ...headers })
      response.end(body)
    }
    const redirect = to => send(302, "", "text/plain", { Location: to })
    const page = platform => send(200, this.pages[platform].replace("__MODE__", this.mode[platform] || "ok"))
    const out = platform => this.mode[platform] === "signed_out"

    if (path === "/fixture/common.js") return send(200, this.common, "text/javascript")
    if (host === "www.tiktok.com") {
      if (path === "/tiktokstudio/upload") return out("tiktok") ? redirect("https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Ftiktokstudio%2Fupload") : page("tiktok")
      if (path === "/tiktokstudio/content") return send(200, tiktokContent)
      if (path === "/login") return send(200, loginPage("TikTok"))
    }
    if (host === "www.youtube.com" && path === "/upload") {
      // The real address redirects into Studio, or to Google's sign-in when nobody is signed in.
      return redirect(out("youtube") ? "https://accounts.google.com/ServiceLogin?service=youtube" : "https://studio.youtube.com/channel/UCfixture000000000000000/videos/upload?d=ud")
    }
    if (host === "studio.youtube.com" && path.startsWith("/channel/")) return page("youtube")
    if (host === "accounts.google.com" && path === "/ServiceLogin") return send(200, loginPage("Google"))
    if (host === "www.instagram.com" && path === "/") return page("instagram")
    if (host === "x.com") {
      if (path === "/compose/post") return out("x") ? redirect("https://x.com/i/flow/login") : page("x")
      if (path === "/i/flow/login") return send(200, loginPage("X"))
    }
    if (host === "reelforge.tools" && path === "/") return send(200, sitePage)
    return send(404, emptyPage)
  }
}
