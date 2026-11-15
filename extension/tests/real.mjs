// The real ReelForge server and ledger, for the tests that run the extension against it (real.test.mjs).
//
// The stand-in (fake_app.mjs) is quick and lets a test set up anything, but it is a second copy of the rules,
// and two copies drift. Here the Python side is the real one (dashboard/extension_api.py, dashboard/posting.py),
// started by real_app.py on a scratch channel: nothing of the person's is read, and no site is reached.
import { spawn } from "node:child_process"
import { createInterface } from "node:readline"
import { existsSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const KIT = dirname(dirname(HERE))

// The Python that has ReelForge's packages: the one named, else the ReelForge folder's own.
export function python() {
  const named = process.env.REELFORGE_PYTHON
  if (named) return named
  for (const path of [join(KIT, ".venv", "bin", "python"), join(KIT, ".venv", "Scripts", "python.exe")]) {
    if (existsSync(path)) return path
  }
  return null
}

export class RealApp {
  constructor() {
    this.child = null
    this.port = null
    this.waiting = []
    this.problems = ""
  }

  async start() {
    this.child = spawn(python(), ["-B", join(HERE, "real_app.py"), KIT], { cwd: KIT, stdio: ["pipe", "pipe", "pipe"] })
    this.child.stderr.on("data", chunk => { this.problems += chunk })
    this.child.once("exit", () => { for (const waiter of this.waiting.splice(0)) waiter({ error: "the server stopped: " + this.problems.slice(-600) }) })
    createInterface({ input: this.child.stdout }).on("line", line => {
      let said
      try {
        said = JSON.parse(line)
      } catch {
        return  // not an answer
      }
      this.waiting.shift()?.(said)
    })
    const first = await new Promise(resolve => this.waiting.push(resolve))
    if (first.error) throw new Error(first.error)
    this.port = first.port
    return this
  }

  // One command to real_app.py, and its answer.
  async ask(name, args = {}) {
    const answer = await new Promise(resolve => {
      this.waiting.push(resolve)
      this.child.stdin.write(JSON.stringify({ do: name, ...args }) + "\n")
    })
    if (answer.error) throw new Error(`${name}: ${answer.error}`)
    return answer.ok
  }

  async stop() {
    if (!this.child || this.child.exitCode !== null) return
    const gone = new Promise(resolve => this.child.once("exit", resolve))
    this.child.stdin.end()
    await Promise.race([gone, new Promise(resolve => setTimeout(resolve, 5000))])
    if (this.child.exitCode === null) this.child.kill()
  }

  // What the tests ask for most.
  pairUrl() { return this.ask("pair_url") }
  job(id) { return this.ask("job", { id }) }
  async state(id) { return (await this.job(id)).state }
}
