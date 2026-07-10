/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / dnd
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 4518-5068
   ──────────────────────────────────────────────────────────────────────── */

/* ── Drag-and-drop path extraction ─────────────────────────────────────── */
/* Three strategies in priority order:                                        */
/* 1. file.path   — Chromium exposes the real OS path directly on the File   */
/*                  object when dropped from Finder. Most reliable on macOS.  */
/* 2. text/uri-list — standard HTML5 drag format: file:///path. Works when   */
/*                    Finder includes URI list data (not always guaranteed).   */
/* 3. text/plain  — fallback for terminal-style drags (absolute paths only). */
function _extractDropPath(e) {
  // Strategy 1: Chromium File.path — real filesystem path, no decoding needed
  const files = e.dataTransfer.files;
  if (files && files.length > 0 && files[0].path) {
    return files[0].path.replace(/\/$/, '');
  }
  // Strategy 2: text/uri-list (standard HTML5, Finder usually provides this)
  const uriList = e.dataTransfer.getData('text/uri-list');
  if (uriList) {
    const first = uriList.trim().split(/\r?\n/).find(l => /^file:\/\//i.test(l) && !l.startsWith('#'));
    if (first) return decodeURIComponent(first.replace(/^file:\/\/[^/]*/i, '').replace(/\/$/, ''));
  }
  // Strategy 3: text/plain fallback (terminal drags, absolute paths only)
  const plain = e.dataTransfer.getData('text/plain');
  if (plain) {
    const t = plain.trim();
    if (t.startsWith('/') || t.startsWith('~')) return t.replace(/\/$/, '');
  }
  return null;
}

/* ── Global drag-state class ────────────────────────────────────────────── */
/* Adds body.has-drag while a drag is in flight so CSS can highlight all     */
/* available drop zones simultaneously.                                       */
/* Also pre-fetches Finder's selection on the very first dragenter — at that  */
/* point Finder still has the dragged item selected (pywebview hasn't taken   */
/* full focus yet). This cached path is used as a fallback on drop, because   */
/* by the time drop fires pywebview has focused and Finder clears its         */
/* selection, causing the post-drop osascript query to return empty.          */
let _docDragCount = 0;
let _finderPathCache = null;   // prefetched on first dragenter, consumed on drop
let _finderPrefetching = false;
document.addEventListener('dragenter', () => {
  if (++_docDragCount === 1) {
    document.body.classList.add('has-drag');
    // Prefetch Finder selection while the item is still selected in Finder
    if (!_finderPrefetching) {
      _finderPrefetching = true;
      _finderPathCache = null;
      fetch('/api/finder-selection?source=drop')
        .then(r => r.json())
        .then(d => { _finderPathCache = d.path || null; })
        .catch(() => {})
        .finally(() => { _finderPrefetching = false; });
    }
  }
});
document.addEventListener('dragleave', () => {
  if (--_docDragCount <= 0) { _docDragCount = 0; document.body.classList.remove('has-drag'); }
});
// Capture-phase drop on document: prevent Chrome from navigating to the
// dropped file (its default behaviour) and reset the drag-state counter.
// Zone handlers still call e.preventDefault() individually — this is the
// safety net for any drop that lands outside a wired zone.
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => {
  e.preventDefault();
  _docDragCount = 0;
  document.body.classList.remove('has-drag');
}, true);

/* ── Folder pill zone system ────────────────────────────────────────────────
   Replaces single-path text inputs for all "source folder" type cards.
   Each zone uses CAPTURE-phase drag listeners so the inner <input> can never
   absorb the drop event before the zone sees it. A drag counter correctly
   tracks enter/leave across child elements without false positives.
   Dropped or typed paths appear as removable pills; duplicates are rejected.  */

