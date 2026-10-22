# Ketch retail site — ketchenterprise.net

The US retail / liquor-store pitch site for Ketch Enterprise, paired with the Liquor Store
owner security dashboard. The landing page and public, read-only dashboard demo work without
authentication or backend access. Only explicitly signed-in owners load data from the Go API.

| | |
|---|---|
| Live at | **https://ketchenterprise.net** (+ www) |
| Cloudflare Pages project | **`ketch-enterprise`** |
| Stack | Vite 8 · React 19 · TypeScript · Tailwind v4 · Material UI · TanStack Query · HashRouter |

> ⚠ **`ketchenterprise.net` (no "s") is this site.** `ketchenterprises.net` (with an "s") is a
> different site — the Vietnamese factory pitch — and lives in a different repo. They look
> similar and it is easy to deploy one over the other. Nothing in this repo can touch the
> factory site; keep it that way.

## Run it

For the Go backend, migrations, owner bootstrap and AI ingestion, use
[thinhEden/LS-backend](https://github.com/thinhEden/LS-backend). Its
[setup instructions](https://github.com/thinhEden/LS-backend#installation) and
[environment reference](https://github.com/thinhEden/LS-backend/blob/main/docs/environment.md)
describe the backend variables. This repo contains the landing page and integrated
dashboard; the other repo also contains a standalone dashboard. Start only one
frontend on port 5173.

```bash
npm ci
cp .env.example .env    # PowerShell: Copy-Item .env.example .env
npm run dev        # http://localhost:5173; proxies /api to http://localhost:3000
```

Keep an existing `.env` instead of overwriting it. The only browser environment
variable is `VITE_API_BASE_URL`, normally `/api/v1`. Vite forwards it to the local
Go API; no Cloudflare, database, JWT, Telegram, WhatsApp or AI token is needed to
browse the public demo. These secrets belong on the backend, never in `VITE_*`.
To view real data, start the Go API with its development database, then sign in
with the local owner account at `/#/login`. The public demo does not display new
events from the database. Use the same `localhost:5173` origin as backend
`WEB_ORIGIN`, or update both when using `127.0.0.1` instead.

Routes are:

- `/#/` — Ketch landing page
- `/#/login` — owner login
- `/#/dashboard` — security overview
- `/#/alerts` — alert triage and archive
- `/#/cameras` — camera monitoring

HashRouter is deliberate so static hosting needs no rewrite rules.

```bash
npm run build      # tsc -b && vite build → dist/
npm run preview    # serve dist/ locally
npm run lint
npm run test:security  # proxy, CSP and demo-isolation regression checks (Node 22)
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

The `functions/api/v1/[[path]].js` Pages Function proxies dashboard requests to the Render API.
This keeps authentication same-origin on the Ketch domain while the Go API retains its strict
origin policy. `API_ORIGIN` and `API_TRUSTED_ORIGIN` may override its safe defaults in Cloudflare.

| Variable | Configured in | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | Local Vite `.env` / frontend build | Public browser API base; default `/api/v1`. |
| `API_ORIGIN` | Cloudflare Pages Functions environment | Backend origin; default `https://liquor-store-api-7tq2.onrender.com`. |
| `API_TRUSTED_ORIGIN` | Cloudflare Pages Functions environment | Origin forwarded to the API; must match backend `WEB_ORIGIN`. |
| `CLOUDFLARE_API_TOKEN` | GitHub Actions secret | Credential scoped to Pages deployments; unnecessary locally. |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Actions secret | Account containing project `ketch-enterprise`; unnecessary locally. |

The `.env.example` contains placeholders/public defaults only. Share actual
deployment tokens privately. GitHub Actions secrets are not readable back as
plaintext; access to an existing deployment does not imply the token can be
exported from GitHub.

### Public demo and owner access

- **View dashboard** still opens the demo directly. Its 18 camera records and alert fixtures
  are synthetic frontend data referencing the existing public media, not a database export.
- The demo has no access token, never refreshes an old demo cookie, and cannot modify alerts,
  cameras or notification settings. Review and camera videos remain available.
- **Owner sign in** in the account menu (or `/#/login`) is the separate, explicit login flow.
  Only a successful explicit sign-in opts that browser into restoring an owner session.
  Real UUID alert deep-links require login and keep their destination after sign-in.
- `/api/v1/auth/demo` returns `404` for every method in Cloudflare and local development.
  The duplicate TypeScript proxy and server-held demo credential exchange were removed.
  `DEMO_EMAIL` / `DEMO_PASSWORD` are no longer read by this application.

### Frontend security headers

`security/headers.js` is shared by the API proxy and the build step that generates
`public/_headers`. This covers both static responses and Functions, which
[do not inherit Cloudflare Pages `_headers` rules](https://developers.cloudflare.com/pages/configuration/headers/).
The policy blocks framing (`frame-ancestors 'none'` and `X-Frame-Options: DENY`),
inline/eval JavaScript and plugins. Script/connect sources allow this site and the two
[Cloudflare Web Analytics sources](https://developers.cloudflare.com/web-analytics/faq/)
used by the existing edge-injected beacon. HSTS is enabled for this host;
`includeSubDomains` and preload are intentionally not enabled without reviewing other services.
Inline styles remain allowed for Material UI/Emotion and GSAP. Theme startup uses an external
same-origin script. Existing Render secure-video playback is allowed by `media-src`.

Run `npm run build`, start `npm run preview -- --host 127.0.0.1`, then run
`npm run test:browser` for an isolated Chromium smoke test. Install its browser with
`npx playwright install chromium` if needed. Tests mock all owner API calls, never use real
credentials and never send Telegram/WhatsApp notifications. Screenshots go to `.security-check/`.

This change prevents **new automatic OWNER sessions**; it does not revoke previously issued
sessions or rotate backend credentials. Those, backend rate limiting/AI-ingest hardening,
private storage and Render availability remain separate, deferred work. Public demo media
is still public by design; no existing footage or tracking assets are removed here.

### What your access covers, and what it doesn't

The API token is scoped to **Cloudflare Pages only**. It cannot change DNS, the domain
registration, or email. Those stay with Huy — `ketchenterprise.net`'s DNS zone also carries the
mail records for every `@ketchenterprise.net` address, so a wrong edit there takes down company
email, not a web page. If you need a DNS or domain change, ask; don't look for a way around it.

## Product boundary

Dashboard alerts are review prompts, not automated verdicts. The owner remains responsible for
confirming normal alerts and handling emergency procedures. Keep source citations in
`src/lib/roi.ts` attached to landing-page benchmark numbers.

## Layout

```
src/
  main.tsx                    HashRouter; lazy-loads the dashboard application
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
  security-dashboard/        Auth, owner shell, Overview, Alert, Camera, API client and theme
public/
  demo/                       landing imagery plus alert evidence clips and tracking data
  live-cameras/               looping camera-preview recordings and thumbnails
  cctv/                       fallback camera imagery
  favicon.svg
```

## The demo data

`public/demo/manifest.json` and its clip directories remain the source for selected landing-page
imagery. The owner dashboard uses the top-level evidence clips and tracking JSON copied from the
Liquor Store dashboard. Public demo records live in `src/security-dashboard/lib/demoApi.ts`;
authenticated owner records are loaded from the API and are not mixed with demo fixtures.

⚠ **The clips are not licence-cleared.** `seedance_liquor_01` — the hero — was generated with a
model whose licence for this use is unresolved, and the other three came from third-party store
CCTV whose provenance was never documented. They ship on this site because they already do; do
not reuse them anywhere else, don't put them in a deck, and don't publish them under a different
domain. Resolving this is Huy's open item, not yours.

## Contact placeholders

`ContactSection.tsx` still carries `+84 ___ ___ ____` and `hello@example.com`. They are
placeholders, not live addresses. Real contact details are a Huy/Christ decision.
