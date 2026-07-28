/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / utility
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 1687-1834
   ──────────────────────────────────────────────────────────────────────── */

/* ── Pre-tool drive health ping ─────────────────────────────────────────── */

/**
 * Call before any write tool runs. Returns {ok: true} or {ok: false, message}.
 * Non-fatal: if the fetch fails we allow through (don't block on network error).
 */
/* ── Utility helpers ─────────────────────────────────────────────────────── */

function _esc(s)     { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function _escAttr(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
// Alias names used by rename/preflight modal innerHTML templates
const escapeHtml     = _esc;
const escapeHtmlAttr = _escAttr;

function extractProducerAliasToken(text) {
  return String(text || '')
    .replace(/\s+(remix|dub|edit|mix|rework|version|remaster|bootleg|re-edit|radio\s+edit|extended\s+mix)\s*$/i, '')
    .trim();
}
function _escPath(s) { return (s || '').replace(/'/g,'\\'  ); }
function _fmtDur(s)  {
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}


// First launch: show permission wizard (mandatory, can't skip).
// Returning users: restore permissions from server-side state file, resume silently.
// Server-side state (/api/setup-status → fablegear-state.json) is the source of
// truth; localStorage is used as a fast-path cache on top of it.
(async () => {
  // The onboarding wizard persists db_read/db_write to the server-side state
  // file as booleans, while the in-app permission layer (applyPermissions,
  // settings.js) speaks the 'granted'/'denied' localStorage vocabulary.
  // Normalize here — the server state is the source of truth — so a permission
  // granted in the wizard actually unlocks tools in the app. Accepts booleans
  // (current format) and legacy 'granted' strings for forward/backward compat.
  const _isGranted = (v) => v === true || v === 'granted';
  try {
    const r = await fetch('/api/setup-status');
    const d = await r.json();
    if (d.setup_complete) {
      const readGranted  = _isGranted(d.db_read);
      const writeGranted = _isGranted(d.db_write);
      // Restore permission values into localStorage so applyPermissions works
      localStorage.setItem('fablegear-db-read',  readGranted  ? 'granted' : 'denied');
      localStorage.setItem('fablegear-db-write', writeGranted ? 'granted' : 'denied');
      localStorage.setItem('fablegear-setup-complete', '1');
      applyPermissions();
      if (writeGranted) {
        localStorage.setItem('fablegear-archive-permission', 'granted');
        fetch('/api/setup-archive', { method: 'POST' }).catch(() => {});
      }
      // Run silent audit on every launch for returning users
      if (readGranted) setTimeout(runSilentAudit, 700);
    } else {
      // Setup not finished — the destination is the onboarding wizard, not the
      // informational welcome panel. The "/" route normally redirects here
      // already; this is a safety net for direct navigation to index.html.
      window.location.replace('/onboarding');
    }
  } catch (_) {
    // Server not yet ready — fall back to localStorage cache
    if (!localStorage.getItem('fablegear-setup-complete')) {
      window.location.replace('/onboarding');
    } else {
      applyPermissions();
      if (localStorage.getItem('fablegear-archive-permission') === 'granted') {
        fetch('/api/setup-archive', { method: 'POST' }).catch(() => {});
      }
    }
  }
})();

function choosePath(mode) {
  _sbAnim(document.getElementById('path-modal-box'), 'sb-modal-out', '.18s', () => {
    _sbFadeBd('path-backdrop', false);
  });
  if (mode === 'pipeline') {
    openPipelineWizard();
  }
}

/* ── Config prefill + localStorage persistence ─────────────────────────────── */
const LS_PREFIX = 'fablegear_path_';
const LEGACY_LS_PREFIX = 'superbox_path_';

function lsSave(id) {
  const el = document.getElementById(id);
  if (el) {
    localStorage.setItem(LS_PREFIX + id, el.value);
    localStorage.removeItem(LEGACY_LS_PREFIX + id);
  }
}

function lsLoad(id) {
  const key = LS_PREFIX + id;
  const current = localStorage.getItem(key);
  if (current !== null) return current;

  const legacyKey = LEGACY_LS_PREFIX + id;
  const legacy = localStorage.getItem(legacyKey);
  if (legacy !== null) {
    localStorage.setItem(key, legacy);
    localStorage.removeItem(legacyKey);
    return legacy;
  }

  return '';
}

async function prefillDefaults() {
  // No fields are auto-filled from the music root — leaving destination inputs
  // blank prevents accidental runs against an unconfigured path.
  // All fields restore from localStorage only (user's own previous entries).
  const rootFields = [];
  const freeFields = ['relocate-new', 'organize-target', 'novelty-dest', 'novelty-copy-to', 'relocate-old'];

  // Restore any previously saved value for every tracked field first
  [...rootFields, ...freeFields].forEach(id => {
    const saved = lsLoad(id);
    const el = document.getElementById(id);
    if (el && saved) el.value = saved;
  });

  // Then fill blanks from server config
  try {
    const res    = await fetch('/api/config');
    const config = await res.json();
    const root   = config.music_root;
    if (root) {
      _libraryRoot = root;
    }
  } catch (_) {}

  // Save to localStorage whenever the user edits any remaining path field
  [...rootFields, ...freeFields].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => lsSave(id));
  });
}
prefillDefaults();

/* ── Dead root detection ────────────────────────────────────────────────────── */
async function checkDeadRoots() {
  try {
    const res = await fetch('/api/audit/path-roots');
    const data = await res.json();
    if (data.has_dead_roots) {
      showDeadRootsBanner(data.dead_roots);
      prefillRelocate(data.dead_roots);
    }
  } catch(e) { /* silent — non-critical */ }
}

function showDeadRootsBanner(deadRoots) {
  const banner = document.getElementById('dead-roots-banner');
  const detail = document.getElementById('dead-roots-detail');
  if (!banner || !detail) return;
  const lines = Object.entries(deadRoots)
    .map(([root, count]) => `<code style="color:var(--accent)">${root}</code> — ${count.toLocaleString()} tracks unreachable`);
  detail.innerHTML = lines.join('<br>');
  banner.style.display = 'block';
}

function prefillRelocate(deadRoots) {
  // Add each dead root as a pill in the relocate-old-pills zone
  const sorted = Object.entries(deadRoots).sort((a,b) => b[1]-a[1]);
  if (!sorted.length) return;
  const existing = getFolderPaths('relocate-old-pills');
  sorted.forEach(([oldRoot]) => {
    if (!existing.includes(oldRoot)) addFolderPill('relocate-old-pills', oldRoot);
  });
}

checkDeadRoots();

/* ── Workflow rail — prominent tool icons open the single morphing modal ─────── */
document.querySelectorAll('.step-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.target;
    if (!target) return;
    let switched = true;
    if (typeof handleToolIconClick === 'function') {
      switched = handleToolIconClick(target) !== false;
    } else if (typeof openToolFloatModal === 'function') {
      openToolFloatModal(target);
    }
    if (!switched) return;
    document.querySelectorAll('.step-tab').forEach(tab => tab.classList.remove('active'));
    btn.classList.add('active');
  });
});
