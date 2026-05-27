/* ════════════════════════════════════════════════════════════════════════
   FableGear — chop_shop / tool_modal
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 5215-6059
   ──────────────────────────────────────────────────────────────────────── */

/* ── Tool Drawer (legacy stubs — drawer is hidden, float modal is used) ── */
let _toolDrawerStep   = null;
let _toolDrawerPinned = false;
function _syncToolDrawerPinState() {}
function toggleToolDrawerPin() {}

/* ── Floating Tool Modal ─────────────────────────────────────────────── */
let _toolFloatActive     = null;   // currently displayed toolId
let _toolFloatPrevParent = null;   // DOM parent the card was borrowed from
let _toolFloatPlaceholder = null;  // preserves the card's position while borrowed

function openToolFloatModal(toolId) {
  const modal    = document.getElementById('tool-float-modal');
  const backdrop = document.getElementById('tool-float-modal-backdrop');
  if (!modal) return;

  // Return any previously borrowed card before borrowing a new one
  if (_toolFloatActive) _returnFloatCard();

  const card = document.getElementById(toolId);
  if (!card) return;

  _toolFloatActive    = toolId;
  _toolFloatPrevParent = card.parentNode;
  _toolFloatPlaceholder = document.createComment(`fablegear-tool-placeholder:${toolId}`);
  _toolFloatPrevParent?.insertBefore(_toolFloatPlaceholder, card);

  // Populate modal header
  const navBtn  = document.querySelector(`#tools-panel .tool-btn[data-step="${toolId}"]`);
  const iconImg = navBtn?.querySelector('img') || card.querySelector('.card-icon img');
  const badge   = card.querySelector('.risk-badge');
  const labelEl = navBtn?.querySelector('.tool-label');
  const titleEl = card.querySelector('.card-title');
  const titleTxt = labelEl?.textContent?.trim()
    || titleEl?.textContent?.trim()
    || toolId.replace('step-', '').replace(/-/g, ' ');

  const mIcon  = document.getElementById('tool-float-modal-icon');
  const mTitle = document.getElementById('tool-float-modal-title');
  const mBadge = document.getElementById('tool-float-modal-badge');
  if (mIcon)  { mIcon.src = iconImg?.src || ''; mIcon.alt = titleTxt; }
  if (mTitle) mTitle.textContent = titleTxt;
  if (mBadge) {
    if (badge) {
      mBadge.textContent = badge.textContent;
      mBadge.className   = badge.className;
      mBadge.style.display = '';
    } else {
      mBadge.style.display = 'none';
    }
  }

  // Move card into modal body
  const body = document.getElementById('tool-float-modal-body');
  if (body) body.appendChild(card);

  // Show modal + backdrop
  modal.style.display = 'flex';
  if (backdrop) backdrop.classList.add('active');

  // Sync scan bar state into modal footer
  _syncToolModalScanState();

  // Mark nav button active
  document.querySelectorAll('#tools-panel .tool-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.step === toolId);
  });
  document.querySelectorAll('.step-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.target === toolId);
  });
  _expandedTool = toolId;
}

function closeToolFloatModal() {
  const modal    = document.getElementById('tool-float-modal');
  const backdrop = document.getElementById('tool-float-modal-backdrop');
  if (!modal) return;

  modal.style.display = 'none';
  if (backdrop) backdrop.classList.remove('active');

  _returnFloatCard();
  document.querySelectorAll('#tools-panel .tool-btn').forEach(b => b.classList.remove('active'));
  _expandedTool = null;
}

function _returnFloatCard() {
  if (!_toolFloatActive) return;
  const card = document.getElementById(_toolFloatActive);
  if (card && _toolFloatPrevParent) {
    if (_toolFloatPlaceholder && _toolFloatPlaceholder.parentNode === _toolFloatPrevParent) {
      _toolFloatPrevParent.insertBefore(card, _toolFloatPlaceholder);
      _toolFloatPlaceholder.remove();
    } else {
      _toolFloatPrevParent.appendChild(card);
    }
  }
  _toolFloatActive    = null;
  _toolFloatPrevParent = null;
  _toolFloatPlaceholder = null;
}

