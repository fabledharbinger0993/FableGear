/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / deck.js
   Dual DJ decks (A/B) with independent audio, tempo, pitch, key lock.
   Center strip has SYNC for matching tempo between decks.
   ──────────────────────────────────────────────────────────────────────── */

/* ── Per-deck state ───────────────────────────────────────────────────── */
const _decks = {
  a: { trackId: null, meta: { bpm: 0, key: '', duration: 0, title: '', artist: '', album: '' },
       tempoPct: 0, pitchSemitones: 0, keyLock: true, playing: false,
       audio: null, animId: null, waveData: [] },
  b: { trackId: null, meta: { bpm: 0, key: '', duration: 0, title: '', artist: '', album: '' },
       tempoPct: 0, pitchSemitones: 0, keyLock: true, playing: false,
       audio: null, animId: null, waveData: [] },
};
let _deckNextTarget = 'a';

/* ── Camelot key ↔ semitone maps ───────────────────────────────────────── */
const _CAMELOT_TO_ST = {
  '1A':8,'2A':3,'3A':10,'4A':5,'5A':0,'6A':7,'7A':2,'8A':9,'9A':4,'10A':11,'11A':6,'12A':1,
  '1B':11,'2B':6,'3B':1,'4B':8,'5B':3,'6B':10,'7B':5,'8B':0,'9B':7,'10B':2,'11B':9,'12B':4,
};
const _ST_TO_CAMELOT_A = {0:'5A',1:'12A',2:'7A',3:'2A',4:'9A',5:'4A',6:'11A',7:'6A',8:'1A',9:'8A',10:'3A',11:'10A'};
const _ST_TO_CAMELOT_B = {0:'8B',1:'3B',2:'10B',3:'5B',4:'12B',5:'7B',6:'2B',7:'9B',8:'4B',9:'11B',10:'6B',11:'1B'};

function _deckShiftKey(key, semitones) {
  const st = _CAMELOT_TO_ST[key];
  if (st === undefined) return key || '—';
  const isA = key.endsWith('A');
  const shifted = ((st + semitones) % 12 + 12) % 12;
  return isA ? _ST_TO_CAMELOT_A[shifted] : _ST_TO_CAMELOT_B[shifted];
}

/* ── Helpers ──────────────────────────────────────────────────────────── */
function _d(id, el) { return document.getElementById('deck-' + id + '-' + el); }

function _deckGetAudio(id) {
  const dk = _decks[id];
  if (dk.audio) return dk.audio;
  dk.audio = document.getElementById('deck-audio-' + id);
  if (!dk.audio) { dk.audio = new Audio(); dk.audio.id = 'deck-audio-' + id; }
  dk.audio.addEventListener('play', () => _deckOnPlay(id));
  dk.audio.addEventListener('pause', () => _deckOnPause(id));
  dk.audio.addEventListener('ended', () => _deckOnEnded(id));
  dk.audio.addEventListener('loadedmetadata', () => _deckOnMeta(id));
  return dk.audio;
}

/* ── Playback events ──────────────────────────────────────────────────── */
function _deckOnPlay(id) {
  const dk = _decks[id];
  dk.playing = true;
  _d(id, 'vinyl')?.classList.add('deck-spin');
  const ico = _d(id, 'play-ico');
  const btn = _d(id, 'play-btn');
  if (ico) ico.innerHTML = '&#10074;&#10074;';
  if (btn) { btn.lastChild.textContent = ' PAUSE'; btn.classList.add('deck-btn-active'); }
  _deckStartAnim(id);
  window.leRefreshPlaybackButtons?.();
}

function _deckOnPause(id) {
  const dk = _decks[id];
  dk.playing = false;
  _d(id, 'vinyl')?.classList.remove('deck-spin');
  const ico = _d(id, 'play-ico');
  const btn = _d(id, 'play-btn');
  if (ico) ico.innerHTML = '&#9654;';
  if (btn) { btn.lastChild.textContent = ' PLAY'; btn.classList.remove('deck-btn-active'); }
  cancelAnimationFrame(dk.animId);
  window.leRefreshPlaybackButtons?.();
}

function _deckOnEnded(id) {
  const dk = _decks[id];
  _deckOnPause(id);
  const audio = _deckGetAudio(id);
  audio.currentTime = 0;
  _deckDrawWave(id, 0);
  _deckUpdateTimes(id, 0, dk.meta.duration || 0);
}

