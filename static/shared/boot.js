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
  const recompute = () => {
    const anyVisible = Array.from(dock.children)
      .some(c => getComputedStyle(c).display !== 'none');
    document.body.classList.toggle('fg-chop-banner-open', anyVisible);
    const h = anyVisible ? dock.offsetHeight : 0;
    document.documentElement.style.setProperty('--chop-banner-h', h + 'px');
  };
  const obs = new MutationObserver(recompute);
  Array.from(dock.children).forEach(c =>
    obs.observe(c, { attributes: true, attributeFilter: ['style', 'class'] }));
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
});