function addFolderPill(pillsId, fullPath) {
  const container = document.getElementById(pillsId);
  if (!container) return;
  // Deduplicate — flash existing pill amber and bail rather than adding a copy
  const dupe = Array.from(container.querySelectorAll('.folder-pill'))
    .find(p => p.dataset.path === fullPath);
  if (dupe) {
    dupe.classList.remove('pill-already');
    void dupe.offsetWidth; // force reflow so re-adding the class restarts animation
    dupe.classList.add('pill-already');
    dupe.addEventListener('animationend', () => dupe.classList.remove('pill-already'), { once: true });
    return;
  }
  const name = fullPath.replace(/\/+$/, '').split('/').pop() || fullPath;
  const pill  = document.createElement('span');
  pill.className    = 'folder-pill';
  pill.title        = fullPath;
  pill.dataset.path = fullPath;
  pill.innerHTML    =
    `<span class="folder-pill-name">${name}</span>` +
    `<button class="folder-pill-x" type="button" title="Remove ${name}">✕</button>`;
  pill.querySelector('.folder-pill-x').addEventListener('click', () => pill.remove());
  container.appendChild(pill);
}

function getFolderPaths(pillsId) {
  const container = document.getElementById(pillsId);
  if (!container) return [];
  return Array.from(container.querySelectorAll('.folder-pill'))
    .filter(p => !p.classList.contains('library-pill'))
    .map(p => p.dataset.path).filter(Boolean);
}

/* Single-path drop zone — same glowing visual as setupFolderZone but populates
   a plain text input directly rather than a pills container.
   Used by: Relocate (old + new), Prune CSV, Organize target.              */
function setupSinglePathZone(zoneId, inputId) {
  const zone  = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  if (!zone || !input || zone.dataset.zoneReady) return;
  zone.dataset.zoneReady = '1';

  let _dc = 0;

  zone.addEventListener('dragenter', e => {
    e.preventDefault();
    if (++_dc === 1) zone.classList.add('drag-over');
  }, true);

  zone.addEventListener('dragleave', () => {
    if (--_dc <= 0) { _dc = 0; zone.classList.remove('drag-over'); }
  }, true);

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
  }, true);

  zone.addEventListener('drop', async e => {
    e.preventDefault();
    e.stopPropagation();
    _dc = 0;
    zone.classList.remove('drag-over');
    let path = _extractDropPath(e);
    if (path) {
      input.value = path;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      _markZoneDropSuccess(zone);
    } else if (e.dataTransfer.files.length > 0 || e.dataTransfer.types.length > 0) {
      path = await _recoverDroppedPath();
      if (path) {
        input.value = path;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        _markZoneDropSuccess(zone);
      } else {
        showToast('Could not read the dropped folder path.', 'error');
      }
    }
  }, true);
}

function setupFolderZone(zoneId, pillsId, textId) {
  const zone = document.getElementById(zoneId);
  const text = document.getElementById(textId);
  if (!zone || !text || zone.dataset.zoneReady) return;
  zone.dataset.zoneReady = '1';

  let _dc = 0; // drag-enter counter — reliably tracks nested enter/leave pairs

  const tryAdd = (val) => {
    const p = decodeURIComponent(val.replace(/^file:\/\/[^/]*/i, '')).trim().replace(/\/$/, '');
    if (p) { addFolderPill(pillsId, p); text.value = ''; }
  };

  // ── Capture-phase listeners ──────────────────────────────────────────────
  // Using capture (third arg = true) means the zone intercepts dragover/drop
  // BEFORE the child <input> element sees them. Without this, WebKit routes
  // the drop to the text input's native handler and it never reaches us.

  zone.addEventListener('dragenter', e => {
    e.preventDefault();
    if (++_dc === 1) zone.classList.add('drag-over');
  }, true);

  zone.addEventListener('dragleave', () => {
    if (--_dc <= 0) { _dc = 0; zone.classList.remove('drag-over'); }
  }, true);

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
  }, true);

  zone.addEventListener('drop', async e => {
    e.preventDefault();
    e.stopPropagation();
    _dc = 0;
    zone.classList.remove('drag-over');
    let path = _extractDropPath(e);
    if (path) {
      addFolderPill(pillsId, path);
      _markZoneDropSuccess(zone);
    } else if (e.dataTransfer.files.length > 0 || e.dataTransfer.types.length > 0) {
      path = await _recoverDroppedPath();
      if (path) {
        addFolderPill(pillsId, path);
        _markZoneDropSuccess(zone);
      } else {
        showToast('Could not read the dropped folder path.', 'error');
      }
    }
  }, true);

  // ── Keyboard / button add ────────────────────────────────────────────────
  text.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); tryAdd(text.value); }
  });
}

