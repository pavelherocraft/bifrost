# Где искать user_id залогиненного юзера в LiteLLM UI

## В UI (визуально)
Справа сверху есть блок с пользовательскими данными (user profile dropdown/header).
Внутри виден **USER ID** и кнопка "скопировать" — это самый надёжный visual source.

## В браузере (для injected JS)

### Способ 1: DOM (предпочтительный)
Сканировать `document.body` на наличие текста "User ID" / "USER ID" рядом с которым идёт UUID.
Часто находится в profile-dropdown или sidebar с user info.
```js
function uidFromDOM() {
  // Ищем элементы с data-testid, содержащие user_id
  var candidates = document.querySelectorAll('[data-testid*="user" i], [class*="user" i]');
  for (var i=0; i<candidates.length; i++) {
    var txt = candidates[i].textContent || "";
    var m = txt.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
    if (m) return m[0];
  }
  return null;
}
```

### Способ 2: JWT в sessionStorage/cookie (точный, без UI scraping)
После логина LiteLLM UI сохраняет JWT в **двух местах под ключом `"token"`**:
- `sessionStorage.getItem("token")` — основной канон
- `document.cookie` → `token=<urlencoded JWT>; SameSite=Lax`

JWT — обычный HS256, подписанный master_key. **Без `sub`**, но есть поле `user_id`:

```json
{
  "user_id": "default_user_id",
  "key": "sk-...",            // ephemeral UI session key (1h, ~$10)
  "user_email": null,
  "user_role": "proxy_admin",
  "login_method": "username_password",
  "premium_user": false,
  "auth_header_name": "Authorization",
  "server_root_path": "/litellm"
}
```

### Извлечение user_id из JWT одной строкой:
```js
JSON.parse(atob(sessionStorage.getItem("token").split(".")[1])).user_id
```

### Полный fallback с cookie:
```js
function readToken() {
  var c = document.cookie.split("; ").find(function(r){return r.startsWith("token=")});
  if (c) {
    try { return decodeURIComponent(c.split("=").slice(1).join("=")); } catch(e){}
  }
  return sessionStorage.getItem("token");
}
function uidFromJWT() {
  var t = readToken();
  if (!t || t.split(".").length !== 3) return null;
  try {
    var payload = t.split(".")[1].replace(/-/g,"+").replace(/_/g,"/");
    var json = decodeURIComponent(atob(payload).split("").map(function(c){
      return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
    }).join(""));
    return JSON.parse(json).user_id || null;
  } catch(e) { return null; }
}
```

## Замечания
- **localStorage НЕ используется для auth/user_id**. Только `litellm_worker_url`, `litellm_selected_worker_id`, `hideMissingProviderBanner`, chat history.
- `key` claim — это ephemeral UI session key, можно отправлять как `Authorization: Bearer sk-...` для `/v2/user/info`.
- При logout `clearTokenCookies()` удаляет cookie и `sessionStorage["token"]`.
- ExperimentalUIJWTToken path (если включён) возвращает encrypted opaque string, не декодируется на клиенте — на этой VM не используется.

## Backend (для проверки через API)
```bash
curl -s -X POST "https://hcbifrost.herocraft.com/litellm/v2/login" \
     -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"admin"}' \
  | python3 -c "import sys,json,base64; \
                t=json.load(sys.stdin)['token']; \
                p=t.split('.')[1]+'=='*(-len(t.split('.')[1])%4); \
                print(json.dumps(json.loads(base64.urlsafe_b64decode(p)),indent=2))"
```

## Контекст VM
- URL: https://hcbifrost.herocraft.com/litellm/ui/
- admin/admin credentials
- master_key: sk-litellm-placeholder-replace-before-prod
- Docker port: 4001:4000
- Container: litellm
- Injected script: `/opt/opencode-setup/users-btn.js` (через nginx sub_filter)
