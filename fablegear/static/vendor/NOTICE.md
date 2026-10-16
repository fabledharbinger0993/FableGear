# Vendored third-party libraries

## soundtouch.js

- **Source:** SoundTouchJS v0.1.30 — https://github.com/cutterbl/SoundTouchJS
  (fetched from https://cdn.jsdelivr.net/npm/soundtouchjs@0.1.30/dist/soundtouch.js)
- **License:** GNU Lesser General Public License v2.1 (LGPL-2.1)
- **Copyright:** Olli Parviainen, Ryan Berdeen, Jakub Fiala, Steve 'Cutter' Blades
- **Why bundled:** the Record Room DJ decks route each track through this
  phase-vocoder so the TEMPO fader changes tempo and the KEY control changes key
  independently (real CDJ behavior). It is a pure client-side ES module —
  it runs entirely in the WebView's built-in Web Audio engine and makes **no
  network requests**, so FableGear stays fully offline.
- **Modifications:** none, except the trailing `//# sourceMappingURL=` comment
  was removed (the `.map` file is not shipped).

Per LGPL-2.1, this library is used as a separate, unmodified module. The full
license text is available at https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html
and is reproduced in the header of `soundtouch.js`.
