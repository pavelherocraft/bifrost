# LiteLLM on hcbifrost-vm (sibling to Bifrost)

Standalone LiteLLM proxy installed on the same VM as Bifrost. They are
**fully independent** — LiteLLM does NOT proxy through Bifrost and vice
versa. Each talks to upstream providers on its own.

See `infra/hcbifrost-vm` for VM access (SSH, sudo, etc.).

## Public URLs (through nginx reverse proxy on this VM)

| URL                                                          | Service         |
|--------------------------------------------------------------|-----------------|
| `https://hcbifrost.herocraft.com/`                           | Bifrost UI + API (unchanged) |
| `https://hcbifrost.herocraft.com/v1/`                        | Bifrost OpenAI-compatible API |
| `https://hcbifrost.herocraft.com/litellm/`                   | LiteLLM Swagger (root) |
| `https://hcbifrost.herocraft.com/litellm/ui/`                | **LiteLLM Admin UI** |
| `https://hcbifrost.herocraft.com/litellm/v1/`                 | LiteLLM OpenAI-compatible API |
| `https://hcbifrost.herocraft.com/litellm/health/readiness`   | LiteLLM health |
| `https://hclitellm.herocraft.com/`                           | LiteLLM UI (server block ready in nginx, awaiting Cloudflare A-record) |
| `https://hclitellm.herocraft.com/v1/`                        | LiteLLM OpenAI API (same condition) |

The public edge (Cloudflare) forwards 80/443 → `127.0.0.1:8080` on this VM.
nginx listens on 8080 and 80 and routes by `Host` header + path prefix:

- `Host: hcbifrost.herocraft.com`
  - `/`         → Bifrost (container `unruffled_cannon`, internal port 8081)
  - `/litellm/` → LiteLLM
    - **API paths** (`/v1/`, `/health/`, `/key/`, …) — `/litellm/` prefix stripped before proxying (LiteLLM API expects root-relative paths)
    - **UI paths** (`/ui/`, `/_next/`, `/swagger/`, `/assets/`, `/favicon.ico`, `/fallback/`) — `/litellm/` prefix kept intact (LiteLLM with `SERVER_ROOT_PATH=/litellm` serves these with the prefix baked in)

## Topology

```
            ┌───────────────────── edge (Cloudflare) ─────────────────────┐
            │  https://hcbifrost.herocraft.com/*  →  162.55.137.149:80     │
            └─────────────────────────────┬───────────────────────────────┘
                                          │ (edge forwards to :80 / :8080)
                                          ▼
                                nginx (:80 and :8080 on VM)
                ┌────────────────────────┴────────────────────────┐
       Host: hcbifrost.herocraft.com                     Host: hclitellm.herocraft.com (DNS pending)
                │                                                   │
       ┌────────┴────────┐                                          ▼
       │ /               │                              litellm:4001 (litellm container)
       │ /v1/...         │──► bifrost:8081 (unruffled_cannon)
       │ /ui             │
       │                 │
       │ /litellm/       │──► litellm:4001 (litellm container)
       └─────────────────┘

litellm container ──► postgres:5432 (litellm-pg, in litellm-net docker network)
```

## Containers & ports

| Container     | Image                                       | Host port | Internal  |
|---------------|---------------------------------------------|-----------|-----------|
| `unruffled_cannon` (Bifrost) | `maximhq/bifrost`              | 8081      | 8080      |
| `litellm`     | `ghcr.io/berriai/litellm:main-stable`       | 4001      | 4000      |
| `litellm-pg`  | `postgres:16-alpine`                        | (none)    | 5432      |
| nginx         | `nginx 1.22 (apt)`                          | 80 + 8080 | —         |

**Why Postgres?** The `main-stable` (and all current) LiteLLM Docker images
ship with a Prisma schema hard-coded to PostgreSQL. There is **no** public
SQLite variant. SQLite is only viable if running LiteLLM via
`pip install litellm[proxy]` directly on the host, not in Docker.

## Layout on VM

```
/opt/litellm/
  config.yaml       # model_list + providers + routing + UA override for Kimi
  .env              # API keys (PLACEHOLDERS — user fills), master key, salt, DATABASE_URL
  start.sh          # creates litellm-net, starts pg+litellm, waits for /health/readiness
  stop.sh           # stop+remove both containers
  logs.sh           # docker logs -f litellm
  README.md         # this file

/etc/nginx/sites-available/litellm-bifrost   # vhosts (hcbifrost + /litellm/, hclitellm)
/etc/nginx/snippets/proxy-common.conf        # shared proxy settings
```

Docker named volumes:
- `litellm-pg-data` — Postgres data directory
- `litellm-data`    — LiteLLM misc state
- `06cc911e27e8f48751748af9edf4305ea99020ef521b3c966e04724fc3065193` — Bifrost data

## Models registered (config.yaml)

