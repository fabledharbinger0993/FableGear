/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / context_menu
   Right-click menus for the Record Room: tracks, playlists/folders, drives.
   One delegated listener; every write is an explicit click on a menu item,
   and destructive ones ask for confirmation first.
   ──────────────────────────────────────────────────────────────────────── */

let _fgCtxEl = null;

function _fgCtxDb() { return window._leDbSource || 'fablegear'; }
function _fgCtxIsFg() { return _fgCtxDb() === 'fablegear'; }
function _fgCtxQ() { return `db=${encodeURIComponent(_fgCtxDb())}`; }
function _fgCtxToast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }

function fgCloseContextMenu() {
  if (_fgCtxEl) { _fgCtxEl.remove(); _fgCtxEl = null; }
}

/* items: [{label, onClick, disabled, title, danger} | {sep:true}] */
function fgShowContextMenu(x, y, items) {
  fgCloseContextMenu();
  const menu = document.createElement('div');
  menu.className = 'fg-ctx-menu';
  menu.setAttribute('role', 'menu');
  items.forEach(it => {
    if (it.sep) {
      const hr = document.createElement('div');
      hr.className = 'fg-ctx-sep';
      menu.appendChild(hr);
      return;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'fg-ctx-item' + (it.danger ? ' fg-ctx-danger' : '');
    btn.setAttribute('role', 'menuitem');
    btn.textContent = it.label;
    if (it.title) btn.title = it.title;
    if (it.disabled) {
      btn.disabled = true;
    } else {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (it.keepOpen) { it.onClick(); return; }
        fgCloseContextMenu();
        it.onClick();
      });
    }
    menu.appendChild(btn);
  });
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 4)) + 'px';
  menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 4)) + 'px';
  _fgCtxEl = menu;
  menu.querySelector('.fg-ctx-item:not([disabled])')?.focus();
}

/* Second-level picker (playlist / folder choice) rendered in place. */
function _fgCtxPicker(x, y, title, choices, onPick, backItems) {
  const items = [{ label: '← Back', onClick: () => fgShowContextMenu(x, y, backItems), keepOpen: true }, { sep: true }];
  if (!choices.length) items.push({ label: `No ${title} available`, disabled: true, onClick() {} });
  choices.forEach(c => items.push({ label: c.label, onClick: () => onPick(c) }));
  fgShowContextMenu(x, y, items);
}

function _fgCtxTreeNodes(type) {
  return [...document.querySelectorAll(`#le-playlist-tree .le-tree-item[data-type="${type}"]`)].map(b => ({
    id: b.dataset.id,
    label: b.querySelector('.le-tree-label')?.textContent || b.dataset.id,
  }));
}

async function _fgCtxFetch(url, opts, failMsg) {
  try {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { _fgCtxToast(data.error || failMsg, 'error'); return null; }
    return data;
  } catch (_) {
    _fgCtxToast(failMsg, 'error');
    return null;
  }
}

/* ── Tracks ───────────────────────────────────────────────────────────── */

function _fgCtxTrackTargets(row) {
  const id = row.dataset.id;
  if (!_leSelectedTrackIds.has(id)) {
    _leSelectedTrackIds.clear();
    _leSelectedTrackIds.add(id);
    if (typeof _leSyncSelectionUi === 'function') _leSyncSelectionUi();
  }
  return [..._leSelectedTrackIds];
}

