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

  // Prevent WKWebView frameless-window drag from swallowing range inputs,
  // waveform scrub targets, and scrollable list areas.
  // -webkit-app-region: no-drag is set in CSS but WKWebView doesn't reliably
  // honour it on <input type="range"> thumb/track hits, canvas elements, or
  // scrollbar thumbs inside overflow containers. Stop propagation on the
  // interactive roots in bubble phase so events still reach component handlers
  // (e.g. CUE button mousedown) before being blocked from reaching any
  // window-drag listener on document.
  [
    'input[type="range"]',
    '.deck-wave-wrap',
    '.deck-panel',
    '.le-track-list',
    '.le-split-col-list',
    '.le-sidebar',
    '#library-editor-overlay',
  ].forEach(sel => {
    document.querySelectorAll(sel).forEach(el =>
      el.addEventListener('mousedown', e => e.stopPropagation())
    );
  });

  // Pick up an onboarding import left running in the background — it's a
  // detached server-side thread (skipping the wizard step or exiting it
  // entirely never stops it), but nothing outside the wizard page showed
  // that it was still going. Surface live progress here instead.
  _initBackgroundImportWatch();
});

/* ── Background onboarding import awareness ─────────────────────────────── */
let _bgImportPoll = null;

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
        return;
      }
      clearInterval(_bgImportPoll);
      _bgImportPoll = null;
      document.getElementById('bg-import-pill')?.remove();
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
