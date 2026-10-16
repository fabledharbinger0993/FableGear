"""Degraded-wiring regression tests for the Record Room inline player / deck."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2] / "fablegear"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const root = process.argv[1];
const warns = [], toasts = [];
const listeners = {};
const fakeEl = () => ({ paused: true, addEventListener(){}, pause(){ this.paused = true; },
  load(){}, play(){ this.paused = false; return Promise.resolve(); },
  querySelector(){ return null; }, classList: { toggle(){} }, style: {} });
const withDom = process.argv[2] === 'dom';
const audioEl = fakeEl();
const win = { addEventListener(){}, CSS: undefined };
const ctx = {
  window: win, console: { warn: (m) => warns.push(String(m)), log(){}, error(){} },
  document: {
    getElementById: (id) => (withDom && id === 'le-player-audio') ? audioEl : null,
    querySelectorAll: () => [], querySelector: () => null, addEventListener(){},
    createElement: () => fakeEl(), body: fakeEl(),
  },
  Audio: function () { return fakeEl(); },
  showToast: (m) => toasts.push(m),
  setTimeout, clearTimeout, fetch: () => Promise.reject(new Error('x')),
};
win.showToast = ctx.showToast;
ctx.globalThis = ctx;
vm.createContext(ctx);
for (const f of ['static/record_room/library_editor.js', 'static/chop_shop/tool_modal.js', 'static/shared/boot.js']) {
  vm.runInContext(fs.readFileSync(root + '/' + f, 'utf8'), ctx, { filename: f });
}
(async () => {
  const out = {};
  const p = vm.runInContext('leEnsurePlayer()', ctx);
  out.hasPlay = typeof p.play === 'function';
  out.fallbackWarned = warns.some(w => w.includes('#le-player-audio'));
  // deck globals absent: none of these may throw
  await vm.runInContext('leToggleTrackPlayback(1)', ctx);
  await vm.runInContext('leToggleTrackPlayback(1)', ctx);
  vm.runInContext('leInlinePause(); leClearInlinePreview(); leRefreshPlaybackButtons();', ctx);
  warns.length = 0;
  out.missing = vm.runInContext('leCheckPlaybackIntegrity()', ctx);
  out.integrityWarned = warns.length > 0;
  out.toasts = toasts.length;
  vm.runInContext('leCheckPlaybackIntegrity()', ctx);
  out.toastsAfterSecond = toasts.length;
  // coordination when deck present
  let paused = 0;
  win.deckPauseAll = () => { paused++; };
  await vm.runInContext('leToggleTrackPlayback(2)', ctx);
  out.deckPaused = paused;
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ threw: String(e) })); });
"""


def _run(mode):
    r = subprocess.run(["node", "-e", HARNESS, str(REPO_ROOT), mode],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_missing_audio_element_falls_back_and_degraded_wiring_does_not_throw():
    out = _run("nodom")
    assert "threw" not in out, out
    assert out["hasPlay"] and out["fallbackWarned"]
    assert out["integrityWarned"]
    assert "window.deckPauseAll" in out["missing"]
    assert "#le-player-audio" in out["missing"]
    assert out["toasts"] == 1 and out["toastsAfterSecond"] == 1  # once per session
    assert out["deckPaused"] >= 1  # inline play pauses deck when available


def test_healthy_dom_player_is_used_without_fallback_warning():
    out = _run("dom")
    assert "threw" not in out, out
    assert out["hasPlay"] and not out["fallbackWarned"]
