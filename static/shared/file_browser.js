/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / file_browser
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 39-234
   ──────────────────────────────────────────────────────────────────────── */

/* ── File Browser Panel ─────────────────────────────────────────────────────── */
let _fbCurrentPath = '';

/* ── Right Nav Dropdown ─────────────────────────────────────────────────────── */
let _activeDropdown = null;

function toggleRightNavDropdown(type) {
  const dropdown = document.getElementById(`dropdown-${type}`);
  const btn = document.getElementById(`nav-btn-${type}`);
  
  if (!dropdown || !btn) return;
  
  // If clicking the same button, close it
  if (_activeDropdown === type) {
    closeRightNavDropdown();
    return;
  }
  
  // Close any other dropdown first
  if (_activeDropdown) closeRightNavDropdown();
  
  // Open this one
  dropdown.classList.add('visible');
  btn.classList.add('dropdown-open');
  _activeDropdown = type;
  
  // Load content based on type
  if (type === 'library') {
    loadLibraryFolders(dropdown);
  } else if (type === 'files') {
    loadFileBrowserFolders(dropdown);
  } else if (type === 'drives') {
    loadDrivesDropdown(dropdown);
  }
}

function closeRightNavDropdown() {
  if (!_activeDropdown) return;
  
  const dropdown = document.getElementById(`dropdown-${_activeDropdown}`);
  const btn = document.getElementById(`nav-btn-${_activeDropdown}`);
  
  if (dropdown) dropdown.classList.remove('visible');
  if (btn) btn.classList.remove('dropdown-open');
  
  _activeDropdown = null;
}

function _dropdownMessage(dropdown, className, text) {
  const item = document.createElement('div');
  item.className = className;
  item.textContent = text;
  dropdown.replaceChildren(item);
}

function _makeDropdownIcon(src) {
  const img = document.createElement('img');
  img.src = src;
  img.className = 'folder-item-icon';
  img.alt = '';
  return img;
}

// Click outside to close dropdown
document.addEventListener('click', (e) => {
  if (!_activeDropdown) return;

  const rail = document.getElementById('workflow-rail');
  const dropdown = document.getElementById(`dropdown-${_activeDropdown}`);

  if (!dropdown) return;

  if ((rail && rail.contains(e.target)) || dropdown.contains(e.target)) return;

  closeRightNavDropdown();
});

function loadLibraryFolders(dropdown) {
  _dropdownMessage(dropdown, 'folder-item folder-item-loading', 'Loading…');
  fetch('/api/library/playlists')
    .then(r => r.json())
    .then(items => {
      // API already returns only root-level items in tree order
      const roots = Array.isArray(items) ? items : [];
      if (!roots.length) {
        _dropdownMessage(dropdown, 'folder-item folder-item-empty', 'No playlists found');
        return;
      }
      dropdown.replaceChildren();
      roots.forEach(p => {
        const icon = p.type === 'folder' ? '/static/icon-folder.png' : '/static/icon-fg-library.png';
        const div = document.createElement('div');
        div.className = 'folder-item';
        div.appendChild(_makeDropdownIcon(icon));
        const name = document.createElement('span');
        name.textContent = p.name || 'Playlist';
        div.appendChild(name);
        if (p.track_count) {
          const cnt = document.createElement('span');
          cnt.className = 'folder-item-count';
          cnt.textContent = p.track_count;
          div.appendChild(cnt);
        }
        dropdown.appendChild(div);
      });
    })
    .catch(() => {
      _dropdownMessage(dropdown, 'folder-item folder-item-empty', 'Could not load library');
    });
}

function _appendFileBrowserShortcut(dropdown, label) {
  const item = document.createElement('div');
  item.className = 'folder-item';
  item.appendChild(_makeDropdownIcon('/static/icon-fg-files.png'));
  const span = document.createElement('span');
  span.textContent = label;
  item.appendChild(span);
  item.addEventListener('click', () => {
    toggleFileBrowser();
    closeRightNavDropdown();
  });
  dropdown.appendChild(item);
}