function _fgCtxTrackMenu(x, y, row) {
  const ids = _fgCtxTrackTargets(row);
  const n = ids.length;
  const plural = n === 1 ? 'track' : `${n} tracks`;
  const fg = _fgCtxIsFg();
  const fgOnly = fg ? '' : 'Only available for the FableGear library';
  const inPlaylist = _leActiveNodeType === 'playlist' && !!_leActivePlaylistId;
  const items = [];
  const rebuild = () => fgShowContextMenu(x, y, items);

  items.push(
    { label: 'Add to Chop Shop queue', onClick: () => {
        const paths = _leAllTracks.filter(t => _leSelectedTrackIds.has(String(t.id)) && t.file_path).map(t => t.file_path);
        if (!paths.length) { _fgCtxToast('Selected tracks have no file on disk.', 'error'); return; }
        stagingAddPath(paths);
      } },
    { label: 'Add to playlist…', keepOpen: true, onClick: () => _fgCtxPicker(x, y, 'playlists', _fgCtxTreeNodes('playlist'), async (c) => {
        const d = await _fgCtxFetch(`/api/library/playlists/${encodeURIComponent(c.id)}/tracks?${_fgCtxQ()}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ track_ids: ids }),
        }, 'Could not add tracks to playlist.');
        if (!d) return;
        if (d.added > 0) _fgCtxToast(`Added ${d.added} to “${c.label}”.`, 'success');
        else if (d.missing && d.missing.length) _fgCtxToast('Not in your Rekordbox library yet — import it there first.', 'info');
        else _fgCtxToast(`Already in “${c.label}”.`, 'info');
        if (typeof leLoadLibrary === 'function') leLoadLibrary();
      }, items) },
    { label: 'Remove from playlist', disabled: !inPlaylist, title: inPlaylist ? '' : 'Open a playlist first',
      onClick: () => { if (typeof leRemoveSelectionFromActivePlaylist === 'function') leRemoveSelectionFromActivePlaylist(); } },
    { sep: true },
    { label: 'Find duplicates', disabled: !fg || n !== 1, title: fg ? (n !== 1 ? 'Select a single track' : '') : fgOnly,
      onClick: async () => {
        const base = _leAllTracks.find(t => String(t.id) === ids[0]);
        const d = await _fgCtxFetch(`/api/library/tracks/${encodeURIComponent(ids[0])}/duplicates?${_fgCtxQ()}`, {}, 'Could not search for duplicates.');
        if (!d) return;
        if (!d.duplicates.length) { _fgCtxToast('No duplicates found.', 'info'); return; }
        leSetTrackView([base, ...d.duplicates].filter(Boolean), `Duplicates of “${base?.title || 'track'}”`);
        _fgCtxToast(`${d.duplicates.length} possible duplicate${d.duplicates.length === 1 ? '' : 's'} found.`, 'info');
      } },
    { label: 'Create duplicate', disabled: !fg, title: fgOnly, onClick: async () => {
        let made = 0;
        for (const id of ids) {
          const d = await _fgCtxFetch(`/api/library/tracks/${encodeURIComponent(id)}/duplicate?${_fgCtxQ()}`, { method: 'POST' }, 'Could not duplicate track.');
          if (d) made++;
        }
        if (made) { _fgCtxToast(`Created ${made} duplicate${made === 1 ? '' : 's'}.`, 'success'); _leTracksLoaded = false; await leLoadLibrary(); }
      } },
    { sep: true },
    { label: 'Remove from database', danger: true, disabled: !fg, title: fg ? 'The audio file stays on disk' : fgOnly, onClick: async () => {
        if (!confirm(`Remove ${plural} from the FableGear database?\n\nThe audio file${n === 1 ? '' : 's'} stay on disk.`)) return;
        let removed = 0;
        for (const id of ids) {
          const d = await _fgCtxFetch(`/api/library/tracks/${encodeURIComponent(id)}?${_fgCtxQ()}`, { method: 'DELETE' }, 'Could not remove track.');
          if (d) removed++;
        }
        if (removed) { _fgCtxToast(`Removed ${removed} from the database.`, 'success'); _leTracksLoaded = false; await leLoadLibrary(); }
      } },
  );
  rebuild();
}

/* ── Playlists / folders ──────────────────────────────────────────────── */

async function _fgCtxReloadTree() {
  _leTracksLoaded = false;
  if (typeof leLoadLibrary === 'function') await leLoadLibrary();
}

function _fgCtxPlaylistMenu(x, y, btn) {
  const id = btn.dataset.id;
  const isFolder = btn.dataset.type === 'folder';
  const name = btn.querySelector('.le-tree-label')?.textContent || 'playlist';
  const fg = _fgCtxIsFg();
  const fgOnly = fg ? '' : 'Only available for the FableGear library';
  const kind = isFolder ? 'folder' : 'playlist';
  const items = [];
  items.push(
    { label: `Delete ${kind}`, danger: true, onClick: async () => {
        if (!confirm(`Delete ${kind} “${name}”?${isFolder ? '\n\nItems inside move to the top level.' : '\n\nThe tracks themselves are not deleted.'}`)) return;
        const d = await _fgCtxFetch(`/api/library/playlists/${encodeURIComponent(id)}?${_fgCtxQ()}`, { method: 'DELETE' }, `Could not delete ${kind}.`);
        if (d) { _fgCtxToast(`Deleted “${name}”.`, 'success'); await _fgCtxReloadTree(); }
      } },
    { label: 'Add to another folder…', disabled: !fg, title: fgOnly, keepOpen: true, onClick: () => {
        const folders = [{ id: '', label: 'Top level' }, ..._fgCtxTreeNodes('folder').filter(f => f.id !== id)];
        _fgCtxPicker(x, y, 'folders', folders, async (c) => {
          const d = await _fgCtxFetch(`/api/library/playlists/${encodeURIComponent(id)}/move?${_fgCtxQ()}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parent_id: c.id || null }),
          }, `Could not move ${kind}.`);
          if (d) { _fgCtxToast(`Moved “${name}” to ${c.label}.`, 'success'); await _fgCtxReloadTree(); }
        }, items);
      } },
    { label: `Duplicate ${kind}`, disabled: !fg, title: fgOnly, onClick: async () => {
        const d = await _fgCtxFetch(`/api/library/playlists/${encodeURIComponent(id)}/duplicate?${_fgCtxQ()}`, { method: 'POST' }, `Could not duplicate ${kind}.`);
        if (d) { _fgCtxToast(`Duplicated “${name}”.`, 'success'); await _fgCtxReloadTree(); }
      } },
    { sep: true },
    { label: 'Export to thumb drive…', disabled: isFolder, title: isFolder ? 'Select a playlist, not a folder' : '', onClick: async () => {
        if (typeof leOpenExportModal !== 'function') return;
        await leOpenExportModal();
        document.querySelectorAll('.le-export-pl-cb').forEach(cb => { cb.checked = cb.value === id; });
        if (typeof _leExportUpdateSubmit === 'function') _leExportUpdateSubmit();
      } },
    { label: 'Export XML file', disabled: !fg, title: fgOnly, onClick: () => {
        const a = document.createElement('a');
        a.href = `/api/library/playlists/${encodeURIComponent(id)}/export.xml?${_fgCtxQ()}`;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        a.remove();
      } },
  );
  fgShowContextMenu(x, y, items);
}

