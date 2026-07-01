# LiteLLM MCP permissions — источник правды через Teams

## Источник правды

`object_permission.mcp_servers` — массив в `LiteLLM_ObjectPermissionTable`, привязанный к команде через `LiteLLM_TeamTable.object_permission_id`.

**Что хранится в массиве:** **server_name** (например `"zai_web_search"`) или **alias** (если заполнен). **НЕ UUID server_id**.

Пример:
```sql
-- LiteLLM_ObjectPermissionTable.mcp_servers = ['zai_web_search']
-- LiteLLM_MCPServerTable.server_name = 'zai_web_search', alias = NULL, server_id = '2382f09164bf3fb6b8e2bebdf91038d3'
```

## Workflow для привязки MCP к команде

```bash
# Через UI: Teams → edit team → Allowed MCP Servers → выбрать серверы

# Через API (с master key):
curl -X POST -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod" \
  -H "Content-Type: application/json" \
  --data '{"team_id":"<TEAM_UUID>","object_permission":{"mcp_servers":["zai_web_search","zai_zread"]}}' \
  "https://hcbifrost.herocraft.com/litellm/team/update"
```

## Как api.py резолвит MCP для пользователя

1. `/user/info?user_id=<uid>` → список team_id юзера
2. Для каждого team: `/team/info?team_id=<tid>` → `object_permission.mcp_servers` + `mcp_access_groups`
3. Объединить все уникальные server_name/alias из всех команд
4. `/v1/mcp/server` → зарегистрированные серверы (через LiteLLM internal manager, **не в Postgres**)
5. Фильтр: оставить только те, чей `server_name` ИЛИ `alias` есть в объединённом списке, или чьи `mcp_access_groups` пересекаются с allowed_groups

**Файл:** `/opt/opencode-setup/api.py`, функция `resolve_user_mcp_servers(user_id)`

## URL в opencode.json

`https://hcbifrost.herocraft.com/litellm/<server_name>/mcp` — LiteLLM proxy маршрутизирует на upstream MCP.

Header `Authorization: Bearer {env:LITELLM_API_KEY}` — клиент шлёт свой ключ, LiteLLM применяет per-tool фильтрацию.

## Сравнение моделей доступа

| Модель | Где настраивается | Что видит юзер |
|---|---|---|
| Per-Team (object_permission.mcp_servers) | LiteLLM UI → Teams → edit | Сервер доступен всем членам команды |
| Per-Key (key.object_permission.mcp_servers) | Keys → edit key → MCP Settings | Сервер доступен только через этот ключ |
| Allow All (server.allow_all_keys) | MCP server config | Любой ключ с правом на MCP может использовать |

## Где НЕ настраивается MCP для команды

`/litellm/ui/?page=team-edit&team_id=...` — UI форма Teams показывает MCP серверы для привязки, **но не показывает какой сервер уже привязан к каким командам** (LiteLLM UI bug — `teams` field в MCP server response пустой).

Для просмотра текущих привязок: psql запрос к `LiteLLM_ObjectPermissionTable.mcp_servers` или через `/team/info`.

## Известные MCP серверы в системе (от 2026-06-23)

| server_name | description | url |
|---|---|---|
| zai_web_search | Z.AI Web Search MCP | https://api.z.ai/api/litellm/mcp/web_search_prime/mcp |
| zai_web_reader | Z.AI Web Reader MCP | https://api.z.ai/api/litellm/mcp/web_reader/mcp |
| zai_zread | Z.AI Zread MCP | https://api.z.ai/api/litellm/mcp/zread/mcp |