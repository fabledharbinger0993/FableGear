/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / state
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 1-38
   ──────────────────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  // no-op; retained for load-order safety
});
/* ── State ─────────────────────────────────────────────────────────────────── */
let activeSource = null;
let isRunning    = false;
let rbRunning    = false;
let renamePreflightState = null;
let _fgActiveSpace = 'record';

function setFableGearSpace(space) {
  const nextSpace = space === 'chop' ? 'chop' : 'record';
  _fgActiveSpace = nextSpace;

  document.body.classList.toggle('fg-space-record', nextSpace === 'record');
  document.body.classList.toggle('fg-space-chop', nextSpace === 'chop');
  document.getElementById('lp-record-room-btn')?.classList.toggle('active', nextSpace === 'record');
  document.getElementById('lp-chop-shop-btn')?.classList.toggle('active', nextSpace === 'chop');

  const recordRoom = document.getElementById('library-editor-overlay');
  requestAnimationFrame(() => {
    if (recordRoom) recordRoom.classList.toggle('hidden', nextSpace !== 'record');
  });

  if (nextSpace === 'record') {
    closeDbPanel();
    closeToolFloatModal();
    closeRightNavDropdown();
    dismissBackToRecordHint(true);
    if (!_leTracksLoaded) setLibraryMode('db');
  } else {
    closeRightNavDropdown();
    showBackToRecordHint();
  }

  localStorage.setItem('fablegear-space', nextSpace);
}

/* ── Back-to-Record-Room hint ─────────────────────────────────────────────────
   Transient 10s popup anchored near the FG logo (top-left). Replaces the old
   permanent "Back to Record Room" button in the Chop Shop intro. */
let _fgBackHintTimer = null;

function showBackToRecordHint() {
  dismissBackToRecordHint(true);
  const logo = document.querySelector('.lp-logo');
  const hint = document.createElement('div');
  hint.className = 'fg-back-hint';
  hint.id = 'fg-back-hint';
  hint.setAttribute('role', 'button');
  hint.tabIndex = 0;
  hint.textContent = 'Back to Record Room';
  const goBack = () => setFableGearSpace('record');
  hint.addEventListener('click', goBack);
  hint.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goBack(); } });
  document.body.appendChild(hint);

  // Anchor just to the right of the FG logo; fall back to top-left if absent.
  const r = logo ? logo.getBoundingClientRect() : null;
  hint.style.top  = `${Math.max(8, r ? r.top : 24)}px`;
  hint.style.left = `${(r ? r.right : 80) + 10}px`;

  _fgBackHintTimer = setTimeout(() => dismissBackToRecordHint(), 10000);
}

function dismissBackToRecordHint(immediate) {
  if (_fgBackHintTimer) { clearTimeout(_fgBackHintTimer); _fgBackHintTimer = null; }
  const hint = document.getElementById('fg-back-hint');
  if (!hint) return;
  if (immediate) { hint.remove(); return; }
  hint.classList.add('hiding');
  hint.addEventListener('animationend', () => hint.remove(), { once: true });
}

