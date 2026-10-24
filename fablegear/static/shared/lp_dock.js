/* ════════════════════════════════════════════════════════════════════════
   FableGear — left panel dock magnification (lp_dock.js)

   A macOS-Dock fisheye over the WHOLE left panel. At rest every button is the
   same size; the one nearest the pointer grows and its neighbours ease back
   down with distance. It spans all four groups — the room nav, the TOOLS
   column, Drives, and the bottom utilities — as one continuous dock, so the
   magnification carries across the dividers between them.

   Why it exists: the panel is 80px wide and now carries 15 buttons. At a
   comfortable fixed size they don't fit. Resting small and magnifying on
   approach makes them fit AND makes whatever you're reaching for the biggest
   thing in the panel.

   TWO RULES TO PRESERVE IF YOU TOUCH THIS
   1. Constant sum. Row heights are normalised to always total count x base.
      Growing one shrinks the far ones by exactly as much, so the panel's total
      height never changes. Without it, magnifying grew the column ~50px, shoved
      every group below the pointer downwards, and tripped the panel scrollbar
      every time the pointer crossed it — so the button you were reaching for
      moved out from under you.
   2. Resting geometry. Distances are measured against each button's RESTING
      centre, cached by _measure(). Measuring live rects would feed the fisheye
      its own output and make it chase itself. The groups are separated by a
      label, a divider and a spacer, so the centres can't be derived
      arithmetically — they have to be measured, at rest, and re-measured when
      the panel's layout changes.

   Heights are published as a --lp-h custom property per button; the stylesheet
   owns what that height means for the icon or glyph inside it.
   ──────────────────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  const MAX_SCALE = 2.45;   // peak magnification of the nearest button
  const SPREAD    = 1.35;   // falloff width, in rows — how many neighbours react
  const LABEL_AT  = 1.55;   // show the name once a button is this magnified

  let _panel   = null;
  let _buttons = [];        // currently visible buttons, in document order
  let _centres = [];        // their resting centres, viewport coords
  let _base    = 44;
  let _label   = null;
  let _frame   = 0;
  let _measured = false;

  function _readBase() {
    const v = parseFloat(getComputedStyle(_panel).getPropertyValue('--lp-dock-base'));
    return Number.isFinite(v) && v > 0 ? v : 44;
  }

  /* Buttons that are actually on screen. The room-swap slot hides whichever
     room you're already in, and the drive list can add or remove entries, so
     this is recomputed rather than cached at init. */
  function _visibleButtons() {
    return Array.from(_panel.querySelectorAll('.lp-btn'))
      .filter(b => b.offsetParent !== null);
  }

  /* Cache resting centres. Only valid while the dock is at rest, so every
     caller must guarantee that (page load, pointer entering from outside,
     resize, scroll). */
  function _measure() {
    _base = _readBase();
    _buttons = _visibleButtons();
    _buttons.forEach(b => b.style.setProperty('--lp-h', `${_base}px`));
    // One forced reflow, so the rects below reflect the resting heights rather
    // than whatever the previous frame left behind.
    void _panel.offsetHeight;
    _centres = _buttons.map(b => {
      const r = b.getBoundingClientRect();
      return r.top + r.height / 2;
    });
    _measured = true;
  }

  function _ensureLabel() {
    if (_label && _label.isConnected) return _label;
    _label = document.getElementById('lp-dock-label');
    if (!_label) {
      _label = document.createElement('div');
      _label.id = 'lp-dock-label';
      // Decorative: each button already carries its own accessible name, so a
      // second announcement of it would just be noise.
      _label.setAttribute('aria-hidden', 'true');
      document.body.appendChild(_label);
    }
    return _label;
  }

  function _nameOf(btn) {
    return (btn.getAttribute('aria-label')
      || btn.querySelector('.lp-label')?.textContent
      || btn.getAttribute('title')
      || '').replace(/\s+/g, ' ').trim();
  }

  function _showLabel(btn) {
    const el = _ensureLabel();
    const name = _nameOf(btn);
    if (!name) { _hideLabel(); return; }
    // A title can carry a whole explanation ("Forge — Iron, Anvil & Quench…");
    // the flyout wants the name, so cut at the first dash.
    el.textContent = name.split(/\s+[—–-]\s+/)[0];
    el.classList.add('open');
    const r = btn.getBoundingClientRect();
    // Measured after the text is set, so a long name still centres on the row.
    const h = el.offsetHeight || 24;
    el.style.left = `${r.right + 12}px`;
    el.style.top  = `${Math.max(6, Math.min(window.innerHeight - h - 6,
                                            r.top + r.height / 2 - h / 2))}px`;
  }

  function _hideLabel() {
    if (_label) _label.classList.remove('open');
  }

  /* Lay the panel out for a pointer at viewport-y `pointerY`.
     Pass null to settle everything back to the resting size. */
  function _layout(pointerY) {
    if (!_buttons.length) return;

    if (pointerY == null) {
      _buttons.forEach(b => {
        b.style.setProperty('--lp-h', `${_base}px`);
        b.classList.remove('lp-dock-near');
      });
      _hideLabel();
      return;
    }

    const weights = _centres.map(c => {
      const d = (pointerY - c) / (_base * SPREAD);
      return 1 + (MAX_SCALE - 1) * Math.exp(-d * d);
    });

    // Constant sum: scale the weights so the heights still total count x base.
    const total = weights.reduce((a, b) => a + b, 0);
    const k = (_buttons.length * _base) / total;

    let peak = -1;
    let peakScale = 0;
    _buttons.forEach((b, i) => {
      const h = weights[i] * k;
      b.style.setProperty('--lp-h', `${h.toFixed(2)}px`);
      const scale = h / _base;
      b.classList.toggle('lp-dock-near', scale > 1.25);
      if (scale > peakScale) { peakScale = scale; peak = i; }
    });

    if (peak >= 0 && peakScale >= LABEL_AT) _showLabel(_buttons[peak]);
    else _hideLabel();
  }

  function _onEnter() {
    // Entering from outside means the dock is at rest, which is the only moment
    // the resting centres can be read correctly. Catches anything that changed
    // the panel while the pointer was away: a room switch hiding or showing the
    // swap button, the drive list opening, a window resize.
    _measure();
  }

  function _onMove(e) {
    if (!_measured) _measure();
    const y = e.clientY;
    if (_frame) return;                     // one layout per frame, not per event
    _frame = requestAnimationFrame(() => {
      _frame = 0;
      _layout(y);
    });
  }

  function _onLeave() {
    if (_frame) { cancelAnimationFrame(_frame); _frame = 0; }
    _layout(null);
  }

  function _invalidate() {
    _onLeave();
    _measured = false;      // re-measured on the next enter or move
  }

  function init() {
    _panel = document.getElementById('left-panel');
    if (!_panel) return;
    if (!_visibleButtons().length) return;

    _measure();
    _layout(null);
    // Transitions go on only after the resting layout is set, so the panel
    // doesn't visibly ease out of its fallback height on first paint.
    requestAnimationFrame(() => _panel.classList.add('lp-dock-eased'));

    _panel.addEventListener('pointerenter', _onEnter);
    _panel.addEventListener('pointermove', _onMove);
    _panel.addEventListener('pointerleave', _onLeave);

    // A button reached by keyboard magnifies too — otherwise tabbing through
    // the panel gives no indication of where you are.
    _panel.addEventListener('focusin', (e) => {
      const btn = e.target.closest?.('.lp-btn');
      if (!btn) return;
      if (!_measured) _measure();
      const r = btn.getBoundingClientRect();
      _layout(r.top + r.height / 2);
    });
    _panel.addEventListener('focusout', _onLeave);

    // Anything that moves the rows invalidates the cached resting centres.
    window.addEventListener('resize', _invalidate);
    _panel.addEventListener('scroll', _invalidate);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
