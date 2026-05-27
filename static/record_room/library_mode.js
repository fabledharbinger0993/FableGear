/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / library_mode
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 1389-1686
   ──────────────────────────────────────────────────────────────────────── */

/* ─────────────────────────────────────────────────────────────────────────── */
/* ── Library source mode: DB / Filesystem / Split ───────────────────────── */

let _leMode           = 'db';     // 'db' | 'fs' | 'split'
let _leFsCurrentPath  = null;     // current browsed path in filesystem mode
let _leDbSource       = 'local';  // 'local' | 'device' — which Rekordbox DB to load

function setLibraryMode(mode, fsRootPath = null) {
  _leMode = mode;
  if (fsRootPath) _leFsCurrentPath = fsRootPath;

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
    if (!_leTracksLoaded) leLoadLibrary();

  } else if (mode === 'fs') {
    if (filterBar)   filterBar.style.display = 'none';
    if (sidebarDb)   sidebarDb.style.display = 'none';
    if (sidebarFs)   sidebarFs.style.display = '';
    if (trackHeader) trackHeader.style.display = '';
    if (trackList)   trackList.style.display  = '';
    if (splitView)   splitView.style.display  = 'none';
    if (statusBar)   statusBar.style.display  = '';
    leFsBrowse(_leFsCurrentPath || '/Volumes');

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
  if (_leFsCurrentPath && typeof stagingAddPath === 'function') {
    stagingAddPath(_leFsCurrentPath);
  }
}

function setLeDbSource(source) {
  if (source !== 'local' && source !== 'device') return;
  _leDbSource = source;
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
  _leTracksLoaded = false;
  if (_leMode === 'db') leLoadLibrary();
}

/* ── Filesystem browse mode ──────────────────────────────────────────────── */

