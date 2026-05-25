/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / boot
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 6611-6652
   ──────────────────────────────────────────────────────────────────────── */

// ── Legacy toolkit launcher ──────────────────────────────────────────────────

function openToolkitModal() {
  setFableGearSpace('chop');
}

function closeToolkitModal() {
  const modal = document.getElementById('toolkit-modal');
  if (!modal) return;
  
  modal.style.display = 'none';
  document.body.style.overflow = '';
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    // Close toolkit modal first if open
    const toolkitModal = document.getElementById('toolkit-modal');
    if (toolkitModal && toolkitModal.style.display === 'flex') {
      closeToolkitModal();
      return;
    }
    
  }
});

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Floating tool modal drag
  _initToolFloatModalDrag();

  // Prevent WKWebView frameless-window drag from swallowing range inputs.
  // -webkit-app-region: no-drag is set in CSS but WKWebView doesn't reliably
  // honour it on <input type="range"> thumb/track hits. Stopping mousedown
  // propagation in capture phase blocks the drag hittest before it fires.
  document.addEventListener('mousedown', e => {
    if (e.target.closest('input[type="range"]')) e.stopPropagation();
  }, true);
});
