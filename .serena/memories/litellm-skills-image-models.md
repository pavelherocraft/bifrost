# LiteLLM Skills — behavior with image generation models

**Date:** 2026-07-02

## TL;DR

LiteLLM Skills feature (`SkillsInjectionHook`) — **только для chat completions**.
Image generation endpoints (`/v1/images/generations`, `/v1/images/edits`) не
получают skills injection независимо от payload. Передача skills-параметров
в image request — проксируется as-is к upstream, и поведение зависит от
upstream:

- **OpenAI (gpt-image-2, gpt-image-1.5)**: НЕ поддерживает `container`,
  `skills`, `extra_body` — любой unknown parameter → HTTP 400.
- **Google (gemini/gemini-3-pro-image)**: принимает `container.skills` без
  жалоб, но **не валидирует skill_id** — если id не существует, просто
  игнорирует и генерирует картинку как обычно.

## Endpoint inventory

```
GET  /public/skill_hub       — список публичных skills (0 если не загружено)
POST /v1/skills              — загрузка skill (требует ANTHROPIC_API_KEY)
GET  /v1/skills/{skill_id}   — получение skill content
```

Текущее состояние на нашей VM:
```bash
$ curl https://hcbifrost.herocraft.com/litellm/public/skill_hub
{"plugins":[],"count":0}
```

**Skills не загружены** — нет `ANTHROPIC_API_KEY` для регистрации через Anthropic Skills API.

## Test matrix (2026-07-02)

Запросы через `https://hcbifrost.herocraft.com/litellm/v1/images/generations`:

| Model | Payload | HTTP | Latency | Note |
|-------|---------|------|---------|------|
| `gpt-image-2` | baseline 1024x1024 | 200 | 11.0s | 1 image, 196 img_tokens, quality=low |
| `gpt-image-1.5` | baseline 1024x1024 | 200 | 9.6s | 1 image, 1056 img_tokens, quality=medium |
| `gpt-image-2` | + `container.skills=[...]` | 400 | 0.5s | `OpenAIException: Unknown parameter: 'container'` |
| `gpt-image-2` | + top-level `skills=[...]` | 400 | 0.5s | `Unknown parameter: 'skills'` |
| `gpt-image-2` | + `litellm_params.skills` | 400 | 0.5s | `Unknown parameter: 'litellm_params'` |
| `gpt-image-2` | + `extra_body.skills` | 400 | 0.5s | `Unknown parameter: 'skills'` (extra_body НЕ фильтруется LiteLLM) |
| `gemini/gemini-3-pro-image` | baseline | 200 | 14.3s | 1 image, 1120 img_tokens |
| `gemini/gemini-3-pro-image` | + `container.skills=["skill_id_test"]` | 200 | 13.1s | 1 image — skill_id просто ignored |

## Behavior details

### gpt-image-2 / gpt-image-1.5 (OpenAI-compatible)

Upstream OpenAI image API **не принимает**:
- `container` — Anthropic-стиль, не известен OpenAI
- `skills` — Anthropic-стиль, не известен OpenAI
- `litellm_params` — internal LiteLLM, должен быть отфильтрован ДО отправки к OpenAI
- `extra_body` — OpenAI принимает только определённые fields, всё остальное → 400

**LiteLLM НЕ фильтрует unknown params** для image endpoints — пробрасывает
as-is к OpenAI, OpenAI отвергает. Чтобы skills работали с gpt-image
нужно реализовать в нашем hook (`user_agent_hook.py`) pre-call processing,
которое ИНЖЕКТИТ skills (например, модифицирует `prompt` с инструкциями)
ДО проксирования в OpenAI.

### gemini/gemini-3-pro-image (Google)

Google API более tolerant — принимает extra fields без ошибки. **Но**
skill_id никак не валидируется: fake `"skill_id_test"` → 200 OK, изображение
сгенерировано БЕЗ применения skills. Это silent ignore, НЕ работающий
skill injection.

## Размеры (валидные)

- `gpt-image-2`: `1024x1024`, `1024x1536`, `1536x1024`, `auto` (256x256 = 400, "below minimum")
- `gpt-image-1.5`: `1024x1024`, `1024x1536`, `1536x1024`, `auto`
- `gemini/gemini-3-pro-image`: `1024x1024` (другие не проверял)

## Skills hook в LiteLLM

При старте LiteLLM регистрирует:
```
litellm.proxy.hooks.litellm_skills.main.SkillsInjectionHook
```

Этот hook:
1. Активируется ТОЛЬКО для chat completions (где есть `messages`)
2. Ищет `container.skills` или `litellm_skills` в request payload
3. Загружает skill content (markdown инструкции) из `/v1/skills/{id}`
4. **Inject в `system` message** chat completions

Для `/v1/images/generations` hook **не срабатывает** — нет messages для инжекции.

## Возможные улучшения (TODO)

Если нужно подключить skills к image generation:

**Подход 1: Prompt augmentation** (в нашем `user_agent_hook.py` pre-call hook):
```python
# В image request, если есть container.skills или extra skill field:
if skills := request_payload.get("container", {}).get("skills"):
    skill_prompts = [load_skill(s) for s in skills]
    request_payload["prompt"] = "\n\n".join(skill_prompts) + "\n\n" + request_payload["prompt"]
    # Или передать в system context через chat completions wrapper
```

**Подход 2: Двухшаговый pipeline**:
1. Chat completions с skills → получить augmented prompt
2. Передать augmented prompt в image generations

Подход 1 проще и быстрее. Требует реализации pre-call hook.

## Files

- Test script: `C:\Users\Admin\AppData\Local\Temp\opencode\test_skills_image2.py`
- Hook source: `/app/.venv/lib/python3.13/site-packages/litellm/skills/main.py` (in container)
- OpenAPI: `https://hcbifrost.herocraft.com/litellm/openapi.json` (520 paths, 3 skill-related)