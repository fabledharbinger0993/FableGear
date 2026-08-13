# FableGear — Marketability Review, Pricing, and Go-to-Market

Status: strategy draft, August 2026. Revised against the live landing page
(`guthrieent.com/fablegear`), the beta-gate research funnel, and the beta-expiration
module — not just the in-repo README.

**Read section 1 first.** An earlier draft of this document was written from `README.md` alone
and got two important things wrong in the conservative direction.

---

## 1. What the README understates

The repo's `README.md` is now behind the product. Three claims on the live page change the
strategy, not just the copy:

**1. USB export writes beat grids and waveforms, and it booted a CDJ-3000 with no rekordbox
involved.** The README says the opposite — that export omits analysis data and players may show
blank waveforms. If the live page is current, this is not a caveat to hide; it is the most
valuable thing this project has, and the first draft of this review buried it as a weakness.
**Reconcile the two documents before any campaign starts** — right now the repo actively
undersells the product to anyone who reads it, which is every technical visitor and every
journalist who checks.

**2. There is a real benchmark, with a published method.** 12,687 rekordbox beat grids as an
answer key, 300-track random sample: **91.4% exact agreement, 94.8% within 1%, 98.3% within
4%** — up from 13.4% exact on the previous method. Plus a documented self-caught bug (the
loudness tool clipping and destroying 39 of 39 test files, now fixed to refuse rather than
damage). The first draft said no benchmarks existed and not to invent any. They exist, they're
methodologically honest, and they are the strongest marketing asset in the project after the
CDJ boot.

**3. The actual thesis is rekordbox independence, not library cleanup.** From the page: *"you
should not need rekordbox to get music onto a player… the choice of what manages your
collection stops being made for you by whoever built the gear."* That is a categorically bigger
story than "a toolkit that cleans your library," and it changes who covers it and what it's
worth.

Also under-weighted: **playlist recovery from a USB stick** — rebuild a lost laptop library off
an exported drive. That is the purest panic-purchase feature in the product.

---

## 2. What we are actually selling

Not a utility. Two things, in this order:

**Near term — recovery of work that looks lost.** The buyer's state at purchase is panic: three
hundred missing tracks, a dead laptop, a drive that remounted under a new name, a duplicate
cleanup that emptied playlists. Panic purchases clear far higher prices than convenience
purchases.

**Long term — the exit from Pioneer's library monopoly.** Nobody else is credibly attempting
"get music onto a CDJ without rekordbox." One verified boot on one player on one day is not a
product claim, but it *is* a story, and it's the one that makes press care.

---

## 3. Uniqueness, re-ranked

### 3.1 Writing a CDJ-readable USB without rekordbox — the category-defining claim

Deep Symmetry and rekordcrate reverse-engineered the format; plenty of projects *read* it.
Writing a stick that a CDJ-3000 actually loads and plays, from a third-party app, is rare
enough that it reframes the product from "maintenance tool" to "the beginning of an
alternative." Handle it exactly as the page already does — one player, one day, take a
rekordbox stick to the gig too. The honesty is what makes the claim believable rather than
hype, and it's why the page works.

This is the headline for **press and technical audiences**, not for panicking DJs. Different
audience, different lead — see 7.1.

### 3.2 The 91.4% benchmark — proof, in a market that runs on assertion

Every competitor asserts good analysis. None publishes a method against an answer key with the
adverse cases included. "We tested against 12,687 rekordbox grids and here's where we fail"
buys more trust from this audience than any amount of polish, and it converts especially well
with the meticulous archivist buyer.

The bug disclosure (39 of 39 files damaged, found and fixed) belongs in the marketing, not
hidden from it. Publishing a failure you caught yourself is the strongest possible signal for a
product whose whole pitch is "safe to point at your library."

### 3.3 The two-kinds-of-duplicate thesis

Still the best *teaching* claim, and the best short line of copy: **"Deleting duplicates
shouldn't empty your playlists."** It makes a reader realize they have a problem that has a
name. The page's framing — duration in the match so a 3:30 radio edit never merges with a 7:00
extended mix, fingerprints so a raw rip matches its Mixed In Key pass — is more concrete and
more convincing than the README's version. Use the page's.

