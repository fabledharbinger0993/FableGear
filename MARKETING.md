# FableGear — Marketability Review, Pricing, and Go-to-Market

Status: strategy draft, August 2026. Written against the product as it actually ships today
(v1.1.28, macOS-only, MIT, 1 GitHub star, no code signing, no reviews or press).

Nothing in this document invents social proof, benchmarks, or testimonials. Where proof is
missing it says so, because the missing proof is the main thing standing between this product
and revenue.

---

## 1. What we are actually selling

Not "a Rekordbox utility." The thing being sold is **confidence that a library operation
won't silently destroy work** — where "work" means years of playlists, cues, and crate logic
that a DJ cannot rebuild.

The buyer's emotional state at purchase is not "I'd like nicer tools." It is one of:

- Tracks show `!` and half the collection won't load, two days before a gig.
- Music moved to a new drive and Rekordbox lost the paths.
- They ran a duplicate cleanup and playlists came back empty.
- They inherited/merged a 40,000-track mess across four externals and can't face it manually.

That is a *panic purchase* category, and panic purchases convert at prices far above $10.
This matters for section 3.

---

## 2. Uniqueness: what genuinely sets FableGear apart

Ranked by how defensible each one is and how well it converts.

### 2.1 The two-kinds-of-duplicate thesis — the single strongest asset

Every competitor treats "duplicate" as one problem: two files that sound the same. FableGear
is the only tool that names the second problem out loud — **two `DjmdContent` rows for one
song, where the filesystem is fine and the database is wrong** — and ships a separate tool for
it with the guarantee that *no record is removed until every playlist referencing it has been
re-wired.*

Why this is the lead message:

- It's a **teaching claim**. It makes the reader realize they have a problem they didn't know
  had a name. That is the most reliable way to earn attention without an ad budget.
- It's **verifiable in 30 seconds** by anyone who reads `README.md`, and the code backs it up.
- It reframes competitors as unsafe without naming them. Rekordbox's own duplicate search and
  most third-party dedupers cannot distinguish the two cases, and picking wrong drops the
  track from every playlist with no warning.
- It generates the best single line of copy available: **"Deleting duplicates shouldn't empty
  your playlists."**

### 2.2 Local-first, counter-positioned against the entire paid market

The two best-funded competitors are cloud-and-subscription products (Lexicon: account +
$9.99/mo or $199 lifetime; Mixo: cloud, $7/mo, no buy-once option). FableGear is the opposite
on every axis a suspicious DJ cares about: no account, no cloud, no telemetry, nothing leaves
the Mac, and the source is readable.

Counter-positioning is the cheapest form of differentiation there is, because the incumbents
structurally cannot follow. Lexicon cannot abandon its subscription. Mixo cannot stop being
cloud. FableGear can hold "your library never leaves your machine" indefinitely.

The sharpest version of this: **"You can read the code that writes to your database."** No
competitor in this category can say that.

### 2.3 Safety-as-a-feature — the Health Monitor is under-marketed

Pre-flight checks for iCloud/Dropbox syncing the library folder, symlinked database, read-only
mounts, backups on the same drive as the database, suspicious DB size changes, low disk, and
Rekordbox still running — with enforcement, not advice.

Nobody markets this. It should be a headline, not a footnote, because it does something rare:
it finds a problem the user didn't ask about and didn't know they had. "iCloud is syncing your
Rekordbox database, and that's how libraries get corrupted" is a moment of genuine value
delivered before the user has done any work. That moment is what a free tier exists to create.

### 2.4 Breadth at one price point

Audit, path repair, two kinds of dedupe, BPM/key tagging, EBU R128 normalization, format
conversion, metadata-driven reorganization, pattern/learned-rule renaming, cross-drive novelty
scanning, playlist management, USB export — plus the Pipeline Wizard to chain the file-layer
tools into one run.

Competitors fragment this. RCT (€29.50) is essentially fingerprint dedupe plus relocation.
Lexicon charges $199 lifetime for the management tier. FableGear's whole set at one price is a
legitimate value claim.

### 2.5 Tags written into the files, not just the database

BPM and key go into the audio file's own metadata, so the analysis survives a Rekordbox
rebuild or a move to different software. This is an **anti-lock-in** message and it lands hard
with anyone who has ever lost a library. "Your analysis belongs to your files, not to Pioneer."

### 2.6 The MCP server — a free press hook with zero competition

FableGear runs as an MCP server, so an AI assistant can audit, tag, dedupe, or reorganize a
library conversationally, with the same enforced safety contract (Rekordbox closed, backup
first). As far as this review can determine, **no other DJ library tool does this.**

