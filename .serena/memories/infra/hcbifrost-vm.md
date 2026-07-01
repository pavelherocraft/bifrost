# LiteLLM VM (hcbifrost.herocraft.com)

> **2026-06-30 #1: Bifrost удалён и больше не нужен.** Контейнер `unruffled_cannon`
> убран, весь раздел про Bifrost ниже — только историческая справка, актуальной
> конфигурации не соответствует. На VM остаётся только LiteLLM (см.
> `infra/hcbifrost-vm-litellm`). Nginx теперь терминирует только LiteLLM;
> location `/` (раньше → Bifrost) можно удалить или оставить 404.

LiteLLM proxy установлен на этой VM. Раньше здесь же крутился Bifrost — он
демонтирован 2026-06-30 и обратно не нужен. Любые упоминания Bifrost ниже в
этом файле — история, не отражают текущее состояние.

## Topology (corrected 2026-06-30 — previous memory was stale)

**Public DNS** (`hcbifrost.herocraft.com`, verified via Cloudflare DoH 1.1.1.1):
```
hcbifrost.herocraft.com → 89.19.213.124
```
**NOT** `162.55.137.149` (old value, now stale).

**Network chain:**
```
client → 89.19.213.124 (Timeweb AS210976, Frankfurt)
           nginx-proxy (Server: nginx, no version) — terminates HTTPS
           BODY SIZE LIMIT ~1MB (default client_max_body_size 1m) ← source of 413s
           known to return 500 on streaming/large-but-under-1MB requests
         → (forward via unknown tunnel)
         162.55.137.149 (Hetzner gateway)
           nginx/1.30.1 on :80/:443, Gitea (i_like_gitea cookie)
           SSH :1995 forward → bifrost VM internal
         → 10.10.10.11 hc-srv21-hcbifrost (the actual bifrost VM)
           nginx/1.22.1 on :80, :8080; litellm on :4001
           No :443 listener — TLS terminated at 89.19.213.124
```

**Critical operational consequences:**
- Any HTTP body > ~1MB is rejected with 413 by 89.19.213.124 proxy before
  reaching bifrost VM. nginx on bifrost VM has `client_max_body_size 100M`
  but that's irrelevant — proxy in front is the real gate.
- Streaming responses from LiteLLM sometimes get 500 from the proxy
  (buffering / timeout / connection-tracking issue).
- Direct curl from this VM to `https://hcbifrost.herocraft.com/...` TIMES OUT
  (asymmetric routing or proxy blocks source = 162.55.137.149). Always test
  either from external client or directly against `127.0.0.1:4001` on the VM.
- `162.55.137.149:80` no longer hits bifrost — it serves Gitea (separate
  service on the gateway host). Don't use it for bifrost URLs.

**To get to bifrost VM** (where litellm actually runs):
- SSH: `ssh -p 1995 dev01@162.55.137.149` (forwarded to internal 10.10.10.11)
- HTTP from outside: `https://hcbifrost.herocraft.com/litellm/...` (through 89.19.213.124 proxy)
- HTTP from VM itself: `http://127.0.0.1:4001/...` (direct, bypass everything)

## SSH credentials
- SSH host: `162.55.137.149` (port `1995`)
- User: `dev01` (has sudo)
- Password: `7Cr4iW9l8P`
- bifrost VM hostname: `hc-srv21-hcbifrost`
- bifrost VM internal IP: `10.10.10.11`

## Connection snippet (Windows OpenSSH, password auth)
PowerShell helper because Windows OpenSSH cannot pipe password directly:

```powershell
$askpass = "C:\Users\Admin\AppData\Local\Temp\opencode\askpass.cmd"
Set-Content -LiteralPath $askpass -Value '@echo 7Cr4iW9l8P' -Encoding ASCII
$env:SSH_ASKPASS = $askpass
$env:SSH_ASKPASS_REQUIRE = 'force'
$env:DISPLAY = 'none'
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -p 1995 dev01@162.55.137.149 'bash -s' < script.sh
```

For sudo over ssh, pipe the script via stdin (`ssh ... 'bash -s'`) and inside the script use:
```bash
echo '7Cr4iW9l8P' | sudo -S -p '' <command>
```

