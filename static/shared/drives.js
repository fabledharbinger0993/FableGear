/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / drives
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 1290-1388
   ──────────────────────────────────────────────────────────────────────── */

/* ─────────────────────────────────────────────────────────────────────────── */
/* ── Volume hotplug detection ────────────────────────────────────────────── */

let _knownVolumeMounts = null;      // null = first-run, set = baseline established
let _hotplugDismissed  = false;
let _hotplugPending    = null;      // { name, mountpoint } of pending new drive

function _detectVolumeChanges(volumes) {
  const currentMounts = new Set(volumes.map(v => v.mountpoint));

  if (_knownVolumeMounts === null) {
    // First call on page load: establish baseline silently
    _knownVolumeMounts = currentMounts;
    _updateDrivesNavBadge(volumes);
    return;
  }

  const added   = [...currentMounts].filter(m => !_knownVolumeMounts.has(m));
  const removed = [..._knownVolumeMounts].filter(m => !currentMounts.has(m));

  _knownVolumeMounts = currentMounts;
  _updateDrivesNavBadge(volumes);

  if (removed.length > 0) {
    // Re-arm dismiss state if a drive that was dismissed comes back
    _hotplugDismissed = false;
  }

  if (added.length > 0 && !_hotplugDismissed) {
    const newVol = volumes.find(v => added.includes(v.mountpoint));
    if (!newVol) return;
    _hotplugPending = newVol;
    const banner = document.getElementById('hotplug-banner');
    const msg    = document.getElementById('hotplug-banner-msg');
    if (banner && msg) {
      msg.textContent = `New drive connected: ${newVol.name}` +
                        (newVol.total_gb ? ` (${newVol.total_gb} GB)` : '') +
                        ` — scan for music?`;
      banner.style.display = 'flex';
    }
  }
}

function hotplugDismiss() {
  _hotplugDismissed = true;
  const banner = document.getElementById('hotplug-banner');
  if (banner) banner.style.display = 'none';
}

function hotplugAcceptScan() {
  if (!_hotplugPending) return;
  const mp = _hotplugPending.mountpoint;
  hotplugDismiss();
  // Open filesystem browse in Record Room AND pre-navigate the file browser sidebar
  setLibraryMode('fs', mp);
  openLibraryEditor();
  if (typeof fbNavigateTo === 'function') fbNavigateTo(mp);
}

/* ── Drives nav dropdown ─────────────────────────────────────────────────── */

function _updateDrivesNavBadge(volumes) {
  const badge = document.getElementById('nav-drives-badge');
  if (!badge) return;
  const n = volumes.length;
  badge.textContent = n;
  badge.style.display = n > 0 ? 'inline-block' : 'none';
}

function loadDrivesDropdown(dropdown) {
  dropdown.innerHTML = '<div class="folder-item folder-item-empty">Scanning…</div>';
  fetch('/api/status')
    .then(r => r.json())
    .then(data => {
      const vols = data.volumes || [];
      if (vols.length === 0) {
        dropdown.innerHTML = '<div class="folder-item folder-item-empty">No external drives mounted</div>';
        return;
      }
      dropdown.innerHTML = vols.map(v => `
        <div class="folder-item drives-item">
          <div class="drives-item-row" onclick="openDriveInLibrary('${v.mountpoint}')">
            <span class="drives-item-name">${v.name}</span>
            ${v.has_pioneer_db ? '<span class="drives-item-pill drives-pill-pioneer">Pioneer DB</span>' : ''}
            ${v.is_music_root  ? '<span class="drives-item-pill drives-pill-root">Music Root</span>' : ''}
          </div>
          <div class="drives-item-meta">${v.free_gb != null ? v.free_gb + ' GB free / ' + v.total_gb + ' GB' : ''} &nbsp; ${v.fstype || ''}</div>
          <div class="drives-item-actions">
            <button type="button" class="drives-action-btn" onclick="openDriveInFileBrowser('${v.mountpoint}')" title="Browse in file browser">Browse</button>
            <button type="button" class="drives-action-btn drives-action-stage" onclick="stagingAddPath('${v.mountpoint}')" title="Add entire drive to Staging Queue">+ Queue</button>
          </div>
        </div>
      `).join('');
    })
    .catch(() => {
      dropdown.innerHTML = '<div class="folder-item folder-item-empty">Could not load drives</div>';
    });
}

/* ── Left-rail drive list ─────────────────────────────────────────────────── */

let _driveListOpen = false;
let _driveFlyoutDismissTimer = null;
let _driveFlyoutTrigger = null;

function _maybeFocusDriveFlyout(list) {
  if (!_driveListOpen || !list) return;
  const active = document.activeElement;
  if (active !== _driveFlyoutTrigger && !list.contains(active)) return;
  list.querySelector('.lp-drives-item, button:not([disabled])')?.focus();
}

function toggleDriveList() {
  _driveListOpen = !_driveListOpen;
  const list = document.getElementById('drive-list');
  const btn = document.querySelector('.lp-drives-hdr');
  if (!list) return;
  if (_driveListOpen) {
    _driveFlyoutTrigger = btn || document.activeElement;
    btn?.setAttribute('aria-expanded', 'true');
    list.classList.add('lp-drives-flyout');
    _positionDriveFlyout(list);
    initDriveList();
    _bindDriveFlyoutDismiss();
  } else {
    closeDriveList();
  }
}

/* Anchor the fixed flyout to the right edge of the Drives button. The button's
   vertical position is dynamic (window size, rail content), so measure at open. */
