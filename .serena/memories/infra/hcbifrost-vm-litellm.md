# LiteLLM on hcbifrost-vm

Standalone LiteLLM proxy. **2026-06-30: Bifrost удалён с этой VM и больше
не нужен** — LiteLLM теперь единственный сервис на хосте. Раньше они работали
независимо друг от друга; сейчас просто нет sibling-сервиса.


> **См. также:** `litellm-model-updates.md` — как правильно обновлять
> `model_info` (ctx/out/vision/video). `/model/update` API не персистит
> эти поля, нужен SQL UPDATE + рестарт контейнера + рестарт `opencode-api`.

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

### ⚠ JS chunks sub_filter (added 2026-06-24)

Блок `location ~ ^/litellm/(swagger|assets|_next|static|lazy|favicon\.ico)`
содержит `sub_filter '/ui/' '/litellm/ui/';`. Без него LiteLLM-бандл
отдаёт хардкодженые абсолютные пути (`/ui/login`, `/ui/assets`, `/ui/chat`)
которые обходят basePath — браузер уходит на Bifrost. Если LiteLLM обновится
и добавит новые `/ui/<route>` пути, они автоматически покроются этим правилом.

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

## Image generation daily rate limit (per user / per team) — 2026-06-27

Custom pre-call hook `image_rate_limit_hook.py` in `/opt/litellm/` (bind-mounted
to `/app/image_rate_limit_hook.py`). Enforces per-user per-model per-day request
budget, with daily limit resolved by the user's team_alias.

**Tables** (in `litellm-pg`):
- `image_team_limits(team_alias text PK, daily_limit int)` — daily budget per
  team. Current seed (2026-06-27): `All Access=100`, all other 7 teams=50,
  `_default=50` (fallback for teams with no row).
- `image_request_counter(user_id, model, day, count)` PK — atomic UPSERT on
  each accepted image call. `day = CURRENT_DATE` (UTC).

**Models covered** (hardcoded in hook): `gpt-image-1.5`, `gpt-image-2`,
`gemini/gemini-3.1-flash-image`, `gemini/gemini-3-pro-image`. `n` from request
body is extracted (defensive: `n` / `num_images` / `sample_count` /
`number_of_images`, fallback 1) and increments counter by exactly `n` — one
HTTP request with `n=4` adds 4 to the counter.

**Behavior**:
- chat completions, embeddings, etc. — bypass hook entirely (call_type guard).
- `current + n > limit` → 429 with body `{error, message, limit, used,
  requested, remaining, team, reset_at}`, headers include `Retry-After`
  (minutes until UTC midnight) — **but see "Lost headers" caveat below**.
- Counter is read-then-checked-then-incremented; an UPSERT under contention can
  overshoot by at most the number of in-flight requests from the same user.
- `n > remaining` → entire request rejected (no partial fulfillment).
- DB unreachable or lookup error → fail-open (request allowed, logged).

**Registration**:
- `config.yaml`: `litellm_settings.callbacks` lists
  `image_rate_limit_hook.image_rate_limit_logger` first, then
  `user_agent_hook.user_agent_logger`.
- `litellm_entrypoint.sh` PYEOF4 — belt-and-suspenders patch that imports and
  registers the hook directly via `litellm.callbacks.insert(0, _irl_inst)` at
  startup, mirroring the same insurance patch used for UserAgentLogger.

**psycopg2-binary**: the container's `/app/.venv` ships WITHOUT psycopg2.
`litellm_entrypoint.sh` PYEOF3 ensures it is installed via
`python3 -m pip install --quiet psycopg2-binary` (idempotent) on every
container start. Without this, the hook would silently fail-open on every
call. DATABASE_URL is read from `/app/.env` (already bind-mounted, contains
`postgresql://litellm:litellm@litellm-pg:5432/litellm`).

**start.sh**: the bind-mount line `-v "$(pwd)/image_rate_limit_hook.py:/app/..."`
was added so the file appears inside the container at `/app/`. Without it,
`./start.sh` recreates the container from the existing image-layer bind list
and the new file would be invisible.

**Change a limit**: `UPDATE image_team_limits SET daily_limit=N WHERE
team_alias='...'; docker restart litellm`. Hook caches the table for 60s but
restart is cheap (~10s) and avoids stale-cache surprises.

