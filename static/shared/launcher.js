/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / launcher
   Multi-step overlay: room picker → read permission → write permission.
   Also owns ⌘1/⌘2 hotkeys and persistent rail switcher polish.
   Loaded LAST so all room functions are already defined.
   ──────────────────────────────────────────────────────────────────────── */

(function () {
  const LAUNCHER_SEEN_KEY  = 'fablegear-launcher-seen';
  const SETUP_COMPLETE_KEY = 'fablegear-setup-complete';

  // Per-session state — reset each time the launcher opens.
  let _selectedSpace  = null;
  let _readGranted    = false;
  let _writeGranted   = false;

  // ── Build ─────────────────────────────────────────────────────────────
  function buildLauncherOverlay() {
    if (document.getElementById('fg-room-launcher')) return;
    const overlay = document.createElement('div');
    overlay.id = 'fg-room-launcher';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'FableGear setup');
    overlay.innerHTML = `
      <div class="fg-launcher-inner">

        <!-- ── Step 1: Room picker ──────────────────────────────────── -->
        <div class="fg-lv" id="fg-lv-rooms">
          <div class="fg-launcher-brand">
            <div class="fg-launcher-title">Welcome to FableGear</div>
            <div class="fg-launcher-sub">Pick a starting room. You can switch any time with the left rail or <kbd>⌘1</kbd> / <kbd>⌘2</kbd>.</div>
          </div>
          <div class="fg-launcher-grid">
            <button type="button" class="fg-launcher-card fg-launcher-card-record" data-space="record" aria-label="Open Record Room">
              <div class="fg-launcher-card-icon">🎚️</div>
              <div class="fg-launcher-card-title">Record Room</div>
              <div class="fg-launcher-card-desc">Library &amp; playlist editor. Browse tracks, build playlists, export to Pioneer USB.</div>
              <div class="fg-launcher-card-hint"><kbd>⌘</kbd><kbd>1</kbd></div>
            </button>
            <button type="button" class="fg-launcher-card fg-launcher-card-chop" data-space="chop" aria-label="Open Chop Shop">
              <div class="fg-launcher-card-icon">🛠️</div>
              <div class="fg-launcher-card-title">Chop Shop</div>
              <div class="fg-launcher-card-desc">Maintenance workshop. Tag tracks, normalize, rename, dedupe, organize, import.</div>
              <div class="fg-launcher-card-hint"><kbd>⌘</kbd><kbd>2</kbd></div>
            </button>
          </div>
          <button type="button" class="fg-launcher-dismiss" id="fg-launcher-dismiss-btn">Skip — stay where I am</button>
        </div>

        <!-- ── Step 2: Read permission ──────────────────────────────── -->
        <div class="fg-lv" id="fg-lv-read" hidden>
          <div class="fg-launcher-perm-body">
            <div>
              <span class="welcome-perm-badge read">● Read Only</span>
              <p class="welcome-step-title welcome-step-title-mt">May FableGear read your RekordBox database?</p>
            </div>
            <p class="welcome-step-sub">Reading the database lets FableGear see what tracks are in your library, where their files are, and whether any paths are broken. It never modifies anything at this stage.</p>
            <div class="welcome-tool-list">
              <div class="welcome-tool-item"><strong>▣ Library Audit</strong><span>Maps your full library, flags broken paths, orphaned entries, and missing tags.</span></div>
              <div class="welcome-tool-item"><strong>🏷 Tag Tracks</strong><span>Cross-references the database when prioritising which files need analysis.</span></div>
              <div class="welcome-tool-item"><strong>🔎 Find Duplicates</strong><span>Uses DB metadata for smart pre-filtering before acoustic fingerprinting.</span></div>
            </div>
            <div class="welcome-btn-row">
              <button type="button" class="wbtn-accept" id="fg-lv-read-allow">Allow Read Access</button>
              <button type="button" class="wbtn-deny"   id="fg-lv-read-deny">Skip — limited mode</button>
            </div>
          </div>
        </div>

        <!-- ── Step 3: Write permission ─────────────────────────────── -->
        <div class="fg-lv" id="fg-lv-write" hidden>
          <div class="fg-launcher-perm-body">
            <div>
              <span class="welcome-perm-badge write">● Read &amp; Write</span>
              <p class="welcome-step-title welcome-step-title-mt">May FableGear write to your RekordBox database?</p>
            </div>
            <p class="welcome-step-sub">Some tools make changes to your library. FableGear always creates a timestamped database backup before any write operation, and RekordBox must be closed first.</p>
            <div class="welcome-tool-list">
              <div class="welcome-tool-item"><strong>📍 Fix Broken Paths</strong><span>Updates file paths in the database after a drive rename or folder move.</span></div>
              <div class="welcome-tool-item"><strong>＋ Import Tracks</strong><span>Adds new audio files to your Rekordbox library.</span></div>
              <div class="welcome-tool-item"><strong>🔗 Link Playlists</strong><span>Associates imported tracks to playlists based on folder structure.</span></div>
              <div class="welcome-tool-item"><strong>✂ Prune Duplicates</strong><span>Removes confirmed duplicates from the database and optionally the disk.</span></div>
            </div>
            <div class="welcome-btn-row">
              <button type="button" class="wbtn-accept" id="fg-lv-write-allow">Allow Write Access</button>
              <button type="button" class="wbtn-deny"   id="fg-lv-write-deny">Read-only mode</button>
            </div>
          </div>
        </div>

      </div>
    `;
    document.body.appendChild(overlay);
    _wireOverlay(overlay);
  }

  function _wireOverlay(overlay) {
    // Room cards — save choice and advance to read step.
    overlay.querySelectorAll('.fg-launcher-card').forEach((card) => {
      card.addEventListener('click', () => {
        _selectedSpace = card.getAttribute('data-space');
        _showView('read');
      });
    });

    // Dismiss (skip all) — mark seen, navigate to persisted or default room.
    overlay.querySelector('#fg-launcher-dismiss-btn')?.addEventListener('click', () => {
      _markSeen();
      const space = localStorage.getItem('fablegear-space') || 'record';
      if (typeof setFableGearSpace === 'function') setFableGearSpace(space);
      closeLauncher();
    });

    // Read step buttons.
    overlay.querySelector('#fg-lv-read-allow')?.addEventListener('click', () => {
      _readGranted = true;
      _showView('write');
    });
    overlay.querySelector('#fg-lv-read-deny')?.addEventListener('click', () => {
      _readGranted  = false;
      _writeGranted = false;
      _finishSetup();
    });

    // Write step buttons.
    overlay.querySelector('#fg-lv-write-allow')?.addEventListener('click', () => {
      _writeGranted = true;
      _finishSetup();
    });
    overlay.querySelector('#fg-lv-write-deny')?.addEventListener('click', () => {
      _writeGranted = false;
      _finishSetup();
    });
  }

  function _showView(name) {
    document.querySelectorAll('.fg-lv').forEach(v => { v.hidden = true; });
    const el = document.getElementById('fg-lv-' + name);
    if (el) el.hidden = false;
  }

  function _markSeen() {
    try { localStorage.setItem(LAUNCHER_SEEN_KEY, '1'); } catch (e) { /* private mode */ }
  }

  function _finishSetup() {
    const readVal  = _readGranted  ? 'granted' : 'denied';
    const writeVal = _writeGranted ? 'granted' : 'denied';
    try {
      localStorage.setItem('fablegear-db-read',  readVal);
      localStorage.setItem('fablegear-db-write', writeVal);
      localStorage.setItem(SETUP_COMPLETE_KEY,   '1');
      _markSeen();
    } catch (e) { /* private mode */ }

    // Persist to server-side state so it survives WKWebView localStorage clears.
    fetch('/api/setup-complete', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ db_read: readVal, db_write: writeVal }),
    }).catch(() => {});

    // Apply permission gates to the UI immediately.
    if (typeof applyPermissions === 'function') applyPermissions();

    // Navigate to the chosen room (or persisted default if dismissed).
    const space = _selectedSpace || localStorage.getItem('fablegear-space') || 'record';
    if (typeof setFableGearSpace === 'function') setFableGearSpace(space);

    closeLauncher();
  }

  // ── Open / close ──────────────────────────────────────────────────────
  function openLauncher() {
    // Reset per-session state each time the launcher opens.
    _selectedSpace = null;
    _readGranted   = localStorage.getItem('fablegear-db-read')  === 'granted';
    _writeGranted  = localStorage.getItem('fablegear-db-write') === 'granted';

    buildLauncherOverlay();
    _showView('rooms');
    requestAnimationFrame(() => {
      const o = document.getElementById('fg-room-launcher');
      if (o) o.classList.add('visible');
    });
  }

  function closeLauncher() {
    const o = document.getElementById('fg-room-launcher');
    if (!o) return;
    o.classList.remove('visible');
    setTimeout(() => { o.remove(); }, 260);
  }

  // Expose globally — Welcome button and welcome modal intro both call this.
  window.openFableGearLauncher = openLauncher;

  // ── Keyboard handlers ─────────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('fg-room-launcher')?.classList.contains('visible')) {
      _markSeen();
      closeLauncher();
      e.preventDefault();
      return;
    }
    if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable)) return;
    if (e.key === '1') {
      e.preventDefault();
      if (typeof setFableGearSpace === 'function') setFableGearSpace('record');
    } else if (e.key === '2') {
      e.preventDefault();
      if (typeof setFableGearSpace === 'function') setFableGearSpace('chop');
    }
  });

  // ── First-run / returning-user gate ───────────────────────────────────
  // Show the launcher whenever the user hasn't seen it yet this install.
  // Fresh installs: welcome modal intro shows first; its "Get Started" button
  // calls openFableGearLauncher() to hand off here, so we defer if the modal
  // is currently visible.
  function maybeOpenLauncher() {
    let seen = '';
    try { seen = localStorage.getItem(LAUNCHER_SEEN_KEY) || ''; } catch (e) { return; }
    if (seen) return;

    const welcome = document.getElementById('welcome-backdrop');
    if (welcome && !welcome.classList.contains('hidden')) {
      setTimeout(maybeOpenLauncher, 800);
      return;
    }
    openLauncher();
  }

  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(maybeOpenLauncher, 1200);
  });
})();
