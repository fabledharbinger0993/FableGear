/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / library_mode (Hardened)
   ──────────────────────────────────────────────────────────────────────── */

// This module is loaded before some classic scripts. Keep mode state on
// window so module/classic ordering never throws ReferenceError.
function _leGetState(key, fallback) {
  if (window[key] === undefined) window[key] = fallback;
  return window[key];
}

function _leSetState(key, value) {
  window[key] = value;
  return value;
}

// Helper: Securely creates a row element
function _leCreateRowElement(t, col, type = 'track') {
    const div = document.createElement('div');
    div.className = `le-split-track le-split-track-${col}`;
    div.dataset.path = t.file_path || t.path || '';

    const title = document.createElement('span');
    title.className = 'le-split-title';
    title.textContent = t.title || t.filename || t.file_path || '—';
    div.appendChild(title);

    if (t.artist || t.bpm || t.drive_name) {
        const meta = document.createElement('span');
        meta.className = 'le-split-meta';
        const drive = t.drive_name ? `${t.drive_name} · ` : '';
        meta.textContent = `${drive}${t.artist || ''}${t.bpm ? ' · ' + t.bpm + ' BPM' : ''}`;
        div.appendChild(meta);
    }
    
    // Novelty rows: membership color coding + drag-to-import + stage button.
    // blue = missing from FableGear · yellow = missing from Rekordbox ·
    // green = in neither database.
    if (type === 'novelty') {
        const inFg = !!t.in_fablegear;
        const inRb = !!t.in_rekordbox;
        div.classList.add(
            (!inFg && !inRb) ? 'le-novel-green' : (!inFg ? 'le-novel-blue' : 'le-novel-yellow')
        );
        div.draggable = true;
        div.addEventListener('dragstart', (ev) => {
            ev.dataTransfer.setData('application/x-fablegear-novelty', t.path || '');
            ev.dataTransfer.effectAllowed = 'copy';
        });
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'le-stage-btn le-split-stage';
        btn.textContent = '+Q';
        btn.title = 'Add to the staging queue';
        btn.onclick = () => stagingAddPath(t.path);
        div.appendChild(btn);
    }
    return div;
}

function setLibraryMode(mode, fsRootPath = null) {
  _leSetState('_leMode', mode);
  if (fsRootPath) _leSetState('_leFsCurrentPath', fsRootPath);

  // Update toggle buttons
  document.querySelectorAll('.le-mode-btn').forEach(b => {
    b.classList.toggle('le-mode-active', b.dataset.mode === mode);
  });

  // Show/hide sections
  const filterBar   = document.getElementById('lib-filter-bar');
  const sidebarDb   = document.getElementById('le-sidebar-db');
  const sidebarFs   = document.getElementById('le-sidebar-fs');
  const trackHeader = document.querySelector('.le-track-header');
  const trackList   = document.getElementById('le-track-list');
  const splitView   = document.getElementById('le-split-view');
  const statusBar   = document.querySelector('.le-status-bar');

  if (mode === 'db') {
    if (filterBar)   filterBar.style.display = '';
    if (sidebarDb)   sidebarDb.style.display = '';
    if (sidebarFs)   sidebarFs.style.display = 'none';
    if (trackHeader) trackHeader.style.display = '';
    if (trackList)   trackList.style.display  = '';
    if (splitView)   splitView.style.display  = 'none';
    if (statusBar)   statusBar.style.display  = '';
    // Reload from rekordbox if not yet loaded
    if (!_leGetState('_leTracksLoaded', false)) leLoadLibrary();

  } else if (mode === 'fs') {
    if (filterBar)   filterBar.style.display = 'none';
    if (sidebarDb)   sidebarDb.style.display = 'none';
    if (sidebarFs)   sidebarFs.style.display = '';
    if (trackHeader) trackHeader.style.display = '';
    if (trackList)   trackList.style.display  = '';
    if (splitView)   splitView.style.display  = 'none';
    if (statusBar)   statusBar.style.display  = '';
    leFsBrowse(_leGetState('_leFsCurrentPath', null));

  } else if (mode === 'split') {
    if (filterBar)   filterBar.style.display = 'none';
    if (sidebarDb)   sidebarDb.style.display = 'none';
    if (sidebarFs)   sidebarFs.style.display = 'none';
    if (trackHeader) trackHeader.style.display = 'none';
    if (trackList)   trackList.style.display  = 'none';
    if (splitView)   splitView.style.display  = '';
    if (statusBar)   statusBar.style.display  = 'none';
    leLoadSplitView();
  }
}

