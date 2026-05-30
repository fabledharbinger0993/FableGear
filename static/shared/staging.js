/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / staging
   Staging queue: the deliberate hand-off between Record Room and Chop Shop.
   Paths added here become the default scope for all Chop Shop tool operations.
   ──────────────────────────────────────────────────────────────────────── */

/* ── State ─────────────────────────────────────────────────────────────────── */

let _stagingItems = [];   // [{path, name, is_dir, added_at}]
let _stagingPanelOpen = false;

/* ── Init ──────────────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  _stagingSync();
});

async function _stagingSync() {
  try {
    const res = await fetch('/api/staging');
    if (!res.ok) return;
    _stagingItems = await res.json();
    _stagingRender();
    _stagingUpdateBadges();
  } catch (_) { /* non-fatal */ }
}

/* ── Public API ────────────────────────────────────────────────────────────── */

async function stagingAddPath(pathOrPaths) {
  const paths = Array.isArray(pathOrPaths) ? pathOrPaths : [pathOrPaths];
  if (!paths.length) return;
  try {
    const res = await fetch('/api/staging/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths }),
    });
    if (!res.ok) return;
    _stagingItems = await res.json();
    _stagingRender();
    _stagingUpdateBadges();
    _stagingFlashBadge();
    showToast(`Staged ${paths.length === 1 ? _lastName(paths[0]) : paths.length + ' items'}`, 'info');
  } catch (_) { /* non-fatal */ }
}

async function stagingRemovePath(path) {
  try {
    const res = await fetch('/api/staging/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) return;
    _stagingItems = await res.json();
    _stagingRender();
    _stagingUpdateBadges();
  } catch (_) { /* non-fatal */ }
}

async function stagingClear() {
  if (!_stagingItems.length) return;
  if (!confirm('Clear all staged items?')) return;
  try {
    const res = await fetch('/api/staging/clear', { method: 'POST' });
    if (!res.ok) return;
    _stagingItems = await res.json();
    _stagingRender();
    _stagingUpdateBadges();
  } catch (_) { /* non-fatal */ }
}

async function stagingSaveBatch() {
  const name = prompt('Save this staging queue as a batch named:');
  if (!name?.trim()) return;
  try {
    const res = await fetch('/api/staging/batch/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }),
    });
    if (res.ok) showToast(`Batch "${name.trim()}" saved`, 'info');
  } catch (_) { /* non-fatal */ }
}

async function stagingLoadBatchMenu() {
  try {
    const res = await fetch('/api/staging/batch');
    if (!res.ok) return;
    const batches = await res.json();
    const names = Object.keys(batches);
    if (!names.length) { showToast('No saved batches yet.', 'info'); return; }
    const chosen = prompt(`Load which batch?\n\n${names.join('\n')}`);
    if (!chosen?.trim() || !batches[chosen.trim()]) return;
    const loadRes = await fetch('/api/staging/batch/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: chosen.trim() }),
    });
    if (!loadRes.ok) return;
    _stagingItems = await loadRes.json();
    _stagingRender();
    _stagingUpdateBadges();
    showToast(`Loaded batch "${chosen.trim()}"`, 'info');
  } catch (_) { /* non-fatal */ }
}

/* Return the staged paths as a flat array of strings. */
function stagingGetPaths() {
  return _stagingItems.map(i => i.path);
}

function stagingIsEmpty() {
  return _stagingItems.length === 0;
}

/* ── Panel toggle ──────────────────────────────────────────────────────────── */

function toggleStagingPanel() {
  _stagingPanelOpen = !_stagingPanelOpen;
  const panel = document.getElementById('staging-panel');
  const btn   = document.getElementById('lp-staging-btn');
  if (panel) panel.classList.toggle('staging-panel-open', _stagingPanelOpen);
  if (btn)   btn.classList.toggle('active', _stagingPanelOpen);
  if (_stagingPanelOpen) _stagingRender();
}

function closeStagingPanel() {
  _stagingPanelOpen = false;
  document.getElementById('staging-panel')?.classList.remove('staging-panel-open');
  document.getElementById('lp-staging-btn')?.classList.remove('active');
}

/* ── Render ────────────────────────────────────────────────────────────────── */

function _stagingRender() {
  const list    = document.getElementById('staging-list');
  const hint    = document.getElementById('staging-hint-empty');
  const goBtn   = document.getElementById('staging-goto-chop');
  if (!list) return;

  if (!_stagingItems.length) {
    list.innerHTML = '';
    if (hint)  hint.style.display = '';
    if (goBtn) goBtn.disabled = true;
    return;
  }

  if (hint)  hint.style.display = 'none';
  if (goBtn) goBtn.disabled = false;

  list.innerHTML = _stagingItems.map(item => {
    const icon = item.is_dir ? '📁' : '🎵';
    const safePath = _escAttr ? _escAttr(item.path) : item.path.replace(/"/g, '&quot;');
    const safeName = _esc ? _esc(item.name) : item.name;
    return `
      <div class="staging-item" data-path="${safePath}">
        <span class="staging-item-icon">${icon}</span>
        <div class="staging-item-info">
          <span class="staging-item-name" title="${safePath}">${safeName}</span>
          <span class="staging-item-path">${safePath}</span>
        </div>
        <button type="button" class="staging-item-remove" onclick="stagingRemovePath('${safePath}')" title="Remove from queue">✕</button>
      </div>
    `;
  }).join('');
}

function _stagingUpdateBadges() {
  const n = _stagingItems.length;
  const badge = document.getElementById('staging-nav-badge');
  const count = document.getElementById('staging-count');
  if (badge) {
    badge.textContent = n;
    badge.style.display = n > 0 ? '' : 'none';
  }
  if (count) count.textContent = n === 1 ? '1 item' : `${n} items`;
}

function _stagingFlashBadge() {
  const badge = document.getElementById('staging-nav-badge');
  if (!badge) return;
  badge.classList.remove('staging-badge-flash');
  void badge.offsetWidth;  // reflow to restart animation
  badge.classList.add('staging-badge-flash');
}

/* ── Auto-populate tool pill zones from staging ────────────────────────────── */

/*
 * Call this from a tool card to pre-fill its pill zone with staged items.
 * pillsId: DOM id of the pills container (e.g. 'process-pills')
 * Only adds items not already present. Does nothing if staging is empty.
 */
function stagingPopulatePills(pillsId) {
  if (stagingIsEmpty()) return;
  _stagingItems.forEach(item => {
    if (typeof addFolderPill === 'function') addFolderPill(pillsId, item.path);
  });
}

/* ── Helpers ───────────────────────────────────────────────────────────────── */

function _lastName(path) {
  return path.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || path;
}