/* ── External drives ──────────────────────────────────────────────────── */

const _FG_HIDDEN_DRIVES_KEY = 'fg_hidden_drives';

function fgHiddenDrives() {
  try { return new Set(JSON.parse(localStorage.getItem(_FG_HIDDEN_DRIVES_KEY) || '[]')); }
  catch (_) { return new Set(); }
}

/* Drop entries for drives that are no longer mounted, so a re-plugged drive reappears. */
function fgPruneHiddenDrives(currentPaths) {
  const keep = [...fgHiddenDrives()].filter(p => currentPaths.includes(p));
  try { localStorage.setItem(_FG_HIDDEN_DRIVES_KEY, JSON.stringify(keep)); } catch (_) { /* non-fatal */ }
  return new Set(keep);
}

function _fgCtxDriveMenu(x, y, card) {
  const path = card.dataset.path;
  const name = card.dataset.name || path;
  fgShowContextMenu(x, y, [
    { label: 'Eject from FableGear', title: 'Hide this drive in FableGear; it stays mounted', onClick: () => {
        const hidden = fgHiddenDrives(); hidden.add(path);
        try { localStorage.setItem(_FG_HIDDEN_DRIVES_KEY, JSON.stringify([...hidden])); } catch (_) { /* non-fatal */ }
        _fgCtxToast(`“${name}” ejected from FableGear.`, 'success');
        if (typeof leFsBrowse === 'function') leFsBrowse(null);
      } },
    { label: 'Eject from computer', danger: true, onClick: async () => {
        if (!confirm(`Eject “${name}” from the computer?\n\nMake sure nothing is still copying to it.`)) return;
        const d = await _fgCtxFetch('/api/drives/eject', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }),
        }, 'Could not eject drive.');
        if (d) { _fgCtxToast(`“${name}” ejected. It is safe to remove.`, 'success'); if (typeof leFsBrowse === 'function') leFsBrowse(null); }
      } },
  ]);
}

/* ── Wiring ───────────────────────────────────────────────────────────── */

document.addEventListener('contextmenu', (e) => {
  const t = e.target;
  if (!(t instanceof Element)) return;
  if (t.closest('.fg-ctx-menu')) { e.preventDefault(); return; }
  const vol = t.closest('.le-vol-card[data-path]');
  const tree = t.closest('#le-playlist-tree .le-tree-item[data-id]');
  const row = t.closest('.le-track-row[data-id]');
  const fsRow = t.closest('.le-fs-track-row[data-path]');
  if (vol) { e.preventDefault(); _fgCtxDriveMenu(e.clientX, e.clientY, vol); }
  else if (tree) { e.preventDefault(); _fgCtxPlaylistMenu(e.clientX, e.clientY, tree); }
  else if (row) { e.preventDefault(); _fgCtxTrackMenu(e.clientX, e.clientY, row); }
  else if (fsRow) {
    e.preventDefault();
    fgShowContextMenu(e.clientX, e.clientY, [
      { label: 'Add to Chop Shop queue', onClick: () => stagingAddPath(fsRow.dataset.path) },
    ]);
  } else fgCloseContextMenu();
});

document.addEventListener('click', (e) => { if (_fgCtxEl && !e.target.closest('.fg-ctx-menu')) fgCloseContextMenu(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') fgCloseContextMenu(); });
window.addEventListener('blur', fgCloseContextMenu);
window.addEventListener('resize', fgCloseContextMenu);
document.addEventListener('scroll', fgCloseContextMenu, true);