function _leStageFsCurrentFolder() {
  const currentPath = _leGetState('_leFsCurrentPath', null);
  if (currentPath && typeof stagingAddPath === 'function') {
    stagingAddPath(currentPath);
  }
}

function setLeDbSource(source) {
  if (source !== 'fablegear' && source !== 'local' && source !== 'device') return;
  _leSetState('_leDbSource', source);
  document.querySelectorAll('.le-db-btn').forEach(b => {
    b.classList.toggle('le-db-active', b.dataset.db === source);
  });
  // Device DB tracks have SoundCloud URIs — warn the user
  const notice = document.getElementById('le-db-notice');
  if (notice) {
    notice.textContent = source === 'device'
      ? 'Device DB: tracks use SoundCloud URIs and cannot be played here.'
      : '';
    notice.style.display = source === 'device' ? '' : 'none';
  }
  // Reload library data with the new source
  _leSetState('_leTracksLoaded', false);
  if (_leGetState('_leMode', 'db') === 'db') leLoadLibrary();
}

// FableGear's own database is the default Record Room source.
_leGetState('_leDbSource', 'fablegear');

// Sync the FableGear database against the music library, then reload.
async function leSyncLibrary() {
  const btn = document.getElementById('le-sync-btn');
  const status = document.getElementById('le-sync-status');
  if (btn) { btn.disabled = true; btn.textContent = '↻ Syncing…'; }
  try {
    const r = await fetch('/api/library/db/sync', { method: 'POST' });
    if (r.status === 409) { if (status) status.textContent = 'sync already running…'; }
    const poll = setInterval(async () => {
      const j = await (await fetch('/api/library/db/sync-status')).json();
      if (status) {
        status.textContent = j.running
          ? (j.phase === 'processing' ? `processing ${j.done}/${j.total}…` : j.phase + '…')
          : (j.error ? ('error: ' + j.error)
             : (j.result ? `+${j.result.imported_new} new · ${j.result.imported_updated} updated · ${j.result.moved} moved` : 'done'));
      }
      if (!j.running) {
        clearInterval(poll);
        if (btn) { btn.disabled = false; btn.textContent = '↻ Sync'; }
        setLeDbSource('fablegear');  // reload from refreshed DB
      }
    }, 700);
  } catch (e) {
    if (status) status.textContent = 'sync failed: ' + e.message;
    if (btn) { btn.disabled = false; btn.textContent = '↻ Sync'; }
  }
}
window.leSyncLibrary = leSyncLibrary;

/* ── Filesystem browse mode ──────────────────────────────────────────────── */