- **GLM (Z.AI Coding Plan, base `https://api.z.ai/api/coding/v1`)**: `glm-4.6`, `glm-4.5`, `glm-4.5-air`
- **Alibaba DashScope (Coding Plan, base `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`)**: `qwen3-coder-plus`, `qwen3-coder-flash`, `qwen3-max`
- **Kimi/Moonshot (base `https://api.moonshot.cn/v1`, custom `User-Agent` via `KIMI_USER_AGENT` env)**: `kimi-k2-0905-preview`, `kimi-k2-turbo-preview`, `moonshot-v1-128k`
- **MiniMax (base `https://api.minimax.io/v1`)**: `MiniMax-M2`

All declared with `drop_params: true` (LiteLLM silently drops unsupported
params per provider — avoids 400s on cross-provider requests).

## Initial credentials (placeholders — replace before production!)

In `/opt/litellm/.env`:
- `GLM_API_KEY=replace-with-your-glm-key`
- `ALIBABA_API_KEY=replace-with-your-dashscope-key`
- `MOONSHOT_API_KEY=replace-with-your-moonshot-key`
- `MINIMAX_API_KEY=replace-with-your-minimax-key`
- `LITELLM_MASTER_KEY=<LITELLM_MASTER_KEY from /opt/litellm/.env>`
- `LITELLM_SALT_KEY=placeholder-replace-before-prod-min-32-chars-xxxxx`
- `DATABASE_URL=postgresql://litellm:litellm@litellm-pg:5432/litellm`
- `KIMI_USER_AGENT=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`

## Key env vars on the LiteLLM container

- `SERVER_ROOT_PATH=/litellm` — set in `start.sh`. Tells LiteLLM UI (Next.js)
  to bake `/litellm/` into the static asset URLs and into the `/ui` redirect.
  This is what makes the SPA assets load correctly through the `/litellm/`
  subpath instead of trying to fetch `/_next/...` at the public root.
- `UI_USERNAME=admin`, `UI_PASSWORD=<MASKED> — initial Admin UI login (role
  `proxy_admin`). **Change after first login** (either via the UI Settings
  panel or by editing `start.sh` + restarting the container).
- `STORE_MODEL_IN_DB=True` — required for the Admin UI's Model management
  (add / edit / delete models at runtime). On the first start with this flag,
  LiteLLM auto-migrates models from `config.yaml` into the DB. After that,
  `config.yaml` is read **only on the very first run**; runtime model changes
  must be made through the UI or `/model/new` API.

## Admin UI login

| What  | Value     |
|-------|-----------|
| URL   | `https://hcbifrost.herocraft.com/litellm/ui/` |
| Login | `admin`   |
| Password | `admin` |

Login is verified to work end-to-end: `POST /v2/login {"username":"admin","password":"admin"}`
returns HTTP 200 with a JWT (`role: proxy_admin`).

## opencode config generator (per-user)

A standalone tool is served at `https://hcbifrost.herocraft.com/setup-opencode/`
that emits a ready-to-save `opencode.json` template (with all 10 models, no
key — user provides it via env var `LITELLM_API_KEY`).

A floating **🛠 opencode** button is injected in the bottom-right corner of
the LiteLLM Admin UI (every page) via nginx `sub_filter` on the UI HTML.
When the user navigates to **Users** (`?page=users`), a **🛠 OpenCode** button
is injected into the Actions column of every row in the Internal Users
table. Both elements live outside any React subtree (fixed-positioned /
appended to cell) so they survive React hydration.

**How to use (admin):**
1. Open `https://hcbifrost.herocraft.com/litellm/ui/` and log in as `admin/admin`
2. Two ways to generate a config:
   - **Floating button** (bottom-right, every page) — opens the user picker
   - **Per-row 🛠 OpenCode button** (in Users table Actions column) — opens the picker with that user pre-selected
3. In the popup page, paste your **master virtual key** (e.g. the
   `<LITELLM_MASTER_KEY from /opt/litellm/.env>` from `/opt/litellm/.env`,
   or any other admin virtual key) — it's only used to fetch the user list
   via `GET /user/list` and is never embedded into the generated config
4. The key is saved in `sessionStorage` after first paste — subsequent
   opens in the same browser auto-load the list (no re-paste needed)
