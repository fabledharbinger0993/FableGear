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
  hotplugDismiss();
  // Open filesystem browse to the new drive's mountpoint
  setLibraryMode('fs', _hotplugPending.mountpoint);
  openLibraryEditor();
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
        <div class="folder-item drives-item" onclick="openDriveInLibrary('${v.mountpoint}')">
          <div class="drives-item-row">
            <span class="drives-item-name">${v.name}</span>
            ${v.has_pioneer_db ? '<span class="drives-item-pill drives-pill-pioneer">Pioneer DB</span>' : ''}
            ${v.is_music_root  ? '<span class="drives-item-pill drives-pill-root">Music Root</span>' : ''}
          </div>
          <div class="drives-item-meta">${v.free_gb != null ? v.free_gb + ' GB free / ' + v.total_gb + ' GB' : ''} &nbsp; ${v.fstype || ''}</div>
        </div>
      `).join('');
    })
    .catch(() => {
      dropdown.innerHTML = '<div class="folder-item folder-item-empty">Could not load drives</div>';
    });
}

function openDriveInLibrary(mountpoint) {
  closeRightNavDropdown();
  setLibraryMode('fs', mountpoint);
  openLibraryEditor();
}