function _deckOnMeta(id) {
  const dk = _decks[id];
  const audio = _deckGetAudio(id);
  if (audio.duration && isFinite(audio.duration)) {
    dk.meta.duration = audio.duration;
    const durEl = _d(id, 'dur');
    if (durEl) durEl.textContent = _deckFmtTime(audio.duration);
    const remEl = _d(id, 'time-remaining');
    if (remEl) remEl.textContent = '-' + _deckFmtTime(audio.duration);
  }
}

/* ── Animation loop ───────────────────────────────────────────────────── */
function _deckStartAnim(id) {
  const dk = _decks[id];
  cancelAnimationFrame(dk.animId);
  function tick() {
    if (!dk.playing) return;
    const audio = _deckGetAudio(id);
    const dur = audio.duration || dk.meta.duration || 1;
    const pos = audio.currentTime / dur;
    _deckDrawWave(id, pos);
    _deckUpdateTimes(id, audio.currentTime, dur);
    dk.animId = requestAnimationFrame(tick);
  }
  dk.animId = requestAnimationFrame(tick);
}

/* ── Public: load a track into a specific deck ────────────────────────── */
function deckLoadTrack(trackId, meta, targetDeck) {
  const id = targetDeck || _deckNextTarget;
  const dk = _decks[id];
  if (!dk) return;

  if (dk.playing) {
    _deckGetAudio(id).pause();
  }

  dk.trackId = trackId;
  dk.meta = {
    bpm: parseFloat(meta.bpm) || 0,
    key: meta.key || '',
    duration: parseFloat(meta.duration) || 0,
    title: meta.title || 'Untitled',
    artist: meta.artist || '',
    album: meta.album || '',
  };

  const titleEl = _d(id, 'title');
  const artistEl = _d(id, 'artist');
  if (titleEl) titleEl.textContent = dk.meta.title;
  if (artistEl) artistEl.textContent = dk.meta.artist + (dk.meta.album ? ' — ' + dk.meta.album : '');

  const origKeyEl = _d(id, 'orig-key');
  const durEl = _d(id, 'dur');
  if (origKeyEl) origKeyEl.textContent = dk.meta.key || '—';
  if (durEl) durEl.textContent = dk.meta.duration ? _deckFmtTime(dk.meta.duration) : '—';

  dk.tempoPct = 0;
  dk.pitchSemitones = 0;
  const tempoSlider = _d(id, 'tempo-slider');
  const pitchSlider = _d(id, 'pitch-slider');
  if (tempoSlider) tempoSlider.value = '0';
  if (pitchSlider) pitchSlider.value = '0';
  _deckUpdateControls(id);

  _deckGenerateWave(id, trackId);
  _deckDrawWave(id, 0);
  _deckUpdateTimes(id, 0, dk.meta.duration || 0);

  const lePlayer = document.getElementById('le-player-audio');
  if (lePlayer && !lePlayer.paused) lePlayer.pause();

  const audio = _deckGetAudio(id);
  audio.src = '/api/library/tracks/' + trackId + '/stream';
  audio.load();

  document.getElementById('deck-panel')?.classList.add('deck-active');
  document.body.classList.add('deck-open');
  document.getElementById('deck-toggle-btn')?.classList.add('is-active');
  document.getElementById('deck-half-' + id)?.classList.add('deck-loaded');

  _deckNextTarget = (id === 'a') ? 'b' : 'a';
  window.leRefreshPlaybackButtons?.();
  return id;
}

/* ── Public deck API (used by the library list + drag-drop) ───────────── */
function deckPlay(id) { _deckPlay(id); }
function deckPause(id) { _deckGetAudio(id).pause(); }
function deckFindTrack(trackId) {
  for (const id of ['a', 'b']) {
    if (_decks[id].trackId != null && String(_decks[id].trackId) === String(trackId)) return id;
  }
  return null;
}
function deckIsPlaying(trackId) {
  const id = deckFindTrack(trackId);
  return id ? !!_decks[id].playing : false;
}
window.deckLoadTrack = deckLoadTrack;
window.deckPlay = deckPlay;
window.deckPause = deckPause;
window.deckFindTrack = deckFindTrack;
window.deckIsPlaying = deckIsPlaying;

/* ── Transport ────────────────────────────────────────────────────────── */
function _deckPlay(id) {
  const dk = _decks[id];
  const audio = _deckGetAudio(id);
  if (!audio.src && !dk.trackId) return;
  audio.play().catch(() => {
    if (typeof showToast === 'function') showToast('Could not play Deck ' + id.toUpperCase() + '.', 'error');
  });
}