/* ── Tool Panel Expand/Collapse ──────────────────────────────────────────── */
let _expandedTool = null;

/* ── Floating modal drag ─────────────────────────────────────────────────── */
function _initToolFloatModalDrag() {
  const modal  = document.getElementById('tool-float-modal');
  const header = document.getElementById('tool-float-modal-header');
  if (!modal || !header) return;

  let dragging = false, startX = 0, startY = 0, startL = 0, startT = 0;

  header.addEventListener('mousedown', (e) => {
    if (e.target.closest('button')) return;
    dragging = true;
    const rect = modal.getBoundingClientRect();
    // Materialise explicit position, drop CSS transform centering
    modal.style.transform = 'none';
    modal.style.left = rect.left + 'px';
    modal.style.top  = rect.top  + 'px';
    startX = e.clientX; startY = e.clientY;
    startL = rect.left;  startT = rect.top;
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const newL = Math.max(0, Math.min(window.innerWidth  - 100, startL + e.clientX - startX));
    const newT = Math.max(0, Math.min(window.innerHeight -  40, startT + e.clientY - startY));
    modal.style.left = newL + 'px';
    modal.style.top  = newT + 'px';
  });

  document.addEventListener('mouseup', () => { dragging = false; });
}

/* ── Modal scan-bar mirror ───────────────────────────────────────────────── */
function _syncToolModalScanState() {
  const isActive  = document.getElementById('scan-bar')?.classList.contains('active');
  const actionsEl = document.getElementById('tool-float-modal-actions');
  const idleLabel = document.getElementById('tfm-idle-label');
  if (!actionsEl) return;

  if (isActive) {
    actionsEl.classList.add('active');
    _mirrorScanBarToModal();
    if (idleLabel) idleLabel.style.display = 'none';
  } else {
    actionsEl.classList.remove('active');
    if (idleLabel) idleLabel.style.display = '';
    ['tfm-remaining-wrap', 'tfm-clean-wrap', 'tfm-edited-wrap', 'tfm-errors-wrap'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    const tfmSpinner = document.getElementById('tfm-spinner');
    if (tfmSpinner) tfmSpinner.classList.remove('active');
    const tfmTitle = document.getElementById('tfm-title');
    if (tfmTitle) tfmTitle.textContent = '';
  }
}

function _mirrorScanBarToModal() {
  const map = [
    ['sb-remaining', 'tfm-remaining', 'tfm-remaining-wrap'],
    ['sb-clean',     'tfm-clean',     'tfm-clean-wrap'],
    ['sb-edited',    'tfm-edited',    'tfm-edited-wrap'],
    ['sb-errors',    'tfm-errors',    'tfm-errors-wrap'],
  ];
  map.forEach(([srcId, destId, wrapId]) => {
    const src  = document.getElementById(srcId);
    const dest = document.getElementById(destId);
    const wrap = document.getElementById(wrapId);
    if (src && dest) dest.textContent = src.textContent;
    if (wrap) wrap.style.display = '';
  });
  const title    = document.getElementById('scan-bar-title');
  const tfmTitle = document.getElementById('tfm-title');
  if (title && tfmTitle) tfmTitle.textContent = title.textContent;
  const spinner    = document.getElementById('scan-bar-spinner');
  const tfmSpinner = document.getElementById('tfm-spinner');
  if (spinner && tfmSpinner) tfmSpinner.classList.toggle('active', spinner.classList.contains('active'));
}

function openToolDrawer(stepId) {
  // Redirect legacy calls to float modal
  openToolFloatModal(stepId);
}

function closeToolDrawer() {
  closeToolFloatModal();
}

function leSetStatus(label, count, totalCount) {
  const status = document.getElementById('le-status-text');
  if (!status) return;
  if (typeof count !== 'number') {
    status.textContent = label;
    return;
  }
  const base = count === 1 ? '1 track' : `${count} tracks`;
  if (typeof totalCount === 'number' && totalCount !== count) {
    status.textContent = `${base} shown of ${totalCount} — ${label}`;
    return;
  }
  status.textContent = label && label !== 'All Tracks' ? `${base} — ${label}` : base;
}

function leSetTrackView(tracks, label) {
  _leBaseTracks = Array.isArray(tracks) ? tracks : [];
  _leStatusLabel = label || 'All Tracks';
  _leSelectedTrackIds.clear();
  leRefreshTrackView();
}

function leRefreshTrackView() {
  leApplyFilters();
}

function leSetActiveTreeItem(buttonEl) {
  document.querySelectorAll('.le-tree-item').forEach(b => b.classList.remove('active'));
  buttonEl?.classList.add('active');
}

function leActivateAllTracksSelection() {
  _leActivePlaylistId = null;
  _leActivePlaylistName = '';
  _leActiveNodeId = null;
  _leActiveNodeName = '';
  _leActiveNodeType = 'all';
  leSetActiveTreeItem(document.querySelector('.le-tree-all'));
  leSetTrackView(_leAllTracks, 'All Tracks');
  leUpdateActionState();
}

async function leEnsureAllTracksLoaded() {
  if (_leTracksLoaded) return true;

  leSetStatus('Loading all tracks…');
  const empty = document.getElementById('le-empty-state');
  if (empty) {
    empty.style.display = 'flex';
    empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">⏳</div><div>Loading all tracks…</div>';
  }

  try {
    const tracksRes = await fetch('/api/library/tracks');
    if (!tracksRes.ok) throw new Error('tracks load failed');
    _leAllTracks = await tracksRes.json();
    _leTracksLoaded = true;
    document.getElementById('le-all-count').textContent = _leAllTracks.length;
    libBuildGenreSelect();
    return true;
  } catch (_) {
    leSetStatus('Could not load tracks — is the database connected?');
    if (empty) {
      empty.style.display = 'flex';
      empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">⚠</div><div>Failed to load tracks.</div>';
    }
    return false;
  }
}

async function leLoadPlaylistsOnly() {
  leSetStatus('Loading playlists…');
  const empty = document.getElementById('le-empty-state');
  if (empty) {
    empty.style.display = 'flex';
    empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">⏳</div><div>Loading playlists…</div>';
  }

  try {
    const playlistsRes = await fetch(`/api/library/playlists?db=${encodeURIComponent(_leDbSource)}`);
    if (!playlistsRes.ok) throw new Error('playlist load failed');
    const playlists = await playlistsRes.json();
    leRenderPlaylistTree(playlists);
    leSetStatus('Playlists loaded — select a playlist or load all tracks.');
    if (empty) {
      empty.style.display = 'flex';
      empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">♫</div><div>Select All Tracks or a playlist to load music.</div>';
    }
    leUpdateActionState();
  } catch (_) {
    leSetStatus('Could not load playlists — is the database connected?');
    if (empty) {
      empty.style.display = 'flex';
      empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">⚠</div><div>Failed to load playlists.</div>';
    }
  }
}

async function leRestoreSelection(savedNode = null) {
  const node = savedNode || {
    id: _leActiveNodeId,
    name: _leActiveNodeName,
    type: _leActiveNodeType,
  };

  if (node?.type === 'playlist' && node.id) {
    const button = document.querySelector(`.le-tree-item[data-id="${node.id}"]`);
    if (button) {
      await leSelectPlaylist({ id: node.id, type: 'playlist', name: node.name || 'Playlist' }, button);
      return;
    }
  }

  if (node?.type === 'folder' && node.id) {
    const button = document.querySelector(`.le-tree-item[data-id="${node.id}"]`);
    if (button) {
      leSelectFolder({ id: node.id, type: 'folder', name: node.name || 'Folder' }, button);
      return;
    }
  }

  if (node?.type === 'history') {
    const button = document.getElementById('le-history-btn');
    if (button) {
      await leSelectHistory(button);
      return;
    }
  }

  await leSelectAll();
}

function leUpdateActionState() {
  const addBtn = document.getElementById('le-add-btn');
  const removeBtn = document.getElementById('le-remove-btn');
  const renameBtn = document.getElementById('le-rename-btn');
  const deleteBtn = document.getElementById('le-delete-btn');
  if (!addBtn) return;
  const canAdd = !!_leActivePlaylistId && _leSelectedTrackIds.size > 0;
  addBtn.disabled = !canAdd;
  if (removeBtn) {
    removeBtn.disabled = !canAdd;
    if (!_leActivePlaylistId) {
      removeBtn.textContent = 'Select Playlist';
    } else if (_leSelectedTrackIds.size === 0) {
      removeBtn.textContent = 'Remove Selected';
    } else if (_leSelectedTrackIds.size === 1) {
      removeBtn.textContent = 'Remove 1 Track';
    } else {
      removeBtn.textContent = `Remove ${_leSelectedTrackIds.size} Tracks`;
    }
  }
  if (renameBtn) renameBtn.disabled = !_leActiveNodeId;
  if (deleteBtn) deleteBtn.disabled = !_leActiveNodeId;
  if (!_leActivePlaylistId) {
    addBtn.textContent = 'Select Playlist';
  } else if (_leSelectedTrackIds.size === 0) {
    addBtn.textContent = 'Add Selected';
  } else if (_leSelectedTrackIds.size === 1) {
    addBtn.textContent = 'Add 1 Track';
  } else {
    addBtn.textContent = `Add ${_leSelectedTrackIds.size} Tracks`;
  }
}

function leEnsurePlayer() {
  if (_lePlayer) return _lePlayer;
  // Prefer the DOM-attached element — WKWebView (pywebview/macOS) requires the
  // audio element to be part of the document for media playback to work.
  _lePlayer = document.getElementById('le-player-audio') || new Audio();
  _lePlayer.addEventListener('play', () => leRefreshPlaybackButtons());
  _lePlayer.addEventListener('pause', () => leRefreshPlaybackButtons());
  _lePlayer.addEventListener('ended', () => {
    _lePlayingTrackId = null;
    leRefreshPlaybackButtons();
  });
  _lePlayer.addEventListener('error', (e) => {
    const code = _lePlayer.error?.code;
    // code 4 = MEDIA_ERR_SRC_NOT_SUPPORTED (file not found / bad MIME)
    const msg = code === 4 ? 'Track file not found or format unsupported.'
              : code === 2 ? 'Network error loading track.'
              : 'Could not play track.';
    showToast(msg, 'error');
    _lePlayingTrackId = null;
    leRefreshPlaybackButtons();
  });
  return _lePlayer;
}

function lePlaybackStateFor(trackId) {
  if (_lePlayingTrackId !== String(trackId) || !_lePlayer || _lePlayer.paused) return 'play';
  return 'pause';
}

function leRefreshPlaybackButtons() {
  document.querySelectorAll('.le-play-btn').forEach(btn => {
    const state = lePlaybackStateFor(btn.dataset.trackId);
    btn.textContent = state === 'pause' ? '❚❚' : '▶';
    btn.setAttribute('aria-label', state === 'pause' ? 'Pause track' : 'Play track');
    btn.classList.toggle('is-playing', state === 'pause');
  });
}

async function leToggleTrackPlayback(trackId, event) {
  event?.stopPropagation();
  const player = leEnsurePlayer();
  const normalizedTrackId = String(trackId);
  if (_lePlayingTrackId === normalizedTrackId) {
    if (player.paused) {
      try {
        await player.play();
      } catch (_) {
        showToast('Could not play track.', 'error');
      }
    } else {
      player.pause();
    }
    leRefreshPlaybackButtons();
    return;
  }

  _lePlayingTrackId = normalizedTrackId;
  player.src = `/api/library/tracks/${encodeURIComponent(normalizedTrackId)}/stream`;
  player.load();
  try {
    await player.play();
  } catch (_) {
    _lePlayingTrackId = null;
    showToast('Could not play track.', 'error');
  }
  leRefreshPlaybackButtons();
}

async function leLoadLibrary() {
  leSetStatus('Loading library…');
  document.getElementById('le-empty-state').style.display = 'flex';
  document.getElementById('le-empty-state').innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">⏳</div><div>Loading library…</div>';
  try {
    const [tracksRes, playlistsRes] = await Promise.all([
      fetch(`/api/library/tracks?db=${encodeURIComponent(_leDbSource)}`),
      fetch(`/api/library/playlists?db=${encodeURIComponent(_leDbSource)}`)
    ]);
    if (!tracksRes.ok || !playlistsRes.ok) {
      throw new Error('library load failed');
    }
    if (tracksRes.ok) {
      _leAllTracks = await tracksRes.json();
      _leTracksLoaded = true;
      document.getElementById('le-all-count').textContent = _leAllTracks.length;
      libBuildGenreSelect();
    }
    if (playlistsRes.ok) {
      const playlists = await playlistsRes.json();
      leRenderPlaylistTree(playlists);
    }
    leActivateAllTracksSelection();
  } catch (err) {
    _leTracksLoaded = false;
    leSetStatus('Could not load library — is the database connected?');
    document.getElementById('le-empty-state').innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">⚠</div><div>Failed to load library.</div>';
  }
}

function leRenderPlaylistTree(nodes, parentEl, depth) {
  const container = parentEl || document.getElementById('le-playlist-tree');
  if (!parentEl) container.innerHTML = '';
  depth = depth || 0;
  (nodes || []).forEach(node => {
    const item = document.createElement('button');
    item.className = 'le-tree-item';
    item.style.paddingLeft = (12 + depth * 16) + 'px';
    item.dataset.id = node.id;
    item.dataset.type = node.type;
    const icon = node.type === 'folder' ? '▶ ' : '♫ ';
    item.innerHTML = `<span class="le-tree-icon">${icon}</span><span class="le-tree-label">${_leEsc(node.name)}</span><span class="le-tree-count">${node.track_count ?? ''}</span>`;
    item.onclick = () => {
      if (node.type === 'folder') {
        if (node.children && node.children.length) {
          sub.classList.toggle('le-tree-children-open');
          item.querySelector('.le-tree-icon').textContent = sub.classList.contains('le-tree-children-open') ? '▼ ' : '▶ ';
        }
        leSelectFolder(node, item);
        return;
      }
      leSelectPlaylist(node, item);
    };
    container.appendChild(item);
    let sub = null;
    if (node.children && node.children.length) {
      sub = document.createElement('div');
      sub.className = 'le-tree-children';
      leRenderPlaylistTree(node.children, sub, depth + 1);
      container.appendChild(sub);
    }
  });
}

async function leSelectAll() {
  if (!await leEnsureAllTracksLoaded()) return;
  leActivateAllTracksSelection();
}

async function leSelectPlaylist(node, buttonEl) {
  if (node.type === 'folder') return;
  _leActiveNodeId = node.id;
  _leActiveNodeName = node.name || 'Playlist';
  _leActiveNodeType = 'playlist';
  _leActivePlaylistId = node.id;
  _leActivePlaylistName = node.name || 'Playlist';
  leSetActiveTreeItem(buttonEl);
  try {
    const res = await fetch(`/api/library/playlists/${node.id}/tracks`);
    if (res.ok) {
      const tracks = await res.json();
      leSetTrackView(tracks, node.name);
      leUpdateActionState();
    }
  } catch (_) {}
}

function leSelectFolder(node, buttonEl) {
  _leActiveNodeId = node.id;
  _leActiveNodeName = node.name || 'Folder';
  _leActiveNodeType = 'folder';
  _leActivePlaylistId = null;
  _leActivePlaylistName = '';
  leSetActiveTreeItem(buttonEl);
  leSetTrackView([], `${node.name || 'Folder'} (folder)`);
  leUpdateActionState();
}

async function leSelectHistory(buttonEl) {
  if (!await leEnsureAllTracksLoaded()) return;
  _leActiveNodeId = null;
  _leActiveNodeName = 'Recently Added';
  _leActiveNodeType = 'history';
  _leActivePlaylistId = null;
  _leActivePlaylistName = '';
  _leSortCol = 'date_added';
  _leSortAsc = false;
  leSetActiveTreeItem(buttonEl);
  leRefreshSortIndicators();
  const sorted = [..._leAllTracks].sort((a, b) => (b.date_added || '').localeCompare(a.date_added || ''));
  leSetTrackView(sorted.slice(0, 200), 'Recently Added');
  leUpdateActionState();
}

function leRenderTracks(tracks) {
  const list = document.getElementById('le-track-list');
  if (!list) return;
  list.querySelectorAll('.le-track-row').forEach(row => row.remove());
  let empty = document.getElementById('le-empty-state');
  if (!empty) {
    empty = document.createElement('div');
    empty.id = 'le-empty-state';
    empty.className = 'le-empty-state';
    list.appendChild(empty);
  }
  if (!tracks || !tracks.length) {
    empty.style.display = 'flex';
    empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">♫</div><div>No tracks here.</div>';
    return;
  }
  empty.style.display = 'none';
  const sorted = leSorted(tracks);
  sorted.forEach((t, i) => {
    const row = document.createElement('div');
    row.className = 'le-track-row';
    row.dataset.id = t.id;
    if (_leSelectedTrackIds.has(String(t.id))) row.classList.add('selected');
    const playbackState = lePlaybackStateFor(t.id);
    const key = t.key ? `<span class="le-key-badge">${_leEsc(t.key)}</span>` : '—';
    const bpm = t.bpm ? Math.round(t.bpm) : '—';
    const dur = t.duration ? leFormatDur(t.duration) : '—';
    const date = t.date_added ? t.date_added.slice(0, 10) : '—';
    row.innerHTML = `
      <div class="le-col le-col-play"><button class="le-play-btn${playbackState === 'pause' ? ' is-playing' : ''}" data-track-id="${t.id}" aria-label="${playbackState === 'pause' ? 'Pause track' : 'Play track'}">${playbackState === 'pause' ? '❚❚' : '▶'}</button></div>
      <div class="le-col le-col-num">${i + 1}</div>
      <div class="le-col le-col-title le-editable le-title-editable" data-field="title" data-id="${t.id}" title="Double-click to edit title">${_leEsc(t.title || '—')}</div>
      <div class="le-col le-col-artist">${_leEsc(t.artist || '—')}</div>
      <div class="le-col le-col-album">${_leEsc(t.album || '—')}</div>
      <div class="le-col le-col-bpm">${bpm}</div>
      <div class="le-col le-col-key">${key}</div>
      <div class="le-col le-col-dur">${dur}</div>
      <div class="le-col le-col-date">${date}</div>`;
    row.querySelector('.le-play-btn')?.addEventListener('click', evt => leToggleTrackPlayback(t.id, evt));
    row.querySelector('.le-title-editable')?.addEventListener('dblclick', evt => leEditTrackTitle(t, evt));
    row.addEventListener('click', evt => leToggleTrackSelection(String(t.id), evt));
    list.appendChild(row);
  });
}

async function leEditTrackTitle(track, event) {
  event?.stopPropagation();
  const nextTitle = prompt('Edit track title:', track.title || '');
  if (nextTitle === null) return;
  const trimmed = nextTitle.trim();
  if (!trimmed) {
    showToast('Track title cannot be empty.', 'error');
    return;
  }

  const savedNode = {
    id: _leActiveNodeId,
    name: _leActiveNodeName,
    type: _leActiveNodeType,
  };

  try {
    const res = await fetch(`/api/library/tracks/${encodeURIComponent(track.id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: trimmed }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || 'Could not update track title.', 'error');
      return;
    }
    showToast('Track title updated.', 'success');
    await leLoadLibrary();
    await leRestoreSelection(savedNode);
  } catch (_) {
    showToast('Could not update track title.', 'error');
  }
}

function leToggleTrackSelection(trackId, evt) {
  if (evt.metaKey || evt.ctrlKey) {
    if (_leSelectedTrackIds.has(trackId)) _leSelectedTrackIds.delete(trackId);
    else _leSelectedTrackIds.add(trackId);
  } else {
    const alreadySingle = _leSelectedTrackIds.size === 1 && _leSelectedTrackIds.has(trackId);
    _leSelectedTrackIds.clear();
    if (!alreadySingle) _leSelectedTrackIds.add(trackId);
  }
  document.querySelectorAll('.le-track-row').forEach(row => {
    row.classList.toggle('selected', _leSelectedTrackIds.has(row.dataset.id));
  });
  leUpdateActionState();
}

function leSorted(tracks) {
  return [...tracks].sort((a, b) => {
    let va = a[_leSortCol] ?? '', vb = b[_leSortCol] ?? '';
    if (typeof va === 'string') va = va.toLowerCase(); if (typeof vb === 'string') vb = vb.toLowerCase();
    return _leSortAsc ? (va < vb ? -1 : va > vb ? 1 : 0) : (va > vb ? -1 : va < vb ? 1 : 0);
  });
}

function leRefreshSortIndicators() {
  document.querySelectorAll('.le-sort-arrow').forEach(el => {
    el.textContent = el.dataset.col === _leSortCol ? (_leSortAsc ? ' ↑' : ' ↓') : '';
  });
}

function leSortBy(col) {
  if (_leSortCol === col) _leSortAsc = !_leSortAsc; else { _leSortCol = col; _leSortAsc = true; }
  leRefreshSortIndicators();
  leRefreshTrackView();
}

function leFormatDur(secs) {
  const m = Math.floor(secs / 60), s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function leStartCreate(type) {
  _leCreateType = type === 'folder' ? 'folder' : 'playlist';
  const bar = document.getElementById('le-create-bar');
  const label = document.getElementById('le-create-label');
  const input = document.getElementById('le-create-input');
  if (!bar || !label || !input) return;
  label.textContent = _leCreateType === 'folder' ? 'Create folder' : 'Create playlist';
  input.placeholder = _leCreateType === 'folder' ? 'Folder name' : 'Playlist name';
  input.value = '';
  bar.classList.remove('hidden');
  input.focus();
}

function leCloseCreate() {
  document.getElementById('le-create-bar')?.classList.add('hidden');
}

async function leSubmitCreate() {
  const input = document.getElementById('le-create-input');
  const name = input?.value.trim() || '';
  if (!name) {
    showToast(`Please enter a ${_leCreateType} name.`, 'error');
    input?.focus();
    return;
  }
  const parentId = _leActiveNodeType === 'folder' && _leActiveNodeId ? _leActiveNodeId : '';
  try {
    const res = await fetch('/api/library/playlists', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name, type: _leCreateType, parent_id: parentId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || `Could not create ${_leCreateType}.`, 'error');
      return;
    }
    leCloseCreate();
    await leLoadLibrary();
    showToast(`${_leCreateType === 'folder' ? 'Folder' : 'Playlist'} created.`, 'success');
  } catch (_) {
    showToast(`Could not create ${_leCreateType}.`, 'error');
  }
}

async function leAddSelectionToActivePlaylist() {
  if (!_leActivePlaylistId) {
    showToast('Select a playlist first.', 'error');
    return;
  }
  const playlistId = _leActivePlaylistId;
  const trackIds = [..._leSelectedTrackIds];
  if (!trackIds.length) {
    showToast('Select at least one track first.', 'error');
    return;
  }
  try {
    const res = await fetch(`/api/library/playlists/${_leActivePlaylistId}/tracks`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ track_ids: trackIds }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || 'Could not add tracks to playlist.', 'error');
      return;
    }
    showToast(`Added ${data.added || trackIds.length} track${(data.added || trackIds.length) === 1 ? '' : 's'} to playlist.`, 'success');
    const activeButton = document.querySelector(`.le-tree-item[data-id="${playlistId}"]`);
    const activeLabel = activeButton?.querySelector('.le-tree-label')?.textContent || 'Playlist';
    await leLoadLibrary();
    if (activeButton) {
      await leSelectPlaylist({ id: playlistId, type: 'playlist', name: activeLabel }, document.querySelector(`.le-tree-item[data-id="${playlistId}"]`));
    }
  } catch (_) {
    showToast('Could not add tracks to playlist.', 'error');
  }
}

async function leRenameActivePlaylist() {
  if (!_leActiveNodeId) {
    showToast('Select a playlist or folder first.', 'error');
    return;
  }
  const label = _leActiveNodeType === 'folder' ? 'folder' : 'playlist';
  const nextName = prompt(`Rename ${label}:`, _leActiveNodeName || '');
  if (nextName === null) return;
  const trimmed = nextName.trim();
  if (!trimmed) {
    showToast(`${label[0].toUpperCase() + label.slice(1)} name cannot be empty.`, 'error');
    return;
  }
  const savedNode = { id: _leActiveNodeId, type: _leActiveNodeType, name: trimmed };
  try {
    const res = await fetch(`/api/library/playlists/${encodeURIComponent(_leActiveNodeId)}`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name: trimmed }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || `Could not rename ${label}.`, 'error');
      return;
    }
    showToast(`${label[0].toUpperCase() + label.slice(1)} renamed.`, 'success');
    if (_leActiveNodeType === 'playlist') _leActivePlaylistName = trimmed;
    _leActiveNodeName = trimmed;
    await leLoadLibrary();
    await leRestoreSelection(savedNode);
  } catch (_) {
    showToast(`Could not rename ${label}.`, 'error');
  }
}

async function leDeleteActivePlaylist() {
  if (!_leActiveNodeId) {
    showToast('Select a playlist or folder first.', 'error');
    return;
  }
  const label = _leActiveNodeName || (_leActiveNodeType === 'folder' ? 'this folder' : 'this playlist');
  const entity = _leActiveNodeType === 'folder' ? 'folder' : 'playlist';
  const ok = _leActiveNodeType === 'folder'
    ? confirm(`Delete folder "${label}"?`)
    : confirm(`Delete playlist "${label}"?\n\nTracks will remain in your library.`);
  if (!ok) return;
  try {
    const res = await fetch(`/api/library/playlists/${encodeURIComponent(_leActiveNodeId)}`, {
      method: 'DELETE',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || `Could not delete ${entity}.`, 'error');
      return;
    }
    showToast(`${entity[0].toUpperCase() + entity.slice(1)} deleted.`, 'success');
    _leActivePlaylistId = null;
    _leActivePlaylistName = '';
    _leActiveNodeId = null;
    _leActiveNodeName = '';
    _leActiveNodeType = 'all';
    await leLoadLibrary();
    await leSelectAll();
  } catch (_) {
    showToast(`Could not delete ${entity}.`, 'error');
  }
}

async function leRemoveSelectionFromActivePlaylist() {
  if (!_leActivePlaylistId) {
    showToast('Select a playlist first.', 'error');
    return;
  }
  const trackIds = [..._leSelectedTrackIds];
  if (!trackIds.length) {
    showToast('Select at least one track first.', 'error');
    return;
  }
  try {
    const res = await fetch(`/api/library/playlists/${encodeURIComponent(_leActivePlaylistId)}/tracks`, {
      method: 'DELETE',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ track_ids: trackIds }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || 'Could not remove selected tracks from playlist.', 'error');
      return;
    }
    const removed = Number(data.removed || 0);
    if (!removed) {
      showToast('No selected tracks were removed.', 'error');
      return;
    }
    showToast(`Removed ${removed} playlist entr${removed === 1 ? 'y' : 'ies'}.`, 'success');
    const activeBtn = document.querySelector(`.le-tree-item[data-id="${_leActivePlaylistId}"]`);
    const activeLabel = activeBtn?.querySelector('.le-tree-label')?.textContent || _leActivePlaylistName || 'Playlist';
    await leLoadLibrary();
    if (activeBtn) {
      await leSelectPlaylist({ id: _leActivePlaylistId, type: 'playlist', name: activeLabel }, document.querySelector(`.le-tree-item[data-id="${_leActivePlaylistId}"]`));
    }
  } catch (_) {
    showToast('Could not remove selected tracks from playlist.', 'error');
  }
}

