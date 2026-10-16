/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / library_editor
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 5117-5214
   ──────────────────────────────────────────────────────────────────────── */

/* ══ Library & Playlist Editor ════════════════════════════════════════════ */
let _leOpen = true;
let _leAllTracks = [];
let _leBaseTracks = [];
let _leSearchQuery = '';
let _leSortCol = 'title';
let _leSortAsc = true;
let _leActivePlaylistId = null;
let _leActivePlaylistName = '';
let _leActiveNodeId = null;
let _leActiveNodeName = '';
let _leActiveNodeType = 'all';
let _leCreateType = 'playlist';
let _leStatusLabel = 'Loading…';
let _leSelectedTrackIds = new Set();
let _lePlayer = null;
let _lePlayingTrackId = null;
let _leTracksLoaded = false;

/* Library editor is always the primary view — openLibraryEditor just reloads if needed */
function openLibraryEditor() {
  if (_fgActiveSpace !== 'record') setFableGearSpace('record');
  _leOpen = true;
  // setLibraryMode('db') is idempotent: leLoadLibrary() checks _leTracksLoaded
  // before fetching, so this is safe to call on every open. It also activates
  // the flat "All Tracks" view by default rather than an empty-state prompt.
  if (!_leTracksLoaded) setLibraryMode('db');
}

/* ── Filter state ─────────────────────────────────────────────────────── */
const _leFilters = { rating: 0, color: -1 };

function leApplyFilters() {
  const q = _leSearchQuery;
  const bMin = parseFloat(document.getElementById('lf-bpm-min')?.value) || null;
  const bMax = parseFloat(document.getElementById('lf-bpm-max')?.value) || null;
  const key  = document.getElementById('lf-key')?.value  || '';
  const genre = document.getElementById('lf-genre')?.value || '';
  const filtered = _leBaseTracks.filter(t => {
    if (q && !((t.title||'').toLowerCase().includes(q) ||
               (t.artist||'').toLowerCase().includes(q) ||
               (t.album||'').toLowerCase().includes(q))) return false;
    if (bMin != null && (t.bpm == null || t.bpm < bMin)) return false;
    if (bMax != null && (t.bpm == null || t.bpm > bMax)) return false;
    if (key   && t.key   !== key)   return false;
    if (genre && t.genre !== genre) return false;
    if (_leFilters.rating > 0 && (t.rating || 0) < _leFilters.rating) return false;
    if (_leFilters.color >= 0 && (t.color ?? -1) !== _leFilters.color) return false;
    return true;
  });
  leRenderTracks(filtered);
  leSetStatus(_leStatusLabel, filtered.length, _leBaseTracks.length);
  leUpdateActionState();
}

function leSetRatingFilter(stars) {
  _leFilters.rating = _leFilters.rating === stars ? 0 : stars;
  document.querySelectorAll('.lf-star').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.min) <= _leFilters.rating && _leFilters.rating > 0);
  });
  leApplyFilters();
}

function leSetColorFilter(colorId) {
  _leFilters.color = colorId;
  document.querySelectorAll('.lf-color-dot').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.color) === colorId);
  });
  leApplyFilters();
}

function leClearFilters() {
  _leFilters.rating = 0;
  _leFilters.color = -1;
  ['lf-bpm-min','lf-bpm-max'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  const key = document.getElementById('lf-key');   if (key)   key.value   = '';
  const genre = document.getElementById('lf-genre'); if (genre) genre.value = '';
  document.querySelectorAll('.lf-star').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.lf-color-dot').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.color) === -1);
  });
  leApplyFilters();
}

function leStageSelected() {
  if (!_leSelectedTrackIds.size) return;
  const paths = _leAllTracks
    .filter(t => _leSelectedTrackIds.has(t.id) && t.file_path)
    .map(t => t.file_path);
  if (paths.length && typeof stagingAddPath === 'function') {
    stagingAddPath(paths);
  }
}

function libBuildGenreSelect() {
  const genres = [...new Set(_leAllTracks.map(t => t.genre).filter(Boolean))].sort();
  const sel = document.getElementById('lf-genre');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">Any Genre</option>';
  genres.forEach(g => {
    const opt = document.createElement('option');
    opt.value = g; opt.textContent = g;
    sel.appendChild(opt);
  });
  if (cur) sel.value = cur;
}

