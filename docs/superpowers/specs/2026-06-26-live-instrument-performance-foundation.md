# Live Instrument Mode — High-Value Build Prompt

## Rewritten Prompt (Highest Clarity)
Build FableGear into a live performance instrument that can replace Rekordbox on Pioneer workflows.

Execute Phase 1 now, inside the current codebase, without breaking existing library/editor behavior.

Phase 1 scope:
1. Implement a shared dual-deck timing engine (single audio clock).
2. Add deterministic deck state reporting (position, duration, playing state, tempo, BPM, key).
3. Add tempo sync and phase sync controls between decks.
4. Add key-lock and key-shift control plumbing for harmonic workflows.
5. Expose a stable integration surface for UI and controller mapping to consume.

Hard constraints:
1. Keep implementation local-first and offline-safe.
2. Do not add external services.
3. Keep behavior reversible and non-destructive.
4. Preserve current playback paths while introducing new APIs.
5. Maintain compatibility with existing script/module loading.

Definition of done:
1. A and B decks can load and play with one shared engine clock.
2. External UI can subscribe to deck state updates.
3. Sync operations can match tempo and phase from source deck to target deck.
4. Key lock and key shift are represented in deck state and controllable by API.
5. A global bridge object exists for future platter/FX/HID UI.

## Execution Notes
- This phase intentionally ships a strong engine skeleton first.
- Future phases should layer turntable UI, FX graph, beatgrid quantize, and HID mappings.
- Keep this spec as the canonical reference when extending the player stack.
