/* ════════════════════════════════════════════════════════════════════════
   FableGear — chop_shop / normalize_preview
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 6287-6458
   ──────────────────────────────────────────────────────────────────────── */

/* ── Normalize loudness preview player ──────────────────────────────────────
   4 glowing sample rows: quietest original, quietest normalised,
   loudest original, loudest normalised.
   Triggered automatically whenever a folder is added to normalize-pills.     */

let _normPreviewJobId = null;
let _normPreviewTimer = null;
let _normActiveAudio  = null;   // currently-playing Audio element

function normPreviewSetupObserver() {
  const pills = document.getElementById('normalize-pills');
  if (!pills) return;
  new MutationObserver(() => {
    const paths = getFolderPaths('normalize-pills');
    if (paths.length > 0) _normPreviewStart(paths[0]);
    else _normPreviewReset();
  }).observe(pills, { childList: true });
}

async function _normPreviewStart(folderPath) {
  clearTimeout(_normPreviewTimer);
  _normPreviewSetStatus('Scanning tracks…');
  _normPreviewSetSkeleton();

  let resp, data;
  try {
    resp = await fetch('/api/normalize/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: folderPath }),
    });
    data = await resp.json();
  } catch (_) {
    _normPreviewSetStatus('Could not start preview scan.');
    return;
  }
  if (!resp.ok || !data.job_id) {
    _normPreviewSetStatus(data.error || 'Preview scan failed.');
    return;
  }
  _normPreviewJobId = data.job_id;
  _normPreviewPoll();
}

function _normPreviewPoll() {
  clearTimeout(_normPreviewTimer);
  _normPreviewTimer = setTimeout(async () => {
    if (!_normPreviewJobId) return;
    let data;
    try {
      const r = await fetch(`/api/normalize/preview/${_normPreviewJobId}`,
                            { cache: 'no-store' });
      data = await r.json();
    } catch (_) { _normPreviewPoll(); return; }

    const { status, msg, progress, total } = data;
    if (status === 'done') {
      _normPreviewSetStatus('');
      _normPreviewRender(data.clips || []);
    } else if (status === 'error') {
      _normPreviewSetStatus(msg || 'Preview failed.');
    } else {
      const pct = total > 0 ? ` (${progress}/${total})` : '';
      _normPreviewSetStatus((msg || 'Scanning…') + pct);
      _normPreviewPoll();
    }
  }, 800);
}

function _normPreviewRender(clips) {
  const rows = document.querySelectorAll('#norm-sample-list .norm-sample-row');
  clips.slice(0, 4).forEach((clip, i) => {
    const row = rows[i];
    if (!row) return;
    row.classList.remove('norm-sample-placeholder');

    const meta    = row.querySelector('.norm-sample-meta');
    const kindEl  = row.querySelector('.norm-sample-kind');
    const tagEl   = row.querySelector('.norm-sample-tag');
    const btn     = row.querySelector('.norm-play-btn');
    const fill    = row.querySelector('.norm-progress-fill');

    // Inject name + LUFS span if not already present
    let nameEl = row.querySelector('.norm-sample-name');
    if (!nameEl) {
      nameEl = document.createElement('span');
      nameEl.className = 'norm-sample-name';
      meta.insertBefore(nameEl, tagEl);
    }
    let lufsEl = row.querySelector('.norm-sample-lufs');
    if (!lufsEl) {
      lufsEl = document.createElement('span');
      lufsEl.className = 'norm-sample-lufs';
      meta.appendChild(lufsEl);
    }

    kindEl.textContent = clip.kind === 'quietest' ? 'Quietest' : 'Loudest';
    nameEl.textContent = clip.track;
    lufsEl.textContent = `${clip.lufs > 0 ? '+' : ''}${clip.lufs} LUFS`;

    if (!clip.clip_id) { btn.disabled = true; return; }
    btn.disabled = false;

    const audio = new Audio(`/api/normalize/preview/clip/${clip.clip_id}`);
    audio.preload = 'metadata';

    audio.addEventListener('timeupdate', () => {
      if (!audio.duration) return;
      fill.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
    });
    audio.addEventListener('ended', () => {
      btn.classList.remove('playing');
      fill.style.width = '0%';
    });

    btn.onclick = () => _normTogglePlay(audio, btn);

    row.querySelector('.norm-progress-track').onclick = e => {
      if (!audio.duration) return;
      const rect  = e.currentTarget.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      audio.currentTime = ratio * audio.duration;
      fill.style.width  = `${ratio * 100}%`;
    };
  });
}

function _normTogglePlay(audio, btn) {
  // Pause any other playing sample first
  if (_normActiveAudio && _normActiveAudio !== audio) {
    _normActiveAudio.pause();
    document.querySelectorAll('.norm-play-btn.playing')
      .forEach(b => b.classList.remove('playing'));
  }
  if (audio.paused) {
    audio.play().catch(() => {});
    btn.classList.add('playing');
    _normActiveAudio = audio;
  } else {
    audio.pause();
    btn.classList.remove('playing');
    _normActiveAudio = null;
  }
}

function _normPreviewSetStatus(msg) {
  const el = document.getElementById('norm-preview-status');
  if (el) el.textContent = msg;
}

function _normPreviewSetSkeleton() {
  document.querySelectorAll('#norm-sample-list .norm-sample-row').forEach(row => {
    row.classList.add('norm-sample-placeholder');
    const btn = row.querySelector('.norm-play-btn');
    if (btn) { btn.disabled = true; btn.classList.remove('playing'); }
    const fill = row.querySelector('.norm-progress-fill');
    if (fill) fill.style.width = '0%';
    const n = row.querySelector('.norm-sample-name');
    if (n) n.textContent = '';
    const l = row.querySelector('.norm-sample-lufs');
    if (l) l.textContent = '';
  });
  if (_normActiveAudio) { _normActiveAudio.pause(); _normActiveAudio = null; }
}

function _normPreviewReset() {
  clearTimeout(_normPreviewTimer);
  _normPreviewJobId = null;
  _normPreviewSetSkeleton();
  _normPreviewSetStatus('Add a folder above to load loudness previews.');
}

