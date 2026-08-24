/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / launcher
   Single-source launcher flow: always use the welcome modal from index.html.
   Also owns ⌘1/⌘2 room hotkeys.
   ──────────────────────────────────────────────────────────────────────── */

(function () {
  // Expose globally — Welcome button in the header calls this.
  window.openFableGearLauncher = function () {
    if (typeof openWelcome === 'function') openWelcome();
  };

  // ⌘1/⌘2 room shortcuts.
  document.addEventListener('keydown', (e) => {
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

  // Auto-show welcome shortly after boot.
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
      if (typeof openWelcome === 'function') openWelcome();
    }, 1200);
  });
})();