### 3.4 Playlist recovery from an exported USB

Highest emotional value per word in the whole feature list. "Your laptop died. Your USB
didn't." Lead with this to the panic segment.

### 3.5 Local-first, counter-positioned against every paid competitor

Lexicon and Rekord Cloud are account-and-cloud products. Mixo is subscription-only. FableGear
is the opposite on every axis a suspicious DJ cares about, and the incumbents structurally
cannot follow: Lexicon can't abandon its subscription, Rekord Cloud can't stop being a browser
service. **"You can read the code that writes to your database"** is available to nobody else
in this category.

### 3.6 Honesty as the actual brand

The "What we are not going to pretend about" section is the most differentiated thing on the
site. In a category full of overclaiming, a page that volunteers *loudness normalization often
declines to do anything, and that means the feature does less than its name suggests* is doing
something competitors cannot copy without repositioning their whole company. This is not a
weakness section. **It is the brand.** Protect it, and never let a launch deadline sand it down.

### 3.7 The MCP server, with enforced tool ordering

The ordering constraint — an agent can't deduplicate a library it hasn't audited — is the
detail that makes this more than a checkbox. Few DJs will use it in year one; that isn't the
point. It's the only part of the product a technical audience will cover unprompted.

### 3.8 Breadth, and the four-subscriptions argument

The page's framing is right: *we got tired of paying four separate companies to do four things
to the same folder of music.* Mixed In Key + Platinum Notes + Lexicon + a dedupe tool is real
money annually. One free local app doing the maintenance parts is a clean value claim — and it
becomes the price argument in section 6.

---

## 4. Blockers, in priority order

### 4.1 `SUBMIT_ENDPOINT` is empty — the funnel currently collects nothing

Per the handoff notes, submissions fall back to `localStorage` and display the JSON. Every
survey completed right now is **lost**. This is the single highest-priority item; driving any
traffic before it's wired is pure waste. Needs the Cloudflare Worker → Apps Script → Sheet path
built, or an off-the-shelf form endpoint as a stopgap.

### 4.2 The 8-minute survey gate is costing more than it collects

The download link doesn't exist anywhere in the static HTML — it's injected only after
submission. That is a deliberate research decision with a legitimate goal, and the research
question is real: three people's blind spots genuinely aren't the community's. But it has costs
worth naming:

- **Nobody can link to the download.** Not a Reddit comment, not a press article, not a
  Homebrew cask, not a friend in a group chat. This forecloses most of section 7's channels.
- **8 minutes of forms before a free beta** converts in the low single digits from cold
  traffic. Warm traffic — someone who read the whole page — does much better, maybe a third.
  But cold traffic is what a launch produces.
- **At one GitHub star, the binding constraint is users, not research data.** Installs produce
  issues, testimonials, and word of mouth. Survey rows produce a spreadsheet.

**Recommendation: keep the survey, ungate the binary.** Make Download the primary CTA and the
survey a strong, prominent secondary with a real incentive attached — founding-tester credit
and a permanent free license (which section 6 needs anyway). You will get fewer responses per
visitor and far more installs, and the responses you do get will come from people who actually
ran the thing, which makes them worth more. If the gate stays, at minimum publish one stable
direct download URL that press and package managers can point at.

### 4.3 Copyleft dependencies vs. selling a binary

`essentia` is **AGPL-3.0**. `mutagen` is **GPL-2.0-or-later**. `ffmpeg` is LGPL/GPL. Today
this is fine: the page says these install from PyPI on the user's machine at first run rather
than shipping inside the download, and FableGear's own source is public.

Two things must be verified before money changes hands:

1. **What the PyInstaller specs actually bundle.** `FableGear.spec` and `FableGear_win.spec`
   exist. If a distributed `.app` bundles essentia or mutagen, selling that bundle pulls the
   combined work into AGPL-3.0 and GPL-2.0 territory, with source obligations attached. Check
   the built bundle's contents, not the intent.
2. **FableGo's network exposure.** AGPL §13 is triggered by users interacting with the program
   over a network. FableGear serves a UI to a phone over LAN or Tailscale. Public MIT source
   likely satisfies it today; a closed paid build would not.