async function leFsBrowse(path) {
  const trackList = document.getElementById('le-track-list');
  const folderList = document.getElementById('le-fs-folder-list');
  if (!trackList) return;
  trackList.innerHTML = '<div class="le-empty-state"><div class="le-empty-music-icon">⏳</div><div>Loading…</div></div>';
  if (folderList) folderList.innerHTML = '';

  // Keep browsing shallow by default so users can navigate folders deliberately.
  const base = path
    ? `/api/library/fs-browse?path=${encodeURIComponent(path)}`
    : '/api/library/fs-browse';

  let data;
  try {
    const res = await fetch(base);
    if (!res.ok) throw new Error(await res.text());
    data = await res.json();
  } catch (e) {
    trackList.innerHTML = `<div class="le-empty-state"><div>⚠ Could not load: ${e.message}</div></div>`;
    return;
  }

  _leSetState('_leFsCurrentPath', data.path);

  // ── Platform volume root — render drive picker cards ───────────────────
  if (data.is_volumes_root) {
    if (folderList) folderList.innerHTML = '';
    const vols = data.volumes || [];
    if (!vols.length) {
      trackList.innerHTML = '<div class="le-empty-state"><div class="le-empty-music-icon">💿</div><div>No external drives found</div></div>';
      return;
    }
    const cards = '<div class="le-vol-grid">' + vols.map(v => {
      const pioneer = v.has_pioneer_db ? '<span class="le-vol-badge le-vol-badge--pioneer" title="Pioneer DB found">Pioneer DB</span>' : '';
      const freeStr = v.free_gb != null ? `${v.free_gb} GB free` : '';
      const totalStr = v.total_gb != null ? `/ ${v.total_gb} GB` : '';
      const rec = v.recommended_home ? '<span class="le-vol-badge" title="Largest detected library">Recommended</span>' : '';
      const countStr = v.audio_estimate > 0 ? `${v.audio_estimate.toLocaleString()} audio files` : 'No music files found';
      return `<div class="le-vol-card" onclick="leFsBrowse('${_escPath(v.path)}')" title="Browse ${_esc(v.name)}">
        <div class="le-vol-icon">💿</div>
        <div class="le-vol-name">${_esc(v.name)}</div>
        <div class="le-vol-meta">${countStr}</div>
        <div class="le-vol-disk">${freeStr}${freeStr && totalStr ? ' ' : ''}${totalStr}</div>
        ${pioneer}
        ${rec}
        <button type="button" class="le-vol-stage-btn" onclick="event.stopPropagation(); stagingAddPath('${_escAttr(v.path)}')" title="Stage entire drive for Chop Shop">+ Queue</button>
      </div>`;
    }).join('') + '</div>';
    const recommended = vols.find(v => v.recommended_home) || vols[0];
    const summary = recommended
      ? `<div class="le-vol-root-summary">Recommended home drive: <strong>${_esc(recommended.name)}</strong>${recommended.recommended_archive_root ? ` — archive → ${_esc(recommended.recommended_archive_root)}` : ''}</div>`
      : '';
    const groupedRows = data.grouped_by_drive ? _leFsGroupedTrackRows(data.tracks || [], vols) : '';
    trackList.innerHTML = cards + summary + groupedRows;
    if (groupedRows) _bindFsTrackPlay();
    return;
  }

  // shallow browsing, so navigation is always preserved.
  if (folderList) {
    let crumbHtml = '';
    if (data.parent) {
      crumbHtml += `<div class="le-fs-up" onclick="leFsBrowse('${_escPath(data.parent)}')">↑ Up</div>`;
    }
    crumbHtml += `<div class="le-fs-crumb-path" title="${data.path}">${data.path.replace(data.music_root, '⌂')}</div>`;

    // Fetch immediate subdirs separately so sidebar stays navigable.
    let dirsHtml = '';
    try {
      const dirRes = await fetch(`/api/library/fs-browse?path=${encodeURIComponent(data.path)}`);
      if (dirRes.ok) {
        const dirData = await dirRes.json();
        dirsHtml = (dirData.subdirs || []).map(d => `
          <div class="le-tree-item le-fs-dir" onclick="leFsBrowse('${_escPath(d.path)}')">
            <span class="le-tree-icon">📁</span>
            <span class="le-tree-label">${_esc(d.name)}</span>
            <span class="le-tree-count">${d.audio_count || ''}</span>
            <button type="button" class="le-stage-folder-btn" onclick="event.stopPropagation(); stagingAddPath('${_escAttr(d.path)}')" title="Stage folder for Chop Shop">+Q</button>
          </div>
        `).join('');
      }
    } catch (_) { /* sidebar is a nice-to-have */ }

    folderList.innerHTML = crumbHtml + dirsHtml;
  }

  // Update track list
  const tracks = data.tracks || [];
  if (tracks.length === 0) {
    trackList.innerHTML = '<div class="le-empty-state"><div class="le-empty-music-icon">📂</div><div>No audio files found in this folder</div></div>';
    return;
  }

  const truncMsg = data.truncated
    ? `<div class="le-fs-truncated">Showing first ${tracks.length} of ${data.track_count.toLocaleString()} tracks — navigate into a subfolder for a focused view</div>`
    : '';

  const rows = tracks.map((t, i) => _leFsTrackRow(t, i)).join('');
  trackList.innerHTML = truncMsg + rows;
  _bindFsTrackPlay();
}

function _leFsTrackRow(t, idx) {
  const dur = t.duration_s ? _fmtDur(t.duration_s) : '—';
  const folder = t.path ? t.path.split('/').slice(-2, -1)[0] || '' : '';
  const safePath = _escAttr(t.path);
  return `
    <div class="le-track-row le-fs-track-row" data-path="${safePath}">
      <div class="le-col le-col-play">
        <button type="button" class="le-play-btn fs-play-btn" data-path="${safePath}" title="Play">▶</button>
      </div>
      <div class="le-col le-col-stage">
        <button type="button" class="le-stage-btn" onclick="stagingAddPath('${safePath}')" title="Stage for Chop Shop">+Q</button>
      </div>
      <div class="le-col le-col-num">${idx + 1}</div>
      <div class="le-col le-col-title" title="${safePath}">${_esc(t.title)}</div>
      <div class="le-col le-col-artist">${_esc(t.artist)}</div>
      <div class="le-col le-col-album">${_esc(t.album) || _esc(folder)}</div>
      <div class="le-col le-col-bpm">${t.bpm || '—'}</div>
      <div class="le-col le-col-key">${typeof fgKeyBadge === 'function' ? fgKeyBadge(t.key) : (t.key || '—')}</div>
      <div class="le-col le-col-dur">${dur}</div>
      <div class="le-col le-col-date">—</div>
    </div>
  `;
}

