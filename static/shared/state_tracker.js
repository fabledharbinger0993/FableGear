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

// ── FableGo walkthrough ──────────────────────────────────────────────────────
let _rkgStep = 1;
const _rkgTotal = 4;

function closeFableGo() {
  document.getElementById('fablego-panel').classList.remove('open');
  document.getElementById('fablego-backdrop').classList.remove('open');
}

function rkgGoTo(step) {
  step = Math.max(1, Math.min(_rkgTotal, step));
  _rkgStep = step;
  for (let i = 1; i <= _rkgTotal; i++) {
    const page = document.getElementById(`rkg-page-${i}`);
    if (page) page.classList.toggle('hidden', i !== step);
    const ind = document.getElementById(`rkg-step-${i}`);
    if (!ind) continue;
    ind.classList.remove('active', 'done');
    if (i < step) ind.classList.add('done');
    else if (i === step) ind.classList.add('active');
  }
  const prev = document.getElementById('rkg-prev');
  const next = document.getElementById('rkg-next');
  const ctr  = document.getElementById('rkg-counter');
  if (prev) prev.style.visibility = step === 1 ? 'hidden' : 'visible';
  if (next) next.textContent = step === _rkgTotal ? 'Done' : 'Next →';
  if (ctr)  ctr.textContent  = `${step} / ${_rkgTotal}`;
}

function rkgNext() {
  if (_rkgStep === _rkgTotal) { closeFableGo(); return; }
  rkgGoTo(_rkgStep + 1);
}

function rkgPrev() { rkgGoTo(_rkgStep - 1); }

function _loadConnectivity() {
  fetch('/api/connectivity')
    .then(r => r.json())
    .then(d => {
      const dot     = document.getElementById('fablego-status-dot');
      const btnDot  = document.getElementById('fablego-btn-dot');
      const label   = document.getElementById('fablego-status-label');
      const qr      = document.getElementById('fablego-qr');
      const localEl = document.getElementById('fablego-local');
      const tsEl    = document.getElementById('fablego-tailscale');
      const offline = document.getElementById('fablego-offline-msg');
      const qrWrap  = document.getElementById('fablego-qr-wrap');

      // Status dot
      if (dot) dot.className = '';
      if (btnDot) btnDot.className = 'tool-dot';
      if (d.remote_ready) {
        dot  && dot.classList.add('remote');
        btnDot && btnDot.classList.add('remote');
        if (label) label.textContent = 'Remote access ready (Tailscale)';
      } else if (d.local_ip && d.local_ip !== '127.0.0.1') {
        dot  && dot.classList.add('lan');
        btnDot && btnDot.classList.add('lan');
        if (label) label.textContent = 'LAN access only — Tailscale not connected';
      } else {
        dot && dot.classList.add('offline');
        if (label) label.textContent = 'Offline — local tools still work normally';
      }

      if (localEl)  localEl.textContent  = d.local_ip    ? `http://${d.local_ip}:5001`      : '—';
      if (tsEl)     tsEl.textContent     = d.tailscale_ip ? `http://${d.tailscale_ip}:5001`  : 'not connected';

      // Pairing QR (step 4) — now shows the PWA URL so iPhone can open in Safari
      if ((d.qr_pwa_url || d.qr_svg) && qr) {
        qr.innerHTML = d.qr_pwa_url || d.qr_svg;
        if (qrWrap) qrWrap.style.display = 'flex';
        if (offline) offline.style.display = 'none';
      } else {
        if (qrWrap) qrWrap.style.display = 'none';
        if (offline) offline.style.display = 'block';
      }

      // Setup QRs (green) — steps 2 & 3
      _injectSetupQr('rkg-qr-ts-mac',       d.qr_tailscale_mac);
      _injectSetupQr('rkg-qr-ts-ios',       d.qr_tailscale_ios);
      // Step 3 FableGo slot: now shows the PWA URL QR (scan → Safari → Add to Home Screen)
      _injectSetupQr('rkg-qr-fablego-ios',  d.qr_pwa_url || d.qr_fablego_ios);
    })
    .catch(() => {
      const label = document.getElementById('fablego-status-label');
      if (label) label.textContent = 'Could not fetch connectivity info';
    });
}

function _injectSetupQr(elId, svg) {
  if (!svg) return;
  const box = document.getElementById(elId);
  if (!box || box.querySelector('svg')) return; // already injected
  const wrap = document.createElement('div');
  wrap.innerHTML = svg;
  const svgEl = wrap.querySelector('svg');
  if (svgEl) box.insertBefore(svgEl, box.firstChild);
}

// Update button dot on page load (silent, no panel)
fetch('/api/connectivity')
  .then(r => r.json())
  .then(d => {
    const btnDot = document.getElementById('fablego-btn-dot');
    if (!btnDot) return;
    if (d.remote_ready)                              btnDot.classList.add('remote');
    else if (d.local_ip && d.local_ip !== '127.0.0.1') btnDot.classList.add('lan');
  })
  .catch(() => {});