5. Pick the user from the dropdown (or it's already pre-selected)
6. Click **📋 Copy** or **⬇ Download opencode.json**
7. Send the file to the user

**How to use (end user):**
1. Save `opencode.json` to `~/.config/opencode/opencode.json` (Windows: `C:\Users\<User>\.config\opencode\`)
2. Set the env var `LITELLM_API_KEY` to your virtual key from LiteLLM Virtual Keys page
3. Run `opencode` — in `/models` you'll see all 10 models; LiteLLM enforces access by key

Files involved:
- `/opt/opencode-setup/index.html` — the tool
- `/etc/nginx/sites-available/litellm-bifrost` — `location ^~ /setup-opencode` and the `sub_filter` injection that adds both the FAB and per-row buttons

## Daily operations

```bash
# Start LiteLLM stack (postgres + litellm)
ssh -p 1995 dev01@162.55.137.149 'cd /opt/litellm && ./start.sh'

# Follow LiteLLM logs
ssh -p 1995 dev01@162.55.137.149 '/opt/litellm/logs.sh'

# Stop LiteLLM stack (postgres + litellm)
ssh -p 1995 dev01@162.55.137.149 '/opt/litellm/stop.sh'

# Edit models: edit /opt/litellm/config.yaml, then
ssh -p 1995 dev01@162.55.137.149 'docker restart litellm'

# Edit routing/nginx: scp the new config to /tmp/, then
echo '<MASKED>' | ssh -p 1995 dev01@162.55.137.149 'sudo -S -p "" bash -c "install -m 0644 /tmp/litellm-bifrost /etc/nginx/sites-available/litellm-bifrost && nginx -t && systemctl reload nginx"'

# Update LiteLLM image
ssh -p 1995 dev01@162.55.137.149 'docker pull ghcr.io/berriai/litellm:main-stable && cd /opt/litellm && ./stop.sh && ./start.sh'

# Wipe LiteLLM DB (CAUTION: deletes spend logs, virtual keys, etc.)
ssh -p 1995 dev01@162.55.137.149 'cd /opt/litellm && ./stop.sh && docker volume rm litellm-pg-data litellm-data'

# Reload Bifrost container (e.g., after env edits)
ssh -p 1995 dev01@162.55.137.149 'docker restart unruffled_cannon'
```

## Cloudflare DNS records required

The edge (Cloudflare) already proxies `herocraft.com`. Add one A-record
in the Cloudflare dashboard to expose LiteLLM on its own subdomain (no
subpath):

| Type | Name       | Content         | Proxy       | Notes                              |
|------|------------|-----------------|-------------|------------------------------------|
| A    | hclitellm  | `162.55.137.149`| Proxied (orange cloud) | Origin port defaults to 80 — matches our nginx |

If Universal SSL is enabled for `herocraft.com` (default on CF Free/Pro),
the wildcard cert covers `hclitellm.herocraft.com` automatically — no
further TLS work needed on either side. The nginx server block for
`hclitellm.herocraft.com` is already in `/etc/nginx/sites-available/litellm-bifrost`
and will activate the moment the A-record is created.

## Verified working (2026-06-20)

- `curl -sS https://hcbifrost.herocraft.com/health` → Bifrost `{"components":{"db_pings":"ok"},"status":"ok"}`
- `curl -sS https://hcbifrost.herocraft.com/litellm/health/readiness` → `{"status":"healthy","db":"connected"}`
- `curl -sS https://hcbifrost.herocraft.com/litellm/v1/models -H "Authorization: Bearer <LITELLM_MASTER_KEY from /opt/litellm/.env>"` → 10 models
- `curl -sS https://hcbifrost.herocraft.com/litellm/v1/models` (no auth) → HTTP 401
- `curl -sSI https://hcbifrost.herocraft.com/litellm/ui` → `HTTP/2 307  Location: http://hcbifrost.herocraft.com/litellm/ui/` (preserves prefix, no :8080 leak)
- `curl -sSL https://hcbifrost.herocraft.com/litellm/ui/` → 200, `<title>LiteLLM Dashboard</title>`, 17194 bytes
- All `/litellm/_next/static/chunks/*.css` and `*.js` assets: 200 with correct `text/css` / `text/javascript` Content-Type
- All `/litellm/_next/static/media/*.woff2` fonts: 200 `application/octet-stream`

## Admin endpoints IP whitelist (nginx) — updated 2026-08-25

Write operations are protected by an IP whitelist (`allow`/`deny` blocks in
`/etc/nginx/sites-enabled/litellm-bifrost`, 12 locations):
`/litellm/key/generate|update|delete`, `/litellm/model/new|update|delete`,
`/litellm/config/list`, `/litellm/get/config`, `/litellm/organization`,
`/litellm/guardrails`, `/litellm/sso`, `/litellm/admin/glm-swap/`.

Whitelisted IPs:

| IP | Purpose |
|----|---------|
| 89.19.213.124  | Timeweb reverse proxy (self) |
| 162.55.180.78  | office / VPN |
| 127.0.0.1      | localhost (VM) |
| 10.10.10.1     | internal network |
| 31.77.201.242  | proxy egress #1 (added 2026-08-25) |
| 193.160.209.118| proxy egress #2 (added 2026-08-25) |
| 91.108.1.0     | proxy egress #3 (added 2026-08-25, Telegram range) |

Client IP is recovered via nginx `real_ip` (`set_real_ip_from 89.19.213.124` +
`real_ip_header X-Forwarded-For` in `/etc/nginx/nginx.conf`): the edge proxy
passes the real client IP in XFF.

NOTE: the corporate proxy ROTATES egress IPs. If admin UI write operations
suddenly return 403 again, find the new egress IP in
`/var/log/nginx/access.log` (format `xff_combined`, field `XFF=`) and add it
to all 12 whitelist blocks. Egress `91.108.1.0` (Telegram range) was initially not
whitelisted (shared range), then added on user decision the same day.

WARNING: never leave `*.bak*` files in `/etc/nginx/sites-enabled/` — nginx
`include`s every file there and a backup with a duplicate `upstream` block
breaks `nginx -t`. Keep backups in `sites-available/` (current convention).
Keep `sites-available` and `sites-enabled` copies identical (enabled is live).