Very few DJs will use it in year one. That is not the point. The point is that it is the only
part of this product that a tech audience — Hacker News, MCP directories, dev newsletters,
AI-tooling roundups — will cover for free. It buys reach that the DJ-tool framing cannot.

### 2.7 FableGo — phone control without a cloud

Browse, manage playlists, trigger analysis jobs and USB exports from a phone over LAN or
Tailscale, with no service in the middle. Unusual, demo-friendly, and it reinforces 2.2 rather
than diluting it.

### 2.8 Free and open source, MIT

A trust asset first and a distribution asset second. It is the reason strangers will tolerate
you posting about it in their communities, and the reason a nervous DJ will let it near their
database.

---

## 3. Honest weaknesses — these set the price ceiling

State these plainly, because pricing decisions made without them are fiction.

| # | Weakness | Marketing consequence |
|---|---|---|
| 1 | **The app is not signed or notarized.** No `codesign`/`notarytool` step exists in `build_release.sh` or the release workflow. | The hard blocker on charging money. Asking someone to pay, then telling them macOS will call the app unverified, then having it open a Terminal window to install dependencies — for software whose job is writing to their irreplaceable library — is a refund generator and a trust catastrophe. **Fix before any paid launch.** |
| 2 | **First-run onboarding is developer-grade.** Terminal window, Homebrew, `ffmpeg` + `chromaprint`, internet required. | Fine for free/open-source. Unacceptable for a paid consumer product. Every friction step is a refund. |
| 3 | **macOS only.** `FableGear_win.spec` exists but release CI is `macos-latest` only. | Roughly halves the addressable market. Don't imply Windows support anywhere. When Windows ships, that's a second launch — treat it as a free relaunch event. |
| 4 | **USB export omits waveform, beat-grid, and hot-cue analysis**; players may show blank waveforms until Rekordbox re-analyzes. | Must never be a headline feature. If a buyer's mental model is "this replaces Rekordbox export," they will feel cheated on a dancefloor. Keep the README's honesty in all copy. |
| 5 | **No Serato / Traktor / VirtualDJ support.** | Forfeits the funnel every competitor uses: free library *conversion* as the top of funnel, paid management behind it. FableGear has no equivalent free hook, so section 5 builds one out of the Health Monitor and Audit instead. |
| 6 | **Zero social proof.** 1 star, 0 forks, no reviews, no press, no video. | The actual bottleneck. No amount of pricing cleverness beats this. Solve it before charging. |
| 7 | **"FableGear" is not a searchable name.** Nobody types it. It doesn't say what it does. | All discovery must be symptom-led, not brand-led. See section 5.2. Don't rename — build the symptom content instead. |
| 8 | **Dependency on `pyrekordbox` + the Rekordbox DB format.** An AlphaTheta update can break writes overnight. | Real support and refund exposure once money is involved. Disclose it; don't get caught by it. |
| 9 | **MIT license + a paywall are in tension** — and `PRODUCT.md` principle 5 explicitly forbids copy that implies gated features. | Needs an explicit decision. See 3.1. |

### 3.1 The license question, answered

Everything published to date is MIT and stays MIT forever; that can't be recalled. Anyone can
fork the last free commit and redistribute. So a license-key DRM scheme in an MIT, self-updating
Python app is bypassable in an afternoon, and building it is wasted engineering.

**Recommendation: keep MIT and sell the convenience, not the code.**

Sell the signed, notarized, self-updating `.app` — the build nobody wants to produce themselves
— while source stays free and auditable. Charge for the packaging, the update channel, and
priority attention. This is a well-worn model and it has three advantages: it's honest, it
keeps 2.2 and 2.8 intact, and it doesn't set fire to the goodwill of the people who give you
your first stars.

Do **not** relicense to source-available to protect revenue. The copyright is single-author and
LLC-held, so it's legally available, but the OSS audience that gives FableGear its only current
credibility will read it as a bait-and-switch, and at this price point there is nothing to
protect. Revisit only if there's real revenue to defend.

Then update `PRODUCT.md` principle 5, which currently reads "free/open-source with no account
system — copy should never imply gated features, upsells, or logins." It contradicts the plan.
Rewrite it as: *free and open source with no account system; a paid convenience build may exist,
but no capability is ever locked behind payment and nothing requires a login.* That keeps the
principle's spirit and stays true.

---

## 4. Pricing

### 4.1 What the market actually charges