**Reset a user's counter** (manual undo): `DELETE FROM image_request_counter
WHERE user_id='...';`. Useful after admin-issued overrides.

**Known caveat — `Retry-After` header is dropped on image endpoints**: the
image_generation endpoint in LiteLLM (`/app/.venv/lib/python3.13/site-packages/
litellm/proxy/image_endpoints/endpoints.py` ~line 187) catches HTTPException
and re-wraps it as `ProxyException` carrying only `message/type/param/code` —
the `headers` dict (where `Retry-After` lives) is discarded. The 429 body still
contains `reset_at: "UTC midnight"` and `remaining: N`, which clients can use
to schedule retries. Chat-completions endpoints preserve the headers (they
re-raise HTTPException as-is in their streaming path) — but image gen does
not. Workaround if header preservation becomes critical: patch the image
endpoint's except block to forward `exc.headers`.

**Verified** (2026-06-27) with two real virtual keys (CreativeTeam user, limit
50; All Access user, limit 100): all 7 tests pass — single +1, +49 then +1 to
50, +1 over limit → 429, +4 in one request → +4, +47 then n=4 → 429 no
increment, chat completions bypass, 100th for All Access OK, 101st → 429.

## Vision (image) support troubleshooting — 2026-06-26

### LiteLLM does NOT strip image content

Verified by hooking `OpenAIGPTConfig.transform_request` from inside the
litellm container: for **all** models (`moonshot/kimi-k2.6`,
`moonshot/kimi-k2.7`, `minimax/MiniMax-M3`), image content blocks
(`type: image_url`, both `data:` URIs and `http(s)://` URLs) are forwarded
intact to the upstream API. There is no `supports_vision`-gated content
stripping in LiteLLM core or in the per-provider handlers
(`MoonshotChatConfig._transform_messages`,
`MinimaxChatConfig`). If a model "doesn't see" an image through LiteLLM,
the cause is **upstream-side** (wrong endpoint, wrong headers, unsupported
image format), not LiteLLM silently dropping it.

### DB models vs config.yaml models — two separate worlds

Models loaded from `config.yaml` (`kimi-k2-0905-preview`, `MiniMax-M2`,
`glm-4.6`, etc.) carry explicit `api_base`, `api_key: os.environ/X`, and
(per-Kimi) `headers.User-Agent: os.environ/KIMI_USER_AGENT`. Models added
through the Admin UI / `/model/new` API live in
`LiteLLM_ProxyModelTable` and reference a **credential**
(`litellm_credential_name`) for `api_key`/`api_base`. The credential's
fields are Fernet-encrypted in `litellm_params` itself
(`custom_llm_provider`, `model`, `litellm_credential_name` all appear as
opaque base64 blobs when read via raw SQL). When overriding `api_base`
in `litellm_params` via SQL, **the credential's api_key is still used** —
so pointing at a different host than the credential was issued for will
produce a 401 from that host.

### Kimi K2.7 vision fix (2026-06-26)

Kimi K2.7 (`model_id 0ec138c9-a78d-477a-9cc2-1cc9477be77b`) was added
through the UI with `custom_llm_provider: moonshot`,
`litellm_credential_name: Kimi`, but **no** `headers.User-Agent`. The
moonshot upstream silently degrades image handling for requests without
a browser-like User-Agent. Adding
`litellm_params.headers.User-Agent = <KIMI_USER_AGENT value>` via SQL
(restoring parity with how `kimi-k2-0905-preview` is declared in
`config.yaml`) fixed vision through LiteLLM.

**Do NOT add `litellm_params.api_base`** to K2.7 — the credential
already encodes the correct endpoint and overriding it breaks auth
(`401 Invalid Authentication`) because the credential's api_key is bound
to the credential's api_base host.

SQL that worked (run on the VM as
`docker exec litellm-pg psql -U litellm -d litellm -c "..."`):

```sql
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = litellm_params
    || jsonb_build_object('headers', jsonb_build_object(
        'User-Agent', '<value of KIMI_USER_AGENT from /opt/litellm/.env>'
       ))
WHERE model_id = '0ec138c9-a78d-477a-9cc2-1cc9477be77b';
```

Followed by `docker restart litellm` and `systemctl restart opencode-api`
(the latter to flush the cached `_MODEL_INFO` in `api.py`).

### MiniMax-M3 vision (still broken as of 2026-06-26)

`MiniMax-M3` (`model_id 3039366c-ccc9-40cb-b62d-266aad75839e`) uses
`custom_llm_provider: minimax` and the `Minimax` credential. LiteLLM's
`minimax` provider defaults to `https://api.minimax.io/v1` (confirmed
by user as the correct endpoint). Image content reaches upstream
(verified via transform_request hook), but `api.minimax.io` returns
`400 (2013) — remote returned status 403` for **URL-form** image_url
blocks (minimax.io does not fetch remote URLs) and `200 OK` with empty
content for **base64 data-URI** image_url blocks. User reports M3 works
when called directly from opencode (same endpoint, same key), so the
remaining difference is presumably in how opencode's Go client forms the
image_url payload vs. how LiteLLM's Python handler forms it. This is
**not yet resolved** — see open investigation.

