# Bifrost LiteLLM UI injection — OpenCode кнопки

## Что есть
В LiteLLM UI инжектится JS-скрипт через nginx `sub_filter '</head>' '${oc_inject}</head>'`. 
Скрипт добавляет две кнопки в правый нижний угол / в таблицу Users.

## Где находится

- **JS-скрипт:** `/opt/opencode-setup/users-btn.js` (на VM, контейнер `litellm` НЕ содержит этот файл — он в nginx file root)
- **HTML-генератор:** `/opt/opencode-setup/index.html` (страница `/setup-opencode/` — копируемая конфигурация для opencode)
- **API для генератора:** `/opt/opencode-setup/api.py` (порт 9000, проксируется nginx → `/setup-opencode/api/`). Читает `/model/info` для лимитов и vision.
- **Nginx конфиг:** `/etc/nginx/sites-enabled/litellm-bifrost`:
  - `set $oc_inject '<script src="/setup-opencode/users-btn.js?v=<N>"></script>';` (N — cache-bust число)
  - `sub_filter '</head>' '${oc_inject}</head>';` в блоках `/litellm/(ui|fallback)`
  - `sub_filter_once off;`

## Локальные копии (для редактирования)
- `C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\users-btn.js`
- `C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\index.html`
- `C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\api.py`

## Deploy helper
`C:\Users\Admin\AppData\Local\Temp\opencode\deploy3.py <local> <remote>` — base64 stream SSH deploy.

## Изменение скрипта — workflow
1. Отредактировать `users-btn.js` локально
2. Деплой: `python C:\Users\Admin\AppData\Local\Temp\opencode\deploy3.py "C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\users-btn.js" /opt/opencode-setup/users-btn.js`
3. Поднять cache-bust: `echo "7Cr4iW9l8P" | sudo -S -p "" sed -i "s/users-btn.js?v=N/users-btn.js?v=N+1/g" /etc/nginx/sites-enabled/litellm-bifrost && echo "7Cr4iW9l8P" | sudo -S -p "" nginx -s reload`
4. Перезагрузить UI (Ctrl+Shift+R)

## Кнопка "🛠 Мои настройки" (floating bottom-right)
- Видна на ЛЮБОЙ странице LiteLLM UI
- При клике определяет user_id автоматически:
  1. JWT из `sessionStorage["token"]` / cookie `token` → `claims.user_id` (claim поле: `"user_id"`, top-level, без `sub`)
  2. DOM-сканирование: `[data-testid*="user"]`, `[class*="profile"]`, `header`, `aside` — ищем UUID regex
  3. localStorage `bifrost_opencode_user_id`
  4. prompt() — последний рубеж
- Сохраняет в `localStorage.bifrost_opencode_user_id`
- Рядом маленькая кнопка `×` — сбрасывает кэш
- URL: `/setup-opencode/?user=<uid>` — открывает новую вкладку с копируемой конфигурацией opencode

## Кнопка "🛠 OpenCode" (в Users таблице)
- Видна только на `?page=users`
- Ищет строки по `data-testid*="user-status-<UUID>"`
- Вставляет в Action cell (`div[class*="flex"]`)
- `data-oc-row-btn` маркер чтобы не дублировать

## КРИТИЧЕСКИ ВАЖНО — MutationObserver
- **НЕ злоупотреблять тяжёлыми DOM-сканами на каждое изменение** — Next.js делает сотни мутаций при hydration, это замораживает страницу
- Используется throttling 750ms (`throttledScan()`)
- UUID определяется ОДИН раз на startup, кэшируется в `cachedUid`
- Инвалидация кэша только при клике на кнопку или clear

## index.html страница настройки
- Заголовок "opencode.json — Bifrost LiteLLM"
- GET `?user=<UUID>` → fetch `/setup-opencode/api/models?user=<UUID>` → рендерит JSON
- AGENT_PROMPT — префикс перед JSON (для копирования в агента)
- Кнопки: 📋 Copy (копирует AGENT_PROMPT + JSON), ⬇ Download (txt файл)
- buildCfg() создаёт конфиг с `provider.bifrost-litellm` (npm: @ai-sdk/openai-compatible, baseURL, env apiKey)
- Vision модели получают `modalities: { input: ["text","image"] }` + `attachment: true`
- **ВАЖНО:** используется `modalities.input`, НЕ `capabilities.input.image` — последнее opencode игнорирует!

## VM / ssh
- VM: 162.55.137.149:1995, user dev01, password 7Cr4iW9l8P
- Master key: sk-litellm-placeholder-replace-before-prod
- `runsudo.py` для SSH+sudo: `python C:\Users\Admin\AppData\Local\Temp\opencode\runsudo.py <base64-encoded-shell>`
- Кодирование: `[Text.Encoding]::UTF8.GetBytes('echo "7Cr4iW9l8P" | sudo -S -p "" <cmd>')` → base64

## LiteLLM UI headers на которые инжектится
- `text/html` ответы от upstream `litellm_upstream` (port 4001)
- `proxy_hide_header Content-Security-Policy;` — иначе CSP блокирует наш injected script
- `proxy_hide_header X-Frame-Options;`

## Troubleshooting
- Кнопка не появляется → проверить что injected script в HTML: `curl -sL https://hcbifrost.herocraft.com/litellm/ui/ | grep users-btn`
- Браузер зависает → throttle недостаточный, уменьшить частоту DOM-скана
- User_id не определяется → добавить `?ocdebug=1` к URL и смотреть `[oc-inject]` в Console