function _positionDriveFlyout(list) {
  const btn = document.querySelector('.lp-drives-hdr');
  if (!btn) return;
  const r = btn.getBoundingClientRect();
  list.style.left = Math.round(r.right + 6) + 'px';
  // Clamp so a tall list never runs off the bottom of the viewport.
  const maxHeight = parseFloat(getComputedStyle(list).maxHeight) || (window.innerHeight * 0.6);
  const top = Math.min(Math.round(r.top), window.innerHeight - Math.round(maxHeight) - 8);
  list.style.top = Math.max(8, top) + 'px';
}

function closeDriveList({ restoreFocus = false } = {}) {
  _driveListOpen = false;
  const list = document.getElementById('drive-list');
  const btn = document.querySelector('.lp-drives-hdr');
  btn?.setAttribute('aria-expanded', 'false');
  if (list) {
    list.classList.remove('lp-drives-flyout');
    list.removeAttribute('style');
    list.innerHTML = '';
  }
  _unbindDriveFlyoutDismiss();
  if (restoreFocus && _driveFlyoutTrigger && typeof _driveFlyoutTrigger.focus === 'function') {
    _driveFlyoutTrigger.focus();
  }
}

function _onDriveFlyoutDocClick(e) {
  const target = e.target instanceof Element ? e.target : e.target?.parentElement;
  if (target?.closest('#drive-list') || target?.closest('.lp-drives-hdr')) return;
  closeDriveList();
}
function _onDriveFlyoutKey(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    closeDriveList({ restoreFocus: true });
    return;
  }
  if (e.key !== 'Tab' || !_driveListOpen) return;
  const list = document.getElementById('drive-list');
  if (!list?.classList.contains('lp-drives-flyout')) return;
  const first = list.querySelector('.lp-drives-item, button:not([disabled])');
  if (!first) return;
  if (!e.shiftKey && document.activeElement === _driveFlyoutTrigger) {
    e.preventDefault();
    first.focus();
    return;
  }
  if (e.shiftKey && (document.activeElement === first || document.activeElement === list)) {
    e.preventDefault();
    _driveFlyoutTrigger?.focus();
  }
}
function _onDriveFlyoutResize() {
  const list = document.getElementById('drive-list');
  if (list && list.classList.contains('lp-drives-flyout')) _positionDriveFlyout(list);
}
function _bindDriveFlyoutDismiss() {
  // Defer so the opening click doesn't immediately dismiss it.
  clearTimeout(_driveFlyoutDismissTimer);
  _driveFlyoutDismissTimer = setTimeout(() => {
    document.addEventListener('click', _onDriveFlyoutDocClick);
    _driveFlyoutDismissTimer = null;
  }, 0);
  document.addEventListener('keydown', _onDriveFlyoutKey);
  window.addEventListener('resize', _onDriveFlyoutResize);
}
function _unbindDriveFlyoutDismiss() {
  clearTimeout(_driveFlyoutDismissTimer);
  _driveFlyoutDismissTimer = null;
  document.removeEventListener('click', _onDriveFlyoutDocClick);
  document.removeEventListener('keydown', _onDriveFlyoutKey);
  window.removeEventListener('resize', _onDriveFlyoutResize);
}

function initDriveList() {
  const list = document.getElementById('drive-list');
  if (!list) return;
  list.innerHTML = '<div class="lp-drives-loading">Scanning…</div>';
  fetch('/api/status')
    .then(r => r.json())
    .then(data => {
      const vols = data.volumes || [];
      if (!vols.length) {
        list.innerHTML = '<div class="lp-drives-empty">No drives</div>';
        if (document.activeElement === _driveFlyoutTrigger || list.contains(document.activeElement)) list.focus();
        return;
      }
      list.innerHTML = vols.map(v => `
        <div class="lp-drives-item" role="button" tabindex="0"
             onclick="openDriveInFileBrowser('${_escPath(v.mountpoint)}')"
             onkeydown="if(event.key==='Enter'||event.key===' '){ event.preventDefault(); openDriveInFileBrowser('${_escPath(v.mountpoint)}'); }">
          <span class="lp-drives-name">${_esc(v.name)}</span>
          ${v.has_pioneer_db ? '<span class="lp-drives-badge">Pioneer</span>' : ''}
          ${v.free_gb != null ? `<span class="lp-drives-meta">${v.free_gb}/${v.total_gb} GB</span>` : ''}
          <button type="button" class="le-stage-btn" title="Stage drive for Chop Shop"
                  onclick="event.stopPropagation(); stagingAddPath('${_escAttr(v.mountpoint)}')">+Q</button>
        </div>
      `).join('');
      _maybeFocusDriveFlyout(list);
    })
    .catch(() => {
      if (list) {
        list.innerHTML = '<div class="lp-drives-empty">Unavailable</div>';
        if (document.activeElement === _driveFlyoutTrigger || list.contains(document.activeElement)) list.focus();
      }
    });
}

function openDriveInLibrary(mountpoint) {
  closeRightNavDropdown();
  setLibraryMode('fs', mountpoint);
  openLibraryEditor();
}

function openDriveInFileBrowser(mountpoint) {
  closeRightNavDropdown();
  closeDriveList();
  // Navigate the file browser sidebar to this drive's root
  if (typeof fbNavigateTo === 'function') {
    fbNavigateTo(mountpoint);
    const panel = document.getElementById('fb-panel');
    if (panel && !panel.classList.contains('fb-open')) {
      if (typeof toggleFileBrowser === 'function') toggleFileBrowser();
    }
  }
}
