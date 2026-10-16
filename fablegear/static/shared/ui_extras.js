/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / ui_extras
   1. Camelot key helpers   — fgKeyBadge(key) renders a wheel-colored badge
   2. Booth Mode            — fgToggleBooth(), persisted, ⌘⇧B
   3. Command palette       — ⌘K / Ctrl+K, fuzzy filter, keyboard nav
   Classic script; shares global scope with the other shared slices.
   ──────────────────────────────────────────────────────────────────────── */

/* ══ 1. Camelot helpers ════════════════════════════════════════════════ */

/* Musical key → Camelot position. Covers the common rekordbox spellings. */
const FG_KEY_TO_CAMELOT = {
  'abm':'1A','g#m':'1A','b':'1B',
  'ebm':'2A','d#m':'2A','f#':'2B','gb':'2B',
  'bbm':'3A','a#m':'3A','db':'3B','c#':'3B',
  'fm':'4A','ab':'4B','g#':'4B',
  'cm':'5A','eb':'5B','d#':'5B',
  'gm':'6A','bb':'6B','a#':'6B',
  'dm':'7A','f':'7B',
  'am':'8A','c':'8B',
  'em':'9A','g':'9B',
  'bm':'10A','d':'10B',
  'f#m':'11A','gbm':'11A','a':'11B',
  'dbm':'12A','c#m':'12A','e':'12B',
};

function fgNormalizeCamelot(key) {
  if (!key) return null;
  const k = String(key).trim();
  // Already Camelot? ("8A", "08B", "11a")
  const cam = k.match(/^0?(1[0-2]|[1-9])\s*([ABab])$/);
  if (cam) return cam[1] + cam[2].toUpperCase();
  // Musical spelling ("Am", "F#m", "Db", "C#m")
  const norm = k.toLowerCase().replace(/\s+/g, '').replace('min', 'm').replace('maj', '');
  return FG_KEY_TO_CAMELOT[norm] || null;
}

function fgKeyBadge(key) {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => (
    { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]
  ));
  if (!key) return '<span class="cam-badge cam-none">—</span>';
  const cam = fgNormalizeCamelot(key);
  if (!cam) return `<span class="cam-badge cam-none">${esc(key)}</span>`;
  return `<span class="cam-badge cam-${cam.toLowerCase()}" title="${esc(key)}">${cam}</span>`;
}

/* ══ 2. Booth Mode ═════════════════════════════════════════════════════ */

function fgApplyTheme(theme) {
  if (theme === 'booth') {
    document.body.setAttribute('data-theme', 'booth');
  } else {
    document.body.removeAttribute('data-theme');
  }
  const btn = document.getElementById('fg-booth-btn');
  if (btn) btn.classList.toggle('lp-btn-active', theme === 'booth');
}

function fgToggleBooth() {
  const next = document.body.getAttribute('data-theme') === 'booth' ? 'default' : 'booth';
  try { localStorage.setItem('fg-theme', next); } catch (e) { /* private mode */ }
  fgApplyTheme(next);
}

(function fgBootTheme() {
  let saved = 'default';
  try { saved = localStorage.getItem('fg-theme') || 'default'; } catch (e) { /* private mode */ }
  if (document.body) {
    fgApplyTheme(saved);
  } else {
    document.addEventListener('DOMContentLoaded', () => fgApplyTheme(saved));
  }
})();

/* ══ 3. Command palette ════════════════════════════════════════════════ */

function fgPaletteEntries() {
  const entries = [];
  const add = (label, hint, fn) => entries.push({ label, hint, fn });

  // Stable, known actions — each guarded so a missing global never breaks the list.
  if (typeof openDbPanel === 'function') {
    add('Audit — library health check', 'Record Room', () => openDbPanel('audit'));
    add('Relocate — repair broken paths', 'Record Room', () => openDbPanel('relocate'));
    add('Link — connect tracks to playlists', 'Record Room', () => openDbPanel('link'));
    add('Import — add files to database', 'Record Room', () => openDbPanel('import'));
    add('Dead Files — find untracked audio', 'Record Room', () => openDbPanel('dead-files'));
  }
  if (typeof leOpenExportModal === 'function') {
    add('USB Export — prepare a device stick', 'Record Room', () => leOpenExportModal());
  }
  if (typeof openSettings === 'function') add('Settings', 'App', () => openSettings());
  if (typeof openSiteKey === 'function') add('Site Key — terms & definitions', 'App', () => openSiteKey());
  if (typeof openFableGearLauncher === 'function') add('Welcome & Permissions', 'App', () => openFableGearLauncher());
  add('Toggle Booth Mode — red-shift for dark booths', '⌘⇧B', () => fgToggleBooth());

  // Harvest any visible tool buttons not covered above (best-effort, additive).
  document.querySelectorAll('button[onclick]').forEach((btn) => {
    const label = (btn.textContent || btn.title || '').trim();
    if (!label || label.length > 48) return;
    if (entries.some((e) => e.label.toLowerCase().startsWith(label.toLowerCase()))) return;
    if (btn.offsetParent === null) return; // hidden
    add(label, 'Tool', () => btn.click());
  });

  return entries;
}

