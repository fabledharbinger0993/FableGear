/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / launcher
   Room-picker overlay — shown on every app launch and via the Welcome button.
   Permissions are handled during the first-run onboarding wizard (/onboarding).
   Also owns ⌘1/⌘2 hotkeys and persistent rail switcher polish.
   Loaded LAST so all room functions are already defined.
   ──────────────────────────────────────────────────────────────────────── */

(function () {

  // ── Build ─────────────────────────────────────────────────────────────
  function buildLauncherOverlay() {
    if (document.getElementById('fg-room-launcher')) return;
    const overlay = document.createElement('div');
    overlay.id = 'fg-room-launcher';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Choose a room');
    overlay.innerHTML = `
      <div class="fg-launcher-inner">

        <!-- ── Room picker ──────────────────────────────────────────── -->
        <div class="fg-lv" id="fg-lv-rooms">
          <div class="fg-launcher-brand">
            <div class="fg-launcher-title">Where to?</div>
            <div class="fg-launcher-sub">Pick a room. Switch any time with the left rail or <kbd>⌘1</kbd> / <kbd>⌘2</kbd>.</div>
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
          <button type="button" class="fg-launcher-dismiss" id="fg-launcher-dismiss-btn">Stay where I am</button>
        </div>

      </div>
    `;
    document.body.appendChild(overlay);
    _wireOverlay(overlay);
  }

  function _wireOverlay(overlay) {
    // Room cards — navigate immediately and close.
    overlay.querySelectorAll('.fg-launcher-card').forEach((card) => {
      card.addEventListener('click', () => {
        const space = card.getAttribute('data-space');
        try { localStorage.setItem('fablegear-space', space); } catch (e) { /* private mode */ }
        if (typeof setFableGearSpace === 'function') setFableGearSpace(space);
        closeLauncher();
      });
    });

    // Dismiss — keep the current room, close.
    overlay.querySelector('#fg-launcher-dismiss-btn')?.addEventListener('click', () => {
      const space = localStorage.getItem('fablegear-space') || 'record';
      if (typeof setFableGearSpace === 'function') setFableGearSpace(space);
      closeLauncher();
    });
  }

  // ── Open / close ──────────────────────────────────────────────────────
  function openLauncher() {
    buildLauncherOverlay();
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

  // Expose globally — Welcome button in the header calls this.
  window.openFableGearLauncher = function () {
    if (typeof openWelcome === 'function') {
      openWelcome();
      return;
    }
    openLauncher();
  };

  // ── Keyboard handlers ─────────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('fg-room-launcher')?.classList.contains('visible')) {
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

  // ── Auto-show on every launch ─────────────────────────────────────────
  // Shown on every DOMContentLoaded so the user always picks a room at startup.
  // Defers briefly if the welcome/what's-new modal is visible (it takes priority).
  function maybeOpenLauncher() {
    const welcome = document.getElementById('welcome-backdrop');
    if (welcome && !welcome.classList.contains('hidden')) {
      setTimeout(maybeOpenLauncher, 800);
      return;
    }
    if (typeof openWelcome === 'function') {
      openWelcome();
      return;
    }
    openLauncher();
  }

  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(maybeOpenLauncher, 1200);
  });
})();
