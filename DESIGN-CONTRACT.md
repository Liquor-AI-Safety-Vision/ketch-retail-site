# DESIGN-CONTRACT — frontend agents (STREAM B + C)

The artifact: a polished, **honest** sales demo for Christ to show a US liquor/c-store
owner and land a paid one-store pilot. Two pages. Vite + React 19 + Tailwind v4.
Read this once fully before writing anything.

> **Still live, with one correction (2026-07-27).** The **Golden rule below is load-bearing** —
> five shipped components cite it by name (`EventCard`, `EventFeed`, `DashboardPage`,
> `PhonePushMockup`, `ZoneOverlay`) and it is the product's core constraint. Keep it.
>
> **Correction — the scope line "Robbery and 'concealment' are omitted entirely" is out of
> date.** That was v2 landing-copy discipline. Detection scope was re-locked with the client on
> 2026-06-22 as **robbery + employee theft** (`context/00-status.md`). What survives, and what
> the rule was actually protecting, is the *claim* discipline, not the vocabulary ban: never
> assert **intent** — no "concealment", no "caught stealing", no verdict the model didn't
> output. A scenario may be *named* robbery; a detection may never *conclude* it.
>
> This file also predates the factory site (it describes two pages; there are now four routes
> across two sites) and the 2026-06-29 ROI round. `context/frontend.md` is the current
> architecture map.

---

## Golden rule — HONESTY (scoping-spec §5). Violating this kills the demo.

The owner will pressure-test every claim. So:
- This is a **triage / attention** system, never a verdict. **Never** the word "theft" as a
  conclusion, never "caught stealing", never a fabricated accuracy %, $ figure, or confidence
  the model didn't output.
- Every detection event carries a `how_it_knows` sentence — **surface it** on the card. If a
  claim can't be said in one true sentence, don't show it.
- The headline red alert = a person **lingered at the spirits shelf then moved to the exit** —
  a geometric fact. Whether the item was paid for is **only** knowable from the POS → that's
  named openly as the pilot upgrade. Do NOT imply the camera proved payment/intent.
- Robbery and "concealment" are **omitted entirely**. Don't invent them.
- Stat counters: only defensible facts (frames processed, zones monitored, FPS, "0 cloud —
  runs on-prem") OR explicitly labelled "illustrative / sample". No made-up numbers.
- Footage is synthetic (Seedance) — that's disclosed honestly on the landing framing strip
  ("Demonstrated on synthetic CCTV; tuned to your cameras in the pilot"). It is NOT hidden.

## How It Grows / add-ons (landing) — Phase 2 overlays are labelled `Illustrative · post-pilot`
Three modular CapEx add-ons the owner buys incrementally:
1. **SKU / object detection** — knows *what* (bottle, generic `$$$ high-value` tag, no $ figure).
2. **Behavior classification** — knows the *action* (`carried past register · not returned to shelf`).
3. **POS fusion** — the ONLY module that turns triage → verdict (`Register: 0 scanned · 1 leaving · ✖ unpaid`).
Honesty hinge: the *verdict* always comes from POS (was it scanned?), never the camera.
Engine narrative = **data flywheel**: store's 30-day archive + human confirm/dismiss (the
"oracle") → labels → trained add-ons; surfacing least-certain alerts = "uncertainty sampling".

---

## Design system (scoping-spec §8). Trust-first B2B. Dials: variance 5 / motion 4 / density 5.

Tokens are already defined in `src/index.css` (Tailwind v4 `@theme`). USE THE UTILITY CLASSES,
do not hardcode hex:
| Token | class examples | use |
|---|---|---|
| deep teal `#0E5C5B` | `bg-teal-900 text-teal-900` | headers, CTA fills, dark bands |
| brand teal `#128C8A` | `text-teal-600 bg-teal-600` | accents, links, eyebrows |
| teal-300 `#6FB3B1` | `text-teal-300` | tints on dark |
| cream `#F6F1E7` | `bg-cream text-cream` | page base |
| cream-200 `#ECE4D4` | `bg-cream-200` | card surfaces |
| ink `#15211F` | `text-ink` | body text |
| ink-600 `#3A4744` | `text-ink-600` | secondary text |
| amber `#E2A03F` | `bg-amber text-amber` | **Tier 2 early-warning ONLY** |
| amber-bg `#F7ECD6` | `bg-amber-bg` | amber soft chip |
| red `#C24A3A` | `bg-red text-red` | **Tier 1 priority alert ONLY** |
| red-bg `#F3DAD4` | `bg-red-bg` | red soft chip |

- **amber/red are functional only** — alert states (badges, cards, video boxes). Never decoration.
- **No AI-purple, no gradient slop.** One accent family (teal). Motion restrained (fades, the
  live-feed dot, a counter tick) — not cinematic.
- Radius: cards/inputs `rounded-xl`, badges `rounded-md`, buttons `rounded-lg` (already in primitives).
- Headlines `tracking-tight`, body `leading-relaxed max-w-[65ch]`. Font = system stack (set in tokens).
- Mobile: hero uses `min-h-[100dvh]` not `h-screen`. Layout via CSS grid, contain to `max-w-[1200px]`/`1400px`.

### Shared primitives — IMPORT, never edit (`src/components/ui`)
```ts
import { Button, Card, TierBadge, SectionShell } from '../../components/ui'
```
- `Button` — `variant: primary|secondary|ghost`, `size: sm|md|lg`. Keep labels ≤3 words, one line.
- `Card` — `surface: plain|cream`.
- `TierBadge` — `<TierBadge tier={1|2|3} variant="soft|solid" />`. The only tier→color/name source.
  Names are honest: 1 = **Priority Alert**, 2 = **Early Warning**, 3 = **Zone Breach**.
