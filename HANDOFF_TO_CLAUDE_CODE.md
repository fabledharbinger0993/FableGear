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

## In flight — `guthrieent` repo, PR #6 (NOT merged, still draft)

`guthrieent` is the separate Cloudflare Pages marketing site
(`fabledharbinger0993/guthrieent`), not this repo. It hosts `fablegear.html` —
the public write-up + adaptive research survey + beta download page. **This
was previously discussed and the decision was to keep it a separate repo from
FableGear itself** (Cloudflare/static-site vs. Python app) — don't merge them.

PR: https://github.com/fabledharbinger0993/guthrieent/pull/6
Live branch preview: https://claude-fablegear-survey-down.guthrieent-git.pages.dev/fablegear.html

What's in it:
- An adaptive survey (not a flat form) — checking a tool in the inventory
  step reveals a satisfaction block for *that* tool only; hardware and
  "have you tried FableGear" questions branch the same way. Captures DJ
  software users, Apple Music/iTunes-only users, and "just files in
  folders" users as distinct respondent paths.
- A mascot (the `icon-logo-fablegear.png` character) that floats
  continuously and narrates each survey step in a speech bubble as you
  scroll to it.
- Backend wiring: `functions/api/survey.js` (Cloudflare Pages Function,
  same-origin relay) → `apps-script/Code.gs` (Google Apps Script collector,
  dynamic-schema so per-tool Likert keys don't get dropped) →
  `functions/fablegear/release.js` (decorates the download button with live
  release version/size instead of 404ing).

**Why it's still draft, and what's actually blocking merge:** the Apps
Script backend needs to be deployed and wired by hand — this is a
human-console step, not something a coding session can do from here:
1. Create the spreadsheet, paste in `apps-script/Code.gs`, deploy as a Web
   app ("Execute as: Me", "Who has access: Anyone").
2. Set `SURVEY_SHEET_URL` to the `/exec` URL in Cloudflare Pages →
   Settings → Environment variables.
3. Sanity check: visiting the `/exec` URL directly should return
   `{"status":"ok"}`.

Until that's done, survey submissions 503 gracefully and get stashed in
`localStorage` client-side — nothing is lost, it's just not landing
anywhere durable yet.

I'm subscribed to PR #6 and will keep handling CI/review activity on it from
the cloud side. If you merge it locally instead, no need to coordinate —
just let the PR close naturally and I'll stop polling it.

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
