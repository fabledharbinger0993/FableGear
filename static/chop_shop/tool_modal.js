/* ════════════════════════════════════════════════════════════════════════
   FableGear — chop_shop / tool_modal
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 5215-6059
   ──────────────────────────────────────────────────────────────────────── */

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

  // Spell out the "what is this?" help inline above the form when idle. When a
  // tool is running we leave the explainers collapsed so the running tool's
  // modal stays focused on progress.
  const _tfmRunning = (typeof isRunning !== 'undefined' && isRunning);
  const _tfmExpl = card.querySelector('.explainers');
  if (_tfmExpl) {
    _tfmExpl.classList.toggle('explainers-expanded', !_tfmRunning);
    _tfmExpl.querySelectorAll('details').forEach(d => { d.open = !_tfmRunning; });
  }
  modal.classList.toggle('tfm-help-inline', !_tfmRunning);

  // Show modal + backdrop
  modal.style.display = 'flex';
  if (backdrop) backdrop.classList.add('active');

  // In the Chop Shop the modal is docked (CSS-positioned). Drop any inline
  // left/top/transform left over from a previous free-float drag so the
  // docking rules take effect.
  if (document.body.classList.contains('fg-space-chop')) {
    modal.style.left = '';
    modal.style.top = '';
    modal.style.transform = '';
  }

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
    // Docked in the Chop Shop — dragging is disabled there.
    if (document.body.classList.contains('fg-space-chop')) return;
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

/* ── Tool-icon dispatcher ────────────────────────────────────────────────────
   Entry point for the prominent workflow-rail icons. Idle → open the single
   morphing modal for the tool. If a tool is RUNNING and a *different* tool icon
   is clicked, the running tool keeps the modal and the clicked tool's "what is
   this?" help is surfaced in a read-only side panel instead. */
function handleToolIconClick(toolId) {
  const running = (typeof isRunning !== 'undefined' && isRunning);
  if (running && _toolFloatActive && toolId !== _toolFloatActive) {
    // Keep the running tool locked in the main panel.
    if (typeof showToast === 'function') {
      showToast('A tool is still running — click Interrupt to stop it before starting another.', 'warning');
    }
    closeToolHoverTip();
    return false;
  }
  closeToolHelpPanel();
  if (typeof openToolFloatModal === 'function') openToolFloatModal(toolId);
  return true;
}

/* ── Running-tool "what is this?" side panel ──────────────────────────────────
   Clones the clicked tool card's explainer text (read-only) so the user can
   learn about another tool without interrupting the one that is running. */
function openToolHelpPanel(toolId) {
  const card  = document.getElementById(toolId);
  if (!card) return;
  showToolHoverTip(toolId);
}

function closeToolHelpPanel() {
  closeToolHoverTip();
}

function _toolHoverCopy(toolId) {
  const card = document.getElementById(toolId);
  if (!card) {
    return {
      title: toolId.replace('step-', '').replace(/-/g, ' '),
      body: 'No description available for this tool.',
    };
  }

  const title = card.querySelector('.card-title')?.textContent?.trim()
    || toolId.replace('step-', '').replace(/-/g, ' ');

  const body = card.querySelector('.explain-body p')?.textContent?.trim()
    || card.querySelector('.card-blurb')?.textContent?.trim()
    || 'No description available for this tool.';

  return { title, body };
}

function showToolHoverTip(toolId, anchorEl) {
  const tip = document.getElementById('tool-hover-tip');
  if (!tip) return;

  const copy = _toolHoverCopy(toolId);
  const titleEl = document.getElementById('tht-title');
  const bodyEl = document.getElementById('tht-body');
  if (titleEl) titleEl.textContent = copy.title;
  if (bodyEl) bodyEl.textContent = copy.body;

  const anchor = anchorEl || document.querySelector(`.step-tab[data-target="${toolId}"]`);
  if (anchor) {
    const rect = anchor.getBoundingClientRect();
    tip.style.top = `${rect.bottom + 8}px`;
    tip.style.left = `${Math.max(16, rect.left)}px`;
  }

  tip.classList.add('open');
}

