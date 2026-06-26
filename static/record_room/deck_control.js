/**
 * deck_control.js — FableGear Performance Engine Foundation (Phase 1)
 */

const AudioCtx = window.AudioContext || window.webkitAudioContext;
const SHARED_CTX = new AudioCtx();
const MASTER_GAIN = SHARED_CTX.createGain();
MASTER_GAIN.gain.value = 1.0;
MASTER_GAIN.connect(SHARED_CTX.destination);

const _CAM_MAJOR = ['8B', '3B', '10B', '5B', '12B', '7B', '2B', '9B', '4B', '11B', '6B', '1B'];
const _CAM_MINOR = ['5A', '12A', '7A', '2A', '9A', '4A', '11A', '6A', '1A', '8A', '3A', '10A'];

function _clamp(num, min, max) {
    return Math.max(min, Math.min(max, num));
}

function _normalizeDeckId(value) {
    const s = String(value || '').toUpperCase();
    return s === 'A' || s === 'B' ? s : 'A';
}

function _parseCamelot(key) {
    const m = String(key || '').trim().toUpperCase().match(/^(\d{1,2})([AB])$/);
    if (!m) return null;
    const n = Number(m[1]);
    if (!Number.isInteger(n) || n < 1 || n > 12) return null;
    return { n, mode: m[2] };
}

function _shiftCamelot(key, semitones) {
    const parsed = _parseCamelot(key);
    if (!parsed || !Number.isFinite(semitones)) return key || null;
    const ring = parsed.mode === 'A' ? _CAM_MINOR : _CAM_MAJOR;
    const idx = ring.indexOf(`${parsed.n}${parsed.mode}`);
    if (idx < 0) return key || null;
    const next = (idx + semitones % 12 + 12) % 12;
    return ring[next];
}

class Deck {
    constructor(id) {
        this.id = _normalizeDeckId(id);
        this.audioCtx = SHARED_CTX;
        this.gainNode = this.audioCtx.createGain();
        this.gainNode.gain.value = 1.0;
        this.gainNode.connect(MASTER_GAIN);

        this.source = null;
        this.buffer = null;
        this.trackMeta = null;
        this.sourceUrl = null;

        this.startTime = 0;
        this.pausedAt = 0;
        this.isPlaying = false;

        this.tempo = 1.0;
        this.keyShift = 0;
        this.keyLock = true;
    }

    async loadTrack(url, trackMeta = null) {
        this.stop();
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`Failed to load track (${response.status})`);
        }
        const arrayBuffer = await response.arrayBuffer();
        this.buffer = await this.audioCtx.decodeAudioData(arrayBuffer);
        this.sourceUrl = url;
        this.trackMeta = {
            id: trackMeta?.id || null,
            title: trackMeta?.title || '',
            artist: trackMeta?.artist || '',
            bpm: Number(trackMeta?.bpm) || null,
            key: trackMeta?.key || null,
        };
        this.pausedAt = 0;
    }

    _createSource() {
        const src = this.audioCtx.createBufferSource();
        src.buffer = this.buffer;
        src.playbackRate.value = this.tempo;
        src.connect(this.gainNode);
        src.onended = () => {
            if (!this.isPlaying) return;
            this.isPlaying = false;
            this.source = null;
            this.pausedAt = this.duration();
            DeckManager._emitState();
        };
        return src;
    }

    async play() {
        if (!this.buffer || this.isPlaying) return;
        if (this.audioCtx.state !== 'running') {
            await this.audioCtx.resume();
        }
        this.source = this._createSource();
        this.source.start(0, _clamp(this.pausedAt, 0, this.duration()));
        this.startTime = this.audioCtx.currentTime - (this.pausedAt / this.tempo);
        this.isPlaying = true;
    }

    pause() {
        if (!this.isPlaying || !this.source) return;
        const pos = this.position();
        this.source.onended = null;
        this.source.stop();
        this.source = null;
        this.pausedAt = pos;
        this.isPlaying = false;
    }

    stop() {
        if (this.source) {
            this.source.onended = null;
            this.source.stop();
            this.source = null;
        }
        this.startTime = 0;
        this.pausedAt = 0;
        this.isPlaying = false;
    }

    seek(seconds) {
        if (!this.buffer) return;
        const clamped = _clamp(Number(seconds) || 0, 0, this.duration());
        const wasPlaying = this.isPlaying;
        this.pause();
        this.pausedAt = clamped;
        if (wasPlaying) {
            this.play();
        }
    }

    setTempo(rate) {
        const nextTempo = _clamp(Number(rate) || 1.0, 0.5, 2.0);
        const pos = this.position();
        this.tempo = nextTempo;
        if (this.isPlaying && this.source) {
            this.source.playbackRate.value = nextTempo;
            this.startTime = this.audioCtx.currentTime - (pos / nextTempo);
        }
    }

    setKeyShift(semitones) {
        this.keyShift = _clamp(Math.round(Number(semitones) || 0), -12, 12);
    }

    setKeyLock(enabled) {
        this.keyLock = !!enabled;
    }

    position() {
        if (!this.buffer) return 0;
        if (!this.isPlaying) return _clamp(this.pausedAt, 0, this.duration());
        const elapsed = (this.audioCtx.currentTime - this.startTime) * this.tempo;
        return _clamp(elapsed, 0, this.duration());
    }

    duration() {
        return this.buffer?.duration || 0;
    }

    effectiveBpm() {
        const base = Number(this.trackMeta?.bpm) || null;
        if (!base) return null;
        return base * this.tempo;
    }

    displayKey() {
        const base = this.trackMeta?.key || null;
        if (!base) return null;
        if (this.keyLock) return base;
        return _shiftCamelot(base, this.keyShift);
    }

    phaseBeats() {
        const bpm = this.effectiveBpm();
        if (!bpm) return null;
        return (this.position() * bpm) / 60;
    }
}

