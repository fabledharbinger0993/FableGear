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
  // scrollbar thumbs inside overflow containers. Stopping mousedown
  // propagation in capture phase blocks the drag hittest before it fires.
  document.addEventListener('mousedown', e => {
    if (
      e.target.closest('input[type="range"]') ||
      e.target.closest('.deck-wave-wrap')     ||
      e.target.closest('.deck-panel')         ||
      e.target.closest('.le-track-list')      ||
      e.target.closest('.le-split-col-list')  ||
      e.target.closest('.le-sidebar')         ||
      e.target.closest('#library-editor-overlay')
    ) {
      e.stopPropagation();
    }
  }, true);
});