| Product | Model | Price | Scope |
|---|---|---|---|
| Lexicon DJ | Subscription or lifetime | $9.99/mo or **$199 lifetime** (Essential); $19.99/mo or $399 (Ultimate) | Cloud/account, multi-app conversion + management |
| Mixo | Subscription only | **$7/mo** (Gold) | Cloud library, sync/bridging |
| Rekordbox Collection Tool (RCT) | One-time | **€29.50** | macOS, fingerprint dedupe + relocation — a *subset* of FableGear |
| Music Library Doctor | Free tier + one-time lifetime Pro | Free / one-time | Multi-app audit, fingerprint dedupe, playlist transfer |
| Rekordbox Library Fixer | Free, open source | $0 | Dupes, relocate, organize |

The category clears **$30–$200**. A macOS tool doing *less* than FableGear sustains €29.50 one-time.

### 4.2 On the $2.99–9.99 range

$2.99 is the wrong number and it's worth being direct about why:

- **Price is a quality signal here.** The buyer has $2,000+ in CDJs and an irreplaceable
  library. A $2.99 app that writes to that library reads as a weekend hobby project, not
  something to trust. Cheap actively repels this buyer.
- **The fee math is brutal at the bottom.** On a $2.99 sale, platform + payment fees take
  roughly 13–20%. Net is around $2.40–2.60. You need ~4x the unit volume of a $9.99 price to
  earn the same money — while carrying 4x the support load, on identical software.
- **It caps the story.** Getting into a "best DJ library tools" roundup at $2.99 invites the
  reader to assume it's a toy. At $19–29 the same listing reads as the value pick against
  Lexicon's $199.

$9.99 — the top of the range — is defensible and shippable. It clears the impulse threshold
where DJs buy sample packs without deliberating, it doesn't signal abandonware, and it's low
enough that "just try it" beats "research it."

### 4.3 Recommendation

**Primary: $9.99 one-time founder price, then $24.99 standing.**

```
Free forever        Source (MIT) + self-built or unsigned build.
                    Every capability. No feature gates, ever.

FableGear Signed    $9.99 one-time, "founding build," first 500 buyers
                    $24.99 one-time thereafter
                    -> notarized, Gatekeeper-clean install
                    -> automatic update channel
                    -> priority on issues
                    -> all future versions, no subscription
```

Why this shape:

- **$9.99 is inside the stated range**, so it's actionable now, and framing it as a founder
  price makes the later $24.99 a fulfilled promise instead of a price hike. Announce the
  standing price *at launch* — that's what makes $9.99 feel like a decision rather than a
  discount.
- **One-time, not subscription.** Anti-subscription sentiment is the strongest wedge against
  both Lexicon and Mixo. Give it up and the counter-positioning in 2.2 collapses.
- **Nothing is feature-gated.** This is what keeps the promise in 2.8 credible and keeps the
  OSS audience on side. You are selling the notarized build and the update channel, which are
  real costs you actually bear.
- **$24.99 lands under RCT's €29.50** while the product does more. That is a clean value
  argument in every roundup and comparison table.

**Do not turn on payments until items 1, 2, and 6 in section 3 are fixed.** In order:
notarized build, one-command install, and at least a handful of real users willing to be
quoted. Charging before that converts badly *and* burns the launch.

### 4.4 Realistic revenue expectations

Starting audience is one GitHub star. Set expectations honestly: a good year-one outcome from
a $0 campaign executed well is on the order of **100–500 paid units** — roughly **$1,000–5,000
gross**, less 5–10% in fees. That pays for the Apple Developer Program, a domain, and a
reinvestment budget. It is not income yet.

The realistic year-one goal is not revenue. It is **proof**: users, quotes, roundup listings,
and a video that converts. Revenue follows those, and it can't precede them.

---

## 5. The $0 marketing campaign

Total cash cost: **$0**, with one optional $99 exception (Apple Developer Program) that section
5.6 shows how to fund out of the free phase.

### 5.1 Positioning and message hierarchy

**Positioning statement**
> FableGear is the local-first library room for Rekordbox DJs on Mac. It audits, repairs, and
> cleans a collection with pre-flight safety checks and playlist-safe deduplication — and
> nothing ever leaves your machine.

**Primary hook (lead with this everywhere)**
> Deleting duplicates shouldn't empty your playlists.

**Supporting lines, by audience**

| Audience | Line |
|---|---|
| Panicking DJ | "Rekordbox lost your files? Fix every broken path in one pass." |
| Suspicious DJ | "No account. No cloud. No telemetry. You can read the code that writes to your database." |
| Anti-subscription DJ | "Buy it once. Lexicon is $199 or $10 a month." |
| Meticulous DJ | "It checks whether iCloud is quietly corrupting your database before you touch anything." |
| Tech / press | "The first DJ library tool an AI assistant can safely drive." |