let _fgPalState = { open: false, entries: [], filtered: [], active: 0 };

function fgPaletteEnsureDom() {
  if (document.getElementById('fg-palette-backdrop')) return;
  const wrap = document.createElement('div');
  wrap.id = 'fg-palette-backdrop';
  wrap.className = 'hidden';
  wrap.innerHTML = `
    <div id="fg-palette" role="dialog" aria-label="Command palette">
      <input id="fg-palette-input" type="text" autocomplete="off" spellcheck="false"
             placeholder="Type a tool or action…  (Esc to close)">
      <div id="fg-palette-list" role="listbox"></div>
    </div>`;
  document.body.appendChild(wrap);
  wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) fgPaletteClose(); });
  document.getElementById('fg-palette-input').addEventListener('input', fgPaletteFilter);
}

function fgPaletteRender() {
  const list = document.getElementById('fg-palette-list');
  const { filtered, active } = _fgPalState;
  if (!filtered.length) {
    list.innerHTML = '<div class="fg-palette-empty">No matching actions</div>';
    return;
  }
  list.innerHTML = filtered.map((e, i) => `
    <div class="fg-palette-item${i === active ? ' active' : ''}" data-idx="${i}" role="option">
      <span>${e.label}</span><span class="fg-pi-hint">${e.hint}</span>
    </div>`).join('');
  list.querySelectorAll('.fg-palette-item').forEach((el) => {
    el.addEventListener('click', () => fgPaletteExec(Number(el.dataset.idx)));
  });
  const activeEl = list.querySelector('.fg-palette-item.active');
  if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
}

function fgPaletteFilter() {
  const q = document.getElementById('fg-palette-input').value.trim().toLowerCase();
  const terms = q.split(/\s+/).filter(Boolean);
  _fgPalState.filtered = !terms.length
    ? _fgPalState.entries
    : _fgPalState.entries.filter((e) =>
        terms.every((t) => e.label.toLowerCase().includes(t)));
  _fgPalState.active = 0;
  fgPaletteRender();
}

function fgPaletteExec(idx) {
  const entry = _fgPalState.filtered[idx];
  fgPaletteClose();
  if (entry) entry.fn();
}

function fgPaletteOpen() {
  fgPaletteEnsureDom();
  _fgPalState.entries = fgPaletteEntries();
  _fgPalState.filtered = _fgPalState.entries;
  _fgPalState.active = 0;
  _fgPalState.open = true;
  document.getElementById('fg-palette-backdrop').classList.remove('hidden');
  const input = document.getElementById('fg-palette-input');
  input.value = '';
  fgPaletteRender();
  input.focus();
}

function fgPaletteClose() {
  _fgPalState.open = false;
  const el = document.getElementById('fg-palette-backdrop');
  if (el) el.classList.add('hidden');
}

document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && !e.shiftKey && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    _fgPalState.open ? fgPaletteClose() : fgPaletteOpen();
    return;
  }
  if (mod && e.shiftKey && e.key.toLowerCase() === 'b') {
    e.preventDefault();
    fgToggleBooth();
    return;
  }
  if (!_fgPalState.open) return;
  if (e.key === 'Escape') { e.preventDefault(); fgPaletteClose(); }
  else if (e.key === 'ArrowDown') {
    e.preventDefault();
    _fgPalState.active = Math.min(_fgPalState.active + 1, _fgPalState.filtered.length - 1);
    fgPaletteRender();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    _fgPalState.active = Math.max(_fgPalState.active - 1, 0);
    fgPaletteRender();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    fgPaletteExec(_fgPalState.active);
  }
});