/* Per-zone add-button handlers */
function auditZoneAdd()     { const t = document.getElementById('audit-zone-text');     if (t?.value.trim()) { addFolderPill('audit-pills',     t.value.trim()); t.value = ''; } }
function processZoneAdd()   { const t = document.getElementById('process-zone-text');   if (t?.value.trim()) { addFolderPill('process-pills',   t.value.trim()); t.value = ''; } }
function dupesZoneAdd()     { const t = document.getElementById('dupes-zone-text');     if (t?.value.trim()) { addFolderPill('dupes-pills',     t.value.trim()); t.value = ''; } }
function normalizeZoneAdd() { const t = document.getElementById('normalize-zone-text'); if (t?.value.trim()) { addFolderPill('normalize-pills', t.value.trim()); t.value = ''; } }
function convertZoneAdd()   { const t = document.getElementById('convert-zone-text');   if (t?.value.trim()) { addFolderPill('convert-pills',   t.value.trim()); t.value = ''; } }
function importZoneAdd()    { const t = document.getElementById('import-zone-text');    if (t?.value.trim()) { addFolderPill('import-pills',    t.value.trim()); t.value = ''; } }
function organizeZoneAdd()  { const t = document.getElementById('organize-zone-text');  if (t?.value.trim()) { addFolderPill('organize-source-pills', t.value.trim()); t.value = ''; } }
function relocateOldZoneAdd() { const t = document.getElementById('relocate-old-zone-text'); if (t?.value.trim()) { addFolderPill('relocate-old-pills', t.value.trim()); t.value = ''; } }
function linkZoneAdd()      { const t = document.getElementById('link-zone-text');      if (t?.value.trim()) { addFolderPill('link-pills',      t.value.trim()); t.value = ''; } }
function noveltyZoneAdd()   { const t = document.getElementById('novelty-zone-text');   if (t?.value.trim()) { addFolderPill('novelty-pills',   t.value.trim()); t.value = ''; } }
function deadFilesZoneAdd() { const t = document.getElementById('dead-files-zone-text'); if (t?.value.trim()) { addFolderPill('dead-files-pills', t.value.trim()); t.value = ''; } }

/* Browse buttons — opens the native folder picker dialog.
   Prefers window.pywebview.api.pick_folder() when running inside the
   PyInstaller bundle (pywebview exposes the _Api class from main.py).
   Falls back to /api/pick-folder (osascript choose folder) in dev mode. */
async function _nativePick() {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
    try {
      const path = await window.pywebview.api.pick_folder();
      return path || null;
    } catch (e) {
      console.warn('[_nativePick] pywebview api error, falling back:', e);
    }
  }
  const r = await fetch('/api/pick-folder');
  const d = await r.json();
  return d.path || null;
}
async function pickFolderFor(pillsId) {
  const path = await _nativePick();
  if (path) {
    addFolderPill(pillsId, path);
  } else {
    // Native picker unavailable (non-macOS or pywebview not focused) — open file browser
    showToast('Use the file browser sidebar to navigate to your folder, then drag it here.', 'info');
    const panel = document.getElementById('fb-panel');
    if (panel && !panel.classList.contains('fb-open') && typeof toggleFileBrowser === 'function') {
      toggleFileBrowser();
    }
  }
}
async function pickPathFor(inputId) {
  const path = await _nativePick();
  if (path) {
    const el = document.getElementById(inputId);
    if (el) { el.value = path; el.dispatchEvent(new Event('input', { bubbles: true })); }
  }
}
/* Drop fallback — reads Finder's selection (which still holds the dragged item
   immediately after a drop), so the user never has to navigate twice.
   source=drop tells the server not to open a picker dialog if Finder returns
   nothing — on some drops pywebview focuses before osascript runs and Finder's
   selection is momentarily empty. Silently returns null rather than prompting. */
