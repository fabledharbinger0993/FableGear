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
  if (recordRoom) recordRoom.classList.toggle('hidden', nextSpace !== 'record');

  if (nextSpace === 'record') {
    closeDbPanel();
    closeToolFloatModal();
    closeRightNavDropdown();
    if (!_leTracksLoaded) setLibraryMode('db');
  } else {
    closeRightNavDropdown();
  }

  localStorage.setItem('fablegear-space', nextSpace);
}

