/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / launcher
   First-run room picker + ⌘1/⌘2 hotkeys + persistent rail switcher polish.
   Loaded LAST so all room functions are already defined.
   ──────────────────────────────────────────────────────────────────────── */

(function () {
  const LAUNCHER_SEEN_KEY = 'fablegear-launcher-seen';
  // Welcome wizard owns the very-first-launch onboarding (permission grants,
  // archive paths). The room launcher should only appear AFTER setup completes.
  const SETUP_COMPLETE_KEY = 'fablegear-setup-complete';

  function buildLauncherOverlay() {
    if (document.getElementById('fg-room-launcher')) return;
    const overlay = document.createElement('div');
    overlay.id = 'fg-room-launcher';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Choose your FableGear workspace');
    overlay.innerHTML = `
      <div class="fg-launcher-inner">
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
    `;
    document.body.appendChild(overlay);

    overlay.querySelectorAll('.fg-launcher-card').forEach((card) => {
      card.addEventListener('click', () => {
        const space = card.getAttribute('data-space');
        try { localStorage.setItem(LAUNCHER_SEEN_KEY, '1'); } catch (e) { /* private mode */ }
        // setFableGearSpace() persists the active room to 'fablegear-space'.
        if (typeof setFableGearSpace === 'function') setFableGearSpace(space);
        closeLauncher();
      });
    });

    document.getElementById('fg-launcher-dismiss-btn')?.addEventListener('click', () => {
      try { localStorage.setItem(LAUNCHER_SEEN_KEY, '1'); } catch (e) { /* private mode */ }
      closeLauncher();
    });
  }

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

  // Expose so the user can re-open the launcher from a future settings entry.
  window.openFableGearLauncher = openLauncher;

  // ── Keyboard handlers ─────────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    // Escape closes the room launcher overlay first when open.
    if (e.key === 'Escape' && document.getElementById('fg-room-launcher')?.classList.contains('visible')) {
      try { localStorage.setItem(LAUNCHER_SEEN_KEY, '1'); } catch (err) { /* private mode */ }
      closeLauncher();
      e.preventDefault();
      return;
    }

    // ⌘1 / ⌘2 (or Ctrl1 / Ctrl2 on non-mac shells) toggle rooms.
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

  // ── First-run gate ────────────────────────────────────────────────────
  // Do NOT show on truly-fresh installs — the welcome wizard runs first and
  // owns that onboarding. The room launcher appears on the NEXT launch after
  // setup completes, so the user sees it once they actually have content.
  function maybeOpenLauncher() {
    let seen = '1';
    let setupComplete = '';
    try {
      seen = localStorage.getItem(LAUNCHER_SEEN_KEY) || '';
      setupComplete = localStorage.getItem(SETUP_COMPLETE_KEY) || '';
    } catch (e) { return; }

    if (seen || !setupComplete) return;
    // If the welcome modal is currently visible, defer until it closes.
    const welcome = document.getElementById('welcome-modal');
    if (welcome && welcome.style.display && welcome.style.display !== 'none') {
      // Re-check shortly; cheap polling avoids wiring into welcome internals.
      setTimeout(maybeOpenLauncher, 800);
      return;
    }
    openLauncher();
  }

  document.addEventListener('DOMContentLoaded', () => {
    // Slight delay lets settings.js / utility.js sync setup-complete state
    // from the server before we decide whether to show.
    setTimeout(maybeOpenLauncher, 1200);
  });
})();
