---
name: FableGear
description: A dark, dual-room control shell for Rekordbox library surgery
colors:
  bg: "#07090e"
  surface: "#0d1420"
  surface-hi: "#162030"
  border: "#243040"
  border-hi: "#3a5060"
  text: "#dce8ef"
  text-muted: "#8aabba"
  text-dim: "#4f6878"
  safe: "#1A8200"
  caution: "#cf9d46"
  warn: "#f0b44b"
  danger: "#EF4444"
  signal-cyan: "#00d4e8"
  signal-cyan-deep: "#00b4c8"
  chop-magenta: "#ff2d78"
typography:
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: ".8rem"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: ".78rem"
    fontWeight: 700
    letterSpacing: ".04em"
  mono:
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', ui-monospace, monospace"
    fontSize: ".85rem"
    fontWeight: 400
rounded:
  sm: "5px"
  md: "8px"
  full: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.signal-cyan}"
    textColor: "{colors.bg}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  button-secondary:
    backgroundColor: "{colors.surface-hi}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "16px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "7px 10px"
---

# Design System: FableGear

## Overview

**Creative North Star: "The Two-Room Workshop"**

FableGear is one shell that speaks two dialects. Record Room is database precision — Rekordbox truth, read carefully, written cautiously, lit in Signal Cyan. Chop Shop is physical file work — hands on the actual audio, lit in Chop Magenta. Same dark control room, same monospace readouts, same VS Code-style fixed chrome, but the accent that lights up tells you which kind of danger you're in: a bad database write, or a bad file operation.

The mood is moody and atmospheric, not clinical. This is a late-night studio tool built by a DJ, not a SaaS dashboard — it should feel like a piece of rack gear you trust because it's exacting, not because it's friendly. Deliberately rougher edges over polish where the two conflict. It should never read as a cloud-consumer app (the anti-reference is exactly that genre: light, chrome-forward, gradient-happy library apps like Lexicon). No gradients, no light mode, no rounded-pill everything, no bouncy motion.

