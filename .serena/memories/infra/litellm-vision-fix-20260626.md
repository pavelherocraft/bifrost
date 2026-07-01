# Vision (image) support fix — 2026-06-26

## Суть
Kimi K2.7 и MiniMax-M3 не распознавали картинки через LiteLLM, хотя работали напрямую.
Причина: в `litellm_params` отсутствовал заголовок `User-Agent` (headers).

## Фикс
SQL UPDATE добавил `headers.User-Agent` (из `KIMI_USER_AGENT` в `.env`) для обеих моделей.

## Ключевые факты
- LiteLLM **не стриппит** image content (verified via `OpenAIGPTConfig.transform_request` hook)
- `supports_vision: true` в `model_info` — только метаданные, НЕ используется для фильтрации
- `litellm_params` в БД: зашифрованные поля (`custom_llm_provider`, `model`, `litellm_credential_name`) хранятся в Fernet-формате; доступны для обновления только `headers`, `api_base`, `max_tokens` и т.п.
- НЕ добавлять `api_base` в `litellm_params` — credential `Kimi` уже содержит правильный endpoint
- moonshot API требует `User-Agent` для vision; minimax.io — аналогично

## Модели
| Модель | model_id | Статус |
|---|---|---|
| Kimi K2.6 | 9a84d2e1-... | ✓ работает (headers не требовался — есть в catalog) |
| Kimi K2.7 | 0ec138c9-... | ✓ пофикшено (добавлен headers.User-Agent) |
| MiniMax-M3 | 3039366c-... | ✓ пофикшено (добавлен headers.User-Agent) |
| QWEN3.7-plus | 03fd5e9e-... | ✓ работает |
| mimo-v2.5 | 8c8d17df-... | ✓ работает (custom_openai провайдер) |
