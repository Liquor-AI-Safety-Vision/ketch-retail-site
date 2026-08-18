# Ketch retail site — ketchenterprise.net

The US retail / liquor-store pitch site for Ketch Enterprise: a landing page and a
security-console demo, built from pre-rendered video and event data. Static site, no backend,
no API keys.

| | |
|---|---|
| Live at | **https://ketchenterprise.net** (+ www) |
| Cloudflare Pages project | **`ketch-enterprise`** |
| Stack | Vite 8 · React 19 · TypeScript · Tailwind v4 · HashRouter |

> ⚠ **`ketchenterprise.net` (no "s") is this site.** `ketchenterprises.net` (with an "s") is a
> different site — the Vietnamese factory pitch — and lives in a different repo. They look
> similar and it is easy to deploy one over the other. Nothing in this repo can touch the
> factory site; keep it that way.

## Run it

```bash
npm install
npm run dev        # http://localhost:5173
```

Routes: `/` is the landing page, `/#/dashboard` is the console. HashRouter is deliberate — it
means static hosting needs no rewrite rules.

```bash
npm run build      # tsc -b && vite build → dist/
npm run preview    # serve dist/ locally
npm run lint
```

## Deploying

**Push to `main`.** GitHub Actions builds and publishes to Cloudflare Pages
(`.github/workflows/deploy.yml`). Nothing else is needed and no one has to run a command.

To try a change without touching the live domain: **Actions → Deploy → Run workflow →
environment: preview**. That publishes to a throwaway `*.pages.dev` URL and leaves
ketchenterprise.net alone.

If a deploy goes wrong, roll back in the Cloudflare dashboard (Workers & Pages →
`ketch-enterprise` → Deployments → the previous one → Rollback). Do not try to fix it by
changing DNS.

There is also `npm run deploy` for a manual publish from your machine. It needs
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in your environment, and it goes straight to
production — prefer the workflow.

### What your access covers, and what it doesn't

The API token is scoped to **Cloudflare Pages only**. It cannot change DNS, the domain
registration, or email. Those stay with Huy — `ketchenterprise.net`'s DNS zone also carries the
mail records for every `@ketchenterprise.net` address, so a wrong edit there takes down company
email, not a web page. If you need a DNS or domain change, ask; don't look for a way around it.

## The one rule this site has to follow

**Every box is a real detection. Never box the person and call it the act.**

The console reports geometry — a person dwelled in this zone, an object left that zone — and
every event carries a `how_it_knows` sentence shown verbatim in the UI. It never claims intent:
not "concealment", not "stealing", not a confidence number that nothing produced.

This is not a style preference. The buyer is a store owner who has been sold AI-camera
vaporware before and will pressure-test every claim on screen. The demo's whole edge is that it
survives that. Four files carry comments citing `DESIGN-CONTRACT.md` by name for this reason —
read [`DESIGN-CONTRACT.md`](DESIGN-CONTRACT.md) before changing any copy in the console, and
keep the source citations in `src/lib/roi.ts` attached to their numbers. If you change a
benchmark, change its citation.

## Layout

```
src/
  main.tsx                    HashRouter, 2 routes
  types.ts                    the data contract: DetectionEvent, ClipEvents, Manifest, TIER_META
  index.css                   Tailwind v4 design tokens
  lib/
    demo.ts                   useManifest() / useClipEvents() — fetch public/demo at runtime
    roi.ts                    cost/ROI calculators; every constant carries its source
    seo.ts                    title, description, OG tags, JSON-LD
    i18n.ts, consoleStrings.ts   shared with the factory site; this site is EN-only
    scrollToSection.ts
  components/ui/              Button, Card, SectionShell, TierBadge
  pages/landing/              the landing page and its 13 sections
  pages/dashboard/            DashboardPage + the console components
                              (VideoPanel, EventFeed, EventCard, IncidentGallery,
                               ZoneOverlay, PhonePushMockup)
public/
  demo/                       the pre-rendered gallery — see below
  favicon.svg
```

Some code here is shared verbatim with the factory site: everything in `lib/`,
`components/ui/`, and `pages/dashboard/components/`. Those files still contain
`mode === 'factory'` branches and Vietnamese strings that this site never reaches. They're
inert — leave them or clean them up, but know they aren't dead by accident.

## The demo data

`public/demo/manifest.json` lists the clips; each clip directory holds the video, a poster
frame, and `events.json`. The console fetches the manifest at runtime, so an empty or malformed
manifest gives you an empty console rather than a crash.

**Clip order in the manifest is the gallery order**, and it is a deliberate severity arc:
`normal-customer` → `lingering` → `restricted-area` → `seedance_liquor_01`. It opens on the
hero clip. Reordering changes the story the demo tells.

The clips are produced by a detection pipeline in a separate repo (`CV-Theft-Detection`, Huy's).
If new clips are needed, they come from there — ask Huy. Nothing in this repo generates them.

Colours and tier names in `TIER_META` (`src/types.ts`) and the zone colours in
`ZoneOverlay.tsx` are **byte-identical to constants in that pipeline** — `src/process_video.py`
and `src/rule_engine.py` burn the same colours into the video frames. If you change one side
without the other, the boxes drawn in the video stop matching the legend beside it. Tell Huy
before changing either.

⚠ **The clips are not licence-cleared.** `seedance_liquor_01` — the hero — was generated with a
model whose licence for this use is unresolved, and the other three came from third-party store
CCTV whose provenance was never documented. They ship on this site because they already do; do
not reuse them anywhere else, don't put them in a deck, and don't publish them under a different
domain. Resolving this is Huy's open item, not yours.

## Contact placeholders

`ContactSection.tsx` still carries `+84 ___ ___ ____` and `hello@example.com`. They are
placeholders, not live addresses. Real contact details are a Huy/Christ decision.
