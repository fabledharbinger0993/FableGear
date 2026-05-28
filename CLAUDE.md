# FableSignal — Operating Contract

> **Stack:** This project is a native iOS app: Swift 5.9+, SwiftUI, AVFoundation (AVAudioEngine + AVCaptureDevice torch). Minimum target iOS 16. No third-party dependencies at v1 unless justified via Congress Moment. Xcode project; session-logic isolated in a Swift Package for testability.

---

## Operating Contract

This is the working protocol for the entire build. It is not optional and not aspirational.

### Phase 0 — Prompt Enhancement (non-optional)

Before any implementation step, parse the active request for: explicit goal, implied constraints, edge cases and failure modes, scope ambiguity, underspecified success criteria, and embedded assumptions. Surface `ENHANCED PROMPT` and `INFERENCES MADE` blocks, then ask "Proceed on this, or correct it?" Skip only for trivially unambiguous single-file single-change tasks with no side-effect exposure.

### Phase 1 — Dual-Path Generation (non-trivial tasks)

Internally generate two approaches (Path A: direct/conventional; Path B: alternative structure). Compare against fit with existing conventions, technical-debt impact, cross-file side effects, testability, reversibility. Before selecting, test for **anastomosis**: shared structural material that could fuse into a hybrid (Path C) neither produces alone. Surface winner/fusion, rationale, what the rejected path offered, and whether it was discarded or absorbed. Invite: "Defend the rejected path, attack the winner, or proceed."

### Phase 2 — Live Audit During Implementation

After each meaningful change, identify the active frontier (highest-uncertainty edge) and direct the next probe there rather than reinforcing solid ground. Trace outbound connections after every file change: importers, imports, asset references, Codable contracts, target membership. Run objective checks: `swift build`, `xcodebuild test` (or `swift test` for the logic package), and SwiftLint if configured. On conflict or breakage: stop, surface explicitly, propose resolution, do not proceed past it. Log clean passes.

### Phase 3 — Self-Verification Before "Done"

Verify each conclusion has multi-path support (normal confidence) vs single-chain support (flag as `SINGLE-CHAIN FINDING`, state what a second path would look like) vs contested (`CONTESTED FINDING`, name the conflict). Re-read every file touched, re-trace connections, run a final objective check pass. Surface a verification summary: files touched, connections traced, check results, open findings, status `CLEAN` or `FINDINGS REMAIN`.

### Congress Moments (high-impact decisions)

Trigger on: architecture or data-model changes, anything touching the safety gate, irreversible changes, major UX direction, or conflicting constraints with no dominant resolution. Format: state the decision; Option A and B with strengths/risks; preferred option and rationale; what evidence would reverse it. Ask "Defend, attack, or proceed?"

### Integrity Non-Negotiables

- Never fabricate test results, build output, or device-behavior confirmations. The torch and audio hardware cannot be unit-tested — if a claim depends on real-device behavior and you have not run on device, label it `UNVERIFIED — requires device test`.
- Never paper over uncertainty. Name it and state what would resolve it.
- Distinguish demonstrated / inferred / speculative in any factual claim, including neuroscience claims baked into the UI copy.
- If a tool is unavailable, say so — do not simulate its output.
- Disagree with the brief where evidence requires it.

---

## Research Ground Truth (do not alter)

Evidence tiers: **[D] demonstrated**, **[I] inferred**, **[S] speculative**.

### Core mechanism — [D]
SSVEP: rhythmic visual flicker drives the visual cortex at the flash frequency. Analogous auditory mechanism is the frequency-following response. Source: SSVEP literature; large-sample audiovisual EEG study (bioRxiv 2023, ~248 usable participants).

### Binaural beat band map
- Delta 0.5–4 Hz — deep sleep
- Theta 4–7 Hz — drowsiness, meditation, wake→sleep transition
- Alpha 8–13 Hz — relaxed but alert, eyes-closed calm
- Beta 13–30 Hz — concentration, alertness

Requires headphones. Carriers ~200–500 Hz; beat frequency under ~30 Hz. Subjective effects [I].

### Per-session parameters

**Open / Relax**
- Strobe: 10 Hz → 6 Hz (alpha into theta), eased over session
- Binaural: carrier ~200 Hz, beat 10 Hz → 6 Hz tracking strobe
- Basis: [D] entrainment mechanism; [I] subjective meditative effect

**Alert / Wake**
- Strobe: 40 Hz sustained (gamma)
- Binaural: beta beat ~18 Hz, carrier ~250 Hz
- Basis: [D] 40 Hz audiovisual flicker improved sustained-attention accuracy/reaction time (controlled EEG study, 62 healthy adults)
- CAVEAT [S/UNVERIFIED]: iPhone torch at 40 Hz — run below max intensity (~0.5–0.7), monitor thermalState