Note also that essentia *is* the 91.4% number. The headline benchmark depends on the AGPL
component, so it can't be quietly dropped to simplify licensing.

**This is a "have someone competent confirm it" item, not a blog-post-level question.** Get it
right before the first sale, not after.

### 4.4 `yt-dlp` blocks the best free press channel

The page describes a download tool powered by yt-dlp — *"what you do with it is your
responsibility."* Defensible for a free OSS project. For a paid product it's a problem:

- Digital DJ Tips, DJ TechTools, and DJ Mag take money from Beatport, labels, and distributors.
  They will not feature a product that bundles a YouTube ripper. That forecloses Tier 4 press
  (section 7.2) — the channel where buyers in this category actually shop.
- Storefronts (Gumroad, Lemon Squeezy) have policies on circumvention tooling.
- It attracts rightsholder attention that a two-person LLC does not want.

**Recommendation:** keep it out of the marketed feature set entirely, and out of the paid build.
An optional component nobody advertises costs nothing; a headline feature costs you the press.
Right now it's listed on the dependency page, which is the honest place for it — just never let
it onto a feature list or a store listing.

### 4.5 No code signing or notarization

There is no `codesign` or `notarytool` step in `build_release.sh` or the release workflow.
Acceptable for a free beta. Fatal for a paid product: telling a buyer macOS will call the app
unverified, then opening a Terminal to install Homebrew dependencies, for software that writes
to their irreplaceable library, is a refund generator. **Hard prerequisite for charging.**

### 4.6 Still true from the first draft

macOS only, with no Windows build and correctly no promised date. First-run onboarding is
developer-grade. No Serato/Traktor support, so there's no free-conversion funnel like the one
Lexicon and Music Library Doctor use for top-of-funnel. One GitHub star, no reviews, no demo
video. And **"FableGear" is not a term anyone searches** — all discovery has to be symptom-led.

---

## 5. The promise problem

The page says, in its own words:

> *"That is the reason this is free and the source is public: a library tool you depend on
> should not be something that can be taken away or priced up later."*

That sentence is the best paragraph on the site and it is also a direct constraint on
monetization. Charging for FableGear a few months after publishing it — without care — reads as
precisely the thing it promised not to do, to exactly the audience most likely to notice and
least likely to forgive it. Losing that credibility costs more than the first year of revenue
is worth.

It is not fatal. Three conditions keep the sentence true:

1. **The free path never closes.** MIT source stays public; a working free build stays
   available. Nothing is taken away, so the promise holds.
2. **Everyone from the beta is grandfathered permanently free**, by name, without asking. They
   filled out an 8-minute survey to help build this. They are the people the sentence was
   addressed to.
3. **Price the convenience, never the capability.** What's sold is the signed, notarized,
   self-updating build and the update channel — not a feature the free build lacks.

Say all three *in the same breath as the price*, on the page, permanently. Announced that way
it's a supporter model. Announced any other way it's a bait-and-switch.

Two housekeeping consequences: `PRODUCT.md` principle 5 currently forbids copy implying "gated
features, upsells, or logins" and needs rewording to match this; and given MIT plus a
self-updating Python app, **DRM is not worth building.** Anyone can delete `beta.py`. The paid
build should be the path of least resistance, not a locked door.

### 5.1 The beta-expiry gate is a better free/paid line than anything I proposed

`beta.py` degrades an expired beta to dry-run — every tool still reports what it *would* do,
nothing writes. That is an unusually good freemium boundary, and it's already built:

> **FableGear always tells you what's wrong with your library, free. Fixing it is the paid
> build.**

It maps to delivered value, it matches the safety story (preview first, always), and the free
tier is genuinely useful rather than crippled — a full diagnostic report on your library for
nothing.

The honest caveat: as *enforcement* it's a local clock check in MIT-licensed source, so it's
honor-system. That's fine. Treat it as the shape of the offer, not as a lock, and don't spend
engineering on hardening it.

---

## 6. Pricing

### 6.1 The market