function _deckTogglePlay(id) {
  _decks[id].playing ? _deckGetAudio(id).pause() : _deckPlay(id);
}

function _deckCue(id) {
  const dk = _decks[id];
  const audio = _deckGetAudio(id);
  audio.pause();
  audio.currentTime = 0;
  _deckDrawWave(id, 0);
  _deckUpdateTimes(id, 0, audio.duration || dk.meta.duration || 0);
}

/* ── Rate engine ──────────────────────────────────────────────────────── */
function _deckEffectiveRate(id) {
  const dk = _decks[id];
  const tempoFactor = 1 + dk.tempoPct / 100;
  return dk.keyLock ? tempoFactor : tempoFactor * Math.pow(2, dk.pitchSemitones / 12);
}

function _deckApplyRate(id) {
  const dk = _decks[id];
  const audio = _deckGetAudio(id);
  if (dk.keyLock && dk.pitchSemitones === 0) {
    audio.playbackRate = 1 + dk.tempoPct / 100;
    audio.preservesPitch = true;
  } else if (dk.keyLock && dk.pitchSemitones !== 0) {
    audio.playbackRate = (1 + dk.tempoPct / 100) * Math.pow(2, dk.pitchSemitones / 12);
    audio.preservesPitch = false;
  } else {
    audio.playbackRate = _deckEffectiveRate(id);
    audio.preservesPitch = false;
  }
}

/* ── Controls update ──────────────────────────────────────────────────── */
function _deckUpdateControls(id) {
  const dk = _decks[id];
  const rate = _deckEffectiveRate(id);

  const sign = dk.tempoPct >= 0 ? '+' : '';
  const pctEl = _d(id, 'tempo-pct');
  if (pctEl) pctEl.textContent = sign + dk.tempoPct.toFixed(1) + '%';

  const bpmEl = _d(id, 'bpm-input');
  if (bpmEl && dk.meta.bpm && document.activeElement !== bpmEl) {
    bpmEl.value = (dk.meta.bpm * rate).toFixed(1);
  }

  const rpmEl = _d(id, 'rpm');
  if (rpmEl) rpmEl.textContent = (33 * rate).toFixed(1);

  let displaySemitones = 0;
  if (!dk.keyLock) {
    displaySemitones = Math.round(12 * Math.log2(rate));
  } else if (dk.pitchSemitones !== 0) {
    displaySemitones = dk.pitchSemitones;
  }
  const shiftedKey = displaySemitones !== 0
    ? _deckShiftKey(dk.meta.key, displaySemitones)
    : (dk.meta.key || '—');
  const keyEl = _d(id, 'key-input');
  if (keyEl) keyEl.value = shiftedKey;

  const stEl = _d(id, 'semitone-val');
  if (stEl) stEl.textContent = (dk.pitchSemitones >= 0 ? '+' : '') + dk.pitchSemitones + ' st';

  if (dk.playing) {
    const vinyl = _d(id, 'vinyl');
    if (vinyl) vinyl.style.animationDuration = (1.8 / Math.abs(rate)) + 's';
  }

  const klBtn = _d(id, 'keylock-btn');
  if (klBtn) klBtn.classList.toggle('deck-keylock-on', dk.keyLock);

  _deckApplyRate(id);
  _deckHarmonyUpdate();
}

/* ── BPM text entry ───────────────────────────────────────────────────── */
function _deckBpmInput(id, val) {
  const dk = _decks[id];
  const targetBpm = parseFloat(val);
  if (!targetBpm || !dk.meta.bpm || dk.meta.bpm === 0) return;
  const pitchFactor = dk.keyLock ? 1 : Math.pow(2, dk.pitchSemitones / 12);
  const needed = targetBpm / (dk.meta.bpm * pitchFactor);
  dk.tempoPct = Math.max(-8, Math.min(8, (needed - 1) * 100));
  const slider = _d(id, 'tempo-slider');
  if (slider) slider.value = String(dk.tempoPct);
  _deckUpdateControls(id);
}

/* ── Effective BPM ────────────────────────────────────────────────────── */
function _deckEffectiveBpm(id) {
  const dk = _decks[id];
  return dk.meta.bpm * _deckEffectiveRate(id);
}

