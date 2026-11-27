# Deploying on one server

One GPU machine runs everything: the AI service, the Go API, the dashboard and
the camera gateway. Supervisor keeps them up (no Docker needed, so this also
works inside an unprivileged container such as a vast.ai instance).

```
cameras ──► MediaMTX ──rtsp──► AI (ai/) ──► Neon + R2 ◄── Go API (backend/) ◄── Caddy ◄── browser
              │  127.0.0.1:8554                                        /api ──┘     │
              └── HLS 127.0.0.1:8888 ◄──────────── /live (checked by the API) ─────┘
```

| Service | Program | Listens on |
| --- | --- | --- |
| `liquor-mediamtx` | MediaMTX: every camera as `rtsp://127.0.0.1:8554/<camera_id>`, HLS, 10 min of recordings for evidence clips | loopback only |
| `liquor-ai` | `ai/src/app/run_split_pipeline.py --storage-mode cloud-db` | – |
| `liquor-api` | Go API, migrates the database on start | 3000 |
| `liquor-web` | Caddy: dashboard files, `/api`, `/live` | `WEB_LISTEN`, or 80/443 with `SITE_ADDRESS` |

## Two modes

`MODE` in `deploy/liquor.env` decides only where MediaMTX takes each camera from.
The AI's manifest, the API and the dashboard are identical in both.

- **test**: every camera of `STORE_MANIFEST` replays its own footage
  (`stream.playlist` / `stream.uri` under `data/videos`), looped forever. The AI
  analyses it live and writes real alerts. The dashboard labels the live view
  *TEST MODE (LOOPED FOOTAGE)*. On the first start each video is re-encoded once
  to H.264 (a few minutes); later starts reuse `deploy/runtime/playlists`.
- **production**: every camera is relayed from `deploy/cameras.production.json`
  (`camera_id → {"url": "rtsp://…"}`, see the example). MediaMTX keeps one
  connection per camera and shares it between the AI, the recorder and viewers.
  HEVC cameras need `"transcode": "nvenc"` (or `"cpu"`) so recordings and browser
  playback work.

Switch with `deploy/liquorctl mode test|production` (rewrites `MODE`, restarts).

## Install

On a machine with Go and Node (or the server itself, if it has them):

```
deploy/build.sh                     # deploy/dist/* (linux/amd64) + frontend/dist
```

Copy the repository to the server, including the ignored `ai/.env`,
`backend/.env`, `deploy/dist/` and `frontend/dist/`, and for test mode the
videos the manifest plays (`data/videos/...`). Then on the server:

```
cp deploy/liquor.env.example deploy/liquor.env    # set MODE, PUBLIC_URL, a random MEDIAMTX_READ_PASS
deploy/install.sh                                 # Python env + onnxruntime(-gpu), MediaMTX, supervisor
deploy/liquorctl start
deploy/liquorctl status
```

`ai/.env` and `backend/.env` must point at the **same** Neon database and R2
bucket (see `backend/docs/environment.md`).

## Accounts

Registration stays off in production. Create or reset an account (stores must
already exist; the AI creates them from the manifest on start):

```
OWNER_PASSWORD='…' deploy/liquorctl owner owner@example.com "Owner name" store_test_01
```

## Reaching the dashboard

- **SSH tunnel** (private, no open port): keep `WEB_LISTEN=127.0.0.1:18000`,
  `PUBLIC_URL=http://localhost:18000`, then on your machine
  `ssh -p <ssh_port> -L 18000:localhost:18000 root@<server>` and open
  <http://localhost:18000>. Loopback is the one place plain HTTP is accepted in
  production (browsers treat it as secure).
- **Public**: point a domain at the server, set `SITE_ADDRESS=dashboard.example.com`
  and `PUBLIC_URL=https://dashboard.example.com`; Caddy obtains the certificate.
  On vast.ai, ports 80/443 are not available: use a Cloudflare tunnel to
  `WEB_LISTEN` instead.

## Day to day

```
deploy/liquorctl logs ai          # also: mediamtx, api, web
deploy/liquorctl restart
```

Logs and generated config live in `deploy/runtime/` (never edit it; it is
rewritten on every start). Stopping `liquor-ai` gives it up to 330 s to flush
pending alerts and clips to Neon/R2.
