/**
 * deck_control.js — FableGear Dual-Deck Audio Engine
 */

class Deck {
    constructor(id) {
        this.id = id;
        this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        this.gainNode = this.audioCtx.createGain();
        this.source = null;
        this.buffer = null;
        this.startTime = 0;
        this.pausedAt = 0;
        this.isPlaying = false;
        this.tempo = 1.0;

        this.gainNode.connect(this.audioCtx.destination);
    }

    async loadTrack(url) {
        const response = await fetch(url);
        const arrayBuffer = await response.arrayBuffer();
        this.buffer = await this.audioCtx.decodeAudioData(arrayBuffer);
    }

    play() {
        if (!this.buffer || this.isPlaying) return;
        
        this.source = this.audioCtx.createBufferSource();
        this.source.buffer = this.buffer;
        this.source.playbackRate.value = this.tempo;
        this.source.connect(this.gainNode);
        
        this.source.start(0, this.pausedAt);
        this.startTime = this.audioCtx.currentTime - this.pausedAt;
        this.isPlaying = true;
    }

    pause() {
        if (!this.isPlaying) return;
        this.source.stop();
        this.pausedAt = this.audioCtx.currentTime - this.startTime;
        this.isPlaying = false;
    }

    setTempo(rate) {
        this.tempo = rate;
        if (this.source) {
            this.source.playbackRate.value = rate;
        }
    }
}

// Global Manager
export const DeckManager = {
    deckA: new Deck('A'),
    deckB: new Deck('B'),
    
    setCrossfader(value) {
        // 0 = Full Deck A, 1 = Full Deck B
        this.deckA.gainNode.gain.value = 1 - value;
        this.deckB.gainNode.gain.value = value;
    }
};