/* ── SYNC: match target deck tempo (and beat phase) to source deck ────── */
function _deckSync(fromId, toId) {
  const src = _decks[fromId];
  const dst = _decks[toId];
  if (!src.meta.bpm || !dst.meta.bpm) return;
  const srcBpm = _deckEffectiveBpm(fromId);
  const pitchFactor = dst.keyLock ? 1 : Math.pow(2, dst.pitchSemitones / 12);
  const needed = srcBpm / (dst.meta.bpm * pitchFactor);
  dst.tempoPct = Math.max(-8, Math.min(8, (needed - 1) * 100));
  const slider = _d(toId, 'tempo-slider');
  if (slider) slider.value = String(dst.tempoPct);
  _deckUpdateControls(toId);
  _deckPhaseNudge(fromId, toId);
  if (typeof showToast === 'function') {
    showToast('Deck ' + toId.toUpperCase() + ' synced to ' + srcBpm.toFixed(1) + ' BPM', 'success');
  }
}

/* Beat-phase alignment: nudge the target by less than one beat so both
   decks' downbeat phase lines up. Only meaningful (and only applied) when
   both decks are playing — never yanks a stopped deck around. */
function _deckPhaseNudge(fromId, toId) {
  const src = _decks[fromId];
  const dst = _decks[toId];
  if (!src.playing || !dst.playing) return;
  const srcBpm = _deckEffectiveBpm(fromId);
  const dstBpm = _deckEffectiveBpm(toId);
  if (!srcBpm || !dstBpm) return;
  const srcAudio = _deckGetAudio(fromId);
  const dstAudio = _deckGetAudio(toId);
  const srcPhase = (srcAudio.currentTime * srcBpm / 60) % 1;   // fraction of a beat
  const dstPhase = (dstAudio.currentTime * dstBpm / 60) % 1;
  let delta = srcPhase - dstPhase;                              // beats to shift
  if (delta > 0.5) delta -= 1;                                  // take the short way
  if (delta < -0.5) delta += 1;
  const shiftSec = delta * 60 / dstBpm;
  const next = dstAudio.currentTime + shiftSec;
  if (next >= 0 && (!dstAudio.duration || next < dstAudio.duration)) {
    dstAudio.currentTime = next;
  }
}

/* ── KEY MATCH: shift target deck's key to the source deck's key ──────── */
function _deckCurrentKey(id) {
  const dk = _decks[id];
  let displaySemitones = 0;
  const rate = _deckEffectiveRate(id);
  if (!dk.keyLock) {
    displaySemitones = Math.round(12 * Math.log2(rate));
  } else if (dk.pitchSemitones !== 0) {
    displaySemitones = dk.pitchSemitones;
  }
  return displaySemitones !== 0 ? _deckShiftKey(dk.meta.key, displaySemitones) : (dk.meta.key || '');
}

function _deckKeyMatch(fromId, toId) {
  const srcKey = _deckCurrentKey(fromId);
  const dst = _decks[toId];
  const srcSt = _CAMELOT_TO_ST[srcKey];
  const dstSt = _CAMELOT_TO_ST[dst.meta.key];
  if (srcSt === undefined || dstSt === undefined) {
    if (typeof showToast === 'function') {
      showToast('Key match needs Camelot keys on both decks — run Tag Tracks first.', 'warning');
    }
    return;
  }
  let shift = srcSt - dstSt;                 // pitch-class distance
  if (shift > 6) shift -= 12;                // choose the smaller direction
  if (shift < -6) shift += 12;
  dst.pitchSemitones = shift;                // slider range is ±6, so always fits
  const slider = _d(toId, 'pitch-slider');
  if (slider) slider.value = String(shift);
  _deckUpdateControls(toId);
  if (typeof showToast === 'function') {
    showToast('Deck ' + toId.toUpperCase() + ' key shifted ' + (shift >= 0 ? '+' : '') + shift
      + ' st to match ' + srcKey + '.', 'success');
  }
}

/* ── Harmonic compatibility indicator (center strip) ──────────────────── */
function _deckParseCamelot(key) {
  const m = String(key || '').trim().toUpperCase().match(/^(\d{1,2})([AB])$/);
  if (!m) return null;
  const n = Number(m[1]);
  return (n >= 1 && n <= 12) ? { n: n, mode: m[2] } : null;
}