export const DeckManager = {
    deckA: new Deck('A'),
    deckB: new Deck('B'),
    crossfader: 0.5,
    bus: new EventTarget(),
    _clockTimer: null,

    _deckById(id) {
        return _normalizeDeckId(id) === 'B' ? this.deckB : this.deckA;
    },

    _clockPayload() {
        const deckState = (deck) => ({
            id: deck.id,
            isPlaying: deck.isPlaying,
            position: deck.position(),
            duration: deck.duration(),
            tempo: deck.tempo,
            bpm: deck.effectiveBpm(),
            key: deck.displayKey(),
            keyLock: deck.keyLock,
            keyShift: deck.keyShift,
            title: deck.trackMeta?.title || '',
            artist: deck.trackMeta?.artist || '',
        });

        return {
            ts: performance.now(),
            crossfader: this.crossfader,
            deckA: deckState(this.deckA),
            deckB: deckState(this.deckB),
        };
    },

    _emitState() {
        this.bus.dispatchEvent(new CustomEvent('fg-deck-state', { detail: this._clockPayload() }));
    },

    _startClock() {
        if (this._clockTimer) return;
        this._clockTimer = setInterval(() => {
            if (!this.deckA.isPlaying && !this.deckB.isPlaying) return;
            this._emitState();
        }, 50);
    },

    async loadTrackToDeck(deckId, url, trackMeta = null) {
        await this._deckById(deckId).loadTrack(url, trackMeta);
        this._emitState();
    },

    async playDeck(deckId) {
        await this._deckById(deckId).play();
        this._startClock();
        this._emitState();
    },

    pauseDeck(deckId) {
        this._deckById(deckId).pause();
        this._emitState();
    },

    seekDeck(deckId, seconds) {
        this._deckById(deckId).seek(seconds);
        this._emitState();
    },

    setDeckTempo(deckId, rate) {
        this._deckById(deckId).setTempo(rate);
        this._emitState();
    },

    setDeckKeyLock(deckId, enabled) {
        this._deckById(deckId).setKeyLock(enabled);
        this._emitState();
    },

    setDeckKeyShift(deckId, semitones) {
        this._deckById(deckId).setKeyShift(semitones);
        this._emitState();
    },

    setCrossfader(value) {
        this.crossfader = _clamp(Number(value) || 0, 0, 1);
        this.deckA.gainNode.gain.value = 1 - this.crossfader;
        this.deckB.gainNode.gain.value = this.crossfader;
        this._emitState();
    },

    syncTempo(sourceDeckId, targetDeckId) {
        const source = this._deckById(sourceDeckId);
        const target = this._deckById(targetDeckId);
        const sourceBpm = source.effectiveBpm();
        const targetBaseBpm = Number(target.trackMeta?.bpm) || null;
        if (!sourceBpm || !targetBaseBpm) return false;
        target.setTempo(sourceBpm / targetBaseBpm);
        this._emitState();
        return true;
    },

    syncPhase(sourceDeckId, targetDeckId) {
        const source = this._deckById(sourceDeckId);
        const target = this._deckById(targetDeckId);
        target.seek(source.position());
        this._emitState();
    },

    syncDeck(sourceDeckId, targetDeckId, { phase = true } = {}) {
        const ok = this.syncTempo(sourceDeckId, targetDeckId);
        if (phase) this.syncPhase(sourceDeckId, targetDeckId);
        return ok;
    },

    getState() {
        return this._clockPayload();
    },

    isHarmonicMatch(keyA, keyB) {
        const a = _parseCamelot(keyA);
        const b = _parseCamelot(keyB);
        if (!a || !b) return false;
        if (a.n === b.n && a.mode === b.mode) return true;
        if (a.n === b.n && a.mode !== b.mode) return true;
        if (a.mode === b.mode && ((a.n % 12) + 1 === b.n || (b.n % 12) + 1 === a.n)) return true;
        return false;
    },
};

DeckManager._startClock();
DeckManager.setCrossfader(0.5);

window.FablePerformance = {
    getState: () => DeckManager.getState(),
    onState: (cb) => {
        if (typeof cb !== 'function') return () => {};
        const handler = (evt) => cb(evt.detail);
        DeckManager.bus.addEventListener('fg-deck-state', handler);
        return () => DeckManager.bus.removeEventListener('fg-deck-state', handler);
    },
    syncDeck: (sourceDeck, targetDeck, opts) => DeckManager.syncDeck(sourceDeck, targetDeck, opts),
    setCrossfader: (v) => DeckManager.setCrossfader(v),
    setDeckTempo: (d, t) => DeckManager.setDeckTempo(d, t),
    setDeckKeyLock: (d, on) => DeckManager.setDeckKeyLock(d, on),
    setDeckKeyShift: (d, st) => DeckManager.setDeckKeyShift(d, st),
};
