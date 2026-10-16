// Drives the real streamInstall() from templates/installer_wizard.html with scripted event streams.
// Usage: node stream_install_check.js <path-to-installer_wizard.html>   (exit 0 = all scenarios pass)
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const start = script.indexOf("  function streamInstall");
const end = script.indexOf("  const STATUS_ICON");
if (start < 0 || end < 0) { console.error("could not locate streamInstall in the page"); process.exit(2); }

class ClassList {
  constructor() { this.s = new Set(["is-pending"]); }
  remove(...a) { a.forEach((x) => this.s.delete(x)); }
  add(...a) { a.forEach((x) => this.s.add(x)); }
  contains(x) { return this.s.has(x); }
}
function mkRow(matchFn) {
  const ring = { textContent: "" };
  return { el: { classList: new ClassList(), querySelector: () => ring }, stateEl: { textContent: "queued" }, ring, match: matchFn };
}
let lastES;
class EventSource {
  constructor() { this.handlers = {}; lastES = this; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  close() { this.closed = true; }
}
const streamInstall = new Function("EventSource", "logLine", "revealDetailsOnFailure",
  script.slice(start, end) + "\nreturn streamInstall;")(EventSource, () => {}, () => {});

function scenario(frames, closeConnection = true) {
  const rows = {
    venv: mkRow((c) => c.includes(" -m venv")),
    pip: mkRow((c) => c.includes("--upgrade pip")),
  };
  let result = null;
  streamInstall("/x", {}, {}, rows, (ok) => { result = ok; });
  frames.forEach((f) => lastES.onmessage({ data: f }));
  if (closeConnection) lastES.handlers.error();
  const state = (r) => ["is-pending", "is-installing", "is-done", "is-failed"].find((c) => r.el.classList.contains(c));
  return { result, venv: state(rows.venv), pip: state(rows.pip), venvText: rows.venv.stateEl.textContent };
}

let failures = 0;
function expect(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`}`);
}

// 1. an environment that already existed: only the pip step runs, then the explicit success marker
expect("success: pip row is settled (not left 'installing') and the skipped row reads ready",
  scenario(["$ /v/bin/python -m pip install --upgrade pip", "Requirement already satisfied", "[SEQUENCE_OK]"]),
  { result: true, venv: "is-done", pip: "is-done", venvText: "ready" });
// 2. the connection drops mid-install: no success marker, no failure marker
expect("dropped connection is a failure, not a success",
  scenario(["$ /usr/bin/python3 -m venv /v"]),
  { result: false, venv: "is-failed", pip: "is-pending", venvText: "failed" });
// 3. the server reports a failure
expect("explicit failure marker is a failure",
  scenario(["$ /usr/bin/python3 -m venv /v", "[SEQUENCE_FAILED]"]),
  { result: false, venv: "is-failed", pip: "is-pending", venvText: "failed" });
// 4. nothing at all before the connection closes (e.g. an HTTP 403)
expect("closing with no frames at all is a failure",
  scenario([]),
  { result: false, venv: "is-pending", pip: "is-pending", venvText: "queued" });

process.exit(failures ? 1 : 0);