| Product | Model | Price | Scope |
|---|---|---|---|
| Lexicon | Subscription or lifetime | $9.99/mo or **$199 lifetime**; Ultimate $19.99/mo or $399 | Cloud/account, multi-app sync |
| Mixo | Subscription only | **$7/mo** | Cloud library, no buy-once option |
| Rekordbox Collection Tool | One-time | **€29.50** | macOS, fingerprint dedupe + relocate — a *subset* of FableGear |
| Mixed In Key | One-time | ~$58–98 | Key/energy only |
| Platinum Notes | One-time | ~$99 | Loudness/correction only |
| Music Library Doctor | Free tier + one-time Pro | one-time | Multi-app audit, dedupe, playlist transfer |

The four-subscriptions argument cuts directly: a DJ replacing Mixed In Key plus Platinum Notes
plus a dedupe tool is comparing against roughly **$200 of one-time purchases**, not against
$2.99.

### 6.2 On $2.99–9.99

$2.99 is the wrong number, and with the benchmark in hand it's clearly wrong:

- **Price is a quality signal.** The buyer has $2,000+ in CDJs and an irreplaceable library. A
  $2.99 app that rewrites that library reads as a weekend project. Cheap actively repels this
  buyer.
- **Fees eat the bottom.** ~13–20% of a $2.99 sale. You need roughly 4x the volume for the same
  money at 4x the support load, on identical software.
- **It contradicts the product's own evidence.** A page that publishes a 91.4% grid benchmark
  and a CDJ-3000 boot, priced at $2.99, invites the reader to assume the numbers must be
  softer than they look.

### 6.3 Recommendation

**Now, through beta: free. No price at all.**

Charging during beta — unsigned build, local-clock expiry gate, one player tested — is the
worst available combination. The beta's job is proof: installs, issues, testimonials, and more
hardware verified.

**At v1.0 (signed + notarized, 3+ players confirmed): $19.99 one-time.**

```
Free forever        MIT source. Full diagnostic build: audit, report, preview
                    everything. No capability is hidden — the free build tells
                    you exactly what's wrong with your library.

FableGear Signed    $19.99 one-time at v1.0
                    $9.99 founding-supporter price, offered now to beta testers
                    Free forever for everyone who filled out the survey
                    -> notarized, Gatekeeper-clean install
                    -> automatic update channel
                    -> all future versions. No subscription. Ever.
```

Why this shape:

- **$9.99 stays real**, as the founding price for the people who earned it — so the range you
  had in mind is honored rather than overridden.
- **$19.99 lands under RCT's €29.50** while doing considerably more, which is a clean argument
  in every comparison table, and roughly a tenth of Lexicon's lifetime tier.
- **One-time, never subscription.** That's the entire wedge against Lexicon, Mixo, and Rekord
  Cloud. Give it up and 3.5 collapses.
- **Free tier is genuinely useful**, which keeps section 5's promise intact.

**If rekordbox-free export holds up across XDJ, OPUS-QUAD, and 2000NXS2, this is a different
conversation — $49+ and a different company.** Don't price for that outcome before the hardware
testing exists, but don't sign anything that forecloses it either.

### 6.4 Revenue expectations

Starting from one GitHub star: a good year-one outcome from a $0 campaign executed well is on
the order of **100–500 paid units**, so **$2,000–10,000 gross** at $19.99, less 5–10% fees.
That funds the Apple Developer Program, a domain, and hardware for the player testing that
unlocks the next price tier. It is not income yet.

Year one's real deliverable is **proof**: users, quotable testimonials, roundup listings, more
players verified, and a demo video that converts. Revenue follows those and cannot precede them.

---

## 7. The $0 campaign

Total cash cost **$0**, with one optional $99 exception (Apple Developer Program) funded in 7.4.

### 7.1 Message hierarchy — two audiences, two leads

The single biggest copy mistake available here is leading with the same line for both.

| Audience | Lead with | Why |
|---|---|---|
| **Panicking DJ** (most of the volume) | "Your laptop died. Your USB didn't." / "Rekordbox lost 300 tracks. Get them back." | They don't care about independence. They care about tonight. |
| **Meticulous archivist** | "91.4% exact agreement with rekordbox's own beat grids. Here's the method." | Proof beats polish with this segment. |
| **Anti-subscription DJ** | "Four companies were charging you for four things in one folder." | The Mixed In Key + Platinum Notes + Lexicon stack is the real competitor. |
| **Press / technical** | "We wrote a USB with no rekordbox involved and a CDJ-3000 played it." | The only story here that a journalist will chase. |

