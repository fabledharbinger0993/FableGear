/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / library_mode (Hardened)
   ──────────────────────────────────────────────────────────────────────── */

import { DeckManager } from './deck_control.js';

function onPlayClick(trackId, deckId) {
    const url = `/api/library/tracks/${trackId}/stream`;
    DeckManager[`deck${deckId}`].loadTrack(url).then(() => {
        DeckManager[`deck${deckId}`].play();
    });
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
    
    // Add "Stage" button for unimported tracks
    if (type === 'unimported') {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'le-stage-btn le-split-stage';
        btn.textContent = '+Q';
        btn.onclick = () => stagingAddPath(t.path);
        div.appendChild(btn);
    }
    return div;
}

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
    leFsBrowse(_leFsCurrentPath);

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
      all: document.getElementById('le-split-list-allmusic'),
      rb: document.getElementById('le-split-list-rekordbox'),
      unim: document.getElementById('le-split-list-unimported')
  };
  const cntAll = document.getElementById('le-split-cnt-allmusic');
  const cntRb = document.getElementById('le-split-cnt-rekordbox');
  const cntUnim = document.getElementById('le-split-cnt-unimported');

  Object.values(lists).forEach(el => { if (el) el.textContent = '⏳ Scanning drives…'; });

  try {
    const res = await fetch('/api/library/split-data');
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    // Update Counts
    if (cntAll) cntAll.textContent = data.truncated ? `(${data.all_music.length} of ${data.all_music_count.toLocaleString()})` : `(${data.all_music_count})`;
    if (cntRb) cntRb.textContent = `(${data.rekordbox_count})`;
    if (cntUnim) cntUnim.textContent = `(${data.unimported_count})`;

    // Secure Render Helper
    const render = (container, items, type) => {
        container.innerHTML = '';
        if (!items || items.length === 0) {
            container.textContent = type === 'unimported' ? 'Every file on disk is in rekordbox ✓' : 'No tracks found';
            return;
        }
        items.forEach(t => container.appendChild(_leCreateRowElement(t, type, type)));
    };

    render(lists.all, data.all_music, 'allmusic');
    render(lists.rb, data.rekordbox, 'rekordbox');
    render(lists.unim, data.unimported, 'unimported');

  } catch (e) {
    Object.values(lists).forEach(el => { if (el) el.textContent = `⚠ ${e.message}`; });
  }
}

// Expose functions to the global window object for HTML onclick handlers
window.setLibraryMode = setLibraryMode;
window.leFsBrowse = leFsBrowse;
window.onPlayClick = onPlayClick;
window.setLeDbSource = setLeDbSource;
window.leLoadSplitView = leLoadSplitView;
window._leStageFsCurrentFolder = _leStageFsCurrentFolder;