function _leFsGroupedTrackRows(tracks, volumes) {
  if (!tracks.length) {
    return '<div class="le-empty-state"><div class="le-empty-music-icon">🎚️</div><div>No music files found across connected drives</div></div>';
  }
  const volumeMap = new Map((volumes || []).map(v => [v.path, v]));
  let currentDrive = null;
  let rowIndex = 0;
  return tracks.map((t) => {
    let html = '';
    if (t.drive_path !== currentDrive) {
      currentDrive = t.drive_path;
      const meta = volumeMap.get(t.drive_path) || {};
      const count = meta.audio_estimate ? `${Number(meta.audio_estimate).toLocaleString()} tracks` : '';
      html += `<div class="le-drive-divider"><strong>${_esc(t.drive_name || 'Drive')}</strong><span>${count}</span></div>`;
    }
    rowIndex += 1;
    html += _leFsTrackRow(t, rowIndex - 1);
    return html;
  }).join('');
}

function _bindFsTrackPlay() {
  document.querySelectorAll('.fs-play-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const path = btn.dataset.path;
      const streamUrl = `/api/fs/stream?path=${encodeURIComponent(path)}`;
      _playFsTrack(streamUrl, btn);
    });
  });
}

function _playFsTrack(streamUrl, triggerBtn) {
  // Use the Library Editor player element (added to index.html)
  const playerEl = document.getElementById('le-player-audio') ||
                   document.getElementById('le-preview-audio') ||
                   document.getElementById('audio-player');

  if (playerEl && playerEl.tagName === 'AUDIO') {
    // Single audio focus: silence any performance deck and release the library
    // inline-preview's row ownership before this filesystem track takes over the
    // shared audio element.
    window.deckPauseAll?.();
    window.leClearInlinePreview?.();
    // Stop other play buttons
    document.querySelectorAll('.fs-play-btn').forEach(b => b.textContent = '▶');
    if (triggerBtn) triggerBtn.textContent = '⏸';
    playerEl.src = streamUrl;
    playerEl.play().catch(() => {});
    playerEl.onended = () => { if (triggerBtn) triggerBtn.textContent = '▶'; };
    return;
  }
  // Fallback: open in new tab
  window.open(streamUrl, '_blank');
}

/* ── Split view ──────────────────────────────────────────────────────────── */

async function leLoadSplitView() {
  const lists = {
      fg: document.getElementById('le-split-list-fablegear'),
      rb: document.getElementById('le-split-list-rekordbox'),
      novel: document.getElementById('le-split-list-novelty')
  };
  const cntFg = document.getElementById('le-split-cnt-fablegear');
  const cntRb = document.getElementById('le-split-cnt-rekordbox');
  const cntNovel = document.getElementById('le-split-cnt-novelty');

  Object.values(lists).forEach(el => { if (el) el.textContent = '⏳ Scanning drives…'; });

  try {
    const res = await fetch('/api/library/split-data');
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    // Update Counts
    if (cntFg) cntFg.textContent = `(${data.fablegear_count})`;
    if (cntRb) cntRb.textContent = `(${data.rekordbox_count})`;
    if (cntNovel) cntNovel.textContent = data.truncated
      ? `(${data.novelty_count} of ~${data.fs_scanned.toLocaleString()} scanned)`
      : `(${data.novelty_count})`;

    // Secure Render Helper
    const render = (container, items, type, emptyMsg, errMsg) => {
        if (!container) return;
        container.innerHTML = '';
        if (errMsg) { container.textContent = `⚠ ${errMsg}`; return; }
        if (!items || items.length === 0) {
            container.textContent = emptyMsg;
            return;
        }
        items.forEach(t => container.appendChild(_leCreateRowElement(t, type, type)));
    };

    render(lists.fg, data.fablegear, 'fablegear',
           'FableGear database is empty — sync your library or drop tracks here', data.fablegear_error);
    render(lists.rb, data.rekordbox, 'rekordbox', 'No tracks found', data.rekordbox_error);
    render(lists.novel, data.novelty, 'novelty', 'Both databases know every file on disk ✓', null);

  } catch (e) {
    Object.values(lists).forEach(el => { if (el) el.textContent = `⚠ ${e.message}`; });
  }
}

/* ── Drag novelty tracks onto the FableGear column to import them ─────────── */
function leSplitDragOver(ev) {
  if (![...ev.dataTransfer.types].includes('application/x-fablegear-novelty')) return;
  ev.preventDefault();
  ev.dataTransfer.dropEffect = 'copy';
  ev.currentTarget.classList.add('le-split-drop-hot');
}

function leSplitDragLeave(ev) {
  ev.currentTarget.classList.remove('le-split-drop-hot');
}

