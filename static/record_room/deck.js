/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / deck.js
   Dual DJ decks (A/B) with independent audio, tempo, pitch, key lock.
   Center strip has SYNC for matching tempo between decks.
   ──────────────────────────────────────────────────────────────────────── */

/* ── Shared Web Audio engine (offline: SoundTouchJS phase-vocoder) ─────────
   Each deck decodes its track into an AudioBuffer and plays it through a
   SoundTouch PitchShifter, so the TEMPO fader changes tempo (pitch held) and
   the KEY control changes key (tempo held) — real CDJ behavior, running
   entirely in the WebView's built-in audio engine. No network, fully offline. */
let _actx = null;
function _deckCtx() {
  if (!_actx) _actx = new (window.AudioContext || window.webkitAudioContext)();
  return _actx;
}
let _PitchShifterPromise = null;
function _deckLib() {
  // The vendored library is a local ES module — imported on demand, never fetched
  // from a CDN, so the decks work offline just like the rest of FableGear.
  if (!_PitchShifterPromise) {
    _PitchShifterPromise = import('/static/vendor/soundtouch.js').then(m => m.PitchShifter);
  }
  return _PitchShifterPromise;
}

/* ── Per-deck state ───────────────────────────────────────────────────── */
const _decks = {
  a: { trackId: null, meta: { bpm: 0, key: '', duration: 0, title: '', artist: '', album: '' },
       tempoPct: 0, pitchSemitones: 0, keyLock: true, playing: false,
       shifter: null, gain: null, buffer: null, connected: false, loadToken: 0,
       animId: null, waveData: [],
       hotcues: [null, null, null, null],   // slot -> {inMsec, color} | null
       loop: { active: false, inSec: null, outSec: null } },
  b: { trackId: null, meta: { bpm: 0, key: '', duration: 0, title: '', artist: '', album: '' },
       tempoPct: 0, pitchSemitones: 0, keyLock: true, playing: false,
       shifter: null, gain: null, buffer: null, connected: false, loadToken: 0,
       animId: null, waveData: [],
       hotcues: [null, null, null, null],
       loop: { active: false, inSec: null, outSec: null } },
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

function _deckGain(id) {
  const dk = _decks[id];
  if (dk.gain) return dk.gain;
  const ctx = _deckCtx();
  dk.gain = ctx.createGain();
  dk.gain.gain.value = 1;
  dk.gain.connect(ctx.destination);
  return dk.gain;
}

// Current playback position (seconds) + duration for the loaded deck. The
// SoundTouch pipeline tracks its own source position, so time comes from there.
function _deckPos(id) {
  const dk = _decks[id];
  const dur = (dk.shifter && dk.shifter.duration) || dk.meta.duration || 0;
  const cur = dk.shifter ? Math.min(dk.shifter.timePlayed || 0, dur || Infinity) : 0;
  return { cur, dur };
}

// Show/clear the "decoding…" state while a track is being decoded for a deck.
function _deckSetDecoding(id, on) {
  document.getElementById('deck-half-' + id)?.classList.toggle('deck-decoding', !!on);
}

/* ── Playback UI ──────────────────────────────────────────────────────── */
function _deckSetPlayingUI(id, playing) {
  const dk = _decks[id];
  dk.playing = playing;
  const ico = _d(id, 'play-ico');
  const btn = _d(id, 'play-btn');
  if (playing) {
    _d(id, 'vinyl')?.classList.add('deck-spin');
    if (ico) ico.innerHTML = '&#10074;&#10074;';
    if (btn) { btn.lastChild.textContent = ' PAUSE'; btn.classList.add('deck-btn-active'); }
    _deckStartAnim(id);
  } else {
    _d(id, 'vinyl')?.classList.remove('deck-spin');
    if (ico) ico.innerHTML = '&#9654;';
    if (btn) { btn.lastChild.textContent = ' PLAY'; btn.classList.remove('deck-btn-active'); }
    cancelAnimationFrame(dk.animId);
  }
  window.leRefreshPlaybackButtons?.();
}

// Called by the SoundTouch pipeline when the track runs out.
function _deckOnEnded(id) {
  const dk = _decks[id];
  _deckDisconnect(id);
  _deckSetPlayingUI(id, false);
  if (dk.shifter) dk.shifter.percentagePlayed = 0;
  _deckDrawWave(id, 0);
  _deckUpdateTimes(id, 0, dk.meta.duration || 0);
}

/* ── Loop enforcement ─────────────────────────────────────────────────────
   Called from the shifter's own 'play' event (real audio-block callback —
   see deckLoadTrack) so it fires reliably even when rAF is throttled or
   stopped by a backgrounded tab. Also called from the animation tick below
   as a cheap redundant check while the tab IS visible. Safe to call often:
   it's a no-op unless a loop is actually active and past its out point. */
function _deckLoopCheck(id) {
  const dk = _decks[id];
  if (!dk.loop.active || dk.loop.outSec == null || !dk.shifter) return;
  const { cur, dur } = _deckPos(id);
  if (dur && cur >= dk.loop.outSec) {
    dk.shifter.percentagePlayed = dk.loop.inSec / dur;   // fraction, not percent — see _deckWaveScrub
  }
}

/* ── Animation loop ───────────────────────────────────────────────────── */
function _deckStartAnim(id) {
  const dk = _decks[id];
  cancelAnimationFrame(dk.animId);
  function tick() {
    if (!dk.playing) return;
    _deckLoopCheck(id);
    const { cur, dur } = _deckPos(id);
    const pos = dur ? cur / dur : 0;
    _deckDrawWave(id, pos);
    _deckUpdateTimes(id, cur, dur);
    dk.animId = requestAnimationFrame(tick);
  }
  dk.animId = requestAnimationFrame(tick);
}

/* ── Public: load a track into a specific deck ────────────────────────── */
async function deckLoadTrack(trackId, meta, targetDeck, opts) {
  const id = targetDeck || _deckNextTarget;
  const dk = _decks[id];
  if (!dk) return null;
  const andPlay = !!(opts && opts.play);

  // Tear down whatever is currently on this deck.
  _deckDisconnect(id);
  _deckSetPlayingUI(id, false);
  dk.shifter = null;
  dk.buffer = null;

  dk.trackId = trackId;
  dk.meta = {
    bpm: parseFloat(meta.bpm) || 0,
    key: meta.key || '',
    duration: parseFloat(meta.duration) || 0,
    title: meta.title || 'Untitled',
    artist: meta.artist || '',
    album: meta.album || '',
  };

  dk.hotcues = [null, null, null, null];
  dk.loop = { active: false, inSec: null, outSec: null };
  _deckRenderHotcues(id);
  _deckRenderLoopUI(id);
  _deckFetchCues(id, trackId);

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

  // Single audio focus: the inline sample player never doubles up on a deck.
  window.leInlinePause?.();

  document.getElementById('deck-panel')?.classList.add('deck-active');
  document.body.classList.add('deck-open');
  document.getElementById('deck-toggle-btn')?.classList.add('is-active');
  document.getElementById('deck-half-' + id)?.classList.add('deck-loaded');
  _deckNextTarget = (id === 'a') ? 'b' : 'a';
  window.leRefreshPlaybackButtons?.();

  // Decode the track and build its SoundTouch pipeline. A per-deck load token
  // guards against a newer load landing before this decode resolves.
  const token = ++dk.loadToken;
  _deckSetDecoding(id, true);
  try {
    const ctx = _deckCtx();
    const PitchShifter = await _deckLib();
    const res = await fetch('/api/library/tracks/' + encodeURIComponent(trackId) + '/stream');
    if (!res.ok) throw new Error('stream ' + res.status);
    const buffer = await ctx.decodeAudioData(await res.arrayBuffer());
    if (token !== dk.loadToken) return id;   // superseded by a newer load
    dk.buffer = buffer;
    dk.shifter = new PitchShifter(ctx, buffer, 4096, () => _deckOnEnded(id));
    dk.connected = false;
    // Loop enforcement rides the shifter's own 'play' event (fired from its
    // real audio-block callback) rather than requestAnimationFrame — rAF
    // throttles or fully stops when the tab is backgrounded/minimized, but
    // audio keeps playing regardless, so a rAF-driven loop check can silently
    // miss the loop-out point entirely and just play through it.
    dk.shifter.node.addEventListener('play', () => _deckLoopCheck(id));
    if (!dk.meta.duration) {
      dk.meta.duration = buffer.duration;
      if (durEl) durEl.textContent = _deckFmtTime(buffer.duration);
    }
    _deckUpdateControls(id);   // applies tempo/pitch to the fresh pipeline
    _deckSetDecoding(id, false);
    if (andPlay) _deckPlay(id);
  } catch (e) {
    if (token === dk.loadToken) {
      _deckSetDecoding(id, false);
      if (typeof showToast === 'function') {
        showToast('Could not load Deck ' + id.toUpperCase() + ' (' + (e.message || e) + ').', 'error');
      }
    }
  }
  return id;
}

/* ── Public deck API (used by the library list + drag-drop) ───────────── */
function deckPlay(id) { _deckPlay(id); }
function deckPause(id) { _deckPauseDeck(id); }
function deckPauseAll() { _deckPauseDeck('a'); _deckPauseDeck('b'); }
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
window.deckPauseAll = deckPauseAll;
window.deckFindTrack = deckFindTrack;
window.deckIsPlaying = deckIsPlaying;

// Read-only introspection for diagnostics (support + tests). No side effects.
window.deckState = (id) => {
  const dk = _decks[id];
  if (!dk) return null;
  const st = dk.shifter && dk.shifter._soundtouch;
  return {
    trackId: dk.trackId, playing: dk.playing, connected: dk.connected,
    keyLock: dk.keyLock, tempoPct: dk.tempoPct, pitchSemitones: dk.pitchSemitones,
    hasShifter: !!dk.shifter,
    timePlayed: dk.shifter ? dk.shifter.timePlayed : null,
    duration: dk.shifter ? dk.shifter.duration : null,
    stTempo: st ? st.tempo : null, stRate: st ? st.rate : null, stPitch: st ? st.pitch : null,
  };
};

/* ── Transport ────────────────────────────────────────────────────────────
   A SoundTouch pipeline is a ScriptProcessorNode that only pulls audio while
   connected to the graph. So "play" = connect the node, "pause" = disconnect
   it; the source position is preserved across the two. */
function _deckConnect(id) {
  const dk = _decks[id];
  if (dk.shifter && !dk.connected) { dk.shifter.connect(_deckGain(id)); dk.connected = true; }
}
function _deckDisconnect(id) {
  const dk = _decks[id];
  if (dk.shifter && dk.connected) { try { dk.shifter.disconnect(); } catch (_) {} dk.connected = false; }
}

function _deckPlay(id) {
  const dk = _decks[id];
  if (!dk.shifter) return;                  // nothing decoded yet
  window.leInlinePause?.();                  // single audio focus
  const ctx = _deckCtx();
  if (ctx.state === 'suspended') ctx.resume();   // WKWebView needs a gesture-time resume
  _deckConnect(id);
  _deckSetPlayingUI(id, true);
}

function _deckPauseDeck(id) {
  const dk = _decks[id];
  if (!dk.playing && !dk.connected) return;
  _deckDisconnect(id);
  _deckSetPlayingUI(id, false);
}

function _deckTogglePlay(id) {
  _decks[id].playing ? _deckPauseDeck(id) : _deckPlay(id);
}

function _deckCue(id) {
  const dk = _decks[id];
  _deckDisconnect(id);
  _deckSetPlayingUI(id, false);
  if (dk.shifter) dk.shifter.percentagePlayed = 0;
  _deckDrawWave(id, 0);
  _deckUpdateTimes(id, 0, dk.meta.duration || 0);
}

/* ── Rate engine ──────────────────────────────────────────────────────── */
function _deckEffectiveRate(id) {
  const dk = _decks[id];
  const tempoFactor = 1 + dk.tempoPct / 100;
  return dk.keyLock ? tempoFactor : tempoFactor * Math.pow(2, dk.pitchSemitones / 12);
}

function _deckApplyRate(id) {
  const dk = _decks[id];
  if (!dk.shifter) return;
  const tempoFactor = 1 + dk.tempoPct / 100;
  if (dk.keyLock) {
    // Keylock ON — independent controls: TEMPO time-stretches (pitch held) and
    // KEY shifts pitch (tempo held). This is the fix: the tempo fader now
    // actually changes speed, and the key control no longer changes speed.
    dk.shifter.tempo = tempoFactor;
    dk.shifter.rate = 1;
    dk.shifter.pitchSemitones = dk.pitchSemitones;
  } else {
    // Keylock OFF — vinyl: speed and pitch ride together on a single rate.
    dk.shifter.tempo = 1;
    dk.shifter.pitchSemitones = 0;
    dk.shifter.rate = _deckEffectiveRate(id);
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
  if (!srcBpm || !dstBpm || !dst.shifter) return;
  const srcCur = _deckPos(fromId).cur;
  const { cur: dstCur, dur: dstDur } = _deckPos(toId);
  const srcPhase = (srcCur * srcBpm / 60) % 1;                  // fraction of a beat
  const dstPhase = (dstCur * dstBpm / 60) % 1;
  let delta = srcPhase - dstPhase;                              // beats to shift
  if (delta > 0.5) delta -= 1;                                  // take the short way
  if (delta < -0.5) delta += 1;
  const shiftSec = delta * 60 / dstBpm;
  const next = dstCur + shiftSec;
  if (next >= 0 && (!dstDur || next < dstDur)) {
    // NOTE: PitchShifter.percentagePlayed's setter/getter are asymmetric in
    // the vendored lib — the getter returns 0-100 but the setter expects a
    // 0-1 fraction (sets sourcePosition = perc * duration * sampleRate
    // directly, no /100). Assigning a 0-100 value here seeks 100x too far.
    dst.shifter.percentagePlayed = dstDur ? (next / dstDur) : 0;
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

  // Deck colors come from the app tokens: A = cyan (--accent-rgb), B = magenta (--accent-b-rgb).
  const rootStyle = getComputedStyle(document.documentElement);
  const aRgb = rootStyle.getPropertyValue('--accent-rgb').trim() || '0,212,232';
  const bRgb = rootStyle.getPropertyValue('--accent-b-rgb').trim() || '255,45,120';
  const accent = id === 'a' ? `rgba(${aRgb},0.75)` : `rgba(${bRgb},0.70)`;
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
  const dur = (dk.shifter && dk.shifter.duration) || dk.meta.duration || 0;
  if (dk.shifter && dur > 0) {
    // See the note in _deckPhaseNudge — the setter wants a 0-1 fraction, not
    // a 0-100 percentage, despite the property's own getter returning 0-100.
    dk.shifter.percentagePlayed = pct;
    _deckDrawWave(id, pct);
    _deckUpdateTimes(id, pct * dur, dur);
  }
}

/* ── Hot cues ─────────────────────────────────────────────────────────────
   4 pads per deck (Record Room's own call — Rekordbox ships 8, but the
   "not too busy" sizing goal from the performance-mode audit wins here).
   Backed by fg_cue via /api/library/tracks/<id>/cues — see routes_player.py.
   Click an empty pad: sets it at the current position. Click a set pad:
   jumps there (playback keeps running, CDJ-style). Right-click a set pad:
   clears it. */
async function _deckFetchCues(id, trackId) {
  try {
    const res = await fetch('/api/library/tracks/' + encodeURIComponent(trackId) + '/cues');
    if (!res.ok) return;
    const cues = await res.json();
    if (!Array.isArray(cues)) return;
    if (_decks[id].trackId !== trackId) return;  // a newer load superseded this fetch
    for (const c of cues) {
      if (c.kind === 1 && c.slot >= 0 && c.slot < 4) {
        _decks[id].hotcues[c.slot] = { inMsec: c.in_msec, color: c.color || null };
      }
    }
    _deckRenderHotcues(id);
  } catch (_) { /* offline / library not built — pads just stay empty */ }
}

function _deckRenderHotcues(id) {
  const dk = _decks[id];
  for (let slot = 0; slot < 4; slot++) {
    const pad = _d(id, 'hc-' + slot);
    if (pad) pad.classList.toggle('deck-hotcue-set', !!dk.hotcues[slot]);
  }
}

function _deckPostCue(id, slot, inMsec) {
  const dk = _decks[id];
  if (dk.trackId == null) return;
  fetch('/api/library/tracks/' + encodeURIComponent(dk.trackId) + '/cues', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 1, slot, in_msec: inMsec }),
  }).catch(() => {
    if (typeof showToast === 'function') showToast('Hot cue not saved — offline or library unavailable.', 'warning');
  });
}

