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

function _setDriveDropdownMessage(dropdown, text) {
  const item = document.createElement('div');
  item.className = 'folder-item folder-item-empty';
  item.textContent = text;
  dropdown.replaceChildren(item);
}

function _buildDriveDropdownItem(volume) {
  const item = document.createElement('div');
  item.className = 'folder-item drives-item';

  const row = document.createElement('div');
  row.className = 'drives-item-row';
  row.addEventListener('click', () => openDriveInLibrary(volume.mountpoint));

  const name = document.createElement('span');
  name.className = 'drives-item-name';
  name.textContent = volume.name || volume.mountpoint || 'Drive';
  row.appendChild(name);

  [
    volume.has_pioneer_db ? ['drives-item-pill drives-pill-pioneer', 'Pioneer DB'] : null,
    volume.is_music_root ? ['drives-item-pill drives-pill-root', 'Music Root'] : null,
    volume.is_read_only ? ['drives-item-pill', 'Read-only'] : null,
  ].filter(Boolean).forEach(([className, text]) => {
    const pill = document.createElement('span');
    pill.className = className;
    pill.textContent = text;
    row.appendChild(pill);
  });

  const meta = document.createElement('div');
  meta.className = 'drives-item-meta';
  const metaParts = [];
  if (volume.free_gb != null) metaParts.push(`${volume.free_gb} GB free / ${volume.total_gb} GB`);
  if (volume.fstype) metaParts.push(volume.fstype);
  meta.textContent = metaParts.join(' ');

  const actions = document.createElement('div');
  actions.className = 'drives-item-actions';
  [
    ['drives-action-btn', 'Browse', 'Browse in file browser', () => openDriveInFileBrowser(volume.mountpoint)],
    ['drives-action-btn', 'First Aid', 'Open Disk Utility for First Aid', () => openDriveFirstAid(volume.mountpoint)],
    ['drives-action-btn drives-action-stage', '+ Queue', 'Add entire drive to Staging Queue', () => stagingAddPath(volume.mountpoint)],
  ].forEach(([className, text, title, onClick]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.title = title;
    button.textContent = text;
    button.addEventListener('click', onClick);
    actions.appendChild(button);
  });

  item.append(row, meta, actions);
  return item;
}

function loadDrivesDropdown(dropdown) {
  _setDriveDropdownMessage(dropdown, 'Scanning…');
  fetch('/api/status')
    .then(r => r.json())
    .then(data => {
      const vols = data.volumes || [];
      if (vols.length === 0) {
        _setDriveDropdownMessage(dropdown, 'No external drives mounted');
        return;
      }
      dropdown.replaceChildren(...vols.map(_buildDriveDropdownItem));
    })
    .catch(() => {
      _setDriveDropdownMessage(dropdown, 'Could not load drives');
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
      list.replaceChildren(...vols.map(v => {
        const item = document.createElement('div');
        item.className = 'lp-drives-item';
        item.setAttribute('role', 'button');
        item.tabIndex = 0;
        item.addEventListener('click', () => openDriveInFileBrowser(v.mountpoint));
        item.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openDriveInFileBrowser(v.mountpoint);
          }
        });

        const name = document.createElement('span');
        name.className = 'lp-drives-name';
        name.textContent = v.name || v.mountpoint || 'Drive';
        item.appendChild(name);

        [
          v.has_pioneer_db ? 'Pioneer' : null,
          v.is_read_only ? 'Read-only' : null,
        ].filter(Boolean).forEach(text => {
          const badge = document.createElement('span');
          badge.className = 'lp-drives-badge';
          badge.textContent = text;
          item.appendChild(badge);
        });

        if (v.free_gb != null) {
          const meta = document.createElement('span');
          meta.className = 'lp-drives-meta';
          meta.textContent = `${v.free_gb}/${v.total_gb} GB`;
          item.appendChild(meta);
        }

        [
          ['Open Disk Utility First Aid', '🩺', () => openDriveFirstAid(v.mountpoint)],
          ['Stage drive for Chop Shop', '+Q', () => stagingAddPath(v.mountpoint)],
        ].forEach(([title, text, onClick]) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'le-stage-btn';
          button.title = title;
          button.setAttribute('aria-label', title);
          button.textContent = text;
          button.addEventListener('click', event => {
            event.stopPropagation();
            onClick();
          });
          item.appendChild(button);
        });

        return item;
      }));
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

function openDriveFirstAid(mountpoint) {
  fetch('/api/drives/first-aid', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mountpoint })
  })
    .then(r => r.json())
    .then(data => {
      showToast(data.ok ? (data.message || 'Disk Utility opened.') : (data.error || 'Could not open Disk Utility.'), data.ok ? 'success' : 'error');
    })
    .catch(() => showToast('Could not open Disk Utility.', 'error'));
}