function closeToolHoverTip() {
  document.getElementById('tool-hover-tip')?.classList.remove('open');
}

function _initToolHoverDescriptions() {
  const buttons = document.querySelectorAll('.step-tab[data-target]');
  if (!buttons.length) return;

  buttons.forEach((btn) => {
    const toolId = btn.getAttribute('data-target');
    if (!toolId) return;

    btn.addEventListener('mouseenter', () => {
      const running = (typeof isRunning !== 'undefined' && isRunning);
      if (!running || toolId === _toolFloatActive) return;
      showToolHoverTip(toolId, btn);
    });

    btn.addEventListener('mouseleave', () => {
      closeToolHoverTip();
    });

    btn.addEventListener('focus', () => {
      const running = (typeof isRunning !== 'undefined' && isRunning);
      if (!running || toolId === _toolFloatActive) return;
      showToolHoverTip(toolId, btn);
    });

    btn.addEventListener('blur', () => {
      closeToolHoverTip();
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  _initToolHoverDescriptions();
});

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

function _leSourceLocationForPath(path) {
  const text = String(path || '').trim();
  if (!text) return { key: 'unknown', label: 'Unknown location' };
  if (text.startsWith('/Volumes/')) {
    const parts = text.split('/').filter(Boolean);
    const volume = parts[1] || 'Volume';
    return { key: `/Volumes/${volume}`, label: volume };
  }
  if (/^[A-Za-z]:[\\/]/.test(text)) {
    return { key: text.slice(0, 2).toUpperCase(), label: text.slice(0, 2).toUpperCase() };
  }
  if (text.startsWith('/Users/')) {
    return { key: '/Users', label: 'Home' };
  }
  const parts = text.split('/').filter(Boolean);
  return { key: parts.length ? `/${parts[0]}` : '/', label: parts[0] || '/' };
}

function _leNormalizePath(path) {
  // Normalize separators, trim trailing slashes, and compare case-insensitively
  // so source roots match consistently across mounted-volume path variants.
  const text = String(path || '').trim().replace(/[\\/]+/g, '/');
  if (!text) return '';
  if (text === '/') return '/';
  return text.replace(/\/+$/, '').toLowerCase();
}

function leRenderSourceLocations() {
  const container = document.getElementById('le-source-location-tree');
  if (!container) return;
  const grouped = new Map();
  (_leAllTracks || []).forEach(track => {
    const info = _leSourceLocationForPath(track.file_path);
    const row = grouped.get(info.key) || { ...info, count: 0 };
    row.count += 1;
    grouped.set(info.key, row);
  });
  const rows = [...grouped.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  container.replaceChildren();
  rows.forEach(row => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'le-tree-item';
    btn.dataset.sourceKey = row.key;
    btn.dataset.sourceLabel = row.label;

    const icon = document.createElement('span');
    icon.className = 'le-tree-icon';
    icon.textContent = '💿';

    const label = document.createElement('span');
    label.className = 'le-tree-label';
    label.textContent = row.label;

    const count = document.createElement('span');
    count.className = 'le-tree-count';
    count.textContent = row.count;

    btn.append(icon, label, count);
    btn.addEventListener('click', () => {
      leSelectSourceLocation(btn.dataset.sourceKey || '', btn.dataset.sourceLabel || '', btn);
    });
    container.appendChild(btn);
  });
}

function leSelectSourceLocation(sourceKey, label, buttonEl) {
  _leActiveNodeId = sourceKey;
  _leActiveNodeName = label || 'Source';
  _leActiveNodeType = 'source';
  _leActivePlaylistId = null;
  _leActivePlaylistName = '';
  leSetActiveTreeItem(buttonEl);
  const normalizedSourceKey = _leNormalizePath(sourceKey);
  leSetTrackView(_leAllTracks.filter(t => {
    if (!normalizedSourceKey) return false;
    const normalizedPath = _leNormalizePath(t.file_path);
    if (normalizedSourceKey === '/') return normalizedPath.startsWith('/');
    return normalizedPath === normalizedSourceKey || normalizedPath.startsWith(`${normalizedSourceKey}/`);
  }), `Source - ${label}`);
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
    leRenderSourceLocations();
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
  const stageSelBtn = document.getElementById('le-stage-selected-btn');
  if (stageSelBtn) stageSelBtn.disabled = _leSelectedTrackIds.size === 0;
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
  _lePlayer.addEventListener('play', () => {
    // Single audio focus: the inline sample player and the performance decks
    // never sound at once. Starting a preview silences any running deck.
    window.deckPauseAll?.();
    leRefreshPlaybackButtons();
  });
  _lePlayer.addEventListener('pause', () => leRefreshPlaybackButtons());
  _lePlayer.addEventListener('timeupdate', () => _leUpdateInlineProgress());
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
  // The slim inline sample player is the single source of row-playback truth.
  // A row shows ❚❚ only while it is the loaded preview AND actually playing.
  return (_lePlayingTrackId != null
          && String(trackId) === String(_lePlayingTrackId)
          && _lePlayer && !_lePlayer.paused) ? 'pause' : 'play';
}

// Current inline playback position as a 0–100 percentage (0 when idle).
function _leInlinePct() {
  const p = _lePlayer;
  return (p && p.duration && isFinite(p.duration)) ? (p.currentTime / p.duration) * 100 : 0;
}

// Paint the progress fill behind the active preview row's title.
function _leUpdateInlineProgress() {
  if (_lePlayingTrackId == null) return;
  const row = document.querySelector(
    `.le-track-row[data-id="${(window.CSS && CSS.escape) ? CSS.escape(String(_lePlayingTrackId)) : String(_lePlayingTrackId)}"]`);
  const fill = row?.querySelector('.le-title-progress');
  if (fill) fill.style.width = _leInlinePct() + '%';
}

// Pause the inline preview — called by deck.js so a deck never doubles up on it.
function leInlinePause() {
  if (_lePlayer && !_lePlayer.paused) _lePlayer.pause();
}
window.leInlinePause = leInlinePause;

// Drop the inline preview's row ownership (e.g. when the filesystem player takes
// over the shared audio element) so no library row is left showing ❚❚.
function leClearInlinePreview() {
  _lePlayingTrackId = null;
  leRefreshPlaybackButtons();
}
window.leClearInlinePreview = leClearInlinePreview;

// Seek the inline preview by clicking the thin strip under the active title.
function leSeekInline(evt) {
  evt.stopPropagation();
  const rect = evt.currentTarget.getBoundingClientRect();
  const pct = Math.min(1, Math.max(0, (evt.clientX - rect.left) / (rect.width || 1)));
  if (_lePlayer && _lePlayer.duration && isFinite(_lePlayer.duration)) {
    _lePlayer.currentTime = pct * _lePlayer.duration;
    _leUpdateInlineProgress();
  }
}

function leRefreshPlaybackButtons() {
  const activeId = _lePlayingTrackId;
  const playing = !!(_lePlayer && !_lePlayer.paused);
  document.querySelectorAll('.le-track-row').forEach(row => {
    const id = row.dataset.id;
    if (id == null || id === '') return;   // skip filesystem rows (data-path only)
    const isActive = activeId != null && id === String(activeId);
    const showPause = isActive && playing;
    const btn = row.querySelector('.le-play-btn');
    if (btn) {
      btn.textContent = showPause ? '❚❚' : '▶';
      btn.setAttribute('aria-label', showPause ? 'Pause track' : 'Play track');
      btn.classList.toggle('is-playing', showPause);
    }
    row.querySelector('.le-col-title')?.classList.toggle('le-title-playing', isActive);
  });
  _leUpdateInlineProgress();
}
// Exposed so deck.js can refresh row buttons + resolve metadata for drops.
window.leRefreshPlaybackButtons = leRefreshPlaybackButtons;
window.leTrackMeta = (id) => (_leAllTracks || []).find(t => String(t.id) === String(id));

async function leToggleTrackPlayback(trackId, event) {
  event?.stopPropagation();
  const id = String(trackId);

  // The row ▶ is the slim inline sample player — it auditions the track in
  // place and NEVER opens the performance decks. Load a track onto a deck by
  // dragging it there or via the 🎛 Decks pop-out.
  const player = leEnsurePlayer();

  // Same track already loaded → toggle pause/resume.
  if (_lePlayingTrackId === id) {
    if (player.paused) {
      window.deckPauseAll?.();
      try { await player.play(); } catch (_) { showToast('Could not play track.', 'error'); }
    } else {
      player.pause();
    }
    leRefreshPlaybackButtons();
    return;
  }

  // Different track → swap the single inline stream over to it and play.
  document.querySelectorAll('.fs-play-btn').forEach(b => { b.textContent = '▶'; });  // clear filesystem-row glyphs
  _lePlayingTrackId = id;
  window.deckPauseAll?.();
  player.src = `/api/library/tracks/${encodeURIComponent(id)}/stream`;
  player.load();
  try { await player.play(); } catch (_) { _lePlayingTrackId = null; showToast('Could not play track.', 'error'); }
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
    // "missing" → the FableGear library hasn't been built yet; the read path
    // deliberately did NOT create the database file (no silent automation).
    const libMissing = tracksRes.headers.get('X-FableGear-Library') === 'missing';
    if (tracksRes.ok) {
      _leAllTracks = await tracksRes.json();
      _leTracksLoaded = true;
      document.getElementById('le-all-count').textContent = _leAllTracks.length;
      libBuildGenreSelect();
      leRenderSourceLocations();
    }
    if (playlistsRes.ok) {
      const playlists = await playlistsRes.json();
      leRenderPlaylistTree(playlists);
    }
    leActivateAllTracksSelection();
    if (libMissing && !_leAllTracks.length) {
      const empty = document.getElementById('le-empty-state');
      if (empty) {
        empty.style.display = 'flex';
        empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">♫</div>'
          + '<div>No library yet — import sources to build it.</div>'
          + '<div style="font-size:.8rem;opacity:.6;margin-top:6px">Use ↻ Sync or drag tracks into the FableGear column (Integrated view).</div>';
      }
      leSetStatus('No FableGear library yet — import sources to build it.');
    }
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
    // Drop a dragged library track onto a playlist to add it.
    if (node.type === 'playlist') {
      item.addEventListener('dragover', (e) => {
        if (!e.dataTransfer || !e.dataTransfer.types.includes('text/fg-track')) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        item.classList.add('le-tree-drop-hover');
      });
      item.addEventListener('dragleave', () => item.classList.remove('le-tree-drop-hover'));
      item.addEventListener('drop', async (e) => {
        e.preventDefault();
        item.classList.remove('le-tree-drop-hover');
        const trackId = e.dataTransfer?.getData('text/fg-track');
        if (!trackId) return;
        try {
          const res = await fetch(`/api/library/playlists/${node.id}/tracks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_id: trackId }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { showToast(data.error || 'Could not add track to playlist.', 'error'); return; }
          if (data.added === 0) showToast(`Already in “${node.name}”.`, 'info');
          else showToast(`Added to “${node.name}”.`, 'success');
        } catch (_) {
          showToast('Could not add track to playlist.', 'error');
        }
      });
    }
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

let _leDragSrcId = null;

/* ── Virtualized track list ────────────────────────────────────────────────
 * Rendering every row of a large library (70k+ tracks) into the DOM freezes
 * the renderer — the old code appended one <div> per track with three event
 * listeners each, which is why "Loading library…" never cleared. Instead we
 * hold the full scroll height with a sizer element and build only the rows in
 * (and just past) the viewport, rebuilding that window on scroll. Playlists
 * and short lists still render directly so drag-reorder is untouched.
 */
const LE_ROW_H = 34;         // .le-track-row height (px) — must match the CSS
const LE_VIRTUAL_MIN = 120;  // at/under this many rows, render directly
const LE_OVERSCAN = 8;       // extra rows kept above/below the viewport

let _leRenderRows = [];
let _leVirtualActive = false;
let _leVirtualRaf = 0;
let _leWinStart = -1;
let _leWinEnd = -1;

function _leEnsureEmptyState(list) {
  let empty = document.getElementById('le-empty-state');
  if (!empty) {
    empty = document.createElement('div');
    empty.id = 'le-empty-state';
    empty.className = 'le-empty-state';
    list.appendChild(empty);
  }
  return empty;
}

function _leClearTrackRows(list) {
  list.querySelectorAll('.le-track-row').forEach(row => row.remove());
  document.getElementById('le-track-sizer')?.remove();
}

// Build a single track row. `absIndex` is the row's position in the full
// (sorted/filtered) list — used for the # column and zebra striping, so both
// stay correct even when only a window of rows exists in the DOM.
function _leBuildTrackRow(t, absIndex, inPlaylist) {
  const row = document.createElement('div');
  row.className = 'le-track-row';
  if (absIndex % 2 === 1) row.classList.add('le-row-alt');
  row.dataset.id = t.id;
  if (_leSelectedTrackIds.has(String(t.id))) row.classList.add('selected');
  const playbackState = lePlaybackStateFor(t.id);
  const key = t.key ? `<span class="le-key-badge">${_leEsc(t.key)}</span>` : '—';
  const bpm = t.bpm ? Math.round(t.bpm) : '—';
  const dur = t.duration ? leFormatDur(t.duration) : '—';
  const date = t.date_added ? t.date_added.slice(0, 10) : '—';
  const handle = inPlaylist ? '<div class="le-drag-handle" title="Drag to reorder">⠿</div>' : '';
  row.innerHTML = `
      ${handle}
      <div class="le-col le-col-play"><button class="le-play-btn${playbackState === 'pause' ? ' is-playing' : ''}" data-track-id="${_leEsc(t.id)}" aria-label="${playbackState === 'pause' ? 'Pause track' : 'Play track'}">${playbackState === 'pause' ? '❚❚' : '▶'}</button></div>
      <div class="le-col le-col-num">${absIndex + 1}</div>
      <div class="le-col le-col-title le-editable le-title-editable" data-field="title" data-id="${_leEsc(t.id)}" title="Double-click to edit title"><div class="le-title-progress"></div><span class="le-title-text">${_leEsc(t.title || '—')}</span><div class="le-title-seek" aria-hidden="true"></div></div>
      <div class="le-col le-col-artist">${_leEsc(t.artist || '—')}</div>
      <div class="le-col le-col-album">${_leEsc(t.album || '—')}</div>
      <div class="le-col le-col-bpm">${bpm}</div>
      <div class="le-col le-col-key">${key}</div>
      <div class="le-col le-col-dur">${dur}</div>
      <div class="le-col le-col-date">${date}</div>`;
  row.querySelector('.le-play-btn')?.addEventListener('click', evt => leToggleTrackPlayback(t.id, evt));
  row.querySelector('.le-title-editable')?.addEventListener('dblclick', evt => leEditTrackTitle(t, evt));
  row.querySelector('.le-title-seek')?.addEventListener('click', evt => leSeekInline(evt));
  row.addEventListener('click', evt => leToggleTrackSelection(String(t.id), evt));
  // Restore the inline-preview markers when this row (re)enters a virtualized view.
  if (_lePlayingTrackId != null && String(t.id) === String(_lePlayingTrackId)) {
    const titleCell = row.querySelector('.le-col-title');
    titleCell?.classList.add('le-title-playing');
    const fill = titleCell?.querySelector('.le-title-progress');
    if (fill) fill.style.width = _leInlinePct() + '%';
  }
  if (inPlaylist) {
    // Playlist view: row drag reorders within the playlist.
    _leBindDragReorder(row, t.id);
  } else {
    // Library view: drag a track onto a deck (load) or a playlist (add).
    row.draggable = true;
    row.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/fg-track', String(t.id));
      e.dataTransfer.effectAllowed = 'copy';
      row.classList.add('le-row-dragging');
    });
    row.addEventListener('dragend', () => row.classList.remove('le-row-dragging'));
  }
  return row;
}

function leRenderTracks(tracks) {
  const list = document.getElementById('le-track-list');
  if (!list) return;
  const inPlaylist = _leActiveNodeType === 'playlist' && !!_leActivePlaylistId;
  const sorted = inPlaylist ? tracks : leSorted(tracks);
  _leRenderRows = sorted;
  _leWinStart = _leWinEnd = -1;

  _leClearTrackRows(list);
  const empty = _leEnsureEmptyState(list);

  if (!sorted || !sorted.length) {
    _leVirtualActive = false;
    empty.style.display = 'flex';
    empty.innerHTML = '<div style="font-size:2rem;margin-bottom:10px;opacity:.4">♫</div><div>No tracks here.</div>';
    return;
  }
  empty.style.display = 'none';
  list.scrollTop = 0;  // fresh view starts at the top

  if (inPlaylist || sorted.length <= LE_VIRTUAL_MIN) {
    // Direct render (normal flow): small lists + playlist drag-reorder.
    _leVirtualActive = false;
    const frag = document.createDocumentFragment();
    sorted.forEach((t, i) => frag.appendChild(_leBuildTrackRow(t, i, inPlaylist)));
    list.appendChild(frag);
    return;
  }

  // Virtualized render: only the visible window is ever in the DOM.
  _leVirtualActive = true;
  const sizer = document.createElement('div');
  sizer.id = 'le-track-sizer';
  sizer.style.height = (sorted.length * LE_ROW_H) + 'px';
  list.appendChild(sizer);
  _leBindVirtualScroll(list);
  _leRenderWindow(list);
}

function _leBindVirtualScroll(list) {
  if (list.dataset.leVirtualBound) return;
  list.dataset.leVirtualBound = '1';
  list.addEventListener('scroll', () => {
    if (!_leVirtualActive || _leVirtualRaf) return;
    _leVirtualRaf = requestAnimationFrame(() => {
      _leVirtualRaf = 0;
      _leRenderWindow(list);
    });
  });
}

function _leRenderWindow(list) {
  if (!_leVirtualActive) return;
  const total = _leRenderRows.length;
  const viewH = list.clientHeight || 600;
  const maxScroll = Math.max(0, total * LE_ROW_H - viewH);
  const st = Math.min(list.scrollTop, maxScroll);
  const start = Math.max(0, Math.floor(st / LE_ROW_H) - LE_OVERSCAN);
  const end = Math.min(total, Math.ceil((st + viewH) / LE_ROW_H) + LE_OVERSCAN);
  if (start === _leWinStart && end === _leWinEnd) return;  // window unchanged
  _leWinStart = start;
  _leWinEnd = end;

  list.querySelectorAll('.le-track-row').forEach(row => row.remove());
  const frag = document.createDocumentFragment();
  for (let i = start; i < end; i++) {
    const row = _leBuildTrackRow(_leRenderRows[i], i, false);
    row.classList.add('le-virtual-row');
    row.style.top = (i * LE_ROW_H) + 'px';
    frag.appendChild(row);
  }
  list.appendChild(frag);
}

function _leBindDragReorder(row, trackId) {
  row.setAttribute('draggable', 'true');
  row.addEventListener('dragstart', e => {
    _leDragSrcId = String(trackId);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', _leDragSrcId);
  });
  row.addEventListener('dragend', () => {
    document.querySelectorAll('.le-track-row.drag-over').forEach(r => r.classList.remove('drag-over'));
    _leDragSrcId = null;
  });
  row.addEventListener('dragover', e => {
    if (!_leDragSrcId || _leDragSrcId === String(trackId)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.le-track-row.drag-over').forEach(r => r.classList.remove('drag-over'));
    row.classList.add('drag-over');
  });
  row.addEventListener('dragleave', () => row.classList.remove('drag-over'));
  row.addEventListener('drop', e => {
    e.preventDefault();
    row.classList.remove('drag-over');
    const srcId = _leDragSrcId;
    if (!srcId || srcId === String(trackId)) return;
    _leApplyReorder(srcId, String(trackId));
  });
}

async function _leApplyReorder(srcId, targetId) {
  const list = document.getElementById('le-track-list');
  if (!list) return;
  const rows = [...list.querySelectorAll('.le-track-row[data-id]')];
  const ids = rows.map(r => r.dataset.id);
  const srcIdx = ids.indexOf(srcId);
  const tgtIdx = ids.indexOf(targetId);
  if (srcIdx === -1 || tgtIdx === -1 || srcIdx === tgtIdx) return;

  ids.splice(srcIdx, 1);
  ids.splice(tgtIdx, 0, srcId);

  // Optimistic DOM reorder
  const srcRow = rows[srcIdx];
  const tgtRow = rows[tgtIdx];
  if (srcIdx < tgtIdx) {
    tgtRow.after(srcRow);
  } else {
    tgtRow.before(srcRow);
  }
  list.querySelectorAll('.le-col-num').forEach((el, i) => { el.textContent = i + 1; });

  try {
    const res = await fetch(`/api/library/playlists/${encodeURIComponent(_leActivePlaylistId)}/tracks/order`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: ids }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      showToast(d.error || 'Could not save track order.', 'error');
      await leRestoreSelection({ id: _leActivePlaylistId, name: _leActivePlaylistName, type: 'playlist' });
    }
  } catch (_) {
    showToast('Could not save track order.', 'error');
    await leRestoreSelection({ id: _leActivePlaylistId, name: _leActivePlaylistName, type: 'playlist' });
  }
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
  // Playlist view: header click reorders the playlist itself (persisted),
  // per the playlist-builder spec — sort by key, BPM, name, or length.
  if (_leActiveNodeType === 'playlist' && _leActivePlaylistId) {
    leSortPlaylistBy(col);
    return;
  }
  if (_leSortCol === col) _leSortAsc = !_leSortAsc; else { _leSortCol = col; _leSortAsc = true; }
  leRefreshSortIndicators();
  leRefreshTrackView();
}

/* Camelot keys sort musically (1A, 1B, 2A … 12B), not lexicographically. */
function _leKeySortValue(key) {
  const m = /^(\d{1,2})\s*([ABab])$/.exec(String(key || '').trim());
  if (!m) return 1000; // non-Camelot keys sort to the end, grouped
  return parseInt(m[1], 10) * 2 + (m[2].toUpperCase() === 'B' ? 1 : 0);
}

async function leSortPlaylistBy(col) {
  if (_leSortCol === col) _leSortAsc = !_leSortAsc; else { _leSortCol = col; _leSortAsc = true; }
  leRefreshSortIndicators();

  const sorted = [..._leBaseTracks].sort((a, b) => {
    let va, vb;
    if (col === 'key') {
      va = _leKeySortValue(a.key); vb = _leKeySortValue(b.key);
    } else {
      va = a[col] ?? ''; vb = b[col] ?? '';
      if (typeof va === 'string') va = va.toLowerCase();
      if (typeof vb === 'string') vb = vb.toLowerCase();
    }
    return _leSortAsc ? (va < vb ? -1 : va > vb ? 1 : 0) : (va > vb ? -1 : va < vb ? 1 : 0);
  });

  try {
    const res = await fetch(`/api/library/playlists/${encodeURIComponent(_leActivePlaylistId)}/tracks/order`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: sorted.map(t => String(t.id)) }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      showToast(d.error || 'Could not save the sorted order.', 'error');
      return;
    }
    leSetTrackView(sorted, _leStatusLabel);
    const labels = { title: 'title', artist: 'artist', album: 'album', bpm: 'BPM', key: 'key', duration: 'length', date_added: 'date added' };
    showToast(`Playlist sorted by ${labels[col] || col}${_leSortAsc ? '' : ' (reversed)'} — order saved.`, 'success');
  } catch (_) {
    showToast('Could not save the sorted order.', 'error');
  }
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