function _deckHotcueTap(id, slot) {
  const dk = _decks[id];
  const existing = dk.hotcues[slot];
  const { dur } = _deckPos(id);
  if (existing) {
    // Jump — keep playing, don't stop the mix.
    if (dk.shifter && dur > 0) {
      dk.shifter.percentagePlayed = existing.inMsec / 1000 / dur;   // fraction, not percent
      if (!dk.playing) { _deckDrawWave(id, existing.inMsec / 1000 / dur); _deckUpdateTimes(id, existing.inMsec / 1000, dur); }
    }
    return;
  }
  // Set — capture the current position.
  if (!dk.shifter || dur <= 0) {
    if (typeof showToast === 'function') showToast('Load a track before setting hot cues.', 'warning');
    return;
  }
  const { cur } = _deckPos(id);
  const inMsec = Math.round(cur * 1000);
  dk.hotcues[slot] = { inMsec, color: null };
  _deckRenderHotcues(id);
  _deckPostCue(id, slot, inMsec);
}

function _deckHotcueClear(id, slot) {
  const dk = _decks[id];
  if (!dk.hotcues[slot]) return;
  dk.hotcues[slot] = null;
  _deckRenderHotcues(id);
  _deckPostCue(id, slot, null);
}

/* ── Loop ─────────────────────────────────────────────────────────────────
   IN / OUT define a loop at any length; RELOOP re-arms the last loop without
   redefining it; the ½×/2× pair halves/doubles an *active* loop's length —
   mirrors Rekordbox's IN/OUT/RELOOP + beat-length stepper without trying to
   match its full auto-loop bank. Loop state is playback-session only (not
   persisted) — same scope as tempo/pitch, unlike hot cues. */