/* ── Library root indicator pill ────────────────────────────────────────────
   A dimmed, non-removable pill that marks the configured library root.
   Appears at the front of every pill zone that defaults to the music root.
   getFolderPaths() includes it naturally (it carries data-path).            */

let _libraryRoot = '';   // set by prefillDefaults once /api/config loads

function addLibraryPill(pillsId, path) {
  if (!path) return;
  const container = document.getElementById(pillsId);
  if (!container) return;
  // Update existing pill rather than duplicating
  const existing = container.querySelector('.library-pill');
  if (existing) {
    existing.dataset.path = path;
    const name = path.replace(/\/+$/, '').split('/').pop() || path;
    const nameEl = existing.querySelector('.folder-pill-name');
    if (nameEl) nameEl.textContent = `📍 ${name}`;
    existing.title = `Library root: ${path}`;
    return;
  }
  const name = path.replace(/\/+$/, '').split('/').pop() || path;
  const pill = document.createElement('span');
  pill.className = 'folder-pill library-pill';
  pill.title = `Library root: ${path}`;
  pill.dataset.path = path;
  pill.innerHTML = `<span class="folder-pill-name">📍 ${name}</span>`;
  container.insertBefore(pill, container.firstChild);
}

function _refreshLibraryPills(newRoot) {
  ['process-pills','dupes-pills','normalize-pills','convert-pills',
   'import-pills','link-pills','organize-source-pills'].forEach(id => addLibraryPill(id, newRoot));
}

async function setMusicRoot(newPath) {
  document.getElementById('sb-set-root-banner')?.remove();
  try {
    const r = await fetch('/api/config/set-music-root', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: newPath }),
    });
    const d = await r.json();
    if (d.ok) {
      _libraryRoot = newPath;
      _refreshLibraryPills(newPath);
      showToast(`Library root → ${newPath.split('/').pop() || newPath}`, 'success');
    } else {
      showToast(`Could not update root: ${d.error || 'unknown error'}`, 'error');
    }
  } catch (e) {
    showToast('Failed to update library root', 'error');
  }
}

function _promptSetLibraryRoot(newPath) {
  if (!newPath || newPath === _libraryRoot) return;
  const name = newPath.replace(/\/+$/, '').split('/').pop() || newPath;
  let banner = document.getElementById('sb-set-root-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'sb-set-root-banner';
    document.body.appendChild(banner);
  }
  Object.assign(banner.style, {
    position:'fixed', bottom:'calc(var(--log-h) + var(--scan-bar-h) + 14px)',
    left:'50%', transform:'translateX(-50%)', zIndex:'1200',
    padding:'11px 16px', borderRadius:'10px',
    background:'rgba(14,14,26,.97)',
    border:'1px solid rgba(129,140,248,.35)',
    boxShadow:'0 8px 32px rgba(0,0,0,.6)',
    display:'flex', alignItems:'center', gap:'12px',
    fontSize:'.84rem', color:'var(--text)',
    maxWidth:'min(660px,92vw)',
  });
  // sanitise path for inline onclick
  const safe = newPath.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  banner.innerHTML = `
    <span style="flex:1">📍 Organize moved files to <strong>${name}</strong>. Update the library root?</span>
    <button class="btn btn-neon" style="padding:5px 14px;font-size:.8rem;white-space:nowrap"
            onclick="setMusicRoot('${safe}')">Set Root</button>
    <button class="btn btn-ghost" style="padding:5px 10px;font-size:.8rem"
            onclick="document.getElementById('sb-set-root-banner')?.remove()">Dismiss</button>
  `;
}