**Wind-down / Sleep**
- Strobe: 8 Hz → 5 Hz → 3 Hz, stepped descent
- Binaural: 8 Hz → 5 Hz → 2.5 Hz, stepped, tracking strobe
- Basis: [D] mechanism; [I] sleep effect. Do NOT ship a single-frequency wind-down.

---

## Architecture Decisions

### Timing (Path C — resolved)
Audio engine as master for binaural tones (sample-accurate, lowest jitter). Torch driven by a dedicated high-priority timer, periodically resynced to the audio render clock to eliminate long-term drift. Applies to Engine mode only; Bring-your-own mode uses a freestanding strobe timer.

### Audio Modes (resolved)
- **Engine mode** — BinauralSynth + SoundscapeMixer. Requires headphones. Phase-locked to audio render clock.
- **Bring-your-own mode** — user's own playlist via system audio. No BinauralSynth. Strobe runs standalone. No headphone requirement.

### 40 Hz strobe (resolved)
Deliver via torch. Run intensity ≤0.7. Monitor `ProcessInfo.thermalState`, degrade gracefully. Label device confirmation `UNVERIFIED` until build phase 3.

### Session delivery (resolved)
Bundled JSON at v1. Architect remote-delivery seam in schema/loader but do not implement it.

### Soundscape source (OPEN — Section 9.4)
Generated ambient audio vs licensed/produced audio beds. Blocks SoundscapeMixer architecture; requires decision before Phase 1 on that module.

---

## Safety Requirements (NON-NEGOTIABLE)

- Photosensitive epilepsy gate: blocking warning + explicit acknowledgment before first light session. This gate is a Congress Moment if anyone proposes weakening it.
- Runtime strobe ceiling: enforce `maxStrobeHz` in `StrobeController` regardless of session data.
- Graceful interruption: phone call, alarm, or audio-route change pauses session and turns torch off immediately.
- No medical claims in UI copy. Tier neuroscience claims; only ship [D]-grounded phrasing as fact; phrase [I] as "research suggests."

---

## Module Breakdown

```
SessionKit (Swift Package — fully unit-testable)
  SessionModel       — Codable session/segment/curve types
  CurveEvaluator     — interpolates frequency curve at time t (linear + easeInOut)
  SessionScheduler   — converts Session into time-ordered event stream

AudioEngine (device-dependent)
  BinauralSynth      — AVAudioSourceNode, two hard-panned phase accumulators
  SoundscapeMixer    — musical bed layered under binaural tones

StrobeController (device-dependent)
  Wraps AVCaptureDevice torch; holds lockForConfiguration; resync hook to audio clock

SessionRunner
  Orchestrates AudioEngine + StrobeController; owns play/pause/stop

App (SwiftUI)
  Safety gate → session selection → in-session UI → settings
```

All types in AudioEngine and StrobeController: device-test-only. Cannot be validated in simulator.

---

## Build Phases (each gated by Phase 2/3 verification)

1. **SessionKit logic package** — schema + CurveEvaluator + SessionScheduler. Full unit-test coverage.
2. **BinauralSynth** — sample-accurate two-tone generation, click-free ramping. Device test: clean beat at 6/10/18 Hz.
3. **StrobeController** — torch on/off + intensity + lock + cap. Device test: clean strobe ≤12 Hz; attempt 40 Hz and report (answers 40 Hz Congress Moment).
4. **SessionRunner + shared clock** — Path C timing wired. Device test: strobe/beat phase-correlated, no drift, no artifacts.
5. **SwiftUI shell** — safety gate → session select → in-session UI → settings. Wire three v1 sessions.
6. **Hardening** — interruption handling, thermal degradation, headphone-route handling, battery, accessibility.

Do not advance a phase while prior phase verification reads `FINDINGS REMAIN`.

---

## Testing Strategy

- **Unit-testable (must be covered):** all of SessionKit — decode/encode round-trips, curve interpolation edge cases (before-first, after-last, at-keyframe, between-keyframe, single-keyframe constant), scheduler event ordering, safety-cap enforcement on malformed input.
- **Device-only (label honestly):** torch timing, binaural beat perceptibility, audio/light phase correlation, thermal behavior, interruption recovery. All claims about hardware require a real-device run; if not run, label `UNVERIFIED — requires device test`.
- The simulator has no torch and unreliable audio timing — passing tests there proves nothing about the hardware layers.

---

## Backlog (not blocking v1)

- Optimal carrier Hz per band for perceived beat strength on AirPods. [UNVERIFIED]
- Whether torch duty cycle materially changes entrainment vs frequency alone. [UNVERIFIED]
- Habituation: repeated-exposure studies show entrainment strength can decline over weeks. Relevant to a daily-use product.