async function leSplitDropImport(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('le-split-drop-hot');
  const path = ev.dataTransfer.getData('application/x-fablegear-novelty');
  if (!path) return;
  try {
    const res = await fetch('/api/library/db/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: [path] }),
    });
    const stats = await res.json();
    if (!res.ok) throw new Error(stats.error || res.statusText);
    if (typeof showToast === 'function') {
      const what = stats.new_files ? 'imported' : (stats.updated_files ? 'updated' : 'already in the database');
      showToast(`${path.split('/').pop()} ${what}.`, 'success');
    }
    leLoadSplitView();
  } catch (e) {
    if (typeof showToast === 'function') showToast(`Import failed: ${e.message}`, 'error');
  }
}

/* ── Drag a track from the file browser (or novelty list) onto the open
   library view to import it — and add it to the active playlist if one is
   selected. Covers the gap where dropping a file-browser item onto an open
   playlist previously did nothing: no drop zone anywhere accepted the file
   browser's plain-path drag format except the Integrated view's novelty
   column, which only imports (it never adds to whatever playlist you had
   open, since Integrated view has no concept of "the active playlist"). */
function _leDragPathFromEvent(ev) {
  const dt = ev.dataTransfer;
  if (!dt) return null;
  const novelty = dt.getData('application/x-fablegear-novelty');
  if (novelty) return novelty;
  const plain = dt.getData('text/plain');
  if (plain && plain.startsWith('/')) return plain;
  return null;
}

function _leDragHasImportablePath(ev) {
  // getData() is only readable on drop, not dragover — type presence is all
  // we can check here, same restriction leSplitDragOver already works around.
  const types = [...(ev.dataTransfer?.types || [])];
  return types.includes('application/x-fablegear-novelty') || types.includes('text/plain');
}

function leTrackListDragOver(ev) {
  if (_leGetState('_leMode', 'db') !== 'db') return;
  if (!_leDragHasImportablePath(ev)) return;
  ev.preventDefault();
  ev.dataTransfer.dropEffect = 'copy';
  ev.currentTarget.classList.add('le-split-drop-hot');
}

function leTrackListDragLeave(ev) {
  ev.currentTarget.classList.remove('le-split-drop-hot');
}

async function leTrackListDrop(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('le-split-drop-hot');
  if (_leGetState('_leMode', 'db') !== 'db') return;
  const path = _leDragPathFromEvent(ev);
  if (!path) return;

  const playlistId   = typeof _leActivePlaylistId   !== 'undefined' ? _leActivePlaylistId   : null;
  const playlistName = typeof _leActivePlaylistName !== 'undefined' ? _leActivePlaylistName : '';

  try {
    const res = await fetch('/api/library/db/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: [path] }),
    });
    const stats = await res.json();
    if (!res.ok) throw new Error(stats.error || res.statusText);
    const what = stats.new_files ? 'Imported' : (stats.updated_files ? 'Updated' : 'Already in your library');
    const contentId = stats.content_ids && stats.content_ids[path];

    if (playlistId && contentId) {
      const addRes = await fetch(`/api/library/playlists/${playlistId}/tracks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: contentId }),
      });
      const addData = await addRes.json().catch(() => ({}));
      if (typeof showToast === 'function') {
        if (addRes.ok && addData.added > 0) {
          showToast(`${what} · added to "${playlistName}".`, 'success');
        } else if (addRes.ok) {
          showToast(`${what} · already in "${playlistName}".`, 'info');
        } else {
          showToast(`${what}, but could not add to "${playlistName}": ${addData.error || 'unknown error'}`, 'error');
        }
      }
    } else if (typeof showToast === 'function') {
      showToast(`${what} into your library.` + (playlistId ? '' : ' Open a playlist to add it there too.'), 'success');
    }
    await leLoadLibrary();
  } catch (e) {
    if (typeof showToast === 'function') showToast(`Import failed: ${e.message}`, 'error');
  }
}

// Expose functions to the global window object for HTML onclick handlers
window.setLibraryMode = setLibraryMode;
window.leFsBrowse = leFsBrowse;
window.setLeDbSource = setLeDbSource;
window.leLoadSplitView = leLoadSplitView;
window._leStageFsCurrentFolder = _leStageFsCurrentFolder;
window.leSplitDragOver = leSplitDragOver;
window.leSplitDragLeave = leSplitDragLeave;
window.leSplitDropImport = leSplitDropImport;
window.leTrackListDragOver = leTrackListDragOver;
window.leTrackListDragLeave = leTrackListDragLeave;
window.leTrackListDrop = leTrackListDrop;
