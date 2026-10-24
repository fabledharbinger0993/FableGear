/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / boot
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 6611-6652
   ──────────────────────────────────────────────────────────────────────── */

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    // Close the single morphing tool modal if it's open (tool keeps running).
    const fm = document.getElementById('tool-float-modal');
    if (fm && fm.style.display === 'flex') {
      closeToolFloatModal();
      return;
    }
  }
});

// ── Boot ──────────────────────────────────────────────────────────────────────

// Keep the docked Chop Shop modal clear of pinned safety alerts: when any banner
// in #chop-banner-dock becomes visible, flag the body and publish the dock height
// so CSS can push the modal down by exactly that much.
function _initChopBannerWatch() {
  const dock = document.getElementById('chop-banner-dock');
  if (!dock) return;
  // ResizeObserver tracks the dock's actual rendered box, so it self-corrects
  // for two things a child-attribute MutationObserver can't: (1) the 40vh
  // max-height clipping the dock to 0 for a moment at load, before the window
  // has a real innerHeight — that stale 0 used to stick forever since nothing
  // re-fired once the banners stopped toggling; (2) a banner's *content*
  // changing height (e.g. a findings list) without its own style/class
  // attribute changing.
  // These two existed to push the DOCKED tool panel down so banners couldn't
  // cover the tool form. The tool surface is a centred overlay now, so nothing
  // reads them any more — they're kept because the banner dock still wants a
  // measured height, and restoring any docked panel would want them back.
  const recompute = () => {
    const h = dock.offsetHeight;
    document.body.classList.toggle('fg-chop-banner-open', h > 0);
    document.documentElement.style.setProperty('--chop-banner-h', h + 'px');
  };
  new ResizeObserver(recompute).observe(dock);
  recompute();
}

document.addEventListener('DOMContentLoaded', () => {
  // Floating tool modal drag
  _initToolFloatModalDrag();

  // Keep the docked Chop Shop modal clear of pinned safety alerts.
  _initChopBannerWatch();

  // Prevent WKWebView frameless-window drag from swallowing sliders, terminal
  // scrollback, waveform scrub targets, and scrollable list areas.
  // `-webkit-app-region: no-drag` is necessary but not sufficient here: the
  // native frameless drag behavior can still hijack slider/thumb hits and
  // scrollbar interaction inside overflow containers. Use delegated listeners
  // so dynamic UI (tool modal content, scan log, etc.) is covered too.
  const framelessInteractiveSelector = [
    'input[type="range"]',
    '.fuzzy-threshold-row',
    '.deck-wave-wrap',
    '.deck-panel',
    '.le-track-list',
    '.le-split-col-list',
    '.le-sidebar',
    '#library-editor-overlay',
    '#tool-float-modal-body',
    '#log-panel',
    '#log-output',
  ].join(', ');
  const stopFramelessDragHijack = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest(framelessInteractiveSelector)) {
      event.stopPropagation();
    }
  };
  ['pointerdown', 'mousedown', 'touchstart', 'wheel'].forEach(type => {
    document.addEventListener(type, stopFramelessDragHijack);
  });

  // Pick up an onboarding import left running in the background — it's a
  // detached server-side thread (skipping the wizard step or exiting it
  // entirely never stops it), but nothing outside the wizard page showed
  // that it was still going. Surface live progress here instead.
  _initBackgroundImportWatch();
});

/* ── Background onboarding import awareness ─────────────────────────────── */
let _bgImportPoll = null;
let _bgImportLastDone = -1;
let _bgImportLastRefresh = 0;

function _renderBgImportPill(label) {
  const container = document.getElementById('session-pills-container');
  if (!container) return;
  let pill = document.getElementById('bg-import-pill');
  if (!pill) {
    pill = document.createElement('div');
    pill.id = 'bg-import-pill';
    pill.className = 'summary-pill bg-import-pill';
    container.prepend(pill);
  }
  pill.innerHTML = `<span class="summary-pill-icon">⏳</span>${label}`;
}

function _pollBackgroundImport() {
  fetch('/api/onboarding/import-sources/status')
    .then(r => r.json())
    .then(s => {
      if (s.running) {
        const label = (s.phase === 'importing' && s.total > 0)
          ? `Importing ${s.done.toLocaleString()}/${s.total.toLocaleString()}`
          : 'Scanning library…';
        _renderBgImportPill(label);
        // Live library: pull the Record Room list as batches land, at most every few seconds.
        if (s.phase === 'importing' && s.done !== _bgImportLastDone && Date.now() - _bgImportLastRefresh > 4000) {
          _bgImportLastDone = s.done;
          _bgImportLastRefresh = Date.now();
          window.leRefreshLibraryLive?.();
        }
        return;
      }
      clearInterval(_bgImportPoll);
      _bgImportPoll = null;
      _bgImportLastDone = -1;
      document.getElementById('bg-import-pill')?.remove();
      window.leRefreshLibraryLive?.();  // pick up the final batch
      if (s.error) {
        if (typeof showToast === 'function') showToast(`Background import failed: ${s.error}`, 'error');
        return;
      }
      const r = s.result;
      if (!r) return;  // nothing ran this session
      const summary = `${r.new_files || 0} new · ${r.updated_files || 0} updated · `
        + `${r.skipped_files || 0} already known` + ((r.error_files || 0) ? ` · ${r.error_files} errors` : '');
      if (typeof showToast === 'function') showToast(`Background import complete: ${summary}`, 'success');
      if (typeof _addOrUpdateSummaryPill === 'function' && typeof sessionReports === 'object') {
        const title = 'Onboarding Import — Library Setup';
        sessionReports[title] = { text: summary, reportPath: null, ts: Date.now() };
        _addOrUpdateSummaryPill(title, true);
      }
    })
    .catch(() => {});
}

function _initBackgroundImportWatch() {
  fetch('/api/onboarding/import-sources/status')
    .then(r => r.json())
    .then(s => {
      if (!s.running) return;
      _pollBackgroundImport();
      _bgImportPoll = setInterval(_pollBackgroundImport, 1500);
    })
    .catch(() => {});
}

// ── Record Room playback wiring integrity check (non-fatal) ───────────────────
function leCheckPlaybackIntegrity() {
  const missing = [];
  if (!document.getElementById('le-player-audio')) missing.push('#le-player-audio');
  ['deckTogglePanel', 'deckPauseAll', 'leToggleTrackPlayback'].forEach(n => {
    if (typeof window[n] !== 'function') missing.push('window.' + n);
  });
  if (!missing.length) return [];
  console.warn('[FableGear] Record Room playback wiring incomplete — missing: ' + missing.join(', '));
  if (!window._lePlaybackWarned) {
    window._lePlaybackWarned = true;
    try { window.showToast?.('Media player wiring incomplete: ' + missing.join(', '), 'error'); } catch (_) {}
  }
  return missing;
}
window.leCheckPlaybackIntegrity = leCheckPlaybackIntegrity;
window.addEventListener('load', () => { try { leCheckPlaybackIntegrity(); } catch (_) {} });
