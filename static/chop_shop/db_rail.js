/* ════════════════════════════════════════════════════════════════════════
   FableGear — chop_shop / db_rail
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 5069-5116
   ──────────────────────────────────────────────────────────────────────── */

/* ── DB Rail panel open/close ─────────────────────────────────────────────── */
const DB_PANEL_TITLES = {
  audit:       'Audit Library',
  relocate:    'Relocate — Fix Broken Paths',
  import:      'Import Tracks',
  link:        'Link Playlists',
  'dead-files': 'Dead File Scanner',
};
let _dbPanelActive = null;

function openDbPanel(tool) {
  if (_fgActiveSpace !== 'chop') setFableGearSpace('chop');
  closeRightNavDropdown();
  // Deactivate all sections + rail buttons
  document.querySelectorAll('.db-panel-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.db-tool-btn').forEach(b => b.classList.remove('active'));

  const section = document.getElementById('db-panel-' + tool);
  const btn     = document.getElementById('rail-btn-' + tool);
  if (!section) return;

  section.classList.add('active');
  if (btn) btn.classList.add('active');
  document.getElementById('db-panel-title').textContent = DB_PANEL_TITLES[tool] || 'DB Tools';

  document.getElementById('db-panel').classList.add('open');
  document.getElementById('db-panel-backdrop').classList.add('open');
  document.body.classList.add('sidebar-open');
  document.getElementById('nav-btn-db')?.classList.add('active');
  _dbPanelActive = tool;
}

function closeDbPanel() {
  document.getElementById('db-panel').classList.remove('open');
  document.getElementById('db-panel-backdrop').classList.remove('open');
  document.querySelectorAll('.db-tool-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('nav-btn-db')?.classList.remove('active');
  _dbPanelActive = null;
  // Only remove sidebar-open if file browser isn't also open
  if (!document.getElementById('fb-panel').classList.contains('fb-open')) {
    document.body.classList.remove('sidebar-open');
  }
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && _dbPanelActive) closeDbPanel();
  if (e.key === 'Escape' && _toolFloatActive) closeToolFloatModal();
});

