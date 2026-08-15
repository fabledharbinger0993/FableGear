# Handoff — cloud session, 2026-08-14

Written by a Claude Code cloud session for whoever (human or Claude) picks this
up next, most likely on the Mac Studio. Everything below is already pushed —
`git pull` on `main` gets you all of it. Nothing here is speculative.

## Merged into `FableGear` `main` (done, no action needed)

- **PR #154 — `MARKETING.md`** (docs only). Positioning/pricing review. Flags
  three things nobody has acted on yet — see "Open decisions" below.
- **PR #155 — Record Room dashboard CSS + album art.** Fixed a corrupted CSS
  patch that had a raw unified diff pasted into `static/fablegear.css` as dead
  text, restored the Record Room/Chop Shop "One Room, One Light" color
  boundary, fixed low-contrast library columns, added a dashboard stat strip,
  and added mutagen-based album art extraction
  (`/api/library/tracks/<track_id>/art`).

Both were drafts sitting green; both merged clean, squashed.

## Shipped and live — `guthrieent` repo, PR #6 (merged by Marshall, both envs verified)

`guthrieent` is the separate Cloudflare Pages marketing site
(`fabledharbinger0993/guthrieent`), not this repo. It hosts `fablegear.html` —
the public write-up + adaptive research survey + beta download page. **This
was previously discussed and the decision was to keep it a separate repo from
FableGear itself** (Cloudflare/static-site vs. Python app) — don't merge them.

PR (merged): https://github.com/fabledharbinger0993/guthrieent/pull/6
Live: https://guthrieent.com/fablegear and https://guthrieent.com/dashboard

Both Production and Preview D1 (`DB`) and `DASHBOARD_KEY` bindings are set and
confirmed working — a real test submission was posted to production, landed
in D1 correctly, then deleted. The dashboard's wrong-key path returns 401
(not 503), confirming the key is live. Nothing left to configure here.

What's in it:
- An adaptive survey (not a flat form) — checking a tool in the inventory
  step reveals a satisfaction block for *that* tool only; hardware and
  "have you tried FableGear" questions branch the same way. Captures DJ
  software users, Apple Music/iTunes-only users, and "just files in
  folders" users as distinct respondent paths, each with statements that
  actually fit them (folders-only gets its own four statements rather than
  the DJ-software ones nonsensically substituted in).
- A mascot (the `icon-logo-fablegear.png` character) that floats
  continuously and narrates each survey step in a speech bubble as you
  scroll to it. The bubble flips above the character when there's no room
  below (near the bottom of a long page) instead of clipping off-screen.
- A `suggestions` free-text field, for a longer review after someone's
  actually used FableGear — separate from the pre-use pain-point/blind-spot
  questions.
- **Storage is Cloudflare D1**, not Google Sheets/Apps Script — that plan
  changed mid-session per direct instruction (a "local server" idea got
  scoped down to D1 once the always-on/reachable-by-strangers problem with
  self-hosting was raised). Database `guthrieent-fablegear-survey` already
  exists with its schema applied (`responses` / `response_tools` /
  `response_likert` — see the comment header in `functions/api/survey.js`
  for why three tables). `apps-script/Code.gs` is deleted; nothing reads it.
- **`dashboard.html`** — a key-gated, aggregate-only admin view (stat tiles,
  CSS bar charts, a truncated/expandable open-text feed), reading from
  `functions/api/dashboard-data.js`. Never returns emails or raw payloads.
  Linked quietly from the `fablegear.html` footer at low opacity, not in the
  public nav. The "neuron-inspired 3D graph" idea floated for this was
  explicitly deferred to a later pass — this is the simple version by
  request, not a placeholder for one nobody asked to skip.

**Two gotchas hit while wiring this, worth knowing if it ever needs touching
again:**
1. Cloudflare Pages bindings/env vars are **deployment-scoped** — saving a
   new binding or secret in Settings does not rebuild an already-live
   deployment on its own. A genuinely new deployment (new commit, or a
   manual retry from the Deployments tab) is required before it takes
   effect. This cost real back-and-forth before it was diagnosed.
2. Production and Preview have **separate** binding/variable sets in the
   Cloudflare dashboard (a dropdown at the top of Settings). Both need `DB`
   and `DASHBOARD_KEY` set independently — confirmed both are, now.

**Concurrent-edit note, since it already happened once:** while the D1/
dashboard work was in progress, another session pushed a commit directly to
this same branch (`a43e933` — fixed the speech-bubble clipping and gave
`lib-none` its own statement set) without any coordination. It integrated
cleanly via `git fetch` + `git rebase` before pushing, no conflicts, but if
multiple sessions are ever active on the same repo again: **fetch and
rebase before you push**, don't assume you have the latest tip.

## Open decisions nobody has made yet (flagged in `MARKETING.md`, not acted on)

These are product calls, not bugs — surfacing them here so they don't get
lost:

1. **`PRODUCT.md` principle 5** ("no copy implying gated features") directly
   contradicts the pricing section MARKETING.md recommended and that's now
   live in `MARKETING.md` (one-time $9.99–16 purchase at 1.0, beta free
   forever). MARKETING.md flagged replacement wording but deliberately did
   **not** edit `PRODUCT.md` itself — that's a call for Marshall, not
   something either of us should silently resolve by editing the principle
   or the pricing copy to make them agree.
2. **Licensing audit before selling any binary.** `essentia` is AGPL-3.0,
   `mutagen` is GPL-2.0-or-later. Fine today (deps install from PyPI at
   first run, source is public), but what the PyInstaller build actually
   bundles needs auditing before a paid release ships. `essentia` can't be
   dropped — it's the reason tempo detection went from 13.4% to 91.4% exact.
3. **No `codesign`/`notarytool` step in the release pipeline yet.** Needed
   before the 1.0 paid build; not needed for the free beta.
4. **`yt-dlp` on the public feature list** blocks DJ press/label coverage
   per the marketing review — worth a call on whether to keep it listed
   prominently.

## Branches that are safe to ignore or delete

- `guthrieent:claude/new-session-cql8jd` — confirmed via `git merge-base
  --is-ancestor` to be a strict subset of `claude/fablegear-survey-download`
  (same commit, earlier point in its history). Contributes nothing unique.
  Safe to delete; not merged anywhere and doesn't need to be.

Everything else that used to show up as a stray `claude/*` branch across
either repo (there were dozens, going back months) was already merged into
`main` and cleaned up before this session started — `git branch -a` on both
repos is accurate as of this writing; nothing else is hiding.

## Repo access note

This cloud session has push access to both `fabledharbinger0993/FableGear`
and `fabledharbinger0993/guthrieent` for the remainder of its life. If a
fresh cloud session picks this up later, it starts with FableGear read-only
by default and needs `guthrieent` re-attached with push access before it can
push there — that's a one-time per-session tool call, not a credentials
problem.

Same story for D1: this session has Cloudflare tools connected and used them
to create `guthrieent-fablegear-survey` and apply its schema directly (no
`wrangler` CLI, no local Cloudflare login) — a fresh session needs the
equivalent connector, not file access, to run any further schema changes or
one-off queries against it.