**Never claim:** that it replaces Rekordbox export (weakness #4), that it supports Windows
(#3), or anything about other DJ software (#5).

### 5.2 Channel plan

Because the brand name is unsearchable (#7), **every channel is symptom-led**. The DJ is not
looking for FableGear. They're looking for why their tracks have exclamation marks.

**Tier 1 — Reactive help, where the pain is posted (highest conversion, $0)**

Watch and answer, don't broadcast.

- **r/Beatmatch** — highest volume of exactly these help posts
- **r/DJs** (~310k) — broader, stricter on promo
- **r/DJHelp**, **r/rekordbox**, **r/Pioneer_DJ**, **r/DJProducers**
- **Pioneer DJ official forums** (`forums.pioneerdj.com`) — the rekordbox section has 15,000+
  posts and is exactly the frustrated-power-user pool
- **Facebook groups** — "Pioneer's Rekordbox DJ Forum," Rekordbox user groups, city/regional DJ
  groups. Unfashionable, free, and they convert well for DJ software because the members are
  older and buy tools.
- **Discords** — Pioneer DJ community, Digital DJ Tips, r/DJs, producer servers

Method: standing searches for the symptoms — *missing tracks, exclamation mark, relocate,
"moved to a new drive," duplicates deleted my playlist, blank waveform, library corrupted.*
Answer the question completely and usefully **with no link**. Mention the tool only when it is
the actual answer, disclose authorship every single time, and keep it to roughly one mention
per ten genuinely helpful comments. Never DM. Never paste identical text across subs. Read each
community's self-promo rules first. The free-and-open-source framing is what makes this
tolerated — it's true, so use it.

**Tier 2 — Evergreen searchable content (compounding, $0)**

Write the fix-it guide for each symptom and let the tool be the answer at the bottom. These get
found by search *and* get cited by AI assistants, which is now a real referral channel.

1. "Rekordbox tracks show an exclamation mark — how to relocate hundreds at once"
2. "I moved my music to a new drive and Rekordbox lost everything"
3. "Deleting duplicates in Rekordbox emptied my playlists — what actually happened"
4. "Physical vs. database duplicates: why your DJ library has two different problems"
5. "How to write BPM and key into the file itself, so it survives a library rebuild"
6. "Never keep your Rekordbox database in iCloud or Dropbox — here's why"

Host on **GitHub Pages** (free; the repo has `has_pages` off — turn it on). A `.com` is ~$10/yr
and optional, not required to start.

**Tier 3 — Free listings and directories (one afternoon, permanent returns)**

- **GitHub repo topics**: `rekordbox`, `dj`, `dj-tools`, `pyrekordbox`, `music-library`,
  `macos`, `mcp-server`, `local-first` — currently unset, and this is free discovery
- **GitHub Discussions** — currently off; turn it on so users have somewhere to land
- **AlternativeTo** — list as an alternative to Lexicon, Mixo, and RCT. High-intent traffic:
  these are people shopping the category right now.
- **Product Hunt** — free, one shot, use it after the demo video exists
- **MCP directories** — `awesome-mcp-servers`, PulseMCP, Smithery, mcpservers.org. Free, and
  they reach the one audience that will cover this without being asked.
- **Homebrew cask** — free submission, real credibility, and `brew install --cask fablegear`
  removes most of weakness #2 for technical users
- **r/macapps**, **r/opensource**, **r/selfhosted** (the FableGo + Tailscale angle)

**Tier 4 — Earned media (free, needs a pitch)**

Every one of these publishes "best DJ library tool" roundups, which is where buyers in this
category actually shop.

- **Digital DJ Tips** — already covered RCT, i.e. direct precedent for exactly this product
- **DJ TechTools** — covered Mixo's launch; has a rekordbox forum section
- **We Are Crossfader**, **The DJ Mixtape**, **Cadence DJ**, **ZIPDJ blog**, **DJ Mag Tech**
- Mid-size rekordbox tutorial YouTubers — offer free lifetime keys, never payment

Pitch format: two sentences on the playlist-safety thesis, the 90-second video, a free key, and
one honest line about what it does *not* do. The honesty is what gets a reply — every other
pitch in their inbox is pure claim.

**Tier 5 — The technical launch (one-time reach spike)**

**Show HN**, Lobsters, and AI/dev newsletters. Critically: *do not* pitch this as "my DJ app."
Pitch the engineering — reading and safely writing an encrypted Rekordbox SQLite database via
pyrekordbox, the two-kinds-of-duplicate problem and the playlist re-wiring invariant, and
exposing it all over MCP. That audience rewards a hard technical problem told honestly, and
several of them are DJs.

### 5.3 The one asset worth building first

**A 60–90 second screen recording: broken library → audit → fixed.**

Nothing else moves conversion as much, no channel above works well without it, and it costs
$0 (QuickTime + iMovie; `static/fablegear-splash.mp4` already exists for the top).

Then cut it into 15–30s vertical clips for Shorts / Reels / TikTok, one "oh damn" moment each:

- The fingerprint scan matching a 320kbps MP3 to a WAV of the same track
- The Health Monitor catching Dropbox mid-sync on the library folder
- 400 broken paths repaired in one pass
- Consolidate Duplicates showing playlists being re-wired *before* anything is removed

### 5.4 12-week sequence

| Phase | Weeks | Do | Success = |
|---|---|---|---|
| **0 — Fix the blockers** | 1–2 | Record the demo video. Enable GitHub Pages + Discussions. Set repo topics. List on AlternativeTo + MCP directories. Add a Ko-fi / GitHub Sponsors link. | Product is presentable; a stranger can find it |
| **1 — Free technical launch** | 3–5 | Show HN, r/macapps, r/opensource, MCP dirs, Homebrew cask PR. **Ask for nothing but feedback.** | 100+ stars, first real users, first issues from strangers |
| **2 — Harvest proof** | 4–6 | Answer every issue fast. Ask happy users for one quotable sentence, by name, with permission. | 3–5 real testimonials. This is the phase that unlocks paid. |
| **3 — DJ communities** | 6–10 | Tier 1 reactive help. Publish the six Tier 2 guides. Pitch Tier 4 press with the video. | Roundup listings; steady non-technical users |
| **4 — Turn on paid** | 11–12 | Notarized build ships. $9.99 founder price live, $24.99 announced as the standing price. Free build stays. | First 50 sales; no refund spiral |

Phase 2 is the gate. If nobody will say something nice on the record, do not start phase 4 —
go back and find out why.

### 5.5 Payment rails, all $0 upfront

| Option | Cost | Note |
|---|---|---|
| **Lemon Squeezy** or **Polar** | ~4–5% + fee, $0/mo | Merchant of record — handles EU VAT for you. **Recommended**; VAT exposure is a real risk for a solo LLC selling internationally. |
| **Gumroad** | ~10%, $0/mo | Fastest possible setup, highest fee. Fine to start. |
| **Ko-fi / GitHub Sponsors** | ~0–5% | Use during the free phase for donations (see 5.6) |
| Stripe direct | 2.9% + 30¢ | Cheapest, but sales-tax/VAT compliance becomes your problem. Not worth it at this volume. |

On license keys: keep it a simple offline signed token. Given 3.1, the goal is to make paying
the obvious default path, not to make piracy impossible. Don't spend a week on DRM for a $10
product.

### 5.6 Funding the one non-zero cost

The Apple Developer Program is $99/yr and it's the only thing on this list worth money —
without it there is no notarized build, and without a notarized build there is no honest paid
product. Fund it from the free phase: put a Ko-fi / GitHub Sponsors button in the README during
phases 1–3 and let the first $99 of donations buy the certificate. Zero upfront, and the people
who paid for it become the first advocates.

### 5.7 Metrics, all free

GitHub release download counts (via API), stars over time, repo Insights → Referring sites,
Gumroad/Lemon Squeezy dashboard, and per-post upvote-to-click ratio to learn which framing
lands. One number to watch above all: **downloads-to-issues ratio.** People who file issues are
people who actually ran it, and in the free phase they are the only real signal that this
product is being used rather than starred.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **Trademark / ToS with AlphaTheta (Pioneer DJ)**, especially once money changes hands | Never put "Rekordbox" in the product name, logo, domain, or store title. Always "for Rekordbox" / "works with Rekordbox," always with an explicit no-affiliation disclaimer. Review before the paid launch. |
| **A Rekordbox update breaks database writes** | Disclose the dependency in the store listing before purchase, not after. Free build always available as a fallback. Keep the refund policy generous — one refunded sale is cheaper than one angry roundup mention. |
| **Reddit/forum backlash over self-promotion** | Help-first ratio, disclose authorship every time, never cross-post identical text, read each community's rules. |
| **Someone forks and sells the MIT source** | Genuinely low-stakes at this price. The moat is the notarized build, the update channel, and being the author people trust — not the code. |
| **A paid user's library gets damaged** | The existing safety architecture is the actual mitigation, and it's already strong. Do not let paid-launch pressure ship a write path that skips backup, dry-run, or the Rekordbox-closed check. One horror story in r/DJs outlives any campaign in this document. |
