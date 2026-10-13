"""Live Record Room refresh while a background onboarding import is writing.

Covers the JS half of "continue in background": boot.js polls the import
status and calls window.leRefreshLibraryLive(), which must refresh only the
All Tracks list, keep the reader's scroll place, never reset the active
source/playlist view, never run before the library was loaded, and never
overlap two fetches. Also pins the onboarding wizard's background button.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const root = process.argv[1];
const els = {};
const el = (id) => (els[id] ||= {
  id, textContent: '', innerHTML: '', scrollTop: 0, style: {}, dataset: {},
  classList: { add(){}, remove(){}, toggle(){} }, addEventListener(){},
  prepend(){}, appendChild(){}, remove(){}, querySelector(){ return null; },
  querySelectorAll(){ return []; }, append(){},
});
const ctx = {
  console: { log(){}, warn(){}, error(){} },
  document: {
    getElementById: el, querySelector: () => null, querySelectorAll: () => [],
    createElement: () => el('created'), addEventListener(){},
  },
  setTimeout, clearTimeout, setInterval, clearInterval, Set, Map, Date, JSON, Math, Array, Object, String, Number,
  parseFloat, encodeURIComponent, Promise,
  showToast(){}, addEventListener(){}, __applied: 0, __fetches: [], __next: [], __statusBody: null,
  __holdNextTracks: false, __resolveHeldTracks: null, __dropCalls: [], __playlistButton: null,
};
ctx.window = ctx;
ctx.globalThis = ctx;
ctx.fetch = (url) => {
  ctx.__fetches.push(url);
  if (String(url).includes('import-sources/status')) {
    return Promise.resolve({ json: async () => ctx.__statusBody });
  }
  if (String(url).includes('/api/library/db/import')) {
    return Promise.resolve({ ok: true, json: async () => ({
      new_files: 1, content_ids: { '/music/drop.mp3': 42 },
    }) });
  }
  if (String(url).includes('/api/library/playlists/')) {
    return Promise.resolve({ ok: true, json: async () => ({ added: 1 }) });
  }
  if (String(url).includes('/api/library/tracks?db=fablegear') && ctx.__holdNextTracks) {
    ctx.__holdNextTracks = false;
    return new Promise(resolve => {
      ctx.__resolveHeldTracks = tracks => resolve({ ok: true, json: async () => tracks });
    });
  }
  return Promise.resolve({ ok: true, json: async () => ctx.__next });
};
vm.createContext(ctx);
vm.runInContext(
  "function _leGetState(k, f) { if (window[k] === undefined) window[k] = f; return window[k]; }",
  ctx);
for (const f of ['static/record_room/library_editor.js', 'static/chop_shop/tool_modal.js', 'static/shared/boot.js', 'static/record_room/library_mode.js']) {
  vm.runInContext(fs.readFileSync(root + '/' + f, 'utf8'), ctx, { filename: f });
}
// Stand-ins for the DOM-heavy helpers: record that the filter pass ran and
// mimic leRenderTracks() jumping the list back to the top.
vm.runInContext(`
  leApplyFilters = function () { __applied++; document.getElementById('le-track-list').scrollTop = 0; };
  libBuildGenreSelect = function () {};
  leRenderSourceLocations = function () {};
`, ctx);

const tick = () => new Promise(r => setTimeout(r, 5));
(async () => {
  const out = {};
  const run = (code) => vm.runInContext(code, ctx);
  const list = el('le-track-list');

  // 1. Library never loaded: no fetch, so leLoadLibrary() keeps owning the first load.
  await run('leRefreshLibraryLive()');
  out.unloadedFetches = ctx.__fetches.length;

  // 2. Loaded, All Tracks showing: list refreshes, scroll place survives.
  run('_leTracksLoaded = true; _leActiveNodeType = "all"; _leBaseTracks = []; _leAllTracks = [];');
  list.scrollTop = 120;
  ctx.__next = [{ id: 1 }, { id: 2 }];
  await run('leRefreshLibraryLive()');
  out.allFetchUrl = ctx.__fetches[ctx.__fetches.length - 1];
  out.allCount = run('_leAllTracks.length');
  out.allBaseCount = run('_leBaseTracks.length');
  out.allApplied = ctx.__applied;
  out.scrollKept = list.scrollTop;

  // 3. A source-location view is active: the filtered view must not be replaced.
  run('_leActiveNodeType = "source"; _leBaseTracks = ["kept"];');
  ctx.__next = [{ id: 1 }, { id: 2 }, { id: 3 }];
  await run('leRefreshLibraryLive()');
  out.sourceAllCount = run('_leAllTracks.length');
  out.sourceBaseKept = run('_leBaseTracks[0]');

  // 4. Overlapping ticks collapse into one fetch.
  run('_leActiveNodeType = "all";');
  const before = ctx.__fetches.length;
  await Promise.all([run('leRefreshLibraryLive()'), run('leRefreshLibraryLive()')]);
  out.overlapFetches = ctx.__fetches.length - before;

  // 5. A refresh requested during an in-flight fetch is coalesced into a follow-up.
  const beforePending = ctx.__fetches.length;
  ctx.__holdNextTracks = true;
  ctx.__next = [{ id: 'before-final-batch' }];
  const inFlight = run('leRefreshLibraryLive()');
  await tick();
  run('leRefreshLibraryLive()');
  out.pendingFetchesBeforeResolve = ctx.__fetches.length - beforePending;
  ctx.__next = [{ id: 'final-batch' }];
  ctx.__resolveHeldTracks([{ id: 'before-final-batch' }]);
  await inFlight;
  await tick();
  out.pendingRefreshFetches = ctx.__fetches.length - beforePending;
  out.pendingRefreshTrackId = run('_leAllTracks[0].id');

  // 6. Not in the FableGear database source: no fetch.
  ctx.window._leDbSource = 'local';
  const beforeLocal = ctx.__fetches.length;
  await run('leRefreshLibraryLive()');
  out.localFetches = ctx.__fetches.length - beforeLocal;
  ctx.window._leDbSource = 'fablegear';

  // 7. boot.js: an importing tick triggers a refresh; a repeat tick inside the
  //    throttle window does not; completion triggers one final refresh.
  let refreshes = 0;
  ctx.window.leRefreshLibraryLive = () => { refreshes++; };
  ctx.__statusBody = { running: true, phase: 'importing', done: 5, total: 10 };
  run('_bgImportLastRefresh = 0; _bgImportLastDone = -1;');
  run('_pollBackgroundImport()'); await tick();
  out.importingRefreshes = refreshes;
  run('_pollBackgroundImport()'); await tick();
  out.throttledRefreshes = refreshes;
  ctx.__statusBody = { running: false, phase: 'done', done: 10, total: 10,
                       result: { new_files: 10, updated_files: 0, skipped_files: 0, error_files: 0 } };
  run('_pollBackgroundImport()'); await tick();
  out.completionRefreshes = refreshes;

  // 8. A successful drop reloads the library and restores the active playlist.
  run(`
    _leActivePlaylistId = 7;
    _leActivePlaylistName = 'Crate';
    leLoadLibrary = async function () {
      __dropCalls.push('loadLibrary');
      __playlistButton = { rebuilt: true };
    };
    leSelectPlaylist = async function (node, button) {
      __dropCalls.push('selectPlaylist:' + node.id + ':' + node.name);
      __dropCalls.push(button === __playlistButton ? 'rebuiltButton' : 'wrongButton');
    };
  `);
  ctx.__playlistButton = null;
  ctx.document.querySelector = selector => {
    ctx.__dropSelector = selector;
    return ctx.__playlistButton;
  };
  await ctx.window.leTrackListDrop({
    preventDefault(){},
    currentTarget: { classList: { remove(){} } },
    dataTransfer: { getData: type => type === 'text/plain' ? '/music/drop.mp3' : '' },
  });
  out.dropSelector = ctx.__dropSelector;
  out.dropCalls = ctx.__dropCalls;

  console.log(JSON.stringify(out));
})().catch(e => console.log(JSON.stringify({ threw: String(e && e.stack || e) })));
"""