function _deckRenderLoopUI(id) {
  const dk = _decks[id];
  const btn = _d(id, 'reloop');
  if (btn) btn.classList.toggle('deck-loop-active', dk.loop.active);
  const lenEl = _d(id, 'loop-len');
  if (lenEl) {
    if (dk.loop.inSec != null && dk.loop.outSec != null && dk.meta.bpm) {
      const beats = Math.round((dk.loop.outSec - dk.loop.inSec) * dk.meta.bpm / 60);
      lenEl.textContent = beats > 0 ? String(beats) : '—';
    } else {
      lenEl.textContent = '—';
    }
  }
}

function _deckLoopIn(id) {
  const dk = _decks[id];
  if (!dk.shifter) return;
  dk.loop.inSec = _deckPos(id).cur;
  dk.loop.active = false;   // defining a new IN cancels whatever was looping
  if (dk.loop.outSec != null && dk.loop.outSec <= dk.loop.inSec) dk.loop.outSec = null;
  _deckRenderLoopUI(id);
}

function _deckLoopOut(id) {
  const dk = _decks[id];
  if (!dk.shifter) return;
  const cur = _deckPos(id).cur;
  if (dk.loop.inSec == null || cur <= dk.loop.inSec) {
    if (typeof showToast === 'function') showToast('Set LOOP IN first, further back in the track.', 'warning');
    return;
  }
  dk.loop.outSec = cur;
  dk.loop.active = true;
  _deckRenderLoopUI(id);
}