async function _resolveDropPath() {
  // Use the path prefetched at dragenter — it was read before pywebview took
  // focus and Finder cleared its selection. If the prefetch is still in flight,
  // wait briefly for it; if it already finished, consume the cached value.
  if (_finderPrefetching) await new Promise(res => setTimeout(res, 200));
  if (_finderPathCache) {
    const p = _finderPathCache;
    _finderPathCache = null;
    return p;
  }
  // Cache miss (e.g. prefetch failed or was too slow) — fall back to a fresh query
  try {
    const r = await fetch('/api/finder-selection?source=drop');
    const d = await r.json();
    if (d.path) return d.path;
    // One retry after a short delay
    await new Promise(res => setTimeout(res, 400));
    const r2 = await fetch('/api/finder-selection?source=drop');
    const d2 = await r2.json();
    return d2.path || null;
  } catch { return null; }
}

function _markZoneDropSuccess(zone) {
  if (!zone) return;
  zone.classList.add('drop-success');
  zone.addEventListener('animationend', () => zone.classList.remove('drop-success'), { once: true });
}

async function _recoverDroppedPath() {
  const path = await _resolveDropPath();
  if (path) return path;
  showToast('Drop path was blocked by macOS. Choose the folder once to complete the drop.', 'neutral');
  return await _nativePick();
}

function runAudit() {
  const paths = getFolderPaths('audit-pills');
  if (!paths.length) { showToast('Add at least one folder path to scan.', 'warning'); return; }
  const p = new URLSearchParams();
  // 'paths' param — api_audit() uses first as --root, rest as --also-scan
  paths.forEach(path => p.append('paths', path));
  runCommand(`/api/run/audit?${p.toString()}`, 'Audit — Database + Physical Scan', null, true);
}

function runDeadFiles() {
  const paths = getFolderPaths('dead-files-pills');
  if (!paths.length) { showToast('Add at least one drive or folder to scan.', 'warning'); return; }
  const p = new URLSearchParams();
  paths.forEach(path => p.append('paths', path));
  runCommand(`/api/run/dead-files?${p.toString()}`, 'Dead File Scanner — Untracked Audio Files', null, true);
}

async function previewCanonicalPlan() {
  try {
    const res = await fetch('/api/library/integrity/canonical-paths/plan?max_groups=50', { cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || 'Could not load canonical plan preview.', 'error');
      return;
    }

    const lines = [];
    lines.push('Canonical Path Consolidation Plan (Read-only Preview)');
    lines.push('');
    lines.push(`Tracks scanned: ${data.total_tracks_scanned || 0}`);
    lines.push(`Conflict groups: ${data.total_conflict_groups || 0}`);
    lines.push(`Planned groups shown: ${data.planned_groups || 0}`);
    lines.push('');

    const plans = Array.isArray(data.plans) ? data.plans : [];
    if (!plans.length) {
      lines.push('No canonical-path conflicts detected.');
    } else {
      plans.forEach((plan, idx) => {
        const sig = plan.signature || {};
        const keeper = plan.keeper || {};
        const remove = Array.isArray(plan.remove_candidates) ? plan.remove_candidates : [];
        lines.push(`[${idx + 1}] ${sig.artist || '(unknown artist)'} — ${sig.title || '(untitled)'} (${sig.duration || 0}s)`);
        lines.push(`  Keep: ${keeper.path || '(missing path)'} [ContentID ${keeper.content_id || '?'}]`);
        lines.push(`  Estimated playlist slots to rethread: ${plan.estimated_playlist_slots_to_rethread || 0}`);
        remove.forEach((entry) => {
          lines.push(`  Remove candidate: ${entry.path || '(missing path)'} [ContentID ${entry.content_id || '?'}] refs=${entry.playlist_ref_count || 0}`);
        });
        lines.push('');
      });
    }

    openReportModal('Canonical Path Plan — Read-only Preview', lines.join('\n'), null);
  } catch (_) {
    showToast('Could not load canonical plan preview.', 'error');
  }
}