### LiteLLM callback pitfalls (2026-06-26)

Adding a custom Logger class to `litellm_settings.callbacks` in
`config.yaml` will **break container startup** if the class doesn't
implement the full callback protocol LiteLLM expects. Symptom:
container exits immediately after `[entrypoint]` lines, port 4000 never
opens, `curl http://127.0.0.1:4000/...` from inside the container
returns `Connection refused`. Recovery: revert `config.yaml` from
backup and `docker restart litellm`. To log request bodies, use nginx
`access_log ... $request_body` on the `/litellm/v1/chat/completions`
location instead — it's safer and doesn't touch the LiteLLM process.

## Initial credentials (placeholders — replace before production!)

In `/opt/litellm/.env`:
- `GLM_API_KEY=replace-with-your-glm-key`
- `ALIBABA_API_KEY=replace-with-your-dashscope-key`
- `MOONSHOT_API_KEY=replace-with-your-moonshot-key`
- `MINIMAX_API_KEY=replace-with-your-minimax-key`
- `LITELLM_MASTER_KEY=sk-litellm-placeholder-replace-before-prod`
- `LITELLM_SALT_KEY=placeholder-replace-before-prod-min-32-chars-xxxxx`
- `DATABASE_URL=postgresql://litellm:litellm@litellm-pg:5432/litellm`
- `KIMI_USER_AGENT=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`

## Key env vars on the LiteLLM container

- `SERVER_ROOT_PATH=/litellm` — set in `start.sh`. Tells LiteLLM UI (Next.js)
  to bake `/litellm/` into the static asset URLs and into the `/ui` redirect.
  This is what makes the SPA assets load correctly through the `/litellm/`
  subpath instead of trying to fetch `/_next/...` at the public root.
- `UI_USERNAME=admin`, `UI_PASSWORD=admin` — initial Admin UI login (role
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
echo '7Cr4iW9l8P' | ssh -p 1995 dev01@162.55.137.149 'sudo -S -p "" bash -c "install -m 0644 /tmp/litellm-bifrost /etc/nginx/sites-available/litellm-bifrost && nginx -t && systemctl reload nginx"'

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

## opencode config generator (per-user) — added 2026-06-21

A standalone tool is served at `https://hcbifrost.herocraft.com/setup-opencode/`
that emits a ready-to-save `opencode.json` template (with all 10 models, no
key — user provides it via env var `LITELLM_API_KEY`).

The tool is also linked from inside the LiteLLM Admin UI Users page — a small
"🛠 opencode" button is injected next to each user row by nginx `sub_filter`
in `location ~ ^/litellm/(ui|fallback)(/|$)`. Clicking it opens
`/setup-opencode/?user=<user_id>` in a new tab.

**How to use (admin):**
1. Open `https://hcbifrost.herocraft.com/litellm/ui/` and log in as `admin/admin`
2. Two ways to generate a config:
   - **Floating button** (bottom-right, every page) — opens the user picker
   - **Per-row 🛠 OpenCode button** (in Users table Actions column) — opens the picker with that user pre-selected
3. In the popup page, paste your **master virtual key** (e.g. the
   `sk-litellm-placeholder-replace-before-prod` from `/opt/litellm/.env`,
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
- `/etc/nginx/sites-available/litellm-bifrost` — `location ^~ /setup-opencode` and the `sub_filter` injection

## Verified working (2026-06-20)

- `curl -sS https://hcbifrost.herocraft.com/health` → Bifrost `{"components":{"db_pings":"ok"},"status":"ok"}`
- `curl -sS https://hcbifrost.herocraft.com/litellm/health/readiness` → `{"status":"healthy","db":"connected"}`
- `curl -sS https://hcbifrost.herocraft.com/litellm/v1/models -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod"` → 10 models
- `curl -sS https://hcbifrost.herocraft.com/litellm/v1/models` (no auth) → HTTP 401
- `curl -sSI https://hcbifrost.herocraft.com/litellm/ui` → `HTTP/2 307  Location: http://hcbifrost.herocraft.com/litellm/ui/` (preserves prefix, no :8080 leak)
- `curl -sSL https://hcbifrost.herocraft.com/litellm/ui/` → 200, `<title>LiteLLM Dashboard</title>`, 17194 bytes
- All `/litellm/_next/static/chunks/*.css` and `*.js` assets: 200 with correct `text/css` / `text/javascript` Content-Type
- All `/litellm/_next/static/media/*.woff2` fonts: 200 `application/octet-stream`