The hero line on the page — *"Your library is worth more than the software managing it"* — is
good and should stay. It carries the thesis without overclaiming.

**Never claim:** Windows support, that export is proven beyond one CDJ-3000, or anything about
Serato/Traktor. And keep the download tool off every feature list (4.4).

### 7.2 Channels

**Tier 1 — Reactive help where the pain is posted.** r/Beatmatch, r/DJs (~310k), r/DJHelp,
r/rekordbox, r/Pioneer_DJ; the Pioneer DJ forums (15,000+ rekordbox posts); rekordbox Facebook
groups, which are unfashionable and convert well because the members are older and buy tools;
the Pioneer DJ and Digital DJ Tips Discords.

Method: standing searches for the symptoms — *missing tracks, exclamation mark, relocate, moved
to a new drive, duplicates deleted my playlists, laptop died, rebuild library from USB.* Answer
completely, with no link. Mention the tool only when it is genuinely the answer, disclose
authorship every time, roughly one mention per ten useful comments. Never DM, never cross-post
identical text, read each community's promo rules. Note this channel **needs 4.2 fixed** — a
helpful comment you can't attach a download link to converts badly.

**Tier 2 — Evergreen symptom content.** Because the brand name is unsearchable, write the
fix-it guide for each symptom and let the tool be the answer at the end:

1. "Rekordbox shows an exclamation mark on hundreds of tracks — how to relocate them all"
2. "My laptop died — rebuilding a rekordbox library from an exported USB"
3. "Deleting duplicates in rekordbox emptied my playlists — what actually happened"
4. "How accurate is rekordbox's beat grid? We tested 12,687 of them." ← this one is linkbait
5. "How to write BPM and key into the file so it survives a library rebuild"
6. "Never keep your rekordbox database in iCloud or Dropbox"

Host on GitHub Pages or alongside the existing Cloudflare Pages site — both free.

**Tier 3 — Free listings.** Repo topics (`rekordbox`, `dj`, `pyrekordbox`, `macos`,
`mcp-server`, `local-first`) are currently unset, which is free discovery left on the table.
Plus GitHub Discussions (off), AlternativeTo as a Lexicon/Mixo/RCT alternative — high-intent
traffic from people shopping the category right now — MCP directories, Homebrew cask, Product
Hunt, r/macapps, r/opensource. Several of these **require a linkable download** (4.2).

**Tier 4 — Earned media.** Digital DJ Tips covered RCT, so there's direct precedent for exactly
this product. Also DJ TechTools, We Are Crossfader, The DJ Mixtape, Cadence, ZIPDJ. Every one
publishes "best DJ library tool" roundups, which is where buyers actually shop. Pitch: the CDJ
boot, the benchmark method, the honest caveats section, a 90-second video, and a free key. The
honesty is what earns a reply — every other pitch in their inbox is pure claim. **Gated on
4.4** — resolve yt-dlp first.

**Tier 5 — The technical launch.** Show HN, Lobsters, r/opensource, MCP directories. Pitch the
engineering, not the app: writing a CDJ-readable Pioneer USB from scratch, benchmarking against
12,687 rekordbox grids, safe writes to an encrypted SQLite library, MCP with enforced tool
ordering. That audience rewards a hard problem told honestly, and a surprising number of them DJ.

**Tier 6 — Philadelphia.** Guthrie Entertainment is a Philly company with an Osos Discos
connection. Local record shops, promoters, and DJ nights are free, high-trust distribution that
no competitor can replicate remotely, and local DJs will let you sit next to them while they
run it on a real library — which is worth more than a hundred survey rows.

### 7.3 The survey is a marketing asset, not just research

The Likert data on rekordbox, Serato, Mixed In Key, Lexicon, and Traktor satisfaction is
competitive research **nobody else has and everybody wants.** Aggregated and anonymized, "we
asked N DJs what they hate about managing their libraries" is the most linkable artifact this
project could produce, and it costs nothing but the analysis.