/* ── Legacy single-input drop zones (relocate, import, link, settings, etc.) */
function setupDropZone(input) {
  if (!input || input.dataset.dropReady) return;
  input.dataset.dropReady = '1';

  if (!input.parentElement.classList.contains('drop-wrap')) {
    const wrap = document.createElement('div');
    wrap.className = 'drop-wrap';
    if (input.style.flex) wrap.style.flex = input.style.flex;
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const badge = document.createElement('span');
    badge.className = 'drop-badge';
    badge.textContent = '⤵ drop';
    wrap.appendChild(badge);
  }

  // Attach listeners to the wrap (not the input) using capture phase so we
  // intercept events before WebKit routes them to the input's native handler.
  // This also means dropping on the ⤵ badge works correctly — the wrap sees
  // the event regardless of which child element the pointer is over.
  const wrap = input.closest('.drop-wrap');
  let _dc = 0; // drag counter — tracks nested enter/leave correctly

  wrap.addEventListener('dragenter', e => {
    e.preventDefault();
    if (++_dc === 1) wrap.classList.add('drop-active');
  }, true);

  wrap.addEventListener('dragleave', () => {
    if (--_dc <= 0) { _dc = 0; wrap.classList.remove('drop-active'); }
  }, true);

  wrap.addEventListener('dragover', e => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
  }, true);

  wrap.addEventListener('drop', async e => {
    e.preventDefault();
    e.stopPropagation();
    _dc = 0;
    wrap.classList.remove('drop-active');
    let path = _extractDropPath(e);
    if (path) {
      input.value = path;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      wrap.classList.add('drop-filled');
      wrap.addEventListener('animationend', () => wrap.classList.remove('drop-filled'), { once: true });
    } else if (e.dataTransfer.files.length > 0 || e.dataTransfer.types.length > 0) {
      path = await _recoverDroppedPath();
      if (path) {
        input.value = path;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        wrap.classList.add('drop-filled');
        wrap.addEventListener('animationend', () => wrap.classList.remove('drop-filled'), { once: true });
      } else {
        showToast('Could not read the dropped folder path.', 'error');
      }
    }
  }, true);
}

function setupAllDropZones() {
  // No legacy plain inputs remain — all zones now use setupSinglePathZone / setupFolderZone
}

/* ── Multi-path textarea drop zone: dropped paths are appended as new lines ── */
function setupMultiDropZone(textarea) {
  if (!textarea || textarea.dataset.dropReady) return;
  textarea.dataset.dropReady = '1';

  // Ensure a .drop-wrap parent exists (same structure as setupDropZone)
  if (!textarea.parentElement.classList.contains('drop-wrap')) {
    const wrap  = document.createElement('div');
    wrap.className = 'drop-wrap';
    textarea.parentNode.insertBefore(wrap, textarea);
    wrap.appendChild(textarea);
    const badge  = document.createElement('span');
    badge.className   = 'drop-badge';
    badge.textContent = '⤵ drop';
    badge.style.top   = '8px';
    wrap.appendChild(badge);
  }

  const wrap = textarea.closest('.drop-wrap');
  let _dc = 0;

  wrap.addEventListener('dragenter', e => {
    e.preventDefault();
    if (++_dc === 1) wrap.classList.add('drop-active');
  }, true);
  wrap.addEventListener('dragleave', () => {
    if (--_dc <= 0) { _dc = 0; wrap.classList.remove('drop-active'); }
  }, true);
  wrap.addEventListener('dragover', e => {
    e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'copy';
  }, true);
  wrap.addEventListener('drop', async e => {
    e.preventDefault(); e.stopPropagation();
    _dc = 0; wrap.classList.remove('drop-active');
    let path = _extractDropPath(e);
    if (!path && (e.dataTransfer.files.length > 0 || e.dataTransfer.types.length > 0)) {
      path = await _recoverDroppedPath();
    }
    if (path) {
      const existing = textarea.value.trim();
      textarea.value  = existing ? existing + '\n' + path : path;
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      _markZoneDropSuccess(wrap);
    }
  }, true);
}