CRITICAL gotcha: `echo 'pwd' | sudo -S -p '' bash -s <<'EOS' ... EOS` BREAKS — sudo reads the password from stdin, then bash tries to read the heredoc from the same stdin and fails. Use `echo 'pwd' | sudo -S -p '' bash -c '...inline script...'` OR upload the script via scp first and run `echo 'pwd' | sudo -S -p '' bash /tmp/script.sh`.

## Host facts (verified 2026-06-20)
- Hostname: `hc-srv21-hcbifrost`
- OS: Debian 12, kernel 6.1.0-10-amd64
- CPU/RAM: 7.8 GiB RAM, ~974 MiB swap
- Disk: `/dev/sda1` 39G, 18% used
- Docker: 20.10.24 — **NO `docker compose` plugin installed**. Use `docker run` with explicit ports/volumes/env. (Old docker version also doesn't support `docker ps --format` — use plain `docker ps` / `docker ps -a`.)

## KNOWN BUG — 2026-06-30: 89.19.213.124 proxy breaks large requests

**Symptom**: opencode requests to LiteLLM via `hcbifrost.herocraft.com` fail
with `Internal Server Error` (500) or `Request Entity Too Large` (413),
especially for chat completions with substantial system prompt + agent
definitions + tool definitions + session history.

**Root cause**: nginx-proxy on `89.19.213.124` has `client_max_body_size 1m`
(default). Anything over ~1MB is rejected with 413. Streaming responses and
borderline-size payloads sometimes return 500 (proxy_buffering / timeouts).

**Reproduction** (verified 2026-06-30 from local opencode machine):
- 50KB POST → 500 (proxy / upstream issue)
- 512KB POST → 500
- 1.2MB POST → 413 in 0.18s (proxy rejects, never forwards)
- 5MB POST → 413
- 6.9KB streaming POST → 200 OK in 14s (works)

**Direct on bifrost VM** (bypass proxy):
- 1.2MB POST via `http://127.0.0.1:4001/...` → 200 OK
- nginx on bifrost has `client_max_body_size 100M` — no problem there

**Why "empty opencode sessions" also fail**: opencode.json with 27 subagent
definitions + orchestrator's massive system prompt + MCP tool definitions
easily produces 50-200KB+ payloads even for the first message in a fresh
session. Plus history grows with every tool call. Within minutes of work
the body crosses the proxy's pain threshold.

**Fix options** (whoever owns the 89.19.213.124 Timeweb VPS — unknown to me):
1. **Best**: set `client_max_body_size 100M;` in nginx-proxy config on
   89.19.213.124, plus `proxy_buffering off;` for `/litellm/` location
   (streaming), then `nginx -s reload`.
2. **Alternative**: bypass proxy — change DNS to point directly at
   162.55.137.149 if it can serve HTTPS (currently only Gitea there).
3. **Quick workaround for opencode users**: reduce agent definitions /
   trim system prompt / start fresh sessions frequently.

**This bug is the answer to "why litellm returns 500 only through opencode
while curl tests work" — curl sends small bodies, opencode sends big ones.**

## Port allocation on VM (current, 2026-06-30 — Bifrost gone)
| Port | Service            | Status                    |
|------|--------------------|---------------------------|
| 22   | sshd               | not directly exposed (1995 → 22) |
| 1995 | sshd (external)    | bound                     |
| 80   | nginx              | bound 0.0.0.0             |
| 8080 | nginx              | bound 0.0.0.0 (legacy front proxy port, kept for now) |
| 4001 | LiteLLM (HTTP)     | bound 0.0.0.0 (and IPv6), upstream of nginx |
| 5432 | LiteLLM Postgres   | internal to `litellm-net` docker network only |
| ~~8081~~ | ~~Bifrost~~     | **removed 2026-06-30**    |

## Front proxy (nginx)
Public edge (Cloudflare on `hcbifrost.herocraft.com`) forwards 80/443 → `127.0.0.1:8080` on this VM. nginx on 8080 routes `/litellm/` to LiteLLM.

- Config: `/etc/nginx/sites-available/litellm-bifrost` (still named litellm-bifrost for historical reasons; feel free to rename when convenient)
- `/litellm/` → `127.0.0.1:4001` (LiteLLM container, prefix stripped for API paths)
- `/` was previously → Bifrost — **no longer routed**, will return nginx 404 unless repurposed
- Reload: `echo '7Cr4iW9l8P' | ssh -p 1995 dev01@162.55.137.149 'sudo -S -p "" nginx -t && sudo systemctl reload nginx'`

