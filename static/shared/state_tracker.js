/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / state_tracker
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 6459-6610
   ──────────────────────────────────────────────────────────────────────── */

/* ── State tracker — per-library step completion ─────────────────────────
   Calls /api/state on load and after every successful command.
   Cards get .step-complete or .step-error CSS classes.              */
const STATE_STEP_MAP = {
  audit:'rail-btn-audit', process:'step-process', duplicates:'step-duplicates',
  prune:'step-duplicates', relocate:'rail-btn-relocate', import:'rail-btn-import',
  link:'rail-btn-link', normalize:'step-normalize', convert:'step-convert',
  organize:'step-organize', novelty:'step-novelty',
};
async function loadState(libraryRoot) {
  if (!libraryRoot) return;
  try {
    const res = await fetch('/api/state', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ library_root: libraryRoot }),
    });
    if (res.ok) applyStateToUI(await res.json());
  } catch (_) {}
}
function applyStateToUI(state) {
  Object.entries(STATE_STEP_MAP).forEach(([step, cardId]) => {
    const card = document.getElementById(cardId);
    if (!card) return;
    card.classList.remove('step-complete', 'step-error');
    const info = state[step];
    if (!info) return;
    card.classList.add(info.exit_code === 0 ? 'step-complete' : 'step-error');
  });
}
async function _initStateOverlay() {
  try {
    const cfg = await fetch('/api/config').then(r => r.json());
    if (cfg.music_root) loadState(cfg.music_root);
  } catch (_) {}
}
_initStateOverlay();
['organize-target','novelty-dest','novelty-copy-to'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('change', () => { if (el.value.trim()) loadState(el.value.trim()); });
});