function _deckReloopToggle(id) {
  const dk = _decks[id];
  if (dk.loop.inSec == null || dk.loop.outSec == null) return;  // nothing defined yet
  dk.loop.active = !dk.loop.active;
  if (dk.loop.active && dk.shifter) {
    const { dur } = _deckPos(id);
    if (dur > 0) dk.shifter.percentagePlayed = dk.loop.inSec / dur;   // fraction, not percent
  }
  _deckRenderLoopUI(id);
}

function _deckLoopScale(id, factor) {
  const dk = _decks[id];
  if (!dk.loop.active || dk.loop.inSec == null || dk.loop.outSec == null) return;
  const len = (dk.loop.outSec - dk.loop.inSec) * factor;
  if (len < 0.05) return;  // don't let ½× collapse the loop to nothing
  dk.loop.outSec = dk.loop.inSec + len;
  _deckRenderLoopUI(id);
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

  for (let slot = 0; slot < 4; slot++) {
    const pad = _d(id, 'hc-' + slot);
    if (!pad) continue;
    pad.addEventListener('click', () => _deckHotcueTap(id, slot));
    pad.addEventListener('contextmenu', (e) => { e.preventDefault(); _deckHotcueClear(id, slot); });
  }

  _d(id, 'loop-in')?.addEventListener('click', () => _deckLoopIn(id));
  _d(id, 'loop-out')?.addEventListener('click', () => _deckLoopOut(id));
  _d(id, 'reloop')?.addEventListener('click', () => _deckReloopToggle(id));
  _d(id, 'loop-halve')?.addEventListener('click', () => _deckLoopScale(id, 0.5));
  _d(id, 'loop-double')?.addEventListener('click', () => _deckLoopScale(id, 2));

  const canvas = _d(id, 'wave-canvas');
  if (canvas) {
    new ResizeObserver(() => {
      const { cur, dur } = _deckPos(id);
      _deckDrawWave(id, dur ? cur / dur : 0);
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
      deckLoadTrack(trackId, meta, id, { play: true });
    });
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _deckInit);
} else {
  _deckInit();
}
