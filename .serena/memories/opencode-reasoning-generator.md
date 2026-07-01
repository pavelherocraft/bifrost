# opencode.json generator — reasoning auto-injection

**Date:** 2026-07-01
**Status:** ✅ DEPLOYED

## What

`/opt/opencode-setup/api.py` теперь содержит `_REASONING_CAPABLE` set (20 моделей: GLM серия, Kimi K2, MiniMax-M3, Qwen3, mimo) и добавляет `reasoning: bool` в `/models?user=<UUID>` response.

`/opt/opencode-setup/index.html` `buildCfg()` теперь читает `m.reasoning` и добавляет `entry.options = { thinking: { type: "enabled" } }` для reasoning-capable моделей.

## Files

- `/opt/opencode-setup/api.py` — `+_REASONING_CAPABLE` (lines 39-54), `+reasoning` in `resolve_models()` output
- `/opt/opencode-setup/index.html` — `+if (m.reasoning) entry.options = ...` в `buildCfg()`
- Backup: `/opt/opencode-setup/api.py.bak.reasoning-gen`, `/opt/opencode-setup/index.html.bak.reasoning-gen`

## Deployment

- Deploy script: `C:\Users\Admin\AppData\Local\Temp\opencode\deploy_gen.py <local> <remote>` (generic, не завязан на users-btn.js)
- Restart: `pkill -9 -f "python3 /opt/opencode-setup/api.py"; nohup python3 /opt/opencode-setup/api.py &`

## Reasoning-capable models (set in _REASONING_CAPABLE)

```python
_REASONING_CAPABLE = {
    "glm-4.5", "glm-4.5-air", "glm-4.6",
    "GLM-4.7", "GLM-4.7 (res)",
    "GLM-5.1", "GLM-5.1 (res)",
    "GLM-5.2", "GLM-5.2 (res)",
    "kimi-k2-0905-preview", "kimi-k2-turbo-preview",
    "Kimi K2.6", "Kimi K2.7",
    "MiniMax-M3",
    "QWEN3.7-plus", "qwen3-coder-plus", "qwen3-coder-flash", "qwen3-max",
    "mimo-v2.5", "mimo-v2.5-pro",
}
```

## EXPLICITLY EXCLUDED

- `MiniMax-M2.7` — LiteLLM handler теряет теги `<think>`, капчуринг не работает
- `MiniMax-M2`, `MiniMax-M2.5` — старые, без thinking
- `moonshot-v1-128k` — старая v1 серия, нет reasoning
- `gpt-image-1.5`, `gpt-image-2` — image gen only
- `gemini/gemini-3-pro-image`, `gemini/gemini-3.1-flash-image` — image gen

## Why no budgetTokens

Initial assumption (по аналогии с Anthropic) — что нужен `budget_tokens`. **НЕВЕРНО.**
Official zhipu docs (https://docs.bigmodel.cn/cn/guide/models/text/glm-5) показывают просто `{"thinking": {"type": "enabled"}}` — без budget.

GLM-5 имеет свой `reasoning_effort: "max"|"high"` параметр (отдельный, не в thinking) — НЕ добавлено в этот патч, можно точечно добавить через upstream если нужно.

## Verification done

1. `curl /health` → `{"ok": true}` ✓
2. `curl /models?user=54f4915d-...` (admin user) → JSON содержит `reasoning: true` для GLM-5.2, Kimi K2.7, MiniMax-M3, QWEN3.7-plus, mimo-v2.5, mimo-v2.5-pro; `reasoning: false` для MiniMax-M2.7 ✓
3. Replicated `buildCfg()` логика в Python → для reasoning=true моделей генерируется `options: { thinking: { type: "enabled" } }`, для reasoning=false — нет ✓

## User flow

1. User открывает `/setup-opencode/?user=<uuid>` 
2. `index.html` fetch `/setup-opencode/api/models?user=<uuid>` 
3. Получает models с `reasoning: true/false`
4. `buildCfg()` для reasoning=true → добавляет `options.thinking`
5. User копирует JSON, кладёт в `~/.config/opencode/opencode.json`
6. Reasoning работает автоматически (LiteLLM transparent passthrough в upstream)