It's also a far better press pitch than "please cover my app" — it gives DJ TechTools and
Digital DJ Tips a *story* rather than a favor. Publish it, credit the respondents as a cohort,
and it retroactively justifies the 8 minutes you asked of them.

That reframes the trade in 4.2: you don't need the gate to get responses. You need the
responses to be worth something, and publishing them is what does that.

### 7.4 Sequence

| Phase | Do | Gate to next |
|---|---|---|
| **0 — Unblock** | Wire `SUBMIT_ENDPOINT` (4.1). Ungate the download (4.2). Reconcile README with the live page (§1). Resolve yt-dlp positioning (4.4). Record the 90-second video. Repo topics + Discussions on. | Funnel captures data; download is linkable |
| **1 — Free technical launch** | Show HN, r/macapps, r/opensource, MCP dirs, Homebrew cask, AlternativeTo, Product Hunt. **Ask for nothing but feedback.** | 100+ stars; issues from strangers |
| **2 — Harvest proof** | Answer every issue fast. Collect quotable testimonials by name, with permission. Test more players. Publish the survey aggregate (7.3). | 3–5 testimonials; 3+ players verified |
| **3 — DJ communities** | Tier 1 reactive help. Publish the six Tier 2 guides. Pitch Tier 4 press with the video and the survey story. | Roundup listings; steady non-technical users |
| **4 — v1.0 paid** | Notarized build. $19.99, $9.99 founding, beta cohort free forever — all three stated together (§5). | First 50 sales, no refund spiral |

Phase 2 is the real gate. If nobody will say something good on the record, don't start phase 4
— find out why instead.

**Funding the one non-zero cost:** the Apple Developer Program is $99/yr and it's the only item
worth money, because without it there's no notarized build and therefore no honest paid product.
Put a Ko-fi or GitHub Sponsors link on the page during phases 1–3 and let the first $99 of
donations buy the certificate. Zero upfront, and the people who funded it become the first
advocates.

### 7.5 Payment rails, $0 upfront

**Lemon Squeezy** or **Polar** (~4–5%, merchant of record, handles EU VAT — recommended, since
VAT exposure is a real risk for a solo LLC selling internationally). **Gumroad** (~10%, fastest
setup) is fine to start. Stripe direct is cheapest but makes sales-tax compliance your problem;
not worth it at this volume. Keys should be a simple offline signed token — per §5, the goal is
making payment the easy path, not making bypass impossible.

### 7.6 Metrics, all free

GitHub release download counts, stars over time, repo Insights → referring sites, storefront
dashboard, per-post upvote-to-click ratio. The number that matters most in the free phase is
**downloads-to-issues ratio** — people who file issues actually ran it, and they're the only
real evidence the product is being used rather than starred.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| **Charging after "should not be priced up later"** | §5's three conditions, stated together with the price, permanently. This is the reputational risk, and it's larger than the revenue at stake. |
| **AGPL-3.0 / GPL-2.0 deps in a sold binary** | Audit what the PyInstaller specs actually bundle; confirm FableGo's AGPL §13 position. Before the first sale (4.3). |
| **yt-dlp closing the press channel** | Off every feature list and out of the paid build (4.4). |
| **Trademark / ToS with AlphaTheta** | Never "rekordbox" in the product name, logo, domain, or store title. Always "for rekordbox," always with a no-affiliation disclaimer. Sharpens considerably once the export work aims at replacing their pipeline. |
| **A rekordbox update breaks DB writes** | Disclose the pyrekordbox dependency before purchase, not after. Free build always available as a fallback. Generous refunds — one refunded sale is cheaper than one angry roundup mention. |
| **The USB export claim generalizing badly** | Keep the one-player caveat verbatim in every channel until more hardware is tested. A DJ whose stick fails on an OPUS-QUAD at a gig will say so publicly, and they'll be right. |
| **Someone forks and sells the MIT source** | Low stakes at this price. The moat is the notarized build, the update channel, the benchmark, and being the author people trust. |
| **A paid user's library gets damaged** | The existing safety architecture is the mitigation and it's already strong. Do not let paid-launch pressure ship a write path that skips backup, preview, or the rekordbox-closed check. One horror story in r/DJs outlives every campaign in this document. |