function _deckIsHarmonicMatch(keyA, keyB) {
  const a = _deckParseCamelot(keyA);
  const b = _deckParseCamelot(keyB);
  if (!a || !b) return false;
  if (a.n === b.n) return true;                                   // same slot or relative maj/min
  if (a.mode === b.mode &&
      ((a.n % 12) + 1 === b.n || (b.n % 12) + 1 === a.n)) return true;  // wheel neighbours
  return false;
}

function _deckHarmonyUpdate() {
  const el = document.getElementById('deck-harmony');
  if (!el) return;
  const keyA = _deckCurrentKey('a');
  const keyB = _deckCurrentKey('b');
  if (!_decks.a.trackId || !_decks.b.trackId || !keyA || !keyB) {
    el.className = 'deck-harmony';
    el.title = 'Harmonic compatibility of the two decks’ current keys';
    return;
  }
  const match = _deckIsHarmonicMatch(keyA, keyB);
  el.className = 'deck-harmony ' + (match ? 'deck-harmony-good' : 'deck-harmony-clash');
  el.title = keyA + ' vs ' + keyB + (match ? ' — harmonic mix' : ' — key clash; try KEY match');
}

/* ── Waveform ─────────────────────────────────────────────────────────── */
function _deckGenerateWave(id, seed) {
  const dk = _decks[id];
  let h = 0;
  const s = String(seed);
  for (let i = 0; i < s.length; i++) { h = ((h << 5) - h + s.charCodeAt(i)) | 0; }
  const data = [];
  for (let i = 0; i < 200; i++) {
    h = (h * 1103515245 + 12345) & 0x7fffffff;
    const r = (h % 1000) / 1000;
    const env = Math.sin((i / 200) * Math.PI) * 0.6 + 0.35;
    const wave = (Math.sin(i * 0.25) * 0.35 + Math.sin(i * 0.6) * 0.25 + r * 0.4) * env;
    data.push(Math.abs(wave));
  }
  dk.waveData = data;
}

function _deckDrawWave(id, progress) {
  const canvas = _d(id, 'wave-canvas');
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr;
    canvas.height = h * dpr;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const dk = _decks[id];
  const len = dk.waveData.length;
  if (!len) return;
  const barW = w / len;
  const mid = h / 2;

  const accent = id === 'a' ? 'rgba(0,212,232,0.75)' : 'rgba(255,45,120,0.70)';
  const dim = 'rgba(58,80,96,0.45)';

  for (let i = 0; i < len; i++) {
    const bh = dk.waveData[i] * mid * 0.88;
    ctx.fillStyle = (i / len) < progress ? accent : dim;
    ctx.fillRect(i * barW, mid - bh, Math.max(barW - 0.5, 0.5), bh * 2);
  }

  const posEl = _d(id, 'wave-pos');
  if (posEl) posEl.style.left = (progress * w) + 'px';
}

function _deckUpdateTimes(id, cur, dur) {
  const elapsedEl = _d(id, 'time-elapsed');
  const remainEl = _d(id, 'time-remaining');
  if (elapsedEl) elapsedEl.textContent = _deckFmtTime(cur);
  if (remainEl) remainEl.textContent = '-' + _deckFmtTime(Math.max(0, dur - cur));
}

function _deckFmtTime(s) {
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return m + ':' + (ss < 10 ? '0' : '') + ss;
}

/* ── Waveform scrub ───────────────────────────────────────────────────── */
function _deckWaveScrub(id, e) {
  const wrap = _d(id, 'wave-wrap');
  if (!wrap) return;
  const rect = wrap.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const pct = Math.max(0, Math.min(1, x / rect.width));
  const dk = _decks[id];
  const audio = _deckGetAudio(id);
  const dur = audio.duration || dk.meta.duration || 0;
  if (dur > 0) {
    audio.currentTime = pct * dur;
    _deckDrawWave(id, pct);
    _deckUpdateTimes(id, pct * dur, dur);
  }
}