**Key Characteristics:**
- Near-black surfaces (`--bg` #07090e) with cyan or magenta light doing the work shadows would do elsewhere
- JetBrains Mono for anything numeric or data-dense (BPM, keys, paths, logs); system sans for prose and labels
- Fixed VS Code-style shell (titlebar, sidebar, header) — the app is a workstation, not a scrolling page
- One accent lit at a time per room; the other room's chrome goes quiet
- Status is color-coded consistently: safe (green), caution/warn (amber), danger (red) — never repurposed for anything else

## Colors

Near-black neutrals carry the whole app at rest; the two room accents are the only saturated color allowed to dominate a screen, and only one is lit at a time.

### Primary
- **Signal Cyan** (#00d4e8): Record Room's room-accent. Active nav state, focus rings, primary buttons, database-safe glow. Deepens to `#00b4c8` (Signal Cyan Deep) for pressed/secondary cyan states.

### Secondary
- **Chop Magenta** (#ff2d78): Chop Shop's room-accent, swapped in via `--room-accent` when `body.fg-space-chop` is active. Same functional roles as Signal Cyan but scoped to the file-layer room — chrome should never show both accents lit at once.

### Tertiary — Status
- **Safe Green** (#1A8200): confirmed-safe states, dry-run-clear, health-check pass.
- **Caution Amber** (#cf9d46) / **Warn Amber** (#f0b44b): two adjacent severities — caution is advisory, warn is "look before you proceed." Keep them visually close but not identical; don't collapse into one amber.
- **Danger Red** (#EF4444): destructive actions, health-check failures, blocking errors only. Never decorative.

### Neutral
- **Void** (#07090e): app background (`--bg`).
- **Deep Surface** (#0d1420): card/panel background (`--surface`).
- **Raised Surface** (#162030): hovered/elevated surface (`--surface-hi`).
- **Border** (#243040) / **Border Bright** (#3a5060): default and emphasized dividers.
- **Signal Text** (#dce8ef): primary text. **Muted Text** (#8aabba): secondary/label text. **Dim Text** (#4f6878): tertiary/disabled text.

### Named Rules
**The One Room, One Light Rule.** Only one room-accent is visually active at a time. Chop Magenta never appears in Record Room chrome and vice versa; a screen that shows both accents lit has lost the room boundary.

**The Status Is Sacred Rule.** Safe/caution/warn/danger colors are reserved for actual system state. Never reuse `--danger` for a purely decorative red, or `--safe` for "this is the recommended option" outside a real pass/fail check.

## Typography

**Body Font:** system-ui, -apple-system, 'Segoe UI', sans-serif
**Mono Font:** 'JetBrains Mono', 'Fira Code', 'Cascadia Code', ui-monospace, monospace

**Character:** Sans for prose and labels keeps the UI legible at high density; mono for anything numeric, technical, or logged (BPM, musical key, file paths, terminal-style progress) is what gives FableGear its "instrument panel" read rather than "web form" read.

### Hierarchy
- **Title** (700, .95rem, tight tracking `-0.01em`): card/panel titles.
- **Body** (400, .8rem, 1.45 line-height): default prose, descriptions, table cells.
- **Label** (700, .78rem, `.04em` tracking, uppercase): field labels, section headers, badges — the app's dominant text treatment by volume.
- **Data/Mono** (400, .85rem): BPM, key, duration, file paths, log output — always `--mono`, never sans.
- **Micro** (500, .62–.72rem): nav item labels, tertiary captions, `.btn-xs` controls.

### Named Rules
**The Uppercase Label Rule.** Structural labels (field labels, section headers, nav captions) are uppercase with `.04–.08em` tracking at 700 weight. Body prose and data values are never uppercased — reserve it for structure, not content.

## Layout

Fixed VS Code-style shell, not a scrolling document: `--titlebar-h` (28px) + `--header-h` (56px) at top, `--sidebar-w` (260px) left rail, `--left-panel-w` (80px) icon rail, content fills the remainder. Chop Shop adds a `--rail-h` (84px) workflow rail below the header when active.

Spacing clusters around a loose 4px-stepped rhythm (4, 8, 10, 12, 14, 16, 24) rather than a strict power-of-two scale — treat `sm` (8px), `md` (12px), `lg` (16px), `xl` (24px) as the target scale going forward, with `xs` (4px) for tight inline gaps only. Existing 5px/6px/7px/14px odd values are legacy drift, not intentional design — new work should round to the nearest scale step rather than adding another one-off.

## Elevation & Depth

FableGear is flat by construction — no lifted-card/Material-style shadow steps. Depth reads as **ambient glow**: light comes from the room accent, not an implied light source. A focused input, an active nav item, or a hovered card gets a cyan or magenta glow (`box-shadow: 0 0 Npx rgba(accent, α)`, often paired with an inset 1px accent-tinted border) rather than a bigger drop shadow. Genuine drop shadows (`0 8px 32px rgba(0,0,0,.3)` and deeper `0 24px 80px rgba(0,0,0,.8)` for modals) exist only to separate an overlay from the page behind it, not to imply a stack of everyday surfaces.

### Shadow Vocabulary
- **Ambient focus glow** (`0 0 6-10px rgba(accent-rgb, .2-.75)`, sometimes with `inset 0 0 0 1px rgba(accent-rgb, .12-.35)`): the primary depth signal — active/focused/hovered elements in the room's accent color.
- **Card base** (`0 8px 32px rgba(0,0,0,.3)` or `0 2px 6px rgba(0,0,0,.3)`): minimal separation for resting cards/panels — subtle, not a stack cue.
- **Modal/overlay** (`0 12px 48px rgba(0,0,0,.8)` up to `0 24px 80px rgba(0,0,0,.8)`): reserved for things that visually float above the whole app (confirm dialogs, the library editor overlay).

### Named Rules
**The Glow-Not-Lift Rule.** Depth on interactive elements is communicated with accent-colored glow, never a bigger shadow or a `translateY` lift stack. Reserve heavy neutral shadows (`rgba(0,0,0,...)`) for true overlays separating from the whole app, not for everyday hover states.

## Shapes

Corners are currently inconsistent — audit found `border-radius` set to ~20 distinct hardcoded values (2px through 20px, plus `50%`/`999px`) against a nearly-ignored `--radius: 8px` token. Going forward, FableGear uses a **three-step radius scale** and nothing else:

- **sm (5px):** tight controls — inputs, small buttons (`.btn-xs`/`.btn-sm`), chips.
- **md (8px):** the default — cards, panels, primary buttons, modals. This is the existing `--radius` token; it should actually be used everywhere in this tier instead of the current mix of 6/7/8/10px.
- **full (999px / 50%):** true circles (avatars, status dots) and pill-shaped badges only.

### Named Rules
**The Three-Radius Rule.** Every corner in the app is `sm`, `md`, or `full` — no other radius value is introduced. An element that "needs" 6px or 10px belongs to `sm` or `md`; round to the nearest step rather than adding a fourth tier.

## Components

Buttons, cards, and inputs should feel **tactile and mechanical** — like flipping a switch on real rack gear, not tapping a soft web-app control. Feedback is immediate and slightly blunt: opacity/color shift on hover, a real 1px `translateY` press on active, never an eased bounce.

### Buttons
- **Shape:** `sm` radius (5px) — buttons are controls, not cards.
- **Primary:** Signal Cyan background (or Chop Magenta in Chop Shop), `--bg` text, 700 weight, `8px 16px` padding, `.83rem`.
- **Ghost / ​Secondary:** transparent or `--surface-hi` background with a `--border-hi` outline; text is `--text-muted`, brightens to `--text` on hover.
- **Hover / Active:** hover drops opacity to `.88`; active adds a real `translateY(1px)` press plus reduced shadow — this is the tactile cue, keep it on every button variant.
- **Sizes:** `.btn` (default, `.83rem`), `.btn-sm` (`.78rem`), `.btn-xs` (`.7rem`/`3px 9px`/5px radius) — **fix the current duplicate `.btn-xs` definition** (lines ~1353 and ~5904 disagree on font-size, .72rem vs .7rem, and only the second sets radius/line-height); consolidate to one definition before adding any more button work.

### Cards / Containers
- **Corner Style:** `md` (8px).
- **Background:** `--surface`, sometimes semi-transparent (`rgba(13,16,28,.65)`) over a blurred backdrop for floating panels.
- **Shadow Strategy:** ambient glow on hover/focus per Elevation section; flat `0 8px 32px rgba(0,0,0,.3)` at rest.
- **Border:** 1px, low-alpha white or `--border`, brightening toward the room accent on hover.
- **Internal Padding:** `lg` (16px) standard.

### Inputs / Fields
- **Style:** `--mono` font, `--surface` background, 1px `--border-hi` border, `sm` radius (5px), `7px 10px` padding.
- **Focus:** border shifts to the room accent color; no glow ring beyond that border shift (keep inputs quieter than buttons).
- **Labels:** uppercase, `.78rem`/700/`.04em` tracking, `--text-muted`, sit 5-8px above the field.

### Navigation
- Icon rail + sidebar; items are transparent at rest, tint to `accent-dim` on hover, and gain a **2px left border in the room accent plus a 15% accent-tinted background** when active. Icon + micro-label (`.62rem`) stacked, not side-by-side. This left-border-accent pattern is FableGear's signature active-state — reuse it anywhere else an "active/selected" state is needed instead of inventing a new one.

## Do's and Don'ts

### Do:
- **Do** use `--mono` for every numeric/technical value (BPM, key, duration, path, log line) and `--sans` for everything else.
- **Do** keep exactly one room-accent lit per screen — cyan in Record Room, magenta in Chop Shop, never both.
- **Do** use ambient accent-glow for interactive depth; reserve heavy black drop shadows for true overlays.
- **Do** round every new `border-radius` to `sm` (5px), `md` (8px), or `full` — no in-between values.
- **Do** reuse the left-border-accent + tinted-background pattern for any new "active/selected" state.

### Don't:
- **Don't** introduce a fourth border-radius value, or hand-pick something between 5px and 8px "because it looked right."
- **Don't** repurpose `--safe`/`--caution`/`--warn`/`--danger` for anything that isn't real system state.
- **Don't** add drop-shadow "lift" stacks (Material-style elevation steps) — depth here is glow, not layering.
- **Don't** borrow gradient, light-mode, or bouncy-motion patterns from consumer cloud apps (e.g. Lexicon) — FableGear's identity is deliberately rougher and darker than that genre.
- **Don't** redefine an existing class (like the current duplicate `.btn-xs`) further down the stylesheet instead of extending or overriding it deliberately — audit for repeated top-level selectors before adding new component variants.