function loadFileBrowserFolders(dropdown) {
  _dropdownMessage(dropdown, 'folder-item folder-item-loading', 'Scanning drives…');
  fetch('/api/status')
    .then(r => r.json())
    .then(data => {
      const vols = data.volumes || [];
      dropdown.replaceChildren();
      vols.forEach(v => {
        const item = document.createElement('div');
        item.className = 'folder-item';
        item.appendChild(_makeDropdownIcon('/static/icon-folder.png'));

        const name = document.createElement('span');
        name.textContent = v.name || v.mountpoint || 'Drive';
        item.appendChild(name);

        if (v.has_pioneer_db) {
          const badge = document.createElement('span');
          badge.className = 'drives-item-pill drives-pill-pioneer';
          badge.style.fontSize = '20px';
          badge.style.marginLeft = '4px';
          badge.textContent = 'Pioneer';
          item.appendChild(badge);
        }

        item.addEventListener('click', () => {
          fbNavigateTo(v.mountpoint);
          closeRightNavDropdown();
        });
        dropdown.appendChild(item);
      });
      _appendFileBrowserShortcut(dropdown, 'Open file browser…');
    })
    .catch(() => {
      dropdown.replaceChildren();
      _appendFileBrowserShortcut(dropdown, 'Open file browser');
    });
}

function toggleFileBrowser() {
  const panel = document.getElementById('fb-panel');
  const btn   = document.getElementById('fb-toggle-btn');
  const isOpen = panel.classList.toggle('fb-open');
  if (btn) btn.classList.toggle('active', isOpen);
  document.body.classList.toggle('sidebar-open', isOpen);
  if (isOpen) fbNavigateTo(_fbCurrentPath);
}

async function fbNavigateTo(path) {
  _fbCurrentPath = path || '';
  const list = document.getElementById('fb-list');
  list.innerHTML = '<div class="fb-empty">Loading…</div>';

  let data;
  try {
    const url = path ? `/api/fs/list?audio_only=1&path=${encodeURIComponent(path)}` : '/api/fs/list?audio_only=1';
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    data = await res.json();
  } catch (_) {
    list.innerHTML = '<div class="fb-error">Could not read this folder</div>';
    return;
  }

  // Breadcrumb — show current path reversed so deepest segment stays visible
  const crumb = document.getElementById('fb-breadcrumb');
  const crumbSpan = document.createElement('span');
  crumbSpan.textContent = data.path || '/';
  crumb.innerHTML = '';
  crumb.appendChild(crumbSpan);

  // Up button
  const upBtn = document.getElementById('fb-up-btn');
  upBtn.disabled = !data.parent;
  upBtn._fbParent = data.parent || null;

  // Render entries
  list.innerHTML = '';
  if (!data.entries || data.entries.length === 0) {
    list.innerHTML = '<div class="fb-empty">Empty folder</div>';
    return;
  }

  data.entries.forEach(entry => {
    const cls  = entry.is_dir ? 'fb-dir' : entry.is_audio ? 'fb-audio' : 'fb-file';
    const item = document.createElement('div');
    item.className  = `fb-item ${cls}`;
    item.draggable  = true;
    item.dataset.path = entry.path;

    const img = document.createElement('img');
    img.alt = '';
    img.src = entry.is_dir   ? '/static/icon-folder.png'
            : entry.is_audio ? '/static/icon-track.png'
            :                  '/static/icon-fg-library.png';
    img.onerror = () => { img.onerror = null; img.src = '/static/icon-fg-library.png'; };

    const nameEl = document.createElement('span');
    nameEl.className = 'fb-item-name';
    nameEl.textContent = entry.name;
    nameEl.title = entry.name;

    item.appendChild(img);
    item.appendChild(nameEl);

    // Navigate into folders on click
    if (entry.is_dir) {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        fbNavigateTo(entry.path);
      });
    }

    // Drag — Strategy 3 (text/plain) is picked up by all existing drop zones
    item.addEventListener('dragstart', e => {
      e.dataTransfer.effectAllowed = 'copy';
      e.dataTransfer.setData('text/plain', entry.path);
      item.classList.add('fb-dragging');
    });
    item.addEventListener('dragend', () => item.classList.remove('fb-dragging'));

    list.appendChild(item);
  });
}

function fbUp() {
  const btn = document.getElementById('fb-up-btn');
  if (btn._fbParent) fbNavigateTo(btn._fbParent);
}

function fbHome() { fbNavigateTo(''); }
