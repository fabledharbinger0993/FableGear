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
  try {
    const r = await fetch('/api/setup-status');
    const d = await r.json();
    if (d.setup_complete) {
      // Restore permission values from server into localStorage so applyPermissions works
      if (d.db_read)  localStorage.setItem('fablegear-db-read',  d.db_read);
      if (d.db_write) localStorage.setItem('fablegear-db-write', d.db_write);
      localStorage.setItem('fablegear-setup-complete', '1');
      applyPermissions();
      if (d.db_write === 'granted') {
        localStorage.setItem('fablegear-archive-permission', 'granted');
        fetch('/api/setup-archive', { method: 'POST' }).catch(() => {});
      }
      // Run silent audit on every launch for returning users
      if (d.db_read === 'granted') setTimeout(runSilentAudit, 700);
    } else {
      openWelcome();
    }
  } catch (_) {
    // Server not yet ready — fall back to localStorage cache
    if (!localStorage.getItem('fablegear-setup-complete')) {
      openWelcome();
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
const LS_PREFIX = 'superbox_path_';

function lsSave(id) {
  const el = document.getElementById(id);
  if (el) localStorage.setItem(LS_PREFIX + id, el.value);
}

function lsLoad(id) {
  return localStorage.getItem(LS_PREFIX + id) || '';
}

async function prefillDefaults() {
  // No fields are auto-filled from the music root — leaving destination inputs
  // blank prevents accidental runs against an unconfigured path.
  // All fields restore from localStorage only (user's own previous entries).
  const rootFields = [];
  const freeFields = ['relocate-new', 'organize-target', 'novelty-dest', 'relocate-old'];

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

/* ── Workflow rail scroll ───────────────────────────────────────────────────── */
document.querySelectorAll('.step-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.step-tab').forEach(tab => tab.classList.remove('active'));
    btn.classList.add('active');
    if (btn.dataset.target) openToolDrawer(btn.dataset.target);
  });
});