async function leFsBrowse(path) {
  const trackList = document.getElementById('le-track-list');
  const folderList = document.getElementById('le-fs-folder-list');
  if (!trackList) return;
  trackList.innerHTML = '<div class="le-empty-state"><div class="le-empty-music-icon">⏳</div><div>Loading…</div></div>';
  if (folderList) folderList.innerHTML = '';

  // Always request recursive=1 so clicking any folder surfaces all nested tracks.
  const base = path
    ? `/api/library/fs-browse?path=${encodeURIComponent(path)}&recursive=1`
    : '/api/library/fs-browse?recursive=1';

  let data;
  try {
    const res = await fetch(base);
    if (!res.ok) throw new Error(await res.text());
    data = await res.json();
  } catch (e) {
    trackList.innerHTML = `<div class="le-empty-state"><div>⚠ Could not load: ${e.message}</div></div>`;
    return;
  }

  _leFsCurrentPath = data.path;

  // ── /Volumes root — render drive picker cards ──────────────────────────
  if (data.is_volumes_root) {
    if (folderList) folderList.innerHTML = '';
    const vols = data.volumes || [];
    if (!vols.length) {
      trackList.innerHTML = '<div class="le-empty-state"><div class="le-empty-music-icon">💿</div><div>No volumes found under /Volumes</div></div>';
      return;
    }
    trackList.innerHTML = '<div class="le-vol-grid">' + vols.map(v => {
      const pioneer = v.has_pioneer_db ? '<span class="le-vol-badge le-vol-badge--pioneer" title="Pioneer DB found">Pioneer DB</span>' : '';
      const freeStr = v.free_gb != null ? `${v.free_gb} GB free` : '';
      const totalStr = v.total_gb != null ? `/ ${v.total_gb} GB` : '';
      const countStr = v.audio_estimate > 0 ? `${v.audio_estimate}+ audio files` : 'No audio at root';
      return `<div class="le-vol-card" onclick="leFsBrowse('${_escPath(v.path)}')" title="Browse ${_esc(v.name)}">
        <div class="le-vol-icon">💿</div>
        <div class="le-vol-name">${_esc(v.name)}</div>
        <div class="le-vol-meta">${countStr}</div>
        <div class="le-vol-disk">${freeStr}${freeStr && totalStr ? ' ' : ''}${totalStr}</div>
        ${pioneer}
        <button type="button" class="le-vol-stage-btn" onclick="event.stopPropagation(); stagingAddPath('${_escAttr(v.path)}')" title="Stage entire drive for Chop Shop">+ Queue</button>
      </div>`;
    }).join('') + '</div>';
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
      <div class="le-col le-col-key">${t.key || '—'}</div>
      <div class="le-col le-col-dur">${dur}</div>
      <div class="le-col le-col-date">—</div>
    </div>
  `;
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

async function leLoadSplitView(fsScanPath = null) {
  const listLibrary    = document.getElementById('le-split-list-library');
  const listScattered  = document.getElementById('le-split-list-scattered');
  const listUnimported = document.getElementById('le-split-list-unimported');
  const cntLib  = document.getElementById('le-split-cnt-library');
  const cntScat = document.getElementById('le-split-cnt-scattered');
  const cntUnim = document.getElementById('le-split-cnt-unimported');
  const hint    = document.getElementById('le-split-unimported-hint');

  [listLibrary, listScattered, listUnimported].forEach(el => {
    if (el) el.innerHTML = '<div class="le-split-loading">⏳ Loading…</div>';
  });

  const scanParam = fsScanPath || _leFsCurrentPath || '';
  const url = `/api/library/split-data${scanParam ? '?fs_path=' + encodeURIComponent(scanParam) : ''}`;

  let data;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    data = await res.json();
  } catch (e) {
    [listLibrary, listScattered, listUnimported].forEach(el => {
      if (el) el.innerHTML = `<div class="le-split-err">⚠ ${e.message}</div>`;
    });
    return;
  }

  // In-library column
  if (cntLib)  cntLib.textContent  = `(${data.in_library_count})`;
  if (listLibrary) {
    listLibrary.innerHTML = (data.in_library || []).length === 0
      ? '<div class="le-split-empty">No tracks</div>'
      : (data.in_library || []).map(t => _leSplitTrackRow(t, 'library')).join('');
  }

  // Scattered column
  if (cntScat) cntScat.textContent = `(${data.scattered_count})`;
  if (listScattered) {
    const rows = (data.scattered || []).map(item => {
      if (item.type === 'folder_header') {
        return `<div class="le-split-folder-hdr">📁 ${_esc(item.path)} <span class="le-split-fhdr-count">${item.count}</span></div>`;
      }
      return _leSplitTrackRow(item, 'scattered');
    }).join('');
    listScattered.innerHTML = rows || '<div class="le-split-empty">None — all tracks are in library root</div>';
  }

  // Unimported column
  if (cntUnim) cntUnim.textContent = `(${data.unimported_count})`;
  if (listUnimported) {
    if (hint) hint.style.display = data.unimported_count > 0 || scanParam ? 'none' : '';
    listUnimported.innerHTML = (data.unimported || []).length === 0
      ? (scanParam ? '<div class="le-split-empty">All files in this folder are in rekordbox ✓</div>'
                   : '<div class="le-split-empty">Browse a folder in Filesystem mode first</div>')
      : (data.unimported || []).map(t => `
          <div class="le-split-track le-split-unimported-row">
            <span class="le-split-title">${_esc(t.title)}</span>
            <span class="le-split-meta">${_esc(t.filename)}</span>
          </div>
        `).join('');
  }
}

function _leSplitTrackRow(t, col) {
  const label = t.title || t.file_path || t.filename || '—';
  const meta  = t.artist ? `${t.artist}${t.bpm ? ' · ' + t.bpm + ' BPM' : ''}` : (t.bpm ? t.bpm + ' BPM' : '');
  return `
    <div class="le-split-track le-split-track-${col}">
      <span class="le-split-title" title="${_escAttr(t.file_path || '')}"> ${_esc(label)}</span>
      ${meta ? `<span class="le-split-meta">${_esc(meta)}</span>` : ''}
    </div>
  `;
}