def _run():
    r = subprocess.run(["node", "-e", HARNESS, str(REPO_ROOT)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def out():
    result = _run()
    assert "threw" not in result, result
    return result


def test_no_fetch_before_library_is_loaded(out):
    assert out["unloadedFetches"] == 0


def test_refreshes_all_tracks_with_the_fablegear_source(out):
    assert out["allFetchUrl"] == "/api/library/tracks?db=fablegear"
    assert out["allCount"] == 2
    assert out["allBaseCount"] == 2
    assert out["allApplied"] == 1


def test_scroll_position_survives_a_refresh(out):
    assert out["scrollKept"] == 120


def test_source_view_is_not_replaced(out):
    assert out["sourceAllCount"] == 3
    assert out["sourceBaseKept"] == "kept"


def test_overlapping_refreshes_queue_one_followup_fetch(out):
    assert out["overlapFetches"] == 2


def test_refresh_requested_during_fetch_runs_after_it_finishes(out):
    assert out["pendingFetchesBeforeResolve"] == 1
    assert out["pendingRefreshFetches"] == 2
    assert out["pendingRefreshTrackId"] == "final-batch"


def test_non_fablegear_source_is_not_refreshed(out):
    assert out["localFetches"] == 0


def test_boot_poll_refreshes_while_importing_and_once_on_completion(out):
    assert out["importingRefreshes"] == 1
    assert out["throttledRefreshes"] == 1  # second tick inside the 4s window
    assert out["completionRefreshes"] == 2


def test_onboarding_wizard_has_continue_in_background_button():
    html = (REPO_ROOT / "templates" / "onboarding.html").read_text(encoding="utf-8")
    assert 'id="import-background-btn"' in html
    assert "continueImportInBackground()" in html
    assert "_obImportBackgrounded" in html


def test_playlist_drop_restores_the_active_playlist_after_reload(out):
    assert out["dropSelector"] == '.le-tree-item[data-id="7"]'
    assert out["dropCalls"] == ["loadLibrary", "selectPlaylist:7:Crate", "rebuiltButton"]