- `SectionShell` — `eyebrow / title / intro / tone: cream|cream-200|teal`. Use for landing sections.
- Icons: use `@phosphor-icons/react` if you need icons — RUN `npm i @phosphor-icons/react` first,
  one family only, no hand-rolled SVG (zone polygons are the only allowed inline SVG).

---

## Data contract (real, already in `public/demo/`)

Load via hooks in `src/lib/demo.ts` — do not refork:
```ts
import { useManifest, useClipEvents, videoUrl, thumbUrl } from '../../lib/demo'
```
- `manifest.json` → one clip: `seedance_liquor_01`, title **"CAM 01 · Spirits Aisle"**.
- `events.json` shape = `ClipEvents` in `src/types.ts`: `frame_width:1280, frame_height:720`,
  `zones` (polygons in source-pixel space), and `events[]` (`DetectionEvent` incl. `how_it_knows`).
- **The hero clip has exactly 2 real events** (10.0 s, 24fps):
  | t | tier | label | how_it_knows |
  |---|---|---|---|
  | 00:00:03 | 2 (amber) | Loitering — high-value area | dwell timer on zone presence |
  | 00:00:06 | 1 (red) | Lingered at premium-spirits shelf, now at exit | POS-attribution sentence |
  - These tell the arc: **browse → 🟡 dwell at spirits → 🔴 walk to exit → 📱 phone push**.
  - The amber + red are on the same single shopper (track IDs differ across a brief cooler
    occlusion — if you show the ID, frame the re-acquisition as a pilot tightening, don't hide it).
- **Zone overlay (dashboard):** draw an SVG with `viewBox="0 0 1280 720"` absolutely over the
  video; render each `zones` polygon. Color by name prefix: `hv_*` → amber, `restricted_*` → red,
  else (`checkout_lane`, `exit_door`) → teal-600. Subtle fill (~12% alpha) + 2px stroke + label.

---

## File ownership (disjoint — this is how we avoid collisions)
- **STREAM B (landing):** owns `src/pages/landing/**` (add `src/pages/landing/components/*` freely).
- **STREAM C (dashboard):** owns `src/pages/dashboard/**` (add `src/pages/dashboard/components/*` freely).
- **DO NOT EDIT (shared, coordinator-owned):** `src/index.css`, `src/types.ts`, `src/main.tsx`,
  `src/components/ui/**`, `src/lib/**`, `public/**`, anything outside your page dir.
- You MAY reuse logic from the legacy `src/components/*.tsx` (VideoPlayer, EventTimeline) by
  COPYING into your own page dir and restyling — do not import the legacy navy-themed ones.
- Routing already exists (`/` → landing, `/dashboard` → dashboard). Link with react-router
  `<Link to="/dashboard">` / `<Link to="/">`.

## Done = `npm run build` stays green (`tsc -b` is strict: noUnusedLocals). Verify before finishing.

---

## STREAM B — landing sections (in order)
sticky nav (brand left; EN/VI toggle placeholder + `Book a pilot` button; right) → **asymmetric
hero** (not centered: headline+subcopy+CTA left, a framed still or the live-feed teaser right;
H1 "Stop loss before it leaves the store"; primary CTA → `/dashboard` "See it live") → **honest
stat strip** (frames processed, zones monitored, N FPS, "0 cloud · on-prem") → **How It Works
01–04** (numbered: cameras → person + zone tracking → dwell/exit triage → human confirms (oracle))
→ **Coverage** (US liquor/c-store primary, VN factory secondary) on a teal band → **HonestFramingStrip**
(triage-not-verdict; "the verdict is the POS, not the pixels"; runs on-prem / privacy) → **How It
Grows** (2-panel SAME-clip before/after: Phase 1 base pilot REAL vs Phase 2 `Illustrative · post-pilot`;
+ the 3 add-on cards; + the flywheel diagram) → **pilot strip** (one store, 2-month pilot) → **Results**
(labelled illustrative) → **contact** → cream-on-teal **CTA** → 3-col **footer**. Keep copy tight,
owner-language, no AI hype.

## STREAM C — dashboard console
Header (brand + `← Back` to `/`). Two-column grid (~65/35):
- **Left video panel:** the rendered hero video (`videoUrl(meta)`, autoplay/loop/muted), a
  persistent **SVG zone overlay** (viewBox 1280×720, see above), a top-left **recording dot +
  "LIVE · CAM 01 · Spirits Aisle"**, and a scrubber with event markers (amber/red) that seeks the video.
- **Right event-feed rail:** cards bound to `events.json` — timestamp, `TierBadge`, label, the
  `how_it_knows` caption, track id (small), thumbnail (`thumbUrl`). Clicking a card seeks the video.
  Make the amber→red escalation legible on the one continuous feed.
- Beside/under the feed: a **phone-push mockup** (a phone frame showing an SMS/WhatsApp-style push:
  "⚠ Priority alert · CAM 01 · subject at exit after dwell — review"). Channels: SMS/WhatsApp (US).
- **Incident gallery** shell below: a responsive grid of clip cards (right now just the hero;
  more clips slot in later via manifest — build it to render whatever `manifest.clips` contains).
Polished, calm, dense-but-readable. The video + real events are the star.