/* ── Wire up one deck ─────────────────────────────────────────────────── */
function _deckInitOne(id) {
  _d(id, 'play-btn')?.addEventListener('click', () => _deckTogglePlay(id));

  const cueBtn = _d(id, 'cue-btn');
  if (cueBtn) {
    cueBtn.addEventListener('mousedown', () => { cueBtn.classList.add('deck-btn-cue-active'); _deckCue(id); });
    cueBtn.addEventListener('mouseup', () => cueBtn.classList.remove('deck-btn-cue-active'));
    cueBtn.addEventListener('mouseleave', () => cueBtn.classList.remove('deck-btn-cue-active'));
  }

  _d(id, 'tempo-slider')?.addEventListener('input', function () {
    _decks[id].tempoPct = parseFloat(this.value);
    _deckUpdateControls(id);
  });

  const bpmInput = _d(id, 'bpm-input');
  if (bpmInput) {
    bpmInput.addEventListener('focus', function () { this.select(); });
    bpmInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { _deckBpmInput(id, this.value); this.blur(); }
      if (e.key === 'Escape') { _deckUpdateControls(id); this.blur(); }
    });
    bpmInput.addEventListener('blur', function () { _deckBpmInput(id, this.value); });
  }

  _d(id, 'pitch-slider')?.addEventListener('input', function () {
    _decks[id].pitchSemitones = parseInt(this.value, 10);
    _deckUpdateControls(id);
  });

  _d(id, 'pitch-up')?.addEventListener('click', () => {
    const dk = _decks[id];
    dk.pitchSemitones = Math.min(6, dk.pitchSemitones + 1);
    const slider = _d(id, 'pitch-slider');
    if (slider) slider.value = String(dk.pitchSemitones);
    _deckUpdateControls(id);
  });

  _d(id, 'pitch-down')?.addEventListener('click', () => {
    const dk = _decks[id];
    dk.pitchSemitones = Math.max(-6, dk.pitchSemitones - 1);
    const slider = _d(id, 'pitch-slider');
    if (slider) slider.value = String(dk.pitchSemitones);
    _deckUpdateControls(id);
  });

  _d(id, 'keylock-btn')?.addEventListener('click', () => {
    _decks[id].keyLock = !_decks[id].keyLock;
    _deckUpdateControls(id);
  });

  _d(id, 'wave-wrap')?.addEventListener('click', (e) => _deckWaveScrub(id, e));

  const canvas = _d(id, 'wave-canvas');
  if (canvas) {
    new ResizeObserver(() => {
      const dk = _decks[id];
      const audio = _deckGetAudio(id);
      const dur = audio.duration || dk.meta.duration || 1;
      _deckDrawWave(id, audio.currentTime / dur);
    }).observe(canvas);
  }
}

/* ── Init ─────────────────────────────────────────────────────────────── */
/* ── Public: show / hide the deck panel via the scan-bar toggle ────────── */
function deckSetPanel(open) {
  const panel = document.getElementById('deck-panel');
  if (!panel) return;
  panel.classList.toggle('deck-active', open);
  document.body.classList.toggle('deck-open', open);
  document.getElementById('deck-toggle-btn')?.classList.toggle('is-active', open);
}
function deckTogglePanel() {
  const panel = document.getElementById('deck-panel');
  deckSetPanel(!(panel && panel.classList.contains('deck-active')));
}
window.deckTogglePanel = deckTogglePanel;
window.deckSetPanel = deckSetPanel;

function _deckInit() {
  _deckInitOne('a');
  _deckInitOne('b');

  document.getElementById('deck-sync-a')?.addEventListener('click', () => _deckSync('a', 'b'));
  document.getElementById('deck-sync-b')?.addEventListener('click', () => _deckSync('b', 'a'));
  document.getElementById('deck-key-a')?.addEventListener('click', () => _deckKeyMatch('a', 'b'));
  document.getElementById('deck-key-b')?.addEventListener('click', () => _deckKeyMatch('b', 'a'));
  document.getElementById('deck-close-btn')?.addEventListener('click', () => deckSetPanel(false));

  // Drop a library track onto a deck half to load + play it there.
  ['a', 'b'].forEach((id) => {
    const half = document.getElementById('deck-half-' + id);
    if (!half) return;
    half.addEventListener('dragover', (e) => {
      if (!e.dataTransfer || !e.dataTransfer.types.includes('text/fg-track')) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      half.classList.add('deck-drop-hover');
    });
    half.addEventListener('dragleave', () => half.classList.remove('deck-drop-hover'));
    half.addEventListener('drop', (e) => {
      e.preventDefault();
      half.classList.remove('deck-drop-hover');
      const trackId = e.dataTransfer?.getData('text/fg-track');
      if (!trackId) return;
      const meta = (window.leTrackMeta?.(trackId)) || {};
      deckSetPanel(true);
      deckLoadTrack(trackId, meta, id);
      deckPlay(id);
    });
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _deckInit);
} else {
  _deckInit();
}
